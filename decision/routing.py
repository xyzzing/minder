"""Domain routing (Phase 1 P1.3, PRD v2 §Domain routing contract).

Observe-only route proposals on the existing decision gateway. The
provider proposes (candidate_domain, intent_kind, transition) from
closed menus; `gate` disposes with routing-specific pinned thresholds;
the trace records declared-vs-proposed provenance and what deterministic
Minder decided. HARD Phase 1 invariant: nothing is applied — a proposal
never mutates a TaskContext, never widens egress, never changes a
verifier. `validation` says what the proposal is: `approved` (rules
provider confirming an explicit declaration), `restricted` (abstain →
clarify a human), or `shadow` (a model/router proposal recorded for
evaluation only).

Phase 1 providers are rules/null/fake — deterministic, no network, no
model. A Jev/Laya adapter slots in behind the same assess interface in
Phase 2, shadow-only, after ambiguity-rate and cost baselines exist.
"""
import uuid
from datetime import datetime, timezone

from memory import db as _db

from .contracts import (INTENT_KINDS, ROUTE_DOMAIN_SET, ROUTE_VERSION,
                        domain_route_contract)
from .menu import ActionMenu
from .policy_gate import gate
from .providers.fake import FakeClient
from .providers.null import NullClient

TRANSITION_SET = ("stay", "new_subtask", "switch", "uncertain")
DOMAIN_SET = ROUTE_DOMAIN_SET

# Pinned routing thresholds (PRD: no threshold assumed before
# measurement — these gate Phase 1's deterministic providers only; any
# classifier calibration lands in Phase 2 with routing-core-v1 evidence).
ROUTE_THRESHOLDS = {transition: (0.70, "ask_human_clarify")
                    for transition in TRANSITION_SET}
ROUTE_THRESHOLDS["human"] = (0.00, None)


class RulesRouteProvider:
    """Deterministic baseline: an explicit declaration proposes itself;
    without one the provider abstains (unknown + low confidence → the
    gate restricts to a human clarify). It never invents a switch."""

    model_version = "rules-v1"

    def system_one(self, state, questions, model=None, contract=None):
        declared = (state or {}).get("declared_domain")
        response = NullClient().system_one(state, questions)
        if declared:
            response.choice_probs["candidate_domain"] = _mass(
                declared, _options(questions, "candidate_domain"))
            response.choice_probs["intent_kind"] = _mass(
                "unknown", _options(questions, "intent_kind"))
            response.choice_probs["transition"] = _mass(
                "stay", _options(questions, "transition"))
            response.confidence = 0.90
        return response


def _options(questions, question_id):
    for question in questions:
        if question.id == question_id:
            return question.options
    return ()


def _mass(option, options):
    return {o: (1.0 if o == option else 0.0) for o in options}


def assess_route(*, declared_domain=None, task_id="default",
                 session_id=None, subject_changed=False, provider=None,
                 state_key=None, record=True, db_path=None):
    """One observe-only routing assessment. Returns
    {"policy_transition", "candidate_domain", "intent_kind",
    "abstained", "validation", "decision", "trace_id"}. Never raises."""
    try:
        state = {"declared_domain": declared_domain,
                 "subject_changed": bool(subject_changed),
                 "task_id": task_id}
        if declared_domain not in (None, "") and \
                declared_domain not in _route_domains():
            return {"policy_transition": "uncertain", "abstained": True,
                    "validation": "restricted",
                    "error": f"unknown declared domain: {declared_domain}",
                    "decision": None, "candidate_domain": None,
                    "intent_kind": None, "trace_id": None}
        menu = ActionMenu(TRANSITION_SET + ("human",))
        contract = domain_route_contract(menu.action_ids)
        provider = provider or RulesRouteProvider()
        response = provider.system_one(state, contract.questions,
                                       contract=contract)
        decision = gate(response, menu, contract,
                        thresholds=ROUTE_THRESHOLDS)
        candidate = response.top_choice("candidate_domain")
        intent = response.top_choice("intent_kind")
        # abstain: the provider had no real opinion (unknown candidate)
        # or the gate routed to a human clarify
        abstained = candidate in (None, "unknown") or \
            decision.policy_decision == "human" or \
            decision.override == "low_confidence"
        if decision.policy_decision == "human":
            validation = "restricted"     # clarify a human; never apply
        elif decision.model_recommendation and \
                decision.model_recommendation != "stay":
            validation = "shadow"         # observed proposal, not applied
        else:
            validation = "approved"       # deterministic declaration path
        abstain_reason = (decision.override
                          or ("routed_to_human_clarify"
                              if decision.policy_decision == "human"
                              else None)) if abstained else None
        trace_id = None
        if record:
            trace_id = _record(db_path=db_path, contract=contract,
                               session_id=session_id, task_id=task_id,
                               declared_domain=declared_domain,
                               candidate=candidate, intent=intent,
                               response=response, decision=decision,
                               abstained=abstained,
                               abstain_reason=abstain_reason,
                               validation=validation)
        return {"policy_transition": decision.policy_decision,
                "candidate_domain": candidate, "intent_kind": intent,
                "abstained": abstained, "validation": validation,
                "decision": decision, "trace_id": trace_id}
    except Exception as exc:  # noqa: BLE001 — routing fails safe
        return {"policy_transition": "uncertain", "abstained": True,
                "validation": "restricted",
                "error": f"{type(exc).__name__}: {exc}",
                "decision": None, "candidate_domain": None,
                "intent_kind": None, "trace_id": None}


def _route_domains():
    return ROUTE_DOMAIN_SET


def _record(*, db_path, contract, session_id, task_id, declared_domain,
            candidate, intent, response, decision, abstained,
            abstain_reason, validation):
    trace_id = f"rt_{uuid.uuid4().hex[:12]}"
    conn = _db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO route_traces (trace_id, ts, contract_id,"
            " contract_version, session_id, task_id, declared_domain,"
            " candidate_domain, transition, intent_kind, confidence,"
            " abstained, abstain_reason, provider, model_version,"
            " provenance, validation_result, latency_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?)",
            (trace_id, datetime.now(timezone.utc).isoformat(),
             contract.contract_id, contract.version, session_id, task_id,
             declared_domain, candidate, decision.policy_decision, intent,
             float(decision.confidence or 0.0), 1 if abstained else 0,
             abstain_reason,
             response.provider or "", response.model_version or "",
             "declared" if validation == "approved" else "router_proposed",
             validation, float(response.latency_ms or 0.0)))
        conn.execute("COMMIT")
        return trace_id
    except Exception:  # noqa: BLE001 — trace failure never blocks
        return None
    finally:
        conn.close()


# list_routes lives here so the CLI and the future console share one
# read model.
def list_routes(limit=25, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT trace_id, ts, declared_domain, candidate_domain,"
                " transition, intent_kind, confidence, abstained,"
                " abstain_reason, provider, provenance,"
                " validation_result FROM route_traces"
                " ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
