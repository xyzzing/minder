"""Lesson promotion + invalidation (docs/prd-memory-v1.md PR 4).

Promotion rule (v1): only from an episode whose status is `verified` AND
with verification.tests_passed. The evidence may arrive with the call or
already sit in the episode's event ledger — `_close_on_success` records a
`verification` event when a clean test run closes an episode (issue #9), so
an operator promoting that episode states the lesson, not the proof.
Frontier output is never a trusted lesson on its own (constraint 5); the
default promoter in tests is the operator fixture.
Never raises — returns (lesson_dict, status).
"""
import json

from . import db as _db
from . import lesson_decisions, store
from .store import _now, _uid


def _stored_verification(episode_id, db_path):
    """tests_passed evidence already recorded on the episode, or None."""
    for e in store.episode_events(episode_id, db_path=db_path):
        if e.get("event_type") != "verification":
            continue
        try:
            payload = json.loads(e.get("payload_json") or "{}")
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("tests_passed"):
            return payload
    return None


def promote_lesson(episode_id, instruction, anti_pattern="", verification=None,
                   repo="", failure_key="", actor="operator", db_path=None,
                   expires_when=""):
    try:
        ep = store.get_episode(episode_id, db_path)
        if not ep:
            return None, "rejected:no-such-episode"
        if ep.get("status") != "verified":
            return None, f"rejected:episode-status-{ep.get('status')}"
        verification = dict(verification or {})
        if not verification.get("tests_passed"):
            stored = _stored_verification(episode_id, db_path)
            if stored:
                verification = {**stored, **verification}
        if not verification.get("tests_passed"):
            return None, "rejected:no-verified-tests"
        if not failure_key:
            for e in store.episode_events(episode_id, db_path):
                if e.get("failure_key"):
                    failure_key = e["failure_key"]
                    break
        conn = _db.connect(db_path)
        try:
            lid = _uid("les")
            _db.write(conn, "INSERT INTO lessons (lesson_id, repo,"
                      " failure_key, instruction, anti_pattern,"
                      " verification_json, status, source_episode,"
                      " valid_from, valid_to, expires_when)"
                      " VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?, NULL, ?)",
                      (lid, repo or ep.get("repo"), failure_key,
                       str(instruction), str(anti_pattern or ""),
                       json.dumps({"actor": actor, **verification}),
                       episode_id, _now(), expires_when))
            out = _get(lid, conn)
        finally:
            conn.close()
        if out:  # P3.2 graph projection — additive, never blocks promotion
            try:
                from . import graph_project
                graph_project.project_lesson_promotion(out, db_path=db_path)
            except Exception:
                pass
        return out, "ok"
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def invalidate_lesson(lesson_id, reason, diagnosis=None, db_path=None):
    """Tombstone a lesson (valid_to set; status invalidated). Never raises.

    `diagnosis` is the closed `lesson_decisions.DIAGNOSES` code naming
    what went wrong (issue #14); `reason` stays as the operator's note. An
    out-of-taxonomy diagnosis is refused before anything is written, so a
    typo cannot half-invalidate a lesson.
    """
    code, bad = lesson_decisions.validate_diagnosis(diagnosis)
    if bad:
        return None, bad
    try:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "UPDATE lessons SET valid_to = ?, status ="
                      " 'invalidated', invalidated_diagnosis = ?"
                      " WHERE lesson_id = ?",
                      (_now(), code, lesson_id))
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                               (lesson_id,)).fetchone()
            if not row:
                return None, "rejected:no-such-lesson"
            out = dict(row)
            out["invalidated_reason"] = str(reason)
        finally:
            conn.close()
        lesson_decisions.record_decision(
            lesson_id, lesson_decisions.ACTION_INVALIDATE, code,
            note=str(reason or ""), db_path=db_path)
        return out, "ok"
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def reject_candidate_lesson(lesson_id, code, note="", actor="operator",
                            db_path=None):
    """Reject a frontier-distilled candidate with a closed reason code
    (issue #14). This is the other half of the queue: issue #10 made
    candidates reviewable but only adoption was recorded, so a rejected
    distillation left no countable trace.

    The candidate is tombstoned, not deleted - the unreviewed text stays
    in the ledger next to the decision that refused it. Never raises -
    returns (lesson_dict, status).
    """
    decision, bad = lesson_decisions.validate_code(code)
    if bad:
        return None, bad
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                               (lesson_id,)).fetchone()
            if not row:
                return None, "rejected:no-such-lesson"
            candidate = dict(row)
            if candidate.get("status") != "candidate":
                return None, ("rejected:lesson-status-"
                              f"{candidate.get('status')}")
            _db.write(conn, "UPDATE lessons SET valid_to = ?, status ="
                      " 'invalidated' WHERE lesson_id = ?",
                      (_now(), lesson_id))
            candidate["valid_to"] = _now()
            candidate["status"] = "invalidated"
        finally:
            conn.close()
        lesson_decisions.record_decision(
            lesson_id, lesson_decisions.ACTION_REJECT, decision,
            note=str(note or ""), actor=actor, db_path=db_path)
        return candidate, "ok"
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def _get(lesson_id, conn):
    row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                       (lesson_id,)).fetchone()
    return dict(row) if row else None


