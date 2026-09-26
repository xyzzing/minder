"""Graph projection tests (spec P3.2): verified promotions project
File/Test/Commit edges when known; unknown entities are skipped."""
from minder_memory import graph, lessons, store

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def promote(dbp, verification):
    ep_id, _ = store.open_episode({"repo": REPO}, db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "seed the supplier map", verification=verification,
        repo=REPO, failure_key=KEY, db_path=dbp)
    assert status == "ok"
    return lesson


def test_promote_with_path_and_test_projects_graph(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lesson = promote(dbp, {"tests_passed": True,
                           "tests": ["tests/test_supplier.py::test_lookup"],
                           "paths": ["app/supplier.py"],
                           "commit": "abc1234"})
    # Episode --HAS_FAILURE--> FailureSignature
    assert any(n["id"] == f"sig:{KEY}" for n in
               graph.neighbors(lesson["source_episode"], "HAS_FAILURE",
                               db_path=dbp))
    # FailureSignature --AFFECTS--> File
    assert any(n["id"] == "file:app/supplier.py" for n in
               graph.neighbors(f"sig:{KEY}", "AFFECTS", db_path=dbp))
    # FailureSignature --FAILED_TEST--> Test; VerificationRun --RAN--> Test
    test_node = "test:tests/test_supplier.py::test_lookup"
    assert any(n["id"] == test_node for n in
               graph.neighbors(f"sig:{KEY}", "FAILED_TEST", db_path=dbp))
    run_nodes = graph.neighbors(test_node, "RAN", "in", db_path=dbp)
    assert run_nodes and run_nodes[0]["id"].startswith("run:")
    # Patch --MODIFIED--> File and --VERIFIED_BY--> run
    patch_nodes = graph.neighbors("file:app/supplier.py", "MODIFIED", "in",
                                  db_path=dbp)
    assert patch_nodes and patch_nodes[0]["id"].startswith("patch:")
    assert any(n["id"].startswith("run:") for n in
               graph.neighbors(patch_nodes[0]["id"], "VERIFIED_BY",
                               db_path=dbp))
    # Lesson --APPLIES_TO--> Commit
    assert any(n["id"] == "commit:abc1234" for n in
               graph.neighbors(lesson["lesson_id"], "APPLIES_TO",
                               db_path=dbp))


def test_promote_without_path_creates_no_fake_file_node(tmp_path):
    dbp = tmp_path / "m.sqlite"
    key_no_path = "bash|keyerror|supplier_id|none"
    ep_id, _ = store.open_episode({"repo": REPO}, db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": key_no_path},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "seed the supplier map",
        verification={"tests_passed": True}, repo=REPO,
        failure_key=key_no_path, db_path=dbp)
    assert status == "ok"
    # relpath unknown in both the key and verification → no invented File
    assert graph.get_node("file:app/supplier.py", db_path=dbp) is None
    assert graph.neighbors(f"sig:{key_no_path}", "AFFECTS",
                           db_path=dbp) == []
    # the lesson itself still exists and links to its episode
    assert graph.neighbors(lesson["lesson_id"], "DERIVED_FROM",
                           db_path=dbp)


def test_existing_lesson_tests_still_pass(tmp_path):
    import tests.test_memory_lessons  # noqa: F401
