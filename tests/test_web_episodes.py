"""Episodes pages (8E, PRD D3): list + timeline render on a temp DB,
404 for unknown ids, hostile DB content is escaped, no secret leaks."""
import webseed
from webseed import HOSTILE, SECRET, new_db


def test_episode_list_and_detail(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_episode(dbp)
    webseed.add_event(dbp, payload='{"excerpt": "boom"}')
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/episodes").text
    assert "ep1" in text and "open" in text
    detail = client.get("/episodes/ep1")
    assert detail.status_code == 200
    assert "tool_failure" in detail.text
    assert "boom" in detail.text


def test_episode_unknown_id_404(tmp_path, monkeypatch):
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    assert client.get("/episodes/ghost").status_code == 404


def test_hostile_db_content_is_escaped(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_episode(dbp)  # repo field carries the hostile payload
    client = webseed.client_for(dbp, monkeypatch)
    for path in ("/episodes", "/episodes/ep1"):
        text = client.get(path).text
        assert "&lt;script&gt;" in text
        assert HOSTILE not in text


def test_redacted_excerpt_not_leaked(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_episode(dbp)
    webseed.add_event(dbp, payload=f'{{"excerpt": "key {SECRET}"}}')
    client = webseed.client_for(dbp, monkeypatch)
    assert SECRET not in client.get("/episodes/ep1").text
