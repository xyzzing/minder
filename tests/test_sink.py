"""The sink sidecar and its client: the sandboxed hook's write path.

Regression target (2026-09-25): a confined hook cannot write the state
directory, every hook write is fail-open, and nothing compared hook
invocations with persisted records — three days of capture was lost with
all-green pages. These tests pin the contract that replaced it.
"""
import json
import threading
import urllib.request

import pytest

import sink as sink_mod
from minder_memory import sink as client


class _Server:
    """Run the real sink handler on an ephemeral loopback port."""

    def __init__(self, state_dir, monkeypatch):
        from http.server import ThreadingHTTPServer
        # minder.STATE_DIR is a module constant bound at import time, so the
        # env alone would not move it — patch the attribute the sink reads.
        monkeypatch.setattr(sink_mod.minder, "STATE_DIR", state_dir)
        monkeypatch.setenv("MINDER_SINK_URL", "http://127.0.0.1:0")
        self.state_dir = state_dir
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), sink_mod.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, path, payload, auth="auto"):
        headers = {"Content-Type": "application/json"}
        if auth == "auto":  # attach the state dir's token like the client does
            token_file = self.state_dir / "sink.token"
            if token_file.exists():
                headers["Authorization"] = \
                    f"Bearer {token_file.read_text().strip()}"
        elif auth:
            headers["Authorization"] = auth
        req = urllib.request.Request(
            self.url + path, data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:  # 4xx/5xx carry a body
            return exc.code, json.loads(exc.read())

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r:
            return r.status, json.loads(r.read())

    def get_text(self, path):
        with urllib.request.urlopen(self.url + path, timeout=5) as r:
            return r.status, r.read().decode(), r.headers["Content-Type"]


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(sink_mod, "_STATS", {
        "started_ts": sink_mod.time.time(), "ops": {}, "errors": [],
        "last_persist_ts": None})
    monkeypatch.setattr(sink_mod, "_AUTH", {"token": None})
    # patch STATE_DIR *before* ensure_token, or the token lands in the
    # operator's real state dir
    state = tmp_path / "state"
    monkeypatch.setattr(sink_mod.minder, "STATE_DIR", state)
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    token_state = sink_mod.ensure_token()
    srv = _Server(state, monkeypatch)
    srv.token_state = token_state
    try:
        yield srv
    finally:
        srv.stop()


def test_healthz_and_stats_report_state_dir(server):
    status, body = server.get("/healthz")
    assert status == 200 and body["status"] == "ok"
    assert body["state_dir"] == str(server.state_dir)
    status, body = server.get("/stats")
    assert status == 200 and body["ops"] == {}
    assert "warm" in body


def test_root_is_a_pointer_not_raw_json(server):
    """Typing the port into a browser must not look like a broken UI
    (2026-09-25: "the web ui at 127.0.0.1:8392 has disappeared")."""
    status, raw, ctype = server.get_text("/")
    assert status == 200 and ctype.startswith("text/plain")
    assert "Not a UI" in raw
    assert "http://127.0.0.1:8765" in raw          # where the UI actually is
    assert "minder-op capture" in raw
    # the machine contract is unchanged
    status, body = server.get("/healthz")
    assert status == 200 and body["status"] == "ok"


def test_append_jsonl_writes_the_ledger(server):
    status, body = server.post("/persist", {
        "op": "append_jsonl", "name": "events.jsonl",
        "record": {"ts": 1, "task": "t", "event": "smoke"}})
    assert status == 200 and body["ok"] is True
    lines = (server.state_dir / "events.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["event"] == "smoke"


def test_append_jsonl_is_append_only(server):
    for i in range(3):
        server.post("/persist", {"op": "append_jsonl", "name": "events.jsonl",
                                 "record": {"n": i}})
    lines = (server.state_dir / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["n"] for line in lines] == [0, 1, 2]


@pytest.mark.parametrize("name", ["../../evil", "/etc/passwd",
                                  "memory.sqlite", "other.jsonl", ""])
def test_append_jsonl_rejects_anything_but_the_known_ledgers(server, name):
    status, body = server.post("/persist", {
        "op": "append_jsonl", "name": name, "record": {}})
    assert status in (400, 500) and body["ok"] is False
    # nothing was created inside the state dir by rejection (the auth
    # token is the one legitimate file), and no traversal target outside
    # it was touched
    leftovers = [p for p in server.state_dir.glob("**/*")
                 if p.name != "sink.token"]
    assert leftovers == []


def test_unknown_op_is_rejected_and_counted(server):
    status, body = server.post("/persist", {"op": "wipe"})
    assert status == 400 and body["ok"] is False
    _, stats = server.get("/stats")
    assert stats["ops"]["wipe"]["failed"] == 1
    assert stats["errors"][0]["op"] == "wipe"


def test_write_state_uses_minders_own_naming(server):
    import minder
    status, _ = server.post("/persist", {"op": "write_state",
                                         "task": "session-x",
                                         "text": '{"turn": 3}'})
    assert status == 200
    path = minder._state_path("session-x")
    assert json.loads(path.read_text())["turn"] == 3


def test_record_op_runs_the_memory_pipeline(server, monkeypatch):
    monkeypatch.setenv("MINDER_SINK_URL", server.url)
    status, body = server.post("/persist", {
        "op": "record",
        "hook_event": {"hook_event_name": "PostToolUse", "tool_name": "bash",
                       "session_id": "s1", "cwd": "/repo",
                       "tool_input": {"command": "pytest"},
                       "tool_response": "Traceback\n[exit code: 1]"}})
    assert status == 200 and body["ok"] is True
    assert body["result"]["status"]["recorded"] is True
    assert body["result"]["status"]["episode_id"]


def test_bad_body_and_bad_json_are_rejected(server):
    # authenticated (auth failures are checked first; this pins the body
    # validation behind them)
    token = (server.state_dir / "sink.token").read_text().strip()
    req = urllib.request.Request(server.url + "/persist", data=b"x" * 4,
                                 headers={"Authorization": f"Bearer {token}"},
                                 method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400


def test_client_is_inert_without_the_env(monkeypatch):
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    assert client.enabled() is False
    assert client.sink_url() is None
    # every wrapper stays falsy and never raises
    assert client.append_jsonl("events.jsonl", {"a": 1}) is False
    assert client.write_state("t", "{}") is False
    assert client.record({}) is None
    assert client.policy({}, {}) is None
    assert client.stats() is None


def test_client_fails_open_when_the_sink_is_down(monkeypatch):
    monkeypatch.setenv("MINDER_SINK_URL", "http://127.0.0.1:1")  # closed port
    monkeypatch.setenv("MINDER_SINK_TIMEOUT_MS", "200")
    assert client.enabled() is True
    assert client.append_jsonl("events.jsonl", {"a": 1}) is False
    assert client.record({"tool_name": "bash"}) is None
    assert client.stats(timeout_ms=200) is None


def test_client_round_trips_through_the_server(server, monkeypatch):
    monkeypatch.setenv("MINDER_SINK_URL", server.url)
    assert client.append_jsonl("hook-trace.jsonl",
                               {"hook_event_name": "PostToolUse"}) is True
    assert client.write_state("task-1", '{"turn": 1}') is True
    stats = client.stats()
    assert stats and stats["ops"]["append_jsonl"]["ok"] == 1
    trace = (server.state_dir / "hook-trace.jsonl").read_text()
    assert "PostToolUse" in trace


def test_sink_refuses_a_non_loopback_bind(monkeypatch, capsys):
    assert sink_mod.main(["--host", "0.0.0.0", "--port", "1"]) == 1
    assert "loopback" in capsys.readouterr().err


def test_loopback_hosts_match_the_console_rule():
    from minder_web.__main__ import LOOPBACK_HOSTS as console_hosts
    assert sink_mod.LOOPBACK_HOSTS == console_hosts


# --------------------------------------------------------------------------
# URL discovery: the deployment fact lives in hooks.json, not in the
# observer's environment. Without this, a correctly wired install reported
# "sink not configured" to every operator surface.
# --------------------------------------------------------------------------

def test_declared_url_is_read_from_hooks_json(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [
        {"hooks": [{"command": "MINDER_SINK_URL=http://127.0.0.1:8392 "
                                "python3 hook.py --transport dsh"}]}]}}))
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    assert client.declared_sink_url() == "http://127.0.0.1:8392"
    assert client.sink_url() == "http://127.0.0.1:8392"
    assert client.sink_source() == "hooks.json"
    assert client.enabled() is True


