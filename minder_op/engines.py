"""Engine lifecycle for the dual-engine registry (issue #3).

switch(name) stops the current engine's systemd user unit, starts the
target unit, health-checks the target upstream, and only then flips
active_engine in minder.json. Any failure rolls the units back and
leaves the config untouched: a failed switch must never strand the
operator without a working engine. The command runner and health probe
are injectable so tests never shell out.
"""
import json
import os
import subprocess
import time

import adapter
import minder

HEALTH_TIMEOUT_S = 90.0
HEALTH_POLL_S = 0.5


class EngineError(Exception):
    pass


def _default_run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout, p.stderr


def _probe_upstream(url):
    """The engine's /health; transport failure is unhealthy, not a crash."""
    try:
        st, _body = adapter.http_json("GET", url.rstrip("/") + "/health",
                                      timeout=5)
        return st == 200
    except Exception:  # noqa: BLE001 — unreachable engine is a health no
        return False


def _unit_cmd(action, unit, run):
    rc, _out, err = run(["systemctl", "--user", action, unit])
    if rc != 0:
        raise EngineError(f"systemctl --user {action} {unit} failed: "
                          f"{err.strip() or rc}")


def _wait_healthy(url, probe, sleep, timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe(url):
            return True
        sleep(HEALTH_POLL_S)
    return False


def _set_active(name, config_path):
    """Flip active_engine atomically: backup, write, verify, keep the
    backup for manual recovery (additive-integration law)."""
    path = config_path or minder.CFG_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    backup = path.parent / "minder.json.bak"
    tmp = path.parent / "minder.json.tmp-engine"
    backup.write_text(path.read_text())
    tmp.write_text(json.dumps({**data, "active_engine": name}, indent=2))
    os.replace(tmp, path)
    try:
        if json.loads(path.read_text()).get("active_engine") != name:
            raise EngineError("active_engine verify failed after write")
    except (OSError, ValueError) as exc:
        backup.replace(path)  # restore
        raise EngineError(f"config verify failed, restored backup: {exc}")


def switch(name, run=None, probe=None, sleep=time.sleep,
           config_path=None, health_timeout_s=HEALTH_TIMEOUT_S):
    """Stop the current engine, start the target, verify health, flip the
    config. Rolls back the units when the target never becomes healthy."""
    run = run or _default_run
    probe = probe or _probe_upstream
    cfg = minder.cfg()
    engines_map, active = minder.engine_registry(cfg)
    if not cfg.get("engines"):
        raise EngineError("no engine registry in minder.json - add engines "
                          "with upstream + unit entries first")
    if name not in engines_map:
        raise EngineError(f"unknown engine '{name}'; known: "
                          f"{', '.join(sorted(engines_map))}")
    if name == active:
        return {"switched": False, "from": active, "engine": name}

    old, new = engines_map[active], engines_map[name]
    for e in (old, new):
        if not e.get("unit"):
            raise EngineError("engine entry has no systemd unit; "
                              "cannot lifecycle-switch")
    _unit_cmd("stop", old["unit"], run)
    _unit_cmd("start", new["unit"], run)
    if not _wait_healthy(new["upstream"], probe, sleep, health_timeout_s):
        _unit_cmd("stop", new["unit"], run)
        _unit_cmd("start", old["unit"], run)
        came_back = _wait_healthy(old["upstream"], probe, sleep,
                                  health_timeout_s)
        detail = "" if came_back else " (old engine also not healthy - " \
                                      "check systemctl --user status)"
        raise EngineError(f"engine '{name}' did not become healthy within "
                          f"{health_timeout_s:g}s; rolled back to "
                          f"'{active}'{detail}")
    _set_active(name, config_path)
    return {"switched": True, "from": active, "engine": name}


def status(run=None, probe=None):
    """One row per engine: active flag, unit state, upstream health."""
    run = run or _default_run
    probe = probe or _probe_upstream
    engines_map, active = minder.engine_registry()
    rows = []
    for name, e in engines_map.items():
        unit_state = None
        if e.get("unit"):
            rc, out, _err = run(["systemctl", "--user", "is-active",
                                 e["unit"]])
            # systemctl maps states to rc: 0 active, 3 inactive, 4 unknown
            unit_state = out.strip() if rc == 0 else {
                3: "inactive"}.get(rc, f"rc={rc}")
        rows.append({"name": name, "active": name == active,
                     "upstream": e["upstream"], "unit": e.get("unit"),
                     "unit_state": unit_state, "healthy": probe(e["upstream"])})
    return rows
