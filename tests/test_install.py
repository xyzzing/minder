"""AT-11: installer verified on scratch HOME for both CAP ladder branches.

Branch A: kwargs mock  → full install, exit 0.
Branch B: reject_kwargs mock (mechanism=none) → STOP without flag;
          STOP bypassed with --accept-l1-degraded → digest-only install.
Also covers uninstall round-trip on the dsh settings.yaml.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mock_upstream import MockUpstream

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"

SCRATCH_DSH_SETTINGS = """\
agent-default-model:
  provider: local
  model: qwen27b-fusion
llm-pi-ai:
  providers:
    local:
      displayName: Local llama.cpp
      apiKeyEnv: LOCAL_LLAMA_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:8080/v1
      models:
        - id: qwen27b-fusion
          name: Qwen27b-fusion
          contextWindow: 98304
plugins:
  dshmarket:
    enabled: true
"""

SCRATCH_ZCODE_CONFIG = {
    "mcp": {"servers": {}},
    "hooks": {
        "enabled": True,
        "timeoutMs": 60000,
        "events": {
            "PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "process", "command": "node",
                 "args": ["/x/guard.mjs"], "enabled": True,
                 "timeoutMs": 5000}]}],
        },
    },
}


def scratch_home(tmp_path):
    home = tmp_path / "home"
    (home / ".dsh").mkdir(parents=True, exist_ok=True)
    (home / ".dsh" / "settings.yaml").write_text(SCRATCH_DSH_SETTINGS)
    (home / ".zcode" / "cli").mkdir(parents=True, exist_ok=True)
    (home / ".zcode" / "cli" / "config.json").write_text(
        json.dumps(SCRATCH_ZCODE_CONFIG, indent=2))
    return home


def run_installer(home, mock_url, *flags):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["MINDER_NO_DSH_PLUGIN"] = "1"     # no dsh binary in scratch env
    env["MINDER_NO_SYSTEMD"] = "1"        # no systemd writes in tests
    env["PATH"] = "/usr/bin:/bin"         # hide real dsh/systemctl variants
    return subprocess.run(["bash", str(INSTALL), "--upstream", mock_url,
                           "--no-start", *flags],
                          capture_output=True, text=True, env=env,
                          timeout=180)


def test_at11_branch_a_kwargs_full_install(tmp_path):
    with MockUpstream("default") as mock:
        home = scratch_home(tmp_path)
        r = run_installer(home, mock.url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "mechanism=kwargs" in r.stdout
    share = home / ".local" / "share" / "minder"
    assert (share / "proxy.py").exists()
    assert (share / "dsh" / "hooks.json").exists()
    # caps written and fingerprints only
    caps = json.loads((home / ".config" / "minder" /
                       "model_caps.json").read_text())
    assert caps["thinking"]["mechanism"] == "kwargs"
    # presets patched with the fingerprinted model id (AT-16 prep)
    preset = json.loads((home / ".config" / "minder" / "presets" /
                         "qwen3-exec.json").read_text())
    assert preset["upstream_model"] == "qwen27b-fusion"
    # dsh settings: additive provider + plugin registration; local intact
    text = (home / ".dsh" / "settings.yaml").read_text()
    assert "baseURL: http://127.0.0.1:8390/v1" in text
    assert "baseURL: http://127.0.0.1:8080/v1" in text  # original kept
    assert "@deepseek-ai/dsh-hooks-claude-code" in text
    assert SCRATCH_DSH_SETTINGS.splitlines()[2] in text.splitlines()
    # hooks.json patched with the share path
    hooks = json.loads((share / "dsh" / "hooks.json").read_text())
    assert "__MINDER_SHARE__" not in json.dumps(hooks)
    # zcode merged
    zcfg = json.loads((home / ".zcode" / "cli" /
                       "config.json").read_text())
    args = [a for e in zcfg["hooks"]["events"]["PostToolUse"]
            for h in e["hooks"] for a in h["args"]]
    assert any("hook.py" in a for a in args)
    # idempotent re-run
    r2 = run_installer(home, mock.url)
    assert r2.returncode == 0
    text2 = (home / ".dsh" / "settings.yaml").read_text()
    assert text2.count("@deepseek-ai/dsh-hooks-claude-code") == \
        text.count("@deepseek-ai/dsh-hooks-claude-code")


def test_at11_branch_b_none_stops_then_degraded_installs(tmp_path):
    with MockUpstream("reject_kwargs") as mock:
        home = scratch_home(tmp_path)
        r = run_installer(home, mock.url)  # no flag → STOP
        assert r.returncode != 0
        assert "accept-l1-degraded" in (r.stdout + r.stderr)
        assert "mechanism=none" in (r.stdout + r.stderr) or \
            "cannot toggle" in (r.stdout + r.stderr)

        home2 = scratch_home(tmp_path)
        r = run_installer(home2, mock.url, "--accept-l1-degraded")
    assert r.returncode == 0, r.stdout + r.stderr
    caps = json.loads((home2 / ".config" / "minder" /
                       "model_caps.json").read_text())
    assert caps["thinking"]["mechanism"] == "none"
    cfg = json.loads((home2 / ".config" / "minder" /
                      "minder.json").read_text())
    assert cfg["accept_l1_degraded"] is True


def run_uninstaller(home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["MINDER_NO_DSH_PLUGIN"] = "1"
    env["MINDER_NO_SYSTEMD"] = "1"        # never touch the real user session
    env["PATH"] = "/usr/bin:/bin"
    return subprocess.run(["bash", str(UNINSTALL)], capture_output=True,
                          text=True, env=env, timeout=120)


def test_uninstall_restores_scratch_home(tmp_path):
    with MockUpstream("default") as mock:
        home = scratch_home(tmp_path)
        original_settings = (home / ".dsh" / "settings.yaml").read_text()
        original_zcode = (home / ".zcode" / "cli" /
                          "config.json").read_text()
        assert run_installer(home, mock.url).returncode == 0
        assert run_uninstaller(home).returncode == 0
    assert (home / ".dsh" / "settings.yaml").read_text() == original_settings
    zcfg = json.loads((home / ".zcode" / "cli" / "config.json").read_text())
    assert zcfg["hooks"]["events"] == SCRATCH_ZCODE_CONFIG["hooks"]["events"]
    assert not (home / ".local" / "share" / "minder").exists()
    assert not (home / ".config" / "minder").exists()
