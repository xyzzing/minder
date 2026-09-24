"""The two shipped contracts (Phase 5.5 slices A and F).

failure-triage/v1: failure_kind (frozen taxonomy), needs_new_evidence
(Noul), next_step (runtime options = the live menu ids only).
skill-select/v1: any_skill_applies (Noul), best_skill (runtime options =
shortlist ids + "none").

`next_step` and `best_skill` options are injected per request from the
live menu — never a global hardcoded list. Changing criteria means a new
contract version; the criteria hash stays stable for the frozen parts.
"""
from .types import (ChoiceQuestion, Contract, NoulQuestion, ScoreQuestion,
                    make_contract)

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


# --- task-difficulty/v1: the laya fast decision layer (never a solver) ---
#
# One non-autoregressive pass answers BOTH questions: a 4-label typed
# difficulty (choice) and a 0-3 calibrated score. The rubric text below is
# the user's spec verbatim — changing it means a new contract version.
TASK_DIFFICULTY_ID = "task-difficulty"
TASK_DIFFICULTY_VERSION = "v1"

DIFFICULTY_LABELS = ("mechanical", "routine", "complex",
                     "expert_or_ambiguous")

DIFFICULTY_CRITERIA = {
    "mechanical": ("Localized change, explicit intent, obvious path, "
                   "simple validation."),
    "routine": ("Normal judgement across few known files, clear acceptance "
               "criteria."),
    "complex": ("Multi-file or system design, non-obvious debugging, "
                "migrations, concurrency, security, or performance."),
    "expert_or_ambiguous": ("Conflicting or missing requirements, domain "
                            "expertise, high blast radius, novel "
                            "architecture, or a human decision is needed."),
}

DIFFICULTY_SCORE_CRITERIA = (
    "Explicit local instruction, one obvious change.",
    "Standard practice, limited reading, 1-3 tests.",
    "Reconcile multiple files/APIs/edge cases, compare approaches.",
    "Uncertain causes/trade-offs, architecture, security, concurrency, "
    "migrations, or production behavior.",
)


def task_difficulty_contract():
    """Both rubrics travel in the same single system_one pass."""
    return make_contract(
        TASK_DIFFICULTY_ID, TASK_DIFFICULTY_VERSION,
        (ChoiceQuestion("difficulty", DIFFICULTY_LABELS,
                        criteria=dict(DIFFICULTY_CRITERIA)),
         ScoreQuestion("difficulty_score", 0, 3,
                       criteria=DIFFICULTY_SCORE_CRITERIA)))
