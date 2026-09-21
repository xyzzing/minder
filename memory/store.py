"""Event/episode store for memory v1 (docs/prd-memory-v1.md PR 2).

Every mutating call returns a status string and NEVER raises — the hook
path calls these inline and constraint 10 (fail open) applies. status is
"ok" or "degraded:<ExceptionName>"; callers log and move on.
"""
import uuid
from datetime import datetime, timezone

from . import db as _db


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def record_event(event, db_path=None):
    """Append one event. Returns (event_id, status)."""
    try:
        conn = _db.connect(db_path)
        try:
            eid = event.get("event_id") or _uid("ev")
            _db.write(conn, "INSERT INTO events (event_id, ts, event_type,"
                      " session_id, task_id, repo, repo_version, tool,"
                      " failure_key, action_fingerprint, payload_json,"
                      " redaction_status)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (eid, event.get("ts") or _now(),
                       event.get("event_type") or "tool_failure",
                       event.get("session_id"), event.get("task_id"),
                       event.get("repo"), event.get("repo_version"),
                       event.get("tool"), event.get("failure_key"),
                       event.get("action_fingerprint"),
                       event.get("payload_json") or "{}",
                       event.get("redaction_status") or "redacted"))
            return eid, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def open_episode(event=None, db_path=None, opened_at=None):
    """Open an episode for this task/repo. Returns (episode_id, status)."""
    event = event or {}
    try:
        conn = _db.connect(db_path)
        try:
            eid = _uid("ep")
            _db.write(conn, "INSERT INTO episodes (episode_id, opened_at,"
                      " closed_at, repo, task_id, status)"
                      " VALUES (?, ?, NULL, ?, ?, 'open')",
                      (eid, opened_at or _now(), event.get("repo"),
                       event.get("task_id") or event.get("session_id")))
            return eid, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def add_attempt(episode_id, event, db_path=None):
    """Record the event and link it to the episode as the next attempt.
    Returns (event_id, status) — the link rides the same commit."""
    try:
        conn = _db.connect(db_path)
        try:
            eid = event.get("event_id") or _uid("ev")
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM episode_events"
                " WHERE episode_id = ?", (episode_id,)).fetchone()
            _db.write(conn, "INSERT INTO events (event_id, ts, event_type,"
                      " session_id, task_id, repo, repo_version, tool,"
                      " failure_key, action_fingerprint, payload_json,"
                      " redaction_status)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (eid, event.get("ts") or _now(),
                       event.get("event_type") or "tool_failure",
                       event.get("session_id"), event.get("task_id"),
                       event.get("repo"), event.get("repo_version"),
                       event.get("tool"), event.get("failure_key"),
                       event.get("action_fingerprint"),
                       event.get("payload_json") or "{}",
                       event.get("redaction_status") or "redacted"))
            _db.write(conn, "INSERT INTO episode_events (episode_id,"
                      " event_id, seq) VALUES (?, ?, ?)",
                      (episode_id, eid, seq_row["next"]))
            return eid, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def close_episode(episode_id, status, db_path=None, closed_at=None):
    """Close with status: unresolved | resolved | candidate | verified."""
    try:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "UPDATE episodes SET closed_at = ?, status = ?"
                      " WHERE episode_id = ?",
                      (closed_at or _now(), status, episode_id))
            return status, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def count_attempts(failure_key, action_fingerprint=None, db_path=None):
    """Tool failures recorded for this key (optionally this exact action).
    Returns -1 when the store is unavailable (callers treat < 0 as unknown,
    i.e. the duplicate guard fails open)."""
    try:
        conn = _db.connect(db_path)
        try:
            if action_fingerprint is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE failure_key = ?"
                    " AND event_type = 'tool_failure'",
                    (failure_key,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE failure_key = ?"
                    " AND action_fingerprint = ?"
                    " AND event_type = 'tool_failure'",
                    (failure_key, action_fingerprint)).fetchone()
            return row["n"]
        finally:
            conn.close()
    except Exception:
        return -1


def list_events(failure_key, db_path=None, limit=100):
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE failure_key = ?"
                " ORDER BY ts, event_id LIMIT ?",
                (failure_key, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def find_open_episode(task_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM episodes WHERE status = 'open' AND"
                " (task_id = ? OR task_id = ?)"
                " ORDER BY opened_at DESC LIMIT 1",
                (task_id, task_id)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def get_episode(episode_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM episodes WHERE episode_id = ?",
                               (episode_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def episode_events(episode_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT e.*, ee.seq AS seq FROM episode_events ee JOIN events e"
                " ON e.event_id = ee.event_id WHERE ee.episode_id = ?"
                " ORDER BY ee.seq", (episode_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def event_by_id(event_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?",
                               (event_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
