"""Skill-select contract tests (Phase 5.5 slice F): shortlist from the
existing index (<= 3 + none), none/low-noul never load a body, at most
one body loaded. Hook unchanged — this is a library test."""
import json

from minder_decision import skill_select as dskill
from minder_decision.providers.fake import FakeClient
from minder_decision.providers.null import NullClient

FIXTURES = "minder_decision/evals/skill_select/fixtures.json"


def _load():
    with open(FIXTURES, encoding="utf-8") as handle:
        return json.load(handle)


def _shortlist(fx):
    if not fx.get("shortlist_from_index"):
        return ()
    return dskill.build_skill_shortlist({"error_excerpt": fx["event_text"]})


def test_shortlist_from_live_index_bounded_with_none():
    fx = _load()[0]
    shortlist = _shortlist(fx)
    assert shortlist and len(shortlist) <= dskill.LIMIT
    contract = dskill.skill_select_response_contract(shortlist)
    best = contract.questions[-1]
    assert best.id == "best_skill"
    assert "none" in best.options
    assert len(best.options) == len(shortlist) + 1


def test_fake_replay_hits_expected_skill_or_refuses():
    fixtures = _load()
    client = FakeClient({fx["failure_key"]: fx["fake"] for fx in fixtures})
    for fx in fixtures:
        shortlist = _shortlist(fx)
        contract = dskill.skill_select_response_contract(shortlist)
        response = client.system_one(
            json.dumps({"failure_key": fx["failure_key"]}),
            contract.questions, contract=contract)
        chosen = dskill.decide_skill(response, shortlist, contract)
        if fx["expect"]["load_body"]:
            assert chosen == fx["expect"]["expected_skill"], fx["id"]
            skill = dskill.load_selected_skill(chosen)
            assert skill and skill.get("instructions")  # one body, present
        else:
            assert chosen is None, fx["id"]


def test_null_client_never_selects():
    null = NullClient()
    shortlist = dskill.build_skill_shortlist(
        {"error_excerpt": "KeyError: 'supplier_id'"})
    contract = dskill.skill_select_response_contract(shortlist)
    response = null.system_one("{}", contract.questions, contract=contract)
    assert dskill.decide_skill(response, shortlist, contract) is None


def test_out_of_shortlist_choice_and_missing_body_refuse():
    shortlist = ("inspect-schema-boundary",)
    contract = dskill.skill_select_response_contract(shortlist)
    from minder_decision.types import DecisionResponse
    resp = DecisionResponse(
        contract_id=contract.contract_id, contract_version=contract.version,
        choice_probs={"best_skill": {"none": 0.0,
                                     "not-a-skill": 0.0,
                                     "inspect-schema-boundary": 0.0,
                                     "made-up-lora-skill": 1.0}},
        noul_probs={"any_skill_applies": 0.9}, confidence=0.95)
    assert dskill.decide_skill(resp, shortlist, contract) is None
    # invalid distribution refused too
    resp2 = DecisionResponse(
        contract_id=contract.contract_id, contract_version=contract.version,
        choice_probs={"best_skill": {"none": 0.5}},
        noul_probs={"any_skill_applies": 0.9}, confidence=0.9)
    assert dskill.decide_skill(resp2, shortlist, contract) is None
    # body missing -> no attach, no crash
    assert dskill.load_selected_skill("no-such-skill-in-index") is None
