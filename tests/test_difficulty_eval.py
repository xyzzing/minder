"""difficulty-core-v1 replay engine + calibration metrics (issue #34).

The replay mirrors routing-core-v1's contract: gold-labelled cases
through a provider and resolve_difficulty, an 8C-schema report the
shipped benchmark comparator accepts, and a fingerprint that breaks
comparability when the gold labels change. Calibration metrics
(Brier, binned ECE, selective accuracy/coverage) are the prd-v0.10 T30
slice that makes the before/after claim checkable."""
import json
from datetime import datetime, timezone

import pytest

CASES = "benchmarks/difficulty-core-v1/cases/difficulty_cases.json"
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def _mig(tmp_path):
    from minder_memory import db as _db
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def test_null_provider_replays_to_a_valid_all_abstain_report(tmp_path):
    from minder_decision import difficulty_eval
    from minder_decision.providers.null import NullClient
    from minder_op import benchmark as bench
    report = difficulty_eval.replay_cases(
        CASES, provider=NullClient(), db_path=_mig(tmp_path), now=NOW)
    errors = bench.validate_report(report)
    assert errors == []
    assert report["suite_id"] == "difficulty-core-v1"
    assert report["metrics"]["comparable_runs"] == 200
    assert report["metrics"]["verified_completion_rate"] == 0.0
    assert report["metrics"]["unsafe_executions"] == 0
    detail = report["difficulty_detail"]
    assert detail["acted"] == 0
    assert detail["abstentions"] == 200
    assert detail["selective_accuracy"] is None
    assert detail["coverage"] == 0.0
    assert detail["label_status"].startswith(("draft_", "owner_accepted_"))
    assert report["calibration_detail"]["brier"] is not None


def test_fixture_provider_scores_accuracy_and_selective_metrics(tmp_path):
    from minder_decision import difficulty_eval
    from minder_op import benchmark as bench

    class _Mechanical:
        """Always says mechanical with a passing confidence: accuracy
        must equal the share of mechanical golds, acted must be all."""

        model_version = "mechanical-fixture"

        def system_one(self, state, questions, model=None, contract=None):
            from minder_decision.providers.fake import FakeClient
            spec = {}
            for question in questions:
                if hasattr(question, "options"):
                    spec[f"{question.id}_probs"] = {
                        option: (0.8 if option == "mechanical" else
                                 0.2 / (len(question.options) - 1))
                        for option in question.options}
                else:
                    spec[question.id] = 0
            spec["confidence"] = 0.9
            key = "k"
            return FakeClient({key: spec}, key_fn=lambda _s: key).system_one(
                key, questions, model=model, contract=contract)

    report = difficulty_eval.replay_cases(
        CASES, provider=None, db_path=_mig(tmp_path), now=NOW)
    assert bench.validate_report(report) == []
    report = difficulty_eval.replay_cases(
        CASES, provider=_Mechanical(), db_path=_mig(tmp_path), now=NOW)
    assert bench.validate_report(report) == []
    cases = json.load(open(CASES))["cases"]
    mechanical = sum(1 for c in cases
                     if c["gold"]["label"] == "mechanical")
    detail = report["difficulty_detail"]
    assert detail["acted"] == 200
    assert detail["correct"] == mechanical
    assert detail["selective_accuracy"] == pytest.approx(mechanical / 200)
    assert report["metrics"]["verified_completion_rate"] == pytest.approx(
        mechanical / 200)


def test_fingerprint_breaks_when_gold_labels_change(tmp_path):
    from minder_decision import difficulty_eval
    original = json.load(open(CASES))
    doc = json.loads(json.dumps(original))
    # a consistent relabel (label and its bucket score together) must
    # break comparability via the digest
    doc["cases"][0]["gold"] = {"label": "routine", "score": 1}
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(doc))
    base_fp = difficulty_eval.suite_fingerprint(CASES)
    tampered_fp = difficulty_eval.suite_fingerprint(str(tampered))
    assert base_fp != tampered_fp
    assert len(base_fp) == 64


def test_a_label_that_disagrees_with_its_score_stops_the_replay(tmp_path):
    from minder_decision import difficulty_eval
    doc = json.loads(json.dumps(json.load(open(CASES))))
    doc["cases"][0]["gold"]["label"] = "routine"  # score stays 0: invalid
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        difficulty_eval.load_cases(str(bad))


def test_cli_difficulty_replay_null_provider(tmp_path, capsys):
    from minder_op.cli import EXIT_OK, main
    dbp = _mig(tmp_path)
    out = tmp_path / "report.json"
    code = main(["--db", str(dbp), "difficulty", "replay", "--cases", CASES,
                 "--provider", "null", "--out", str(out)])
    assert code == EXIT_OK
    saved = json.load(open(out))
    assert saved["suite_id"] == "difficulty-core-v1"
    assert saved["difficulty_detail"]["provider"] == "null"


def test_cli_rejects_unknown_provider(tmp_path):
    from minder_op.cli import EXIT_USAGE, main
    dbp = _mig(tmp_path)
    code = main(["--db", str(dbp), "difficulty", "replay", "--cases", CASES,
                 "--provider", "rules"])
    assert code == EXIT_USAGE


def test_brier_score_pins_hand_computed_values():
    from minder_core.calibration import brier_score
    labels = ("a", "b")
    dists = [{"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}]
    golds = ["a", "b"]
    # row 1: perfect -> 0; row 2: uniform -> (0.25 + 0.25)
    assert brier_score(dists, golds, labels) == pytest.approx(0.25)
    assert brier_score([], [], labels) is None


def test_ece_pins_hand_computed_values():
    from minder_core.calibration import expected_calibration_error
    # 10 samples at confidence 0.8, 6 correct -> bin accuracy 0.6,
    # bin confidence 0.8 -> ece = |0.6 - 0.8| = 0.2
    confidences = [0.8] * 10
    hits = [True] * 6 + [False] * 4
    assert expected_calibration_error(confidences, hits) == pytest.approx(
        0.2)
    # perfectly calibrated: ece 0
    assert expected_calibration_error([1.0] * 5, [True] * 5) == pytest.approx(
        0.0)
    assert expected_calibration_error([], []) is None


def test_selective_summary_pins_the_floor_semantics():
    from minder_core.calibration import selective_summary
    records = [(0.9, True), (0.8, True), (0.5, False), (0.2, False)]
    summary = selective_summary(records, floor=0.7)
    assert summary["acted"] == 2
    assert summary["coverage"] == pytest.approx(0.5)
    assert summary["selective_accuracy"] == pytest.approx(1.0)
    nothing = selective_summary(records, floor=0.95)
    assert nothing["acted"] == 0
    assert nothing["selective_accuracy"] is None
    assert nothing["coverage"] == 0.0
