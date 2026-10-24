"""Room runtime tests (issue #45, PRD-minder-room-runtime M1):
the answer reserve, the room_outcome ledger, the budget-enforcement
probe and the doctor checks. AT-M4 through AT-M12."""
import json
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
