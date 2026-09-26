"""Turnstile proxy tests (AT-7a/b/c, AT-8, AT-9/10 fail-open, AT-16, AT-17)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import proxy
from mock_upstream import MockUpstream

CONFIG_DIR = proxy.CONFIG_DIR


def write_caps(mechanism, budget=True, effort=True, channel="ctk",
               effort_levels=None):
    caps = {"fingerprint": {"model_id": "qwen27b-fusion"}, "kwargs_accepted":
            mechanism == "kwargs",
            "thinking": {"mechanism": mechanism, "markers":
                         ["<think>", "</think>"],
                         "thinking_budget_supported": budget,
                         "field": "reasoning_content"},
            "softswitch_tokens": {"on": "/think", "off": "/no_think"},
            "effort_supported": effort, "effort_channel": channel,
            "effort_levels": effort_levels or ["low", "medium", "high"]}
    (CONFIG_DIR / "model_caps.json").write_text(json.dumps(caps))
    return caps


def clear_caps():
    f = CONFIG_DIR / "model_caps.json"
    if f.exists():
        f.unlink()


@pytest.fixture
def proxy_over_mock():
    def start(behavior="default"):
        mock = MockUpstream(behavior)
        mock.__enter__()
        proxy.UPSTREAM = mock.url
        srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return mock, srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start


def stop(mock, srv):
    srv.shutdown()
    srv.server_close()
    mock.__exit__(None, None, None)


def chat_request(url, body, headers=None):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(
        url + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read())


def body_with_marker(user="fix the parser"):
    """The digest lands on an assistant message (block reason transcript)."""
    return {"model": "qwen-exec", "messages": [
        {"role": "user", "content": user},
        {"role": "assistant", "content": "[minder] ESCALATION L1 — think "
         "before retrying.\n- FAILED 2x: edit:/x/a.py"}]}


def last_user(body):
    return next(m for m in reversed(body["messages"])
                if m["role"] == "user")


def msg_tool_call(name, args="{}"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def msg_tool_result(content):
    return {"role": "tool", "content": content}


def test_at8a_kwargs_exec_vs_think(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        st, resp = chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": "What is 2+2?"}]})
        body = mock.requests[-1]
        assert body["temperature"] == 0.15 and body["top_k"] == 20
        assert body["max_tokens"] == 8192
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

        st, resp = chat_request(url, body_with_marker())
        body = mock.requests[-1]
        # escalation upgrades to think preset atomically (AT-8)
        assert body["temperature"] == 0.6 and body["max_tokens"] == 32768
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "thinking_budget": 16384}
        assert not last_user(body)["content"].rstrip().endswith("/think")
    finally:
        stop(mock, srv)


def test_at8b_softswitch_never_both(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("softswitch")
        chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": "What is 2+2? /think"}]})
        body = mock.requests[-1]
        assert "chat_template_kwargs" not in body  # never both (Law #3)
        assert last_user(body)["content"].endswith("/no_think")

        chat_request(url, body_with_marker("fix the parser /no_think"))
        body = mock.requests[-1]
        assert "chat_template_kwargs" not in body
        # prior token stripped, on-token appended (AT-7b)
        assert last_user(body)["content"] == "fix the parser /think"
    finally:
        stop(mock, srv)


def test_at7c_none_mechanism_params_untouched_ledger(proxy_over_mock,
                                                     tmp_path):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("none")
        events = tmp_path / "events.jsonl"
        import minder
        monkey_target = minder.STATE_DIR
        minder.STATE_DIR = tmp_path
        # §6.8.2: escalated request under mechanism=none → think params still
        # applied, adapter no-ops (no kwargs), ledger l1_degraded.
        st, resp = chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": body_with_marker()["messages"][-1]
             ["content"]}]})
        body = mock.requests[-1]
        assert body["temperature"] == 0.6            # think params applied
        assert body["max_tokens"] == 32768
        assert "chat_template_kwargs" not in body    # adapter no-ops
        ledger = events.read_text()
        assert "l1_degraded" in ledger
        # plain exec request: exec params, no degradation noise
        chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": "What is 2+2?"}]})
        body = mock.requests[-1]
        assert body["temperature"] == 0.15
        assert "chat_template_kwargs" not in body
        minder.STATE_DIR = monkey_target
    finally:
        stop(mock, srv)


def test_caps_missing_forwards_without_mechanism(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        clear_caps()
        chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": "hi"}]})
        body = mock.requests[-1]
        assert body["temperature"] == 0.15
        assert "chat_template_kwargs" not in body
    finally:
        stop(mock, srv)


def test_at16_upstream_model_rewrite(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        monkeypatch.setitem(proxy.PRESETS["qwen-exec"], "upstream_model",
                            "qwen27b-fusion")
        chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": "hi"}]})
        assert mock.requests[-1]["model"] == "qwen27b-fusion"
    finally:
        stop(mock, srv)


def test_at17_models_synthesis(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        with urllib.request.urlopen(url + "/v1/models", timeout=10) as r:
            payload = json.loads(r.read())
        ids = {m["id"] for m in payload["data"]}
        assert {"qwen-exec", "qwen-think", "frontier",
                "qwen27b-fusion"} <= ids
        owned = {m["id"]: m.get("owned_by") for m in payload["data"]}
        assert owned["qwen-exec"] == "minder"
        assert owned["qwen27b-fusion"] != "minder"
    finally:
        stop(mock, srv)


def test_frontier_alias_never_forwarded(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        req = urllib.request.Request(
            url + "/v1/chat/completions",
            data=json.dumps({"model": "frontier", "messages": []}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            raised = False
        except urllib.error.HTTPError as e:
            raised = True
            assert e.code == 422
            payload = json.loads(e.read())
            assert payload["error"]["code"] == "frontier_not_forwardable"
        assert raised
        assert mock.requests == []  # nothing reached upstream
    finally:
        stop(mock, srv)


def test_unknown_model_passes_untouched(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        chat_request(url, {"model": "some-custom-model", "temperature": 0.9,
                           "messages": [{"role": "user",
                                         "content": "body_with_marker()"}]})
        body = mock.requests[-1]
        assert body["temperature"] == 0.9
        assert "chat_template_kwargs" not in body
    finally:
        stop(mock, srv)


def test_fail_open_upstream_down(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        monkeypatch.setattr(proxy, "UPSTREAM", "http://127.0.0.1:1")
        req = urllib.request.Request(
            url + "/v1/chat/completions",
            data=json.dumps({"model": "qwen-exec", "messages": []}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
            assert b"minder_upstream_unavailable" in e.read()
        assert code == 502
    finally:
        stop(mock, srv)


def test_get_passthrough(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        with urllib.request.urlopen(url + "/props", timeout=10) as r:
            payload = json.loads(r.read())
        assert payload["path"] == "/props"  # mock echoes path
    finally:
        stop(mock, srv)


def test_sse_relay_byte_exact(proxy_over_mock):
    mock, srv, url = proxy_over_mock("sse")
    try:
        body = json.dumps({"model": "qwen-exec", "stream": True,
                           "messages": []}).encode()
        req = urllib.request.Request(url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            via_proxy = r.read()
        req = urllib.request.Request(mock.url + "/v1/chat/completions",
                                     data=body,
                                     headers={"Content-Type":
                                              "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            direct = r.read()
        assert via_proxy == direct
        assert b"data: [DONE]" in via_proxy
    finally:
        stop(mock, srv)


def test_scan_gate_skips_huge_bodies(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        huge = "x" * (proxy.SCAN_GATE_BYTES + 1024)
        chat_request(url, {"model": "qwen-exec", "messages": [
            {"role": "user", "content": huge}]})
        body = mock.requests[-1]
        assert body["messages"][-1]["content"] == huge  # intact
        assert "temperature" not in body                # pipeline skipped
        assert "chat_template_kwargs" not in body
    finally:
        stop(mock, srv)


def test_auto_pipeline_effort_modes(proxy_over_mock, tmp_path, monkeypatch):
    import minder
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        cfg_file = tmp_path / "minder.json"
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(minder, "CFG_PATH", cfg_file)
        monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")

        # effort_mode off (default): auto behaves like plain exec
        cfg_file.write_text(json.dumps({"effort_mode": "off"}))
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [msg_tool_call("bash"),
                                        msg_tool_result("x" * 5000)]})
        body = mock.requests[-1]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_effort" not in json.dumps(body)

        # effort_mode auto: heavy tool activity → high + thinking on
        cfg_file.write_text(json.dumps({"effort_mode": "auto"}))
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [msg_tool_call("bash"),
                                        msg_tool_result("x" * 5000)]})
        body = mock.requests[-1]
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "high"}

        # plain question, no tool activity → off
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"}]})
        body = mock.requests[-1]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

        # escalation digest overrides everything → high + Standard budget
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"},
                                        {"role": "user", "content":
                                         "[minder] ESCALATION L1 — retry"}]})
        body = mock.requests[-1]
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "high",
            "thinking_budget": 2048}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "auto_effort" in ledger
    finally:
        stop(mock, srv)


def test_auto_pipeline_effort_unsupported(proxy_over_mock, tmp_path,
                                          monkeypatch):
    import minder
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs", effort=False)
        cfg_file = tmp_path / "minder.json"
        cfg_file.write_text(json.dumps({"effort_mode": "auto"}))
        monkeypatch.setattr(minder, "CFG_PATH", cfg_file)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [msg_tool_call("bash"),
                                        msg_tool_result("x" * 5000)]})
        body = mock.requests[-1]
        # effort unsupported → binary fallback, heavy activity still thinks
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert "reasoning_effort" not in json.dumps(body)
    finally:
        stop(mock, srv)


# ---------------------------------------------------------------------------
# escalation observability: digest marker in window ⇒ ledger line + upgrade
# ---------------------------------------------------------------------------

def test_escalation_marker_logged_and_upgrades(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        events = []
        monkeypatch.setattr(proxy.minder, "log",
                            lambda task, ev, **kw: events.append((task, ev, kw)))
        write_caps("kwargs")
        body = {"model": "qwen-exec", "max_tokens": 16, "messages": [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "[minder] ESCALATION L1 — think"},
            {"role": "user", "content": "retry now"}]}
        st, resp = chat_request(url, body)
        assert st == 200
        hits = [e for _, e, _ in events if e == "escalation_upgraded"]
        assert hits, events
    finally:
        clear_caps()
        stop(mock, srv)


def test_no_marker_no_escalation_log(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        events = []
        monkeypatch.setattr(proxy.minder, "log",
                            lambda task, ev, **kw: events.append((task, ev, kw)))
        write_caps("kwargs")
        body = {"model": "qwen-exec", "max_tokens": 16, "messages": [
            {"role": "user", "content": "plain turn"}]}
        st, _ = chat_request(url, body)
        assert st == 200
        assert not [e for _, e, _ in events if e == "escalation_upgraded"]
    finally:
        clear_caps()
        stop(mock, srv)


def test_boot_reverify_writes_fresh_caps(proxy_over_mock, monkeypatch, tmp_path):
    """B2: refresh_caps_in_background re-measures against the live upstream
    and rewrites caps when mechanism or model changed (2026-09-19 blind spot)."""
    import time
    mock, srv, url = proxy_over_mock("default")
    try:
        monkeypatch.setattr(proxy, "UPSTREAM", mock.url)
        monkeypatch.setattr(proxy, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(proxy.minder, "log", lambda *a, **k: None)
        stale = {"fingerprint": {"model_id": "old-model"},
                 "thinking": {"mechanism": "none"}}
        (tmp_path / "model_caps.json").write_text(json.dumps(stale))
        proxy.refresh_caps_in_background()
        deadline = time.time() + 15
        while time.time() < deadline:
            fresh = json.loads((tmp_path / "model_caps.json").read_text())
            if fresh["fingerprint"]["model_id"] != "old-model":
                break
            time.sleep(0.2)
        assert fresh["fingerprint"]["model_id"] != "old-model"
        assert fresh["thinking"]["mechanism"] in ("kwargs", "softswitch")
    finally:
        stop(mock, srv)


# ---------------------------------------------------------------------------
# G2: consequence modes (PRD v2 DIRECT/LEAN/DEEP) + thermal clamp
# ---------------------------------------------------------------------------

def test_mode_direct_disables_thinking(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        st, _ = chat_request(url, {"model": "qwen-think", "max_tokens": 16,
                                   "messages": [{"role": "user",
                                                 "content": "hi"}]},
                             headers={"X-Minder-Mode": "direct"})
        assert st == 200
        body = mock.requests[-1]
        assert body["chat_template_kwargs"]["enable_thinking"] is False
    finally:
        clear_caps()
        stop(mock, srv)


def test_mode_deep_sets_budget(proxy_over_mock):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        st, _ = chat_request(url, {"model": "qwen-exec", "max_tokens": 16,
                                   "messages": [{"role": "user",
                                                 "content": "hi"}]},
                             headers={"X-Minder-Mode": "deep"})
        assert st == 200
        ctk = mock.requests[-1]["chat_template_kwargs"]
        assert ctk["enable_thinking"] is True
        assert ctk["thinking_budget"] == 4096
    finally:
        clear_caps()
        stop(mock, srv)


def test_thermal_downgrade_deep_to_lean(proxy_over_mock, monkeypatch, tmp_path):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        fake = tmp_path / "instance.json"
        fake.write_text(json.dumps(
            {"telemetry": {"pacing_active": True}}))
        monkeypatch.setattr(proxy, "SINTER_STATE", str(fake))
        proxy._pacing_cache.update(mtime=None, active=False)
        events = []
        monkeypatch.setattr(proxy.minder, "log",
                            lambda task, ev, **kw: events.append(ev))
        st, _ = chat_request(url, {"model": "qwen-exec", "max_tokens": 16,
                                   "messages": [{"role": "user",
                                                 "content": "hi"}]},
                             headers={"X-Minder-Mode": "deep"})
        assert st == 200
        assert "thermal_downgrade" in events
        ctk = mock.requests[-1]["chat_template_kwargs"]
        assert ctk["thinking_budget"] == 1024  # lean budget after downgrade
    finally:
        clear_caps()
        stop(mock, srv)


def test_mode_overrides_auto_classifier(proxy_over_mock, monkeypatch, tmp_path):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        cfg_file = tmp_path / "minder.json"
        (tmp_path / "state").mkdir(parents=True, exist_ok=True)
        import minder
        monkeypatch.setattr(minder, "CFG_PATH", cfg_file)
        monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
        cfg_file.write_text(json.dumps({"effort_mode": "auto"}))
        heavy = [{"role": "user", "content": "fix"},
                 {"role": "assistant", "content": "", "tool_calls": [
                     {"id": "c", "type": "function",
                      "function": {"name": "bash",
                                   "arguments": "{\"command\": \"make\"}"}}]},
                 {"role": "tool", "tool_call_id": "c", "content": "x" * 5000},
                 {"role": "user", "content": "go"}]
        st, _ = chat_request(url, {"model": "qwen-auto", "max_tokens": 16,
                                   "messages": heavy},
                             headers={"X-Minder-Mode": "direct"})
        assert st == 200
        # heavy activity would say high; mode direct wins
        assert mock.requests[-1]["chat_template_kwargs"][
            "enable_thinking"] is False
    finally:
        clear_caps()
        stop(mock, srv)


# ---------------------------------------------------------------------------
# v0.5 — token accounting + client effort precedence
# ---------------------------------------------------------------------------

def _raw_stream(url, body, headers=None):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(
        url + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _state_dir(monkeypatch, tmp_path):
    import minder
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    return tmp_path / "state" / "events.jsonl"


def _wait_ledger(ledger, needle="token_usage", timeout=5.0):
    """The usage event is written in the handler's finally — after the
    client sees the full response. Poll briefly instead of racing it."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ledger.exists() and needle in ledger.read_text():
            return True
        time.sleep(0.02)
    return False


