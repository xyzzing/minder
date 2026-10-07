"""User-visible console copy (C3, issue #8). Every string a browser
renders lives here; templates reach it through the Jinja global `S`
and services.py imports the module constants. Entries are code-owned
static text, so entries carrying light markup (<code>, <a>, <em>) are
rendered with `|safe`; user data must never enter this module.
Placeholders use str.format braces. Console copy rules apply: plain
hyphens, `n/a` means the source is empty or unavailable, never zero.
"""
NOT_AVAILABLE, NA_LOWER, UNSET, UNKNOWN, DASH = (
    "not available", "n/a", "(unset)", "unknown", "-")
RECORDING_OK = "recording: ok"
RECORDING_STALE = "recording: stale {age}"
RECORDING_UNKNOWN = "recording: unknown"
AGE_SECONDS, AGE_HOURS, AGE_DAYS = ("{n}s ago", "{n}h ago", "{n}d ago")
WINDOW_DAY, WINDOW_WEEK = "day", "week"
WINDOW_DAYS, WINDOW_HOURS = "{n} days", "{n}h"

S = {
    "common": {
        "not_available": NOT_AVAILABLE, "na": NA_LOWER,
        "unknown": UNKNOWN, "yes": "yes", "no": "no", "none": "none",
        "status": "status", "day": WINDOW_DAY, "week": WINDOW_WEEK,
    },
    "base": {
        "title": "minder operator console",
        "footer": "localhost · observed workflow evidence only, no "
                  "productivity claims - the console is not a policy engine.",
    },
    "nav": {
        "doing": "how am I doing?", "happened": "what happened?",
        "learned": "what has it learned?", "running": "running on",
        "advanced": "advanced", "overview": "overview",
        "scorecard": "scorecard", "sessions": "sessions",
        "events": "events", "episodes": "episodes", "lessons": "lessons",
        "gaps": "gaps", "skills": "skills", "engines": "engines",
        "capture": "capture", "consults": "consults",
        "decisions": "decisions", "difficulty": "difficulty",
        "benchmarks": "benchmarks", "traces": "traces",
    },
    "overview": {
        "title": "overview",
        "no_db_pre": "memory database not available - start the console with ",
        "no_db_post": " pointing at a minder memory sqlite.",
        "improve": "what to improve first",
        "improve_gloss": "(past {window}, from observed evidence on this machine)",
        "window": "window:",
        "none_flagged_window": "nothing flagged in the past {window}.",
        "capture": "capture",
        "capture_gloss": "(is the watchdog recording? - the first thing to check)",
        "coverage": "coverage", "coverage_gloss": "tool calls saved",
        "sink_ready": "save path ready (sink)",
        "stores_fresh": "data sources fresh (stores)",
        "capture_detail": "capture detail", "warnings_suffix": "warning(s)",
        "weekly": "weekly workflow summary",
        "week_gloss": "past week, {since} to {until}",
        "episodes_opened": "episodes opened",
        "episodes_open_now": "episodes open now",
        "verified_resolution": "verified resolution",
        "verified_resolution_gloss": "(fixed with a confirmed fix)",
        "tool_failures": "tool failures",
        "repeat_keys": "repeat failure keys",
        "repeat_keys_gloss": "(the same error coming back)",
        "lessons_created": "lessons created (verified / candidate)",
        "open_gaps": "open gaps",
        "frontier_help": "frontier helpfulness",
        "frontier_help_gloss": "(did the outside model help?)",
        "decisions": "decision traces / overrides",
        "decisions_gloss": "(routing choices / human overrides)",
        "benchmarks": "benchmarks",
        "operator_focus": "operator focus",
        "nothing_flagged": "nothing flagged for this window.",
        "not_available": "not available.",
        "current_flags": "current flags",
        "flags_gloss": "(environment/systemd owned - display only)",
        "flag": "flag", "value": "value", "health": "health",
        "schema_version": "schema version",
        "engine": "engine",
        "engine_gloss": "(which model server the proxy forwards to)",
        "active_engine": "active engine", "reuse": "prompt-cache reuse",
        "reuse_gloss": "(cached share of prompt tokens, past {window})",
    },
    "scorecard": {
        "title": "scorecard",
        "gloss": "(what to improve next, from observed evidence)",
        "note": "last {hours}h (since {since}). Every number is read from data"
                " already on this machine - the console adds no collection."
                " \"n/a\" means the source is empty or unavailable, never zero.",
        "focus": "focus", "capture": "capture",
        "capture_gloss": "(observation itself)", "coverage": "coverage",
        "not_measurable": "not measurable", "hooks": "hooks",
        "sink": "sink", "configured": "configured",
        "reachable": "reachable", "stale_stores": "stale stores",
        "cost": "cost", "cost_gloss": "(per tool call)",
        "hook_p50": "hook p50", "ms": "ms",
        "worst_session": "worst session", "measured": "measured",
        "failures": "failures", "tool_failures": "tool failures",
        "repeat_keys": "repeat failure keys",
        "episodes_open": "episodes open / opened",
        "failure_key": "failure key", "count": "failures",
        "learning": "learning", "skill_gaps_open": "skill gaps open",
        "lessons": "lessons verified / candidate",
        "decision_traces": "decision traces / shadow rows",
        "context": "context", "sessions_measured": "sessions measured",
        "over_80": "over 80%:", "max": "max",
        "llm_retries": "sessions with llm retries",
        "tokens": "tokens (dsh usage ledger)", "hygiene": "hygiene",
        "sessions": "sessions",
        "without_projection": "without projection cache",
        "without_log": "without a log file", "archived": "archived",
        "no_capture_link": "no capture link",
    },
    "sessions": {
        "title": "sessions",
        "gloss": "(dsh sessions joined with projections, workspace and episodes)",
        "note_counts": "{sessions} session(s) · {projection} with a projection"
                       " cache · {archived} archived · showing {shown} ({sort}"
                       " first).",
        "note_ids_pre": "Session ids are dsh's own (<code>~/.dsh/sessions/</code>)"
                        " - the same id the hook records and"
                        " <code>episodes.task_id</code> carries.",
        "legend": "tokens = words of compute the session used (exact number on"
                  " hover); context = how full the AI's working memory was (over"
                  " 80% means it starts forgetting); capture = whether minder"
                  " saved this session's evidence.",
        "filter_placeholder": "filter by path, title or id",
        "filter": "filter", "reset": "reset",
        "col_project": "project", "col_title": "title / session",
        "col_last_seen": "last seen", "col_turns": "turns",
        "col_steps": "steps", "col_tokens": "tokens",
        "col_context": "context", "col_sandbox": "sandbox",
        "col_capture": "capture",
        "badge_archived": "archived",
        "badge_no_projection": "no projection", "badge_none": "none",
        "ep": "ep", "ev": "ev",
        "empty": "no sessions found - is <code>~/.dsh/sessions/</code> populated?",
        "note_none": "\"none\" means no episode or observed event is linked to"
                     " that session yet. When a session has hook invocations but"
                     " nothing captured, open <a href=\"/capture\">capture</a>,"
                     " which compares the two directly.",
    },
    "session": {
        "title": "session", "detail": "session detail",
        "capture_gap": "<strong>capture gap:</strong> this session has {invocations}"
                       " hook invocations but nothing persisted ({events} events)."
                       " See <a href=\"/capture\">capture</a> - the hook fired and"
                       " its writes were dropped.",
        "work": "work", "goal": "goal", "last_activity": "last activity",
        "sandbox": "sandbox",
        "sandbox_gloss": "{preset} preset, approval {approval}",
        "turns_steps": "turns / steps", "tool_calls": "tool calls",
        "tool_failures": "tool failures seen in log",
        "llm_tool_time": "llm / tool time",
        "context_pressure": "context pressure",
        "context_pressure_gloss": "(how full the AI's working memory was)",
        "tokens_total": "tokens (total)",
        "tokens_total_gloss": "(words of compute)",
        "hook_cost": "hook cost",
        "hook_cost_gloss": "(every tool call pays this)",
        "invocations_results": "invocations / results",
        "non_zero_exits": "non-zero exits",
        "duration": "duration p50 / p90 / max", "points": "points",
        "log_unavailable": "session log counters unavailable - no readable"
                           " <code>session.vN.jsonl.zstd</code> for this session"
                           " (zstd reader missing or the log is absent).",
        "tools_used": "tools used", "col_tool": "tool",
        "col_calls": "calls",
        "tools_empty": "nothing recorded in the log.",
        "episodes": "episodes",
        "episodes_gloss": "(joined on task_id == session id)",
        "col_episode": "episode", "col_status": "status",
        "col_opened": "opened", "col_repo": "repo",
        "episodes_empty": "no episode linked to this session.",
        "observed": "observed events", "col_ts": "ts", "col_type": "type",
        "col_tool_name": "tool", "col_failure_key": "failure_key",
        "observed_empty": "nothing observed for this session.",
        "todos": "todos at last projection",
    },
    "events": {
        "title": "events", "gloss": "(raw observed events, newest first)",
        "stale_warn": "<strong>stale data:</strong> the newest observed event is"
                      " {days} day(s) old. That is a capture problem, not an empty"
                      " queue - check <a href=\"/capture\">capture</a> before"
                      " drawing conclusions from this page.",
        "all_types": "all types", "all_tools": "all tools",
        "all_keys": "all failure keys",
        "session_placeholder": "session id contains",
        "filter": "filter", "reset": "reset",
        "col_ts": "ts", "col_type": "type", "col_tool": "tool",
        "col_failure_key": "failure_key", "col_session": "session",
        "col_episode": "episode",
        "empty": "no observed events for this filter",
        "empty_suffix": "(nothing has failed in the stored window)",
    },
    "episodes": {
        "title": "episodes", "col_id": "id", "col_opened": "opened",
        "col_status": "status", "col_repo": "repo", "col_task": "task",
        "empty": "not available.",
        "showing": "showing up to {limit} most recent.",
    },
    "episode": {
        "title": "episode", "timeline": "timeline", "col_seq": "seq",
        "col_ts": "ts", "col_type": "type", "col_tool": "tool",
        "col_failure_key": "failure_key", "col_excerpt": "excerpt",
        "empty": "no events.",
    },
    "lessons": {
        "title": "lessons",
        "note": "candidates are frontier-distilled and inert until promoted with"
                " tests - they are not retrieved as verified authority.",
        "col_id": "id", "col_status": "status",
        "col_valid_from": "valid_from",
        "col_failure_key": "failure_key",
        "col_instruction": "instruction", "empty": "not available.",
        "showing": "showing up to {limit}; default view is live verified"
                   " only.",
    },
    "lesson": {
        "title": "lesson", "col_status": "status", "col_repo": "repo",
        "col_failure_key": "failure_key",
        "col_instruction": "instruction",
        "col_anti_pattern": "anti_pattern",
        "col_verification": "verification",
        "col_source_episode": "source_episode",
        "col_valid_from": "valid_from", "col_valid_to": "valid_to",
    },
    "gaps": {
        "title": "skill gaps", "gloss": "(open)", "col_id": "id",
        "col_ts": "ts", "col_type": "type", "col_repo": "repo",
        "col_failure_key": "failure_key", "col_sample": "sample",
        "empty": "not available.",
    },
    "skills": {
        "title": "skill index",
        "gloss": "(live - metadata only, bodies stay operator-owned)",
        "col_name": "name", "col_description": "description",
        "col_triggers": "triggers", "col_risk": "risk",
        "col_body": "body", "ok": "ok", "missing": "missing",
        "empty": "not available.",
    },
    "engine": {
        "title": "engines", "col_engine": "engine",
        "col_upstream": "upstream", "col_unit": "unit",
        "col_unit_state": "unit state", "col_health": "upstream health",
        "col_switch": "switch", "active": "(active)",
        "healthy": "healthy", "unhealthy": "unhealthy",
        "current": "current", "switch_to": "switch to {name}",
        "empty": "no engine registry configured (minder.json engines"
                 " key).",
        "note": "switching stops the current engine unit, starts the target, and"
                " health-checks it before flipping the config; a failed switch"
                " rolls back. Engines sharing one GPU cannot run at the same"
                " time. CLI: <code>minder-op engine status</code>.",
    },
    "capture": {
        "title": "capture",
        "gloss": "(is the watchdog actually recording anything?)",
        "note": "Every hook write is fail-open: a confined hook that cannot write"
                " the state directory still exits 0. The only reliable symptom is"
                " the gap between <em>hook invocations</em> (dsh's own session"
                " logs) and <em>persisted records</em> - compared below.",
        "verdict": "verdict",
        "healthy": "<strong>capture healthy</strong> - no warnings in the last"
                   " {hours}h.",
        "coverage": "coverage", "coverage_gloss": "(last {hours}h)",
        "invocations": "hook invocations (from session logs)",
        "logs_scanned": "{scanned} log(s) scanned",
        "truncated": "truncated", "persisted": "persisted hook records",
        "ledger": "ledger", "db": "db", "ratio": "ratio",
        "floor": "floor", "col_session": "session",
        "col_invocations": "invocations", "col_hook_p50": "hook p50",
        "stores": "stores", "col_store": "store", "col_file": "file",
        "col_last_write": "last write", "col_status": "status",
        "ago": "ago", "sink": "sink",
        "sink_gloss": "(the sandboxed hook's write path)",
        "configured": "configured",
        "not_configured": "no - confined hooks cannot persist",
        "url": "url", "reachable": "reachable", "ops": "ops", "ok": "ok",
        "failed": "failed", "warm_providers": "warm providers",
        "last_persist": "last persist",
        "sandbox_modes": "sandbox modes",
        "sandbox_gloss": "(why some sessions can write and others cannot)",
        "col_mode": "mode", "col_sessions": "sessions",
        "sandbox_empty": "no projection data.",
        "state_note": "state dir: <code>{dir}</code> · generated {now}"
                      " (unix seconds).",
    },
    "consults": {
        "title": "frontier consults",
        "note": "labels come from <code>frontier_evals</code> (007); raw"
                " prompt/response text is never stored or shown - hashes only.",
        "col_trace_id": "trace_id", "col_ts": "ts", "col_label": "label",
        "col_verification": "verification",
        "col_failure_key": "failure_key", "col_providers": "providers",
        "unclassified": "(unclassified)", "empty": "not available.",
    },
    "consult": {
        "title": "consult", "col_ts": "ts",
        "col_episode_id": "episode_id",
        "col_failure_key": "failure_key",
        "col_local_attempts": "local_attempts",
        "col_redaction_profile": "redaction_profile",
        "col_providers": "providers",
        "col_request_hash": "request_hash",
        "col_response_hash": "response_hash",
        "col_helpfulness": "helpfulness",
        "col_verification_status": "verification_status",
        "col_accepted": "accepted", "col_rejected": "rejected",
        "col_distilled": "distilled",
        "unclassified": "(unclassified)",
        "note": "raw prompt/response text is never stored - hashes only.",
    },
    "decisions": {
        "title": "decision gateway traces", "gloss": "(shadow - log only)",
        "col_id": "id", "col_ts": "ts", "col_contract": "contract",
        "col_failure_key": "failure_key", "col_model": "model",
        "col_policy": "policy", "col_override": "override",
        "col_confidence": "confidence", "col_provider": "provider",
        "empty": "not available.",
        "note": "nothing in policy reads this table; traces separate the"
                " model recommendation from the final policy decision.",
    },
    "difficulty": {
        "title": "difficulty router",
        "gloss": "(laya decision layer - proxy ledger)",
        "event_one": "event", "event_many": "events",
        "in_ledger": "in ledger",
        "col_ts": "ts", "col_kind": "kind", "col_label": "label",
        "col_score": "score", "col_confidence": "confidence",
        "col_band": "band", "col_effort": "effort", "col_budget": "budget",
        "col_max_tokens": "max_tokens", "col_guardrail": "guardrail",
        "shadow": "shadow", "routed": "routed",
        "empty": "no difficulty events yet - the router logs here once it runs"
                 " (shadow mode observes without changing the request).",
        "note": "read-only view of the proxy's <code>events.jsonl</code> ledger."
                " <code>shadow</code> rows are laya's difficulty opinion logged"
                " without touching the request; <code>routed</code> rows show the"
                " band actually applied (effort / thinking-budget / max-tokens"
                " ceiling / spend guardrail). Nothing here feeds policy.",
    },
    "benchmarks": {
        "title": "benchmarks", "suites": "suites",
        "col_suite": "suite", "col_manifest": "manifest",
        "col_tasks": "tasks", "col_fingerprint": "fingerprint",
        "col_status": "status", "suites_empty": "no suites found.",
        "baselines": "pinned baselines",
        "col_generated_at": "generated_at", "col_runs": "runs",
        "col_verified_rate": "verified rate",
        "baselines_empty": "no pinned baseline (created only via <code>minder-op"
                           " benchmark baseline create --yes</code>).",
        "note": "comparison verdicts come from <code>minder-op benchmark"
                " compare</code>; improvement claims require a pinned baseline"
                " comparison, never console activity.",
    },
    "traces": {
        "title": "traces",
        "gloss": "(stored trace reviews - post-run, deterministic)",
        "note_shown": "{count} stored review(s) shown.",
        "note_confirmed": "Reviewer-confirmed findings:",
        "note_rejected": "rejected:", "note_acceptance": "acceptance",
        "note_meaning": "A review is what one evaluator version said about one"
                        " completed dsh session; findings are candidates until a"
                        " reviewer confirms them.",
        "col_reviewed": "reviewed", "col_session": "session",
        "col_severity": "severity", "col_findings": "findings",
        "col_tools": "tools", "col_failures": "failures",
        "col_tokens": "tokens", "col_rubric": "rubric",
        "col_evaluator": "evaluator", "clean": "clean",
        "empty": "no stored reviews yet - run"
                 " <code>minder-op trace review &lt;session&gt;</code>"
                 " with <code>MINDER_TRACE_REVIEW=on</code>.",
        "note_readonly": "This console is read-only. Confirming findings, recording"
                         " feedback and converting a confirmed failure into a"
                         " regression case are writes and live in the CLI:"
                         " <code>minder-op trace feedback … --yes</code> /"
                         " <code>minder-op trace regress … --yes</code>.",
    },
    "trace": {
        "title": "trace review", "findings": "findings",
        "col_severity": "severity", "col_evaluator": "evaluator",
        "col_rule": "rule", "col_events": "events",
        "col_reviewed": "reviewed", "col_message": "message",
        "col_rubric": "rubric",
        "confirmed": "confirmed", "rejected": "rejected",
        "unreviewed": "unreviewed", "suggested": "suggested:",
        "findings_empty": "no findings - this trace is clean under the active"
                          " evaluators.",
        "summary": "summary", "sum_findings": "findings",
        "sum_highest": "highest severity", "sum_by_severity": "by severity",
        "sum_by_evaluator": "by evaluator",
        "sum_tool_calls": "tool calls", "sum_failures": "failures",
        "sum_turns": "turns", "sum_cited": "cited events",
        "feedback": "human feedback", "col_when": "when",
        "col_level": "level", "col_category": "category",
        "col_target": "target", "col_verdict": "verdict",
        "col_reviewer": "reviewer", "col_comment": "comment",
        "confirm": "confirm", "reject": "reject",
        "feedback_empty": "no feedback yet - a finding nobody has judged is a"
                          " hypothesis, and cannot become a regression case.",
    },
}
