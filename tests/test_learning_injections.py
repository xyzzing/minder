"""The injection ledger (issue #13).

minder injects a verified lesson into the Warden digest at two places in
minder_memory/policy.py. Before this change neither one recorded that it
happened, so the operator queue could not show that a lesson keeps
firing and no help-claim had a denominator.

The seam is policy.evaluate(): the same entry point hook.py calls, so the
assertion is on persisted rows plus the digest text that reached the
agent, not on an internal helper.
"""
import json

from minder_memory import (canonicalise as canon, db as _db, from_hook,
                    injections as ledger, lessons as memory_lessons,
                    policy as memory_policy, skill_load, store)

REPO = "/repo"


def hook_event(session="s-13"):
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO,
            "tool_input": {"command": "pnpm install"},
            "tool_response": "Error: pnpm: command not found (exit 127)"}


def seed_lesson(dbp, instruction="install pnpm before the workspace build",
                anti_pattern="do not re-run npm install to fix this"):
    """A verified lesson on the failure key the events below produce."""
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
        ep_id, instruction, anti_pattern=anti_pattern,
        verification={"tests_passed": True}, db_path=dbp)
    assert status == "ok", status
    return lesson, fkey


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


def warden():
    return {"action": "think", "level": 1, "digest": "[minder] warden"}


def repeated_failure_events(dbp, n=3, session="s-13", events=None):
    """The guard needs `memory_fail_threshold` recorded attempts before it
    acts; record them the way the hook does. `events` supplies a different
    failure key so a test can hold both a hit and a miss."""
    ev = (events or [hook_event(session)])[0]
    ev = dict(ev, session_id=session)
    for _ in range(n):
        from_hook.record(ev, db_path=dbp)
    return ev


def test_guard_injection_is_ledgered(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lesson, fkey = seed_lesson(dbp)
    ev = repeated_failure_events(dbp)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    # the lesson reached the agent...
    assert out["action"] == "block_duplicate"
    assert "VERIFIED LESSON" in out["digest"]
    # ...and the ledger knows it happened, with the tier that produced it
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE lesson_id = ? ORDER BY ts", (lesson["lesson_id"],))
    assert len(logged) == 1
    row = logged[0]
    assert row["digest_injected"] == 1
    assert row["tier"] == ledger.TIER_EXACT
    assert row["assist_mode"] == ledger.MODE_BLOCK_DUPLICATE
    assert row["failure_key"] == fkey
    assert row["session_id"] == "s-13"
    # chars_injected counts the digest text the agent actually got
    assert row["chars_injected"] == len(
        memory_policy._lesson_digest_line(lesson))


def test_retrieve_assist_injection_is_ledgered(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    lesson, _fkey = seed_lesson(dbp)
    ev = repeated_failure_events(dbp, session="s-13b")
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    monkeypatch.delenv("MINDER_DECISION", raising=False)

    # the guard must decline so the retrieve path is the one that injects:
    # a new hypothesis means the ladder speaks instead.
    ev_with_hypothesis = dict(ev, hypothesis="maybe a missing binary on PATH")
    out = memory_policy.evaluate(ev_with_hypothesis, warden(), db_path=dbp,
                                 repo=REPO)

    assert out is not None and out.get("assist") == "retrieve"
    assert "VERIFIED LESSON" in out["digest"]
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE assist_mode = ?", (ledger.MODE_RETRIEVE,))
    assert len(logged) == 1
    assert logged[0]["lesson_id"] == lesson["lesson_id"]
    assert logged[0]["digest_injected"] == 1
    assert logged[0]["tier"] == ledger.TIER_EXACT


def test_no_lesson_is_ledgered_as_a_miss(tmp_path, monkeypatch):
    """The denominator. Without the miss rows, "we inject lessons" is
    unmeasurable: every recorded row would be a success."""
    dbp = tmp_path / "m.sqlite"
    # no skill index: this fixture failure ("pnpm install") trigger-matches
    # the shipped diagnose-build-environment skill, and a skill section
    # would add a second row; this test is about the lesson miss alone
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX",
                        tmp_path / "absent-index.json")
    ev = repeated_failure_events(dbp, session="s-13c")

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    assert out["action"] == "block_duplicate"
    assert "VERIFIED LESSON" not in out["digest"]
    logged = rows(dbp, "SELECT * FROM learning_injections")
    assert len(logged) == 1
    assert logged[0]["lesson_id"] is None
    assert logged[0]["digest_injected"] == 0
    assert logged[0]["tier"] == ledger.TIER_NONE
    assert ledger.injection_miss_count(db_path=dbp) == 1


def test_family_tier_is_recorded_as_family(tmp_path):
    """A lesson retrieved by failure-family fallback must not be recorded
    as an exact match, or tier statistics lie about retrieval quality."""
    dbp = tmp_path / "m.sqlite"
    lesson, fkey = seed_lesson(dbp)
    # a different tool on the same failure family
    ev = dict(hook_event("s-13d"), tool_name="Sh")
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)
    ev_key = canon.failure_key(from_hook.to_event(ev), REPO)
    assert ev_key != fkey  # the fixture really is a family, not an exact hit

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in out["digest"]
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE lesson_id = ?", (lesson["lesson_id"],))
    assert logged and logged[0]["tier"] == ledger.TIER_FAMILY