def test_token_usage_injected_and_swallowed(proxy_over_mock, tmp_path,
                                            monkeypatch):
    mock, srv, url = proxy_over_mock("sse_usage")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs")
        raw = _raw_stream(url, {"model": "qwen-auto", "stream": True,
                                "max_tokens": 16,
                                "messages": [{"role": "user",
                                              "content": "hi"}]})
        # upstream was asked for usage on minder's behalf
        assert mock.last_body["stream_options"]["include_usage"] is True
        # client sees the completion it asked for — no injected usage event
        assert b"he" in raw and b"llo" in raw and b"[DONE]" in raw
        assert b"prompt_tokens" not in raw
        assert _wait_ledger(ledger)
        line = ledger.read_text().strip().splitlines()[-1]
        ev = json.loads(line)
        assert ev["event"] == "token_usage"
        assert ev["prompt_tokens"] == 7 and ev["completion_tokens"] == 3
        assert ev["cached_tokens"] == 4
    finally:
        stop(mock, srv)


def test_token_usage_passive_when_client_asked(proxy_over_mock, tmp_path,
                                               monkeypatch):
    mock, srv, url = proxy_over_mock("sse_usage")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs")
        body = {"model": "qwen-auto", "stream": True, "max_tokens": 16,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "hi"}]}
        raw = _raw_stream(url, body)
        # client contract untouched: their flag passes through, the usage
        # event is forwarded too (harvested passively for the ledger)
        assert mock.last_body["stream_options"]["include_usage"] is True
        assert b"prompt_tokens" in raw and b"[DONE]" in raw
        assert _wait_ledger(ledger)
        ev = json.loads(ledger.read_text().strip().splitlines()[-1])
        assert ev["event"] == "token_usage" and ev["prompt_tokens"] == 7
    finally:
        stop(mock, srv)


