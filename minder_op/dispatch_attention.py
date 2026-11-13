"""The dispatch attention queue (issue #89, stage 3): the FR-O5
approval inbox's dispatch section, backend side.

A pending item is a would_accept trigger no human decision covers yet.
A resolution appends one attributed human_input_events row (the
append-only audit trail; nothing mutates the trigger). The first
terminal decision stands: repeating it is a no-op, contradicting it is
refused with a named code - and the check and the insert run inside
ONE write transaction, so two concurrent resolves cannot both pass the
existence check and append a contradictory pair. The revision digests
the context the operator saw (the trigger plus the current accept
mode), so a config flip between render and resolve invalidates the
displayed approval state instead of approving something that changed
meaning; a caller passing expected_revision must also pass the
accept_mode it rendered with - a default would silently digest a
different meaning.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from minder_memory import db as memdb
from minder_op.dispatch_gate import DECISION_WOULD_ACCEPT
from minder_op.queries import _rows

CHOICE_APPROVE = "approve"
CHOICE_OBSERVE = "observe"
CHOICES = (CHOICE_APPROVE, CHOICE_OBSERVE)

_CHOICE_DECISIONS = {CHOICE_APPROVE: "approved", CHOICE_OBSERVE: "deferred"}
_TERMINAL_DECISIONS = ("approved", "rejected", "deferred")

_DEFAULT_QUEUE_LIMIT = 50


def item_revision(trigger_row, accept_mode):
    """Stable digest of what an approval decides: the trigger and the
    accept mode it would dispatch under."""
    material = "|".join(str(trigger_row.get(key) or "")
                        for key in ("trigger_id", "sender", "kind"))
    material += f"|{accept_mode}"
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _dispatch_decisions(conn):
    """{dispatch_trigger: terminal decision} from the audit rows on an
    open connection - one scan, shared by the queue and the resolve
    check (a single parse loop, not two)."""
    found = {}
    rows = conn.execute(
        "SELECT affects_json, decision FROM human_input_events"
        " WHERE decision IS NOT NULL"
        " AND affects_json LIKE '%dispatch_trigger%'").fetchall()
    for row in rows:
        if row["decision"] not in _TERMINAL_DECISIONS:
            continue
        try:
            affects = json.loads(row["affects_json"] or "null")
        except ValueError:
            continue
        if not isinstance(affects, list):
            continue
        for entry in affects:
            if isinstance(entry, dict) and entry.get("dispatch_trigger"):
                found[str(entry["dispatch_trigger"])] = row["decision"]
    return found


def pending_items(db_path, accept_mode, limit=_DEFAULT_QUEUE_LIMIT):
    """Unresolved would_accept triggers, newest first, each carrying the
    revision the approval binds to; display is capped, the count is
    not (see unresolved_count)."""
    resolved = _dispatch_decisions(memdb.connect(db_path))
    rows = _rows(db_path, "SELECT * FROM dispatch_triggers"
                          " WHERE decision = ?"
                          " ORDER BY ts DESC, trigger_id DESC LIMIT ?",
                 (DECISION_WOULD_ACCEPT, max(1, int(limit))))
    items = []
    for row in rows:
        if row["trigger_id"] in resolved:
            continue
        items.append({"trigger_id": row["trigger_id"], "ts": row["ts"],
                      "repo": row["repo"], "kind": row["kind"],
                      "sender": row["sender"],
                      "sender_class": row["sender_class"],
                      "revision": item_revision(row, accept_mode)})
    return items


def unresolved_count(db_path):
    """Exact pending total, independent of the display cap: every
    would_accept trigger id minus the ones a terminal decision
    covers."""
    conn = memdb.connect(db_path)
    try:
        resolved = _dispatch_decisions(conn)
        rows = conn.execute(
            "SELECT trigger_id FROM dispatch_triggers"
            " WHERE decision = ?", (DECISION_WOULD_ACCEPT,)).fetchall()
    finally:
        conn.close()
    return sum(1 for row in rows if row["trigger_id"] not in resolved)


def _trigger_row(conn, trigger_id):
    row = conn.execute(
        "SELECT * FROM dispatch_triggers WHERE trigger_id = ?",
        (trigger_id,)).fetchone()
    return dict(row) if row is not None else None


def resolve(db_path, trigger_id, choice, actor, expected_revision=None,
            accept_mode=None):
    """Resolve one pending item: (ok, code).

    ok is True for a recorded resolution and for an idempotent repeat
    of the same one; every refusal carries a stable code. The
    existence check and the audit insert share one BEGIN IMMEDIATE, so
    interleaved resolves serialize and the first terminal decision
    stands even under a double-click.
    """
    if choice not in CHOICES:
        return False, "unknown_choice"
    if expected_revision is not None and accept_mode is None:
        # a revision without the mode it was rendered under digests a
        # meaning the caller never saw
        return False, "accept_mode_required"
    decision = _CHOICE_DECISIONS[choice]
    try:
        with memdb.transaction(db_path) as conn:
            row = _trigger_row(conn, trigger_id)
            if row is None:
                return False, "unknown_trigger"
            if row["decision"] != DECISION_WOULD_ACCEPT:
                return False, "not_pending"
            existing = _dispatch_decisions(conn).get(trigger_id)
            if existing is not None:
                if existing == decision:
                    return True, "already_resolved"
                return False, "already_resolved_differently"
            if expected_revision is not None:
                if expected_revision != item_revision(row, accept_mode):
                    return False, "revision_invalidated"
            conn.execute(
                "INSERT INTO human_input_events"
                " (event_id, ts, actor, authority, kind, question,"
                " answer_summary, affects_json, decision, method, note)"
                " VALUES (?, ?, ?, 'user', 'permission', ?, NULL, ?,"
                " ?, NULL, ?)",
                (f"hin_{uuid.uuid4().hex[:12]}",
                 datetime.now(timezone.utc).isoformat(), actor,
                 f"dispatch approval: {trigger_id}",
                 json.dumps([{"dispatch_trigger": trigger_id}]),
                 decision, f"dispatch inbox {choice}"))
    except Exception as exc:  # noqa: BLE001 - named failure, not a raise
        return False, f"record_failed:{type(exc).__name__}"
    return True, "ok"
