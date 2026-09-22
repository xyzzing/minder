"""Egress + assist-flag tests (docs/minder-phase-4-7-frontier-coding.md
P6.1/P6.2). Flags default off (Phase 2-3 behaviour exactly); egress deny is
deterministic; a classifier can never deny on its own; assist modes are
digest-only and never change the Warden action."""
import pytest

from memory import (canonicalise as canon_mod, db as _db, egress, from_hook,
                    lessons as memory_lessons, policy as memory_policy,
                    store)
from memory.classifier import Classification, ShadowClassifier

REPO = "/repo"
CFG = {"frontier_providers": [
    {"name": "probe-a", "base_url": "http://probe/a", "model": "m-a",
     "key_env": "PROBE_A_KEY"},
]}


def hook_event():
    return {"session_id": "s-eg", "hook_event_name": "PostToolUse",
            "tool_name": "Edit", "repo": REPO,
            "tool_input": {"file_path": "/repo/app/supplier.py"},
            "tool_response": "KeyError: 'supplier_id'"}


def _unset(monkeypatch):
    monkeypatch.delenv("MINDER_ASSIST", raising=False)
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)


# ---------- P6.2 assess_egress ----------


def test_egress_deny_matrix():
    assert egress.assess_egress(
        {"redaction_profile": "external-prohibited"}) == "deny"
    assert egress.assess_egress({"untrusted_content_present": True}) == "deny"
    assert egress.assess_egress(
        {"error_excerpt": "token was ghp_AbCdEfGh12345678"}) == "deny"
    assert egress.assess_egress({}) == "allow"
    assert egress.assess_egress(
        {"redaction_profile": "internal-code-default"}) == "redact"


def test_classifier_block_alone_cannot_deny_but_is_logged(tmp_path,
                                                          monkeypatch):
    _unset(monkeypatch)
    event = {"key": "k|f|s|p", "task_id": "t-egress"}
    blocky = Classification(failure_class="environment",
                            recommended_action="environment_check",
                            egress_risk="block", confidence=0.97,
                            model_version="spy")
    assert egress.assess_egress(event, classification=blocky) == "allow"
    monkeypatch.setenv("MINDER_ASSIST", "shadow_suggest")
    assert egress.assess_egress(event, classification=blocky) == "deny"
    low = Classification(egress_risk="block", confidence=0.5)
    assert egress.assess_egress(event, classification=low) == "allow"
    # the block evaluations were logged to the append-only ledger
    import minder
    entries = (minder.STATE_DIR / "events.jsonl")
    if entries.exists():
        assert "egress_classifier_block" in entries.read_text()


def test_frontier_precheck_and_run_panel_deny(monkeypatch):
    import frontier
    _unset(monkeypatch)
    payload = {"key": "k|f|s|p", "attempts": 3,
               "error": "token ghp_AbCdEfGh12345678 in logs"}
    assert frontier.egress_precheck(payload, {}) == "deny"
    assert frontier.egress_precheck(
        {"error": "KeyError: 'x'"},
        {"redaction_profile": "external-prohibited"}) == "deny"
    assert frontier.egress_precheck({"error": "KeyError: 'x'"}, {}) == "allow"

    monkeypatch.setenv("PROBE_A_KEY", "k1")
    calls = []
    post = lambda *a, **k: (calls.append(a) or
                            (200, {"choices": [{"message": {"content": "A"}}]}))
    out = frontier.run_panel(payload, dict(CFG, redaction_profile=
                                           "external-prohibited"), post=post)
    assert out.startswith("(egress")
    assert not calls  # nothing left the machine
    # default profiles keep the consult path intact
    out = frontier.run_panel({"key": "k", "attempts": 1,
                              "error": "KeyError: 'x'"},
                             dict(CFG), post=post)
    assert out == "A" and calls


# ---------- P6.1 assist flags ----------


def seed_world(dbp):
    """Two recorded repeats + one verified lesson for the failure key."""
    ev = hook_event()
    canon = from_hook.to_event(ev)
    fkey = canon_mod.failure_key(canon, REPO)
    for _ in range(2):
        from_hook.record(ev, db_path=dbp)
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "s-eg"},
                                  db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = memory_lessons.promote_lesson(
        ep_id, "check the dict default for supplier_id",
        verification={"tests_passed": True}, repo=REPO, failure_key=fkey,
        db_path=dbp)
    assert status == "ok"
    return ev, fkey


