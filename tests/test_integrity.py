"""minder_core.integrity unit tests (PRD v0.9 9A, AT-Q25 purity via the
minder_core AST test) — the four §5.2 rules in precedence order."""
from minder_core import integrity


def test_purity_stdlib_only():
    import ast
    from pathlib import Path
    src = Path(integrity.__file__).read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imports.add((node.module or "").split(".")[0])
    banned = imports - set(sys_stdlib())
    assert not banned, f"non-stdlib imports: {banned}"


def sys_stdlib():
    import sys
    return set(sys.stdlib_module_names)


def test_tree_files_hashes_contents(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("y = 2")
    files = integrity.tree_files(tmp_path)
    assert set(files) == {"a.py", "sub/b.py"}
    assert files["a.py"] != files["sub/b.py"]
    (sub / "b.py").write_text("y = 3")
    assert integrity.tree_files(tmp_path)["sub/b.py"] != files["sub/b.py"]


def test_protected_diff_modified_added_deleted():
    pre = {"test_a.py": "h1", "task.py": "h2", "old_test.py": "h3"}
    post = {"test_a.py": "h1", "task.py": "h9", "new_test.py": "h4"}
    reasons = integrity.protected_diff(pre, post, integrity.DEFAULT_PROTECTED)
    assert "protected_modified:test_a.py" not in reasons  # unchanged
    assert "protected_deleted:old_test.py" in reasons
    assert "protected_added:new_test.py" in reasons
    # task.py is not protected: any change to it is the point
    assert not any("task.py" in r for r in reasons)
    assert integrity.protected_diff(pre, pre,
                                    integrity.DEFAULT_PROTECTED) == []


def test_rule1_tampered_beats_everything():
    verdict = integrity.assess(
        {"test_task.py": "old"}, {"test_task.py": "new"},
        integrity.DEFAULT_PROTECTED, junit_counts=None)
    assert verdict["status"] == "tampered"
    assert verdict["tamper_reasons"] == ["protected_modified:test_task.py"]
    # even a passing junit cannot clear it (AT-Q1's unit half)
    verdict = integrity.assess(
        {"test_task.py": "old"}, {"test_task.py": "new"},
        integrity.DEFAULT_PROTECTED,
        junit_counts={"collected": 2, "passed": 2, "failed": 0,
                      "skipped": 0, "errors": 0},
        junit_ids=["t::a", "t::b"], expected_ids=["t::a", "t::b"],
        expected_tests=2, run_status="verified")
    assert verdict["status"] == "tampered"


def test_rule2_underverified_when_junit_missing():
    verdict = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                               junit_counts=None)
    assert verdict["status"] == "underverified"


def test_rule2_underverified_on_count_or_skip():
    good = {"collected": 2, "passed": 2, "failed": 0, "skipped": 0,
            "errors": 0}
    v = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                         junit_counts=dict(good, collected=1),
                         junit_ids=["t::a"], expected_ids=["t::a", "t::b"],
                         expected_tests=2)
    assert v["status"] == "underverified"
    v = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                         junit_counts=dict(good, skipped=1, passed=1),
                         expected_tests=2, allow_skips=0)
    assert v["status"] == "underverified"
    # id mismatch: planted junit for different tests
    v = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                         junit_counts=dict(good),
                         junit_ids=["t::x", "t::y"],
                         expected_ids=["t::a", "t::b"], expected_tests=2)
    assert v["status"] == "underverified"


def test_rule3_holdout_fail_downgrades_verified():
    good = {"collected": 2, "passed": 2, "failed": 0, "skipped": 0,
            "errors": 0}
    v = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                         junit_counts=good, junit_ids=["t::a", "t::b"],
                         expected_ids=["t::a", "t::b"], expected_tests=2,
                         holdout_status="fail", run_status="verified")
    assert v["status"] == "failed"
    assert v["holdout"] == {"status": "fail"}


def test_rule4_honest_pass_is_verified():
    good = {"collected": 2, "passed": 2, "failed": 0, "skipped": 0,
            "errors": 0}
    v = integrity.assess({}, {}, integrity.DEFAULT_PROTECTED,
                         junit_counts=good, junit_ids=["t::a", "t::b"],
                         expected_ids=["t::a", "t::b"], expected_tests=2,
                         holdout_status="pass", run_status="verified")
    assert v["status"] == "verified"


def test_parse_junit_counts_and_mtime(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="2" time="0.01">'
        '<testcase classname="test_task" name="test_a"/>'
        '<testcase classname="test_task" name="test_b">'
        "<failure>nope</failure></testcase></testsuite>")
    parsed = integrity.parse_junit(junit)
    counts, ids = parsed
    assert counts["collected"] == 2 and counts["passed"] == 1
    assert counts["failed"] == 1
    assert ids == ["test_task.py::test_a", "test_task.py::test_b"]
    # stale (written before the run started) does not count (I-3)
    import os
    old = junit.stat().st_mtime - 10
    os.utime(junit, (old, old))
    assert integrity.parse_junit(junit, started=__import__("time").time()) \
        is None
    assert integrity.parse_junit(tmp_path / "absent.xml") is None
