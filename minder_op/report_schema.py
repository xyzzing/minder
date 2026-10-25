"""Benchmark report-envelope schema validation (minder benchmark
report v1/v2), split from minder_op.benchmark so the envelope law owns
a file of its own - it grows with every measurement plane (v2 quality
tier, issue #48's pass^k / cost / carbon) while the rest of benchmark
handles suites, manifests, and baselines.

Unknown extra keys are tolerated (the fingerprint pins
comparability); required keys are strict. Validation shapes numbers,
it never judges them: cost is a reported trend, never a gate
(issue #24's law).
"""
from __future__ import annotations

import re
from datetime import datetime

SUPPORTED_REPORT_VERSIONS = (1, 2)
KNOWN_KINDS = ("baseline", "candidate")

_FP_RE = re.compile(r"[0-9a-f]{64}")

_METRIC_INTS = ("comparable_runs", "unsafe_executions",
                "harmful_frontier_acceptances", "external_prohibited_egress")


def is_int(value):
    """True for real ints; bools are ints in Python but never counts."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_report(report):
    """Report/baseline schema validation. Returns error strings; empty
    means valid. Unknown extra keys are tolerated (the fingerprint
    pins comparability), required keys are strict."""
    errors = []
    if not isinstance(report, dict):
        return ["report is not a JSON object"]
    if report.get("report_version") not in SUPPORTED_REPORT_VERSIONS:
        errors.append(f"unsupported report_version: "
                      f"{report.get('report_version')!r}")
    if not isinstance(report.get("suite_id"), str) or \
            not report.get("suite_id"):
        errors.append("suite_id must be a non-empty string")
    fp = report.get("suite_fingerprint")
    if not isinstance(fp, str) or not _FP_RE.fullmatch(fp):
        errors.append("suite_fingerprint must be 64 lowercase hex chars")
    try:
        datetime.fromisoformat(str(report.get("generated_at")))
    except (TypeError, ValueError):
        errors.append(f"generated_at is not an ISO timestamp: "
                      f"{report.get('generated_at')!r}")
    kind = report.get("kind")
    if kind is not None and kind not in KNOWN_KINDS:
        errors.append(f"kind must be one of {KNOWN_KINDS}, got {kind!r}")
    runs = report.get("runs")
    if runs is not None:
        if not isinstance(runs, list):
            errors.append("runs must be a list")
        else:
            for run in runs:
                if not isinstance(run, dict) or \
                        not isinstance(run.get("task_id"), str) or \
                        not isinstance(run.get("status"), str):
                    errors.append(f"bad run entry: {run!r}")
                    break
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")
        return errors
    for key in _METRIC_INTS:
        value = metrics.get(key)
        if not is_int(value) or value < 0:
            errors.append(f"metrics.{key} must be an integer >= 0, "
                          f"got {value!r}")
    rate = metrics.get("verified_completion_rate")
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) \
            or not 0.0 <= float(rate) <= 1.0:
        errors.append(f"metrics.verified_completion_rate must be a "
                      f"number in [0, 1], got {rate!r}")
    # Measurement-backbone metrics (issue #48): optional on v1 - a
    # report without them did not measure them, never a failure - and
    # none of them is a gate (issue #24's law).
    pk = metrics.get("pass_k")
    if pk is not None and (not isinstance(pk, (int, float))
                           or isinstance(pk, bool)
                           or not 0.0 <= float(pk) <= 1.0):
        errors.append(f"metrics.pass_k must be a number in [0, 1], "
                      f"got {pk!r}")
    cost = metrics.get("cost_per_correct")
    if cost is not None:
        if not isinstance(cost, dict):
            errors.append("metrics.cost_per_correct must be an object")
        else:
            toks = cost.get("tokens")
            if toks is not None and (not is_int(toks) or toks < 0):
                errors.append("metrics.cost_per_correct.tokens must be "
                              f"an integer >= 0, got {toks!r}")
            usd = cost.get("usd")
            if usd is not None and (not isinstance(usd, (int, float))
                                    or isinstance(usd, bool) or usd < 0):
                errors.append("metrics.cost_per_correct.usd must be a "
                              f"number >= 0, got {usd!r}")
    carbon = metrics.get("gco2e_per_correct")
    if carbon is not None and (not isinstance(carbon, (int, float))
                               or isinstance(carbon, bool) or carbon < 0):
        errors.append(f"metrics.gco2e_per_correct must be a number "
                      f">= 0, got {carbon!r}")
    if report.get("report_version") == 2:
        # Quality tier keys are STRICT for v2 (PRD v0.9 5.6): a v2
        # report that quietly omits the quality tier would read as a
        # clean run rather than an unmeasured one (I-3).
        for key in ("clean_completion_rate",):
            rate = metrics.get(key)
            if not isinstance(rate, (int, float)) \
                    or isinstance(rate, bool) \
                    or not 0.0 <= float(rate) <= 1.0:
                errors.append(f"metrics.{key} must be a number in "
                              f"[0, 1], got {rate!r}")
        for key in ("verification_tampering", "underverified_runs",
                    "holdout_failures", "quality_measured_runs"):
            value = metrics.get(key)
            if not is_int(value) or value < 0:
                errors.append(f"metrics.{key} must be an integer >= 0, "
                              f"got {value!r}")
        for key in ("mean_net_lines_per_verified",
                    "blocking_findings_per_verified",
                    "advisory_findings_per_verified"):
            value = metrics.get(key)
            if not isinstance(value, (int, float)) \
                    or isinstance(value, bool) or value < 0:
                errors.append(f"metrics.{key} must be a number >= 0, "
                              f"got {value!r}")
        afp = report.get("analyzer_set_fingerprint")
        if not isinstance(afp, str) or not (
                _FP_RE.fullmatch(afp.removeprefix("sha256:"))
                if afp.startswith("sha256:") else False):
            errors.append("analyzer_set_fingerprint must be "
                          "sha256:<64 hex>")
    return errors
