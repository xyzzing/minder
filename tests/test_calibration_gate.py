"""The promotion gate: fit -> replay the eval set -> compare (issue #36).

A fitted scale is only promotable if the disjoint eval set agrees. The
live shape of the defect: a scale chosen on the fitting split, replayed
against the eval set, and a `minder-op` verdict that quietly passes a
comparison of two different suites, or passes on eight cases.

prd-v0.10 6.9.3: fitting and evaluating on the same cases is refused,
not warned about. prd-v0.10 6.9 thresholds: a selective-accuracy drop
beyond 5 points or a calibration-error rise beyond 5 points fails;
below MIN_COMPARABLE_RUNS is INSUFFICIENT_SAMPLE; a suite-fingerprint
mismatch is NON_COMPARABLE. The difficulty report's numbers live in
difficulty_detail/calibration_detail, which the shipped
protected-metric comparator never reads, so the gate needs its own
verdict pass over those keys.
"""
import copy
import json
from datetime import datetime, timezone

import pytest

# getattr, not a module-level import: the proven-red gate runs this file
# against pre-change code, where a missing module fails collection and
# reports no symbol instead of the assertion that shows the gate was
# absent.
calibrate = getattr(
    __import__("minder_decision", fromlist=["calibrate"]), "calibrate", None)

CASES = "benchmarks/difficulty-core-v1/cases/difficulty_cases.json"
SUITE = "difficulty-core-v1"
BUNDLE = "presets/laya-specializations/difficulty-v1"
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


def _report(**over):
    """A minimal valid difficulty replay report, the shape
    minder_decision.difficulty_eval.replay_cases emits."""
    report = {
        "report_version": 1,
        "suite_id": SUITE,
        "suite_fingerprint": "a" * 64,
        "generated_at": NOW.isoformat(),
        "kind": "candidate",
        "runs": [],
        "metrics": {
            "comparable_runs": 200,
            "verified_completion_rate": 0.2,
            "unsafe_executions": 0,
            "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0,
        },
        "difficulty_detail": {
            "contract_id": "task-difficulty",
            "provider": "laya-spec",
            "correct": 50,
            "abstentions": 134,
            "label_status": "draft_pending_owner_review",
            "mean_band_distance": 0.4,
            "acted": 66,
            "coverage": 0.33,
            "selective_accuracy": 0.7576,
        },
        "calibration_detail": {"brier": 0.5, "ece": 0.2, "ece_bins": 10},
    }
    for key, value in over.items():
        if key in ("difficulty_detail", "calibration_detail", "metrics"):
            report[key] = {**report[key], **value}
        else:
            report[key] = value
    return report


def test_a_matching_improvement_passes_the_gate():
    assert calibrate is not None
    base = _report()
    cand = _report(difficulty_detail={"selective_accuracy": 0.80,
                                      "coverage": 0.40, "acted": 80},
                   calibration_detail={"ece": 0.15})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "PASS"
    assert verdict["reasons"] == []


def test_a_selective_accuracy_drop_beyond_five_points_fails():
    assert calibrate is not None
    base = _report()
    cand = _report(difficulty_detail={"selective_accuracy": 0.70})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "FAIL"
    assert any("selective_accuracy" in r for r in verdict["reasons"])


def test_a_selective_accuracy_drop_within_the_bound_does_not_fail():
    """The bound is 5 points, not 'any drop'. A candidate 4 points down
    with better coverage is a real trade the gate must allow."""
    assert calibrate is not None
    base = _report()
    cand = _report(difficulty_detail={"selective_accuracy": 0.7176})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "PASS"


def test_a_calibration_error_rise_beyond_five_points_fails():
    assert calibrate is not None
    base = _report()
    cand = _report(calibration_detail={"ece": 0.26})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "FAIL"
    assert any("ece" in r for r in verdict["reasons"])


def test_a_candidate_that_acts_on_nothing_fails_rather_than_passing_none():
    """selective_accuracy None is not a zero and not a pass: it is the
    live inert-router state (831 below_confidence, 0 applied), and a
    gate that passes it is how the inert install survived this long."""
    assert calibrate is not None
    base = _report()
    cand = _report(difficulty_detail={"selective_accuracy": None,
                                      "coverage": 0.0, "acted": 0,
                                      "abstentions": 200})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "FAIL"
    assert any("coverage" in r or "acts on nothing" in r
               for r in verdict["reasons"])


def test_too_few_comparable_cases_is_insufficient_not_a_pass():
    assert calibrate is not None
    from minder_core.comparator import MIN_COMPARABLE_RUNS
    base = _report(metrics={"comparable_runs": MIN_COMPARABLE_RUNS - 1})
    cand = _report(metrics={"comparable_runs": MIN_COMPARABLE_RUNS - 1})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "INSUFFICIENT_SAMPLE"


def test_a_fingerprint_mismatch_is_non_comparable():
    assert calibrate is not None
    base = _report()
    cand = _report(suite_fingerprint="b" * 64)
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "NON_COMPARABLE"
    assert any("suite_fingerprint" in r for r in verdict["reasons"])


