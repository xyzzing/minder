"""Duplicate-action guard tests (docs/prd-memory-v1.md PR 3)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import minder
from memory import canonicalise as canon
from memory import policy, store

HOOK = str(Path(__file__).resolve().parent.parent / "hook.py")
SUBPROC_TIMEOUT = int(os.environ.get("MINDER_TEST_TIMEOUT", "60"))

REPO = "/repo"


def raw_event(**kw):
    base = {"session_id": "guard-s1", "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "make lint"},
            "tool_response": "process failed: exit code 2"}
    base.update(kw)
    return base


def seed_attempts(dbp, n=2, **overrides):
    """Record n identical failure attempts exactly the way PR 5's hook
    writer will: canonicalise, then store."""
    key = None
    for _ in range(n):
        e = policy._as_event(raw_event())
        e.update(overrides)
        e["failure_key"] = e.get("failure_key") or \
            canon.failure_key(e)
        e["action_fingerprint"] = e.get("action_fingerprint") or \
            canon.action_fingerprint(e)
        _, status = store.record_event(e, db_path=dbp)
        assert status == "ok"
        key = e["failure_key"]
    return dbp, key


def test_first_failure_no_block(tmp_path):
    seed_attempts(tmp_path / "m.sqlite", n=1)
    out = policy.evaluate(raw_event(), db_path=tmp_path / "m.sqlite")
    assert out is None


def test_second_identical_failure_blocks(tmp_path):
    dbp, key = seed_attempts(tmp_path / "m.sqlite", n=2)
    out = policy.evaluate(raw_event(), db_path=dbp)
    assert out and out["action"] == "block_duplicate"
    assert out["duplicate"]["failure_key"] == key
    assert "duplicate guard" in out["digest"]
    assert out["duplicate"]["attempts"] == 2


def test_new_hypothesis_bypasses_block(tmp_path):
    dbp, _ = seed_attempts(tmp_path / "m.sqlite", n=3)
    out = policy.evaluate(raw_event(hypothesis="linker stale, rm -rf build"),
                          db_path=dbp)
    assert out is None
    # canonical-event form carries the same escape hatch
    ev = dict(policy._as_event(raw_event()), hypothesis="new approach")
    assert policy.evaluate(ev, db_path=dbp) is None


def test_store_down_fails_open(tmp_path):
    nowhere = tmp_path / "no" / "such" / "dir.sqlite"
    out = policy.evaluate(raw_event(), db_path=nowhere)
    assert out is None  # count unreadable (-1) → the Warden path stands


def test_digest_marker_compatible_with_turnstile(tmp_path):
    dbp, _ = seed_attempts(tmp_path / "m.sqlite", n=2)
    out = policy.evaluate(raw_event(), db_path=dbp)
    assert out["digest"].startswith(minder.DIGEST_MARKERS[1])
    # excerpt wording is noise (like warden float masking): same failure
    # shape + same action + no hypothesis → still a verbatim repeat
    noisy = raw_event(tool_response="process failed: exit code 2 "
                                     "(took 41.7s this time)")
    again = policy.evaluate(noisy, db_path=dbp)
    assert again and again["action"] == "block_duplicate"
    # a different action is a different fingerprint → ladder territory
    changed = raw_event(tool_input={"command": "make test"})
    assert policy.evaluate(changed, db_path=dbp) is None
    changed = raw_event(tool_input={"command": "make test"})
    assert policy.evaluate(changed, db_path=dbp) is None


def _run_hook(event, transport, tmp_path, dbp):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "MINDER_STATE_DIR": str(tmp_path / "state"),
        "MINDER_CONFIG": str(tmp_path / "minder.json"),
        "MINDER_CAPS": str(tmp_path / "caps.json"),
        "HOME": str(tmp_path),
        "MINDER_MEMORY_DB": str(dbp),
    }
    return subprocess.run(
        [sys.executable, HOOK, "--transport", transport],
        input=json.dumps(event), capture_output=True, text=True, env=env,
        timeout=SUBPROC_TIMEOUT)


def test_transports_unchanged_contract(tmp_path):
    dbp, _ = seed_attempts(tmp_path / "m.sqlite", n=3)
    zcode = _run_hook(raw_event(), "zcode", tmp_path, dbp)
    assert zcode.returncode == 0
    payload = json.loads(zcode.stdout)
    assert payload["decision"] == "block" and "duplicate guard" in \
        payload["reason"]
    dsh = _run_hook(raw_event(), "dsh", tmp_path, dbp)
    assert dsh.returncode == 2 and "duplicate guard" in dsh.stderr


def test_success_event_never_blocks(tmp_path):
    dbp, _ = seed_attempts(tmp_path / "m.sqlite", n=5)
    ok = raw_event(tool_response="all checks passed", tool_name="Bash")
    assert policy.evaluate(ok, db_path=dbp) is None


def test_warden_tests_isolation_warden_never_writes_store(tmp_path, monkeypatch):
    """The guard is read-only: minder.process() alone never creates the
    memory DB (that is PR 5's writer), so existing ladder tests are
    unaffected whether or not a store exists."""
    dbp = tmp_path / "never.sqlite"
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    ev = raw_event()
    for _ in range(3):
        out = minder.process(ev)
    assert not dbp.exists()
    assert out["action"] in (None, "think", "frontier", "alarm")
