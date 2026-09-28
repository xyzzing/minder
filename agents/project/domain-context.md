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
