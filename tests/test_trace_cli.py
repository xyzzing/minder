"""Trace CLI tests (Slices 3-4): `trace ls|review|show|feedback|regress`.

The laws under test are the project's own: flag-off is byte-inert (no
writes at all), exit codes are 0/1/2, every write needs `--yes`, and a
regression case is only created from a finding a human has *confirmed* —
the evidence chain `trace → finding → reviewer confirmation → regression
case` is enforced rather than documented.
"""
import json
import os
from pathlib import Path

import pytest

from minder_op import benchmark as bench
from minder_op.cli import EXIT_OK, EXIT_USAGE, main
from tracebuild import bash, build_session
import tracebuild

# CI runs 3.11-3.14; stdlib `compression.zstd` only exists on 3.14, and the
# `zstandard` module is never a dependency, so everything here needs either
# it or the `zstd` binary. The same probe covers reading, since a working
# writer means one of the two paths is present.
pytestmark = pytest.mark.skipif(
    tracebuild.compressor() is None,
    reason="no zstd writer (needs Python 3.14 stdlib or the zstd binary)")

CURL = 'curl -L -o dbs.pdf "https://www.dbs.com/sustainability/report"'
CURL_OUT = ["  0 181.2k  0  0  1.24M --:--:-- 100",
            "  0 181.2k  0  0  905.5k --:--:-- 100",
            "  0 181.2k  0  0 573.6k --:--:-- 100"]


def _loop_session(root, session_id="session-loop"):
    events = [{"type": "session", "seq": 0, "time": 1,
               "data": {"id": session_id, "cwd": "/repo"}}]
    seq = 1
    for payload in CURL_OUT:
        events += bash(seq, CURL, payload)
        seq += 2
    return build_session(root, session_id, events)


def _suite(root, suite_id="coding-core-v1"):
    """The smallest suite the manifest validator accepts."""
    suite = root / suite_id
    (suite / "tasks").mkdir(parents=True)
    (suite / "manifest.json").write_text(json.dumps({
        "manifest_version": 1, "suite_id": suite_id, "title": "mini",
        "description": "test suite", "tiers": [0, 3],
        "tasks": [{"task_id": "t0", "tier": 0, "family": "contract",
                   "title": "gate", "fixtures": [],
                   "expected": "tests_pass",
                   "forbidden": ["network", "package_install", "ssh",
                                 "writes_outside_sandbox"]}],
    }, indent=2))
    return suite


@pytest.fixture
def env(tmp_path, monkeypatch):
    dsh = tmp_path / "dsh"
    benchmarks = tmp_path / "benchmarks"
    benchmarks.mkdir()
    _suite(benchmarks)
    dbp = tmp_path / "m.sqlite"
    monkeypatch.setenv("MINDER_DSH_HOME", str(dsh))
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(benchmarks))
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    monkeypatch.delenv("MINDER_TRACE_REVIEW", raising=False)
    from minder_op import dsh_sessions
    dsh_sessions._CACHE.clear()
    _loop_session(dsh)
    dsh_sessions._CACHE.clear()
    return {"tmp": tmp_path, "db": dbp, "benchmarks": benchmarks,
            "dsh": dsh}


def _review(env, *extra, expect=EXIT_OK):
    args = ["--db", str(env["db"]), "trace", "review", "session-loop",
            "--json", *extra]
    assert main(args) == expect
    return args


def _captured_json(capsys):
    return json.loads(capsys.readouterr().out)


def _store_a_review(env, capsys, monkeypatch):
    """Store one review and return (review_id, finding_id)."""
    monkeypatch.setenv("MINDER_TRACE_REVIEW", "on")
    _review(env)
    data = _captured_json(capsys)
    assert data["store"] == "ok", data
    finding = next(f for f in data["findings"]
                   if f["rule_id"] == "success-loop-same-result")
    return data["review_id"], finding["finding_id"]


# --- ls ------------------------------------------------------------------

def test_ls_lists_sessions_without_a_db_write(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "session-loop" in out
    assert "MINDER_TRACE_REVIEW=off" in out


def test_ls_json_shape(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "ls",
                 "--json"]) == EXIT_OK
    rows = _captured_json(capsys)
    assert [r["session"] for r in rows] == ["session-loop"]
    assert rows[0]["reviewed"] == "-"


# --- review: the flag law ------------------------------------------------

def test_review_is_read_only_by_default(env, capsys, monkeypatch):
    """Default off = byte-inert: nothing is written, not even the DB."""
    monkeypatch.delenv("MINDER_TRACE_REVIEW", raising=False)
    _review(env)
    data = _captured_json(capsys)
    assert data["store"] == "not-stored" and data["review_id"] is None
    assert not env["db"].exists()