def test_env_wins_over_the_declared_url(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks.json"
    hooks.write_text("MINDER_SINK_URL=http://127.0.0.1:9999")
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    monkeypatch.setenv("MINDER_SINK_URL", "http://127.0.0.1:1234")
    assert client.sink_url() == "http://127.0.0.1:1234"
    assert client.sink_source() == "env"


def test_undeclared_or_template_url_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "absent.json"))
    assert client.declared_sink_url() is None
    assert client.sink_url() is None
    assert client.sink_source() is None
    # the shipped template still has the placeholder — never a URL
    template = tmp_path / "hooks.json"
    template.write_text("MINDER_SINK_URL=__MINDER_SINK_URL__ python3 hook.py")
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(template))
    assert client.declared_sink_url() is None


def test_declared_url_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    import os
    hooks = tmp_path / "hooks.json"
    hooks.write_text("MINDER_SINK_URL=http://127.0.0.1:1111")
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    assert client.declared_sink_url() == "http://127.0.0.1:1111"
    hooks.write_text("MINDER_SINK_URL=http://127.0.0.1:2222")
    os.utime(hooks, (1, 1))
    assert client.declared_sink_url() == "http://127.0.0.1:2222"


def test_declared_flags_are_parsed_from_the_hook_command(tmp_path,
                                                         monkeypatch):
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [{"hooks": [
        {"command": "MINDER_HOOK_TRACE=1 MINDER_ASSIST=decision_skill "
                    "MINDER_CLASSIFIER=shadow MINDER_DECISION=laya "
                    "MINDER_SUCCESS_GUARD=advisory "
                    "MINDER_SINK_URL=http://127.0.0.1:8392 python3 hook.py"}
    ]}]}}))
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    flags = client.declared_flags()
    assert flags["MINDER_ASSIST"] == "decision_skill"
    assert flags["MINDER_DECISION"] == "laya"
    assert flags["MINDER_CLASSIFIER"] == "shadow"
    assert flags["MINDER_SUCCESS_GUARD"] == "advisory"
    assert flags["MINDER_SINK_URL"] == "http://127.0.0.1:8392"
    assert client.declared_flags(None) == flags  # cached, same answer


