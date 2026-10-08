"""Lessons pages (8E, PRD D3): candidate lessons visibly differ from
verified lessons (status mapped to a distinct badge class on the same
row), 404 for unknown ids, escaping and redaction hold, and the
injection ledger (issue #13) shows whether a lesson still fires."""
import re

import webseed
from minder_memory import db as _db
from webseed import HOSTILE, SECRET, new_db


def _row_fragment(text, row_id):
    """The one <tr>…</tr> block containing the row id, if any. Row ids
    are not rendered, so callers pass a value unique to the row."""
    return re.search(
        r"<tr>(?:(?!</tr>).)*" + re.escape(row_id) +
        r"(?:(?!</tr>).)*</tr>", text, re.S)


def _drop_ledger(dbp):
    """Simulate a store predating migration 015: the table is gone, so
    the read side must classify, not guess."""
    conn = _db.connect(dbp)
    try:
        conn.execute("DROP TABLE learning_injections")
        conn.commit()
    finally:
        conn.close()


def test_candidate_badge_distinct_from_verified(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_v", status="verified",
                       instruction="verified instruction")
    webseed.add_lesson(dbp, lesson_id="les_c", status="candidate",
                       instruction="candidate instruction")
    client = webseed.client_for(dbp, monkeypatch)
    default_text = client.get("/lessons").text  # verified-only view
    assert _row_fragment(default_text, "les_v")
    assert "badge-verified" in _row_fragment(default_text,
                                             "les_v").group(0)
    assert not _row_fragment(default_text, "les_c")  # inert, hidden

    cand_text = client.get("/lessons?status=candidate").text
    cand_row = _row_fragment(cand_text, "les_c")
    assert cand_row and "badge-candidate" in cand_row.group(0)
    # the two statuses map to different badge classes
    assert "badge-candidate" != "badge-verified"


def test_lessons_status_filter(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_i", status="invalidated")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/lessons?status=invalidated").text
    assert "les_i" in text


def test_lesson_detail_and_404(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/lessons/les1")
    assert resp.status_code == 200
    assert "instruction" in resp.text
    assert client.get("/lessons/ghost").status_code == 404


def test_lesson_escaping_and_redaction(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_h",
                       instruction=f"trust {HOSTILE} and {SECRET}")
    client = webseed.client_for(dbp, monkeypatch)
    for path in ("/lessons", "/lessons/les_h", "/lessons?status=all"):
        text = client.get(path).text
        assert SECRET not in text
        assert "&lt;script&gt;" in text or "les_h" not in text
    detail = client.get("/lessons/les_h").text
    assert HOSTILE not in detail


def test_lesson_detail_shows_injection_history(tmp_path, monkeypatch):
    """Issue #13: the lesson page answers "does this still fire?". The
    tier that produced each injection is rendered, and the ledger's
    failure_key is redacted like every other free-text field."""
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_i")
    webseed.add_injection(dbp, injection_id="inj_new", lesson_id="les_i",
                          ts="2026-09-23T10:00:00+00:00", tier="exact",
                          chars=137)
    webseed.add_injection(dbp, injection_id="inj_old", lesson_id="les_i",
                          ts="2026-09-20T10:00:00+00:00", tier="family",
                          chars=91, mode="retrieve")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/lessons/les_i").text
    row_new = _row_fragment(text, "inj_new")
    row_old = _row_fragment(text, "inj_old")
    assert row_new and "exact" in row_new.group(0)
    assert "137" in row_new.group(0)
    assert row_old and "family" in row_old.group(0)
    assert "91" in row_old.group(0)
    # newest first: the ledger is read as "what happened last"
    assert text.index("inj_new") < text.index("inj_old")
    # the seeded failure_key carries the marker secret
    assert SECRET not in text


def test_lesson_detail_empty_ledger_is_not_a_misleading_zero(
        tmp_path, monkeypatch):
    """A lesson with no rows says "never injected"; a store without the
    ledger table says "not available". Neither renders as a bare 0."""
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_q")
    client = webseed.client_for(dbp, monkeypatch)
    assert "never injected" in client.get("/lessons/les_q").text

    legacy = tmp_path / "legacy.sqlite"
    legacy.write_bytes(dbp.read_bytes())
    _drop_ledger(legacy)
    legacy_client = webseed.client_for(legacy, monkeypatch)
    legacy_text = legacy_client.get("/lessons/les_q").text
    assert "not available" in legacy_text
    assert "never injected" not in legacy_text
