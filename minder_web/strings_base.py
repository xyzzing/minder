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
        "caption": "one row per {unit}, newest first",
        "raw_fields": "raw fields",
        "raw_note": "the same rows, with the storage field names and the"
                    " values exactly as stored - nothing rounded,"
                    " humanized or renamed.",
    },
    "base": {
        "title": "minder operator console",
        "skip": "skip to content",
        "footer": "localhost · observed workflow evidence only, no "
                  "productivity claims - the console is not a policy "
                  "engine. There is one write path: the engine switch on ",
        "footer_engine_link_suffix": " page.",
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
        "benchmarks": "benchmarks", "traces": "traces", "domains": "domains",
    },
    "overview": {
        "title": "overview",
        "no_db_pre": "memory database not available - start the console with ",
        "no_db_post": " pointing at a minder memory sqlite.",
        "improve": "what to improve first",
        "evidence_through": "evidence through {as_of}",
        "stale_warn": "no new evidence for {age} - check"
                      " <a href=\"/capture\">capture</a> before trusting"
                      " this page.",
        "improve_gloss": "(past {window}, from observed evidence on this machine)",
        "window": "window:", "none_flagged_window": "nothing flagged in the past {window}.",
        "capture": "capture",
        "capture_gloss": "(is the watchdog recording? - the first thing to check)",
        "coverage": "coverage", "coverage_gloss": "tool calls saved",
        "sink_ready": "save path ready (sink)",
        "stores_fresh": "data sources fresh (stores)",
        "capture_detail": "capture detail", "warnings_suffix": "warning(s)",
        "weekly": "weekly workflow summary",
        "week_gloss": "past week, {since} to {until}",
        "episodes_opened": "episodes opened", "episodes_open_now": "episodes open now",
        "verified_resolution": "verified resolution",
        "verified_resolution_gloss": "(fixed with a confirmed fix)",
        "tool_failures": "tool failures",
        "repeat_keys": "repeat failure keys",
        "repeat_keys_gloss": "(the same error coming back)",
        "lessons_created": "lessons created (verified / candidate)",
        "injected": "lesson injections",
        "injected_gloss": "(times a lesson reached an agent / decisions"
                          " with no lesson to offer)",
        "unused_lessons": "verified lessons never injected",
        "open_gaps": "open gaps", "frontier_help": "frontier helpfulness",
        "frontier_help_gloss": "(did the outside model help?)",
        "decisions": "decision traces / overrides",
        "decisions_gloss": "(routing choices / human overrides)",
        "benchmarks": "benchmarks", "operator_focus": "operator focus",
        "nothing_flagged": "nothing flagged for this window.",
        "not_available": "not available.",
        "current_flags": "current flags",
        "flags_gloss": "(environment/systemd owned - display only)",
        "flag": "flag", "value": "value", "health": "health",
        "flag_unit": "environment flag",
        "schema_version": "schema version",
        "engine": "engine",
        "engine_gloss": "(which model server the proxy forwards to)",
        "active_engine": "active engine", "reuse": "prompt-cache reuse",
        "reuse_gloss": "(cached share of prompt tokens, past {window})",
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
        "unit": "observed event",
        "empty": "no observed events for this filter",
        "empty_suffix": "(nothing has failed in the stored window)",
    },
    "episodes": {
        "title": "episodes", "col_id": "id", "col_opened": "opened",
        "col_status": "status", "col_repo": "repo", "col_task": "task",
        "unit": "episode", "empty": "not available.",
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
        "filter_label": "lesson status filter",
        "unit": "lesson",
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
    "injections": {
        "title": "injections",
        "unit": "injection",
        "col_when": "when", "col_session": "session",
        "col_failure_key": "failure_key", "col_tier": "tier",
        "col_mode": "path", "col_chars": "chars",
        "none": "never injected - no lesson injection has carried this"
                " lesson yet.",
        "not_available": "not available - this store predates the"
                         " injection ledger.",
        "note": "one row per decision that considered this lesson; the"
                " tier names how retrieval matched it.",
    },
    "gaps": {
        "title": "skill gaps", "gloss": "(open)", "col_id": "id",
        "col_ts": "ts", "col_type": "type", "col_repo": "repo",
        "col_failure_key": "failure_key", "col_sample": "sample",
        "unit": "open skill gap", "empty": "not available.",
    },
    "skills": {
        "title": "skill index",
        "gloss": "(live - metadata only, bodies stay operator-owned)",
        "col_name": "name", "col_description": "description",
        "col_triggers": "triggers", "col_risk": "risk",
        "col_body": "body", "ok": "ok", "missing": "missing",
        "unit": "skill", "empty": "not available.",
    },
    "engine": {
        "title": "engines", "col_engine": "engine",
        "col_upstream": "upstream", "col_unit": "unit",
        "col_unit_state": "unit state", "col_health": "upstream health",
        "col_switch": "switch", "active": "(active)",
        "unit": "engine",
        "healthy": "healthy", "unhealthy": "unhealthy",
        "current": "current", "switch_to": "switch to {name}",
        "switch": "switch",
        "switch_hint": "opens a confirm step; nothing changes until you"
                       " confirm it",
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
        "session_unit": "captured session", "store_unit": "state store",
        "sandbox_unit": "sandbox mode",
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
        "unit": "frontier consult",
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
        "empty": "not available.", "unit": "gateway decision",
        "note": "nothing in policy reads this table; traces separate the"
                " model recommendation from the final policy decision.",
    },
    "benchmarks": {
        "title": "benchmarks", "suites": "suites",
        "col_suite": "suite", "col_manifest": "manifest",
        "col_tasks": "tasks", "col_fingerprint": "fingerprint",
        "col_status": "status", "suites_empty": "no suites found.",
        "unit": "benchmark suite", "baseline_unit": "pinned baseline",
        "baselines": "pinned baselines",
        "col_generated_at": "generated_at", "col_runs": "runs",
        "col_verified_rate": "verified rate",
        "baselines_empty": "no pinned baseline (created only via <code>minder-op"
                           " benchmark baseline create --yes</code>).",
        "note": "comparison verdicts come from <code>minder-op benchmark"
                " compare</code>; improvement claims require a pinned baseline"
                " comparison, never console activity.",
    },
    "engine_confirm": {
        "title": "switch the engine?",
        "lead": "this stops one running unit and starts another",
        "body": "<code>{name}</code> will take the unit now held by the"
                " active engine. The unit is {unit} and its state is"
                " {upstream}. Nothing else in the console writes.",
        "confirm": "switch to {name}",
        "cancel": "cancel - keep the current engine",
        "note": "a running unit can take a moment to hand over; the"
                " engines page shows the new state once it settles.",
        "unknown": "no engine named <code>{name}</code> in this console,"
                   " so nothing was switched.",
        "back": "back to engines",
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
        "unit": "stored trace review",
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
        "finding_unit": "finding", "feedback_unit": "feedback entry",
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
