"""Progressive skill disclosure (P2.1): metadata advertised freely, bodies
read only on a specific match, missing bodies degrade to instructions=None.

These tests run against a temp index so they pin the *mechanism*, not the
current repo skill catalog.
"""
import json

from minder_memory import skill_load


def _index(tmp_path, entries, body_text=None):
    import copy
    index = tmp_path / "index.json"
    entries = copy.deepcopy(entries)
    if body_text is not None:
        body = tmp_path / "body.md"
        body.write_text(body_text)
        for entry in entries:
            if entry.get("body") == "body.md":
                entry["body"] = str(body)
    index.write_text(json.dumps(entries))
    return index


ENTRIES = [
    {"name": "diagnose-env", "description": "Fix environment breakage.",
     "triggers": ["FileNotFoundError"], "risk_level": "low",
     "body": "body.md"},
    {"name": "inspect-schema", "description": "Check the contract.",
     "triggers": ["KeyError"], "risk_level": "medium"},
]


def test_metadata_never_carries_body_text(tmp_path):
    index = _index(tmp_path, ENTRIES, body_text="SECRET BODY STEPS")
    meta = skill_load.list_skill_metadata(index_path=index)
    assert [m["name"] for m in meta] == ["diagnose-env", "inspect-schema"]
    assert all("body" not in m and "instructions" not in m for m in meta)
    assert "SECRET BODY STEPS" not in json.dumps(meta)


def test_match_is_case_insensitive_over_excerpt_and_key(tmp_path):
    index = _index(tmp_path, ENTRIES)
    hit = skill_load.match_skills(
        {"error_excerpt": "path: filenotfounderror: no such file"},
        index_path=index)
    assert hit == ["diagnose-env"]
    hit = skill_load.match_skills(
        {"failure_key": "edit:|keyerror|none|none", "error_excerpt": ""},
        index_path=index)
    assert hit == ["inspect-schema"]
    assert skill_load.match_skills({"error_excerpt": "all good"},
                                   index_path=index) == []
    assert skill_load.match_skills("not a dict", index_path=index) == []


def test_load_skill_reads_body_only_for_the_named_skill(tmp_path):
    index = _index(tmp_path, ENTRIES, body_text="STEP ONE: probe")
    skill = skill_load.load_skill("diagnose-env", index_path=index)
    assert skill["instructions"] == "STEP ONE: probe"
    assert skill["risk_level"] == "low"
    # bodyless skill: known name, instructions=None
    bodyless = skill_load.load_skill("inspect-schema", index_path=index)
    assert bodyless is not None and bodyless["instructions"] is None
    assert skill_load.load_skill("nope", index_path=index) is None


def test_select_loads_only_matched_bodies_and_reports_the_gap(tmp_path):
    index = _index(tmp_path, ENTRIES, body_text="STEP ONE: probe")
    out = skill_load.select_skills_for_event(
        {"failure_key": "x|filenotfounderror|none|none",
         "error_excerpt": "FileNotFoundError: missing.bin"},
        index_path=index)
    assert out["selected"] == ["diagnose-env"]
    assert [s["name"] for s in out["loaded"]] == ["diagnose-env"]
    assert out["gap"] is None

    miss = skill_load.select_skills_for_event(
        {"failure_key": "x|oserror|none|none", "error_excerpt": ""},
        index_path=index)
    assert miss["selected"] == [] and miss["loaded"] == []
    assert miss["gap"] == {"gap": True, "gap_type": "environment"}
