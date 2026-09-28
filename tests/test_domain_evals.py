"""domain-core-v1: deterministic non-coding oracles. No network, no model."""
import json
import random

import pytest

from minder_domain_evals import rulebook
from minder_domain_evals.__main__ import main, mutate, reference_answers
from minder_domain_evals.families import finance, governance, trade
from minder_domain_evals.generate import (HoldoutLocked, generate,
                                          render_prompt, suite_fingerprint)
from minder_domain_evals.score import grade_case, parse_answer, score


@pytest.fixture(scope="module")
def cases():
    return generate(seed=11, n_per_family=40)


def test_generation_is_deterministic():
    a, b = generate(5, 10), generate(5, 10)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert suite_fingerprint(a) == suite_fingerprint(b)
    assert suite_fingerprint(a) != suite_fingerprint(generate(6, 10))


def test_holdout_split_is_locked():
    with pytest.raises(HoldoutLocked):
        generate(1000, 1)
    assert generate(1000, 1, unlock_holdout=True)


def test_oracle_passes_itself_and_every_mutant_fails(cases):
    ref = reference_answers(cases)
    rep = score(cases, ref)
    assert rep["domain_metrics"]["passed"] == len(cases)
    rng = random.Random(0)
    for c in cases:
        assert not grade_case(c, mutate(c, ref[c["id"]], rng))["passed"], c["id"]


def test_report_fits_minder_benchmark_envelope(cases):
    from minder_op.benchmark import validate_report
    rep = score(cases, reference_answers(cases))
    assert validate_report(rep) == []


def test_abstention_outcomes(cases):
    probe = next(c for c in cases if c["relation"] == "abstain_probe")
    guessed = {k: "1" for k in probe["answer_types"]}
    assert grade_case(probe, guessed)["outcome"] == "unsupported_answer"
    base = next(c for c in cases if c["relation"] == "base"
                and "status" not in c["expected"])
    assert grade_case(base, {"status": "insufficient_data"})["outcome"] == \
        "false_abstain"


def test_prompts_hide_oracle_truth(cases):
    for c in cases:
        p = render_prompt(c)
        assert "_truth" not in p and "_sentences" not in p
        assert "expected" not in json.loads(json.dumps(c["inputs"]))


def test_parse_answer_tolerates_fences_and_think():
    raw = '<think>{"x": 1}</think>\n```json\n{"a": "b {c}"}\n```'
    assert parse_answer(raw) == {"a": "b {c}"}
    assert parse_answer("no json") is None


# --- hand-worked anchors (independent of the generators) ------------------

def test_gst_rate_boundaries():
    assert finance.gst_rate("2022-12-31") == 7
    assert finance.gst_rate("2023-01-01") == 8
    assert finance.gst_rate("2024-01-01") == 9


def test_gst_inclusive_extraction():
    x = {"kind": "gst_invoice", "invoice_date": "2024-03-01", "lines": [
        {"amount": "109.00", "price_basis": "inclusive", "supply": "standard"},
        {"amount": "100.00", "price_basis": "exclusive", "supply": "exempt"}]}
    assert finance.solve(x) == {"rate_pct": 9, "net_total_sgd": "200.00",
                                "gst_total_sgd": "9.00", "gross_total_sgd": "209.00"}


def test_ucp600_art29_rolls_closed_last_day():
    # ship Mon 2026-03-02 + 21 days = Mon 03-23, a listed holiday -> Tue 03-24
    x = {"kind": "ucp600_lc", "credit_amount_usd": "100.00",
         "amount_wording": "exact", "quantity": 100, "quantity_unit": "MT",
         "quantity_wording": "exact", "unit_price_usd": "1.00",
         "partial_shipments": "prohibited", "latest_shipment_date": "2026-03-05",
         "expiry_date": "2026-04-30", "presentation_period_days": None,
         "on_board_date": "2026-03-02", "presentation_date": "2026-03-24",
         "quantity_shipped": 104, "drawing_amount_usd": "100.00",
         "bank_holidays": ["2026-03-23"]}
    out = trade.solve(x)
    assert out["latest_presentation_date"] == "2026-03-24"
    assert out["discrepancies"] == []          # 104 MT inside the 5% band
    assert out["examination_deadline"] == "2026-03-31"
    x["quantity_unit"] = "pieces"                # counted units: no tolerance
    assert trade.solve(x)["discrepancies"] == ["quantity_outside_tolerance"]


def test_incoterms_unloading_risk_splits_dap_dpu():
    ev = "during_unloading_at_named_destination_terminal"
    assert trade.solve({"kind": "incoterms2020", "term": "DAP",
                        "loss_event": ev})["risk_bearer"] == "buyer"
    assert trade.solve({"kind": "incoterms2020", "term": "DPU",
                        "loss_event": ev})["risk_bearer"] == "seller"
    cif = trade.solve({"kind": "incoterms2020", "term": "CIF",
                       "loss_event": "at_sea_during_main_carriage"})
    assert cif["risk_bearer"] == "buyer" and cif["seller_min_insurance"] == "ICC_C"


def test_pdpa_scale_limb_and_deadline():
    x = {"kind": "pdpa_breach", "affected_individuals": 500,
         "involves_prescribed_personal_data": False,
         "assessed_notifiable_on": "2026-02-27",
         "remedial_action_renders_harm_unlikely": False,
         "prior_technological_measure_renders_harm_unlikely": False}
    assert governance.solve(x) == {"notifiable": True,
                                   "notify_pdpc_by": "2026-03-02",
                                   "notify_individuals": False}
    x["affected_individuals"] = 499
    assert governance.solve(x)["notifiable"] is False


def test_rulebook_every_legal_rule_has_provenance():
    for rid, r in rulebook.RULES.items():
        if r["verification"] != "convention":
            assert r["source"] and r["url"] and r["verified_on"], rid


def test_cli_selfcheck_passes(capsys):
    assert main(["selfcheck", "--seed", "3", "--n", "15"]) == 0
    assert '"verdict": "PASS"' in capsys.readouterr().out
