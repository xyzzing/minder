"""Skill-gap record tests (docs/prd-memory-v1.md PR 6)."""
from memory import skills

REPO = "/repo"


def env_event(**kw):
    base = {"event_type": "tool_failure", "tool": "bash", "repo": REPO,
            "failure_key": "bash|permissionerror|none|app.py",
            "error_excerpt": "PermissionError: operation not permitted"}
    base.update(kw)
    return base


def test_matching_trigger_attaches_skill_no_gap(tmp_path):
    ev = env_event(failure_key="bash|modulenotfounderror|none|app.py",
                   error_excerpt="ModuleNotFoundError: No module named "
                                 "'pydantic'")
    out = skills.check_skill_gap(ev, db_path=tmp_path / "m.sqlite")
    assert out["gap"] is False
    assert out["skill"] == "diagnose-environment"
    assert out["recorded"] is False


def test_no_match_repeated_failure_records_procedural_gap(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ev = env_event(failure_key="bash|zerodivisionerror|calc|math.py",
                   error_excerpt="ZeroDivisionError: division by zero")
    skills.check_skill_gap(ev, db_path=dbp, attempts=1)   # first: unknown, no row
    out = skills.check_skill_gap(ev, db_path=dbp, attempts=2)
    assert out["gap"] is True and out["gap_type"] == "procedural"
    assert out["recorded"] is True
    gaps = skills.list_gaps(REPO, db_path=dbp)
    assert len(gaps) == 1 and gaps[0]["gap_type"] == "procedural"


def test_environment_family_maps_to_environment_not_frontier(tmp_path):
    dbp = tmp_path / "m.sqlite"
    out = skills.check_skill_gap(env_event(), db_path=dbp, attempts=1)
    assert out["gap"] is True and out["gap_type"] == "environment"
    assert out["recorded"] is True
    # the function only records: it returns no directive and never calls
    # frontier — its output cannot drive an escalation
    assert "digest" not in out and "action" not in out


def test_no_skills_md_ever_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dbp = tmp_path / "m.sqlite"
    skills.check_skill_gap(env_event(), db_path=dbp, index_path=dbp.parent /
                           "no-index.json")
    skills.check_skill_gap(env_event(failure_key="bash|keyerror|x|y",
                                     error_excerpt="KeyError: 'x'"),
                           db_path=dbp, index_path=dbp.parent / "no.json",
                           attempts=5)
    assert not (tmp_path / "SKILLS.md").exists()
    assert not (tmp_path / "skills").exists()


def test_missing_index_degrades_to_no_match(tmp_path):
    out = skills.check_skill_gap(env_event(), db_path=tmp_path / "m.sqlite",
                                 index_path=tmp_path / "absent.json",
                                 attempts=3)
    assert out["gap"] is True and out["skill"] is None
