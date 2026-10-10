# minder domain context

Verified project facts for writing code: API quirks, platform behavior,
and approaches that already failed. Read before working on the subsystem
it covers. New entries arrive through the self-improvement protocol
(AGENTS.md M5) when a session learns a durable fact the hard way. Entries
carry no personal data, hostnames, or addresses. Each entry cites the
commit hash behind it; the instruction gate checks the hash exists.

## Runtime and deployment

- The live install runs from the staged share (`~/.local/share/minder`),
  not the repo: repo edits do nothing to the running console, proxy, or
  sink until `install.sh` re-stages and the unit restarts. (6aa38de)
- The dsh web host reads `dsh/hooks.json` once at startup; flag changes
  in the staged file need a dsh host restart before any session picks
  them up. (07dbfc2)
- The laya decision worker is not a unit: the client spawns it with
  `sys.executable -m minder_decision.worker` and a `PYTHONPATH` derived
  from the importing package's parent, so it runs the share the *proxy*
  process imported. Re-staging the share does not replace a worker
  already listening on the socket - restart the proxy service and the
  next spawn picks up the new code. (e8e4d06)
- `doctor`'s `laya-worker` check never infers liveness from
  `laya-worker.log`. The log is append-only and rotates only at 1 MB, so
  its tail is evidence about the moment a line was written, not about
  whether a worker is up: reading it that way made a live, answering
  worker warn off a `spawn_failed` from before the fix. Liveness is a dial
  (`minder_decision/log_stamp.py:worker_liveness`); the log names the last
  failure and its age, with client-side lines stamped ISO-8601 UTC and an
  unstamped line reporting the file's mtime as a floor ("at least"). A
  worker that is not up and has never failed is `info`, not a finding.
  (`minder_op/worker.py`.) (ab42a08)
- `doctor`'s `difficulty-router` line takes its cause from the ledger,
  never from the configured mode. Flipping `difficulty_router` to `laya`
  applied no bands on the live store, and the `warn` still read "the
  router is outranked, not broken; a client-declared effort wins" - which
  was true of the store the message was written against and false of the
  one it was now running on. The abstentions were `below_confidence`
  (the floor working) and `malformed_response` (the worker), each needing
  a different repair, and an aggregate count hid which. The check counts
  abstentions per recorded reason and names the dominant one.
  (`minder_op/router.py`.) (14a6317)
- `difficulty_skipped reason=client_effort` on every request is the
  approved precedence working, not the router being broken: the dsh
  profile declares `reasoningEfforts` for `qwen-auto`, so the client
  sends an effort and outranks laya. The router only runs for clients
  that send none. Measured on one store: 12,452 auto requests answered
  with `off`, 872 skips, zero `difficulty_routed` - an enabled router
  that changed nothing. `difficulty_router: laya` is the operator's
  answer and the only mode where a band may replace a client-declared
  effort, and only when it schedules strictly more thinking; `minder-op
  doctor`'s `difficulty-router` line reports the inert condition
  (enabled, abstentions, 0 applied) instead of leaving it to be
  discovered from the ledger. (e8e4d06)
- `install.sh` resolves the hooks.json guard mode through
  `dsh/dsh_install.py guard-default`: the `MINDER_SUCCESS_GUARD` env
  value, else the mode the live hooks.json already declares, else the
  template's pinned `advisory`. Passing the env is still the way to set
  `block` on a fresh install; omitting it now preserves the live mode
  instead of downgrading it. (fd57ddc, e8e4d06)
- Inside the zcode harness, run Python as `env -u LD_LIBRARY_PATH
  python3 ...`; the appimage library path poisons subprocess Python
  (GUI launches instead of pytest). (1aad390)
- The dsh hooks-bridge plugin version must match the dsh host version.
  A 0.1.5 bridge under a 0.1.7 host dies at `bash.run is not a function`
  inside the engine on every hook, fail-open, ~0.1 ms - minder looks
  healthy (sink ops ok) while capturing nothing. Diagnose with
  `minder-op capture` and a hook/result scan of the dsh session log.
  (07dbfc2)
