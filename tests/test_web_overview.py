"""Overview + healthz page tests (8E, PRD D2): HTTP 200 on a temp DB,
shared weekly-summary headings and operator focus, no raw secret
fixture, JSON healthz; a missing DB renders 'not available', never a
500."""
import webseed
from webseed import SECRET, new_db


def test_overview_renders_shared_summary(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_gap(dbp)  # gives the focus list something honest to say
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    text = resp.text
    assert "weekly workflow summary" in text
    assert "operator focus" in text
    assert "current flags" in text
    assert "minder-op gaps ls" in text  # shared focus actions
    assert SECRET not in text


def test_overview_verdicts_lead_the_page(tmp_path, monkeypatch):
    """The landing page answers 'what to improve first' before any
    data table: the verdict section renders, ahead of the weekly
    summary, and the header chip reports recording status."""
    dbp = new_db(tmp_path)
    webseed.add_gap(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "what to improve first" in text
    assert text.index("what to improve first") < \
        text.index("weekly workflow summary")
    # every page carries the recording chip (fresh seeded events)
    assert "recording:" in text


def test_overview_window_param_reaches_capture_strip(tmp_path,
                                                     monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/?window_hours=48").text
    assert "past 48h" in text
    assert "48h" in text  # verdict section label carries the window


def test_overview_shows_env_flags_display_only(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "MINDER_ASSIST" in text and "retrieve" in text


def test_overview_missing_db_renders_not_available(tmp_path, monkeypatch):
    client = webseed.client_for(tmp_path / "nope.sqlite", monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "not available" in resp.text


def _latest_schema():
    from minder_memory import db as _db
    return max(int(f.name.split("_", 1)[0]) for f in
               _db.MIGRATIONS_DIR.glob("*.sql") if f.name[0].isdigit())


def test_healthz_json_no_sensitive_data(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    data = client.get("/healthz").json()
    assert data["status"] == "ok"
    assert data["schema_version"] == _latest_schema()
    assert str(dbp) not in str(data)
    # degraded, not 500, when the store is missing
    client = webseed.client_for(tmp_path / "nope.sqlite", monkeypatch)
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.json()["status"] == \
        "degraded"


def test_entrypoint_refuses_non_loopback_bind(capsys):
    from minder_web.__main__ import build_parser, main
    assert build_parser().parse_args([]).host == "127.0.0.1"
    assert main(["--host", "0.0.0.0"]) == 1
    assert "localhost-only" in capsys.readouterr().err
