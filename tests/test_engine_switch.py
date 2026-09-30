"""Engine lifecycle switch (issue #3): stop the current engine unit,
start the target, health-check it, flip active_engine, roll back on
failure. The command runner and health probe are injected; no test
shells out to systemctl."""
import json

import pytest

import minder
from minder_op import engines
from minder_op.cli import main

REGISTRY = {
    "engines": {
        "llama": {"upstream": "http://127.0.0.1:8080",
                  "unit": "u-llama"},
        "strata": {"upstream": "http://127.0.0.1:8081",
                   "unit": "u-strata"},
    },
    "active_engine": "llama",
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    cfg = tmp_path / "minder.json"
    monkeypatch.setattr(minder, "CFG_PATH", cfg)
    cfg.write_text(json.dumps(REGISTRY))
    return cfg


def fake_run(states, log):
    def run(cmd):
        log.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return 0, states.get(cmd[3], "inactive") + "\n", ""
        return 0, "", ""
    return run


def test_switch_stops_then_starts_then_flips(config):
    cmds = []
    states = {"u-llama": "active", "u-strata": "inactive"}
    result = engines.switch(
        "strata", run=fake_run(states, cmds),
        probe=lambda url: url.endswith(":8081"), sleep=lambda s: None)
    assert result["switched"] is True
    stops = [i for i, c in enumerate(cmds)
             if c[:3] == ["systemctl", "--user", "stop"]]
    starts = [i for i, c in enumerate(cmds)
              if c[:3] == ["systemctl", "--user", "start"]]
    assert cmds[stops[0]][3] == "u-llama"
    assert cmds[starts[0]][3] == "u-strata"
    # VRAM exclusivity: the old engine is down before the new one starts
    assert stops[0] < starts[0]
    assert json.loads(config.read_text())["active_engine"] == "strata"
    assert (config.parent / "minder.json.bak").exists()


def test_switch_rolls_back_when_target_never_healthy(config):
    cmds = []
    states = {"u-llama": "active", "u-strata": "inactive"}
    with pytest.raises(engines.EngineError):
        engines.switch(
            "strata", run=fake_run(states, cmds),
            probe=lambda url: url.endswith(":8080"),  # old engine only
            sleep=lambda s: None, health_timeout_s=0.2)
    # config untouched, old engine running again, target stopped
    assert json.loads(config.read_text())["active_engine"] == "llama"
    assert ["systemctl", "--user", "stop", "u-strata"] in cmds
    assert ["systemctl", "--user", "start", "u-llama"] in cmds


def test_switch_to_same_engine_is_a_noop(config):
    cmds = []
    result = engines.switch(
        "llama", run=fake_run({}, cmds), probe=lambda url: True,
        sleep=lambda s: None)
    assert result["switched"] is False
    assert not [c for c in cmds if c[:2] == ["systemctl", "--user"]]


def test_switch_unknown_engine(config):
    with pytest.raises(engines.EngineError):
        engines.switch("nope", run=fake_run({}, []),
                       probe=lambda url: True, sleep=lambda s: None)


def test_switch_requires_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "CFG_PATH", tmp_path / "minder.json")
    with pytest.raises(engines.EngineError):
        engines.switch("llama", run=fake_run({}, []),
                       probe=lambda url: True, sleep=lambda s: None)


def test_status_reports_units_and_health(config):
    states = {"u-llama": "active", "u-strata": "inactive"}
    rows = engines.status(run=fake_run(states, []),
                          probe=lambda url: url.endswith(":8080"))
    by_name = {r["name"]: r for r in rows}
    assert by_name["llama"]["active"] is True
    assert by_name["llama"]["unit_state"] == "active"
    assert by_name["llama"]["healthy"] is True
    assert by_name["strata"]["unit_state"] == "inactive"
    assert by_name["strata"]["healthy"] is False


def test_cli_engine_switch_requires_yes(config, capsys):
    rc = main(["engine", "switch", "strata"])
    assert rc == 1
    assert "--yes" in capsys.readouterr().out


def test_cli_engine_switch_runs(config, capsys, monkeypatch):
    cmds = []
    states = {"u-llama": "active", "u-strata": "inactive"}
    monkeypatch.setattr(engines, "_default_run", fake_run(states, cmds))
    monkeypatch.setattr(engines, "_probe_upstream",
                        lambda url: url.endswith(":8081"))
    rc = main(["engine", "switch", "strata", "--yes"])
    assert rc == 0
    assert "strata" in capsys.readouterr().out
    assert json.loads(config.read_text())["active_engine"] == "strata"


def test_cli_engine_status(config, capsys, monkeypatch):
    monkeypatch.setattr(engines, "_default_run", fake_run(
        {"u-llama": "active", "u-strata": "inactive"}, []))
    monkeypatch.setattr(engines, "_probe_upstream",
                        lambda url: url.endswith(":8080"))
    rc = main(["engine", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "llama" in out and "strata" in out
