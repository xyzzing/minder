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
    summary object, the capture verdict, and the operator focus list
    (same object as the CLI's weekly-summary)."""
    summary = _try(build_weekly_summary, db_path, default=None)
    try:
        status = queries.status(db_path)
        db_ok = True
    except DBError:
        status = None
        db_ok = False
    capture = capture_page(db_path)
    # Capture breaks are the one failure that invalidates every other page,
    # so they come first in the operator focus list.
    focus = list((summary or {}).get("focus", []))
    if not capture["ok"]:
        focus = capture["warnings"][:2] + focus
    return {
        "db_ok": db_ok,
        "health": health(db_path),
        "flags": [{"name": var, "value": os.environ.get(var)
                   or "(unset)"} for var in FLAG_VARS],
        "summary": summary,
        "capture": capture,
        "focus": focus[:3],
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


# Difficulty-router events live in the proxy's raw audit ledger
# (events.jsonl), not in memory.sqlite — the console reads that file
# directly, read-only, like the CLI does.
_DIFFICULTY_EVENTS = ("difficulty_shadow", "difficulty_routed")


def difficulty_page(limit=DEFAULT_LIMIT):
    """Laya difficulty-router events from the proxy ledger, newest
    first. Shadow rows are observations only (no request change);
    routed rows show the band actually applied. Missing or unreadable
    ledger becomes an empty model — the route never 500s."""
    import json as _json
    import os as _os
    from pathlib import Path as _Path
    # Resolved per call (env overridable) so tests and systemd units can
    # point the same console at a different ledger — mirrors how the DB
    # path is resolved per request.
    state_dir = _Path(_os.environ.get(
        "MINDER_STATE_DIR",
        _os.path.expanduser("~/.local/state/minder")))
    path = state_dir / "events.jsonl"
    rows = []
    if path.exists():
        try:
            with path.open("rb") as fh:
                for line in fh.read().splitlines():
                    if not line.strip():
                        continue
                    try:
                        ev = _json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("event") not in _DIFFICULTY_EVENTS:
                        continue
                    rows.append(ev)
        except OSError:
            rows = []
    # newest first (ledger is append-only, ts ascending)
    rows.reverse()
    total = len(rows)
    rows = rows[:_bounded_limit(limit)]
    # counts by label for the summary strip
    by_label = {}
    for ev in rows:
        label = ev.get("label") or "?"
        by_label[label] = by_label.get(label, 0) + 1
    return {"rows": rows, "limit": _bounded_limit(limit),
            "total": total, "by_label": by_label}


def events_page(db_path, limit=DEFAULT_LIMIT):
    """Raw observed events, newest first. The episode column comes from
    a subquery so unlinked events still list."""
    rows = _try(queries.events, db_path,
                limit=_bounded_limit(limit), default=[]) or []
    return {"rows": [_safe_row(r, ("failure_key", "error_excerpt",
                                    "payload_json")) for r in rows],
            "limit": _bounded_limit(limit)}


def sessions_page(db_path, query=None, sort=None, limit=200,
                  dsh_root=None):
    """Dsh sessions joined with the projection cache, the workspace
    registry and the memory DB. Read-only; no writes to dsh state."""
    from pathlib import Path as _Path

    from minder_op import dsh_sessions
    linkage = _try(queries.session_linkage, db_path, default={}) or {}
    model = dsh_sessions.list_sessions(root=dsh_root, db_counts=linkage)
    rows = model["rows"]
    if query:
        needle = str(query).lower()
        rows = [r for r in rows
                if needle in (r.get("session_id") or "").lower()
                or needle in (r.get("project_path") or "").lower()
                or needle in (r.get("project_title") or "").lower()
                or needle in (r.get("title") or "").lower()]
    sort_key = {
        "recent": lambda r: r.get("mtime") or 0,
        "project": lambda r: (r.get("project_title") or "",
                              -(r.get("mtime") or 0)),
        "tokens": lambda r: r.get("tokens_total") or 0,
        "steps": lambda r: r.get("steps") or 0,
        "capture": lambda r: (r.get("event_count") or 0),
    }
    if sort in sort_key:
        rows = sorted(rows, key=sort_key[sort],
                      reverse=(sort != "project"))
    total = len(rows)
    rows = rows[:_bounded_limit(limit)]
    for row in rows:
        row["age"] = _age_text(row.get("age_s"))
        row["capture_ok"] = (row.get("event_count") or 0) > 0
    return {"rows": rows, "total": total,
            "counts": model["counts"], "query": query or "",
            "sort": sort or "recent",
            "limit": _bounded_limit(limit),
            "sorts": ("recent", "project", "tokens", "steps", "capture")}


def session_detail_page(db_path, session_id, dsh_root=None):
    """One session: projections, log-derived counters, DB linkage."""
    from minder_op import dsh_sessions
    linkage = _try(queries.session_linkage, db_path, default={}) or {}
    detail = dsh_sessions.session_detail(session_id, root=dsh_root,
                                         db_counts=linkage)
    if detail is None:
        return None
    detail["age"] = _age_text(detail.get("age_s"))
    detail["events"] = []
    episodes = []
    for episode_id in detail.get("episode_ids") or []:
        episode = _try(queries.episode, db_path, episode_id, default=None)
        if episode:
            episodes.append(_safe_row(episode, ("repo", "task_id")))
    detail["episodes"] = episodes
    rows = _try(queries.events, db_path, session_id=session_id,
                limit=MAX_LIMIT, default=[]) or []
    detail["events"] = [_safe_row(r, ("failure_key", "error_excerpt",
                                      "payload_json")) for r in rows][:50]
    detail["capture_gap"] = (bool(detail.get("hook_invocations"))
                             and not (detail.get("event_count") or 0))
    return detail


def _age_text(age_s):
    if age_s is None:
        return "unknown"
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return "unknown"
    if age < 90:
        return f"{int(age)}s ago"
    if age < 48 * 3600:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def capture_page(db_path, dsh_root=None):
    """Capture health: is the watchdog actually persisting anything?"""
    from minder_op import capture
    report = capture.build(db_path, dsh_root=dsh_root)
    for store in report["stores"]:
        store["age"] = capture.age_text(store.get("age_s"))
    for session in report["coverage"].get("sessions") or []:
        session["short"] = str(session.get("session_id") or "")[8:20]
    return report


def scorecard_page(db_path, window_hours=24, dsh_root=None):
    """The improvement scorecard as a page model."""
    from minder_op import scorecard
    report = scorecard.build(db_path, dsh_root=dsh_root,
                             window_hours=window_hours)
    report["focus"] = scorecard._focus(report)
    return report


def events_page(db_path, limit=DEFAULT_LIMIT, event_type=None, tool=None,
                failure_key=None, session=None):
    """Raw observed events, newest first, with the filter controls the
    operator needs to answer "what failed, where, how often"."""
    rows = _try(queries.events, db_path, failure_key=failure_key,
                event_type=event_type, tool=tool, limit=_bounded_limit(limit),
                default=[]) or []
    if session:
        rows = [r for r in rows
                if session in str(r.get("session_id") or "")]
    filters = _try(queries.event_filter_values, db_path, default={}) or {}
    last = _try(queries.last_event_ts, db_path, default=None)
    stale_days = None
    if last:
        try:
            from datetime import datetime, timezone
            parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - parsed).total_seconds()
            if age > 86400:
                stale_days = int(age // 86400)
        except (TypeError, ValueError):
            stale_days = None
    return {"rows": [_safe_row(r, ("failure_key", "error_excerpt",
                                   "payload_json")) for r in rows],
            "limit": _bounded_limit(limit),
            "filters": filters, "event_type": event_type or "",
            "tool": tool or "", "failure_key": failure_key or "",
            "session": session or "", "stale_days": stale_days}


def skills_page():
    """Live skill index: metadata only (name, description, triggers,
    risk) plus whether each body file is present. No body text is
    served — the console is read-only and bodies are operator-owned."""
    try:
        from memory import skill_load
        rows = []
        for meta in skill_load.list_skill_metadata():
            name = meta.get("name")
            if not name:
                continue
            full = skill_load.load_skill(name)
            rows.append({
                "name": name,
                "description": fmt.safe(meta.get("description"), 160),
                "triggers": ", ".join(str(t) for t in
                                      meta.get("triggers") or []),
                "risk_level": meta.get("risk_level") or "?",
                "body_ok": bool(full and full.get("instructions")),
            })
        return {"rows": rows}
    except Exception:
        return {"rows": []}


def benchmarks_page():
    suites = bench.list_suites()
    baselines = bench.list_baselines()
    return {"suites": suites or NOT_AVAILABLE, "baselines": baselines}
