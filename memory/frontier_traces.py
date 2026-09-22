"""Frontier consult traces (docs/prd-memory-v1.md PR 7).

Additive audit metadata per panel consult: hashes instead of raw prompts,
helpfulness left null until a verification event exists. Panel behaviour is
untouched; every failure here degrades to no-trace.

P4.1 governance (docs/minder-phase-4-7-frontier-coding.md): record_consult /
classify_consult / get_consult add a governed layer on top. Helpfulness and
distilled actions live in frontier_evals (migration 007) keyed by trace_id;
the legacy frontier_traces columns keep their PR 7 semantics untouched.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from . import db as _db
from .frontier_redaction import (EXTERNAL_PROHIBITED, INTERNAL_CODE_DEFAULT,
                                 allows_response_text, redact_for_profile)


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


def _panel_names(providers):
    """Accept dicts (frontier providers), names, or a comma string."""
    if isinstance(providers, str):
        return providers
    names = []
    for p in (providers or []):
        names.append(p.get("name", "?") if isinstance(p, dict) else str(p))
    return ",".join(names)


def record_consult(payload, db_path=None):
    """P4.1 governed consult record. Stores hashes + optional redacted
    distilled actions only — never raw prompt/response text. Under the
    external-prohibited profile, response-derived content is refused
    entirely (hashes only). Returns trace_id or None. Never raises."""
    try:
        profile = (payload.get("redaction_profile")
                   or INTERNAL_CODE_DEFAULT)
        request_hash = (payload.get("request_hash")
                        or sha(payload.get("prompt")
                               or payload.get("error")))
        response_hash = (payload.get("response_hash")
                         or sha(payload.get("response")
                                or payload.get("answer")))
        distilled = payload.get("distilled") or []
        distilled_clean = []
        if allows_response_text(profile):
            for action in distilled:
                clean = redact_for_profile(str(action), profile)
                if clean:
                    distilled_clean.append(clean)
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
                (trace_id, _now(),
                 payload.get("episode_id"),
                 payload.get("failure_key") or payload.get("key"),
                 payload.get("local_attempts") or payload.get("attempts"),
                 profile,
                 _panel_names(payload.get("providers")
                              or payload.get("panel")),
                 request_hash, response_hash))
            if distilled_clean or payload.get("trigger"):
                conn.execute(
                    "INSERT INTO frontier_evals (trace_id, consult_trigger,"
                    " distilled_json) VALUES (?, ?, ?)"
                    " ON CONFLICT(trace_id) DO NOTHING",
                    (trace_id, payload.get("trigger"),
                     json.dumps(distilled_clean) if distilled_clean else None))
            conn.execute("COMMIT")
            return trace_id
        finally:
            conn.close()
    except Exception:
        return None


def classify_consult(trace_id, verification_status, accepted=None,
                     rejected=None, db_path=None):
    """P4.1 helpfulness classification. pass + accepted -> helpful (partial
    when something was also rejected); fail + applied advice -> harmful;
    everything else -> inconclusive. Under external-prohibited the action
    text is not stored (hashes-only law) — the label still is. Never
    raises; any problem returns 'inconclusive'."""
    try:
        status = str(verification_status or "").strip().lower() or None
        accepted = [str(a) for a in (accepted or [])]
        rejected = [str(a) for a in (rejected or [])]
        if status == "pass" and accepted:
            helpfulness = "partial" if rejected else "helpful"
        elif status == "fail" and accepted:
            helpfulness = "harmful"  # advice was applied and the run failed
        else:
            helpfulness = "inconclusive"
        store_text = True
        try:
            conn = _db.connect(db_path)
            row = conn.execute(
                "SELECT redaction_profile FROM frontier_traces"
                " WHERE trace_id = ?", (trace_id,)).fetchone()
            if row and row["redaction_profile"] == EXTERNAL_PROHIBITED:
                store_text = False
        finally:
            conn.close()
        accepted_json = (json.dumps([redact_for_profile(a) for a in accepted])
                         if (accepted and store_text) else None)
        rejected_json = (json.dumps([redact_for_profile(a) for a in rejected])
                         if (rejected and store_text) else None)
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO frontier_evals (trace_id, helpfulness,"
                " verification_status, accepted_actions_json,"
                " rejected_actions_json, classified_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(trace_id) DO UPDATE SET"
                " helpfulness=excluded.helpfulness,"
                " verification_status=excluded.verification_status,"
                " accepted_actions_json=excluded.accepted_actions_json,"
                " rejected_actions_json=excluded.rejected_actions_json,"
                " classified_at=excluded.classified_at",
                (trace_id, helpfulness, status, accepted_json,
                 rejected_json, _now()))
            conn.execute("COMMIT")
            return helpfulness
        finally:
            conn.close()
    except Exception:
        return "inconclusive"


def get_consult(trace_id, db_path=None):
    """Joined governed view of one consult. No raw prompt/response field
    exists in storage; hashes and redacted action lists only."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT t.trace_id, t.ts, t.episode_id, t.failure_key,"
                " t.local_attempts, t.redaction_profile,"
                " t.provider_fingerprint, t.request_hash, t.response_hash,"
                " e.consult_trigger, e.helpfulness, e.verification_status,"
                " e.distilled_json, e.accepted_actions_json,"
                " e.rejected_actions_json, e.classified_at"
                " FROM frontier_traces t LEFT JOIN frontier_evals e"
                " ON e.trace_id = t.trace_id WHERE t.trace_id = ?",
                (trace_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            for key in ("distilled_json", "accepted_actions_json",
                        "rejected_actions_json"):
                if out.get(key):
                    try:
                        out[key] = json.loads(out[key])
                    except ValueError:
                        pass
            return out
        finally:
            conn.close()
    except Exception:
        return None
