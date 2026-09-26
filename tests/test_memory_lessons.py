"""Verified lesson create/retrieve/invalidate (docs/prd-memory-v1.md PR 4)."""

from minder_memory import lessons, policy, retrieval, store

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def verified_episode(dbp, repo=REPO, key=KEY, n=2):
    ep_id, _ = store.open_episode({"repo": repo, "task_id": "t1"}, db_path=dbp)
    for _ in range(n):
        store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                                  "repo": repo, "failure_key": key},
                          db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    return ep_id


def test_unverified_episode_cannot_promote(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id, _ = store.open_episode({"repo": REPO}, db_path=dbp)
    store.close_episode(ep_id, "candidate", db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "do the thing", verification={"tests_passed": True},
        db_path=dbp)
    assert lesson is None and status == "rejected:episode-status-candidate"


def test_promotion_requires_tests_passed(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "seed the map first", verification={"tests_passed": False},
        db_path=dbp)
    assert lesson is None and status == "rejected:no-verified-tests"
    lesson, status = lessons.promote_lesson(
        ep_id, "seed the map first",
        verification={"tests_passed": True, "tests": ["tests/test_s.py"]},
        db_path=dbp)
    assert status == "ok" and lesson["status"] == "verified"
    assert lesson["failure_key"] == KEY  # inherited from episode evidence
    assert lesson["repo"] == REPO


def test_retrieve_same_repo_same_key(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lessons.promote_lesson(ep_id, "seed the supplier map before queries",
                           anti_pattern="querying an empty cache",
                           verification={"tests_passed": True}, db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert len(got) == 1
    assert got[0]["instruction"] == "seed the supplier map before queries"
    assert got[0]["verification"]["tests_passed"] is True


def test_no_cross_repo_retrieval(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lessons.promote_lesson(ep_id, "seed it", verification={"tests_passed": True},
                           db_path=dbp)
    assert retrieval.retrieve_lessons("/other", KEY, db_path=dbp) == []


def test_invalidated_lesson_excluded(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lesson, _ = lessons.promote_lesson(ep_id, "seed it",
                                       verification={"tests_passed": True},
                                       db_path=dbp)
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    out, status = lessons.invalidate_lesson(lesson["lesson_id"],
                                            "superseded by schema v2",
                                            db_path=dbp)
    assert status == "ok" and out["valid_to"]
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []


def test_limit_enforced(tmp_path):
    dbp = tmp_path / "m.sqlite"
    for i in range(5):
        ep_id = verified_episode(dbp)
        lessons.promote_lesson(ep_id, f"lesson {i}",
                               verification={"tests_passed": True},
                               db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp, limit=3)
    assert len(got) == 3


def test_retrieved_payload_compact_no_raw_logs(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lessons.promote_lesson(ep_id, "seed it",
                           verification={"tests_passed": True},
                           db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert set(got[0]) == {"lesson_id", "instruction", "anti_pattern",
                           "verification", "status"}
    assert "payload" not in got[0] and "tool_logs" not in got[0]


def test_duplicate_guard_digest_includes_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp, key="bash|exit-2|none|makefile")
    lessons.promote_lesson(ep_id, "run make clean before make lint",
                           anti_pattern="re-running lint on stale objects",
                           verification={"tests_passed": True},
                           repo="/repo", db_path=dbp)
    # two identical failed attempts for the same key, recorded with repo
    raw = {"session_id": "guard-l1", "hook_event_name": "PostToolUse",
           "tool_name": "Bash", "tool_input": {"command": "make lint"},
           "tool_response": "process failed: exit code 2", "repo": "/repo"}
    from minder_memory import canonicalise as canon
    for _ in range(2):
        e = policy._as_event(raw)
        e["failure_key"] = canon.failure_key(e, "/repo")
        e["action_fingerprint"] = canon.action_fingerprint(e, "/repo")
        store.record_event(e, db_path=dbp)
    out = policy.evaluate(raw, db_path=dbp, repo="/repo")
    assert out and out["action"] == "block_duplicate"
    assert "run make clean before make lint" in out["digest"]
    assert out["duplicate"]["lesson"]["anti_pattern"] == \
        "re-running lint on stale objects"
