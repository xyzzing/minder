# Changelog

All notable changes to minder. Versions follow [SemVer](https://semver.org/)
loosely; the single source of truth is `MINDER_VERSION` in `minder.py`.

## 0.5 — 2026-09-20

- **Token accounting in the ledger** — the proxy harvests upstream `usage`
  (prompt/completion/total, plus cached and reasoning tokens when offered)
  into a `token_usage` event per governed request. For streaming requests
  that didn't ask for usage, minder injects `stream_options.include_usage`
  upstream and swallows the injected usage-only event, so clients see
  exactly the bytes they asked for.
- **Effort picker is real** — the installer declares the CAP-measured effort
  vocabulary on the `qwen-auto` model card (`reasoningEfforts`), so the dsh
  UI picker offers levels the server actually accepts. A UI-picked effort
  wins over the activity scheduler (escalation markers still win over both).
- **First public release** — published to github.com/xyzzing/minder with CI
  (Python 3.10–3.12), CHANGELOG, CONTRIBUTING, and ADR-0004 (why minder is a
  sidecar proxy, not a harness plugin).

## 0.4 — 2026-09-20

- **Compaction survival** — zcode SessionStart matcher upgraded to
  `startup|clear|compact`; on compact the hook emits a ledger brief (failure
  keys, budgets, and an in-flight escalation marker) so the escalation channel
  survives context compaction.
- **Mode regulation** — the proxy honors `X-Minder-Mode: direct|lean|deep`
  (marker > mode > preset class) with thinking budgets; a thermal clamp reads
  `~/.local/state/sinter/instance.json` and downgrades deep→lean when the
  machine is pacing or hot.
- **Verify consults** — when a frontier-spent key deescalates, one panel
  verify consult checks ADDRESSED / NOT_ADDRESSED and records it.
- **Domain profiles** — `profiles.{name}` overlay: extra fail signs, custom
  frontier prompt template, egress redaction before consults, hash-chained
  audit ledger.
- **Frontier panel** — L2 consults now query every configured provider in
  parallel and the first responder synthesizes, flagging disagreements
  (DeepSeek ↔ OpenAI cross-check). Providers without keys are skipped honestly.
- **Reflex tier (advisory)** — optional CPU micro-classifier sidecar enriches
  L1 digests with a probable failure class; never governs, fails open.
- **CAP hardening** — tool-call cleanliness probe (T1c), effort-vocabulary
  measurement (semantic levels mapped onto the model's real vocabulary), and
  re-verification against the live server at every proxy boot.
- **Escalation fairness** — same-error-signature gate (counters reset when the
  failure actually changes, so TDD red-green cycles don't escalate), budget
  refund on deescalate, per-target tool keys (no more `read:generic`
  aggregation).
- 111 tests, Python 3.10+ stdlib only.

## 0.3 — 2026-09-19

- Initial feature-complete ladder: **measure → instruct → escalate → audit**.
- CAP capability probe (thinking mechanism, effort channel, budgets —
  measured, never assumed).
- Stateless escalation channel via `[minder] ESCALATION` digest markers in the
  last 4 messages; budgeted L0 exec → L1 think → L2 frontier consult → L3
  alarm with backfire breaker and cooldown.
- Additive dsh integration (minder provider on :8390 + Claude-Code-style hooks
  bridge) and zcode hooks; `qwen-auto` effort scheduling absorbed from the
  proxy side.
- Audit surfaces: `events.jsonl` ledger, `consults.jsonl`,
  `minder.py report`.
