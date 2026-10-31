"""The capture canary (#73): prove the pipeline still records.

Silence is the failure mode: dsh can update, the plugin manager can deny
the hooks bridge, and every component looks fine while minder goes blind.
This module runs one synthetic delegation-shaped request through the live
harness and asserts the sink received THAT session's records - not just
any records, because another transport (zcode) writes the same ledger and
would false-green a freshness check.

Three surfaces, one verdict:
- drift: dsh version + pinned bridge version vs the last green fingerprint
- endpoints: proxy 8390 and strata 8081 answer
- capture: a headless run's own session appears in the events ledger

Reasons are stable codes, not prose; the scheduler and console render the
words. Exit 0 green, 1 red. Every check degrades to a named fail, never a
crash, so the watchdog itself stays answerable when parts are missing.
"""
import json
import subprocess
import time

import adapter
from minder_op import dsh_sessions
from minder_op import router

CANARY_PROMPT = "Reply with exactly: OK"
RUN_TIMEOUT_S = 240
SETTLE_SECONDS = 90
PROBE_TIMEOUT_S = 5
BRIDGE_PACKAGE = "@deepseek-ai/dsh-hooks-claude-code"
PROBE_PORTS = (8390, 8081)
FINGERPRINT_NAME = "canary_state.json"
PROFILE_NAME = "headless"


def _state_dir():
    override = __import__("os").environ.get("MINDER_STATE_DIR")
    return router.DEFAULT_STATE_DIR if not override else __import__("os").environ[
        "MINDER_STATE_DIR"]


def _default_runner(cmd, timeout=None):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, shell=False)
    return proc


def _default_probe(url, timeout=None):
    status, _body = adapter.http_json("GET", url, timeout=timeout)
    return status == 200


def read_bridge_pin(manifest_path):
    """The pinned bridge version from the profile manifest, or None."""
    try:
        with open(manifest_path) as fh:
            deps = json.load(fh).get("dependencies") or {}
    except (OSError, ValueError):
        return None
    pin = deps.get(BRIDGE_PACKAGE)
    return str(pin) if pin else None


