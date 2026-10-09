"""Overview + healthz page tests (8E, PRD D2): HTTP 200 on a temp DB,
shared weekly-summary headings and operator focus, no raw secret
fixture, JSON healthz; a missing DB renders 'not available', never a
500."""
import webseed
from minder_memory import db as _db
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
    assert "past 2 days" in text  # human window label, not raw hours
    # day/week toggle links present, day active at default window
    assert "window:" in text
    assert '/?window_hours=24"' in text and '/?window_hours=168"' in text


def test_nav_groups_links_behind_advanced_drawer(tmp_path, monkeypatch):
    """Wave 4: the nav answers three questions; operator-only surfaces
    sit behind the advanced drawer (all still one click, GET-only)."""
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    for label in ("how am I doing?", "what happened?",
                  "what has it learned?"):
        assert label in text
    assert 'class="adv"' in text and "<summary>advanced</summary>" in text
    for href in ('href="/capture"', 'href="/difficulty"',
                 'href="/traces"'):
        assert href in text
    # plain-language glosses on the landing page
    assert "tool calls saved" in text
    assert "save path ready" in text


def _seed_injections(dbp, lessons=(None, "L1", None, "L2", None)):
    """Rows straight into the ledger: a miss is a row with no lesson_id,
    exactly as policy.py writes one."""
    conn = _db.connect(dbp)
    try:
        for i, lesson in enumerate(lessons):
            _db.write(
                conn,
                "INSERT INTO learning_injections (injection_id, ts,"
                " session_id, failure_key, lesson_id, tier,"
                " digest_injected, chars_injected, assist_mode)"
                " VALUES (?, '2026-10-01T00:00:00+00:00', 's1', 'k1', ?, ?,"
                " 1, ?, 'retrieve')",
                (f"inj_{i}", lesson, "n/a" if lesson is None else "exact",
                 0 if lesson is None else 240))
    finally:
        conn.close()


def test_learning_panel_shows_the_retrieval_hit_rate(tmp_path, monkeypatch):
    """Issue #20 acceptance 3: the learning panel states the plane-level
    hit rate - of the decisions that asked for a lesson, how many got one
    - and names the misses, so a per-lesson impact split has a base rate
    to be read against."""
    dbp = new_db(tmp_path)
    _seed_injections(dbp)
    text = webseed.client_for(dbp, monkeypatch).get("/").text
    assert "retrieval hit rate" in text
    assert "2 of 5 asked" in text
    assert "40.0%" in text
    assert "3 missed" in text


def test_learning_panel_says_not_available_without_the_ledger(
        tmp_path, monkeypatch):
    """A store predating migration 015 gets the same words the CLI uses,
    never a 0 % that reads as a retrieval failure."""
    dbp = new_db(tmp_path)
    conn = _db.connect(dbp)
    conn.execute("DROP TABLE learning_injections")
    conn.commit()
    conn.close()
    text = webseed.client_for(dbp, monkeypatch).get("/").text
    assert "retrieval hit rate" in text
    assert "not available" in text
    assert "0 of 0 asked" not in text


def test_overview_glosses_weekly_jargon(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "the same error coming back" in text
    assert "did the outside model help?" in text


def test_overview_shows_env_flags_display_only(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "MINDER_ASSIST" in text and "retrieve" in text


def test_overview_freshness_strip_names_its_data_age(tmp_path,
                                                     monkeypatch):
    """DDIA (issue #8): a derived page carries its lag - the landing
    states through when the evidence runs and warns when recording has
    gone stale instead of silently showing old numbers."""
    dbp = new_db(tmp_path)
    webseed.add_episode(dbp)
    webseed.add_event(dbp)  # fixed seed ts, days behind the wall clock
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "evidence through" in text
    assert "Sep 22" in text
    assert "no new evidence for" in text
    assert 'href="/capture"' in text


def test_overview_freshness_unknown_without_events(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/").text
    assert "evidence through" in text
    assert "no new evidence for" not in text


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
