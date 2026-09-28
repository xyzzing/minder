# Documentation playbook

Read before editing user or developer documentation.

## Scope

- `docs/` is local-private and NEVER committed (see
  `agents/project/out-of-scope.md`). The committed surfaces are
  README.md, CHANGELOG.md, CONTRIBUTING.md, and the instruction files
  (AGENTS.md, AGENTS.project.md, `agents/**`).
- User-visible behavior changes update README.md (features, flags,
  install) and a CHANGELOG entry.
- New modules, entry points, or contracts update AGENTS.project.md
  through the self-improvement protocol - never by a drive-by edit.

## Style

- Write and edit with the slop-mop skill. The rules below add what it
  does not cover.
- Write like a developer explaining to a colleague. No headline-style
  headings and no news-article cadence.
- Examples must grep-hit the codebase unless marked simplified. Cite
  symbols, never line numbers; line numbers rot within hours.
- Docs cite AGENTS.md rule IDs or AGENTS.project.md contract names
  instead of copying process rules.
- CHANGELOG: released sections are `## <version> - <date>`; unreleased
  work accumulates under one `## Unreleased - <topic>` heading and is
  consolidated at release. The version source is `MINDER_VERSION` in
  `minder.py` (version single-source contract).
- Console copy: plain hyphens, never an em-dash; `n/a` means empty or
  unavailable source, never zero.
