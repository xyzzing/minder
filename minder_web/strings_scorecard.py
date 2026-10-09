"""Scorecard page copy (issue #8).

The scorecard section of the console copy, kept in its own module so
the main strings module stays under the C2 line budget. `app.py` merges
it into the Jinja global as `S.scorecard`; same C3 rules as
minder_web.strings_base.
"""

SCORECARD = {
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
}
