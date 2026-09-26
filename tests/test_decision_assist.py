"""Decision-skill assist tests (Phase 6.5). MINDER_ASSIST=decision_skill
may inject ONE low-risk gateway-selected SKILL digest, only when every
gate passes. Default off; NullClient never attaches; the gateway can
never create a frontier consult."""

from minder_memory import (canonicalise as canon_mod, from_hook,
                    policy as memory_policy)

REPO = "/repo"


def hook_event():
    # ZeroDivisionError matches no shipped skill trigger, so any SKILL
    # section in the digest can only come from the gateway
    return {"session_id": "s-65", "hook_event_name": "PostToolUse",
            "tool_name": "Edit", "repo": REPO,
            "tool_input": {"file_path": "/repo/app/math.py"},
            "tool_response": "ZeroDivisionError: division by zero"}


def fake_spec(ev):
    fkey = canon_mod.failure_key(from_hook.to_event(ev), REPO)
    spec = {"failure_kind": "code_logic", "needs_new_evidence": 0.1,
            "next_step": "inspect", "confidence": 0.9,
            "any_skill_applies": 0.9, "best_skill": "diagnose-environment"}
    return fkey, {fkey: spec}


def install_client(monkeypatch, client):
    from minder_decision import client as dclient
    monkeypatch.setattr(dclient, "get_decision_client",
                        lambda: client, raising=False)


def seed_world(dbp, ev, n=2):
    for _ in range(n):
        from_hook.record(ev, db_path=dbp)


def shortlist_stub(monkeypatch, names):
    from minder_decision import skill_select as dskill
    monkeypatch.setattr(dskill, "build_skill_shortlist",
                        lambda event, index_path=None: tuple(names))


def warden():
    return {"action": "think", "level": 1, "digest": "[minder] warden"}


def test_all_gates_pass_attaches_one_low_risk_skill(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out["action"] == "block_duplicate"  # warden path untouched
    assert "SKILL: diagnose-environment" in out["digest"]  # gateway attach
    assert out["assist"] == "decision_skill"
    # exactly one gateway skill, compact body (first 10 lines max)
    assert out["digest"].count("SKILL:") == 1


def test_null_client_never_attaches(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import NullClient
    install_client(monkeypatch, NullClient())
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "SKILL:" not in (out or {}).get("digest", "")


def test_low_any_skill_applies_never_attaches(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    fixtures[fkey] = dict(fixtures[fkey], any_skill_applies=0.4)
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "SKILL:" not in (out or {}).get("digest", "")


def test_skill_outside_shortlist_menu_never_attaches(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    fixtures[fkey] = dict(fixtures[fkey], best_skill="rewrite-everything")
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "rewrite-everything" not in (out or {}).get("digest", "")
    assert "SKILL:" not in (out or {}).get("digest", "")


def test_no_attach_when_assist_off(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.delenv("MINDER_ASSIST", raising=False)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert out["action"] == "block_duplicate"  # guard still fires
    assert "SKILL: diagnose-environment" not in out["digest"]


def test_no_attach_without_decision_client(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    install_client(monkeypatch, None)  # MINDER_DECISION unset
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "SKILL: diagnose-environment" not in (out or {}).get("digest", "")


def test_gate_override_to_human_blocks_attach(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    # triage: permissions-like, low headroom -> environment_check at 0.7
    # falls under the 0.75 threshold -> human_if_permissions -> human
    fixtures[fkey] = dict(fixtures[fkey], failure_kind="permissions",
                          next_step="environment_check", confidence=0.7)
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("diagnose-environment",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "SKILL: diagnose-environment" not in (out or {}).get("digest", "")


def test_missing_body_blocks_attach(tmp_path, monkeypatch):
    memory_policy._DECISION_DEBOUNCE.clear()
    ev = hook_event()
    fkey, fixtures = fake_spec(ev)
    fixtures[fkey] = dict(fixtures[fkey], best_skill="ghost-skill")
    dbp = tmp_path / "m.sqlite"
    seed_world(dbp, ev)
    from minder_decision.client import FakeClient
    install_client(monkeypatch, FakeClient(fixtures))
    shortlist_stub(monkeypatch, ("ghost-skill",))  # in the live shortlist
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp)
    assert "SKILL: ghost-skill" not in (out or {}).get("digest", "")
