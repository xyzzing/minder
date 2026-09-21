"""Lesson invalidation / supersession / contradiction tests (spec P3.3,
P3.5)."""
from memory import invalidation, lessons, retrieval, store

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def verified_lesson(dbp, instruction, key=KEY, repo=REPO, commit=None):
    ep_id, _ = store.open_episode({"repo": repo}, db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": repo, "failure_key": key},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    verification = {"tests_passed": True}
    if commit:
        verification["commit"] = commit
    lesson, status = lessons.promote_lesson(
        ep_id, instruction, verification=verification, repo=repo,
        failure_key=key, db_path=dbp)
    assert status == "ok"
    return lesson


def test_superseded_lesson_not_retrieved(tmp_path):
    dbp = tmp_path / "m.sqlite"
    old = verified_lesson(dbp, "seed the map (old)")
    new = verified_lesson(dbp, "seed the map via the loader (new)")
    assert len(retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)) == 2
    assert invalidation.supersede_lesson(old["lesson_id"],
                                         new["lesson_id"], db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert [g["lesson_id"] for g in got] == [new["lesson_id"]]


def test_invalidated_lesson_not_retrieved(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lesson = verified_lesson(dbp, "seed the map")
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    lessons.invalidate_lesson(lesson["lesson_id"], "wrong advice",
                              db_path=dbp)
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []


def test_newer_verified_lesson_retrieved_instead(tmp_path):
    dbp = tmp_path / "m.sqlite"
    old = verified_lesson(dbp, "old advice")
    new = verified_lesson(dbp, "new advice")
    invalidation.supersede_lesson(old["lesson_id"], new["lesson_id"],
                                  db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert got and got[0]["instruction"] == "new advice"


def test_same_commit_still_retrieved(tmp_path):
    dbp = tmp_path / "m.sqlite"
    verified_lesson(dbp, "seed the map", commit="abc1234")
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp,
                                     current_commit="abc1234")
    assert got and got[0]["instruction"] == "seed the map"


def test_change_invalidates_scoped_lessons(tmp_path):
    dbp = tmp_path / "m.sqlite"
    verified_lesson(dbp, "seed the map")  # affects app/supplier.py
    n = invalidation.invalidate_lessons_for_change(
        {"repo": REPO, "paths": ["app/supplier.py"], "commit": "def5678",
         "reason": "schema changed"}, db_path=dbp)
    assert n == 1
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []
    # commit node marked changed for the P3.3 commit filter
    node = __import__("memory.graph", fromlist=["get_node"]).get_node(
        "commit:def5678", db_path=dbp)
    assert node["properties"]["changed"] is True


def test_unrelated_path_change_does_not_invalidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    verified_lesson(dbp, "seed the map")
    n = invalidation.invalidate_lessons_for_change(
        {"repo": REPO, "paths": ["app/other.py"], "commit": "def5678",
         "reason": "unrelated"}, db_path=dbp)
    assert n == 0
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)


def test_contradiction_serves_one_authoritative_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    old = verified_lesson(dbp, "write the cache before querying")
    new = verified_lesson(dbp, "query directly; the cache lies")
    # divergent instructions on the same repo+failure_key
    assert invalidation.mark_contradiction(old["lesson_id"],
                                           new["lesson_id"],
                                           db_path=dbp)
    got = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert [g["lesson_id"] for g in got] == [new["lesson_id"]]
    # history stays queryable in the graph
    hist = __import__("memory.graph", fromlist=["neighbors"]).neighbors(
        new["lesson_id"], "CONTRADICTS", db_path=dbp, include_invalid=True)
    assert hist and hist[0]["id"] == old["lesson_id"]
    # identical instructions are NOT a contradiction
    twin = verified_lesson(dbp, "query directly; the cache lies")
    other = verified_lesson(dbp, "query directly; the cache lies")
    assert invalidation.mark_contradiction(twin["lesson_id"],
                                           other["lesson_id"],
                                           db_path=dbp) is False
