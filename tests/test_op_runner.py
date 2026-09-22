"""Controlled local benchmark runner tests (8D).

The runner executes ONLY allowlisted pytest verification inside a fresh
temporary workspace: no shell, no network use, no DSH/GUI automation,
no writes outside the sandbox (plus the operator-directed report).
Execution is double-flag gated; a timeout kills the whole process group
and yields a failed report; the child env is scrubbed (no proxy vars,
no LD_LIBRARY_PATH, no MINDER_* runtime flags).

8C invariants re-checked here: produced reports pass validate_report,
`baseline create` still requires --yes, and a 1-run report compares as
INSUFFICIENT_SAMPLE — the sample guard works on real runs.
"""
import json
import os
import tempfile
import time
from pathlib import Path

from minder_op import benchmark as bench
from minder_op import runner
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE = "coding-core-v1"
KEY_TASK = "t3_keyerror_default"
KEY_SOLUTION = (REPO_ROOT / "benchmarks" / SUITE /
                "tasks" / "keyerror_default" / "solution")
BOTH_FLAGS = ["--execute", "--i-understand-this-runs-local-agent-tasks"]


def run_json(*extra):
    """Execute the runner via the CLI and return (code, report dict)."""
    import contextlib
    import io
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "run", "--suite", SUITE, "--json",
                     *extra])
    return code, json.loads(buf.getvalue())


def test_execution_requires_both_flags(capsys):
    for flags in ([], ["--execute"],
                  ["--i-understand-this-runs-local-agent-tasks"]):
        code = main(["benchmark", "run", "--suite", SUITE, "--task",
                     KEY_TASK, *flags])
        assert code == EXIT_USAGE
        err = capsys.readouterr().err
        assert "--execute" in err and \
            "--i-understand-this-runs-local-agent-tasks" in err


def test_dry_run_still_plans_and_execute_exclusive(capsys):
    code = main(["benchmark", "run", "--suite", SUITE, "--dry-run"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert KEY_TASK in out and "8D" in out
    assert main(["benchmark", "run", "--suite", SUITE, "--dry-run",
                 *BOTH_FLAGS]) == EXIT_USAGE
    assert "dry-run" in capsys.readouterr().err


def test_execute_fixture_fails_without_fix():
    """The shipped task ships broken on purpose: without a fix the run
    must complete, report failed, rate 0, and a schema-valid 8C report
    (execution exit 0 — `compare` is the pass/fail gate, not `run`)."""
    code, report = run_json("--task", KEY_TASK, *BOTH_FLAGS)
    assert code == EXIT_OK
    assert bench.validate_report(report) == []
    assert report["suite_id"] == SUITE
    assert report["suite_fingerprint"] == bench.manifest_fingerprint(
        bench.read_manifest(SUITE))
    assert report["metrics"]["comparable_runs"] == 1
    assert report["metrics"]["verified_completion_rate"] == 0.0
    assert report["runs"][0]["status"] == "failed"
    assert report["runs"][0]["task_id"] == KEY_TASK
    assert "KeyError" in report["runs"][0]["output_tail"]


def test_execute_with_reference_solution_verifies():
    code, report = run_json("--task", KEY_TASK,
                            "--overlay", str(KEY_SOLUTION), *BOTH_FLAGS)
    assert code == EXIT_OK
    assert report["runs"][0]["status"] == "verified"
    assert report["metrics"]["verified_completion_rate"] == 1.0
    assert bench.validate_report(report) == []


def test_workspace_removed_by_default_and_kept_on_flag():
    before = _bench_dirs()
    code, report = run_json("--task", KEY_TASK, *BOTH_FLAGS)
    assert not (_bench_dirs() - before)  # cleaned up
    code, report = run_json("--task", KEY_TASK, *BOTH_FLAGS,
                            "--keep-workspace")
    ws = Path(report["benchmark_runner"]["workspace"])
    assert ws.is_dir() and "minder-bench-" in ws.name
    assert (ws / "tasks" / "keyerror_default" / "task.py").exists()
    assert _bench_dirs() - before == {ws}


def _bench_dirs():
    return set(Path(tempfile.gettempdir()).glob("minder-bench-*"))


def test_overlay_traversal_refused(tmp_path, capsys):
    # missing overlay dir -> clean usage error
    assert main(["benchmark", "run", "--suite", SUITE, "--task",
                 KEY_TASK, "--overlay", str(tmp_path / "nope"),
                 *BOTH_FLAGS]) == EXIT_USAGE
    assert "overlay" in capsys.readouterr().err
    # a symlinked overlay member must be refused, never followed: the
    # symlink points at a path outside the overlay that must stay
    # unborn
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    payload = tmp_path / "outside" / "ESCAPED.txt"
    os.symlink(payload, overlay / "up")
    assert main(["benchmark", "run", "--suite", SUITE, "--task",
                 KEY_TASK, "--overlay", str(overlay),
                 *BOTH_FLAGS]) == EXIT_USAGE
    assert "overlay" in capsys.readouterr().err
    assert not payload.exists()


def _write_runner_suite(root, entry, body, task_id="t_slow"):
    manifest = {
        "manifest_version": 1, "suite_id": "runner-x", "title": "t",
        "tiers": [3],
        "tasks": [{"task_id": task_id, "tier": 3, "family": "fixture",
                   "title": "t", "fixtures": [f"tasks/{task_id}/{entry}"],
                   "expected": "tests_pass",
                   "forbidden": sorted(bench.FORBIDDEN_VOCABULARY),
                   "runner": {"kind": "pytest", "entry": [entry]}}]}
    suite = root / "runner-x"
    task_dir = suite / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (suite / "manifest.json").write_text(json.dumps(manifest))
    (task_dir / entry).write_text(body)


def test_timeout_kills_process_and_writes_failed_report(tmp_path,
                                                        monkeypatch,
                                                        capsys):
    root = tmp_path / "benchmarks"
    root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    _write_runner_suite(root, "test_slow.py",
                        "import time\n"
                        "def test_sleep():\n"
                        "    time.sleep(120)\n")
    before = _bench_dirs()
    started = time.monotonic()
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "run", "--suite", "runner-x", "--json",
                     "--timeout", "2", *BOTH_FLAGS])
    wall = time.monotonic() - started
    assert code == EXIT_USAGE and wall < 30  # killed, not waited out
    report = json.loads(buf.getvalue())
    assert report["runs"][0]["status"] == "timeout"
    assert bench.validate_report(report) == []
    # failed report auto-persisted next to the suite root
    failed = root / "reports" / "runner-x-failed.json"
    assert failed.exists()
    stored = json.loads(failed.read_text())
    assert stored["runs"][0]["status"] == "timeout"
    assert not (_bench_dirs() - before)  # workspace cleaned on timeout


