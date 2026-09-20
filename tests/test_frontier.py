"""Frontier consult runner tests (L2 out-of-band channel, panel semantics)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import frontier

FRONTIER = str(Path(__file__).resolve().parent.parent / "frontier.py")
SUBPROC_TIMEOUT = int(os.environ.get("MINDER_TEST_TIMEOUT", "60"))


def test_build_prompt_carries_payload():
    p = frontier.build_prompt({"key": "edit:/x/a.py", "attempts": 2,
                               "error": "old_string not found"})
    assert "edit:/x/a.py" in p
    assert "old_string not found" in p


def test_build_request_shape():
    provider = {"name": "x", "base_url": "https://api.x.com",
                "key_env": "X_KEY", "timeout": 30}
    url, body, headers = frontier.build_request(
        "prompt", provider, "m1", "sk-test")
    assert url == "https://api.x.com/v1/chat/completions"
    assert body["model"] == "m1"
    assert body["messages"][0]["content"] == "prompt"
    assert body["stream"] is False
    assert headers["Authorization"] == "Bearer sk-test"


def test_resolve_providers_legacy_and_list():
    legacy = frontier.resolve_providers(
        {"frontier_base_url": "https://a", "frontier_model": "m",
         "frontier_key_env": "K"})
    assert len(legacy) == 1
    assert legacy[0]["base_url"] == "https://a"
    panel = frontier.resolve_providers(
        {"frontier_providers": [{"name": "a", "base_url": "https://a"},
                                {"name": "b", "base_url": "https://b",
                                 "model": "mb", "key_env": "KB"}]})
    assert [p["name"] for p in panel] == ["a", "b"]
    assert panel[0]["key_env"] == "DEEPSEEK_API_KEY"  # filled default
    assert panel[1]["model"] == "mb"


def test_run_single_provider_trims_to_4000():
    post = lambda u, b, h, t: (200, {"choices": [
        {"message": {"content": "x" * 9000}}]})
    out = frontier.run({"key": "k"}, {}, api_key="sk", post=post)
    assert len(out) == 4000


def test_run_error_strings_start_with_paren():
    def boom(u, b, h, t):
        raise RuntimeError("network down")
    out = frontier.run({}, {}, api_key="sk", post=boom)
    assert out.startswith("(")


PANEL_CFG = {"frontier_providers": [
    {"name": "deepseek", "base_url": "https://api.deepseek.com",
     "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    {"name": "openai", "base_url": "https://api.openai.com",
     "model": "gpt-test", "key_env": "OPENAI_API_KEY"},
]}


def test_run_panel_synthesizes_two_answers(monkeypatch):
    monkeypatch.setattr(frontier, "load_key_by_name", lambda name: "sk-" + name)
    calls = []

    def post(url, body, headers, timeout):
        calls.append((url, body["messages"][0]["content"]))
        if "deepseek" in url:
            answer = "root cause A; run check-foo"
        elif "openai" in url:
            answer = "root cause B; run check-bar"
        else:
            answer = "unexpected url"
        if "Reconcile" in body["messages"][0]["content"]:
            return 200, {"choices": [{"message": {"content": "MERGED: do X"}}]}
        return 200, {"choices": [{"message": {"content": answer}}]}

    out = frontier.run_panel({"key": "k", "attempts": 3,
                              "error": "boom"}, PANEL_CFG, post=post)
    assert len(calls) == 3  # two consults + one synthesis
    assert "PANEL CONSULT: deepseek ✓, openai ✓" in out
    assert "SYNTHESIS" in out and "MERGED: do X" in out
    assert "[deepseek] root cause A" in out
    assert "[openai] root cause B" in out
    # the synthesis prompt carries both consultants' answers
    synth_prompt = [c for _, c in calls if "Reconcile" in c][0]
    assert "root cause A" in synth_prompt and "root cause B" in synth_prompt


def test_run_panel_skips_provider_without_key(monkeypatch):
    monkeypatch.setattr(frontier, "load_key_by_name",
                        lambda name: "sk" if name == "DEEPSEEK_API_KEY"
                        else None)
    post = lambda u, b, h, t: (200, {"choices": [
        {"message": {"content": "solo answer"}}]})
    out = frontier.run_panel({"key": "k"}, PANEL_CFG, post=post)
    assert out == "solo answer"  # no panel machinery on a single answer


def test_run_panel_no_keys_at_all(monkeypatch):
    monkeypatch.setattr(frontier, "load_key_by_name", lambda name: None)
    out = frontier.run_panel({"key": "k"}, PANEL_CFG, post=None)
    assert out.startswith("(no frontier provider answered")
    assert "deepseek" in out and "openai" in out


def test_pick_model_prefers_modern_ids():
    provider = {"base_url": "https://api.openai.com", "model": ""}
    get = lambda u, h, t: (200, {"data": [
        {"id": "davinci-002"}, {"id": "text-embedding-3"},
        {"id": "gpt-4.1-mini"}, {"id": "gpt-4o"}]})
    assert frontier.pick_model(provider, "sk", get=get) == "gpt-4.1-mini"
    def fail_get(u, h, t):
        raise RuntimeError("no listing")
    assert frontier.pick_model(provider, "sk", get=fail_get) is None


def test_load_key_from_env_file(tmp_path, monkeypatch):
    f = tmp_path / "frontier.env"
    f.write_text("# comment\nDEEPSEEK_API_KEY=sk-from-file\nOTHER=1\n")
    monkeypatch.setenv("MINDER_FRONTIER_ENV", str(f))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert frontier.load_key_by_name("DEEPSEEK_API_KEY") == "sk-from-file"
    # real env wins over the file
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    assert frontier.load_key_by_name("DEEPSEEK_API_KEY") == "sk-from-env"


def test_main_exit3_without_key(tmp_path, monkeypatch):
    env = {"PATH": "/usr/bin:/bin",
           "MINDER_CONFIG": str(tmp_path / "minder.json"),
           "MINDER_FRONTIER_ENV": str(tmp_path / "absent.env"),
           "HOME": str(tmp_path)}
    proc = subprocess.run([sys.executable, FRONTIER],
                          input=json.dumps({"key": "k"}),
                          capture_output=True, text=True, env=env,
                          timeout=SUBPROC_TIMEOUT)
    assert proc.returncode == 3
    assert "DEEPSEEK_API_KEY" in proc.stderr


def test_redaction_scrubs_before_egress():
    cfg = {"egress_redaction": [r"ACME-[A-Z]+-\d+"]}
    scrubbed = frontier.scrub_payload(
        {"key": "edit:/contracts/ACME-CORP-9973.md", "error": "see ACME-CORP-9973"},
        cfg)
    assert "ACME-CORP-9973" not in scrubbed["key"]
    assert "[REDACTED]" in scrubbed["key"]


def test_verify_prompt_and_template():
    v = frontier.build_prompt({"kind": "verify", "key": "cmd:make",
                               "attempts": 5, "error": "boom",
                               "resolution": "all good"})
    assert "VERDICT: ADDRESSED" in v and "all good" in v
    t = frontier.build_prompt({"key": "k", "attempts": 2, "error": "E"},
                              template="TRADING MODE: {key} failed {attempts}x")
    assert t == "TRADING MODE: k failed 2x"
    # malformed template falls back to the default prompt
    t2 = frontier.build_prompt({"key": "k", "attempts": 2, "error": "E"},
                               template="broken {nosuchslot}")
    assert "frontier consultant" in t2
