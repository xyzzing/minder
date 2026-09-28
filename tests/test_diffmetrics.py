"""diffmetrics tests (PRD v0.9 9B, AT-Q9..Q12 + detector pins)."""
from minder_core.diffmetrics import BLOCKING_RULES, measure


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


BUGGY = '''SETTINGS = {"retries": 2}

def get_setting(key, settings=None):
    settings = settings or SETTINGS
    return settings[key]
'''

FIXED = '''SETTINGS = {"retries": 2}

def get_setting(key, settings=None):
    settings = settings or SETTINGS
    return settings.get(key)
'''


def test_at_q9_identical_trees_zero_everything(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", FIXED)
    write(post, "task.py", FIXED)
    record = measure(pre, post)
    assert record["measured"] is True
    assert record["counts"] == {"blocking": 0, "advisory": 0}
    assert record["diff"]["net_lines"] == 0


def test_at_q10_pre_existing_swallow_untouched_not_counted(tmp_path):
    sloppy = BUGGY + '''
def helper(key):
    try:
        return SETTINGS[key]
    except Exception:
        pass
'''
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", sloppy)
    write(post, "task.py", sloppy)  # untouched
    record = measure(pre, post)
    assert record["counts"]["blocking"] == 0


def test_new_swallow_is_blocking(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY.replace(
        "return settings[key]",
        "try:\n        return settings[key]\n    except Exception:\n"
        "        pass"))
    record = measure(pre, post)
    rules = [f["rule"] for f in record["new_findings"]]
    assert "core.broad_except_swallow" in rules
    assert record["counts"]["blocking"] >= 1


def test_at_q11_near_duplicate_renamed_is_advisory(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY + '''

def fetch_setting(key, config=None):
    config = config or SETTINGS
    return config[key]
''')
    record = measure(pre, post)
    dupes = [f for f in record["new_findings"]
             if f["rule"] == "core.near_duplicate_def"]
    assert len(dupes) == 1 and dupes[0]["severity"] == "advisory"


def test_at_q12_undeclared_import_is_blocking(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", "import requests\n" + BUGGY)
    record = measure(pre, post)
    rules = [f["rule"] for f in record["new_findings"]]
    assert "core.undeclared_import" in rules
    assert record["counts"]["blocking"] >= 1


def test_stdlib_and_local_imports_are_not_findings(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py",
          "import json\nimport os\nfrom helper import thing\n"
          + BUGGY)
    write(post, "helper.py", "thing = 1\n")
    record = measure(pre, post, allowed_paths=["task.py", "helper.py"])
    assert not [f for f in record["new_findings"]
                if f["rule"] == "core.undeclared_import"]


def test_placeholder_and_skip_marker_and_debug_residue(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY + '''

def not_written_yet():
    pass
''')
    write(post, "task_debug.py", 'print("debug residue")\n')
    write(pre, "test_task.py",
          "import pytest\n\ndef test_a():\n    assert True\n")
    write(post, "test_task.py",
          "import pytest\n\n"
          "@pytest.mark.skip(reason='wip')\n"
          "def test_a():\n    assert True\n")
    record = measure(pre, post)
    rules = {f["rule"] for f in record["new_findings"]}
    assert "core.placeholder_body" in rules      # blocking
    assert "core.skip_marker_added" in rules     # blocking
    assert "core.debug_residue" in rules         # advisory, top-level
    assert "core.undeclared_import" not in rules  # pytest was in pre


def test_debug_residue_top_level_only(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY + '''

def has_a_print_inside():
    print("inside a function is not top-level")
''')
    record = measure(pre, post)
    assert not [f for f in record["new_findings"]
                if f["rule"] == "core.debug_residue"]


def test_todo_and_orphan_file_advisory(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY + "\n# TODO: clean this up\n")
    write(post, "debug_scratch.py", "x = 1\n")
    record = measure(pre, post)
    rules = {f["rule"]: f for f in record["new_findings"]}
    assert "core.todo_marker" in rules
    assert "core.orphan_file" in rules
    assert rules["core.orphan_file"]["path"] == "debug_scratch.py"


def test_syntax_error_is_blocking_not_crash(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(post, "task.py", BUGGY + "\ndef broken(:\n")
    record = measure(pre, post)
    assert any(f["rule"] == "core.syntax_error"
               for f in record["new_findings"])


def test_scope_violation_recorded_not_blocked(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(pre, "other.py", "A = 1\n")
    write(post, "task.py", FIXED)
    write(post, "other.py", "A = 2\n")
    record = measure(pre, post, allowed_paths=["task.py"])
    assert "other.py" in record["diff"]["scope_violations"]


def test_diff_ratio_vs_reference(tmp_path):
    pre, post, ref = (tmp_path / "pre", tmp_path / "post",
                      tmp_path / "ref")
    write(pre, "task.py", "a\n")
    write(ref, "task.py", "a\nb\n")            # reference net = +1
    write(post, "task.py", "a\nb\nc\n")        # candidate net  = +2
    record = measure(pre, post, reference_dir=ref)
    assert record["diff"]["diff_ratio_vs_reference"] == 2.0


def test_blocking_rules_pin():
    assert BLOCKING_RULES == frozenset({
        "core.protected_path_edit", "core.broad_except_swallow",
        "core.placeholder_body", "core.skip_marker_added",
        "core.undeclared_import", "core.syntax_error"})


def test_protected_edit_surfaces_as_finding(tmp_path):
    pre, post = tmp_path / "pre", tmp_path / "post"
    write(pre, "task.py", BUGGY)
    write(pre, "test_task.py", "def test_a():\n    assert True\n")
    write(post, "task.py", FIXED)
    write(post, "test_task.py", "def test_ok():\n    assert True\n")
    record = measure(pre, post)
    assert any(f["rule"] == "core.protected_path_edit"
               and f["path"] == "test_task.py"
               for f in record["new_findings"])
