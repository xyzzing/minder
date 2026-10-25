"""Lessons at the proxy (issue #48): the declared memory contribution.

`minder_memory.proxy_assist.assist` retrieves verified lessons scoped
to the room band's label, renders them behind an authority-limit
header, journals every decision - injected or miss - in the #13
ledger under its own assist_mode, and fails open end to end. The
proxy wiring tests pin the opt-in gate and the system-message landing
with a real store, not a mock."""
import json


from minder_memory import db as _db
from minder_memory import injections, proxy_assist

REPO = "domain-evals"


def _seed(dbp, lesson_id, failure_key, instruction="prefer the ledger",
          anti="never grep the summary"):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO lessons (lesson_id, repo, failure_key,"
                  " instruction, anti_pattern, verification_json, status,"
                  " source_episode, valid_from, valid_to, expires_when)"
                  " VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?, NULL, ?)",
                  (lesson_id, REPO, failure_key, instruction, anti,
                   '{"tests_passed": true}', None,
                   "2026-10-01T00:00:00+00:00", None))
    finally:
        conn.close()


def _rows(dbp):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM learning_injections").fetchall()]
    finally:
        conn.close()


# ---- render_block -----------------------------------------------------------

def test_block_header_states_authority_limits():
    block = proxy_assist.render_block(
        [{"lesson_id": "les_1", "instruction": "do X",
          "anti_pattern": "not Y", "tier": "repo"}])
    assert "NOT instructions" in block
    assert "[les_1]" in block and "(repo)" in block
    assert "do X" in block and "not Y" in block


def test_render_empty_is_none_not_an_empty_block():
    assert proxy_assist.render_block([]) is None


# ---- assist -----------------------------------------------------------------

def test_disabled_returns_none_and_writes_nothing(tmp_path):
    dbp = tmp_path / "m.sqlite"
    assert proxy_assist.assist(label="complex", repo=REPO, cfg={},
                               db_path=dbp) is None
    # master switch on but no repo configured anywhere: not attempted
    assert proxy_assist.assist(label="complex",
                               cfg={"proxy_lessons": True},
                               db_path=dbp) is None
    assert _rows(dbp) == []


def test_miss_is_journaled_not_silent(tmp_path):
    dbp = tmp_path / "m.sqlite"
    summary = proxy_assist.assist(label="complex", repo=REPO,
                                  cfg={"proxy_lessons": True},
                                  db_path=dbp)
    assert summary["injected"] is False
    rows = _rows(dbp)
    assert len(rows) == 1
    assert rows[0]["tier"] == injections.TIER_NONE
    assert rows[0]["assist_mode"] == proxy_assist.MODE_PROXY_LESSONS


def test_repo_preference_lesson_is_injected_and_journaled(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp, "les_pref", "")  # repo-wide preference: empty failure_key
    summary = proxy_assist.assist(label="complex", repo=REPO,
                                  cfg={"proxy_lessons": True},
                                  db_path=dbp)
    assert summary["injected"] is True
    assert "NOT instructions" in summary["block"]
    assert summary["n_lessons"] == 1 and summary["chars"] > 0
    rows = [r for r in _rows(dbp) if r["lesson_id"] == "les_pref"]
    assert len(rows) == 1
    assert rows[0]["tier"] == injections.TIER_REPO
    assert rows[0]["assist_mode"] == proxy_assist.MODE_PROXY_LESSONS
    assert rows[0]["digest_injected"] == 1


def test_label_maps_to_a_failure_family(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp, "les_npv", "solver|npv|discount",
          instruction="write the discounting steps")
    cfg = {"proxy_lessons": True,
           "proxy_lesson_families": {"complex": "npv"}}
    summary = proxy_assist.assist(label="complex", repo=REPO, cfg=cfg,
                                  db_path=dbp)
    assert summary["injected"] is True
    assert "discounting steps" in summary["block"]
    rows = [r for r in _rows(dbp) if r["lesson_id"] == "les_npv"]
    assert rows[0]["tier"] == injections.TIER_FAMILY


def test_unmapped_label_misses_but_still_journals(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp, "les_npv", "solver|npv|discount")
    summary = proxy_assist.assist(label="routine", repo=REPO,
                                  cfg={"proxy_lessons": True},
                                  db_path=dbp)
    # preferences tier still runs, so this is a miss only because no
    # preference exists either
    assert summary["injected"] is False
    assert _rows(dbp)[0]["tier"] == injections.TIER_NONE


def test_fail_open_on_unusable_store(tmp_path):
    # retrieval cannot tell "no lessons" from "store broken"; both are
    # an honest miss for the caller, and neither may raise
    summary = proxy_assist.assist(
        label="complex", repo=REPO, cfg={"proxy_lessons": True},
        db_path=tmp_path / "no" / "such" / "dir" / "m.sqlite")
    assert summary["injected"] is False


# ---- proxy wiring -----------------------------------------------------------

def _events(ledger):
    try:
        return [json.loads(ln) for ln in
                open(ledger, encoding="utf-8").read().splitlines() if ln]
    except OSError:
        return []


def test_proxy_lands_block_in_system_message(tmp_path, monkeypatch):
    import minder
    import proxy as proxy_mod
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path)
    dbp = tmp_path / "m.sqlite"
    _seed(dbp, "les_pref", "")
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    req = {"messages": [{"role": "system", "content": "be careful."},
                        {"role": "user", "content": "q"}]}
    proxy_mod._apply_lessons(req, "complex", "sess-l",
                             {"proxy_lessons": True,
                              "proxy_lessons_repo": REPO})
    assert "NOT instructions" in req["messages"][0]["content"]
    assert req["messages"][0]["content"].startswith("be careful.")
    events = _events(tmp_path / "events.jsonl")
    injected = [e for e in events
                if e.get("event") == "lesson_proxy_injected"]
    assert len(injected) == 1 and injected[0]["n_lessons"] == 1


def test_proxy_off_by_default_untouched_request(tmp_path, monkeypatch):
    import minder
    import proxy as proxy_mod
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path)
    monkeypatch.setenv("MINDER_MEMORY_DB", str(tmp_path / "m.sqlite"))
    req = {"messages": [{"role": "user", "content": "q"}]}
    proxy_mod._apply_lessons(req, "complex", "sess-m", {})
    assert req["messages"] == [{"role": "user", "content": "q"}]
    assert _events(tmp_path / "events.jsonl") == []


def test_proxy_inserts_system_message_when_missing(tmp_path, monkeypatch):
    import minder
    import proxy as proxy_mod
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path)
    dbp = tmp_path / "m.sqlite"
    _seed(dbp, "les_pref", "")
    monkeypatch.setenv("MINDER_MEMORY_DB", str(dbp))
    req = {"messages": [{"role": "user", "content": "q"}]}
    proxy_mod._apply_lessons(req, "complex", "sess-n",
                             {"proxy_lessons": True,
                              "proxy_lessons_repo": REPO})
    assert req["messages"][0]["role"] == "system"
    assert req["messages"][1]["content"] == "q"