def _read_fingerprint(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_fingerprint(path, data):
    payload = dict(data, recorded_at=time.time())
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    __import__("os").replace(tmp, path)


def _dsh_version(runner):
    try:
        proc = runner(["dsh", "--version"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or "").split()
    return parts[0] if parts else None


def _drift_surface(stored, current):
    """ok on first run (baseline), ok when unchanged, warn when changed.

    A missing bridge pin warns even on a baseline: an unpinned bridge is
    exactly the surface the plugin manager can silently deny."""
    if current.get("bridge_pin") is None:
        return {"id": "drift", "status": "warn",
                "detail": f"{BRIDGE_PACKAGE} not pinned in profile manifest",
                "reason": "bridge_not_pinned"}
    if stored is None:
        return {"id": "drift", "status": "ok",
                "detail": "baseline (first run)"}
    changed = [key for key in ("dsh_version", "bridge_pin")
               if stored.get(key) != current.get(key)]
    if not changed:
        return {"id": "drift", "status": "ok", "detail": "unchanged"}
    detail = "; ".join(
        f"{key}: {stored.get(key)} -> {current.get(key)}" for key in changed)
    return {"id": "drift", "status": "warn", "detail": detail,
            "reason": "drift_changed"}


def _endpoint_surface(probe):
    for port in PROBE_PORTS:
        url = f"http://127.0.0.1:{port}/v1/models"
        try:
            ok = probe(url, timeout=PROBE_TIMEOUT_S)
        except Exception as exc:  # transport errors are the finding
            return {"id": "endpoints", "status": "fail",
                    "detail": f"{port}: {exc}", "reason": "endpoint_down"}
        if not ok:
            return {"id": "endpoints", "status": "fail",
                    "detail": f"{port}: not answering",
                    "reason": "endpoint_down"}
    return {"id": "endpoints", "status": "ok",
            "detail": "8390 and 8081 answering"}


def _new_session(before_ids, session_dirs_fn, dsh_home):
    after = session_dirs_fn(dsh_home, cache=False)
    fresh = [e for e in after if e.get("session_id")
             and e["session_id"] not in before_ids]
    if not fresh:
        return None
    fresh.sort(key=lambda e: e.get("mtime") or 0, reverse=True)
    return fresh[0]["session_id"]


def _ledger_has_session(ledger_path, offset, session_id):
    """A schema-shaped record whose task names THIS session, past offset."""
    try:
        with open(ledger_path, "rb") as fh:
            fh.seek(offset)
            tail = fh.read()
    except OSError:
        return False
    for line in tail.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if "ts" not in record or "event" not in record:
            continue
        task = str(record.get("task") or "")
        if session_id in task:
            return True
    return False


def _capture_surface(runner, session_dirs_fn, dsh_home, ledger_path,
                     settle_seconds):
    before = session_dirs_fn(dsh_home, cache=False)
    before_ids = {e.get("session_id") for e in before}
    try:
        offset = __import__("os").path.getsize(ledger_path)
    except OSError:
        offset = 0
    try:
        proc = runner(["dsh", "headless", CANARY_PROMPT],
                      timeout=RUN_TIMEOUT_S)
        failed = proc.returncode != 0
        note = (proc.stderr or "")[-200:]
    except (OSError, subprocess.SubprocessError) as exc:
        failed, note = True, str(exc)[-200:]
    session_id = _new_session(before_ids, session_dirs_fn, dsh_home)
    if session_id is None:
        return {"id": "capture", "status": "fail",
                "detail": f"dsh rc={proc.returncode if failed else '?'} {note}",
                "reason": "capture_no_session"}
    deadline = time.time() + max(0.0, settle_seconds)
    while True:
        if _ledger_has_session(ledger_path, offset, session_id):
            return {"id": "capture", "status": "ok",
                    "detail": f"session {session_id} persisted"}
        if time.time() >= deadline:
            break
        time.sleep(1.0)
    return {"id": "capture", "status": "fail",
            "detail": f"session {session_id}: no sink record in window",
            "reason": "capture_hooks_not_persisted"}


def run_canary(dsh_home=None, profile_manifest=None, ledger_path=None,
               fingerprint_path=None, runner=None, probe=None,
               session_dirs_fn=None, settle_seconds=None, now=None):
    """One canary pass. Returns {"verdict", "surfaces", "rebaselined"}."""
    now = time.time() if now is None else now
    dsh_home = dsh_home or str(dsh_sessions.dsh_home())
    profile_manifest = profile_manifest or __import__("os").path.join(
        dsh_home, "profiles", PROFILE_NAME, "package.json")
    state = _state_dir()
    ledger_path = ledger_path or __import__("os").path.join(
        str(state), router.LEDGER_NAME)
    fingerprint_path = fingerprint_path or __import__("os").path.join(
        str(state), FINGERPRINT_NAME)
    runner = runner or _default_runner
    probe = probe or _default_probe
    session_dirs_fn = session_dirs_fn or dsh_sessions.session_dirs
    settle_seconds = SETTLE_SECONDS if settle_seconds is None else settle_seconds

    current = {"dsh_version": _dsh_version(runner),
               "bridge_pin": read_bridge_pin(profile_manifest)}
    stored = _read_fingerprint(fingerprint_path)
    surfaces = [_drift_surface(stored, current),
                _endpoint_surface(probe),
                _capture_surface(runner, session_dirs_fn, dsh_home,
                                 ledger_path, settle_seconds)]
    failed = [s for s in surfaces if s["status"] == "fail"]
    verdict = "red" if failed else "green"
    rebaselined = False
    if verdict == "green":
        _write_fingerprint(fingerprint_path, current)
        rebaselined = True
    return {"verdict": verdict, "surfaces": surfaces, "now": now,
            "rebaselined": rebaselined,
            "fingerprint": current if verdict == "green" else None}


if __name__ == "__main__":
    import sys
    report = run_canary()
    print(json.dumps(report))
    sys.exit(0 if report["verdict"] == "green" else 1)
