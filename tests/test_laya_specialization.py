"""The laya-studio specialization seam.

With MINDER_LAYA_SPECIALIZATION pointing at an exported bundle and
laya_studio importable, the provider answers the task-difficulty
contract from the specialized artifact and delegates every other
contract to the base laya head. Unset means byte-identical behavior; a
missing package, missing bundle, or bundle error falls open to the
base head exactly like every other provider failure. The specialized
distribution is flat by nature (prototype softmax), so its confidence
mapping carries its own measured scale - pinned here."""
import sys

import pytest

from minder_decision.contracts import task_difficulty_contract
from minder_decision.providers import laya, laya_spec
from minder_decision.types import ChoiceQuestion

BUNDLE_LABELS = ("mechanical", "routine", "complex", "expert_or_ambiguous")


class _StubBaseAgent:
    """Stands in for laya.Agent: records predict calls, answers the
    difficulty question with a sharpened distribution."""

    def __init__(self, probs=None):
        self.calls = []
        self._probs = probs or {"mechanical": 0.62, "routine": 0.2,
                                "complex": 0.1, "expert_or_ambiguous": 0.08}

    def predict(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        for qid, q in questions.items():
            if q.get("type") == "choice":
                options = list((q.get("criteria") or {}).keys()) or list(
                    self._probs)
                probs = {opt: (0.62 if i == 0 else 0.38 / max(len(options) - 1, 1))
                         for i, opt in enumerate(options)}
                answers[qid] = {"type": "choice",
                                "choice": max(probs, key=probs.get),
                                "probabilities": probs,
                                "confidence": 0.2}
            elif q.get("type") == "score":
                answers[qid] = {"type": "score", "score": 0}
        return {"answers": answers,
                "usage": {"input_tokens": 3, "output_tokens": 1}}


class _StubSpecialized:
    """Stands in for laya_studio.SpecializedLaya: answers the rendered
    state from a fixed distribution, or raises when rigged to."""

    class manifest:
        name = "stub-bundle"

    def __init__(self, probs=None, error=None):
        self.calls = []
        self._probs = probs or {"mechanical": 0.34, "routine": 0.27,
                                "complex": 0.21, "expert_or_ambiguous": 0.18}
        self._error = error

    def system_one(self, state, **kwargs):
        self.calls.append(state)
        if self._error is not None:
            raise self._error
        probs = self._probs
        choice = max(probs, key=probs.get)
        return {"model": "stub-bundle",
                "answers": {"decision": {"type": "choice", "choice": choice,
                                         "confidence": probs[choice],
                                         "probabilities": dict(probs)}},
                "usage": {"input_tokens": None, "output_tokens": 0}}


class _StubStudioModule:
    """Stands in for the laya_studio package: from_pretrained returns the
    given instance (or raises ImportError when set to None-ish)."""

    loaded_from = None

    @classmethod
    def install(cls, monkeypatch, instance):
        module = type(sys)("laya_studio")

        class _SpecializedLaya:
            @classmethod
            def from_pretrained(cls, path, **kwargs):
                cls.loaded_from = str(path)
                if isinstance(instance, Exception):
                    raise instance
                return instance

        module.SpecializedLaya = _SpecializedLaya
        monkeypatch.setitem(sys.modules, "laya_studio", module)


def _install_laya(monkeypatch, agent):
    module = type(sys)("laya")
    module.Agent = lambda path, device=None: agent
    module.Router = object  # the modern-surface probe
    monkeypatch.setitem(sys.modules, "laya", module)


@pytest.fixture(autouse=True)
def _fresh_provider_cache(monkeypatch):
    """try_laya_client/_build cache once per process; the seam tests need
    their own env each time."""
    monkeypatch.setattr(laya, "_CACHE", {"tried": False, "client": None})
    monkeypatch.delenv("MINDER_LAYA_SPECIALIZATION", raising=False)


def _difficulty_questions():
    contract = task_difficulty_contract()
    return contract.questions, contract


def test_unset_env_builds_the_plain_base_client(monkeypatch):
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    client = laya._try_modern()
    assert client is not None
    assert not isinstance(client, laya_spec.SpecializedLayaClient)


def test_seam_loads_the_bundle_and_answers_difficulty(monkeypatch):
    spec = _StubSpecialized()
    _StubStudioModule.install(monkeypatch, spec)
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION",
                       "presets/laya-specializations/difficulty-v1")
    client = laya._try_modern()
    assert isinstance(client, laya_spec.SpecializedLayaClient)
    assert client.model_version.startswith("laya-spec/stub-bundle")
    questions, contract = _difficulty_questions()
    resp = client.system_one({"task": "fix a typo", "turn": 1},
                             questions, contract=contract)
    assert spec.calls, "specialized artifact was never consulted"
    assert not base.calls, "base head answered for the difficulty contract"
    assert resp.provider == "laya-spec"
    assert resp.choice_probs["difficulty"] == {
        "mechanical": 0.34, "routine": 0.27, "complex": 0.21,
        "expert_or_ambiguous": 0.18}
    # the score is the expected label index over the same distribution
    expected = 0 * 0.34 + 1 * 0.27 + 2 * 0.21 + 3 * 0.18
    assert resp.score_values["difficulty_score"] == pytest.approx(
        expected, abs=1e-9)
    # confidence uses the specialized scale: clip01(20 x margin)
    margin = 0.34 - 0.27
    assert resp.confidence == pytest.approx(min(1.0, margin * 20.0))
    assert resp.top_two_margin == pytest.approx(margin)


