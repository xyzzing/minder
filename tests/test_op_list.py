"""Operator list/show tests (8A): episodes, lessons (verified vs
candidate), gaps. Candidate lessons appear only under --status
candidate; free-text output is redacted."""
from minder_memory import (db as _db, lessons as memory_lessons, skills, store)
from minder_op.cli import EXIT_OK, main

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"
SECRET = "sk-proj-operatorleak99999999"


def seed_lesson(dbp, instruction="check the dict default"):
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t1"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY,
                              "error_excerpt": f"KeyError with {SECRET}"},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = memory_lessons.promote_lesson(
        ep_id, instruction, verification={"tests_passed": True},
        repo=REPO, failure_key=KEY, db_path=dbp)
    assert status == "ok"
    return ep_id, lesson["lesson_id"]


def seed_candidate(dbp):
    """A frontier-distilled candidate (status='candidate', no graph)."""
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t2"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO lessons (lesson_id, repo, failure_key,"
                  " instruction, anti_pattern, verification_json, status,"
                  " source_episode, valid_from, valid_to, expires_when)"
                  " VALUES ('les_cand1', ?, ?, 'candidate instruction',"
                  " '', '{}', 'candidate', ?, '2026-09-22', NULL, '')",
                  (REPO, KEY, ep_id))
    finally:
        conn.close()
    return "les_cand1"


def seed_gap(dbp):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO skill_gaps (gap_id, ts, repo,"
                  " failure_key, gap_type, sample_error, status)"
                  " VALUES ('gap_x1', '2026-09-22T00:00:00', ?, ?,"
                  " 'procedural', 'sample', 'open')", (REPO, KEY))
    finally:
        conn.close()
    return "gap_x1"


def test_verified_shown_candidate_hidden_by_default(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _ep, verified_id = seed_lesson(dbp)
    candidate_id = seed_candidate(dbp)

    assert main(["--db", str(dbp), "lessons", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert verified_id in out and candidate_id not in out  # default view

    assert main(["--db", str(dbp), "lessons", "ls",
                 "--status", "verified"]) == EXIT_OK
    out = capsys.readouterr().out
    assert verified_id in out and candidate_id not in out

    assert main(["--db", str(dbp), "lessons", "ls",
                 "--status", "candidate"]) == EXIT_OK
    out = capsys.readouterr().out
    assert candidate_id in out and verified_id not in out


def test_lessons_show_redacts_secret(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    seed_lesson(dbp, instruction=f"use {SECRET} carefully")
    assert main(["--db", str(dbp), "lessons", "show",
                 "les_" + "x"]) != EXIT_OK  # not found -> usage exit
    capsys.readouterr()
    # fetch the real id
    rows = _db.connect(dbp).execute(
        "SELECT lesson_id FROM lessons").fetchall()
    lid = rows[0][0]
    assert main(["--db", str(dbp), "lessons", "show", lid]) == EXIT_OK
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "instruction" in out


def test_episodes_ls_and_show(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t1"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": KEY,
                              "error_excerpt": f"boom {SECRET}"},
                      db_path=dbp)
    assert main(["--db", str(dbp), "episodes", "ls",
                 "--repo", REPO]) == EXIT_OK
    out = capsys.readouterr().out
    assert ep_id in out and SECRET not in out
    assert main(["--db", str(dbp), "episodes", "show", ep_id]) == EXIT_OK
    out = capsys.readouterr().out
    assert "tool_failure" in out and SECRET not in out
    assert main(["--db", str(dbp), "episodes", "show",
                 "ep_missing"]) != EXIT_OK


def test_gaps_ls_open_default(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    gap_id = seed_gap(dbp)
    assert main(["--db", str(dbp), "gaps", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert gap_id in out
    # closed gaps drop out of the default open view
    row, status = skills.close_gap(gap_id, "done", db_path=dbp)
    assert status == "ok" and row["status"] == "closed"
    assert main(["--db", str(dbp), "gaps", "ls"]) == EXIT_OK
    assert gap_id not in capsys.readouterr().out
    assert main(["--db", str(dbp), "gaps", "ls",
                 "--status", "closed"]) == EXIT_OK
    out = capsys.readouterr().out
    assert gap_id in out
