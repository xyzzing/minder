# ADR 0002: additive dsh provider instead of baseURL cutover

Date: 2026-09-18 · Status: accepted

## Context
Pointing dsh at minder's Turnstile proxy required choosing between mutating the
user's existing `local` provider (baseURL cutover; every session flows through
the proxy) and adding a separate provider.

## Decision
Additive only: a new `minder` provider (baseURL `http://127.0.0.1:8390/v1`) with
model cards `qwen-exec`, `qwen-think`, `frontier`. The existing `local` provider,
`agent-default-model`, and all other settings are byte-preserved (verified by
round-trip tests). Rollback deletes one block.

## Why
- Zero breakage risk for a working setup; the watchdog's detection tier (hooks)
  is provider-agnostic and still covers sessions on the original provider.
- The PRD's fail-closed/backup-verify posture extends naturally: the patcher
  verifies post-edit (PyYAML when present, structural scan otherwise) and rolls
  back automatically.

## Consequences
- Mode switching requires picking a minder model in dsh's picker once per session.
- Full-cutover remains available later by editing one baseURL line.
