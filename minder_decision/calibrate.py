"""The calibration loop: fit on the fitting split, gate on the eval set,
pin what the gate accepted (issue #36).

The confidence the difficulty router gates on is clip01(scale x
top-two margin). The scale was hand-picked, once, from percentiles
printed in a provider comment. That is the whole defect: nobody can say
which scale is in force, what it was chosen on, or whether it still
describes the artifact that is installed.

This module owns only the order of operations and the seams between the
three parts: `calibration_fit` reads and scores the fitting corpus,
`calibration_gate` decides, `calibration_promotion` pins the decision and
says when it stopped being true. The order is not a style choice -
prd-v0.10 6.9.3 makes the split check run before anything is fitted, and
6.9 makes the verdict the comparator's rather than a number chosen in the
same breath as the candidate.
"""
import os

from minder_core.calibration import DEFAULT_MARGIN_SCALES, fit_margin_scale

from .calibration_fit import _eval_inputs, check_split_disjoint
from .calibration_fit import load_fitting_rows, score_fitting_rows
from .calibration_gate import (check_baseline_scale, default_baseline_path_for,
                               gate_difficulty, provider_margin_scale,
                               read_pinned_baseline)
from .calibration_promotion import (DEFAULT_CONFIDENCE_FLOOR, bundle_revision,
                                    promotion_path, promotion_record,
                                    read_promotion, record_drift,
                                    write_promotion)
from .difficulty_eval import SUITE_ID, load_cases, suite_fingerprint
from .providers.laya import MARGIN_SCALE_ENV

__all__ = ["DEFAULT_CONFIDENCE_FLOOR", "SUITE_ID", "bundle_revision",
           "check_baseline_scale", "check_split_disjoint",
           "default_baseline_path_for", "fit_and_gate", "fit_margin_scale",
           "gate_difficulty", "load_fitting_rows", "promotion_path",
           "promotion_record", "provider_margin_scale", "read_promotion",
           "read_pinned_baseline", "record_drift", "score_fitting_rows",
           "suite_fingerprint", "write_promotion"]


def fit_and_gate(*, cases_path, fitting_rows, provider=None, cfg=None,
                 scales=None, floor=DEFAULT_CONFIDENCE_FLOOR,
                 baseline=None, bundle_path=None, now=None):
    """The whole loop in one call: split check, fit, replay, verdict.

    The split check runs before anything is fitted, so a corpus that
    leaks into the eval set raises instead of producing a flattering
    number. `provider` is the thing being calibrated (the specialization
    bundle, or the base head); None is the trivial baseline, which acts
    on nothing and so refuses to fit.
    """
    from .contracts import task_difficulty_contract
    contract = task_difficulty_contract()
    if provider is None:
        # replay accepts None as "the trivial baseline"; fitting does
        # not, because a margin is what a provider says about a row and
        # None says nothing about any of them
        raise ValueError("fit_and_gate needs a provider to score the "
                         "fitting split with")
    # 6.9.3 runs before anything is fitted: a corpus that leaks into the
    # eval set is refused, not scored on data it chose its number from
    disjoint = check_split_disjoint(fitting_rows, _eval_inputs(cases_path))
    if not disjoint["ok"]:
        raise ValueError(
            f"6.9.3: fitting split overlaps the eval set - "
            f"{'; '.join(disjoint['reasons'])}")
    doc, cases, _digest = load_cases(cases_path)
    scored = score_fitting_rows(fitting_rows, provider, contract, cfg)
    fit = fit_margin_scale(scored, floor=floor,
                           scales=scales or DEFAULT_MARGIN_SCALES)
    if cfg is None:
        # the candidate must run at the floor the fit chose, not the
        # runtime default: a scale fitted against one floor and replayed
        # against another answers two different questions
        cfg = {"laya_min_confidence": fit["floor"]}
    if baseline is None:
        # the pinned baseline is the one the comparator's own rules
        # would refuse: a candidate replayed against a baseline chosen
        # in the same breath is a number with no denominator
        baseline = read_pinned_baseline()
        if baseline is None:
            raise ValueError(
                "no pinned baseline for " + SUITE_ID + ": the gate needs "
                "a report pinned by `minder-op benchmark baseline create "
                "--yes` (or a baseline= argument) before a scale can be "
                "promoted")
    baseline_problems = check_baseline_scale(baseline,
                                             provider_margin_scale(provider))
    if baseline_problems:
        raise ValueError(
            "unusable baseline for " + SUITE_ID + ": "
            + "; ".join(baseline_problems))
    candidate = _replay_with_scale(cases_path, provider, cfg, fit, now)
    gate = gate_difficulty(baseline, candidate)
    return {"fit": fit, "gate": gate, "split": disjoint,
            "baseline": baseline, "candidate": candidate,
            "label_status": doc.get("label_status", ""),
            "baseline_scale": provider_margin_scale(provider),
            "suite_fingerprint": candidate["suite_fingerprint"]}


def _replay_with_scale(cases_path, provider, cfg, fit, now):
    """Replay the eval set with the fitted scale in force, so the
    verdict measures the candidate that would actually run."""
    from .difficulty_eval import replay_cases
    scale = fit.get("scale")
    previous = os.environ.get(MARGIN_SCALE_ENV) if scale is not None \
        else None
    if scale is not None:
        os.environ[MARGIN_SCALE_ENV] = str(scale)
    try:
        merged = {**(cfg or {})}
        if fit.get("floor") is not None:
            merged.setdefault("laya_min_confidence", fit["floor"])
        return replay_cases(cases_path, provider=provider, cfg=merged,
                            now=now)
    finally:
        if scale is not None:
            if previous is None:
                os.environ.pop(MARGIN_SCALE_ENV, None)
            else:
                os.environ[MARGIN_SCALE_ENV] = previous
