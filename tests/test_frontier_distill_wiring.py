"""The governed consult path in production (issue #10).

`record_consult`, `classify_consult` and `distill_lesson_from_consult` were
complete and tested, and nothing in the runtime ever called them: the panel
wrote legacy un-governed traces, `helpfulness` stayed NULL, and the candidate
lesson queue stayed empty forever. These tests drive the two production
seams - `frontier.py`'s trace hook and the hook's episode close - and assert
on persisted rows.
"""
import json
import sqlite3

import frontier
from minder_memory import (frontier_traces, from_hook, retrieval)

# The cap lives in minder_core.panel_text, which does not exist before this
# change. Read it through frontier so the module-level import stays a name
# the pre-change code has (proven-red scores an ImportError as weak red).
DISTILL_MAX_LINES = getattr(
    getattr(frontier, "_panel", None), "DISTILL_MAX_LINES", 0)

SECRET = "sk-proj-supersecret1234567890"
REPO = "/repo"
CMD = "pytest tests/test_supplier.py"
FAIL_OUT = ("FAILED tests/test_supplier.py::test_lookup - "
            "KeyError: 'supplier_id'")
PASS_OUT = "7 passed in 0.31s"
KEY = "bash|keyerror|supplier_id|tests/test_supplier.py"

CFG = {"frontier_providers": [
    {"name": "probe-a", "base_url": "http://probe/a", "model": "m-a",
     "key_env": "PROBE_A_KEY"},
]}

PANEL_ANSWER = (
    "PANEL CONSULT: probe-a \u2713\n"
    "1. The dict lookup misses on a missing supplier_id.\n"
    "- Use dict.setdefault for supplier_id before the lookup\n"
    "- per deepseek-v4-pro: add a test for the empty case\n")
CAUSES_ONLY_ANSWER = (
    "PANEL CONSULT: probe-a \u2713\n"
    "1. The dict lookup misses on a missing supplier_id.\n"
    "2. The fixture never seeds supplier rows.\n")


def fail_event(session="fd-s1"):
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO, "cwd": REPO,
            "tool_input": {"command": CMD}, "tool_response": FAIL_OUT}


def pass_event(session="fd-s1"):
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO, "cwd": REPO,
            "tool_input": {"command": CMD}, "tool_response": PASS_OUT}


def struggle(dbp, session="fd-s1", rounds=2):
    """Record the failing calls, then return the episode they opened."""
    episode_id = None
    for _ in range(rounds):
        out = from_hook.record(fail_event(session), db_path=dbp)
        episode_id = out["episode_id"]
    return episode_id


def consult_about(dbp, episode_id, failure_key, answer=PANEL_ANSWER):
    """One governed consult recorded against this episode, the way
    frontier.py's trace hook does it."""
    payload = {"failure_key": failure_key, "local_attempts": 2,
               "episode_id": episode_id, "trigger": "warden-l2",
               "prompt": f"fix KeyError in {REPO}, key {SECRET}",
               "response": answer, "providers": [{"name": "probe-a"}],
               "distilled": ["use dict.setdefault for supplier_id",
                             "add a test for the empty case per deepseek-v4-pro"]}
    trace_id = frontier_traces.record_consult(payload, db_path=dbp)
    assert trace_id
    return trace_id


def _rows(dbp, sql, args=()):
    conn = sqlite3.connect(str(dbp))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _one(dbp, sql, args=()):
    rows = _rows(dbp, sql, args)
    return rows[0] if rows else None


def _close(dbp, session="fd-s1", event=None):
    return from_hook.record(event or pass_event(session), db_path=dbp)


# --- acceptance 1: frontier.py records a governed consult ----------------

