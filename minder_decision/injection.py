"""Isolated prompt-injection guard worker — client side (issue #65).

The proxy is minder's trust boundary, and until now nothing scored a
request for prompt injection. The guard mirrors the laya decision worker
(minder_decision/worker.py): the classifier lives only in the server half
(`injection_worker.py`, run as `python3 -m
minder_decision.injection_worker`), a persistent subprocess with a
per-request deadline enforced on BOTH sides, and every failure mode is
None — the request goes out untouched and the failure is named once in
`injection-worker.log`.

Shadow only: an opinion is a ledger event plus an injection_shadow row,
never a modification. MINDER_INJECTION_GUARD=off (default) takes no code
path past the mode check — no events, no processes, byte-identical
behavior.

Protocol (v1, one request per connection over a unix socket):
  -> {"v": 1, "id": N, "deadline_ms": ms, "text": "..."}
  <- {"v": 1, "id": N, "ok": true, "response": {"verdict", "score",
         "model"}}
  <- {"v": 1, "id": N, "ok": true, "agent": {"model"|"error"}}
  <- {"v": 1, "id": N, "ok": false, "error": "..."}

Env knobs:
  MINDER_INJECTION_GUARD            off (default) | shadow
  MINDER_INJECTION_GUARD_SOCK       socket path override (default
                                    $MINDER_STATE_DIR/injection-worker.sock)
  MINDER_INJECTION_GUARD_DEADLINE_MS  per-opinion deadline (default 1500)
  MINDER_INJECTION_GUARD_IDLE_S     server exit after idle (default 1800)
  MINDER_INJECTION_GUARD_MODEL      classifier id or local path
  MINDER_INJECTION_GUARD_STUB       test-only stub spec (JSON)
"""
import itertools
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import minder
from .worker import MAX_DEADLINE_MS, DEFAULT_DEADLINE_MS
from .worker import _child_env, _dial, _dial_err, _readline

DEFAULT_IDLE_S = 1800.0
SPAWN_COOLDOWN_S = 30.0
# Single source for both halves of the socket pair.
PROTOCOL_VERSION = 1

GUARD_SHADOW = "shadow"
GUARD_MODES = ("off", "shadow")
EVENT_SHADOW = "injection_shadow"
VERDICTS = ("safe", "injection")
LOG_NAME = "injection-worker.log"
# The classifier sees a redacted, role-tagged tail of the conversation:
# injection payloads ride the untrusted content near the end. Per-message
# and total caps bound the socket payload; one huge tool result cannot
# erase the structure of everything before it.
PROMPT_MAX_CHARS = 12000
MESSAGE_MAX_CHARS = 4000


def guard_mode():
    """The configured guard mode, or None when off or misnamed."""
    mode = (os.environ.get("MINDER_INJECTION_GUARD") or "").strip().lower()
    return mode if mode in GUARD_MODES else None


def socket_path():
    env = os.environ.get("MINDER_INJECTION_GUARD_SOCK")
    if env:
        return Path(env)
    from .worker import _state_dir
    return _state_dir() / "injection-worker.sock"


def _redact(text):
    """Strip key-shaped strings before the classifier (or anyone) sees
    them; a missing core degrades to passthrough like the router's."""
    try:
        from minder_core.identity import redact
        return redact(text)
    except ImportError:
        return text


def guard_text(req):
    """The text the classifier sees: every string message role-tagged,
    redacted, truncated to the tail. Never the raw body, never structured
    (non-string) content."""
    parts = []
    for m in (req.get("messages") or []):
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content:
            continue
        parts.append(f"{m.get('role', '?')}: "
                     f"{_redact(content)[:MESSAGE_MAX_CHARS]}")
    return "\n".join(parts)[-PROMPT_MAX_CHARS:]


_IDS = itertools.count(1)

# One failed spawn puts the guard in cooldown: while the worker is down,
# per-request cost is one cheap liveness dial, never a spawn attempt.
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


