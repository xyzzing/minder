"""Budget-enforcement probe (issue #45, room runtime PRD M1, finding F2).

`thinking_budget_supported` in the caps means "not HTTP 400", not
"enforced": a server can accept the kwarg and ignore it. This probe
measures enforcement with one request whose prompt reliably elicits
long reasoning under a tiny budget, then reads the reasoning size back
from usage (exact), reasoning_content length (estimate, /4), or gives
up (null - unmeasurable, never guessed).

Called by adapter.run_cap only when mechanism == "kwargs" and budgets
are accepted; a softswitch mechanism has no budget to enforce, so the
probe never runs there. Pure with respect to minder: it takes the
`post` callable and returns a caps fragment.
"""
from __future__ import annotations

from minder_decision.room import ROOM_BUDGET_SLACK

# a prompt that reliably elicits long chain-of-thought from a thinking
# model; deliberately arithmetic-heavy so it cannot be answered short
ROOM_PROBE_PROMPT = (
    "Work through this step by step, showing every intermediate "
    "product. Compute 17*23, then 89*46, then add both products, then "
    "divide the sum by 7, then multiply that quotient by 13, then "
    "subtract 412, and finally state the result.")
PROBE_MAX_TOKENS = 1024
PROBE_BUDGET = 64
# token estimate divisor for reasoning_content length
CHARS_PER_TOKEN = 4


def probe_budget_enforcement(post, slack=ROOM_BUDGET_SLACK):
    """One enforcement request via `post(body) -> (status, parsed)`.

    Returns the caps fragment {"thinking_budget_enforced": bool|None,
    "budget_probe": {...}} or None when the request itself failed
    (probe not applicable - transport error or non-200).
    """
    body = {
        "model": "default",
        "messages": [{"role": "user", "content": ROOM_PROBE_PROMPT}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": True,
                                 "thinking_budget": PROBE_BUDGET},
    }
    try:
        status, resp = post(body)
    except Exception:  # noqa: BLE001 - probe must not break CAP
        return None
    if status != 200 or not isinstance(resp, dict):
        return None
    reasoning, source = _reasoning_size(resp)
    enforced = None if reasoning is None else \
        reasoning <= PROBE_BUDGET * slack + 32
    return {"thinking_budget_enforced": enforced,
            "budget_probe": {"budget": PROBE_BUDGET,
                             "reasoning_tokens": reasoning,
                             "max_tokens": PROBE_MAX_TOKENS,
                             "source": source}}


def _reasoning_size(resp):
    """(reasoning tokens, source) from usage, else estimated from
    reasoning_content length, else (None, "none")."""
    usage = resp.get("usage")
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and \
                isinstance(details.get("reasoning_tokens"), int):
            return details["reasoning_tokens"], "usage"
    try:
        message = resp["choices"][0]["message"]
        content = message.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, "none"
    if content:
        return len(content) // CHARS_PER_TOKEN, "reasoning_content"
    return None, "none"
