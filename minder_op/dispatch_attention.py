"""The dispatch attention queue (issue #89, stage 3): the FR-O5
approval inbox's dispatch section, backend side.

A pending item is a would_accept trigger no human decision covers yet.
A resolution appends one attributed human_input_events row (the
append-only audit trail; nothing mutates the trigger). The first
terminal decision stands: repeating it is a no-op, contradicting it is
refused with a named code. The revision digests the context the
operator saw (the trigger plus the current accept mode), so a config
flip between render and resolve invalidates the displayed approval
state instead of approving something that changed meaning.
"""
import hashlib
import json

from minder_memory import task_context
from minder_op.dispatch_gate import DECISION_WOULD_ACCEPT
from minder_op.queries import _rows

CHOICE_APPROVE = "approve"
CHOICE_OBSERVE = "observe"
CHOICES = (CHOICE_APPROVE, CHOICE_OBSERVE)

_CHOICE_DECISIONS = {CHOICE_APPROVE: "approved", CHOICE_OBSERVE: "deferred"}
_TERMINAL_DECISIONS = ("approved", "rejected", "deferred")


def item_revision(trigger_row, accept_mode):
    """Stable digest of what an approval decides: the trigger and the
    accept mode it would dispatch under."""
    material = "|".join(str(trigger_row.get(key) or "")
                        for key in ("trigger_id", "sender", "kind"))
    material += f"|{accept_mode}"
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def _resolved_trigger_ids(db_path):
    """Trigger ids a terminal human decision already covers."""
    rows = _rows(db_path, "SELECT affects_json, decision"
                          " FROM human_input_events"
                          " WHERE decision IS NOT NULL")
    resolved = set()
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
                resolved.add(str(entry["dispatch_trigger"]))
    return resolved


def pending_items(db_path, accept_mode):
    """Unresolved would_accept triggers, newest first, each carrying the
    revision the approval binds to."""
    rows = _rows(db_path, "SELECT * FROM dispatch_triggers"
                          " WHERE decision = ?"
                          " ORDER BY ts DESC, trigger_id DESC",
                 (DECISION_WOULD_ACCEPT,))
    resolved = _resolved_trigger_ids(db_path)
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


def unresolved_count(db_path, accept_mode):
    return len(pending_items(db_path, accept_mode))


def _trigger_row(db_path, trigger_id):
    rows = _rows(db_path, "SELECT * FROM dispatch_triggers"
                          " WHERE trigger_id = ?", (trigger_id,))
    return rows[0] if rows else None


def _decision_for(db_path, trigger_id):
    """The terminal decision already on record for one trigger."""
    rows = _rows(db_path, "SELECT affects_json, decision"
                          " FROM human_input_events"
                          " WHERE decision IS NOT NULL")
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
            if (isinstance(entry, dict)
                    and str(entry.get("dispatch_trigger") or "")
                    == trigger_id):
                return row["decision"]
    return None


def resolve(db_path, trigger_id, choice, actor, expected_revision=None,
            accept_mode=None):
    """Resolve one pending item: (ok, code).

    ok is True for a recorded resolution and for an idempotent repeat
    of the same one; every refusal carries a stable code.
    """
    if choice not in CHOICES:
        return False, "unknown_choice"
    row = _trigger_row(db_path, trigger_id)
    if row is None:
        return False, "unknown_trigger"
    if row["decision"] != DECISION_WOULD_ACCEPT:
        return False, "not_pending"
    existing = _decision_for(db_path, trigger_id)
    if existing is not None:
        if existing == _CHOICE_DECISIONS[choice]:
            return True, "already_resolved"
        return False, "already_resolved_differently"
    if expected_revision is not None:
        current = item_revision(row, accept_mode
                                or "observe")
        if expected_revision != current:
            return False, "revision_invalidated"
    decision = _CHOICE_DECISIONS[choice]
    _row, status = task_context.record_human_input(
        "permission", actor=actor, decision=decision,
        question=f"dispatch approval: {trigger_id}",
        affects=[{"dispatch_trigger": trigger_id}],
        note=f"dispatch inbox {choice}", db_path=db_path)
    if status != "ok":
        return False, f"record_failed:{status}"
    return True, "ok"