def test_seam_delegates_non_difficulty_contracts_to_the_base_head(
        monkeypatch):
    spec = _StubSpecialized()
    _StubStudioModule.install(monkeypatch, spec)
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", "/any/bundle")
    client = laya._try_modern()
    questions = [ChoiceQuestion("best_skill", ("alpha", "none")),
                 ChoiceQuestion("any_skill_applies", ("yes", "no"))]
    resp = client.system_one({"task": "x"}, questions)
    assert base.calls, "base head was never consulted"
    assert not spec.calls
    assert resp.provider == "laya"
    assert resp.choice_probs["best_skill"] == {
        "alpha": 0.62, "none": 0.38}


def test_seam_falls_open_when_the_bundle_fails(monkeypatch):
    spec = _StubSpecialized(error=RuntimeError("checkpoint locked"))
    _StubStudioModule.install(monkeypatch, spec)
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", "/any/bundle")
    client = laya._try_modern()
    questions, contract = _difficulty_questions()
    resp = client.system_one({"task": "fix a typo", "turn": 1},
                             questions, contract=contract)
    assert base.calls, "base head did not answer after the bundle failed"
    assert resp.provider == "laya"


def test_seam_falls_open_when_laya_studio_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "laya_studio", None)  # import guard
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", "/any/bundle")
    client = laya._try_modern()
    assert client is not None
    assert not isinstance(client, laya_spec.SpecializedLayaClient)


def test_bundle_failure_names_itself_once_on_stderr(monkeypatch, capsys):
    """A bundle that fails at request time must be visible: an operator
    who set the flag has to learn it stopped being served (the inert-
    flag class of failure), exactly once, not per request."""
    spec = _StubSpecialized(error=RuntimeError("checkpoint locked"))
    _StubStudioModule.install(monkeypatch, spec)
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", "/any/bundle")
    client = laya._try_modern()
    questions, contract = _difficulty_questions()
    resp = client.system_one({"task": "t", "turn": 1}, questions,
                             contract=contract)
    assert resp.provider == "laya"
    err = capsys.readouterr().err
    assert "specialization bundle failed" in err
    assert "checkpoint locked" in err
    client.system_one({"task": "t", "turn": 1}, questions,
                      contract=contract)
    assert capsys.readouterr().err == ""


def test_unloadable_bundle_names_itself_on_stderr(monkeypatch, capsys):
    _StubStudioModule.install(monkeypatch, RuntimeError("no such directory"))
    base = _StubBaseAgent()
    _install_laya(monkeypatch, base)
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", "/missing/bundle")
    client = laya._try_modern()
    assert not isinstance(client, laya_spec.SpecializedLayaClient)
    err = capsys.readouterr().err
    assert "MINDER_LAYA_SPECIALIZATION" in err
    assert "no such directory" in err


def test_specialized_confidence_scale_is_pinned():
    from minder_decision.providers.laya_spec import (
        SPEC_MARGIN_SCALE, specialized_confidence)
    # measured scale: the 0.7 floor corresponds to a 0.035 margin on the
    # drafted hard split; the raw 2.0x scale would abstain on everything
    assert SPEC_MARGIN_SCALE == 20.0
    # a 0.035 top-two margin reads as exactly the 0.7 floor
    flat = {"mechanical": 0.40, "routine": 0.365, "complex": 0.13,
            "expert_or_ambiguous": 0.105}
    sharp = {"mechanical": 0.80, "routine": 0.10, "complex": 0.06,
             "expert_or_ambiguous": 0.04}
    assert specialized_confidence(flat) == pytest.approx(0.7)
    assert specialized_confidence(sharp) == 1.0
    tie = {"mechanical": 0.25, "routine": 0.25, "complex": 0.25,
           "expert_or_ambiguous": 0.25}
    assert specialized_confidence(tie) == 0.0
    assert specialized_confidence({}) == 0.0
    assert specialized_confidence(None) == 0.0


def test_a_non_difficulty_bundle_path_is_ignored_for_other_contracts():
    """Detection is by contract identity: a bundle trained for another
    contract must never answer the difficulty question. The seam loads
    only for task-difficulty; anything else falls through (covered by
    delegation test) - this pins the detection predicate itself."""
    from minder_decision.providers.laya_spec import _is_difficulty_contract
    questions, contract = _difficulty_questions()
    assert _is_difficulty_contract(contract)
    assert _is_difficulty_contract(None) is False
    skill = [ChoiceQuestion("best_skill", ("alpha", "none"))]
    assert _is_difficulty_contract(skill) is False


@pytest.mark.laya
def test_real_bundle_answers_through_the_seam(monkeypatch):
    """The shipped difficulty-v1 bundle loads through the seam and its
    answers validate against the contract. Skipped without laya_studio
    or the bundle."""
    try:
        import laya_studio  # noqa: F401
    except ImportError:
        pytest.skip("laya_studio not installed")
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle = os.path.join(root, "presets", "laya-specializations",
                          "difficulty-v1")
    if not os.path.isdir(bundle):
        pytest.skip("difficulty-v1 bundle not present")
    monkeypatch.setenv("MINDER_LAYA_SPECIALIZATION", bundle)
    monkeypatch.setattr(laya, "_CACHE", {"tried": False, "client": None})
    client = laya._build(500)
    assert isinstance(client, laya_spec.SpecializedLayaClient)
    questions, contract = _difficulty_questions()
    resp = client.system_one({"task": "Fix the typo in the README.",
                              "turn": 1},
                             questions, contract=contract)
    assert resp.provider == "laya-spec"
    resp.validate(contract)
    assert resp.choice_probs["difficulty"]
    assert 0.0 <= resp.score_values["difficulty_score"] <= 3.0
