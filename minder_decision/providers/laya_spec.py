"""The laya-studio specialization seam (issue #34).

Optional layer over the base laya provider: when
MINDER_LAYA_SPECIALIZATION points at an exported laya-studio bundle and
the laya_studio package is importable, the task-difficulty contract is
answered from the specialized artifact (a whitened-prototype classifier
over the frozen encoder - the decision head showed no signal on this
rubric); every other contract delegates to the base head untouched.
Any failure - missing package, missing bundle, bundle error - falls
open to the base head, never raises.

The specialized distribution is flat by nature (prototype softmax over
encoder similarities), so the margin calibration the head gets
(CALIBRATION_MARGIN_SCALE = 2.0) would abstain on everything here. The
specialized mapping carries its own measured scale below.
"""
from ..types import ChoiceQuestion, top_two_margin
import sys

DIFFICULTY_CONTRACT_ID = "task-difficulty"

# Adapter-side confidence calibration for the specialized distribution,
# measured, not assumed (Law #9): on the drafted hard split (fitting/
# hard.json, 34 rows - boundary pairs, noise, injection, styles) the
# margin separating the runner-up sits at p90 = 0.018 for wrong answers
# and p50 = 0.026 / p75 = 0.035 for right ones, so the 0.7 floor is
# pinned at a 0.035 margin: clip01(20 x margin). The provisional eval
# set (harder than the calibration split; labels still owner-pending)
# is what ruled out the looser end of that range - at this scale the
# bundle dominates the base head where it acts (0.754 selective accuracy
# at 33% coverage vs the head's 0.625 at 24%). Both numbers move when
# the owner accepts labels; live shadow is the arbiter after that.
SPEC_MARGIN_SCALE = 20.0


def specialized_confidence(choice_probs):
    """clip01(SPEC_MARGIN_SCALE x margin) over the specialized choice
    distribution. Pure function; never raises."""
    try:
        margin = top_two_margin(choice_probs or {})
        return max(0.0, min(1.0, margin * SPEC_MARGIN_SCALE))
    except Exception:
        return 0.0


def _is_difficulty_contract(contract_or_questions):
    """True when the call is the task-difficulty contract - by contract
    identity when the caller passed one, otherwise by question shape."""
    obj = contract_or_questions
    if obj is None:
        return False
    contract_id = getattr(obj, "contract_id", None)
    if contract_id is not None:
        return contract_id == DIFFICULTY_CONTRACT_ID
    questions = obj if isinstance(obj, (list, tuple)) else None
    if questions is None:
        return False
    for question in questions:
        if not isinstance(question, ChoiceQuestion):
            continue
        if question.id == "difficulty" and set(
                question.options or ()) == {
                "mechanical", "routine", "complex", "expert_or_ambiguous"}:
            return True
    return False


class _StaticAnswerAgent:
    """Feeds prebuilt laya-shaped answers through the base mapping so
    the spec-building, option matching, and FakeClient coercion stay in
    exactly one code path (LayaSystemOneClient.system_one)."""

    def __init__(self, answers, usage):
        self._answers = answers
        self._usage = usage

    def predict(self, _state, _questions):
        return {"answers": self._answers, "usage": self._usage}


class SpecializedLayaClient:
    """The seam's client: difficulty from the bundle, everything else
    from the base head. Exposes the same system_one surface as
    LayaSystemOneClient (worker and in-process paths both call it)."""

    def __init__(self, base_client, specialized):
        self._base = base_client
        self._specialized = specialized
        self._failure_noted = False
        self.model_version = self._bundle_version()

    def _note_failure(self, exc):
        """One named stderr line per process: a bundle that fails at
        request time degrades to the base head, and an operator who set
        the flag must be able to see that it stopped being served."""
        if self._failure_noted:
            return
        self._failure_noted = True
        print(f"minder-decision: specialization bundle failed; the base "
              f"head answers until restart: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)

    def _bundle_version(self):
        try:
            name = self._specialized.manifest.name
        except Exception:
            name = "bundle"
        return f"laya-spec/{name}"

    def system_one(self, state, questions, model=None, contract=None):
        if _is_difficulty_contract(contract if contract is not None
                                   else questions):
            try:
                return self._specialized_system_one(
                    state, questions, model=model, contract=contract)
            except Exception as exc:  # noqa: BLE001 - fail open, named
                self._note_failure(exc)
        return self._base.system_one(state, questions, model=model,
                                     contract=contract)

    def _specialized_system_one(self, state, questions, model=None,
                                contract=None):
        from .laya import LayaSystemOneClient, _render_state
        raw = self._specialized.system_one(_render_state(state))
        answer = (raw.get("answers") or {}).get("decision") or {}
        probs = answer.get("probabilities")
        if not isinstance(probs, dict) or not probs:
            raise ValueError("bundle returned no probability distribution")
        choice = answer.get("choice") or max(probs, key=probs.get)
        score = sum(index * float(probs.get(label, 0.0))
                    for index, label in enumerate(
                        ("mechanical", "routine", "complex",
                         "expert_or_ambiguous")))
        answers = {
            "difficulty": {"type": "choice", "choice": choice,
                           "probabilities": dict(probs)},
            "difficulty_score": {"type": "score",
                                 "score": max(0.0, min(3.0, score))},
        }
        static = LayaSystemOneClient(
            _StaticAnswerAgent(answers, raw.get("usage") or {}),
            source=self.model_version)
        response = static.system_one(state, questions, model=model,
                                     contract=contract)
        # the specialized distribution needs its own calibration scale;
        # the base mapping (2.0x) would read every prototype softmax as
        # a near-tie
        response.confidence = specialized_confidence(
            (response.choice_probs or {}).get("difficulty"))
        response.provider = "laya-spec"
        response.model_version = self.model_version
        response.top_two_margin = top_two_margin(
            (response.choice_probs or {}).get("difficulty") or {})
        return response


def try_specialized(base_client, device):
    """SpecializedLayaClient | base_client. Returns the base client
    unchanged unless MINDER_LAYA_SPECIALIZATION names a loadable
    bundle and laya_studio imports; never raises."""
    import os
    path = os.environ.get("MINDER_LAYA_SPECIALIZATION", "").strip()
    if not path or base_client is None:
        return base_client
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from laya_studio import SpecializedLaya
            specialized = SpecializedLaya.from_pretrained(path, device=device)
        return SpecializedLayaClient(base_client, specialized)
    except Exception as exc:  # noqa: BLE001 - fail open, named once
        print(f"minder-decision: MINDER_LAYA_SPECIALIZATION={path!r} did "
              f"not load; the base head serves the difficulty contract: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return base_client
