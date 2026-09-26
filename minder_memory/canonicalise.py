"""Canonical failure keys and action fingerprints (docs/prd-memory-v1.md
PR 1). Pure functions — no I/O, no minder imports, safe to call from any
transport.

The implementation lives in minder_core.identity (the dependency-free,
extractable surface — see minder_core/__init__.py); this module is the
historical import location and re-exports it unchanged. New code may
import from either; the two are the same objects.
"""
from minder_core.identity import (  # noqa: F401  (re-export surface)
    _REDACTED,
    action_fingerprint,
    canonical_action,
    error_family,
    failure_key,
    normalise_error_excerpt,
    redact,
    relpath_of,
    same_failure,
    symbol_or_test_id,
    unchanged_retry,
)
