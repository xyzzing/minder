"""python -m minder_domain_evals {generate,reference,run,score,selfcheck,rules}"""
import argparse
import copy
import json
import os
import random
import sys
from pathlib import Path

from . import client, rulebook
from .generate import HoldoutLocked, generate, render_prompt
from .score import grade_case, parse_answer, score


def _read_jsonl(path):
    return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]


def _write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def _gen_args(p):
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--n", type=int, default=20, help="base cases per family")
    # domain-core-v1 is pinned to its five families by manifest; legal has
    # its own suite (legal-core-v1), so it stays opt-in here.
    p.add_argument("--families",
                   default="sustainability,finance,trade,governance,extraction")
    p.add_argument("--abstain-rate", type=float, default=0.15)
    p.add_argument("--no-variants", action="store_true")
    p.add_argument("--closed-book", action="store_true",
                   help="omit rule text: tests recall, not rule application")
    p.add_argument("--unlock-holdout", action="store_true")


def _gen(a):
    return generate(a.seed, a.n, a.families.split(","), a.abstain_rate,
                    not a.no_variants, a.closed_book, a.unlock_holdout)


def reference_answers(cases):
    return {c["id"]: c["expected"] for c in cases}


def mutate(case, answer, rng):
    """One wrong answer per case — the grader must reject every one."""
    a = copy.deepcopy(answer)
    if a.get("status") == "insufficient_data":
        return {k: "0" for k in case["answer_types"]}  # guessed instead
    name = rng.choice(sorted(case["grading"]))
    t, v = case["grading"][name]["type"], a[name]
    if t == "decimal":
        a[name] = str(float(v) + float(case["grading"][name]["tol"]) + 1.0)
    elif t == "int":
        a[name] = int(v) + 1
    elif t == "bool":
        a[name] = not v
    elif t == "enum":
        a[name] = v + "_x"
    elif t in ("date", "date_or_null"):
        a[name] = "1999-01-01" if v else "2026-01-01"
    elif t == "set":
        a[name] = v[1:] if v else ["spurious"]
    elif t == "evidence":
        a[name] = {"value": 1, "quote": "invented"} if v["value"] is None else \
            {"value": v["value"], "quote": v["quote"] + " (paraphrased)"}
    return a


def main(argv=None):
    ap = argparse.ArgumentParser(prog="minder_domain_evals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    _gen_args(g)
    g.add_argument("--out", required=True)
    g.add_argument("--prompts", help="also write rendered prompts (.jsonl)")
    r = sub.add_parser("reference", help="oracle answers (for pipeline tests)")
    r.add_argument("--cases", required=True)
    r.add_argument("--out", required=True)
    ru = sub.add_parser("run", help="ask an OpenAI-compatible endpoint")
    ru.add_argument("--cases", required=True)
    ru.add_argument("--out", required=True)
    ru.add_argument("--base-url", default=os.environ.get(
        "MINDER_EVAL_BASE_URL", "http://127.0.0.1:8390/v1"))
    ru.add_argument("--model", default="qwen-exec")
    ru.add_argument("--limit", type=int)
    ru.add_argument("--max-tokens", type=int, default=2048)
    sc = sub.add_parser("score")
    sc.add_argument("--cases", required=True)
    sc.add_argument("--answers", required=True)
    sc.add_argument("--out")
    sc.add_argument("--label")
    sc.add_argument("--kind", choices=("baseline", "candidate"), default="candidate")
    sc.add_argument("--suite-id",
                    help="override the suite id derived from the case families")
    sf = sub.add_parser("selfcheck", help="oracle passes 100%%, mutants all fail")
    _gen_args(sf)
    sub.add_parser("rules", help="rule provenance table")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "generate":
            cases = _gen(a)
            _write_jsonl(a.out, cases)
            if a.prompts:
                _write_jsonl(a.prompts, [{"id": c["id"], "prompt": render_prompt(c)}
                                         for c in cases])
            print(f"{len(cases)} cases -> {a.out}")
        elif a.cmd == "reference":
            cases = _read_jsonl(a.cases)
            _write_jsonl(a.out, [{"id": k, "answer": v} for k, v in
                                 reference_answers(cases).items()])
        elif a.cmd == "run":
            cases = _read_jsonl(a.cases)[: a.limit]
            done = {x["id"] for x in _read_jsonl(a.out)} if Path(a.out).exists() else set()
            with open(a.out, "a") as fh:
                for i, c in enumerate(cases, 1):
                    if c["id"] in done:
                        continue  # resumable
                    res = client.ask(a.base_url, a.model, render_prompt(c),
                                     max_tokens=a.max_tokens)
                    res.update(id=c["id"], answer=parse_answer(res["raw"]))
                    fh.write(json.dumps(res) + "\n")
                    fh.flush()
                    print(f"[{i}/{len(cases)}] {c['id']} "
                          f"{'ERR ' + res['error'] if res['error'] else 'ok'} "
                          f"{res['latency_s']}s", file=sys.stderr)
        elif a.cmd == "score":
            cases = _read_jsonl(a.cases)
            answers = {x["id"]: x.get("answer") for x in _read_jsonl(a.answers)}
            rep = score(cases, answers, a.kind, a.label,
                        suite_id=getattr(a, "suite_id", None))
            text = json.dumps(rep, indent=1, default=str)
            if a.out:
                Path(a.out).write_text(text)
            summary = {k: rep[k] for k in ("metrics", "domain_metrics", "by_family")}
            print(json.dumps(summary, indent=1))
        elif a.cmd == "selfcheck":
            cases = _gen(a)
            ref = reference_answers(cases)
            rep = score(cases, ref)
            rng = random.Random(a.seed)
            killed = sum(not grade_case(c, mutate(c, ref[c["id"]], rng))["passed"]
                         for c in cases)
            ok = rep["domain_metrics"]["passed"] == len(cases) and killed == len(cases)
            print(json.dumps({"cases": len(cases),
                              "oracle_pass": rep["domain_metrics"]["passed"],
                              "mutants_killed": killed,
                              "abstain_probes": rep["domain_metrics"]["outcomes"]
                              .get("correct_abstain", 0),
                              "metamorphic_groups":
                              rep["domain_metrics"]["metamorphic_groups"],
                              "verdict": "PASS" if ok else "FAIL"}, indent=1))
            return 0 if ok else 1
        elif a.cmd == "rules":
            for row in rulebook.provenance_table():
                print(f"{row['rule']:<28} {row['verification']:<10} "
                      f"{row['verified_on'] or '-':<11} {row['source'] or ''}")
    except HoldoutLocked as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
