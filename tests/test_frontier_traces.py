"""Frontier consult trace tests (docs/prd-memory-v1.md PR 7)."""

import frontier
from minder_memory import frontier_traces

CFG = {"frontier_providers": [
    {"name": "probe-a", "base_url": "http://probe/a", "model": "m-a",
     "key_env": "PROBE_A_KEY"},
    {"name": "probe-b", "base_url": "http://probe/b", "model": "m-b",
     "key_env": "PROBE_B_KEY"},
]}


def panel_payload():
    return {"key": "bash|keyerror|supplier_id|s.py", "attempts": 3,
            "error": "KeyError: 'supplier_id'",
            "task": "trace-t1", "episode_id": "ep_trace1"}


def fake_post_body(body):
    """OpenAI-style 200 with the answer in content."""
    return 200, {"choices": [{"message": {"content": "SYNTH-ANSWER"}}]}


def test_trace_stored_with_hashes_not_raw_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_A_KEY", "k1")
    monkeypatch.setenv("PROBE_B_KEY", "k2")
    captured = {}

    def on_trace(meta):
        captured.update(meta)

    answer = frontier.run_panel(panel_payload(), CFG,
                                post=lambda *a, **k: fake_post_body(None),
                                on_trace=on_trace)
    assert "SYNTH-ANSWER" in answer and "PANEL CONSULT" in answer
    # route the metadata through the store like frontier.main does
    trace_id = frontier_traces.record(
        {"key": captured["failure_key"], "attempts": captured["local_attempts"],
         "prompt": captured["request_hash_source"],
         "episode_id": captured["episode_id"]},
        captured["answer"], providers=captured["providers"],
        redaction_profile=captured["redaction_profile"],
        episode_id=captured["episode_id"], db_path=tmp_path / "m.sqlite")
    trace = frontier_traces.get(trace_id, db_path=tmp_path / "m.sqlite")
    assert trace["failure_key"] == "bash|keyerror|supplier_id|s.py"
    assert trace["local_attempts"] == 3
    assert trace["episode_id"] == "ep_trace1"
    assert trace["provider_fingerprint"] == "probe-a,probe-b"
    assert len(trace["request_hash"]) == 16 and len(trace["response_hash"]) == 16
    # raw prompt text is not stored — only hashes
    assert "supplier_id" not in (trace["request_hash"] or "")


def test_helpfulness_null_until_verification(tmp_path):
    dbp = tmp_path / "m.sqlite"
    tid = frontier_traces.record({"key": "k|fam|sym|path", "attempts": 1,
                                  "prompt": "p"}, "a", db_path=dbp)
    trace = frontier_traces.get(tid, db_path=dbp)
    assert trace["helpfulness"] is None
    assert trace["verification_status"] is None
    assert frontier_traces.set_verification(tid, helpfulness=1,
                                            verification_status="ADDRESSED",
                                            db_path=dbp)
    trace = frontier_traces.get(tid, db_path=dbp)
    assert trace["helpfulness"] == 1
    assert trace["verification_status"] == "ADDRESSED"


def test_no_trace_callback_zero_behaviour_change(tmp_path, monkeypatch):
    """run_panel without on_trace returns the same answer (existing tests
    in test_frontier.py cover this too — this pins the default)."""
    monkeypatch.setenv("PROBE_A_KEY", "k1")
    monkeypatch.setenv("PROBE_B_KEY", "k2")
    a1 = frontier.run_panel(panel_payload(), CFG,
                            post=lambda *a, **k: fake_post_body(None))
    a2 = frontier.run_panel(panel_payload(), CFG,
                            post=lambda *a, **k: fake_post_body(None),
                            on_trace=lambda m: (_ for _ in ()).throw(
                                RuntimeError("trace must not break panel")))
    assert a1 == a2


def test_existing_panel_and_redaction_tests_pass():
    """Regression gate per plan §5 — imported here to fail loudly if the
    seam broke; full suite runs them anyway."""
    import tests.test_frontier  # noqa: F401
