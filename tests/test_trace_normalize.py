"""Trace normalization tests (Slice 1).

The mapping from DSH's on-disk event vocabulary to Minder's canonical
event shape is where every downstream feature gets its facts, so these
tests pin the awkward parts of the real format rather than a convenient
idealisation of it: `arguments` arrives as a JSON *string*, result text is
nested three levels deep, and a call may have no result at all.

`tests/fixtures/tracebuild.py` writes the same records DSH writes, so a
fixture session exercises the real reader (`minder_op.dsh_sessions`), the
real zstd handling and this mapping together.
"""
import json
import os

import pytest

from minder_trace import normalize
from tracebuild import build_session, call, result


@pytest.fixture(autouse=True)
def _fixture_dsh_home(tmp_path, monkeypatch):
    """Point the readers at a per-test DSH home and clear their module
    cache (5 s TTL, process-global), so one test's fixture session can
    never leak into the next and the machine's real ~/.dsh is never read."""
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "dsh"))
    from minder_op import dsh_sessions
    dsh_sessions._CACHE.clear()


# --- the raw mapping ------------------------------------------------------

def test_call_and_result_pair_into_one_event():
    records = [
        call(seq=1, call_id="c1", name="bash", turn=1, step=1,
             arguments={"command": "pytest -q"}),
        result(seq=2, call_id="c1", text="1 failed"),
    ]
    run, report = normalize.normalize(records, session_id="s1", repo="/repo")
    assert report["status"] == "ok"
    assert report["tool_calls"] == 1 and report["tool_results"] == 1
    assert report["paired"] == 1 and report["unpaired"] == 0
    assert len(run["events"]) == 1
    event = run["events"][0]
    assert event["tool"] == "bash"
    assert event["command"] == "pytest -q"
    assert event["paired"] is True
    assert event["ds_call_id"] == "c1" and event["ds_seq"] == 1
    assert event["failure_key"] and event["action_fingerprint"]


def test_arguments_json_string_is_parsed():
    """DSH stores `arguments` as a JSON string, not an object."""
    records = [call(seq=1, call_id="c1", name="read",
                    arguments=json.dumps({"file_path": "/repo/a.py"})),
               result(seq=2, call_id="c1", text="contents")]
    run, _ = normalize.normalize(records, session_id="s", repo="/repo")
    event = run["events"][0]
    assert event["file_path"] == "/repo/a.py"
    assert json.loads(event["args_json"])["file_path"] == "/repo/a.py"


def test_malformed_arguments_degrade_to_raw_string():
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments="{not json"),
               result(seq=2, call_id="c1", text="boom")]
    run, report = normalize.normalize(records, session_id="s", repo="/repo")
    assert report["status"] == "ok"
    assert run["events"][0]["tool"] == "bash"


def test_unpaired_call_is_kept_and_counted():
    """A truncated trace is evidence about the run, not a reason to drop
    the only record of what the agent attempted."""
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments={"command": "make"})]
    run, report = normalize.normalize(records, session_id="s", repo="/repo")
    assert len(run["events"]) == 1
    assert run["events"][0]["paired"] is False
    assert report["unpaired"] == 1
    assert any("no result" in n for n in report["notes"])


def test_result_without_a_call_is_kept():
    records = [result(seq=9, call_id="ghost", text="orphan output")]
    run, report = normalize.normalize(records, session_id="s", repo="/repo")
    assert len(run["events"]) == 1
    assert report["unpaired"] == 1


def test_failure_marker_and_exit_code_are_read():
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments={"command": "pytest"}),
               result(seq=2, call_id="c1",
                      text="FAILED test_x\n[exit code: 1]")]
    run, _ = normalize.normalize(records, session_id="s", repo="/repo")
    event = run["events"][0]
    assert event["status"] == "error"
    assert event["event_type"] == "tool_failure"
    assert event["exit_code"] == 1


def test_successful_command_has_no_exit_code_and_is_not_a_failure():
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments={"command": "echo hi"}),
               result(seq=2, call_id="c1", text="hi")]
    run, _ = normalize.normalize(records, session_id="s", repo="/repo")
    event = run["events"][0]
    assert event["status"] == "success"
    assert event["exit_code"] is None


def test_secrets_are_redacted_before_anything_can_store_them():
    secret = "sk-proj-operatorleak99999999"
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments={"command": f"curl -H 'key: {secret}'"}),
               result(seq=2, call_id="c1", text=f"used {secret}")]
    run, report = normalize.normalize(records, session_id="s", repo="/repo")
    blob = json.dumps(run, default=str)
    assert secret not in blob
    assert report["redaction_status"] == "redacted"


def test_run_id_is_deterministic_per_session():
    a = normalize.normalize([], session_id="session-x")[0]["run_id"]
    b = normalize.normalize([], session_id="session-x")[0]["run_id"]
    c = normalize.normalize([], session_id="session-y")[0]["run_id"]
    assert a == b and a != c


def test_cost_is_explicitly_null_and_explained():
    """dsh's usage ledger is per-day/per-model, so a per-run cost would be
    an invention. Say so instead of guessing from tokens."""
    run, _ = normalize.normalize([], session_id="s")
    assert run["execution"]["estimated_cost"] is None
    assert "not attributable" in run["execution"]["cost_note"]


