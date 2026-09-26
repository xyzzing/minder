# ADR 0003: digest-marker-in-recent-window escalation channel

Date: 2026-09-18 · Status: accepted

## Context
The PRD leaves the hook→proxy escalation channel implicit: the warden decides
"think now", but the proxy forwards what the harness asked for. A session-header
channel (X-Minder-Session) would couple hook and proxy state.

## Decision
The digest text itself is the signal: the Turnstile proxy upgrades a request to
think-preset params (via the Mode Adapter) iff a `[minder] ESCALATION L1|L2`
marker appears in the last 4 messages; otherwise the requested alias preset
applies. Unknown model strings pass untouched (PRD §6.8.1). Under
`mechanism: none`, think params still apply and the adapter no-ops (§6.8.2).

## Why
- Stateless: no hook↔proxy session coupling, no fingerprint mapping, no new
  headers; works identically for zcode and dsh.
- Deterministic and unit-testable (pure function over the parsed request, §7).
- De-escalation is automatic: once the digest ages out of the recent window,
  exec params resume.

## Consequences
- The recent-window constant (4) is a tuning knob; digests persisting longer in
  context than the window end the upgrade early.
- Digests must keep the grep-able marker prefix — enforced by the shared
  digest templates in minder.py.
