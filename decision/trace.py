"""DecisionTrace persistence (Phase 5.5 slice D). Log-only: policy never
reads this table to decide. Every call fails open."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from memory import db as _db


def _now():
    return datetime.now(timezone.utc).isoformat()


def record_decision(db_path=None, contract_id="", contract_version="",
                    session_id="", failure_key="", state="",
                    menu=None, decision=None, response=None):
    """Persist one DecisionTrace row. Returns the row id or None; never
    raises. `decision` is a policy_gate.PolicyDecision; `response` a
    DecisionResponse (provider/model_version/latency)."""
    try:
        trace_id = f"dt_{uuid.uuid4().hex[:12]}"
        menu_json = json.dumps(list(menu or []))
        state_hash = hashlib.sha256(str(state or "").encode()).hexdigest()[:16]
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO decision_traces (id, ts, contract_id,"
                " contract_version, session_id, failure_key, state_hash,"
                " menu_json, model_recommendation, policy_decision,"
                " override, fallback, confidence, failure_kind,"
                " needs_new_evidence, provider, model_version, latency_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?)",
                (trace_id, _now(), contract_id, contract_version,
                 session_id, failure_key, state_hash, menu_json,
                 getattr(decision, "model_recommendation", ""),
                 getattr(decision, "policy_decision", ""),
                 getattr(decision, "override", ""),
                 getattr(decision, "fallback", ""),
                 _as_float(getattr(response, "confidence", 0.0)),
                 getattr(decision, "failure_kind", "unknown"),
                 _as_float(getattr(decision, "needs_new_evidence", 0.0)),
                 getattr(response, "provider", ""),
                 getattr(response, "model_version", ""),
                 _as_float(getattr(response, "latency_ms", 0.0))))
            conn.execute("COMMIT")
            return trace_id
        finally:
            conn.close()
    except Exception:
        return None


def latest_for_failure_key(failure_key, contract_id=None, session_id=None,
                           db_path=None):
    """Most recent trace for a failure key (optionally per contract and
    session), or None. Read-only helper for debounce and Phase 7 export
    linkage."""
    try:
        conn = _db.connect(db_path)
        try:
            sql = "SELECT * FROM decision_traces WHERE failure_key = ?"
            params = [failure_key]
            if contract_id:
                sql += " AND contract_id = ?"
                params.append(contract_id)
            if session_id:
                sql += " AND session_id = ?"
                params.append(session_id)
            sql += " ORDER BY ts DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _as_float(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
