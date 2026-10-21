"""Stated preferences become lessons (issue #22).

A user says "for this repo always run the tests through make, not
pytest". The agent made no mistake, so there is no episode, and the
whole promotion chain (`promote_lesson` needs a verified episode plus
`verification.tests_passed`) has no route for that preference. It lives
in the transcript and the next session re-derives the wrong habit.

The seam is the operator path end to end: `record_human_input` with a
preference kind, then a promotion that names the event, then both
operator planes showing where the lesson came from. The assertions are
on persisted rows and rendered output, never on internal helpers.
"""
import json

import webseed
from minder_memory import db as _db
from minder_memory import lessons as memory_lessons
from minder_memory import retrieval, task_context
from minder_op.cli import EXIT_OK, EXIT_USAGE, main
from webseed import new_db

REPO = "/repo"
PREFERENCE = "run the tests through make, not pytest"


def _ins(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, sql, params)
    finally:
        conn.close()


def _rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _seed_preference_event(dbp, kind="working_preference",
                           authority="user", actor="zac"):
    event, status = task_context.record_human_input(
        kind, actor=actor, authority=authority,
        answer_summary=PREFERENCE,
        affects=[{"type": "repo", "id": REPO}], db_path=dbp)
    assert status == "ok", status
    return event


# --- acceptance 1: the closed vocabulary gains the two kinds ---------

def test_working_preference_and_expert_answer_are_recordable_kinds(tmp_path):
    """A durable working preference and an ideal answer for future work
    are evidence the store can hold; the vocabulary stays closed."""
    dbp = new_db(tmp_path)
    for kind in ("working_preference", "expert_answer"):
        event, status = task_context.record_human_input(
            kind, actor="zac", answer_summary=PREFERENCE, db_path=dbp)
        assert status == "ok", (kind, status)
        assert event["kind"] == kind
    bad, status = task_context.record_human_input(
        "vibe_preference", actor="zac", db_path=dbp)
    assert bad is None
    assert status == "rejected:unknown_kind:vibe_preference"
    rows = _rows(dbp, "SELECT kind FROM human_input_events")
    assert [r["kind"] for r in rows] == ["working_preference",
                                         "expert_answer"]


# --- acceptance 2: a stated preference with no episode produces a
#     lesson. Before the change the same input yields no lesson: the
#     promotion path takes an EPISODE_ID and nothing else. -------------

def test_a_stated_preference_with_no_episode_becomes_a_lesson(tmp_path):
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, status = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    assert status == "ok", status
    assert lesson["instruction"] == PREFERENCE
    assert lesson["status"] == "verified"
    assert lesson["valid_to"] is None
    assert lesson["source_human_input"] == event["event_id"]
    stored = _rows(dbp, "SELECT * FROM lessons WHERE lesson_id = ?",
                   (lesson["lesson_id"],))
    assert len(stored) == 1
    assert stored[0]["instruction"] == PREFERENCE


def test_a_preference_with_no_failure_key_is_retrievable_by_repo(tmp_path):
    """The preference binds a repo, not a failure. Retrieval's exact and
    family tiers match on failure_key, so neither can carry it; the repo
    tier (issue #22) is what does, and the ledger records that tier."""
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    assert lesson["failure_key"] == ""
    found = retrieval.retrieve_lessons(REPO, "some|brand-new|key|none",
                                       db_path=dbp)
    assert [row["lesson_id"] for row in found] == [lesson["lesson_id"]]
    assert found[0]["instruction"] == PREFERENCE
    assert found[0]["tier"] == "repo"


# --- acceptance 3: promote_lesson's gates stay, and the record says
#     what stands in place of tests_passed. ---------------------------

def test_the_promotion_names_the_authority_instead_of_test_evidence(tmp_path):
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp, authority="operator")
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    verification = json.loads(lesson["verification_json"])
    assert verification["tests_passed"] is False
    assert verification["evidence"] == "stated_preference"
    assert verification["human_input_event_id"] == event["event_id"]
    assert verification["authority"] == "operator"
    assert verification["kind"] == "working_preference"


def test_an_unauthoritative_event_does_not_become_a_lesson(tmp_path):
    """`user` and `operator` carry the authority to state how work is
    done here; a `reviewer` does not, so the closed vocabulary plus the
    stored authority is what gates promotion."""
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp, authority="reviewer")
    lesson, status = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    assert lesson is None
    assert status == "rejected:authority-reviewer"
    assert _rows(dbp, "SELECT * FROM lessons") == []