def test_frontier_trace_hook_writes_governed_consult(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_A_KEY", "k1")
    dbp = tmp_path / "m.sqlite"
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    payload = {"key": "bash|keyerror|supplier_id|app/supplier.py",
               "attempts": 3, "error": FAIL_OUT, "task": "fd-t1",
               "episode_id": "ep_fd1"}
    answer = frontier.run_panel(
        payload, CFG,
        post=lambda *a, **k: (200, {"choices": [
            {"message": {"content": PANEL_ANSWER}}]}),
        on_trace=frontier.trace_hook({**CFG, "MINDER_MEMORY_DB": str(dbp)},
                                     db_path=dbp))
    assert "PANEL CONSULT" in answer

    trace = frontier_traces.get_consult(
        _only_trace_id(dbp), db_path=dbp)
    assert trace["episode_id"] == "ep_fd1"
    assert trace["consult_trigger"] == "warden-l2"
    # the distilled action list is stored, redacted, one entry per advice line
    actions = trace["distilled_json"]
    assert isinstance(actions, list) and actions
    assert any("setdefault" in a for a in actions)
    # raw prompt/response text never lands in the governed row
    assert SECRET not in json.dumps(trace)
    assert "PANEL CONSULT" not in json.dumps(actions)
    assert len(trace["request_hash"]) == 16 and len(trace["response_hash"]) == 16


def _only_trace_id(dbp):
    row = _one(dbp, "SELECT trace_id FROM frontier_traces")
    assert row, "no consult trace was stored"
    return row["trace_id"]


def test_frontier_module_uses_governed_record(tmp_path, monkeypatch):
    """The seam itself: frontier.py's own trace hook, not a test stand-in."""
    monkeypatch.setenv("PROBE_A_KEY", "k1")
    dbp = tmp_path / "m.sqlite"
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    trace = frontier.trace_hook(CFG)
    trace({"failure_key": "bash|keyerror|supplier_id|s.py",
           "local_attempts": 2, "episode_id": "ep_fd2",
           "redaction_profile": "internal-code-default",
           "providers": [{"name": "probe-a"}],
           "request_hash_source": f"prompt {SECRET}",
           "answer": PANEL_ANSWER,
           "distilled_actions": ["use dict.setdefault for supplier_id"],
           "trigger": "warden-l2"})
    row = _one(dbp, "SELECT * FROM frontier_evals")
    assert row, "frontier.py wrote no governed eval row"
    assert row["consult_trigger"] == "warden-l2"
    assert json.loads(row["distilled_json"]) == [
        "use dict.setdefault for supplier_id"]


def test_distilled_actions_extracted_from_panel_answer():
    """Pure: the actionable lines of an answer only, capped, prose dropped.
    The consult prompt asks for ranked root causes plus THE single next
    concrete action, so a line is an action only when it reads like one;
    a cause explains the failure and must not become lesson instruction."""
    assert frontier.distill_actions(PANEL_ANSWER) == [
        "Use dict.setdefault for supplier_id before the lookup",
        "add a test for the empty case"]
    assert frontier.distill_actions(CAUSES_ONLY_ANSWER) == []
    assert frontier.distill_actions("") == []
    assert frontier.distill_actions("(call failed: timeout)") == []
    many = "\n".join(f"- run check number {i}" for i in range(20))
    assert len(frontier.distill_actions(many)) == DISTILL_MAX_LINES
    # The panel's own per-consultant notes are answer text, not the merged
    # recommendation: they never become distilled actions.
    noted = ("SYNTHESIS (merged, disagreements flagged):\n"
             "- run pytest tests/test_supplier.py\n---\n"
             "[probe-a] - run it again with -x\n")
    assert frontier.distill_actions(noted) == [
        "run pytest tests/test_supplier.py"]


# --- acceptance 2/3: a verified close classifies and distills ------------

def test_verified_close_classifies_consult_helpful(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = struggle(dbp)
    key = _failure_key(dbp)
    consult_about(dbp, ep_id, key)
    assert _close(dbp)["closed"] == "verified"

    row = _one(dbp, "SELECT e.helpfulness AS h, e.verification_status AS v"
                    " FROM frontier_evals e JOIN frontier_traces t"
                    " ON t.trace_id = e.trace_id")
    assert row["h"] == "helpful"
    assert row["v"] == "pass"


def test_verified_close_distills_one_candidate_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = struggle(dbp)
    key = _failure_key(dbp)
    consult_about(dbp, ep_id, key)
    _close(dbp)

    lessons = _rows(dbp, "SELECT * FROM lessons")
    assert len(lessons) == 1
    lesson = lessons[0]
    assert lesson["status"] == "candidate"
    assert lesson["source_episode"] == ep_id
    assert lesson["failure_key"] == key
    # instruction built from distilled actions, attributions stripped
    assert "setdefault" in lesson["instruction"]
    assert "deepseek" not in lesson["instruction"].lower()
    assert lesson["instruction"].startswith("- ")


def test_candidate_lesson_is_inert(tmp_path):
    """P4.2 laws, pinned end to end through the production seam: retrieval
    never sees a candidate, and no graph node is projected for it."""
    dbp = tmp_path / "m.sqlite"
    ep_id = struggle(dbp)
    key = _failure_key(dbp)
    consult_about(dbp, ep_id, key)
    _close(dbp)

    lesson = _one(dbp, "SELECT * FROM lessons")
    assert retrieval.retrieve_lessons(REPO, key, db_path=dbp) == []
    nodes = _rows(dbp, "SELECT id, type FROM nodes WHERE type = 'Lesson'")
    assert not [n for n in nodes if n["id"] == lesson["lesson_id"]]


# --- acceptance 4: no verification evidence, no lesson -------------------

def test_candidate_close_leaves_consult_unclassified(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = struggle(dbp)
    key = _failure_key(dbp)
    consult_about(dbp, ep_id, key)
    closed = _close(dbp, event=dict(pass_event(),
                                    tool_input={"command": "ls -la"}))
    assert closed["closed"] == "candidate"

    row = _one(dbp, "SELECT e.helpfulness AS h FROM frontier_evals e")
    assert row["h"] is None
    assert _rows(dbp, "SELECT 1 FROM lessons") == []


# --- acceptance 5: an unlinked consult is not adopted --------------------

def test_consult_without_episode_stays_unclassified(tmp_path):
    dbp = tmp_path / "m.sqlite"
    struggle(dbp)
    frontier_traces.record_consult(
        {"failure_key": "bash|keyerror|supplier_id|app/supplier.py",
         "local_attempts": 2, "trigger": "warden-l2",
         "prompt": "fix", "response": PANEL_ANSWER,
         "distilled": ["use dict.setdefault"]}, db_path=dbp)
    _close(dbp)

    row = _one(dbp, "SELECT e.helpfulness AS h FROM frontier_evals e")
    assert row["h"] is None
    assert _rows(dbp, "SELECT 1 FROM lessons") == []


# --- acceptance 6: the wiring is idempotent ------------------------------

def test_second_verified_close_does_not_duplicate_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ep_id = struggle(dbp)
    key = _failure_key(dbp)
    consult_about(dbp, ep_id, key)
    _close(dbp)
    # a later success on a fresh episode must not distil the same consult twice
    struggle(dbp, session="fd-s2")
    _close(dbp, session="fd-s2")
    assert len(_rows(dbp, "SELECT 1 FROM lessons")) == 1


def _failure_key(dbp):
    """The key the recorded failures actually carry. Asserted, never
    assumed: an empty result here means capture changed shape."""
    row = _one(dbp, "SELECT failure_key AS k FROM events WHERE"
                    " event_type = 'tool_failure'")
    assert row and row["k"]
    return row["k"]
