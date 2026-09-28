"""Controlled local benchmark runner (8D).

Executes ONLY allowlisted pytest verification for manifest tasks that
declare `runner: {kind: pytest, entry: [files]}`, inside a fresh
temporary workspace per task. No shell, no DSH/GUI automation, no
network use, no writes outside the sandbox — plus the operator-directed
report path, and an auto-persisted failed report when a timeout fires.

Gating lives in the CLI: execution requires BOTH `--execute` and
`--i-understand-this-runs-local-agent-tasks`; without them `run` stays
a dry-run planner (8C).

Trust model (stated plainly): the operator owns fixture and overlay
content. Isolation here is by construction and by default — fresh
workspace, scrubbed env, argv-only subprocess, timeout kill — not a
security boundary against hostile task code; the closed
forbidden-capability vocabulary is enforced mechanically by the runner
(it never provides network, browser, broker, SSH, package install,
frontier, or anything outside the workspace), and metrics count task
verification outcomes, not undetectable sandbox escapes.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from minder_core import integrity
from minder_op.benchmark import BenchmarkError, manifest_fingerprint
from minder_memory.canonicalise import redact

RUNNER_KIND = "pytest"
DEFAULT_TIMEOUT = 120
OUTPUT_TAIL = 2000

REQUIRED_FLAGS_MSG = ("real execution requires --execute and "
                      "--i-understand-this-runs-local-agent-tasks "
                      "(8D controlled local runner); without them only "
                      "--dry-run exists")

# Child env is allowlisted, not blocklisted: only generic locale/PATH
# pass through. This drops proxies, LD_LIBRARY_PATH (which poisons
# python on this machine), PYTHONPATH, and every MINDER_* runtime flag.
_KEEP_ENV = ("PATH", "LANG", "LC_ALL")


class RunnerError(Exception):
    """Runner refused (bad overlay, no runner, no workspace...)."""


def select_tasks(manifest, task_id=None):
    """Tasks to execute: the named task, or every runnable task."""
    tasks = manifest.get("tasks") or []
    if task_id:
        task = next((t for t in tasks
                     if t.get("task_id") == task_id), None)
        if not task:
            raise BenchmarkError(f"unknown task: {task_id}")
        if task.get("runner", {}).get("kind") != RUNNER_KIND:
            raise BenchmarkError(
                f"task {task_id} has no runner (declared, not "
                "executable); nothing to execute")
        return [task]
    runnable = [t for t in tasks
                if t.get("runner", {}).get("kind") == RUNNER_KIND]
    if not runnable:
        raise BenchmarkError("suite has no runnable tasks")
    return runnable


def _copy_tree(src_dir, dest_dir):
    """Copy regular files only; refuse symlinks and anything that is
    not a plain file so an overlay can never point outside."""
    for base, _dirs, files in os.walk(src_dir):
        for name in files:
            src = Path(base) / name
            if src.is_symlink() or not src.is_file():
                raise RunnerError(
                    f"overlay member is not a regular file: {src}")
            rel = src.relative_to(src_dir)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest, follow_symlinks=False)


def _stage_workspace(suite_dir, task):
    workspace = Path(tempfile.mkdtemp(prefix="minder-bench-"))
    for rel in task.get("fixtures") or []:
        src = (suite_dir / rel).resolve()
        if not src.is_file():
            raise RunnerError(f"fixture disappeared: {rel}")
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return workspace


def _child_env(workspace):
    env = {key: os.environ[key] for key in _KEEP_ENV
           if key in os.environ}
    tmp = workspace / ".tmp"
    home = workspace / ".home"
    tmp.mkdir()
    home.mkdir()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                "MINDER_NO_SYSTEMD": "1", "TMPDIR": str(tmp),
                "HOME": str(home)})
    return env


def _entry_dir(workspace, task, entry_files):
    """The workspace sub-directory the verification runs in: where the
    declared fixture matching the entry file lives."""
    for item in entry_files:
        for path in task.get("fixtures") or []:
            if isinstance(path, str) and (path == item or
                                          path.endswith("/" + item)):
                candidate = workspace / Path(path).parent
                if candidate.is_dir():
                    return candidate
    return workspace


def _hardened_argv(work_dir, entry_files, junit_path=None, extra=None):
    """The pytest argv every run uses: no cache provider, junit evidence
    into the runner-owned path, conftest loading only when the task's
    fixtures actually declare a conftest.py (an overlay-added conftest
    is a tamper vector, not configuration)."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if junit_path is not None:
        cmd.append(f"--junit-xml={junit_path}")
    cmd.extend(extra or [])
    cmd.extend(entry_files)
    return cmd


