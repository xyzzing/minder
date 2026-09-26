"""Decision contract tests (Phase 5.5 slice A): serialization, rejection
of empty options, stable criteria hash for the frozen failure_kind
criteria."""
import json

import pytest

from minder_decision import contracts as dcontracts
from minder_decision.types import (ChoiceQuestion, InvalidDistribution,
                            DecisionResponse, NoulQuestion, contract_to_json,
                            make_contract, top_two_margin)


def test_contract_serializes_round_trip():
    contract = dcontracts.failure_triage_contract(
        ("inspect", "retrieve_lesson", "human"))
    blob = json.loads(contract_to_json(contract))
    assert blob["contract_id"] == "failure-triage"
    assert blob["version"] == "v1"
    kinds = [q for q in blob["questions"] if q["id"] == "failure_kind"][0]
    assert kinds["options"] == list(dcontracts.FAILURE_KINDS)
    step = [q for q in blob["questions"] if q["id"] == "next_step"][0]
    assert step["options"] is None  # runtime menu, not frozen
    assert blob["criteria_hash"] == contract.criteria_hash


def test_empty_options_rejected():
    with pytest.raises(ValueError):
        make_contract("failure-triage", "v1",
                      (ChoiceQuestion("failure_kind", ()),))
    with pytest.raises(ValueError):
        make_contract("failure-triage", "v1", ())
    with pytest.raises(ValueError):
        make_contract("", "v1", (NoulQuestion("needs_new_evidence"),))


def test_criteria_hash_stable_for_frozen_criteria():
    a = dcontracts.failure_triage_contract(("inspect", "human"))
    b = dcontracts.failure_triage_contract(("inspect", "human"))
    assert a.criteria_hash == b.criteria_hash  # frozen part identical
    c = dcontracts.failure_triage_contract(("environment_check", "human"))
    assert a.criteria_hash == c.criteria_hash  # runtime menu not in hash
    old = make_contract("failure-triage", "v0",
                        (ChoiceQuestion("failure_kind", ("code_logic",)),
                         NoulQuestion("needs_new_evidence")))
    assert old.criteria_hash != a.criteria_hash  # new version, new hash


def test_response_validate_rejects_invalid_distributions():
    contract = dcontracts.failure_triage_contract(("inspect", "human"))

    def response(**over):
        fields = dict(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            choice_probs={
                "failure_kind": {k: 1 / 6 for k in
                                 dcontracts.FAILURE_KINDS},
                "next_step": {"inspect": 0.9, "human": 0.1},
            },
            noul_probs={"needs_new_evidence": 0.2},
            confidence=0.8,
        )
        fields.update(over)
        return DecisionResponse(**fields)

    response().validate(contract)  # well-formed passes
    with pytest.raises(InvalidDistribution):
        response(choice_probs={"failure_kind": {"code_logic": 0.5}}).validate(
            contract)  # missing options
    with pytest.raises(InvalidDistribution):
        response(noul_probs={"needs_new_evidence": 1.4}).validate(contract)
    with pytest.raises(InvalidDistribution):
        response(choice_probs={
            "failure_kind": {k: 1 / 6 for k in dcontracts.FAILURE_KINDS},
            "next_step": {"inspect": 0.5, "human": 0.2}},
        ).validate(contract)  # does not sum to 1
    with pytest.raises(InvalidDistribution):
        response(contract_version="v9").validate(contract)


def test_top_choice_and_margin():
    contract = dcontracts.failure_triage_contract(
        ("inspect", "think_retry", "human"))
    resp = DecisionResponse(
        contract_id=contract.contract_id, contract_version=contract.version,
        choice_probs={"failure_kind": {k: 1 / 6 for k in
                                       dcontracts.FAILURE_KINDS},
                      "next_step": {"inspect": 0.7, "think_retry": 0.2,
                                    "human": 0.1}},
        noul_probs={"needs_new_evidence": 0.3}, confidence=0.7,
        top_two_margin=0.5)
    assert resp.top_choice("next_step") == "inspect"
    assert resp.top_choice("missing") is None
    assert top_two_margin(resp.choice_probs["next_step"]) == pytest.approx(
        0.5)
