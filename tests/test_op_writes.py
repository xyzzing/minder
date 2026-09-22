"""Operator write tests (8B). Writes go through the existing memory APIs
only, require --yes (dry plan + exit 1 otherwise), and never touch
SKILLS.md or skills/index.json."""
from memory import (db as _db, lessons as memory_lessons, retrieval,
                    skills, store)
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def verified_lesson(dbp, instruction="check the dict default"):
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t1"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = memory_lessons.promote_lesson(
        ep_id, instruction, verification={"tests_passed": True},
        repo=REPO, failure_key=KEY, db_path=dbp)
    assert status == "ok"
    return ep_id, lesson["lesson_id"]


def seed_gap(dbp):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO skill_gaps (gap_id, ts, repo,"
                  " failure_key, gap_type, sample_error, status)"
                  " VALUES ('gap_w1', '2026-09-22T00:00:00', ?, ?,"
                  " 'procedural', 'sample', 'open')", (REPO, KEY))
    finally:
        conn.close()
    return "gap_w1"


def test_invalidate_without_yes_is_a_dry_plan(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _ep, lesson_id = verified_lesson(dbp)
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)  # live
    code = main(["--db", str(dbp), "lessons", "invalidate", lesson_id,
                 "--reason", "superseded by review"])
    assert code == EXIT_USAGE  # plan printed, no DB change
    out = capsys.readouterr().out
    assert "PLAN" in out and "--yes" in out
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)  # unchanged


def test_invalidate_with_yes_removes_from_retrieval(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _ep, lesson_id = verified_lesson(dbp)
    code = main(["--db", str(dbp), "lessons", "invalidate", lesson_id,
                 "--reason", "superseded by review", "--yes"])
    assert code == EXIT_OK
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []
    conn = _db.connect(dbp)
    try:
        row = dict(conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                                (lesson_id,)).fetchone())
    finally:
        conn.close()
    assert row["status"] == "invalidated"  # tombstone, not delete
    assert main(["--db", str(dbp), "lessons", "ls", "--status",
                 "invalidated"]) == EXIT_OK
    assert lesson_id in capsys.readouterr().out


def test_promote_without_tests_passed_fails(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t9"},
                                  db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    code = main(["--db", str(dbp), "lessons", "promote", ep_id,
                 "--instruction", "do the thing", "--yes"])
    assert code == EXIT_USAGE  # promote_lesson gate: rejected
    assert "tests" in capsys.readouterr().err
    conn = _db.connect(dbp)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_promote_with_tests_passed_lands_in_verified(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t10"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    code = main(["--db", str(dbp), "lessons", "promote", ep_id,
                 "--instruction", "guard supplier_id with .get",
                 "--failure-key", KEY, "--repo", REPO,
                 "--tests-passed", "--yes"])
    assert code == EXIT_OK
    lessons = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert lessons and "supplier_id" in lessons[0]["instruction"]
    capsys.readouterr()
    assert main(["--db", str(dbp), "lessons", "ls",
                 "--status", "verified"]) == EXIT_OK
    assert "guard supplier_id" in capsys.readouterr().out


def test_gaps_close_requires_yes_then_closes(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    gap_id = seed_gap(dbp)
    assert main(["--db", str(dbp), "gaps", "close", gap_id,
                 "--reason", "skill authored"]) == EXIT_USAGE
    row = _db.connect(dbp).execute(
        "SELECT status FROM skill_gaps WHERE gap_id = ?",
        (gap_id,)).fetchone()
    assert row["status"] == "open"  # dry run wrote nothing
    assert main(["--db", str(dbp), "gaps", "close", gap_id,
                 "--reason", "skill authored", "--yes"]) == EXIT_OK
    conn = _db.connect(dbp)
    try:
        row = dict(conn.execute(
            "SELECT * FROM skill_gaps WHERE gap_id = ?",
            (gap_id,)).fetchone())
    finally:
        conn.close()
    assert row["status"] == "closed"
    assert main(["--db", str(dbp), "gaps", "close", "gap_missing",
                 "--reason", "x", "--yes"]) == EXIT_USAGE


def test_skills_index_and_skills_md_untouched(tmp_path, monkeypatch):
    from pathlib import Path
    index = Path("skills/index.json")
    before = index.read_text()
    skills_md = Path("SKILLS.md")
    assert not skills_md.exists()  # invariant of the whole project
    dbp = tmp_path / "m.sqlite"
    _ep, lesson_id = verified_lesson(dbp)
    gap_id = seed_gap(dbp)
    assert main(["--db", str(dbp), "lessons", "invalidate", lesson_id,
                 "--reason", "r", "--yes"]) == EXIT_OK
    assert main(["--db", str(dbp), "gaps", "close", gap_id,
                 "--reason", "r", "--yes"]) == EXIT_OK
    assert index.read_text() == before
    assert not skills_md.exists()
    # and the skill index itself is still serviceable
    assert skills.load_index()
