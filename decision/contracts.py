"""The two shipped contracts (Phase 5.5 slices A and F).

failure-triage/v1: failure_kind (frozen taxonomy), needs_new_evidence
(Noul), next_step (runtime options = the live menu ids only).
skill-select/v1: any_skill_applies (Noul), best_skill (runtime options =
shortlist ids + "none").

`next_step` and `best_skill` options are injected per request from the
live menu — never a global hardcoded list. Changing criteria means a new
contract version; the criteria hash stays stable for the frozen parts.
"""
from .types import ChoiceQuestion, Contract, NoulQuestion, make_contract

FAILURE_TRIAGE_ID = "failure-triage"
SKILL_SELECT_ID = "skill-select"

FAILURE_KINDS = ("code_logic", "schema_contract", "environment",
                 "permissions", "test_expectation", "unknown")

TRIAGE_VERSION = "v1"
SKILL_SELECT_VERSION = "v1"


def failure_triage_contract(next_step_options):
    """next_step options are the runtime menu ids and nothing else."""
    return make_contract(
        FAILURE_TRIAGE_ID, TRIAGE_VERSION,
        (ChoiceQuestion("failure_kind", FAILURE_KINDS),
         NoulQuestion("needs_new_evidence"),
         ChoiceQuestion("next_step", tuple(next_step_options),
                        runtime_options=True)))


def skill_select_contract(skill_ids):
    """best_skill options: the shortlist plus "none" (always legal)."""
    options = tuple(skill_ids) + ("none",)
    return make_contract(
        SKILL_SELECT_ID, SKILL_SELECT_VERSION,
        (NoulQuestion("any_skill_applies"),
         ChoiceQuestion("best_skill", options, runtime_options=True)))


DOMAIN_ROUTE_ID = "domain-route"
ROUTE_VERSION = "v1"

# Closed routing vocabulary (PRD v2 §Domain routing contract). Free-text
# domains are never accepted; model-generated labels cannot extend these.
ROUTE_DOMAIN_SET = ("coding", "trading_research", "resume_application",
                    "cited_research", "mixed", "unknown")
INTENT_KINDS = ("factual_correction", "wording_choice",
                "presentation_preference", "research_question", "unknown")


def domain_route_contract(transition_options):
    """transition options are the runtime menu ids (the live transition
    set + human) and nothing else. The LAST choice question is the
    gate-primary action — keep `transition` last."""
    return make_contract(
        DOMAIN_ROUTE_ID, ROUTE_VERSION,
        (ChoiceQuestion("candidate_domain", ROUTE_DOMAIN_SET),
         ChoiceQuestion("intent_kind", INTENT_KINDS),
         ChoiceQuestion("transition", tuple(transition_options),
                        runtime_options=True)))