def test_token_usage_non_streaming(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("json_usage")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs")
        st, body = chat_request(url, {"model": "qwen-exec", "max_tokens": 16,
                                      "messages": [{"role": "user",
                                                    "content": "hi"}]})
        assert st == 200 and body["usage"]["total_tokens"] == 16
        assert _wait_ledger(ledger)
        ev = json.loads(ledger.read_text().strip().splitlines()[-1])
        assert ev["event"] == "token_usage" and ev["reasoning_tokens"] == 4
    finally:
        stop(mock, srv)


def test_client_effort_beats_scheduler(proxy_over_mock, tmp_path,
                                       monkeypatch):
    import minder
    mock, srv, url = proxy_over_mock("default")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs", effort_levels=["low", "medium", "xhigh"])
        minder.CFG_PATH.write_text(json.dumps({"effort_mode": "auto"}))
        # plain question would schedule off; UI pick of xhigh wins
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "reasoning_effort": "xhigh",
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"}]})
        assert mock.last_body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh"}
        assert '"source": "client"' in ledger.read_text()
        # escalation marker beats the client pick (semantic high → xhigh
        # on the [low, medium, xhigh] vocabulary) + Standard budget
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "reasoning_effort": "off",
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L1 — retry"}]})
        assert mock.last_body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh",
            "thinking_budget": 2048}
        # unknown vocabulary value is ignored → scheduler (off for plain)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "reasoning_effort": "ultramax",
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"}]})
        assert mock.last_body["chat_template_kwargs"] == {
            "enable_thinking": False}
    finally:
        clear_caps()
        stop(mock, srv)


