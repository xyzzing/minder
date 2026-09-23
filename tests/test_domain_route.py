"""Domain routing tests (Phase 1 P1.3): observe-only route proposals on
the existing decision gateway. The router proposes from the closed menu;
deterministic Minder validates and records; NOTHING is applied — task
contexts are never mutated by a router proposal, and every proposal
leaves a trace separating declared from router-proposed provenance."""
import os

from memory import db as _db, task_context
from decision import routing
from decision.contracts import domain_route_contract
from minder_op.cli import EXIT_OK, EXIT_USAGE, main


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _rows(dbp, sql, params=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def test_contract_shape_and_stable_hash():
    contract = domain_route_contract(routing.TRANSITION_SET + ("human",))
    assert contract.contract_id == "domain-route"
    assert contract.version == "v1"
    # the LAST choice question is the gate-primary action: transition
    from decision.types import ChoiceQuestion
    assert contract.questions[-1].id == "transition"
    assert contract.questions[-1].runtime_options is True
    assert contract.criteria_hash == domain_route_contract(
        routing.TRANSITION_SET + ("human",)).criteria_hash
    # frozen questions include the closed domain set
    domains = {q for q in contract.questions
               if q.id == "candidate_domain"}
    assert next(iter(domains)).options == routing.DOMAIN_SET


def test_rules_provider_with_declaration_stays_and_approves(tmp_path):
    dbp = _mig(tmp_path)
    task_context.declare_task("coding", task_id="t1", db_path=dbp)
    result = routing.assess_route(
        declared_domain="coding", task_id="t1", db_path=dbp,
        record=True)
    assert result["policy_transition"] == "stay"
    assert result["validation"] == "approved"
    assert result["abstained"] is False
    traces = _rows(dbp, "SELECT * FROM route_traces")
    assert len(traces) == 1
    assert traces[0]["provenance"] == "declared"
    assert traces[0]["validation_result"] == "approved"


def test_rules_provider_without_declaration_abstains(tmp_path):
    dbp = _mig(tmp_path)
    result = routing.assess_route(task_id="t1", db_path=dbp, record=True)
    assert result["abstained"] is True
    assert result["validation"] == "restricted"  # clarify, never apply
    traces = _rows(dbp, "SELECT * FROM route_traces")
    assert traces[0]["abstained"] == 1
    assert traces[0]["abstain_reason"]


def test_router_proposal_is_never_applied(tmp_path):
    """A confident fake 'switch' proposal is recorded as a shadow trace
    and mutates nothing: observe-only is a hard Phase 1 invariant."""
    from decision.providers.fake import FakeClient
    dbp = _mig(tmp_path)
    task_context.declare_task("coding", task_id="t1", db_path=dbp)

    def mass(option):
        return {o: (1.0 if o == option else 0.0)
                for o in routing.DOMAIN_SET}

    def transition_mass(option):
        options = routing.TRANSITION_SET + ("human",)
        return {o: (1.0 if o == option else 0.0) for o in options}

    fixtures = {"state1": {
        "candidate_domain_probs": mass("trading_research"),
        "intent_kind_probs": {o: (1.0 if o == "research_question"
                                  else 0.0)
                              for o in routing.INTENT_KINDS},
        "transition_probs": transition_mass("switch"),
        "confidence": 0.93,
    }}
    provider = FakeClient(fixtures, key_fn=lambda s: "state1")
    result = routing.assess_route(
        task_id="t1", db_path=dbp, record=True, provider=provider,
        state_key="state1")
    assert result["policy_transition"] == "switch"  # gate passed it
    assert result["validation"] == "shadow"          # and nothing applied
    ctx = task_context.task_status(task_id="t1", db_path=dbp)
    assert ctx["open"]["domain"] == "coding"         # unchanged
    transitions = _rows(dbp, "SELECT * FROM domain_transitions")
    assert transitions == []                         # no lifecycle change
    trace = _rows(dbp, "SELECT * FROM route_traces")[0]
    assert trace["provenance"] == "router_proposed"
    assert trace["validation_result"] == "shadow"
    assert trace["candidate_domain"] == "trading_research"


def test_low_confidence_proposal_restricted_to_clarify(tmp_path):
    from decision.providers.fake import FakeClient
    dbp = _mig(tmp_path)

    def mass(option, options):
        return {o: (1.0 if o == option else 0.0) for o in options}

    fixtures = {"s": {
        "candidate_domain_probs": mass("cited_research",
                                       routing.DOMAIN_SET),
        "intent_kind_probs": mass("unknown", routing.INTENT_KINDS),
        "transition_probs": mass("switch",
                                 routing.TRANSITION_SET + ("human",)),
        "confidence": 0.4,  # below the pinned 0.70 routing threshold
    }}
    result = routing.assess_route(
        db_path=dbp, record=True,
        provider=FakeClient(fixtures, key_fn=lambda s: "s"),
        state_key="s")
    assert result["decision"].policy_decision == "human"  # ask, don't act
    assert result["validation"] == "restricted"


def test_routes_cli_eval_and_ls(tmp_path, capsys):
    dbp = _mig(tmp_path)
    assert main(["--db", str(dbp), "routes", "eval", "--declared-domain",
                 "coding", "--task", "t1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "stay" in out and "approved" in out
    assert main(["--db", str(dbp), "routes", "eval",
                 "--task", "t2"]) == EXIT_OK  # no declaration -> abstain
    assert main(["--db", str(dbp), "routes", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "declared" in out and "router_proposed" in out
    assert main(["--db", str(dbp), "routes", "eval", "--declared-domain",
                 "astrology"]) == EXIT_USAGE


def test_assess_records_usage_ledger_event(tmp_path, monkeypatch):
    """P0.2 cost capture: every recorded assessment emits a
    decision_usage ledger event (tokens 0 for the local rules
    provider) — the baseline for Phase 2 cost comparisons."""
    import os
    dbp = _mig(tmp_path)
    minder_state = os.environ["MINDER_STATE_DIR"]
    before = _usage_events(minder_state)
    routing.assess_route(declared_domain="coding", task_id="t1",
                         db_path=dbp, record=True)
    after = _usage_events(minder_state)
    assert len(after) == len(before) + 1
    event = after[-1]
    assert event["event"] == "decision_usage"
    assert event["contract_id"] == "domain-route"
    assert event["provider"] == "rules-v1"
    assert event["prompt_tokens"] == 0  # local rules: no tokens


def _usage_events(state_dir):
    import json
    path = os.path.join(state_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()
                and json.loads(line).get("event") == "decision_usage"]
