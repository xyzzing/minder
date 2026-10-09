# minder operator CLI

Human view + maintenance over minder's memory plane. Read-only first,
writes only behind `--yes`.

```bash
python3 -m minder_op doctor [--no-probe] [--json]
python3 -m minder_op status
python3 -m minder_op migrate
# apply the migrations this runtime ships to the store it is pointed at,
# printing the versions it landed. Additive and idempotent, so no --yes.
# Refuses (exit 1) a store already past this runtime's migration set.
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
# also prints the lesson's five most recent injection-ledger rows: when,
# which retrieval tier matched, which assist path carried it, chars
# injected, session (issue #13). A store predating migration 015 prints
# `injection ledger: not available` instead of an empty table.
python3 -m minder_op lessons invalidate LESSON_ID --reason "..." \
[--diagnosis unknown|content_defect|application_failure|external_failure|no_issue] \
--yes # the diagnosis names what went wrong; the reason stays the note
python3 -m minder_op lessons reject LESSON_ID --code generic \
[--note "..."] --yes # refuse a frontier candidate with a closed reason code
python3 -m minder_op lessons injections [--limit N]
# per-lesson injection counts, busiest first, plus `misses: N` on its own
# line: the decisions the store had no lesson to offer. They are counted
# separately because they are the denominator of the hit rate, not a
# lesson with an empty id (issue #20). A store predating migration 015
# prints `injection ledger: not available`, never zeros.
python3 -m minder_op lessons decisions [--id LESSON_ID] [--limit N]
# no --id: decisions per (action, code) straight off the ledger; with
# --id: that lesson's decision history, newest first (issue #14). A store
# predating migration 016 prints `decision ledger: not available`.
python3 -m minder_op lessons promote EPISODE_ID --instruction "..." \
[--tests-passed] --yes # same gates as promote_lesson
python3 -m minder_op lessons promote LESSON_ID --from-candidate \
[--instruction "..."] --yes # adopt a frontier-distilled candidate
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
Exit codes: `0` ok, `1` usage / not found, `2` DB missing or unreadable.

Schema currency:

- `status` prints the learning plane's retrieval hit rate as four named
numbers: `retrieval_asked`, `retrieval_hits`, `retrieval_misses` and
`retrieval_hit_rate` (issue #20). The misses are printed rather than
left to be subtracted, because the split is the whole point of the
ledger: a rate alone cannot tell an operator whether learning is
under-retrieving or under-populated. A store predating migration 015
prints `not available` for all four; an empty ledger prints `0` asked
and no rate at all, which are different facts.
- `status` prints `schema_version` (what the store has applied),
`schema_latest` (what the running code can apply) and
`schema_installed` (what the installed share staged at install time).
`schema_behind: yes` means the store predates the running code; the
gap is closed with `minder-op migrate`.
- The two denominators are different numbers on purpose. A store can
be current for the code that runs and still be ahead of what the
installed share staged — that means the staged files are stale and
`install.sh` needs a re-run, not a migration.
- Migration files use bare `CREATE TABLE`, so a store whose recorded
`user_version` sits below the tables it already holds refuses to
replay. The error says so and names `PRAGMA user_version`; the store
is not damaged and must not be deleted.
- `migrate` only ever applies the migration files sitting beside the
copy of minder answering the call. A store above that ceiling is
refused with exit `1` and a sentence naming which runtime can do the
work; stamping a store with migrations the deployed code has no files
for would turn the next read into a missing table reported as
corruption. When a successful run pushes the store past what the
installed share staged, the same run says the staged files are stale.
- A DB error names its own kind: `memory db not found`, `memory db not
readable as a sqlite database`, `db schema is missing an object`, or
`db corrupt or wrong schema`. Each points at a different repair, so
one message for all four sends the operator to the wrong one.

Doctor facts (operator health check):

- `doctor` is the first command to run when anything feels off: DB
presence, schema currency, flag values (unknown `MINDER_*` values
fail — typo detection), hook wiring (share + zcode config), event
staleness, loopback proxy probe, benchmark suites, pinned baselines.
- The `schema` line compares the store against the migrations this
runtime ships, and names `minder-op migrate` when it is behind. When
the store is ahead of the installed share it says the staged files are
stale instead.
- The `coverage` line compares two counts that must measure the same
unit of work: PostToolUse hook invocations in dsh's session logs against
PostToolUse `hook_timing` records in the store. A `hook_timing` row is
written for every hook point, so the filter is what keeps the number
honest — before it, a host running both hook points always read 100 %
and the floor could not fire. The line says `partial view` when the log
scan hit its caps (`coverage.complete` is false): the invocation count
is then a floor, not a measurement, and 100 % does not mean nothing was
lost. The store side is reported separately as
`coverage.persisted_db`, with `coverage.db_measured` saying whether that
number was measured at all.
An unreadable store gives `db_measured: false` and no count, never a 0,
because 0 is the value that fails the floor and sends you to fix hooks
that are working.
- The `laya-worker` line reports the isolated decision worker (issue
#7): `ok` when a worker is answering on its socket, `info` when none
is live (it spawns on the first router-eligible request), and `warn`
with the last recorded failure when the client-side log holds one —
that last case means the difficulty router is failing open, so
`difficulty_router` is costing nothing but achieving nothing either.
The log has no timestamps and rolls only at 1 MB, so a `warn` can name
a defect that is already fixed: confirm with a direct call before
acting, and move the log aside (`mv ~/.local/state/minder/laya-worker.log
{,.old}`) to clear the evidence. The worker is spawned by the proxy, so
it runs the staged share the proxy imported — after `install.sh`, restart
the proxy service or an already-listening worker keeps the old code.
- The router's own modes (`~/.config/minder/minder.json`,
`difficulty_router`): `shadow` consults and records without acting,
`active` may move effort in either direction, `lower` consults and
applies the band only when it lowers effort — never a raise. A client
that set its own effort always wins, in every mode. Only presets
marked `"class": "auto"` reach the router at all, and the router is
`shadow` by default, so a store with no `difficulty_*` events means
the router has never been consulted — check for `auto_effort` events
first to see whether any request was eligible. When it has been,
`minder-op events ls --type difficulty_skipped` says why it abstained
(`client_effort`, `escalation_marker`, `no_client`,
`below_confidence`, `malformed_response`) rather than leaving the
operator to guess between "never ran" and "ran and did nothing".
- `minder-op events ls --type declined` counts the actions the operator
declined (issue #27). A decline is a third outcome, not a failure: the
harness writes `Error: The user dismissed the plan review to speak
instead`, the shared `error:` fail sign made that a command failure, and
two dismissals of one tool produced an L1 directive telling the agent to
retry with a regenerated anchor. `minder.classify_outcome` checks the
named `DECLINE_SIGNS` first and returns `failed`, `declined` or `success`;
the ladder records a decline and changes nothing else — it neither raises
a level nor refunds the budget of a loop that is still there. `is_failure`
still answers yes, deliberately: the memory store's event vocabulary is
`tool_failure|tool_success|verification`, and a decline is closer to
needing a lesson than to a verified success.
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
- A `verified` episode already carries its proof. When a hook sees a
recognised test-runner command (`pytest`, `unittest`, `vitest`, `jest`,
`go test`, `cargo test`, `npm test`, ...) whose output states a passing
count and no failing count, it closes the session's open episode
`verified` and appends a `verification` event holding the runner and the
redacted command. `lessons promote` then needs only `--instruction`;
`--tests-passed` remains for episodes closed `verified` by another path,
and promoting an episode with neither still fails with
`rejected:no-verified-tests`. Recognition and the clean-run judgement
live in `minder_core/verification.py`.
- A verified close also finishes the frontier consult that was about it
(issue #10). The Warden names the open episode when it escalates, and the
panel records every consult against it, so when that episode closes
`verified` the consult is labelled `pass`/`helpful` from that close and its
distilled actions become one **candidate** lesson. A consult with no
episode, or one whose episode closes any other way, stays unclassified and
yields nothing. The join is `minder_memory/frontier_link.py`, it runs once
per consult, and the events ledger names what it did
(`frontier_distilled`).
- What reaches that queue is decided by the answer's own structure (issue
#11). The ask demands ranked root causes and one labelled next action, and
the reading keeps the labelled line as the action and treats everything else
as a cause: a cause is never distilled as an instruction, whatever label
precedes it, and the per-consultant notes appended after the merged answer
are not distilled either. An answer that ignores the shape falls back to an
imperative-verb test, which is why a lesson instruction can read like prose
rather than a command. The contract is one constant,
`minder_core/panel_text.py:ANSWER_SHAPE`; `minder_core/panel_answer.py`
implements the reading.
- Reviewing that queue is one command:
`minder-op lessons promote <lesson_id> --from-candidate --yes`. It adopts
the candidate's own distilled text, or an edited one via
`--instruction`, and the tests evidence is the source episode's, so
`--tests-passed` is refused there. The candidate is tombstoned
(`invalidated`, reason `adopted by operator as <lesson_id>`) and the
verified lesson keeps the consult's `trace_id` in its verification. A
candidate whose episode has no `verification` event is still refused with
`rejected:no-verified-tests`.
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
- `lessons invalidate --diagnosis` and `lessons reject --code` write a
closed code from `minder_memory/lesson_decisions.py` next to the free-text
reason (issue #14), so the queue's history is countable rather than only
readable. `lessons decisions` reads those counts back. An out-of-taxonomy
code is a usage error and writes nothing; the default diagnosis is
`unknown`, which names no mechanism. A store predating migration 016
reports `not available`, never zeros.
- The `lesson injections` section separates the two numbers that make
each other meaningful (issue #13): decisions that carried a lesson, and
decisions that had none to offer. A verified lesson with no injection
rows is a live lesson doing nothing, so it becomes a focus item; a store
without the ledger table reports `not available`, never zeros.
- Issue #20 states the ratio those counts imply instead of leaving it to
be computed at read time: `retrieval hit rate`, in the same words, on
`minder-op status`, `minder-op lessons injections`, the weekly summary,
`minder-op scorecard`'s learning group and the console overview. It is a
plane-level number, so it belongs where the plane is read; the per-lesson
impact split issue #16 added has no base rate to be read against without
it. No retrieval behaviour changed: the same lesson that fired before
this change fires now.

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
