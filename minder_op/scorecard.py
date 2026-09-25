"""Improvement scorecard — the metrics that drive work, in one object.

Six groups, every number from a source that already exists on the box
(no new collection):

  capture   hook invocations vs persisted records, store freshness, sink,
            sandbox modes          -> is observation itself working?
  cost      hook p50/p90/max, llm vs tool time, tokens
                                   -> what the agent spends per turn
  failures  tool failure rate by tool, repeat failure keys, episodes
                                   -> where the agent actually struggles
  learning  skill gaps, lessons, decision traces
                                   -> is anything being learned?
  context   context pressure, retries, compactions
                                   -> is the session degrading?
  hygiene   sessions without a projection cache, archived backlog
                                   -> housekeeping debt

Read-only and deterministic: `now` is injectable like
`minder_op.summary.build_weekly_summary`.
"""
import time

from minder_op import capture as capture_mod
from minder_op import dsh_sessions


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _counts(db_path, table):
    try:
        from minder_op import queries
        rows = queries._rows(db_path, f"SELECT COUNT(*) AS n FROM {table}")
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return None


def build(db_path=None, dsh_root=None, now=None, window_hours=24):
    now = now if now is not None else time.time()
    since = now - max(1, int(window_hours)) * 3600
    report = {"now": now, "window_hours": window_hours,
              "since": _iso(since), "scores": {}, "evidence": {}}

    # --- capture --------------------------------------------------------
    cap = capture_mod.build(db_path, dsh_root=dsh_root, now=now,
                            window_hours=window_hours)
    report["evidence"]["capture"] = cap
    report["scores"]["capture"] = {
        "coverage_ratio": cap["coverage"]["ratio"],
        "min_ratio": cap["coverage"]["min_ratio"],
        "invocations": cap["coverage"]["invocations"],
        "persisted": cap["coverage"]["persisted"],
        "sink_configured": cap["sink"]["configured"],
        "sink_reachable": cap["sink"]["reachable"],
        "stale_stores": [s["name"] for s in cap["stores"]
                         if s["status"] == "stale"],
        "warnings": cap["warnings"],
    }

    # --- cost -----------------------------------------------------------
    sessions = cap["coverage"]["sessions"]
    p50 = [s["hook_p50_ms"] for s in sessions
           if s.get("hook_p50_ms") is not None]
    report["scores"]["cost"] = {
        "hook_p50_ms": round(sum(p50) / len(p50), 1) if p50 else None,
        "hook_p50_ms_max": max(p50) if p50 else None,
        "sessions_measured": len(sessions),
    }

    # --- failures, learning, context ------------------------------------
    report["scores"]["failures"] = _failure_scores(db_path, since)
    report["scores"]["learning"] = {
        "skill_gaps_open": _count_scoped(db_path, "skill_gaps", since,
                                         status="open"),
        "lessons_verified": _count_scoped(db_path, "lessons", since,
                                          status="verified"),
        "lessons_candidate": _count_scoped(db_path, "lessons", since,
                                           status="candidate"),
        "decision_traces": _count_since(db_path, "decision_traces", since),
        "classifier_shadow": _count_since(db_path, "classifier_shadow",
                                          since),
    }

    # --- context --------------------------------------------------------
    pressure, retries = [], 0
    for row in dsh_sessions.list_sessions(root=dsh_root)["rows"]:
        if row.get("context_pressure_pct") is not None:
            pressure.append(row["context_pressure_pct"])
    try:
        proj = dsh_sessions.projection_cache(dsh_root)
        for record in proj.values():
            if (record.get("rows") or {}).get("llmRetry"):
                retries += 1
    except Exception:
        pass
    report["scores"]["context"] = {
        "sessions": len(pressure),
        "sessions_over_80pct": sum(1 for p in pressure if p >= 80),
        "max_pressure_pct": max(pressure) if pressure else None,
        "sessions_with_llm_retries": retries,
        "tokens_total": _tokens_total(dsh_root),
    }

    # --- hygiene --------------------------------------------------------
    listing = dsh_sessions.list_sessions(root=dsh_root)
    rows = listing["rows"]
    report["scores"]["hygiene"] = {
        "sessions": len(rows),
        "without_projection": sum(1 for r in rows if not r["has_projection"]),
        "without_log": sum(1 for r in rows if not r["log_ok"]),
        "archived": sum(1 for r in rows if r["archived"]),
        "no_capture_link": sum(1 for r in rows
                               if not r["event_count"]
                               and not r["episode_count"]),
    }

    report["focus"] = _focus(report)
    return report


