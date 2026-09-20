#!/usr/bin/env python3
"""Scripted mock upstream for minder tests (prd.md §8).

Behaviors (constructor arg):
  default          — honors chat_template_kwargs.enable_thinking: reasoning
                     appears as <think>...</think> in content when true
  reject_kwargs    — 400 on chat_template_kwargs / thinking_budget fields
  reasoning_content— thinking lands in message.reasoning_content, content clean
  softswitch_only  — ignores kwargs; honors trailing /think //no_think on the
                     last user message
  sse              — streams a fixture chunked SSE response regardless of body
Records every request body to .requests (list) for body assertions (AT-7b, AT-16).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

THINK_BLOCK = ("<think>Let me compute 7 times 6. Seven sixes are forty-two."
               "</think>42")
PLAIN = "42"


class MockUpstream:
    def __init__(self, behavior="default", port=0):
        self.behavior = behavior
        self.requests = []
        self._lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                with outer._lock:
                    outer.requests.append(body)
                if self.path.rstrip("/").endswith("/models"):
                    payload = json.dumps(
                        {"object": "list",
                         "data": [{"id": "qwen27b-fusion", "object": "model"}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if outer.behavior == "reject_kwargs" and body and \
                        ("chat_template_kwargs" in body or
                         any("thinking_budget" in str(k) for k in body)):
                    self._json(400, {"error": {"message":
                        "unknown field rejected by strict parser"}})
                    return
                if outer.behavior == "reject_effort" and body and \
                        "reasoning_effort" in json.dumps(body):
                    self._json(400, {"error": {"message":
                        "reasoning_effort rejected"}})
                    return
                if outer.behavior == "sse":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in (b'data: {"choices": [{"delta": '
                                  b'{"content": "he"}}]}\n\n',
                                  b'data: {"choices": [{"delta": '
                                  b'{"content": "llo"}}]}\n\n',
                                  b"data: [DONE]\n\n"):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                want_think = False
                if body:
                    msgs = body.get("messages", [])
                    last_user = next((m for m in reversed(msgs)
                                      if m.get("role") == "user"), {})
                    text = str(last_user.get("content", ""))
                    if outer.behavior == "softswitch_only":
                        want_think = not text.rstrip().endswith("/no_think")
                    else:
                        ctk = body.get("chat_template_kwargs") or {}
                        want_think = bool(ctk.get("enable_thinking", True))
                if outer.behavior == "reasoning_content" and want_think:
                    msg = {"role": "assistant", "content": PLAIN,
                           "reasoning_content": THINK_BLOCK[8:-9]}
                elif want_think:
                    msg = {"role": "assistant", "content": THINK_BLOCK}
                else:
                    msg = {"role": "assistant", "content": PLAIN}
                self._json(200, {"id": "mock", "object":
                          "chat.completion", "model": body.get("model", "mock")
                          if body else "mock",
                          "choices": [{"index": 0, "message": msg,
                                       "finish_reason": "stop"}]})

            def _json(self, code, obj):
                payload = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    payload = json.dumps(
                        {"object": "list",
                         "data": [{"id": "qwen27b-fusion", "object": "model"}]}
                    ).encode()
                else:
                    payload = json.dumps({"status": "ok",
                                          "path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    @property
    def last_body(self):
        return self.requests[-1] if self.requests else None


if __name__ == "__main__":
    import sys
    with MockUpstream(sys.argv[1] if len(sys.argv) > 1 else "default") as m:
        print(m.url)
        m._thread.join()
