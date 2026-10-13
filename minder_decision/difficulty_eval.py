"""Offline replay for difficulty-core-v1 (issue #34).

Mirrors routing.replay_cases: gold-labelled cases through a provider
and resolve_difficulty, emitting an 8C-schema report the shipped
benchmark comparator accepts. The difficulty contract has no hard
safety mapping - a wrong band misallocates thinking budget, it does
not widen egress - so the protected metrics stay zero by construction
and the interesting numbers live in difficulty_detail: accuracy,
acted/abstained under the confidence floor, selective accuracy, and
the calibration detail (Brier, binned ECE) that makes a before/after
claim checkable. Reports are candidate-kind; pin/compare via
minder-op benchmark.
"""
import hashlib
import json
from datetime import datetime, timezone

from .contracts import TASK_DIFFICULTY_ID, task_difficulty_contract
from .difficulty import LABELS, confidence_floor, resolve_difficulty
from .providers.laya import margin_scale

SUITE_ID = "difficulty-core-v1"


def load_cases(cases_path):
    """(doc, cases, digest). Raises ValueError on any malformed case:
    the suite is owner data, and a bad case must stop the replay, not
    silently skip."""
    with open(cases_path, encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict) or doc.get("contract") != \
            "task-difficulty/v1":
        raise ValueError(f"{cases_path}: contract must be "
                         "task-difficulty/v1")
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{cases_path}: cases must be a non-empty list")
    seen = set()
    for case in cases:
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"case missing id: {case!r}")
        if cid in seen:
            raise ValueError(f"{cid}: duplicate case id")
        seen.add(cid)
        gold = case.get("gold") or {}
        if gold.get("label") not in LABELS:
            raise ValueError(f"{cid}: gold.label outside the closed set")
        if int(gold.get("score", -1)) != LABELS.index(gold["label"]):
            raise ValueError(f"{cid}: gold.score does not bucket to the "
                             "label")
        state = case.get("state") or {}
        if set(state) != {"task", "turn"} or not state.get("task"):
            raise ValueError(f"{cid}: state must be {{task, turn}} with a "
                             "non-empty task")
    digest_src = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(digest_src.encode()).hexdigest()
    return doc, cases, digest


def suite_fingerprint(cases_path):
    """Comparability fingerprint = manifest functional core + case-set
    digest. Any gold-label change breaks comparability on purpose."""
    doc, _cases, cases_digest = load_cases(cases_path)
    try:
        from minder_op.benchmark import manifest_fingerprint, read_manifest
        manifest_fp = manifest_fingerprint(read_manifest(SUITE_ID))
    except Exception:  # noqa: BLE001 - suite absent: cases-only fallback
        manifest_fp = ""
    core = {"manifest_fp": manifest_fp, "cases_digest": cases_digest,
            "contract": {"id": TASK_DIFFICULTY_ID, "version": "v1"}}
    return hashlib.sha256(json.dumps(core, sort_keys=True,
                                     separators=(",", ":"))
                          .encode()).hexdigest()


def replay_cases(cases_path, *, provider=None, cfg=None, db_path=None,
                 now=None):
    """Evaluate every case through the provider and resolve_difficulty.
    provider None means the trivial baseline: uniform, never above the
    confidence floor, never acts."""
    from minder_core.calibration import (brier_score,
                                         expected_calibration_error)

    doc, cases, _ = load_cases(cases_path)
    suite_fp = suite_fingerprint(cases_path)
    contract = task_difficulty_contract()
    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    provider_name = (provider.model_version if provider is not None
                     and getattr(provider, "model_version", None)
                     else "null")
    correct = 0
    abstentions = 0
    band_distance = 0
    # The confidence the floor gates on is clip01(scale x margin), so a
    # report that does not name its scale is a number without units: two
    # replays of the same cases at different scales look
    # interchangeable, and that comparison is exactly the calibration
    # gate's denominator. Read it from the environment the provider
    # would have seen - the gate replays the candidate under the fitted
    # scale - rather than assuming the shipped constant.
    scale_in_force = margin_scale()
    records = []  # (confidence, hit) for the calibration detail
    dists = []    # (choice distribution, gold label) for Brier
    runs = []
    for case in cases:
        gold = case["gold"]
        state = case["state"]
        confidence = 0.0
        probs = None
        observed = None
        if provider is not None:
            response = provider.system_one(state, contract.questions,
                                           contract=contract)
            if response is not None:
                probs = (response.choice_probs or {}).get("difficulty")
                confidence = float(response.confidence or 0.0)
                resolved = resolve_difficulty(response, contract, cfg or {})
                observed = resolved[0] if resolved else None
        hit = observed == gold["label"]
        if observed is None:
            abstentions += 1
        else:
            if hit:
                correct += 1
            band_distance += abs(LABELS.index(observed)
                                 - LABELS.index(gold["label"]))
        records.append((confidence, hit))
        if probs is not None:
            dists.append((probs, gold["label"]))
        runs.append({"task_id": case["id"],
                     "status": "verified" if hit else "failed",
                     "output_tail": (f"gold={gold['label']} "
                                     f"observed={observed} "
                                     f"confidence={confidence:.3f}")[:200]})
    total = len(cases)
    acted = total - abstentions
    # the floor lives in resolve_difficulty, not here; the selective
    # summary reads the acted flags off the replay itself
    sel = {
        "acted": acted,
        "coverage": round(acted / total, 4) if total else 0.0,
        "selective_accuracy": round(correct / acted, 4) if acted else None,
    }
    return {
        "report_version": 1,
        "suite_id": SUITE_ID,
        "suite_fingerprint": suite_fp,
        "generated_at": generated_at,
        "kind": "candidate",
        "runs": runs,
        "metrics": {
            "comparable_runs": total,
            "verified_completion_rate": round(correct / total, 4)
            if total else 0.0,
            # the difficulty band has no egress/frontier surface; the
            # protected vocabulary stays zero by construction
            "unsafe_executions": 0,
            "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0,
        },
        "difficulty_detail": {
            "contract_id": TASK_DIFFICULTY_ID,
            "provider": provider_name,
            "margin_scale": scale_in_force,
            "confidence_floor": confidence_floor(cfg or {}),
            "correct": correct,
            "abstentions": abstentions,
            "label_status": doc.get("label_status", ""),
            "mean_band_distance": round(band_distance / acted, 4)
            if acted else 0.0,
            **sel,
        },
        "calibration_detail": {
            "brier": brier_score(
                [d for d, _ in dists], [g for _, g in dists], list(LABELS)),
            "ece": expected_calibration_error(
                [c for c, _ in records], [h for _, h in records]),
            "ece_bins": 10,
        },
        "benchmark_runner": {"mode": f"offline replay ({SUITE_ID})"},
    }