def test_ledger_read_side_names_the_firing_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    lesson, _fkey = seed_lesson(dbp)
    for i in range(3):
        ev = repeated_failure_events(dbp, session=f"s-13e{i}")
        memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    counts = ledger.injection_counts(db_path=dbp)
    assert len(counts["lessons"]) == 1
    assert counts["lessons"][0]["lesson_id"] == lesson["lesson_id"]
    assert counts["lessons"][0]["injections"] == 3

    recent = ledger.injections_for_lesson(lesson["lesson_id"], db_path=dbp)
    assert len(recent) == 3
    assert all(r["tier"] == ledger.TIER_EXACT for r in recent)


def test_injection_counts_separates_misses_from_lessons(tmp_path):
    """Issue #20 acceptance 2: the `lesson_id IS NULL` rows are the
    denominator, not a lesson with an empty id. Read them as misses or a
    console renders a bucket no operator can act on.

    Two repeated failure shapes on one store: one the store has a lesson
    for, one it does not. The first is a hit row, the second a miss row -
    the case migration 015 put in the table on purpose."""
    dbp = tmp_path / "m.sqlite"
    lesson, _fkey = seed_lesson(dbp)
    # A different tool and a different error, so the failure key and the
    # action fingerprint both differ from the lesson's key.
    other = dict(hook_event("s-20x"), tool_name="Cargo",
                 tool_input={"command": "cargo build"},
                 tool_response="Error: could not compile (exit 101)")
    hit_ev = repeated_failure_events(dbp, session="s-20hit")
    miss_ev = repeated_failure_events(dbp, session="s-20miss",
                                      events=[other])

    hit = memory_policy.evaluate(hit_ev, warden(), db_path=dbp, repo=REPO)
    miss = memory_policy.evaluate(miss_ev, warden(), db_path=dbp, repo=REPO)
    assert "VERIFIED LESSON" in hit["digest"]
    assert "VERIFIED LESSON" not in miss["digest"]

    counts = ledger.injection_counts(db_path=dbp)
    assert [c["lesson_id"] for c in counts["lessons"]] == [lesson["lesson_id"]]
    assert counts["lessons"][0]["injections"] == 1
    # the nothing case is a miss, counted as one, never a lesson row
    assert counts["misses"] == 1


def test_injection_reads_degrade_when_the_table_is_absent(tmp_path):
    """A store predating migration 015: no rows, and no zero to mistake
    for one."""
    dbp = tmp_path / "m.sqlite"
    conn = _db.connect(dbp)
    conn.execute("DROP TABLE learning_injections")
    conn.commit()
    conn.close()
    counts = ledger.injection_counts(db_path=dbp)
    assert counts["lessons"] is None
    assert counts["misses"] is None
    assert ledger.injection_miss_count(db_path=dbp) is None


def test_ledger_failure_cannot_change_a_directive(tmp_path):
    """Constraint 10: the ledger is additive. A store that cannot take the
    injection row must not cost the agent the directive it would have
    got."""
    dbp = tmp_path / "m.sqlite"
    lesson, _fkey = seed_lesson(dbp)
    ev = repeated_failure_events(dbp, session="s-13f")

    real = ledger.record_injection

    def explode(**kwargs):
        raise RuntimeError("ledger unavailable")

    ledger.record_injection = explode
    try:
        out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    finally:
        ledger.record_injection = real

    assert out["action"] == "block_duplicate"
    assert "VERIFIED LESSON" in out["digest"]
    assert rows(dbp, "SELECT * FROM learning_injections") == []


# --- skill assists (issue #13 acceptance 3) -------------------------------
#
# A skill section reaching the digest is an injection like a lesson is, so
# the ledger must name the skill. The fixture is a synthetic skill index
# (the test_skill_load.py pattern): these tests pin the mechanism, not the
# shipped skill catalog.