def adopt_candidate_lesson(lesson_id, instruction=None, actor="operator",
                           db_path=None):
    """Adopt a frontier-distilled candidate as a verified lesson (issue #10).

    The gates are `promote_lesson`'s, unchanged: the candidate's source
    episode must be `verified` and carry `verification.tests_passed`
    evidence, which issue #9's clean test run records. Nothing about the
    frontier counts as proof here; the operator is the actor and the
    episode's own test run is the evidence.

    The instruction defaults to the candidate's distilled text, which is
    what reviewing the queue is for; passing one replaces it. The candidate
    is tombstoned and replaced rather than updated in place, so the
    unreviewed text stays in the ledger next to the adopted lesson and its
    verification keeps the trace_id it was distilled from. Never raises -
    returns (lesson_dict, status).
    """
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                               (lesson_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None, "rejected:no-such-lesson"
        candidate = dict(row)
        if candidate.get("status") != "candidate":
            return None, f"rejected:lesson-status-{candidate.get('status')}"
        episode_id = candidate.get("source_episode") or ""
        text = str(instruction or "").strip() or str(
            candidate.get("instruction") or "").strip()
        if not text:
            return None, "rejected:no-instruction"
        lesson, status = promote_lesson(
            episode_id, text, anti_pattern=candidate.get("anti_pattern") or "",
            verification=_candidate_verification(candidate),
            repo=candidate.get("repo") or "",
            failure_key=candidate.get("failure_key") or "", actor=actor,
            db_path=db_path)
        if not lesson:
            return None, status
        # The candidate is tombstoned, not diagnosed: the adoption says the
        # text was good, so no mechanism belongs in invalidated_diagnosis.
        # invalidate_lesson is not reused here - it would write an
        # invalidate/unknown decision row alongside the adopt one and make
        # the queue's accept/reject counts lie.
        tombstoned, status = _tombstone_candidate(lesson_id, db_path)
        if not tombstoned:
            return lesson, "adopted-but-candidate-still-open"
        # The accepting half of the queue's history: an adoption is a
        # decision with a reason, so the accept/reject rate over
        # candidates is countable from stored rows (issue #14).
        lesson_decisions.record_decision(
            lesson_id, lesson_decisions.ACTION_ADOPT, "grounded_useful",
            note=f"adopted as {lesson['lesson_id']}", actor=actor,
            db_path=db_path)
        return lesson, "ok"
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def _tombstone_candidate(lesson_id, db_path):
    """Close a candidate's validity window without a diagnosis. The
    unreviewed text stays in the ledger; only its life ends."""
    try:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "UPDATE lessons SET valid_to = ?, status ="
                      " 'invalidated' WHERE lesson_id = ?",
                      (_now(), lesson_id))
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                               (lesson_id,)).fetchone()
        finally:
            conn.close()
        return (dict(row), "ok") if row else (None, "rejected:no-such-lesson")
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def _candidate_verification(candidate):
    """The candidate's distill provenance, carried onto the adopted lesson so
    the verified row still names the consult it came from. `tests_passed` is
    deliberately not copied from here: it must come from the episode."""
    try:
        payload = json.loads(candidate.get("verification_json") or "{}")
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k != "tests_passed"}
