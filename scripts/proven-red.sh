#!/bin/sh
# Proven red (gap-trap, P2): for a range, run each CHANGED unit test
# file against the code from BEFORE the range, in a worktree at the
# fork point, and fail when a test passes there. A test that has never
# failed does not demonstrate it can catch the bug.
#
#   scripts/proven-red.sh <base> [head]
#
# Exit 0: every changed test was red on the old code (or an announced
# skip). Exit 1: a changed test passed on the old code, or source
# changed with no test change. Exit 2: the gate could not run.
set -u
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR/.."
base=${1:?usage: proven-red.sh <base> [head]}
head=${2:-HEAD}

fork=$(git merge-base "$base" "$head") || {
  echo "proven-red: no merge-base of $base..$head" >&2; exit 2; }

changed=$(git diff --name-only "$fork".."$head")
[ -n "$changed" ] || { echo "proven-red: empty range; nothing to prove"; exit 0; }

# A changed file that is neither a test, a gate, nor instruction/docs
# prose counts as source.
is_source() {
  case "$1" in
    tests/*|scripts/*|AGENTS*|CLAUDE.md|agents/*|*.md|Makefile|\
.github/*|.githooks/*|.gitignore|.ratchet-*|pyproject.toml|\
CONTRIBUTING.md) return 1;;
    *) return 0;;
  esac
}
source_changed=0
for f in $changed; do is_source "$f" && source_changed=1; done

tests_changed=$(printf '%s\n' $changed |
  grep -E '^tests/test_.*\.py$' | grep -v '^$' || true)

if [ -z "$tests_changed" ]; then
  if [ "$source_changed" -eq 0 ]; then
    echo "proven-red: skip - only gates/docs/instruction files changed"
    exit 0
  fi
  echo "proven-red: FAIL - source changed but no unit test did:" >&2
  printf '%s\n' $changed | while read -r f; do is_source "$f" && echo "  $f" >&2; done
  exit 1
fi

WT=$(mktemp -d /tmp/proven-red.XXXXXX)
trap 'git worktree remove --force "$WT" >/dev/null 2>&1; rm -rf "$WT"' EXIT
git worktree add --detach "$WT" "$fork" >/dev/null 2>&1 || {
  echo "proven-red: could not create worktree at $fork" >&2; exit 2; }

fail=0; ran=0
for t in $tests_changed; do
  [ -f "$t" ] || continue   # deleted at head: the worktree copy passes, skip
  mkdir -p "$WT/$(dirname "$t")"
  cp "$t" "$WT/$t"
  out=$(cd "$WT" && env -u LD_LIBRARY_PATH python3 -m pytest -q "$t" 2>&1)
  rc=$?
  ran=$((ran + 1))
  if [ "$rc" -eq 0 ]; then
    echo "proven-red: FAIL - $t PASSES on pre-change code ($fork)" >&2
    fail=1
    continue
  fi
  if printf '%s' "$out" | grep -qE "AssertionError|AssertionError|^E +assert|FAILED"; then
    echo "proven-red: ok - $t red by assertion on pre-change code"
  elif printf '%s' "$out" | grep -qE "ModuleNotFoundError|ImportError|cannot import|not a function|AttributeError|No module named|FileNotFoundError|ERROR collecting"; then
    echo "proven-red: WEAK - $t red only by missing reference on pre-change code" >&2
  else
    echo "proven-red: ERROR - $t exited $rc with no recognizable pytest report" >&2
    exit 2
  fi
done
[ "$ran" -gt 0 ] || {
  echo "proven-red: no changed test file existed at head; nothing run" >&2
  exit 2; }
[ "$fail" -eq 0 ] && echo "proven-red: ok ($ran test file(s) proven red)"
exit "$fail"
