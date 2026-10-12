"""Repo skills registered with the gap detector (issue #37).

`skills/index.json` is what `check_skill_gap()` matches a failure against, so
a skill that exists only as `skills/<name>/SKILL.md` is invisible to it and
its failures get filed as gaps. These tests hold the two together.
"""
import json
from pathlib import Path

from minder_memory import skills

REPO = "/repo"
ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "skills" / "index.json"

NEW_SKILLS = ("minder-store-access", "minder-gap-triage")


def failure(**kw):
    base = {"event_type": "tool_failure", "tool": "bash", "repo": REPO,
            "failure_key": "bash|operationalerror|none|sqlite3.connect",
            "error_excerpt": "sqlite3.OperationalError: unable to open "
                             "database file"}
    base.update(kw)
    return base


def test_index_registers_the_two_new_skills_by_name():
    names = {s["name"] for s in json.loads(INDEX.read_text())}
    for want in NEW_SKILLS:
        assert want in names, f"{want} missing from skills/index.json"


def test_every_indexed_skill_has_a_body_that_exists():
    for entry in json.loads(INDEX.read_text()):
        body = ROOT / entry["body"]
        assert body.is_file(), f"{entry['name']} body {entry['body']} missing"
        assert body.read_text().strip(), f"{entry['name']} body is empty"


def test_every_indexed_skill_has_an_agent_skill_file():
    for name in NEW_SKILLS:
        skill_md = ROOT / "skills" / name / "SKILL.md"
        assert skill_md.is_file(), f"skills/{name}/SKILL.md missing"
        text = skill_md.read_text()
        assert text.startswith("---\nname: " + name + "\n"), \
            f"{name} frontmatter name must lead and match the directory"


def test_store_access_failure_records_no_gap(tmp_path):
    """A failure the skill covers must not be filed as a missing skill."""
    dbp = tmp_path / "m.sqlite"
    out = skills.check_skill_gap(failure(), db_path=dbp, attempts=2)
    assert out["skill"] == "minder-store-access"
    assert out["gap"] is False
    assert out["recorded"] is False
    assert skills.list_gaps(REPO, db_path=dbp) == []


def test_schema_drift_failure_records_no_gap(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ev = failure(failure_key="bash|operationalerror|tool_name|queries.py",
                 error_excerpt="sqlite3.OperationalError: no such column: "
                               "tool_name")
    out = skills.check_skill_gap(ev, db_path=dbp, attempts=2)
    assert out["skill"] == "minder-store-access"
    assert out["recorded"] is False


def test_read_only_filesystem_failure_records_no_gap(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ev = failure(failure_key="bash|oserror|ab|log_path.rename",
                 error_excerpt="OSError: [Errno 30] Read-only file system: "
                               "'/home/zacch/.local/state/minder/events.jsonl'")
    out = skills.check_skill_gap(ev, db_path=dbp, attempts=2)
    assert out["skill"] == "minder-store-access"
    assert out["recorded"] is False


def test_gap_machinery_failure_records_no_gap(tmp_path):
    dbp = tmp_path / "m.sqlite"
    ev = failure(failure_key="bash|valueerror|gap|skills.close_gap",
                 error_excerpt="ValueError: close_gap rejected: no such gap")
    out = skills.check_skill_gap(ev, db_path=dbp, attempts=2)
    assert out["skill"] == "minder-gap-triage"
    assert out["recorded"] is False


def test_unrelated_failure_still_files_a_gap(tmp_path):
    """The new triggers must stay narrow: a real unmapped failure still
    records, or the index is suppressing evidence instead of covering it."""
    dbp = tmp_path / "m.sqlite"
    ev = failure(failure_key="bash|zerodivisionerror|calc|math.py",
                 error_excerpt="ZeroDivisionError: division by zero")
    out = skills.check_skill_gap(ev, db_path=dbp, attempts=2)
    assert out["skill"] is None
    assert out["gap"] is True
    assert out["recorded"] is True
    assert len(skills.list_gaps(REPO, db_path=dbp)) == 1