def test_child_env_is_scrubbed(tmp_path, monkeypatch, capsys):
    root = tmp_path / "benchmarks"
    root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/lib-poison")
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")
    _write_runner_suite(root, "test_env.py",
                        "import os\n"
                        "def test_env():\n"
                        "    assert 'http_proxy' not in os.environ\n"
                        "    assert 'LD_LIBRARY_PATH' not in os.environ\n"
                        "    assert 'MINDER_ASSIST' not in os.environ\n",
                        task_id="t_env")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "run", "--suite", "runner-x", "--json",
                     *BOTH_FLAGS])
    assert code == EXIT_OK
    report = json.loads(buf.getvalue())
    assert report["runs"][0]["status"] == "verified", \
        report["runs"][0]["output_tail"]


def test_task_without_runner_and_unknown_task_refused(capsys):
    assert main(["benchmark", "run", "--suite", SUITE, "--task",
                 "t0_contracts", *BOTH_FLAGS]) == EXIT_USAGE
    assert "no runner" in capsys.readouterr().err
    assert main(["benchmark", "run", "--suite", SUITE, "--task",
                 "ghost", *BOTH_FLAGS]) == EXIT_USAGE
    assert "ghost" in capsys.readouterr().err


def test_out_report_feeds_8c_pipeline(tmp_path, monkeypatch):
    """--out persists a schema-valid report; pinning + comparing works
    end to end and the <20-runs guard still holds on real runs."""
    out = str(tmp_path / "run-report.json")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "run", "--suite", SUITE, "--task",
                     KEY_TASK, "--overlay", str(KEY_SOLUTION),
                     "--out", out, *BOTH_FLAGS])
    assert code == EXIT_OK
    stored = json.loads(Path(out).read_text())
    assert bench.validate_report(stored) == []
    root = tmp_path / "benchmarks"
    root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["benchmark", "baseline", "create", out,
                     "--yes"]) == EXIT_OK
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "compare", out, out])
    assert code == EXIT_USAGE  # 1 run < 20 -> INSUFFICIENT_SAMPLE
    assert "INSUFFICIENT_SAMPLE" in buf.getvalue()


def test_runner_module_never_uses_shell():
    """Allowlist discipline: the runner constructs argv lists and never
    hands a shell string to the OS."""
    import inspect
    src = inspect.getsource(runner)
    assert "shell=True" not in src
    assert "os.system" not in src
