"""Domain-layer counts for the weekly summary (Phase 1).

Its own module because the group reads six tables the rest of the summary
never touches, and `summary.py` had grown to hold both. The degradation
rule is the one every summary section follows: a store predating
migration 011 has no route tables, so the section says it is not
available rather than reporting zeros an operator would read as "nothing
was routed".

Read-only. The report object is shared by `minder-op weekly-summary` and
the web overview page, so a number here appears in both.
"""
from datetime import datetime, timedelta

from minder_op.queries import _count


def domain_section(db_path, win, now):
    """Domain-layer counts (Phase 1). Degrades to zeros + available
    False on stores predating migration 011 — the read-only summary
    never migrates a database."""
    start, until = win
    try:
        declared = _count(
            db_path, "SELECT COUNT(*) AS n FROM task_contexts"
            " WHERE opened_at >= ? AND opened_at < ?", (start, until))
        traces = _count(
            db_path, "SELECT COUNT(*) AS n FROM route_traces"
            " WHERE ts >= ? AND ts < ?", (start, until))
        agreements = _count(
            db_path, "SELECT COUNT(*) AS n FROM route_traces"
            " WHERE ts >= ? AND ts < ? AND provenance = 'declared'"
            " AND validation_result = 'approved'"
            " AND candidate_domain = declared_domain", (start, until))
        abstentions = _count(
            db_path, "SELECT COUNT(*) AS n FROM route_traces"
            " WHERE ts >= ? AND ts < ? AND abstained = 1", (start, until))
        expiring_horizon = (datetime.fromisoformat(until)
                            + timedelta(days=7)).isoformat()
        expiring = _count(
            db_path, "SELECT COUNT(*) AS n FROM application_intents"
            " WHERE status = 'active' AND expires_at >= ?"
            " AND expires_at < ?",
            (until, expiring_horizon))
        advisories = _count(
            db_path, "SELECT COUNT(*) AS n FROM success_observations"
            " WHERE advisory = 1 AND ts >= ? AND ts < ?", (start, until))
        return {"available": True, "declared_boundaries": declared,
                "route_traces": traces, "route_agreements": agreements,
                "route_abstentions": abstentions,
                "resume_intents_expiring_7d": expiring,
                "success_advisories": advisories}
    except Exception:  # noqa: BLE001 — pre-011 store: degrade, don't crash
        return {"available": False, "declared_boundaries": 0,
                "route_traces": 0, "route_agreements": 0,
                "route_abstentions": 0, "resume_intents_expiring_7d": 0,
                "success_advisories": 0}
