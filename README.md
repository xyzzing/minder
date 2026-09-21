# minder — a deterministic escalation watchdog for local coding agents

Local models are fast, private, and cheap — and they loop. A 27B model will
retry the same failing edit anchor or the same red test until the context
window fills, burning hours of tokens on nothing. Frontier models solve the
loop in one look, but you don't want to pay frontier prices for every turn,
and you don't want to hand-escalate at 2am.

minder sits between your agent harness and your local `llama-server` and
governs that boundary with four deterministic moves:

**measure → instruct → escalate → audit**

```
 L0  exec        normal traffic, thinking off, full speed
      │  same action fails N times with the SAME error signature
      ▼
 L1  think       the retry is upgraded to thinking mode; the model gets a
      │          digest that orders a root-cause hypothesis FIRST
      │  still failing after the think-retry
      ▼
 L2  frontier   an out-of-band consult to a cheap frontier API (DeepSeek,
      │          optionally cross-checked against OpenAI); the merged,
      │          disagreement-flagged answer is injected into the digest
      │  budgets exhausted, still failing
      ▼
 L3  alarm      STOP retrying. Report to the operator and move on.
```

Everything minder decides lands in an append-only JSONL ledger
(`events.jsonl`) — every escalation, every refund, every root-cause hint —
so "what did the watchdog do last night?" is a one-command question.

## What makes it different

- **Capabilities are measured, never assumed.** At install (and again at
  every proxy boot) minder probes your actual server: does
  `chat_template_kwargs.enable_thinking` work? Which effort names does the
  template accept (`xhigh` vs `high` vs `low`)? Are tool calls clean, or
  does the template make the model drift into unparseable dialects? The
  answers drive every request minder rewrites. A community GGUF with a
  weird template is a first-class citizen, not a bug report.
- **Triggers are structural, not semantic.** Failure detection keys on exit
  codes, error strings, and *error-signature repetition* — a changing error
  is progress (TDD red-green cycles never escalate); an identical error
  twice is a loop. No model judges the model.
- **Budgets are sovereign and refundable.** Think and frontier budgets are
  finite per session — and a failure key that resolves gives its budget
  back, so early noise can't starve later real failures of the whole
  ladder.
- **A backfire breaker.** If the model retries the same action right after
  an escalation, minder notices, demotes, and eventually suppresses —
  escalation digests themselves can become a loop.

## Install

Requirements: Python 3.10+ (stdlib only — no pip dependencies), a local
OpenAI-compatible server (`llama-server`), and optionally systemd `--user`
for the proxy service.

```bash
git clone https://github.com/xyzzing/minder.git
cd minder
./install.sh                       # probes, installs, wires everything it finds
```

`install.sh` is zero-input and fail-closed: it runs preflight probes and a
**capability probe (CAP)** against your server, then installs according to
what it measured — full thinking-toggle support, soft-switch markers, or an
honest degraded mode (digest-only L1) when the server can't toggle thinking
at all. It refuses to guess.

What it wires up, when found:

| Target | What you get |
|---|---|
| any OpenAI-compatible harness | the proxy on `:8390` with model aliases `qwen-exec` / `qwen-think` / `qwen-auto` / `frontier` |
| Claude-Code-style hooks bridges (dsh) | SessionStart + PostToolUse hooks; escalation digests block the tool result and reach the model |
| zcode-compatible config | PostToolUse + SessionStart hooks, compaction survival |

Point your harness at `http://127.0.0.1:8390/v1` and pick a model alias:

- **`qwen-exec`** — thinking off, fast, for normal turns
- **`qwen-think`** — thinking on, for hard turns
- **`qwen-auto`** — per-request effort scheduling from recent tool activity
  (plain question → off, heavy bash/edit → high, mapped onto the effort
  vocabulary your server actually accepts)
- **`frontier`** — not a model; the L2 consult channel (calling it returns
  an honest 422)

The escalation channel is stateless: when a digest marker
(`[minder] ESCALATION L1/L2`) appears in the recent message window, the
proxy upgrades that request to thinking mode — no session coupling, works
with any harness.

### Uninstalling

`./uninstall.sh` reverses every integration additively (hooks, provider
entry, service) and **keeps your ledger** (`~/.local/state/minder/`).
It does remove `~/.config/minder/`, which contains `frontier.env` —
**your L2 API keys**. Copy that file somewhere safe first if you plan to
reinstall.

## Frontier consults (L2)

Set one key in `~/.config/minder/frontier.env` (chmod 600, no restart
needed):

```
DEEPSEEK_API_KEY=sk-...
OPENAI_API_KEY=sk-...      # optional — enables the cross-check panel
```

With one key you get single-consultant answers; with both, minder consults
the providers in parallel and synthesizes the answers into one
recommendation with disagreements flagged. When an L2-escalated failure
resolves, minder spends one more cheap call on a verification consult
("did the fix address the root cause?") and records the verdict in the
ledger. No key? L2 degrades honestly to a guided root-cause digest — by
design, never a hang.

## Optional reflex tier (advisory micro-classification)

A tiny CPU-side decision model can classify each failure by root cause and
append a one-line hint to L1 digests (e.g. `[reflex 0.97] probable
logic_bug: re-read the exact assertion inputs`). minder never lets it
govern — it only enriches digests, and any failure is invisible.

