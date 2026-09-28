# Out of scope

Requests the maintainer has declined, with the reason, so nobody re-opens
or re-proposes them without new evidence. Check this before opening an
issue or suggesting a feature (P1). One entry per decision: the gist, why,
and the session that recorded it. An entry is reopened only by a new issue
that cites the old one and says what changed.

- Integrating verl (XiaomiMiMo/verl) into minder. Assessed 2026-09-27:
  layer mismatch (post-training vs governance), architecture violation
  (minder is stdlib-only), scale mismatch (minder exports a dataset, it
  does not train). The only sanctioned touchpoint is a future offline
  JSONL-to-parquet exporter if local post-training ever happens.
- Chasing upstream-agent breadth or agent-memory product features (the
  QA panel's standing anti-recommendation): minder's niche is the
  governed local-first operator plane, not parity with hosted agents.
- Committing `docs/` to the public repo. It is local-private by owner
  decision; only README, CHANGELOG, CONTRIBUTING, and the AGENTS files
  are public. Never push user data or session evidence.
- Shipping console auth / non-loopback binds. The console is
  localhost-only by design (8E); beyond loopback it would need auth and
  CSRF, which are deliberately not built.
