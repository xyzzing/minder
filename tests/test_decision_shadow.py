"""MINDER_DECISION=shadow glue tests (Phase 5.5-G): the decision loop
runs after Warden + memory policy, writes a DecisionTrace, and changes
NOTHING — digest and action are byte-identical to the unset run.
Debounce limits it to one call per (session, failure_key) per 5s."""
from minder_memory import db as _db, from_hook, policy as memory_policy

HOOK_EVENT = {"session_id": "s-decision", "hook_event_name": "PostToolUse",
              "tool_name": "Edit",
              "tool_input": {"file_path": "/repo/app/supplier.py"},
              "tool_response": "KeyError: 'supplier_id'"}
WARDEN = {"action": "think", "level": 1, "digest": "[minder] warden thinks"}


def seed(dbp):
    for _ in range(2):
        from_hook.record(HOOK_EVENT, db_path=dbp)


def rows(dbp):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM decision_traces ORDER BY ts").fetchall()]
    finally:
        conn.close()


def test_shadow_writes_trace_and_changes_nothing(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    dbp = tmp_path / "m.sqlite"
    seed(dbp)
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    out_without = memory_policy.evaluate(HOOK_EVENT, WARDEN, db_path=dbp)
    assert rows(dbp) == []  # default off: no traces, no calls

    monkeypatch.setenv("MINDER_DECISION", "shadow")
    out_with = memory_policy.evaluate(HOOK_EVENT, WARDEN, db_path=dbp)
    assert out_with == out_without  # bitwise-equivalent directive
    assert out_without["action"] == "block_duplicate"

    traces = rows(dbp)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["contract_id"] == "failure-triage"
    assert trace["provider"] == "null" and trace["confidence"] == 0.0
    assert trace["failure_key"] == out_without["duplicate"]["failure_key"]
    # model recommendation and policy decision stored separately
    assert "model_recommendation" in trace and "policy_decision" in trace


def test_debounce_one_call_per_session_key_per_window(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    dbp = tmp_path / "m.sqlite"
    seed(dbp)
    monkeypatch.setenv("MINDER_DECISION", "shadow")
    memory_policy.evaluate(HOOK_EVENT, WARDEN, db_path=dbp)
    memory_policy.evaluate(HOOK_EVENT, WARDEN, db_path=dbp)  # same key, 0s
    assert len(rows(dbp)) == 1  # debounced (process dict)
    other = dict(HOOK_EVENT, session_id="s-decision-2")
    from_hook.record(other, db_path=dbp)
    from_hook.record(other, db_path=dbp)
    memory_policy.evaluate(other, WARDEN, db_path=dbp)  # new session -> fires
    assert len(rows(dbp)) == 2
    # the window is DB-backed too (hook.py is a fresh process per event):
    # a same-session trace older than the window lets the call fire again
    monkeypatch.setattr(memory_policy, "_DECISION_DEBOUNCE", {})
    monkeypatch.setattr(memory_policy, "_ts_age_seconds", lambda ts: 999)
    memory_policy.evaluate(HOOK_EVENT, WARDEN, db_path=dbp)
    assert len(rows(dbp)) == 3


def test_l3_event_traced_as_human_only(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    dbp = tmp_path / "m.sqlite"
    seed(dbp)
    alarm = {"action": "alarm", "level": 3, "digest": "L3"}
    monkeypatch.setenv("MINDER_DECISION", "shadow")
    memory_policy.evaluate(HOOK_EVENT, alarm, db_path=dbp)
    trace = rows(dbp)[0]
    assert trace["policy_decision"] == "human"
    assert trace["override"] == "warden_l3"
