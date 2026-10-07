"""Domains page tests (issue #8): the read-only pack surface - active
profile, suites with plain-language verdicts, rulebook provenance,
routing mode. A missing or malformed config degrades honestly, never a
500; the seeded secret never renders."""
import json

import webseed
from webseed import HOSTILE, SECRET, new_db

_PRIMARY_RULE = "sg_gst.rate"  # shipped rulebook: verification primary


def _config(tmp_path, config):
    path = tmp_path / "minder.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_domains_page_renders_profile_suites_provenance(tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", _config(tmp_path, {
        "profile": "finance",
        "profiles": {"finance": {"l1_budget": 4096}}}))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    text = resp.text
    # active profile named, its override visible as a plain value
    assert "finance" in text
    assert "4096" in text
    # suites listed with their plain-language verdicts
    assert "legal-core-v1" in text and "finance-core-v1" in text
    assert "no pinned baseline yet" in text
    # provenance grades rendered with the secondary gloss
    assert "primary" in text and "secondary" in text
    assert "re-verify before relying on it externally" in text
    assert _PRIMARY_RULE in text
    # seeded fixture secrets never reach the page
    assert SECRET not in text and HOSTILE not in text


def test_domains_page_without_config_shows_default_profile(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", str(tmp_path / "absent.json"))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    assert "coding (default" in resp.text
    assert "no pinned baseline yet" in resp.text


def test_domains_page_malformed_config_is_a_named_error(tmp_path,
                                                        monkeypatch):
    bad = tmp_path / "minder.json"
    bad.write_text("{not json")
    monkeypatch.setenv("MINDER_CONFIG", str(bad))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    assert "JSONDecodeError" in resp.text
    assert "coding (default" in resp.text


def test_domains_page_routing_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", _config(tmp_path, {
        "difficulty_router": "shadow"}))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/domains").text
    assert "shadow - proposals are logged, nothing is rerouted yet" in text