def test_a_safety_metric_regression_fails_at_any_sample_size():
    """Precedence: safety first. The difficulty band has no egress
    surface today, so this stays zero by construction, and the gate says
    so rather than trusting it."""
    assert calibrate is not None
    base = _report()
    cand = _report(metrics={"external_prohibited_egress": 1,
                            "comparable_runs": 5})
    verdict = calibrate.gate_difficulty(base, cand)
    assert verdict["verdict"] == "FAIL"


def test_the_two_verdict_passes_agree_on_the_shared_rules():
    """M2: the gate reuses the shipped comparator's precedence, it does
    not fork a weaker law. On the shared rules (fingerprint, safety,
    sample size) both must hand back the same verdict."""
    assert calibrate is not None
    from minder_core.comparator import compare_reports
    for cand in (_report(suite_fingerprint="c" * 64),
                 _report(metrics={"unsafe_executions": 1}),
                 _report(metrics={"comparable_runs": 3})):
        base = _report() if cand is not _report(metrics={"comparable_runs": 3}) \
            else _report(metrics={"comparable_runs": 3})
        assert (calibrate.gate_difficulty(base, cand)["verdict"]
                == compare_reports(base, cand)["verdict"])


def test_a_fitted_scale_sharing_case_ids_with_the_eval_set_is_refused():
    """prd-v0.10 6.9.3: the refusal is the point. A fitter that scores
    itself on the cases it fitted on reports an accuracy the live
    router will never show."""
    assert calibrate is not None
    fitting = [{"id": "dc001", "margin": 0.2, "correct": True,
                "probs": _probs(0.2), "gold": "mechanical"}]
    with pytest.raises(ValueError) as exc:
        calibrate.fit_and_gate(cases_path=CASES, fitting_rows=fitting,
                               provider=_NullProvider(),
                               scales=(10.0, 20.0))
    assert "dc001" in str(exc.value)


def test_the_shipped_fitting_corpus_is_disjoint_from_the_eval_set():
    """M2: the gate's denominator. The refusal above is only worth
    anything if the real corpus can pass it, and the real corpus is the
    one the bundle was exported from."""
    assert calibrate is not None
    rows = calibrate.load_fitting_rows(
        "benchmarks/difficulty-core-v1/fitting/hard.json")
    assert len(rows) == 34
    verdict = calibrate.check_split_disjoint(
        rows, _eval_case_ids(CASES))
    assert verdict["ok"] is True
    assert verdict["fitting"] == 34
    assert verdict["eval"] == 200


class _NullProvider:
    """Enough of a provider to reach the split check. The refusal has to
    come before any fitting work, so the test needs a provider object
    but never a working one."""

    model_version = "null-fixture"

    def system_one(self, state, questions, model=None, contract=None):
        return None


def _probs(margin):
    return {"mechanical": margin + 0.4, "routine": 0.4, "complex": 0.1,
            "expert_or_ambiguous": 0.1}


def _eval_case_ids(cases_path):
    doc = json.loads(open(cases_path).read())
    return [c["id"] for c in doc["cases"]]


def test_the_promotion_record_names_everything_that_invalidates_it():
    """A promotion pins a scale to a bundle revision, a label digest and
    a suite fingerprint. Any of those moving means the pinned number
    describes something no longer installed."""
    assert calibrate is not None
    record = calibrate.promotion_record(
        scale=18.0, bundle_path="presets/laya-specializations/difficulty-v1",
        cases_path=CASES, now=NOW)
    assert record["margin_scale"] == 18.0
    assert record["suite_id"] == SUITE
    assert len(record["suite_fingerprint"]) == 64
    assert len(record["label_digest"]) == 64
    assert record["bundle_revision"]
    assert record["promoted_at"] == NOW.isoformat()
    assert record["confidence_source"] == "distribution-margin"


def test_the_record_names_the_baseline_it_was_gated_against():
    """The gate refuses to run without a pinned baseline, so a record
    that does not say which report was the denominator cannot be
    re-checked after the fact. The baseline is a file an operator can
    overwrite, and a promotion pinned against the old one then claims a
    comparison that no longer exists."""
    assert calibrate is not None
    # the fingerprint the record must carry is the baseline's own, not
    # the candidate's: a 64-hex value no other report in this test has
    baseline = _report(kind="baseline", suite_fingerprint="b" * 64)
    record = calibrate.promotion_record(
        scale=18.0, bundle_path="presets/laya-specializations/difficulty-v1",
        cases_path=CASES, now=NOW, baseline=baseline)
    assert record["baseline_fingerprint"] == "b" * 64
    assert record["baseline_path"].endswith(
        "baselines/difficulty-core-v1.json")


def test_the_record_is_invalidated_by_a_relabel():
    assert calibrate is not None
    record = calibrate.promotion_record(
        scale=18.0, bundle_path="presets/laya-specializations/difficulty-v1",
        cases_path=CASES, now=NOW)
    tampered = copy.deepcopy(json.loads(open(CASES).read()))
    tampered["cases"][0]["gold"] = {"label": "routine", "score": 1}
    tampered_path = "/tmp/tampered_cases.json"
    with open(tampered_path, "w") as fh:
        json.dump(tampered, fh)
    drift = calibrate.record_drift(
        record, cases_path=tampered_path,
        bundle_path="presets/laya-specializations/difficulty-v1")
    assert "label_digest" in drift
    assert "suite_fingerprint" in drift
    assert "bundle_revision" not in drift


