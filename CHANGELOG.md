# Changelog

All notable changes to minder. Versions follow [SemVer](https://semver.org/)
loosely; the single source of truth is `MINDER_VERSION` in `minder.py`.

## 0.8 — 2026-09-23

The operator plane (milestones 8A–8E) plus the first two phases of
domain-routed evidence governance.

- **Operator CLI** (`minder_op`) — doctor, status, flags, episodes,
  lessons (verified/candidate/invalidated), gaps, consults
  (labels from `frontier_evals`), decisions, export-stats, raw events,
  weekly-summary (observed evidence only, never productivity claims).
  Explicit review writes (invalidate/promote/close) behind `--yes`.
- **Localhost web console** (`minder_web`) — read-only, 127.0.0.1-only
  bind guard, overview/episodes/lessons/gaps/consults/decisions/
  benchmarks/healthz; candidate lessons visibly distinct; optional web
  extra (`requirements-web.txt`).
- **Benchmark harness** — versioned suite manifests
  (`coding-core-v1`, `routing-core-v1`), 8C report/baseline schemas,
  protected-metric comparator (NON_COMPARABLE / INSUFFICIENT_SAMPLE /
  FAIL / PASS), sandboxed task runner behind
  `--execute --i-understand-this-runs-local-agent-tasks`, pinned
  baselines never created automatically.
- **Domain governance Phase 1–2** — explicit task-context intake
  (`task declare/status/close`, closed domain vocabulary), trading
  research protocol registry (preregistration, append-only trial
  manifests, multiple-testing bookkeeping, holdout-unlock gate,
  vintage-staleness triage), résumé fact/wording/intent evidence with
  JD-scoped retention (default 90 days), observe-only
  `domain-route/v1` routing with shadow traces, read-only impact
  propagation algebra (property-tested), graph type vocabulary
  enforcement. Migration 011 + 012; schema v12.
- **Evaluation results** — `routing-core-v1` replays 40 adversarial
  cases; deterministic rules baseline 40/40; the laya 0.3.6 routing
  model measured 14/40 (0 unsafe, 0 injections followed) → comparator
  FAIL. Classifiers stay shadow-only until they beat the baseline.
- **Install hygiene** — `install.sh` stages `minder_op` beside
  `memory`/`decision` and writes `~/.local/bin/minder-op` /
  `minder-web` shims; CI runs the suite on Python 3.11–3.14 plus an
  optional real-model job.

## 0.7 — 2026-09-21

Phases 2–3 of the memory PRD (docs/minder-phase-2-3-frontier-coding.md):
skill integration finished, graph relationships and invalidation.

- **Progressive skill disclosure** — index entries carry
  description/risk_level/body/preconditions/verification; full bodies live
  under `skills/bodies/` and load only for matched skills
  (`memory/skill_load.py`). Missing bodies degrade to `instructions=None`.
- **Temporary plans** — unmatched failures draft a deterministic procedure
  (`memory/plans.py`, migration 004) from per-gap-type step templates; a
  temp plan is never auto-converted into a skill.
- **Candidate skill proposals** — ≥3 distinct verified lessons sharing
  repo+failure family distill into a `proposed` candidate
  (`memory/skill_promote.py`, migration 005); instructions come only from
  lesson fields, never frontier text. Only
  `accept_skill_candidate(actor="operator", apply=True)` writes index
  metadata + a body file. SKILLS.md is never written by anything.
- **Skill evaluation fixtures** — offline fixtures +
  `evaluate_skill_selection` fails loudly on selection mismatches.
- **Policy digest wiring** — the duplicate-block digest now appends a
  matched skill's compact instructions, else a TEMPORARY PLAN, else stays
  lesson-only.
- **SQLite graph** — nodes/edges (migration 006; SQLite only, no graph
  database) with temporal edges; verified promotions project
  DERIVED_FROM/HAS_FAILURE/AFFECTS/FAILED_TEST/MODIFIED/VERIFIED_BY/RAN/
  APPLIES_TO edges when the data exists — unknown entities are skipped.
- **Invalidation & impact** — superseded or path-invalidated lessons are
  no longer served (`supersede_lesson`,
  `invalidate_lessons_for_change`, commit-staleness via `current_commit`);
  `suggest_verification` returns the tests linked to changed files and the
  lessons at risk. Contradictory verified lessons are linked CONTRADICTS
  and the older one is marked needs_revalidation — at most one
  authoritative lesson is served.
- 217 tests.

## 0.6 — 2026-09-21

Memory v1 (docs/prd-memory-v1.md, PRs 0–7; spec: canonical failure keys →
SQLite episode/lesson store → duplicate-action guard → verified lessons →
hook wiring → skill gaps → frontier traces):

- **Canonical failure keys** — pure functions turn hook/tool events into
  stable `tool|family|symbol|relpath` keys; timestamps stripped, absolute
  paths made repo-relative, API-key-shaped strings redacted before
  keying/storage. `unchanged_retry` distinguishes verbatim repeats from
  progress (new hypothesis / content).
- **SQLite store beside the JSONL ledger** — append-only `events` (UPDATE/
  DELETE rejected by triggers), episodes + per-episode attempts, lessons.
  `MINDER_MEMORY_DB` overrides the location; every store call fails open
  with a degraded status, never crashing a hook.
- **Duplicate-action guard** — after the Warden ladder runs, a verbatim
  repeat (same failure key + same action fingerprint) with
  `memory_fail_threshold` (default 2) recorded attempts is replaced by a
  `block_duplicate` directive demanding a new hypothesis. Read-only over
  the store; any store problem = the ordinary ladder. Marker-compatible
  with the Turnstile escalation channel; both transports behave as before
  (zcode exit 0 directive, dsh exit 2).
- **Verified lessons** — promotion only from `verified` episodes with
  explicit `tests_passed` verification; retrieval scoped repo+key then
  failure family (no cross-repo), compact payloads; the duplicate-block
  digest carries the retrieved lesson when one exists.
- **Hook wiring** — PostToolUse observations record before the Warden runs;
  success closes the episode `verified` only with an explicit tests-passed
  payload, else `candidate`.
- **Skill gaps** — unmatched repeated failures (or environment-like
  families) record queryable gap rows; SKILLS.md is never written.
- **Frontier traces** — hashed per-consult metadata (request/response
  hashes, provider fingerprint, redaction profile) with
  helpfulness/verification left null until joined later; panel behaviour
  unchanged.
- 173 tests.

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
