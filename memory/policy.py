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
from . import from_hook
from . import plans
from . import skill_load
from . import store
from .retrieval import retrieve_lessons


def _as_event(ev):
    """Single conversion path for raw hook payloads (PR 5 unification)."""
    return from_hook.to_event(ev)


DEFAULT_THRESHOLD = 2


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
        return _directive(ev, fkey, count, cfg, warden_out, repo=repo,
                          db_path=db_path)
    except Exception:
        return None


_SKILL_BODY_LINES = 10  # compact: first N lines of instructions in digest


def _skill_section(ev, repo, db_path):
    """P2.5: 'SKILL: <name>' + short instruction excerpt when a skill
    matches; None when nothing matches (the caller may draft a plan)."""
    try:
        selection = skill_load.select_skills_for_event(ev)
        if not selection["loaded"]:
            return None, selection.get("gap")
        skill = selection["loaded"][0]
        lines = str(skill.get("instructions") or "").splitlines()
        excerpt = "\n".join(lines[:_SKILL_BODY_LINES]).strip()
        section = f"SKILL: {skill['name']}"
        if excerpt:
            section += f"\n{excerpt}"
        return section, None
    except Exception:
        return None, None


def _plan_section(ev, fkey, repo, db_path):
    """P2.5: reuse the open temp plan for this failure or draft one;
    returns its TEMPORARY PLAN directive, or empty string."""
    try:
        ev_with_key = dict(ev, failure_key=fkey, repo=repo or
                           ev.get("repo") or "")
        existing = plans.open_plan_for(ev_with_key, db_path=db_path)
        if existing:
            return plans.plan_directive(existing["plan_id"],
                                        db_path=db_path)
        selection = skill_load.select_skills_for_event(ev_with_key)
        gap_type = (selection.get("gap") or {}).get("gap_type") or "unknown"
        plan_id = plans.create_temp_plan(ev_with_key, gap_type,
                                         db_path=db_path)
        if not plan_id:
            return ""
        return plans.plan_directive(plan_id, db_path=db_path)
    except Exception:
        return ""


def _directive(ev, fkey, count, cfg, warden_out, repo=None, db_path=None):
    task = ev.get("session_id") or ev.get("task_id") or "default"
    try:
        minder.log(task, "block_duplicate", key=fkey, n=count)
    except Exception:
        pass
    lesson_line = ""
    lessons = []
    try:
        lessons = retrieve_lessons(repo or ev.get("repo") or "", fkey,
                                   db_path=db_path, limit=1)
        if lessons:
            lesson_line = (f"\n- VERIFIED LESSON from a past resolved "
                           f"episode: {lessons[0]['instruction']}")
            if lessons[0].get("anti_pattern"):
                lesson_line += f"\n- Anti-pattern: {lessons[0]['anti_pattern']}"
    except Exception:
        lesson_line = ""
    digest = (f"{minder.DIGEST_MARKERS[1]} — duplicate guard: this exact "
              f"attempt already failed {count}x.\n"
              f"- {fkey}\n"
              f"- STOP repeating this action verbatim. Read the recorded "
              f"evidence first (python3 ~/.local/share/minder/minder.py "
              f"report), state a NEW root-cause hypothesis in plain text, "
              f"and change exactly one variable before the next attempt."
              f"{lesson_line}")
    # P2.5: prefer a matched skill's compact instructions; else a
    # TEMPORARY PLAN (existing or freshly drafted); else lesson-only.
    skill_section, _gap = _skill_section(ev, repo, db_path)
    if skill_section:
        digest = digest + "\n" + skill_section
    else:
        plan_section = _plan_section(ev, fkey, repo, db_path)
        if plan_section:
            digest = digest + "\n" + plan_section
    return {"action": "block_duplicate", "level": 1, "digest": digest,
            "frontier_payload": None,
            "duplicate": {"failure_key": fkey, "attempts": count,
                          "lesson": (lessons[0] if lesson_line else None)}}
