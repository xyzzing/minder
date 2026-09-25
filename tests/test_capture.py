"""Capture health: the check that would have caught three days of loss.

The unit under test compares hook invocations (dsh's own session logs)
with the records the watchdog actually persisted, per store, in a
deterministic window (`now=` injection, like the weekly summary).
"""
import json
import time

import dshseed
from minder_op import capture

NOW = 1_790_003_600.0


def _home(tmp_path, monkeypatch, hooks=3):
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    sid = "session-aaaa-bbbb"
    dshseed.write_session(root, sid, cwd="/home/dev/proj",
                          base_ms=int((NOW - 600) * 1000),
                          tool_calls=("bash",) * hooks, hook_ms=5500.0)
    dshseed.write_projection(root, sid, cwd="/home/dev/proj",
                             base_ms=int((NOW - 600) * 1000),
                             sandbox="workspace-write")
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    return root, sid


def test_no_sessions_means_not_measurable(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "absent"))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert report["coverage"]["invocations"] == 0
    assert report["coverage"]["ratio"] is None
    assert report["sandbox"] == {}
    # The sink warning is independent of dsh and always applies when unset.
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert any("SINK_URL" in w for w in report["warnings"])


def test_covering_hooks_with_no_records_is_reported(tmp_path, monkeypatch):
    """The exact production fault: hooks fire, nothing is persisted."""
    _home(tmp_path, monkeypatch, hooks=3)
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    cov = report["coverage"]
    assert cov["invocations"] == 3
    assert cov["persisted"] == 0
    assert cov["ratio"] == 0.0
    assert report["ok"] is False
    assert any("coverage" in w for w in report["warnings"])
    assert report["sandbox"] == {"workspace-write": 1}


def test_ledger_records_raise_coverage(tmp_path, monkeypatch):
    _root, sid = _home(tmp_path, monkeypatch, hooks=4)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    with (state / "events.jsonl").open("w") as fh:
        for _ in range(4):
            fh.write(json.dumps({"ts": NOW - 60, "task": sid,
                                 "event": "hook_timing"}) + "\n")
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert report["coverage"]["persisted_ledger"] == 4
    assert report["coverage"]["persisted_unattributed"] == 0
    assert report["coverage"]["ratio"] == 1.0
    assert not any("coverage" in w for w in report["warnings"])


def test_records_for_unknown_sessions_do_not_count_as_capture(
        tmp_path, monkeypatch):
    """A synthetic probe (or another harness) must not make a dead capture
    path look alive — but it is reported, never hidden."""
    _root, sid = _home(tmp_path, monkeypatch, hooks=4)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    with (state / "events.jsonl").open("w") as fh:
        for task in ("session-test", "session-probe", sid):
            fh.write(json.dumps({"ts": NOW - 60, "task": task,
                                 "event": "hook_timing"}) + "\n")
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert report["coverage"]["persisted_ledger"] == 1
    assert report["coverage"]["persisted_unattributed"] == 2
    assert report["coverage"]["ratio"] == 0.25


def test_records_outside_the_window_do_not_count(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, hooks=2)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    with (state / "events.jsonl").open("w") as fh:
        fh.write(json.dumps({"ts": NOW - 7200, "task": "t",
                             "event": "hook_timing"}) + "\n")
    report = capture.build(tmp_path / "m.sqlite", now=NOW, window_hours=1)
    assert report["coverage"]["persisted"] == 0
    assert report["coverage"]["ratio"] == 0.0


def test_store_freshness_statuses(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "absent"))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    (state / "hook-trace.jsonl").write_text("{}\n")
    import os
    os.utime(state / "hook-trace.jsonl", (NOW - 100, NOW - 100))
    stale = state / "consults.jsonl"
    stale.write_text("{}\n")
    os.utime(stale, (NOW - 7 * 86400, NOW - 7 * 86400))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    by_name = {s["name"]: s for s in report["stores"]}
    assert by_name["hook trace"]["status"] == "fresh"
    assert by_name["consult trail"]["status"] == "stale"
    assert by_name["memory DB events"]["status"] == "missing"
    assert "consult trail" in by_name["consult trail"]["name"]
    assert any("consult trail" in w for w in report["warnings"])


def test_slow_hooks_are_flagged(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, hooks=2)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert any("p50" in w for w in report["warnings"])
    assert report["coverage"]["sessions"][0]["hook_p50_ms"] == 5500.0


def test_report_never_raises_on_a_hostile_home(tmp_path, monkeypatch):
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True)
    (root / "sessions" / "not-a-dir").write_text("x")
    (root / "storages").mkdir()
    (root / "storages" / "workspace.json").write_text("{not json")
    (root / "storages" / "session_projcache").mkdir()
    (root / "storages" / "session_projcache" / "sessions").mkdir()
    (root / "storages" / "session_projcache" / "sessions"
     / "session-x.json").write_text("[]")
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert report["coverage"]["invocations"] == 0
    assert isinstance(report["warnings"], list)


