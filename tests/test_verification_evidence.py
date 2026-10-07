"""Test-runner success as verification evidence (issue #9).

The live store held 234 episodes and zero lessons because nothing ever set
`verification.tests_passed`, so `_close_on_success` always took the
candidate branch. These tests pin the producer: a clean test-runner run
verifies the session's open episode.
"""

from minder_memory import from_hook, lessons, retrieval, store

REPO = "/repo"
FAIL_RESPONSE = ("FAILED tests/test_supplier.py::test_lookup - "
                 "KeyError: 'supplier_id'")

pytest_event = {
    "session_id": "ver-s1", "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "pytest tests/test_supplier.py"},
    "tool_response": FAIL_RESPONSE,
    "repo": REPO, "cwd": REPO,
}


def _open_episode_with_failure(dbp, event=None):
    out = from_hook.record(event or pytest_event, db_path=dbp)
    assert out["recorded"] is True
    return out["episode_id"]


def _episode(dbp, episode_id):
    conn = _conn(dbp)
    try:
        row = conn.execute("SELECT * FROM episodes WHERE episode_id = ?",
                           (episode_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _conn(dbp):
    from minder_memory import db as mdb
    return mdb.connect(dbp)


def _episode_payloads(dbp, episode_id):
    return [e.get("payload_json") or ""
            for e in store.episode_events(episode_id, db_path=dbp)]


# --- acceptance 1: a clean test run closes the episode verified ----------

def test_clean_test_run_closes_episode_verified(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = _open_episode_with_failure(dbp)
    ok = dict(pytest_event, tool_response="7 passed in 0.42s")
    out = from_hook.record(ok, db_path=dbp)
    assert out["closed"] == "verified"
    assert _episode(dbp, ep_id)["status"] == "verified"


def test_recognised_runners_verify(tmp_path):
    for i, (cmd, out_text) in enumerate((
            ("python3 -m pytest tests/test_a.py", "12 passed in 0.42s"),
            ("npx vitest run", "Tests  8 passed (8)"),
            ("npm test", "24 passing (1s)"),
            ("go test ./...", "ok  \t0.31s"),
            ("cargo test", "test result: ok. 15 passed; 0 failed"),
            ("make test", "7 passed in 1.10s"),
            ("jest tests/a.test.js", "Tests: 3 passed, 3 total"))):
        dbp = tmp_path / f"m{i}.sqlite"
        ep_id = _open_episode_with_failure(
            dbp, dict(pytest_event, session_id=f"ver-r{i}"))
        out = from_hook.record(dict(pytest_event, session_id=f"ver-r{i}",
                                    tool_input={"command": cmd},
                                    tool_response=out_text),
                               db_path=dbp)
        assert out["closed"] == "verified", cmd
        assert _episode(dbp, ep_id)["status"] == "verified", cmd


# --- acceptance 2: a non-test success stays candidate -------------------

def test_clean_non_test_run_stays_candidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _open_episode_with_failure(dbp)
    out = from_hook.record(dict(pytest_event,
                                tool_input={"command": "git status"},
                                tool_response="clean"), db_path=dbp)
    assert out["closed"] == "candidate"


# --- acceptance 3: the evidence is persisted, redacted ------------------

def test_verification_evidence_recorded_on_episode(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = _open_episode_with_failure(dbp)
    from_hook.record(dict(pytest_event, tool_response="7 passed in 0.42s"),
                     db_path=dbp)
    payloads = _episode_payloads(dbp, ep_id)
    assert any('"tests_passed": true' in p for p in payloads)
    assert any("pytest" in p for p in payloads)


def test_verification_evidence_is_redacted(tmp_path):
    dbp = tmp_path / "m.sqlite"
    secret = "sk-proj-abcdefghijklmnop1234"
    ep_id = _open_episode_with_failure(dbp)
    from_hook.record(dict(pytest_event,
                          tool_input={"command": f"pytest -k x {secret}"},
                          tool_response="7 passed in 0.42s"), db_path=dbp)
    joined = " ".join(_episode_payloads(dbp, ep_id))
    assert secret not in joined
    assert "REDACTED" in joined


# --- acceptance 4: recognition is a module, not inlined -----------------

def test_runner_recognition_lives_in_a_module():
    from minder_core import verification
    assert verification.test_runner("pytest tests/test_a.py") == "pytest"
    assert verification.test_runner("python3 -m pytest -q") == "pytest"
    assert verification.test_runner("git status") is None
    assert verification.test_runner("") is None
    # a runner name inside an argument must not count as a runner call
    assert verification.test_runner("cat pytest_notes.md") is None


# --- acceptance 5: a failing test run verifies nothing ------------------

def test_failing_test_run_does_not_verify(tmp_path):
    """`1 failed, 3 passed` carries no FAIL_SIGNS substring, so the Warden
    classifies it as a success and the episode does get closed. It must
    close as candidate, never verified: a run that reports a failing count
    verifies nothing."""
    dbp = tmp_path / "m.sqlite"
    ep_id = _open_episode_with_failure(dbp)
    out = from_hook.record(dict(pytest_event,
                                tool_response="1 failed, 3 passed in 0.9s"),
                           db_path=dbp)
    assert out["closed"] == "candidate"
    assert _episode(dbp, ep_id)["status"] == "candidate"


def test_count_free_output_does_not_verify(tmp_path):
    """A recognised runner whose output states no result counts is not
    evidence either."""
    dbp = tmp_path / "m.sqlite"
    ep_id = _open_episode_with_failure(dbp)
    out = from_hook.record(dict(pytest_event, tool_response=""), db_path=dbp)
    assert out["closed"] == "candidate"
    assert _episode(dbp, ep_id)["status"] == "candidate"


# --- the loop closes: a verified episode can yield a retrieved lesson ---

def test_verified_episode_yields_a_retrievable_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = _open_episode_with_failure(dbp)
    from_hook.record(dict(pytest_event, tool_response="7 passed in 0.42s"),
                     db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "seed the supplier map before the lookup",
        verification={"tests_passed": True}, repo=REPO, db_path=dbp)
    assert status == "ok" and lesson["status"] == "verified"
    key = lesson["failure_key"]
    got = retrieval.retrieve_lessons(REPO, key, db_path=dbp)
    assert [g["instruction"] for g in got] == \
        ["seed the supplier map before the lookup"]
