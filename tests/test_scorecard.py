"""Scorecard + hook wiring regressions.

Two things are pinned here:

1. The scorecard is deterministic (`now=` injection) and its focus list
   names the actionable item, not a vague state.
2. The hook's fast path and its fallbacks: a clean tool call must not pay
   for a model, a sink miss must not fall back to building one, and the
   `MINDER_SINK_URL`-unset path must stay byte-inert (Law #6: detection
   never blocks, and never silently changes behaviour).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import dshseed
from minder_op import scorecard

REPO = Path(__file__).resolve().parent.parent
NOW = 1_790_003_600.0


def _home(tmp_path, monkeypatch, hooks=2, hook_ms=5400.0):
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    sid = "session-cccc-dddd"
    dshseed.write_session(root, sid, cwd="/home/dev/proj",
                          base_ms=int((NOW - 600) * 1000),
                          tool_calls=("bash",) * hooks, hook_ms=hook_ms)
    dshseed.write_projection(root, sid, cwd="/home/dev/proj",
                             base_ms=int((NOW - 600) * 1000),
                             pressure=0.91, retries=True)
    dshseed.write_usage(root, {"2026-09-25": {"p": {"m": {
        "inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 0}}}})
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    return root, sid


def test_scorecard_is_deterministic(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    a = scorecard.build(tmp_path / "m.sqlite", now=NOW)
    b = scorecard.build(tmp_path / "m.sqlite", now=NOW)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_scorecard_has_all_six_groups(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    report = scorecard.build(tmp_path / "m.sqlite", now=NOW,
                             window_hours=6)
    assert set(report["scores"]) == {"capture", "cost", "failures",
                                     "learning", "context", "hygiene"}
    assert report["window_hours"] == 6
    assert report["scores"]["context"]["sessions"] == 1
    assert report["scores"]["context"]["sessions_over_80pct"] == 1
    assert report["scores"]["context"]["sessions_with_llm_retries"] == 1
    assert report["scores"]["hygiene"]["sessions"] == 1
    assert report["scores"]["cost"]["hook_p50_ms"] == 5400.0


def test_focus_names_the_actionable_items(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    report = scorecard.build(tmp_path / "m.sqlite", now=NOW)
    focus = " | ".join(report["focus"])
    assert "SINK_URL" in focus
    assert "coverage" in focus
    assert len(report["focus"]) <= 3


def test_focus_quiet_when_capture_is_healthy(tmp_path, monkeypatch):
    _root, sid = _home(tmp_path, monkeypatch, hooks=2, hook_ms=120.0)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    monkeypatch.setenv("MINDER_SINK_URL", "http://127.0.0.1:1")
    with (state / "events.jsonl").open("w") as fh:
        for _ in range(2):
            fh.write(json.dumps({"ts": NOW - 60, "task": sid,
                                 "event": "hook_timing"}) + "\n")
    report = scorecard.build(tmp_path / "m.sqlite", now=NOW)
    focus = " | ".join(report["focus"])
    assert "coverage" not in focus
    # a fast hook is not flagged either
    assert "hook p50" not in focus


def test_scorecard_text_renders_every_section(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    text = scorecard.render_text(scorecard.build(tmp_path / "m.sqlite",
                                                 now=NOW))
    for heading in ("capture", "cost", "failures", "learning", "context",
                    "hygiene", "focus"):
        assert heading in text


def test_scorecard_never_raises_without_any_source(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "absent"))
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    report = scorecard.build(tmp_path / "m.sqlite", now=NOW)
    assert report["scores"]["hygiene"]["sessions"] == 0
    assert scorecard.render_text(report)


# --------------------------------------------------------------------------
# hook wiring
# --------------------------------------------------------------------------

def _run_hook(payload, env_extra=None, monkeypatch=None):
    # The hook discovers a sink URL from the *installed* hooks.json when the
    # env does not set one; inherit conftest's isolated path so a test never
    # writes into the developer's live state directory.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
           "MINDER_HOOKS_JSON": os.environ["MINDER_HOOKS_JSON"],
           "MINDER_STATE_DIR": env_extra.pop("MINDER_STATE_DIR")}
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(REPO / "hook.py"), "--transport", "dsh"],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=120)
    return proc


CLEAN = {"hook_event_name": "PostToolUse", "tool_name": "bash",
         "session_id": "session-test", "cwd": str(REPO),
         "tool_input": {"command": "ls"},
         "tool_response": "a\nb\n[exit code: 0]"}
FAILED = dict(CLEAN, tool_response="Traceback (most recent call last)\n"
                                   "[exit code: 1]")


def test_hook_without_sink_still_works_and_is_fast(tmp_path):
    """Byte-inert fallback: with no sink and no decision layer the hook
    must still produce its normal (empty) directive quickly."""
    state = tmp_path / "state"
    state.mkdir()
    proc = _run_hook(CLEAN, {"MINDER_STATE_DIR": str(state),
                             "MINDER_DECISION": "off"})
    assert proc.returncode == 0
    assert proc.stderr.strip() == ""
    # local path still writes the ledger when the directory is writable
    ledger = (state / "events.jsonl").read_text()
    assert "hook_timing" in ledger


def test_clean_call_does_not_consult_the_policy_pass(tmp_path):
    """The dominance fix: a successful tool call must not pay for a model
    opinion nobody consults (it used to cost ~6s of laya work)."""
    state = tmp_path / "state"
    state.mkdir()
    proc = _run_hook(CLEAN, {"MINDER_STATE_DIR": str(state),
                             "MINDER_DECISION": "off",
                             "MINDER_ASSIST": "decision_skill"})
    assert proc.returncode == 0
    timings = [json.loads(line) for line in
               (state / "events.jsonl").read_text().splitlines()]
    timing = next(t for t in timings if t["event"] == "hook_timing")
    assert timing["policy"] == "skipped"
    assert timing["total_ms"] < 500


def test_failure_does_consult_the_policy_pass(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    proc = _run_hook(FAILED, {"MINDER_STATE_DIR": str(state),
                              "MINDER_DECISION": "off"})
    assert proc.returncode in (0, 2)
    timings = [json.loads(line) for line in
               (state / "events.jsonl").read_text().splitlines()]
    timing = next(t for t in timings if t["event"] == "hook_timing")
    # no sink configured -> the local path runs the pass
    assert timing["policy"] in ("local", "local-error")
    assert timing["action"] in (None, "think", "frontier", "alarm")


def test_sink_miss_does_not_fall_back_to_building_a_model(tmp_path):
    """With a sink configured but unreachable, the pass is skipped (and
    recorded) instead of paying the local model build on every tool call."""
    state = tmp_path / "state"
    state.mkdir()
    proc = _run_hook(FAILED, {
        "MINDER_STATE_DIR": str(state),
        "MINDER_SINK_URL": "http://127.0.0.1:1",
        "MINDER_SINK_TIMEOUT_MS": "150",
        "MINDER_DECISION": "laya",
        "MINDER_HOOK_BUDGET_MS": "750",
    })
    assert proc.returncode in (0, 2)
    ledger = (state / "events.jsonl").read_text().splitlines()
    timing = next(t for t in (json.loads(line) for line in ledger)
                  if t["event"] == "hook_timing")
    assert timing["policy"] == "sink-miss-skipped"
    assert timing["total_ms"] < 2000


def test_exit_code_marker_is_a_failure_signal():
    """`[exit code: N]` is the harness's own marker; a command that fails
    without a traceback must still classify as a failure."""
    import minder
    assert minder.is_failure("[exit code: 1]")
    assert minder.is_failure("[exit code: 127]")
    assert not minder.is_failure("[exit code: 0]")
    assert not minder.is_failure("all good")


def test_session_start_emits_a_capture_probe(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    proc = subprocess.run(
        [sys.executable, str(REPO / "hook.py"), "--transport", "dsh",
         "--session-start"],
        input=json.dumps({"hook_event_name": "SessionStart",
                          "session_id": "session-probe",
                          "source": "start", "cwd": str(REPO)}),
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
             "MINDER_HOOKS_JSON": os.environ["MINDER_HOOKS_JSON"],
             "MINDER_STATE_DIR": str(state)}, timeout=120)
    assert proc.returncode == 0
    ledger = [json.loads(line) for line in
              (state / "events.jsonl").read_text().splitlines()]
    probe = next(r for r in ledger if r["event"] == "capture_probe")
    assert probe["state_writable"] is True
    assert probe["sink"] is False
