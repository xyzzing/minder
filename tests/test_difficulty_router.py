"""Unit tests for the difficulty router (decision/difficulty.py) and the
task-difficulty contract (minder_decision/contracts.py). No laya, no network —
FakeClient fixtures only.
"""
import pytest

from minder_decision.contracts import (DIFFICULTY_LABELS, task_difficulty_contract)
from minder_decision.difficulty import (DEFAULT_BANDS, apply_band, band_for,
                                 resolve_difficulty, score_label)
from minder_decision.providers.fake import FakeClient
from minder_decision.types import (DecisionResponse,
                            InvalidDistribution)

CFG = {"laya_min_confidence": 0.7}


def _contract():
    return task_difficulty_contract()


def _response(label, score, confidence=0.9):
    contract = _contract()
    probs = {opt: (1.0 if opt == label else 0.0) for opt in DIFFICULTY_LABELS}
    return DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"difficulty": probs},
        score_values={"difficulty_score": score},
        confidence=confidence)


# ---------------------------------------------------------------------------
# score bucketing
# ---------------------------------------------------------------------------

def test_score_label_buckets():
    assert score_label(0.0) == "mechanical"
    assert score_label(0.4) == "mechanical"
    assert score_label(1.0) == "routine"
    assert score_label(1.4) == "routine"
    assert score_label(2.0) == "complex"
    assert score_label(2.4) == "complex"
    assert score_label(3.0) == "expert_or_ambiguous"
    assert score_label(2.5) == "expert_or_ambiguous"


def test_score_label_malformed():
    assert score_label("nonsense") is None
    assert score_label(None) is None


# ---------------------------------------------------------------------------
# T1: each label maps to its band
# ---------------------------------------------------------------------------

def test_t1_labels_map_to_bands():
    assert DEFAULT_BANDS["mechanical"]["level"] == 0
    assert DEFAULT_BANDS["mechanical"]["effort"] == "off"
    assert DEFAULT_BANDS["routine"]["level"] == 1
    assert DEFAULT_BANDS["routine"]["effort"] == "low"
    assert DEFAULT_BANDS["routine"]["budget"] == 2048
    assert DEFAULT_BANDS["complex"]["level"] == 2
    assert DEFAULT_BANDS["complex"]["effort"] == "high"
    assert DEFAULT_BANDS["complex"]["budget"] == 10240
    assert DEFAULT_BANDS["complex"]["guardrail"] == "spend"
    assert DEFAULT_BANDS["expert_or_ambiguous"]["level"] == 2
    assert DEFAULT_BANDS["expert_or_ambiguous"]["effort"] == "xhigh"
    assert DEFAULT_BANDS["expert_or_ambiguous"]["budget"] == 12000
    assert DEFAULT_BANDS["expert_or_ambiguous"]["guardrail"] == "spend"


def test_t1_resolve_each_label():
    for label in DIFFICULTY_LABELS:
        resolved = resolve_difficulty(_response(label, float(
            DIFFICULTY_LABELS.index(label))), _contract(), CFG)
        assert resolved is not None
        got_label, band = resolved
        assert got_label == label
        assert band["label"] == label
        assert band["effort"] == DEFAULT_BANDS[label]["effort"]


# ---------------------------------------------------------------------------
# T2: score cross-check escalates, never de-escalates
# ---------------------------------------------------------------------------

def test_t2_score_escalates():
    # routine label but score 3 -> expert_or_ambiguous (conservative)
    resolved = resolve_difficulty(_response("routine", 3.0), _contract(), CFG)
    assert resolved is not None
    assert resolved[0] == "expert_or_ambiguous"


def test_t2_score_never_deescalates():
    # complex label but score 0 -> stays complex
    resolved = resolve_difficulty(_response("complex", 0.0), _contract(), CFG)
    assert resolved is not None
    assert resolved[0] == "complex"


# ---------------------------------------------------------------------------
# T3: low confidence -> no opinion
# ---------------------------------------------------------------------------

def test_t3_low_confidence_no_opinion():
    resolved = resolve_difficulty(_response("routine", 1.0,
                                            confidence=0.5),
                                  _contract(), CFG)
    assert resolved is None


def test_t3_confidence_at_threshold_opinion():
    resolved = resolve_difficulty(_response("routine", 1.0,
                                            confidence=0.7),
                                  _contract(), CFG)
    assert resolved is not None


# ---------------------------------------------------------------------------
# T4: malformed / missing -> no opinion, never raises
# ---------------------------------------------------------------------------

def test_t4_missing_score_no_opinion():
    contract = _contract()
    probs = {opt: (1.0 if opt == "routine" else 0.0)
             for opt in DIFFICULTY_LABELS}
    resp = DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"difficulty": probs},
        score_values={},  # missing
        confidence=0.9)
    assert resolve_difficulty(resp, contract, CFG) is None


