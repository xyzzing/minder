"""dsh package tests (AT-dsh-1..5, prd.md plan §dsh)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dsh"))
import dsh_install  # noqa: E402

# Realistic replica of the user's ~/.dsh/settings.yaml shape, including block
# scalars and plugin config maps — plus adversarial lines inside the scalar.
SETTINGS = """\
agent-default-model:
  provider: local
  model: qwen27b-fusion
llm-pi-ai:
  providers:
    local:
      displayName: Local llama.cpp (7900 XTX)
      apiKeyEnv: LOCAL_LLAMA_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:8080/v1
      defaultContextWindow: 32768
      models:
        - id: qwen27b-fusion
          name: Qwen27b-fusion
          contextWindow: 98304
          maxTokens: 16356
ui-onboarding:
  welcomeNoticeVersion: 2026-08-13.1
plugins:
  '@deepseek-ai/dsh-system-prompt':
    includeHarnessIdentity: true
    persona: |
      You are an expert software engineer.

      Sample lines that must NEVER be treated as config keys:
      plugins:
        minder:
          baseURL: http://evil.example
      'dshmarket':
        enabled: true
  dshmarket:
    enabled: true
  agent-teams:
    enabled: true
    config:
      max_parallel_workers: 1
"""


@pytest.fixture
def settings_file(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(SETTINGS)
    return p


def hooks_json_template(tmp_path):
    src = Path(__file__).resolve().parent.parent / "dsh" / "hooks.json"
    return src.read_text()


def test_at_dsh_1_additive_provider_preserves_local(settings_file):
    original = settings_file.read_text()
    text, status = dsh_install.add_provider(original, "http://127.0.0.1:8390/v1",
                                            98304)
    assert status == "inserted"
    ok, msg = dsh_install.verify(text, expect_plugin=False)
    assert ok, msg
    # idempotent
    text2, status2 = dsh_install.add_provider(text, "http://127.0.0.1:8390/v1",
                                              98304)
    assert status2 == "already-present" and text2 == text
    # the local provider card and default model are byte-identical
    for line in original.splitlines():
        if ("qwen27b-fusion" in line or "7900 XTX" in line
                or "8080" in line) and "8390" not in line:
            assert text.count(line) >= original.count(line)
    assert original.count("agent-default-model") == text.count(
        "agent-default-model")
    # exactly one minder provider card (line-anchored), with the alias models
    assert sum(1 for ln in text.splitlines() if ln == "    minder:") == 1
    assert "baseURL: http://127.0.0.1:8390/v1" in text
    for model_id in ("qwen-exec", "qwen-think", "qwen-auto", "frontier"):
        assert f"- id: {model_id}" in text
    # credential reused from the existing provider
    assert "apiKeyEnv: LOCAL_LLAMA_KEY" in text


def test_block_scalar_content_not_matched(settings_file):
    """The 'minder:' line inside the persona block scalar is not config."""
    text, _ = dsh_install.add_provider(SETTINGS, "http://x", 32768)
    # the plugins: occurrences (top-level + inside persona) are untouched,
    # and the insertion adds none
    assert text.count("plugins:") == SETTINGS.count("plugins:")
    assert "http://evil.example" in text  # still there, still inert
    lines = text.splitlines()
    child = dsh_install.find_indented(
        lines, dsh_install.PROVIDER_NAME,
        dsh_install._providers_idx(lines, dsh_install.block_scalar_mask(lines)),
        2, dsh_install.block_scalar_mask(lines))
    assert child is not None
    assert "displayName" in lines[child + 1]


def test_at_dsh_3_plugin_registration_preserves_existing(settings_file):
    text, status = dsh_install.register_plugin(
        SETTINGS, "/home/u/.local/share/minder/dsh/hooks.json")
    assert status == "inserted"
    ok, msg = dsh_install.verify(text, expect_provider=False)
    assert ok, msg
    assert "'@deepseek-ai/dsh-hooks-claude-code':" in text
    assert "configPath: /home/u/.local/share/minder/dsh/hooks.json" in text
    # existing plugin entries intact
    assert "'@deepseek-ai/dsh-system-prompt':" in text
    assert "agent-teams:" in text
    # idempotent
    text2, status2 = dsh_install.register_plugin(
        text, "/home/u/.local/share/minder/dsh/hooks.json")
    assert status2 == "already-present"


def test_at_dsh_5_remove_restores_byte_identical(settings_file):
    original = settings_file.read_text()
    text, _ = dsh_install.add_provider(original, "http://127.0.0.1:8390/v1",
                                       98304)
    text, _ = dsh_install.register_plugin(
        text, "/home/u/.local/share/minder/dsh/hooks.json")
    text, st1 = dsh_install.remove_provider(text)
    text, st2 = dsh_install.remove_plugin(text)
    assert (st1, st2) == ("removed", "removed")
    assert text == original


def test_at_dsh_2_hooks_json_schema():
    raw = hooks_json_template(None).replace("__MINDER_SHARE__",
                                            "/home/u/.local/share/minder")
    data = json.loads(raw)
    entry = data["hooks"]["PostToolUse"][0]
    assert entry["matcher"] == ""
    hook = entry["hooks"][0]
    assert hook["type"] == "command"
    # optional leading env assignments (e.g. MINDER_HOOK_TRACE=1) are part of
    # the command contract; the payload after them is what must stay stable
    assert re.match(r"^(?:[A-Z_][A-Z0-9_]*=\S+\s+)*"
                    r"python3 /home/u/\.local/share/minder/",
                    hook["command"]), hook["command"]
    assert "--transport dsh" in hook["command"]
    assert isinstance(hook["timeout"], int) and 30 <= hook["timeout"] <= 320


def test_verify_rejects_broken_yaml():
    ok, _ = dsh_install.verify("plugins: [unclosed")
    assert ok is False
    ok, _ = dsh_install.verify(SETTINGS, expect_provider=False,
                               expect_plugin=False)
    assert ok is True  # unedited settings pass with expectations cleared


def test_cli_apply_and_remove_with_backup(settings_file, monkeypatch):
    r = subprocess.run(
        [sys.executable, str(Path(dsh_install.__file__)), "apply",
         "--settings", str(settings_file),
         "--hooks-json", "/home/u/.local/share/minder/dsh/hooks.json",
         "--n-ctx", "98304"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    backups = list(settings_file.parent.glob("settings.yaml.minder-*.bak"))
    assert backups
    applied = settings_file.read_text()
    ok, msg = dsh_install.verify(applied)
    assert ok, msg

    r = subprocess.run(
        [sys.executable, str(Path(dsh_install.__file__)), "remove",
         "--settings", str(settings_file)],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert settings_file.read_text() == SETTINGS


def test_ensure_provider_model_upgrades_existing_card(settings_file):
    """Upgrade path: a card installed before qwen-auto gains the model."""
    original = settings_file.read_text()
    text, _ = dsh_install.add_provider(original, "http://127.0.0.1:8390/v1",
                                       98304)
    # simulate a 3-model legacy card by removing the qwen-auto lines
    lines = [ln for ln in text.splitlines(keepends=True)
             if "qwen-auto" not in ln]
    legacy = "".join(lines)
    up, status = dsh_install.ensure_provider_model(legacy, "qwen-auto", 98304)
    assert status == "inserted"
    ok, msg = dsh_install.verify(up, expect_plugin=False)
    assert ok, msg
    # idempotent
    up2, status2 = dsh_install.ensure_provider_model(up, "qwen-auto", 98304)
    assert status2 == "already-present" and up2 == up


def test_real_dsh_settings_readonly():
    """Golden check against the live ~/.dsh/settings.yaml — read-only."""
    real = Path.home() / ".dsh" / "settings.yaml"
    if not real.exists():
        pytest.skip("no live dsh settings")
    original = real.read_text()
    text, status = dsh_install.add_provider(original,
                                            "http://127.0.0.1:8390/v1", 98304)
    if status == "already-present":
        # installed state: exercise the model-upgrade path idempotently
        up, st = dsh_install.ensure_provider_model(original, "qwen-auto",
                                                   98304)
        assert st in ("already-present", "inserted")
        up2, st2 = dsh_install.ensure_provider_model(up, "qwen-auto", 98304)
        assert st2 == "already-present" and up2 == up
        ok, msg = dsh_install.verify(up)
        assert ok, msg
        return
    text, _ = dsh_install.register_plugin(
        text, str(Path.home() / ".local/share/minder/dsh/hooks.json"))
    ok, msg = dsh_install.verify(text)
    assert ok, msg
    back, _ = dsh_install.remove_provider(text)
    back, _ = dsh_install.remove_plugin(back)
    assert back == original  # round-trip is lossless on the real file
    try:
        import yaml
        data = yaml.safe_load(text)
        assert data["llm-pi-ai"]["providers"]["minder"]["baseURL"] == \
            "http://127.0.0.1:8390/v1"
    except ImportError:
        pass
