"""Skill evaluation fixtures runner (docs/minder-phase-2-3-frontier-coding.md
P2.4). Evaluates a fixture by running the real progressive-disclosure
selector and comparing selections. No GPU, no network. The runner FAILS
loudly on mismatch — never silently passes.
"""
import json
from pathlib import Path

from . import skill_load

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" \
    / "skills" / "eval"


def load_fixture(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "event" not in data or \
            "expect" not in data:
        raise ValueError(f"malformed skill fixture: {path}")
    return data


def evaluate_skill_selection(fixture, index_path=None):
    """{ok, selected, expected, errors[]} — deterministic, offline."""
    event = fixture.get("event") or {}
    expect = fixture.get("expect") or {}
    selection = skill_load.select_skills_for_event(event,
                                                   index_path=index_path)
    selected = list(selection["selected"])
    expected = list(expect.get("selected") or [])
    errors = []
    for name in expected:
        if name not in selected:
            errors.append(f"expected skill not selected: {name}")
    for name in selected:
        if name not in expected:
            errors.append(f"unexpected skill selected: {name}")
    return {"ok": not errors, "selected": selected, "expected": expected,
            "errors": errors,
            "loaded": [s["name"] for s in selection["loaded"]],
            "gap": selection["gap"],
            "forbidden_actions": list(expect.get("forbidden_actions") or [])}


def run_fixture_file(path, index_path=None):
    return evaluate_skill_selection(load_fixture(path),
                                    index_path=index_path)
