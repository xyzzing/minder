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
# Counters run on a temp copy of the baseline: a counter that reads
# .ratchet-baseline (or greps the tree for it) must see the committed
# numbers, not one another counter's --update result from this same run.
# Without this, a counter that counts baseline lines reported 4 -> 5 and
# the ratchet aborted mid-run, leaving earlier counters already lowered.
BASELINE_TMP=$(mktemp "${TMPDIR:-/tmp}/ratchet-baseline.XXXXXX")
cp "$BASELINE" "$BASELINE_TMP"
trap 'rm -f "$BASELINE_TMP"' EXIT
while IFS='|' read -r name cmd; do
  case "$name" in ''|'#'*) continue;; esac
  real=$(eval "$cmd" 2>/dev/null) || {
    echo "ratchet: counter $name failed to run" >&2; exit 2; }
  case "$real" in ''|*[!0-9]*)
    echo "ratchet: counter $name printed '$real', not a whole number" >&2
    exit 2;; esac
  base=$(awk -v n="$name" '$1 == n {print $2}' "$BASELINE_TMP")
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
        "$BASELINE_TMP" > "$BASELINE.new" && mv "$BASELINE.new" "$BASELINE_TMP"
    fi
  fi
  # A baseline sitting above the real count is a raised number nobody
  # lowered back (M2). Zero tolerance for a gap: the cheapest way to
  # make a violation legal is to raise its floor, and the only
  # sanctioned way to lower it (--update, check mode) is right there.
  # The old 10%/min-2 slack let a 0 -> 1 raise pass silently at small
  # counts, which is exactly where a new violation lands (found in
  # windtunnel, same script: runtime_deps 0 -> 1 stayed green).
  if [ "$mode" = check ]; then
    gap=$((base - real))
    [ "$gap" -gt 0 ] && {
      echo "ratchet: $name baseline $base sits $gap above real $real; run scripts/ratchet.sh --update (a raised floor needs a reason in the commit message, C7)" >&2
      fail=1; }
  fi
done < "$COUNTERS"

# Every baseline name must have a counter: deleting a counter cannot
# retire the number it held.
while read -r bname _bcount; do
  case "$bname" in ''|'#'*) continue;; esac
  grep -q "^$bname|" "$COUNTERS" || {
    echo "ratchet: baseline name $bname has no counter" >&2; exit 2; }
done < "$BASELINE_TMP"

[ "$fail" -eq 0 ] && echo "ratchet: ok"
exit "$fail"