_SKILL_ENTRIES = [
    {"name": "diagnose-env", "description": "Fix environment breakage.",
     "triggers": ["FileNotFoundError"], "risk_level": "low",
     "body": "body.md"},
]

_SKILL_BODY = ("STEP ONE: confirm the runtime and PATH.\n"
               "STEP TWO: check permissions on the state dir.")

# The compact section a skill injection contributes to the digest: the
# header plus the excerpt, as _skill_section and the gateway build it.
# The leading newline is the boundary the digest appends it with.
_SKILL_SECTION = f"\nSKILL: diagnose-env\n{_SKILL_BODY}"


def _skill_index(tmp_path):
    """A temp index whose one entry matches FileNotFoundError, with an
    absolute body path so _read_body resolves it."""
    body = tmp_path / "body.md"
    body.write_text(_SKILL_BODY)
    index = tmp_path / "index.json"
    index.write_text(json.dumps(
        [dict(e, body=str(body)) for e in _SKILL_ENTRIES]))
    return index


def _skill_failure(session="s-13g"):
    """A failure whose family the synthetic skill triggers on. The
    substring 'FileNotFoundError' in the excerpt is what match_skills
    looks for."""
    return {"session_id": session, "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO,
            "tool_input": {"command": "python3 app.py"},
            "tool_response": "FileNotFoundError: [Errno 2] config.toml"}


def test_skill_section_injection_records_the_skill_name(tmp_path,
                                                        monkeypatch):
    """Acceptance 3: a skill-section injection records the skill name as
    well. The guard attaches a SKILL section to this very digest; the
    ledger must carry a row that names it, with no lesson_id, so an
    operator can tell a skill assist from a lesson assist."""
    dbp = tmp_path / "m.sqlite"
    index = _skill_index(tmp_path)
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)
    ev = _skill_failure()
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    # the skill text reached the agent...
    assert out["action"] == "block_duplicate"
    assert "SKILL: diagnose-env" in out["digest"]
    # ...exactly once: the guard attaches the matched skill, and the
    # gateway attach must not stack a second copy of it on the digest
    assert out["digest"].count("SKILL:") == 1
    # ...and the ledger names the skill that got it there
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE assist_mode = ?", (ledger.MODE_SKILL,))
    assert len(logged) == 1
    row = logged[0]
    assert row["skill_name"] == "diagnose-env"
    assert row["lesson_id"] is None
    assert row["digest_injected"] == 1
    assert row["session_id"] == "s-13g"
    # chars_injected counts the whole section the agent got: the
    # "SKILL: name" header rides the injection, not just the body
    assert row["chars_injected"] == len(_SKILL_SECTION)
    # the lesson decision this guard pass also made is still its own row
    lesson_rows = [r for r in rows(dbp, "SELECT * FROM learning_injections")
                   if r["assist_mode"] == ledger.MODE_BLOCK_DUPLICATE]
    assert len(lesson_rows) == 1
    assert lesson_rows[0]["digest_injected"] == 0  # no lesson on this store


def test_a_skill_assist_is_not_a_miss(tmp_path, monkeypatch):
    """The miss count is the retrieval denominator (#20). A skill assist
    has no lesson_id but did inject text, so counting it as a miss would
    make the hit rate lie about retrieval."""
    dbp = tmp_path / "m.sqlite"
    index = _skill_index(tmp_path)
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)
    ev = _skill_failure("s-13h")
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "SKILL: diagnose-env" in out["digest"]

    # the skill row exists before its miss-ness is judged; its absence
    # would be a missing feature, not a passing denominator check
    skill_rows = rows(dbp, "SELECT * FROM learning_injections"
                           " WHERE assist_mode = ?", (ledger.MODE_SKILL,))
    assert len(skill_rows) == 1
    assert skill_rows[0]["digest_injected"] == 1

    assert ledger.injection_miss_count(db_path=dbp) == 1  # the lesson row
    counts = ledger.injection_counts(db_path=dbp)
    assert counts["misses"] == 1
    assert counts["lessons"] == []  # a skill row is neither


def test_skill_injection_read_side_names_the_skill(tmp_path, monkeypatch):
    """The ledger read side for skills, mirroring injections_for_lesson:
    newest first, and a name with no rows is an empty list, not a zero."""
    dbp = tmp_path / "m.sqlite"
    index = _skill_index(tmp_path)
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)
    for session in ("s-13i0", "s-13i1"):
        ev = _skill_failure(session)
        for _ in range(3):
            from_hook.record(ev, db_path=dbp)
        memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    recent = ledger.injections_for_skill("diagnose-env", db_path=dbp)
    assert len(recent) == 2
    assert {r["session_id"] for r in recent} == {"s-13i0", "s-13i1"}
    assert all(r["assist_mode"] == ledger.MODE_SKILL for r in recent)
    assert ledger.injections_for_skill("no-such-skill", db_path=dbp) == []