# ---------------------------------------------------------------------------
# laya fast decision layer (task difficulty prior) + level-aware budgets
# ---------------------------------------------------------------------------

def _fake_difficulty_client(monkeypatch, label, score, confidence=0.9):
    """Monkeypatch the decision client to a FakeClient that always answers
    the given difficulty label/score. Returns the client (for assertions)."""
    import minder_decision.client as decision_client
    from minder_decision.providers.fake import FakeClient
    key = "difficulty-test"
    client = FakeClient(
        fixtures={key: {"difficulty": label, "difficulty_score": score,
                        "confidence": confidence}},
        key_fn=lambda _s: key)
    monkeypatch.setattr(decision_client, "get_decision_client",
                        lambda: client)
    return client


def _difficulty_cfg(tmp_path, router, monkeypatch, **extra):
    import minder
    cfg_file = tmp_path / "minder.json"
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(minder, "CFG_PATH", cfg_file)
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    cfg = {"effort_mode": "auto", "difficulty_router": router}
    cfg.update(extra)
    cfg_file.write_text(json.dumps(cfg))
    return cfg_file


def test_t6_active_band_applied(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "active", monkeypatch)
        _fake_difficulty_client(monkeypatch, "routine", 1.0)
        # plain question, no marker, no client effort, no mode header
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32768,
                           "messages": [{"role": "user",
                                         "content": "refactor this module"}]})
        body = mock.last_body
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "low",
            "thinking_budget": 2048}
        # ceiling caps the client's 32768 down to the routine band's 8192
        assert body["max_tokens"] == 8192
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_routed" in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t7_marker_beats_laya(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "active", monkeypatch)
        _fake_difficulty_client(monkeypatch, "routine", 1.0)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L1 — retry"}]})
        body = mock.last_body
        # marker wins: L1 → high + Standard budget (not laya's routine/low)
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "high",
            "thinking_budget": 2048}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_routed" not in ledger
        assert "escalation_upgraded" in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t8_client_effort_beats_laya(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs", effort_levels=["low", "medium", "xhigh"])
        _difficulty_cfg(tmp_path, "active", monkeypatch)
        _fake_difficulty_client(monkeypatch, "routine", 1.0)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "reasoning_effort": "xhigh",
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"}]})
        body = mock.last_body
        # client pick wins: xhigh, no level budget (client path has none)
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh"}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_routed" not in ledger
        assert '"source": "client"' in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t9_shadow_request_untouched(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "shadow", monkeypatch)
        _fake_difficulty_client(monkeypatch, "routine", 1.0)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32768,
                           "messages": [{"role": "user",
                                         "content": "refactor this module"}]})
        body = mock.last_body
        # shadow: no budget, no ceiling, scheduler effort (off for plain)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["max_tokens"] == 32768
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_shadow" in ledger
        assert "difficulty_routed" not in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t10_off_zero_behavior_change(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "off", monkeypatch)
        _fake_difficulty_client(monkeypatch, "routine", 1.0)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32768,
                           "messages": [{"role": "user",
                                         "content": "refactor this module"}]})
        body = mock.last_body
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_shadow" not in ledger
        assert "difficulty_routed" not in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t11_laya_unavailable_falls_through(proxy_over_mock, tmp_path,
                                            monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "active", monkeypatch)
        import minder_decision.client as decision_client
        monkeypatch.setattr(decision_client, "get_decision_client",
                            lambda: None)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [{"role": "user",
                                         "content": "what is 2+2?"}]})
        body = mock.last_body
        # no client → falls through to scheduler (off for plain)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "difficulty_routed" not in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t12_spend_guardrail_downgrades(proxy_over_mock, tmp_path,
                                        monkeypatch):
    mock, srv, url = proxy_over_mock("json_usage")
    try:
        write_caps("kwargs")
        # cap 3; each json_usage reply reports reasoning_tokens=4
        _difficulty_cfg(tmp_path, "active", monkeypatch,
                        spend_guardrail_tokens=3)
        _fake_difficulty_client(monkeypatch, "complex", 2.0)
        # request 1: ledger 0 → not capped → complex band
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32768,
                           "messages": [{"role": "user",
                                         "content": "migrate the schema"}]})
        assert mock.last_body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "high",
            "thinking_budget": 10240}
        # request 2: ledger now 4 > 3 → downgraded complex → routine
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32768,
                           "messages": [{"role": "user",
                                         "content": "migrate the schema"}]})
        assert mock.last_body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "low",
            "thinking_budget": 2048}
        ledger = (tmp_path / "state" / "events.jsonl").read_text()
        assert "spend_guardrail_downgrade" in ledger
    finally:
        clear_caps()
        stop(mock, srv)


