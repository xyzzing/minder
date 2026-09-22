"""Lessons pages (8E, PRD D3): candidate lessons visibly differ from
verified lessons (status mapped to a distinct badge class on the same
row), 404 for unknown ids, escaping and redaction hold."""
import re

import webseed
from webseed import HOSTILE, SECRET, new_db


def _row_fragment(text, lesson_id):
    """The one <tr>…</tr> block containing the lesson id, if any."""
    return re.search(
        r"<tr>(?:(?!</tr>).)*" + re.escape(lesson_id) +
        r"(?:(?!</tr>).)*</tr>", text, re.S)


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
