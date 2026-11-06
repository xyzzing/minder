#!/usr/bin/env bash
# minder-dispatch-poll: one dispatch intake poll under systemd (issue #89).
# Logs one line pair per poll. A failed poll (gh unreachable, bad json,
# unreadable config) exits nonzero so the timer surfaces it in journald.
# No AI session is involved: intake is a deterministic command, and it
# must not share a failure domain with an assistant session. Stage 1 is
# observe mode: nothing is dispatched, whatever the gate records.
set -u

DISPATCH_DIR="${MINDER_DISPATCH_DIR:-$HOME/projects/minder}"
cd "$DISPATCH_DIR" || exit 1

exec env -u LD_LIBRARY_PATH python3 -m minder_op dispatch poll
