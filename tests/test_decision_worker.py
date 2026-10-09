"""Isolated laya decision worker (issue #7): protocol, deadline
enforcement, and fallback semantics.

Integration tests spawn the real worker subprocess with the test-only
stub agent (MINDER_LAYA_WORKER_STUB) so no laya install or network is
needed; the deadline and invalid-output tests point the client at a
local socket server that misbehaves on purpose.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from minder_decision.client import get_difficulty_client
from minder_decision.contracts import task_difficulty_contract
from minder_decision.difficulty import resolve_difficulty
from minder_decision import worker as worker_mod

CFG = {"laya_min_confidence": 0.7}


@pytest.fixture(autouse=True)
def _reset_worker_cooldown():
    """Each test starts with the client's spawn cooldown cleared, and with
    the once-per-process client-log marks cleared so each reason can be
    asserted as written.

    getattr, not a direct call: the proven-red gate runs this file against
    pre-change code, and a missing seam there would error out every test in
    the file and hide which assertions actually catch the bug."""
    reset = getattr(worker_mod, "_reset_client_log_marks", None)

    def _clear():
        worker_mod._COOLDOWN["until"] = 0.0
        if reset is not None:
            reset()

    _clear()
    yield
    _clear()


def _contract():
    return task_difficulty_contract()


# ---------------------------------------------------------------------------
# client selection (the proxy's seam)
# ---------------------------------------------------------------------------

def test_get_difficulty_client_gating(monkeypatch):
    monkeypatch.setenv("MINDER_LAYA_WORKER", "0")
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    assert get_difficulty_client() is None
    monkeypatch.setenv("MINDER_LAYA_WORKER", "1")
    monkeypatch.setenv("MINDER_DECISION", "off")
    assert get_difficulty_client() is None
    monkeypatch.setenv("MINDER_DECISION", "fake")
    from minder_decision.providers.fake import FakeClient
    assert isinstance(get_difficulty_client(), FakeClient)
    monkeypatch.setenv("MINDER_DECISION", "")
    assert isinstance(get_difficulty_client(),
                      worker_mod.WorkerDifficultyClient)


# ---------------------------------------------------------------------------
# real worker subprocess (stub agent, full IPC path)
# ---------------------------------------------------------------------------

def _spawn_worker(tmp_path, stub):
    sock = tmp_path / "laya-worker.sock"
    env = dict(os.environ)
    env["MINDER_LAYA_WORKER_SOCK"] = str(sock)
    env["MINDER_LAYA_WORKER_STUB"] = json.dumps(stub)
    env.pop("MINDER_DECISION", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "minder_decision.worker"],
        env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, sock


def test_worker_answers_difficulty_contract(tmp_path):
    proc, sock = _spawn_worker(
        tmp_path, {"difficulty": "routine", "difficulty_score": 1.2})
    try:
        client = worker_mod.WorkerDifficultyClient(spawn=False, path=sock)
        resp, waited = None, 0.0
        while resp is None and waited < 20.0:
            resp = client.system_one({"task": "refactor the module"},
                                     _contract().questions,
                                     contract=_contract())
            if resp is None:
                time.sleep(0.1)
                waited += 0.1
        assert resp is not None
        resolved = resolve_difficulty(resp, _contract(), CFG)
        assert resolved is not None
        label, band = resolved
        assert label == "routine"
        assert band["effort"] == "medium"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_client_spawns_worker_lazily(tmp_path, monkeypatch):
    sock = tmp_path / "lazy.sock"
    monkeypatch.setenv("MINDER_LAYA_WORKER_SOCK", str(sock))
    monkeypatch.setenv("MINDER_LAYA_WORKER_STUB", json.dumps(
        {"difficulty": "complex", "difficulty_score": 2.1}))
    client = worker_mod.WorkerDifficultyClient()
    resp, waited = None, 0.0
    while resp is None and waited < 20.0:
        resp = client.system_one({"task": "migrate the schema"},
                                 _contract().questions,
                                 contract=_contract())
        if resp is None:
            time.sleep(0.1)
            waited += 0.1
    assert resp is not None
    label, band = resolve_difficulty(resp, _contract(), CFG)
    assert label == "complex"
    assert band["effort"] == "high"
    # the lazily spawned worker is detached — reap it so the test does
    # not leave a model process behind for the idle budget (SIGTERM
    # leaves the socket file behind; a refused dial proves it is gone)
    subprocess.run(["pkill", "-f", "minder_decision.worker"], check=False)
    waited = 0.0
    while waited < 10.0:
        if worker_mod._dial(sock, 0.1) is None:
            break
        time.sleep(0.2)
        waited += 0.2
    assert worker_mod._dial(sock, 0.1) is None


def test_worker_exits_when_idle(tmp_path):
    """The worker is bounded: with no request inside its idle budget it
    unlinks its socket and exits (a later client respawns it)."""
    sock = tmp_path / "idle.sock"
    env = dict(os.environ)
    env["MINDER_LAYA_WORKER_SOCK"] = str(sock)
    env["MINDER_LAYA_WORKER_STUB"] = json.dumps(
        {"difficulty": "routine", "difficulty_score": 1.0})
    env["MINDER_LAYA_WORKER_IDLE_S"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "minder_decision.worker"],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        waited = 0.0
        while waited < 15.0:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
            waited += 0.2
        assert proc.poll() == 0
        assert not sock.exists()
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# deadline + invalid output (misbehaving in-test socket server)
# ---------------------------------------------------------------------------

def _bad_server(tmp_path, reply, delay=0.0):
    sock_path = tmp_path / "bad.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(2)

    def _run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.recv(65536)
                if delay:
                    time.sleep(delay)
                conn.sendall(reply)
            except OSError:
                pass
            finally:
                conn.close()

    threading.Thread(target=_run, daemon=True).start()
    return sock_path


def test_deadline_enforced_client_side(tmp_path):
    """A wedged worker must never block model traffic: the client gives
    up at its deadline and the router falls back deterministically."""
    sock = _bad_server(tmp_path, b'{"v":1,"ok":true}\n', delay=5.0)
    client = worker_mod.WorkerDifficultyClient(deadline_ms=300,
                                               spawn=False, path=sock)
    t0 = time.monotonic()
    resp = client.system_one({"task": "x"}, _contract().questions,
                             contract=_contract())
    elapsed = time.monotonic() - t0
    assert resp is None
    assert elapsed < 3.0


def test_invalid_output_falls_back(tmp_path):
    """A response from the wrong contract fails validation downstream —
    the router sees no opinion and the scheduler takes over."""
    sock = _bad_server(tmp_path, (json.dumps(
        {"v": 1, "ok": True, "response": {
            "contract_id": "not-the-difficulty-contract",
            "contract_version": 1,
            "choice_probs": {"difficulty": {"mechanical": 1.0}},
            "confidence": 0.99}}) + "\n").encode())
    client = worker_mod.WorkerDifficultyClient(spawn=False, path=sock)
    resp = client.system_one({"task": "x"}, _contract().questions,
                             contract=_contract())
    assert resp is not None  # transport delivered it; the gate rejects it
    assert resolve_difficulty(resp, _contract(), CFG) is None


def test_garbage_reply_is_none(tmp_path):
    sock = _bad_server(tmp_path, b"not json at all\n")
    client = worker_mod.WorkerDifficultyClient(spawn=False, path=sock)
    assert client.system_one({"task": "x"}, _contract().questions,
                             contract=_contract()) is None


def test_no_worker_is_none_and_quick(tmp_path):
    client = worker_mod.WorkerDifficultyClient(spawn=False,
                                               path=tmp_path / "absent.sock")
    t0 = time.monotonic()
    assert client.system_one({"task": "x"}, _contract().questions,
                             contract=_contract()) is None
    assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# issue #28: a protocol mismatch must be answerable, not a dropped socket.
# The live laya-worker.log holds two bare "unsupported protocol version"
# lines with no version and no client-side record of what happened.
# ---------------------------------------------------------------------------

def test_protocol_mismatch_gets_an_error_reply(tmp_path):
    """A pre-v1 client gets ok:false naming the version it sent, so both
    halves of the pair are diagnosable instead of one seeing a closed
    connection."""
    proc, sock = _spawn_worker(
        tmp_path, {"difficulty": "routine", "difficulty_score": 1.0})
    try:
        conn = None
        waited = 0.0
        while conn is None and waited < 20.0:
            conn = worker_mod._dial(sock, 0.5)
            if conn is None:
                time.sleep(0.1)
                waited += 0.1
        assert conn is not None, "worker never bound its socket"
        try:
            conn.sendall((json.dumps({"v": 0, "id": 7,
                                      "state": {"task": "x"}}) + "\n")
                         .encode())
            conn.settimeout(5.0)
            line = worker_mod._readline(conn)
        finally:
            conn.close()
        assert line, "the worker dropped the connection with no reply"
        reply = json.loads(line.decode("utf-8", "replace"))
        assert reply.get("ok") is False
        assert "unsupported protocol version" in str(reply.get("error"))
        assert "0" in str(reply.get("error"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_worker_error_reaches_the_client_log(tmp_path):
    """Every client-side failure names itself in laya-worker.log; before this
    change system_one returned None and the operator had no record at all.

    The interesting case is a worker that is up but answers ok:false — the
    live install's laya-worker.log holds two bare "unsupported protocol
    version" lines and the client recorded nothing about any of them. The
    client is pointed at a socket held by a server that answers ok:false, so
    no real worker is needed and the answer is deterministic."""
    sock = tmp_path / "laya-worker.sock"
    log_path = sock.with_name("laya-worker.log")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock))
    srv.listen(4)

    def _serve_once():
        conn = None
        try:
            conn, _ = srv.accept()
            conn.recv(65536)  # the request, whose content does not matter
            conn.sendall((json.dumps(
                {"ok": False,
                 "error": "ValueError('unsupported protocol version')"})
                + "\n").encode())
        except OSError:
            pass
        finally:
            if conn is not None:
                conn.close()

    serve = threading.Thread(target=_serve_once, daemon=True)
    serve.start()
    client = worker_mod.WorkerDifficultyClient(spawn=False, path=sock)
    assert client.system_one({"task": "x"}, _contract().questions,
                             contract=_contract()) is None
    serve.join(timeout=10)
    srv.close()
    assert log_path.exists(), "a client that got nothing wrote no record"
    log = log_path.read_text()
    assert "worker_error" in log, (
        "the worker's ok:false answer left no client-side record")
    assert "unsupported protocol version" in log, (
        "the worker's own error text never reached the client log")
