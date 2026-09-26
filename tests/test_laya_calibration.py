"""Laya adapter confidence calibration + the routing declaration guard.

The regression this pins: calibrated_confidence originally imported
top_two_margin via `from .types import ...` inside providers/laya.py,
which resolves to minder_decision.PROVIDERS.types (nonexistent) — the
bare except swallowed it and every confidence read as 0.0, silently
reverting to all-abstain behavior while tests stayed green.
"""
from minder_decision.providers.laya import calibrated_confidence


def test_margin_maps_to_confidence():
    assert calibrated_confidence(
        {"stay": 0.51, "switch": 0.18, "human": 0.12}) == 0.66
    assert calibrated_confidence({"a": 0.9, "b": 0.05}) == 1.0


def test_tie_and_empty_are_zero_confidence():
    assert calibrated_confidence({"a": 0.5, "b": 0.5}) == 0.0
    assert calibrated_confidence({}) == 0.0
    assert calibrated_confidence(None) == 0.0


def test_single_option_menu_reads_as_full_confidence():
    """A one-option menu (p_top 1.0, no runner-up) has maximal margin."""
    assert calibrated_confidence({"stay": 1.0}) == 1.0


def _fake_provider_response(transition, probs, confidence=0.1):
    """A provider whose primary choice + distribution we control. The
    fixture key is pinned explicitly: FakeClient's default state key
    extracts failure_key, which assess_route's state does not carry."""
    from minder_decision.providers.fake import FakeClient

    key = "fixture-key"

    class _P:
        model_version = "test"

        def system_one(self, state, questions, model=None, contract=None):
            spec = {"candidate_domain": "coding",
                    "transition": transition,
                    "transition_probs": probs,
                    "confidence": confidence}
            return FakeClient({key: spec},
                              key_fn=lambda _s: key).system_one(
                key, questions, model=model, contract=contract)

    return _P()


def test_undeclared_state_cannot_route(tmp_path):
    """Structural restriction: without an explicit declaration NOTHING
    routes, no matter how confident the provider is. Measured regression:
    calibrated laya answered injection/out-of-domain cases (2 prohibited-
    egress violations) until this guard existed."""
    from minder_decision import routing as R

    result = R.assess_route(
        declared_domain=None, task_id="t1",
        provider=_fake_provider_response(
            "switch", {"switch": 0.6, "stay": 0.2, "human": 0.2}),
        record=False, db_path=tmp_path / "x.sqlite")
    assert result["abstained"] is True
    assert result["policy_transition"] == "uncertain"
    assert result["validation"] == "restricted"
    assert result["decision"].model_recommendation == "switch"  # shadowed


def test_declared_state_still_routes(tmp_path):
    from minder_decision import routing as R

    result = R.assess_route(
        declared_domain="coding", current_domain="coding",
        task_id="t1", provider=_fake_provider_response(
            "stay", {"stay": 0.9, "switch": 0.05, "uncertain": 0.05},
            confidence=0.9),
        record=False, db_path=tmp_path / "x.sqlite")
    assert result["abstained"] is False
    assert result["policy_transition"] == "stay"


def test_adapter_wiring_gates_on_margin_not_selfreport():
    """End-to-end over LayaSystemOneClient with a stub agent: the
    confidence the gate sees is margin-derived (transition margin 0.5
    -> 1.0), never the model's self-report (0.11) and never 0.0 — the
    two historical failure modes this adapter shipped."""
    import pytest

    from minder_decision.contracts import domain_route_contract
    from minder_decision.providers.laya import LayaSystemOneClient
    from minder_decision.routing import TRANSITION_SET

    class _StubAgent:
        def predict(self, state, questions):
            return {"answers": {
                "candidate_domain": {"probabilities": {
                    "coding": 0.7, "unknown": 0.3}, "confidence": 0.11},
                "intent_kind": {"probabilities": {
                    "research_question": 0.5, "unknown": 0.5},
                    "confidence": 0.11},
                "transition": {"probabilities": {
                    "stay": 0.75, "switch": 0.25}, "confidence": 0.11},
            }, "usage": {}}

    contract = domain_route_contract(TRANSITION_SET + ("human",))
    response = LayaSystemOneClient(_StubAgent()).system_one(
        {"declared_domain": "coding"}, contract.questions,
        contract=contract)
    assert response.provider == "laya"
    assert response.confidence == pytest.approx(1.0)
