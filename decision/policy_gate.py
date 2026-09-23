"""Policy gate (Phase 5.5 slice D): the Python side that decides.

The model proposes; this gate disposes. Rules, in order:
1. Warden L3 (menu is human-only) -> ignore the model entirely.
2. Recommended id is `frontier_consult` -> NEVER from the model; the
   Warden's own L2 flow is the only path to a consult (defer_warden).
3. Recommended id not in the live menu -> `human`, override
   illegal_action.
4. Recommended id duplicate-blocked -> `human`, override
   duplicate_blocked.
5. Confidence below the per-action threshold -> the pinned fallback
   (never tuned on holdout in this phase).
6. Otherwise the recommendation stands.

`model_recommendation` and `policy_decision` are stored separately —
the trace always shows both. DB/decision errors fail open: callers keep
the Warden result.
"""
from dataclasses import dataclass

from .types import InvalidDistribution

# action -> (min confidence, fallback strategy); pin with contract v1
THRESHOLDS = {
    "inspect": (0.70, "deterministic_memory_policy"),
    "retrieve_lesson": (0.70, "deterministic_memory_policy"),
    "think_retry": (0.80, "inspect"),
    "environment_check": (0.75, "human_if_permissions"),
    "frontier_consult": (None, "warden_l2_only"),  # never from the model
    "human": (0.00, None),  # always legal
}

HUMAN = "human"
FRONTIER = "frontier_consult"
DEFER_WARDEN = "defer_warden"


@dataclass
class PolicyDecision:
    contract_id: str
    contract_version: str
    model_recommendation: str
    policy_decision: str
    override: str = ""
    fallback: str = ""
    confidence: float = 0.0
    failure_kind: str = "unknown"
    needs_new_evidence: float = 0.0


def gate(response, menu, contract, cfg=None, thresholds=None):
    """Apply the code rules to one response. Never raises: an invalid or
    missing response becomes a conservative human decision."""
    try:
        response.validate(contract)
    except InvalidDistribution as exc:
        return PolicyDecision(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            model_recommendation="", policy_decision=HUMAN,
            override="invalid_distribution", fallback=str(exc),
            failure_kind=_failure_kind(response))
    except Exception as exc:  # noqa: BLE001 — fail open, never break Warden
        return PolicyDecision(
            contract_id=contract.contract_id,
            contract_version=contract.version,
            model_recommendation="", policy_decision=HUMAN,
            override="gate_error", fallback=type(exc).__name__)

    failure_kind = _failure_kind(response)
    needs_new_evidence = _noul(response, contract, "needs_new_evidence")
    recommended = _primary_choice(response, contract)
    common = dict(contract_id=contract.contract_id,
                  contract_version=contract.version,
                  model_recommendation=recommended or "",
                  confidence=float(response.confidence or 0.0),
                  failure_kind=failure_kind,
                  needs_new_evidence=float(needs_new_evidence or 0.0))

    if menu.l3 or set(menu.action_ids) == {HUMAN}:  # rule: Warden L3 wins
        return PolicyDecision(policy_decision=HUMAN,
                              override="warden_l3", **common)
    if recommended == FRONTIER:  # rule: consults never come from the model
        return PolicyDecision(policy_decision=DEFER_WARDEN,
                              override="frontier_never_from_model",
                              fallback="warden_l2_only", **common)
    if recommended not in menu.action_ids:  # rule: closed menu
        return PolicyDecision(policy_decision=HUMAN,
                              override="illegal_action", **common)
    if recommended in set(menu.blocked_action_ids):  # rule: no same retry
        return PolicyDecision(policy_decision=HUMAN,
                              override="duplicate_blocked", **common)

    active_thresholds = thresholds or THRESHOLDS
    min_confidence, fallback = active_thresholds.get(
        recommended, (0.70, "deterministic_memory_policy"))
    if confidence_of(response) < min_confidence:  # rule: pinned thresholds
        resolved = _resolve_fallback(fallback, failure_kind, menu)
        return PolicyDecision(policy_decision=resolved,
                              override="low_confidence",
                              fallback=resolved, **common)
    return PolicyDecision(policy_decision=recommended, **common)


def confidence_of(response):
    try:
        return max(0.0, min(1.0, float(response.confidence)))
    except (TypeError, ValueError):
        return 0.0


def _primary_choice(response, contract):
    from .types import ChoiceQuestion
    choice_ids = [q.id for q in contract.questions
                  if isinstance(q, ChoiceQuestion)]
    if not choice_ids:
        return None
    primary_id = choice_ids[-1]  # the last choice question is primary
    return response.top_choice(primary_id)


def _failure_kind(response):
    probs = (response.choice_probs or {}).get("failure_kind") or {}
    if not probs:
        return "unknown"
    return max(sorted(probs), key=lambda opt: probs[opt])


def _noul(response, contract, question_id):
    value = (response.noul_probs or {}).get(question_id)
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _resolve_fallback(fallback, failure_kind, menu):
    if fallback == "inspect":
        return "inspect" if "inspect" in menu else HUMAN
    if fallback == "human_if_permissions":
        if failure_kind == "permissions" or HUMAN in menu:
            return HUMAN if failure_kind == "permissions" else "inspect"
        return HUMAN
    if fallback == "deterministic_memory_policy":
        return "retrieve_lesson" if "retrieve_lesson" in menu else "inspect"
    return HUMAN
