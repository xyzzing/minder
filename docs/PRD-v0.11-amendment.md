# minder PRD v0.11 amendment — the security and operations planes

Status: draft for maintainer review. Lands as PRD v0.11 additions; existing
v0.10 requirements unchanged. Two new planes join the supervision plane.
Naming: minder stays; role = the tower (the keeper whose discipline is that
authority stays human).

## Plane 3: security enforcement (posture: shadow-first, human-gated)

The tower owns enforcement because it owns the trust boundary. Posture for
every detector: shadow (records only) -> human-gated canary -> inline-block.
No automatic escalation path exists by owner decision (2026-10-31, PR #67
comment).

- FR-S1. Request-side injection guard (SHIPPED, #67): every proxied request
  scored by an isolated worker; `injection_shadow` events; requests never
  modified; off = byte-identical.
- FR-S2. Fail-closed hook option (SHIPPED, #68): `MINDER_HOOK_FAIL_MODE=closed`
  blocks on outer-guard failure; inner detection fail-opens stay
  allow-by-design (availability inversion).
- FR-S3. Response-side shadow scan: tool results entering context from
  upstream responses get the same worker treatment (second opinion slot);
  closes the indirect-injection path.
- FR-S4. Egress redaction: `frontier.py` cloud-bound requests pass a
  redaction filter (redact-before-egress; presidio).
- FR-S5. E-stop: one command sets deny-all hooks + shadow-only proxy + engine
  hold, with the reason journaled; reversible the same way. Incident
  containment.
- FR-S6. Security alerting: daily digest + immediate notify on high-signal
  events (high-confidence shadow verdicts, tamper rows, blocked actions);
  extends the blindness-watchdog pattern.
- FR-S7. Supply-chain doctor checks: mcp-scan on MCP configs; guarddog at
  skill/plugin install.
- FR-S8. Serve-mode trio + toolcall-guard contract: egress, out-of-workspace
  writes, push become gated surfaces; the toolcall contract follows the
  room-contract shape.
- FR-S9. Instrument calibration cadence (#59): detectors and graders
  re-validate on a schedule; security rooms re-run on it.

## Plane 4: operations (the factory around the loop)

- FR-O1. Work-order contract (KEystone): at task intake, record task +
  acceptance criterion + room family (schema owned by windtunnel, runtime
  here). Attempts grade against the contract. Simultaneously the quality plan
  (process engineering) and the reward definition (loop engineering).
- FR-O2. First-pass-yield surface: attempts-to-success distribution per
  contract and family, from ledger data. The process vital sign.
- FR-O3. Goodhart monitor: contract-pass trend vs rework/override trend;
  divergence is an alarm.
- FR-O4. Context-rot metrics: lessons injected per request, memory token
  share, retrieval hit-rate trend.
- FR-O5. Approval inbox: one console surface for pending lessons, canary
  decisions, policy changes; every action attributed.
- FR-O6. Escalation bundles: on loop exhaustion, emit gap + attempts + lessons
  consulted + failing evidence as a work-order closing artifact.
- FR-O7. Delegation capture (#71): headless subagent events reach the ledger
  (owner-prioritized P0).

## Declared objective

Cost-per-correct at a fixed quality floor. The program scalar everything else
reports into. Decision, not code.

## Non-goals (unchanged)

No training in minder; no vector-by-default; no auto-promotion; no reward
models or dense shaping; no guardrails-runtime adoption.