def _spawn_worker(path):
    """Start the detached worker if no live one answers (flock-guarded,
    same discipline as the laya worker's spawn)."""
    import fcntl
    lock_path = path.with_name(path.name + ".spawn.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError:
            return
        try:
            conn, _errno = _dial_err(path, 0.25)
            if conn is not None:
                conn.close()
                return  # someone else won the race
            try:
                path.unlink()
            except OSError:
                pass
            log_path = path.with_name(LOG_NAME)
            log = open(log_path, "ab")
            try:
                subprocess.Popen(
                    [sys.executable, "-m", "minder_decision.injection_worker"],
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
    """Name a client-side failure once per reason per process in the
    worker's log file. Fail-open by contract (Law #2) — this line is the
    only trace an opinion-less request leaves. Never raises."""
    line = f"minder-injection-worker-client: {reason}"
    if detail:
        line += f": {detail}"
    from . import log_stamp
    line = log_stamp.stamp() + line
    if _CLIENT_LOGGED.get(reason):
        return
    _CLIENT_LOGGED[reason] = True
    try:
        log_path = Path(path).with_name(LOG_NAME)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as fh:
            fh.write((line + "\n").encode())
    except OSError:
        pass


def _opinion_from_dict(data):
    if not isinstance(data, dict):
        return None
    try:
        verdict = str(data.get("verdict"))
        if verdict not in VERDICTS:
            return None
        return {"verdict": verdict, "score": float(data.get("score") or 0.0),
                "model": str(data.get("model") or "unknown")}
    except (TypeError, ValueError):
        return None


class InjectionGuardClient:
    """One classifier opinion from the isolated worker. Returns None on
    any failure (no opinion, no event); never raises for ordinary
    inputs."""

    def __init__(self, deadline_ms=None, spawn=True, path=None):
        self._deadline_ms = deadline_ms
        self._spawn = spawn
        self._path = path

    def _deadline(self):
        if self._deadline_ms is not None:
            return max(50, min(MAX_DEADLINE_MS, self._deadline_ms)) / 1000.0
        try:
            ms = int(os.environ.get("MINDER_INJECTION_GUARD_DEADLINE_MS")
                     or DEFAULT_DEADLINE_MS)
        except ValueError:
            ms = DEFAULT_DEADLINE_MS
        return max(50, min(MAX_DEADLINE_MS, ms)) / 1000.0

    def _connect(self, deadline, path):
        if _in_cooldown():
            conn = _dial(path, 0.02)  # cheap liveness check only
            if conn is not None:
                _clear_cooldown()
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

    def observe(self, text):
        """One opinion: {"verdict", "score", "model"} or None."""
        try:
            return self._decide(text)
        except Exception as err:
            # Law #2: never raise at the caller. Name it in the log first.
            _client_fail(self._path or socket_path(), "client_error",
                         repr(err))
            return None

    def status(self):
        """What the worker can classify with right now: {"model": name} or
        {"error": reason}. Best effort - the one degraded state a liveness
        dial alone cannot see is a worker with no classifier."""
        try:
            return self._status()
        except Exception:
            return {"error": "status_error"}

    def _decide(self, text):
        deadline = self._deadline()
        path = self._path or socket_path()
        conn = self._connect(deadline, path)
        if conn is None:
            return None
        try:
            req = {"v": PROTOCOL_VERSION, "id": next(_IDS),
                   "deadline_ms": int(deadline * 1000), "text": text}
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
            out = _opinion_from_dict(reply.get("response"))
            if out is None:
                _client_fail(path, "bad_reply", "malformed opinion")
            return out
        except (OSError, ValueError) as err:
            _client_fail(path, "deadline" if isinstance(err, OSError)
                         else "bad_reply", repr(err))
            return None
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _status(self):
        path = self._path or socket_path()
        conn = _dial(path, 0.3)
        if conn is None:
            return {"error": "dial_refused"}
        try:
            req = {"v": PROTOCOL_VERSION, "id": next(_IDS), "status": True}
            conn.sendall((json.dumps(req) + "\n").encode())
            conn.settimeout(1.0)
            line = _readline(conn)
            if not line:
                return {"error": "no_reply"}
            reply = json.loads(line.decode("utf-8", "replace"))
            if not isinstance(reply, dict) or not reply.get("ok"):
                return {"error": str((reply or {}).get("error")
                                     or "bad_reply")[:200]}
            agent = reply.get("agent")
            if not isinstance(agent, dict):
                return {"error": "bad_reply"}
            return agent
        except (OSError, ValueError):
            return {"error": "deadline"}
        finally:
            try:
                conn.close()
            except OSError:
                pass


def get_injection_client():
    """The guard's worker client when MINDER_INJECTION_GUARD=shadow, else
    None (the whole guard is off and behaviour is byte-identical).
    Independent of MINDER_DECISION and MINDER_LAYA_WORKER, which govern
    the difficulty router."""
    if guard_mode() != GUARD_SHADOW:
        return None
    return InjectionGuardClient()


def observe(req, session_fp):
    """The proxy's seam (issue #65): one shadow opinion per proxied
    request. A silent no-op unless the flag says shadow. The request is
    never modified; a worker failure records nothing here — the client
    names it once in injection-worker.log and doctor reads it back."""
    if guard_mode() != GUARD_SHADOW:
        return
    try:
        _observe(req, session_fp)
    except Exception as exc:
        minder.log(session_fp, "injection_guard_error",
                   error=type(exc).__name__)


def _observe(req, session_fp):
    client = get_injection_client()
    if client is None:
        return
    text = guard_text(req)
    t0 = time.monotonic()
    out = client.observe(text)
    latency_ms = (time.monotonic() - t0) * 1000.0
    if out is None:
        return  # no opinion: the log holds the reason, doctor surfaces it
    _record(session_fp, out, latency_ms, len(text))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _record(session_fp, out, latency_ms, chars):
    """The shadow record: one ledger event (the contract) plus one row in
    the queryable injection_shadow table. The row is best effort — a
    store problem is named, never raised (Law #2)."""
    minder.log(session_fp, EVENT_SHADOW, verdict=out["verdict"],
               score=out["score"], latency_ms=round(latency_ms, 1),
               model=out.get("model") or "unknown", chars=chars)
    try:
        from minder_memory import db as _db
        conn = _db.connect()
        try:
            _db.write(
                conn,
                "INSERT INTO injection_shadow (id, ts, session_id,"
                " verdict, score, latency_ms, model, chars_classified)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"inj_{uuid.uuid4().hex[:12]}", _now(), session_fp,
                 out["verdict"], float(out["score"]), round(latency_ms, 1),
                 out.get("model"), int(chars)))
        finally:
            conn.close()
    except Exception as exc:
        minder.log(session_fp, "injection_shadow_log_failed",
                   error=type(exc).__name__)
