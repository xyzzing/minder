#!/usr/bin/env python3
"""minder sink — loopback persistence (and warm-policy) sidecar.

The hook process runs inside DSH's file sandbox, whose only writable path
is the deployment workspace root, so it cannot write
`~/.local/state/minder` (`EROFS`). Every hook write is fail-open, which
made that loss silent. This sidecar runs *unconfined* (systemd --user,
like `minder-proxy.service`) and performs those writes on the hook's
behalf; loopback is not confined.

It also gives the decision/classifier stack a long-lived home, so the
laya model is built once instead of once per tool call (measured: ~6.1 s
per PostToolUse hook, 335 s in one session).

Ops (all validated, all confined to `STATE_DIR` + the memory DB):
  append_jsonl {name, record}   append to events/consults/hook-trace JSONL
  write_state  {task, text}     session state document (minder's own naming)
  record       {hook_event}     memory.from_hook.record() in-process
  policy       {hook_event, warden_out}
                                the whole memory policy pass, warm

Boundary: 127.0.0.1 only, mirroring the console's loopback rule — this is
not an auth boundary. `GET /healthz` and `GET /stats` are read-only.
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minder

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
DEFAULT_PORT = 8392
JSONL_NAMES = ("events.jsonl", "consults.jsonl", "hook-trace.jsonl")
MAX_ERRORS = 20
MAX_BODY_BYTES = 1_000_000

_LOCK = threading.Lock()
_STATS = {
    "started_ts": time.time(),
    "ops": {},
    "errors": [],
    "last_persist_ts": None,
}


def _bump(op, ok=True, detail=None):
    with _LOCK:
        entry = _STATS["ops"].setdefault(op, {"ok": 0, "failed": 0})
        entry["ok" if ok else "failed"] += 1
        if ok:
            _STATS["last_persist_ts"] = time.time()
        elif detail:
            _STATS["errors"].append(
                {"ts": time.time(), "op": op, "error": str(detail)[:300]})
            del _STATS["errors"][:-MAX_ERRORS]


def _op_append_jsonl(payload):
    name = str(payload.get("name") or "")
    if name not in JSONL_NAMES:
        raise ValueError(f"ledger not allowed: {name!r}")
    record = payload.get("record")
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    path = minder.STATE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return {"name": name}


def _op_write_state(payload):
    task = payload.get("task")
    text = payload.get("text")
    if not task or not isinstance(text, str):
        raise ValueError("task and text are required")
    minder.save_state_local(task, text)
    return {"task": str(task)[:200]}


def _op_record(payload):
    from memory import from_hook
    hook_event = payload.get("hook_event")
    if not isinstance(hook_event, dict):
        raise ValueError("hook_event must be an object")
    status = from_hook.record(hook_event)
    advisory = None
    try:
        advisory = from_hook.success_advisory()
    except Exception:
        advisory = None
    return {"status": status if isinstance(status, dict) else {},
            "advisory": advisory}


def _op_policy(payload):
    from memory import policy
    hook_event = payload.get("hook_event")
    if not isinstance(hook_event, dict):
        raise ValueError("hook_event must be an object")
    warden_out = payload.get("warden_out")
    if not isinstance(warden_out, dict):
        warden_out = None
    guard = policy.evaluate(hook_event, warden_out)
    return {"guard": guard if isinstance(guard, dict) else None}


_OPS = {
    "append_jsonl": _op_append_jsonl,
    "write_state": _op_write_state,
    "record": _op_record,
    "policy": _op_policy,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "minder-sink"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet: the console reads /stats
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, status, text):
        raw = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/healthz":
            self._send(200, {"status": "ok",
                             "state_dir": str(minder.STATE_DIR),
                             "uptime_s": round(time.time()
                                               - _STATS["started_ts"], 1)})
        elif path in ("", "/"):
            # Reached by typing the port into a browser, which happened on
            # 2026-09-25 ("the web UI at 8392 has disappeared"). There is no
            # UI here on purpose — say what this is and where the UI lives,
            # instead of answering a person with raw JSON.
            self._send_text(200, (
                "minder sink — loopback persistence sidecar for dsh hooks.\n"
                "Not a UI: this process only accepts POST /persist (and\n"
                "GET /healthz, /stats) from hook processes, which dsh's file\n"
                "sandbox would otherwise stop from writing the state dir.\n"
                "\n"
                f"state dir : {minder.STATE_DIR}\n"
                f"operator UI: http://127.0.0.1:8765  (minder-web, "
                "read-only)\n"
                "health    : GET /healthz      stats: GET /stats\n"
                "capture   : minder-op capture   scorecard: minder-op "
                "scorecard\n"))
        elif path == "/stats":
            with _LOCK:
                body = json.loads(json.dumps(_STATS))
            body["status"] = "ok"
            body["state_dir"] = str(minder.STATE_DIR)
            body["uptime_s"] = round(time.time() - _STATS["started_ts"], 1)
            body["warm"] = _warm_report()
            # The flags this process actually runs the policy pass under —
            # the console compares them with the hook declaration, so a
            # divergence can never be silent.
            body["flags"] = _effective_flags()
            self._send(200, body)
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 1 or length > MAX_BODY_BYTES:
            return self._send(400, {"ok": False, "error": "bad body size"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._send(400, {"ok": False, "error": "bad json"})
        if not isinstance(payload, dict):
            return self._send(400, {"ok": False, "error": "bad payload"})
        path = self.path.rstrip("/")
        if path != "/persist":
            return self._send(404, {"ok": False, "error": "not found"})
        op = str(payload.get("op") or "")
        fn = _OPS.get(op)
        if fn is None:
            _bump(op or "?", ok=False, detail="unknown op")
            return self._send(400, {"ok": False, "error": f"unknown op: {op}"})
        try:
            result = fn(payload)
        except Exception as e:  # a bad op must never kill the sidecar
            _bump(op, ok=False, detail=f"{type(e).__name__}: {e}")
            return self._send(500, {"ok": False,
                                    "error": f"{type(e).__name__}: {e}"})
        _bump(op, ok=True)
        return self._send(200, {"ok": True, "result": result})


def adopt_hook_flags():
    """Run the policy pass under the same flags the hooks declare.

    The pass moved into this process, so a flag declared in the hook
    command but *not* here would be silently inert — the exact class of
    invisible behaviour change this sidecar exists to end. Adopting the
    declaration makes hooks.json the single source of truth; a value in
    our own environment still wins. Returns what was adopted."""
    adopted = {}
    try:
        from memory import sink as client
        declared = client.declared_flags()
    except Exception:
        return adopted
    for var, value in (declared or {}).items():
        if var == "MINDER_SINK_URL":  # our own address, not a policy flag
            continue
        if not os.environ.get(var):
            os.environ[var] = value
            adopted[var] = value
    return adopted


def _effective_flags():
    try:
        from memory import sink as client
        tracked = client.TRACKED_FLAGS
    except Exception:
        tracked = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION",
                   "MINDER_SUCCESS_GUARD", "MINDER_HOOK_TRACE")
    return {var: os.environ.get(var) for var in tracked}


def _warm_report():
    """What the sidecar has already built (so the console can show it)."""
    out = {}
    try:
        from decision import client as dclient
        out["decision"] = dclient.warm_status()
    except Exception as e:
        out["decision"] = {"error": type(e).__name__}
    try:
        from memory import classifier_laya
        out["classifier"] = classifier_laya.warm_status()
    except Exception as e:
        out["classifier"] = {"error": type(e).__name__}
    return out


def _warm_up():
    """Build the expensive providers once, off the request path.

    The whole point of the sidecar is that the laya model is constructed
    once per *session* instead of once per tool call; without this the
    first hook would pay the build (and time out). Best-effort: a missing
    model is the same degraded state the hook already tolerates."""
    try:
        from decision import client as dclient
        dclient.get_decision_client()
    except Exception:
        pass
    try:
        from memory import classifier_laya
        classifier_laya.try_laya_classifier()
    except Exception:
        pass


def build_parser():
    ap = argparse.ArgumentParser(
        prog="minder-sink",
        description="minder loopback persistence sidecar (127.0.0.1 only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("MINDER_SINK_PORT",
                                               DEFAULT_PORT)))
    ap.add_argument("--no-warm", action="store_true",
                    help="skip the background provider warm-up")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        print(f"error: refusing to bind {args.host!r}: the sink is "
              "loopback-only (it performs filesystem writes on behalf of "
              "confined hook processes)", file=sys.stderr)
        return 1
    minder.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Before warming anything: run under the flags the hooks declare, so the
    # policy pass answers exactly as it would have inside the hook.
    adopted = adopt_hook_flags()
    if not args.no_warm:
        # Serve immediately; warm in the background so a slow model build
        # never blocks the first captured event.
        threading.Thread(target=_warm_up, daemon=True).start()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"minder sink on http://{args.host}:{args.port} "
          f"(state_dir={minder.STATE_DIR})", flush=True)
    if adopted:
        print("adopted hook flags: "
              + " ".join(f"{k}={v}" for k, v in sorted(adopted.items())),
              flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