def test_review_no_store_overrides_the_flag(env, capsys, monkeypatch):
    monkeypatch.setenv("MINDER_TRACE_REVIEW", "on")
    _review(env, "--no-store")
    data = _captured_json(capsys)
    assert data["review_id"] is None
    from minder_memory import trace_reviews
    assert trace_reviews.list_reviews(db_path=env["db"]) == []


def test_review_stores_when_the_flag_is_on(env, capsys, monkeypatch):
    rid, _fid = _store_a_review(env, capsys, monkeypatch)
    from minder_memory import trace_reviews
    assert trace_reviews.get_review(rid, db_path=env["db"]) is not None


def test_review_reports_the_incident_findings(env, capsys):
    _review(env)
    data = _captured_json(capsys)
    rules = {f["rule_id"] for f in data["findings"]}
    assert "success-loop-same-result" in rules
    assert data["summary"]["findings"] == len(data["findings"])
    assert data["summary"]["highest_severity"] in ("medium", "low", "info")


def test_review_on_an_unknown_session_is_usage_error(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "review",
                 "nope-not-a-session", "--json"]) == EXIT_USAGE
    assert "no session log" in capsys.readouterr().err


def test_review_accepts_a_session_id_suffix(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "review", "loop",
                 "--json"]) == EXIT_OK
    assert _captured_json(capsys)["report"]["status"] == "ok"


def test_rubric_adds_declared_workflow_rules(env, capsys, tmp_path):
    rubric = tmp_path / "r.json"
    rubric.write_text(json.dumps({
        "rubric_id": "coding-core-v1",
        "forbidden_tools": ["curl"],
        "required_tools": ["pytest"],
    }))
    _review(env, "--rubric", str(rubric))
    data = _captured_json(capsys)
    rules = {f["rule_id"] for f in data["findings"]}
    assert "forbidden-tool-used" in rules
    assert "required-tool-missing" in rules


def test_a_broken_rubric_is_refused_not_ignored(env, capsys, tmp_path):
    """Evaluating against the wrong standard is worse than not
    evaluating, so a bad rubric is an error, never a silent default."""
    rubric = tmp_path / "r.json"
    rubric.write_text("{not json")
    assert main(["--db", str(env["db"]), "trace", "review", "session-loop",
                 "--rubric", str(rubric)]) == EXIT_USAGE
    assert "not valid JSON" in capsys.readouterr().err

    rubric.write_text(json.dumps({"rubric_id": "x", "mystery": 1}))
    assert main(["--db", str(env["db"]), "trace", "review", "session-loop",
                 "--rubric", str(rubric)]) == EXIT_USAGE
    assert "unknown key" in capsys.readouterr().err


# --- show ----------------------------------------------------------------

def test_show_renders_a_stored_review(env, capsys, monkeypatch):
    rid, _fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "show", rid]) == EXIT_OK
    out = capsys.readouterr().out
    assert rid in out and "success-loop-same-result" in out
    assert "no feedback yet" in out


def test_show_unknown_review_is_usage_error(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "show",
                 "trv_nope"]) == EXIT_USAGE
    assert "no review" in capsys.readouterr().err


# --- feedback ------------------------------------------------------------

def test_feedback_dry_run_writes_nothing(env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "feedback", rid,
                 "--level", "run", "--category", "partly_correct",
                 "--finding", fid, "--verdict", "confirm"]) == EXIT_OK
    assert "dry-run" in capsys.readouterr().out
    from minder_memory import trace_reviews
    assert trace_reviews.list_feedback(rid, db_path=env["db"]) == []


def test_feedback_confirms_a_finding(env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "feedback", rid,
                 "--level", "run", "--category", "evidence_quality",
                 "--finding", fid, "--verdict", "confirm",
                 "--comment", "confirmed", "--yes"]) == EXIT_OK
    from minder_memory import trace_reviews
    assert trace_reviews.finding_verdicts(rid,
                                         db_path=env["db"])[fid] == "confirm"


def test_feedback_rejects_a_finding_from_another_review(env, capsys,
                                                        monkeypatch):
    rid, _fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "feedback", rid,
                 "--level", "run", "--category", "correct",
                 "--finding", "trf_not_in_this_review", "--verdict",
                 "confirm", "--yes"]) == EXIT_USAGE
    assert "is not in review" in capsys.readouterr().err


def test_feedback_rejects_a_category_outside_the_taxonomy(env, capsys,
                                                          monkeypatch):
    """argparse owns the closed vocabulary, so a typo fails as a usage
    error before anything is written."""
    rid, _fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "feedback", rid,
                 "--level", "run", "--category", "looks_fine",
                 "--yes"]) == EXIT_USAGE


# --- regress: the evidence chain ----------------------------------------

def test_regress_refuses_an_unconfirmed_finding(env, capsys, monkeypatch):
    """A finding nobody judged is a hypothesis; promoting it would make an
    evaluator's false positive the standard."""
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid, "--yes"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "not been confirmed" in err
    assert "trace feedback" in err  # tells the operator how to proceed


