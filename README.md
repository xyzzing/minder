# minder — a local-first governed-agent runtime

[![CI](https://github.com/xyzzing/minder/actions/workflows/ci.yml/badge.svg)](https://github.com/xyzzing/minder/actions/workflows/ci.yml)

Local models are fast, private, and cheap — and they loop. minder sits
between your agent harness and your local `llama-server` and governs that
boundary: **measure → instruct → escalate → audit**. Everything it decides
lands in an append-only ledger, so "what did the watchdog do last night?"
is a one-command question.

Around that core minder has grown a full governance plane:

| Layer | What it does |
|---|---|
| Warden (L0–L3) | measures failures, upgrades retries to thinking, consults frontier APIs within budgets, then stops and alarms |
| Memory plane | SQLite evidence store: episodes, canonical failure keys, verified/candidate lessons, skills, temporal graph |
| Frontier governance | quarantined consults — raw text never stored, labels + hashes only |
| Decision gateway | typed System-One proposals over closed menus; a deterministic policy gate decides; shadow traces separate proposal from decision |
| Egress + intent | deterministic deny on external-prohibited content; adapter preferences dropped |
| Operator plane | `minder-op` CLI, localhost read-only web console, weekly evidence summaries |
| Benchmarks | versioned suites, protected-metric comparator, sandboxed task runner |
| Domain governance | task contexts, domain routing (observe-only), trading research protocol, résumé fact/wording/intent |

Deterministic policy always decides. Classifiers and models propose and are
shadow-evaluated until they beat a pinned baseline — never authoritative.

## Requirements

- Python 3.10+ — the core is **stdlib only**, no pip dependencies
- Optional web console: `pip install --user -r requirements-web.txt`
- Optional local classifier/routing model: [laya](https://huggingface.co/convaiinnovations/laya) (see below)
- Full governed runtime additionally needs: a local `llama.cpp llama-server`
  upstream, Linux + `systemd --user`, and a zcode or dsh harness

## Quick start — operator tools (no model required)

```bash
git clone https://github.com/xyzzing/minder && cd minder

python3 -m minder_op doctor --no-probe          # is an install healthy, and why not
python3 -m minder_op status                     # schema, counts, flags
python3 -m minder_op weekly-summary             # observed workflow evidence, no claims
python3 -m minder_op events ls --limit 10       # raw failure events, redacted

# localhost read-only console (separate web extra)
pip install --user -r requirements-web.txt
minder-web --port 8765 --db ~/.local/state/minder/memory.sqlite
curl -sS http://127.0.0.1:8765/healthz
```

`install.sh` leaves `minder-op` / `minder-web` shims in `~/.local/bin`
(usable from any directory). Without them, run the modules from the repo
checkout: `python3 -m minder_op …`.

Exit codes: `0` ok, `1` usage/not-found, `2` DB missing or corrupt. All data
lives under `~/.local/state/minder/` — nothing leaves the machine.

## Full governed runtime (local model)

```bash
./install.sh --upstream http://127.0.0.1:8080 [--skip-dsh] [--skip-zcode] [--no-start]
```

The installer probes your server's actual capabilities (thinking toggles,
effort names, tool-call dialects), installs a capability-appropriate
fail-closed ladder, merges zcode/dsh hook config (with backups), and
installs a systemd --user unit. Behaviour flags are environment-owned and
read at hook start:

```bash
MINDER_ASSIST=off | retrieve | decision_skill      # digest-level assistance
MINDER_CLASSIFIER=shadow                           # log-only classifier labels
MINDER_DECISION=shadow                             # log-only gateway traces
```

Frontier consults (L2) run through the bundled `frontier.py` runner or your
own via `--frontier-cmd`; bring your own key — minder never ships one.

## Benchmark and evaluation harness

```bash
python3 -m minder_op benchmark list
python3 -m minder_op benchmark validate --suite coding-core-v1
python3 -m minder_op benchmark run --suite coding-core-v1 --task t3_keyerror_default \
    --execute --i-understand-this-runs-local-agent-tasks
python3 -m minder_op benchmark baseline create REPORT.json --yes   # never automatic
python3 -m minder_op benchmark compare BASELINE.json CANDIDATE.json
```

Protected comparison rules: unsafe execution / harmful-frontier /
prohibited egress > 0 → FAIL (any sample size); < 20 comparable runs →
INSUFFICIENT_SAMPLE; completion drop > 5 points → FAIL; fingerprint
mismatch → NON_COMPARABLE. `routing-core-v1` replays 40 adversarial
domain-routing cases through any provider
(`minder-op routes replay --provider rules|laya|null`) using the same
comparator.

## Privacy and safety posture

- Local-first: SQLite + JSONL ledgers in `~/.local/state/minder/`; the web
  console binds 127.0.0.1 only and refuses any other address; no telemetry.
- Secrets and API-key-shaped strings are redacted at ingestion; consult
  traces store hashes, never raw prompts or responses.
- Deterministic policy decides; models and classifiers propose.
  Corrections append evidence — history is never rewritten. No automatic
  skill apply, no DB-edited flags, no trading execution path.

## Status and known limits

Shipped: the governed runtime through the operator plane (CLI, weekly
summary, console; milestones 8A–8E), the benchmark harness + sandboxed
runner, and domain governance Phase 1–2 (explicit task boundaries, a
trading preregistration/manifest protocol, résumé fact-vs-intent
evidence, observe-only domain routing; the deterministic baseline is
measured at 40/40 on the shipped fixture set).

Not here yet: multi-user or remote access (by design), models beyond the
qwen3 presets (untested), Windows, a public docs site, and classifier
providers that beat the deterministic baseline — the harness will tell
you when one does.

## Development

```bash
python3 -m pytest -q        # no network, no model required
```

Optional: install the laya local routing model
(`pip install --user torch --index-url
https://download.pytorch.org/whl/cpu && pip install --user laya`) and the
marked tests exercise it.

MIT — see LICENSE.
