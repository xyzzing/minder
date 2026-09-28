#!/bin/sh
# Mutation smoke (gap-trap): flip one branch in each risky module and
# require that module's tests to FAIL. The one check that proves
# existing tests can fail. Restores the file whether or not the run
# failed. Add a target here when a module joins this risk class
# (auth, parsers, verdict laws, money-adjacent paths).
set -u
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR/.."

run_tests() {
  env -u LD_LIBRARY_PATH python3 -m pytest -q "$1" >/dev/null 2>&1
}

mutate() {  # file, from, to (first occurrence only)
  env -u LD_LIBRARY_PATH python3 - "$1" "$2" "$3" <<'PY'
import sys, pathlib
f, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f)
t = p.read_text()
assert old in t, f"mutation anchor not found in {f}"
assert t.count(old) == 1, f"mutation anchor not unique in {f}"
p.write_text(t.replace(old, new, 1))
PY
}

smoke() {  # label, file, from, to, tests
  label=$1; file=$2; from=$3; to=$4; tests=$5
  cp -p "$file" "$file.gt-smoke"
  if ! mutate "$file" "$from" "$to"; then
    echo "mutation-smoke: BROKEN - $label anchor missing; fix the target" >&2
    drop_cache "$file"; mv "$file.gt-smoke" "$file"; exit 2
  fi
  if run_tests "$tests"; then
    echo "mutation-smoke: FAIL - $label: $tests still pass with the mutation" >&2
    drop_cache "$file"; mv "$file.gt-smoke" "$file"; exit 1
  fi
  echo "mutation-smoke: ok - $label (tests failed as required)"
  # The pyc header stores whole-second mtimes: a mutate+restore inside
  # one second leaves the MUTATED bytecode looking valid (this shipped
  # a false comparator failure once). Drop the cache on restore.
  drop_cache "$file"
  mv "$file.gt-smoke" "$file"
}

drop_cache() {
  find "$(dirname "$1")" -maxdepth 2 -name __pycache__ \
    -exec rm -rf {} + 2>/dev/null || true
}

# 1. The benchmark verdict law: an unsafe metric must fail at ANY value
#    above zero, not above one.
smoke "comparator unsafe-at-any-nonzero" \
  minder_core/comparator.py \
  'cand[metric] > 0' \
  'cand[metric] > 1' \
  tests/test_op_benchmark.py

# 2. Secret redaction: the API-key regex must actually strip.
smoke "identity redact strips keys" \
  minder_core/identity.py \
  '_API_KEY_RE.sub(_REDACTED, str(text or ""))' \
  '_API_KEY_RE.sub(lambda m: m.group(0), str(text or ""))' \
  tests/test_web_services.py

# 3. transaction() must commit on normal exit.
smoke "db.transaction commits" \
  minder_memory/db.py \
  'yield conn
            conn.execute("COMMIT")' \
  'yield conn
            conn.execute("ROLLBACK")' \
  tests/test_db_transaction.py

# 4. Integrity (9A): a neutered protected-path diff must fail the
#    verdict tests - the I-1 law cannot silently stop being enforced.
smoke "integrity protected diff" \
  minder_core/integrity.py \
  'for rel in sorted(set(pre_files) | set(post_files)):' \
  'for rel in sorted(set() | set()):' \
  tests/test_integrity.py

echo "mutation-smoke: ok (4 targets)"
