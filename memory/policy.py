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
import time

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

# Phase 5.5-G shadow debounce: at most one decision call per
# (session_id, failure_key) per 5s. Process-local by design.
DECISION_DEBOUNCE_SECONDS = 5.0
_DECISION_DEBOUNCE = {}


def evaluate(event, warden_out=None, cfg=None, db_path=None,
             threshold=None, repo=None):
    """Returns a block directive dict, or None (let the Warden speak).
    Never raises. P5: with MINDER_CLASSIFIER=shadow a log-only shadow
    classification is recorded after the decision; it can never change
    the returned directive. P6: MINDER_ASSIST turns on one narrow,
    digest-only automation at a time (default off = Phase 2-3 exactly)."""
    directive = _evaluate(event, warden_out=warden_out, cfg=cfg,
                          db_path=db_path, threshold=threshold, repo=repo)
    directive = _assist(event, warden_out, directive, cfg=cfg,
                        db_path=db_path, threshold=threshold, repo=repo)
    _shadow_observe(event, warden_out, directive, db_path=db_path)
    _decision_shadow(event, warden_out, directive, db_path=db_path)
    return directive


def _assist(event, warden_out, directive, cfg=None, db_path=None,
            threshold=None, repo=None):
    """P6.1 flag wiring. shadow_suggest: append a CLASSIFIER_SUGGEST line
    to a guard digest when the latest shadow row for this failure is a
    high-confidence environment/permissions call — digest-only. retrieve:
    when the guard declined but the failure is repeated, attach the top
    verified lesson to the Warden digest without changing its action.
    Any problem returns the directive untouched."""
    try:
        from .egress import (SHADOW_SUGGEST_CLASSES,
                             SHADOW_SUGGEST_MIN_CONFIDENCE, assist_mode)
        mode = assist_mode()
        if mode == "decision_skill":
            # Phase 6.5: the ONLY place the gateway may change a digest
            return _decision_skill_assist(event, warden_out, directive,
                                          cfg=cfg, db_path=db_path,
                                          threshold=threshold, repo=repo)
        if mode == "shadow_suggest" and directive is not None:
            line = _classifier_suggest_line(
                (directive.get("duplicate") or {}).get("failure_key"),
                SHADOW_SUGGEST_CLASSES, SHADOW_SUGGEST_MIN_CONFIDENCE,
                db_path)
            if line:
                return dict(directive, digest=directive["digest"] + line)
        if mode == "retrieve" and directive is None:
            return _retrieve_passthrough(event, warden_out, cfg=cfg,
                                         db_path=db_path, threshold=threshold,
                                         repo=repo)
        return directive
    except Exception:
        return directive


