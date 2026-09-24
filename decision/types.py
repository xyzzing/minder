"""Typed questions, contracts, and responses for the decision loop
(Phase 5.5 slice A).

A Contract pins contract_id + schema version + the frozen question set;
changing criteria means a new version, and `criteria_hash` is a stable
digest of those frozen criteria (runtime-menu questions contribute their
id and kind, not their per-call options).

DecisionResponse.validate rejects malformed distributions — the tests
treat an invalid distribution as a hard error, never a silent fallback.
"""
import hashlib
import json
from dataclasses import dataclass, field


class InvalidDistribution(ValueError):
    """A client response whose probabilities are malformed."""


@dataclass(frozen=True)
class ChoiceQuestion:
    """Pick exactly one option. options must be non-empty and unique;
    runtime_options=True means the options come from the live menu per
    request (e.g. next_step) rather than from frozen contract criteria.
    criteria (optional) maps option -> rubric definition; it is frozen into
    the criteria hash and rendered into the model's question text."""
    id: str
    options: tuple
    runtime_options: bool = False
    criteria: dict = None


@dataclass(frozen=True)
class ScoreQuestion:
    """A bounded numeric score: expected value in [min_value, max_value].
    criteria (optional) is the per-level rubric, index-aligned with the
    range (e.g. 0..3 -> four level definitions)."""
    id: str
    min_value: int = 0
    max_value: int = 3
    criteria: tuple = ()


@dataclass(frozen=True)
class NoulQuestion:
    """A probability question: P(yes), 0.0..1.0."""
    id: str


@dataclass(frozen=True)
class Contract:
    contract_id: str
    version: str
    questions: tuple
    criteria_hash: str = ""


def make_contract(contract_id, version, questions):
    """Validated constructor: rejects empty ids and empty/duplicate choice
    options; computes the stable criteria hash. Raises ValueError."""
    if not contract_id or not version:
        raise ValueError("contract_id and version are required")
    if not questions:
        raise ValueError("contract needs at least one question")
    seen_ids = set()
    for question in questions:
        if not getattr(question, "id", ""):
            raise ValueError("every question needs an id")
        if question.id in seen_ids:
            raise ValueError(f"duplicate question id: {question.id}")
        seen_ids.add(question.id)
        options = getattr(question, "options", None)
        if options is not None:
            if not options:
                raise ValueError(f"question {question.id} has empty options")
            if len(set(options)) != len(list(options)):
                raise ValueError(
                    f"question {question.id} has duplicate options")
    return Contract(contract_id=contract_id, version=version,
                    questions=tuple(questions),
                    criteria_hash=_criteria_hash(contract_id, version,
                                                 questions))


def _criteria_hash(contract_id, version, questions):
    frozen = [_question_spec(q) for q in questions]
    blob = json.dumps({"contract_id": contract_id, "version": version,
                       "questions": frozen}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _question_spec(question):
    options = getattr(question, "options", None)
    spec = {"kind": type(question).__name__, "id": question.id,
            "options": (None if options is None
                        or getattr(question, "runtime_options", False)
                        else list(options))}
    criteria = getattr(question, "criteria", None)
    if criteria:
        spec["criteria"] = (dict(criteria) if isinstance(criteria, dict)
                            else list(criteria))
    for key in ("min_value", "max_value"):
        value = getattr(question, key, None)
        if value is not None:
            spec[key] = value
    return spec


def contract_to_json(contract):
    """Serialize a contract (id, version, questions, hash)."""
    return json.dumps({
        "contract_id": contract.contract_id, "version": contract.version,
        "criteria_hash": contract.criteria_hash,
        "questions": [_question_spec(q) for q in contract.questions],
    }, sort_keys=True)


@dataclass
class DecisionResponse:
    """What a System One client returns for one contract.
    choice_probs: question id -> {option: probability}
    noul_probs: question id -> P(yes)
    score_values: question id -> numeric score in the question's range"""
    contract_id: str
    contract_version: str
    choice_probs: dict = field(default_factory=dict)
    noul_probs: dict = field(default_factory=dict)
    score_values: dict = field(default_factory=dict)
    confidence: float = 0.0
    top_two_margin: float = 0.0
    latency_ms: float = 0.0
    provider: str = ""
    model_version: str = ""

    def validate(self, contract):
        """Raise InvalidDistribution on any malformed probability: wrong
        option sets, out-of-range values, or distributions that do not
        sum to ~1. Noul questions must be within [0, 1]. Score questions must be
        numeric and within [min_value, max_value]."""
        if self.contract_id and (
                self.contract_id != contract.contract_id
                or self.contract_version != contract.version):
            raise InvalidDistribution("contract mismatch")
        for question in contract.questions:
            if isinstance(question, NoulQuestion):
                value = self.noul_probs.get(question.id)
                if value is None:
                    raise InvalidDistribution(
                        f"missing noul probability: {question.id}")
                if not 0.0 <= float(value) <= 1.0:
                    raise InvalidDistribution(
                        f"noul {question.id} out of range: {value}")
                continue
            if isinstance(question, ScoreQuestion):
                value = self.score_values.get(question.id)
                if value is None:
                    raise InvalidDistribution(
                        f"missing score: {question.id}")
                try:
                    score = float(value)
                except (TypeError, ValueError):
                    raise InvalidDistribution(
                        f"score {question.id} not numeric: {value!r}")
                if not question.min_value <= score <= question.max_value:
                    raise InvalidDistribution(
                        f"score {question.id} out of range: {score}")
                continue
            probs = self.choice_probs.get(question.id)
            if not isinstance(probs, dict):
                raise InvalidDistribution(
                    f"missing choice distribution: {question.id}")
            if set(probs) != set(question.options):
                raise InvalidDistribution(
                    f"{question.id} option set mismatch")
            total = 0.0
            for option, value in probs.items():
                value = float(value)
                if not 0.0 <= value <= 1.0:
                    raise InvalidDistribution(
                        f"{question.id}[{option}] out of range: {value}")
                total += value
            if abs(total - 1.0) > 1e-6:
                raise InvalidDistribution(
                    f"{question.id} does not sum to 1 (got {total})")

    def top_choice(self, question_id):
        """Argmax option for a choice question, or None."""
        probs = self.choice_probs.get(question_id) or {}
        if not probs:
            return None
        return max(sorted(probs), key=lambda opt: probs[opt])


def top_two_margin(probs):
    """Gap between the best and second-best option (0 for <2 options)."""
    if not probs:
        return 0.0
    ordered = sorted((float(v) for v in probs.values()), reverse=True)
    if len(ordered) < 2:
        return ordered[0]
    return ordered[0] - ordered[1]