def test_t4_out_of_range_score_no_opinion():
    contract = _contract()
    probs = {opt: (1.0 if opt == "routine" else 0.0)
             for opt in DIFFICULTY_LABELS}
    resp = DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"difficulty": probs},
        score_values={"difficulty_score": 5.0},  # out of range
        confidence=0.9)
    assert resolve_difficulty(resp, contract, CFG) is None


def test_t4_bad_distribution_no_opinion():
    contract = _contract()
    resp = DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"difficulty": {"routine": 0.5}},  # doesn't sum to 1
        score_values={"difficulty_score": 1.0},
        confidence=0.9)
    assert resolve_difficulty(resp, contract, CFG) is None


def test_t4_none_response_no_opinion():
    assert resolve_difficulty(None, _contract(), CFG) is None
    assert resolve_difficulty(_response("routine", 1.0), None, CFG) is None


# ---------------------------------------------------------------------------
# T5: cfg band override honored
# ---------------------------------------------------------------------------

def test_t5_band_override():
    cfg = {"laya_min_confidence": 0.7,
           "difficulty_bands": {"routine": {"budget": 4096}}}
    resolved = resolve_difficulty(_response("routine", 1.0), _contract(), cfg)
    assert resolved is not None
    assert resolved[1]["budget"] == 4096
    # non-overridden fields stay default
    assert resolved[1]["effort"] == "low"
    assert resolved[1]["level"] == 1


# ---------------------------------------------------------------------------
# apply_band: ceiling only, budget into chat_template_kwargs
# ---------------------------------------------------------------------------

def test_apply_band_sets_budget_and_ceiling():
    band = band_for("complex", CFG)
    req = {"max_tokens": 8192}
    applied = apply_band(req, band, CFG)
    assert req["chat_template_kwargs"]["thinking_budget"] == 10240
    # ceiling lowers the client's 8192 to the band's 32768? no — 8192 < 32768
    assert req["max_tokens"] == 8192
    assert applied["budget"] == 10240


def test_apply_band_never_raises_ceiling():
    band = band_for("mechanical", CFG)  # ceiling 2048
    req = {"max_tokens": 4096}
    apply_band(req, band, CFG)
    assert req["max_tokens"] == 2048


def test_apply_band_no_client_ceiling_takes_band():
    band = band_for("routine", CFG)  # ceiling 8192
    req = {}
    apply_band(req, band, CFG)
    assert req["max_tokens"] == 8192


# ---------------------------------------------------------------------------
# contract + types
# ---------------------------------------------------------------------------

def test_task_difficulty_contract_shape():
    contract = _contract()
    assert contract.contract_id == "task-difficulty"
    assert contract.version == "v1"
    kinds = [type(q).__name__ for q in contract.questions]
    assert kinds == ["ChoiceQuestion", "ScoreQuestion"]
    choice = contract.questions[0]
    assert choice.options == DIFFICULTY_LABELS
    assert set(choice.criteria) == set(DIFFICULTY_LABELS)
    score = contract.questions[1]
    assert score.min_value == 0 and score.max_value == 3
    assert len(score.criteria) == 4


def test_score_question_validation_pass():
    contract = _contract()
    resp = _response("routine", 1.5)
    resp.validate(contract)  # must not raise


def test_score_question_out_of_range_raises():
    contract = _contract()
    resp = _response("routine", 4.0)
    with pytest.raises(InvalidDistribution):
        resp.validate(contract)


def test_score_question_missing_raises():
    contract = _contract()
    probs = {opt: (1.0 if opt == "routine" else 0.0)
             for opt in DIFFICULTY_LABELS}
    resp = DecisionResponse(
        contract_id=contract.contract_id,
        contract_version=contract.version,
        choice_probs={"difficulty": probs},
        score_values={},
        confidence=0.9)
    with pytest.raises(InvalidDistribution):
        resp.validate(contract)


# ---------------------------------------------------------------------------
# FakeClient fills scores (clamped to range)
# ---------------------------------------------------------------------------

def test_fake_client_fills_score():
    contract = _contract()
    client = FakeClient(
        fixtures={"k": {"difficulty": "complex",
                        "difficulty_score": 2.0,
                        "confidence": 0.9}},
        key_fn=lambda s: "k")
    resp = client.system_one("k", contract.questions, contract=contract)
    resp.validate(contract)
    assert resp.score_values["difficulty_score"] == 2.0
    assert resp.top_choice("difficulty") == "complex"


def test_fake_client_clamps_score():
    contract = _contract()
    client = FakeClient(
        fixtures={"k": {"difficulty": "mechanical",
                        "difficulty_score": 9.9,
                        "confidence": 0.9}},
        key_fn=lambda s: "k")
    resp = client.system_one("k", contract.questions, contract=contract)
    resp.validate(contract)
    assert resp.score_values["difficulty_score"] == 3.0