def test_t13_l1_marker_high_and_standard_budget(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs", effort_levels=["low", "medium", "xhigh"])
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L1 — retry"}]})
        body = mock.last_body
        # semantic high → xhigh on the measured vocab + Standard budget
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh",
            "thinking_budget": 2048}
    finally:
        clear_caps()
        stop(mock, srv)


def test_t14_l2_marker_xhigh_and_deep_budget(proxy_over_mock, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs", effort_levels=["low", "medium", "xhigh"])
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L2 — think harder"}]})
        body = mock.last_body
        assert body["chat_template_kwargs"] == {
            "enable_thinking": True, "reasoning_effort": "xhigh",
            "thinking_budget": 10240}
    finally:
        clear_caps()
        stop(mock, srv)


def test_t15_level_budget_beats_mode_deep(proxy_over_mock, tmp_path,
                                          monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "off", monkeypatch)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L1 — retry"}]},
                     headers={"X-Minder-Mode": "deep"})
        body = mock.last_body
        # level budget (2048) beats the mode's deep budget (4096)
        assert body["chat_template_kwargs"]["thinking_budget"] == 2048
    finally:
        clear_caps()
        stop(mock, srv)


def test_t16_level_budget_cfg_override(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    try:
        write_caps("kwargs")
        _difficulty_cfg(tmp_path, "off", monkeypatch,
                        l1_budget=3072, l2_budget=16384)
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L1 — retry"}]})
        assert mock.last_body["chat_template_kwargs"]["thinking_budget"] == \
            3072
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [
                               {"role": "user", "content": "2+2?"},
                               {"role": "assistant", "content":
                                "[minder] ESCALATION L2 — think harder"}]})
        assert mock.last_body["chat_template_kwargs"]["thinking_budget"] == \
            16384
    finally:
        clear_caps()
        stop(mock, srv)


def test_t17_deescalation_audit(proxy_over_mock, tmp_path, monkeypatch):
    mock, srv, url = proxy_over_mock("default")
    ledger = _state_dir(monkeypatch, tmp_path)
    try:
        write_caps("kwargs")
        # same first user message → same session fp across both requests
        first = {"role": "user", "content": "fix the parser"}
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [first,
                                        {"role": "assistant", "content":
                                         "[minder] ESCALATION L1 — retry"}]})
        # marker ages out of the window → downgrade logged
        chat_request(url, {"model": "qwen-auto", "max_tokens": 32,
                           "messages": [first]})
        text = ledger.read_text()
        assert "escalation_downgraded" in text
        assert '"from_level": 1' in text
        assert '"to_level": null' in text
    finally:
        clear_caps()
        stop(mock, srv)
