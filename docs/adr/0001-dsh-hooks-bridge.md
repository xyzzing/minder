# ADR 0001: dsh native detection via the Claude-Code hooks bridge

Date: 2026-09-18 · Status: accepted

## Context
Minder needs to observe failing tool calls inside dsh (deepseek-harness) sessions.
Three options existed: (a) the official `@deepseek-ai/dsh-hooks-claude-code` bridge,
(b) a dependency-free native Cordis `.mjs` plugin on `tools/post-execute` waterfalls,
(c) proxy-tier message scanning only.

## Decision
Bridge plugin (a), failing closed to (c) when the plugin cannot install or load.
Config lives in `~/.dsh/settings.yaml` `plugins:` map (web profile) and per-profile
`cordis.patch.yml` insert entries (headless/other profiles); hooks.json ships from
minder with an absolute `configPath` (bridge resolves relative paths against launch cwd).

## Why
- The bridge's semantics are documented and source-verified: `PostToolUse` →
  block via exit 2 + stderr (model-visible), `additionalContext` on 4 events.
- (b) depends on partially-unverified per-event payload schemas on an alpha
  (0.1.x) that promises breaking changes; (c) loses tool_name/tool_input fidelity
  and the hard block.
- Reuses the exact hook core as the zcode tier (one script, two transports).

## Consequences
- One npm dependency inside the dsh profile (not minder's Python runtime).
- Bridge limits accepted: no `updatedInput`, no hard run-halt, serial hooks.
- Live hook-fire verification was blocked by a pre-existing dsh bug: headless
  tool execution dies (exit 2) with zero user patches on 0.1.5-rc.1. Composition
  is verified (`--dump-config`); firing must be confirmed in a web session.
