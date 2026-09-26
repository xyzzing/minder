"""Characterization tests for db.transaction() (audit F8), pinning the
semantics the 31 converted call sites rely on BEFORE the refactor:
commit-on-normal-exit (including an early `return` inside the body, which
commits an empty transaction — identical observable behavior to the old
close-discards-open-txn shape), rollback + re-raise on error, reads seeing
their own uncommitted writes, and the always-closed connection."""
import pytest

from minder_memory import db as _db
from minder_memory import resume_evidence

INSERT = ("INSERT INTO career_assertions (assertion_id, created_at, "
          "subject_digest, claim_text, wording_variants_json, actor) "
          "VALUES ('a1', '2026-01-01T00:00:00Z', 'd1', 'claim', '[]', "
          "'user')")


def _count(dbp, table):
    conn = _db.connect(dbp)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_commits_on_normal_exit_and_closes(tmp_path):
    dbp = tmp_path / "m.sqlite"
    with _db.transaction(dbp) as conn:
        conn.execute(INSERT)
    assert _count(dbp, "career_assertions") == 1


def test_early_return_inside_body_commits_empty_txn(tmp_path):
    """The `rejected:`/`reused` early returns (resume_evidence,
    trading_protocol, task_context) return BEFORE any write: an empty
    COMMIT must behave like the old close-without-commit."""
    dbp = tmp_path / "m.sqlite"

    def early():
        with _db.transaction(dbp) as conn:
            conn.execute("SELECT 1")
            return "rejected:nothing_written"

    assert early() == "rejected:nothing_written"
    assert _count(dbp, "career_assertions") == 0
    # and the write lock is released: a second transaction succeeds
    with _db.transaction(dbp) as conn:
        conn.execute("SELECT 1")


def test_rolls_back_and_reraises_on_error(tmp_path):
    dbp = tmp_path / "m.sqlite"
    with pytest.raises(Exception):
        with _db.transaction(dbp) as conn:
            conn.execute(INSERT)
            # same PK again: the violation must roll back BOTH rows
            conn.execute(INSERT)
    assert _count(dbp, "career_assertions") == 0


def test_reads_see_own_uncommitted_writes(tmp_path):
    """Post-commit re-SELECTs moved inside the with-body (the
    resume_evidence/trading_protocol shape) must read the row the same
    transaction just wrote."""
    dbp = tmp_path / "m.sqlite"
    with _db.transaction(dbp) as conn:
        conn.execute(INSERT)
        row = conn.execute(
            "SELECT assertion_id FROM career_assertions").fetchone()
        assert row["assertion_id"] == "a1"


def test_caller_rejected_path_unchanged(tmp_path):
    """API-level pin: approve_wording on an unknown id returns the
    rejected tuple and writes nothing (early return inside the txn)."""
    dbp = tmp_path / "m.sqlite"
    wording, status = resume_evidence.approve_wording(
        "nope", phrase="p", db_path=dbp)
    assert wording is None
    assert status == "rejected:unknown_assertion"
    assert _count(dbp, "career_assertions") == 0
