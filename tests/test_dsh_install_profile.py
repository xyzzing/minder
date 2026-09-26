"""dsh profile wiring: the three points that make hook capture work.

The regression this exists for: install targeted `~/.dsh/settings.yaml`,
which modern dsh no longer has, so on this host the hooks bridge was fine
while `hooks.json` quietly lost the runtime flags and the sink URL — and
nothing verified any of it end to end.
"""
import json

import pytest

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "dsh_install_under_test", _REPO / "dsh" / "dsh_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load()


def _profile(tmp_path, name="web"):
    profile = tmp_path / ".dsh" / "profiles" / name
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text("# patch layer\n")
    return profile


def test_apply_wires_all_three_points(tmp_path, mod):
    profile = _profile(tmp_path)
    share = tmp_path / "share"
    hooks = share / "dsh" / "hooks.json"
    result = mod.apply_profile(tmp_path / ".dsh", "web", share,
                               "http://127.0.0.1:8392", hooks)
    assert result["hooks-json"] == "written"
    assert result["bridge-loader"] == "written"
    assert result["bridge-entry"] == "written"
    ok, points = mod.check_profile(tmp_path / ".dsh", "web", share,
                                   "http://127.0.0.1:8392", hooks)
    assert ok, points


def test_hooks_json_carries_flags_and_sink(tmp_path, mod):
    share = tmp_path / "share"
    hooks = share / "dsh" / "hooks.json"
    mod.write_hooks_json(hooks, share, "http://127.0.0.1:8392")
    config = json.loads(hooks.read_text())
    commands = [h["command"] for event in config["hooks"].values()
                for group in event for h in group["hooks"]]
    assert len(commands) == 3
    # the success-loop block mode needs a pre-execution hook point: without
    # PreToolUse the guard can only advise after the tool already ran.
    assert set(config["hooks"]) == {"SessionStart", "PostToolUse",
                                    "PreToolUse"}
    pre = [h["command"] for h in
           config["hooks"]["PreToolUse"][0]["hooks"]]
    assert len(pre) == 1 and pre[0].endswith("--pre-tool")
    for command in commands:
        for flag in ("MINDER_ASSIST=", "MINDER_CLASSIFIER=",
                     "MINDER_DECISION=", "MINDER_SUCCESS_GUARD=",
                     "MINDER_SINK_URL=http://127.0.0.1:8392"):
            assert flag in command, (flag, command)
        assert str(share) in command
        assert "__MINDER_" not in command


def test_apply_is_idempotent(tmp_path, mod):
    profile = _profile(tmp_path)
    share = tmp_path / "share"
    hooks = share / "dsh" / "hooks.json"
    mod.apply_profile(tmp_path / ".dsh", "web", share, "s", hooks)
    patch_before = (profile / "cordis.patch.yml").read_text()
    second = mod.apply_profile(tmp_path / ".dsh", "web", share, "s", hooks)
    assert second == {"profile": "web",
                      "profile_dir": str(profile),
                      "hooks-json": "already-present",
                      "bridge-loader": "already-present",
                      "bridge-entry": "already-present"}
    assert (profile / "cordis.patch.yml").read_text() == patch_before


def test_check_fails_on_a_flagless_hooks_json(tmp_path, mod):
    """Exactly the live state on 2026-09-25."""
    profile = _profile(tmp_path)
    share = tmp_path / "share"
    hooks = share / "dsh" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"hooks": {"PostToolUse": [
        {"matcher": "", "hooks": [{"type": "command",
                                   "command": "python3 hook.py"}]}]}}))
    mod.ensure_bridge_loader(profile)
    mod.ensure_patch_entry(profile, hooks)
    ok, points = mod.check_profile(tmp_path / ".dsh", "web", share, "sink",
                                   hooks)
    assert ok is False
    assert any(p == "hooks-json" and s == "fail" for p, s, _d in points)


def test_check_reports_a_missing_profile(tmp_path, mod):
    ok, points = mod.check_profile(tmp_path / ".dsh", "web",
                                   tmp_path / "share", "sink")
    assert ok is False
    assert any(p == "bridge-entry" and s == "fail" for p, s, _d in points)


def test_check_rejects_invalid_json(tmp_path, mod):
    profile = _profile(tmp_path)
    share = tmp_path / "share"
    hooks = share / "dsh" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{not json")
    mod.ensure_bridge_loader(profile)
    mod.ensure_patch_entry(profile, hooks)
    _ok, points = mod.check_profile(tmp_path / ".dsh", "web", share, "sink",
                                    hooks)
    detail = next(d for p, _s, d in points if p == "hooks-json")
    assert "INVALID JSON" in detail


def test_render_refuses_a_non_template(tmp_path, mod):
    bogus = tmp_path / "hooks.json"
    bogus.write_text('{"hooks": {}}')
    with pytest.raises(SystemExit):
        mod.render_hooks_json(tmp_path, "http://x", template=bogus)


def test_discover_profile_prefers_web_then_any(tmp_path, mod):
    assert mod.discover_profile(tmp_path / ".dsh") == "web"
    (tmp_path / ".dsh" / "profiles" / "headless").mkdir(parents=True)
    (tmp_path / ".dsh" / "profiles" / "headless"
     / "cordis.patch.yml").write_text("[]")
    assert mod.discover_profile(tmp_path / ".dsh") == "headless"
    (tmp_path / ".dsh" / "profiles" / "custom").mkdir()
    (tmp_path / ".dsh" / "profiles" / "custom"
     / "cordis.patch.yml").write_text("[]")
    assert mod.discover_profile(tmp_path / ".dsh") == "headless"


def test_legacy_settings_command_requires_settings(capsys, mod, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dsh_install.py", "apply"])
    assert mod.main() == 2
    assert "settings.yaml" in capsys.readouterr().err


def test_live_repo_hooks_template_is_usable(mod):
    """The shipped template must carry the placeholders install fills in."""
    text = mod.repo_hooks_template().read_text()
    assert "__MINDER_SHARE__" in text
    assert "__MINDER_SINK_URL__" in text
    assert json.loads(text.replace("__MINDER_SHARE__", "/s")
                          .replace("__MINDER_SINK_URL__", "http://x"))