def test_an_unknown_kind_event_does_not_become_a_lesson(tmp_path):
    """The vocabulary is closed at the row level: an event written by
    another writer under an unknown kind is not promotion evidence."""
    dbp = new_db(tmp_path)
    _ins(dbp, "INSERT INTO human_input_events (event_id, ts, actor,"
             " authority, kind, answer_summary) VALUES ('hin-fake',"
             " '2026-10-01T00:00:00+00:00', 'zac', 'user',"
             " 'vibe_preference', ?)", (PREFERENCE,))
    lesson, status = memory_lessons.promote_stated_preference(
        "hin-fake", PREFERENCE, repo=REPO, db_path=dbp)
    assert lesson is None
    assert status == "rejected:kind-vibe_preference"
    assert _rows(dbp, "SELECT * FROM lessons") == []


def test_an_expert_answer_is_promotable_evidence(tmp_path):
    """The second new kind is the ideal answer supplied for future work
    (Reflexio's expert_content input). Recording it is acceptance 1;
    this proves the promotion path treats it as evidence too. Extracting
    a delta from it stays out of scope."""
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp, kind="expert_answer")
    lesson, status = memory_lessons.promote_stated_preference(
        event["event_id"], "answer with the citation first",
        repo=REPO, db_path=dbp)
    assert status == "ok", status
    assert json.loads(lesson["verification_json"])["kind"] == \
        "expert_answer"


def test_a_missing_event_does_not_become_a_lesson(tmp_path):
    dbp = new_db(tmp_path)
    lesson, status = memory_lessons.promote_stated_preference(
        "hin-nope", PREFERENCE, repo=REPO, db_path=dbp)
    assert lesson is None
    assert status == "rejected:no-such-event"


def test_a_preference_without_an_instruction_is_refused(tmp_path):
    """The operator states the instruction; nothing is guessed from the
    event's summary."""
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, status = memory_lessons.promote_stated_preference(
        event["event_id"], "   ", repo=REPO, db_path=dbp)
    assert lesson is None
    assert status == "rejected:no-instruction"


def test_promote_lesson_still_refuses_an_unverified_episode(tmp_path):
    """The episode path's gates are untouched: this is a new entry
    point, not a relaxed one."""
    dbp = new_db(tmp_path)
    webseed.add_episode(dbp, episode_id="ep-open", status="open")
    lesson, status = memory_lessons.promote_lesson(
        "ep-open", PREFERENCE, db_path=dbp)
    assert lesson is None
    assert status == "rejected:episode-status-open"


# --- acceptance 4: the lesson's evidence names the event row, and the
#     operator queue shows the source kind. ---------------------------

def test_lessons_show_names_the_source_kind_and_event(tmp_path, capsys):
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    capsys.readouterr()
    assert main(["--db", str(dbp), "lessons", "show", lesson["lesson_id"]]) \
        == EXIT_OK
    out = capsys.readouterr().out
    assert "working_preference" in out
    assert event["event_id"] in out
    # An episode-sourced lesson keeps saying what it always said.
    assert "source_episode" in out


# --- acceptance 5: both operator planes can record the event and see
#     the lesson it produced. -----------------------------------------

