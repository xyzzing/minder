"""Every installed skill is visible to the console (issue #40).

`skills/index.json` is the only thing the gap detector and `/skills`
read today, so an installed-pack skill - a folder under `skills/` plus a
`skills-lock.json` entry - is invisible: the page lists 5 rows and calls
it the whole index, and `check_skill_gap()` files gaps for failures the
installed set already covers. These tests hold the inventory and the
page together against a synthetic skills tree, so they never depend on
what happens to be installed on the developer's machine.
"""
import json

import webseed
from webseed import new_db

REPO = "/repo"

# an installed-pack skill: folder + lock entry, no index.json entry
_PACK = "review-pack"
_PACK_DESC = "ZeroDivisionError"
_PACK_SOURCE = "somebody/skills"
# a repo-owned skill: folder + index entry
_OWNED = "owned-skill"
_OWNED_TRIG = "ConnectionRefused"


def _tree(tmp_path):
    """A skills root with one pack skill, one repo-owned skill, and one
    index entry whose body file is absent. Returns (root, index_path)."""
    root = tmp_path / "skills"
    for name in (_PACK, _OWNED):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: " + name + "\ndescription: handles " + _PACK_DESC +
            " failures.\n---\n\nDo the thing.\n")
    (root / "bodies").mkdir()
    (root / "bodies" / (_OWNED + ".md")).write_text("owned body\n")
    index = [
        {"name": _OWNED, "description": "owned", "risk_level": "low",
         "triggers": [_OWNED_TRIG], "body": str(root / "bodies" /
                                                (_OWNED + ".md"))},
        {"name": "gone-skill", "description": "no body file",
         "risk_level": "low", "triggers": ["NoSuchThing"],
         "body": str(root / "bodies" / "gone-skill.md")},
    ]
    index_path = root / "index.json"
    index_path.write_text(json.dumps(index))
    (root.parent / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {_PACK: {"source": _PACK_SOURCE, "sourceType": "github",
                           "skillPath": "skills/" + _PACK + "/SKILL.md",
                           "computedHash": "0" * 64}}}))
    return root, index_path


def _failure(trig):
    return {"event_type": "tool_failure", "tool": "bash", "repo": REPO,
            "failure_key": "bash|zerodivisionerror|calc|math.py",
            "error_excerpt": trig + ": something"}


# --- the inventory itself -------------------------------------------------

def test_inventory_lists_the_pack_skill_with_its_source(tmp_path):
    from minder_memory import skill_inventory
    root, index_path = _tree(tmp_path)
    rows = skill_inventory.inventory(index=index_path,
                                     skills_root=root)
    by_name = {r["name"]: r for r in rows}
    assert _PACK in by_name, f"{_PACK} invisible: {sorted(by_name)}"
    row = by_name[_PACK]
    assert row["source"] == _PACK_SOURCE
    assert row["description"].startswith("handles")
    # an unindexed folder is visible but never matched: triggers are a
    # claim about matching, and only the index makes that claim
    assert row["triggers"] == []
    assert row["body_ok"] is True


def test_inventory_marks_a_folderless_index_entry_body_missing(tmp_path):
    """An index entry with no body file is inert: it is advertised but
    never loads. The inventory must keep reporting that (issue #12's
    page action depends on it)."""
    from minder_memory import skill_inventory
    root, index_path = _tree(tmp_path)
    rows = skill_inventory.inventory(index=index_path,
                                     skills_root=root)
    gone = [r for r in rows if r["name"] == "gone-skill"]
    assert gone and gone[0]["body_ok"] is False


def test_inventory_survives_a_missing_lock_file(tmp_path):
    """No lock file is the pre-install state, not an error: the index
    entries alone must still come back."""
    from minder_memory import skill_inventory
    root, index_path = _tree(tmp_path)
    (root.parent / "skills-lock.json").unlink()
    rows = skill_inventory.inventory(index=index_path,
                                     skills_root=root)
    names = {r["name"] for r in rows}
    assert _OWNED in names and "gone-skill" in names


