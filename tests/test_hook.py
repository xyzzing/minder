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
             extra_args=()):
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "MINDER_STATE_DIR": str(tmp_path / "state"),
        "MINDER_CONFIG": str(tmp_path / "minder.json"),
        "MINDER_CAPS": str(tmp_path / "caps.json"),
        "HOME": str(tmp_path),
    }
    if frontier_cmd:
        env["MINDER_FRONTIER_CMD"] = frontier_cmd
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
