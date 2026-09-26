# minder operator CLI

Human view + maintenance over minder's memory plane. Read-only first,
writes only behind `--yes`.

```bash
python3 -m minder_op doctor [--no-probe] [--json]
python3 -m minder_op status
python3 -m minder_op flags
python3 -m minder_op task declare --domain D [--task T] [--subtask S] [--note N]
python3 -m minder_op task status [--task T]
python3 -m minder_op task close [--task T] [--reason R]
python3 -m minder_op events ls [--failure-key K] [--type T] [--episode EP] [--limit N]
python3 -m minder_op events show EVENT_ID
python3 -m minder_op families register --family F --method-digest H --metric M --splits S --sources JSON [--expected-trials N]
python3 -m minder_op families trial --family F --config-hash H --vintage JSON --split S --result JSON
python3 -m minder_op families analysis --family F --method M --code-digest H [--n N] [--variance V]
python3 -m minder_op families status F
python3 -m minder_op families holdout-unlock F --actor A --yes
python3 -m minder_op routes eval [--declared-domain D] [--task T]
python3 -m minder_op routes ls [--limit N]
python3 -m minder_op routes replay --cases PATH [--out PATH] [--json]
python3 -m minder_op routes ambiguity [--days 30]
python3 -m minder_op resume assert --claim C [--variant P ...] [--actor U]
python3 -m minder_op resume approve --assertion ID --phrase P [--scope any|JD] --actor U
python3 -m minder_op resume uncertain --assertion ID
python3 -m minder_op resume intent --jd JD --jd-digest H --assertion ID --phrase P [--retention N]
python3 -m minder_op resume draft --draft D --assertion ID --phrase P [--jd JD]
python3 -m minder_op resume impact ID
python3 -m minder_op resume correct ID --claim C [--actor U] --yes
python3 -m minder_op resume expire
python3 -m minder_op episodes ls [--repo R] [--status S] [--limit N]
python3 -m minder_op episodes show EPISODE_ID
python3 -m minder_op lessons ls [--status verified|candidate|invalidated]
python3 -m minder_op lessons show LESSON_ID
python3 -m minder_op lessons invalidate LESSON_ID --reason "..." --yes
python3 -m minder_op lessons promote EPISODE_ID --instruction "..." \
--tests-passed --yes # same gates as promote_lesson
python3 -m minder_op gaps ls [--status open|closed]
python3 -m minder_op gaps close GAP_ID --reason "..." --yes
python3 -m minder_op consults ls [--limit N]
python3 -m minder_op consults show TRACE_ID
python3 -m minder_op decisions ls [--limit N]
python3 -m minder_op export-stats [--path minder_memory/exports/training_candidates.jsonl]
python3 -m minder_op weekly-summary [--days 7 | --since ISO] [--json]
python3 -m minder_op benchmark list
python3 -m minder_op benchmark validate --suite coding-core-v1
python3 -m minder_op benchmark run --suite coding-core-v1 --dry-run
python3 -m minder_op benchmark compare BASELINE.json CANDIDATE.json [--json]
python3 -m minder_op benchmark baseline create REPORT.json [--out PATH] --yes
python3 -m minder_op success-loops ls [--limit N]
python3 -m minder_op trace ls [--limit N] [--json]
python3 -m minder_op trace review SESSION [--rubric RUBRIC.json] [--json] [--no-store]
python3 -m minder_op trace show REVIEW_ID [--json]
python3 -m minder_op trace feedback REVIEW_ID --level run|event|claim \
  --category CATEGORY [--target REF] [--finding ID --verdict confirm|reject] \
  [--comment "..."] [--reviewer NAME] --yes
python3 -m minder_op trace regress SESSION --finding FINDING_ID \
  [--suite coding-core-v1] [--task-id ID] --yes
```

`--db PATH` points at another memory sqlite (tests, second installs).
Exit codes: `0` ok, `1` usage / not found, `2` DB missing or corrupt.

Doctor facts (operator health check):

- `doctor` is the first command to run when anything feels off: DB
presence, schema currency, flag values (unknown `MINDER_*` values
fail — typo detection), hook wiring (share + zcode config), event
staleness, loopback proxy probe, benchmark suites, pinned baselines.
- Verdict semantics: `fail` -> exit 1 (broken); `warn` -> exit 0 but
look (missing wiring, silent-for-a-week hook); `info` -> context
only (proxy not running, no baseline pinned).
- The proxy probe is one GET to `127.0.0.1:$MINDER_PORT` and nothing
else; `--no-probe` skips it (tests always skip it).
- `events ls/show` exposes the raw observed-events table (newest
first, redacted on display) — the quickest way to see what actually
happened behind a weekly-summary focus item.

Phase 1 facts (domain routing):

