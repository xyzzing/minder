"""Task-context intake tests (Phase 1 P1.1): explicit declaration first,
no model calls, closed domain vocabulary, reuse-vs-switch lifecycle,
auditable transitions, and human-input events (the first captured human
evidence events in the repo)."""
import sqlite3

import pytest

from memory import db as _db, task_context
from minder_op.cli import EXIT_OK, EXIT_USAGE, main


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_declare_open_reuse_switch(tmp_path):
    dbp = _mig(tmp_path)
    row, status = task_context.declare_task(
        "coding", task_id="t1", db_path=dbp)
    assert status == "opened" and row["domain"] == "coding"
    # routine follow-up reuses the context — no new rows
    row2, status2 = task_context.declare_task(
        "coding", task_id="t1", db_path=dbp)
    assert status2 == "reused" and row2["context_id"] == row["context_id"]
    assert len(_rows(dbp, "SELECT * FROM task_contexts")) == 1
    # a different domain is a switch: old pinned, new open, transition row
    row3, status3 = task_context.declare_task(
        "trading_research", task_id="t1", db_path=dbp)
    assert status3 == "switched"
    ctxs = _rows(dbp, "SELECT * FROM task_contexts ORDER BY opened_at")
    assert ctxs[-1]["domain"] == "trading_research"
    assert ctxs[0]["closed_at"] and ctxs[0]["close_reason"] == "switched"
    trans = _rows(dbp, "SELECT * FROM domain_transitions")
    assert len(trans) == 1
    assert trans[0]["from_context_id"] == row["context_id"]
    assert trans[0]["to_context_id"] == row3["context_id"]
    assert trans[0]["provenance"] == "declared"
    # subtask sequence advances across the switch
    assert row3["subtask_seq"] == row["subtask_seq"] + 1


def test_declare_unknown_domain_rejected(tmp_path):
    dbp = _mig(tmp_path)
    row, status = task_context.declare_task(
        "daytrading", task_id="t1", db_path=dbp)
    assert row is None and "unknown_domain" in status
    assert _rows(dbp, "SELECT * FROM task_contexts") == []


def test_close_and_redeclare(tmp_path):
    dbp = _mig(tmp_path)
    row, _ = task_context.declare_task("coding", task_id="t1", db_path=dbp)
    closed, status = task_context.close_task(task_id="t1", db_path=dbp,
                                             reason="done")
    assert status == "closed" and closed["closed_at"]
    open_rows = _rows(dbp, "SELECT * FROM task_contexts"
                      " WHERE closed_at IS NULL")
    assert open_rows == []
    row2, status2 = task_context.declare_task("cited_research",
                                              task_id="t1", db_path=dbp)
    assert status2 == "opened" and row2["domain"] == "cited_research"


def test_human_input_events(tmp_path):
    dbp = _mig(tmp_path)
    event, status = task_context.record_human_input(
        "holdout_unlock", actor="zac", decision="approved",
        question="unlock holdout for fam_x?",
        affects=[{"type": "hypothesis_family", "id": "fam_x"}],
        db_path=dbp)
    assert status == "ok" and event["kind"] == "holdout_unlock"
    rows = _rows(dbp, "SELECT * FROM human_input_events")
    assert len(rows) == 1 and rows[0]["authority"] == "user"
    assert "fam_x" in rows[0]["affects_json"]
    bad, status2 = task_context.record_human_input(
        "magic_answer", actor="zac", db_path=dbp)
    assert bad is None and "unknown_kind" in status2


def test_task_cli_declare_status_close(tmp_path, capsys):
    dbp = _mig(tmp_path)
    assert main(["--db", str(dbp), "task", "declare", "--domain",
                 "coding", "--task", "t9"]) == EXIT_OK
    assert main(["--db", str(dbp), "task", "status",
                 "--task", "t9"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "coding" in out and "t9" in out
    assert main(["--db", str(dbp), "task", "close", "--task", "t9",
                 "--reason", "done"]) == EXIT_OK
    assert main(["--db", str(dbp), "task", "status",
                 "--task", "t9"]) == EXIT_OK
    assert "(none)" in capsys.readouterr().out
    assert main(["--db", str(dbp), "task", "declare", "--domain",
                 "astrology"]) == EXIT_USAGE
    assert "unknown_domain" in capsys.readouterr().err


def test_task_contexts_append_only_transitions(tmp_path):
    dbp = _mig(tmp_path)
    task_context.declare_task("coding", task_id="t1", db_path=dbp)
    task_context.declare_task("cited_research", task_id="t1", db_path=dbp)
    conn = _db.connect(dbp)
    with pytest.raises(sqlite3.IntegrityError):
        _db.write(conn, "UPDATE domain_transitions SET actor = 'x'")
    conn.close()
