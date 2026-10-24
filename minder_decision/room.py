"""Room accounting (issue #45, room runtime PRD M1).

Two pure functions and the constants they share. `reserve_budget`
guarantees a thinking budget never eats the answer's room under
`max_tokens`; `classify_outcome` maps a relayed response plus the room
that was applied to one ledger row. Both are pure: the proxy wires
them in fail-open wrappers and does the logging, so this module
imports nothing from minder and never sees a socket.

Measured basis (Assignment 1): bare-JSON answers peaked at 265
completion tokens, so the default reserve is comfortably above that;
`starved` answers (finish_reason length, nothing visible) were 23 of
50 under an xhigh default with a 2048 cap.
"""
from __future__ import annotations

# reserve carved out of max_tokens for the answer itself; configurable
# via answer_reserve_tokens, values below the floor clamp to the floor
ANSWER_RESERVE_TOKENS = 512
ROOM_RESERVE_FLOOR = 64
# smallest budget worth keeping when the reserve cannot be met
ROOM_MIN_BUDGET_TOKENS = 64
# probe tolerance: enforced within budget * slack + 32 tokens
ROOM_BUDGET_SLACK = 1.5


def _as_int(value):
    """int(value) for real ints and int-shaped values, else None."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reserve_budget(req, cfg=None):
    """Clamp `chat_template_kwargs.thinking_budget` so the answer keeps
    room under `max_tokens`. Returns (req, event-or-None); the caller
    logs the event. `max_tokens` is never raised - it is the caller's
    ceiling, not ours.

    budget <= max_tokens - reserve   -> unchanged
    else when that slice is at least ROOM_MIN_BUDGET_TOKENS
                                     -> budget = the slice (reason=reserve)
    else                             -> budget = max_tokens // 2
                                         (reason=reserve_unmet: a cap this
                                         small cannot fund both reasoning
                                         and an answer; halving is the
                                         honest split)
    """
    cfg = cfg or {}
    reserve = _as_int(cfg.get("answer_reserve_tokens"))
    if reserve is None:
        reserve = ANSWER_RESERVE_TOKENS
    reserve = max(ROOM_RESERVE_FLOOR, reserve)
    min_budget = _as_int(cfg.get("room_min_budget_tokens"))
    if min_budget is None:
        min_budget = ROOM_MIN_BUDGET_TOKENS
    ctk = req.get("chat_template_kwargs")
    budget = _as_int(ctk.get("thinking_budget")) \
        if isinstance(ctk, dict) else None
    cap = _as_int(req.get("max_tokens"))
    if budget is None or cap is None:
        return req, None
    allowed = cap - reserve
    if budget <= allowed:
        return req, None
    if allowed >= min_budget:
        new_budget, reason = allowed, "reserve"
    else:
        new_budget, reason = max(1, cap // 2), "reserve_unmet"
    ctk = dict(ctk)
    ctk["thinking_budget"] = new_budget
    req["chat_template_kwargs"] = ctk
    return req, {"event": "budget_clamped", "reason": reason,
                 "was": budget, "now": new_budget, "max_tokens": cap}


def classify_outcome(summary, applied=None):
    """Map one relayed response to a room_outcome ledger row.

    summary (built by the relay, never from message text):
      error         upstream error or no response      -> "error"
      finish_reason missing (passthrough, unparseable) -> "unknown"
      length, nothing visible, no tool calls           -> "starved"
      length                                            -> "truncated"
      otherwise                                         -> "fit"
    applied is the room the pipeline scheduled: effort, budget,
    max_tokens. `budget_used` is reasoning tokens over the budget, a
    number, when both exist.
    """
    summary = summary or {}
    applied = applied or {}
    if summary.get("error"):
        outcome = "error"
    elif summary.get("finish_reason") is None:
        outcome = "unknown"
    elif summary["finish_reason"] == "length":
        outcome = ("starved" if not summary.get("content_chars")
                   and not summary.get("tool_calls") else "truncated")
    else:
        outcome = "fit"
    budget = applied.get("budget")
    reasoning = summary.get("reasoning_tokens")
    row = {
        "outcome": outcome,
        "finish_reason": summary.get("finish_reason"),
        "content_chars": summary.get("content_chars") or 0,
        "reasoning_tokens": reasoning or 0,
        "completion_tokens": summary.get("completion_tokens") or 0,
        "effort": applied.get("effort"),
        "budget": budget,
        "max_tokens": applied.get("max_tokens"),
        "stream": bool(summary.get("stream")),
    }
    if budget and reasoning:
        row["budget_used"] = round(reasoning / budget, 2)
    return row
