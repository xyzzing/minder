"""The calibration loop: fit on the fitting split, gate on the eval set
(issue #36).

The confidence the difficulty router gates on is clip01(scale x
top-two margin). The scale was hand-picked, once, from percentiles
printed in a provider comment. That is the whole defect: nobody can say
which scale is in force, what it was chosen on, or whether it still
describes the artifact that is installed.

prd-v0.10 6.9.3 fixes the order of operations - fit on a calibration
split, score on a disjoint eval set, and refuse the pair when they
overlap - and 6.9 fixes the promotion thresholds, which are the
comparator's: a selective-accuracy drop or a calibration-error rise
beyond 5 points fails, below MIN_COMPARABLE_RUNS is never a pass, a
fingerprint mismatch is never comparable, a safety regression fails at
any sample size.

The difficulty numbers live in difficulty_detail / calibration_detail,
which the shipped protected-metric comparator never reads, so the
verdict has its own pass over those keys on top of the shared one.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from minder_core.calibration import (DEFAULT_MARGIN_SCALES,
                                     fit_margin_scale)
from minder_core.comparator import (MAX_COMPLETION_DROP, VERDICT_FAIL,
                                    VERDICT_PASS, compare_reports)

from .difficulty_eval import SUITE_ID, load_cases, suite_fingerprint
from .providers.laya import MARGIN_SCALE_ENV

# The confidence floor the scale is fitted against. Same value the
# router reads (minder.py DEFAULTS), restated here because the fit is
# meaningless without naming the floor it was chosen for.
DEFAULT_CONFIDENCE_FLOOR = 0.7

# prd-v0.10 6.9: the same 5-point bound the protected metrics use,
# applied to the difficulty pair.
MAX_SELECTIVE_ACCURACY_DROP = MAX_COMPLETION_DROP
MAX_CALIBRATION_ERROR_RISE = MAX_COMPLETION_DROP

CONFIDENCE_SOURCE = "distribution-margin"
PROMOTION_NAME = "promotion.json"
# under benchmarks_root(), which the operator may relocate; naming the
# dir twice (MINDER_BENCHMARKS_DIR ending in benchmarks/) is how a
# pinned record ends up somewhere nobody looks
PROMOTIONS_DIR = "promotions"

_INPUT_TOKEN = re.compile(r"[a-z0-9]+")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()
                          ).hexdigest()


def _norm_input(text):
    return " ".join(_INPUT_TOKEN.findall(str(text or "").lower()))


def load_fitting_rows(path):
    """The fitting corpus as rows: id, the rendered input, the gold
    label. No margins here - a margin is what a provider says about a
    row, so margins arrive with the replay (issue #36: the fitter is
    fed by whatever produced the distribution)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    examples = doc.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ValueError(f"{path}: examples must be a non-empty list")
    rows = []
    for row in examples:
        if not isinstance(row, dict) or not row.get("id"):
            raise ValueError(f"{path}: fitting row missing id: {row!r}")
        if not row.get("input") or not row.get("label"):
            raise ValueError(f"{path}: fitting row {row['id']} needs an "
                             "input and a label")
        rows.append({"id": row["id"], "input": row["input"],
                     "label": row["label"], "split": row.get("split", ""),
                     "input_norm": _norm_input(row["input"])})
    return rows


def check_split_disjoint(fitting_rows, eval_ids):
    """6.9.3 as a check, not a comment. Two kinds of overlap count: a
    shared case id, and a fitting input whose text is the eval case's
    task (whitespace- and case-normalized). Either one means the
    fitted number was chosen on data it will be scored against."""
    eval_id_set = {str(cid) for cid in eval_ids or ()}
    shared_ids = sorted({str(row.get("id")) for row in fitting_rows
                         if str(row.get("id")) in eval_id_set})
    shared_inputs = sorted({str(row.get("id")) for row in fitting_rows
                            if row.get("input_norm")
                            and _norm_input(row.get("input")) in eval_id_set})
    reasons = []
    if shared_ids:
        reasons.append(f"fitting rows share case ids with the eval set: "
                       f"{', '.join(shared_ids[:8])}")
    if shared_inputs:
        reasons.append(f"fitting rows reuse eval case inputs: "
                       f"{', '.join(shared_inputs[:8])}")
    return {"ok": not reasons, "reasons": reasons,
            "fitting": len(fitting_rows), "eval": len(eval_id_set)}


