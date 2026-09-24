"""Hook event → memory records (docs/prd-memory-v1.md PR 5).

Production path: hook.py calls record() BEFORE minder.process() so the
duplicate guard can read fresh counts, and never lets a memory failure
reach the directive (constraint 10). Success events close the open episode:
'verified' only when the payload carries an explicit verification with
tests_passed=true, otherwise 'candidate' (never auto-verify — plan PR 5
test 3).
"""
import json

import minder
from . import canonicalise as canon
from . import store


def to_event(hook_ev, cfg=None):
    """Raw hook payload (or canonical event) → canonical memory event dict.
    Failure classification uses the Warden's own is_failure signs so memory
    and ladder never disagree about what a failure is."""
    if not isinstance(hook_ev, dict):
        return {}
    if hook_ev.get("event_type"):
        return dict(hook_ev)
    resp = hook_ev.get("tool_response", "")
    text = resp if isinstance(resp, str) else json.dumps(resp, default=str)
    args = hook_ev.get("tool_input") or {}
    if not isinstance(args, dict):
        args = {}
    cfg = cfg if cfg is not None else minder.cfg()
    failed = minder.is_failure(text, cfg.get("fail_signs_extra"))
    return {
        "event_type": "tool_failure" if failed else "tool_success",
        "tool": hook_ev.get("tool_name") or "",
        "session_id": hook_ev.get("session_id") or "",
        "task_id": hook_ev.get("session_id") or "",
        "repo": str(hook_ev.get("repo") or hook_ev.get("cwd") or ""),
        "command": str(args.get("command", "")),
        "args_json": json.dumps(canon_redact_args(args)),
        "file_path": str(args.get("file_path", "") or args.get("path", "")),
        "error_excerpt": text,
        "content_hash": str(hook_ev.get("content_hash") or ""),
        "hypothesis": str(hook_ev.get("hypothesis") or ""),
        "payload_json": json.dumps(_redacted_payload(hook_ev)),
    }


def canon_redact_args(args):
    """Args kept for evidence, API-key-shaped values redacted."""
    out = {}
    for k, v in (args or {}).items():
        s = v if isinstance(v, str) else json.dumps(v, default=str)
        out[k] = canon.redact(str(s))
    return out


def _redacted_payload(hook_ev):
    keep = {k: hook_ev.get(k) for k in
            ("hook_event_name", "tool_name", "session_id", "cwd") }
    blob = json.dumps(keep, default=str)
    return json.loads(canon.redact(blob))


def record(hook_ev, db_path=None, cfg=None):
    """Record one hook observation and maintain episodes. Returns a small
    status dict; never raises."""
    out = {"recorded": False, "event_id": None, "episode_id": None,
           "closed": None}
    try:
        ev = to_event(hook_ev, cfg)
        if not ev.get("tool"):
            return out  # nothing identifiable — nothing recorded
        if ev.get("event_type") != "tool_failure":
            out = _close_on_success(hook_ev, ev, out, db_path)
            _observe_success(ev, out, db_path)
            return out
        ev["failure_key"] = canon.failure_key(ev, ev.get("repo"))
        ev["action_fingerprint"] = canon.action_fingerprint(ev, ev.get("repo"))
        ep = store.find_open_episode(ev.get("task_id") or
                                     ev.get("session_id"), db_path)
        if not ep:
            ep_id, _ = store.open_episode(ev, db_path)
            out["episode_id"] = ep_id
        else:
            ep_id = ep["episode_id"]
            out["episode_id"] = ep_id
        # add_attempt records AND links — exactly one insert per observation
        eid, status = store.add_attempt(ep_id, ev, db_path)
        if status != "ok":
            out["status"] = status
            return out
        out["recorded"] = True
        out["event_id"] = eid
        # Skill-gap check (PR 6): does this failure match any known skill
        # trigger? If not, and it is environmental or repeating, record a
        # skill_gaps row. Never raises; never writes SKILLS.md; never
        # escalates.
        try:
            from . import skills as _skills
            gap_result = _skills.check_skill_gap(ev, db_path=db_path)
            if gap_result.get("recorded"):
                out["skill_gap"] = True
            if gap_result.get("skill"):
                out["skill_match"] = gap_result["skill"]
        except Exception:
            pass
        return out
    except Exception as e:
        out["status"] = f"degraded:{type(e).__name__}"
        return out


_LAST_SUCCESS_ADVISORY = None


def _observe_success(ev, out, db_path):
    """Success-loop guard (flag-gated, docs/success-loop-guard-design.md):
    record the normalized action+result signature and stash an advisory
    for the hook's model-visible stderr channel. Fails open; without
    MINDER_SUCCESS_GUARD=advisory this does nothing at all."""
    global _LAST_SUCCESS_ADVISORY
    try:
        from . import success_guard
        if not success_guard.guard_enabled():
            return
        repo = ev.get("repo") or ""
        result = success_guard.observe(
            ev.get("session_id") or "unknown", ev.get("tool") or "",
            canon.action_fingerprint(ev, repo),
            ev.get("exit_code", 0), str(ev.get("tool_response", "")),
            db_path=db_path)
        out["success_advisory"] = result.get("advisory")
        _LAST_SUCCESS_ADVISORY = result.get("advisory")
    except Exception:
        pass


def success_advisory():
    """Advisory from the most recent successful record() in this hook
    process, or None. Read only when MINDER_SUCCESS_GUARD=advisory."""
    from . import success_guard
    if not success_guard.guard_enabled():
        return None
    return _LAST_SUCCESS_ADVISORY


def _close_on_success(hook_ev, ev, out, db_path):
    try:
        ep = store.find_open_episode(ev.get("task_id") or
                                     ev.get("session_id"), db_path)
        if not ep:
            return out
        verification = hook_ev.get("verification") or {}
        if isinstance(verification, dict) and verification.get("tests_passed"):
            status = "verified"
        else:
            status = "candidate"
        closed_status, _ = store.close_episode(ep["episode_id"], status,
                                               db_path=db_path)
        out["closed"] = closed_status or status
        out["episode_id"] = ep["episode_id"]
        return out
    except Exception as e:
        out["status"] = f"degraded:{type(e).__name__}"
        return out
