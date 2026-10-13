"""The promotion verdict for a difficulty replay pair (issue #36).

The shared rules come from the shipped comparator, so the difficulty
gate cannot fork a weaker law: fingerprint mismatch is NON_COMPARABLE, a
safety metric above zero is FAIL at any sample size, and fewer than
MIN_COMPARABLE_RUNS comparable cases is never a pass. On top of that sits
the difficulty pass over difficulty_detail / calibration_detail, which
the protected-metric comparator never reads - without it a scale that
abstains on everything passes on the protected metrics alone.

The other half of honesty is the denominator. A pinned baseline that
does not name the margin scale it was replayed at cannot be the
comparison target of a decision about changing that scale, so the gate
refuses it rather than assuming the shipped constant (M2).
"""
from minder_core.comparator import (MAX_COMPLETION_DROP, VERDICT_FAIL,
                                    VERDICT_PASS, compare_reports)

from .difficulty_eval import SUITE_ID
from .providers.laya import MARGIN_SCALE_ENV

# prd-v0.10 6.9: the same 5-point bound the protected metrics use,
# applied to the difficulty pair.
MAX_SELECTIVE_ACCURACY_DROP = MAX_COMPLETION_DROP
MAX_CALIBRATION_ERROR_RISE = MAX_COMPLETION_DROP


def gate_difficulty(baseline, candidate):
    """The promotion verdict for a difficulty replay pair.

    The shared rules come from compare_reports, so the difficulty gate
    cannot fork a weaker law: fingerprint mismatch is NON_COMPARABLE, a
    safety metric above zero is FAIL at any sample size, and fewer than
    MIN_COMPARABLE_RUNS comparable cases is INSUFFICIENT_SAMPLE. On top
    of that sits the difficulty pass over difficulty_detail and
    calibration_detail, which the protected comparator does not read."""
    shared = compare_reports(baseline, candidate)
    if shared["verdict"] != VERDICT_PASS:
        return {"verdict": shared["verdict"], "reasons": shared["reasons"],
                "baseline": baseline.get("metrics"),
                "candidate": candidate.get("metrics")}
    base_detail = baseline.get("difficulty_detail") or {}
    cand_detail = candidate.get("difficulty_detail") or {}
    base_cal = baseline.get("calibration_detail") or {}
    cand_cal = candidate.get("calibration_detail") or {}
    reasons = []
    cand_accuracy = cand_detail.get("selective_accuracy")
    if cand_accuracy is None or not cand_detail.get("acted"):
        # the live inert-router state: the floor declined every prior,
        # so there is nothing to score and nothing to promote
        reasons.append("candidate acts on nothing (coverage 0): a router "
                       "that never acts cannot be a calibrated one")
    else:
        base_accuracy = base_detail.get("selective_accuracy")
        if base_accuracy is not None:
            drop = base_accuracy - cand_accuracy
            if drop > MAX_SELECTIVE_ACCURACY_DROP + 1e-9:
                reasons.append(
                    f"selective_accuracy dropped {drop * 100:.1f}pp "
                    f"({base_accuracy:.4f} -> {cand_accuracy:.4f}, limit "
                    f"{MAX_SELECTIVE_ACCURACY_DROP * 100:.0f}pp)")
    base_ece, cand_ece = base_cal.get("ece"), cand_cal.get("ece")
    if base_ece is not None and cand_ece is not None:
        rise = cand_ece - base_ece
        if rise > MAX_CALIBRATION_ERROR_RISE + 1e-9:
            reasons.append(
                f"ece rose {rise:.3f} ({base_ece:.3f} -> {cand_ece:.3f}, "
                f"limit {MAX_CALIBRATION_ERROR_RISE:.3f})")
    verdict = VERDICT_FAIL if reasons else VERDICT_PASS
    return {"verdict": verdict, "reasons": reasons,
            "baseline": baseline.get("metrics"),
            "candidate": candidate.get("metrics"),
            "difficulty": {"baseline": base_detail, "candidate": cand_detail,
                           "baseline_ece": base_ece, "candidate_ece": cand_ece,
                           "comparable_runs": min(
                               (baseline.get("metrics") or {}).get(
                                   "comparable_runs", 0),
                               (candidate.get("metrics") or {}).get(
                                   "comparable_runs", 0))}}


def default_baseline_path_for(suite_id=SUITE_ID):
    """Where the suite's pinned baseline lives. Named here so the gate
    and a test can point at the same file without importing the
    operator plane for the path."""
    from minder_op.benchmark import default_baseline_path
    return default_baseline_path(suite_id)


def read_pinned_baseline(suite_id=SUITE_ID):
    """The pinned baseline report for the suite, or None. The gate
    compares against a report pinned earlier, never against one made up
    in the same run - that is the comparator law, and it is what makes
    the verdict a claim about the live router rather than about today's
    luck."""
    path = default_baseline_path_for(suite_id)
    if not path.is_file():
        return None
    from minder_op.benchmark import read_report
    return read_report(path)


def provider_margin_scale(provider):
    """The scale the provider under calibration maps with today: the
    promoted value if the environment carries one, otherwise the
    provider's own measured constant. A provider declares that constant
    (`margin_scale_default`) because the base head and the
    specialization bundle have different ones, and a scale is only
    worth fitting for the provider that will use it."""
    from .providers.laya import margin_scale
    default = getattr(provider, "margin_scale_default", None)
    if default is None:
        from .providers.laya import CALIBRATION_MARGIN_SCALE
        default = CALIBRATION_MARGIN_SCALE
    return margin_scale(float(default))


def check_baseline_scale(baseline, scale):
    """Whether a pinned baseline may be the denominator of a scale
    comparison.

    A baseline is a report, and a report that does not say which margin
    scale produced it cannot be the denominator of a verdict about
    changing that scale. Reports from before this field existed look
    exactly like reports from the scale in force, so the ambiguity is
    resolved toward the flattering direction: 18.0 beats a baseline made
    at 2.0 by a mile, and the gate would have said yes. Refusing with
    the repair in the message is cheaper than a promoted number nobody
    can reproduce."""
    if not isinstance(baseline, dict):
        return ["baseline is not a report"]
    detail = baseline.get("difficulty_detail") or {}
    pinned = detail.get("margin_scale")
    if pinned is None:
        return [f"the pinned baseline names no margin scale, so it cannot "
                f"be the denominator of a scale comparison: replay the "
                f"suite with {MARGIN_SCALE_ENV}={scale} in the "
                f"environment and pin that report before promoting"]
    try:
        if abs(float(pinned) - float(scale)) <= 1e-9:
            return []
    except (TypeError, ValueError):
        return [f"the pinned baseline names an unreadable margin scale: "
                f"{pinned!r}"]
    return [f"the pinned baseline was replayed at margin scale {pinned}, "
            f"not the {scale} in force - the gate would be comparing a "
            "fitted scale against a number that never ran"]
