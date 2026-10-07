"""minder_core — the reusable, dependency-free heart of minder.

Everything here is Python 3.10+ STDLIB ONLY and imports nothing from the
rest of minder (no minder module, no minder_memory/minder_decision): a
pure library surface other projects can lift or cite without installing
the governed runtime. The parent repo's modules re-export these names at
their historical locations, so internal callers are unaffected.

Contents:
- identity    canonical failure keys, action fingerprints, secret
              redaction, and result signatures (loop identity)
- comparator  the protected-metric benchmark comparator (safety
              regressions fail at any sample size; completion drops are
              bounded; fingerprint mismatch = NON_COMPARABLE)
- verification  test/lint runner recognition and clean-run judgement — the
              local evidence minder's lesson gate requires
- panel_text  the frontier panel's prompts and the rules for reading an
              answer back (action vs cause vs attribution). Not re-exported
              here: it is cited by module path, and `__all__` stays the
              runtime's contract surface
"""

from .comparator import (MAX_COMPLETION_DROP, MIN_COMPARABLE_RUNS,
                         VERDICT_FAIL, VERDICT_INSUFFICIENT,
                         VERDICT_NON_COMPARABLE, VERDICT_PASS,
                         compare_reports)
from .identity import (action_fingerprint, canonical_action, error_family,
                       failure_key, normalize_output, redact, relpath_of,
                       result_signature, same_failure, symbol_or_test_id,
                       unchanged_retry)
from .verification import run_is_clean, test_runner

__all__ = (
    "action_fingerprint", "canonical_action", "compare_reports",
    "error_family", "failure_key", "MAX_COMPLETION_DROP",
    "MIN_COMPARABLE_RUNS", "normalize_output", "redact", "relpath_of",
    "result_signature", "run_is_clean", "same_failure", "symbol_or_test_id",
    "test_runner", "unchanged_retry", "VERDICT_FAIL", "VERDICT_INSUFFICIENT",
    "VERDICT_NON_COMPARABLE", "VERDICT_PASS",
)
