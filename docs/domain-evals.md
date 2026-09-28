# domain-core-v1 — deterministic tests for non-coding work

Coding benchmarks get a free oracle: the test suite passes or it does not.
Business work usually does not, which is why most "domain evals" fall
back to an LLM judge. This suite takes the other route. It uses only task
shapes where the regulation, standard or policy fixes a **unique correct
answer**, computes that answer in code, and grades field by field.

```bash
python3 -m minder_domain_evals selfcheck --seed 7 --n 200      # oracle 100%, all mutants fail
python3 -m minder_domain_evals generate  --seed 7 --n 50 --out cases.jsonl --prompts prompts.jsonl
python3 -m minder_domain_evals run       --cases cases.jsonl --out answers.jsonl \
        --base-url http://127.0.0.1:8390/v1 --model qwen-exec    # through minder's proxy
python3 -m minder_domain_evals score     --cases cases.jsonl --answers answers.jsonl --out report.json
python3 -m minder_op benchmark compare BASELINE.json report.json  # same envelope as coding-core
python3 -m minder_domain_evals rules                              # provenance of every parameter
```

Stdlib only, no network except the model endpoint in `run`. `run` is resumable.

## How the answer is made unique

| Technique | What it removes |
|---|---|
| **Code oracle per family.** The expected answer is computed by a reference solver, never written by hand | Label noise; judge-model drift |
| **Parameters in a pinned rulebook.** Rates and thresholds sit in `rulebook.py`, keyed by effective period, each with a source, a check date and a verification grade | Silent staleness when the law changes |
| **Conventions stated in the task.** Rounding step, day-count, closed-day handling | "Both answers are defensible" ambiguity |
| **Open-book by default.** The rule text is in the prompt; `--closed-book` drops it to test recall instead | Mixing up *knows the law* with *applies the law* |
| **Typed field grading.** Decimal with tolerance, date, enum, set, bool, and evidence (value plus a verbatim quote) | Fuzzy string matching |

## What is measured, beyond right or wrong

- **Abstention probes.** About 15% of cases have a required input removed, and some ask for a carbon-tax year with no legislated rate. The only passing answer is `insufficient_data`. Answering anyway counts as `unsupported_answers`, the business equivalent of an unsafe execution.
- **Grounded extraction.** Generated supplier reports contain decoy figures: the prior year, targets, market-based values, restatements and industry averages. A value given for an undisclosed field is a `hallucinated_value`. A quote that is not verbatim in the document is a `fabricated_quote`.
- **Metamorphic twins.** Each base case has variants whose correct answer is identical: reordered lines, a quantity restated in kg instead of t with the factor rescaled, an irrelevant rename. A model that passes the base case but fails a twin is `brittle`. This catches pattern-matching that single cases miss.
- **Grader mutation testing.** `selfcheck` checks that the oracle passes its own answers. It also generates one wrong answer per case and requires the grader to reject every one. A grader that cannot fail is not a test.

## Families (v1)

| Family | Kinds | Rules exercised |
|---|---|---|
| sustainability | carbon_tax | Carbon Pricing Act thresholds (2,000 / 25,000 tCO2e), rate by year, 5% ICC offset cap, Scope 2 location-based excluded from tax |
| finance | gst_invoice, npv, loan, ifrs16, variance | GST 7/8/9% by date, inclusive extraction, IFRS 16 ¶24/26, sign conventions, a two-condition materiality test |
| trade | ucp600_lc, incoterms2020 | UCP 600 Art 14(b)(c), 29(a)(c), 30(a)(b); Incoterms 2020 risk points, carriage, CIF/CIP insurance, clearance |
| governance | doa, three_way_match, sod, pdpa_breach | Delegation-of-authority limits including split-PO aggregation and capex floor, match tolerances, SoD pairs, PDPA s26B/26D and reg 4 |
| extraction | grounded_extraction | Reporting-year discipline, null for undisclosed, verbatim evidence |

## Honest limits

- **Recency of parameters.** Rule values are correct as of `verified_on`. UCP 600, Incoterms 2020 and IFRS 16 are graded `secondary`: the authoritative texts are paywalled, and the values come from practitioner summaries plus working knowledge. `pdpa.breach` carries a caveat on the scope of the individual-notification duty.
- **Synthetic inputs.** Emission factors, holiday calendars and company policies are generated inputs, not real ones. The tests measure rule application under stated inputs. They do not certify a model for regulatory filings.
- **Narrow scope by design.** Judgment-heavy work, such as materiality assessment or drafting a disclosure, is excluded: no oracle can grade it deterministically. Those tasks need a human rubric.
- **Holdout split.** Seeds ≥ 1000 refuse to generate without `--unlock-holdout`. Keep them unseen while tuning prompts or presets.

## Adding a family

Write `make(rng)`, which returns the task, inputs, grading, rules and droppable fields, together with `solve(inputs)` and `variants(inputs, rng)`. Then register the family in `generate.FAMILIES`, add at least one hand-worked anchor test, and run `selfcheck`. Put any new legal parameter in `rulebook.py` with its source and check date, never inline.
