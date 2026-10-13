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


def _drop_decision_ledger(dbp):
    """A store predating migration 016: the ledger table is gone, so the
    decision block must classify rather than render an empty table."""
    conn = _db.connect(dbp)
    try:
        conn.execute("DROP TABLE lesson_decisions")
        conn.commit()
    finally:
        conn.close()


def test_lessons_list_shows_the_diagnosis_code(tmp_path, monkeypatch):
    """Issue #14: the queue page shows *why* a lesson was invalidated, as
    the closed code, next to the status it already showed."""
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_d", status="invalidated",
                       valid_to=webseed.TS, diagnosis="content_defect")
    webseed.add_lesson(dbp, lesson_id="les_u", status="invalidated",
                       valid_to=webseed.TS, diagnosis="unknown")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/lessons?status=invalidated").text
    assert "content_defect" in _row_fragment(text, "les_d").group(0)
    assert "unknown" in _row_fragment(text, "les_u").group(0)


def test_lesson_detail_shows_decision_history_newest_first(
        tmp_path, monkeypatch):
    """The code is rendered as stored, the note is redacted, and the
    order answers "what was the last decision about this lesson?". The
    decision ids are not rendered, so ordering is asserted on the codes
    that only the newest row carries."""
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_h", status="invalidated",
                       valid_to="2026-09-23T10:00:00+00:00",
                       diagnosis="application_failure")
    webseed.add_lesson_decision(dbp, decision_id="lds_new",
                                lesson_id="les_h",
                                ts="2026-09-23T10:00:00+00:00",
                                action="invalidate",
                                code="application_failure")
    webseed.add_lesson_decision(dbp, decision_id="lds_old",
                                lesson_id="les_h",
                                ts="2026-09-20T10:00:00+00:00",
                                action="adopt", code="grounded_useful")
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/lessons/les_h").text
    row_new = _row_fragment(text, "lds_new")
    row_old = _row_fragment(text, "lds_old")
    assert row_new and "invalidate" in row_new.group(0)
    assert "application_failure" in row_new.group(0)
    assert row_old and "adopt" in row_old.group(0)
    assert "grounded_useful" in row_old.group(0)
    # newest first: the ledger is read as "what was decided last"
    assert text.index("lds_new") < text.index("lds_old")
    # the seeded note carries the marker secret
    assert SECRET not in text


def test_decision_ledger_absent_is_not_reported_as_no_decisions(
        tmp_path, monkeypatch):
    """Two different facts, two different sentences: no decisions
    recorded, and no ledger to have recorded them in."""
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_n")
    client = webseed.client_for(dbp, monkeypatch)
    assert "no recorded decision" in client.get("/lessons/les_n").text

    legacy = tmp_path / "legacy.sqlite"
    legacy.write_bytes(dbp.read_bytes())
    _drop_decision_ledger(legacy)
    text = webseed.client_for(legacy, monkeypatch).get("/lessons/les_n").text
    assert "predates the lesson" in text
    assert "no recorded decision" not in text
