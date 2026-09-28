# minder Project Instructions

Read `AGENTS.md` first. Contracts, project rules, verification, playbooks.
The sanctioned path is the only path; a bypass is a bug even when it works.

## Architecture contracts

### SQLite access
Owns: every sqlite connection, transaction, and migration.
Path: `connect`, `transaction`, `write` (`minder_memory/db.py`); reads via
`sqlite3.connect` (`minder_op/queries.py`).
Never: `sqlite3.connect` or a `BEGIN IMMEDIATE` execute anywhere else;
nested write transactions (a read helper must stay on its own plain
connection - WAL allows the concurrent read, a nested BEGIN busy-times out).
Gate: `tests/test_instruction_gate.py` (greps `sqlite3.connect` and
`BEGIN IMMEDIATE` outside the two sanctioned files); review for nesting.

### Outbound HTTP
Owns: every network request minder makes.
Path: `http_json` (`adapter.py`); `urlopen` (`proxy.py`) for the upstream.
Never: `requests.` / `httpx.` or any third-party HTTP client; a new
runtime dependency.
Gate: `tests/test_instruction_gate.py` (greps the client libraries);
the ratchet holds runtime_deps at zero.

### Process execution
Owns: spawning subprocesses from hooks.
Path: `run_frontier` (`hook.py`) via `shlex.split` with `shell=False`.
Never: `shell=True`, `os.system(`, `eval(`, `exec(`.
Gate: `tests/test_instruction_gate.py` (greps all four).

### Secret redaction
Owns: stripping key-shaped strings from any text before storage or display.
Path: `redact` (`minder_core/identity.py`), `safe` (`minder_op/format.py`),
`_safe_row` (`minder_web/services.py`).
Never: a raw free-text DB field (error excerpt, payload, lesson body)
rendered or stored unredacted; a test fixture secret reaching a page.
Gate: review - the bypass is an omitted call, which no text search settles.

### Version single-source
Owns: the minder version string.
Path: `MINDER_VERSION` (`minder.py`).
Never: a version literal in non-test source anywhere else.
Gate: `tests/test_instruction_gate.py` (greps `0.8.` outside `minder.py`).

## Project rules

- Run commands from the repo root. Run `git config core.hooksPath
  .githooks` once per clone so the pre-commit gates exist.
- This repo lands on `main` by direct push (P1 exception, owner's
  release.sh flow; main is not branch-protected). Issue-first still
  applies to feature and bug work when feasible; PRs, when used, carry
  `## Acceptance` lines (the `pr-acceptance` CI job checks).
- Fail-open handlers: a bare `except Exception: pass` only where the
  failure is documented as harmless; a diagnostically-critical fail-open
  names the failure via `minder.log` first. Gate: the ratchet holds
  `bare_except_pass` (may fall, never grow).
- No runtime dependencies; optional extras only (`[web]`). Gate: the
  ratchet holds `runtime_deps` at 0.
- Console copy: plain hyphens, never an em-dash, in anything a browser
  renders; `n/a` means the source is empty or unavailable, never zero.
  Gate: `tests/test_instruction_gate.py` (templates + string literals).
- Run `pytest` as `env -u LD_LIBRARY_PATH python3 -m pytest` inside the
  zcode harness (the appimage library path poisons subprocess python).
- Prose people read (docs, commit bodies, PR and issue bodies, review
  comments) is written with the slop-mop skill; prose reviews run its
  detect mode on the harness's top coding model.
- Console (web UI) changes that alter behavior or navigation need a
  `tests/test_web_*` pin on the rendered content; cosmetic changes rely
  on existing gates.
- A `feat` push links its spec or says why it needs none.
- GitHub comments by an agent end with `Posted by <agent>, assisting
  @<login>.` where `<login>` comes from `gh api user --jq .login`.

- Measured, never assumed: anything the proxy does to a request must be
  probed first (CAP) or degrade honestly; no hard-coded model dialects.
  Gate: review.
- Additive integrations: never mutate a user's harness config without
  backup + verify + rollback (`install.sh` / `dsh/dsh_install.py`).
  Gate: review.
- Honest degradation: a missing key, plugin, or systemd unit is a WARN
  with manual instructions, never a silent skip or a crash. Gate:
  review.

## Verification

```
make gates                 # suite (incl. instruction gate), ruff, ratchet
env -u LD_LIBRARY_PATH python3 -m pytest -q        # suite alone
ruff check .                                       # lint
mypy minder_core minder_memory minder_decision minder_trace \
  minder_op minder_web --ignore-missing-imports    # types (CI-pinned)
```

Per commit, run what the change touches; the full set before push. CI
also runs proven red (P2) on PRs and `pr-acceptance`. Ratchet baselines
lower with `scripts/ratchet.sh --update`, which refuses to write a rise;
raising one is a hand edit to `.ratchet-baseline` with a reason in the
commit message (C7). State completed checks in handoff.

## Playbooks

Read each listed playbook before work in that area.

| Work | Read first |
|---|---|
| Multi-agent or long-running work | `agents/generic/agent-workflows.md` |
| Naming, briefs, docs, proposing work | `agents/project/glossary.md`, `agents/project/out-of-scope.md` |
| Tests or platform checks | `agents/project/testing.md` |
| Documentation | `agents/project/documentation.md` |
| minder runtime quirks and failed approaches | `agents/project/domain-context.md` |

Portable playbooks live in `agents/generic/`; project ones in
`agents/project/`.

<!-- graft:start -->
## Graft — repo context graph

This repo is indexed with `graft` (if you have the `graft` CLI): small linked
markdown nodes that explain each system and carry exact file:line spans. The
`graft/` directory is **gitignored** (regenerable) — after cloning, run
`graft build` once (deterministic, no API key) to create the local graph.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back to
  raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->
