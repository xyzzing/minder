"""Engine console page (issue #3): the engine registry renders with the
active engine and unit health, and the switch route is a console write
path (section 7.6 preconditions since issue #89 stage 3: the POST must
carry the confirm page's CSRF token and an actor). Loopback-only like
every console route."""
import json
import re

import minder
import minder_web.services as services
import webseed
from webseed import SECRET, new_db


def _switch_form(client, engine):
    """The form fields a browser submits: the CSRF token the confirm
    page embedded for this engine, plus the actor."""
    page = client.get(f"/engine/switch?engine={engine}")
    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"',
                      page.text)
    assert token, "the confirm page must embed the csrf token"
    return {"csrf_token": token.group(1), "actor": "operator"}


def _registry_config(tmp_path, monkeypatch):
    cfg = tmp_path / "minder.json"
    monkeypatch.setattr(minder, "CFG_PATH", cfg)
    cfg.write_text(json.dumps({
        "engines": {
            "llama": {"upstream": "http://127.0.0.1:8080",
                      "unit": "u-llama"},
            "strata": {"upstream": "http://127.0.0.1:8081",
                       "unit": "u-strata"},
        },
        "active_engine": "strata"}))
    return cfg


def test_engine_page_renders_registry(tmp_path, monkeypatch):
    _registry_config(tmp_path, monkeypatch)
    monkeypatch.setattr(services, "_engine_rows", lambda: [
        {"name": "llama", "active": False, "upstream": "http://127.0.0.1:8080",
         "unit": "u-llama", "unit_state": "inactive", "healthy": True},
        {"name": "strata", "active": True, "upstream": "http://127.0.0.1:8081",
         "unit": "u-strata", "unit_state": "active", "healthy": True},
    ])
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/engine")
    assert resp.status_code == 200
    text = resp.text
    assert "strata" in text and "llama" in text
    assert "active" in text
    assert "/engine/switch" in text
    assert SECRET not in text


def test_engine_page_answers_in_plain_words(tmp_path, monkeypatch):
    """Issue #12: the page leads with its conclusion before the table.
    /engine includes the answer partial but answers.py had no branch for
    it, so the partial rendered nothing and the page opened with a table.
    The sentence must name the engine actually running, and the unhealthy
    one when there is one - not just a row count."""
    _registry_config(tmp_path, monkeypatch)
    monkeypatch.setattr(services, "_engine_rows", lambda: [
        {"name": "llama", "active": False, "upstream": "http://127.0.0.1:8080",
         "unit": "u-llama", "unit_state": "inactive", "healthy": True},
        {"name": "strata", "active": True, "upstream": "http://127.0.0.1:8081",
         "unit": "u-strata", "unit_state": "active", "healthy": True},
    ])
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    text = client.get("/engine").text
    assert "the short answer" in text
    assert "strata" in text.split("<table")[0]

    monkeypatch.setattr(services, "_engine_rows", lambda: [
        {"name": "strata", "active": True, "upstream": "http://127.0.0.1:8081",
         "unit": "u-strata", "unit_state": "active", "healthy": False},
    ])
    text = client.get("/engine").text
    lead = text.split("<table")[0]
    assert "the short answer" in lead
    assert "not answering" in lead



def test_engine_switch_route_flips(tmp_path, monkeypatch):
    _registry_config(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(services, "engine_switch",
                        lambda name: seen.append(name) or
                        {"switched": True, "from": "llama", "engine": name})
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.post("/engine/switch?engine=strata",
                       data=_switch_form(client, "strata"),
                       follow_redirects=False)
    assert resp.status_code == 303
    assert seen == ["strata"]


def test_engine_switch_route_names_failure(tmp_path, monkeypatch):
    _registry_config(tmp_path, monkeypatch)

    def boom(name):
        raise minder_op_engines_error(name)

    monkeypatch.setattr(services, "engine_switch", boom)
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.post("/engine/switch?engine=strata",
                       data=_switch_form(client, "strata"))
    assert resp.status_code == 200
    assert "strata" in resp.text


def test_engine_switch_post_for_unknown_engine_is_refused(tmp_path,
                                                          monkeypatch):
    """No confirm page exists for an unknown engine, so no CSRF token
    was ever issued for it: the section 7.6 guard refuses before the
    handler can run. The confirm GET is what names the unknown engine."""
    _registry_config(tmp_path, monkeypatch)
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    named = client.get("/engine/switch?engine=nope")
    assert named.status_code == 200
    assert "nope" in named.text
    resp = client.post("/engine/switch?engine=nope",
                       data={"csrf_token": "0" * 64, "actor": "operator"})
    assert resp.status_code == 400


def minder_op_engines_error(name):
    from minder_op.engines import EngineError
    return EngineError(f"unknown engine '{name}'")


def test_switch_needs_a_confirm_step_that_names_the_unit(tmp_path,
                                                         monkeypatch):
    """The console's one write path stops a systemd unit and flips the
    live upstream. A click on the row must land on a page that says what
    it will stop, not on the switch itself (issue #12)."""
    _registry_config(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(services, "engine_switch",
                        lambda name: seen.append(name) or
                        {"switched": True, "from": "strata",
                         "engine": name})
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/engine/switch?engine=llama&confirm=1")
    assert resp.status_code == 200
    assert "u-llama" in resp.text
    assert "llama" in resp.text
    assert seen == [], "the confirm view must not switch anything"
    assert 'method="post"' in resp.text


def test_engine_page_links_to_the_confirm_step_not_the_switch(
        tmp_path, monkeypatch):
    _registry_config(tmp_path, monkeypatch)
    monkeypatch.setattr(services, "_engine_rows", lambda: [
        {"name": "llama", "active": False,
         "upstream": "http://127.0.0.1:8080",
         "unit": "u-llama", "unit_state": "inactive", "healthy": True}])
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/engine").text
    assert "/engine/switch?engine=llama&confirm=1" in text


def test_footer_names_the_write_path(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/").text
    assert "one write path" in text
