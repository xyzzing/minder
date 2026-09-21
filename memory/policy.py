"""Duplicate-action guard (docs/prd-memory-v1.md PR 3).

An ADDITIONAL predicate beside the Warden ladder — never a replacement.
The Warden owns budgets/cooldowns/breakers and runs untouched; this module
runs afterwards in hook.py and replaces the directive only when the stored
evidence says the attempt is a verbatim repeat:

    IF same failure_key
    AND same action_fingerprint
    AND recorded attempts >= memory_fail_threshold (default 2)
    AND no new hypothesis / content change
    THEN block_duplicate (+ compact directive)
    ELSE the existing Warden ladder output stands.

Fail-open law (constraint 10): any store/config problem → None, the Warden
result stands. Reads only — the guard never writes Warden state; events are
recorded by memory.from_hook before minder.process() runs (PR 5).

Known v1 trade-off: when the guard preempts an escalation the Warden has
already booked its budget for this event; the guard does not reach into
Warden state. Blocked verbatim retries should not recur often enough for
the leak to matter.
"""
import json

import minder
from . import canonicalise as canon
from . import store

DEFAULT_THRESHOLD = 2


def _as_event(ev):
    """Accept a raw hook payload or a canonical memory event dict."""
    if not isinstance(ev, dict):
        return {}
    if ev.get("event_type"):
        return dict(ev)
    resp = ev.get("tool_response", "")
    text = resp if isinstance(resp, str) else json.dumps(resp, default=str)
    args = ev.get("tool_input") or {}
    if not isinstance(args, dict):
        args = {}
    failed = minder.is_failure(text, minder.cfg().get("fail_signs_extra"))
    return {
        "event_type": "tool_failure" if failed else "tool_success",
        "tool": ev.get("tool_name") or "",
        "session_id": ev.get("session_id") or "",
        "task_id": ev.get("session_id") or "",
        "command": str(args.get("command", "")),
        "args_json": json.dumps(args),
        "file_path": str(args.get("file_path", "")
                         or args.get("path", "")),
        "error_excerpt": text,
        "content_hash": str(ev.get("content_hash") or ""),
        "hypothesis": str(ev.get("hypothesis") or ""),
        "payload_json": "{}",
    }


def evaluate(event, warden_out=None, cfg=None, db_path=None,
             threshold=None, repo=None):
    """Returns a block directive dict, or None (let the Warden speak).
    Never raises."""
    try:
        cfg = cfg if cfg is not None else minder.cfg()
        threshold = int(threshold or cfg.get("memory_fail_threshold",
                                             DEFAULT_THRESHOLD))
        ev = _as_event(event)
        if ev.get("event_type") != "tool_failure":
            return None
        if ev.get("hypothesis") or ev.get("new_hypothesis"):
            return None  # the agent changed approach — the ladder may speak
        fkey = ev.get("failure_key") or canon.failure_key(ev, repo)
        fp = ev.get("action_fingerprint") or canon.action_fingerprint(ev, repo)
        if not fkey or fkey == "unknown|unknown|none|none":
            return None  # never block on an event we cannot identify
        count = store.count_attempts(fkey, fp, db_path=db_path)
        if count < threshold:  # includes -1 (store unavailable) → fail open
            return None
        return _directive(ev, fkey, count, cfg, warden_out)
    except Exception:
        return None


def _directive(ev, fkey, count, cfg, warden_out):
    task = ev.get("session_id") or ev.get("task_id") or "default"
    try:
        minder.log(task, "block_duplicate", key=fkey, n=count)
    except Exception:
        pass
    digest = (f"{minder.DIGEST_MARKERS[1]} — duplicate guard: this exact "
              f"attempt already failed {count}x.\n"
              f"- {fkey}\n"
              f"- STOP repeating this action verbatim. Read the recorded "
              f"evidence first (python3 ~/.local/share/minder/minder.py "
              f"report), state a NEW root-cause hypothesis in plain text, "
              f"and change exactly one variable before the next attempt.")
    return {"action": "block_duplicate", "level": 1, "digest": digest,
            "frontier_payload": None,
            "duplicate": {"failure_key": fkey, "attempts": count}}