def _failure_scores(db_path, since):
    out = {"tool_failures": None, "repeat_failure_keys": None,
           "episodes_open": None, "episodes_opened_in_window": None,
           "top_tools": [], "top_failure_keys": []}
    try:
        from minder_op import queries
        rows = queries._rows(
            db_path,
            "SELECT tool, COUNT(*) AS n FROM events"
            " WHERE event_type = 'tool_failure' AND ts >= ?"
            " GROUP BY tool ORDER BY n DESC LIMIT 5", (_iso(since),))
        out["top_tools"] = [(r["tool"] or "?", int(r["n"])) for r in rows]
        out["tool_failures"] = sum(n for _t, n in out["top_tools"]) or None
        rows = queries._rows(
            db_path,
            "SELECT failure_key, COUNT(*) AS n FROM events"
            " WHERE event_type = 'tool_failure' AND ts >= ?"
            " GROUP BY failure_key HAVING n > 1 ORDER BY n DESC LIMIT 5",
            (_iso(since),))
        out["top_failure_keys"] = [(r["failure_key"] or "?",
                                    int(r["n"])) for r in rows]
        out["repeat_failure_keys"] = len(out["top_failure_keys"]) or None
        rows = queries._rows(db_path,
                             "SELECT COUNT(*) AS n FROM episodes"
                             " WHERE opened_at >= ?", (_iso(since),))
        out["episodes_opened_in_window"] = int(rows[0]["n"]) if rows else None
        rows = queries._rows(db_path,
                             "SELECT COUNT(*) AS n FROM episodes"
                             " WHERE status = 'open'")
        out["episodes_open"] = int(rows[0]["n"]) if rows else None
    except Exception:
        pass
    return out


def _count_scoped(db_path, table, since, status=None):
    try:
        from minder_op import queries
        if status:
            rows = queries._rows(
                db_path, f"SELECT COUNT(*) AS n FROM {table}"
                         f" WHERE status = ? AND ts >= ?",
                (status, _iso(since)))
        else:
            rows = queries._rows(
                db_path, f"SELECT COUNT(*) AS n FROM {table} WHERE ts >= ?",
                (_iso(since),))
        return int(rows[0]["n"]) if rows else None
    except Exception:
        return None


def _count_since(db_path, table, since):
    column = "ts"
    try:
        from minder_op import queries
        rows = queries._rows(
            db_path, f"SELECT COUNT(*) AS n FROM {table} WHERE {column} >= ?",
            (_iso(since),))
        return int(rows[0]["n"]) if rows else None
    except Exception:
        return None


def _tokens_total(dsh_root):
    total = 0
    try:
        for providers in (dsh_sessions.usage_ledger(dsh_root)
                          or {}).values():
            if not isinstance(providers, dict):
                continue
            for models in providers.values():
                if not isinstance(models, dict):
                    continue
                for counts in models.values():
                    if not isinstance(counts, dict):
                        continue
                    total += int(counts.get("inputTokens", 0) or 0)
                    total += int(counts.get("outputTokens", 0) or 0)
    except Exception:
        return None
    return total or None


