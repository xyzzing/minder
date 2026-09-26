"""Weekly workflow summary (8A) — the one report object shared by the
CLI (`minder-op weekly-summary`) and, later, the 8E overview page.

Evidence only: every number is read from the v10 tables (episodes,
events, lessons, skill_gaps, frontier_evals, decision_traces). The
report never claims Minder improved anything — FR-7 reserves improvement
claims for benchmark comparisons against a pinned baseline (8C).

Labels come from frontier_evals (007); the frontier_traces INTEGER
helpfulness column (003) is legacy and never read here. Deterministic
tests inject `now=`; window bounds are ISO-UTC strings compared
lexicographically, matching how every writer stamps rows.
"""
from datetime import datetime, timedelta, timezone

from minder_memory.canonicalise import redact

from minder_op import format as fmt
from minder_op.queries import UNCLASSIFIED, _rows

NOTE = ("observed workflow evidence only; minder makes no productivity "
        "claim")

EPISODE_BUCKETS = ("verified", "candidate", "resolved", "unresolved")
LESSON_BUCKETS = ("verified", "candidate", "invalidated")
CONSULT_LABELS = ("helpful", "partial", "harmful", "inconclusive")

# Operator-focus priority is fixed so output is deterministic; the top
# three surface in the report, the rest are cut.
FOCUS_REPEAT_MIN = 2


