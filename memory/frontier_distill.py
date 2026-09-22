"""Distil a candidate lesson from a locally verified frontier consult
(docs/minder-phase-4-7-frontier-coding.md P4.2).

Hard laws:
- only helpfulness in {helpful, partial}, with the episode verified OR the
  consult verification_status pass (gate via frontier_policy.may_distill);
- instruction text comes from accepted_actions / distilled_json ONLY —
  raw provider response text is never read, and frontier model names are
  stripped from any action line;
- the result is a CANDIDATE lesson. retrieve_lessons filters
  status='verified', so a candidate is inert until an operator promotes it
  through lessons.promote_lesson (which still demands tests_passed);
- verified direct-from-distill is allowed ONLY for actor="operator" with
  local tests evidence on the episode, and then only by delegating to
  promote_lesson itself (same gates);
- candidates get NO graph projection — a candidate Lesson node could poison
  retrieval's supersede-based authority checks;
- never writes SKILLS.md or skill_candidates.

Never raises — returns lesson_id or None.
"""
import json
import re

from . import db as _db
from . import frontier_policy
from . import store
from .frontier_redaction import redact_for_profile
from .store import _now, _uid

# P4.2 rule 5: a frontier model attribution must not leak into instruction
_MODEL_ATTR_RE = re.compile(
    r"\b(?:per|from|by|via|according to)\s+"
    r"(deepseek[\w.-]*|gpt[- ]?[\w.]*|o\d[\w.-]*(?:mini|preview)?|"
    r"claude[\w.-]*|qwen[\w.-]*|llama[\w.-]*|mistral[\w.-]*|"
    r"gemini[\w.-]*|kimi[\w.-]*|glm[\w.-]*)\b", re.IGNORECASE)


def distill_lesson_from_consult(trace_id, episode_id, actor="system",
                                db_path=None):
    try:
        from . import frontier_traces
        trace = frontier_traces.get_consult(trace_id, db_path=db_path)
        if not trace:
            return None
        ep = store.get_episode(episode_id, db_path)
        ep_status = ep.get("status") if ep else None
        if not frontier_policy.may_distill(trace, episode_status=ep_status):
            return None
        instruction = _instruction_from_actions(trace)
        if not instruction:
            return None
        if actor == "operator" and ep_status == "verified" \
                and _tests_evidence(episode_id, db_path):
            # verified allowed — but only through the standard gates
            lesson, status = _promote_via_gates(
                episode_id, instruction, trace, ep, db_path)
            return lesson["lesson_id"] if lesson and status == "ok" else None
        return _insert_candidate(trace, episode_id, instruction, ep,
                                 actor, db_path)
    except Exception:
        return None


def _instruction_from_actions(trace):
    """accepted_actions win; distilled_json is the fallback. Redacted,
    model attributions stripped, one '- ' bullet per action."""
    for key in ("accepted_actions_json", "distilled_json"):
        raw = trace.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        if not raw:
            continue
        lines = []
        for action in raw:
            text = _MODEL_ATTR_RE.sub("", redact_for_profile(str(action)))
            text = " ".join(text.split())
            if text:
                lines.append("- " + text)
        if lines:
            return "\n".join(lines)
    return ""


def _tests_evidence(episode_id, db_path):
    """Local evidence that the episode's fix passed tests: a recorded
    tool_success event, or an explicit tests_passed payload."""
    for e in store.episode_events(episode_id, db_path=db_path):
        if e.get("event_type") == "tool_success":
            return True
        if '"tests_passed": true' in str(e.get("payload_json") or ""):
            return True
    return False


def _promote_via_gates(episode_id, instruction, trace, ep, db_path):
    from . import lessons
    return lessons.promote_lesson(
        episode_id, instruction,
        verification={"tests_passed": True, "source": "frontier-distill",
                      "trace_id": trace.get("trace_id")},
        repo=ep.get("repo") or "",
        failure_key=trace.get("failure_key") or "",
        actor="operator", db_path=db_path)


def _insert_candidate(trace, episode_id, instruction, ep, actor, db_path):
    failure_key = trace.get("failure_key") or ""
    if not failure_key:
        for e in store.episode_events(episode_id, db_path):
            if e.get("failure_key"):
                failure_key = e["failure_key"]
                break
    lid = _uid("les")
    conn = _db.connect(db_path)
    try:
        _db.write(conn, "INSERT INTO lessons (lesson_id, repo,"
                  " failure_key, instruction, anti_pattern,"
                  " verification_json, status, source_episode,"
                  " valid_from, valid_to, expires_when)"
                  " VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?, ?, NULL, ?)",
                  (lid, ep.get("repo") or "", failure_key, instruction, "",
                   json.dumps({"actor": actor, "source": "frontier-distill",
                               "trace_id": trace.get("trace_id"),
                               "helpfulness": trace.get("helpfulness")}),
                   episode_id, _now(), ""))
        return lid
    finally:
        conn.close()
