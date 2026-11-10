#!/usr/bin/env bash
# minder-capture-canary: run the capture canary under systemd (issues #73, #94).
# --fast (the 30-minute timer) checks drift + freshness only: no network
# probe, no dsh spawn, no model. --full (the daily off-peak E2E) adds the
# endpoint probes and the live synthetic capture run, which needs the model
# stack. Green: one journald line, exit 0. Red: desktop notification with
# the reason codes, exit 1. --digest: fuller status line plus a
# normal-urgency notification, for the 09:00 timer.
#
# The watchdog must not share a failure domain with an assistant session,
# so the frequent pass stays model-free; only --full is allowed to boot dsh.
set -u

CANARY_DIR="${MINDER_CANARY_DIR:-$HOME/projects/minder}"
cd "$CANARY_DIR" || exit 1

mode=()
digest=0
for arg in "$@"; do
    case "$arg" in
        --fast|--full) mode+=("$arg") ;;
        --digest) digest=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

report=$(env -u LD_LIBRARY_PATH timeout 500 python3 -m minder_op.capture_canary "${mode[@]}" 2>/dev/null)
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
    if [ "$digest" = "1" ]; then
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
