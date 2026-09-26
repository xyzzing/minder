"""Minder Trace Review — offline, post-run evaluation of completed DSH
sessions (docs/trace-review/prd-trace-review.md).

Deterministic and stdlib-only. This package never writes to DSH, never
calls a model, and never raises out of its public functions: every
failure becomes a status string in a report (the memory-plane fail-open
law).

Reading DSH's session logs is NOT reimplemented here — `minder_op
.dsh_sessions` already owns zstd decompression, session discovery and the
projection-cache join, and this package delegates to it.
"""

__all__ = ["normalize", "evaluate"]