def _focus(report):
    """The 3 most actionable items, deterministic order."""
    out = []
    cap = report["scores"]["capture"]
    if not cap["sink_configured"]:
        out.append("capture: MINDER_SINK_URL is unset — confined dsh hooks "
                   "cannot persist; run install.sh to wire the sink")
    elif not cap["sink_reachable"]:
        out.append("capture: the sink is unreachable — start "
                   "minder-sink.service")
    if cap["coverage_ratio"] is not None \
            and cap["coverage_ratio"] < cap["min_ratio"]:
        out.append(f"capture: coverage {cap['coverage_ratio']:.0%} "
                   f"({cap['persisted']}/{cap['invocations']} hooks) — "
                   "investigate before trusting any other page")
    cost = report["scores"]["cost"]
    if cost["hook_p50_ms"] and cost["hook_p50_ms"] > 1000:
        out.append(f"cost: hook p50 {cost['hook_p50_ms']:.0f} ms per tool "
                   "call — check the sink warm tier")
    failures = report["scores"]["failures"]
    if failures.get("top_failure_keys"):
        key, n = failures["top_failure_keys"][0]
        out.append(f"failures: '{key}' failed {n}x in "
                   f"{report['window_hours']}h — inspect for a loop")
    learning = report["scores"]["learning"]
    if learning.get("skill_gaps_open"):
        out.append(f"learning: {learning['skill_gaps_open']} open skill "
                   "gap(s) — write or close a skill")
    if learning.get("lessons_candidate"):
        out.append(f"learning: {learning['lessons_candidate']} candidate "
                   "lesson(s) awaiting promotion")
    context = report["scores"]["context"]
    if context.get("sessions_over_80pct"):
        out.append(f"context: {context['sessions_over_80pct']} session(s) "
                   "above 80% pressure — expect compaction loss")
    hygiene = report["scores"]["hygiene"]
    if hygiene.get("without_projection"):
        out.append(f"hygiene: {hygiene['without_projection']} session(s) "
                   "without a projection cache")
    return out[:3]


def _fmt_ms(value):
    return "-" if value is None else f"{value:.0f} ms"


def render_text(report):
    lines = ["minder scorecard — last "
             + f"{report['window_hours']}h "
             + f"(since {report['since']})", ""]
    cap = report["scores"]["capture"]
    ratio = cap["coverage_ratio"]
    lines.append("capture")
    lines.append(f"  coverage            "
                 f"{'n/a' if ratio is None else format(ratio, '.0%')} "
                 f"({cap['persisted']} persisted / {cap['invocations']} "
                 f"hook invocations)")
    lines.append(f"  sink                configured={cap['sink_configured']} "
                 f"reachable={cap['sink_reachable']}")
    lines.append(f"  stale stores        "
                 f"{', '.join(cap['stale_stores']) or 'none'}")
    cost = report["scores"]["cost"]
    lines.append("cost")
    lines.append(f"  hook p50            {_fmt_ms(cost['hook_p50_ms'])} "
                 f"across {cost['sessions_measured']} live session(s)")
    failures = report["scores"]["failures"]
    lines.append("failures")
    lines.append(f"  tool failures       {failures['tool_failures']}")
    lines.append(f"  repeat keys         {failures['repeat_failure_keys']}")
    lines.append(f"  episodes open       {failures['episodes_open']}")
    for key, n in failures["top_failure_keys"][:3]:
        lines.append(f"    {n:>4}x {key}")
    learning = report["scores"]["learning"]
    lines.append("learning")
    lines.append(f"  skill gaps open     {learning['skill_gaps_open']}")
    lines.append(f"  lessons v/c         {learning['lessons_verified']} / "
                 f"{learning['lessons_candidate']}")
    lines.append(f"  decision traces     {learning['decision_traces']}")
    context = report["scores"]["context"]
    lines.append("context")
    lines.append(f"  sessions            {context['sessions']} "
                 f"(over 80%: {context['sessions_over_80pct']}, "
                 f"max {context['max_pressure_pct']}%)")
    lines.append(f"  llm retries         "
                 f"{context['sessions_with_llm_retries']}")
    hygiene = report["scores"]["hygiene"]
    lines.append("hygiene")
    lines.append(f"  sessions            {hygiene['sessions']} "
                 f"(no projection: {hygiene['without_projection']}, "
                 f"archived: {hygiene['archived']}, "
                 f"unlinked: {hygiene['no_capture_link']})")
    lines.append("")
    lines.append("focus")
    if report["focus"]:
        for item in report["focus"]:
            lines.append(f"  - {item}")
    else:
        lines.append("  nothing flagged.")
    lines.append("")
    lines.append("observed workflow evidence only — no productivity claims.")
    return "\n".join(lines)
