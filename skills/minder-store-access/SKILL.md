---
name: minder-store-access
description: Read or write minder's sqlite store from inside the harness file sandbox. Use when sqlite3 raises "unable to open database file" against the minder state dir, when a query against minder tables raises "no such column" or "no such table", or when a copy tool reports Read-only file system on the state dir or the repo. Not for application SQL design, migrations, or a database that is not minder's.
---

# minder-store-access

The state dir is outside the sandbox's writable root and the schema moves
under you. Both failures look like data problems and are not.

## Sequence

1. Copy the store out of the state dir before opening it. The sandbox refuses
   the WAL sidecar write and reports that as a read failure; a copy in the
   workspace opens cleanly. Completion: the file you connect to is inside the
   workspace, and the connect returns without raising.
2. Introspect before you project. Run `PRAGMA table_info` on every table you
   are about to select from, and `sqlite_master` before assuming a table
   exists. Completion: every column in your `SELECT` list is one you saw in
   that output, not one you remembered.
3. Confirm the field before you measure it. A column that is uniformly zero
   across every row is unwritten, and a number nothing varies is not a gate
   input. Completion: the value you are about to report differs somewhere in
   the data, or you say it does not.
4. Read [`../bodies/minder-store-access.md`](../bodies/minder-store-access.md)
   when a signature you have is not covered by steps 1 to 3.

The signature-to-correction table, the worked example, and the Avoid list
live in that body; it is the one place they are written down.
