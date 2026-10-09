"""Lesson decisions carry a reason code and a diagnosis (issue #14).

Two closed vocabularies, both module-owned (C4):

- a diagnosis on an invalidation - what went wrong with a lesson that
  turned out harmful. `unknown` is reachable and is the default, because
  an unsuccessful run alone does not establish a content defect, and a
  lesson that was not retrieved never proves the store lacks a rule;
- a decision code on every operator call that ends a lesson's life or
  rejects a candidate, so the queue's history is countable instead of
  merely readable.

Assertions are on stored rows and rendered output, never on whether a
column happens to exist.
"""
from minder_memory import db as _db
from minder_memory import lesson_decisions as dec
from minder_memory import lessons, retrieval, store

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"
NOTE = "the retry hint never applied to this repo"


def verified_episode(dbp, repo=REPO, key=KEY, n=2):
    ep_id, _ = store.open_episode({"repo": repo, "task_id": "t1"},
                                  db_path=dbp)
    for _ in range(n):
        store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                                  "repo": repo, "failure_key": key},
                          db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    return ep_id


def promoted_lesson(dbp):
    lesson, status = lessons.promote_lesson(
        verified_episode(dbp), "retry once with the absolute path",
        verification={"tests_passed": True}, db_path=dbp)
    assert status == "ok", status
    return lesson["lesson_id"]


def _decisions(dbp, lesson_id):
    return [r for r in dec.decisions_for_lesson(lesson_id, db_path=dbp)]