def _eval_inputs(cases_path):
    """The eval set's identity as the fitter can see it: case ids and
    the normalized task text of every case."""
    _doc, cases, _digest = load_cases(cases_path)
    ids = set()
    for case in cases:
        ids.add(str(case["id"]))
        state = case.get("state") or {}
        text = _norm_input(state.get("task"))
        if text:
            ids.add(text)
    return ids


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


def bundle_revision(bundle_path):
    """The installed artifact's identity: base revision plus the
    strategy and its parameters. A re-export changes it, which is
    exactly when a pinned scale stops describing the installed model."""
    path = Path(bundle_path) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    core = {"base_revision": manifest.get("base_revision"),
            "strategy": manifest.get("strategy"),
            "config": manifest.get("specialization_config"),
            "format_version": manifest.get("format_version")}
    return _digest(core)


def promotion_record(*, scale, bundle_path, cases_path,
                     fit=None, gate=None, now=None,
                     confidence_source=CONFIDENCE_SOURCE, baseline=None):
    """What a promotion actually pinned. Every key here is a thing that
    can move under the pin, and `record_drift` names the ones that did."""
    now = now or datetime.now(timezone.utc)
    pinned = default_baseline_path_for()
    # Store where the report lives inside the benchmarks root, not where
    # it happened to be on disk for this run. `MINDER_BENCHMARKS_DIR` is
    # per-invocation, so an absolute path makes the drift check report a
    # moved baseline every time the root moves - and the operator re-pins
    # a denominator that was never lost.
    pinned = _baseline_location(pinned)
    return {
        "record_version": 1,
        "suite_id": SUITE_ID,
        "suite_fingerprint": suite_fingerprint(cases_path),
        "label_digest": load_cases(cases_path)[2],
        "bundle_path": str(bundle_path),
        "bundle_revision": bundle_revision(bundle_path),
        "margin_scale": scale,
        "confidence_source": confidence_source,
        "confidence_floor": (fit or {}).get("floor", DEFAULT_CONFIDENCE_FLOOR),
        "fit": fit,
        "gate": None if gate is None else {
            "verdict": gate["verdict"], "reasons": gate["reasons"]},
        # the report the verdict was computed against. The gate refuses
        # to run without a pinned baseline, so a record that did not
        # name it would claim a comparison nothing can re-check once
        # the operator re-pins a baseline over that file
        "baseline_path": pinned,
        "baseline_fingerprint": (baseline or {}).get("suite_fingerprint"),
        # the scale the baseline was replayed at. The gate refuses a
        # baseline whose scale is not the provider's, so pinning it here
        # is what lets a later check tell "the baseline was replaced"
        # from "the baseline was replaced with one at another scale"
        "baseline_scale": (baseline or {}).get(
            "difficulty_detail", {}).get("margin_scale"),
        # where the pinned digests came from, so a later check can
        # recompute them without guessing at the suite layout
        "cases_path": str(cases_path),
        "promoted_at": now.isoformat(),
    }


def promotion_path(suite_id=SUITE_ID):
    """Where a pinned promotion lives, next to the pinned baselines."""
    from minder_op.benchmark import benchmarks_root
    return benchmarks_root() / PROMOTIONS_DIR / f"{suite_id}.json"


