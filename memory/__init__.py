"""Minder memory v1 (docs/prd-memory-v1.md): canonical failure keys, a
durable episode/lesson store beside the JSONL ledger, the duplicate-action
guard, and frontier consult traces. Stdlib only.

Law for this package (plan §0 constraint 10): every entry point that can
touch disk or network fails open — callers (hook.py) must never crash
because memory is unavailable. The only deliberate hard edge is the
duplicate-action block itself, and only after a verified lesson exists or
the attempt threshold is met on an unchanged fingerprint.
"""