- `task declare/status/close` is the explicit boundary intake: one open
context per task, closed vocabulary (`coding | trading_research |
resume_application | cited_research | mixed`), re-declaring the same
domain reuses the context, a different domain switches (old context
pinned, `domain_transitions` row recorded). Unknown domains are
rejected, never guessed.
- `families …` is the trading research registry: preregistration
before verifiability, append-only trial manifests (no winners-only
history), trial-count bookkeeping cross-checked against DSR/PSR-style
analysis artifacts, holdout trials flagged until an explicit
`holdout-unlock --yes` human decision, vintage changes mark results
stale for re-run triage without erasing anything. Analyses are born
`candidate` and never auto-promote; Minder never certifies
profitability and there is no order-execution path.
- `routes eval/ls` is observe-only domain routing on the
`domain-route/v1` decision contract: the rules provider proposes from
a closed menu, the gate applies pinned thresholds, and every proposal
is traced with declared-vs-proposed provenance. A proposal NEVER
applies itself — task contexts change only via explicit declaration.
- `resume …` separates three linked objects: career assertions (what
happened), approved wordings (which phrasings are accurate), and
JD-scoped application intents (presentation per pinned JD). A
factual correction (`resume correct --yes`) supersedes the assertion,
appends `assertion_superseded` evidence flags on dependent drafts,
and drops dependent intents to review — history is never erased, and
other JDs are untouched. A positioning choice only sets a JD-scoped
intent from an already-approved variant; a classifier label can never
create a correction. Retention: intents expire with
the application cycle — default 90 days, `MINDER_RESUME_RETENTION_DAYS`
or `--retention` override, expiry recorded at creation and enforced
by `resume expire`; expiry never touches career history.

Benchmark facts (foundation + controlled local runner):

- `benchmarks/coding-core-v1/manifest.json` is versioned; its
fingerprint covers the functional core (manifest_version, suite_id,
tasks), so cosmetic edits keep reports comparable and task edits do
not. `MINDER_BENCHMARKS_DIR` overrides the suite root (tests).
- `run` is a dry-run planner unless BOTH `--execute` and
`--i-understand-this-runs-local-agent-tasks` are given (mutually
exclusive with `--dry-run`). Execution is allowlisted pytest
verification of manifest tasks declaring
`runner: {kind: pytest, entry: [...]}` — nothing else. No DSH/GUI
automation, no browser, no broker, no frontier, no package install,
no SSH, no network use.
- The runner stages each task's fixtures into a fresh
`minder-bench-*` temp workspace, optionally copies `--overlay DIR`
over the task's working directory (a candidate fix; regular files
only — symlinks are refused), scrubs the child env (allowlist: PATH,
LANG, LC_ALL; TMPDIR/HOME move inside the sandbox; proxies,
LD_LIBRARY_PATH, PYTHONPATH and MINDER_* flags dropped), and runs
`python -m pytest -q <entry>` with a hard `--timeout` (default 120).
Timeout kills the whole process group and writes a failed report
(to `--out`, else `<benchmarks>/reports/<suite>-failed.json`).
- Trust model: the operator owns fixture/overlay content. The sandbox
is isolation-by-default, not a defence against hostile code; safety
metrics in reports count what the runner can honestly observe.
- Comparator precedence: fingerprint mismatch -> NON_COMPARABLE;
candidate unsafe-execution/harmful-frontier/egress > 0 -> FAIL
(absolute, any sample size); <20 comparable runs ->
INSUFFICIENT_SAMPLE; verified completion drop > 5 points -> FAIL;
else PASS. Baseline is first, candidate second.
- A baseline is never created automatically: only
`baseline create --yes` pins one, and a pinned baseline is never
overwritten (move it aside first).
- Reference fixes live beside each task
(`tasks/<id>/solution/`); overlay one to produce a verified run,
e.g. `minder-op benchmark run --suite coding-core-v1 --task
t3_keyerror_default --overlay benchmarks/coding-core-v1/tasks/
keyerror_default/solution --execute
--i-understand-this-runs-local-agent-tasks`.

Trace review facts (post-run evaluation of completed dsh sessions):

