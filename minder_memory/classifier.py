"""Event classifier protocol + Null/Shadow implementations
(docs/minder-phase-4-7-frontier-coding.md P5.1).

A classifier is LOG-ONLY: it never changes policy, has no authority over
L2/L3/egress, and the hook must not block on it. compact_event() is a
7-key whitelist; the excerpt is redacted and truncated to
MAX_EXCERPT_CHARS before any classifier sees it — never the full repo,
full logs, or frontier prompts.

- NullClassifier: no opinion (unknown / inspect / safe / 0.0 / "null").
  Used when no real classifier is available.
- ShadowClassifier: wraps an inner classifier, writes a classifier_shadow
  row per call, returns the inner result untouched. An inner crash
  degrades to a Null-style result plus a degraded row — never raises.
"""
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db as _db
from .canonicalise import redact

MAX_EXCERPT_CHARS = 500

COMPACT_KEYS = ("tool", "exit_code", "failure_key", "error_excerpt",
                "same_failure_count", "previous_route",
                "untrusted_content_present")

FAILURE_CLASSES = ("code_logic", "schema_contract", "environment",
                   "permissions", "test_expectation", "unknown")
RECOMMENDED_ACTIONS = ("inspect", "retrieve_memory", "think_retry",
                       "environment_check", "block_duplicate",
                       "escalate_candidate")
EGRESS_RISKS = ("safe", "uncertain", "block")


@dataclass
class Classification:
    failure_class: str = "unknown"
    recommended_action: str = "inspect"
    egress_risk: str = "safe"
    confidence: float = 0.0
    model_version: str = "null"


def _now():
    return datetime.now(timezone.utc).isoformat()


def compact_event(event):
    """Whitelist + redact + truncate to the classifier-visible shape.
    Never raises."""
    try:
        out = {key: event.get(key) for key in COMPACT_KEYS}
        out["error_excerpt"] = redact(
            str(event.get("error_excerpt") or ""))[:MAX_EXCERPT_CHARS]
        out["untrusted_content_present"] = bool(
            event.get("untrusted_content_present"))
        return out
    except Exception:
        return {key: None for key in COMPACT_KEYS}


class EventClassifier:
    """Protocol: classify one compact event, returning a Classification."""

    def classify(self, compact_event):
        raise NotImplementedError


class NullClassifier(EventClassifier):
    """Deterministic fallback: has no opinion, never raises."""

    def classify(self, compact_event):
        return Classification()


class ShadowClassifier(EventClassifier):
    """Wraps an inner classifier; writes a classifier_shadow row and
    returns the inner result WITHOUT affecting policy. The event is
    normalised (whitelist / redact / truncate) before the inner model
    sees it."""

    def __init__(self, inner=None, db_path=None, model_version=None):
        self._inner = inner
        self._db_path = db_path
        self._model_version = (model_version
                               or getattr(inner, "model_version", None)
                               or type(inner).__name__)

    def classify(self, event, policy_action=None, event_id=None):
        compact = compact_event(event)
        try:
            out = self._inner.classify(compact)
            if not isinstance(out, Classification):
                raise ValueError("inner did not return a Classification")
        except Exception:
            out = Classification(model_version="degraded")
        try:
            self._log(compact, out, policy_action, event_id)
        except Exception:
            pass
        return out

    def _log(self, compact, out, policy_action, event_id):
        conn = _db.connect(self._db_path)
        try:
            _db.write(conn, "INSERT INTO classifier_shadow (id, ts,"
                      " event_id, failure_key, failure_class,"
                      " recommended_action, egress_risk, confidence,"
                      " model_version, policy_action)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (f"cls_{uuid.uuid4().hex[:12]}", _now(), event_id,
                       compact.get("failure_key"), out.failure_class,
                       out.recommended_action, out.egress_risk,
                       float(out.confidence),
                       out.model_version or self._model_version,
                       policy_action))
        finally:
            conn.close()


# Phase 5.5: optional GatewayClassifier wraps decision.SystemOneClient


class GatewayClassifier(EventClassifier):
    """Maps a compact_event onto failure_kind + next_step through a
    decision.SystemOneClient (Phase 5.5). Still shadow-only — no policy
    authority; the gateway menu never contains frontier_consult, and any
    problem degrades to a Null-style Classification."""

    model_version = "gateway"

    def __init__(self, client=None):
        self._client = client

    def classify(self, compact_event):
        try:
            from minder_decision import contracts as dcontracts
            from minder_decision import menu as dmenu
            from minder_decision import policy_gate as dgate
            if self._client is None:
                raise ValueError("no SystemOneClient configured")
            contract = dcontracts.failure_triage_contract(
                dmenu.BASE_ACTIONS + ("frontier_consult",))
            response = self._client.system_one(compact_event,
                                               contract.questions)
            menu = dmenu.build_menu(frontier_allowed=False)
            decision = dgate.gate(response, menu, contract)
            action = decision.policy_decision
            if action not in RECOMMENDED_ACTIONS:
                action = "inspect"
            return Classification(
                failure_class=decision.failure_kind,
                recommended_action=action,
                egress_risk="safe",
                confidence=float(response.confidence),
                model_version=f"gateway/{response.model_version}")
        except Exception:
            return Classification(model_version="gateway-degraded")


def get_gateway_classifier():
    """A GatewayClassifier over the configured decision client (Phase 5.5),
    or None. Optional, never authoritative, never required by tests."""
    try:
        from minder_decision.client import get_decision_client
        client = get_decision_client()
        if client is None:
            return None
        return GatewayClassifier(client)
    except Exception:
        return None


def get_classifier(db_path=None):
    """MINDER_CLASSIFIER=shadow -> a ShadowClassifier (inner: the laya
    adapter when importable, else None → Null-style labels still logged).
    Unset or any other value -> None: no classify, zero behaviour change.
    Never raises."""
    try:
        mode = (os.environ.get("MINDER_CLASSIFIER") or "").strip().lower()
        if mode != "shadow":
            return None
        inner = None
        try:
            from . import classifier_laya
            inner = classifier_laya.try_laya_classifier()
        except Exception:
            inner = None
        return ShadowClassifier(inner, db_path=db_path)
    except Exception:
        return None