def test_decision_skill_assist_injection_is_ledgered(tmp_path, monkeypatch):
    """The other skill path: the Phase 6.5 gateway attach also puts a
    SKILL section in the digest, and that injection must be ledgered
    with the same assist_mode."""
    memory_policy._DECISION_DEBOUNCE.clear()
    dbp = tmp_path / "m.sqlite"
    index = _skill_index(tmp_path)
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)
    ev = _skill_failure("s-13j")
    fkey = canon.failure_key(from_hook.to_event(ev), REPO)
    for _ in range(2):
        from_hook.record(ev, db_path=dbp)

    from minder_decision.client import FakeClient
    from minder_decision import client as dclient
    from minder_decision import skill_select as dskill
    spec = {"failure_kind": "environment", "needs_new_evidence": 0.1,
            "next_step": "inspect", "confidence": 0.9,
            "any_skill_applies": 0.9, "best_skill": "diagnose-env"}
    monkeypatch.setattr(dclient, "get_decision_client",
                        lambda: FakeClient({fkey: spec}), raising=False)
    monkeypatch.setattr(dskill, "build_skill_shortlist",
                        lambda event, index_path=None: ("diagnose-env",))
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)

    assert out["assist"] == "decision_skill"
    assert "SKILL: diagnose-env" in out["digest"]
    # exactly one skill section on the digest: the gateway attach skips a
    # skill the guard already attached (test_decision_assist.py's
    # count-SKILL-1 invariant, now on a failure that matches both paths)
    assert out["digest"].count("SKILL:") == 1
    logged = rows(dbp, "SELECT * FROM learning_injections"
                       " WHERE assist_mode = ?", (ledger.MODE_SKILL,))
    assert len(logged) == 1
    assert logged[0]["skill_name"] == "diagnose-env"
    assert logged[0]["digest_injected"] == 1
    assert logged[0]["chars_injected"] == len(_SKILL_SECTION)


def test_ledger_failure_cannot_cost_the_agent_a_skill_section(tmp_path,
                                                              monkeypatch):
    """Constraint 10 on the skill path: a store that cannot take the
    skill row must not cost the agent the SKILL section it would have
    got."""
    dbp = tmp_path / "m.sqlite"
    index = _skill_index(tmp_path)
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)
    ev = _skill_failure("s-13k")
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)

    real = ledger.record_injection

    def explode(**kwargs):
        raise RuntimeError("ledger unavailable")

    ledger.record_injection = explode
    try:
        out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    finally:
        ledger.record_injection = real

    assert out["action"] == "block_duplicate"
    assert "SKILL: diagnose-env" in out["digest"]
    assert rows(dbp, "SELECT * FROM learning_injections") == []


def test_the_skill_row_is_written_by_the_injection_call_itself(tmp_path,
                                                               monkeypatch):
    """Issue #13 acceptance 3, carried by one self-contained test.

    The other #13 assertions lean on module fixtures, and a gate that
    runs this file against pre-change code needs at least one test that
    needs nothing from outside itself. This one drives a skill-section
    injection through policy.evaluate() and reads the row back: on code
    before the change the ledger has no skill_name column and
    injections_for_skill does not exist, so the injection cannot be
    named and the assertion fails."""
    body = tmp_path / "skill-body.md"
    body.write_text("STEP ONE: confirm the runtime and PATH.")
    index = tmp_path / "index.json"
    index.write_text(json.dumps([{
        "name": "diagnose-env", "description": "environment failures",
        "triggers": ["FileNotFoundError"], "risk_level": "low",
        "body": str(body)}]))
    monkeypatch.setattr(skill_load, "DEFAULT_INDEX", index)

    dbp = tmp_path / "m.sqlite"
    ev = {"session_id": "s-13-carrier", "hook_event_name": "PostToolUse",
          "tool_name": "Bash", "repo": REPO,
          "tool_input": {"command": "python3 app.py"},
          "tool_response": "FileNotFoundError: [Errno 2] config.toml"}
    for _ in range(3):
        from_hook.record(ev, db_path=dbp)

    out = memory_policy.evaluate(ev, warden(), db_path=dbp, repo=REPO)
    assert "SKILL: diagnose-env" in out["digest"]

    named = ledger.injections_for_skill("diagnose-env", db_path=dbp)
    assert len(named) == 1
    assert named[0]["skill_name"] == "diagnose-env"
    assert named[0]["digest_injected"] == 1