def _classifier_suggest_line(fkey, allowed_classes, min_confidence, db_path):
    if not fkey:
        return ""
    from . import db as _db
    conn = _db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT failure_class, recommended_action, confidence"
            " FROM classifier_shadow WHERE failure_key = ?"
            " ORDER BY ts DESC LIMIT 1", (fkey,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    try:
        confidence = float(row["confidence"] or 0.0)
    except (TypeError, ValueError):
        return ""
    if confidence < min_confidence or row["failure_class"] not in allowed_classes:
        return ""
    return (f"\n- CLASSIFIER_SUGGEST: {row['failure_class']} → "
            f"{row['recommended_action']} (shadow {confidence:.2f}, "
            f"advisory only — the ladder is unchanged)")


def _retrieve_passthrough(event, warden_out, cfg=None, db_path=None,
                          threshold=None, repo=None):
    """MINDER_ASSIST=retrieve: attach the top verified lesson to the
    Warden digest on a repeated failure the guard declined to block
    (typically: the agent supplied a new hypothesis). Digest-only — the
    Warden action, level and frontier payload pass through untouched."""
    try:
        digest = (warden_out or {}).get("digest")
        if not digest:
            return None
        cfg = cfg if cfg is not None else minder.cfg()
        threshold = int(threshold or cfg.get("memory_fail_threshold",
                                             DEFAULT_THRESHOLD))
        ev = _as_event(event)
        if ev.get("event_type") != "tool_failure":
            return None
        fkey = ev.get("failure_key") or canon.failure_key(ev, repo)
        if not fkey or fkey == "unknown|unknown|none|none":
            return None
        count = store.count_attempts(fkey, None, db_path=db_path)
        if count < threshold:
            return None
        lessons = retrieve_lessons(repo or ev.get("repo") or "", fkey,
                                   db_path=db_path, limit=1)
        if not lessons:
            return None
        line = (f"\n- VERIFIED LESSON from a past resolved episode: "
                f"{lessons[0]['instruction']}")
        if lessons[0].get("anti_pattern"):
            line += f"\n- Anti-pattern: {lessons[0]['anti_pattern']}"
        return {"action": (warden_out or {}).get("action"),
                "level": (warden_out or {}).get("level"),
                "digest": digest + line,
                "frontier_payload": (warden_out or {}).get("frontier_payload"),
                "assist": "retrieve",
                "duplicate": {"failure_key": fkey, "attempts": count,
                              "lesson": lessons[0]}}
    except Exception:
        return None


def _evaluate(event, warden_out=None, cfg=None, db_path=None,
              threshold=None, repo=None):
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


def _shadow_observe(event, warden_out, directive, db_path=None):
    """P5 shadow mode: record what a classifier would say next to what
    Minder actually did. Log-only, best-effort, and off unless
    MINDER_CLASSIFIER=shadow."""
    try:
        from . import classifier
        clf = classifier.get_classifier(db_path=db_path)
        if clf is None:
            return
        ev = _as_event(event)
        fkey = ev.get("failure_key") or canon.failure_key(ev)
        try:
            same = store.count_attempts(fkey, None, db_path=db_path)
        except Exception:
            same = None
        compact = {
            "tool": ev.get("tool"),
            "exit_code": ev.get("exit_code"),
            "failure_key": fkey,
            "error_excerpt": ev.get("error_excerpt"),
            "same_failure_count": (directive or {}).get("duplicate", {}).get(
                "attempts", same),
            "previous_route": (warden_out or {}).get("action"),
            "untrusted_content_present": False,
        }
        policy_action = ((directive or {}).get("action")
                         or (warden_out or {}).get("action") or "pass")
        clf.classify(compact, policy_action=policy_action,
                     event_id=ev.get("event_id"))
    except Exception:
        pass


def _decision_shadow(event, warden_out, directive, db_path=None):
    """Phase 5.5-G shadow glue: run the decision assess loop AFTER the
    Warden and the memory policy, write a DecisionTrace, and change
    NOTHING. Off entirely unless MINDER_DECISION selects a provider.
    Debounced to at most one call per (session_id, failure_key) per
    DECISION_DEBOUNCE_SECONDS. Digest and action stay bitwise-equivalent
    to Phase 5 apart from new log/trace rows."""
    try:
        from decision import client as dclient
        clf = dclient.get_decision_client()
        if clf is None:
            return
        from decision import contracts as dcontracts
        from decision import menu as dmenu
        from decision import policy_gate as dgate
        from decision import state as dstate
        from decision import trace as dtrace
        ev = _as_event(event)
        fkey = ev.get("failure_key") or canon.failure_key(ev)
        if not fkey or fkey == "unknown|unknown|none|none":
            return
        session = ev.get("session_id") or ev.get("task_id") or "default"
        now = time.monotonic()
        debounce_key = (str(session), str(fkey))
        last = _DECISION_DEBOUNCE.get(debounce_key)
        if last is not None and (now - last) < DECISION_DEBOUNCE_SECONDS:
            return
        # cross-process layer: hook.py runs a fresh process per event, so
        # the in-memory dict alone would never debounce in production
        from decision import trace as dtrace
        recent = dtrace.latest_for_failure_key(fkey, session_id=str(session),
                                               db_path=db_path)
        if recent and _ts_age_seconds(recent.get("ts")) \
                < DECISION_DEBOUNCE_SECONDS:
            _DECISION_DEBOUNCE[debounce_key] = now
            return
        _DECISION_DEBOUNCE[debounce_key] = now

        warden = warden_out or {}
        wlevel = warden.get("level") or 0
        l3 = warden.get("action") == "alarm"
        frontier_allowed = bool(warden.get("frontier_payload")) \
            or warden.get("action") == "frontier"
        blocked = ("think_retry",) if directive else ()
        menu = dmenu.build_menu(level=wlevel, l3_fired=l3,
                                frontier_allowed=frontier_allowed,
                                blocked_action_ids=blocked)
        try:
            count = store.count_attempts(fkey, None, db_path=db_path)
        except Exception:
            count = None
        state = dstate.build_state(
            goal=ev.get("task_id") or "",
            failure_key=fkey, count=count,
            excerpt=ev.get("error_excerpt") or "",
            available_action_ids=menu.action_ids,
            constraints={"l3_fired": l3, "egress": "local",
                         "budget_left": True},
            fingerprints=([ev["action_fingerprint"]]
                          if ev.get("action_fingerprint") else []),
            verification=None)
        contract = dcontracts.failure_triage_contract(menu.action_ids)
        response = clf.system_one(state, contract.questions,
                                  contract=contract)
        decision = dgate.gate(response, menu, contract)
        dtrace.record_decision(
            db_path=db_path, contract_id=contract.contract_id,
            contract_version=contract.version, session_id=str(session),
            failure_key=fkey, state=state, menu=menu.action_ids,
            decision=decision, response=response)
    except Exception:
        pass


def _ts_age_seconds(ts):
    """Age of an ISO timestamp in seconds; None when unparseable."""
    try:
        from datetime import datetime, timezone
        moment = datetime.fromisoformat(str(ts))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - moment).total_seconds()
    except (TypeError, ValueError):
        return None