def read_promotion(suite_id=SUITE_ID):
    path = promotion_path(suite_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_promotion(record, path=None):
    """Atomic write; the previous record is kept as `.bak` so a bad
    promotion is one move away from the last good one (I2)."""
    path = path or promotion_path(record["suite_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.parent.joinpath(path.name + ".bak").write_text(
            path.read_text(encoding="utf-8"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
    tmp.replace(path)
    return path


def record_drift(record, *, cases_path, bundle_path, baseline_path=None):
    """Which pinned facts no longer hold. A relabel moves the label
    digest and with it the suite fingerprint; a re-export moves the
    bundle revision; re-pinning a baseline replaces the denominator the
    verdict was computed against. A promotion whose facts moved
    describes a model, a suite or a comparison that no longer exists, so
    the pin stops meaning anything."""
    drift = []
    try:
        digest = load_cases(cases_path)[2]
    except (OSError, ValueError):
        digest = None
    if digest is not None and record.get("label_digest") != digest:
        drift.append("label_digest")
    try:
        fingerprint = suite_fingerprint(cases_path)
    except (OSError, ValueError):
        fingerprint = None
    if fingerprint is not None and record.get("suite_fingerprint") != \
            fingerprint:
        drift.append("suite_fingerprint")
    try:
        revision = bundle_revision(bundle_path)
    except (OSError, ValueError):
        revision = None
    if revision is not None and record.get("bundle_revision") != revision:
        drift.append("bundle_revision")
    # The baseline is a file, not a digest the record keeps a copy of:
    # `benchmark baseline create --yes` overwrites it, and a record
    # gated against the old report then claims a comparison nobody can
    # rerun. An absent or unreadable file counts the same as a moved one.
    pinned = baseline_path or record.get("baseline_path")
    report = _pinned_baseline_report(pinned)
    if record.get("baseline_fingerprint") != (report or {}).get(
            "suite_fingerprint"):
        drift.append("baseline_fingerprint")
    # The fingerprint says the denominator is the same suite; only the
    # scale says whether it is the same comparison. A baseline re-pinned
    # at another margin scale keeps the fingerprint and voids the verdict.
    if report is not None and record.get("baseline_scale") != \
            (report.get("difficulty_detail") or {}).get("margin_scale"):
        drift.append("baseline_scale")
    return drift


def _baseline_location(path):
    """The baseline's location relative to the benchmarks root, or the
    path itself when it is not under that root."""
    from minder_op.benchmark import benchmarks_root
    try:
        return str(Path(path).relative_to(benchmarks_root()))
    except (ValueError, OSError):
        return str(path)


def _resolve_baseline_location(location):
    """A recorded baseline location as a readable path. An absolute path
    from an older record still resolves; a relative one resolves against
    the root in force, which is what makes it survive a moved root."""
    if not location:
        return None
    path = Path(location)
    if path.is_absolute():
        return path
    from minder_op.benchmark import benchmarks_root
    candidate = benchmarks_root() / path
    if candidate.is_file():
        return candidate
    if path.is_file():
        return path
    return candidate


def _pinned_baseline_report(path):
    """The pinned report, or None when it is absent or invalid. A drift
    check runs inside doctor, so it degrades to a finding rather than a
    traceback."""
    if not path:
        return None
    from minder_op.benchmark import BenchmarkError, read_report
    try:
        return read_report(_resolve_baseline_location(path))
    except (BenchmarkError, OSError, ValueError):
        return None


def score_fitting_rows(rows, provider, contract, cfg=None):
    """Run the fitting corpus through a provider and score each row.

    The scored row is what `fit_margin_scale` consumes: the margin the
    provider actually produced and whether its argmax was the gold
    label. Fitting on the margin rather than the confidence keeps the
    fitter independent of the scale it is choosing."""
    from .types import top_two_margin
    if provider is None:
        # replay accepts None as "the trivial baseline"; fitting does
        # not, because a margin is what a provider says about a row and
        # None says nothing about any of them
        raise ValueError("score_fitting_rows needs a provider to score the "
                         "fitting split with")
    scored = []
    for row in rows:
        state = {"task": row["input"], "turn": 1}
        response = provider.system_one(state, contract.questions,
                                       contract=contract)
        if response is None:
            continue
        probs = (response.choice_probs or {}).get("difficulty") or {}
        scored.append({"id": row["id"],
                       "margin": top_two_margin(probs),
                       "correct": _argmax(probs) == row["label"],
                       "probs": probs, "gold": row["label"]})
    return scored


def _argmax(probs):
    if not probs:
        return None
    return max(sorted(probs), key=lambda key: probs[key])


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
    import os

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
