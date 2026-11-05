"""The work-order contract at intake (FR-O1, issue #84).

One work order per task: task, acceptance_criterion, room_family under
windtunnel.work-order/v1 - the quality plan and the reward definition
(v0.11 amendment). The shape is owned by windtunnel (FR-W1); minder
imports no bird, so this module carries its own validator pinned to the
mirrored golden fixture, and the mews tripwire cross-checks the two.

Intake law (SCHEMAS-room.md section 12): task_id is the stable key;
criterion and family are immutable while a work order is open. A live
contract is never rewritten - same task_id with identical text reuses
the open row, changed text is refused, and a new revision (with new
text) requires closing the old one first. Blank contract text is
refused: a work order with no criterion is a hollow promise, and
recording one would make FR-O1's promise ungradeable later.

Every mutating call returns (row|None, status) and fails open, matching
memory-store conventions. Attempt grading against the criterion is a
named later seam; nothing here grades.
"""
import uuid
from datetime import datetime, timezone

from . import db as _db

CONTRACT_VERSION = "windtunnel.work-order/v1"

# SCHEMAS-room.md section 12, mirrored in tests/fixtures/schemas/
# work-order.json (rule 4). windtunnel owns the shape; this pin is
# minder's copy, cross-checked by the mews tripwire.
REQUIRED_KEYS = ("id", "task", "acceptance_criterion", "room_family")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def validate_work_order(doc):
    """Validate one work-order document against the mirrored v1 shape.
    Returns a list of error strings; an empty list means valid. Same
    strictness as the windtunnel validator: required keys strict,
    unknown extra keys tolerated - plus the intake rule that contract
    text may not be blank."""
    if not isinstance(doc, dict):
        return ["document is not a JSON object"]
    if doc.get("schema") != CONTRACT_VERSION:
        return [f"unknown or unsupported schema: {doc.get('schema')!r}"]
    errors = [f"missing key: {key}" for key in REQUIRED_KEYS
              if key not in doc]
    for key in ("task", "acceptance_criterion", "room_family"):
        value = doc.get(key)
        if key in doc and (not isinstance(value, str)
                           or not value.strip()):
            errors.append(f"{key} must be a non-empty string")
    return errors


def declare_work_order(task, *, acceptance_criterion, room_family,
                       task_id="default", kind=None, actor="operator",
                       db_path=None):
    """Declare (or reuse) the open work order for a task."""
    doc = {"schema": CONTRACT_VERSION, "id": task_id, "task": task,
           "acceptance_criterion": acceptance_criterion,
           "room_family": room_family}
    if kind:
        doc["kind"] = kind
    errors = validate_work_order(doc)
    if errors:
        return None, "rejected:" + ";".join(
            e.replace(" ", "_") for e in errors)
    try:
        with _db.transaction(db_path) as conn:
            open_row = conn.execute(
                "SELECT * FROM work_orders WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY declared_at DESC LIMIT 1",
                (task_id,)).fetchone()
            if open_row:
                same = (open_row["task"] == task
                        and open_row["acceptance_criterion"]
                        == acceptance_criterion
                        and open_row["room_family"] == room_family)
                if same:
                    return dict(open_row), "reused"
                if open_row["acceptance_criterion"] != acceptance_criterion:
                    return None, (
                        "rejected:criterion_immutable:close the open work"
                        " order and declare a new revision, or use a new"
                        " task id")
                return None, (
                    "rejected:family_immutable:close the open work order"
                    " and declare a new revision, or use a new task id")
            max_rev = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM work_orders"
                " WHERE task_id = ?", (task_id,)).fetchone()[0]
            wo_id = _uid("wo")
            conn.execute(
                "INSERT INTO work_orders (wo_id, task_id, revision, task,"
                " acceptance_criterion, room_family, kind,"
                " contract_version, actor, declared_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (wo_id, task_id, max_rev + 1, task, acceptance_criterion,
                 room_family, kind, CONTRACT_VERSION, actor, _now()))
            row = conn.execute(
                "SELECT * FROM work_orders WHERE wo_id = ?",
                (wo_id,)).fetchone()
            return dict(row), "declared"
    except Exception as exc:  # noqa: BLE001 - fail open, caller decides
        return None, f"error:{type(exc).__name__}"


def get_work_order(task_id="default", db_path=None):
    """The latest work order for a task (open preferred), or None."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM work_orders WHERE task_id = ?"
                " ORDER BY closed_at IS NOT NULL, declared_at DESC"
                " LIMIT 1", (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def close_work_order(*, task_id="default", reason="closed",
                     actor="operator", db_path=None):
    """Pin the open work order for a task. Returns (row|None, status)."""
    try:
        with _db.transaction(db_path) as conn:
            open_row = conn.execute(
                "SELECT * FROM work_orders WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY declared_at DESC LIMIT 1",
                (task_id,)).fetchone()
            if not open_row:
                return None, "rejected:no_open_work_order"
            conn.execute(
                "UPDATE work_orders SET closed_at = ?, close_reason = ?"
                " WHERE wo_id = ?",
                (_now(), reason, open_row["wo_id"]))
            row = conn.execute(
                "SELECT * FROM work_orders WHERE wo_id = ?",
                (open_row["wo_id"],)).fetchone()
            return dict(row), "closed"
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"
