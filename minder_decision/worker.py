"""Isolated laya decision worker — client side (issue #7).

The server half lives in `worker_server.py`; run it as
`python3 -m minder_decision.worker` (the __main__ shim below). This
module holds the protocol constants the two halves share and the
client the proxy's difficulty router talks to.

WorkerDifficultyClient — connect-or-spawn (cooldown-guarded), send the
redacted task state, wait at most deadline_ms, and return a
DecisionResponse or None. Every failure mode (no worker, connect
refused, timeout, garbage, wrong contract) is None: the difficulty
router treats None as "no opinion" and the deterministic four-level
scheduler takes over. Never raises for ordinary inputs.

Protocol (v1, one request per connection over a unix socket):
  -> {"v": 1, "id": N, "deadline_ms": ms, "state": {...}}
  <- {"v": 1, "id": N, "ok": true, "response": {...DecisionResponse...}}
  <- {"v": 1, "id": N, "ok": false, "error": "..."}

Env knobs:
  MINDER_LAYA_WORKER_SOCK      socket path override (default
                               $MINDER_STATE_DIR/laya-worker.sock)
  MINDER_LAYA_WORKER_DEADLINE_MS  per-decision deadline (default 1500)
  MINDER_LAYA_WORKER_IDLE_S    server exit after idle (default 1800)
  MINDER_LAYA_WORKER_STUB      test-only stub agent spec (JSON)
"""
import itertools
import json

from . import log_stamp
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .types import DecisionResponse

MAX_LINE = 256 * 1024
MAX_DEADLINE_MS = 5000
DEFAULT_DEADLINE_MS = 1500
SPAWN_COOLDOWN_S = 30.0
DEFAULT_IDLE_S = 1800.0
# Single source for both halves of the socket pair. A mismatch used to be
# a bare exception on the server and a closed connection on the client.
PROTOCOL_VERSION = 1


def _state_dir():
    return Path(os.environ.get(
        "MINDER_STATE_DIR", os.path.expanduser("~/.local/state/minder")))


def socket_path():
    env = os.environ.get("MINDER_LAYA_WORKER_SOCK")
    if env:
        return Path(env)
    return _state_dir() / "laya-worker.sock"


def _readline(conn):
    buf = bytearray()
    while len(buf) < MAX_LINE:
        try:
            chunk = conn.recv(4096)
        except (socket.timeout, OSError):
            return None
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in chunk:
            break
    return bytes(buf) if buf else None


def _dial(path, timeout):
    conn, _errno = _dial_err(path, timeout)
    return conn


