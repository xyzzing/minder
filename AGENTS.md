# Development Guidelines

Portable core. This file contains no project-specific names and copies
verbatim into any project. Load order: this file, then
`AGENTS.project.md` (architecture contracts, project rules, verification
commands, playbooks), then the playbook it lists for your work area.

Adopting this core in another project: copy this file unchanged, then write
your own `AGENTS.project.md` and the gates it names. Project facts never
belong in this file.

## Rule format

Every rule is a statement, a one-clause why, and a gate. A rule a script
could check but names no gate is a defect here. Rules carry stable IDs by
tier; docs reference IDs, never copied text.

## Invariants (never simplified away)

- I1. Validate at trust boundaries: server responses, user input, IPC.
  Malformed input is routine, not rare.
- I2. Destructive operations need error handling and a recovery path. Lost
  user data cannot be patched later.
- I3. Security and accessibility are never traded for simplicity or speed.
  Gate: blocking accessibility and correctness lints.

## Process

- P1. Create or use an issue before feature or bug work, after checking the
  out-of-scope ledger; land through an issue-linked PR whose body quotes the
  issue's acceptance lines. Commits reference the issue; closing keywords
  only after the user confirms. If instructed to push directly to the default branch,
  do so and verify the issue timeline. Typo-level fixes and doc
  corrections with no behavior change need no issue.
- P2. Test first: a failing test precedes the implementation of every
  feature and bugfix. A test that has never failed does not demonstrate it
  can catch the bug. Changes an existing gate already covers fully rely on
  that gate instead of a bespoke new test. Gate: the proven-red CI job runs
  each change's tests against the pre-change code, fails when they pass, and
  says whether the red was an assertion or only a missing symbol.
- P3. Run the gates covering the change before every commit; run the full
  suite before push or PR. Never commit after a failed or unrun gate.
- P4. Read failures and fix the cause. Never blindly retry.
- P5. One logical change per conventional commit.
- P6. Verification runs direct commands. Tooling that transforms output,
  such as wrappers, compressors, or summarizers, is untrusted until
  validated once against raw output.
- P7. Finish the requested behavior. Materially different UX options need
  approval before choosing.
- P8. Never merge the default branch without approval.
- P10. Docs move with behavior: user docs for changed behavior, developer
  docs and call flows for new APIs, components, hooks, and utilities. All
  prose reads like a developer explaining to a colleague: no marketing
  language, filler, headline headings, aphorisms, or news cadence.

## Code

- C1. Reuse ladder: existing codebase helper, then stdlib, then platform
  feature, then installed dependency, then new code. A new dependency is a
  last resort.
- C2. Keep files under 400 lines of code. No dead code, commented-out
  replacements, or speculative abstractions. Gate: the lint ratchet holds
  the count of over-long files.
- C3. Never hardcode user-facing text; every locale updates together.
- C4. Never inline semantic values; constants live in their dedicated
  modules.
- C5. New modules live in domain folders.
- C6. Test assertions must be able to fail: assert fetched values or
  user-visible outcomes, never element existence or child count. Gate: the
  quality ratchet.
- C7. The lint ratchet baseline shrinks or holds, never grows. Raising a
  number by hand needs a reason in the commit message.

## Meta (governs this file)

- M1. A rule a script can check needs a gate, added in the same change.
  Ungated rules drift; an audit of ungated rules found every one violated
  while every gated rule held.
- M2. A gate's input needs checking, not just its exit code. Confirm the
  number a gate reports describes what it claims to measure.
- M3. Instruction files change only through the self-improvement protocol
  below. One-off facts go to the project file or a playbook, never here.
- M4. This file owns process rules; other docs link to rule IDs and never
  copy the text.
- M5. Project facts (API quirks, platform behavior, failed approaches) go
  to the domain playbook, proven workflow practices to the generic
  playbooks, through the protocol, never only into agent memory, which no
  other agent sees. Repo files carry no
  personal or private data; only such specifics (names, hosts,
  credentials) and unproven taste stay in agent memory.

## Self-improvement protocol

Trigger: a breakage, review finding, or wasted session an instruction or
contract would have prevented, or an instruction that itself caused harm.

Action: the PR fixing the problem also proposes the instruction edit, with
the gate M1 requires. The maintainer merges or rejects it like any diff.
Agents never edit instruction files outside this protocol.

## Project knowledge

Architecture contracts, project rules, verification commands, and the
playbook table live in `AGENTS.project.md`. Each contract states what it
owns, the sanctioned path, forbidden bypasses, and the gate. Trust the
contract over rediscovering the invariant from code; a code/contract
mismatch is a finding for the self-improvement protocol.

<!-- graft:start -->
## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

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
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
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
