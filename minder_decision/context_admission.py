"""The context-admission gate for lesson injection (issue #100).

The decision layer applied to memory instead of requests: when the
retrieve path has a verified lesson to attach to the Warden digest,
this gate decides whether that lesson should enter the agent's context
at all. The model never edits the digest and never writes a lesson; it
returns a typed admit/decline with a calibrated confidence and this
module maps that onto attach/skip. Everything fails open to today's
behavior: off and shadow never narrow the digest, and in gate mode only
a validated, above-floor decline skips the line.

Modes (config key `context_admission`): `off` — default, byte-identical,
the worker is never dialed; `shadow` — every opinion ledgered, the
lesson attaches regardless; `gate` — a decline passes the digest
through untouched and records `admission_declined` in the injection
ledger so the deliveries/misses split (issue #87) counts it with its
reason instead of silently dropping it.
"""
import minder

ADMIT = "admit"
DECLINE = "decline"

REASON_OFF = "off"
REASON_OPINION = "opinion"
REASON_NO_OPINION = "no_opinion"
REASON_BELOW_CONFIDENCE = "below_confidence"
REASON_MALFORMED = "malformed_response"
REASON_CLIENT_ERROR = "client_error"


def admission_mode(cfg):
    """`shadow` or `gate`; None for off, absent, or unparseable — the
    gate then takes no code path past this check (the injection-guard
    law: a disabled surface is byte-identical, not merely inert)."""
    mode = (cfg or {}).get("context_admission")
    return mode if mode in ("shadow", "gate") else None


def _decision_client():
    """Late import so a missing or disabled decision layer is a routine
    None, not an import error at hook load (the router's law)."""
    from minder_decision.client import get_difficulty_client
    return get_difficulty_client()


def admission_state(ev, fkey, count, lesson):
    """The redacted state the worker sees. The compact lesson payload
    retrieval already built is the redaction boundary — instruction,
    anti-pattern, and verification summary travel; raw tool logs never
    do."""
    return {
        "failure_key": fkey,
        "attempts": count,
        "repo": ev.get("repo") or "",
        "tier": lesson.get("tier") or "",
        "lesson": {k: lesson.get(k) for k in
                   ("instruction", "anti_pattern", "verification")},
    }


def _resolve(response, contract, cfg):
    """(choice, confidence, reason); choice is None whenever the
    response cannot ground a decision. The floor is the router's
    (`laya_min_confidence`): one calibrated scale, one knob."""
    from .difficulty import confidence_floor
    try:
        if response is None or contract is None:
            return None, 0.0, REASON_NO_OPINION
        response.validate(contract)
    except Exception:  # noqa: BLE001 — any malformed reply is fail-open
        return None, 0.0, REASON_MALFORMED
    confidence = float(response.confidence or 0.0)
    if confidence < confidence_floor(cfg):
        return None, confidence, REASON_BELOW_CONFIDENCE
    from .contracts import ADMISSION_OPTIONS
    choice = response.top_choice("admit")
    if choice not in ADMISSION_OPTIONS:
        return None, confidence, REASON_MALFORMED
    return choice, confidence, REASON_OPINION


def opinion(ev, fkey, count, lesson, cfg, client=None):
    """(decision, reason); decision is `admit` or `decline`. A decline
    requires gate mode AND a validated above-floor response saying so —
    every other outcome, including every failure, is admit. Never
    raises: a broken gate must not break the retrieve path it sits in.
    Every shadow/gate call writes one `context_admission` ledger event
    (decision, reason, mode, tier, lesson, attempts, confidence)."""
    session = ev.get("session_id") or ev.get("task_id") or ""
    try:
        mode = admission_mode(cfg)
        if mode is None:
            return ADMIT, REASON_OFF
        if client is None:
            client = _decision_client()
        if client is None:
            minder.log(session, "context_admission", decision=ADMIT,
                       reason=REASON_NO_OPINION, mode=mode,
                       tier=lesson.get("tier") or "",
                       lesson_id=lesson.get("lesson_id"), attempts=count)
            return ADMIT, REASON_NO_OPINION
        from .contracts import context_admission_contract
        contract = context_admission_contract()
        response = client.system_one(
            admission_state(ev, fkey, count, lesson),
            contract.questions, contract=contract)
        choice, confidence, reason = _resolve(response, contract, cfg)
        decision = DECLINE if (mode == "gate" and choice == DECLINE) \
            else ADMIT
        # opinion is what the model said, decision is what was applied -
        # they differ exactly when shadow records a dissent (the
        # policy_gate's model_recommendation/policy_decision shape)
        minder.log(session, "context_admission", decision=decision,
                   opinion=choice, reason=reason, mode=mode,
                   tier=lesson.get("tier") or "",
                   lesson_id=lesson.get("lesson_id"), attempts=count,
                   confidence=round(confidence, 3))
        return decision, reason
    except Exception:  # noqa: BLE001 — fail-open names itself
        minder.log(session, "context_admission", decision=ADMIT,
                   reason=REASON_CLIENT_ERROR, mode="unknown",
                   lesson_id=(lesson or {}).get("lesson_id"))
        return ADMIT, REASON_CLIENT_ERROR
