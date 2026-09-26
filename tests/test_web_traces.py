"""Trace review console pages (Slice 4): `/traces` and `/traces/{id}`.

The console stays read-only: these routes render stored reviews and
nothing else. Confirming findings and converting them to regression cases
are writes and live in the CLI, so a reviewer working in a browser can
never change what the agent does.
"""
import webseed
from webseed import new_db

from memory import trace_reviews

FINDING = {
    "finding_id": "trf_abc123",
    "session_id": "session-x",
    "evaluator": "success_loop",
    "rule_id": "success-loop-same-result",
    "severity": "medium",
    "evidence": {"ds_seqs": [10, 12, 14],
                 "excerpts": ["  0 181.2k  0  0  1.24M"],
                 "call_ids": ["c1", "c2", "c3"]},
    "message": "bash produced the same result 3 times in 30 min "
               "with no new evidence",
    "suggested_fix": "Verify the goal was actually reached.",
}
RUN = {"run_id": "mndr_run_x", "source": {"session_id": "session-x"}}


def _store(dbp, findings=None, summary=None, report=None):
    return trace_reviews.store_review(
        RUN, findings if findings is not None else [FINDING],
        summary if summary is not None else {"findings": 1,
                                             "highest_severity": "medium",
                                             "tool_calls": 12},
        report=report or {"status": "ok"}, db_path=dbp)


def test_traces_page_renders_empty_state(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/traces")
    assert resp.status_code == 200
    assert "no stored reviews yet" in resp.text


def test_traces_page_lists_a_stored_review(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    review_id, status = _store(dbp)
    assert status == "ok"
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/traces")
    assert resp.status_code == 200
    assert review_id in resp.text
    assert "session-x" in resp.text
    assert "medium" in resp.text


def test_trace_detail_renders_findings(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    review_id, _ = _store(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get(f"/traces/{review_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "success-loop-same-result" in body
    assert "trf_abc123" in body
    assert "10, 12, 14" in body          # cited events, so it is checkable
    assert "unreviewed" in body
    assert "no feedback yet" in body


def test_trace_detail_shows_a_confirmed_verdict(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    review_id, _ = _store(dbp)
    trace_reviews.store_feedback(review_id, "run", "evidence_quality",
                                 finding_id="trf_abc123",
                                 finding_verdict="confirm",
                                 comment="confirmed on review",
                                 reviewer="alice", db_path=dbp)
    client = webseed.client_for(dbp, monkeypatch)
    body = client.get(f"/traces/{review_id}").text
    assert "confirmed" in body
    assert "alice" in body
    assert "evidence_quality" in body


def test_trace_detail_404s_on_an_unknown_review(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    assert client.get("/traces/trv_nope").status_code == 404


def test_console_never_leaks_a_secret_from_a_finding(tmp_path, monkeypatch):
    """Redaction is an invariant on every surface, not just the CLI."""
    secret = "sk-proj-operatorleak99999999"
    dbp = new_db(tmp_path)
    _store(dbp, findings=[dict(FINDING, message=f"leaked {secret}",
                               suggested_fix=f"rotate {secret}")],
           summary={"findings": 1, "highest_severity": "medium"})
    client = webseed.client_for(dbp, monkeypatch)
    listing = client.get("/traces")
    review_id = trace_reviews.list_reviews(db_path=dbp)[0]["review_id"]
    detail = client.get(f"/traces/{review_id}")
    assert secret not in listing.text
    assert secret not in detail.text


def test_a_degraded_review_still_renders(tmp_path, monkeypatch):
    """An evaluator or decompression failure is recorded as a status, not
    an exception, and the console must show it rather than 500."""
    dbp = new_db(tmp_path)
    review_id, _ = _store(dbp, report={"status": "degraded:no-zstd"})
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get(f"/traces/{review_id}")
    assert resp.status_code == 200
    assert "degraded:no-zstd" in resp.text


def test_clean_review_says_clean(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    _store(dbp, findings=[], summary={"findings": 0,
                                     "highest_severity": "none"})
    client = webseed.client_for(dbp, monkeypatch)
    body = client.get("/traces").text
    assert "clean" in body


def test_trace_routes_are_get_only():
    """The console's write-freedom is a property of the app, asserted
    directly so a future write endpoint cannot slip in unnoticed."""
    from minder_web.app import app
    unsafe = {"POST", "PUT", "PATCH", "DELETE"}
    touched = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/traces"):
            continue
        touched.append(path)
        methods = getattr(route, "methods", None)
        if methods:
            assert not methods & unsafe, (path, methods)
    assert "/traces" in touched and "/traces/{review_id}" in touched