def test_governance_signals_are_captured():
    records = [
        {"type": "plan/mode", "seq": 1, "time": 10, "data": {"active": True}},
        {"type": "plan/mode", "seq": 2, "time": 20,
         "data": {"active": False}},
        {"type": "approval/decided", "seq": 3, "time": 30,
         "data": {"id": "a1", "outcome": "allowed-once"}},
        {"type": "todo/write", "seq": 4, "time": 40,
         "data": {"todos": [{"content": "step one",
                             "status": "in_progress"}]}},
        {"type": "hook/result", "seq": 5, "time": 50,
         "data": {"point": "PostToolUse", "decision": "block",
                  "exitCode": 2}},
    ]
    run, report = normalize.normalize(records, session_id="s")
    assert report["plan_mode_transitions"] == 2
    assert report["approvals_decided"] == 1
    assert report["todo_writes"] == 1
    assert report["hook_blocks"] == 1
    kinds = [m["kind"] for m in run["timeline"]]
    assert kinds == ["plan_mode", "plan_mode", "approval", "todo"]
    assert run["governance"]["todo_state"]["signature"]


def test_unknown_event_types_are_reported_but_routine_ones_are_not():
    """The unrecognised-type note is the DSH-schema-change canary, so it
    must stay quiet for normal traffic and speak up for genuinely new
    events."""
    routine = [{"type": "assistant/message", "seq": 1, "time": 1,
                "data": {}},
               {"type": "request/header", "seq": 2, "time": 2, "data": {}}]
    _run, report = normalize.normalize(routine, session_id="s")
    assert report["unknown_types"] == 0

    novel = [{"type": "brand/new-event-type", "seq": 1, "time": 1,
              "data": {}}]
    _run, report = normalize.normalize(novel, session_id="s")
    assert report["unknown_types"] == 1
    assert any("brand/new-event-type" in n for n in report["notes"])


def test_normalize_never_raises_on_junk():
    for junk in ([], None, [None, 1, "x"], [{"type": "tool/call"}],
                 [{"type": "tool/result", "data": None}]):
        run, report = normalize.normalize(junk, session_id="s")
        assert report["status"].startswith("ok") or \
            report["status"].startswith("degraded")
        assert isinstance(run["events"], list)


def test_determinism_same_records_same_bytes():
    records = [call(seq=1, call_id="c1", name="bash",
                    arguments={"command": "a"}),
               result(seq=2, call_id="c1", text="out"),
               call(seq=3, call_id="c2", name="read",
                    arguments={"file_path": "/repo/b.py"}),
               result(seq=4, call_id="c2", text="body")]
    first = normalize.normalize(records, session_id="s", repo="/repo")[0]
    second = normalize.normalize(records, session_id="s", repo="/repo")[0]
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)


# --- the reader delegation (real zstd path) ------------------------------

def test_load_session_reads_a_real_zstd_session(tmp_path):
    """End-to-end through minder_op.dsh_sessions: a real .zstd log on disk
    is discovered, decompressed and normalized."""
    events = [
        {"type": "session", "seq": 0, "time": 1,
         "data": {"id": "session-abc", "cwd": "/repo"}},
        call(seq=1, call_id="c1", name="bash", arguments={"command": "ls"}),
        result(seq=2, call_id="c1", text="a.py"),
    ]
    path = build_session(tmp_path / "dsh", "session-abc", events)
    if path is None:
        pytest.skip("no zstd writer available")
    run, report = normalize.load_session("abc")
    assert report["status"] == "ok", report
    assert report["tool_calls"] == 1
    assert run["source"]["session_id"] == "session-abc"
    assert run["events"][0]["command"] == "ls"


def test_load_session_unknown_id_is_not_found_not_a_crash(tmp_path):
    build_session(tmp_path / "dsh", "session-abc", [
        {"type": "session", "seq": 0, "time": 1, "data": {"id": "x"}}])
    run, report = normalize.load_session("does-not-exist")
    assert report["status"] == "not_found"
    assert run["events"] == []


def test_load_session_never_writes_under_the_dsh_home(tmp_path):
    """Read-only on DSH is an invariant, not a preference."""
    root = tmp_path / "dsh"
    sessions = build_session(root, "session-abc", [
        {"type": "session", "seq": 0, "time": 1, "data": {"id": "x"}}])
    if sessions is None:
        pytest.skip("no zstd writer available")
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    normalize.load_session("abc")
    after = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert before == after


def test_load_session_picks_newest_when_no_id_given(tmp_path, monkeypatch):
    root = tmp_path / "dsh"
    old = build_session(root, "session-old", [
        {"type": "session", "seq": 0, "time": 1, "data": {"id": "old"}}])
    new = build_session(root, "session-new", [
        {"type": "session", "seq": 0, "time": 2, "data": {"id": "new"}}])
    if old is None or new is None:
        pytest.skip("no zstd writer available")
    # session_dirs() orders by the newest *log* mtime, so age the logs.
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    run, report = normalize.load_session()
    assert report["status"] == "ok"
    assert run["source"]["session_id"] == "session-new"