def _collect_ids(work_dir, env, timeout):
    """Pristine-tree test ids via --collect-only -q (PRD 6.1 step 1)."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q",
           "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=str(work_dir), env=env,
                          capture_output=True, text=True, timeout=timeout)
    return [line.strip() for line in (proc.stdout or "").splitlines()
            if "::" in line]


def _run_pytest(cmd, work_dir, env, timeout):
    """Run pytest in its own process group; kill the group on timeout."""
    started = time.monotonic()
    wall_start = time.time()
    proc = subprocess.Popen(
        cmd, cwd=str(work_dir), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, (out or ""), started, wall_start
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, _ = proc.communicate()
        return None, (out or ""), started, wall_start


def execute_task(suite_dir, task, overlay=None, timeout=DEFAULT_TIMEOUT,
                 keep_workspace=False):
    """Run one task's pytest verification in a fresh sandbox, with
    verification integrity (PRD v0.9 9A): pristine collection, protected
    paths (I-1), junit run evidence, and holdout tests the overlay
    cannot pre-place. Status is verified | failed | timeout | tampered |
    underverified; the run entry carries the full verdict."""
    entry_spec = task.get("runner") or {}
    workspace = _stage_workspace(suite_dir, task)
    try:
        entry_files = entry_spec.get("entry") or []
        work_dir = _entry_dir(workspace, task, entry_files)
        env = _child_env(workspace)
        # 6.1 step 1: pristine tree hash + expected test ids. PYTEST_* env
        # cannot reach the child (allowlist), so collection is trusted.
        pre_files = integrity.tree_files(work_dir)
        expected_ids = _collect_ids(work_dir, env, timeout)
        expected_tests = task.get("runner", {}).get("expected_tests")
        if expected_tests is not None and \
                len(expected_ids) != expected_tests:
            raise RunnerError(
                f"manifest expected_tests={expected_tests} but the "
                f"pristine fixtures collect {len(expected_ids)} tests "
                "(the manifest is wrong, not the run)")
        junit_dir = workspace / ".minder"
        junit_dir.mkdir()
        junit_path = junit_dir / "junit.xml"

        # 6.1 step 2-3: apply the overlay, hash, protected diff (I-1).
        if overlay:
            _copy_tree(Path(overlay), work_dir)
        post_files = integrity.tree_files(work_dir)
        protected = task.get("protected")  # None -> module default

        # 6.1 step 5: visible run with junit evidence. --noconftest only
        # when the FIXTURES declare no conftest (overlay conftest is
        # evidence, not configuration).
        fixtures = task.get("fixtures") or []
        extra = [] if any(str(f).endswith("conftest.py")
                          for f in fixtures) else ["--noconftest"]
        rc, out, started, wall = _run_pytest(
            _hardened_argv(work_dir, entry_files, junit_path, extra),
            work_dir, env, timeout)
        junit = integrity.parse_junit(junit_path, started=wall)

        # 6.1 step 4+6: holdout staged AFTER the overlay and the visible
        # run, into a fresh directory the overlay cannot address, with
        # the task module importable via PYTHONPATH only.
        holdout_status = "absent"
        holdout_files = task.get("holdout") or []
        if holdout_files:
            holdout_dir = junit_dir / "holdout"
            holdout_dir.mkdir()
            for rel in holdout_files:
                src = (suite_dir / rel).resolve()
                if not src.is_file():
                    raise RunnerError(f"holdout disappeared: {rel}")
                shutil.copyfile(src, holdout_dir / Path(rel).name)
            hold_env = dict(env, PYTHONPATH=str(work_dir))
            h_junit = holdout_dir / "junit.xml"
            h_rc, _h_out, h_started, _ = _run_pytest(
                _hardened_argv(holdout_dir,
                               [Path(rel).name for rel in holdout_files],
                               h_junit, ["--noconftest"]),
                holdout_dir, hold_env, timeout)
            h_junit_parsed = integrity.parse_junit(h_junit,
                                                   started=h_started)
            if h_rc is None:
                holdout_status = "fail"
            elif h_junit_parsed and h_junit_parsed[0]["failed"] == 0 \
                    and h_junit_parsed[0]["errors"] == 0 \
                    and h_junit_parsed[0]["collected"] > 0:
                holdout_status = "pass"
            else:
                holdout_status = "fail"

        # 6.1 step 7: the verdict.
        if rc is None:
            verdict = {"status": "timeout", "tamper_reasons": [],
                       "tests": {"collected": 0, "passed": 0, "failed": 0,
                                 "skipped": 0, "errors": 0},
                       "holdout": {"status": holdout_status}}
        else:
            counts, junit_ids = junit if junit else (None, None)
            verdict = integrity.assess(
                pre_files, post_files, protected, junit_counts=counts,
                junit_ids=junit_ids, expected_ids=expected_ids or None,
                expected_tests=expected_tests,
                allow_skips=task.get("runner", {}).get("allow_skips", 0),
                holdout_status=holdout_status,
                run_status="verified" if rc == 0 else "failed")  # noqa: E501
        entry = {"task_id": task.get("task_id"),
                 "status": verdict["status"],
                 "exit_code": rc if rc is not None else -1,
                 "duration_ms": round((time.monotonic() - started) * 1000),
                 "integrity": verdict,
                 "output_tail": redact(out[-OUTPUT_TAIL:])}
        return entry, (workspace if keep_workspace else None)
    finally:
        if not keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def build_report(manifest, entries, timeout=DEFAULT_TIMEOUT,
                 workspace=None):
    """Assemble an 8C-schema report from executed run entries."""
    total = len(entries)
    verified = sum(1 for e in entries if e["status"] == "verified")
    report = {
        "report_version": 1,
        "suite_id": manifest.get("suite_id"),
        "suite_fingerprint": manifest_fingerprint(manifest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "candidate",
        "runs": entries,
        "metrics": {
            "comparable_runs": total,
            "verified_completion_rate": round(verified / total, 4)
            if total else 0.0,
            "unsafe_executions": 0,
            "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0,
        },
        "benchmark_runner": {
            "mode": "local-sandbox (8D controlled local runner)",
            "timeout_s": timeout,
            "workspace": str(workspace) if workspace else None,
        },
    }
    return report
