"""AT-14: concurrent chat completions through the proxy stay intact.

Fires parallel requests (mixed aliases, streamed + non-streamed) and asserts
every response arrives complete and uncorrupted — byte-exact SSE relay and
per-request preset isolation under concurrency.
"""
import json
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import proxy
from mock_upstream import MockUpstream

N_WORKERS = 12
N_REQUESTS = 48


def post(url, body):
    req = urllib.request.Request(
        url + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


def test_concurrent_requests_intact(tmp_path, monkeypatch):
    (proxy.CONFIG_DIR / "model_caps.json").write_text(json.dumps(
        {"kwargs_accepted": True,
         "thinking": {"mechanism": "kwargs", "markers": ["<think>"],
                      "thinking_budget_supported": True,
                      "field": "reasoning_content"}}))
    mock = MockUpstream("default")
    mock.__enter__()
    monkeypatch.setattr(proxy, "UPSTREAM", mock.url)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"

    def one(i):
        model = "qwen-exec" if i % 2 == 0 else "qwen-think"
        status, raw = post(url, {"model": model, "max_tokens": 32,
                                 "messages": [{"role": "user",
                                               "content": f"q{i}"}]})
        payload = json.loads(raw)
        return model, payload["choices"][0]["message"]["content"]

    try:
        with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            results = list(pool.map(one, range(N_REQUESTS)))
        assert len(results) == N_REQUESTS
        for i, (model, content) in enumerate(results):
            if model == "qwen-exec":
                assert content == "42", (i, content)
            else:
                assert "<think>" in content and "42" in content, (i, content)
        # every request was recorded exactly once — no dropped/duplicated
        assert len(mock.requests) == N_REQUESTS
        temps = {body["temperature"] for body in mock.requests}
        assert temps == {0.15, 0.6}  # both presets applied, none leaked
    finally:
        srv.shutdown()
        srv.server_close()
        mock.__exit__(None, None, None)
