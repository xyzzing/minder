"""Replay tests (Phase 5.5 slice E). FakeClient must hit expected_primary
on every fixture; NullClient may be wrong on accuracy but must never
select an action omitted from the menu (frontier_consult above all)."""

from minder_decision import replay as dreplay
from minder_decision.providers.null import NullClient

FIXTURE_DIR = "minder_decision/evals/failure_triage"


def test_at_least_ten_fixtures_exist():
    fixtures = dreplay.load_fixtures(FIXTURE_DIR)
    assert len(fixtures) >= 10
    for fx in fixtures:
        assert set(fx["expect"]) >= {"expected_primary"}
        assert "must_not_select" in fx["expect"]


def test_fake_client_hits_expected_primary_everywhere():
    summary = dreplay.replay(FIXTURE_DIR)
    assert summary["total"] == len(summary["results"])
    assert summary["passed"] == summary["total"], summary["results"]
    for result in summary["results"]:
        assert result["checks"]["must_require_human"], result
        assert result["checks"]["must_not_select"], result


def test_frontier_never_selected_when_omitted_even_for_null():
    ok, violations = dreplay.safety_holds(FIXTURE_DIR, client=NullClient())
    assert ok, violations  # accuracy may fail; safety may not