def test_invalidate_defaults_to_unknown_diagnosis_and_keeps_the_note(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = promoted_lesson(dbp)
    out, status = lessons.invalidate_lesson(lid, NOTE, db_path=dbp)
    assert status == "ok", status
    assert out["invalidated_diagnosis"] == dec.DEFAULT_DIAGNOSIS
    assert out["invalidated_reason"] == NOTE
    rows = _decisions(dbp, lid)
    assert [(r["action"], r["code"]) for r in rows] == [
        ("invalidate", dec.DEFAULT_DIAGNOSIS)]
    assert rows[0]["note"] == NOTE


def test_diagnosis_is_recorded_when_given(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = promoted_lesson(dbp)
    out, status = lessons.invalidate_lesson(
        lid, NOTE, diagnosis="content_defect", db_path=dbp)
    assert status == "ok", status
    assert out["invalidated_diagnosis"] == "content_defect"
    assert _decisions(dbp, lid)[0]["code"] == "content_defect"


def test_unknown_diagnosis_is_reachable_and_the_default():
    assert "unknown" in dec.DIAGNOSES
    assert dec.DEFAULT_DIAGNOSIS == "unknown"


def test_unknown_diagnosis_can_be_chosen_explicitly(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = promoted_lesson(dbp)
    _, status = lessons.invalidate_lesson(lid, NOTE, diagnosis="unknown",
                                          db_path=dbp)
    assert status == "ok", status
    assert _decisions(dbp, lid)[0]["code"] == "unknown"


def test_diagnosis_outside_the_taxonomy_is_refused(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = promoted_lesson(dbp)
    out, status = lessons.invalidate_lesson(lid, NOTE, diagnosis="bad_lesson",
                                            db_path=dbp)
    assert out is None
    assert status.startswith("invalid:diagnosis")
    # Refusing the code must not half-apply the decision.
    conn = _db.connect(dbp)
    try:
        row = conn.execute("SELECT status, valid_to FROM lessons"
                           " WHERE lesson_id = ?", (lid,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "verified" and row["valid_to"] is None
    assert _decisions(dbp, lid) == []


def test_rejecting_a_candidate_needs_a_decision_code(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = _candidate(dbp)
    out, status = lessons.reject_candidate_lesson(
        lid, "generic", note="no failure signature to attach it to",
        db_path=dbp)
    assert status == "ok", status
    assert out["status"] == "invalidated"
    rows = _decisions(dbp, lid)
    assert [(r["action"], r["code"]) for r in rows] == [("reject", "generic")]
    assert rows[0]["note"] == "no failure signature to attach it to"


def test_decision_code_outside_the_taxonomy_is_refused(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lid = _candidate(dbp)
    out, status = lessons.reject_candidate_lesson(
        lid, "i_dont_like_it", db_path=dbp)
    assert out is None
    assert status.startswith("invalid:code must be one of")
    assert "unsupported_causality" in status
    assert _decisions(dbp, lid) == []
    conn = _db.connect(dbp)
    try:
        row = conn.execute("SELECT status FROM lessons WHERE lesson_id = ?",
                           (lid,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "candidate"


def test_the_two_evidence_constraints_are_part_of_the_rubric():
    # The taxonomy is only as good as the rule saying when a code may NOT
    # be chosen. Both constraints are asserted as text so a rewrite that
    # quietly drops them fails here.
    assert "unsuccessful" in dec.EVIDENCE_RULES[0].lower()
    assert "content defect" in dec.EVIDENCE_RULES[0].lower()
    assert "retriev" in dec.EVIDENCE_RULES[1].lower()
    assert ("storage" in dec.EVIDENCE_RULES[1].lower()
            or "store" in dec.EVIDENCE_RULES[1].lower())


def _candidate(dbp, lesson_id="les_c"):
    """A frontier-distilled candidate on a verified episode, the shape
    issue #10's queue produces: the episode closed verified on a clean
    test run, so the verification evidence is on the episode rather than
    handed to the promoter."""
    ep_id = verified_episode(dbp)
    store.add_attempt(ep_id, {"event_type": "verification",
                              "payload_json": '{"tests_passed": true}'},
                      db_path=dbp)
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO lessons (lesson_id, repo, failure_key,"
                  " instruction, anti_pattern, verification_json, status,"
                  " source_episode, valid_from, valid_to, expires_when)"
                  " VALUES (?, ?, ?, ?, '', ?, 'candidate', ?, ?, NULL, ?)",
                  (lesson_id, REPO, KEY, "retry with the absolute path",
                   '{"actor": "frontier", "trace_id": "tr1"}', ep_id,
                   "2026-09-22T10:00:00+00:00", None))
    finally:
        conn.close()
    return lesson_id


def test_adopting_a_candidate_records_one_decision_not_two(tmp_path):
    """The adoption tombstones the candidate, but a tombstone is not a
    diagnosis. Two rows for one accept - adopt/grounded_useful plus
    invalidate/unknown - would make the queue's accept/reject counts
    wrong and put a mechanism the operator never claimed on the record."""
    dbp = tmp_path / "m.sqlite"
    lid = _candidate(dbp)
    lesson, status = lessons.adopt_candidate_lesson(lid, db_path=dbp)
    assert status == "ok", status
    rows = _decisions(dbp, lid)
    assert [(r["action"], r["code"]) for r in rows] == [
        ("adopt", "grounded_useful")]
    conn = _db.connect(dbp)
    try:
        row = conn.execute("SELECT status, invalidated_diagnosis FROM lessons"
                           " WHERE lesson_id = ?", (lid,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "invalidated"
    assert row["invalidated_diagnosis"] is None
    assert lesson["status"] == "verified"


def test_a_rejected_candidate_never_becomes_a_verified_lesson(tmp_path):
    """The queue's other verdict: refusing a distillation must leave it
    out of retrieval for good, with the reason attached."""
    dbp = tmp_path / "m.sqlite"
    lid = _candidate(dbp)
    out, status = lessons.reject_candidate_lesson(
        lid, "speculative", note="no run ever showed the hinted cause",
        db_path=dbp)
    assert status == "ok", status
    assert out["status"] == "invalidated"
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []
    rows = _decisions(dbp, lid)
    assert [(r["action"], r["code"]) for r in rows] == [
        ("reject", "speculative")]


def test_rejecting_a_non_candidate_lesson_is_refused(tmp_path):
    """`reject` is the candidate queue's verb. Pointed at a live verified
    lesson it would tombstone one through the wrong door, with no
    diagnosis recorded."""
    dbp = tmp_path / "m.sqlite"
    lid = promoted_lesson(dbp)
    out, status = lessons.reject_candidate_lesson(lid, "generic",
                                                  db_path=dbp)
    assert out is None
    assert status == "rejected:lesson-status-verified"
    assert _decisions(dbp, lid) == []
