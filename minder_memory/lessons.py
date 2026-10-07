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