def test_regress_requires_a_stored_review(env, capsys):
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", "trf_whatever", "--yes"]) == EXIT_USAGE
    assert "no stored review" in capsys.readouterr().err


def test_regress_dry_run_writes_nothing(env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    suite = env["benchmarks"] / "coding-core-v1"
    before = sorted(p.relative_to(suite) for p in suite.rglob("*"))
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid]) == EXIT_OK
    assert "dry-run" in capsys.readouterr().out
    assert sorted(p.relative_to(suite)
                  for p in suite.rglob("*")) == before


def test_regress_writes_a_valid_case_and_leaves_a_clean_manifest(
        env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid, "--yes"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "DRAFT" in out

    manifest = bench.read_manifest("coding-core-v1")
    assert bench.validate_manifest(manifest,
                                   bench.suite_dir("coding-core-v1")) == []
    added = [t for t in manifest["tasks"] if t["task_id"] != "t0"]
    assert len(added) == 1
    task = added[0]
    assert task["expected"] == "tests_pass"
    assert task["family"] == "trace_regression"
    # provenance travels with the case
    assert task["source"]["finding_id"] == fid
    assert task["source"]["review_id"] == rid
    suite = env["benchmarks"] / "coding-core-v1"
    for rel in task["fixtures"]:
        assert (suite / rel).is_file(), rel


def test_regress_refuses_a_non_failure_shaped_finding(env, capsys,
                                                      monkeypatch):
    """Advisory judgements are not converted: encoding 'fewer calls' as a
    test is the prescriptive trap the product avoids."""
    from minder_memory import trace_reviews
    from minder_trace import evaluate, normalize
    monkeypatch.setenv("MINDER_TRACE_REVIEW", "on")
    run, report = normalize.load_session("session-loop")
    findings = evaluate.run_all(run, report, config_overrides={
        "max_tool_calls": 1})
    budget = next(f for f in findings
                  if f["rule_id"] == "tool-call-budget")
    rid, _status = trace_reviews.store_review(run, findings, {},
                                             report=report,
                                             db_path=env["db"])
    trace_reviews.store_feedback(rid, "run", "correct",
                                 finding_id=budget["finding_id"],
                                 finding_verdict="confirm",
                                 db_path=env["db"])
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", budget["finding_id"], "--yes"]) == EXIT_USAGE
    assert "not a failure-shaped finding" in capsys.readouterr().err


def test_regress_refuses_to_add_to_an_already_broken_suite(
        env, capsys, monkeypatch):
    """A suite that is already invalid must be reported as such — blaming
    the new case would be wrong and unactionable."""
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    manifest_path = env["benchmarks"] / "coding-core-v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tasks"][0]["fixtures"] = ["tasks/missing.py"]
    manifest_path.write_text(json.dumps(manifest, indent=2))
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid, "--yes"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "already invalid" in err and "missing.py" in err
    # and the broken manifest is left exactly as the operator had it
    assert json.loads(manifest_path.read_text())["tasks"][0]["fixtures"] \
        == ["tasks/missing.py"]


def test_regress_refuses_a_duplicate_task_id(env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    for _ in range(2):
        main(["--db", str(env["db"]), "trace", "regress", "session-loop",
              "--finding", fid, "--task-id", "tr_fixed", "--yes"])
    capsys.readouterr()
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid, "--task-id", "tr_fixed",
                 "--yes"]) == EXIT_USAGE
    assert "already has a task" in capsys.readouterr().err


def test_regress_unknown_suite_is_usage_error(env, capsys, monkeypatch):
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    assert main(["--db", str(env["db"]), "trace", "regress", "session-loop",
                 "--finding", fid, "--suite", "nope-v9",
                 "--yes"]) == EXIT_USAGE
    assert "unknown suite" in capsys.readouterr().err


def test_generated_case_passes_its_own_test(env, capsys, monkeypatch,
                                            tmp_path):
    """The generated case must be green as written: a case that fails on
    arrival would be committed red."""
    import subprocess
    import sys
    rid, fid = _store_a_review(env, capsys, monkeypatch)
    main(["--db", str(env["db"]), "trace", "feedback", rid,
          "--level", "run", "--category", "evidence_quality",
          "--finding", fid, "--verdict", "confirm", "--yes"])
    capsys.readouterr()
    main(["--db", str(env["db"]), "trace", "regress", "session-loop",
          "--finding", fid, "--task-id", "tr_case", "--yes"])
    capsys.readouterr()
    task_dir = env["benchmarks"] / "coding-core-v1" / "tasks" / "tr_case"
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "test_tr_case.py"],
        cwd=task_dir, capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(repo),
                 MINDER_REPO_ROOT=str(repo)), timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # ...and it asserts the rule both ways, or it is not a regression test
    source = (task_dir / "test_tr_case.py").read_text()
    assert "test_the_recorded_failure_is_detected" in source
    assert "test_the_corrected_behaviour_is_clean" in source
