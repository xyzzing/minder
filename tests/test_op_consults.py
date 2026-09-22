"""Consults / decisions / export-stats tests (8A).

Labels come from frontier_evals (007) — the 003 INTEGER column is never
read as a classified label. The decisions listing is checked against a
real in-process shadow evaluate (never through hook.py), and the digest
identity assertion from Phase 5.5 is re-checked here.
"""
import os

from memory import (db as _db, from_hook, frontier_traces,
                    policy as memory_policy)
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

HOOK_EVENT = {"session_id": "s-op", "hook_event_name": "PostToolUse",
              "tool_name": "Edit",
              "tool_input": {"file_path": "/repo/app/supplier.py"},
              "tool_response": "KeyError: 'supplier_id'"}


def test_consults_ls_uses_frontier_evals_labels(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    tid = frontier_traces.record_consult(
        {"key": "bash|keyerror|supplier_id|a.py", "attempts": 2,
         "prompt": "fix", "response": "advice",
         "distilled": ["check defaults"]}, db_path=dbp)
    frontier_traces.classify_consult(
        tid, "pass", accepted=["check defaults"], db_path=dbp)
    # the legacy 003 column is left untouched on purpose (integer era)
    conn = _db.connect(dbp)
    conn.execute("UPDATE frontier_traces SET helpfulness = 7"
                 " WHERE trace_id = ?", (tid,))
    conn.close()
    assert main(["--db", str(dbp), "consults", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert tid in out and "helpful" in out and "7" not in out.split(tid)[0]

    assert main(["--db", str(dbp), "consults", "show", tid]) == EXIT_OK
    out = capsys.readouterr().out
    assert "helpful" in out and "hashes only" in out
    assert main(["--db", str(dbp), "consults", "show",
                 "tr_missing"]) == EXIT_USAGE


def test_decisions_empty_table_exits_zero(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    assert main(["--db", str(dbp), "decisions", "ls"]) == EXIT_OK
    assert "(none)" in capsys.readouterr().out


def test_decisions_ls_shadows_real_evaluate(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(memory_policy, "_DECISION_DEBOUNCE", {})
    dbp = tmp_path / "m.sqlite"
    for _ in range(2):
        from_hook.record(HOOK_EVENT, db_path=dbp)
    warden = {"action": "think", "level": 1, "digest": "d"}
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    out_without = memory_policy.evaluate(HOOK_EVENT, warden, db_path=dbp)

    monkeypatch.setenv("MINDER_DECISION", "shadow")
    out_with = memory_policy.evaluate(HOOK_EVENT, warden, db_path=dbp)
    assert out_with == out_without  # digest tests remain byte-identical

    assert main(["--db", str(dbp), "decisions", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "failure-triage" in out and "null" in out
    assert out_without["action"] in out or "defer_warden" in out or \
        "inspect" in out


def test_export_stats_missing_path_exit_usage_no_traceback(tmp_path, capsys):
    code = main(["export-stats",
                 "--path", str(tmp_path / "nothing.jsonl")])
    assert code == EXIT_USAGE
    captured = capsys.readouterr()
    assert "not found" in captured.err
    assert "Traceback" not in captured.err


def test_export_stats_on_real_export(tmp_path, capsys):
    from memory import (lessons as memory_lessons, store as mstore,
                        train_export)
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    for i in range(5):
        ep_id, _ = mstore.open_episode({"repo": "/repo",
                                        "task_id": f"t{i}"}, db_path=dbp)
        mstore.add_attempt(ep_id, {"event_type": "tool_failure",
                                   "tool": "bash", "repo": "/repo",
                                   "failure_key": f"bash|keyerror|s{i}|m.py",
                                   "error_excerpt": "KeyError"},
                           db_path=dbp)
        mstore.close_episode(ep_id, "verified", db_path=dbp)
        lesson, status = memory_lessons.promote_lesson(
            ep_id, f"guard {i}", verification={"tests_passed": True},
            repo="/repo", failure_key=f"bash|keyerror|s{i}|m.py",
            db_path=dbp)
        assert status == "ok"
    out_dir = tmp_path / "exp"
    train_export.export_training_candidates(db_path=dbp, out_dir=out_dir)
    assert main(["export-stats",
                 "--path", str(out_dir / "training_candidates.jsonl")]) \
        == EXIT_OK
    out = capsys.readouterr().out
    assert "count" in out and "held_out_ratio" in out and \
        "redaction_ok" in out
