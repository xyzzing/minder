"""Policy gate tests (Phase 5.5 slice D): closed menu, pinned thresholds,
Warden L3 supremacy, frontier never from the model, duplicate-block, and
separate storage of model recommendation vs policy decision."""
from decision import contracts as dcontracts
from decision import menu as dmenu
from decision import policy_gate as dgate
from decision.types import DecisionResponse

MENU_IDS = dmenu.BASE_ACTIONS + ("frontier_consult", "human")


def triage_contract():
    return dcontracts.failure_triage_contract(MENU_IDS)


def response(next_step, confidence=0.9, failure_kind="code_logic",
             needs_new_evidence=0.2, contract=None):
    contract = contract or triage_contract()
    return DecisionResponse(
        contract_id=contract.contract_id, contract_version=contract.version,
        choice_probs={
            "failure_kind": {k: (1.0 if k == failure_kind else 0.0)
                             for k in dcontracts.FAILURE_KINDS},
            "next_step": {opt: (1.0 if opt == next_step else 0.0)
                          for opt in contract.questions[-1].options},
        },
        noul_probs={"needs_new_evidence": needs_new_evidence},
        confidence=confidence, provider="fake", model_version="fake-v1")


def test_recommended_outside_menu_is_illegal_human():
    contract = dcontracts.failure_triage_contract(
        MENU_IDS + ("deploy_to_prod",))  # model saw an id the menu lacks
    menu = dmenu.build_menu(level=1, frontier_allowed=False)
    out = dgate.gate(response("deploy_to_prod", contract=contract), menu,
                     contract)
    assert out.model_recommendation == "deploy_to_prod"  # stored separately
    assert out.policy_decision == "human"
    assert out.override == "illegal_action"


def test_low_confidence_falls_back_per_threshold_table():
    menu = dmenu.build_menu(level=1)
    # think_retry needs 0.80; 0.75 falls back to inspect
    out = dgate.gate(response("think_retry", confidence=0.75), menu,
                     triage_contract())
    assert out.policy_decision == "inspect"
    assert out.override == "low_confidence" and out.fallback == "inspect"
    # at threshold it stands
    ok = dgate.gate(response("think_retry", confidence=0.80), menu,
                    triage_contract())
    assert ok.policy_decision == "think_retry" and ok.override == ""
    # inspect tier needs 0.70; below that -> deterministic memory policy
    low = dgate.gate(response("inspect", confidence=0.5), menu,
                     triage_contract())
    assert low.policy_decision == "retrieve_lesson"
    assert low.override == "low_confidence"


def test_environment_low_confidence_falls_back_human_if_permissions():
    menu = dmenu.build_menu(level=1)
    out = dgate.gate(response("environment_check", confidence=0.70,
                              failure_kind="permissions"), menu,
                     triage_contract())
    assert out.policy_decision == "human"  # permissions-like -> human
    out2 = dgate.gate(response("environment_check", confidence=0.70,
                               failure_kind="environment"), menu,
                      triage_contract())
    assert out2.policy_decision == "inspect"


def test_warden_l3_ignores_the_model():
    menu = dmenu.build_menu(level=3, l3_fired=True, frontier_allowed=False)
    out = dgate.gate(response("inspect", confidence=0.99), menu,
                     triage_contract())
    assert out.policy_decision == "human"
    assert out.override == "warden_l3"


def test_frontier_consult_never_from_model():
    menu = dmenu.build_menu(level=2, frontier_allowed=True)  # legal for Warden
    out = dgate.gate(response("frontier_consult", confidence=0.99), menu,
                     triage_contract())
    assert out.model_recommendation == "frontier_consult"  # recorded
    assert out.policy_decision == "defer_warden"  # ...and refused
    assert out.override == "frontier_never_from_model"
    assert out.fallback == "warden_l2_only"


def test_duplicate_blocked_cannot_choose_same_retry():
    menu = dmenu.build_menu(level=1, blocked_action_ids=("think_retry",))
    # the id was omitted from the menu -> illegal path
    out = dgate.gate(response("think_retry", confidence=0.95), menu,
                     triage_contract())
    assert out.policy_decision == "human"
    assert out.override == "illegal_action"
    # and if the menu somehow still carries it, the explicit rule catches it
    wide = dmenu.ActionMenu(dmenu.BASE_ACTIONS + ("human",), level=1,
                            blocked_action_ids=("think_retry",))
    out2 = dgate.gate(response("think_retry", confidence=0.95), wide,
                      triage_contract())
    assert out2.policy_decision == "human"
    assert out2.override == "duplicate_blocked"


def test_human_always_legal_and_invalid_response_fails_conservative():
    menu = dmenu.build_menu(level=0)
    out = dgate.gate(response("human", confidence=0.0), menu,
                     triage_contract())
    assert out.policy_decision == "human" and out.override == ""
    broken = response("inspect")
    broken.choice_probs["next_step"] = {"inspect": 0.5}  # invalid
    out2 = dgate.gate(broken, menu, triage_contract())
    assert out2.policy_decision == "human"
    assert out2.override == "invalid_distribution"


def test_gate_never_raises_on_garbage():
    menu = dmenu.build_menu(level=1)
    out = dgate.gate(None, menu, triage_contract())
    assert out.policy_decision == "human"
