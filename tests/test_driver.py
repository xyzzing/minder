"""Harness driver tests (PRD v0.9 9E, AT-Q18/Q19/Q20)."""
import subprocess
import sys
import time

from minder_quality import driver

TASK_PY = '''SETTINGS = {"retries": 2}

def get_setting(key, settings=None):
    settings = settings or SETTINGS
    return settings[key]
'''

TEST_PY = '''from task import get_setting

def test_missing_key_returns_none():
    assert get_setting("absent_key") is None

def test_known_key_returns_value():
    assert get_setting("retries") == 2
'''

HOLDOUT_PY = '''from task import get_setting

def test_other_missing_key_returns_none():
    assert get_setting("other_absent") is None
'''

TASK = {
    "task_id": "t_demo",
    "tier": 3,
    "runner": {"kind": "pytest", "entry": ["test_task.py"],
               "expected_tests": 2, "allow_skips": 0},
    "fixtures": ["tasks/t_demo/task.py", "tasks/t_demo/test_task.py"],
    "holdout": ["tasks/t_demo/holdout/test_holdout.py"],
    "allowed_paths": ["task.py"],
}


def make_suite(tmp_path):
    suite = tmp_path / "suite"
    td = suite / "tasks" / "t_demo"
    (td / "holdout").mkdir(parents=True)
    (td / "task.py").write_text(TASK_PY)
    (td / "test_task.py").write_text(TEST_PY)
    (td / "holdout" / "test_holdout.py").write_text(HOLDOUT_PY)
    (td / "prompt.md").write_text(
        "Fix the KeyError in task.py: missing keys must return None.")
    return suite


FIXER = ("import pathlib, sys\n"
         "p = pathlib.Path(sys.argv[1])\n"
         "p.write_text(p.read_text().replace(\n"
         "    'return settings[key]', 'return settings.get(key)'))\n"
         "assert 'holdout' not in '\\n'.join(sorted(p.name for p in "
         "pathlib.Path('.').rglob('*'))), 'holdout visible!'\n")


def test_at_q18_mock_driver_fix_is_verified_holdout_invisible(tmp_path):
    suite = make_suite(tmp_path)
    d = {"id": "fixer",
         "argv": [sys.executable, str(tmp_path / "fixer.py"),
                  "{prompt_file}"],
         "timeout_s": 120}
    (tmp_path / "fixer.py").write_text(
        FIXER.replace("sys.argv[1]", "str(pathlib.Path('task.py'))"))
    work = tmp_path / "work"
    work.mkdir()
    entries = driver.run_task(suite, TASK, d, work)
    assert entries[0]["status"] == "verified"
    assert entries[0]["integrity"]["holdout"] == {"status": "pass"}
    assert entries[0]["quality"]["counts"]["blocking"] == 0


def test_at_q19_driver_timeout_kills_process_group(tmp_path):
    suite = make_suite(tmp_path)
    hunger = tmp_path / "hang.py"
    hunger.write_text("import time\n"
                      "time.sleep(300)\n")
    d = {"id": "hang", "argv": [sys.executable, str(hunger)],
         "timeout_s": 2}
    work = tmp_path / "work"
    work.mkdir()
    entries = driver.run_task(suite, TASK, d, work)
    assert entries[0]["status"] == "timeout"
    # no orphans: the process group is gone
    time.sleep(0.2)
    orphan = subprocess.run(["pgrep", "-f", "hang.py"],
                            capture_output=True, text=True)
    assert orphan.stdout.strip() == ""


def test_at_q20_repeats_produce_comparable_runs(tmp_path):
    suite = make_suite(tmp_path)
    d = {"id": "noop", "argv": [sys.executable, "-c", "pass"],
         "timeout_s": 60}
    work = tmp_path / "work"
    work.mkdir()
    entries = driver.run_task(suite, TASK, d, work, repeats=10)
    assert len(entries) == 10
    assert sorted(e["run_index"] for e in entries) == list(range(10))
    assert all(e["status"] in ("failed", "underverified", "tampered")
               for e in entries)  # noop never fixes; never verified


def test_tampering_driver_is_caught(tmp_path):
    suite = make_suite(tmp_path)
    cheater = tmp_path / "cheat.py"
    cheater.write_text(
        "import pathlib\n"
        "t = pathlib.Path('test_task.py')\n"
        "t.write_text('def test_ok():\\n    assert True\\n')\n")
    d = {"id": "cheat",
         "argv": [sys.executable, str(cheater)], "timeout_s": 60}
    work = tmp_path / "work"
    work.mkdir()
    entries = driver.run_task(suite, TASK, d, work)
    assert entries[0]["status"] == "tampered"
