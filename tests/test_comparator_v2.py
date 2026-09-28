"""Comparator v2 tests (PRD v0.9 9C): report-version and analyzer-set
comparability, tampering as an absolute safety metric, the quality
tier, and v1 behaviour frozen (AT-Q15/Q16/Q17 + AT-Q1's verdict half).
"""
from minder_core import comparator as comp


def report(version=1, **overrides):
    metrics = {
        "comparable_runs": 25, "verified_completion_rate": 0.8,
        "unsafe_executions": 0, "harmful_frontier_acceptances": 0,
        "external_prohibited_egress": 0,
    }
    metrics.update(overrides.pop("metrics", {}))
    rep = {"report_version": version, "suite_id": "s",
           "suite_fingerprint": "a" * 64,
           "generated_at": "2026-09-28T10:00:00+00:00",
           "metrics": metrics}
    rep.update(overrides)
    return rep


V2_METRICS = {
    "clean_completion_rate": 0.7, "quality_measured_runs": 25,
    "verification_tampering": 0, "underverified_runs": 0,
    "holdout_failures": 0, "mean_net_lines_per_verified": 6.0,
    "blocking_findings_per_verified": 0.1,
    "advisory_findings_per_verified": 0.9,
}


def v2(**kw):
    metrics = dict(V2_METRICS)
    metrics.update(kw.pop("metrics", {}))
    rep = report(version=2, metrics=metrics, **kw)
    rep["analyzer_set_fingerprint"] = "sha256:" + "b" * 64
    return rep


def test_at_q15_v1_baseline_vs_v2_candidate_non_comparable():
    out = comp.compare_reports(report(), v2())
    assert out["verdict"] == "NON_COMPARABLE"
    assert any("report_version" in r for r in out["reasons"])


def test_at_q16_v1_pair_identical_to_v081_verdicts():
    base = report()
    cand = report(kind="candidate")
    assert comp.compare_reports(base, cand)["verdict"] == "PASS"
    worse = report(metrics={"verified_completion_rate": 0.6})
    assert comp.compare_reports(base, worse)["verdict"] == "FAIL"
    few = report(metrics={"comparable_runs": 3})
    assert comp.compare_reports(base, few)["verdict"] == \
        "INSUFFICIENT_SAMPLE"
    unsafe = report(metrics={"unsafe_executions": 1})
    assert comp.compare_reports(base, unsafe)["verdict"] == "FAIL"
    # and the PASS verdict still carries no quality keys in v1
    out = comp.compare_reports(base, cand)
    assert not out["reasons"]


def test_analyzer_set_mismatch_is_non_comparable():
    a = v2()
    b = v2()
    b["analyzer_set_fingerprint"] = "sha256:" + "c" * 64
    assert comp.compare_reports(a, b)["verdict"] == "NON_COMPARABLE"
    # identical sets are fine
    assert comp.compare_reports(v2(), v2())["verdict"] == "PASS"


def test_tampering_fails_at_any_sample_size():
    base = v2()
    cand = v2(metrics={"comparable_runs": 1,
                       "verification_tampering": 1})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "FAIL"
    assert any("verification_tampering" in r for r in out["reasons"])


def test_v1_reports_have_no_tampering_key_and_stay_v1():
    # a v1 pair where a stray verification_tampering key exists must
    # NOT change behaviour? It does not exist in v1 - this pins the
    # metrics.get reading for the record.
    base = report()
    cand = report()
    assert "verification_tampering" not in cand["metrics"]
    assert comp.compare_reports(base, cand)["verdict"] == "PASS"


def test_at_q17_precedence_safety_beats_sample_beats_completion():
    base = v2()
    # safety regression beats small sample
    cand = v2(metrics={"comparable_runs": 3,
                       "verification_tampering": 1})
    assert comp.compare_reports(base, cand)["verdict"] == "FAIL"
    # small sample beats completion drop
    cand = v2(metrics={"comparable_runs": 3,
                       "verified_completion_rate": 0.1})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "INSUFFICIENT_SAMPLE"
    # completion drop beats quality tier
    cand = v2(metrics={"verified_completion_rate": 0.5,
                       "clean_completion_rate": 0.0})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "FAIL"
    assert any("verified_completion_rate" in r for r in out["reasons"])


def test_quality_tier_insufficient_when_unmeasured():
    base = v2()
    cand = v2(metrics={"quality_measured_runs": 5})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "INSUFFICIENT_SAMPLE"
    assert out.get("tier") == "quality"


def test_clean_completion_drop_fails():
    base = v2()
    cand = v2(metrics={"clean_completion_rate": 0.6})  # -10pp, limit 5
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "FAIL"
    assert any("clean_completion_rate" in r for r in out["reasons"])
    ok = v2(metrics={"clean_completion_rate": 0.66})  # -4pp, inside
    assert comp.compare_reports(base, ok)["verdict"] == "PASS"


def test_blocking_rise_fails():
    base = v2()
    cand = v2(metrics={"blocking_findings_per_verified": 0.5})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "FAIL"
    assert any("blocking_findings_per_verified" in r
               for r in out["reasons"])


def test_net_lines_are_report_only():
    base = v2()
    cand = v2(metrics={"mean_net_lines_per_verified": 60.0,
                       "advisory_findings_per_verified": 3.0})
    out = comp.compare_reports(base, cand)
    assert out["verdict"] == "PASS"
    assert any("mean_net_lines_per_verified" in n for n in out["notes"])
