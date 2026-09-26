"""Candidate skill proposal tests (spec P2.3)."""
import json


from minder_memory import lessons, skill_promote, store

REPO = "/repo"
FAMILY = "keyerror"
KEY = f"bash|{FAMILY}|supplier_id|app/supplier.py"


def verified_lesson(dbp, instruction, repo=REPO, key=KEY, anti=None):
    ep_id, _ = store.open_episode({"repo": repo, "task_id": "t"},
                                  db_path=dbp)
    store.add_attempt(ep_id, {"event_type": "tool_failure", "tool": "bash",
                              "repo": repo, "failure_key": key},
                      db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lesson, status = lessons.promote_lesson(
        ep_id, instruction, anti_pattern=anti,
        verification={"tests_passed": True}, repo=repo, failure_key=key,
        db_path=dbp)
    assert status == "ok"
    return lesson


def test_one_verifiedlesson_no_candidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    verified_lesson(dbp, "seed the map")
    assert skill_promote.propose_skill_from_lessons(REPO, FAMILY,
                                                    db_path=dbp) is None


def test_three_verified_lessons_propose_one_candidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    for i in range(3):
        verified_lesson(dbp, f"seed the map (v{i})",
                        anti="query an empty cache")
    cand = skill_promote.propose_skill_from_lessons(REPO, FAMILY,
                                                    db_path=dbp)
    assert cand and cand["status"] == "proposed"
    assert cand["episode_count"] == 3
    assert len(json.loads(cand["source_lesson_ids"])) == 3
    assert "seed the map (v0)" in cand["instructions"]
    assert "query an empty cache" in cand["anti_pattern"]
    # proposing twice makes a second candidate — dedupe is the operator's
    # accept step, but name-collision on apply is prevented (see below)


def test_mixed_repos_no_candidate(tmp_path):
    dbp = tmp_path / "m.sqlite"
    verified_lesson(dbp, "a", repo="/repo1")
    verified_lesson(dbp, "b", repo="/repo2")
    verified_lesson(dbp, "c", repo="/repo3")
    assert skill_promote.propose_skill_from_lessons("/repo1", FAMILY,
                                                    db_path=dbp) is None


def test_invalidated_lessons_do_not_count(tmp_path):
    dbp = tmp_path / "m.sqlite"
    keep = verified_lesson(dbp, "keep me")
    stale = []
    for i in range(3):
        stale.append(verified_lesson(dbp, f"stale {i}"))
    for lesson in stale:
        lessons.invalidate_lesson(lesson["lesson_id"], "superseded",
                                  db_path=dbp)
    cand = skill_promote.propose_skill_from_lessons(REPO, FAMILY,
                                                    db_path=dbp)
    assert cand is None  # only 1 valid lesson remains
    assert keep["status"] == "verified"


def test_apply_false_leaves_index_byte_identical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    index = tmp_path / "skills" / "index.json"
    index.parent.mkdir()
    index.write_text('[{"name": "existing", "triggers": ["x"]}]\n')
    dbp = tmp_path / "m.sqlite"
    for i in range(3):
        verified_lesson(dbp, f"lesson {i}")
    cand = skill_promote.propose_skill_from_lessons(REPO, FAMILY,
                                                    db_path=dbp)
    before = index.read_bytes()
    out = skill_promote.accept_skill_candidate(cand["candidate_id"],
                                               actor="operator",
                                               apply=False, db_path=dbp)
    assert out["status"] == "accepted" and out["applied"] is False
    assert index.read_bytes() == before
    assert not (tmp_path / "skills" / "bodies").exists()


def test_apply_true_writes_index_and_body_never_skills_md(tmp_path,
                                                          monkeypatch):
    monkeypatch.chdir(tmp_path)
    index = tmp_path / "skills" / "index.json"
    index.parent.mkdir()
    index.write_text('[]\n')
    bodies = tmp_path / "skills" / "bodies"
    dbp = tmp_path / "m.sqlite"
    for i in range(3):
        verified_lesson(dbp, f"lesson {i}", anti="guessing names")
    cand = skill_promote.propose_skill_from_lessons(
        REPO, FAMILY, name="supplier-map-seed", db_path=dbp)
    out = skill_promote.accept_skill_candidate(cand["candidate_id"],
                                               actor="operator", apply=True,
                                               db_path=dbp, index_path=index,
                                               bodies_dir=bodies)
    assert out["applied"] is True
    index_data = json.loads(index.read_text())
    assert any(e["name"] == "supplier-map-seed" for e in index_data)
    body = bodies / "supplier-map-seed.md"
    assert body.exists() and "lesson 0" in body.read_text()
    assert not (tmp_path / "SKILLS.md").exists()
    # non-operator cannot apply
    cand2 = skill_promote.propose_skill_from_lessons(
        REPO, FAMILY, name="supplier-map-seed-2", db_path=dbp)
    out2 = skill_promote.accept_skill_candidate(cand2["candidate_id"],
                                                actor="agent", apply=True,
                                                db_path=dbp, index_path=index,
                                                bodies_dir=bodies)
    assert out2["applied"] is False


def test_frontier_text_never_in_instructions(tmp_path):
    dbp = tmp_path / "m.sqlite"
    frontier_text = "PANEL CONSULT: you should refactor the supplier cache"
    ep_id, _ = store.open_episode({"repo": REPO}, db_path=dbp)
    store.add_attempt(ep_id, {
        "event_type": "tool_failure", "tool": "bash", "repo": REPO,
        "failure_key": KEY,
        "payload_json": json.dumps({"frontier_answer": frontier_text})},
        db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    lessons.promote_lesson(ep_id, "seed the map first",
                           verification={"tests_passed": True,
                                         "frontier_note": frontier_text},
                           repo=REPO, failure_key=KEY, db_path=dbp)
    for i in range(2):
        verified_lesson(dbp, f"companion {i}")
    cand = skill_promote.propose_skill_from_lessons(REPO, FAMILY,
                                                    db_path=dbp)
    assert cand is not None
    assert frontier_text not in cand["instructions"]
    assert "PANEL CONSULT" not in cand["instructions"]
