"""The learning loop, end to end (issue #80 acceptance 1).

> End-to-end demo on a seeded live-shaped store: one failing event ->
> lesson distilled -> retrieval hit -> a learning_injections row with
> digest_injected=1 and tier exact/family/repo.

Every link is the governed path's own code, seeded the way the field
produces it: repeated failures + a clean close verify the episode
(from_hook), the consult records the episode's real failure key and
classifies from that close (frontier_traces), distillation makes a
candidate (frontier_distill), a person adopts it (lesson_decisions -
nothing auto-promotes), and a LATER session's repeat failure retrieves
it into the Warden digest (policy.evaluate, the same entry point
hook.py calls) with the ledger row proving delivery.

The diagnosis (mews .sweeps/keystone/t4-diagnosis.md) measured every
gate this test crosses; this test is the acceptance pinned so a
regression anywhere in the chain fails loudly here first. The chain is
inlined rather than reusing test_op_writes' distilled_candidate because
that fixture's consult carries a hand-written failure key - tier exact
here requires the consult to carry the episode's real key, which is
what the field produces.
"""
from minder_memory import (canonicalise as canon, db as _db, from_hook,
                           frontier_distill, frontier_traces,
                           policy as memory_policy)
from minder_memory import lessons as memory_lessons
from test_op_writes import CMD, FAIL_OUT, PASS_OUT

REPO = "/repo"


def warden():
    return {"action": "think", "level": 1, "digest": "[minder] warden"}


def rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_failure_to_delivered_lesson_end_to_end(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()  # migrate to the latest schema

    # The governed chain: repeated real failures + a clean close verify
    # the episode; the consult records the real failure key and is
    # classified from the close; distillation makes a candidate.
    # from_hook.record takes the RAW hook payload, never a converted
    # event - the close-verified detection reads the payload fields.
    raw_fail = {"session_id": "e2e-s1", "hook_event_name": "PostToolUse",
                "tool_name": "Bash", "repo": REPO, "cwd": REPO,
                "tool_input": {"command": CMD}, "tool_response": FAIL_OUT}
    fkey = canon.failure_key(from_hook.to_event(raw_fail), REPO)
    from_hook.record(dict(raw_fail), db_path=dbp)
    from_hook.record(dict(raw_fail), db_path=dbp)
    out = from_hook.record(dict(raw_fail, tool_response=PASS_OUT),
                           db_path=dbp)
    episode_id = out["episode_id"]
    tid = frontier_traces.record_consult(
        {"failure_key": fkey, "episode_id": episode_id,
         "local_attempts": 2, "trigger": "warden-l2",
         "prompt": "fix KeyError",
         "response": "- check the dict default for the supplier id",
         "providers": [{"name": "probe-a"}],
         "distilled": ["check the dict default for the supplier id"]},
        db_path=dbp)
    frontier_traces.classify_consult(
        tid, "pass",
        accepted=["check the dict default for the supplier id"],
        db_path=dbp)
    lid = frontier_distill.distill_lesson_from_consult(tid, episode_id,
                                                       db_path=dbp)
    assert lid, "distillation produced no candidate"

    # The operator decision: adopt the candidate (candidate lessons are
    # deliberately not retrievable - the promotion law is human-gated).
    lesson, status = memory_lessons.adopt_candidate_lesson(lid,
                                                           db_path=dbp)
    assert status == "ok", status

    # A new session repeats the same failure with a new hypothesis, so
    # the guard declines and the retrieve assist is the injector.
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    event = from_hook.to_event(
        {"session_id": "e2e-s2", "hook_event_name": "PostToolUse",
         "tool_name": "Bash", "repo": REPO, "cwd": REPO,
         "tool_input": {"command": CMD}, "tool_response": FAIL_OUT,
         "hypothesis": "maybe a PATH problem"})
    result = memory_policy.evaluate(event, warden(), db_path=dbp, repo=REPO)

    assert result is not None and result.get("assist") == "retrieve", (
        f"the retrieve assist did not fire: {result!r}")
    assert "check the dict default" in result["digest"], (
        "the delivered lesson text is not in the digest")

    # The ledger records the delivery, not a miss.
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE lesson_id = ?", (lesson["lesson_id"],))
    assert logged, "no delivered row in learning_injections"
    delivered = logged[-1]
    assert delivered["tier"] == "exact", (
        "the consult carried the episode's real failure key, so the"
        " retrieval tier is exact")
    assert delivered["digest_injected"] == 1
    assert delivered["assist_mode"] == "retrieve"
    assert delivered["session_id"] == "e2e-s2"
    assert delivered["failure_key"] == fkey