def _dial_err(path, timeout):
    """(conn, errno) — errno tells refused/absent (safe to respawn)
    apart from backlog-full or unknown failures (never unlink)."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(str(path))
        return conn, 0
    except OSError as err:
        try:
            conn.close()
        except OSError:
            pass
        return None, err.errno


def _response_from_dict(data):
    if not isinstance(data, dict):
        return None
    try:
        return DecisionResponse(
            contract_id=str(data.get("contract_id") or ""),
            contract_version=data.get("contract_version"),
            choice_probs=data.get("choice_probs") or {},
            noul_probs=data.get("noul_probs") or {},
            score_values=data.get("score_values") or {},
            confidence=float(data.get("confidence") or 0.0),
            top_two_margin=float(data.get("top_two_margin") or 0.0),
            latency_ms=float(data.get("latency_ms") or 0.0),
            provider=str(data.get("provider") or "laya-worker"),
            model_version=str(data.get("model_version") or ""))
    except (TypeError, ValueError):
        return None


_IDS = itertools.count(1)

# One failed acquire puts the whole client class in cooldown: while the
# worker is down, per-request cost is one cheap liveness dial, never a
# spawn attempt (and never a model load).
_COOLDOWN = {"until": 0.0}
_COOLDOWN_LOCK = threading.Lock()


def _in_cooldown():
    with _COOLDOWN_LOCK:
        return time.monotonic() < _COOLDOWN["until"]


def _note_failure():
    with _COOLDOWN_LOCK:
        _COOLDOWN["until"] = time.monotonic() + SPAWN_COOLDOWN_S


def _clear_cooldown():
    with _COOLDOWN_LOCK:
        _COOLDOWN["until"] = 0.0


def _child_env():
    env = os.environ.copy()
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"]
                                    if env.get("PYTHONPATH") else "")
    for var in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        env.setdefault(var, "")
    env.setdefault("MINDER_LAYA_DEVICE", "cpu")
    return env


def _spawn_worker(path):
    """Start the detached worker if no live one answers. flock-guarded
    so concurrent clients spawn at most one; the socket is unlinked (and
    the spawn attempted) only when a connect was REFUSED or the file is
    gone — a backlog-full or timed-out dial means a live worker is just
    busy, and unlinking its socket would orphan it."""
    import errno as _errno
    lock_path = path.with_name(path.name + ".spawn.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError:
            return
        try:
            conn, err = _dial_err(path, 0.25)
            if conn is not None:
                conn.close()
                return  # someone else won the race
            if err not in (_errno.ECONNREFUSED, _errno.ENOENT):
                return  # a live worker is busy — do not touch its socket
            try:
                path.unlink()
            except OSError:
                pass
            log_path = path.with_name("laya-worker.log")
            try:
                if log_path.exists() and log_path.stat().st_size > \
                        1_000_000:
                    log_path.rename(log_path.with_name(
                        "laya-worker.log.old"))
            except OSError:
                pass
            log = open(log_path, "ab")
            try:
                subprocess.Popen(
                    [sys.executable, "-m", "minder_decision.worker"],
                    env=_child_env(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=log,
                    start_new_session=True, close_fds=True)
            finally:
                log.close()
        finally:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except OSError:
                pass


_CLIENT_LOGGED: dict = {}


def _client_fail(path, reason, detail=""):
    """Name a client-side failure in the worker's log file.

    The client is fail-open by contract (Law #2) and returns None, which
    is why the live router produced zero events and zero explanation:
    every failure looked identical. One line per reason per process, so
    a permanently dead worker cannot flood the log while still naming
    itself once. Never raises - a failed diagnostic must not change the
    fail-open path."""
    line = f"minder-decision-worker-client: {reason}"
    if detail:
        line += f": {detail}"
    line = log_stamp.stamp() + line
    if _CLIENT_LOGGED.get(reason):
        return
    _CLIENT_LOGGED[reason] = True
    try:
        log_path = Path(path).with_name("laya-worker.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as fh:
            fh.write((line + "\n").encode())
    except OSError:
        pass


def _reset_client_log_marks():
    """Test seam: let a test assert each reason is written once."""
    _CLIENT_LOGGED.clear()
    _LIVE_NOTED.clear()


_LIVE_NOTED: dict = {}
# Module level, not per instance: the process-wide client is a singleton, and
# a per-instance dict would let each new client re-report one dead worker.


class WorkerDifficultyClient:
    """SystemOneClient over the isolated worker. Returns None on any
    failure (the router's deterministic fallback takes over); never
    raises for ordinary inputs. Each failure names itself once in
    laya-worker.log (`_client_fail`)."""

    model_version = "laya-worker"

    def __init__(self, deadline_ms=None, spawn=True, path=None):
        self._deadline_ms = deadline_ms
        self._spawn = spawn
        self._path = path

    def _deadline(self):
        if self._deadline_ms is not None:
            return max(50, min(MAX_DEADLINE_MS, self._deadline_ms)) / 1000.0
        try:
            ms = int(os.environ.get("MINDER_LAYA_WORKER_DEADLINE_MS")
                     or DEFAULT_DEADLINE_MS)
        except ValueError:
            ms = DEFAULT_DEADLINE_MS
        return max(50, min(MAX_DEADLINE_MS, ms)) / 1000.0

    def _connect(self, deadline, path=None):
        path = path or self._path or socket_path()
        if _in_cooldown():
            conn = _dial(path, 0.02)  # cheap liveness check only
            if conn is not None:
                _clear_cooldown()
            elif not _LIVE_NOTED["dial_refused"]:
                _LIVE_NOTED["dial_refused"] = True
                _client_fail(path, "dial_refused",
                            f"cooldown, no worker at {path}")
            return conn
        conn = _dial(path, min(deadline, 0.25))
        if conn is not None:
            return conn
        if not self._spawn:
            _client_fail(path, "dial_refused", f"no worker at {path}")
            return None
        _spawn_worker(path)
        conn = _dial(path, 0.5)
        if conn is None:
            _note_failure()
            _client_fail(path, "spawn_failed",
                         f"no worker after spawn at {path}")
        return conn

    def system_one(self, state, questions=None, model=None, contract=None):
        try:
            return self._decide(state)
        except Exception as err:
            # Law #2: never raise at the caller. Name it in the log first.
            _client_fail(self._path or socket_path(), "client_error",
                         repr(err))
            return None

    def _decide(self, state):
        deadline = self._deadline()
        path = self._path or socket_path()
        conn = self._connect(deadline, path=path)
        if conn is None:
            return None
        try:
            req = {"v": PROTOCOL_VERSION, "id": next(_IDS),
                   "deadline_ms": int(deadline * 1000),
                   "state": state}  # the server rejects a non-object
            conn.sendall((json.dumps(req) + "\n").encode())
            conn.settimeout(deadline)
            line = _readline(conn)
            if not line:
                _client_fail(path, "no_reply", "worker closed the socket")
                return None
            reply = json.loads(line.decode("utf-8", "replace"))
            if not isinstance(reply, dict):
                _client_fail(path, "bad_reply", "reply is not an object")
                return None
            if not reply.get("ok"):
                # the worker named its own failure; carry it to the log
                _client_fail(path, "worker_error",
                             str(reply.get("error") or "?")[:200])
                return None
            resp = _response_from_dict(reply.get("response"))
            if resp is None:
                _client_fail(path, "bad_reply", "malformed response")
            return resp
        except (OSError, ValueError) as err:
            _client_fail(path, "deadline" if isinstance(err, OSError)
                         else "bad_reply", repr(err))
            return None
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    from .worker_server import serve
    sys.exit(serve())
