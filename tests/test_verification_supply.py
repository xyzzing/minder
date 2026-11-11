"""Supply pins from the verification-feed diagnosis (minder#80, the
mews supply wave, 2026-11-07): three measured breaks, each proven red
on landed main by the diagnosis's own pins (/tmp/minderdiag/pins) and
re-pinned here against the repair.

1. the close race: an unrelated success closes the episode `candidate`
   before the session's clean test run lands; the late upgrade
   re-closes it `verified` inside a bounded window.
2. unittest's success block states no passing count - "Ran N tests" +
   OK with no failing count is positive evidence.
3. timeout-wrapped runs are runs (GNU timeout consumes the duration).
"""
import json

from minder_core import verification
from minder_memory import db as _db, from_hook, store

REPO = "."
FAIL_EVENT = {"session_id": "s-supply", "hook_event_name": "PostToolUse",
              "tool_name": "Bash", "repo": REPO,
              "tool_input": {"command": "python -c get_setting"},
              "tool_response": "KeyError: 'missing_key'"}


def test_verification_lands_after_an_intervening_success(tmp_path):
    """Break three, red on landed main: the clean run lands after an
    unrelated success already closed the episode `candidate`; the late
    upgrade records the verification and re-closes verified."""
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    norm = from_hook.to_event(FAIL_EVENT)
    ep_id, _ = store.open_episode(dict(norm, failure_key="bash|keyerror|"
                                       "missing_key|none"), db_path=dbp)
    # the unrelated success: no runner, no verification payload - it
    # closes the episode candidate
    out1 = from_hook.record(dict(FAIL_EVENT, tool_response="ok, done",
                                 session_id="s-supply"), db_path=dbp)
    assert (out1.get("closed") or "") == "candidate"
    # the session's clean test run, minutes later
    out2 = from_hook.record(dict(FAIL_EVENT, tool_input={
        "command": "python3 -m pytest tests/test_task.py -q"},
        tool_response="1 passed in 0.2s", session_id="s-supply"),
        db_path=dbp)
    assert out2.get("closed") == "verified", (
        f"the late upgrade did not fire: {out2}")
    assert out2.get("upgraded_from") == "candidate"
    conn = _db.connect(dbp)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT event_type, payload_json FROM events"
            " WHERE event_type = 'verification'")]
        status = conn.execute("SELECT status FROM episodes"
                              " WHERE episode_id = ?",
                              (ep_id,)).fetchone()["status"]
    finally:
        conn.close()
    assert status == "verified"
    assert rows, "no verification event written by the upgrade"


def test_unittest_success_block_is_clean():
    """Break two (measured: 141 post-gap unittest-runner calls, 148/148
    rejected on 10-24): unittest states no passing count."""
    assert verification.run_is_clean("Ran 12 tests in 0.412s\n\nOK\n", "unittest")
    assert not verification.run_is_clean("Ran 12 tests in 0.412s\n\nFAILED (failures=2)\n",
                            "unittest")


def test_timeout_wrapped_pytest_is_a_run():
    """Break two: GNU timeout consumes the duration before the runner."""
    assert verification.test_runner(
        "cd /x && timeout 240 python3 -m pytest tests -q"
        " 2>&1 | tail -15") == "pytest"


def test_consult_without_episode_ledgers_the_refusal(tmp_path, monkeypatch):
    """A consult recorded without an episode link can never be adopted
    (17 of the first 19 traces) - record time says so in the ledger."""
    import minder
    from minder_memory import frontier_traces
    seen = []
    monkeypatch.setattr(minder, "log",
                        lambda task, event, **kw: seen.append((task,
                                                              event, kw)))
    tid = frontier_traces.record_consult(
        {"failure_key": "probe", "prompt": "p", "response": "r"},
        db_path=tmp_path / "m.sqlite")
    assert tid
    assert any(event == "consult_unlinked" for _t, event, _kw in seen), seen
