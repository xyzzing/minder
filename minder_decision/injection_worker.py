"""Isolated prompt-injection guard worker — server half (issue #65).

A persistent subprocess that owns the classifier so a slow transformers
import or wedged inference can never run inside the proxy. Run via
`python3 -m minder_decision.injection_worker`. Binds a unix socket in the
minder state dir, loads Llama Prompt Guard 2 (a small DeBERTa classifier,
an optional extra - never a runtime dependency) once in a background
thread, serves one newline-JSON request per connection, and exits after
an idle budget (bounded memory).

The per-request deadline is enforced here AND on the client side
(injection.py): an inference thread that runs past the deadline is
answered with a timeout error and the process exits — never serves
another request after a wedge. When transformers or the model is
missing, the worker answers ok:false `model_unavailable...` and the
client records no opinion.
"""
import json
import math
import os
import socket
import sys
import threading
import time

from .injection import (DEFAULT_DEADLINE_MS, DEFAULT_IDLE_S,
                        MAX_DEADLINE_MS, PROTOCOL_VERSION, socket_path)
from .worker import _dial, _readline


class _StubAgent:
    """Test-only stand-in for the classifier (MINDER_INJECTION_GUARD_STUB
    is a JSON spec: verdict, score). No transformers install needed."""

    model = "stub"

    def __init__(self, spec):
        self._spec = spec if isinstance(spec, dict) else {}

    def classify(self, _text):
        try:
            score = float(self._spec.get("score", 0.01))
        except (TypeError, ValueError):
            score = 0.01
        return {"verdict": str(self._spec.get("verdict", "safe")),
                "score": score, "model": self.model}


# Llama Prompt Guard 2: 0 legitimate, 1 injection, 2 jailbreak. The
# verdict reads the untrusted-classes' probability, not the argmax, so a
# jailbreak-shaped payload scores as what it is.
PROMPT_GUARD_MODEL = "meta-llama/Prompt-Guard-2-86M"
PROMPT_GUARD_NAME = "prompt-guard-2-86m"
INJECTION_THRESHOLD = 0.5


class PromptGuardAgent:
    """Llama Prompt Guard 2 via transformers, loaded lazily at worker
    boot. The import and the weights are optional: a missing install
    raises, and serve() reports the worker unavailable."""

    model = PROMPT_GUARD_NAME

    def __init__(self):
        from transformers import (AutoModelForSequenceClassification,
                                  AutoTokenizer)
        self._id = (os.environ.get("MINDER_INJECTION_GUARD_MODEL")
                    or PROMPT_GUARD_MODEL)
        self._tok = AutoTokenizer.from_pretrained(self._id)
        # from_pretrained returns the model in eval mode already
        self._model = AutoModelForSequenceClassification.from_pretrained(
            self._id)

    def classify(self, text):
        inputs = self._tok(text, return_tensors="pt", truncation=True,
                           max_length=512)
        logits = self._model(**inputs).logits[0].tolist()
        peak = max(logits)
        exps = [math.exp(v - peak) for v in logits]
        total = sum(exps)
        probs = [e / total for e in exps]
        score = max(probs[1], probs[2]) if len(probs) > 2 else 0.0
        verdict = "injection" if score >= INJECTION_THRESHOLD else "safe"
        return {"verdict": verdict, "score": score, "model": self.model}


def _build_agent():
    """(agent, error): the stub for tests, else the real classifier with
    CPU-only law (same as the laya worker). Every load failure degrades
    to a named unavailable state, never a crash."""
    stub = os.environ.get("MINDER_INJECTION_GUARD_STUB")
    if stub:
        try:
            spec = json.loads(stub)
        except ValueError:
            spec = {}
        return _StubAgent(spec), None
    for var in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        os.environ.setdefault(var, "")
    try:
        return PromptGuardAgent(), None
    except Exception as err:
        return None, f"model_unavailable: {err!r}"


def _classify(agent, text):
    if not isinstance(text, str):
        # fail open: a malformed body must never yield a confident
        # (wrong) verdict — the client records no opinion
        raise ValueError("text must be a string")
    return agent.classify(text)


def _reply(conn, payload):
    conn.sendall((json.dumps(payload) + "\n").encode())


def _handle(conn, state):
    conn.settimeout(5.0)
    line = _readline(conn)
    req = json.loads(line) if line else {}
    if not isinstance(req, dict):
        raise ValueError("request is not an object")
    rid = req.get("id")
    if req.get("v") != PROTOCOL_VERSION:
        # Answer, never drop: a client on another protocol version has to
        # see the version it sent, or both halves fail open with nothing
        # to compare.
        seen = repr(req.get("v"))
        msg = (f"unsupported protocol version {seen} "
               f"(expected {PROTOCOL_VERSION})")
        print(f"minder-injection-worker: {msg}", file=sys.stderr, flush=True)
        _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": False,
                      "error": msg})
        return
    if req.get("status"):
        # doctor's liveness-with-substance probe: what the worker can
        # classify with right now
        agent, error = state["agent"], state["error"]
        _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": True,
                      "agent": ({"model": agent.model} if agent is not None
                                else {"error": error or "loading"})})
        return
    try:
        deadline = int(req.get("deadline_ms") or DEFAULT_DEADLINE_MS)
    except (TypeError, ValueError):
        deadline = DEFAULT_DEADLINE_MS
    deadline = max(50, min(MAX_DEADLINE_MS, deadline))
    agent = state["agent"]
    if agent is None:
        _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": False,
                      "error": state["error"] or "loading"})
        return
    result = {}

    def _run():
        try:
            result["opinion"] = _classify(agent, req.get("text"))
        except Exception as err:  # reported to the client, which fails open
            result["error"] = repr(err)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline / 1000.0)
    if worker.is_alive():
        # the inference thread is wedged past the deadline: answer the
        # client (best effort) and exit unconditionally — never serve
        # another request after a wedge
        try:
            _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": False,
                          "error": "timeout"})
        except OSError:
            pass
        os._exit(3)
    if "error" in result:
        _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": False,
                      "error": result["error"]})
        return
    _reply(conn, {"v": PROTOCOL_VERSION, "id": rid, "ok": True,
                  "response": result["opinion"]})


def serve():
    path = socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        live = _dial(path, 0.5)
        if live is not None:
            live.close()
            return 0  # another worker already owns the socket
        try:
            path.unlink()
        except OSError:
            pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(4)
    bound_ino = os.stat(path).st_ino  # only unlink our own socket on exit
    state = {"agent": None, "error": None}

    def _load():
        state["agent"], state["error"] = _build_agent()

    threading.Thread(target=_load, daemon=True).start()
    try:
        idle_s = float(os.environ.get("MINDER_INJECTION_GUARD_IDLE_S")
                       or DEFAULT_IDLE_S)
    except ValueError:
        idle_s = DEFAULT_IDLE_S
    srv.settimeout(5.0)
    last_busy = time.monotonic()
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                if time.monotonic() - last_busy > idle_s:
                    break
                continue
            last_busy = time.monotonic()
            try:
                _handle(conn, state)
            except Exception as err:
                # the client sees the dropped connection and fails open;
                # the log file is the diagnostic trail
                print(f"minder-injection-worker: request error: {err!r}",
                      file=sys.stderr, flush=True)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
    finally:
        srv.close()
        try:
            if os.stat(path).st_ino == bound_ino:
                path.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(serve())