def test_a_re_pinned_baseline_invalidates_the_record(tmp_path):
    """The record names the baseline file the verdict was computed
    against. Overwriting that file is legal, and a promotion whose
    denominator changed is a claim about a comparison nobody can rerun,
    so the drift check has to notice the file moved under it."""
    assert calibrate is not None
    baseline = _report(kind="baseline", suite_fingerprint="b" * 64)
    record = calibrate.promotion_record(
        scale=18.0, bundle_path="presets/laya-specializations/difficulty-v1",
        cases_path=CASES, now=NOW, baseline=baseline)
    path = tmp_path / "baselines" / "difficulty-core-v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_report(kind="baseline",
                                       suite_fingerprint="b" * 64),
                               sort_keys=True))
    assert calibrate.record_drift(record, cases_path=CASES,
                                  bundle_path=BUNDLE,
                                  baseline_path=path) == []
    over = tmp_path / "baselines" / "difficulty-core-v1-new.json"
    over.write_text(json.dumps(_report(kind="baseline",
                                       suite_fingerprint="c" * 64),
                               sort_keys=True))
    drift = calibrate.record_drift(record, cases_path=CASES,
                                   bundle_path=BUNDLE, baseline_path=over)
    assert drift == ["baseline_fingerprint"]


def test_a_missing_baseline_is_drift_not_a_crash(tmp_path):
    """The file the record names can be deleted. A drift check that
    raised would take doctor down with it."""
    assert calibrate is not None
    baseline = _report(kind="baseline", suite_fingerprint="b" * 64)
    record = calibrate.promotion_record(
        scale=18.0, bundle_path="presets/laya-specializations/difficulty-v1",
        cases_path=CASES, now=NOW, baseline=baseline)
    drift = calibrate.record_drift(
        record, cases_path=CASES, bundle_path=BUNDLE,
        baseline_path=tmp_path / "absent.json")
    assert drift == ["baseline_fingerprint"]


def test_a_baseline_replaced_at_another_scale_is_named_as_such(tmp_path):
    """Re-pinning is normal; re-pinning at a different margin scale is
    the specific thing that makes a promoted number meaningless, because
    the candidate was replayed at the fitted scale against a denominator
    at the old one. The drift line has to say which fact broke, not just
    that a file changed."""
    assert calibrate is not None
    baseline = _report(kind="baseline", suite_fingerprint="b" * 64,
                       difficulty_detail={"margin_scale": 20.0})
    record = calibrate.promotion_record(
        scale=18.0, bundle_path=BUNDLE, cases_path=CASES, now=NOW,
        baseline=baseline)
    assert record["baseline_scale"] == 20.0
    path = tmp_path / "baselines" / "difficulty-core-v1.json"
    path.parent.mkdir(parents=True)
    same_fp = json.dumps(baseline, sort_keys=True)
    path.write_text(same_fp)
    assert calibrate.record_drift(record, cases_path=CASES,
                                  bundle_path=BUNDLE,
                                  baseline_path=path) == []
    # same suite, new denominator at a scale the record never saw
    path.write_text(json.dumps(_report(kind="baseline",
                                       suite_fingerprint="b" * 64,
                                       difficulty_detail={"margin_scale": 5.0},
                                       generated_at=NOW.isoformat()),
                               sort_keys=True))
    drift = calibrate.record_drift(record, cases_path=CASES,
                                   bundle_path=BUNDLE, baseline_path=path)
    assert drift == ["baseline_scale"]


def test_the_record_finds_its_baseline_after_the_root_moves(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """`MINDER_BENCHMARKS_DIR` is per-invocation, so the record written
    under one root is read back from another. A record that stored the
    absolute path then reports drift on a baseline that has not moved,
    and an operator chasing that drift re-pins a denominator that was
    never lost. The record stores where in the root the report lives."""
    assert calibrate is not None
    import pathlib
    first = tmp_path / "root-a"
    second = tmp_path / "root-b"
    for root in (first, second):
        monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
        path = calibrate.default_baseline_path_for(calibrate.SUITE_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_report(kind="baseline",
                                           suite_fingerprint="b" * 64,
                                           difficulty_detail={
                                               "margin_scale": 20.0}),
                                   sort_keys=True))
        record = calibrate.promotion_record(
            scale=18.0, bundle_path=BUNDLE, cases_path=CASES, now=NOW,
            baseline=_report(kind="baseline", suite_fingerprint="b" * 64,
                             difficulty_detail={"margin_scale": 20.0}))
        if root == first:
            assert pathlib.Path(record["baseline_path"]).is_absolute() is False
            pinned = record["baseline_path"]
        else:
            assert record["baseline_path"] == pinned
            loaded = calibrate._pinned_baseline_report(record["baseline_path"])
            assert loaded is not None, "the record lost its own baseline"
            assert loaded["difficulty_detail"]["margin_scale"] == 20.0
