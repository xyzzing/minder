#!/usr/bin/env bash
# minder-capture-canary: run the capture canary under systemd (issue #73).
# Green: one journald line, exit 0. Red: desktop notification with the
# reason codes, exit 1. --digest: fuller status line plus a normal-urgency
# notification, for the 09:00 timer. No AI session is involved: the canary
# is a deterministic command, and a watchdog must not share a failure
# domain with an assistant session.
set -u

CANARY_DIR="${MINDER_CANARY_DIR:-$HOME/projects/minder}"
cd "$CANARY_DIR" || exit 1

report=$(env -u LD_LIBRARY_PATH timeout 500 python3 -m minder_op.capture_canary 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$report" ]; then
    notify-send -u critical "CAPTURE CANARY RED" \
        "canary crashed or produced no verdict - journalctl --user -u minder-capture-canary"
    echo "canary crashed or produced no verdict"
    exit 1
fi

read -r verdict summary reasons <<<"$(python3 - "$report" <<'PYEOF'
import json, sys
r = json.loads(sys.argv[1])
# comma-joined: read -r splits on whitespace, so the summary must be one word
summary = ",".join(f"{s['id']}:{s['status']}" for s in r["surfaces"])
reasons = "; ".join(f"{s['id']} {s.get('reason', 'ok')}: {s['detail'][:120]}"
                    for s in r["surfaces"] if s["status"] == "fail")
print(r["verdict"], summary, reasons or "-")
PYEOF
)"

if [ "$verdict" = "green" ]; then
    if [ "${1:-}" = "--digest" ]; then
        echo "daily digest: canary green ($summary)"
        notify-send -u normal "minder capture digest" "canary green: $summary"
    else
        echo "canary green ($summary)"
    fi
    exit 0
fi

echo "canary RED: $reasons"
notify-send -u critical "CAPTURE CANARY RED" "$reasons"
exit 1
