"""Trace review storage tests (Slice 3, migration 014).

The memory plane's contract is the thing under test here: append-only,
never-raise, status-string returns, and a closed feedback vocabulary. A
category outside the taxonomy would silently break every count that
depends on it, so it is refused at the door rather than stored and
filtered later.
"""
import sqlite3

import pytest

from minder_memory import db as _db, trace_reviews

FINDINGS = [
    {"finding_id": "trf_a", "session_id": "session-1", "evaluator": "x",
     "rule_id": "r", "severity": "high",
     "evidence": {"ds_seqs": [1], "excerpts": [], "call_ids": []},
     "message": "m", "suggested_fix": "f"},
]
RUN = {"run_id": "mndr_run_abc", "source": {"session_id": "session-1"}}


@pytest.fixture
def dbp(tmp_path):
    path = tmp_path / "m.sqlite"
    _db.connect(path).close()
    return path


def _store(dbp, session="session-1"):
    run = dict(RUN, source={"session_id": session})
    return trace_reviews.store_review(run, FINDINGS,
                                      {"findings": 1, "highest_severity":
                                       "high"},
                                      report={"status": "ok"}, db_path=dbp)


def test_round_trip(dbp):
    review_id, status = _store(dbp)
    assert status == "ok" and review_id
    review = trace_reviews.get_review(review_id, db_path=dbp)
    assert review["session_id"] == "session-1"
    assert review["run_id"] == "mndr_run_abc"
    assert review["findings"][0]["finding_id"] == "trf_a"
    assert review["summary"]["findings"] == 1
    assert review["evaluator_version"] == trace_reviews.EVALUATOR_VERSION
    assert review["redaction_status"] == "redacted"


def test_reviews_are_append_only(dbp):
    review_id, _ = _store(dbp)
    conn = _db.connect(dbp)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _db.write(conn, "UPDATE trace_reviews SET status = 'x'"
                            " WHERE review_id = ?", (review_id,))
        with pytest.raises(sqlite3.IntegrityError):
            _db.write(conn, "DELETE FROM trace_reviews")
    finally:
        conn.close()


def test_two_reviews_of_one_session_both_survive(dbp):
    first, _ = _store(dbp)
    second, _ = _store(dbp)
    assert first != second
    listed = trace_reviews.list_reviews("session-1", db_path=dbp)
    assert len(listed) == 2


def test_latest_review_tracks_the_newest(dbp):
    _store(dbp)
    second, _ = _store(dbp)
    assert trace_reviews.latest_review("session-1",
                                       db_path=dbp)["review_id"] == second


def test_get_missing_review_is_none_not_an_error(dbp):
    assert trace_reviews.get_review("nope", db_path=dbp) is None


def test_store_fails_open_on_a_broken_db(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"junk" * 200)
    review_id, status = trace_reviews.store_review(
        RUN, FINDINGS, {}, report={"status": "ok"}, db_path=bad)
    assert review_id is None and status.startswith("degraded:")


def test_reads_fail_open_on_a_broken_db(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"junk" * 200)
    assert trace_reviews.get_review("x", db_path=bad) is None
    assert trace_reviews.list_reviews(db_path=bad) == []
    assert trace_reviews.list_feedback(db_path=bad) == []


# --- feedback ------------------------------------------------------------

def test_feedback_round_trip_and_redaction(dbp):
    review_id, _ = _store(dbp)
    secret = "sk-proj-operatorleak99999999"
    feedback_id, status = trace_reviews.store_feedback(
        review_id, "run", "partly_correct",
        comment=f"the key {secret} leaked", reviewer="alice", db_path=dbp)
    assert status == "ok" and feedback_id
    items = trace_reviews.list_feedback(review_id, db_path=dbp)
    assert len(items) == 1
    assert secret not in (items[0]["comment"] or "")
    assert items[0]["level"] == "run"
    assert items[0]["category"] == "partly_correct"
    assert items[0]["reviewer"] == "alice"


def test_feedback_rejects_a_category_outside_the_taxonomy(dbp):
    review_id, _ = _store(dbp)
    feedback_id, status = trace_reviews.store_feedback(
        review_id, "run", "looks_fine_to_me", db_path=dbp)
    assert feedback_id is None
    assert status.startswith("invalid:")
    assert trace_reviews.list_feedback(review_id, db_path=dbp) == []


def test_feedback_rejects_an_unknown_level(dbp):
    review_id, _ = _store(dbp)
    feedback_id, status = trace_reviews.store_feedback(
        review_id, "session", "correct", db_path=dbp)
    assert feedback_id is None and status.startswith("invalid:")


def test_feedback_requires_a_finding_for_a_verdict(dbp):
    review_id, _ = _store(dbp)
    _fid, status = trace_reviews.store_feedback(
        review_id, "run", "correct", finding_verdict="confirm", db_path=dbp)
    assert status.startswith("invalid:")


def test_feedback_rejects_an_unknown_verdict(dbp):
    review_id, _ = _store(dbp)
    _fid, status = trace_reviews.store_feedback(
        review_id, "run", "correct", finding_id="trf_a",
        finding_verdict="maybe", db_path=dbp)
    assert status.startswith("invalid:")


def test_feedback_on_a_missing_review_is_refused(dbp):
    feedback_id, status = trace_reviews.store_feedback(
        "nope", "run", "correct", db_path=dbp)
    assert feedback_id is None and status.startswith("not_found:")


def test_feedback_is_append_only(dbp):
    review_id, _ = _store(dbp)
    feedback_id, _ = trace_reviews.store_feedback(review_id, "run",
                                                  "correct", db_path=dbp)
    conn = _db.connect(dbp)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _db.write(conn, "UPDATE trace_feedback SET category = 'x'"
                            " WHERE feedback_id = ?", (feedback_id,))
    finally:
        conn.close()


def test_finding_verdicts_last_one_wins(dbp):
    """A reviewer may change their mind; later feedback is newer evidence."""
    review_id, _ = _store(dbp)
    trace_reviews.store_feedback(review_id, "run", "correct",
                                 finding_id="trf_a",
                                 finding_verdict="reject", db_path=dbp)
    trace_reviews.store_feedback(review_id, "run", "correct",
                                 finding_id="trf_a",
                                 finding_verdict="confirm", db_path=dbp)
    assert trace_reviews.finding_verdicts(review_id,
                                          db_path=dbp) == {"trf_a":
                                                           "confirm"}


def test_acceptance_stats_reports_the_rate(dbp):
    review_id, _ = _store(dbp)
    trace_reviews.store_feedback(review_id, "run", "correct",
                                 finding_id="trf_a",
                                 finding_verdict="confirm", db_path=dbp)
    trace_reviews.store_feedback(review_id, "run", "correct",
                                 finding_id="trf_b",
                                 finding_verdict="reject", db_path=dbp)
    stats = trace_reviews.acceptance_stats(db_path=dbp)
    assert stats == {"confirmed": 1, "rejected": 1, "total": 2,
                     "acceptance_rate": 0.5}


def test_acceptance_stats_with_no_feedback_has_no_rate(dbp):
    assert trace_reviews.acceptance_stats(db_path=dbp)["acceptance_rate"] \
        is None
