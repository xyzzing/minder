"""The console inbox (#89, stage 3): the page lists pending would-accept
triggers with action-specific choices and an actor field, and the
resolve POST carries the section 7.6 preconditions (CSRF bound to the
item and its revision, an actor, an audit row). No test touches the
network or the live state dir.
"""
import re

import pytest
from fastapi.testclient import TestClient
from minder_op import dispatch_attention, dispatch_store
from minder_op.queries import _rows
from webseed import new_db

TID = "github:111:2026-11-07T10:00:00Z"


def _trigger(tid=TID, sender="alice"):
    return {"trigger_id": tid, "ts": "2026-11-07T10:00:00Z",
            "source": "github", "repo": "xyzzing/minder",
            "kind": "issue", "sender": sender}


def _would():
    return {"decision": "would_accept", "reason": None,
            "sender_class": "listed"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    import minder
    dbp = new_db(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(minder, "STATE_DIR", state)
    dispatch_store.record_trigger(dbp, _trigger(), _would())
    from minder_web.app import app
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    monkeypatch.setenv("MINDER_WEB_DB", str(dbp))
    return client, dbp


def _form_from_page(body, tid=TID):
    """The hidden csrf token and revision the page embedded for one
    item, exactly what a browser would submit."""
    row = re.search(
        r'action="/inbox/' + re.escape(tid) + r'/resolve".*?'
        r'name="csrf_token" value="([0-9a-f]+)".*?'
        r'name="revision" value="([0-9a-f]+)"', body, re.S)
    assert row, "the page must embed the csrf token and revision"
    return row.group(1), row.group(2)


def _human_rows(dbp):
    return _rows(dbp, "SELECT actor, decision, affects_json"
                      " FROM human_input_events")


def test_inbox_page_lists_pending_items(env):
    client, _dbp = env
    page = client.get("/inbox")
    assert page.status_code == 200
    assert TID in page.text
    assert "alice" in page.text
    assert "approve" in page.text  # action-specific label, not a bare yes
    assert 'name="actor"' in page.text
    _form_from_page(page.text)  # csrf + revision embedded


def test_inbox_empty_state_names_the_truth(env, tmp_path):
    client, dbp = env
    dispatch_attention.resolve(dbp, TID, "observe", actor="zacch")
    page = client.get("/inbox")
    assert page.status_code == 200
    assert "nothing needs you" in page.text


def test_approve_redirects_and_records_attribution(env):
    client, dbp = env
    token, revision = _form_from_page(client.get("/inbox").text)
    resp = client.post(f"/inbox/{TID}/resolve", data={
        "csrf_token": token, "revision": revision, "choice": "approve",
        "actor": "zacch"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/inbox"
    rows = _human_rows(dbp)
    assert len(rows) == 1
    assert rows[0]["actor"] == "zacch"
    assert rows[0]["decision"] == "approved"
    assert TID in rows[0]["affects_json"]


def test_repeat_post_is_a_no_op(env):
    client, dbp = env
    token, revision = _form_from_page(client.get("/inbox").text)
    payload = {"csrf_token": token, "revision": revision,
               "choice": "approve", "actor": "zacch"}
    client.post(f"/inbox/{TID}/resolve", data=payload,
                follow_redirects=False)
    client.post(f"/inbox/{TID}/resolve", data=payload,
                follow_redirects=False)
    assert len(_human_rows(dbp)) == 1


def test_bad_csrf_is_refused(env):
    client, dbp = env
    resp = client.post(f"/inbox/{TID}/resolve", data={
        "csrf_token": "0" * 64, "revision": "x", "choice": "approve",
        "actor": "zacch"})
    assert resp.status_code == 400
    assert "csrf" in resp.text
    assert _human_rows(dbp) == []


def test_missing_actor_is_refused(env):
    client, dbp = env
    token, revision = _form_from_page(client.get("/inbox").text)
    resp = client.post(f"/inbox/{TID}/resolve", data={
        "csrf_token": token, "revision": revision, "choice": "approve",
        "actor": "   "})
    assert resp.status_code == 400
    assert "actor" in resp.text
    assert _human_rows(dbp) == []


def test_stale_revision_is_refused(env):
    """The D-04 rule: the page said observe, the config flipped to auto
    before the POST landed, and the approval state the operator saw is
    invalidated rather than honored. The token itself stays valid (it
    binds the item and revision, not the mode) - the revision check is
    what refuses."""
    import json

    import minder
    client, dbp = env
    cfg = minder.STATE_DIR / "dispatch.json"
    cfg.write_text(json.dumps({"repo": "xyzzing/minder",
                               "accept_mode": "observe"}))
    token, revision = _form_from_page(client.get("/inbox").text)
    cfg.write_text(json.dumps({"repo": "xyzzing/minder",
                               "accept_mode": "auto"}))
    resp = client.post(f"/inbox/{TID}/resolve", data={
        "csrf_token": token, "revision": revision, "choice": "approve",
        "actor": "zacch"})
    assert resp.status_code == 400
    assert "revision" in resp.text
    assert _human_rows(dbp) == []


def test_engine_switch_refuses_a_bare_post(env):
    client, _dbp = env
    resp = client.post("/engine/switch?engine=strata", data={})
    assert resp.status_code == 400
    assert "csrf" in resp.text
