"""Frontier consult traces (docs/prd-memory-v1.md PR 7).

Additive audit metadata per panel consult: hashes instead of raw prompts,
helpfulness left null until a verification event exists. Panel behaviour is
untouched; every failure here degrades to no-trace.
"""
import hashlib
import uuid
from datetime import datetime, timezone

from . import db as _db


def _now():
    return datetime.now(timezone.utc).isoformat()


def sha(text):
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:16]


def record(payload, answer, providers=None, redaction_profile=None,
           episode_id=None, db_path=None):
    """Store one trace for a completed consult. Returns trace_id or None."""
    try:
        conn = _db.connect(db_path)
        try:
            trace_id = f"tr_{uuid.uuid4().hex[:12]}"
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO frontier_traces (trace_id, ts, episode_id,"
                " failure_key, local_attempts, redaction_profile,"
                " provider_fingerprint, request_hash, response_hash,"
                " helpfulness, verification_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (trace_id, _now(), episode_id,
                 payload.get("key"), payload.get("attempts"),
                 redaction_profile,
                 ",".join(p.get("name", "?") for p in (providers or [])),
                 sha(payload.get("prompt") or payload.get("error")),
                 sha(answer)))
            conn.execute("COMMIT")
            return trace_id
        finally:
            conn.close()
    except Exception:
        return None


def set_verification(trace_id, helpfulness=None, verification_status=None,
                     db_path=None):
    """Join a later verification event to a trace. Best-effort."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE frontier_traces SET helpfulness = ?,"
                " verification_status = ? WHERE trace_id = ?",
                (helpfulness, verification_status, trace_id))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return False


def get(trace_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM frontier_traces WHERE trace_id = ?",
                (trace_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None
