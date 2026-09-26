# 4. minder remains a harness-agnostic sidecar proxy, not a native harness plugin

Date: 2026-09-20

## Status

Accepted

## Context

minder's integrations ride public but external seams: an additive
OpenAI-compatible provider entry pointing at the proxy (ADR-0002) and a
Claude-Code-style hooks bridge (ADR-0001), each with documented workarounds
(loader file-entries, version-locked bridge packages, headless profiles that
emit no lifecycle events). A native harness plugin — as demonstrated by
`Yunado/dsh-qwen38-local-qol` (MIT), a polished dsh plugin using
`ctx.llm.registerAdapter`, `dsh.bundle.patch`, preset generation and a
settings tab for the same local-Qwen3.8-on-llama-server stack — avoids those
seams entirely. That repo prompted the question: should minder be rebuilt as
a dsh plugin?

## Decision

No. minder stays a standalone sidecar proxy that any OpenAI-compatible
harness can point at. From dsh-qwen38-local-qol we absorb **patterns, not
architecture**:

1. declare the CAP-measured effort vocabulary on the provider card
   (`reasoningEfforts`) so the harness UI is real, not hidden inside minder;
2. record token usage (incl. cached/reasoning tokens) in the ledger;
3. document coexistence: qol's `qwen38` preset routes straight to the model
   server and **bypasses minder's proxy** — users pick one route.

A harness plugin cannot host minder's core: the frontier panel makes outbound
consult calls the harness never issues, the ledger spans harnesses (dsh and
zcode write into one audit chain), and the escalation ladder governs the
model channel itself, not one harness's request stream. A plugin would bind
minder to a single alpha-stage harness (version-locked seams) and strand the
zcode half.

## Consequences

- Easier: minder works unchanged across harnesses and harness updates;
  harness-specific breakage stays confined to thin adapters (`hook.py`
  transports, `dsh/dsh_install.py`) and degrades honestly (ADR-0001's
  fail-closed stance).
- Easier: new harness support = point it at `:8390`; no plugin port.
- Harder: every dsh seam quirk (mounting, bridge version lock, headless
  event gaps) must be tracked by minder rather than inherited from a plugin
  ecosystem; the installer carries the compensating workarounds.
- Harder: effort/budget vocabulary surfaced to a harness UI must be
  duplicated per integration (provider card fields) instead of living once in
  a plugin API.
- Constraint: keep the proxy's wire contract plain OpenAI — no harness-only
  request fields may become load-bearing.
