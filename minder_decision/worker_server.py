"""Isolated laya decision worker — server half (issue #7).

A persistent subprocess that owns the laya model so a slow import or
wedged inference can never run inside the proxy. Run via
`python3 -m minder_decision.worker` (the shim in worker.py). Binds a
unix socket in the minder state dir, loads laya once, serves one
newline-JSON request per connection, and exits after an idle budget
(bounded memory). The per-request deadline is enforced here AND on the
client side (worker.py), so model traffic never blocks on laya
regardless of what this process does; an inference thread that runs
past the deadline is answered with a timeout error and the process
exits — never serves another request after a wedge.
"""
import json
import os
import socket
import sys
import threading
import time

from .worker import (DEFAULT_DEADLINE_MS, DEFAULT_IDLE_S, MAX_DEADLINE_MS,
                     _dial, _readline, socket_path)


class _StubAgent:
    """Test-only stand-in for the laya agent (MINDER_LAYA_WORKER_STUB is
    a JSON spec: difficulty, difficulty_score). Speaks enough of the
    laya predict surface for LayaSystemOneClient to map."""

    def __init__(self, spec):
        self._spec = spec if isinstance(spec, dict) else {}

    def predict(self, _text, _questions):
        label = str(self._spec.get("difficulty", "routine"))
        try:
            score = float(self._spec.get("difficulty_score", 1.0))
        except (TypeError, ValueError):
            score = 1.0
        return {"answers": {"difficulty": {"choice": label},
                            "difficulty_score": {"score": score}},
                "usage": {"input_tokens": 1, "output_tokens": 1}}


def _build_client():
    stub = os.environ.get("MINDER_LAYA_WORKER_STUB")
    if stub:
        try:
            spec = json.loads(stub)
        except ValueError:
            spec = {}
        from .providers.laya import LayaSystemOneClient
        return LayaSystemOneClient(_StubAgent(spec), source="stub")
    # CPU-only law, same as the in-process provider
    for var in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        os.environ.setdefault(var, "")
    os.environ.setdefault("MINDER_LAYA_DEVICE", "cpu")
    from .providers.laya import _try_modern
    return _try_modern()


def _decide(client, state):
    if not isinstance(state, dict):
        # fail open: a malformed task state must never yield a
        # confident (wrong) opinion — the client falls back
        raise ValueError("state must be an object")
    from .contracts import task_difficulty_contract
    contract = task_difficulty_contract()
    resp = client.system_one(state, contract.questions, contract=contract)
    if resp is None:
        raise ValueError("provider returned no response")
    return {"contract_id": resp.contract_id,
            "contract_version": resp.contract_version,
            "choice_probs": resp.choice_probs,
            "noul_probs": resp.noul_probs,
            "score_values": resp.score_values,
            "confidence": resp.confidence,
            "top_two_margin": resp.top_two_margin,
            "latency_ms": resp.latency_ms,
            "provider": resp.provider,
            "model_version": resp.model_version}


def _reply(conn, payload):
    conn.sendall((json.dumps(payload) + "\n").encode())


def _handle(conn, state):
    conn.settimeout(5.0)
    line = _readline(conn)
    req = json.loads(line) if line else {}
    if not isinstance(req, dict):
        raise ValueError("request is not an object")
    rid = req.get("id")
    if req.get("v") != 1:
        raise ValueError("unsupported protocol version")
    try:
        deadline = int(req.get("deadline_ms") or DEFAULT_DEADLINE_MS)
    except (TypeError, ValueError):
        deadline = DEFAULT_DEADLINE_MS
    deadline = max(50, min(MAX_DEADLINE_MS, deadline))
    client = state["client"]
    if client is None:
        _reply(conn, {"v": 1, "id": rid, "ok": False,
                      "error": state["error"] or "loading"})
        return
    result = {}

    def _run():
        try:
            result["response"] = _decide(client, req.get("state"))
        except Exception as err:  # reported to the client, which falls back
            result["error"] = repr(err)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline / 1000.0)
    if worker.is_alive():
        # the inference thread is wedged past the deadline: answer the
        # client (best effort) and exit unconditionally — never serve
        # another request after a wedge
        try:
            _reply(conn, {"v": 1, "id": rid, "ok": False,
                          "error": "timeout"})
        except OSError:
            pass
        os._exit(3)
    if "error" in result:
        _reply(conn, {"v": 1, "id": rid, "ok": False,
                      "error": result["error"]})
        return
    _reply(conn, {"v": 1, "id": rid, "ok": True,
                  "response": result["response"]})


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
    state = {"client": None, "error": None}

    def _load():
        try:
            state["client"] = _build_client()
            if state["client"] is None:
                state["error"] = "model_unavailable"
        except Exception as err:
            state["error"] = repr(err)

    threading.Thread(target=_load, daemon=True).start()
    try:
        idle_s = float(os.environ.get("MINDER_LAYA_WORKER_IDLE_S")
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
                # the client sees the dropped connection and falls back;
                # the log file is the diagnostic trail
                print(f"minder-decision-worker: request error: {err!r}",
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
