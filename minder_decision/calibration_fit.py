"""The fitting side of the calibration loop (issue #36).

prd-v0.10 6.9.3 fixes the order of operations: fit on a calibration
split, score on a disjoint eval set, and refuse the pair when they
overlap. Everything here belongs to the first half of that sentence -
reading the fitting corpus, checking it does not leak into the eval set,
and scoring it through the provider under calibration. The verdict lives
in `calibration_gate.py`; the loop that joins them is `calibrate.py`.
"""
import json
import re
from pathlib import Path

from .difficulty_eval import load_cases

_INPUT_TOKEN = re.compile(r"[a-z0-9]+")


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
