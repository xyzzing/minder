# Preface to v0.3 — What Changed and Why

**Honesty note:** I cannot fetch the two Hugging Face links from this session. I know DavidAU's catalog pattern (community merges/quants, GGUF, "NEO-MTP" implying multi-token-prediction tensors, non-standard metadata, often sampler-sensitive) and template-pack repos of the Qwen-Sharp type (third-party Jinja variants that alter or remove thinking-block handling). **The exact repos are not verified.** The design consequence is the point:

> **v0.3 design law #9: Capabilities are measured, never assumed — not from model names, not from repos, not from "it's Qwen3 so it must support `enable_thinking`."** A community GGUF may ship a template that ignores `chat_template_kwargs`, rejects unknown fields, emits reasoning in a different field, or cannot toggle thinking at all. Minder therefore gains an empirical **Capability Probe** and a **Mode Adapter** that translates "we want thinking" into whatever mechanism actually works — or honestly degrades L1 when none does.

---

# PRODUCT REQUIREMENTS DOCUMENT
## Minder v0.3 — Agent Build Brief (heterogeneous local models)

**Reader contract:** This document is executable without prior conversation. Specs are contracts. Where reality disagrees, **stop and report actuals** — never silently patch the spec.

---

## 0. Agent Operating Rules

1. Read fully before coding. Execute §2 preflight first; any unresolved probe ⇒ stop that step, report.
2. Build in §9 order; each step gates on named acceptance tests. Do not pass red gates.
3. Production code: **Python 3.10+ stdlib only.** `pytest` for tests only.
4. Never: add dependencies, commit, run foreground daemons, touch files outside §4, auto-merge configs without backup, **or infer model behavior from model-name strings** (Law #9).
5. Final deliverable: §11 report.

---

## 1. Mission & Context

**Problem:** Local Qwen3-family models — **official or community derivatives** (e.g., DavidAU TWIN-TURBO-class GGUF merges with MTP tensors; custom template packs à la Qwen-Sharp) — drive coding agents via `llama-server`. Observed failure: the model loops on failing edit anchors, burning context. Thinking/non-thinking is switchable **in principle**, but the *mechanism varies per build*: some honor `chat_template_kwargs.enable_thinking`, some only soft-switch markers (`/think`, `/no_think`), some can't toggle at all, and reasoning may surface in `content` or `reasoning_content`. Nothing decides *when* to think; frontier escalation is manual.

**Product:** Minder — deterministic escalation watchdog (L0 exec → L1 think → L2 frontier → L3 alarm), now with:
- **Capability Probe**: empirical, per-server measurement of the thinking mechanism (§2.1)
- **Mode Adapter**: mechanism-agnostic translation of intent → request mutation (§6.6)
- **Graceful L1 degradation**: if no toggle exists, L1 becomes digest-only deliberation, budgeted and breaker-armed — never silently faked

**Tiers:** zcode native hooks (primary); stateful proxy (universal fallback, any OpenAI-compatible harness).

**Laws:** (1) structural triggers only · (2) fail-open transport · (3) atomic parameter coupling · (4) budget sovereignty · (5) local-first privacy · (6) detection never blocks the hot path · (7) digests add information, not exhortation — *except the explicitly-degraded L1, which is exhortation by necessity and is breaker-armed* · (8) surgery is last resort (stub-only this build) · **(9) capabilities are measured, never assumed**.

---

## 2. Preflight Probes

| # | Probe | Method | Pass | On fail |
|---|---|---|---|---|
| P-1 | Python ≥ 3.10 | `python3 -c "import sys; assert sys.version_info >= (3,10)"` | exit 0 | STOP |
| P-2 | Upstream alive | `GET $MINDER_UPSTREAM/health` | 200 | warn; mock-mode only |
| P-2b | **Generation smoke** | POST 4-token completion | 200 + non-empty content | **STOP** — server may load but fail on generation (MTP/quant quants can 500 on completion despite healthy `/health`); report body verbatim |
| P-3 | kwargs accepted | completion with `"chat_template_kwargs":{"enable_thinking":false}` | 200 | **no longer a STOP** — feeds CAP probe; record `kwargs_accepted:false` if 400 |
| P-4 | Context size | `/props` → `default_generation_settings.n_ctx`, else env `MINDER_N_CTX` | integer | warn; `no_clamp` ledger event |
| P-5 | zcode settings file | probe `~/.zcode/`, `~/.config/zcode/`, `./.zcode/` | parsable JSON | record path=none; manual-step report (§6.9) |
| **P-6** | **CAP (Capability Probe)** | §2.1 — mandatory | `model_caps.json` written | STOP unless `--accept-l1-degraded` (§2.2) |

### 2.1 Capability Probe (CAP) — normative

Cheap, deterministic: `max_tokens=256`, `temperature=0`, single user turn ("Briefly: what is 7 times 6?"). Never hash weights. Never parse the model *name* for a decision — names are recorded as fingerprint only.

**Procedure:**
1. **Fingerprint:** `GET /v1/models` → first id; `GET /props` → model path if present; template source: `embedded` if `/props` exposes a non-empty chat template, else `unknown` (tolerate absent fields).
2. **T1 — kwargs differential:** two requests, `enable_thinking:false` vs `true`. Score thinking by: thinking markers present in `message.content` **or** `message.reasoning_content` (llama-server `--reasoning-format` moves reasoning out of `content` — always check both), with response-length differential as corroborating evidence only. Differential ⇒ `mechanism: kwargs` (requires `kwargs_accepted:true` from P-3).
3. **T2 — softswitch differential:** two plain requests vs. same request with `/no_think` appended to the last user message. If plain shows thinking and `/no_think` strips it ⇒ `mechanism: softswitch`, tokens `{on:"/think", off:"/no_think"}` (configurable, §5.7).
4. **T3 — none:** no differential anywhere ⇒ `mechanism: none`.
5. Record `thinking_budget_supported`: false if the `thinking_budget` kwarg yields HTTP 400 (Q3/A3 override).

**Output `~/.config/minder/model_caps.json`:**

```json
{"fingerprint": {"model_id": "...", "props_path": "...", "template_source": "embedded"},
 "kwargs_accepted": true,
 "thinking": {"mechanism": "kwargs|softswitch|none",
              "markers": ["<think>", "</think>"],
              "field": "content|reasoning_content",
              "thinking_budget_supported": true,
              "evidence": {"probe_on_chars": 0, "probe_off_chars": 0}},
 "softswitch_tokens": {"on": "/think", "off": "/no_think"},
 "probed_at": 0.0, "minder_version": "0.3"}
```

**Marker list default** `["<think>", "</think>"]`, configurable — community templates may use different markers; if the user declares markers in config, the probe uses theirs.

### 2.2 Install fail-closed ladder (replaces v0.2's single P-3 gate)

| CAP result | Install behavior |
|---|---|
| `kwargs` | Full L1 (presets as §5.4) |
| `softswitch` | Full L1 via marker injection (§6.6) |
| `none` | **STOP** unless `--accept-l1-degraded` ⇒ install with digest-only L1; ledger `l1_degraded` at every L1 |
| Probe itself errors (server unreachable mid-probe) | STOP; report probe transcripts verbatim |

---

## 3. Scope

**In scope:** Warden core; Capability Probe + Mode Adapter; Turnstile proxy (aliases, rewrite, `/v1/models` synthesis, clamped presets); zcode package; installer with probe-driven fail-closed ladder; sampler-passthrough presets; mock upstream with scripted server behaviors; full suite; frontier plumbing.

**Out of scope (report, don't build):** runtime llama-server template switching (requires relaunch — if the user runs a template pack that lacks toggles, the installer *documents* which template variant to pick instead of working around it); template-pack management; model-weight defects (MTP tensor support, quant quirks — surfaced only via P-2b or user reports); content moderation/policy for uncensored models (minder is behavior-agnostic); dsh-native wiring; Sinter plugin; surgery (stub: log `surgery_requested`, no-op); STALL/DEGEN (config accepted, disabled, unimplemented).

---

## 4. Repository Manifest

```
minder/
├── minder.py                  # Warden core (~250 loc)
├── adapter.py                 # CAP probe + Mode Adapter (~140 loc)
├── proxy.py                   # Turnstile (~200 loc)
├── presets/
│   ├── qwen3-exec.json        # static; installer patches installed copies
│   ├── qwen3-think.json
│   └── frontier.json
├── zcode/{hook.py, escalation.skill.md, settings.snippet.json}
├── install.sh
└── tests/
    ├── test_warden.py  test_adapter.py  test_proxy.py
    ├── test_hook.py    test_concurrency.py
    └── mock_upstream.py   # scripted behaviors: default | reject_kwargs |
                            # reasoning_content | softswitch_only | sse
```

Install targets: code → `~/.local/share/minder/`; **installed+patched presets → `~/.config/minder/presets/`** (proxy reads config-dir presets first, falls back to share-dir statics); state → `~/.local/state/minder/`; caps → `~/.config/minder/model_caps.json`.

---

## 5. Data Contracts

### 5.1 Hook event (stdin; A1) — unchanged from v0.2 §5.1
### 5.2 Hook directive (stdout; always exit 0) — unchanged (§5.2)

### 5.3 Session state file — unchanged (§5.3), plus `"caps_mechanism"` snapshot copied at session start (so mid-upgrade behavior is stable within a session).

### 5.4 Preset schema v2

```json
{"alias": "qwen-exec", "class": "exec",
 "upstream_model": null,
 "upstream_params": {"temperature": 0.15, "top_p": 0.8, "top_k": 20,
   "max_tokens": 8192, "chat_template_kwargs": {"enable_thinking": false}},
 "sampler_overrides": {"min_p": null, "dry_multiplier": null, "xtc_probability": null}}
```

- **Atomicity:** the five exec / five think parameters move together, as before. **Law #3 extension:** the *mechanism field* (`chat_template_kwargs` vs softswitch vs nothing) is selected by the Mode Adapter from caps — a preset never carries a mechanism the probe didn't confirm.
- `upstream_model`: if non-null, proxy rewrites `req["model"]` to it (community servers may validate model ids; llama-server generally ignores — rewrite when known anyway). Installer patches installed presets with the `/v1/models` id discovered during CAP.
- `sampler_overrides`: numeric pass-through map merged into params when non-null (llama.cpp proprietary samplers: DRY, XTC, min_p). Community merges are sampler-sensitive; users may add their own preset files (`presets/twin-turbo-exec.json` etc.) — the loader accepts any `*.json` with a unique `alias`. Defaults remain official-Qwen3 values; **no name-based sampler guessing** (Law #9).
- Think preset unchanged: temp 0.6, top_p 0.95, top_k 20, max_tokens 32768, `enable_thinking:true` + `thinking_budget:16384` (omit kwarg if `thinking_budget_supported:false`, ledger warn).
- Frontier preset: `{"alias":"frontier","class":"frontier"}` — never forwarded.

### 5.5 Ledger — unchanged schema/events, plus: `l1_degraded`, `cap_result` (mechanism + fingerprint id only), `kwargs_rejected`. Privacy invariant unchanged.

### 5.6 Digest templates

L1 `standard`, L1 `minimal`, L2, L3 — **verbatim as v0.2 §5.6**, plus:

**L1 `degraded`** (mechanism=none; exhortation returns by necessity — breaker-armed):
```
[minder] ESCALATION L1 — this model cannot toggle reasoning server-side.
- FAILED {n}x: {key}
- last error: {err[:300]}
- Prior attempts are VOID. First, in plain text, list 2-3 root-cause hypotheses
  with a check for each. Then acquire fresh ground truth. Only then retry.
```
Backfire breaker applies to **all** templates including `degraded`; `degraded` demotes to `minimal` like any other.

### 5.7 Config additions

```json
{"thinking_markers": ["<think>", "</think>"],
 "softswitch_tokens": {"on": "/think", "off": "/no_think"},
 "mechanism_override": null,
 "accept_l1_degraded": false}
```

`mechanism_override` ("kwargs"|"softswitch"|"none") forces the adapter — for power users who know better than the probe; every forced session logs `cap_override`.

---

## 6. Behavior Specification

§6.1 signals, §6.2 ladder/budgets/de-escalation, §6.4 dedupe, §6.5 backfire breaker, §6.7 Warden algorithm, §6.9 zcode adapter + installer merge, §6.10 skill text, §6.11 frontier contract — **all unchanged from v0.2.** Changes below.

### 6.3 Session scoping — unchanged (prefix fingerprint + `X-Minder-Session` override).

### 6.6 Mode Adapter (new — normative)

`apply_mode(req, want_thinking: bool, caps, preset) -> (req, degraded_flag)`

| mechanism | want=true | want=false |
|---|---|---|
| `kwargs` | merge `chat_template_kwargs:{enable_thinking:true, thinking_budget?}` | merge `enable_thinking:false` |
| `softswitch` | strip trailing softswitch tokens (regex `\s*/(no_)?think\s*$`) from last user message, append `on` token | same, append `off` token |
| `none` | no request mutation; return `degraded=True` (L1 digest-only; ledger `l1_degraded`) | no mutation |

Adapter runs **after** preset param merge, **before** digest append. It never touches non-final messages.

### 6.8 Turnstile pipeline — as v0.2 §6.8, with three additions:

1. **Alias resolution:** `req["model"]` → preset lookup → if `upstream_model` set, rewrite; unknown model strings pass untouched (unchanged).
2. **Mechanism translation:** param merge is now preset-params (sampler/temp/max_tokens — always safe) + Adapter output (mechanism-dependent). If preset is `class:think` but caps say `none`: params still applied, adapter no-ops, ledger `l1_degraded` once per session.
3. **`GET /v1/models` synthesis:** respond with upstream list **plus** minder aliases (`owned_by:"minder"`) so harnesses that enumerate models can select `qwen-exec`/`qwen-think`/`frontier`. Other GETs pass through.

Relay invariants, 4 MB scan gate, byte-exact SSE relay, degraded-path fail-open — all unchanged.

---

## 7. Engineering Constraints — as v0.2 §7, plus: **adapter mutations are pure functions over the parsed request** (unit-testable without network); probe code shares the same rule.

---

## 8. Acceptance Tests

| ID | Test | Pass |
|---|---|---|
| AT-1..AT-6, AT-11..AT-14 | unchanged from v0.2 §8 | unchanged |
| AT-7a | **kwargs differential** (live or mock): `qwen-think` shows markers/`reasoning_content`; `qwen-exec` shows none | per-request |
| AT-7b | **softswitch path**: mock `softswitch_only` upstream; recorded 3rd request body has trailing `/think` (or `/no_think`) on final user message, prior markers stripped | body assertion |
| AT-7c | **degraded path**: mock `none` upstream + `--accept-l1-degraded`; request params unchanged, digest still injected, ledger `l1_degraded` | |
| AT-15 | **CAP robustness**: scripted mocks — (a) server 400s on `chat_template_kwargs` ⇒ `kwargs_accepted:false`, falls through to T2; (b) reasoning in `reasoning_content` only ⇒ still detected; (c) probe on dead server ⇒ clean STOP, transcripts in report | |
| AT-16 | `upstream_model` rewrite present in recorded upstream body | |
| AT-17 | `/v1/models` synthesis lists aliases alongside upstream ids | |
| AT-8 | extended: with caps=kwargs, body rewritten to think preset atomically; with caps=softswitch, marker injected; **never both** | |
| AT-13 | extended: `cap_result` lines contain fingerprint ids only | |

**Mock upstream scripted behaviors** (selected via constructor arg): `default`, `reject_kwargs` (400 on unknown fields), `reasoning_content` (thinking lands in separate field), `softswitch_only` (honors markers, ignores kwargs), `sse` fixture stream.

---

## 9. Build Order

| Step | Deliverable | Gate |
|---|---|---|
| 0 | Preflight P-1..P-5 (P-6 requires adapter — run at step 2 against live server if present) | resolved or documented |
| 1 | `minder.py` + `test_warden.py` | AT-1..6, 13 |
| 2 | `adapter.py` (CAP + Mode Adapter) + `test_adapter.py`; run live CAP if upstream up | AT-15, AT-7a/7b/7c (mock-level) |
| 3 | `mock_upstream.py` variants + `proxy.py` + `test_proxy.py` | AT-8..10, 16, 17 |
| 4 | presets + zcode package + `test_hook.py` | AT-12 |
| 5 | `install.sh`: probes → CAP → fail-closed ladder → patched presets → merges (backup + verify) → skill append → systemd-user unit | AT-11 (point at `reject_kwargs` + `none` mocks for both ladder branches) |
| 6 | `test_concurrency.py`; full suite | AT-14; `pytest -q` green |
| 7 | Report §11 | — |

---

## 10. Assumptions

| ID | Assumption | If false |
|---|---|---|
| A1–A5 | unchanged from v0.2 | unchanged handling |
| A6 | The linked HF repos are **unverified** (no network in design session). Nothing in minder depends on their specifics; CAP decides empirically | none — design is repo-agnostic by construction |
| A7 | Softswitch conventions `/think`/`/no_think` (Qwen3 official) | probe measures empirically; tokens configurable (§5.7) |
| A8 | Reasoning may surface in `reasoning_content` (llama-server `--reasoning-format`) or inline | probe checks both fields; markers configurable |
| A9 | Server may reject unknown body fields (strict parsing builds) | `kwargs_accepted` recorded; adapter falls through |
| A10 | Community merges may need non-default samplers | `sampler_overrides` pass-through; users may add preset files; no name-based guessing |

---

## 11. Definition of Done & Report

Done = all ATs green or SKIP-with-reason (live AT-7a without GPU); installer verified on scratch `HOME` for **two ladder branches**: `kwargs` mock and `none`+`--accept-l1-degraded` mock.

```
REPORT
- Preflight: P-1..P-6 actuals (P-6: full model_caps.json verbatim)
- Changed behavior: files + one-line purpose
- Checks run: pytest summary; AT table status
- Assumptions: A1..A10 verified/degraded/failed + evidence
- CAP findings: mechanism, evidence char counts, kwargs_accepted, budget support
- Remaining failures & limitations: verbatim, no fixes applied
- Deviations from this PRD: must be empty; deviations are report items, not decisions
```

**One-line acceptance image:** the same zcode session works whether the user loaded official Qwen3 (kwargs flips thinking on the retry), a Qwen-Sharp-templated build (softswitch marker lands on the final user turn), or a toggle-less community merge (the void-marker digest still fires, budgets still hold, and the ledger says `l1_degraded` honestly) — because minder asked the server what it can do instead of trusting the name on the GGUF.
