"""Decision client tests (Phase 5.5 slice C): Null uniformity, Fake
canned distributions, invalid distributions rejected in tests."""
import pytest

from decision import contracts as dcontracts
from decision.client import FakeClient, SystemOneClient  # noqa: F401
from decision.client import get_decision_client
from decision.providers.fake import FakeClient as DirectFake
from decision.providers.null import NullClient
from decision.types import DecisionResponse, InvalidDistribution

STATE = '{"failure_key": "bash|keyerror|sid|a.py", "excerpt": "KeyError"}'


def triage_questions(menu=("inspect", "environment_check", "human")):
    return dcontracts.failure_triage_contract(menu).questions


def test_null_client_uniform_confidence_zero():
    null = NullClient()
    resp = null.system_one(STATE, triage_questions())
    assert resp.model_version == "null" and resp.provider == "null"
    assert resp.confidence == 0.0
    probs = resp.choice_probs["next_step"]
    assert len(set(probs.values())) == 1  # uniform
    assert sum(probs.values()) == pytest.approx(1.0)
    assert resp.noul_probs["needs_new_evidence"] == 0.5
    contract = dcontracts.failure_triage_contract(
        ("inspect", "environment_check", "human"))
    resp.validate(contract)  # uniform is a legal distribution
    # never raises on odd input
    null.system_one(None, ())


def test_fake_client_canned_full_distributions():
    fake = DirectFake({
        "bash|keyerror|sid|a.py": {
            "failure_kind": "code_logic", "needs_new_evidence": 0.1,
            "next_step": "inspect", "confidence": 0.9}})
    resp = fake.system_one(STATE, triage_questions())
    assert resp.provider == "fake"
    assert resp.confidence == 0.9
    assert resp.choice_probs["next_step"] == {"inspect": 1.0,
                                              "environment_check": 0.0,
                                              "human": 0.0}
    assert resp.noul_probs["needs_new_evidence"] == 0.1
    contract = dcontracts.failure_triage_contract(
        ("inspect", "environment_check", "human"))
    resp.validate(contract)
    assert resp.top_choice("next_step") == "inspect"


def test_fake_client_unmapped_state_falls_back_to_no_confidence():
    fake = DirectFake({"other|key": {"next_step": "inspect",
                                     "confidence": 0.9}})
    resp = fake.system_one(STATE, triage_questions())
    assert resp.confidence == 0.0  # never invents confidence
    assert resp.choice_probs["next_step"]["inspect"] == 0.0


def test_invalid_distribution_rejected_in_tests():
    contract = dcontracts.failure_triage_contract(("inspect", "human"))
    bad = DecisionResponse(
        contract_id=contract.contract_id, contract_version=contract.version,
        choice_probs={"failure_kind": {"code_logic": 1.5},
                      "next_step": {"inspect": 0.9, "human": 0.9}},
        noul_probs={"needs_new_evidence": 0.2}, confidence=0.5)
    with pytest.raises(InvalidDistribution):
        bad.validate(contract)


def test_env_selection(monkeypatch):
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    assert get_decision_client() is None  # default off
    monkeypatch.setenv("MINDER_DECISION", "shadow")
    assert isinstance(get_decision_client(), NullClient)
    monkeypatch.setenv("MINDER_DECISION", "fake")
    assert isinstance(get_decision_client(), FakeClient)
    monkeypatch.setenv("MINDER_DECISION", "nonsense")
    assert get_decision_client() is None
