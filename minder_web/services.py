"""Read-model services for the localhost operator console (8E).

Every number and label comes from the minder_op query/summary layer —
this module issues no SQL of its own and knows no policy. Anything
free-text passes through minder_op.format.safe (redact + truncate)
before it can reach a template; missing DB or optional tables become
the NOT_AVAILABLE model (minder_web.strings) here, so a route can
never 500 on storage.
"""
import os

from minder_op import benchmark as bench
from minder_op import format as fmt
from minder_op import queries
from minder_op.queries import DBError
from minder_op.summary import build_weekly_summary

from minder_web import strings

FLAG_VARS = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION",
             "MINDER_SUCCESS_GUARD")
NOT_AVAILABLE = strings.NOT_AVAILABLE
DEFAULT_LIMIT = 25
MAX_LIMIT = 200
# a page is stale when the newest stored event is older than this
STALE_AFTER_S = 86400


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


def overview(db_path, window_hours=24):
    """Overview page model: the scorecard's improvement verdicts lead
    (capture breaks invalidate every other page), then the capture
    strip, the shared weekly summary object, and the operator focus
    list (same object as the CLI's weekly-summary). The capture strip
    and the verdicts come from ONE scorecard build so a single window
    (default 24h, ?window_hours=) is shown consistently."""
    from minder_op import scorecard
    summary = _try(build_weekly_summary, db_path, default=None)
    try:
        queries.status(db_path)  # raises DBError when unreadable
        db_ok = True
    except DBError:
        db_ok = False
    verdicts = []
    capture = None
    since = None
    try:
        report = scorecard.build(db_path, window_hours=window_hours)
        capture = _decorate_capture(report["evidence"]["capture"])
        verdicts = report.get("focus") or []
        since = report.get("since")
    except Exception as e:
        # the landing page must not 500, but a broken scan must be named
        try:
            import minder
            minder.log("web", "overview_scorecard_failed",
                       error=f"{type(e).__name__}: {e}")
        except Exception:
            pass
    # Capture breaks are the one failure that invalidates every other page,
    # so they come first in the operator focus list.
    focus = list((summary or {}).get("focus", []))
    if capture is not None and not capture["ok"]:
        focus = capture["warnings"][:2] + focus
    return {
        "db_ok": db_ok,
        "health": health(db_path),
        "freshness": freshness(db_path),
        "flags": [{"name": var, "value": os.environ.get(var)
                   or strings.UNSET} for var in FLAG_VARS],
        "summary": summary,
        "capture": capture,
        "verdicts": verdicts,
        "since": since,
        "window_hours": window_hours,
        "window_text": _window_text(window_hours),
        "focus": focus[:3],
        "engine": _try(engine_summary, db_path, window_hours=window_hours,
                       default=None),
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


def sessions_page(db_path, query=None, sort=None, limit=200,
                  dsh_root=None):
    """Dsh sessions joined with the projection cache, the workspace
    registry and the memory DB. Read-only; no writes to dsh state."""

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
        return strings.UNKNOWN
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return strings.UNKNOWN
    if age < 90:
        return strings.AGE_SECONDS.format(n=int(age))
    if age < 48 * 3600:
        return strings.AGE_HOURS.format(n=int(age // 3600))
    return strings.AGE_DAYS.format(n=int(age // 86400))


def _human_date(iso):
    """'2026-09-19T16:13:07+00:00' -> 'Sep 19' (blank when unparseable)."""
    if not iso:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return f"{dt.strftime('%b')} {dt.day}"
    except (TypeError, ValueError):
        return ""


def _window_text(hours):
    """24 -> 'day', 168 -> 'week', 48 -> '2 days', 6 -> '6h'."""
    h = int(hours or 0)
    if h and h % 24 == 0:
        days = h // 24
        if days == 1:
            return strings.WINDOW_DAY
        if days == 7:
            return strings.WINDOW_WEEK
        return strings.WINDOW_DAYS.format(n=days)
    return strings.WINDOW_HOURS.format(n=h)


def _human_tokens(n):
    """2056100 -> '2.06M'; 5315 -> '5.3k'; 918 -> '918'."""
    try:
        value = float(n)
    except (TypeError, ValueError):
        return str(n or "-")
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{int(value)}" if value else strings.DASH


def _last_event_age(db_path):
    """(last_ts_iso, age_seconds) for the newest stored event; (None,
    None) when the store is empty or unreadable. Shared by the header
    chip (recording) and the overview freshness strip."""
    last = _try(queries.last_event_ts, db_path, default=None)
    if not last:
        return None, None
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed).total_seconds()
    except (TypeError, ValueError):
        return None, None
    return str(last), age


def recording(db_path):
    """Header-chip text: is evidence still being written? One cheap
    MAX(ts) query per page render — no dsh scan. The schema version and
    db health stay available via the title attribute and /healthz."""
    _last, age = _last_event_age(db_path)
    if age is None:
        return strings.RECORDING_UNKNOWN
    if age > STALE_AFTER_S:
        return strings.RECORDING_STALE.format(age=_age_text(age))
    return strings.RECORDING_OK


def freshness(db_path):
    """Landing-page lag model (DDIA): through when does the evidence
    run, and is it stale? Empty store reads as unknown, never as
    stale - absence of data is not a capture break."""
    _last, age = _last_event_age(db_path)
    if age is None:
        return {"as_of": None, "stale": False, "age": None}
    return {"as_of": _human_date(_last) or None, "stale": age > STALE_AFTER_S,
            "age": _age_text(age)}


def _decorate_capture(report):
    """capture.build post-processing shared by / and /capture."""
    from minder_op import capture
    for store in report["stores"]:
        store["age"] = capture.age_text(store.get("age_s"))
    for session in report["coverage"].get("sessions") or []:
        session["short"] = str(session.get("session_id") or "")[8:20]
    return report


def capture_page(db_path, dsh_root=None, window_hours=1):
    """Capture health: is the watchdog actually persisting anything?"""
    from minder_op import capture
    return _decorate_capture(capture.build(db_path, dsh_root=dsh_root,
                                           window_hours=window_hours))


def scorecard_page(db_path, window_hours=24, dsh_root=None):
    """The improvement scorecard as a page model."""
    from minder_op import scorecard
    return scorecard.build(db_path, dsh_root=dsh_root,
                           window_hours=window_hours)


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
        from minder_memory import skill_load
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


# --- trace review (read-only) -------------------------------------------
#
# The console mirrors the CLI's read surface only. Regression conversion
# and feedback stay in `minder-op`: they are writes, and this console has
# no write endpoint by design (GET-only, loopback-only).

SEVERITY_ORDER = ("blocker", "high", "medium", "low", "info")


def _review_row(review):
    summary = review.get("summary") or {}
    return {
        "review_id": review.get("review_id"),
        "session_id": review.get("session_id"),
        "run_id": review.get("run_id"),
        "ts": (review.get("ts") or "")[:19],
        "status": review.get("status"),
        "evaluator_version": review.get("evaluator_version"),
        "rubric_id": review.get("rubric_id") or "-",
        "findings": summary.get("findings"),
        "highest_severity": summary.get("highest_severity") or "-",
        "tool_calls": summary.get("tool_calls"),
        "failures": summary.get("failures"),
        "tokens_total": summary.get("tokens_total"),
    }


def traces_page(db_path, limit=DEFAULT_LIMIT):
    """Stored trace reviews, newest first. A missing DB or an unmigrated
    schema reads as an empty list, never a 500."""
    from minder_memory import trace_reviews
    rows = trace_reviews.list_reviews(limit=_bounded_limit(limit),
                                      db_path=db_path)
    stats = trace_reviews.acceptance_stats(db_path=db_path)
    return {
        "rows": [_review_row(r) for r in rows],
        "counts": {"reviews": len(rows),
                   "confirmed": stats.get("confirmed"),
                   "rejected": stats.get("rejected"),
                   "acceptance_rate": stats.get("acceptance_rate")},
        "severity_order": SEVERITY_ORDER,
    }


def _finding_row(finding, verdicts):
    evidence = finding.get("evidence") or {}
    seqs = evidence.get("ds_seqs") or []
    return {
        "finding_id": finding.get("finding_id"),
        "severity": finding.get("severity"),
        "evaluator": finding.get("evaluator"),
        "rule_id": finding.get("rule_id"),
        "events": ", ".join(str(s) for s in seqs[:6]) or "-",
        "event_count": len(seqs),
        "message": fmt.safe(finding.get("message"), 300),
        "suggested_fix": fmt.safe(finding.get("suggested_fix"), 300),
        "excerpts": [fmt.safe(e, 160) for e in
                     (evidence.get("excerpts") or [])],
        "reviewed": verdicts.get(finding.get("finding_id"), ""),
    }


def trace_detail(db_path, review_id):
    """One review with its findings, verdicts and feedback. None when the
    review does not exist (the route turns that into a 404)."""
    from minder_memory import trace_reviews
    review = trace_reviews.get_review(review_id, db_path=db_path)
    if review is None:
        return None
    verdicts = trace_reviews.finding_verdicts(review_id, db_path=db_path)
    feedback = trace_reviews.list_feedback(review_id, limit=100,
                                          db_path=db_path)
    order = {name: index for index, name in enumerate(SEVERITY_ORDER)}
    findings = sorted(review.get("findings") or [], key=lambda f: (
        order.get(f.get("severity"), 99),
        (f.get("evidence") or {}).get("ds_seqs") or [0]))
    return {
        "review": _review_row(review),
        "summary": review.get("summary") or {},
        "findings": [_finding_row(f, verdicts) for f in findings],
        "feedback": [{"ts": (item.get("ts") or "")[:19],
                      "level": item.get("level"),
                      "category": item.get("category"),
                      "target": item.get("target_ref") or "-",
                      "verdict": item.get("finding_verdict") or "-",
                      "reviewer": item.get("reviewer") or "-",
                      "comment": fmt.safe(item.get("comment"), 200)}
                     for item in feedback],
        "redaction_status": review.get("redaction_status"),
        "severity_order": SEVERITY_ORDER,
    }


def _engine_rows():
    from minder_op import engines
    return engines.status()


def engine_page():
    """Engine registry rows for the console; a broken probe or a missing
    registry renders as a named error, never a 500."""
    try:
        return {"rows": _engine_rows(), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"rows": [], "error": f"{type(e).__name__}: {e}"}


def engine_switch(name):
    """Console write path (issue #3): the engine lifecycle switch."""
    from minder_op import engines
    return engines.switch(name)


def engine_summary(db_path, window_hours=24):
    """Active engine and prompt-cache reuse for the window (issue #3).
    Reuse is cached_tokens / prompt_tokens from token_usage events; a
    ratio near zero means the engine's prompt cache is being defeated."""
    try:
        import minder
        _engines, active = minder.engine_registry()
    except Exception:  # noqa: BLE001
        active = None
    try:
        from minder_op import queries
        stats = queries.token_reuse(db_path, window_hours=window_hours)
    except Exception:  # noqa: BLE001
        stats = None
    reuse = None
    if stats and stats["prompt_tokens"]:
        reuse = round(100.0 * stats["cached_tokens"] /
                      stats["prompt_tokens"])
    return {"name": active, "reuse_pct": reuse,
            "events": (stats or {}).get("events", 0)}
