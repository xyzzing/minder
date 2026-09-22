"""Frontier distill tests (docs/minder-phase-4-7-frontier-coding.md P4.2).
Only locally verified consults become candidate lessons; raw provider text
and model attributions never reach the instruction; candidates are invisible
to retrieve_lessons until an operator promotes them."""
from memory import (db as _db, frontier_distill, frontier_traces, lessons,
                    retrieval, store)

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def verified_episode(dbp, key=KEY, success=False):
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t1"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": REPO, "failure_key": key},
                      db_path=dbp)
    if success:
        store.add_attempt(ep_id, {"event_type": "tool_success", "tool": "bash",
                                  "repo": REPO, "failure_key": key},
                          db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    return ep_id


def helpful_trace(dbp, episode_id, response="PANEL: refactor everything"):
    tid = frontier_traces.record_consult(
        {"key": KEY, "attempts": 2, "episode_id": episode_id,
         "prompt": "fix KeyError", "response": response,
         "distilled": ["check the dict default for supplier_id"]},
        db_path=dbp)
    frontier_traces.classify_consult(
        tid, "pass", accepted=["check the dict default for supplier_id"],
        db_path=dbp)
    return tid


def harmful_trace(dbp, episode_id):
    tid = frontier_traces.record_consult(
        {"key": KEY, "attempts": 2, "episode_id": episode_id,
         "prompt": "fix KeyError", "response": "advice"},
        db_path=dbp)
    frontier_traces.classify_consult(
        tid, "fail", accepted=["rename the column"], db_path=dbp)
    return tid


def _lesson_count(dbp):
    conn = _db.connect(dbp)
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()["n"]
    finally:
        conn.close()


def test_helpful_verified_episode_makes_candidate_not_retrievable(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    tid = helpful_trace(dbp, ep_id)
    lid = frontier_distill.distill_lesson_from_consult(tid, ep_id,
                                                       db_path=dbp)
    assert lid
    conn = _db.connect(dbp)
    try:
        row = dict(conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                                (lid,)).fetchone())
    finally:
        conn.close()
    assert row["status"] == "candidate"
    assert row["source_episode"] == ep_id
    # candidates are inert: verified-only retrieval must not return them
    assert retrieval.retrieve_lessons(REPO, KEY, db_path=dbp) == []


def test_harmful_consult_makes_nothing(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    tid = harmful_trace(dbp, ep_id)
    assert frontier_distill.distill_lesson_from_consult(
        tid, ep_id, db_path=dbp) is None
    assert _lesson_count(dbp) == 0
    # inconclusive too
    tid2 = frontier_traces.record_consult(
        {"key": KEY, "attempts": 1, "episode_id": ep_id}, db_path=dbp)
    frontier_traces.classify_consult(tid2, "not_run", db_path=dbp)
    assert frontier_distill.distill_lesson_from_consult(
        tid2, ep_id, db_path=dbp) is None
    assert _lesson_count(dbp) == 0


def test_raw_response_text_never_reaches_instruction(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    tid = helpful_trace(dbp, ep_id,
                        response="PANEL: refactor everything, trust me "
                                 "(per deepseek-chat)")
    lid = frontier_distill.distill_lesson_from_consult(tid, ep_id,
                                                       db_path=dbp)
    assert lid
    conn = _db.connect(dbp)
    try:
        instruction = conn.execute(
            "SELECT instruction FROM lessons WHERE lesson_id = ?",
            (lid,)).fetchone()["instruction"]
    finally:
        conn.close()
    assert "refactor everything" not in instruction
    assert "deepseek" not in instruction
    assert "check the dict default for supplier_id" in instruction


def test_no_skill_candidates_or_skills_md_written(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    tid = helpful_trace(dbp, ep_id)
    assert frontier_distill.distill_lesson_from_consult(
        tid, ep_id, db_path=dbp)
    conn = _db.connect(dbp)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM skill_candidates").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0  # propose_skill_from_lessons thresholds were not run


def test_operator_with_tests_evidence_goes_through_promote_gates(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp, success=True)
    tid = helpful_trace(dbp, ep_id)
    lid = frontier_distill.distill_lesson_from_consult(
        tid, ep_id, actor="operator", db_path=dbp)
    assert lid
    conn = _db.connect(dbp)
    try:
        row = dict(conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                                (lid,)).fetchone())
    finally:
        conn.close()
    assert row["status"] == "verified"  # same promote_lesson gates
    lessons_hit = retrieval.retrieve_lessons(REPO, KEY, db_path=dbp)
    assert lessons_hit and "supplier_id" in lessons_hit[0]["instruction"]
    # without local tests evidence, an operator actor still only candidates
    dbp2 = tmp_path / "m2.sqlite"
    ep2 = verified_episode(dbp2)
    tid2 = helpful_trace(dbp2, ep2)
    lid2 = frontier_distill.distill_lesson_from_consult(
        tid2, ep2, actor="operator", db_path=dbp2)
    conn2 = _db.connect(dbp2)
    try:
        status = conn2.execute(
            "SELECT status FROM lessons WHERE lesson_id = ?",
            (lid2,)).fetchone()["status"]
    finally:
        conn2.close()
    assert status == "candidate"


def test_promote_lesson_still_requires_tests_passed(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, "do the thing", verification={"reviewed": True},
        db_path=dbp)
    assert lesson is None and status == "rejected:no-verified-tests"


def test_missing_trace_or_episode_returns_none(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = verified_episode(dbp)
    assert frontier_distill.distill_lesson_from_consult(
        "tr_missing", ep_id, db_path=dbp) is None
    tid = helpful_trace(dbp, ep_id)
    assert frontier_distill.distill_lesson_from_consult(
        tid, "ep_missing", db_path=dbp) is None
