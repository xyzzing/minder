#!/bin/sh
# Ratchet (gap-trap): named counts in .ratchet-baseline may fall or
# hold, never grow. Counters are one-liners in .ratchet-counters
# ("name|command"), each printing exactly one whole number.
#
#   scripts/ratchet.sh            check (exit 1 on growth, 2 on error)
#   scripts/ratchet.sh --update   lower the baseline to today's counts
#
# Raising a number is a hand edit to .ratchet-baseline with a reason in
# the commit message (C7). --update refuses to write a rise.
set -u
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASELINE="$DIR/../.ratchet-baseline"
COUNTERS="$DIR/../.ratchet-counters"
mode=${1:-check}
[ "$mode" = "--update" ] && mode=update
[ -f "$COUNTERS" ] || { echo "ratchet: no $COUNTERS" >&2; exit 2; }
[ -f "$BASELINE" ] || { echo "ratchet: no $BASELINE (seed it)" >&2; exit 2; }

fail=0
while IFS='|' read -r name cmd; do
  case "$name" in ''|'#'*) continue;; esac
  real=$(eval "$cmd" 2>/dev/null) || {
    echo "ratchet: counter $name failed to run" >&2; exit 2; }
  case "$real" in ''|*[!0-9]*)
    echo "ratchet: counter $name printed '$real', not a whole number" >&2
    exit 2;; esac
  base=$(awk -v n="$name" '$1 == n {print $2}' "$BASELINE")
  if [ -z "$base" ]; then
    echo "ratchet: $name has no baseline entry; seed .ratchet-baseline" >&2
    exit 2
  fi
  if [ "$real" -gt "$base" ]; then
    echo "ratchet: $name grew $base -> $real; lower the count or hand-raise the baseline with a reason (C7)" >&2
    fail=1
  elif [ "$real" -lt "$base" ]; then
    echo "ratchet: $name improved $base -> $real"
    if [ "$mode" = update ]; then
      awk -v n="$name" -v v="$real" '$1 == n {$2 = v} {print}' \
        "$BASELINE" > "$BASELINE.new" && mv "$BASELINE.new" "$BASELINE"
    fi
  fi
  # A baseline sitting far above the real count is a raised number
  # nobody lowered back (M2). Slack: 10%, minimum 2. Check mode only:
  # --update exists precisely to reconcile the baseline down.
  if [ "$mode" = check ]; then
    slack=$((base / 10)); [ "$slack" -lt 2 ] && slack=2
    gap=$((base - real))
    [ "$gap" -gt "$slack" ] && {
      echo "ratchet: $name baseline $base sits $gap above real $real; run --update" >&2
      fail=1; }
  fi
done < "$COUNTERS"

# Every baseline name must have a counter: deleting a counter cannot
# retire the number it held.
while read -r bname _bcount; do
  case "$bname" in ''|'#'*) continue;; esac
  grep -q "^$bname|" "$COUNTERS" || {
    echo "ratchet: baseline name $bname has no counter" >&2; exit 2; }
done < "$BASELINE"

[ "$fail" -eq 0 ] && echo "ratchet: ok"
exit "$fail"
