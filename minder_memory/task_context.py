"""Task-context intake (Phase 1 P1.1, PRD v2 §Domain routing contract).

Explicit declaration first: `declare_task` establishes the authoritative
TaskContext without any model call. One open context per task_id;
re-declaring the same domain reuses it (routine follow-ups never create
rows); a different domain switches — the old context is pinned with a
close reason, a new one opens with an advanced subtask sequence, and an
append-only `domain_transitions` row records the lifecycle. Unknown
domains/origins are rejected, never guessed. `record_human_input`
captures the repo's first human evidence events, modelled on the
frontier consult-trace pattern (redacted text, structured affects).

Every mutating call returns (row|None, status) and fails open, matching
memory-store conventions.
"""
import uuid
from datetime import datetime, timezone

from . import db as _db

DOMAINS = ("coding", "trading_research", "resume_application",
           "cited_research", "mixed")
ORIGINS = ("explicit_user", "harness", "router_validated", "restricted")
HUMAN_KINDS = ("clarify_request", "factual_correction", "wording_approval",
               "intent_choice", "holdout_unlock", "permission",
               "review_decision")
HUMAN_AUTHORITIES = ("user", "operator", "reviewer")
DEFAULT_CONTRACT_VERSION = "domain-route/v1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def find_open_context(task_id, db_path=None):
    """The open context for a task, or None. Never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
                (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def declare_task(domain, *, task_id="default", actor="operator",
                 subtask=None, origin="explicit_user", egress_class=None,
                 required_verifier=None, note=None, db_path=None):
    """Declare (or reuse) the authoritative context for a task."""
    try:
        if domain not in DOMAINS:
            return None, f"rejected:unknown_domain:{domain}"
        if origin not in ORIGINS:
            return None, f"rejected:unknown_origin:{origin}"
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            open_row = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
                (task_id,)).fetchone()
            now = _now()
            if open_row and open_row["domain"] == domain:
                return dict(open_row), "reused"
            seq = (open_row["subtask_seq"] + 1) if open_row else 1
            context_id = _uid("ctx")
            conn.execute(
                "INSERT INTO task_contexts (context_id, task_id, actor,"
                " domain, subtask, subtask_seq, origin, contract_version,"
                " egress_class, required_verifier, opened_at, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (context_id, task_id, actor, domain, subtask, seq, origin,
                 DEFAULT_CONTRACT_VERSION, egress_class, required_verifier,
                 now, note))
            if open_row:
                conn.execute(
                    "UPDATE task_contexts SET closed_at = ?,"
                    " close_reason = 'switched' WHERE context_id = ?",
                    (now, open_row["context_id"]))
                conn.execute(
                    "INSERT INTO domain_transitions (transition_id, ts,"
                    " from_context_id, to_context_id, actor, trigger,"
                    " provenance, validation_result, reason)"
                    " VALUES (?, ?, ?, ?, ?, 'explicit_declare',"
                    " 'declared', 'approved', ?)",
                    (_uid("tr"), now, open_row["context_id"], context_id,
                     actor, note))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM task_contexts WHERE context_id = ?",
                (context_id,)).fetchone()
            return dict(row), ("switched" if open_row else "opened")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail open, caller decides
        return None, f"error:{type(exc).__name__}"


def close_task(*, task_id="default", reason="closed", actor="operator",
               db_path=None):
    """Pin the open context for a task. Returns (row|None, status)."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            open_row = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
                (task_id,)).fetchone()
            if not open_row:
                return None, "rejected:no_open_context"
            conn.execute(
                "UPDATE task_contexts SET closed_at = ?, close_reason = ?"
                " WHERE context_id = ?",
                (_now(), reason, open_row["context_id"]))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM task_contexts WHERE context_id = ?",
                (open_row["context_id"],)).fetchone()
            return dict(row), "closed"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def task_status(task_id="default", db_path=None):
    """Open context (or None) plus the task's recent contexts."""
    try:
        conn = _db.connect(db_path)
        try:
            open_row = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id = ? AND"
                " closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
                (task_id,)).fetchone()
            recent = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id = ?"
                " ORDER BY opened_at DESC LIMIT 10",
                (task_id,)).fetchall()
            return {"open": dict(open_row) if open_row else None,
                    "recent": [dict(r) for r in recent]}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"open": None, "recent": [],
                "status": f"error:{type(exc).__name__}"}


def record_human_input(kind, *, actor, authority="user", decision=None,
                       question=None, answer_summary=None, affects=None,
                       method=None, note=None, db_path=None):
    """Record one auditable human evidence event. A consultation trace,
    not automatically an independent source (PRD §Evidence graph)."""
    try:
        if kind not in HUMAN_KINDS:
            return None, f"rejected:unknown_kind:{kind}"
        if authority not in HUMAN_AUTHORITIES:
            return None, f"rejected:unknown_authority:{authority}"
        import json
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            event_id = _uid("hin")
            conn.execute(
                "INSERT INTO human_input_events (event_id, ts, actor,"
                " authority, kind, question, answer_summary, affects_json,"
                " decision, method, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, _now(), actor, authority, kind, question,
                 answer_summary,
                 json.dumps(affects) if affects else None,
                 decision, method, note))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM human_input_events WHERE event_id = ?",
                (event_id,)).fetchone()
            return dict(row), "ok"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"
