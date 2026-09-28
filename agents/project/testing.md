# Testing playbook

Read before writing or changing tests.

## Test design

- Test outcomes an operator sees: persisted rows, rendered page content,
  exit codes, escalation directives, verdicts - never "no exception was
  raised" or `is not None` alone (C6).
- Name the seam under test before writing the test: the highest
  interface that reaches the behavior. minder's seams, high to low:
  HTTP routes (`minder_web/app.py` via TestClient), CLI commands
  (`minder_op/cli.py` via main()), the hook process boundary
  (`hook.py` via subprocess in tests/test_hook.py), then module APIs.
- Mock only the system boundary (the upstream model server, the
  frontier command, the filesystem via tmp_path). Web tests seed a
  real sqlite through `tests/webseed.py` - never a mocked store; a mock
  of the repo's own modules is counted by the ratchet if introduced.
- Prove red before green: a bug fix starts by showing the new test
  fails on the pre-fix code (`scripts/proven-red.sh <base> <head>`;
  CI runs it on PRs).
- A new gate assertion (anything under `tests/test_instruction_gate.py`
  or `scripts/`) is proven red against a scratch violation, then the
  scratch is removed in the same commit.
- Characterization tests (pinning current behavior ahead of a refactor)
  are marked with a `# CHARACTERIZATION: current behaviour, <why>`
  comment so a later change may flip, not silently break, them.
- No fixed sleeps; the suite uses timeouts (MINDER_TEST_TIMEOUT) and
  polls where it must wait.

## Commands

- Full suite: `make gates` (or `env -u LD_LIBRARY_PATH python3 -m
  pytest -q`). Inside the zcode harness the `env -u` is required; CI
  does not need it.
- One module: `env -u LD_LIBRARY_PATH python3 -m pytest
  tests/test_<module>.py -q`.
- Web pages: tests/test_web_*.py run against TestClient with the
  loopback Host header (`tests/webseed.py` enforces it); a new page
  gets a test seeding the store, including a secret fixture the page
  must not render.
- Mutation smoke: `scripts/mutation-smoke.sh` (comparator verdict law,
  secret redaction, transaction commit) - extend the target list when a
  new module joins that risk class.
