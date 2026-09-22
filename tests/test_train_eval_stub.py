"""Summarise-export stub tests (docs/minder-phase-4-7-frontier-coding.md
P7.2). Counts, families, held-out ratio, redaction check — and nothing
that resembles training."""
import json

import pytest

from memory import train_eval

FIXTURE = "tests/fixtures/export/held_out.json"


def test_summarise_fixture_counts_and_ratio():
    out = train_eval.summarise_export(FIXTURE)
    assert out["count"] == 2
    assert out["families"] == {"keyerror": 2}
    assert out["held_out_ratio"] == 0.5
    assert out["splits"] == {"held_out": 1, "train": 1}
    assert out["redaction_ok"] is True


def test_summarise_jsonl_export(tmp_path):
    record = {"candidate_id": "tc_x", "failure_family": "asserterror",
              "split": "train", "trajectory": [{"step": "inspect",
                                                "detail": "boom"}]}
    path = tmp_path / "training_candidates.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out = train_eval.summarise_export(path)
    assert out["count"] == 1 and out["families"] == {"asserterror": 1}
    assert out["held_out_ratio"] == 0.0
    assert out["redaction_ok"] is True


def test_redaction_check_flags_surviving_secrets(tmp_path):
    record = {"candidate_id": "tc_bad", "failure_family": "keyerror",
              "split": "train",
              "trajectory": [{"step": "inspect",
                              "detail": "leak ghp_AbCdEfGh12345678"}]}
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out = train_eval.summarise_export(path)
    assert out["redaction_ok"] is False


def test_missing_file_degrades_without_raising(tmp_path):
    out = train_eval.summarise_export(tmp_path / "nope.jsonl")
    assert out["count"] == 0
    assert out["redaction_ok"] is False


def test_no_training_surface():
    """Stub law: imports no trainer/shell/network surface at all."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(train_eval.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"json", "pathlib", "memory", "canonicalise"}
