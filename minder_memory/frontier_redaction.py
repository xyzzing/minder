"""Deterministic redaction profiles for frontier consult traces
(docs/minder-phase-4-7-frontier-coding.md P4.1).

Two profiles:
- internal-code-default: strip API-key-shaped strings (reusing
  memory.canonicalise.redact), emails and bearer tokens; keep error
  excerpts, failure keys, relative paths and test names; strip payload
  bodies larger than MAX_BODY_CHARS.
- external-prohibited: nothing response-derived is stored at all — hashes
  only (enforced by frontier_traces.record_consult / classify_consult).

Pure functions, deterministic, never raise.
"""
import re

from . import canonicalise as canon

INTERNAL_CODE_DEFAULT = "internal-code-default"
EXTERNAL_PROHIBITED = "external-prohibited"
PROFILES = (INTERNAL_CODE_DEFAULT, EXTERNAL_PROHIBITED)

MAX_BODY_CHARS = 4096  # spec: strip payload bodies larger than 4 KiB

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_BEARER_RE = re.compile(
    r"\b(?:bearer|token)\s+[A-Za-z0-9._~+/-]{8,}", re.IGNORECASE)


def _truncate(text):
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[:MAX_BODY_CHARS] + "\n...[truncated]"


def redact_for_profile(text, profile=INTERNAL_CODE_DEFAULT):
    """Deterministic redaction for storage under a profile. Never raises;
    on any internal problem returns the empty string (safe direction)."""
    try:
        out = canon.redact(text)
        out = _BEARER_RE.sub("bearer ***REDACTED***", out)
        out = _EMAIL_RE.sub("***REDACTED***", out)
        return _truncate(out)
    except Exception:
        return ""


def allows_response_text(profile):
    """external-prohibited refuses response-derived text in storage."""
    return profile != EXTERNAL_PROHIBITED
