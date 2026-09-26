"""Hook transport tests (AT-12: zcode directive contract + dsh bridge)."""
import json
import os
import subprocess
import sys
from pathlib import Path

# Subprocess spawn time is load-sensitive (a busy llama-server starves spawns);
# raise under load instead of flaking.
SUBPROC_TIMEOUT = int(os.environ.get("MINDER_TEST_TIMEOUT", "60"))

import minder

HOOK = str(Path(__file__).resolve().parent.parent / "hook.py")


def run_hook(event, transport, tmp_path, caps=None, frontier_cmd=None,
             extra_args=(), env_extra=None):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "MINDER_STATE_DIR": str(tmp_path / "state"),
        "MINDER_CONFIG": str(tmp_path / "minder.json"),
        "MINDER_CAPS": str(tmp_path / "caps.json"),
        "HOME": str(tmp_path),
    }
    if frontier_cmd:
        env["MINDER_FRONTIER_CMD"] = frontier_cmd
    env.update(env_extra or {})
    if caps is not None:
        (tmp_path / "caps.json").write_text(json.dumps(caps))
    proc = subprocess.run(
        [sys.executable, HOOK, "--transport", transport, *extra_args],
        input=json.dumps(event), capture_output=True, text=True, env=env,
        timeout=SUBPROC_TIMEOUT)
    return proc


FAIL_EVENT = {"session_id": "hook-s1", "hook_event_name": "PostToolUse",
              "tool_name": "Edit",
              "tool_input": {"file_path": "/x/a.py"},
              "tool_response": "old_string not found"}


def test_zcode_first_failure_passes_silently(tmp_path):
    proc = run_hook(FAIL_EVENT, "zcode", tmp_path)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_zcode_escalation_blocks_with_digest_exit0(tmp_path):
    cfg = {"fail_threshold": 1, "cooldown_turns": 99}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    proc = run_hook(FAIL_EVENT, "zcode", tmp_path)
    assert proc.returncode == 0
    directive = json.loads(proc.stdout)
    assert directive["decision"] == "block"
    assert directive["reason"].startswith("[minder] ESCALATION L1")


def test_dsh_transport_blocks_via_exit2_stderr(tmp_path):
    cfg = {"fail_threshold": 1, "cooldown_turns": 99}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    proc = run_hook(FAIL_EVENT, "dsh", tmp_path)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("[minder] ESCALATION L1")
    assert "old_string not found" in proc.stderr  # err digest included


def test_dsh_non_block_silent_exit0(tmp_path):
    ok_event = dict(FAIL_EVENT, tool_response="patched cleanly")
    proc = run_hook(ok_event, "dsh", tmp_path)
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_hook_never_crashes_on_garbage(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "MINDER_STATE_DIR": str(tmp_path / "s"),
           "HOME": str(tmp_path)}
    for bad in ("", "not json", "[1,2,3]"):
        proc = subprocess.run([sys.executable, HOOK], input=bad,
                              capture_output=True, text=True, env=env,
                              timeout=SUBPROC_TIMEOUT)
        assert proc.returncode == 0


def test_session_start_snapshots_caps(tmp_path):
    (tmp_path / "caps.json").write_text(json.dumps(
        {"thinking": {"mechanism": "kwargs"}}))
    proc = run_hook({"session_id": "ss", "hook_event_name": "SessionStart"},
                    "zcode", tmp_path, extra_args=("--session-start",))
    assert proc.returncode == 0
    files = list((tmp_path / "state").glob("*.json"))
    assert files and json.loads(files[0].read_text())[
        "caps_mechanism"] == "kwargs"


def test_frontier_command_runs_and_appends(tmp_path):
    cfg = {"fail_threshold": 1, "cooldown_turns": 0, "think_budget": 1,
           "frontier_budget": 1}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    ev = dict(FAIL_EVENT)
    # L1 first…
    run_hook(ev, "dsh", tmp_path)
    # …then a different failing key to hit L2 quickly
    ev2 = dict(FAIL_EVENT, tool_input={"file_path": "/x/b.py"})
    proc = run_hook(ev2, "dsh", tmp_path,
                    frontier_cmd="cat")
    assert proc.returncode == 2
    assert "ESCALATION L2" in proc.stderr
    assert "FRONTIER RESPONSE" in proc.stderr
    # consult trail written (audit seam)
    consults = tmp_path / "state" / "consults.jsonl"
    assert consults.exists()
    rec = json.loads(consults.read_text().splitlines()[-1])
    assert rec["key"] == "edit:/x/b.py" and rec["attempts"] == 1
    assert "old_string not found" in rec["error"]


