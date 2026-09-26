"""Reflex tier tests (advisory micro-classification client)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reflex


def ok_post(choice, conf):
    return lambda url, payload: (200, {"choice": choice, "confidence": conf})


def test_classify_disabled_by_default():
    assert reflex.classify("boom", {"reflex": {"enabled": False}}) == (None, 0.0)


def test_classify_via_injected_post():
    cfg = {"reflex": {"enabled": True,
                      "url": "http://x", "threshold": 0.85}}
    assert reflex.classify("AssertionError: wrong value", cfg,
                           post=ok_post("logic_bug", 0.93)) == \
        ("logic_bug", 0.93)
    # sidecar down → fail-open
    def dead(url, payload):
        raise RuntimeError("conn refused")
    assert reflex.classify("x", cfg, post=dead) == (None, 0.0)


def test_digest_hint_threshold_and_format():
    cfg = {"reflex": {"enabled": True, "url": "http://x"}}
    hint = reflex.digest_hint("IndexError at waterfall.py", cfg,
                              post=ok_post("logic_bug", 0.91))
    assert hint.startswith("[reflex 0.91] probable logic_bug")
    assert "assertion inputs" in hint
    # below threshold → no hint
    assert reflex.digest_hint("x", cfg, post=ok_post("flaky", 0.70)) == ""
    # unknown class → no hint
    assert reflex.digest_hint("x", cfg, post=ok_post("martian", 0.99)) == ""


def test_hook_digest_unaffected_when_sidecar_down(tmp_path, monkeypatch,
                                                  capsys):
    """Full hook path with reflex enabled and the sidecar dead: digest
    survives unchanged (fail-open). In-process (no subprocess spawn)."""
    import io
    import json
    import sys as _sys
    import minder
    import hook

    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    monkeypatch.setattr(minder, "CAPS_PATH", tmp_path / "caps.json")
    cfg = {"fail_threshold": 1, "cooldown_turns": 99,
           "reflex": {"enabled": True, "url": "http://127.0.0.1:1/x"}}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    event = json.dumps({"session_id": "rx", "hook_event_name": "PostToolUse",
                        "tool_name": "Edit",
                        "tool_input": {"file_path": "/x/a.py"},
                        "tool_response": "old_string not found"})
    monkeypatch.setattr(_sys, "stdin", io.StringIO(event))
    rc = hook.main(["--transport", "dsh"])  # threshold 1: escalates at once
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("[minder] ESCALATION L1")  # digest unchanged
    assert "[reflex" not in err  # dead sidecar -> no hint
