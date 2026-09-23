"""Success-loop guard tests (Phase 2+ backlog item, designed in
docs/success-loop-guard-design.md after the live DBS incident).

Core property: three *successful* tool calls whose outputs differ only
in volatile numbers (curl speeds) collapse to ONE result signature, and
the third observation raises an advisory. Flag law: without
MINDER_SUCCESS_GUARD=advisory nothing is recorded and no advisory is
produced (byte-inert). Fail-open everywhere: a broken store never
raises. Draft usages append; success observations are append-only too.
"""
import json
import sqlite3

import pytest

from memory import db as _db, from_hook, success_guard
from minder_op.cli import EXIT_OK, main

# Verbatim from the live incident (three consecutive curl outputs).
DBS_1 = ("  0  0  0  0  0  0  0  0 --:--:-- --:--:-- --:--:--     0  0  0  0"
         "  0  0  0  0 --:--:-- --:--:-- --:--:--  100 181.2k  0 181.2k  0  0"
         "  0  0  1.24M  0 --:--:-- --:--:-- --:--:--  1.24M")
DBS_2 = ("  0  0  0  0  0  0  0  0 --:--:-- --:--:-- --:--:--     0  0  0  0"
         "  0  0  0  0 --:--:-- --:--:-- --:--:--  100 181.2k  0 181.2k  0  0"
         "  0  0  0  0 905.5k  0  0  0 --:--:-- --:--:-- --:--:--  905.2k")
DBS_3 = ("  0  0  0  0  0  0  0  0 --:--:-- --:--:-- --:--:--     0  0  0  0"
         "  0  0  0  0 --:--:-- --:--:-- --:--:--  100 181.2k  0 181.2k  0  0"
         "  0  0 573.6k  0  0  0  0  0 --:--:-- --:--:-- --:--:--  573.4k")
SECRET = "sk-proj-operatorleak99999999"


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _obs(dbp, out, i=0, sig=None, n=None):
    return success_guard.observe(
        "sess1", "bash", "fp_" + out[:6], 0,
        [DBS_1, DBS_2, DBS_3, DBS_1][i], db_path=dbp)


def test_normalization_collapses_volatile_numbers():
    """The three incident outputs carry different speeds — they must
    normalize to the SAME result signature (that is the whole fix)."""
    s1 = success_guard.result_signature(0, DBS_1)
    s2 = success_guard.result_signature(0, DBS_2)
    s3 = success_guard.result_signature(0, DBS_3)
    assert s1 == s2 == s3
    # a genuinely different result must not collapse
    other = success_guard.result_signature(1, "curl: (22) The requested URL"
                                           " returned error: 404")
    assert other != s1


def test_normalization_redacts_and_caps(tmp_path):
    text = f"token {SECRET} " + "x" * 5000
    normalized = success_guard.normalize_output(text)
    assert SECRET not in normalized
    assert len(normalized) <= 512


def test_observe_trigger_at_n_with_count_text(tmp_path):
    dbp = _mig(tmp_path)
    r1 = _obs(dbp, DBS_1, 0)
    assert r1["advisory"] is None and r1["count"] == 1
    r2 = _obs(dbp, DBS_2, 1)
    assert r2["advisory"] is None and r2["count"] == 2
    r3 = _obs(dbp, DBS_3, 2)
    assert r3["advisory"] is not None
    assert "3 times" in r3["advisory"] and "not advancing" in r3["advisory"]
    r4 = _obs(dbp, DBS_1, 0)  # a fourth repeat: still advisory, count grows
    assert r4["count"] == 4 and r4["advisory"] is not None


def test_window_excludes_old_observations(tmp_path):
    dbp = _mig(tmp_path)
    old = "2026-01-01T00:00:00+00:00"
    success_guard.observe("s", "bash", "fp", 0, DBS_1, ts=old, db_path=dbp)
    success_guard.observe("s", "bash", "fp", 0, DBS_2, ts=old, db_path=dbp)
    r = success_guard.observe("s", "bash", "fp", 0, DBS_3, db_path=dbp)
    assert r["count"] == 1 and r["advisory"] is None  # old ones aged out


def test_different_results_do_not_share_signature(tmp_path):
    dbp = _mig(tmp_path)
    _obs(dbp, DBS_1, 0)
    _obs(dbp, DBS_2, 1)
    r = success_guard.observe("s", "bash", "other_fp", 0, DBS_3, db_path=dbp)
    assert r["count"] == 1  # different fingerprint: independent counter


def test_flag_law_inert_by_default(tmp_path, monkeypatch):
    """Without MINDER_SUCCESS_GUARD=advisory, from_hook.record must not
    write observations (byte-inert behaviour change)."""
    dbp = _mig(tmp_path)
    monkeypatch.delenv("MINDER_SUCCESS_GUARD", raising=False)
    ev = {"event_type": "tool_success", "tool": "bash",
          "tool_response": DBS_1, "session_id": "s1",
          "command": "curl -o x.pdf URL", "cwd": "/repo"}
    out = from_hook.record(ev, db_path=dbp)
    conn = _db.connect(dbp)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM success_observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_flag_on_records_and_advisory_surfaces(tmp_path, monkeypatch):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SUCCESS_GUARD", "advisory")
    ev = {"event_type": "tool_success", "tool": "bash",
          "tool_response": DBS_1, "session_id": "s1",
          "command": "curl -o x.pdf URL", "cwd": "/repo"}
    for i, payload in enumerate((DBS_1, DBS_2, DBS_3)):
        from_hook.record(ev | {"tool_response": payload}, db_path=dbp)
    note = from_hook.success_advisory()
    assert note and "3 times" in note
    # hook emission contract: the note goes to stderr, NEVER to a block
    from hook import emit  # noqa — emit() itself is covered by hook tests


def test_fail_open_on_broken_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_SUCCESS_GUARD", "advisory")
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"junk" * 100)
    r = success_guard.observe("s", "bash", "fp", 0, DBS_1, db_path=bad)
    assert r["advisory"] is None  # degraded, never raised


def test_success_observations_append_only(tmp_path):
    dbp = _mig(tmp_path)
    _obs(dbp, DBS_1, 0)
    conn = _db.connect(dbp)
    with pytest.raises(sqlite3.IntegrityError):
        _db.write(conn, "DELETE FROM success_observations")
    with pytest.raises(sqlite3.IntegrityError):
        _db.write(conn, "UPDATE success_observations SET advisory = 1",
                  ())
    conn.close()


def test_summary_counts_advisories(tmp_path):
    dbp = _mig(tmp_path)
    from datetime import datetime, timezone
    from minder_op.summary import build_weekly_summary
    ts = "2026-09-20T10:00:00+00:00"
    conn = _db.connect(dbp)
    try:
        for i in range(2):
            _db.write(conn, "INSERT INTO success_observations (obs_id, ts,"
                      " session_id, tool, action_fingerprint,"
                      " result_signature, exit_code, excerpt, advisory)"
                      " VALUES (?, ?, 's', 'bash', 'fp', 'sig', 0, '', 1)",
                      (f"o{i}", ts))
    finally:
        conn.close()
    report = build_weekly_summary(
        dbp, days=7, now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
    assert report["domain"]["success_advisories"] == 2


def test_cli_success_loops_ls(tmp_path, monkeypatch, capsys):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SUCCESS_GUARD_N", "1")  # single raise
    _obs(dbp, DBS_1, 0)
    assert main(["--db", str(dbp), "success-loops", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "sess1" in out and "never blocks" in out