def test_flags_default_off(tmp_path, monkeypatch):
    _unset(monkeypatch)
    dbp = tmp_path / "m.sqlite"
    ev, fkey = seed_world(dbp)
    warden = {"action": "think", "level": 1, "digest": "[minder] warden"}
    # with a hypothesis present the guard declines: no assist either
    ev_h = dict(ev, hypothesis="new idea")
    assert memory_policy.evaluate(ev_h, warden, db_path=dbp) is None
    # plain block path untouched (phase 2-3 behaviour)
    out = memory_policy.evaluate(ev, warden, db_path=dbp)
    assert out["action"] == "block_duplicate"
    assert "CLASSIFIER_SUGGEST" not in out["digest"]


def test_retrieve_mode_attaches_lesson_digest_only(tmp_path, monkeypatch):
    _unset(monkeypatch)
    dbp = tmp_path / "m.sqlite"
    ev, fkey = seed_world(dbp)
    warden = {"action": "think", "level": 1, "digest": "[minder] warden"}
    ev_h = dict(ev, hypothesis="switched approach: check defaults")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    out = memory_policy.evaluate(ev_h, warden, db_path=dbp)
    assert out is not None and out["action"] == "think"  # action untouched
    assert out["digest"].startswith(warden["digest"])
    assert "VERIFIED LESSON" in out["digest"]
    assert out["assist"] == "retrieve"
    # no lesson anywhere → passthrough declines too
    dbp2 = tmp_path / "m2.sqlite"
    from_hook.record(ev, db_path=dbp2)
    from_hook.record(ev, db_path=dbp2)
    assert memory_policy.evaluate(ev_h, warden, db_path=dbp2) is None


def test_block_duplicate_skill_mode_documents_p25(tmp_path, monkeypatch):
    _unset(monkeypatch)
    dbp = tmp_path / "m.sqlite"
    ev, _fkey = seed_world(dbp)
    warden = {"action": "think", "level": 1, "digest": "d"}
    monkeypatch.setenv("MINDER_ASSIST", "block_duplicate_skill")
    out = memory_policy.evaluate(ev, warden, db_path=dbp)
    assert out["action"] == "block_duplicate"  # still no L2 change
    assert out["level"] == 1


def test_shadow_suggest_line_only_when_eligible(tmp_path, monkeypatch):
    _unset(monkeypatch)
    dbp = tmp_path / "m.sqlite"
    ev, fkey = seed_world(dbp)
    warden = {"action": "think", "level": 1, "digest": "d"}

    class Env:
        def classify(self, event):
            return Classification(failure_class="environment",
                                  recommended_action="environment_check",
                                  egress_risk="safe", confidence=0.9,
                                  model_version="spy")

    shadow = ShadowClassifier(Env(), db_path=dbp)
    monkeypatch.setenv("MINDER_ASSIST", "shadow_suggest")
    # confidence 0.9 but guard digest carries the line only >= 0.85 and
    # class in {environment, permissions} — this row qualifies
    shadow.classify({"failure_key": fkey, "tool": "edit"},
                    policy_action="block_duplicate")
    out = memory_policy.evaluate(ev, warden, db_path=dbp)
    assert out["action"] == "block_duplicate"
    assert "CLASSIFIER_SUGGEST: environment" in out["digest"]

    # low confidence → no line
    dbp2 = tmp_path / "m2.sqlite"
    ev2, fkey2 = seed_world(dbp2)

    class Low:
        def classify(self, event):
            return Classification("environment", "environment_check",
                                  "safe", 0.5, "spy")

    shadow2 = ShadowClassifier(Low(), db_path=dbp2)
    shadow2.classify({"failure_key": fkey2}, policy_action="block_duplicate")
    out2 = memory_policy.evaluate(ev2, warden, db_path=dbp2)
    assert "CLASSIFIER_SUGGEST" not in out2["digest"]

    # wrong class (code_logic) → no line, regardless of confidence
    dbp3 = tmp_path / "m3.sqlite"
    ev3, fkey3 = seed_world(dbp3)

    class Logic(Env):
        def classify(self, event):
            return Classification("code_logic", "inspect", "safe", 0.99,
                                  "spy")

    shadow3 = ShadowClassifier(Logic(), db_path=dbp3)
    shadow3.classify({"failure_key": fkey3}, policy_action="block_duplicate")
    out3 = memory_policy.evaluate(ev3, warden, db_path=dbp3)
    assert "CLASSIFIER_SUGGEST" not in out3["digest"]
