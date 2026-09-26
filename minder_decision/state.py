"""Compact decision state (Phase 5.5 slice B).

One bounded, redacted string (<= MAX_STATE_CHARS, target
TARGET_STATE_CHARS) carrying everything the assess loop may see: goal,
failure_key, attempt count, a <=200-char redacted excerpt, the live
available action ids, constraints (budgets / egress / L3), the last 3
action fingerprints, and verification flags. Never the full repo, full
logs, or frontier prompts.
"""
import json

from minder_memory.canonicalise import redact

MAX_STATE_CHARS = 2000
TARGET_STATE_CHARS = 1200
EXCERPT_CAP = 200
GOAL_CAP = 200
FINGERPRINTS_KEPT = 3


def _cap(text, cap):
    return redact(str(text or ""))[:cap]


def build_state(goal="", failure_key="", count=0, excerpt="",
                available_action_ids=(), constraints=None,
                fingerprints=(), verification=None):
    """Deterministic compact state string. Never raises; degrades by
    trimming."""
    try:
        payload = {
            "goal": _cap(goal, GOAL_CAP),
            "failure_key": _cap(failure_key, 200),
            "count": max(0, int(count or 0)),
            "excerpt": _cap(excerpt, EXCERPT_CAP),
            "available_action_ids": [str(a) for a in
                                     (available_action_ids or [])],
            "constraints": {
                "l3_fired": bool((constraints or {}).get("l3_fired")),
                "egress": _cap((constraints or {}).get("egress") or "local",
                               40),
                "budget_left": bool((constraints or {}).get("budget_left",
                                                            True)),
            },
            "recent_action_fingerprints": [str(f) for f in
                                           (fingerprints or [])][-FINGERPRINTS_KEPT:],
            "verification": _verification_flags(verification),
        }
        text = json.dumps(payload, sort_keys=True)
        if len(text) > TARGET_STATE_CHARS:  # shed the excerpt first
            payload["excerpt"] = ""
            text = json.dumps(payload, sort_keys=True)
        return text[:MAX_STATE_CHARS]
    except Exception:
        return json.dumps({"goal": "", "failure_key": _cap(failure_key, 200),
                           "count": 0, "excerpt": "",
                           "available_action_ids": [],
                           "constraints": {"l3_fired": False,
                                           "egress": "local",
                                           "budget_left": True},
                           "recent_action_fingerprints": [],
                           "verification": None})


def _verification_flags(verification):
    if not isinstance(verification, dict):
        return None
    return {str(k): bool(v) for k, v in sorted(verification.items())
            if k in ("tests_passed", "episode_verified", "lesson_verified")}
