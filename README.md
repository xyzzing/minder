# minder

[![CI](https://github.com/xyzzing/minder/actions/workflows/ci.yml/badge.svg)](https://github.com/xyzzing/minder/actions/workflows/ci.yml)

A supervisor for AI coding assistants, running entirely on your own
computer.

## What minder does

AI coding assistants get stuck. The same error comes back, the proposed
fix does not fix anything, and each retry costs time and patience. minder
sits between your assistant and the local AI model and watches for
exactly that.

When the same failure shows up again, minder steps in, in stages:

1. **Retries with more careful thinking.** It re-sends the request with a
   bigger reasoning budget, so the model works the problem instead of
   guessing again.
2. **Asks for a second opinion.** If the retry fails too, minder can
   consult a bigger model from an outside provider. You set the budget,
   your text is redacted before anything is stored, and minder records
   only labels and hashes, never the raw conversation.
3. **Stops and tells you.** If the second opinion does not help, minder
   raises a clear alarm instead of letting the assistant flail.

Everything minder decides is written to an append-only log (entries go
in, nothing gets edited out). "What did the supervisor do last night?"
is a one-command answer, not an archaeology project.

Nothing leaves your machine. The log is a local database, the web
console only answers on 127.0.0.1 (your computer's private loopback
address, unreachable from the network), and there is no telemetry.

## The pieces

| Piece | What it does in practice |
|---|---|
| Proxy | A small go-between. Your assistant talks to it, it talks to the model. It speaks the standard OpenAI chat format, so any tool that can point at a web address works. |
| Warden | The supervisor itself: spots repeated failures, upgrades retries to deeper thinking, spends second-opinion budgets, then stops and alarms. |
| Memory | A local database of past failures and verified fixes, so last week's dead end is not rediscovered this week. |
| Decision gateway | When smarter components (classifiers, models) suggest an action, they only propose. A fixed rule set decides, and new components must beat the old rules on pinned benchmarks before they are trusted. |
| Operator tools | The `minder-op` command and a localhost web console for reading the evidence. |
| Benchmarks | Versioned test suites with tamper-resistant comparison, so "it got better" is a measured claim, not a feeling. |

Two rules hold across all of it: fixed policy always decides (models
only propose), and history is append-only. Corrections add evidence;
the record is never rewritten.

## Requirements

- Python 3.10+. The core is standard library only, no pip dependencies.
- Optional web console: `pip install --user -r requirements-web.txt`
- Optional local classifier model: [laya](https://huggingface.co/convaiinnovations/laya) (see below)
- A full governed runtime also needs a local model server
  (`llama.cpp llama-server`, or another OpenAI-compatible engine).
  The systemd/dsh/zcode integration tier needs Linux; the generic proxy
  path works anywhere Python runs.

## Quick start: look around (no model required)

The operator tools read minder's local evidence store and work even
without a running model:

```bash
git clone https://github.com/xyzzing/minder && cd minder

python3 -m minder_op doctor --no-probe          # is an install healthy, and why not
python3 -m minder_op status                     # schema, counts, flags
python3 -m minder_op weekly-summary             # observed workflow evidence, no claims
python3 -m minder_op events ls --limit 10       # raw failure events, redacted
python3 -m minder_op capture                    # is the watchdog recording anything at all?
python3 -m minder_op scorecard                  # capture, cost, failures, learning, context, hygiene
```

The web console shows the same evidence in a browser:

```bash
pip install --user -r requirements-web.txt
minder-web --port 8765 --db ~/.local/state/minder/memory.sqlite
# then open http://127.0.0.1:8765
```

`capture` and `scorecard` read dsh's own session store (`~/.dsh`, or
`$MINDER_DSH_HOME`). dsh hooks run inside a file sandbox whose only
writable path is the session workspace, so they cannot write minder's
state directory themselves; the loopback `sink.py` sidecar does it for
them (see [docs/operator-web.md](docs/operator-web.md#capture-how-the-hooks-persist)).
The console's `/capture`, `/sessions` and `/scorecard` pages show the
same data.

`install.sh` leaves `minder-op` / `minder-web` / `minder-sink` shims in
`~/.local/bin` (usable from any directory). Without them, run the
modules from the repo checkout: `python3 -m minder_op ...`.

Exit codes: `0` ok, `1` usage/not-found, `2` DB missing or corrupt. All
data lives under `~/.local/state/minder/`. Nothing leaves the machine.

## The full runtime (with a local model)

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
shims, and touches nothing else: no dsh/zcode wiring, no systemd. The
proxy upgrades reasoning budgets when it sees minder's escalation
markers in recent context, so the supervisor works through the base URL
alone.

### dsh + zcode (Linux with systemd, first-class hooks)

```bash
./install.sh --upstream http://127.0.0.1:8080 [--skip-dsh] [--skip-zcode] [--no-start]
```

The installer probes your server's actual capabilities (thinking
toggles, effort names, tool-call dialects), installs a
capability-appropriate fail-closed ladder, merges zcode/dsh hook config
(with backups), and installs a systemd --user unit. Behaviour flags are
environment-owned and read at hook start:

```bash
MINDER_ASSIST=off | retrieve | decision_skill      # digest-level assistance
MINDER_CLASSIFIER=shadow                           # log-only classifier labels
MINDER_DECISION=shadow                             # log-only gateway traces
```

Second-opinion consults run through the bundled `frontier.py` runner or
your own via `--frontier-cmd`. Bring your own API key; minder never
ships one.

### Switching the model server

The proxy serves one model server ("engine") at a time. To point a
running install at a different OpenAI-compatible server, add a systemd
drop-in and restart:

```bash
mkdir -p ~/.config/systemd/user/minder-proxy.service.d
cat > ~/.config/systemd/user/minder-proxy.service.d/upstream.conf <<'EOF'
[Service]
Environment=MINDER_UPSTREAM=http://127.0.0.1:8081
EOF
systemctl --user daemon-reload && systemctl --user restart minder-proxy
```

The proxy re-runs the capability probe against the new server on boot
and rewrites `model_caps.json`, so thinking mechanisms and tool-call
dialects are re-measured on every switch. Presets only need changes if
the new server validates model names; a server that ignores them works
with the installed presets as-is. Two engines competing for the same
GPU cannot run concurrently: stop one before starting the other. While
the configured engine is down, the proxy returns an honest 502
(`minder_upstream_unavailable`) instead of failing silently.

### Engine registry

For installs that move between engines regularly, `minder.json` can
name them. Each entry carries the engine's address and the systemd user
unit that runs it; `active_engine` picks which one the proxy forwards
to:

```json
{
  "engines": {
    "llama":  {"upstream": "http://127.0.0.1:8080", "unit": "llama-model@qwen27b"},
    "strata": {"upstream": "http://127.0.0.1:8081", "unit": "strata-hip"}
  },
  "active_engine": "llama"
}
```

With a registry present the proxy re-resolves the active engine on
every request, so switching is a one-line config edit and a unit
restart, with no proxy restart. Capabilities are measured per engine
into `model_caps.<engine>.json`. An install without an `engines` key
keeps the single-server behavior above.

### Switching engines (lifecycle)

When each engine entry carries a systemd user `unit`, the operator CLI
and the console can do the whole switch: stop the current engine unit,
start the target, health-check it, and only then flip `active_engine`
in `minder.json` (a backup is written to `minder.json.bak`). A target
that never becomes healthy rolls back to the previous engine, so a
failed switch cannot leave you without a working model.

```bash
minder-op engine status             # rows: active flag, unit state, health
minder-op engine switch strata --yes
```

The console exposes the same switch at `/engine`. `doctor` reports the
active engine's health and warns when two engine units run at the same
time, because they compete for the GPU. `install.sh
--engine NAME=URL[,UNIT]` (repeatable) plus `--active-engine NAME`
write the registry at install time.

## Benchmarks and evaluation

### Multi-turn prompt-cache replay

`benchmarks/multiturn_cache.py` replays a growing conversation against
any OpenAI-compatible engine under three request patterns (append-only,
alternating thinking flags, reordered history) and reports per-turn
cache reuse from usage or engine metrics:

```bash
python3 benchmarks/multiturn_cache.py --base-url http://127.0.0.1:8081 \
    --turns 12 --tail-chars 1200 --json-out /tmp/multiturn.json
```

Measured against the strata-amd HIP build (2026-10-01): the append-only
pattern re-reads about 263 tokens per turn (86% reuse); alternating
thinking flags re-read the whole conversation every turn (2.3x wall at
3.6k context, linear in context length); a one-time history reorder
costs one re-prefill and then caching resumes. This is why the proxy
pins thinking settings per session on single-slot engines. The fork's
usage payload omits `cached_tokens`, so on strata the ledger's reuse
field stays empty until the engine maps its internal `reused` counter
into the OpenAI usage shape.

### Coding suites with protected comparison

```bash
python3 -m minder_op benchmark list
python3 -m minder_op benchmark validate --suite coding-core-v1
python3 -m minder_op benchmark run --suite coding-core-v1 --task t3_keyerror_default \
    --execute --i-understand-this-runs-local-agent-tasks
python3 -m minder_op benchmark baseline create REPORT.json --yes   # never automatic
python3 -m minder_op benchmark compare BASELINE.json CANDIDATE.json
```

Protected comparison rules: unsafe execution / harmful-frontier /
prohibited egress > 0 means FAIL at any sample size; fewer than 20
comparable runs means INSUFFICIENT_SAMPLE; a completion drop over 5
points means FAIL; a fingerprint mismatch means NON_COMPARABLE.
`routing-core-v1` replays 40 adversarial domain-routing cases through
any provider (`minder-op routes replay --provider rules|laya|null`)
using the same comparator.

### Non-coding domains: `domain-core-v1`

Finance, trade finance, sustainability and governance tasks with code
oracles instead of a judge model. Seeded generators produce unlimited
cases. Each case has a unique answer fixed by a rule (GST, IFRS 16,
UCP 600, Incoterms 2020, the Singapore Carbon Pricing Act, PDPA Part
6A) or by a stated company policy. The suite also scores abstention
(`insufficient_data` when an input is missing), fabricated evidence in
document extraction, and metamorphic consistency.

```bash
python3 -m minder_domain_evals selfcheck --seed 7 --n 200
python3 -m minder_domain_evals generate --seed 7 --n 50 --out cases.jsonl
python3 -m minder_domain_evals run --cases cases.jsonl --out answers.jsonl --model qwen-exec
python3 -m minder_domain_evals score --cases cases.jsonl --answers answers.jsonl --out report.json
```

Details, rule provenance and limits:
[docs/domain-evals.md](docs/domain-evals.md).

## Use the pieces without the runtime

Two parts of minder are deliberately dependency-free (Python standard
library only, zero minder imports) and importable from `minder_core`:

- **`minder_core.comparator`**, the protected-metric benchmark
  comparator behind `minder-op benchmark compare`: safety regressions
  fail at any sample size, completion drops are bounded, and the
  sample-size and fingerprint guards cannot be argued into a PASS.
- **`minder_core.identity`**: canonical failure keys, action
  fingerprints, secret redaction, and result signatures, the exact
  functions minder's evidence log is keyed by.

```python
from minder_core import compare_reports, failure_key, result_signature
```

## Privacy and safety posture

- Local-first: SQLite + JSONL logs in `~/.local/state/minder/`; the web
  console binds 127.0.0.1 only and refuses any other address; no
  telemetry.
- Secrets and API-key-shaped strings are redacted when records are
  written; consult traces store hashes, never raw prompts or responses.
- Fixed policy decides; models and classifiers propose. Corrections
  append evidence; history is never rewritten. No automatic skill
  apply, no DB-edited flags, no trading execution path.

## Status and known limits

Current release: **v0.8.2** (see CHANGELOG). On main, the dual-engine
work is also shipped: an engine registry with per-engine capability
measurement, a request queue and prompt-cache protections for
single-slot engines like strata-amd, and a lifecycle switch in the CLI
and the console (`minder-op engine switch`, `/engine`).

Shipped overall: the governed runtime through the operator plane (CLI,
weekly summary, console), the benchmark harness with a sandboxed
runner, domain governance phase 1-2 (explicit task boundaries, a
trading preregistration protocol, resume fact-vs-intent evidence,
observe-only domain routing; the deterministic baseline measures 40/40
on the shipped fixture set).

Not here yet: multi-user or remote access (by design), models beyond
the qwen3 presets (untested), Windows, a public docs site, and
classifier providers that beat the deterministic baseline. The harness
will tell you when one does.

Worth knowing: dsh runs command hooks inside its file sandbox, whose
only writable path is the session workspace. A hook therefore cannot
write `~/.local/state/minder` directly (it gets `EROFS`), and because
every hook write is fail-open, that loss would be silent. `sink.py` (a
loopback systemd --user sidecar) performs those writes and keeps the
laya models warm; `minder-op capture` is the check that says whether
it is working. The mechanics and the ten monitored metrics are in
[docs/operator-web.md](docs/operator-web.md#capture-how-the-hooks-persist).

## Development

```bash
python3 -m pytest -q        # no network, no model required
ruff check .                # config: ruff.toml (CI enforces it)
```

Packaging: `pip wheel . --no-deps` builds a wheel of the operator plane
+ `minder_core` (entry points `minder-op` / `minder-web`, version
synced to `MINDER_VERSION` in minder.py). The hook transports, proxy
and sink stay staged by `install.sh`; they are wired to a real model
server and a specific harness, which a wheel cannot do honestly.

Optional: install the laya local routing model (`pip install --user
torch --index-url https://download.pytorch.org/whl/cpu && pip install
--user laya`) and the marked tests exercise it.

MIT, see LICENSE.