def test_the_op_cli_records_the_event_then_promotes_on_request(tmp_path,
                                                               capsys):
    """Acceptance 5 and 6 together: the command writes the event, and
    the lesson appears only when the operator asks for it with the
    instruction they state."""
    dbp = new_db(tmp_path)
    capsys.readouterr()
    rc = main(["--db", str(dbp), "human", "input", "--kind",
               "working_preference", "--answer", PREFERENCE,
               "--affects-repo", REPO, "--yes"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "working_preference" in out
    assert PREFERENCE in out
    assert _rows(dbp, "SELECT * FROM lessons") == []
    events = _rows(dbp, "SELECT event_id FROM human_input_events")
    assert len(events) == 1
    assert json.loads(_rows(dbp, "SELECT affects_json"
                            " FROM human_input_events")[0]["affects_json"]) \
        == [{"type": "repo", "id": REPO}]
    rc = main(["--db", str(dbp), "lessons", "promote", "--from-event",
               events[0]["event_id"], "--instruction", PREFERENCE,
               "--repo", REPO, "--yes"])
    assert rc == EXIT_OK
    assert "lesson" in capsys.readouterr().out
    lessons = _rows(dbp, "SELECT * FROM lessons")
    assert len(lessons) == 1
    assert lessons[0]["instruction"] == PREFERENCE


def test_the_op_cli_promotes_the_event_in_one_step_when_asked(tmp_path,
                                                              capsys):
    dbp = new_db(tmp_path)
    capsys.readouterr()
    rc = main(["--db", str(dbp), "human", "input", "--kind",
               "working_preference", "--answer", PREFERENCE,
               "--affects-repo", REPO, "--promote", "--yes"])
    assert rc == EXIT_OK
    assert "lesson" in capsys.readouterr().out
    assert len(_rows(dbp, "SELECT * FROM lessons")) == 1


def test_the_op_cli_refuses_a_preference_write_without_yes(tmp_path,
                                                           capsys):
    dbp = new_db(tmp_path)
    capsys.readouterr()
    rc = main(["--db", str(dbp), "human", "input", "--kind",
               "working_preference", "--answer", PREFERENCE,
               "--affects-repo", REPO])
    assert rc == EXIT_USAGE
    out = capsys.readouterr().out
    assert "PLAN" in out
    assert _rows(dbp, "SELECT * FROM human_input_events") == []


def test_the_op_cli_rejects_an_unknown_kind(tmp_path, capsys):
    dbp = new_db(tmp_path)
    capsys.readouterr()
    rc = main(["--db", str(dbp), "human", "input", "--kind",
               "magic_answer", "--answer", "x", "--yes"])
    assert rc == EXIT_USAGE
    assert "unknown_kind" in capsys.readouterr().err


def test_the_op_cli_lists_a_preference_lesson_with_its_source(tmp_path,
                                                              capsys):
    """The operator queue shows the source kind, so rejecting a
    preference-based lesson records a #14 reason code against a real,
    labelled source."""
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    capsys.readouterr()
    assert main(["--db", str(dbp), "lessons", "ls", "--status", "verified"]) \
        == EXIT_OK
    out = capsys.readouterr().out
    assert lesson["lesson_id"] in out
    assert "working_preference" in out


# --- acceptance 5 (web plane): the lesson detail page shows which
#     human input event a lesson came from. ---------------------------

def test_the_lesson_page_shows_the_human_input_event(tmp_path):
    from minder_web import services
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    model = services.lesson_detail(dbp, lesson["lesson_id"])
    assert model["lesson"]["source_human_input"] == event["event_id"]
    assert model["human_input"]["kind"] == "working_preference"
    assert model["human_input"]["actor"] == "zac"
    assert model["human_input"]["authority"] == "user"
    assert model["human_input"]["answer_summary"] == PREFERENCE


def test_the_lesson_page_says_when_a_lesson_has_no_human_input(tmp_path):
    """An episode-sourced lesson renders the n/a line, not an empty
    field the operator reads as a missing record."""
    from minder_web import services
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les-ep", status="verified",
                       instruction="use pnpm")
    model = services.lesson_detail(dbp, "les-ep")
    assert model["lesson"]["source_human_input"] is None
    assert model["human_input"] is None


def test_the_lesson_page_renders_the_event_and_survives_an_old_store(
        tmp_path, monkeypatch):
    """The rendered page carries the kind and the answer, and a store
    whose lessons table lacks the column degrades to the n/a line
    instead of a 500."""
    import re

    from minder_web import services
    import webseed
    dbp = new_db(tmp_path)
    event = _seed_preference_event(dbp)
    lesson, _ = memory_lessons.promote_stated_preference(
        event["event_id"], PREFERENCE, repo=REPO, db_path=dbp)
    client = webseed.client_for(dbp, monkeypatch)
    page = client.get(f"/lessons/{lesson['lesson_id']}")
    assert page.status_code == 200
    assert "working_preference" in page.text
    assert PREFERENCE in page.text
    # The row is rendered inside the lesson's own table, not just
    # present somewhere on the page.
    row = re.search(r"<tr>(?:(?!</tr>).)*working_preference"
                    r"(?:(?!</tr>).)*</tr>", page.text, re.S)
    assert row
    # A store whose lessons table predates the source column: the read
    # side classifies the gap, it does not guess.
    _ins(dbp, "CREATE TABLE lessons_old (lesson_id TEXT PRIMARY KEY,"
             " repo TEXT, failure_key TEXT, instruction TEXT NOT NULL,"
             " anti_pattern TEXT, verification_json TEXT,"
             " status TEXT NOT NULL, source_episode TEXT, valid_from TEXT,"
             " valid_to TEXT, expires_when TEXT)")
    _ins(dbp, "INSERT INTO lessons_old SELECT lesson_id, repo, failure_key,"
             " instruction, anti_pattern, verification_json, status,"
             " source_episode, valid_from, valid_to, expires_when"
             " FROM lessons")
    _ins(dbp, "DROP TABLE lessons")
    _ins(dbp, "ALTER TABLE lessons_old RENAME TO lessons")
    model = services.lesson_detail(dbp, lesson["lesson_id"])
    assert model is not None
    assert model["lesson"]["source_human_input"] is None
    assert model["human_input"] is None
