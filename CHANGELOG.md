# Changelog

All notable changes to minder. Versions follow [SemVer](https://semver.org/)
loosely; the single source of truth is `MINDER_VERSION` in `minder.py`.

## Unreleased — laya confidence calibration (measured) + declaration guard

- **Laya's confidence is now derived from its distribution, not its
  self-report.** Measured on routing-core-v1 (40 cases, laya 0.3.6 CPU):
  the model's self-reported confidence lands at 0.11-0.28 while its
  actual distribution is informative — top choices 2.5-3x uniform where
  laya is right, near-tied exactly where it is wrong. The adapter now
  maps the primary choice margin to confidence (2x margin, clipped), so
  the pinned gate thresholds judge a meaningful signal. Replay: correct
  14/40 -> 35/40 with zero protected-metric violations; the deterministic
  rules baseline stays 40/40. The raw self-report is dropped at the
  adapter — it carries no routing signal and no trace column stores it.
  The margin scale is a measured
  constant (`CALIBRATION_MARGIN_SCALE`), and the first version of this
  change shipped a bug worth recording: a wrong relative import inside a
  fail-open helper silently returned 0.0 for everything — `tests/
  test_laya_calibration.py` pins the function so that cannot recur.
- **Declaration precondition is now structural** (`routing.assess_route`):
  without an explicit domain declaration in the request, nothing routes —
  `uncertain`/`restricted`/`abstained` with the proposal still visible as
  `router_proposed`. The first calibration pass violated
  `external_prohibited_egress` twice (confident answers on injection /
  out-of-domain cases that must abstain); the deterministic provider
  self-limited all along, and the guard makes that precondition hold for
  every future provider instead of trusting it.
- Live installs: `MINDER_SUCCESS_GUARD=block` is now the declared runtime
  value (idempotent `profile-apply --success-guard block`; reinstall
  preserves it).

## Unreleased — ecosystem pass (namespaces, minder_core, --generic, wheel)

The panel's strategic recommendations, landed: adoptable packaging, a
dependency-free extractable core, and a first-class path for harnesses
that are not dsh/zcode. Also fixes a latent install bug: `trace/` was
never staged to the share, so `minder-op trace ls|review` broke on live
installs with an ImportError.

- **Internal packages namespaced**: `memory/` → `minder_memory/`,
  `decision/` → `minder_decision/`, `trace/` → `minder_trace/`. The old
  top-level names collide with the stdlib (`trace`) and PyPI reality
  (`memory`), which made honest packaging impossible. All imports,
  benchmark fixture paths, and install staging updated; install.sh now
  also stages `minder_trace/` and `minder_core/` (fixing the trace gap
  above). Behaviour unchanged: 764 tests pass.
- **`minder_core`** — the extractable, citable surface: `comparator`
  (the protected-metric verdict law) and `identity` (canonical failure
  keys, action fingerprints, redaction, result signatures). Stdlib only,
  imports nothing from minder, pinned by AST tests
  (`tests/test_minder_core.py`). `minder_memory.canonicalise`,
  `minder_memory.success_guard` and `minder_op.benchmark` re-export the
  same objects — no forked laws.
- **`install.sh --generic`** — code, config and operator shims only: no
  dsh/zcode wiring, no systemd units. Any OpenAI-compatible harness then
  points at the self-started proxy (`http://127.0.0.1:8390/v1`); the
  escalation markers in recent context do the rest. README restructured
  around it.
- **Wheel packaging** (`pyproject.toml`): the operator plane,
  evidence store, decision gateway and minder_core are pip-installable
  (`minder-op` / `minder-web` entry points; migrations + console
  templates ship as package data; version is dynamic from
  `MINDER_VERSION`). Hook transports/proxy/sink stay install.sh-staged —
  they need a real upstream and harness to be honest about what they are.
- **ADRs published**: `docs/adr/0001–0004` are tracked again (private
  info reviewed; none found), so CONTRIBUTING's design-convention links
  resolve for cloners.

## Unreleased — QA panel hardening (session-id joins, reinstall guard, sink auth)

Findings from an external-perspective architecture/QA review; every fix
landed with a regression test.

- **Reinstall can no longer downgrade the loop stop.** The live hooks.json
  ran `MINDER_SUCCESS_GUARD=block` while the repo template pinned
  `advisory`, `check_profile` validated flag *names* but not values, and a
  reinstall silently restored `advisory`. The guard mode is now a template
  placeholder: `dsh_install profile-apply` preserves the live value unless
  `--success-guard` says otherwise, `check_profile` fails on an illegal
  value (a typo reads as `off` in `guard_mode()`), and `minder-op doctor`
  gained a `hook-flags` check that validates the values the hook command
  actually declares.
- **Session ids are never truncated.** `hook.py` sliced ids to 40 chars
  while dsh ids are 44 (`session-<uuid>`) — the missing tail broke every
  join between the JSONL ledgers and `~/.dsh/sessions` (the stale
  `/sessions` page). Full ids are now written everywhere.
- **Sessionless payloads no longer share one `default` bucket.** A payload
  without a session id used to merge failure counters, escalation budgets
  and breaker memory across unrelated sessions. `minder.session_key()`
  derives an action-scoped `sessionless-<sha1>` key instead: identical
  calls still accumulate into one countable loop, different actions never
  share a bucket, and the self-describing key name makes the degraded
  identity visible in the ledger and state dir.
- **Migrations are atomic.** A migration failing mid-file used to leave
  partial DDL with the old `user_version` stamped, so every later
  `connect()` re-ran the broken file and raised forever (fail-open to "no
  memory"). Each migration now runs in one transaction with its version
  bump; a failure rolls back whole and the retry starts clean.
- **Console Host allowlist.** The read-only console is deliberately
  unauthenticated, so a DNS-rebinding page re-resolving to 127.0.0.1 could
  read private evidence cross-origin. Non-loopback `Host` headers are now
  refused with 403 (`minder_web.app.host_from_header`; loopback names and
  missing-host HTTP/1.0 probes still answer).
- **Sink writes need the shared secret.** Loopback was "not an auth
  boundary" while sink writes flow back into agent context — local memory
  poisoning was a one-POST affair. The sink now ensures
  `STATE_DIR/sink.token` (0600) at startup and requires
  `Authorization: Bearer` on `POST /persist`; clients read the token
  per-spawn. `GET /healthz`/`/stats` stay open, and a missing token file
  still opens writes (documented escape hatch, never a half-state).
- **Context injection is bounded.** The compaction brief is capped at 20
  failure keys / 4000 chars so a long session cannot crowd out the context
  the summarizer just kept.
- **CI now lints.** A `ruff` job (config: `ruff.toml`, default rule set +
  bare-except ban; test-only idioms ignored per-file) runs beside the test
  matrix. Landing it cleaned 90+ findings: dead imports/variables, a
  redefined `events_page`, leftover locals — no behavior changes intended
  or observed (757 tests pass).
- **Out-of-band health signal.** install.sh writes an optional
  `minder-doctor.service` + daily `minder-doctor.timer` (skipped under
  `MINDER_NO_SYSTEMD`): doctor runs daily, files
  `$STATE/doctor-last.txt`, and exits non-zero on an unhealthy verdict so
  a silent watchdog shows in `systemctl --user --failed`.

## Unreleased — trace review + a loop stop that actually stops

Post-run evaluation of completed dsh sessions, and the fix for the live
DBS incident that the advisory-only success guard never actually
interrupted. Design: `docs/trace-review/prd-trace-review.md`.

- **Success-loop guard now stops** (`MINDER_SUCCESS_GUARD=off|advisory|
  block`, default off = byte-inert). The old advisory was written to
  stderr on a *successful* exit — the bridge only keeps that as a
  bounded, log-only `stderrSummary`, so it never reached the model (0
  occurrences in 40 real traces, 703/703 PostToolUse results `pass`).
  `advisory` now rides exit-0 stdout as
  `hookSpecificOutput.additionalContext`, the channel the bridge injects
  into the next request. `block` returns a structured recovery directive
  as the block reason, and a new **PreToolUse** hook stops the *next*
  identical call before it runs (it only needs the requested action; the
  ledger already knows which actions looped). An unrecognised flag value
  reads as `off`, so a typo can never start blocking tool calls.
- **Bug fix** — `from_hook._observe_success` read `tool_response` off
  the *canonical* event, where `to_event()` has already moved that text
  to `error_excerpt`. Every production success therefore signed the empty
  string: the counter still fired on exact repeats, but it could no
  longer tell two different results from one action apart. The existing
  unit test passed because it fed the pre-canonical shape.
- **Trace review** (`minder-op trace ls|review|show|feedback|regress`,
  web `/traces`) — reads dsh's own session logs through the existing
  read-only `minder_op.dsh_sessions` reader. Eight deterministic
  evaluators (no model, no network, no clock; same trace → byte-identical
  findings) over failures, successful-but-useless loops, edits without a
  read, edits without a test, redundant calls, declared workflow rules and
  stretches with no new artifact. Reuses `canonicalise` ("the same
  failure") and `success_guard` ("the same result"), so an offline finding
  and a live advisory can never disagree. Every finding cites dsh event
  `seq`s. No quality *scores*: counts and severities only, because a
  0-to-1 number would imply a calibration this layer does not have.
- **Evidence chain is enforced, not documented** — structured feedback
  (closed 12-category taxonomy at run/event/claim level) is append-only,
  and `trace regress` refuses a finding that is not failure-shaped and one
  no human has confirmed. It emits a content-only benchmark case in the
  existing suite shape, gates the manifest write on `validate_manifest`,
  and rolls back byte-for-byte on any error. The generated case passes as
  written and fails if the rule it encodes is weakened.
- **Migration 014** — `trace_reviews` and `trace_feedback`, both
  append-only with update/delete triggers. Reviews keep the summary and
  the findings, not the normalized events, so the live event table stays
  untouched and a re-review is always possible from the original log.
- **Flag law** — `MINDER_TRACE_REVIEW` (default off) gates review
  persistence; `--no-store` overrides it off. With the flag off, `trace
  review` writes nothing, not even the DB file. `MINDER_SUCCESS_GUARD`
  is now registered in `minder-op flags`; both new flags are tracked by
  the console and the sink's divergence check.
- **Honest limits, stated in the product** — per-run cost is not
  attributable (dsh's usage ledger is per-day/per-model), so
  `estimated_cost` is explicitly `null` with a note and efficiency is
  judged on call counts, duration and per-session tokens. Rubrics are JSON
  (stdlib only); unknown rubric keys are refused rather than guessed.

## Unreleased — dsh capture + console sessions

Fixes the silent loss of every dsh session: hooks fired (50
`hook/invoked` records in one session), exited 0, and persisted nothing,
because dsh's `workspace-write` file sandbox made the state directory
read-only for the hook process and every write is fail-open. See the
capture section of [docs/operator-web.md](docs/operator-web.md#capture-how-the-hooks-persist).

- **Sink sidecar** (`sink.py`, `minder-sink.service`, loopback-only) —
  performs the hook's persistence unconfined, over loopback. Ops:
  `append_jsonl`, `write_state`, `record`, `policy`; `GET /healthz`,
  `GET /stats`. Client in `memory/sink.py`; **inert and byte-identical
  when `MINDER_SINK_URL` is unset** (Law #6 preserved).
- **Latency** — the laya decision/classifier build ran in a fresh process
  on every tool call (measured 6.1 s per PostToolUse hook, 335 s in one
  session; p50 5.81 s). The policy pass now runs warm in the sink and only
  when it can change the outcome. Clean tool calls: ~5.8 s → ~0.10 s.
- **Capture is observable** — `hook_timing` per invocation, a
  `capture_probe` per SessionStart, `minder-op capture` and `/capture`
  comparing hook invocations against persisted records, store freshness,
  sink reachability and sandbox-mode mix. `minder-op doctor` gained sink
  and coverage checks and now recognises the production flag values
  (`MINDER_DECISION=laya`, `MINDER_SUCCESS_GUARD`).
- **Console sessions** — `/sessions` and `/sessions/{id}` are built from
  dsh's own surfaces (session logs, projection cache, workspace registry,
  usage ledger) via the new read-only `minder_op/dsh_sessions.py`: real
  workspace paths and titles, turns/steps/tokens/context pressure, sandbox
  and archived badges, episode and event counts, per-session hook p50 and
  tool breakdown. `/events` gained type/tool/failure-key/session filters
  and a staleness banner.
- **Scorecard** — `minder-op scorecard` and `/scorecard`: capture, cost,
  failures, learning, context and hygiene groups plus a 3-item focus list.
- **Installer** — stages `sink.py`, writes the `minder-sink` unit and
  shim, regenerates `hooks.json` with the runtime flags **and** the sink
  URL, and wires the dsh *profile* layout
  (`dsh/dsh_install.py profile-apply|profile-check`) instead of silently
  targeting the now-absent `~/.dsh/settings.yaml`.
- **Bug fix** — `minder.is_failure` did not recognise the harness's own
  shell marker `[exit code: N]` (it only matched `exit code N`), so a
  failed command with no traceback — the common `pytest`/`git`/`make`
  exit-code-only failure — was classified as a success and never
  escalated.
- **Docs** — `docs/operator-web.md` gains the capture mechanics (the
  sandbox constraint, the sink protocol, the flag-adoption rule, the ten
  metrics), the new routes and the identity relabelling.
- **Sink URL and flags are read back from `hooks.json`** — the URL is a
  deployment fact, so `capture`/`doctor`/the console no longer report
  "sink not configured" just because the operator's shell lacks the
  variable; and because the policy pass runs inside the sink, the sink
  adopts the hook command's `MINDER_*` flags at startup (an explicit unit
  value still wins) and reports them in `GET /stats`. `capture` warns on
  any divergence among the tracked policy flags, so a flag that only one
  of the two processes sees can never be silently inert again (the sink
  URL is the address, not a behaviour switch, and is excluded).
- **The console has a unit** (`minder-web.service`, `Restart=always`) —
  it was started by hand with `nohup`, which is why it kept vanishing and
  once served stale code against new templates. `minder_web` is now staged
  into the share like every other module, so the shim and the unit do not
  depend on the repo's location. The unit is written but left stopped when
  the optional web extra is missing, and says so.
- **The sink says it is not a UI** — `GET /` on 8392 returns a
  plain-text pointer (state dir, `http://127.0.0.1:8765`, `minder-op
  capture`), because opening that port in a browser used to look like a
  broken web UI. `GET /healthz` keeps its JSON contract.
- **Coverage is attribution-checked** — only persisted hook records whose
  session dsh actually knows count towards coverage, so a synthetic probe
  cannot make a dead capture path look alive; unattributable rows are
  reported separately rather than hidden.

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
