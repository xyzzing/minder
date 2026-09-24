"""Deterministic single-match shortcut (Phase 6.5 fix). When exactly one
live skill trigger-matches the failure, the deterministic matcher IS the
answer: gate 3 (model confirms any_skill_applies) replaces gate 4 (model
choice confidence), which laya is miscalibrated on. Multi-match and
no-match cases keep the full gate 4 floor.

Note: the P2.5 _skill_section path (in _evaluate) also attaches a matching
skill to the guard digest independently of the gateway. The gateway is
verified by the assist="decision_skill" key, which only the gateway sets."""
import pytest

from memory import (canonicalise as canon_mod, from_hook,
                    policy as memory_policy)

REPO = "/repo"


def hook_event(excerpt="Error: pnpm: command not found (exit 127)"):
    return {"session_id": "s-66", "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO,
            "tool_input": {"command": "pnpm install"},
            "tool_response": excerpt}


def fkey_of(ev):
    return canon_mod.failure_key(from_hook.to_event(ev), REPO)


def install_client(monkeypatch, client):
    from decision import client as dclient
    monkeypatch.setattr(dclient, "get_decision_client",
                        lambda: client, raising=False)


def seed_world(dbp, ev, n=3):
    for _ in range(n):
        from_hook.record(ev, db_path=dbp)


def warden():
    return {"action": "think", "level": 1, "digest": "[minder] warden"}


def _spec(fkey, applies=0.9, confidence=0.9, best="diagnose-build-environment"):
    return {"failure_kind": "environment",
            "needs_new_evidence": 0.8,
            "next_step": "environment_check",
            "confidence": confidence,
            "any_skill_applies": applies,
            "best_skill": best}


def test_single_match_attaches_despite_low_choice_confidence(tmp_path,
                                                             monkeypatch):
    """One deterministic trigger match + model confirms applies, but the
    model's choice confidence is far below 0.70 (laya's miscalibration).
    The shortcut must still let the gateway attach the matched skill."""
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey = fkey_of(ev)
    # confidence=0.2 is far below the 0.70 gate-4 floor, but the
    # single-match shortcut replaces gate 4 with gate 3 (applies=0.9).
    fixtures = {fkey: _spec(fkey, applies=0.9, confidence=0.2)}
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out is not None
    assert out.get("assist") == "decision_skill"  # gateway fired
    assert "SKILL: diagnose-build-environment" in out["digest"]


def test_single_match_rejected_when_model_disagrees(tmp_path, monkeypatch):
    """One deterministic trigger match, but the model says NO skill
    applies (any_skill_applies below the 0.70 floor). Gate 3 still
    blocks the gateway."""
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey = fkey_of(ev)
    fixtures = {fkey: _spec(fkey, applies=0.3, confidence=0.9)}
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out is not None
    assert out.get("assist") != "decision_skill"  # gateway blocked


def test_multi_match_keeps_full_confidence_floor(tmp_path, monkeypatch):
    """Two deterministic trigger matches: the shortcut must NOT apply;
    the model's choice confidence below 0.70 blocks the gateway."""
    memory_policy._DECISION_DEBOUNCE.clear()
    # Matches both diagnose-build-environment (pnpm) and
    # diagnose-environment (ModuleNotFoundError) deterministically.
    ev = hook_event("ModuleNotFoundError: No module named 'pnpm' "
                    "(pnpm-lock.yaml)")
    fkey = fkey_of(ev)
    fixtures = {fkey: _spec(fkey, applies=0.9, confidence=0.2)}
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out is not None
    # multi-match -> gate 4 floor (0.2 < 0.70) -> gateway blocked
    assert out.get("assist") != "decision_skill"


def test_multi_match_attaches_when_confident(tmp_path, monkeypatch):
    """Two deterministic matches, model confident: the normal path
    (decide_skill) still lets the gateway attach."""
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event("ModuleNotFoundError: No module named 'pnpm' "
                    "(pnpm-lock.yaml)")
    fkey = fkey_of(ev)
    fixtures = {fkey: _spec(fkey, applies=0.9, confidence=0.9)}
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out is not None
    assert out.get("assist") == "decision_skill"
    assert "SKILL: diagnose-build-environment" in out["digest"]


def test_no_match_keeps_full_confidence_floor(tmp_path, monkeypatch):
    """No deterministic trigger match: the shortcut does not apply and
    the model's low choice confidence blocks the gateway."""
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event("ZeroDivisionError: division by zero")
    fkey = fkey_of(ev)
    fixtures = {fkey: {"failure_kind": "code_logic",
                       "needs_new_evidence": 0.1,
                       "next_step": "inspect",
                       "confidence": 0.2,
                       "any_skill_applies": 0.9,
                       "best_skill": "diagnose-environment"}}
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out is not None
    assert out.get("assist") != "decision_skill"
