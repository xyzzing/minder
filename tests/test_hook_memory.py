"""Hook → memory wiring tests (docs/prd-memory-v1.md PR 5)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from memory import from_hook, store

HOOK = str(Path(__file__).resolve().parent.parent / "hook.py")
SUBPROC_TIMEOUT = int(os.environ.get("MINDER_TEST_TIMEOUT", "60"))

FAIL_EVENT = {"session_id": "mem-s1", "hook_event_name": "PostToolUse",
              "tool_name": "Bash",
              "tool_input": {"command": "pytest tests/test_supplier.py"},
              "tool_response": "process failed: exit code 2",
              "repo": "/repo", "cwd": "/repo"}


def test_hook_failure_writes_expected_failure_key(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    out = from_hook.record(FAIL_EVENT, db_path=dbp)
    assert out["recorded"] is True and out["event_id"]
    rows = store.list_events(out["event_id"] and
                             _key_of(dbp, out["event_id"]), db_path=dbp)
    assert rows and rows[0]["failure_key"].startswith("bash|")
    # a pytest-shaped excerpt yields the node id as the symbol segment
    ev2 = dict(FAIL_EVENT, tool_response=
               "FAILED tests/test_supplier.py::test_lookup - "
               "KeyError: 'supplier_id'")
    from memory import canonicalise as canon
    ev = from_hook.to_event(ev2)
    assert canon.failure_key(ev, "/repo").split("|")[2] == \
        "tests/test_supplier.py::test_lookup"


def _key_of(dbp, event_id):
    ev = store.event_by_id(event_id, db_path=dbp)
    return ev["failure_key"]


def test_two_failures_same_key_increment_attempts(tmp_path):
    dbp = tmp_path / "m.sqlite"
    from memory import canonicalise as canon
    for _ in range(2):
        out = from_hook.record(FAIL_EVENT, db_path=dbp)
    ev = from_hook.to_event(FAIL_EVENT)
    key = canon.failure_key(ev, "/repo")
    assert store.count_attempts(key, db_path=dbp) == 2
    # both attempts live in one open episode
    eps = _episodes(dbp)
    assert len([e for e in eps if e["status"] == "open"]) == 1


def _episodes(dbp):
    from memory import db as mdb
    conn = mdb.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM episodes").fetchall()]
    finally:
        conn.close()


def test_success_without_verification_closes_candidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    from_hook.record(FAIL_EVENT, db_path=dbp)
    ok = dict(FAIL_EVENT, tool_response="all tests passed")
    out = from_hook.record(ok, db_path=dbp)
    assert out["closed"] == "candidate"
    ep = _episodes(dbp)[0]
    assert ep["status"] == "candidate" and ep["closed_at"]


def test_success_with_tests_passed_closes_verified(tmp_path):
    dbp = tmp_path / "m.sqlite"
    from_hook.record(FAIL_EVENT, db_path=dbp)
    ok = dict(FAIL_EVENT, tool_response="all tests passed",
              verification={"tests_passed": True,
                            "tests": ["tests/test_supplier.py"]})
    out = from_hook.record(ok, db_path=dbp)
    assert out["closed"] == "verified"


def test_zcode_transport_still_exit0(tmp_path):
    proc = _run_hook(FAIL_EVENT, "zcode", tmp_path)
    assert proc.returncode == 0


def test_dsh_transport_decision_payload(tmp_path):
    proc = _run_hook(dict(FAIL_EVENT, tool_response="error: boom"),
                     "dsh", tmp_path, twice=False)
    assert proc.returncode == 0  # single failure: no digest, silent pass
    assert proc.stderr == ""


def _run_hook(event, transport, tmp_path, twice=True):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "MINDER_STATE_DIR": str(tmp_path / "state"),
        "MINDER_CONFIG": str(tmp_path / "minder.json"),
        "MINDER_CAPS": str(tmp_path / "caps.json"),
        "HOME": str(tmp_path),
        "MINDER_MEMORY_DB": str(tmp_path / "mem.sqlite"),
    }
    inp = json.dumps(event)
    if twice:  # second identical failure: guard replaces the directive
        subprocess.run([sys.executable, HOOK, "--transport", transport],
                       input=inp, capture_output=True, text=True, env=env,
                       timeout=SUBPROC_TIMEOUT)
    return subprocess.run([sys.executable, HOOK, "--transport", transport],
                          input=inp, capture_output=True, text=True,
                          env=env, timeout=SUBPROC_TIMEOUT)


def test_guard_fires_through_real_hook_path(tmp_path):
    """Definition of done #1: two identical failures through the real hook
    produce the duplicate-block directive, with the DB populated by the
    hook itself (not seeded)."""
    proc = _run_hook(FAIL_EVENT, "zcode", tmp_path)
    payload = json.loads(proc.stdout)
    assert payload["decision"] == "block"
    assert "duplicate guard" in payload["reason"]
    assert (tmp_path / "mem.sqlite").exists()


def test_existing_hook_behaviour_unchanged(tmp_path):
    """First failure stays silent end-to-end (record + warden agree)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "MINDER_STATE_DIR": str(tmp_path / "state"),
        "MINDER_CONFIG": str(tmp_path / "minder.json"),
        "MINDER_CAPS": str(tmp_path / "caps.json"),
        "HOME": str(tmp_path),
        "MINDER_MEMORY_DB": str(tmp_path / "mem.sqlite"),
    }
    proc = subprocess.run([sys.executable, HOOK, "--transport", "zcode"],
                          input=json.dumps(FAIL_EVENT), capture_output=True,
                          text=True, env=env, timeout=SUBPROC_TIMEOUT)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}