def test_frontier_unavailable_degrades_digest(tmp_path):
    cfg = {"fail_threshold": 1, "cooldown_turns": 0, "think_budget": 1,
           "frontier_budget": 1}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    run_hook(dict(FAIL_EVENT), "dsh", tmp_path)
    proc = run_hook(dict(FAIL_EVENT, tool_input={"file_path": "/x/c.py"}),
                    "dsh", tmp_path)
    assert proc.returncode == 2
    assert "no frontier command configured" in proc.stderr


def test_frontier_command_from_config_not_env(tmp_path):
    """C1: frontier_command in minder.json is honored when the env var is
    absent — the installer/hook gap closed."""
    cfg = {"fail_threshold": 1, "cooldown_turns": 0, "think_budget": 1,
           "frontier_budget": 1, "frontier_command": "cat"}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    run_hook(dict(FAIL_EVENT), "dsh", tmp_path)  # L1 first
    ev2 = dict(FAIL_EVENT, tool_input={"file_path": "/x/b.py"})
    proc = run_hook(ev2, "dsh", tmp_path)  # no frontier_cmd → config path
    assert proc.returncode == 2
    assert "ESCALATION L2" in proc.stderr
    assert "FRONTIER RESPONSE" in proc.stderr
    assert (tmp_path / "state" / "consults.jsonl").exists()


def test_session_start_compact_emits_failure_brief(tmp_path):
    """G1: compaction survives — failure memory re-enters context."""
    cfg = {"fail_threshold": 1, "cooldown_turns": 99}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    run_hook(FAIL_EVENT, "zcode", tmp_path)  # seed one failure
    proc = run_hook({"session_id": "hook-s1",
                     "hook_event_name": "SessionStart",
                     "source": "compact"},
                    "zcode", tmp_path, extra_args=("--session-start",))
    assert proc.returncode == 0
    directive = json.loads(proc.stdout)
    ctx = directive.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "edit:/x/a.py" in ctx
    assert "failed 1x" in ctx


def test_session_start_compact_brief_carries_escalation_marker(tmp_path):
    cfg = {"fail_threshold": 1, "cooldown_turns": 99}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    run_hook(FAIL_EVENT, "zcode", tmp_path)   # n=1
    run_hook(FAIL_EVENT, "zcode", tmp_path)   # n=2 -> L1
    proc = run_hook({"session_id": "hook-s1",
                     "hook_event_name": "SessionStart",
                     "source": "compact"},
                    "zcode", tmp_path, extra_args=("--session-start",))
    ctx = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "[minder] ESCALATION L1" in ctx  # re-arms the proxy channel


def test_session_start_plain_startup_no_brief(tmp_path):
    proc = run_hook({"session_id": "fresh",
                     "hook_event_name": "SessionStart",
                     "source": "startup"},
                    "zcode", tmp_path, extra_args=("--session-start",))
    assert json.loads(proc.stdout) == {}


