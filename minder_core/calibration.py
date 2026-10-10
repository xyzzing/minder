"""Calibration and selective-prediction metrics (prd-v0.10 T30 slice).

The numbers a "the model is calibrated" claim owes the operator:
Brier score over full distributions, binned expected calibration error
over (confidence, hit) pairs, and the selective summary (coverage and
selective accuracy at a confidence floor) that says where a confidence
threshold actually operates. Pure functions over plain values; no
model, no I/O. The T30 remainder (PSI, wire provider) stays open.
"""


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


__all__ = ["brier_score", "expected_calibration_error", "selective_summary"]
