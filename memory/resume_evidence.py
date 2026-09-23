"""Résumé evidence (Phase 1 résumé slice, PRD v2 §Résumé fact, wording,
and intent; retention gate lifted by the owner 2026-09-23).

Three linked, never-collapsed objects:

- CareerAssertion — what happened, first-party, with wording variants.
- ApprovedWording — which expressions the user accepts as accurate
  (any scope, or scoped to one JD).
- ApplicationIntent — how accurate experience is presented to ONE
  pinned JD, with an explicit expiry (retention decision 4: default 90
  days, operator-configurable, recorded at creation, enforced
  mechanically — expiry is intent-scoped and never touches history).
- DraftUsage — which phrase appears in which draft, append-only with a
  flag column so a factual correction can reach exactly the drafts that
  rely on the disputed assertion (paraphrases included, because the
  link is by assertion, not by text matching).

Epistemics enforced by construction: a factual correction is an explicit
human event that supersedes an assertion and flags dependents; a
positioning choice only creates/updates a JD-scoped intent; and nothing
here is reachable from a classifier proposal — routing intent_kind is
advisory and this module is never called from it.

Every mutating call returns (row|None, status) and fails open.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from . import db as _db
from . import task_context

DEFAULT_RETENTION_DAYS = 90

SUPPORT_CLASSES = ("none", "first_party", "corroborated")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _digest(text):
    return hashlib.sha256(str(text).encode()).hexdigest()[:16]


def get_assertion(assertion_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _approved_wording_exists(conn, assertion_id, phrase, jd_id=None):
    rows = conn.execute(
        "SELECT scope FROM approved_wordings WHERE assertion_id = ?"
        " AND phrase = ? AND status = 'approved'",
        (assertion_id, phrase)).fetchall()
    scopes = {r["scope"] for r in rows}
    if "any" in scopes:
        return True
    return jd_id is not None and jd_id in scopes


def assert_career_fact(claim_text, *, wording_variants=(), actor="user",
                       support="first_party", assertion_id=None,
                       db_path=None):
    """Record a first-party career assertion (the fact layer)."""
    try:
        if not claim_text:
            return None, "rejected:claim_text required"
        if support not in SUPPORT_CLASSES:
            return None, f"rejected:unknown_support:{support}"
        variants = [v for v in (wording_variants or ()) if v]
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            aid = assertion_id or _uid("as")
            conn.execute(
                "INSERT INTO career_assertions (assertion_id, created_at,"
                " subject_digest, claim_text, wording_variants_json,"
                " support, status, uncertain, actor)"
                " VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?)",
                (aid, _now(), _digest(claim_text), claim_text,
                 json.dumps(variants), support, actor))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (aid,)).fetchone()
            return dict(row), "asserted"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def approve_wording(assertion_id, *, phrase, actor="user", scope="any",
                    db_path=None):
    """Approve one phrasing as accurate (any scope, or one JD)."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            assertion = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            if not assertion:
                return None, "rejected:unknown_assertion"
            variants = json.loads(assertion["wording_variants_json"] or "[]")
            if phrase not in variants:
                return None, ("rejected:phrase_not_in_variants "
                              f"{variants}")
            wording_id = _uid("wd")
            conn.execute(
                "INSERT INTO approved_wordings (wording_id, assertion_id,"
                " phrase, scope, status, created_at, actor)"
                " VALUES (?, ?, ?, ?, 'approved', ?, ?)",
                (wording_id, assertion_id, phrase, scope, _now(), actor))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM approved_wordings WHERE wording_id = ?",
                (wording_id,)).fetchone()
            return dict(row), "approved"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def mark_uncertain(assertion_id, *, actor="user", question=None,
                   db_path=None):
    """'I'm unsure whether led overstates my role' — the assertion goes
    uncertain and its approved wordings drop to review until an explicit
    factual/wording answer exists. No intent can be set meanwhile."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            assertion = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            if not assertion:
                return None, "rejected:unknown_assertion"
            conn.execute(
                "UPDATE career_assertions SET uncertain = 1"
                " WHERE assertion_id = ?", (assertion_id,))
            conn.execute(
                "UPDATE approved_wordings SET status = 'review'"
                " WHERE assertion_id = ? AND status = 'approved'",
                (assertion_id,))
            conn.execute(
                "INSERT INTO human_input_events (event_id, ts, actor,"
                " authority, kind, question, affects_json, decision)"
                " VALUES (?, ?, ?, 'user', 'clarify_request', ?, ?,"
                " 'deferred')",
                (_uid("hin"), _now(), actor,
                 question or f"does '{assertion['claim_text']}' overstate"
                 " your role?", json.dumps([{"type": "career_assertion",
                                             "id": assertion_id}])))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            return dict(row), "review_requested"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def set_intent(jd_id, jd_digest, assertion_id, *, chosen_variant,
               actor="user", retention_days=None, now=None, db_path=None):
    """Record how accurate experience is presented to ONE pinned JD.
    The variant must already be an approved wording; the intent expires
    with the application cycle (default 90 days, env
    MINDER_RESUME_RETENTION_DAYS, per-call retention_days override)."""
    try:
        import os
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            assertion = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            if not assertion:
                return None, "rejected:unknown_assertion"
            if not _approved_wording_exists(conn, assertion_id,
                                            chosen_variant, jd_id=jd_id):
                return None, ("rejected:variant_not_approved — a "
                              "positioning choice presents approved "
                              "wordings only")
            if retention_days is None:
                try:
                    retention_days = int(os.environ.get(
                        "MINDER_RESUME_RETENTION_DAYS",
                        str(DEFAULT_RETENTION_DAYS)))
                except ValueError:
                    retention_days = DEFAULT_RETENTION_DAYS
            created = now or datetime.now(timezone.utc)
            if isinstance(created, str):
                created = datetime.fromisoformat(created)
            expires = created + timedelta(days=int(retention_days))
            intent_id = _uid("int")
            conn.execute(
                "INSERT INTO application_intents (intent_id, jd_id,"
                " jd_digest, assertion_id, chosen_variant, created_at,"
                " actor, expires_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
                (intent_id, jd_id, jd_digest, assertion_id,
                 chosen_variant, created.isoformat(), actor,
                 expires.isoformat()))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM application_intents WHERE intent_id = ?",
                (intent_id,)).fetchone()
            return dict(row), "intent_recorded"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def expire_intents(*, now=None, db_path=None):
    """Mechanical retention enforcement: active intents past their
    recorded expires_at become 'expired' (rows kept). History and the
    career layer are untouched."""
    try:
        now_dt = now or datetime.now(timezone.utc)
        if isinstance(now_dt, str):
            now_dt = datetime.fromisoformat(now_dt)
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT intent_id, expires_at FROM application_intents"
                " WHERE status = 'active'").fetchall()
            expired = 0
            for row in rows:
                try:
                    due = datetime.fromisoformat(row["expires_at"])
                except ValueError:
                    continue
                if due <= now_dt:
                    conn.execute(
                        "UPDATE application_intents SET status ="
                        " 'expired', expired_at = ? WHERE intent_id = ?",
                        (now_dt.isoformat(), row["intent_id"]))
                    expired += 1
            conn.execute("COMMIT")
            return {"expired": expired}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"expired": 0, "status": f"error:{type(exc).__name__}"}


def record_draft(draft_id, assertion_id, *, phrase, jd_id=None,
                 db_path=None):
    """Record which phrase appears in which draft (append-only). An
    unapproved phrase is recorded and flagged, never silently dropped."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            assertion = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            if not assertion:
                return None, "rejected:unknown_assertion"
            flagged = None
            if not _approved_wording_exists(conn, assertion_id, phrase,
                                            jd_id=jd_id):
                flagged = "unapproved_phrase"
            usage_id = _uid("du")
            conn.execute(
                "INSERT INTO draft_usages (usage_id, draft_id, jd_id,"
                " assertion_id, phrase, recorded_at, flagged)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (usage_id, draft_id, jd_id, assertion_id, phrase,
                 _now(), flagged))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM draft_usages WHERE usage_id = ?",
                (usage_id,)).fetchone()
            status = "recorded" if not flagged \
                else f"flagged:{flagged}"
            return dict(row), status
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def correct_fact(assertion_id, *, corrected_claim, actor="user",
                 wording_variants=(), db_path=None):
    """'I never led that workstream' — an explicit human factual
    correction: supersede the disputed assertion, create the corrected
    one, flag dependent drafts and intents for review, and record the
    human event. History stays auditable; unaffected assertions are
    untouched."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (assertion_id,)).fetchone()
            if not old:
                return None, "rejected:unknown_assertion"
            now = _now()
            new_id = _uid("as")
            variants = [v for v in (wording_variants or ())
                        if v] or [corrected_claim]
            conn.execute(
                "INSERT INTO career_assertions (assertion_id, created_at,"
                " subject_digest, claim_text, wording_variants_json,"
                " support, status, uncertain, actor)"
                " VALUES (?, ?, ?, ?, ?, 'first_party', 'active', 0, ?)",
                (new_id, now, old["subject_digest"], corrected_claim,
                 json.dumps(variants), actor))
            conn.execute(
                "UPDATE career_assertions SET status = 'superseded',"
                " superseded_by = ? WHERE assertion_id = ?",
                (new_id, assertion_id))
            # drop superseded wordings to review (they assert the old text)
            conn.execute(
                "UPDATE approved_wordings SET status = 'review'"
                " WHERE assertion_id = ? AND status = 'approved'",
                (assertion_id,))
            # dependents flagged, not erased: draft usages are
            # append-only, so corrections append flag rows; intents have
            # a mutable status by design (retention)
            dependent_drafts = conn.execute(
                "SELECT usage_id FROM draft_usages WHERE assertion_id = ?",
                (assertion_id,)).fetchall()
            for draft_row in dependent_drafts:
                conn.execute(
                    "INSERT INTO evidence_flags (flag_id, ts, target_type,"
                    " target_id, flag, reason, actor)"
                    " VALUES (?, ?, 'draft_usage', ?,"
                    " 'assertion_superseded', ?, ?)",
                    (_uid("ef"), now, draft_row["usage_id"],
                     f"assertion {assertion_id} superseded by {new_id}",
                     actor))
            conn.execute(
                "UPDATE application_intents SET status = 'review'"
                " WHERE assertion_id = ? AND status = 'active'",
                (assertion_id,))
            conn.execute(
                "INSERT INTO human_input_events (event_id, ts, actor,"
                " authority, kind, question, answer_summary, affects_json,"
                " decision)"
                " VALUES (?, ?, ?, 'user', 'factual_correction', ?, ?, ?,"
                " 'approved')",
                (_uid("hin"), now, actor,
                 f"was '{old['claim_text']}' accurate?",
                 corrected_claim,
                 json.dumps([{"type": "career_assertion",
                              "id": assertion_id},
                             {"type": "career_assertion", "id": new_id}])))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM career_assertions WHERE assertion_id = ?",
                (new_id,)).fetchone()
            return dict(row), "corrected"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def impact_preview(assertion_id, db_path=None):
    """What a factual correction WOULD reach: dependent drafts, intents,
    wordings. Read-only; the correction itself is human-gated."""
    try:
        conn = _db.connect(db_path)
        try:
            drafts = [dict(r) for r in conn.execute(
                "SELECT d.draft_id, d.phrase, d.flagged,"
                " (SELECT flag FROM evidence_flags f WHERE"
                "  f.target_type = 'draft_usage' AND f.target_id ="
                "  d.usage_id ORDER BY f.ts DESC LIMIT 1) AS"
                " correction_flag FROM draft_usages d"
                " WHERE d.assertion_id = ?",
                (assertion_id,)).fetchall()]
            for draft in drafts:
                draft["effective_flag"] = draft["correction_flag"] \
                    or draft["flagged"]
            intents = [dict(r) for r in conn.execute(
                "SELECT intent_id, jd_id, chosen_variant, status"
                " FROM application_intents WHERE assertion_id = ?",
                (assertion_id,)).fetchall()]
            wordings = [dict(r) for r in conn.execute(
                "SELECT wording_id, phrase, status FROM approved_wordings"
                " WHERE assertion_id = ?", (assertion_id,)).fetchall()]
            return {"assertion_id": assertion_id, "drafts": drafts,
                    "intents": intents, "wordings": wordings}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"assertion_id": assertion_id, "drafts": [], "intents": [],
                "wordings": [], "status": f"error:{type(exc).__name__}"}
