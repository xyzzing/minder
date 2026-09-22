"""FakeClient (Phase 5.5 slice C): deterministic canned responses for
replay fixtures and tests. No network, no model, no keys.

`fixtures` maps a state key (by default the state's failure_key) to a
spec dict:
    {"failure_kind": "environment", "needs_new_evidence": 0.2,
     "next_step": "environment_check", "confidence": 0.9,
     "best_skill": "diagnose-environment", "any_skill_applies": 0.9}
For each ChoiceQuestion the canned option gets mass 1.0 (or spread with
`<id>_probs`); for each NoulQuestion the canned float is P(yes). An
unmapped key falls back to uniform + confidence 0 — the Fake never
invents confidence it was not given.
"""
import json

from ..types import DecisionResponse, top_two_margin
from .null import NullClient

VERSION = "fake-v1"


def _state_key(state):
    if isinstance(state, dict):
        return state.get("failure_key")
    try:
        return json.loads(state).get("failure_key")
    except (ValueError, AttributeError):
        return None


class FakeClient:
    model_version = VERSION

    def __init__(self, fixtures=None, key_fn=None):
        self._fixtures = dict(fixtures or {})
        self._key_fn = key_fn or _state_key

    def system_one(self, state, questions, model=None, contract=None):
        spec = self._fixtures.get(self._key_fn(state)) or {}
        choice_probs = {}
        noul_probs = {}
        margin = 0.0
        primary = None
        for question in questions:
            options = getattr(question, "options", None)
            if options is None:  # Noul
                noul_probs[question.id] = min(
                    1.0, max(0.0, float(spec.get(question.id, 0.5))))
                continue
            if f"{question.id}_probs" in spec:
                probs = {opt: float(spec[f"{question.id}_probs"].get(opt, 0.0))
                         for opt in options}
            else:
                pick = spec.get(question.id, options[-1])
                probs = {opt: (1.0 if opt == pick else 0.0) for opt in options}
                if question.id in ("next_step", "best_skill"):
                    primary = probs
            choice_probs[question.id] = probs
            if question.id in ("next_step", "best_skill"):
                margin = top_two_margin(probs)
        return DecisionResponse(
            contract_id=contract.contract_id if contract else "",
            contract_version=contract.version if contract else "",
            choice_probs=choice_probs, noul_probs=noul_probs,
            confidence=min(1.0, max(0.0, float(spec.get("confidence", 0.0)))),
            top_two_margin=margin, latency_ms=0.0,
            provider="fake", model_version=VERSION)


def uniform_client():
    """The Fake with no fixtures behaves like Null — explicit helper for
    the 'may be wrong' cases."""
    return NullClient()
