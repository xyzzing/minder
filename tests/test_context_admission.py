"""The context-admission gate for lesson injection (issue #100).

The seam is the one test_learning_injections pins: policy.evaluate under
MINDER_ASSIST=retrieve. The assertion target is what the agent's digest
actually received plus the persisted rows. The gate never widens
behavior: off is byte-identical, shadow attaches regardless, and only a
validated above-floor decline in gate mode narrows the digest.
"""
import json

import minder
from minder_decision import context_admission
from minder_decision.contracts import (ADMISSION_OPTIONS,
                                       context_admission_contract)
from minder_decision.types import DecisionResponse
from minder_memory import (canonicalise as canon, db as _db, from_hook,
                           injections as ledger, lessons as memory_lessons,
                           policy as memory_policy, store)

REPO = "/repo"


def hook_event(session="s-100"):
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO,
            "tool_input": {"command": "pnpm install"},
            "tool_response": "Error: pnpm: command not found (exit 127)"}


def _ins(dbp, sql, params):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, sql, params)
    finally:
        conn.close()


def rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def seed_lesson(dbp):
    """A verified lesson on the failure key the events produce."""
    ev = from_hook.to_event(hook_event())
    fkey = canon.failure_key(ev, REPO)
    ep_id, _ = store.open_episode(dict(ev, failure_key=fkey), db_path=dbp)
    _ins(dbp, "INSERT INTO events (event_id, ts, event_type, session_id,"
          " task_id, repo, repo_version, tool, failure_key,"
          " action_fingerprint, payload_json, redaction_status)"
          " VALUES ('ev-verify', '2026-10-01T00:00:00+00:00',"
          " 'verification', 's-verify', 's-verify', ?, '', 'bash', ?,"
          " 'fp', '{\"tests_passed\": 1}', 'redacted')", (REPO, fkey))
    _ins(dbp, "INSERT INTO episode_events (episode_id, event_id, seq)"
          " VALUES (?, 'ev-verify', 1)", (ep_id,))
    _ins(dbp, "UPDATE episodes SET status = 'verified', closed_at ="
          " '2026-10-01T00:00:00+00:00' WHERE episode_id = ?", (ep_id,))
    lesson, status = memory_lessons.promote_lesson(
        ep_id, "install pnpm before the workspace build",
        anti_pattern="do not re-run npm install to fix this",
        verification={"tests_passed": True}, db_path=dbp)
    assert status == "ok", status
    return lesson, fkey


def retrieve_event(dbp, session="s-100"):
    """The repeated failure with a hypothesis, so the guard declines and
    the retrieve path is the one that would inject."""
    ev = dict(hook_event(session), session_id=session,
              hypothesis="maybe a missing binary on PATH")
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)
    return ev


def warden():
    return {"action": "think", "level": 1, "digest": "[minder] warden"}


def _admission_response(choice, confidence=0.9):
    contract = context_admission_contract()
    probs = {opt: (1.0 if opt == choice else 0.0)
             for opt in ADMISSION_OPTIONS}
    return DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"admit": probs},
        score_values={"match_quality": 3 if choice == "admit" else 0},
        confidence=confidence)


class StubClient:
    """Answers the admission contract with a canned response."""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def system_one(self, state, questions, model=None, contract=None):
        self.calls += 1
        return self._response


class _Garbage:
    """A client whose answer fails the contract's validation."""

    def system_one(self, state, questions, model=None, contract=None):
        contract = context_admission_contract()
        return DecisionResponse(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            choice_probs={"admit": {"admit": 0.6, "irrelevant": 0.4}},
            confidence=0.99)


def _events():
    path = minder.STATE_DIR / "events.jsonl"
    if not path.exists():
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _admission_events():
    return [e for e in _events() if e.get("event") == "context_admission"]


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

def test_contract_is_two_options_and_a_score():
    contract = context_admission_contract()
    assert contract.contract_id == "context-admission"
    assert contract.version == "v1"
    (choice, score) = contract.questions
    assert choice.options == ("admit", "decline")
    assert (score.min_value, score.max_value) == (0, 3)


# ---------------------------------------------------------------------------
# mode ladder: off is byte-identical and never dials
# ---------------------------------------------------------------------------

def test_off_mode_attaches_and_never_dials(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp)
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")

    def _no_client():
        raise AssertionError("off mode must not resolve a decision client")

    monkeypatch.setattr(context_admission, "_decision_client", _no_client)
    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    assert out is not None and out.get("assist") == "retrieve"
    assert "VERIFIED LESSON" in out["digest"]
    assert _admission_events() == []


def test_unknown_mode_is_off(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp)
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "sideways"})
    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]


# ---------------------------------------------------------------------------
# shadow: an opinion is recorded, nothing is withheld
# ---------------------------------------------------------------------------

def test_shadow_decline_attaches_anyway(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-shadow")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "shadow"})
    stub = StubClient(_admission_response("decline"))
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: stub)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    # the lesson still reached the agent...
    assert "VERIFIED LESSON" in out["digest"]
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE assist_mode = ?", (ledger.MODE_RETRIEVE,))
    assert len(logged) == 1 and logged[0]["digest_injected"] == 1
    # ...and the dissent is on the ledger with its mode: opinion is what
    # the model said, decision is what was applied
    events = _admission_events()
    assert len(events) == 1
    assert events[0]["opinion"] == "decline"
    assert events[0]["decision"] == "admit"
    assert events[0]["reason"] == "opinion"
    assert events[0]["mode"] == "shadow"
    assert stub.calls == 1


# ---------------------------------------------------------------------------
# gate: only a validated above-floor decline narrows the digest
# ---------------------------------------------------------------------------

def test_gate_decline_skips_the_line(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    lesson, _ = seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-gate")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: StubClient(_admission_response("decline")))

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    # the digest the agent received is exactly the warden's
    assert out["digest"] == "[minder] warden"
    assert out.get("assist") == "retrieve"
    # the miss is counted with its reason, not silently dropped
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE assist_mode = ?",
                  (ledger.MODE_ADMISSION_DECLINED,))
    assert len(logged) == 1
    assert logged[0]["lesson_id"] == lesson["lesson_id"]
    assert logged[0]["digest_injected"] == 0
    assert logged[0]["chars_injected"] == 0
    events = _admission_events()
    assert events[-1]["decision"] == "decline"
    assert events[-1]["mode"] == "gate"


def test_gate_admit_attaches(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-admit")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: StubClient(_admission_response("admit")))

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]


def test_gate_worker_down_fail_opens(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-down")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: StubClient(None))

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]
    events = _admission_events()
    assert events[-1]["decision"] == "admit"
    assert events[-1]["reason"] == "no_opinion"


def test_gate_below_floor_fail_opens(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-floor")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: StubClient(
                            _admission_response("decline", confidence=0.3)))

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]
    events = _admission_events()
    assert events[-1]["decision"] == "admit"
    assert events[-1]["reason"] == "below_confidence"


def test_gate_malformed_response_fail_opens(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    seed_lesson(dbp)
    ev = retrieve_event(dbp, session="s-100-garbage")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: _Garbage())

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]
    events = _admission_events()
    assert events[-1]["reason"] == "malformed_response"


def test_opinion_never_raises_on_bad_state(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "cfg",
                        lambda: {"context_admission": "gate"})
    monkeypatch.setattr(context_admission, "_decision_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    decision, reason = context_admission.opinion(
        {"session_id": "s"}, "cmd|x|y|z", 3, {"lesson_id": "l"}, {})
    assert decision == "admit"
