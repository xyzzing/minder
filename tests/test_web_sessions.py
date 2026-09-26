"""The sessions pages: real dsh data, real joins, no fabricated sessions.

Before this, /sessions listed directory names, mangled the project path,
sorted UUIDs alphabetically and could only say "episodes: yes/no" (1 of 94
sessions ever matched). These tests pin the replacement against a
synthetic dsh home, so they never depend on the developer's own ~/.dsh.
"""
import dshseed
from fastapi.testclient import TestClient
from minder_memory import db as _db
from minder_op import dsh_sessions
from webseed import HOSTILE, SECRET

import minder_web.services as services

CWD = "/home/dev/proj"
SID = "session-1111-2222"


def _home(tmp_path, monkeypatch, *, title="fixture title",
          sandbox="workspace-write", archived=False, steps=2,
          tool_failure=False):
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    wrote = dshseed.write_session(root, SID, cwd=CWD, steps=steps,
                                  tool_calls=("bash", "read"),
                                  tool_failure=tool_failure,
                                  sandbox=sandbox)
    dshseed.write_projection(root, SID, cwd=CWD, title=title,
                             sandbox=sandbox, steps=steps)
    dshseed.write_workspace(root, [(CWD, "proj", [SID])],
                            archived=[SID] if archived else [])
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    return root, wrote is not None


def _db_with_session(tmp_path, session_id=SID):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO episodes (episode_id, opened_at,"
                  " closed_at, repo, task_id, status)"
                  " VALUES ('ep1', '2026-09-25T10:00:00+00:00', NULL, ?,"
                  " ?, 'open')", (CWD, session_id))
        _db.write(conn, "INSERT INTO events (event_id, ts, event_type,"
                  " session_id, task_id, repo, repo_version, tool,"
                  " failure_key, action_fingerprint, payload_json,"
                  " redaction_status) VALUES ('ev1',"
                  " '2026-09-25T10:00:00+00:00', 'tool_failure', ?, ?, ?,"
                  " '', 'bash', ?, 'fp', '{}', 'redacted')",
                  (session_id, session_id, CWD, "bash|keyerror|k|a.py"))
    finally:
        conn.close()
    return dbp


def _client(dbp, monkeypatch):
    monkeypatch.setenv("MINDER_WEB_DB", str(dbp))
    from minder_web.app import app
    # loopback base_url: the app refuses non-loopback Host headers
    # (DNS-rebinding guard); TestClient's default host is "testserver".
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_sessions_page_uses_the_real_workspace_and_title(tmp_path,
                                                         monkeypatch):
    _home(tmp_path, monkeypatch)
    dbp = _db_with_session(tmp_path)
    text = _client(dbp, monkeypatch).get("/sessions").text
    assert CWD in text              # real path (previously mangled)
    assert "fixture title" in text
    assert SID in text
    assert "workspace-write" in text
    assert "1 ep" in text           # episode count, not a yes/no badge


def test_sessions_page_lists_projection_and_log_metadata(tmp_path,
                                                         monkeypatch):
    _home(tmp_path, monkeypatch, steps=3)
    dbp = _db_with_session(tmp_path)
    text = _client(dbp, monkeypatch).get("/sessions").text
    assert "93" not in text  # no cross-contamination from the real host
    assert "v4" in text and "KiB" in text


def test_archived_badge(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, archived=True)
    dbp = _db_with_session(tmp_path)
    text = _client(dbp, monkeypatch).get("/sessions").text
    assert "archived" in text