def test_sink_adopts_hook_flags_but_never_overrides_its_own(tmp_path,
                                                            monkeypatch):
    """The policy pass runs in the sink, so a flag declared only in the hook
    command must reach it — without overriding an explicit environment."""
    hooks = tmp_path / "hooks.json"
    hooks.write_text("MINDER_ASSIST=decision_skill MINDER_DECISION=laya "
                     "MINDER_SINK_URL=http://127.0.0.1:1 python3 hook.py")
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))
    monkeypatch.delenv("MINDER_ASSIST", raising=False)
    monkeypatch.setenv("MINDER_DECISION", "shadow")  # explicit env wins
    adopted = sink_mod.adopt_hook_flags()
    import os as _os
    assert _os.environ["MINDER_ASSIST"] == "decision_skill"
    assert _os.environ["MINDER_DECISION"] == "shadow"
    assert adopted == {"MINDER_ASSIST": "decision_skill"}
    # our own address is not a policy flag
    assert "MINDER_SINK_URL" not in _os.environ or \
        _os.environ["MINDER_SINK_URL"] != "http://127.0.0.1:1"
    flags = sink_mod._effective_flags()
    assert flags["MINDER_ASSIST"] == "decision_skill"
    assert flags["MINDER_DECISION"] == "shadow"


def test_writes_without_the_token_are_refused(server):
    status, body = server.post("/persist",
                               {"op": "append_jsonl", "name": "events.jsonl",
                                "record": {"a": 1}}, auth=None)
    assert status == 401 and body["ok"] is False
    status, body = server.post("/persist",
                               {"op": "append_jsonl", "name": "events.jsonl",
                                "record": {"a": 1}}, auth="Bearer wrong")
    assert status == 401
    assert not (server.state_dir / "events.jsonl").exists()


def test_reads_stay_open_but_writes_need_the_token(server):
    assert server.get("/healthz")[0] == 200
    assert server.get("/stats")[0] == 200
    assert server.post("/persist", {"op": "append_jsonl",
                                    "name": "events.jsonl",
                                    "record": {"a": 1}})[0] == 200


def test_token_file_is_created_operator_private(server):
    import os
    import stat
    token_file = server.state_dir / "sink.token"
    assert token_file.exists()
    mode = stat.S_IMODE(os.stat(token_file).st_mode)
    assert mode == 0o600, oct(mode)
    assert len(token_file.read_text().strip()) >= 32


def test_without_a_token_file_writes_stay_open(tmp_path, monkeypatch):
    """Escape hatch: no token in force at request time → the sink serves
    writes unauthenticated rather than half-blocking the capture path.
    (This server never runs ensure_token, so _AUTH stays empty.)"""
    monkeypatch.setattr(sink_mod, "_STATS", {
        "started_ts": sink_mod.time.time(), "ops": {}, "errors": [],
        "last_persist_ts": None})
    monkeypatch.setattr(sink_mod, "_AUTH", {"token": None})
    monkeypatch.delenv("MINDER_STATE_DIR", raising=False)
    srv = _Server(tmp_path / "state2", monkeypatch)
    try:
        status, _ = srv.post("/persist", {"op": "append_jsonl",
                                          "name": "events.jsonl",
                                          "record": {"open": True}},
                             auth=None)
        assert status == 200
    finally:
        srv.stop()
