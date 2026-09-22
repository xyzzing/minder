"""NullClient (Phase 5.5 slice C): the default provider.

Uniform distributions over whatever the contract offers, confidence 0,
model_version "null". It has no opinion, can never pass a confidence
gate, and never raises.
"""
from ..types import DecisionResponse, top_two_margin

VERSION = "null"


class NullClient:
    model_version = VERSION

    def system_one(self, state, questions, model=None, contract=None):
        choice_probs = {}
        margin = 0.0
        noul_probs = {}
        for question in questions:
            options = getattr(question, "options", None)
            if options is None:  # Noul
                noul_probs[question.id] = 0.5
                continue
            uniform = 1.0 / len(options)
            choice_probs[question.id] = {option: uniform
                                         for option in options}
            margin = top_two_margin(choice_probs[question.id])
        return DecisionResponse(
            contract_id=contract.contract_id if contract else "",
            contract_version=contract.version if contract else "",
            choice_probs=choice_probs, noul_probs=noul_probs,
            confidence=0.0, top_two_margin=margin, latency_ms=0.0,
            provider="null", model_version=VERSION)
