"""Sessionless payloads must never share one bucket (the `default` merge).

The regression: minder.py/policy.py fell back to `session_id or "default"`,
so every payload without a session id shared one state file — failure
counters, escalation budgets and breaker memory merged across unrelated
sessions. session_key() derives an action-scoped fallback instead: same
action still accumulates into one countable loop, different actions stay
isolated.
"""
import minder


def test_present_session_id_is_returned_untouched():
    ev = {"session_id": "  session-be2cae94-11e2-4b52  "}
    assert minder.session_key(ev) == "session-be2cae94-11e2-4b52"


def test_sessionless_keys_are_scoped_by_action():
    a = {"tool_name": "Bash", "tool_input": {"command": "curl bad"},
         "hook_event_name": "PostToolUse"}
    b = {"tool_name": "Bash", "tool_input": {"command": "pytest -q"},
         "hook_event_name": "PostToolUse"}
    ka, kb = minder.session_key(a), minder.session_key(b)
    assert ka.startswith("sessionless-") and kb.startswith("sessionless-")
    assert ka != kb


def test_sessionless_repeats_share_one_key_so_loops_still_count():
    ev = {"tool_name": "Edit",
          "tool_input": {"file_path": "/x/a.py"},
          "hook_event_name": "PostToolUse"}
    assert minder.session_key(ev) == minder.session_key(dict(ev))


def test_canonical_shape_is_supported():
    """from_hook passes the canonical event (tool/args_json, no tool_input);
    it must not collapse to one key for all tools."""
    a = {"tool": "Bash", "args_json": "{\"command\": \"curl bad\"}"}
    b = {"tool": "Edit", "args_json": "{\"file_path\": \"/x/a.py\"}"}
    assert minder.session_key(a) != minder.session_key(b)


def test_warden_escalates_a_sessionless_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    monkeypatch.setattr(minder, "CAPS_PATH", tmp_path / "caps.json")
    cfg = {"fail_threshold": 2, "think_budget": 2, "frontier_budget": 1,
           "cooldown_turns": 2, "backfire_window": 2, "backfire_trip": 3}
    ev = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
          "tool_input": {"file_path": "/x/a.py"},
          "tool_response": "old_string not found"}
    assert minder.process(ev, cfg)["action"] is None
    assert minder.process(ev, cfg)["action"] == "think"
    # …while a different sessionless action keeps its own count at zero.
    other = dict(ev, tool_input={"file_path": "/x/b.py"})
    assert minder.process(other, cfg)["action"] is None
    # two isolated state files, and no shared `default` bucket file.
    assert len(list((tmp_path / "state").glob("*.json"))) == 2
    assert not minder._state_path("default").exists()