# --- the gap detector sees the whole inventory ----------------------------

def test_an_indexed_skill_trigger_suppresses_a_gap_row(tmp_path):
    """The runtime half of the issue: a failure a registered skill
    already covers must not be filed as missing. This one passed before
    the change and is here because widening the inventory must not break
    the one behaviour the index is for."""
    from minder_memory import skills
    root, index_path = _tree(tmp_path)
    dbp = new_db(tmp_path)
    ev = _failure(_OWNED_TRIG)
    ev["failure_key"] = "bash|connectionrefusederror|conn|a.py"
    out = skills.check_skill_gap(ev, db_path=dbp, index_path=index_path,
                                 attempts=2)
    assert out["skill"] == _OWNED
    assert out["recorded"] is False
    assert skills.list_gaps(REPO, db_path=dbp) == []


def test_uncovered_failure_still_files_a_gap(tmp_path):
    """The inventory widens coverage, it does not silence evidence: an
    unmapped failure still records, or the page stops being a record."""
    from minder_memory import skills
    root, index_path = _tree(tmp_path)
    dbp = new_db(tmp_path)
    ev = _failure("IndexError: list index out of range")
    ev["failure_key"] = "bash|indexerror|row|report.py"
    out = skills.check_skill_gap(ev, db_path=dbp, index_path=index_path,
                                 attempts=2)
    assert out["skill"] is None
    assert out["recorded"] is True
    assert len(skills.list_gaps(REPO, db_path=dbp)) == 1


# --- the console page -----------------------------------------------------

def _patch_roots(monkeypatch, root, index_path):
    """Point the inventory and the loaders at a synthetic tree. The lock
    file sits next to the skills root, the way it does in a real
    install, so the environment override alone is enough."""
    monkeypatch.setenv("MINDER_SKILLS_INDEX", str(index_path))
    import minder_memory.skill_load as load
    import minder_memory.skills as skills
    monkeypatch.setattr(load, "DEFAULT_INDEX", index_path)
    monkeypatch.setattr(skills, "DEFAULT_INDEX", index_path)


def test_skills_page_lists_the_pack_skill(tmp_path, monkeypatch):
    root, index_path = _tree(tmp_path)
    _patch_roots(monkeypatch, root, index_path)
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/skills").text
    assert _PACK in text, f"{_PACK} missing from /skills"
    assert _PACK_SOURCE in text
    assert _OWNED in text


def test_skills_page_says_an_index_entry_has_no_body(tmp_path, monkeypatch):
    root, index_path = _tree(tmp_path)
    _patch_roots(monkeypatch, root, index_path)
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/skills").text
    assert "gone-skill" in text
    # the copy for an index entry whose body file is absent
    assert "missing" in text


def test_skills_page_names_how_many_skills_are_covered(tmp_path,
                                                       monkeypatch):
    """The point of the page for a non-technical reader is the count:
    how many skills exist and how many of them can actually fire. A page
    that only lists rows never says which of the two the reader is
    looking at."""
    root, index_path = _tree(tmp_path)
    _patch_roots(monkeypatch, root, index_path)
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/skills").text
    assert "3 skill(s) installed" in text
    assert "2 of them" in text and "trigger" in text


def test_skills_page_inventoried_rows_carry_a_source_column(
        tmp_path, monkeypatch):
    """The raw tier is the technical reader's check against a CLI row,
    so the new source column must be named there too (issue #12's
    tier contract)."""
    root, index_path = _tree(tmp_path)
    _patch_roots(monkeypatch, root, index_path)
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/skills").text
    assert "raw fields: " in text
    tier = text.split("raw fields:", 1)[1].split("</summary>", 1)[0]
    for field in ("name", "description", "triggers", "risk_level",
                  "body_ok", "source"):
        assert field in tier, f"tier omits {field}: {tier}"
