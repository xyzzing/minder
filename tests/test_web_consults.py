"""Consults pages (8E, PRD D3): labels come from frontier_evals (007)
— the legacy 003 INTEGER never renders as a classification — raw
prompt/response text is never present, unknown trace 404s."""
import webseed
from webseed import new_db


def test_consult_list_label_from_frontier_evals(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_consult(dbp)  # legacy 003 column poisoned with 7
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/consults").text
    assert "tr1" in text and "helpful" in text
    # the 003 INTEGER is not displayed as a label
    body_after_id = text.split("tr1", 1)[1]
    assert ">7<" not in body_after_id


def test_consult_detail_hashes_only(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_consult(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/consults/tr1")
    assert resp.status_code == 200
    assert "request_hash" in resp.text and "hashes" in resp.text


def test_consult_unknown_404(tmp_path, monkeypatch):
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    assert client.get("/consults/ghost").status_code == 404


def test_consults_empty_db_renders_not_available(tmp_path, monkeypatch):
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    resp = client.get("/consults")
    assert resp.status_code == 200 and "not available" in resp.text
