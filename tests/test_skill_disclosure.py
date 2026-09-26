"""Progressive skill disclosure tests (spec P2.1)."""
import json

import minder_memory.skill_load as sl

INDEX = {
    "name": "inspect-schema-boundary",
    "description": "Inspect source fixture/contract before mapping edits.",
    "triggers": ["KeyError", "ValidationError", "schema"],
    "risk_level": "medium",
    "body": "skills/bodies/inspect-schema-boundary.md",
    "preconditions": ["repo_version_known"],
    "verification": ["direct_tests", "downstream_tests"],
}


def keyed_event(**kw):
    base = {"event_type": "tool_failure", "tool": "bash",
            "failure_key": "bash|keyerror|supplier_id|app/supplier.py",
            "error_excerpt": "KeyError: 'supplier_id'"}
    base.update(kw)
    return base


def test_list_skill_metadata_never_includes_body_text():
    metas = sl.list_skill_metadata()
    assert metas, "index must have skills"
    for m in metas:
        assert set(m) <= {"name", "description", "triggers", "risk_level"}
        assert "instructions" not in m
        assert "##" not in json.dumps(m)  # no markdown body leaked


def test_unmatched_event_empty_selection_with_gap(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps([INDEX]))
    ev = keyed_event(failure_key="bash|zerodivisionerror|calc|m.py",
                     error_excerpt="ZeroDivisionError: division by zero")
    out = sl.select_skills_for_event(ev, index_path=idx)
    assert out["selected"] == [] and out["loaded"] == []
    assert out["advertised"]  # metadata still advertised
    assert out["gap"] == {"gap": True, "gap_type": "unknown"}
    # environment-like families classify the gap
    ev_env = keyed_event(failure_key="bash|filenotfounderror|none|x",
                         error_excerpt="FileNotFoundError: no su")
    out = sl.select_skills_for_event(ev_env, index_path=idx)
    assert out["gap"]["gap_type"] == "environment"


def test_matched_event_loads_only_matching_bodies(tmp_path, monkeypatch):
    # sentinel "body" is a directory: opening it would raise, so a read
    # attempt cannot pass silently
    sentinel = tmp_path / "sentinel-body-dir"
    sentinel.mkdir()
    real_body = tmp_path / "real.md"
    real_body.write_text("# real skill body\nstep one")
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps([
        dict(INDEX, body=str(real_body)),
        {"name": "never-selected", "description": "trap",
         "triggers": ["NoSuchTriggerEver"], "risk_level": "low",
         "body": str(sentinel)},
    ]))
    reads = []
    original = sl._read_body

    def spy(rel):
        reads.append(rel)
        return original(rel)

    monkeypatch.setattr(sl, "_read_body", spy)
    ev = keyed_event()  # matches inspect-schema-boundary only
    out = sl.select_skills_for_event(ev, index_path=idx)
    assert out["selected"] == ["inspect-schema-boundary"]
    assert len(out["loaded"]) == 1
    assert "real skill body" in out["loaded"][0]["instructions"]
    assert reads == [str(real_body)]  # sentinel body never requested


def test_missing_body_degrades_not_raises(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps([
        dict(INDEX, body=str(tmp_path / "nope" / "missing.md")),
        {"name": "bodyless", "description": "metadata only",
         "triggers": ["KeyError"], "risk_level": "low"},
    ]))
    skill = sl.load_skill("inspect-schema-boundary", index_path=idx)
    assert skill["instructions"] is None
    assert skill["name"] == "inspect-schema-boundary"
    bodyless = sl.load_skill("bodyless", index_path=idx)
    assert bodyless["instructions"] is None
    # through the selector too
    out = sl.select_skills_for_event(keyed_event(), index_path=idx)
    assert out["loaded"][0]["instructions"] is None


def test_trigger_match_case_insensitive_uses_family():
    # family segment carries the match even when the excerpt doesn't
    ev = keyed_event(error_excerpt="process failed: exit code 2",
                     failure_key="bash|validationerror|none|none")
    assert sl.match_skills(ev) == ["inspect-schema-boundary"]
    # excerpt match also works case-insensitively
    ev2 = keyed_event(failure_key="", error_excerpt="SCHEMA mismatch: got int")
    assert "inspect-schema-boundary" in sl.match_skills(ev2)
    # unknown family + silent excerpt → no match
    ev3 = keyed_event(failure_key="", error_excerpt="")
    assert sl.match_skills(ev3) == []


def test_existing_skill_gap_tests_still_pass():
    import tests.test_memory_skill_gaps  # noqa: F401
