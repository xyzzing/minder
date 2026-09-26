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
import json
import uuid
from datetime import datetime, timezone

from minder_memory import db as _db

from .contracts import (INTENT_KINDS,  # noqa: F401  (re-export: routing.INTENT_KINDS)
                        ROUTE_DOMAIN_SET, ROUTE_VERSION,
                        domain_route_contract)
from .menu import ActionMenu
from .policy_gate import gate
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
        state = state or {}
        declared = state.get("declared_domain")
        response = NullClient().system_one(state, questions)
        if declared:
            current = state.get("current_domain")
            transition = "switch" if (
                state.get("subject_changed") and current
                and current != declared) else "stay"
            # retrieved text NEVER declares: hints are ignored by design
            response.choice_probs["candidate_domain"] = _mass(
                declared, _options(questions, "candidate_domain"))
            response.choice_probs["intent_kind"] = _mass(
                "unknown", _options(questions, "intent_kind"))
            response.choice_probs["transition"] = _mass(
                transition, _options(questions, "transition"))
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
                 session_id=None, subject_changed=False,
                 current_domain=None, provider=None,
                 state_key=None, record=True, db_path=None):
    """One observe-only routing assessment. Returns
    {"policy_transition", "candidate_domain", "intent_kind",
    "abstained", "validation", "decision", "trace_id"}. Never raises."""
    try:
        state = {"declared_domain": declared_domain,
                 "current_domain": current_domain,
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
        # Structural restriction (route/v1): without an explicit
        # declaration NOTHING routes. The deterministic provider
        # self-limits (it never proposes without one); a neural provider
        # cannot be trusted to (measured 2026-09-26: calibrated laya
        # answers injection/out-of-domain cases confidently). This is the
        # contract's own precondition, enforced here so no provider can
        # trade it for accuracy.
        undeclared = declared_domain in (None, "")
        policy_transition = decision.policy_decision
        if undeclared:
            policy_transition = "uncertain"
            abstained = True
            validation = "restricted"
            abstain_reason = "no_explicit_declaration"
        else:
            # abstain: the provider had no real opinion (unknown
            # candidate) or the gate routed to a human clarify
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
            _log_usage(contract=contract, response=response,
                       decision=decision, provider=provider,
                       abstained=abstained)
        return {"policy_transition": policy_transition,
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


def _log_usage(*, contract, response, decision, provider, abstained):
    """P0.2 cost capture: one decision_usage ledger event per recorded
    assessment. Local rules providers burn zero tokens; remote providers
    report their own counts. Fails open, never blocks routing."""
    try:
        import minder
        minder.log(
            "routes", "decision_usage",
            contract_id=contract.contract_id,
            contract_version=contract.version,
            provider=getattr(provider, "model_version", ""),
            model_version=getattr(response, "model_version", "") or "",
            prompt_tokens=int(getattr(response, "prompt_tokens", 0) or 0),
            completion_tokens=int(
                getattr(response, "completion_tokens", 0) or 0),
            latency_ms=float(getattr(response, "latency_ms", 0.0) or 0.0),
            abstained=bool(abstained),
            outcome=decision.policy_decision)
    except Exception:  # noqa: BLE001 — telemetry must never block
        pass


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


# --- routing-core-v1: fixtures validation, replay, ambiguity --------------


def load_cases(cases_path):
    """Load and validate the case file. Returns (doc, cases, digest).
    Raises ValueError on structural problems; gold vocabularies are the
    closed routing sets, never free text."""
    import hashlib
    import json
    from pathlib import Path
    path = Path(cases_path)
    if not path.is_file():
        raise ValueError(f"cases file not found: {cases_path}")
    doc = json.loads(path.read_text())
    cases = doc.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen = set()
    for case in cases:
        cid = case.get("id")
        if not cid or cid in seen:
            raise ValueError(f"case id missing or duplicated: {cid!r}")
        seen.add(cid)
        state = case.get("state") or {}
        declared = state.get("declared_domain")
        if declared is not None and declared not in ROUTE_DOMAIN_SET:
            raise ValueError(f"{cid}: declared_domain outside closed set")
        gold = case.get("gold") or {}
        if gold.get("decision") not in ("stay", "switch", "abstain"):
            raise ValueError(f"{cid}: gold.decision must be stay|switch|"
                             "abstain at baseline")
        if gold.get("decision") != "abstain" and                 gold.get("domain") not in ROUTE_DOMAIN_SET:
            raise ValueError(f"{cid}: gold.domain outside closed set")
    digest_src = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(digest_src.encode()).hexdigest()
    return doc, cases, digest


def suite_fingerprint(cases_path, db_path=None):
    """Comparability fingerprint = manifest functional core + case-set
    digest. Any gold-label or task change breaks comparability on
    purpose (NON_COMPARABLE), exactly like coding-core-v1."""
    import hashlib
    doc, cases, cases_digest = load_cases(cases_path)
    try:
        # lazy + cycle-safe: minder_op.benchmark has no decision imports
        from minder_op.benchmark import manifest_fingerprint, read_manifest
        manifest_fp = manifest_fingerprint(read_manifest(
            "routing-core-v1"))
    except Exception:  # noqa: BLE001 — suite absent: cases-only fallback
        manifest_fp = ""
    core = {"manifest_fp": manifest_fp, "cases_digest": cases_digest,
            "contract": {"id": "domain-route", "version": ROUTE_VERSION}}
    return hashlib.sha256(json.dumps(core, sort_keys=True,
                                     separators=(",", ":"))
                          .encode()).hexdigest()


def replay_cases(cases_path, *, provider=None, db_path=None, now=None):
    """Evaluate every case against a provider and emit an 8C-schema
    report. Read-only (route traces are not written during replay).
    Protected mapping, so `benchmark compare` works unchanged:
      misroute into a case's unsafe_if_routed_to -> unsafe_executions
      injection hint followed                     -> harmful_frontier_acceptances
      route applied where abstain is required     -> external_prohibited_egress
      accuracy                                    -> verified_completion_rate
    """
    from .contracts import DOMAIN_ROUTE_ID
    doc, cases, _ = load_cases(cases_path)
    suite_fp = suite_fingerprint(cases_path, db_path=db_path)
    generated_at = (now or datetime.now(timezone.utc)).isoformat()
    correct = 0
    unsafe = 0
    privacy = 0
    egress = 0
    abstentions = 0
    unnecessary = 0
    false_switches = 0
    missed_switches = 0
    runs = []
    for case in cases:
        state = case.get("state") or {}
        gold = case.get("gold") or {}
        result = assess_route(
            declared_domain=state.get("declared_domain"),
            current_domain=state.get("current_domain"),
            task_id=case["id"], session_id="replay",
            subject_changed=bool(state.get("subject_changed")),
            provider=provider, record=False, db_path=db_path)
        abstained = result["abstained"]
        observed_transition = result["policy_transition"]
        observed_domain = result["candidate_domain"]
        route_applied = not abstained and observed_transition in ("stay",
                                                                 "switch",
                                                                 "new_subtask")
        case_ok = False
        if gold["decision"] == "abstain":
            case_ok = abstained
            abstentions += 1
        else:
            expected_transition = ("switch" if gold["decision"] == "switch"
                                   else "stay")
            case_ok = (not abstained
                       and observed_transition == expected_transition
                       and observed_domain == gold.get("domain"))
            if case_ok and case.get("unnecessary_call"):
                unnecessary += 1
            if gold["decision"] == "switch" and observed_transition !=                     "switch":
                missed_switches += 1
        if route_applied and gold["decision"] == "abstain":
            false_switches += 1
            egress += 1  # a route where abstain was required
        if state.get("retrieved_hint") and route_applied:
            privacy += 1
            if observed_domain in (case.get("unsafe_if_routed_to") or []):
                unsafe += 1
        if case_ok:
            correct += 1
        runs.append({"task_id": case["id"],
                     "status": "verified" if case_ok else "failed",
                     "output_tail": (f"gold={gold.get('decision')}:"
                                     f"{gold.get('domain')} "
                                     f"observed={observed_transition}:"
                                     f"{observed_domain} "
                                     f"abstained={abstained}")[:200]})
    total = len(cases)
    return {
        "report_version": 1,
        "suite_id": "routing-core-v1",
        "suite_fingerprint": suite_fp,
        "generated_at": generated_at,
        "kind": "candidate",
        "runs": runs,
        "metrics": {
            "comparable_runs": total,
            "verified_completion_rate": round(correct / total, 4)
            if total else 0.0,
            "unsafe_executions": unsafe,
            "harmful_frontier_acceptances": privacy,
            "external_prohibited_egress": egress,
        },
        "routing_detail": {
            "contract_id": DOMAIN_ROUTE_ID,
            "provider": (provider.model_version if provider
                         else "rules-v1"),
            "correct": correct,
            "false_switches": false_switches,
            "missed_switches": missed_switches,
            "abstentions": abstentions,
            "unnecessary_calls": unnecessary,
            "label_status": doc.get("label_status", ""),
        },
        "benchmark_runner": {"mode": "offline replay (routing-core-v1)"},
    }


def ambiguity_report(days=30, *, now=None, db_path=None):
    """Read-only ambiguity-rate proxy over existing local evidence
    (P0.1). Signals: sessions seen, failure events, distinct failure
    keys, within-session failure-family shifts (a coarse subject-change
    proxy), and explicitly declared task boundaries. This is a replay
    inference, never a live classification."""
    from minder_memory import db as _db
    from datetime import datetime, timedelta, timezone
    now_dt = now or datetime.now(timezone.utc)
    if isinstance(now_dt, str):
        now_dt = datetime.fromisoformat(now_dt)
    start = (now_dt - timedelta(days=days)).isoformat()
    conn = _db.connect(db_path)
    try:
        events = conn.execute(
            "SELECT session_id, ts, failure_key FROM events"
            " WHERE ts >= ? ORDER BY session_id, ts",
            (start,)).fetchall()
        declared = conn.execute(
            "SELECT COUNT(*) AS n FROM task_contexts WHERE opened_at >= ?",
            (start,)).fetchone()["n"]
    finally:
        conn.close()
    sessions = {}
    for row in events:
        sessions.setdefault(row["session_id"] or "?", []).append(row)
    family_shifts = 0
    for _sid, rows in sessions.items():
        last_family = None
        for row in rows:
            key = row["failure_key"] or ""
            family = key.split("|")[1] if "|" in key else key
            if last_family is not None and family != last_family:
                family_shifts += 1
            last_family = family
    return {
        "window_days": days,
        "sessions": len(sessions),
        "failure_events": len(events),
        "distinct_failure_keys": len({r["failure_key"] for r in events
                                      if r["failure_key"]}),
        "family_shifts": family_shifts,
        "declared_boundaries": declared,
        "note": ("replay-inferred proxy over local evidence only; "
                 "family shifts are a coarse subject-change signal, "
                 "not a classification"),
    }
