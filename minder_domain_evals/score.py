"""Deterministic grader. No model, no judge, no fuzzy similarity: each
field is compared by type, and every verdict carries a reason."""
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import InvalidOperation

from .generate import GENERATOR_VERSION, suite_fingerprint
from .util import D

_NUM_JUNK = re.compile(r"[,\s$]|SGD|USD|S\$", re.I)


def parse_answer(text):
    """First balanced JSON object in a model reply (fences/think tolerated)."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = ch == "\\" and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _num(x):
    if isinstance(x, bool) or x is None:
        return None
    try:
        return D(_NUM_JUNK.sub("", str(x)))
    except (InvalidOperation, ValueError):
        return None


def _bool(x):
    if isinstance(x, bool):
        return x
    if isinstance(x, str) and x.strip().lower() in ("true", "false"):
        return x.strip().lower() == "true"
    return None


def _norm_ws(s):
    return re.sub(r"\s+", " ", s).strip()


def check_field(spec, exp, got, inputs):
    """-> (ok: bool, reason: str)"""
    t = spec["type"]
    if t == "decimal":
        g = _num(got)
        if g is None:
            return False, f"not a number: {got!r}"
        diff = abs(g - D(exp))
        return diff <= D(spec["tol"]), f"|{g} - {exp}| = {diff} (tol {spec['tol']})"
    if t == "int":
        g = _num(got)
        return g is not None and g == D(exp), f"{got!r} vs {exp!r}"
    if t == "bool":
        return _bool(got) is exp, f"{got!r} vs {exp!r}"
    if t == "enum":
        return isinstance(got, str) and got.strip().lower() == str(exp).lower(), \
            f"{got!r} vs {exp!r}"
    if t in ("date", "date_or_null"):
        if exp is None:
            return got in (None, "", "null"), f"{got!r} vs null"
        return isinstance(got, str) and got.strip()[:10] == exp, f"{got!r} vs {exp!r}"
    if t == "set":
        if not isinstance(got, list):
            return False, f"not a list: {got!r}"
        g = {str(v).strip().lower() for v in got}
        e = {str(v).lower() for v in exp}
        return g == e, f"missing {sorted(e - g)} extra {sorted(g - e)}"
    if t == "evidence":
        return _evidence(exp, got, inputs)
    raise ValueError(t)


def _evidence(exp, got, inputs):
    if not isinstance(got, dict):
        return False, f"not an object: {got!r}"
    v, quote = got.get("value"), got.get("quote")
    if exp["value"] is None:
        if v is not None:
            return False, "hallucinated: value for an undisclosed field"
        return True, "correctly null"
    g = _num(v)
    if g is None or g != D(exp["value"]):
        return False, f"value {v!r} vs {exp['value']!r}"
    if not isinstance(quote, str) or not quote.strip():
        return False, "missing quote"
    doc = _norm_ws(inputs["document"])
    if _norm_ws(quote) not in doc:
        return False, "fabricated: quote is not verbatim in the document"
    digits = str(exp["value"])
    if digits not in quote.replace(",", ""):
        return False, "quote does not contain the value"
    return True, "value and verbatim quote"


def grade_case(case, answer):
    """-> dict(id, passed, outcome, fields{name: (ok, reason)})"""
    abstain_expected = case["expected"].get("status") == "insufficient_data"
    if answer is None:
        return {"id": case["id"], "passed": False, "outcome": "parse_failure",
                "fields": {}}
    abstained = str(answer.get("status", "")).lower() == "insufficient_data"
    if abstain_expected:
        return {"id": case["id"], "passed": abstained,
                "outcome": "correct_abstain" if abstained else "unsupported_answer",
                "missing_named": answer.get("missing"),
                "fields": {}}
    if abstained:
        return {"id": case["id"], "passed": False, "outcome": "false_abstain",
                "fields": {}}
    fields = {}
    for name, spec in case["grading"].items():
        fields[name] = check_field(spec, case["expected"][name], answer.get(name),
                                   case["inputs"])
    ok = all(v[0] for v in fields.values())
    return {"id": case["id"], "passed": ok,
            "outcome": "correct" if ok else "wrong", "fields": fields}


def score(cases, answers_by_id, kind="candidate", label=None):
    results = [grade_case(c, answers_by_id.get(c["id"])) for c in cases]
    by = {c["id"]: c for c in cases}
    fam = defaultdict(lambda: [0, 0])
    outcomes = defaultdict(int)
    field_fail = defaultdict(int)
    fabricated = hallucinated = 0
    groups = defaultdict(list)
    for r in results:
        c = by[r["id"]]
        key = f"{c['family']}/{c['kind']}"
        fam[key][0] += r["passed"]
        fam[key][1] += 1
        outcomes[r["outcome"]] += 1
        groups[c["group"]].append((c["relation"], r["passed"]))
        for fname, (ok, why) in r["fields"].items():
            if not ok:
                field_fail[f"{key}.{fname}"] += 1
                fabricated += why.startswith("fabricated")
                hallucinated += why.startswith("hallucinated")
    meta = [g for g in groups.values() if len(g) > 1]
    brittle = [g for g in meta if g[0][1] and not all(p for _, p in g)]
    consistent = [g for g in meta if len({p for _, p in g}) == 1]
    n = len(results)
    passed = sum(r["passed"] for r in results)
    return {
        # minder benchmark report v1 envelope (minder_op.benchmark.validate_report)
        "report_version": 1, "suite_id": "domain-core-v1", "kind": kind,
        "label": label, "generator_version": GENERATOR_VERSION,
        "suite_fingerprint": suite_fingerprint(cases),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "comparable_runs": n,
            "verified_completion_rate": round(passed / n, 4) if n else 0.0,
            "unsafe_executions": 0, "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0,
        },
        "domain_metrics": {
            "passed": passed, "cases": n, "outcomes": dict(outcomes),
            "unsupported_answers": outcomes["unsupported_answer"],
            "false_abstains": outcomes["false_abstain"],
            "fabricated_quotes": fabricated, "hallucinated_values": hallucinated,
            "metamorphic_groups": len(meta),
            "metamorphic_consistency": round(len(consistent) / len(meta), 4)
            if meta else None,
            "brittle_groups": len(brittle),
        },
        "by_family": {k: {"passed": p, "cases": t, "rate": round(p / t, 4)}
                      for k, (p, t) in sorted(fam.items())},
        "top_field_failures": dict(sorted(field_fail.items(),
                                          key=lambda kv: -kv[1])[:15]),
        "runs": [{"task_id": r["id"], "status": r["outcome"],
                  "failed_fields": {f: why for f, (ok, why) in r["fields"].items()
                                    if not ok}} for r in results],
    }

