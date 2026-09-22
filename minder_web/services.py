"""Read-model services for the localhost operator console (8E).

Every number and label comes from the minder_op query/summary layer —
this module issues no SQL of its own and knows no policy. Anything
free-text passes through minder_op.format.safe (redact + truncate)
before it can reach a template; missing DB or optional tables become
"not available" models here, so a route can never 500 on storage.
"""
import os

from minder_op import benchmark as bench
from minder_op import format as fmt
from minder_op import queries
from minder_op.queries import DBError, UNCLASSIFIED
from minder_op.summary import build_weekly_summary

FLAG_VARS = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION")
NOT_AVAILABLE = "not available"
DEFAULT_LIMIT = 25
MAX_LIMIT = 200


def _bounded_limit(limit):
    try:
        value = int(limit or 0)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if value < 1:
        return DEFAULT_LIMIT
    return min(value, MAX_LIMIT)


def _safe_row(row, fields):
    """Copy a query row into a template-safe dict; the listed fields
    are free text and get redact+truncate."""
    out = dict(row)
    for field in fields:
        out[field] = fmt.safe(out.get(field), 160)
    return out


def health(db_path):
    """Local health check — no sensitive data."""
    try:
        return {"status": "ok",
                "schema_version": queries.schema_version(db_path)}
    except DBError:
        return {"status": "degraded", "schema_version": None}


def _try(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DBError:
        return default


def overview(db_path):
    """Overview page model: health, env flags, the shared weekly
    summary object, and the operator focus list (same object as the
    CLI's weekly-summary)."""
    summary = _try(build_weekly_summary, db_path, default=None)
    try:
        status = queries.status(db_path)
        db_ok = True
    except DBError:
        status = None
        db_ok = False
    return {
        "db_ok": db_ok,
        "health": health(db_path),
        "flags": [{"name": var, "value": os.environ.get(var)
                   or "(unset)"} for var in FLAG_VARS],
        "summary": summary,
        "focus": (summary or {}).get("focus", []),
    }


def episodes_page(db_path, limit=DEFAULT_LIMIT):
    rows = _try(queries.episodes, db_path, limit=_bounded_limit(limit),
                default=[]) or []
    return {"rows": [_safe_row(r, ("repo", "task_id")) for r in rows],
            "limit": _bounded_limit(limit)}


def episode_detail(db_path, episode_id):
    episode = _try(queries.episode, db_path, episode_id, default=None)
    if not episode:
        return None
    events = _try(queries.episode_events, db_path, episode_id,
                  default=[]) or []
    return {"episode": _safe_row(episode, ("repo", "task_id")),
            "events": [_safe_row(e, ("failure_key", "payload_json"))
                       for e in events]}


LESSON_STATUSES = ("verified", "candidate", "invalidated")


def lessons_page(db_path, status=None, limit=DEFAULT_LIMIT):
    if status and status not in LESSON_STATUSES:
        status = None
    rows = _try(queries.lessons, db_path, status_filter=status,
                limit=_bounded_limit(limit), default=[]) or []
    return {"rows": [_safe_row(r, ("failure_key", "instruction"))
                     for r in rows],
            "status": status or "verified",
            "limit": _bounded_limit(limit)}


def lesson_detail(db_path, lesson_id):
    row = _try(queries.lesson, db_path, lesson_id, default=None)
    if not row:
        return None
    return {"lesson": _safe_row(row, ("failure_key", "instruction",
                                      "anti_pattern",
                                      "verification_json"))}


def gaps_page(db_path):
    rows = _try(queries.gaps, db_path, status_filter="open",
                default=[]) or []
    return {"rows": [_safe_row(r, ("repo", "failure_key", "sample_error"))
                     for r in rows]}


def consults_page(db_path, limit=DEFAULT_LIMIT):
    rows = _try(queries.consults, db_path,
                limit=_bounded_limit(limit), default=[]) or []
    return {"rows": [_safe_row(r, ("failure_key",
                                   "provider_fingerprint"))
                     for r in rows]}


def consult_detail(db_path, trace_id):
    row = _try(queries.consult, db_path, trace_id, default=None)
    if not row:
        return None
    # hashes and labels only — raw prompt/response has no column and
    # must never appear
    return {"consult": _safe_row(row, ("failure_key", "request_hash",
                                       "response_hash",
                                       "provider_fingerprint",
                                       "accepted_actions_json",
                                       "rejected_actions_json",
                                       "distilled_json"))}


def decisions_page(db_path, limit=DEFAULT_LIMIT):
    rows = _try(queries.decisions, db_path,
                limit=_bounded_limit(limit), default=[]) or []
    return {"rows": [_safe_row(r, ("failure_key",)) for r in rows]}


def benchmarks_page():
    suites = bench.list_suites()
    baselines = bench.list_baselines()
    return {"suites": suites or NOT_AVAILABLE, "baselines": baselines}
