"""Temporary plan tests (spec P2.2)."""
from memory import plans, skill_load, skills

REPO = "/repo"


def unmatched_event(**kw):
    base = {"event_type": "tool_failure", "tool": "bash", "repo": REPO,
            "failure_key": "bash|zerodivisionerror|calc|m.py",
            "error_excerpt": "ZeroDivisionError: division by zero"}
    base.update(kw)
    return base


def test_no_matching_skill_creates_plan_with_skills_gap_type(tmp_path):
    dbp = tmp_path / "m.sqlite"
    sel = skill_load.select_skills_for_event(unmatched_event(),
                                             index_path=tmp_path /
                                             "absent.json")
    assert sel["selected"] == [] and sel["gap"]
    plan_id = plans.create_temp_plan(unmatched_event(),
                                     gap_type=sel["gap"]["gap_type"],
                                     db_path=dbp)
    assert plan_id
    # environment-like event maps through the same path
    env_ev = unmatched_event(failure_key="bash|filenotfounderror|none|x",
                             error_excerpt="FileNotFoundError: gone")
    sel_env = skill_load.select_skills_for_event(
        env_ev, index_path=tmp_path / "absent.json")
    assert sel_env["gap"]["gap_type"] == "environment"
    plan_id2 = plans.create_temp_plan(env_ev,
                                      gap_type=sel_env["gap"]["gap_type"],
                                      db_path=dbp)
    assert plan_id2 and plan_id2 != plan_id


def test_directive_says_temporary_plan_not_lesson(tmp_path):
    dbp = tmp_path / "m.sqlite"
    plan_id = plans.create_temp_plan(unmatched_event(), "procedural",
                                     db_path=dbp)
    text = plans.plan_directive(plan_id, db_path=dbp)
    assert text.startswith("TEMPORARY PLAN")
    assert "verified skill" in text and "not a" in text
    assert "VERIFIED LESSON" not in text
    # deterministic default steps, no frontier dump
    assert "Inspect the relevant file" in text
    assert len(text.splitlines()) <= 6


def test_verified_episode_does_not_auto_convert_plan(tmp_path):
    from memory import store
    dbp = tmp_path / "m.sqlite"
    ep_id, _ = store.open_episode({"repo": REPO, "task_id": "t"},
                                  db_path=dbp)
    plan_id = plans.create_temp_plan(unmatched_event(episode_id=ep_id),
                                     "procedural", db_path=dbp)
    store.close_episode(ep_id, "verified", db_path=dbp)
    # plan still open, no candidate/skill machinery fired
    plan = plans.open_plan_for(unmatched_event(), db_path=dbp)
    assert plan and plan["plan_id"] == plan_id
    assert plan["status"] == "open"
    from memory import lessons
    lesson, status = lessons.promote_lesson(
        ep_id, "instruction", verification={"tests_passed": True},
        db_path=dbp)
    assert status == "ok"  # the lesson promotes — the plan does not follow
    assert plans.plan_directive(plan_id, db_path=dbp).startswith(
        "TEMPORARY PLAN")


def test_io_failure_returns_none_no_exception(tmp_path):
    import os
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pytest
        pytest.skip("running as root: read-only dir not read-only")
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        nowhere = ro / "mem.sqlite"
        assert plans.create_temp_plan(unmatched_event(), "procedural",
                                      db_path=nowhere) is None
        assert plans.mark_plan("plan_x", "verified", db_path=nowhere) is False
        assert plans.plan_directive("plan_x", db_path=nowhere) == ""
        assert plans.open_plan_for(unmatched_event(), db_path=nowhere) is None
    finally:
        ro.chmod(0o700)


def test_skills_md_never_created_or_modified(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dbp = tmp_path / "m.sqlite"
    skills_md = tmp_path / "SKILLS.md"
    skills_md.write_text("hand authored\n")
    before = skills_md.read_bytes()
    plan_id = plans.create_temp_plan(unmatched_event(), "environment",
                                     db_path=dbp)
    plans.mark_plan(plan_id, "executed", db_path=dbp)
    plans.plan_directive(plan_id, db_path=dbp)
    assert skills_md.read_bytes() == before
    assert not (tmp_path / "skills").exists()


def test_mark_plan_lifecycle(tmp_path):
    dbp = tmp_path / "m.sqlite"
    plan_id = plans.create_temp_plan(unmatched_event(), "unknown",
                                     db_path=dbp)
    assert plans.mark_plan(plan_id, "executed", db_path=dbp)
    assert plans.mark_plan(plan_id, "verified", db_path=dbp)
    assert plans.mark_plan(plan_id, "bogus-status", db_path=dbp) is False
    conn = __import__("memory.db", fromlist=["connect"]).connect(dbp)
    row = conn.execute("SELECT status, closed_at FROM temp_plans WHERE"
                       " plan_id = ?", (plan_id,)).fetchone()
    conn.close()
    assert row["status"] == "verified" and row["closed_at"]
