"""Résumé evidence tests (Phase 1 résumé slice; PRD v2 §Résumé fact,
wording, and intent — retention gate lifted by the owner 2026-09-23).

The core distinction under test: a factual correction, an approved
wording, and a JD-scoped presentation intent are three different
objects. Correcting a fact supersedes the assertion and flags dependent
drafts; choosing a variant for one JD never touches history or other
JDs; and a classifier label alone can never create a correction.

Retention (owner-accepted decision 4): JD-scoped intents expire with
the application cycle — default 90 days, operator-configurable, expiry
explicitly recorded at creation (expires_at) and mechanically enforced.
"""
from datetime import datetime, timedelta, timezone

from memory import db as _db, resume_evidence as resume
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _seed(assertion_id="as_1", variants=("led a workstream",
                                         "supported delivery")):
    def seed(dbp):
        row, status = resume.assert_career_fact(
            "I coordinated project X", wording_variants=variants,
            actor="user", assertion_id=assertion_id, db_path=dbp)
        assert status == "asserted"
        for phrase in variants:
            w, wstatus = resume.approve_wording(
                assertion_id, phrase=phrase, actor="user", db_path=dbp)
            assert wstatus == "approved"
        return row
    return seed


def test_assert_fact_and_approve_wordings(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    assert row["status"] == "active" and row["uncertain"] == 0
    wordings = _rows(dbp, "SELECT * FROM approved_wordings")
    assert {w["phrase"] for w in wordings} == {"led a workstream",
                                               "supported delivery"}
    # a phrase outside the assertion's variants cannot be approved
    bad, status = resume.approve_wording(
        row["assertion_id"], phrase="invented a time machine",
        actor="user", db_path=dbp)
    assert bad is None and "not_in_variants" in status


def test_positioning_choice_is_jd_scoped(tmp_path):
    """'Both are fair; use supported for JD-A' — JD-A's intent changes;
    JD-B and the historical fact are untouched. led is not marked false."""
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    intent_a, s1 = resume.set_intent(
        jd_id="jd-a", jd_digest="jd" * 8, assertion_id=row["assertion_id"],
        chosen_variant="supported delivery", actor="user", now=NOW,
        db_path=dbp)
    intent_b, s2 = resume.set_intent(
        jd_id="jd-b", jd_digest="jb" * 8, assertion_id=row["assertion_id"],
        chosen_variant="led a workstream", actor="user", now=NOW,
        db_path=dbp)
    assert s1 == s2 == "intent_recorded"
    assert intent_a["chosen_variant"] == "supported delivery"
    assert intent_b["chosen_variant"] == "led a workstream"
    assertion = resume.get_assertion(row["assertion_id"], db_path=dbp)
    assert assertion["status"] == "active"  # history untouched


def test_intent_requires_an_approved_variant(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)  # both variants approved; use a third, unapproved
    intent, status = resume.set_intent(
        jd_id="jd-c", jd_digest="jc" * 8, assertion_id=row["assertion_id"],
        chosen_variant="single-handedly built it", actor="user", now=NOW,
        db_path=dbp)
    assert intent is None and "not_approved" in status


def test_factual_correction_supersedes_and_flags_dependents(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed(assertion_id="as_1")(dbp)
    resume.set_intent(jd_id="jd-a", jd_digest="jd" * 8,
                      assertion_id=row["assertion_id"],
                      chosen_variant="supported delivery", actor="user",
                      now=NOW, db_path=dbp)
    resume.record_draft(draft_id="draft1",
                        assertion_id=row["assertion_id"],
                        phrase="led a workstream", jd_id="jd-a",
                        db_path=dbp)
    # "I never led that workstream" — an explicit factual correction
    new_row, status = resume.correct_fact(
        row["assertion_id"], corrected_claim="I supported delivery only",
        actor="user", db_path=dbp)
    assert status == "corrected"
    old = resume.get_assertion(row["assertion_id"], db_path=dbp)
    assert old["status"] == "superseded" and old["superseded_by"] == \
        new_row["assertion_id"]
    # dependent drafts and intents are flagged for review, not erased:
    # draft usages are append-only, so corrections append evidence_flags
    drafts = _rows(dbp, "SELECT * FROM draft_usages")
    assert drafts[0]["flagged"] is None  # the row itself stays intact
    flags = _rows(dbp, "SELECT * FROM evidence_flags WHERE"
                 " flag = 'assertion_superseded'")
    assert {f["target_id"] for f in flags} == \
        {drafts[0]["usage_id"]}
    intents = _rows(dbp, "SELECT * FROM application_intents")
    assert intents[0]["status"] == "review"
    preview = resume.impact_preview(row["assertion_id"], db_path=dbp)
    assert preview["drafts"][0]["effective_flag"] == \
        "assertion_superseded"
    # the correction itself is an auditable human event
    events = _rows(dbp, "SELECT * FROM human_input_events"
                   " WHERE kind = 'factual_correction'")
    assert len(events) == 1
    # history preserved; unaffected claims would survive (only one here)
    assert len(_rows(dbp, "SELECT * FROM career_assertions")) == 2


def test_correction_requires_yes_gate(tmp_path, capsys):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    code = main(["--db", str(dbp), "resume", "correct", row["assertion_id"],
                 "--claim", "I supported delivery only", "--actor",
                 "user"])
    assert code == EXIT_USAGE
    assert "PLAN" in capsys.readouterr().out
    assert len(_rows(dbp, "SELECT * FROM career_assertions")) == 1


def test_uncertainty_moves_wordings_to_review(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    _, status = resume.mark_uncertain(row["assertion_id"], actor="user",
                                      db_path=dbp)
    assert status == "review_requested"
    assertion = resume.get_assertion(row["assertion_id"], db_path=dbp)
    assert assertion["uncertain"] == 1
    wordings = _rows(dbp, "SELECT status FROM approved_wordings")
    assert all(w["status"] == "review" for w in wordings)
    # and an intent cannot be set from a review-state wording
    intent, istatus = resume.set_intent(
        jd_id="jd-x", jd_digest="jx" * 8, assertion_id=row["assertion_id"],
        chosen_variant="led a workstream", actor="user", now=NOW,
        db_path=dbp)
    assert intent is None and "not_approved" in istatus


def test_draft_flags_unapproved_phrase(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)  # only the two variants approved
    usage, status = resume.record_draft(
        draft_id="draft9", assertion_id=row["assertion_id"],
        phrase="single-handedly built it", db_path=dbp)
    assert status == "flagged:unapproved_phrase"
    assert usage["flagged"] == "unapproved_phrase"


def test_retention_default_configurable_and_enforced(tmp_path,
                                                     monkeypatch):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    monkeypatch.delenv("MINDER_RESUME_RETENTION_DAYS", raising=False)
    intent, _ = resume.set_intent(
        jd_id="jd-a", jd_digest="jd" * 8, assertion_id=row["assertion_id"],
        chosen_variant="supported delivery", actor="user", now=NOW,
        db_path=dbp)
    expected = NOW + timedelta(days=90)
    assert datetime.fromisoformat(intent["expires_at"]) == expected
    # before expiry: still active
    assert resume.expire_intents(now=NOW + timedelta(days=89),
                                 db_path=dbp)["expired"] == 0
    assert _rows(dbp, "SELECT status FROM application_intents")[0]\
        ["status"] == "active"
    # after expiry: mechanically expired, row kept
    summary = resume.expire_intents(now=NOW + timedelta(days=91),
                                    db_path=dbp)
    assert summary["expired"] == 1
    assert _rows(dbp, "SELECT status FROM application_intents")[0]\
        ["status"] == "expired"
    # operator-configurable per call
    intent2, _ = resume.set_intent(
        jd_id="jd-z", jd_digest="jz" * 8, assertion_id=row["assertion_id"],
        chosen_variant="supported delivery", actor="user", now=NOW,
        retention_days=14, db_path=dbp)
    assert datetime.fromisoformat(intent2["expires_at"]) == \
        NOW + timedelta(days=14)
    # env override changes the default
    monkeypatch.setenv("MINDER_RESUME_RETENTION_DAYS", "30")
    intent3, _ = resume.set_intent(
        jd_id="jd-y", jd_digest="jy" * 8, assertion_id=row["assertion_id"],
        chosen_variant="supported delivery", actor="user", now=NOW,
        db_path=dbp)
    assert datetime.fromisoformat(intent3["expires_at"]) == \
        NOW + timedelta(days=30)


def test_intent_expiry_does_not_touch_history(tmp_path):
    dbp = _mig(tmp_path)
    row = _seed()(dbp)
    resume.set_intent(jd_id="jd-a", jd_digest="jd" * 8,
                      assertion_id=row["assertion_id"],
                      chosen_variant="supported delivery", actor="user",
                      now=NOW, db_path=dbp)
    resume.expire_intents(now=NOW + timedelta(days=91), db_path=dbp)
    assertion = resume.get_assertion(row["assertion_id"], db_path=dbp)
    assert assertion["status"] == "active"  # expiry is intent-scoped
    wordings = _rows(dbp, "SELECT status FROM approved_wordings")
    assert all(w["status"] == "approved" for w in wordings)


def test_classifier_label_cannot_create_correction(tmp_path):
    """Acceptance: a classifier intent_kind=factual_correction proposal
    is advisory only — routing must never write résumé evidence."""
    from decision import routing
    from decision.providers.fake import FakeClient

    def mass(option, options):
        return {o: (1.0 if o == option else 0.0) for o in options}

    dbp = _mig(tmp_path)
    fixtures = {"s": {
        "candidate_domain_probs": mass("resume_application",
                                       routing.DOMAIN_SET),
        "intent_kind_probs": mass("factual_correction",
                                  routing.INTENT_KINDS),
        "transition_probs": mass("switch",
                                 routing.TRANSITION_SET + ("human",)),
        "confidence": 0.95,
    }}
    routing.assess_route(db_path=dbp, record=True, provider=FakeClient(
        fixtures, key_fn=lambda s: "s"), state_key="s")
    assert _rows(dbp, "SELECT * FROM career_assertions") == []
    assert _rows(dbp, "SELECT * FROM human_input_events WHERE"
                 " kind = 'factual_correction'") == []


def test_resume_cli(tmp_path, capsys):
    dbp = _mig(tmp_path)
    assert main(["--db", str(dbp), "resume", "assert", "--claim",
                 "I coordinated project X", "--variant",
                 "led a workstream", "--variant", "supported delivery",
                 "--actor", "user"]) == EXIT_OK
    assertion_id = _rows(dbp, "SELECT assertion_id FROM"
                         " career_assertions")[0]["assertion_id"]
    assert main(["--db", str(dbp), "resume", "approve", "--assertion",
                 assertion_id, "--phrase", "led a workstream",
                 "--actor", "user"]) == EXIT_OK
    assert main(["--db", str(dbp), "resume", "intent", "--jd", "jd-a",
                 "--jd-digest", "jd" * 8, "--assertion", assertion_id,
                 "--phrase", "led a workstream", "--actor",
                 "user"]) == EXIT_OK
    assert main(["--db", str(dbp), "resume", "impact",
                 assertion_id]) == EXIT_OK
    out = capsys.readouterr().out
    assert "jd-a" in out
    assert main(["--db", str(dbp), "resume", "correct", assertion_id,
                 "--claim", "corrected", "--actor", "user",
                 "--yes"]) == EXIT_OK
    assert main(["--db", str(dbp), "resume", "expire"]) == EXIT_OK
    assert main(["--db", str(dbp), "resume", "approve", "--assertion",
                 assertion_id, "--phrase", "nonsense",
                 "--actor", "user"]) == EXIT_USAGE
