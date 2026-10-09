"""The difficulty router: turn a laya fast-decision opinion into an effort
band, or record precisely why it abstained (issue #28).

The proxy used to hold this logic inline, and every no-op returned the same
bare `None`. A live install running `difficulty_router: shadow` recorded
zero difficulty events across 635 router-eligible requests, which made an
inert router indistinguishable from a working one. So this module's whole
contract is that an abstention is a *named* event, never silence.

It owns the vocabulary (`ROUTER_MODES`, the skip reasons, the direction
names), the abstention recording, and the direction comparison. The proxy
keeps the request mechanics: what lands in the outgoing body, the spending
guardrail, the capability translation.

Fail-open by contract (Law #2): the router never raises at the caller. A
failure records `difficulty_error` with the exception type and returns
None, so the request proceeds on the deterministic scheduler.
"""
import minder


# `shadow` only records; `active` applies the band in both directions
# (issue #7's four-level ladder); `lower` applies it only when it cuts
# effort, so a local prior can save reasoning without ever inflating spend.
ROUTER_MODES = ("shadow", "active", "lower")
# Why the router had no opinion on a request it was enabled for. Each is a
# different defect: `no_client` is a wiring or config failure,
# `malformed_response` is the worker or the protocol, `below_confidence` is
# the floor working as designed.
ROUTER_SKIP_CLIENT = "client_effort"
ROUTER_SKIP_MARKER = "escalation_marker"
ROUTER_SKIP_NO_CLIENT = "no_client"
ROUTER_SKIP_CONFIDENCE = "below_confidence"
ROUTER_SKIP_MALFORMED = "malformed_response"
# Semantic effort scale the proxy translates onto each upstream's measured
# vocabulary. Kept here because the direction comparison needs it and the
# proxy must not re-derive it (C4).
KNOWN_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# The task text laya sees is truncated: the rubric grades required
# reasoning, not task length.
STATE_TASK_CHARS = 500


def router_mode(cfg):
    """The configured mode, or None when the router is off or misnamed."""
    mode = (cfg.get("difficulty_router") or "off").strip().lower()
    return mode if mode in ROUTER_MODES else None


def difficulty_state(req):
    """The task text laya sees: the first user message, redacted and
    truncated. Never the full conversation."""
    messages = req.get("messages") or []
    for m in messages:
        if m.get("role") == "user":
            try:
                from minder_memory.canonicalise import redact
            except ImportError:
                def redact(s):
                    return s
            return {"task": redact(str(m.get("content", "")))[:STATE_TASK_CHARS],
                    "turn": len(messages)}
    return {"task": "", "turn": len(messages)}


def _accepted_efforts(caps):
    return list(KNOWN_EFFORTS) + list((caps or {}).get("effort_levels") or [])


def client_effort_wins(client_effort, caps):
    """A client- or UI-declared effort outranks laya (issue #7 precedence).
    Honored only when it is a known semantic name or in the CAP-measured
    vocabulary; anything else is ignored and the router stays in play."""
    return client_effort in _accepted_efforts(caps)


def record_client_skipped(session_fp, cfg):
    """Record that an enabled router was outranked by a client effort. Silent
    when the router is off, so the off path stays event-free. `minder.log` is
    itself fail-open, so no blanket guard belongs here."""
    mode = router_mode(cfg)
    if mode is not None:
        minder.log(session_fp, "difficulty_skipped",
                   reason=ROUTER_SKIP_CLIENT, router=mode)


def band_direction(band, messages, effort_rank):
    """`down` | `up` | `same` — how the band's effort ranks against the
    deterministic scheduler's pick for this request. The baseline is the
    scheduler, not the preset: the router competes with the scheduler's
    choice, which is the value `lower` mode is allowed to cut."""
    import adapter
    try:
        want = adapter.schedule_effort(messages, minder.cfg())
        return effort_rank(band.get("effort"), want)
    except Exception:
        return effort_rank(None, None)  # the conservative "same"


def opinion(req, session_fp, cfg, level, client_effort, caps):
    """Laya fast decision layer: task difficulty prior (never a solver).

    Returns (label, band) for an opinion that should be applied, or None.
    None means no opinion — off, shadow-logged, outranked, declined by
    `lower` mode, or fail-open — and the caller falls through to
    X-Minder-Mode and the scheduler. Every None on an enabled router has
    already written its reason to the ledger.
    """
    try:
        mode = router_mode(cfg)
        if mode is None:
            return None
        if level:
            minder.log(session_fp, "difficulty_skipped",
                       reason=ROUTER_SKIP_MARKER, router=mode)
            return None
        if client_effort_wins(client_effort, caps):
            minder.log(session_fp, "difficulty_skipped",
                       reason=ROUTER_SKIP_CLIENT, router=mode)
            return None
        from minder_decision.contracts import task_difficulty_contract
        from minder_decision.difficulty import (confidence_floor, effort_rank,
                                                resolve_difficulty)
        client = _decision_client()
        if client is None:
            minder.log(session_fp, "difficulty_skipped",
                       reason=ROUTER_SKIP_NO_CLIENT, router=mode)
            return None
        contract = task_difficulty_contract()
        state = difficulty_state(req)
        response = client.system_one(state, contract.questions,
                                     contract=contract)
        resolved = resolve_difficulty(response, contract, cfg)
        if resolved is None:
            # the two abstentions are different defects: a low-confidence
            # prior is the floor working, a malformed reply is the worker
            # (or the protocol) broken
            low = (response is not None
                   and float(response.confidence or 0.0)
                   < confidence_floor(cfg))
            minder.log(session_fp, "difficulty_skipped",
                       reason=ROUTER_SKIP_CONFIDENCE if low
                       else ROUTER_SKIP_MALFORMED, router=mode)
            return None
        label, band = resolved
        direction = band_direction(band, req.get("messages"), effort_rank)
        if mode == "shadow":
            minder.log(session_fp, "difficulty_shadow", label=label,
                       score=response.score_values.get("difficulty_score"),
                       confidence=response.confidence, band=band["label"],
                       direction=direction)
            return None
        from minder_decision.difficulty import EFFORT_DOWN
        if mode == "lower" and direction != EFFORT_DOWN:
            # the operator authorized downgrades only in this mode
            minder.log(session_fp, "difficulty_no_upgrade", label=label,
                       band=band["label"], router=mode)
            return None
        return label, band
    except Exception as exc:
        # fail-open: the request proceeds unchanged (Law #2), but a
        # diagnostically-critical fail-open names the failure first.
        minder.log(session_fp, "difficulty_error", error=type(exc).__name__)
        return None


def _decision_client():
    """Late import so a missing or disabled decision layer is a routine
    None, not an import error at proxy start."""
    from minder_decision.client import get_difficulty_client
    return get_difficulty_client()
