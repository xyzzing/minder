"""Decision state + menu tests (Phase 5.5 slice B)."""
import json

from decision import menu as dmenu
from decision import state as dstate

SECRET = "sk-proj-quantumleek12345678"


def test_state_bounded_and_complete():
    state = dstate.build_state(
        goal="make pytest green", failure_key="bash|keyerror|sid|a.py",
        count=3, excerpt="KeyError: 'sid'",
        available_action_ids=("inspect", "human"),
        constraints={"l3_fired": False, "egress": "local",
                     "budget_left": True},
        fingerprints=["fp1", "fp2", "fp3", "fp4"],
        verification={"tests_passed": False, "episode_verified": True})
    assert len(state) <= dstate.MAX_STATE_CHARS
    payload = json.loads(state)
    for key in ("goal", "failure_key", "count", "excerpt",
                "available_action_ids", "constraints",
                "recent_action_fingerprints", "verification"):
        assert key in payload
    assert payload["count"] == 3
    assert payload["recent_action_fingerprints"] == ["fp2", "fp3", "fp4"]
    assert payload["constraints"]["l3_fired"] is False
    assert payload["verification"] == {"tests_passed": False,
                                       "episode_verified": True}


def test_state_strips_secrets_and_huge_excerpts():
    state = dstate.build_state(
        goal=f"fix it, key={SECRET}",
        excerpt=f"leak {SECRET} " + "x" * 5000)
    assert SECRET not in state
    assert len(state) <= dstate.MAX_STATE_CHARS
    payload = json.loads(state)
    assert len(payload["excerpt"]) <= dstate.EXCERPT_CAP


def test_state_over_limit_sheds_excerpt_first():
    state = dstate.build_state(
        goal="g" * 4000, failure_key="k|f|s|p",
        excerpt="e" * 3000,
        available_action_ids=("inspect", "human"))
    assert len(state) <= dstate.MAX_STATE_CHARS


def test_menu_omits_frontier_when_not_allowed():
    menu = dmenu.build_menu(level=1, frontier_allowed=False)
    assert "frontier_consult" not in menu
    assert "human" in menu
    full = dmenu.build_menu(level=2, frontier_allowed=True)
    assert "frontier_consult" in full


def test_menu_l3_is_human_only():
    for kwargs in ({"l3_fired": True}, {"level": 3}):
        menu = dmenu.build_menu(frontier_allowed=True, **kwargs)
        assert menu.action_ids == ("human",)
        assert menu.l3


def test_menu_rebuilds_after_new_event_blocks_retry():
    before = dmenu.build_menu(level=1, frontier_allowed=False)
    assert "think_retry" in before
    after = dmenu.build_menu(level=1, frontier_allowed=False,
                             blocked_action_ids=("think_retry",))
    assert "think_retry" not in after  # duplicate unchanged retry omitted
    assert "human" in after  # human survives every omission
    # even blocking human leaves the always-legal escape hatch
    extreme = dmenu.build_menu(blocked_action_ids=("human", "inspect",
                                                   "retrieve_lesson",
                                                   "think_retry",
                                                   "environment_check"))
    assert extreme.action_ids == ("human",)
