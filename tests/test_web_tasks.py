"""The /tasks/{task_id} page: the BD-1 seed slice (issue #84).

Read-only contract view: the work order renders with its acceptance
criterion, room family and revision; a task with no work order says so
honestly (never a zero, never a fabricated contract); hostile contract
text is escaped like every other stored value.
"""
import webseed
from webseed import HOSTILE, new_db


def test_task_page_renders_the_contract(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_work_order(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/tasks/t-1041").text
    assert "All 5 configured comparisons match." in text
    assert "finance/reconciliation" in text
    assert "windtunnel.work-order/v1" in text


def test_task_page_names_the_revision(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_work_order(dbp, revision=2)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/tasks/t-1041").text
    assert "2" in text and "revision" in text.lower()


def test_task_page_not_declared_is_honest(tmp_path, monkeypatch):
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    resp = client.get("/tasks/t-unknown")
    assert resp.status_code == 200
    assert "not declared" in resp.text


def test_task_page_escapes_hostile_contract(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_work_order(dbp, criterion=f"pass if {HOSTILE}")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/tasks/t-1041").text
    assert "&lt;script&gt;" in text and HOSTILE not in text
