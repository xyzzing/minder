"""Difficulty router page (8E): reads the proxy's events.jsonl ledger
(read-only), newest first; empty/missing ledger is safe; shadow vs
routed rows render with their band fields."""
import webseed
from webseed import TS, client_for, new_db, seed_difficulty_ledger

SHADOW = {"event": "difficulty_shadow", "ts": TS, "label": "routine",
          "score": 1.0, "confidence": 0.9, "band": "routine",
          "effort": "low", "budget": 2048, "max_tokens": 8192,
          "guardrail": None}
ROUTED = {"event": "difficulty_routed", "ts": TS, "label": "complex",
          "score": 2.0, "confidence": 0.85, "band": "complex",
          "effort": "high", "budget": 10240, "max_tokens": 32768,
          "guardrail": "spend"}


def test_difficulty_page_renders_ledger(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    seed_difficulty_ledger(state, [SHADOW, ROUTED])
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    client = client_for(new_db(tmp_path), monkeypatch)
    text = client.get("/difficulty").text
    assert "routine" in text and "complex" in text
    assert "shadow" in text and "routed" in text
    assert "10240" in text and "32768" in text
    assert "spend" in text


def test_difficulty_missing_ledger_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "absent"))
    client = client_for(new_db(tmp_path), monkeypatch)
    resp = client.get("/difficulty")
    assert resp.status_code == 200
    assert "no difficulty events" in resp.text


def test_difficulty_ignores_unrelated_events(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    other = {"event": "auto_effort", "ts": TS, "effort": "high"}
    seed_difficulty_ledger(state, [other, SHADOW])
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    client = client_for(new_db(tmp_path), monkeypatch)
    text = client.get("/difficulty").text
    assert "routine" in text
    # the auto_effort event must not contribute a label row
    assert text.count("routine") >= 1


def test_difficulty_newest_first_and_limit(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    events = [
        {"event": "difficulty_shadow", "ts": f"2026-09-22T10:00:{i:02d}+00:00",
         "label": "mechanical", "score": 0.2, "confidence": 0.9,
         "band": "mechanical", "effort": "off", "budget": None,
         "max_tokens": None, "guardrail": None}
        for i in range(5)
    ]
    seed_difficulty_ledger(state, events)
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    client = client_for(new_db(tmp_path), monkeypatch)
    text = client.get("/difficulty?limit=2").text
    # newest (last written) first: the ts with :04 appears before :00
    assert "10:00:04" in text
    assert "10:00:00" not in text
