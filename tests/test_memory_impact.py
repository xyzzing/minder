"""Change-impact suggestion tests (spec P3.4)."""
from memory import impact, lessons, store

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def seed(dbp, with_tests=True):
    ep_id, _ = store.open_episode({"repo": REPO}, db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    verification = {"tests_passed": True}
    if with_tests:
        verification["tests"] = ["tests/test_supplier.py::test_lookup"]
    lessons.promote_lesson(ep_id, "seed the map", verification=verification,
                           repo=REPO, failure_key=KEY, db_path=dbp)


def test_file_with_linked_tests_returns_them(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed(dbp)
    out = impact.suggest_verification(["app/supplier.py"], REPO,
                                      db_path=dbp)
    assert "tests/test_supplier.py::test_lookup" in out["tests"]
    assert out["lessons_at_risk"]
    assert out["directive"].startswith("Run: pytest tests/test_supplier.py")
    assert "Revalidate lessons" in out["directive"]


def test_unknown_file_returns_empty_lists_not_error(tmp_path):
    dbp = tmp_path / "m.sqlite"
    out = impact.suggest_verification(["app/unknown.py"], REPO,
                                      db_path=dbp)
    assert out == {"tests": [], "lessons_at_risk": [], "directive": ""}


def test_directive_compact_and_contains_test_names(tmp_path):
    dbp = tmp_path / "m.sqlite"
    seed(dbp)
    out = impact.suggest_verification(["app/supplier.py"], REPO,
                                      db_path=dbp)
    assert 0 < len(out["directive"]) < 300
    assert "test_lookup" in out["directive"]
    # no tests known → directive still honest
    empty = impact.suggest_verification([], REPO, db_path=dbp)
    assert empty["directive"] == ""
