# minder — a local-first governed-agent runtime

[![CI](https://github.com/xyzzing/minder/actions/workflows/ci.yml/badge.svg)](https://github.com/xyzzing/minder/actions/workflows/ci.yml)

Local models are fast, private, and cheap — and they loop. minder sits
between your agent harness and your local `llama-server` and governs that
boundary: **measure → instruct → escalate → audit**. Everything it decides
lands in an append-only ledger, so "what did the watchdog do last night?"
is a one-command question.

It is harness-agnostic by design
([ADR-0004](docs/adr/0004-harness-agnostic-sidecar-proxy.md)): the proxy
speaks plain OpenAI chat-completions, so any harness or client that can
point at a base URL works — dsh and zcode get first-class hook
integrations, everything else just points at `http://127.0.0.1:8390/v1`.

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
- A governed runtime additionally needs a local `llama.cpp llama-server`
  upstream. The systemd/dsh/zcode **integration tier** needs Linux;
  the generic proxy path (below) works anywhere Python runs.

## Quick start — operator tools (no model required)

```bash
git clone https://github.com/xyzzing/minder && cd minder

python3 -m minder_op doctor --no-probe          # is an install healthy, and why not
python3 -m minder_op status                     # schema, counts, flags
python3 -m minder_op weekly-summary             # observed workflow evidence, no claims
python3 -m minder_op events ls --limit 10       # raw failure events, redacted
python3 -m minder_op capture                    # is the watchdog recording anything at all?
python3 -m minder_op scorecard                  # capture, cost, failures, learning, context, hygiene

# localhost read-only console (separate web extra) — port 8765, not 8392
pip install --user -r requirements-web.txt
minder-web --port 8765 --db ~/.local/state/minder/memory.sqlite
systemctl --user status minder-web.service      # install.sh ships this unit
curl -sS http://127.0.0.1:8765/healthz
```

`capture` and `scorecard` read dsh's own session store (`~/.dsh`, or
`$MINDER_DSH_HOME`). dsh hooks run inside a file sandbox whose only
writable path is the session workspace, so they cannot write minder's
state directory themselves; the loopback `sink.py` sidecar does it for
them (see [docs/operator-web.md](docs/operator-web.md#capture-how-the-hooks-persist)).
The console's `/capture`, `/sessions` and `/scorecard` pages show the same
data.

`install.sh` leaves `minder-op` / `minder-web` / `minder-sink` shims in
`~/.local/bin` (usable from any directory). Without them, run the modules
from the repo checkout: `python3 -m minder_op …`.

Exit codes: `0` ok, `1` usage/not-found, `2` DB missing or corrupt. All data
lives under `~/.local/state/minder/` — nothing leaves the machine.

## Full governed runtime (local model)

### Any OpenAI-compatible harness (`--generic`, no Linux required)

```bash
git clone https://github.com/xyzzing/minder && cd minder
./install.sh --generic --upstream http://127.0.0.1:8080
MINDER_UPSTREAM=http://127.0.0.1:8080 ~/.local/share/minder/proxy.py &
# then point your harness at http://127.0.0.1:8390/v1
# models: qwen-exec | qwen-think | qwen-auto (auto = escalation-managed)
```

`--generic` stages the code, probes your server's real capabilities
(fail-closed ladder, same as below), writes the config and the operator
shims — and touches nothing else: no dsh/zcode wiring, no systemd. The
proxy upgrades reasoning budgets when it sees minder's escalation markers
in recent context, so loop-governance works through the base URL alone.

### dsh + zcode (Linux + `systemd --user`, first-class hooks)

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

### Switching the model upstream

The proxy serves one upstream at a time. To point a running install at a
different OpenAI-compatible server, add a systemd drop-in and restart:

```bash
mkdir -p ~/.config/systemd/user/minder-proxy.service.d
cat > ~/.config/systemd/user/minder-proxy.service.d/upstream.conf <<'EOF'
[Service]
Environment=MINDER_UPSTREAM=http://127.0.0.1:8081
EOF
systemctl --user daemon-reload && systemctl --user restart minder-proxy
```

The proxy re-runs the capability probe against the new upstream on boot and
rewrites `model_caps.json`, so thinking mechanisms and tool-call dialects
are re-measured on every switch. Presets only need changes if the new
server validates model names — a server that ignores them works with the
installed presets as-is. Two engines competing for the same GPU cannot run
concurrently: stop one before starting the other, and while the configured
upstream is down the proxy returns an honest 502
(`minder_upstream_unavailable`) instead of failing silently.

### Engine registry

For installs that move between engines regularly, `minder.json` can name
them. Each entry carries the upstream URL and the systemd user unit that
runs the engine; `active_engine` picks which one the proxy forwards to:

```json
{
  "engines": {
    "llama":  {"upstream": "http://127.0.0.1:8080", "unit": "llama-model@qwen27b"},
    "strata": {"upstream": "http://127.0.0.1:8081", "unit": "strata-hip"}
  },
  "active_engine": "llama"
}
```

With a registry present the proxy re-resolves the active engine on every
request, so switching is a one-line config edit and a unit restart — no
proxy restart. Capabilities are measured per engine into
`model_caps.<engine>.json`. An install without an `engines` key keeps the
single-upstream behavior above.

### Switching engines (lifecycle)

When each engine entry carries a systemd user `unit`, the operator CLI
and the console can do the whole switch: stop the current engine unit,
start the target, health-check its `/health` endpoint, and only then
flip `active_engine` in `minder.json` (backup written to
`minder.json.bak`). A target that never becomes healthy rolls back to
the previous engine.

```bash
minder-op engine status             # rows: active flag, unit state, health
minder-op engine switch strata --yes
```

The console exposes the same switch at `/engine`. `doctor` reports the
active engine's health and warns when two engine units run at the same
time — they compete for the GPU. `install.sh --engine NAME=URL[,UNIT]`
(repeatable) plus `--active-engine NAME` write the registry at install
time.

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

### Non-coding domains: `domain-core-v1`

Finance, trade finance, sustainability and governance tasks with **code
oracles instead of a judge model**. Seeded generators produce unlimited
cases. Each case has a unique answer fixed by a rule (GST, IFRS 16, UCP 600,
Incoterms 2020, the Singapore Carbon Pricing Act, PDPA Part 6A) or by a
stated company policy. The suite also scores abstention (`insufficient_data`
when an input is missing), fabricated evidence in document extraction, and
metamorphic consistency.

```bash
python3 -m minder_domain_evals selfcheck --seed 7 --n 200
python3 -m minder_domain_evals generate --seed 7 --n 50 --out cases.jsonl
python3 -m minder_domain_evals run --cases cases.jsonl --out answers.jsonl --model qwen-exec
python3 -m minder_domain_evals score --cases cases.jsonl --answers answers.jsonl --out report.json
```

Details, rule provenance and limits: [docs/domain-evals.md](docs/domain-evals.md).

## minder_core — use the pieces without the runtime

Two parts of minder are deliberately dependency-free (Python stdlib only,
zero minder imports) and importable from `minder_core`:

- **`minder_core.comparator`** — the protected-metric benchmark
  comparator behind `minder-op benchmark compare`: safety regressions
  fail at any sample size, completion drops are bounded, sample-size and
  fingerprint guards can never be argued into a PASS.
- **`minder_core.identity`** — canonical failure keys, action
  fingerprints, secret redaction, and result signatures: the exact
  functions minder's evidence ledger and loop guard are keyed by.

```python
from minder_core import compare_reports, failure_key, result_signature
```

## Privacy and safety posture

- Local-first: SQLite + JSONL ledgers in `~/.local/state/minder/`; the web
  console binds 127.0.0.1 only and refuses any other address; no telemetry.
- Secrets and API-key-shaped strings are redacted at ingestion; consult
  traces store hashes, never raw prompts or responses.
- Deterministic policy decides; models and classifiers propose.
  Corrections append evidence — history is never rewritten. No automatic
  skill apply, no DB-edited flags, no trading execution path.

## Status and known limits

Current release: **v0.8.0** — the operator plane.

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

Worth knowing: dsh runs command hooks inside its file sandbox, whose only
writable path is the session workspace. A hook therefore cannot write
`~/.local/state/minder` directly (it gets `EROFS`), and because every hook
write is fail-open that loss is silent. `sink.py` (a loopback, systemd
`--user` sidecar) performs those writes and keeps the laya models warm;
`minder-op capture` is the check that says whether it is working. The
mechanics and the ten monitored metrics are in
[docs/operator-web.md](docs/operator-web.md#capture-how-the-hooks-persist).

## Development

```bash
python3 -m pytest -q        # no network, no model required
ruff check .                # config: ruff.toml (CI enforces it)
```

Packaging: `pip wheel . --no-deps` builds a wheel of the operator plane +
`minder_core` (entry points `minder-op` / `minder-web`, version synced to
`MINDER_VERSION` in minder.py). The hook transports, proxy and sink stay
staged by `install.sh` — they are wired to a real llama-server and a
specific harness, which a wheel cannot do honestly.

Optional: install the laya local routing model
(`pip install --user torch --index-url
https://download.pytorch.org/whl/cpu && pip install --user laya`) and the
marked tests exercise it.

MIT — see LICENSE.
