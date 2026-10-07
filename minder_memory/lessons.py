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
from . import store
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


def invalidate_lesson(lesson_id, reason, db_path=None):
    """Tombstone a lesson (valid_to set; status invalidated). Never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "UPDATE lessons SET valid_to = ?, status ="
                      " 'invalidated' WHERE lesson_id = ?",
                      (_now(), lesson_id))
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                               (lesson_id,)).fetchone()
            if not row:
                return None, "rejected:no-such-lesson"
            out = dict(row)
            out["invalidated_reason"] = str(reason)
            return out, "ok"
        finally:
            conn.close()
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
        invalidated, _ = invalidate_lesson(
            lesson_id, "adopted by operator as "
            f"{lesson['lesson_id']}", db_path=db_path)
        if not invalidated:
            return lesson, "adopted-but-candidate-still-open"
        return lesson, "ok"
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
