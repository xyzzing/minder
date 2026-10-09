"""Session detail page copy (issue #8).

The single-session view's section of the console copy, kept in its own
module so the main strings module stays under the C2 line budget.
`app.py` merges it into the Jinja global as `S.session`; same C3 rules
as minder_web.strings_base.
"""

SESSION = {
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
}