- `trace` reads dsh's own session logs (`~/.dsh/sessions/<project>/
  <session>/session.v3.jsonl.zstd`) through `minder_op.dsh_sessions`
  — the same read-only reader `/sessions` uses. Nothing under the dsh
  home is ever written, and the trace is read as a snapshot.
- **Default is read-only.** Persistence needs `MINDER_TRACE_REVIEW=on`;
  `--no-store` forces it off for one run. With the flag off, `trace
  review` writes nothing at all — not even the DB file.
- Evaluators are deterministic (no model, no network, no clock): the same
  trace produces byte-identical findings. Severity is a statement about
  evidence, not a score — there are deliberately no 0-to-1 quality
  numbers, because that would imply a calibration this layer lacks.
  Rules: `dup-unchanged-retry`, `success-loop-same-result`,
  `edit-without-read`, `edit-without-test`, `repeated-identical-command`
  / `tool-call-budget`, `required-tool-missing` / `forbidden-tool-used`,
  `minder-not-wired` / `minder-intervened`, `no-new-artifact`.
- Every finding cites `ds_seqs` (dsh's own event `seq`), so a finding
  navigates back to the exact event in the original session log.
- Reuse, not reinvention: "the same failure" is
  `minder_memory/canonicalise.py`, "the same result" is
  `minder_memory/success_guard.py`. An offline finding and a live advisory can
  therefore never disagree about what a repeat is.
- A rubric is **JSON**, not YAML (stdlib only, no PyYAML). Keys:
  `rubric_id`, `applies_to` (informational), `required_tools`,
  `forbidden_tools`, `config`. Unknown keys are refused rather than
  ignored: evaluating against the wrong standard is worse than not
  evaluating. Without a rubric the workflow evaluator is silent — a rule
  that was never declared cannot be violated.
- `trace feedback` is the confirmation gate. It takes a closed
  taxonomy (`correct`, `partly_correct`, `incorrect_conclusion`,
  `unsupported_claim`, `evidence_quality`, `tool_selection`,
  `tool_parameter`, `tool_result_ignored`, `inefficient`,
  `policy_violation`, `safety_privacy`, `incomplete`) at run, event or
  claim level, and may carry `--finding ID --verdict confirm|reject`.
  Both or neither: a verdict names a finding. Feedback rows are
  append-only, so a changed mind is newer evidence, not an edit.
- `trace regress` enforces the evidence chain
  `trace -> finding -> reviewer confirmation -> regression case`. It
  refuses a finding that is not failure-shaped (a call-volume judgement
  is not a test) and refuses one no human has confirmed, because
  promoting an unconfirmed finding would make an evaluator's false
  positive the standard. It then emits a `benchmarks/<suite>/tasks/
  <id>/` case in the existing content-only shape and gates the manifest
  write on `validate_manifest`; on any validation error the staged
  directory is removed and the manifest restored byte-for-byte. The
  generated case passes as written *and* fails if the rule it encodes is
  weakened, so it is a regression test rather than a snapshot.
- Order of operations when a suite is already invalid: `trace regress`
  reports the suite's own pre-existing errors and refuses, rather than
  blaming the new case (fixture paths resolve relative to the suite
  directory, so a checkout missing sibling directories reads as invalid).
- `success-loops ls` prints the guard's delivery mode. With
  `MINDER_SUCCESS_GUARD=block`, a repeat of an action that already
  looped is stopped *before* it runs by the PreToolUse hook, with a
  structured directive as the reason; `advisory` attaches the same
  evidence as non-blocking context, and `off` (default) records nothing.
- Per-run cost is **not** available: dsh's usage ledger is
  per-day/per-model. `execution.estimated_cost` is therefore explicitly
  `null` with a `cost_note`, and efficiency is judged on call counts,
  wall-clock duration and per-session tokens — never an invented cost.

Facts worth remembering:

- Consult labels come from `frontier_evals` (migration 007). The`frontier_traces.helpfulness` INTEGER column (003) is legacy and is
never displayed as a label.
- The default `lessons ls` view is verified-only, mirroring retrieval.
Frontier-distilled **candidates** are inert until promoted and appear
only with `--status candidate`.
- All flags (`MINDER_ASSIST`, `MINDER_CLASSIFIER`, `MINDER_DECISION`,
`MINDER_SUCCESS_GUARD`, `MINDER_TRACE_REVIEW`)
are environment/systemd owned. Change them there and restart the hook;
the CLI cannot persist flags. `MINDER_DECISION` shadow debounce is
DB-backed (session + failure_key, 5s window) — visible in
`decisions ls`.
- Writes go through the existing memory APIs only:
`invalidate_lesson`, `promote_lesson` (still requires tests_passed),
`skills.close_gap`. No `SKILLS.md`, no auto skill apply, no DB-edited
flags, no log deletion.
- The contract hash shown nowhere here is a frozen-criteria digest, not
a full-input fingerprint.
- `weekly-summary` reports **observed workflow evidence only** — it
never claims Minder improved productivity (improvement claims require a benchmark comparison against a pinned baseline). The
report object comes from `minder_op.summary.build_weekly_summary()`;
the web overview reuses the same object rather than duplicating
SQL. Consult labels come from `frontier_evals` (007), never the
legacy 003 integer. Benchmarks render "not available" when no suites exist. The
"operator focus" block is a fixed-priority, max-three list of
follow-up commands; `now=` injection keeps it deterministic in
tests.

## Deferred (+ — deliberately not built here)

| Item | Sketch |
|---|---|
| Web console | localhost app reusing `minder_op.queries` |
| Auth / multi-user | `actor` recorded on review writes first |
| Live Jev in CLI | `decision replay` offline first; never default network |
| DB-edited `MINDER_ASSIST` | env overrides DB; hook re-read/restart documented |
| Graph visualiser | `graph export --dot` before any canvas |
| Auto skill apply | `skills accept ID --yes` only, never from the hook |
| Log retention | `minder-op logs prune --older-than`; no silent DELETE |
| Skill-risk denylist | explicit ids, not name prefixes |
| 003 vs frontier_evals sync | planned hygiene pass |
