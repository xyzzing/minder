"""Room runtime tests (issue #45, PRD-minder-room-runtime M1):
the answer reserve, the room_outcome ledger, the budget-enforcement
probe and the doctor checks. AT-M4 through AT-M12."""
import json
import pathlib
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import proxy as proxy_mod
from minder_decision import room
from mock_upstream import MockUpstream


@pytest.fixture
def proxy_over_mock():
    def start(behavior="default"):
        mock = MockUpstream(behavior)
        mock.__enter__()
        proxy_mod.UPSTREAM = mock.url
        srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy_mod.Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return mock, srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start


def _stop(mock, srv):
    srv.shutdown()
    srv.server_close()
    mock.__exit__(None, None, None)


# ---- pure: answer reserve (AT-M4) -----------------------------------------

def test_reserve_clamps_to_cap_minus_reserve():
    req = {"max_tokens": 2048,
           "chat_template_kwargs": {"thinking_budget": 12000}}
    out, ev = room.reserve_budget(req)
    assert out["chat_template_kwargs"]["thinking_budget"] == 1536
    assert out["max_tokens"] == 2048  # never raised
    assert ev == {"event": "budget_clamped", "reason": "reserve",
                  "was": 12000, "now": 1536, "max_tokens": 2048}


def test_reserve_unmet_falls_back_to_half_cap():
    req = {"max_tokens": 300,
           "chat_template_kwargs": {"thinking_budget": 12000}}
    out, ev = room.reserve_budget(req)
    assert out["chat_template_kwargs"]["thinking_budget"] == 150
    assert ev["reason"] == "reserve_unmet" and ev["now"] == 150


def test_reserve_leaves_room_alone_when_it_fits():
    req = {"max_tokens": 8192,
           "chat_template_kwargs": {"thinking_budget": 1024}}
    out, ev = room.reserve_budget(req)
    assert out["chat_template_kwargs"]["thinking_budget"] == 1024
    assert ev is None


def test_reserve_ignores_requests_without_both_numbers():
    for req in ({"max_tokens": 2048},
                {"chat_template_kwargs": {"thinking_budget": 64}},
                {"max_tokens": "big",
                 "chat_template_kwargs": {"thinking_budget": 64}}):
        out, ev = room.reserve_budget(dict(req))
        assert out == req and ev is None


def test_reserve_honors_cfg_overrides_and_floor():
    req = {"max_tokens": 2048,
           "chat_template_kwargs": {"thinking_budget": 2048}}
    out, ev = room.reserve_budget(dict(req), {"answer_reserve_tokens": 128})
    assert out["chat_template_kwargs"]["thinking_budget"] == 1920
    assert ev["reason"] == "reserve"
    # a configured reserve below the floor clamps to the floor
    out2, ev2 = room.reserve_budget(
        {"max_tokens": 600, "chat_template_kwargs": {"thinking_budget": 600}},
        {"answer_reserve_tokens": 8})
    assert ev2["now"] == 600 - room.ROOM_RESERVE_FLOOR


# ---- pure: room outcome (AT-M6) -------------------------------------------

def _summary(**kw):
    base = {"finish_reason": "stop", "content_chars": 40,
            "tool_calls": False, "error": False, "stream": False}
    base.update(kw)
    return base


def test_outcome_rows():
    rows = [
        (_summary(error=True), "error"),
        (_summary(finish_reason=None), "unknown"),
        (_summary(finish_reason="length", content_chars=0), "starved"),
        (_summary(finish_reason="length", content_chars=0,
                  tool_calls=True), "truncated"),
        (_summary(finish_reason="length", content_chars=42), "truncated"),
        (_summary(), "fit"),
    ]
    for summary, want in rows:
        assert room.classify_outcome(summary)["outcome"] == want


def test_outcome_carries_room_and_usage_without_text():
    row = room.classify_outcome(
        _summary(reasoning_tokens=512, completion_tokens=640),
        {"effort": "low", "budget": 1024, "max_tokens": 4096})
    assert row["effort"] == "low" and row["budget"] == 1024
    assert row["budget_used"] == 0.5
    assert row["reasoning_tokens"] == 512 and row["completion_tokens"] == 640
    assert not any(isinstance(v, str) and len(v) > 12 for v in row.values())


