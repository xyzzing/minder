"""Deterministic egress assessment (docs/minder-phase-4-7-frontier-coding.md
P6.2) plus the shared MINDER_ASSIST flag parser (P6.1).

assess_egress never sends anything itself — the Warden / frontier.py remain
the source of whether a consult happens. A classifier's egress_risk=block is
ADVISORY: it can only escalate to a deny when MINDER_ASSIST=shadow_suggest
AND confidence >= 0.9, and it is always logged. Deterministic; never raises
(unknown states fail toward redact, not deny).
"""
import os

import minder
from . import canonicalise as canon
from .frontier_redaction import EXTERNAL_PROHIBITED, INTERNAL_CODE_DEFAULT

ALLOW = "allow"
REDACT = "redact"
DENY = "deny"

ASSIST_MODES = ("off", "retrieve", "block_duplicate_skill", "shadow_suggest",
                "decision_skill")

# P6.1 shadow_suggest digest line: only high-confidence environment /
# permissions suggestions, advisory wording, never a Warden action change
SHADOW_SUGGEST_MIN_CONFIDENCE = 0.85
SHADOW_SUGGEST_CLASSES = ("environment", "permissions")

# P6.2 rule 4: a classifier alone may deny only under shadow_suggest >= 0.9
CLASSIFIER_EGRESS_DENY_CONFIDENCE = 0.9


def assist_mode():
    """MINDER_ASSIST, validated against the allowed modes; default 'off'.
    Never raises."""
    try:
        mode = (os.environ.get("MINDER_ASSIST") or "off").strip().lower()
        return mode if mode in ASSIST_MODES else "off"
    except Exception:
        return "off"


def assess_egress(event, classification=None, mode=None):
    """allow | redact | deny. Rules, in order:
    1. external-prohibited profile or untrusted content present -> deny;
    2. canonicalise detects secret-shaped content in the excerpt -> deny;
    3. otherwise redact when an internal profile demands scrubbing, allow
       when nothing is configured.
    A classification with egress_risk=block is logged, and denies only
    under MINDER_ASSIST=shadow_suggest with confidence >= 0.9."""
    try:
        event = event or {}
        if mode is None:
            mode = assist_mode()
        profile = str(event.get("redaction_profile") or "")
        excerpt = str(event.get("error_excerpt") or "")
        if profile == EXTERNAL_PROHIBITED or event.get(
                "untrusted_content_present"):
            _log(event, "egress_deny", why="external-prohibited/untrusted")
            return DENY
        if canon.redact(excerpt) != excerpt:
            _log(event, "egress_deny", why="secret-like content in excerpt")
            return DENY
        if _classifier_block(classification, mode, event):
            return DENY
        if profile == INTERNAL_CODE_DEFAULT:
            return REDACT
        return ALLOW
    except Exception:
        return REDACT


def _field(classification, name, default=None):
    if isinstance(classification, dict):
        return classification.get(name, default)
    return getattr(classification, name, default)


def _classifier_block(classification, mode, event):
    try:
        if not classification:
            return False
        if _field(classification, "egress_risk") != "block":
            return False
        try:
            confidence = float(_field(classification, "confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        assisted = (mode == "shadow_suggest"
                    and confidence >= CLASSIFIER_EGRESS_DENY_CONFIDENCE)
        _log(event, "egress_classifier_block", confidence=confidence,
             enforced=assisted)
        return assisted
    except Exception:
        return False


def _log(event, name, **kw):
    try:
        task = (event.get("task_id") or event.get("key") or "egress")
        minder.log(task, name, **kw)
    except Exception:
        pass
