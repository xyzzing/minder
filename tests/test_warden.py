"""Warden core acceptance tests (AT-1..AT-6, AT-13 per prd.md §8 intent)."""
import json

import minder


def fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    monkeypatch.setattr(minder, "CAPS_PATH", tmp_path / "caps.json")


def ev(tool, resp, sid="s1", args=None):
    return {"session_id": sid, "hook_event_name": "PostToolUse",
            "tool_name": tool, "tool_input": args or {},
            "tool_response": resp}


CFG = {"fail_threshold": 2, "think_budget": 2, "frontier_budget": 1,
       "cooldown_turns": 2, "backfire_window": 2, "backfire_trip": 3}


def test_at1_structural_failure_detection(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    assert minder.is_failure("Error: old_string not found in file")
    assert minder.is_failure("bash: thing: command not found\nexit code 127")
    assert minder.is_failure("Traceback (most recent call last):")
    assert not minder.is_failure("edited 3 lines successfully")
    assert not minder.is_failure("42")


def test_at2_threshold_and_l1_digest(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    out1 = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/a.py"}), CFG)
    assert out1["action"] is None
    out2 = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/a.py"}), CFG)
    assert out2["action"] == "think"
    assert out2["level"] == 1
    assert out2["digest"].startswith("[minder] ESCALATION L1")
    assert "/x/a.py" in out2["digest"]


def test_at4_deescalation_on_success(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    for _ in range(2):
        minder.process(ev("Bash", "exit code 1", args={"command": "pytest"}), CFG)
    out = minder.process(ev("Bash", "all tests passed", args={
        "command": "pytest"}), CFG)
    assert out["action"] is None
    st = minder.load_state("s1")
    assert st["failures"] == {}


def test_at3_ladder_budgets_l2_then_l3(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    acts = []
    resp = "exit code 1"
    for _ in range(12):  # keep the same key failing past all budgets
        acts.append(minder.process(ev("Bash", resp, args={
            "command": "make test"}), CFG)["action"])
    assert "think" in acts and "frontier" in acts and "alarm" in acts
    # L3 fires exactly once
    st = minder.load_state("s1")
    assert st["l3_fired"] is True
    assert acts.count("alarm") == 1


def test_at5_cooldown_between_escalations(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    c = dict(CFG, cooldown_turns=3)
    a1 = minder.process(ev("Edit", "no match found", args={
        "file_path": "/x/b.py"}), c)
    a2 = minder.process(ev("Edit", "no match found", args={
        "file_path": "/x/b.py"}), c)
    assert a1["action"] is None and a2["action"] == "think"
    # cooldown blocks immediate further escalation even though n keeps growing
    a3 = minder.process(ev("Edit", "no match found", args={
        "file_path": "/x/b.py"}), c)
    assert a3["action"] is None


def test_at6_backfire_breaker_demotes_then_trips(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    c = dict(CFG, fail_threshold=2, cooldown_turns=2, backfire_window=3,
             backfire_trip=2)
    # t2: L1 fires; t3/t4: immediate repeat failures => backfire x2 => trip
    minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/c.py"}), c)
    esc = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/c.py"}), c)
    assert esc["action"] == "think"
    for _ in range(2):
        out = minder.process(ev("Edit", "old_string not found", args={
            "file_path": "/x/c.py"}), c)
        assert out["action"] is None
    st = minder.load_state("s1")
    assert st["breaker"]["edit:/x/c.py"]["tripped"] is True
    # while tripped: even cooled crossings emit nothing
    for _ in range(3):
        suppressed = minder.process(ev("Edit", "old_string not found", args={
            "file_path": "/x/c.py"}), c)
        assert suppressed["action"] is None
    # after the trip window passes the breaker resets and escalation resumes;
    # this key already used its L1, so the ladder correctly advances to L2
    resumed = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/c.py"}), c)
    assert resumed["action"] == "frontier"
    st = minder.load_state("s1")
    assert st["breaker"]["edit:/x/c.py"]["tripped"] is False


def test_breaker_survives_success_then_demotes_l1(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    c = dict(CFG, cooldown_turns=1, backfire_window=3, backfire_trip=9)
    minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/d.py"}), c)
    minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/d.py"}), c)          # L1
    minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/d.py"}), c)          # backfire 1
    minder.process(ev("Edit", "ok", args={"file_path": "/x/d.py"}), c)  # success
    out = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/d.py"}), c)
    out = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/d.py"}), c)
    assert out["action"] == "think"
    assert "repeat pattern broken" in out["digest"]  # minimal template


def test_degraded_l1_uses_verbatim_template(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    caps = {"thinking": {"mechanism": "none"}}
    (tmp_path / "caps.json").write_text(json.dumps(caps))
    minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/e.py"}), CFG)
    out = minder.process(ev("Edit", "old_string not found", args={
        "file_path": "/x/e.py"}), CFG)
    assert out["action"] == "think"
    assert "cannot toggle reasoning server-side" in out["digest"]


def test_session_start_caps_snapshot(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    (tmp_path / "caps.json").write_text(json.dumps(
        {"thinking": {"mechanism": "kwargs"}}))
    minder.snapshot_caps("sess-9")
    st = minder.load_state("sess-9")
    assert st["caps_mechanism"] == "kwargs"


def test_at13_ledger_privacy(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    secret = "api_key=SKIS-TOPSECRET-xyz old_string not found"
    minder.process(ev("Bash", secret, args={"command": "curl evil"}), CFG)
    minder.process(ev("Bash", secret, args={"command": "curl evil"}), CFG)
    minder.log("proxy", "cap_result", mechanism="kwargs",
               model_id="qwen27b-fusion")
    lines = [json.loads(ln) for ln in
             (tmp_path / "state" / "events.jsonl").read_text().splitlines()]
    for line in lines:
        blob = json.dumps(line)
        assert "TOPSECRET" not in blob
    cap = [e for e in lines if e["event"] == "cap_result"]
    assert cap and cap[0]["model_id"] == "qwen27b-fusion"
    assert "props_path" not in cap[0] and "model_path" not in cap[0]


def test_escalation_uses_session_caps_not_live(tmp_path, monkeypatch):
    """caps_mechanism snapshot at session start governs template choice (§5.3)."""
    fresh_state(tmp_path, monkeypatch)
    (tmp_path / "caps.json").write_text(json.dumps(
        {"thinking": {"mechanism": "softswitch"}}))
    minder.snapshot_caps("s2")
    # caps file changes mid-session to "none" — session must stay softswitch
    (tmp_path / "caps.json").write_text(json.dumps(
        {"thinking": {"mechanism": "none"}}))
    minder.process(ev("Edit", "old_string not found",
                      sid="s2", args={"file_path": "/x/f.py"}), CFG)
    out = minder.process(ev("Edit", "old_string not found",
                            sid="s2", args={"file_path": "/x/f.py"}), CFG)
    assert out["action"] == "think"
    assert "cannot toggle reasoning" not in out["digest"]


# ---------------------------------------------------------------------------
# 2026-09-20 field-run fixes: key granularity, signature gate, budget refunds
# ---------------------------------------------------------------------------

def test_tool_key_carries_inspection_target():
    assert minder.tool_key("read", {"file_path": "/x/a.py"}) == "read:/x/a.py"
    assert minder.tool_key("grep", {"pattern": "foo.*bar"}) == "grep:foo.*bar"
    assert minder.tool_key("web_fetch", {"url": "https://x/y"}) == \
        "web_fetch:https://x/y"
    assert minder.tool_key("read", {}) == "read:generic"


def test_err_hash_ignores_timing_noise():
    a = minder.err_digest_hash("failed in 0.42s [ 3%]")
    b = minder.err_digest_hash("failed in 17.9s [ 3%]")
    assert a == b
    assert minder.err_digest_hash("exit code 1") != minder.err_digest_hash(
        "exit code 2")


def test_changed_error_signature_resets(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    a1 = minder.process(ev("Bash", "exit code 1: foo",
                           args={"command": "make"}), CFG)
    a2 = minder.process(ev("Bash", "exit code 1: DIFFERENT bug",
                           args={"command": "make"}), CFG)
    assert a1["action"] is None and a2["action"] is None  # progress, no L1
    a3 = minder.process(ev("Bash", "exit code 1: DIFFERENT bug",
                           args={"command": "make"}), CFG)
    assert a3["action"] == "think"  # same error twice → escalate


def test_budget_refund_on_deescalate(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    for _ in range(2):
        minder.process(ev("Bash", "exit code 1",
                          args={"command": "make"}), CFG)
    assert minder.load_state("s1")["think_used"] == 1
    minder.process(ev("Bash", "all good", args={"command": "make"}), CFG)
    st = minder.load_state("s1")
    assert st["think_used"] == 0 and st["failures"] == {}
    # a later key gets its own ladder — early noise can no longer starve it
    out = None
    for _ in range(2):
        out = minder.process(ev("Bash", "exit code 1",
                                args={"command": "other"}), CFG)
    assert out["action"] == "think"


def test_frontier_budget_refunded_too(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    resp = "exit code 1"
    actions = [minder.process(ev("Bash", resp, args={
        "command": "make"}), CFG)["action"] for _ in range(5)]
    assert "think" in actions and "frontier" in actions
    st = minder.load_state("s1")
    assert st["think_used"] == 1 and st["frontier_used"] == 1
    minder.process(ev("Bash", "fixed", args={"command": "make"}), CFG)
    st = minder.load_state("s1")
    assert st["think_used"] == 0 and st["frontier_used"] == 0


def test_verify_payload_on_frontier_deescalate(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    out = None
    for _ in range(5):
        out = minder.process(ev("Bash", "exit code 1",
                                args={"command": "make"}), CFG)
    assert out["action"] == "frontier"
    out = minder.process(ev("Bash", "fixed", args={"command": "make"}), CFG)
    assert out["verify_payload"]["kind"] == "verify"
    assert out["verify_payload"]["key"] == "cmd:make"
    assert "exit code 1" in out["verify_payload"]["error"]


def test_profile_fail_signs_overlay(tmp_path, monkeypatch):
    fresh_state(tmp_path, monkeypatch)
    (tmp_path / "minder.json").write_text(json.dumps({
        "profile": "trading",
        "profiles": {"trading": {"fail_signs_extra": ["margin call"]}}}))
    assert minder.is_failure("position rejected: margin call",
                             minder.cfg().get("fail_signs_extra"))
    assert minder.is_failure("position rejected: margin call") is False


def test_audit_chain_ledger(tmp_path, monkeypatch):
    import hashlib
    fresh_state(tmp_path, monkeypatch)
    (tmp_path / "minder.json").write_text(json.dumps({"audit_chain": True}))
    minder.log("t", "e1", k=1)
    minder.log("t", "e2", k=2)
    lines = (tmp_path / "state" / "events.jsonl").read_text().splitlines()
    r1, r2 = (json.loads(ln) for ln in lines)
    expect = hashlib.sha256(
        (r1["chain"] + json.dumps({"ts": r2["ts"], "task": "t",
                                   "event": "e2", "k": 2},
                                  sort_keys=True)).encode()).hexdigest()
    assert r2["chain"] == expect


def test_compact_brief_is_capped(tmp_path, monkeypatch):
    """The brief rides additionalContext into the next model request; a
    session with hundreds of failure keys must not crowd out the context
    the summarizer just kept."""
    fresh_state(tmp_path, monkeypatch)
    st = {"turn": 500, "caps_mechanism": "kwargs", "think_used": 2,
          "frontier_used": 1, "last_esc": -10_000, "failures": {},
          "breaker": {}, "l3_fired": False}
    for i in range(250):
        st["failures"][f"cmd:loop-{i}"] = {
            "n": 3, "h": "x", "level": 1, "esc_turn": 1}
    minder.save_state("s-cap", st)
    brief = minder.compact_brief("s-cap")
    assert len(brief) <= minder.BRIEF_MAX_CHARS
    assert "more failure keys" in brief
    assert "loop-249" not in brief.split("more failure keys")[0]
