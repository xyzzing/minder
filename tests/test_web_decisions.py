"""Decision traces page (8E, PRD D3): shadow traces render with the
policy decision separated from the model recommendation; empty DB is
safe; hostile content escaped."""
import webseed
from webseed import HOSTILE, new_db


def test_decisions_page_renders_traces(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_decision(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/decisions").text
    assert "failure-triage" in text and "inspect" in text
    assert "dt1" in text


def test_decisions_empty_db_is_safe(tmp_path, monkeypatch):
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    resp = client.get("/decisions")
    assert resp.status_code == 200 and "not available" in resp.text


def test_decisions_hostile_content_escaped(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_decision(dbp)  # failure_key carries the hostile payload
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/decisions").text
    assert "&lt;script&gt;" in text and HOSTILE not in text
