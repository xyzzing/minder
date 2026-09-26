"""Replay (Phase 5.5 slice E): run JSON fixtures through state -> menu ->
contract -> client -> gate and check the expectations.

Each fixture carries `expected_primary`, `must_not_select` and
`must_require_human`. With FakeClient, ordinary cases must hit their
expected primary exactly. NullClient may be wrong on accuracy, but the
SAFETY property is asserted for every provider: an action omitted from
the menu (notably frontier_consult) is never selected.
"""
import json
from pathlib import Path

from . import contracts as dcontracts
from . import menu as dmenu
from . import policy_gate as dgate
from . import state as dstate
from .providers.fake import FakeClient
from .providers.null import NullClient

FIXTURE_GLOB = "*.json"


def load_fixtures(directory):
    paths = sorted(Path(directory).glob(FIXTURE_GLOB))
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def build_fake_client(fixtures):
    """FakeClient keyed by each fixture's failure_key."""
    mapping = {fx["state"]["failure_key"]: fx.get("fake") or {}
               for fx in fixtures}
    return FakeClient(mapping)


def build_menu(fixture):
    menu_spec = fixture.get("menu") or {}
    return dmenu.build_menu(
        level=menu_spec.get("level", 1),
        l3_fired=menu_spec.get("l3_fired", False),
        frontier_allowed=menu_spec.get("frontier_allowed", False),
        blocked_action_ids=menu_spec.get("blocked_action_ids", ()))


def run_fixture(fixture, client):
    menu = build_menu(fixture)
    state = dstate.build_state(
        goal=fixture["state"].get("goal", ""),
        failure_key=fixture["state"].get("failure_key", ""),
        count=fixture["state"].get("count", 0),
        excerpt=fixture["state"].get("excerpt", ""),
        available_action_ids=menu.action_ids)
    # the model may be shown ids beyond the menu (e.g. a misbehaving
    # client proposing frontier_consult) — the contract must be able to
    # express them so the gate can refuse them by rule
    extras = (fixture.get("menu") or {}).get("extra_model_options") or ()
    contract = dcontracts.failure_triage_contract(
        tuple(menu.action_ids) + tuple(extras))
    response = client.system_one(state, contract.questions, contract=contract)
    decision = dgate.gate(response, menu, contract)
    expect = fixture.get("expect") or {}
    forbidden = set(expect.get("must_not_select") or ())
    checks = {
        "expected_primary":
            decision.policy_decision == expect.get("expected_primary"),
        "must_not_select": decision.policy_decision not in forbidden,
        "must_require_human":
            (not expect.get("must_require_human"))
            or decision.policy_decision == "human",
    }
    return {"id": fixture.get("id"), "passed": all(checks.values()),
            "checks": checks,
            "model_recommendation": decision.model_recommendation,
            "policy_decision": decision.policy_decision,
            "override": decision.override}


def replay(directory, client=None):
    """Returns a summary dict; never raises on individual fixtures."""
    try:
        fixtures = load_fixtures(directory)
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "passed": 0, "results": [],
                "error": f"fixtures unavailable: {exc}"}
    client = client or build_fake_client(fixtures)
    results = [run_fixture(fx, client) for fx in fixtures]
    passed = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": passed, "results": results}


def safety_holds(directory, client=None):
    """The invariant that matters: nothing omitted from a menu is ever
    selected, for ANY provider (used with NullClient too)."""
    try:
        fixtures = load_fixtures(directory)
    except Exception:
        return False, []
    client = client or NullClient()
    violations = []
    for fx in fixtures:
        result = run_fixture(fx, client)
        forbidden = set((fx.get("expect") or {}).get("must_not_select") or ())
        if result["policy_decision"] in forbidden:
            violations.append(result["id"])
    return not violations, violations
