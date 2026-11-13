"""The dispatch trigger store (issue #89, stage 1).

Writes through minder_memory/db.py (the sanctioned path), reads through
the queries helpers, the same split lesson_queries made. Idempotent on
the stable trigger id: INSERT OR IGNORE, so a poll replayed after a
restart records nothing new. The table itself is append-only by
trigger (migration 020): what the gate said about a trigger is a fact
about the moment it said it.
"""
from minder_memory import db as memdb
from minder_op.dispatch_gate import (DECISION_ADMITTED,
                                     DECISION_FILTERED,
                                     DECISION_WOULD_ACCEPT)
from minder_op.queries import _rows

_INSERT = ("INSERT OR IGNORE INTO dispatch_triggers"
           " (trigger_id, ts, source, repo, kind, sender, sender_class,"
           " decision, reason, work_order_id)"
           " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)")


def record_trigger(db_path, trigger, gate_result):
    """True when this call inserted the row, False when the trigger id
    was already recorded (the replay case)."""
    conn = memdb.connect(db_path)
    try:
        cur = memdb.write(conn, _INSERT, (
            trigger["trigger_id"], trigger["ts"], trigger["source"],
            trigger["repo"], trigger["kind"], trigger.get("sender"),
            gate_result["sender_class"], gate_result["decision"],
            gate_result.get("reason")))
        return cur.rowcount == 1
    finally:
        conn.close()


def triggers(db_path, limit=50, decision=None):
    # SQLite reads a negative LIMIT as no limit; a negative --limit must
    # not silently turn into "everything"
    limit = max(1, int(limit))
    sql = "SELECT * FROM dispatch_triggers"
    params = []
    if decision:
        sql += " WHERE decision = ?"
        params.append(decision)
    sql += " ORDER BY ts DESC, trigger_id DESC LIMIT ?"
    params.append(int(limit))
    return _rows(db_path, sql, params)


def counts(db_path):
    """New-recorded totals per decision; zeroed keys, never missing."""
    rows = _rows(db_path, "SELECT decision, COUNT(*) AS n"
                          " FROM dispatch_triggers GROUP BY decision")
    found = {r["decision"]: r["n"] for r in rows}
    return {DECISION_ADMITTED: found.get(DECISION_ADMITTED, 0),
            DECISION_WOULD_ACCEPT: found.get(DECISION_WOULD_ACCEPT, 0),
            DECISION_FILTERED: found.get(DECISION_FILTERED, 0)}


def newest_ts(db_path):
    rows = _rows(db_path,
                 "SELECT MAX(ts) AS newest FROM dispatch_triggers")
    return rows[0]["newest"] if rows else None
