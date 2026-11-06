"""End-to-end lesson delivery (issue #80): a failing event recorded
through the real hook path becomes a verified episode, a promoted
lesson, and a delivered digest - one ledger row with digest_injected=1
and the exact tier. Nothing here seeds the store directly: the episode
comes from from_hook.record, the verification from a clean test run,
and the failure key from the episode itself."""
from minder_memory import (db as _db, from_hook,
                           lessons as memory_lessons,
                           policy as memory_policy)

FAIL_RESP = ("FAILED tests/test_supplier.py::test_lookup - "
             "KeyError: 'supplier_id'\n[exit code: 2]")
PASS_RESP = "2 passed in 0.31s"
FAIL_EVENT = {"session_id": "dlv-s1", "hook_event_name": "PostToolUse",
              "tool_name": "Bash",
              "tool_input": {"command": "pytest tests/test_supplier.py"},
              "tool_response": FAIL_RESP, "repo": "/repo", "cwd": "/repo"}
WARDEN = {"action": "think", "level": 1, "digest": "[minder] warden"}


def _ledger_rows(dbp):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM learning_injections").fetchall()]
    finally:
        conn.close()


def test_failure_to_delivered_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    # first failure: recorded through the hook path, episode open
    out1 = from_hook.record(FAIL_EVENT, db_path=dbp)
    assert out1["recorded"] is True and out1["episode_id"]
    # a clean pytest run closes the episode verified: the evidence is the
    # recognised runner and its clean output, not a payload flag
    out2 = from_hook.record(dict(FAIL_EVENT, tool_response=PASS_RESP),
                            db_path=dbp)
    assert out2["closed"] == "verified"
    # the operator promotes; verification and failure_key come from the
    # episode, not from this test
    lesson, status = memory_lessons.promote_lesson(
        out1["episode_id"], "supply supplier_id in the fixture dict",
        repo="/repo", db_path=dbp)
    assert status == "ok" and lesson["status"] == "verified"
    assert lesson["failure_key"] and lesson["failure_key"] != \
        "unknown|unknown|none|none"
    # the same failure again reaches the threshold: the guard directive
    # carries the lesson line
    out3 = from_hook.record(FAIL_EVENT, db_path=dbp)
    assert out3["recorded"] is True
    directive = memory_policy.evaluate(FAIL_EVENT, WARDEN, db_path=dbp)
    assert directive and directive["action"] == "block_duplicate"
    assert directive["duplicate"]["lesson"]["lesson_id"] == \
        lesson["lesson_id"]
    assert "supply supplier_id in the fixture dict" in directive["digest"]
    # the ledger names the delivery: the lesson row, exact tier. A skill
    # assist may also fire on the same event (both planes can deliver);
    # the lesson row is the one this test pins.
    rows = _ledger_rows(dbp)
    lesson_rows = [r for r in rows
                   if r["assist_mode"] == "block_duplicate"]
    assert len(lesson_rows) == 1
    assert lesson_rows[0]["lesson_id"] == lesson["lesson_id"]
    assert lesson_rows[0]["digest_injected"] == 1
    assert lesson_rows[0]["tier"] == "exact"
    skill_rows = [r for r in rows if r["assist_mode"] == "skill"]
    for r in skill_rows:
        assert r["skill_name"] and r["digest_injected"] == 1


def test_delivery_total_and_misses_separate_on_the_read_side(tmp_path):
    from minder_memory import injections as ledger
    dbp = tmp_path / "m.sqlite"
    from_hook.record(FAIL_EVENT, db_path=dbp)
    from_hook.record(dict(FAIL_EVENT, tool_response=PASS_RESP),
                     db_path=dbp)
    lesson, status = memory_lessons.promote_lesson(
        _first_episode(dbp), "supply supplier_id in the fixture dict",
        repo="/repo", db_path=dbp)
    assert status == "ok"
    from_hook.record(FAIL_EVENT, db_path=dbp)
    # a miss: a failure shape the store has no lesson for
    other = dict(FAIL_EVENT, session_id="dlv-s2", tool_name="Cargo",
                 tool_input={"command": "cargo build"},
                 tool_response="Error: could not compile (exit 101)")
    from_hook.record(other, db_path=dbp)
    from_hook.record(other, db_path=dbp)
    hit = memory_policy.evaluate(FAIL_EVENT, WARDEN, db_path=dbp)
    assert "VERIFIED LESSON" in hit["digest"]
    miss = memory_policy.evaluate(other, WARDEN, db_path=dbp)
    assert miss and miss["action"] == "block_duplicate"
    assert "VERIFIED LESSON" not in miss["digest"]

    # deliveries counts every digest that carried text (a skill assist
    # may fire alongside the lesson), misses counts the nothing rows
    counts = ledger.injection_counts(db_path=dbp)
    assert counts["misses"] == 1
    assert counts["deliveries"] >= 1
    assert [c["lesson_id"] for c in counts["lessons"]] == \
        [lesson["lesson_id"]]


def _first_episode(dbp):
    conn = _db.connect(dbp)
    try:
        return conn.execute(
            "SELECT episode_id FROM episodes WHERE status = 'verified'"
            " ORDER BY opened_at LIMIT 1").fetchone()["episode_id"]
    finally:
        conn.close()