Measured gate on 66 labeled agent failures (your mileage will vary —
re-run the eval on your own traffic before trusting it): 57.6% full
accuracy vs 22.7% majority baseline; **79.3% accuracy at confidence ≥ 0.85**
(43.9% coverage); `logic_bug` 14/14. Off by default; see `reflex.py`.

## Configuration (`~/.config/minder/minder.json`)

| Key | Default | Meaning |
|---|---|---|
| `effort_mode` | `off` | `off` / `auto` (activity-based) / `fixed` |
| `fail_threshold` | `2` | identical-error failures before L1 |
| `think_budget` / `frontier_budget` | `2` / `1` | escalations per session (refunded on resolution) |
| `verify_consult` | `true` | panel-verifies resolutions of L2 keys |
| `reflex` | disabled | `{enabled, url, threshold}` advisory classifier |
| `profile` + `profiles` | — | domain overlays: extra fail-signs, frontier prompt templates, **egress redaction** (strip secrets before consults), `audit_chain` (hash-chained ledger for audit-grade domains) |

Harnesses can also send `X-Minder-Mode: direct|lean|deep` per request to
select thinking depth (with thinking budgets 0 / ~1024 / 4096); an active
escalation marker always wins, and minder downgrades `deep→lean` when a
supervisor reports thermal backpressure — by mode change, never by
blocking the relay.

## Watching it work

```bash
tail -f ~/.local/state/minder/events.jsonl      # the ledger
python3 ~/.local/share/minder/minder.py report  # rendered timeline
```

Quiet is normal: successful hook calls log nothing. You'll see
`session_start`, `auto_effort`, `escalate`, `deescalate`, `error_changed`,
`escalation_upgraded`, frontier consults (in `consults.jsonl`),
`verify_consult`, `l3_alarm`, and `cap_reverified` (the proxy re-measuring
your server at boot).

## Memory (v1)

minder records what happened: every PostToolUse failure is canonicalised
into a stable failure key (timestamps stripped, secrets redacted) and
stored — append-only — in a SQLite episode/lesson store next to the JSONL
ledger. When the *same* action fails the *same* way twice, the duplicate
guard replaces the usual escalation with a block that demands a new
hypothesis instead of another identical retry. When an episode resolves
with passing tests, it can be promoted to a **verified lesson** which is
retrieved (same repo, same failure family) and attached to the block.
Unmatched repeated failures record a *skill gap* — evidence that a skill
is missing, never an automatic SKILLS.md edit. Frontier consults get
hashed trace metadata so helpfulness can be measured later. Set
`MINDER_MEMORY_DB` to relocate the store (default
`~/.local/state/minder/memory.sqlite`); every memory failure is fail-open
and can never crash a hook.

## Repo layout

```
minder.py        warden core: ladder, budgets, breaker, ledger
adapter.py       capability probe (CAP) + mode adapter + effort scheduling
proxy.py         Turnstile: alias resolution, escalation channel, modes
hook.py          one hook script, two transports (zcode directive / dsh bridge)
frontier.py      L2 panel: multi-provider consults, synthesis, redaction
reflex.py        optional advisory classifier client
probe_dialect.py tool-call cleanliness prober (diagnostics)
install.sh       fail-closed installer       uninstall.sh
presets/         qwen-exec / qwen-think / qwen-auto / frontier
memory/          episode/lesson store, duplicate guard, consult traces
skills/          tiny skill-trigger index (gap detection; SKILLS.md untouched)
zcode/ dsh/      harness packages
docs/adr/        architecture decision records
tests/           111 tests, stdlib-only: MINDER_NO_SYSTEMD=1 pytest tests/ -q
prd.md           the build contract (spec-first; deviations are reported)
```

## Companion plugins

[dsh-qwen38-local-qol](https://github.com/Yunado/dsh-qwen38-local-qol) (MIT)
is an independent dsh plugin covering wire-level quality-of-life for local
Qwen3.8: per-dialect thinking budgets, a compaction backend, a live settings
tab. It complements minder but **does not stack with it** — its `qwen38`
preset routes straight to the model server, bypassing minder's proxy. Pick
one route per session. minder declares its CAP-measured effort vocabulary on
the provider card, so the dsh effort picker works on `qwen-auto`; a
picker choice wins over the activity scheduler, and escalation markers win
over everything.

## Known limitations (honest ones)

- L2 consults need an external API key; without one you get the degraded
  guided digest, not a frontier answer.
- The reflex classifier is advisory and measured on a small set; its
  weakest class is environment failures (often confused with logic bugs).
- dsh's headless profile emits no agent lifecycle events to plugins, so
  hooks only fire in its web sessions (upstream issue, documented in the
  ADRs).
- The dsh hooks-bridge package (`@deepseek-ai/dsh-hooks-claude-code`) must
  match your dsh core version — a mismatched bridge silently never fires.
  The installer pins one known-good line; if your dsh is newer, install the
  matching bridge version manually (see ADR-0001).
- Every capability claim in the ledger is a measurement of *your* server
  at *probe time* — if you relaunch the server with different flags, the
  proxy re-measures at its next boot and logs what changed.

## License

MIT — see [LICENSE](LICENSE).