def test_minder_env_loader_activates_flags(tmp_path):
    """Operator flags in ~/.config/minder/minder.env (KEY=VALUE) are loaded
    into os.environ at hook startup, fail-open. Observable here: with
    MINDER_SUCCESS_GUARD=advisory from the file, a tool_success event
    records a success observation; without the file, nothing is recorded
    (byte-inert)."""
    import sqlite3
    # The loader's default path is ~/.config/minder/minder.env; run_hook
    # sets HOME=tmp_path, so create it there.
    envdir = tmp_path / ".config" / "minder"
    envdir.mkdir(parents=True)
    (envdir / "minder.env").write_text(
        "MINDER_SUCCESS_GUARD=advisory\n")
    ok_event = {"session_id": "env-s1", "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "curl -o x.pdf URL"},
                "tool_response": "  100 181.2k  0 181.2k  1.24M"}
    proc = run_hook(ok_event, "dsh", tmp_path)
    assert proc.returncode == 0
    dbp = tmp_path / "state" / "memory.sqlite"
    conn = sqlite3.connect(dbp)
    try:
        conn.row_factory = sqlite3.Row
        n = conn.execute("SELECT COUNT(*) FROM success_observations"
                         ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, "flag from minder.env must activate the success guard"


def test_minder_env_loader_absent_is_inert(tmp_path):
    """No minder.env file -> flags stay unset -> success guard byte-inert."""
    import sqlite3
    ok_event = {"session_id": "env-s2", "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "curl -o x.pdf URL"},
                "tool_response": "  100 181.2k  0 181.2k  1.24M"}
    proc = run_hook(ok_event, "dsh", tmp_path)
    assert proc.returncode == 0
    dbp = tmp_path / "state" / "memory.sqlite"
    conn = sqlite3.connect(dbp)
    try:
        conn.row_factory = sqlite3.Row
        n = conn.execute("SELECT COUNT(*) FROM success_observations"
                         ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0, "without minder.env the success guard must stay inert"


def test_verify_consult_fires_on_frontier_deescalate(tmp_path):
    """G3: L2 key resolves -> one verify consult recorded."""
    cfg = {"fail_threshold": 1, "cooldown_turns": 0, "think_budget": 1,
           "frontier_budget": 1, "verify_consult": True,
           "frontier_command": "cat"}
    (tmp_path / "minder.json").write_text(json.dumps(cfg))
    ev = dict(FAIL_EVENT)
    run_hook(ev, "dsh", tmp_path)                      # L1
    ev2 = dict(FAIL_EVENT, tool_input={"file_path": "/x/b.py"})
    run_hook(ev2, "dsh", tmp_path)                     # L2
    ok = run_hook(dict(FAIL_EVENT, tool_input={"file_path": "/x/b.py"},
                       tool_response="patched cleanly"), "dsh", tmp_path)
    assert ok.returncode == 0
    consults = (tmp_path / "state" / "consults.jsonl").read_text().splitlines()
    verify = [json.loads(l) for l in consults
              if json.loads(l)["response"].startswith("VERIFY")]
    assert verify, consults


# --- success-loop guard: delivery channels and the pre-emptive stop -------
#
# The live DBS incident: the identical curl succeeded 20+ times, the guard
# detected it, and nothing reached the model — the advisory was written to
# stderr on a successful exit, which the bridge only keeps as a bounded,
# log-only stderrSummary. These tests pin the two real channels.

# Verbose-volatile curl progress output: differs only in numbers, so all
# three normalize to ONE result signature (the incident's own property).
CURL_1 = "  0 181.2k  0  0  1.24M --:--:-- 100"
CURL_2 = "  0 181.2k  0  0  905.5k --:--:-- 100"
CURL_3 = "  0 181.2k  0  0 573.6k --:--:-- 100"


def _loop_payload(response="", event="PostToolUse"):
    return {"session_id": "hook-s1", "hook_event_name": event,
            "tool_name": "bash",
            "tool_input": {"command": "curl -L -o dbs.pdf URL"},
            "tool_response": response}


def _seed_loop(tmp_path, payloads, name="seed.sqlite"):
    """Pre-seed the guard ledger through the REAL production path
    (from_hook.record with a raw dsh payload, not success_guard.observe
    directly) and return the db path.

    Going through record() matters: the guard's result text is read from
    the *canonical* event, where to_event maps a raw payload's
    `tool_response` onto `error_excerpt`. Seeding around that path is how
    the empty-signature bug stayed invisible while the unit tests passed.
    """
    from unittest import mock

    from memory import db as _db, from_hook
    dbp = tmp_path / name
    _db.connect(dbp).close()
    with mock.patch.dict(os.environ, {"MINDER_SUCCESS_GUARD": "advisory"}):
        for payload in payloads:
            from_hook.record(_loop_payload(payload), db_path=dbp)
    return dbp


def test_pretool_blocks_a_looping_action(tmp_path):
    """block mode: the repeat of an action that already looped is stopped
    BEFORE it runs, with the directive as the reason."""
    dbp = _seed_loop(tmp_path, (CURL_1, CURL_2, CURL_3))
    proc = run_hook(_loop_payload(), "dsh", tmp_path,
                    extra_args=("--pre-tool",),
                    env_extra={"MINDER_SUCCESS_GUARD": "block",
                               "MINDER_MEMORY_DB": str(dbp)})
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert "EXECUTION PAUSED" in proc.stderr
    assert "Do not repeat this action" in proc.stderr
    assert "Change at least one causal element" in proc.stderr


def test_pretool_is_inert_when_mode_off_or_advisory(tmp_path):
    """Only `block` stops anything; off and advisory both let the call run.
    A misconfigured or absent flag must never start blocking."""
    dbp = _seed_loop(tmp_path, (CURL_1, CURL_2, CURL_3))
    for mode in (None, "advisory", "typo"):
        env = {"MINDER_MEMORY_DB": str(dbp)}
        if mode is not None:
            env["MINDER_SUCCESS_GUARD"] = mode
        proc = run_hook(_loop_payload(), "dsh", tmp_path,
                        extra_args=("--pre-tool",), env_extra=env)
        assert proc.returncode == 0, (mode, proc.stderr)
        assert proc.stdout == ""


def test_pretool_does_not_record_a_bogus_observation(tmp_path):
    """A pre-tool payload has no result. Recording it would insert a
    zero-output row and corrupt the very counts the stop relies on."""
    from memory import db as _db
    dbp = _seed_loop(tmp_path, (CURL_1,))

    def rows():
        conn = _db.connect(dbp)
        try:
            return conn.execute("SELECT COUNT(*) FROM success_observations"
                                ).fetchone()[0]
        finally:
            conn.close()

    before = rows()
    proc = run_hook(_loop_payload(), "dsh", tmp_path,
                    extra_args=("--pre-tool",),
                    env_extra={"MINDER_SUCCESS_GUARD": "block",
                               "MINDER_MEMORY_DB": str(dbp)})
    assert proc.returncode == 0  # below threshold: no stop
    assert rows() == before, "PreToolUse must not write observations"


def test_advisory_rides_additional_context_not_stderr(tmp_path):
    """advisory mode: the note goes to exit-0 stdout as
    hookSpecificOutput.additionalContext (the channel the bridge injects
    into the next request) and NEVER blocks."""
    dbp = _seed_loop(tmp_path, (CURL_1, CURL_2))
    proc = run_hook(_loop_payload(CURL_1), "dsh", tmp_path,
                    env_extra={"MINDER_SUCCESS_GUARD": "advisory",
                               "MINDER_MEMORY_DB": str(dbp)})
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert proc.stderr == ""
    payload = json.loads(proc.stdout)
    assert "decision" not in payload, payload  # advisory never blocks
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "success-loop" in ctx and "3 times" in ctx


def test_block_mode_directive_blocks_the_posttool_repeat(tmp_path):
    """block mode after the fact: a repeat that already ran comes back as
    a block whose reason is the structured directive."""
    dbp = _seed_loop(tmp_path, (CURL_1, CURL_2))
    proc = run_hook(_loop_payload(CURL_1), "dsh", tmp_path,
                    env_extra={"MINDER_SUCCESS_GUARD": "block",
                               "MINDER_MEMORY_DB": str(dbp)})
    assert proc.returncode == 2, (proc.returncode, proc.stdout)
    assert "EXECUTION PAUSED" in proc.stderr
    assert "Do not repeat this action" in proc.stderr


def test_flag_off_is_byte_inert_for_a_looping_session(tmp_path):
    """The default-off path must stay exactly as it was: exit 0, no
    stdout, no stderr, and not one observation written."""
    from memory import db as _db
    off = tmp_path / "off.sqlite"
    proc = run_hook(_loop_payload(CURL_1), "dsh", tmp_path,
                    env_extra={"MINDER_MEMORY_DB": str(off)})
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""
    conn = _db.connect(off)
    try:
        assert conn.execute("SELECT COUNT(*) FROM success_observations"
                            ).fetchone()[0] == 0
    finally:
        conn.close()


def test_real_payload_signs_the_actual_output_not_the_empty_string(tmp_path):
    """Regression: _observe_success must sign the tool's real output.

    to_event() maps a raw payload's `tool_response` onto `error_excerpt`,
    so reading only `tool_response` off the canonical event signed every
    production success as the empty string. Two different results from one
    action then shared a signature — the counter over-fires."""
    from unittest import mock

    from memory import db as _db, from_hook, success_guard
    dbp = tmp_path / "sig.sqlite"
    _db.connect(dbp).close()
    with mock.patch.dict(os.environ, {"MINDER_SUCCESS_GUARD": "advisory"}):
        from_hook.record(_loop_payload(CURL_1), db_path=dbp)
        from_hook.record(_loop_payload("completely different body"),
                         db_path=dbp)
    conn = _db.connect(dbp)
    try:
        sigs = [r[0] for r in conn.execute(
            "SELECT result_signature FROM success_observations").fetchall()]
    finally:
        conn.close()
    assert len(sigs) == 2 and sigs[0] != sigs[1], sigs
    assert success_guard.result_signature(0, "") not in sigs