# Phase 6.5 gate 6: attach is for inspect/retrieve/environment-class
# skills — never a code-edit-everything skill.
DECISION_SKILL_MIN_APPLIES = 0.70
DECISION_SKILL_MIN_CONFIDENCE = 0.70
_SAFE_NAME_PREFIXES = ("inspect", "diagnose", "retrieve", "check",
                       "verify", "environment", "schema")


def _low_risk_skill(skill):
    if not skill:
        return False
    if str(skill.get("risk_level") or "").lower() == "high":
        return False
    if str(skill.get("risk_level") or "").lower() == "low":
        return True
    name = str(skill.get("name") or "").lower()
    return any(name.startswith(prefix) for prefix in _SAFE_NAME_PREFIXES)


def _decision_skill_assist(event, warden_out, directive, cfg=None,
                           db_path=None, threshold=None, repo=None):
    """Phase 6.5: MINDER_ASSIST=decision_skill. The gateway may inject
    ONE low-risk SKILL digest, only when ALL gates pass:

    1. duplicate-guard or repeated failure (never the first failure);
    2. best_skill is in a shortlist of <=3 LIVE index ids (not "none");
    3. any_skill_applies >= 0.70;
    4. choice confidence >= 0.70;
    5. the skill is live and loadable (existing index/invalidation
       filters — superseded/missing entries never resolve);
    6. low-risk inspect/retrieve/environment-class skill only;
    7. the failure-triage policy gate did not override to human;
    8. digest injection ONLY — 'SKILL: <name>' + first 8-12 lines, no
       tool execution;
    9. never frontier_consult — the attach cannot create a consult and
       frontier_payload passes through untouched;
    10. body missing -> no attach, no crash.

    Warden stays authoritative: without a client (MINDER_DECISION unset)
    or with NullClient, nothing attaches."""
    try:
        from decision import client as dclient
        clf = dclient.get_decision_client()
        if clf is None:  # no gateway, no attach
            return directive
        from decision import contracts as dcontracts
        from decision import menu as dmenu
        from decision import policy_gate as dgate
        from decision import skill_select as dskill
        from decision import state as dstate
        from decision import trace as dtrace

        ev = _as_event(event)
        if ev.get("event_type") != "tool_failure":
            return directive
        fkey = ev.get("failure_key") or canon.failure_key(ev, repo)
        if not fkey or fkey == "unknown|unknown|none|none":
            return directive
        cfg = cfg if cfg is not None else minder.cfg()
        threshold = int(threshold or cfg.get("memory_fail_threshold",
                                             DEFAULT_THRESHOLD))
        count = store.count_attempts(fkey, None, db_path=db_path)
        if count < max(threshold, 2):  # gate 1: not a first failure
            return directive

        shortlist = dskill.build_skill_shortlist(ev)  # gate 2 (live index)
        if not shortlist:
            return directive
        contract = dskill.skill_select_response_contract(shortlist)
        state = dstate.build_state(
            goal=ev.get("task_id") or "", failure_key=fkey, count=count,
            excerpt=ev.get("error_excerpt") or "",
            available_action_ids=tuple(shortlist) + ("none",))
        response = clf.system_one(state, contract.questions,
                                  contract=contract)
        try:  # gates 3+4: the 0.70 floors
            applies = float(response.noul_probs.get("any_skill_applies")
                             or 0.0)
        except (TypeError, ValueError):
            applies = 0.0
        if applies < DECISION_SKILL_MIN_APPLIES:
            return directive
        try:
            confidence = float(response.confidence or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < DECISION_SKILL_MIN_CONFIDENCE:
            return directive
        chosen = dskill.decide_skill(response, shortlist, contract)
        if not chosen:  # "none", out-of-shortlist, tied, or invalid
            return directive
        skill = dskill.load_selected_skill(chosen)  # gates 5+10
        if not skill:
            return directive
        if not _low_risk_skill(skill):  # gate 6
            return directive

        # gate 7: the triage gate must not have overridden to human
        warden = warden_out or {}
        l3 = warden.get("action") == "alarm"
        triage_menu = dmenu.build_menu(
            level=warden.get("level") or 0, l3_fired=l3,
            frontier_allowed=bool(warden.get("frontier_payload")))
        triage_contract = dcontracts.failure_triage_contract(
            triage_menu.action_ids)
        triage_state = dstate.build_state(
            goal=ev.get("task_id") or "", failure_key=fkey, count=count,
            excerpt=ev.get("error_excerpt") or "",
            available_action_ids=triage_menu.action_ids)
        triage_response = clf.system_one(triage_state,
                                         triage_contract.questions,
                                         contract=triage_contract)
        triage_decision = dgate.gate(triage_response, triage_menu,
                                     triage_contract)
        if triage_decision.policy_decision == "human" or l3:
            return directive

        lines = str(skill.get("instructions") or "").splitlines()
        excerpt = "\n".join(lines[:_SKILL_BODY_LINES]).strip()
        if not excerpt:  # gate 8 has nothing compact to inject
            return directive
        section = f"\nSKILL: {skill.get('name') or chosen}\n{excerpt}"
        dtrace.record_decision(  # reuse DecisionTrace, log-only
            db_path=db_path, contract_id=contract.contract_id,
            contract_version=contract.version,
            session_id=str(ev.get("session_id") or ""), failure_key=fkey,
            state=state, menu=list(shortlist) + ["none"],
            decision=triage_decision, response=response)
        # gate 9: this returns digest text only; frontier_payload is
        # never created or modified here
        if directive:
            return dict(directive, digest=directive["digest"] + section,
                        assist="decision_skill")
        digest = warden.get("digest")
        if not digest:
            return directive
        return {"action": warden.get("action"),
                "level": warden.get("level"),
                "digest": digest + section,
                "frontier_payload": warden.get("frontier_payload"),
                "assist": "decision_skill",
                "duplicate": {"failure_key": fkey, "attempts": count,
                              "lesson": None}}
    except Exception:
        return directive


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