- The proxy upstream is switchable on a running install via the systemd
  drop-in `minder-proxy.service.d/upstream.conf`; the CAP probe
  re-measures the engine on every proxy restart. The second upstream
  (Strata HIP server, loopback :8081) is mutually exclusive with
  llama-server on GPU VRAM: stop one engine before starting the other.
  (#1)
- systemd applies that drop-in *after* the base unit, so the drop-in wins
  and the base unit's `MINDER_UPSTREAM` becomes a value nothing reads.
  `install.sh` rewrites the base unit every run from `MINDER_UPSTREAM` or
  its own loopback `:8080` default, and ran CAP against that value
  while the proxy dialled the drop-in's - so `model_caps.json` described a
  model the proxy never contacted, and `systemctl --user cat` showed both
  files without saying which one applied. The installer now prints both
  values and leaves the drop-in alone; `minder-op doctor` has an
  `engine-upstream` line that reads the effective value from
  `systemctl --user show <unit> -p Environment` and warns when it differs
  from the registry's active engine, naming the masked base-unit value.
  The check queries the *proxy* unit, not the engine's: an engine unit is
  a server that listens on a port and declares no upstream.
  (`minder_op/unit_upstream.py`.) (dd34ed2)
- The Strata upstream serves one sequence at a time (FIFO), so
  concurrent minder-routed requests queue behind each other, and its
  tuning tooling stops the server mid-run - honest 502s from the proxy
  until it returns. It ignores request model names and reports usage on
  the final SSE chunk, so installed presets and the token ledger work
  unchanged. (#1)
- Multi-turn Strata prefix-cache measurements (2026-10-01, engine 0.1.24
  + PR #121 HIP build, 12-turn replay, ~260-token turns growing to
  ~3.6k prompt tokens; `benchmarks/multiturn_cache.py`): an append-only
  conversation with constant chat_template_kwargs re-reads ~263 tokens
  per turn (86% reuse); alternating enable_thinking per turn re-reads
  the whole conversation every turn (0% reuse, re-reads growing 826 ->
  3588 tokens, 2.3x wall at 3.6k context and linear in context length);
  a one-time history reorder costs exactly one full re-prefill (~1.6k
  tokens) and caching then resumes. Two implications: the proxy pins
  thinking kwargs per session on single-slot engines (#3), and any
  harness that mutates rendered history mid-session pays a full
  re-prefill per mutation on this engine. Caveat: the fork's OpenAI
  usage omits prompt_tokens_details.cached_tokens - reuse is visible
  only in /metrics totals (reused counter), which is why the ledger's
  cached_tokens field stays empty on Strata until the fork maps its
  `reused` counter into the usage payload. (#3)

## Code paths that failed silently

- A level-1 relative import inside `minder_decision/providers/` resolves
  against the package root (nonexistent) and died under a bare `except`,
  returning 0.0 confidence everywhere: silent all-abstain while every
  test stayed green. Measurement (routing replay) exposed it; since
  then, calibration changes pin the adapter wiring with a stub-agent
  test, not just the pure function. (8a2367b)
- `db.connect()` used to leak its connection handle whenever a migration
  failed; fixed with close-before-reraise and a broken-migration test.
  (9eed22e)
- The lesson pipeline had a consumer with no producer: `_close_on_success`
  read `hook_ev["verification"]["tests_passed"]`, a key nothing in the repo
  ever wrote, so all 228 closed episodes in the live store were `candidate`
  and `lessons` was empty. A gate on an input no path can set is dead code
  that looks like a safety property. Since issue #9 the producer is the
  hook itself: a recognised test-runner call whose output states a passing
  count and no failing count closes the session's open episode `verified`
  and records a `verification` event on it. Two traps in that area:
  `to_event()` maps the payload's `tool_response` onto `error_excerpt` and
  never copies `tool_response`, so a success-path reader must use
  `error_excerpt`; and `go test` prints per-package `ok`/`FAIL` with no
  counts, so a failing package matches none of `FAIL_SIGNS` and needs its
  own clean-run rule. (issue #9)

- `proven-red.sh` copies only the changed `tests/test_*.py` files into a
  worktree at the fork point, so a shared helper (`tests/webseed.py`,
  `tests/tracebuild.py`) stays at the pre-change version. Two traps this
  creates: a new keyword argument on a shared helper can never turn a
  test red, and a new module imported at a test file's top level makes
  every test in that file a collection error, which the gate reports as
  `WEAK - red only by missing reference` instead of a real red. Import a
  brand-new module inside the test bodies that need it. A test file whose
  only diff is a comment is a `FAIL - PASSES on pre-change code`, so
  comment-only test edits must stay out of the commit. (#16)
- `db.migrate` skips a migration whose created objects already exist, so
  `DROP TABLE x` plus a reconnect leaves the table gone. That is the
  sanctioned way to build a "store predating migration N" fixture, and it
  works for 015, 016 and 018 alike. (#16)
- A store-predating-migration fixture and a "query answered zero" case are
  different facts and the console law requires them to render
  differently. Returning `[]` for "the table is missing" collapses the
  first into the second, which is what a legacy-store test caught: the
  page said "no verdict yet" for a store that has no verdict table.
  Read helpers that feed a console count return `None` for "cannot
  answer" and `[]` for "answered zero", and every surface in between
  propagates the distinction. (#16)
- The ratchet counters run over `git ls-files` from the repo root, so a
  ratchet check needs a tree whose index matches the candidate commit;
  `git checkout-index -a` into a scratch dir leaves an empty index and
  the gate reports 0, a fake improvement (M2). Gate in a real worktree
  (`git worktree add --detach <dir> <commit>`). When the counter grows,
  run the same gate on the parent alone before touching the baseline. (#16)
- A test that asserts a SQLite structure must select the declared index
  by `PRAGMA index_list` `origin`, not by `unique`: a table's primary key
  contributes its own `sqlite_autoindex_*` with `unique = 1`, so an
  assertion over unique indexes passes when the declared pair index is
  dropped. (#16)
- `capsys.readouterr()` drains. A test that reads `.out` and then calls
  `readouterr()` again for `.err` sees the empty string and concludes the
  code never wrote to stderr. One `readouterr()` per call under test, both
  streams read off the returned object. (#26)
- Migration files are bare `CREATE TABLE`, not `IF NOT EXISTS`, so a store
  whose `PRAGMA user_version` sits below the tables it already holds cannot
  be migrated at all — the replay raises mid-file and rolls back whole.
  The repair is the number (`PRAGMA user_version = N`), never deleting the
  store. Two fixture shapes follow, and they are not interchangeable:
  `_stamped(n)` builds a store whose tables *and* version stop at n (the
  genuine behind case, migratable), while stamping a full store to a
  higher version builds the ahead case (refused). Building the second with
  the first helper yields a store with no tables and a high version, which
  tests a number instead of a state. (#26)
- Which runtime answers a schema question decides what it can apply:
  `minder_memory.db.MIGRATIONS_DIR` is the imported copy's own directory,
  so `migrate` can only ever apply the migrations beside the code that ran
  it. The installed share's files are a separate denominator (`MINDER_SHARE`
  / `MINDER_SHARE_DIR`), and on the live install the store had outgrown it —
  a store ahead of the staged share means the staged files are stale, and
  no migration can or should fix that. Tests that name a version must pin
  the share, or the denominator is whatever the developer's own
  `~/.local/share/minder` happens to ship. (#26)
- A `WEAK` proven-red verdict has more than one cause: a top-level import
  of a new module, and an autouse fixture that calls a new symbol — the
  whole file then errors at collection. Reach a new symbol through
  `getattr(mod, "_new_hook", None)` in an autouse fixture, and keep a
  fixture that only *redirects* the environment rather than one that
  asserts, or the file goes red for the wrong reason. (#28)
- The difficulty router only runs for presets marked `"class": "auto"`,
  and `difficulty_router` defaults to `shadow`, so a store with zero
  `difficulty_*` events is not evidence the router is broken — first check
  whether any request was eligible (`auto_effort` events). Before the
  abstention ledger, "consulted and did nothing" and "never consulted"
  were the same silence. (#28)
- The isolated decision worker keeps a client-side log
  (`laya-worker.log` beside the socket) because a spawn or dial failure
  inside the request path cannot be surfaced any other way: it is
  fail-open by design. One line per distinct reason per process, so a
  broken worker cannot fill the state dir. `doctor`'s `laya-worker` check
  reads it for the reason only, which is how a stale protocol-version error
  from an un-restarted worker became visible without touching the request
  path. Every line it writes carries a UTC stamp (`log_stamp.stamp()`), so
  the reason can be dated; the file's own rotation and the once-per-process
  rule mean an old line survives, which is what made an undated tail
  untrustworthy. (#28)
- The harness prefixes an operator decline with `Error: ` — a dismissed
  plan review arrives as `Error: The user dismissed the plan review to
  speak instead`. Any consumer of the shared `error:` fail sign therefore
  reads a deliberate decision as a command failure, and two of them
  escalated with a retry instruction. `is_failure` is the wrong predicate
  wherever the ladder counts: use `classify_outcome`, which tests
  `DECLINE_SIGNS` first. `is_failure` itself still answers yes for a
  decline on purpose, because the memory store's event vocabulary has no
  third value and a decline is not a verified success. (#27)
- A decline must not take the success branch either. That branch pops the
  key's failure record and refunds the think/frontier budget it spent, so
  treating a decline as success erases the evidence of a loop that is
  still running. The two-way `if not is_failure(...)` shape is the bug;
  three outcomes need three branches. (#27)
- A rendered file can carry a flag name and still be unconfigured.
  `install.sh` filled two of the three placeholders in `dsh/hooks.json`,
  so every hook command shipped `MINDER_SUCCESS_GUARD=__MINDER_
  SUCCESS_GUARD__`, and `guard_mode()` maps any unrecognised value to
  `off`. The loop stop was inert while `doctor` reported the flag as
  present. Two rules follow: a mode belongs in the template as a real
  value, never as a placeholder awaiting substitution; and one render
  path must not hand-roll `sed` for a field another module owns -
  `install.sh` now calls `dsh_install render-hooks` so both paths share
  the guard rule and the refuse-to-write check. Staging also has to keep
  the installed hooks.json, or the live mode is gone before the render
  can read it. (#29)
- A ratio whose two sides count different units cannot fail in the
  direction it exists for. `coverage` compared all `hook_timing` ledger
  rows in a window against PostToolUse invocations only, and `hook.py`
  writes one timing row per invocation at *both* hook points, so
  persisted was double the invocations and `min(1.0, persisted /
  invocations)` pinned the ratio at 1.0: the floor could only fire when
  the whole hook path was dead, never when half of it was. Filter both
  sides to one unit (`hook_event` on the ledger row) before trusting the
  verdict. (5fe1708)
- The dsh session log records a hook twice - `hook/invoked` and
  `hook/result` - and the cost (`durationMs`) is on the result, not the
  invocation. The two join on `handlerId`, which names one call on both
  record types; a fixture that writes different ids on the pair hides
  that join from any test which relies on it. `time` on those records is
  epoch **milliseconds**, while the events ledger's `ts` is seconds.
  (5fe1708)
- Session logs are append-only, so a byte cap that keeps the head keeps
  the oldest records and drops the recent ones. A windowed scan must
  take the tail (`dsh_sessions.log_scan(..., tail=True)`) and report
  `truncated` when the cap bit, otherwise a busy session reads as idle
  and the count reads as complete. `coverage.complete` is that flag;
  `minder_op/capture_ground.py` owns the counting. (7175dd3)

- A catch-all that returns `None` makes a programming error
  indistinguishable from a legitimate zero, and a constant borrowed from
  another package is a name that does not exist. The store side of
  capture coverage passed `limit=queries.MAX_LIMIT` - the name lives in
  `minder_web.services`, where 200 is a display cap - so the call raised,
  the `except Exception: return None` swallowed it, and `persisted_db`
  was `None` on every install while the report still looked healthy. Two
  rules: a count belongs in SQL (`ts >= ?`), not in a fetch-then-filter
  whose limit silently truncates it; and a "could not measure" state
  needs its own field (`db_measured`), never the same value a zero
  produces. (32b4c4b)
- **A ledger nobody reads back is a ledger nobody acts on.** Migration
  015 wrote a row for every injection decision, including the
  `lesson_id IS NULL` ones, precisely so retrieval could be measured. The
  write side was right and the read side was missing: no command, panel
  or summary divided hits by decisions, so the learning plane's only
  question had no number. Two related traps showed up building it. The
  `lesson_id IS NULL` bucket renders as a lesson with an empty id unless
  it is counted and labelled as a miss, and `injection_counts` returned
  `[]` for both "no rows" and "no table" — the same `None` vs zero mistake
  as #31's `persisted_db`. A read helper that degrades must distinguish
  "nothing" from "cannot say" in its return shape. (8f39374)

## Platform quirks

- SQLite WAL allows a plain read on a second connection while a write
  transaction holds the lock; a nested `BEGIN IMMEDIATE` busy-times out
  instead. Read helpers must never take the write path (see the SQLite
  access contract).

## Libraries and state

- State layout: `~/.local/state/minder` (events.jsonl ledger,
  memory.sqlite, hook-trace.jsonl, consults.jsonl, sink.token). The
  events ledger and the sqlite store are separate write paths; capture
  coverage compares them.
