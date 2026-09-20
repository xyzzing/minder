# Contributing to minder

Thanks for your interest. minder is deliberately small: Python 3.10+
**stdlib only** at runtime (the test suite additionally needs `pytest`), no
root, no pip install. Please keep it that way unless a change genuinely
requires otherwise.

## Running the tests

```sh
MINDER_NO_SYSTEMD=1 python3 -m pytest tests/ -q
```

- `MINDER_NO_SYSTEMD=1` is required unless you actually want the suite to
  touch your user systemd units — install/uninstall paths are per-user.
- The suite is hermetic: all traffic hits an in-repo mock upstream, and state
  is isolated into a temp dir. A slow machine may need `MINDER_TEST_TIMEOUT=120`.
- Please run the suite before opening a PR; CI runs the same command on
  Python 3.10–3.12.

## Design conventions

- **Measured, never assumed**: anything the proxy does to a request must be
  probed first (CAP) or degrade honestly. No hard-coded model dialects.
- **Additive integrations**: never mutate a user's harness config without
  backup + verify + rollback (see `install.sh` / `dsh/dsh_install.py`).
- **Honest degradation**: a missing key, missing plugin, or missing systemd
  is a WARN with manual instructions, never a silent skip or a crash.
- **Architectural decisions** get a numbered ADR in `docs/adr/` (see 0001–
  0004 for the format and the existing decisions — read them before proposing
  changes to the integration seams).

## Filing issues

Include: harness (dsh / zcode / other), the relevant lines from
`~/.local/state/minder/events.jsonl`, and `minder.py report` output. Logs
first, screenshots second.
