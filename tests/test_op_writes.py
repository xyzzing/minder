"""Operator write tests (8B). Writes go through the existing memory APIs
only, require --yes (dry plan + exit 1 otherwise), and never touch
SKILLS.md or skills/index.json."""
import sqlite3

from minder_memory import (db as _db, from_hook,
                    lessons as memory_lessons, retrieval, skills, store)
from minder_memory import (frontier_distill, frontier_policy,
                    frontier_traces)
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"
CMD = "pytest tests/test_supplier.py"
FAIL_OUT = ("FAILED tests/test_supplier.py::test_lookup - "
            "KeyError: 'supplier_id'")
PASS_OUT = "7 passed in 0.31s"


def _rows(dbp, sql, args=()):
    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


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


def seed_candidate(dbp):
    """A hand-written candidate over an episode that was never verified by a
    test run: the shape the adoption gate must refuse."""
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t11"},
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


def distilled_candidate(dbp, session="opw-s1"):
    """A candidate lesson exactly as issue #10's close produces it: the
    episode closed verified on a clean test run, its consult was classified
    from that close, and the candidate came from the consult's actions."""
    for _ in range(2):
        from_hook.record({"session_id": session,
                          "hook_event_name": "PostToolUse",
                          "tool_name": "Bash", "repo": REPO, "cwd": REPO,
                          "tool_input": {"command": CMD},
                          "tool_response": FAIL_OUT}, db_path=dbp)
    out = from_hook.record({"session_id": session,
                            "hook_event_name": "PostToolUse",
                            "tool_name": "Bash", "repo": REPO, "cwd": REPO,
                            "tool_input": {"command": CMD},
                            "tool_response": PASS_OUT}, db_path=dbp)
    episode_id = out["episode_id"]
    trace_id = frontier_traces.record_consult(
        {"failure_key": KEY, "episode_id": episode_id, "local_attempts": 2,
         "trigger": "warden-l2", "prompt": "fix KeyError",
         "response": "- guard supplier_id with .get",
         "providers": [{"name": "probe-a"}],
         "distilled": ["guard supplier_id with .get"]}, db_path=dbp)
    # Seed the candidate with the governed path's own parts: the close
    # labels the consult, the policy decides, the distiller writes. The
    # production wiring of that join is issue #10's first commit and is
    # pinned there; here the queue is the input.
    frontier_traces.classify_consult(trace_id, "pass",
                                     accepted=["guard supplier_id with .get"],
                                     db_path=dbp)
    assert frontier_policy.may_distill(
        frontier_traces.get_consult(trace_id, db_path=dbp),
        episode_status="verified")
    lesson_id = frontier_distill.distill_lesson_from_consult(
        trace_id, episode_id, db_path=dbp)
    assert lesson_id  # red here means the distiller stopped producing
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "UPDATE lessons SET anti_pattern = ?"
                  " WHERE lesson_id = ?",
                  ("avoid bare dict indexing", lesson_id))
    finally:
        conn.close()
    return lesson_id


def test_promote_candidate_lesson_adopts_its_own_text(tmp_path, capsys):
    """The candidate queue has to be closable. The distilled instruction is
    already the operator-facing text, so reviewing a candidate is one
    command, and the tests evidence is the episode's, not a re-assertion."""
    dbp = tmp_path / "m.sqlite"
    lesson_id = distilled_candidate(dbp)
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []

    code = main(["--db", str(dbp), "lessons", "promote", lesson_id,
                 "--from-candidate", "--yes"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "guard supplier_id" in out
    lessons = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert len(lessons) == 1
    assert lessons[0]["instruction"] == "- guard supplier_id with .get"


def test_promote_candidate_is_a_dry_plan_without_yes(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    lesson_id = distilled_candidate(dbp)
    code = main(["--db", str(dbp), "lessons", "promote", lesson_id,
                 "--from-candidate"])
    assert code == EXIT_USAGE
    assert "PLAN" in capsys.readouterr().out
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []


def test_promote_candidate_with_edited_instruction(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    lesson_id = distilled_candidate(dbp)
    code = main(["--db", str(dbp), "lessons", "promote", lesson_id,
                 "--from-candidate", "--instruction",
                 "guard supplier_id with .get, then seed the fixture",
                 "--yes"])
    assert code == EXIT_OK
    capsys.readouterr()
    assert main(["--db", str(dbp), "lessons", "ls", "--status",
                 "verified"]) == EXIT_OK
    assert "seed the fixture" in capsys.readouterr().out


def test_promote_candidate_without_verification_evidence_fails(tmp_path,
                                                               capsys):
    """The gate is the episode's evidence, not the candidate's existence: a
    candidate written by hand over an episode with no verification event
    still cannot be adopted."""
    dbp = tmp_path / "m.sqlite"
    seed_candidate(dbp)  # status='candidate', verification_json '{}'
    code = main(["--db", str(dbp), "lessons", "promote", "les_cand1",
                 "--from-candidate", "--yes"])
    assert code == EXIT_USAGE
    assert "tests" in capsys.readouterr().err
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []


def test_promote_candidate_unknown_id_is_a_usage_error(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    code = main(["--db", str(dbp), "lessons", "promote", "les_ghost",
                 "--from-candidate", "--yes"])
    assert code == EXIT_USAGE
    assert "no-such-lesson" in capsys.readouterr().err


def test_adopt_candidate_reuses_the_promotion_gates(tmp_path, monkeypatch):
    """Adoption is not a second gate. It delegates to promote_lesson with the
    candidate's own text and provenance, so the verified episode and
    tests_passed checks exist in exactly one place."""
    dbp = tmp_path / "m.sqlite"
    lesson_id = distilled_candidate(dbp)
    seen = {}

    def spy(episode_id, instruction, **kw):
        seen["episode_id"] = episode_id
        seen["instruction"] = instruction
        seen["verification"] = kw.get("verification")
        seen["failure_key"] = kw.get("failure_key")
        seen["repo"] = kw.get("repo")
        seen["actor"] = kw.get("actor")
        seen["anti_pattern"] = kw.get("anti_pattern")
        return None, "rejected:no-verified-tests"

    import minder_memory.lessons as lesson_module
    monkeypatch.setattr(lesson_module, "promote_lesson", spy)
    lesson, status = memory_lessons.adopt_candidate_lesson(lesson_id,
                                                           db_path=dbp)
    assert lesson is None and status == "rejected:no-verified-tests"
    assert seen["instruction"] == "- guard supplier_id with .get"
    assert seen["actor"] == "operator"
    assert seen["anti_pattern"] == "avoid bare dict indexing"
    assert seen["failure_key"] == KEY and seen["repo"] == REPO
    # the episode's evidence is never asserted by the candidate's side
    assert "tests_passed" not in seen["verification"]
    assert seen["verification"]["source"] == "frontier-distill"


def test_adopt_candidate_refuses_a_verified_lesson(tmp_path, capsys):
    """Only the candidate queue is adoptable. Re-running the command on an
    already verified lesson must not mint a second one."""
    dbp = tmp_path / "m.sqlite"
    lesson_id = distilled_candidate(dbp)
    assert main(["--db", str(dbp), "lessons", "promote", lesson_id,
                 "--from-candidate", "--yes"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--db", str(dbp), "lessons", "promote", lesson_id,
                 "--from-candidate", "--yes"]) == EXIT_USAGE
    assert "rejected:lesson-status-invalidated" in capsys.readouterr().err
    assert len(retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)) == 1