# ---- probe (AT-M9) ----------------------------------------------------------

def test_probe_enforced_true_within_slack():
    from minder_decision import room_probe
    def post(body):
        return 200, {"choices": [{"message": {"content": ""}}],
                     "usage": {"completion_tokens_details":
                               {"reasoning_tokens": 61}}}
    frag = room_probe.probe_budget_enforcement(post)
    assert frag["thinking_budget_enforced"] is True
    assert frag["budget_probe"]["reasoning_tokens"] == 61
    assert frag["budget_probe"]["source"] == "usage"


def test_probe_false_when_budget_ignored():
    from minder_decision import room_probe
    def post(body):
        return 200, {"choices": [{"message": {"content": ""}}],
                     "usage": {"completion_tokens_details":
                               {"reasoning_tokens": 900}}}
    frag = room_probe.probe_budget_enforcement(post)
    assert frag["thinking_budget_enforced"] is False


def test_probe_estimates_from_reasoning_content_then_null():
    from minder_decision import room_probe
    def post(body):
        return 200, {"choices": [{"message": {
            "content": "", "reasoning_content": "x" * 400}}]}
    frag = room_probe.probe_budget_enforcement(post)
    assert frag["budget_probe"]["source"] == "reasoning_content"
    assert frag["budget_probe"]["reasoning_tokens"] == 100
    assert frag["thinking_budget_enforced"] is True
    # nothing measurable at all
    frag2 = room_probe.probe_budget_enforcement(
        lambda body: (200, {"choices": [{"message": {"content": ""}}]}))
    assert frag2["thinking_budget_enforced"] is None
    assert frag2["budget_probe"]["source"] == "none"


def test_probe_not_applicable_on_transport_error_or_status():
    from minder_decision import room_probe
    def boom(body):
        raise OSError("down")
    assert room_probe.probe_budget_enforcement(boom) is None
    assert room_probe.probe_budget_enforcement(
        lambda body: (500, {})) is None


# ---- proxy integration ------------------------------------------------------

