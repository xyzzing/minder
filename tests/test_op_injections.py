"""Injection ledger read surfaces (issue #13).

The ledger is only worth having if an operator can see it. Two surfaces:
`minder-op lessons show` names whether the lesson is still firing, and
the weekly summary reports injections against the decisions that had no
lesson to offer. Both must degrade honestly on a store without the
ledger - a missing table is `not available`, never a zero that reads as
"unused".
"""
from datetime import datetime, timezone

import webseed
from minder_memory import db as _db
from minder_op.cli import EXIT_OK, main
from minder_op.summary import build_weekly_summary, render_text

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
TS_IN = "2026-10-07T09:00:00+00:00"
TS_OLD = "2026-09-01T09:00:00+00:00"


def _inj(conn, inj_id, ts, lesson_id, tier="exact", chars=120,
         mode="block_duplicate", failure_key=webseed.HOSTILE):
    """failure_key carries the hostile marker so the read side is asserted
    to redact it, not assumed to."""
    _db.write(conn, "INSERT INTO learning_injections (injection_id, ts,"
              " session_id, event_id, failure_key, repo, lesson_id, tier,"
              " trigger_matched, digest_injected, chars_injected,"
              " assist_mode, redaction_status)"
              " VALUES (?, ?, 's1', 'ev1', ?, '/repo', ?, ?, NULL, 1, ?,"
              " ?, 'redacted')",
              (inj_id, ts, failure_key, lesson_id, tier, chars, mode))


def test_lesson_show_lists_injections_with_redaction(tmp_path, capsys):
    dbp, conn = _mig(tmp_path)
    try:
        webseed.add_lesson(dbp, lesson_id="les1")
        _inj(conn, "inj1", TS_IN, "les1")
        _inj(conn, "inj2", TS_OLD, "les1", tier="family", chars=88)
    finally:
        conn.close()
    assert main(["--db", str(dbp), "lessons", "show", "les1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "injections" in out
    # both rows, newest first, with the tier that produced each
    assert out.index("2026-10-07") < out.index("2026-09-01")
    assert "family" in out and "exact" in out
    assert "120" in out and "88" in out
    assert webseed.HOSTILE not in out  # failure_key is redacted+escaped


def test_weekly_summary_reports_injections_and_misses(tmp_path, capsys):
    dbp, conn = _mig(tmp_path)
    try:
        webseed.add_lesson(dbp, lesson_id="les_used")
        webseed.add_lesson(dbp, lesson_id="les_unused")
        _inj(conn, "inj1", TS_IN, "les_used")
        _inj(conn, "inj2", TS_OLD, "les_used")
        _inj(conn, "inj3", TS_IN, None, tier="n/a", chars=0,
             mode="retrieve")
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    inj = report["injections"]
    assert inj["available"] is True
    assert inj["injected_in_window"] == 1
    assert inj["injected_total"] == 2
    assert inj["missed_total"] == 1
    # les_unused is live and verified but never reached an agent
    assert inj["unused_verified"] == 1

    render_text(report)
    text = capsys.readouterr().out
    assert "lesson injections" in text
    assert "verified lessons never injected" in text


def test_uninjected_lesson_reaches_operator_focus(tmp_path):
    """The point of the ledger: a lesson nobody uses becomes an action,
    not a silent row."""
    dbp, conn = _mig(tmp_path)
    try:
        webseed.add_lesson(dbp, lesson_id="les_unused")
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert any("never injected" in f for f in report["focus"])


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    conn = _db.connect(dbp)
    return dbp, conn
