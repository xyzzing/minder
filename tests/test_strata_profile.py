"""Strata single-slot profile (issue #3): concurrency probing, request
queueing, thinking-prefix pinning, context-400 clamp retry, ledger engine
field."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import adapter
import minder
import proxy
from mock_upstream import MockUpstream

from test_proxy import stop


@pytest.fixture
def proxy_over_mock():
    def start(behavior="default"):
        mock = MockUpstream(behavior)
        mock.__enter__()
        proxy.UPSTREAM = mock.url
        proxy._caps_cache.update(path=None, mtime=None, caps=None)
        proxy._MODE_PIN.clear()
        proxy._LAST_KWARGS.clear()
        proxy._slot_reset()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        return mock, srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start


def raw_request(url, body, headers=None):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(
        url + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def single_slot_caps(mechanism="kwargs"):
    return {"kwargs_accepted": mechanism == "kwargs",
            "thinking": {"mechanism": mechanism, "markers":
                         ["<think>", "</think>"],
                         "thinking_budget_supported": True,
                         "field": "reasoning_content"},
            "softswitch_tokens": {"on": "/think", "off": "/no_think"},
            "effort_supported": False, "effort_channel": None,
            "concurrency": {"single_slot": True, "serving": 1,
                            "source": "status"}}


def write_caps_file(caps):
    proxy.minder.caps_path().write_text(json.dumps(caps))


@pytest.fixture
def events(monkeypatch):
    seen = []

    def log(task, event, **kw):
        seen.append((task, event, kw))

    monkeypatch.setattr(proxy.minder, "log", log)
    return seen


def test_probe_concurrency_from_status():
    def get(path, t=30):
        if path == "/v1/status":
            return 200, {"concurrency": {"serving": 1, "requested": 1}}
        raise AssertionError(f"unexpected probe {path}")

    assert adapter.probe_concurrency("http://x", get=get) == {
        "single_slot": True, "serving": 1, "source": "status"}


def test_probe_concurrency_from_slots():
    calls = []

    def get(path, t=30):
        calls.append(path)
        if path == "/v1/status":
            return 404, {"error": "no status"}
        return 200, {"slots": [{"id": i} for i in range(4)]}

    out = adapter.probe_concurrency("http://x", get=get)
    assert out == {"single_slot": False, "serving": 4, "source": "slots"}
    assert "/slots" in calls


def test_probe_concurrency_unknown_shape():
    def get(path, t=30):
        return 404, {}

    assert adapter.probe_concurrency("http://x", get=get) is None


def test_run_cap_carries_concurrency():
    """run_cap reports the measured concurrency posture in caps."""
    def get(path, t=30):
        if path == "/v1/status":
            return 200, {"concurrency": {"serving": 1, "requested": 1}}
        if path == "/v1/models":
            return 200, {"data": [{"id": "strata-8b"}]}
        if path == "/props":
            return 404, {}
        return 404, {}

    def post(body, t=120):
        # kwargs differential: enable_thinking honored; softswitch absent
        ctk = body.get("chat_template_kwargs") or {}
        on = bool(ctk.get("enable_thinking", True))
        msg = {"role": "assistant", "content": "<think>x</think>42" if on
               else "42"}
        return 200, {"choices": [{"message": msg}]}

    caps, err = adapter.run_cap("http://x", post=post, get=get)
    assert err is None
    assert caps["concurrency"]["single_slot"] is True
    assert caps["concurrency"]["source"] == "status"


def test_queue_serializes_and_logs_wait(proxy_over_mock, events,
                                        monkeypatch, tmp_path):
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("slow")
    try:
        results = []

        def one(i):
            results.append(raw_request(url, {"model": "qwen-exec",
                                              "max_tokens": 16,
                                              "messages": [
                                                  {"role": "user",
                                                   "content": f"q{i}"}]}))

        t1 = threading.Thread(target=one, args=(1,))
        t1.start()
        time.sleep(0.1)  # t1 holds the slot inside the slow upstream
        t2 = threading.Thread(target=one, args=(2,))
        t2.start()
        t1.join(10)
        t2.join(10)
        assert {st for st, _ in results} == {200}
        # second request arrived at the upstream only after the first
        # finished: the queue held it for the rest of the slow window
        waits = [kw for _, ev, kw in events if ev == "queue_wait"]
        assert len(waits) == 1
        assert waits[0]["wait_ms"] >= 100
    finally:
        stop(mock, srv)


def test_queue_full_returns_503(proxy_over_mock, events, monkeypatch,
                                tmp_path):
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    tmp_path.joinpath("minder.json").write_text(
        json.dumps({"single_slot_queue_depth": 0}))
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("slow")
    try:
        out = {}

        def one(i, key):
            out[key] = raw_request(url, {"model": "qwen-exec",
                                          "max_tokens": 16,
                                          "messages": [
                                              {"role": "user",
                                               "content": f"q{i}"}]})

        t1 = threading.Thread(target=one, args=(1, "first"))
        t1.start()
        time.sleep(0.1)
        t2 = threading.Thread(target=one, args=(2, "second"))
        t2.start()
        t2.join(10)
        st, body = out["second"]
        assert st == 503
        err = json.loads(body)["error"]
        assert err["type"] == "minder_queue_full"
        t1.join(10)
        assert out["first"][0] == 200
        assert [e for _, e, _ in events if e == "queue_full"]
    finally:
        stop(mock, srv)


def test_no_queue_without_single_slot(proxy_over_mock, events):
    write_caps_file({"kwargs_accepted": True,
                     "thinking": {"mechanism": "kwargs",
                                  "markers": ["<think>", "</think>"],
                                  "thinking_budget_supported": True,
                                  "field": "reasoning_content"}})
    mock, srv, url = proxy_over_mock("slow")
    try:
        arrivals = []

        def one(i):
            arrivals.append(time.monotonic())
            raw_request(url, {"model": "qwen-exec", "max_tokens": 16,
                               "messages": [{"role": "user",
                                             "content": f"q{i}"}]})

        t1 = threading.Thread(target=one, args=(1,))
        t1.start()
        t2 = threading.Thread(target=one, args=(2,))
        t2.start()
        t1.join(10)
        t2.join(10)
        # both reached the upstream while the first was still in flight
        assert arrivals[1] - arrivals[0] < 0.2
        assert not [e for _, e, _ in events if e == "queue_wait"]
    finally:
        stop(mock, srv)


SESSION_MSG = [{"role": "user", "content": "shared first message"}]


def test_mode_flip_pinned_under_single_slot(proxy_over_mock, events):
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("default")
    try:
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                                   "messages": SESSION_MSG},
                             headers={"X-Minder-Mode": "deep"})
        assert st == 200
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                                   "messages": SESSION_MSG},
                             headers={"X-Minder-Mode": "direct"})
        assert st == 200
        # the held request kept the pinned thinking-on kwargs
        assert mock.requests[-1]["chat_template_kwargs"] == \
            {"enable_thinking": True}
        assert [e for _, e, _ in events if e == "mode_pin_hold"]
    finally:
        stop(mock, srv)


def test_escalation_breaks_pin_and_logs(proxy_over_mock, events):
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("default")
    try:
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                                   "messages": SESSION_MSG},
                             headers={"X-Minder-Mode": "direct"})
        assert st == 200
        assert mock.requests[-1]["chat_template_kwargs"] == \
            {"enable_thinking": False}
        escalated = SESSION_MSG + [
            {"role": "user",
             "content": "[minder] ESCALATION L1 retry budget hit"}]
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                                   "messages": escalated})
        assert st == 200
        assert mock.requests[-1]["chat_template_kwargs"][
            "enable_thinking"] is True
        breaks = [e for _, e, _ in events if e == "cache_prefix_break"]
        assert breaks
    finally:
        stop(mock, srv)


def test_mode_flip_free_when_not_single_slot(proxy_over_mock, events):
    write_caps_file({"kwargs_accepted": True,
                     "thinking": {"mechanism": "kwargs",
                                  "markers": ["<think>", "</think>"],
                                  "thinking_budget_supported": True,
                                  "field": "reasoning_content"}})
    mock, srv, url = proxy_over_mock("default")
    try:
        raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                           "messages": SESSION_MSG},
                     headers={"X-Minder-Mode": "deep"})
        raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                           "messages": SESSION_MSG},
                     headers={"X-Minder-Mode": "direct"})
        assert mock.requests[-1]["chat_template_kwargs"] == \
            {"enable_thinking": False}
        assert not [e for _, e, _ in events if e == "mode_pin_hold"]
    finally:
        stop(mock, srv)


def test_softswitch_flips_not_pinned(proxy_over_mock, events):
    write_caps_file(single_slot_caps(mechanism="softswitch"))
    mock, srv, url = proxy_over_mock("softswitch_only")
    try:
        raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                           "messages": SESSION_MSG},
                     headers={"X-Minder-Mode": "deep"})
        raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                           "messages": SESSION_MSG},
                     headers={"X-Minder-Mode": "direct"})
        last = str(mock.requests[-1]["messages"][-1]["content"])
        assert last.rstrip().endswith("/no_think")
        assert not [e for _, e, _ in events if e == "mode_pin_hold"]
    finally:
        stop(mock, srv)


def test_auto_kwargs_change_logs_break(proxy_over_mock, events):
    """The auto effort scheduler varies kwargs per turn by design; under a
    single-slot engine every change is a visible prefix break."""
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("default")
    try:
        raw_request(url, {"model": "qwen-auto", "max_tokens": 32,
                          "messages": SESSION_MSG},
                    headers={"X-Minder-Mode": "lean"})
        raw_request(url, {"model": "qwen-auto", "max_tokens": 32,
                          "messages": SESSION_MSG},
                    headers={"X-Minder-Mode": "deep"})
        breaks = [kw for _, ev, kw in events if ev == "cache_prefix_break"]
        assert breaks
        # the first break carries the lean budget, the flip to deep the
        # deep one: both kwargs renders are visible in the audit trail
        assert any("1024" in b["now"] or "1024" in b["was"] for b in breaks)
    finally:
        stop(mock, srv)


def test_context_400_clamped_once(proxy_over_mock, events):
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("context_400")
    try:
        # unknown model string: no preset overrides max_tokens, so the
        # requested 1000 reaches the upstream verbatim
        st, _ = raw_request(url, {"model": "unlisted-model",
                                  "max_tokens": 1000,
                                  "messages": SESSION_MSG})
        assert st == 200
        # one retry with the clamp floor applied, then the upstream answered
        assert len(mock.requests) == 2
        assert mock.requests[-1]["max_tokens"] == 512
        clamps = [kw for _, ev, kw in events if ev == "context_clamp_retry"]
        assert clamps == [{"was": 1000, "now": 512}]
    finally:
        stop(mock, srv)


def test_context_400_not_retried_without_single_slot(proxy_over_mock,
                                                     events):
    write_caps_file({"kwargs_accepted": True,
                     "thinking": {"mechanism": "kwargs",
                                  "markers": ["<think>", "</think>"],
                                  "thinking_budget_supported": True,
                                  "field": "reasoning_content"}})
    mock, srv, url = proxy_over_mock("context_400")
    try:
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 1000,
                                   "messages": SESSION_MSG})
        assert st == 400
        assert len(mock.requests) == 1
    finally:
        stop(mock, srv)


def test_other_400_not_retried(proxy_over_mock, events):
    write_caps_file(single_slot_caps())
    mock, srv, url = proxy_over_mock("reject_kwargs")
    try:
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 32,
                                   "messages": SESSION_MSG})
        assert st == 400
        assert len(mock.requests) == 1
    finally:
        stop(mock, srv)


def wait_for_event(events, name, timeout=5.0):
    """Events can land just after the response body reaches the client
    (token_usage logs in the handler's finally); poll instead of racing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hits = [kw for _, ev, kw in events if ev == name]
        if hits:
            return hits
        time.sleep(0.05)
    return []


def test_token_usage_records_engine(proxy_over_mock, events, monkeypatch,
                                    tmp_path):
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    mock, srv, url = proxy_over_mock("json_usage")
    try:
        (tmp_path / "minder.json").write_text(json.dumps({
            "engines": {"llama": {"upstream": "http://127.0.0.1:1"},
                        "strata": {"upstream": mock.url}},
            "active_engine": "strata"}))
        write_caps_file(single_slot_caps())
        st, _ = raw_request(url, {"model": "qwen-exec", "max_tokens": 16,
                                   "messages": SESSION_MSG})
        assert st == 200
        usage = wait_for_event(events, "token_usage")
        assert usage and usage[-1]["engine"] == "strata"
        assert usage[-1]["reasoning_tokens"] == 4
    finally:
        stop(mock, srv)