def _state_dir(monkeypatch, tmp_path):
    """Reuse the token-usage test pattern: a fresh ledger per test."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(minder_state_target(), "STATE_DIR", state)
    return state / "events.jsonl"


def minder_state_target():
    import minder
    return minder


def _events(ledger):
    try:
        text = ledger.read_text().strip()
    except OSError:
        return []  # the row lands after the client sees the response
    return [json.loads(ln) for ln in text.splitlines() if ln]


def _wait(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _room_events(ledger):
    return [e for e in _events(ledger) if e.get("event") == "room_outcome"]


def test_atm5_band_ceiling_clamps_budget_and_logs(tmp_path, monkeypatch):
    """Forced expert band with a 2048 ceiling: the reserve clamps the
    upstream budget to 1536; the default bands leave room untouched
    with no event (AT-M5, unit composition of band + reserve)."""
    from minder_decision.difficulty import DEFAULT_BANDS
    monkeypatch.setattr(minder_state_target(), "STATE_DIR",
                        tmp_path / "state")
    band = dict(DEFAULT_BANDS["expert_or_ambiguous"],
                max_tokens=2048, label="expert_or_ambiguous")
    req = {"model": "qwen-auto", "max_tokens": 32768,
           "chat_template_kwargs": {},
           "messages": [{"role": "user", "content": "q"}]}
    proxy_mod._apply_band_opinion(
        ("expert_or_ambiguous", band), req, "session:atm5", {})
    out, ev = room.reserve_budget(req, {})
    assert out["chat_template_kwargs"]["thinking_budget"] == 1536
    assert out["max_tokens"] == 2048
    assert ev["reason"] == "reserve"
    # the defaults leave room without any event
    routine = {"max_tokens": 8192, "chat_template_kwargs":
               {"thinking_budget": DEFAULT_BANDS["routine"]["budget"]}}
    assert room.reserve_budget(routine, {})[1] is None


def test_atm7_starved_outcome_non_stream_and_sse_bytes(proxy_over_mock,
                                                       tmp_path,
                                                       monkeypatch):
    from test_proxy import write_caps
    for behavior, stream in (("starved", False), ("sse_starved", True)):
        mock, srv, url = proxy_over_mock(behavior)
        ledger = _state_dir(monkeypatch, tmp_path)
        try:
            write_caps("kwargs")
            body = {"model": "qwen-exec", "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}]}
            if stream:
                body["stream"] = True
            req = urllib.request.Request(
                url + "/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            raw = urllib.request.urlopen(req, timeout=10).read()
            if stream:
                # client contract: the relayed bytes still name length
                assert b"finish_reason" in raw
            assert _wait(lambda: _room_events(ledger)), behavior
            row = _room_events(ledger)[-1]
            assert row["outcome"] == "starved", behavior
            assert row["finish_reason"] == "length"
            assert row["content_chars"] == 0
            assert row["stream"] is stream
        finally:
            _stop(mock, srv)


def test_atm8_classifier_failure_never_blocks(proxy_over_mock, tmp_path,
                                              monkeypatch):
    from test_proxy import write_caps
    mock, srv, url = proxy_over_mock("default")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs")
        def boom(summary, applied=None):
            raise RuntimeError("classifier bug")
        monkeypatch.setattr(room, "classify_outcome", boom)
        req = urllib.request.Request(
            url + "/v1/chat/completions",
            data=json.dumps({"model": "qwen-exec", "max_tokens": 16,
                             "messages": [{"role": "user",
                                           "content": "hi"}]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        assert _wait(lambda: [e for e in _events(ledger)
                              if e.get("event") == "room_outcome_error"])
        time.sleep(0.2)
        assert _room_events(ledger) == []
    finally:
        _stop(mock, srv)


def test_atm10_budget_unenforced_logged_once_per_session(
        proxy_over_mock, tmp_path, monkeypatch):
    from test_proxy import write_caps, body_with_marker
    mock, srv, url = proxy_over_mock("default")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        # caps say budgets are accepted but NOT measured-enforced; the
        # escalation marker schedules a level budget (l1_budget), the
        # only default path that always sends thinking_budget
        caps = write_caps("kwargs")
        caps["thinking"]["thinking_budget_enforced"] = False
        (proxy_config_dir() / "model_caps.json").write_text(
            json.dumps(caps))
        # a distinct user text keeps this session's warn-once budget
        # untouched by test_proxy's escalation tests (shared module
        # state, same pytest process)
        body = json.dumps(body_with_marker(
            user="budget enforcement probe")).encode()
        for _ in range(3):  # same first message: same session
            req = urllib.request.Request(
                url + "/v1/chat/completions", data=body,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        def warned():
            return [e for e in _events(ledger)
                    if e.get("event") == "budget_unenforced"]
        _wait(lambda: warned())
        time.sleep(0.2)
        assert len(warned()) == 1
        # and the marker request still relayed fine
        assert _wait(lambda: _room_events(ledger))
    finally:
        _stop(mock, srv)


def proxy_config_dir():
    import proxy
    return proxy.CONFIG_DIR


# ---- doctor checks (AT-M11) -------------------------------------------------

def test_atm11_doctor_room_checks(tmp_path, monkeypatch):
    from minder_op import room_checks
    # 1. unenforced budget -> warn with a manual step
    cfg = {"difficulty_bands": {}, "l1_budget": 2048, "l2_budget": 10240}
    caps = {"thinking": {"thinking_budget_enforced": False}}
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg=cfg, caps=caps)}
    assert checks["room_budget_enforced"]["status"] == "warn"
    # enforced + default bands -> ok
    caps_ok = {"thinking": {"thinking_budget_enforced": True}}
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg=cfg, caps=caps_ok)}
    assert checks["room_budget_enforced"]["status"] == "ok"
    # 2. a band that breaks the reserve -> warn naming it
    breaking = {"difficulty_bands": {"expert_or_ambiguous":
                                     {"max_tokens": 2048}}}
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg=breaking, caps=caps_ok)}
    assert checks["room_band_reserve"]["status"] == "warn"
    assert "expert_or_ambiguous" in checks["room_band_reserve"]["detail"]
    # defaults pass the reserve check
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg={}, caps=caps_ok)}
    assert checks["room_band_reserve"]["status"] == "ok"
    # 3. starved share above 2% -> warn; no events -> n/a
    ledger = tmp_path / "events.jsonl"
    now = time.time()
    rows = [dict(r, ts=now - 60) for r in
            [{"event": "room_outcome", "outcome": "fit"}] * 100 +
            [{"event": "room_outcome", "outcome": "starved"}] * 3]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg={}, caps=caps_ok,
                                      events_ledger=ledger)}
    assert checks["room_starved_share"]["status"] == "warn"
    checks = {c["id"]: c for c in
              room_checks.room_checks(cfg={}, caps=caps_ok,
                                      events_ledger=tmp_path / "none.jsonl")}
    assert checks["room_starved_share"]["status"] in ("info", "na")


def test_atm12_coding_multiturn_reserve_stable_no_prefix_break(
        proxy_over_mock, tmp_path, monkeypatch):
    """Three coding-style turns with tool calls: the reserve is
    deterministic for a given cap (no flip mid-session) and the prefix
    audit does not grow (the reserve never rewrites kwargs between
    turns once clamped to a stable cap)."""
    from test_proxy import write_caps
    from test_proxy import msg_tool_call, msg_tool_result
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        convo = [{"role": "system", "content": "you are a coder"}]
        convo.append({"role": "user", "content": "run the tests"})
        convo.append(msg_tool_call("bash", "{\"cmd\": \"pytest -q\"}"))
        convo.append(msg_tool_result("3 passed"))
        seen = []
        for _turn in range(3):
            req = {"model": "qwen-auto", "max_tokens": 8192,
                   "messages": list(convo),
                   "chat_template_kwargs": {"enable_thinking": True,
                                            "thinking_budget": 12000}}
            out, ev = room.reserve_budget(req, {})
            seen.append((out["chat_template_kwargs"]["thinking_budget"],
                         out["max_tokens"], ev))
        budgets = {b for b, _cap, _ev in seen}
        assert budgets == {8192 - room.ANSWER_RESERVE_TOKENS}
        # the third turn is identical: deterministic, never re-clamped
        assert seen[2] == seen[0]
    finally:
        _stop(mock, srv)


# ---- room apply (AT-M13) ----------------------------------------------------

def _policy(tmp_path, **over):
    policy = {
        "schema": "roomwise.room-policy/v1",
        "generated_at": "2026-10-25T00:00:00Z",
        "source_report_sha256": "a" * 64,
        "model_fingerprint": {"model_id": "qwen3.8-flash-next"},
        "requires_approval": ["effort_off_for_auto"],
        "rules": [
            {"match": {"contract": "task-difficulty/v1", "label": "complex"},
             "room": {"reply_format": "work", "effort": "low",
                      "budget": 1024, "max_tokens": 4096},
             "evidence": {"families": ["finance/npv"], "passed": 21,
                          "n": 30, "repeats": 3}},
            {"match": {"contract": "task-difficulty/v1",
                       "label": "mechanical"},
             "room": {"reply_format": "json", "effort": "off",
                      "budget": None, "max_tokens": 2048},
             "evidence": {"families": ["finance/gst_invoice"],
                          "passed": 42, "n": 42, "repeats": 3}}],
        "label_map": {"finance/npv": "complex"},
    }
    policy.update(over)
    path = tmp_path / "room-policy.json"
    path.write_text(json.dumps(policy))
    return path


_CAPS = {"fingerprint": {"model_id": "qwen3.8-flash-next"}}


def test_atm13_room_apply_refusals_and_opt_in(tmp_path, monkeypatch):
    from minder_op import room_apply
    monkeypatch.setattr(minder_state_target(), "STATE_DIR",
                        tmp_path / "state")
    config = tmp_path / "minder.json"
    config.write_text(json.dumps({"difficulty_bands": {}}))

    def run(policy, **kw):
        return room_apply.apply_policy(
            str(policy), config_path=str(config), caps=_CAPS, **kw)

    # unknown schema major refused
    bad = _policy(tmp_path, schema="roomwise.room-policy/v2")
    code, msg = run(bad)
    assert code == 1 and "v2" in msg
    # fingerprint mismatch refused; override applies and logs
    code, msg = run(_policy(tmp_path, model_fingerprint={
        "model_id": "other-model"}))
    assert code == 1 and "mismatch" in msg
    code, msg = run(_policy(tmp_path, model_fingerprint={
        "model_id": "other-model"}), allow_mismatch=True)
    assert code == 0 and "difficulty_bands" in msg
    # effort-off rule skipped without the opt-in, applied with it
    code, msg = run(_policy(tmp_path))
    assert code == 0
    assert "effort_off_for_auto" in msg
    # nothing written: config untouched in every dry run above
    assert json.loads(config.read_text()) == {"difficulty_bands": {}}
    code, msg = run(_policy(tmp_path), opt_ins=("effort_off_for_auto",))
    assert "skipped" not in msg
    assert "mechanical" in msg and "off" in msg


def test_atm13_room_apply_reserve_refusal(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    starve = _policy(tmp_path, rules=[
        {"match": {"contract": "task-difficulty/v1",
                   "label": "expert_or_ambiguous"},
         "room": {"reply_format": "json", "effort": "xhigh",
                  "budget": 12000, "max_tokens": 2048}}])
    code, msg = room_apply.apply_policy(
        str(starve), config_path=str(config), caps=_CAPS)
    assert code == 1 and "expert_or_ambiguous" in msg
    assert json.loads(config.read_text()) == {}


def test_atm13_room_apply_write_verifies_and_logs(tmp_path, monkeypatch):
    from minder_op import room_apply
    state = tmp_path / "state"
    monkeypatch.setattr(minder_state_target(), "STATE_DIR", state)
    config = tmp_path / "minder.json"
    config.write_text(json.dumps({"profile": "finance",
                                  "difficulty_bands":
                                      {"routine": {"effort": "low"}}}))
    code, msg = room_apply.apply_policy(
        str(_policy(tmp_path)), write=True, config_path=str(config),
        caps=_CAPS)
    assert code == 0 and "backup" in msg
    written = json.loads(config.read_text())
    assert written["profile"] == "finance"  # other keys survive
    assert written["difficulty_bands"]["complex"] == \
        {"effort": "low", "budget": 1024, "max_tokens": 4096}
    assert "routine" not in written["difficulty_bands"]  # replaced set
    assert (tmp_path / "minder.json.bak-room").exists()
    events = [json.loads(ln) for ln in
              (state / "events.jsonl").read_text().splitlines() if ln]
    applied = [e for e in events if e.get("event") == "room_policy_applied"]
    assert len(applied) == 1
    assert applied[0]["source_report_sha256"] == "a" * 64
    assert applied[0]["rules"] == 1 and applied[0]["skipped"] == 1


def test_atm13_room_apply_reports_evidence_and_reply_format(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(_policy(tmp_path)), config_path=str(config), caps=_CAPS)
    assert code == 0
    assert "finance/npv" in msg          # evidence provenance printed
    assert "advisory" in msg             # reply_format=work reported


# ---- room.contract rules (issue #47) ----------------------------------------

def _template(**over):
    tpl = {"template_id": "finance-npv-working",
           "family": "finance",
           "status": "evidence_checked",
           "producer_profile": "qwen3-8b-local",
           "applicability": {"model_specific": True},
           "payload": {"system_hint": "Work inside <work></work>.",
                       "suffix_template": "Then answer with one JSON."},
           "est_tokens": 180}
    tpl.update(over)
    return tpl


def _contract_policy(tmp_path, sha=None, approvals=("effort_off_for_auto",
                                                   "prompt_contract_for_auto")):
    room = {"reply_format": "work", "effort": "high", "budget": 10240,
            "max_tokens": 32768,
            "contract": {"name": "finance-npv-working"}}
    if sha:
        room["contract"]["sha256"] = sha
    return _policy(tmp_path, requires_approval=list(approvals), rules=[
        {"match": {"contract": "task-difficulty/v1", "label": "complex"},
         "room": room,
         "evidence": {"families": ["finance/npv"], "passed": 21, "n": 30,
                      "repeats": 3}}])


def test_atm14_contract_rule_skipped_without_operator_opt_in(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(_contract_policy(tmp_path)), config_path=str(config),
        caps=_CAPS, templates={"finance-npv-working": _template()})
    assert code == 0
    assert "prompt_contract_for_auto" in msg and "skipped" in msg
    assert json.loads(config.read_text()) == {}  # dry run regardless


def test_atm14_contract_rule_skipped_when_policy_never_asks(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(_contract_policy(tmp_path, approvals=("effort_off_for_auto",))),
        config_path=str(config), caps=_CAPS,
        opt_ins=("prompt_contract_for_auto",),
        templates={"finance-npv-working": _template()})
    assert code == 0
    assert "requires_approval" in msg


def test_atm14_contract_rule_applies_name_and_digest_pin(tmp_path):
    from minder_decision.output_contracts import payload_digest
    from minder_op import room_apply
    tpl = _template()
    policy = _contract_policy(tmp_path, sha=payload_digest(tpl))
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(policy), config_path=str(config), caps=_CAPS, write=True,
        opt_ins=("prompt_contract_for_auto",),
        templates={"finance-npv-working": tpl})
    assert code == 0, msg
    band = json.loads(config.read_text())["difficulty_bands"]["complex"]
    assert band["contract"] == "finance-npv-working"
    assert band["contract_sha256"] == payload_digest(tpl)
    assert band["budget"] == 10240 and band["effort"] == "high"


def test_atm14_contract_rule_refuses_unknown_template(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(_contract_policy(tmp_path)), config_path=str(config),
        caps=_CAPS, opt_ins=("prompt_contract_for_auto",), templates={})
    assert code == 1 and "not in the local store" in msg
    assert json.loads(config.read_text()) == {}


def test_atm14_contract_rule_refuses_digest_mismatch(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    code, msg = room_apply.apply_policy(
        str(_contract_policy(tmp_path, sha="c" * 64)),
        config_path=str(config), caps=_CAPS,
        opt_ins=("prompt_contract_for_auto",),
        templates={"finance-npv-working": _template()})
    assert code == 1 and "digest mismatch" in msg
    assert json.loads(config.read_text()) == {}


def test_atm14_contract_rule_refused_when_room_cannot_fund_it(tmp_path):
    from minder_op import room_apply
    config = tmp_path / "minder.json"
    config.write_text("{}")
    starved = _contract_policy(tmp_path)
    policy = json.loads(starved.read_text())
    policy["rules"][0]["room"]["max_tokens"] = 4096  # 10240+512+180 > 4096
    starved.write_text(json.dumps(policy))
    code, msg = room_apply.apply_policy(
        str(starved), config_path=str(config), caps=_CAPS,
        opt_ins=("prompt_contract_for_auto",),
        templates={"finance-npv-working": _template()})
    assert code == 1 and "complex" in msg
    assert json.loads(config.read_text()) == {}


# ---- proxy contract injection (issue #47) -----------------------------------

def _events(ledger):
    try:
        return [json.loads(ln) for ln in
                pathlib.Path(ledger).read_text().splitlines() if ln]
    except OSError:
        return []


def test_proxy_contract_applied_and_audited(tmp_path, monkeypatch):
    import proxy as proxy_mod
    from minder_decision.output_contracts import payload_digest
    monkeypatch.setattr(minder_state_target(), "STATE_DIR", tmp_path)
    band = {"label": "complex", "effort": "high", "budget": 10240,
            "max_tokens": 32768, "guardrail": None,
            "contract": "finance-npv-working"}
    tpl = {"template_id": "finance-npv-working",
           "status": "evidence_checked", "producer_profile": "p",
           "applicability": {"model_specific": True},
           "payload": {"system_hint": "hint", "suffix_template": "sfx"},
           "est_tokens": 180}
    monkeypatch.setattr(proxy_mod, "_CONTRACTS",
                        {"finance-npv-working": tpl})
    req = {"messages": [{"role": "user", "content": "q"}], "max_tokens": 32768}
    cfg = {"prompt_contracts": True}
    proxy_mod._apply_band_opinion(("complex", band), req, "sess-c", cfg)
    assert req["messages"][-1]["content"].endswith("sfx")
    events = _events(tmp_path / "events.jsonl")
    applied = [e for e in events if e.get("event") == "contract_applied"]
    assert len(applied) == 1
    assert applied[0]["contract"] == "finance-npv-working"
    assert applied[0]["est_tokens"] == 180
    # digest pin honored: a wrong pin refuses the whole injection
    band["contract_sha256"] = payload_digest(
        {**tpl, "payload": {**tpl["payload"], "suffix_template": "other"}})
    req2 = {"messages": [{"role": "user", "content": "q"}]}
    proxy_mod._apply_band_opinion(("complex", band), req2, "sess-d", cfg)
    assert req2["messages"][-1]["content"] == "q"
    skips = [e for e in _events(tmp_path / "events.jsonl")
             if e.get("event") == "contract_skipped"]
    assert skips and skips[-1]["why"] == "digest-mismatch"


def test_proxy_contract_opt_in_gate_and_streaming_skip(tmp_path, monkeypatch):
    import proxy as proxy_mod
    monkeypatch.setattr(minder_state_target(), "STATE_DIR", tmp_path)
    band = {"label": "complex", "contract": "finance-npv-working"}
    tpl = {"template_id": "finance-npv-working",
           "status": "evidence_checked", "producer_profile": "p",
           "applicability": {"model_specific": True},
           "payload": {"system_hint": "", "suffix_template": "sfx"},
           "est_tokens": 180}
    monkeypatch.setattr(proxy_mod, "_CONTRACTS",
                        {"finance-npv-working": tpl})
    # opt-in off: skipped, untouched
    req = {"messages": [{"role": "user", "content": "q"}]}
    proxy_mod._apply_band_opinion(("complex", band), req, "sess-a", {})
    assert req["messages"][-1]["content"] == "q"
    skips = [e for e in _events(tmp_path / "events.jsonl")
             if e.get("event") == "contract_skipped"]
    assert skips[-1]["why"] == "no-opt-in"
    # streaming request: skipped even with opt-in
    req = {"messages": [{"role": "user", "content": "q"}], "stream": True}
    proxy_mod._apply_band_opinion(("complex", band), req, "sess-b",
                                  {"prompt_contracts": True})
    assert req["messages"][-1]["content"] == "q"
    skips = [e for e in _events(tmp_path / "events.jsonl")
             if e.get("event") == "contract_skipped"]
    assert skips[-1]["why"] == "streaming"


def test_proxy_contract_silent_when_band_schedules_none(tmp_path, monkeypatch):
    import proxy as proxy_mod
    monkeypatch.setattr(minder_state_target(), "STATE_DIR", tmp_path)
    band = {"label": "routine", "effort": "medium", "budget": 2048,
            "max_tokens": 8192, "guardrail": None, "contract": None}
    req = {"messages": [{"role": "user", "content": "q"}]}
    applied = proxy_mod._apply_band_opinion(("routine", band), req, "sess-e",
                                            {"prompt_contracts": True})
    assert applied[0] == "medium"
    assert req["messages"][-1]["content"] == "q"
    assert not [e for e in _events(tmp_path / "events.jsonl")
                if e.get("event", "").startswith("contract_")]


# ---- contract template doctor check (issue #47) -----------------------------

def test_atm15_doctor_reports_contract_template_problems():
    from minder_op import room_checks
    checks = {c["id"]: c for c in room_checks.room_checks(
        caps={}, contract_problems=["npv.json: est_tokens must be a "
                                    "non-negative integer"])}
    assert checks["contract_templates"]["status"] == "warn"
    assert "npv.json" in checks["contract_templates"]["detail"]


def test_atm15_doctor_counts_loaded_contract_templates():
    from minder_op import room_checks
    checks = {c["id"]: c for c in room_checks.room_checks(
        caps={}, contracts_loaded=3)}
    assert checks["contract_templates"]["status"] == "ok"
    assert "3" in checks["contract_templates"]["detail"]
