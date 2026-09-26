"""Temporary plans (docs/minder-phase-2-3-frontier-coding.md P2.2).

When no skill matches (or the gap is environmental), minder drafts a
short deterministic procedure — a TEMPORARY PLAN, not a skill. Steps come
from the gap-type table below; no LLM and no frontier dump. A verified
episode never auto-converts a plan into a skill. SKILLS.md is never
written. Every call fails open: plan_id/directive degrade to None/"".
"""
import json
import uuid
from datetime import datetime, timezone

from . import db as _db

DEFAULT_STEPS = {
    "environment": [
        "Check the command exists and its version",
        "Check the working directory and required files",
        "Check environment variables / services / credentials",
        "Do not edit product code for an environmental failure",
    ],
    "permissions": [
        "Stop retrying the failing action",
        "Report the missing permission or credential precisely",
        "Do not invent secrets or escalate privileges",
    ],
    "procedural": [
        "Inspect the relevant file / test / docs",
        "State a root-cause hypothesis",
        "Make the smallest change that tests the hypothesis",
        "Run the targeted tests",
    ],
    "unknown": [
        "Collect evidence before acting",
        "Avoid an unchanged retry",
        "Run the failing test once more only after a new hypothesis",
    ],
}

PLAN_STATUSES = {"open", "executed", "verified", "abandoned"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def default_steps(gap_type):
    return list(DEFAULT_STEPS.get(gap_type, DEFAULT_STEPS["unknown"]))


def create_temp_plan(event, gap_type, steps=None, db_path=None,
                     episode_id=None):
    """Persist a plan for this failure. Returns plan_id or None (fail-open)."""
    try:
        steps = [str(s) for s in (steps or default_steps(gap_type))][:8]
        conn = _db.connect(db_path)
        try:
            plan_id = f"plan_{uuid.uuid4().hex[:12]}"
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO temp_plans (plan_id, episode_id, repo,"
                " failure_key, gap_type, steps_json, status, created_at,"
                " closed_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, NULL)",
                (plan_id, episode_id or event.get("episode_id"),
                 event.get("repo"), event.get("failure_key"),
                 gap_type or "unknown", json.dumps(steps), _now()))
            conn.execute("COMMIT")
            return plan_id
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return None


def mark_plan(plan_id, status, db_path=None):
    """Transition a plan: open | executed | verified | abandoned."""
    if status not in PLAN_STATUSES:
        return False
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE temp_plans SET status = ?,"
                " closed_at = CASE WHEN ? IN ('verified', 'abandoned')"
                " THEN ? ELSE closed_at END WHERE plan_id = ?",
                (status, status, _now(), plan_id))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return False


def plan_directive(plan_id, db_path=None):
    """Compact 'TEMPORARY PLAN' directive text; empty string on failure.
    Never claims to be a verified lesson."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM temp_plans WHERE plan_id = ?",
                (plan_id,)).fetchone()
            if not row:
                return ""
            steps = json.loads(row["steps_json"])
            lines = [f"TEMPORARY PLAN ({row['gap_type']} gap) — not a "
                     f"verified skill, follow until it earns one:"]
            lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
            return "\n".join(lines)
        finally:
            conn.close()
    except Exception:
        return ""


def open_plan_for(event, db_path=None):
    """The open plan for this repo/failure_key, if any."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM temp_plans WHERE status = 'open' AND"
                " (failure_key = ? OR ? IS NULL)"
                " ORDER BY created_at DESC LIMIT 1",
                (event.get("failure_key"),
                 event.get("failure_key"))).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