def test_filter_and_sort_are_bounded_and_safe(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    dbp = _db_with_session(tmp_path)
    client = _client(dbp, monkeypatch)
    assert SID in client.get(f"/sessions?q={SID[:12]}").text
    assert SID not in client.get("/sessions?q=no-such-session").text
    assert SID in client.get("/sessions?sort=tokens").text
    assert SID in client.get("/sessions?sort=bogus").text  # falls back
    assert client.get("/sessions?limit=100000").status_code == 200


def test_session_detail_links_episodes_and_events(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, tool_failure=True)
    dbp = _db_with_session(tmp_path)
    text = _client(dbp, monkeypatch).get(f"/sessions/{SID}").text
    assert "ep1" in text
    assert "tool_failure" in text
    assert "bash|keyerror|k|a.py" in text
    assert "/episodes/ep1" in text


def test_session_detail_shows_the_capture_gap_banner(tmp_path, monkeypatch):
    """A session with hook invocations but no persisted rows is exactly
    the fault this work exists for; the page must say so."""
    _home(tmp_path, monkeypatch, steps=2)
    dbp = tmp_path / "empty.sqlite"
    _db.connect(dbp).close()
    text = _client(dbp, monkeypatch).get(f"/sessions/{SID}").text
    assert "capture gap" in text


def test_unknown_session_is_404(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    dbp = _db_with_session(tmp_path)
    assert _client(dbp, monkeypatch).get(
        "/sessions/session-ghost").status_code == 404


def test_hostile_session_text_is_escaped(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, title=f"evil {HOSTILE}")
    dbp = _db_with_session(tmp_path)
    text = _client(dbp, monkeypatch).get("/sessions").text
    assert HOSTILE not in text
    assert "&lt;script&gt;" in text


def test_services_never_leak_secrets_from_the_db(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    dbp = _db_with_session(tmp_path)
    ctx = services.session_detail_page(dbp, SID)
    assert ctx is not None
    assert SECRET not in str(ctx)


def test_adapter_reports_missing_surfaces_without_raising(tmp_path,
                                                          monkeypatch):
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "absent"))
    model = dsh_sessions.list_sessions()
    assert model["rows"] == []
    assert model["counts"]["sessions"] == 0
    assert dsh_sessions.session_detail(SID) is None
    assert dsh_sessions.projection_cache() == {}
    assert dsh_sessions.workspace_registry() == ({}, set())


def test_adapter_tolerates_a_corrupt_projection_cache(tmp_path,
                                                      monkeypatch):
    root = tmp_path / "dsh"
    directory = root / "storages" / "session_projcache" / "sessions"
    directory.mkdir(parents=True)
    (directory / f"{SID}.json").write_text("{not json")
    (directory / "session-ok.json").write_text("[]")
    (root / "storages" / "workspace.json").write_text("null")
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    assert dsh_sessions.projection_cache() == {}
    assert dsh_sessions.workspace_registry() == ({}, set())


def test_log_stats_are_derived_from_the_log(tmp_path, monkeypatch):
    root, wrote = _home(tmp_path, monkeypatch, steps=2)
    assert wrote, "no zstd writer available"
    entry = next(e for e in dsh_sessions.session_dirs(root)
                 if e["session_id"] == SID)
    stats = dsh_sessions.log_stats(entry["log_path"])
    assert stats["hook_invocations"] == stats["hook_results"] > 0
    assert stats["tool_calls"] == 4
    assert stats["sandbox_mode"] == "workspace-write"
    assert dsh_sessions.hook_duration_stats(stats["hook_ms"])["n"] == \
        stats["hook_invocations"]


def test_route_surface_is_still_get_only():
    from minder_web.app import app
    unsafe = {"POST", "PUT", "DELETE", "PATCH"}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            assert not methods & unsafe, (route.path, methods)


def test_non_loopback_host_header_is_refused(monkeypatch):
    """DNS-rebinding guard: a page at an attacker domain re-resolved to
    127.0.0.1 sends its own hostname in Host; the console must not answer
    it, because the browser would hand the private evidence in the response
    to that page's scripts."""
    from minder_web.app import app
    monkeypatch.delenv("MINDER_WEB_DB", raising=False)
    from fastapi.testclient import TestClient
    client = TestClient(app, base_url="http://evil.example:8765")
    resp = client.get("/healthz")
    assert resp.status_code == 403
    assert "evil.example" in resp.text
    # loopback names still answer
    loopback = TestClient(app, base_url="http://127.0.0.1:8765")
    assert loopback.get("/healthz").status_code == 200
    assert TestClient(app, base_url="http://localhost:8765")\
        .get("/healthz").status_code == 200
