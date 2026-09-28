"""Minimal OpenAI-compatible chat client (stdlib urllib). Point it at
minder's proxy (http://127.0.0.1:8390/v1) to evaluate the governed
runtime, or straight at llama-server to measure the bare model."""
import json
import time
import urllib.error
import urllib.request

SYSTEM = ("You are completing a graded business-rules task. Apply only the "
          "inputs and rules given. Reply with one JSON object and no prose.")


def ask(base_url, model, prompt, *, max_tokens=2048, timeout=300, api_key=None):
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        return {"raw": msg.get("content") or "", "error": None,
                "latency_s": round(time.monotonic() - t0, 2),
                "usage": data.get("usage")}
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return {"raw": "", "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - t0, 2), "usage": None}
