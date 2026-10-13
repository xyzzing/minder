"""Calibration and selective-prediction metrics (prd-v0.10 T30 slice).

The numbers a "the model is calibrated" claim owes the operator:
Brier score over full distributions, binned expected calibration error
over (confidence, hit) pairs, and the selective summary (coverage and
selective accuracy at a confidence floor) that says where a confidence
threshold actually operates. Pure functions over plain values; no
model, no I/O. The T30 remainder (PSI, wire provider) stays open.

`fit_margin_scale` is the fitting half of prd-v0.10 6.9.3: the scale
that turns a top-two margin into the confidence the router gates on is
chosen on a fitting split, never on the set that scores it. Rows arrive
already scored for correctness; which split they came from is the
caller's contract, and `minder_decision.calibrate` is what refuses an
overlapping pair.
"""

# The candidate grid, log-spaced. The shipped scales (2.0 for the base
# head, 20.0 for the specialization bundle) sit inside it, and the top
# of the grid is where a flat prototype softmax needs its floor to sit.
DEFAULT_MARGIN_SCALES = (1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 12.5, 15.0,
                         18.0, 20.0, 25.0, 30.0, 40.0, 50.0)


def fit_margin_scale(rows, floor=0.7, scales=None):
    """Choose the margin scale that makes the floor act on the most
    correct cases without giving up accuracy.

    Rows are dicts with `margin` and `correct`; `id` is optional and
    only travels to the caller. Confidence is clip01(scale x margin),
    the mapping the providers apply, so a fitted number means exactly
    what the live floor reads.

    Ranking: selective accuracy first, then coverage (the live failure
    is 831 abstentions and zero applied bands, not accuracy), then the
    lower ECE, then the smaller scale. A scale whose floor acts on
    nothing is refused rather than chosen: `selective_accuracy` is None
    there, and a router that never acts is not a calibrated one.
    """
    if floor is None:
        raise ValueError("fit_margin_scale needs a floor to fit against")
    grid = DEFAULT_MARGIN_SCALES if scales is None else tuple(scales)
    if not grid:
        raise ValueError("fit_margin_scale needs a non-empty scale grid")
    scored = []
    for row in rows or ():
        if not isinstance(row, dict) or "margin" not in row \
                or "correct" not in row:
            raise ValueError(
                f"every row needs a margin and a correct flag: {row!r}")
        scored.append((float(row["margin"]), bool(row["correct"])))
    if not scored:
        raise ValueError("fit_margin_scale needs at least one row")

    best = None
    for scale in grid:
        confidences = [min(1.0, max(0.0, margin * float(scale)))
                       for margin, _ in scored]
        hits = [correct for _, correct in scored]
        summary = selective_summary(
            [(c, h) for c, h in zip(confidences, hits)], floor)
        if summary["selective_accuracy"] is None:
            continue
        ece = expected_calibration_error(confidences, hits)
        key = (summary["selective_accuracy"], summary["coverage"],
               -(ece or 0.0), -float(scale))
        if best is None or key > best[0]:
            best = (key, scale, summary, ece)
    if best is None:
        return {"scale": None, "reason": "no_scale_acts",
                "scales_tried": len(grid), "acted": 0, "coverage": 0.0,
                "selective_accuracy": None, "ece": None, "floor": floor,
                "rows": len(scored)}
    _key, scale, summary, ece = best
    return {"scale": scale, "reason": "ok", "scales_tried": len(grid),
            "acted": summary["acted"], "coverage": summary["coverage"],
            "selective_accuracy": summary["selective_accuracy"],
            "ece": ece, "floor": floor, "rows": len(scored)}


def brier_score(dists, golds, labels):
    """Multi-class Brier: mean over rows of sum_i (p_i - y_i)^2 over the
    closed label set. None when there is nothing to score."""
    if not dists or len(dists) != len(golds):
        return None
    total = 0.0
    for dist, gold in zip(dists, golds):
        for label in labels:
            p = min(1.0, max(0.0, float((dist or {}).get(label, 0.0))))
            y = 1.0 if label == gold else 0.0
            total += (p - y) ** 2
    return round(total / len(dists), 6)


def expected_calibration_error(confidences, hits, bins=10):
    """Binned ECE over (confidence, hit) pairs: sum over bins of the
    bin's share times |bin accuracy - bin mean confidence|. None when
    there is nothing to score."""
    if not confidences or len(confidences) != len(hits):
        return None
    n = len(confidences)
    ece = 0.0
    for low in [i / bins for i in range(bins)]:
        high = low + 1.0 / bins
        picked = [(float(c), bool(h))
                  for c, h in zip(confidences, hits)
                  if low <= float(c) < high
                  or (high == 1.0 and float(c) <= 1.0 and float(c) >= low)]
        if not picked:
            continue
        share = len(picked) / n
        accuracy = sum(1.0 for _, h in picked if h) / len(picked)
        mean_conf = sum(c for c, _ in picked) / len(picked)
        ece += share * abs(accuracy - mean_conf)
    return round(ece, 6)


def selective_summary(records, floor):
    """Where a confidence floor operates: acted (records at or above
    the floor), coverage, and selective accuracy among acted. None
    accuracy when the floor acts on nothing (no actor is not a zero -
    the two states render differently everywhere downstream)."""
    if floor is None:
        acted = list(records)
    else:
        acted = [(float(c), bool(h)) for c, h in records
                 if float(c) >= float(floor)]
    total = len(records)
    if total == 0:
        return {"acted": 0, "coverage": 0.0, "selective_accuracy": None}
    coverage = len(acted) / total
    if not acted:
        return {"acted": 0, "coverage": 0.0, "selective_accuracy": None}
    hits = sum(1.0 for _, h in acted if h)
    return {"acted": len(acted), "coverage": round(coverage, 4),
            "selective_accuracy": round(hits / len(acted), 4)}


__all__ = ["brier_score", "expected_calibration_error",
           "selective_summary", "fit_margin_scale",
           "DEFAULT_MARGIN_SCALES"]