def test_age_text_is_stable():
    assert capture.age_text(None) == "never"
    assert capture.age_text(10) == "10s"
    assert capture.age_text(3600 * 5) == "5h"
    assert capture.age_text(86400 * 3) == "3d"


def test_window_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "absent"))
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    report = capture.build(tmp_path / "m.sqlite", now=NOW, window_hours=6)
    assert report["window_hours"] == 6
    assert report["coverage"]["window_hours"] == 6


def test_now_injection_makes_it_deterministic(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, hooks=1)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    a = capture.build(tmp_path / "m.sqlite", now=NOW)
    b = capture.build(tmp_path / "m.sqlite", now=NOW)
    a.pop("now"), b.pop("now")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert time.time() > NOW  # the injected clock is in the past


def test_sink_is_discovered_from_hooks_json(tmp_path, monkeypatch):
    """A wired install must not read as "sink not configured" just because
    the observer's own environment lacks MINDER_SINK_URL."""
    _home(tmp_path, monkeypatch, hooks=1)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    hooks = tmp_path / "hooks.json"
    hooks.write_text('{"hooks": {"PostToolUse": [{"hooks": [{"command": '
                     '"MINDER_SINK_URL=http://127.0.0.1:1 python3 hook.py"'
                     '}]}]}}')
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert report["sink"]["configured"] is True
    assert report["sink"]["source"] == "hooks.json"
    assert report["sink"]["declared"] == "http://127.0.0.1:1"
    # declared but unreachable (closed port) -> the actionable warning
    assert any("unreachable" in w and "minder-sink" in w
               for w in report["warnings"])


def test_declared_but_never_called_names_the_restart(tmp_path, monkeypatch):
    """The live 2026-09-25 state after installing: the sink is up and
    reachable, and no hook reaches it because the dsh host loaded its hook
    command at startup."""
    _home(tmp_path, monkeypatch, hooks=2)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(capture, "_sink_report", lambda _state: {
        "configured": True, "url": "http://127.0.0.1:8392",
        "source": "hooks.json", "declared": "http://127.0.0.1:8392",
        "reachable": True, "stats": {"ops": {}}})
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert "no hook has ever called it" in report["warnings"][0]
    assert "restart it" in report["warnings"][0].lower()
    assert report["ok"] is False
    # A sink that has served some traffic still names the restart, with
    # wording that no longer claims it was never called at all.
    monkeypatch.setattr(capture, "_sink_report", lambda _state: {
        "configured": True, "url": "http://127.0.0.1:8392",
        "source": "hooks.json", "declared": "http://127.0.0.1:8392",
        "reachable": True,
        "stats": {"ops": {"append_jsonl": {"ok": 3, "failed": 0}}}})
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert "only a fraction" in report["warnings"][0]
    assert "restart it" in report["warnings"][0].lower()
    # With coverage at or above the floor there is nothing to warn about.
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    with (state / "events.jsonl").open("a") as fh:
        for _ in range(2):
            fh.write(json.dumps({"ts": NOW - 30, "task": "session-aaaa-bbbb",
                                 "event": "hook_timing"}) + "\n")
    monkeypatch.setattr(capture, "_sink_report", lambda _state: {
        "configured": True, "url": "http://127.0.0.1:8392",
        "source": "env", "declared": "http://127.0.0.1:8392",
        "reachable": True,
        "stats": {"ops": {"append_jsonl": {"ok": 9, "failed": 0}}}})
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    assert not any("restart it" in w.lower() for w in report["warnings"])


def test_flag_drift_between_hooks_and_sink_is_called_out(tmp_path,
                                                         monkeypatch):
    """The policy pass runs in the sink, so a flag the hook declares but the
    sink did not start with is silently inert — it must be named."""
    _home(tmp_path, monkeypatch, hooks=1)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    hooks = tmp_path / "hooks.json"
    hooks.write_text('{"hooks": {"PostToolUse": [{"hooks": [{"command": '
                     '"MINDER_ASSIST=decision_skill MINDER_DECISION=laya '
                     'MINDER_SINK_URL=http://127.0.0.1:1 hook.py"}]}]}}')
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    monkeypatch.setattr(capture, "_sink_report", lambda _state: {
        "configured": True, "url": "http://127.0.0.1:8392",
        "source": "hooks.json", "declared": "http://127.0.0.1:8392",
        "reachable": True,
        "stats": {"ops": {"append_jsonl": {"ok": 9, "failed": 0}},
                  "flags": {"MINDER_ASSIST": None,
                            "MINDER_DECISION": "laya"}}})
    report = capture.build(tmp_path / "m.sqlite", now=NOW)
    drift = next(w for w in report["warnings"] if "disagree" in w)
    assert "MINDER_ASSIST" in drift and "decision_skill" in drift
    assert "MINDER_DECISION" not in drift  # agrees, so not reported
    assert "minder-sink.service" in drift
    # The sink URL is the address, not a behaviour switch: hooks.json
    # carries it, the sink deliberately does not, and that is not drift.
    assert "MINDER_SINK_URL" not in drift
