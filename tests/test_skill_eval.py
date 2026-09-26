"""Skill evaluation fixture tests (spec P2.4)."""
import json
from pathlib import Path

import pytest

from minder_memory import skill_eval

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skills" / "eval"


def test_schema_mismatch_selects_inspect_skill():
    result = skill_eval.run_fixture_file(FIXTURES / "schema_mismatch.json")
    assert result["ok"], result["errors"]
    assert result["selected"] == ["inspect-schema-boundary"]
    assert result["loaded"] == ["inspect-schema-boundary"]


def test_env_fixture_selects_no_code_edit_skill():
    result = skill_eval.run_fixture_file(FIXTURES / "env_missing.json")
    assert result["ok"], result["errors"]
    assert result["selected"] == ["diagnose-environment"]
    assert "inspect-schema-boundary" not in result["selected"]


def test_eval_runner_fails_loudly_on_mismatch(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "name": "wrong_expectation",
        "event": {"event_type": "tool_failure", "tool": "bash",
                  "failure_key": "bash|keyerror|x|y",
                  "error_excerpt": "KeyError: 'x'"},
        "expect": {"selected": ["diagnose-environment"]},
    }))
    result = skill_eval.run_fixture_file(bad)
    assert result["ok"] is False
    assert result["errors"] == [
        "expected skill not selected: diagnose-environment",
        "unexpected skill selected: inspect-schema-boundary"]


def test_malformed_fixture_raises_at_load_not_eval():
    bad = FIXTURES.parent / "malformed.json"
    bad.write_text('{"nope": true}')
    try:
        with pytest.raises(ValueError):
            skill_eval.load_fixture(bad)
    finally:
        bad.unlink()


def test_fixtures_are_offline_and_selfcontained():
    for f in FIXTURES.glob("*.json"):
        data = json.loads(f.read_text())
        blob = f.read_text().lower()
        assert "http://" not in blob and "https://" not in blob
        assert set(data) >= {"name", "event", "expect"}
