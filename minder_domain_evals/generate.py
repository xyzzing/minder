"""Seeded case generation: family makers + reference solvers + abstention
injection + metamorphic twins. Same (seed, n, families, options) always
yields byte-identical cases, so a suite is pinned by its parameters."""
import copy
import hashlib
import json
import random

from . import rulebook
from .families import extraction, finance, governance, sustainability, trade
from .util import Missing

GENERATOR_VERSION = "domain-core/1"
FAMILIES = {"sustainability": sustainability, "finance": finance,
            "trade": trade, "governance": governance,
            "extraction": extraction}
HOLDOUT_SEED_FLOOR = 1000  # seeds >= this are the held-out split
ABSTAIN = {"status": "insufficient_data"}


class HoldoutLocked(Exception):
    pass


def expected_for(mod, inputs):
    try:
        return mod.solve(inputs), None
    except Missing as exc:
        return dict(ABSTAIN), exc.fields


def _case(cid, fam, spec, inputs, group, relation, closed_book):
    mod = FAMILIES[fam]
    expected, missing = expected_for(mod, inputs)
    return {"id": cid, "family": fam, "kind": inputs.get("kind"),
            "group": group, "relation": relation,
            "task": spec["task"], "inputs": inputs,
            "grading": spec["grading"] if missing is None else
            {"status": {"type": "enum"}},
            "answer_types": {k: v["type"] for k, v in spec["grading"].items()},
            "rules": [] if closed_book else spec["rules"],
            "closed_book": closed_book,
            "expected": expected, "expected_missing": missing}


def generate(seed, n_per_family, families=None, abstain_rate=0.15,
             variants=True, closed_book=False, unlock_holdout=False):
    if seed >= HOLDOUT_SEED_FLOOR and not unlock_holdout:
        raise HoldoutLocked(f"seed {seed} is in the holdout split; pass "
                            "unlock_holdout=True (and record why)")
    cases = []
    for fam in families or FAMILIES:
        mod = FAMILIES[fam]
        for i in range(n_per_family):
            rng = random.Random(f"{GENERATOR_VERSION}:{seed}:{fam}:{i}")
            spec = mod.make(rng)
            inputs = spec["inputs"]
            base_id = f"{fam}-{seed}-{i:05d}"
            if spec["droppable"] and rng.random() < abstain_rate:
                inputs = copy.deepcopy(inputs)
                del inputs[rng.choice(spec["droppable"])]
                cases.append(_case(base_id, fam, spec, inputs, base_id,
                                   "abstain_probe", closed_book))
                continue
            base = _case(base_id, fam, spec, inputs, base_id, "base",
                         closed_book)
            cases.append(base)
            if variants and base["expected_missing"] is None:
                for j, (rel, vin) in enumerate(mod.variants(inputs, rng)):
                    cases.append(_case(f"{base_id}-v{j}", fam, spec, vin,
                                       base_id, rel, closed_book))
    return cases


def public_inputs(inputs):
    return {k: v for k, v in inputs.items() if not k.startswith("_")}


def render_prompt(case):
    shape = []
    for k, t in case["answer_types"].items():
        shape.append(f'  "{k}": ' + {
            "evidence": '{"value": <number|null>, "quote": <string|null>}',
            "set": "[<string>, ...]", "bool": "<true|false>",
            "date": '"YYYY-MM-DD"', "date_or_null": '"YYYY-MM-DD" | null',
        }.get(t, "<value>"))
    parts = [case["task"], "", "INPUTS (JSON):",
             json.dumps(public_inputs(case["inputs"]), indent=1)]
    if case["rules"]:
        parts += ["", "RULES YOU MUST APPLY:", rulebook.excerpt(case["rules"])]
    parts += ["", "Answer with ONE JSON object and nothing else, shaped:",
              "{\n" + ",\n".join(shape) + "\n}",
              "If an input or rule needed to perform the task is not provided, "
              "do not guess: answer exactly "
              '{"status": "insufficient_data", "missing": [<what is missing>]}. '
              "(A field the task says may be null is not a missing input.)"]
    return "\n".join(parts)


def suite_fingerprint(cases):
    h = hashlib.sha256(GENERATOR_VERSION.encode())
    for c in cases:
        h.update(json.dumps([c["id"], c["inputs"], c["expected"]],
                            sort_keys=True, default=str).encode())
    return h.hexdigest()
