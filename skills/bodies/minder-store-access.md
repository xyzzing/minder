# minder-store-access reference

One row per signature the sandbox actually produces. The correction is what
to do, not what it means.

| signature | correction |
|---|---|
| `sqlite3.OperationalError: unable to open database file` on a path under `~/.local/state/minder/` | the sandbox refuses the WAL sidecar write, not the read. `cp` the `.sqlite` into the workspace and open the copy there. |
| `Read-only file system` from `sed`, `cp`, or `mv` aimed at the state dir or the repo | the path is outside the writable root. Copy the input out, work on the copy, and leave the original alone. |
| `no such column: X` on a `SELECT` | `PRAGMA table_info(<table>)` before projecting anything. The schema is versioned (the console header prints the version) and moves under you. |
| `no such table` | `SELECT name FROM sqlite_master WHERE type='table'` first. Roughly 20 of the 33 tables are empty scaffolding with a schema but no rows. |
| a `.sqlite` beside orphaned `-shm` / `-wal` files | those siblings are leftovers of a copy whose writer is gone. The `.sqlite` with no live `-wal` beside it is the safe read target. |
| a count or a column that is uniformly zero across every row | the field exists but nothing writes it. Confirm against an excerpt that must differ before trusting it as a gate input. |

## Bad and good

Bad — the connect fails and the run stops:

```
$ python3 -c "import sqlite3; sqlite3.connect('/home/zacch/.local/state/minder/memory.sqlite')"
sqlite3.OperationalError: unable to open database file
```

Good — copy first, then open the copy:

```
$ cp /home/zacch/.local/state/minder/memory.sqlite ./scratch.sqlite
$ python3 -c "import sqlite3; print(sqlite3.connect('./scratch.sqlite').execute('select count(*) from skill_gaps').fetchone())"
(607,)
```

## Avoid

- Opening the live store write-mode in order to read it.
- Guessing a column name because a sibling table has one.
- Reading `success_observations.exit_code` as a real exit code: it is `0` on
  every row, including rows whose excerpt begins `[stderr]`.
- Treating `skill_gaps.sample_error` as the error. It is the tail of the
  whole tool response.
