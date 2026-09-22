"""Frontier distillation policy gate (docs/minder-phase-4-7-frontier-coding.md
P4.3).

may_distill is consumed ONLY by memory.frontier_distill — the Warden never
consults it, and no L0–L3 threshold changes. It restates, as one pure
predicate, the hard preconditions for turning a frontier consult into a
candidate lesson: the consult was locally helpful AND at least one of
(episode verified, consult verification pass) holds AND there is distilled
action text to build an instruction from. Never raises.
"""

HELPFUL_FOR_DISTILL = ("helpful", "partial")


def may_distill(trace, episode_status=None):
    try:
        if not isinstance(trace, dict):
            return False
        if trace.get("helpfulness") not in HELPFUL_FOR_DISTILL:
            return False
        if (trace.get("verification_status") != "pass"
                and episode_status != "verified"):
            return False
        return bool(_action_source(trace))
    except Exception:
        return False


def _action_source(trace):
    for key in ("accepted_actions_json", "distilled_json"):
        raw = trace.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
        if isinstance(raw, list) and raw:
            return raw
    return None
