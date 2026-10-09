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
- Run `install.sh` with `MINDER_SUCCESS_GUARD=block`; without the env it
  renders the live hooks.json guard as advisory - a silent downgrade of
  the verified operator setting. (fd57ddc)
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