def _utc(value):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        return _parse_iso(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso(text):
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot parse timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _window(days, since, now):
    until = _utc(now)
    if since is not None:
        start = _parse_iso(since)
        if start > until:
            raise ValueError("--since is after now; empty window")
        return start.isoformat(), until.isoformat(), None
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("--days must be an integer >= 1 (or use --since)")
    start = until - timedelta(days=days)
    return start.isoformat(), until.isoformat(), days


def _count(path, sql, params):
    rows = _rows(path, sql, params)
    return rows[0]["n"] if rows else 0


def _bucketed(path, sql, params, known):
    """GROUP BY status counts; JSON keeps only non-empty buckets, with
    everything outside `known` folded into 'other'."""
    out = {}
    for row in _rows(path, sql, params):
        key = row["k"] if row["k"] in known else "other"
        out[key] = out.get(key, 0) + row["n"]
    return out


def build_weekly_summary(db_path, days=7, since=None, now=None):
    """One read-only pass over the v10 tables -> JSON-safe report dict.

    Window counts use each table's own timestamp (episodes.opened_at /
    closed_at, events.ts, lessons.valid_from / valid_to, skill_gaps.ts,
    frontier_traces.ts, decision_traces.ts). `candidates_open_total` and
    `open_now` are current-state counts by design: they name what an
    operator can act on today, not window history.
    """
    start, until, days_out = _window(days, since, now)
    win = (start, until)

    closed = _bucketed(db_path,
                       "SELECT status AS k, COUNT(*) AS n FROM episodes"
                       " WHERE closed_at >= ? AND closed_at < ?"
                       " GROUP BY status", win, EPISODE_BUCKETS)
    closed_total = sum(closed.values())
    rate = None
    if closed_total:
        rate = round(closed.get("verified", 0) / closed_total, 4)

    top = sorted(
        _rows(db_path,
              "SELECT failure_key AS k, COUNT(*) AS n FROM events"
              " WHERE event_type = 'tool_failure' AND failure_key IS NOT"
              " NULL AND failure_key != '' AND ts >= ? AND ts < ?"
              " GROUP BY failure_key", win),
        key=lambda r: (-r["n"], r["k"]))
    per_key = {row["k"]: row["n"] for row in top}

    by_label = {row["k"]: row["n"] for row in _rows(
        db_path,
        "SELECT COALESCE(e.helpfulness, ?) AS k, COUNT(*) AS n"
        " FROM frontier_traces t LEFT JOIN frontier_evals e"
        " ON e.trace_id = t.trace_id"
        " WHERE t.ts >= ? AND t.ts < ? GROUP BY k", (UNCLASSIFIED, *win))}
    consult_total = sum(by_label.values())

    report = {
        "window": {"since": start, "until": until, "days": days_out},
        "episodes": {
            "opened_in_window": _count(
                db_path, "SELECT COUNT(*) AS n FROM episodes"
                " WHERE opened_at >= ? AND opened_at < ?", win),
            "open_now": _count(
                db_path, "SELECT COUNT(*) AS n FROM episodes"
                " WHERE status = 'open'", ()),
            "closed_in_window": closed,
            "verified_resolution_rate": rate,
        },
        "failures": {
            "tool_failures_in_window": _count(
                db_path, "SELECT COUNT(*) AS n FROM events"
                " WHERE event_type = 'tool_failure' AND ts >= ?"
                " AND ts < ?", win),
            "distinct_failure_keys": len(per_key),
            "repeat_failure_keys": sum(
                1 for n in per_key.values() if n >= FOCUS_REPEAT_MIN),
            "top_failure_keys": [
                {"failure_key": redact(row["k"]), "count": row["n"]}
                for row in top[:3]],
        },
        "lessons": {
            "created_in_window": _bucketed(
                db_path, "SELECT status AS k, COUNT(*) AS n FROM lessons"
                " WHERE valid_from >= ? AND valid_from < ?"
                " GROUP BY status", win, LESSON_BUCKETS),
            "invalidated_in_window": _count(
                db_path, "SELECT COUNT(*) AS n FROM lessons"
                " WHERE valid_to IS NOT NULL AND valid_to >= ?"
                " AND valid_to < ?", win),
            "candidates_open_total": _count(
                db_path, "SELECT COUNT(*) AS n FROM lessons"
                " WHERE status = 'candidate'", ()),
        },
        "gaps": {
            "opened_in_window": _count(
                db_path, "SELECT COUNT(*) AS n FROM skill_gaps"
                " WHERE ts >= ? AND ts < ?", win),
            "open_now": _count(
                db_path, "SELECT COUNT(*) AS n FROM skill_gaps"
                " WHERE status = 'open'", ()),
        },
        "consults": {
            "total_in_window": consult_total,
            "by_helpfulness": by_label,
        },
        "decisions": {
            "traces_in_window": _count(
                db_path, "SELECT COUNT(*) AS n FROM decision_traces"
                " WHERE ts >= ? AND ts < ?", win),
            "agreements": _count(
                db_path, "SELECT COUNT(*) AS n FROM decision_traces"
                " WHERE ts >= ? AND ts < ? AND COALESCE"
                "(model_recommendation, '') != ''"
                " AND model_recommendation = policy_decision", win),
            "overrides": _count(
                db_path, "SELECT COUNT(*) AS n FROM decision_traces"
                " WHERE ts >= ? AND ts < ? AND COALESCE(override, '')"
                " != ''", win),
        },
        # 8C lays the benchmark foundation; until then there is no
        # baseline to report and the summary says so instead of guessing.
        "benchmarks": {"status": "not available"},
        "note": NOTE,
    }
    report["domain"] = _domain_section(db_path, win, now)
    report["focus"] = _focus(report)
    return report


def _domain_section(db_path, win, now):
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


def _focus(report):
    """Up to three deterministic actions, fixed priority order."""
    focus = []
    harmful = report["consults"]["by_helpfulness"].get("harmful", 0)
    if harmful:
        focus.append(f"review {harmful} harmful frontier consult(s): "
                     "minder-op consults ls")
    candidates = report["lessons"]["candidates_open_total"]
    if candidates:
        focus.append(f"review {candidates} candidate lesson(s): "
                     "minder-op lessons ls --status candidate")
    if report["gaps"]["open_now"]:
        focus.append(f"write or close {report['gaps']['open_now']} open "
                     "skill gap(s): minder-op gaps ls")
    if report["failures"]["repeat_failure_keys"]:
        worst = report["failures"]["top_failure_keys"][0]
        focus.append(f"inspect repeat failure key '{worst['failure_key']}'"
                     f" ({worst['count']} failures in window): "
                     "minder-op episodes ls")
    if report["decisions"]["overrides"]:
        focus.append(f"review {report['decisions']['overrides']} "
                     "decision-gateway override(s): minder-op decisions ls")
    return focus[:3]


def _section(title, pairs):
    print(title)
    fmt.kv(pairs)
    print()


def render_text(report):
    """Human rendering of build_weekly_summary output. Same facts, no
    claims: the closing note states the evidence-only scope."""
    window = report["window"]
    span = (f"{window['days']} days" if window["days"] is not None
            else f"since {window['since']}")
    print("minder weekly workflow summary")
    fmt.kv([("window", f"{window['since']} -> {window['until']} ({span})")])
    print()

    ep = report["episodes"]
    closed = ep["closed_in_window"]
    rate = ep["verified_resolution_rate"]
    if rate is None:
        rate_text = "n/a"
    else:
        closed_total = sum(closed.values())
        rate_text = (f"{closed.get('verified', 0)}/{closed_total} closed"
                     f" ({rate:.1%})")
    _section("episodes", [
        ("opened in window", ep["opened_in_window"]),
        ("open now", ep["open_now"])]
        + [(f"closed {bucket}", closed.get(bucket, 0))
           for bucket in EPISODE_BUCKETS]
        + [("closed other", closed.get("other", 0)),
           ("verified resolution", rate_text)])

    fails = report["failures"]
    _section("failures", [
        ("tool failures in window", fails["tool_failures_in_window"]),
        ("distinct failure keys", fails["distinct_failure_keys"]),
        ("repeat failure keys", fails["repeat_failure_keys"])])
    if fails["top_failure_keys"]:
        print("top failure keys")
        for row in fails["top_failure_keys"]:
            print(f"  {row['failure_key']} x{row['count']}")
        print()

    created = report["lessons"]["created_in_window"]
    _section("lessons", [
        *[(f"created {bucket}", created.get(bucket, 0))
          for bucket in LESSON_BUCKETS],
        ("invalidated in window", report["lessons"]["invalidated_in_window"]),
        ("candidates open", report["lessons"]["candidates_open_total"])])

    _section("skill gaps", [
        ("opened in window", report["gaps"]["opened_in_window"]),
        ("open now", report["gaps"]["open_now"])])

    labels = report["consults"]["by_helpfulness"]
    _section("frontier consults (labels: frontier_evals)", [
        *[(label, labels.get(label, 0)) for label in CONSULT_LABELS],
        (UNCLASSIFIED, labels.get(UNCLASSIFIED, 0))])

    dec = report["decisions"]
    _section("decision gateway traces", [
        ("traces in window", dec["traces_in_window"]),
        ("model == policy", dec["agreements"]),
        ("overrides", dec["overrides"])])

    _section("benchmarks", list(report["benchmarks"].items()))

    domain = report["domain"]
    if domain["available"]:
        _section("domain layer", [
            ("declared boundaries", domain["declared_boundaries"]),
            ("route traces", domain["route_traces"]),
            ("route agreements", domain["route_agreements"]),
            ("route abstentions", domain["route_abstentions"]),
            ("resume intents expiring (7d)",
             domain["resume_intents_expiring_7d"]),
            ("success-loop advisories", domain["success_advisories"])])
    else:
        _section("domain layer", [("status", "not available (migrate by "
                                   "running any minder runtime command)")])

    print("operator focus")
    if report["focus"]:
        for i, action in enumerate(report["focus"], start=1):
            print(f"  {i}. {action}")
    else:
        print("  (nothing flagged for this window)")
    print()
    print(NOTE)
