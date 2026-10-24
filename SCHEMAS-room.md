# Shared file formats: Roomwise <-> minder

Single source of truth for every file that crosses between Roomwise
(measurement and training) and minder (runtime). Neither project imports
the other; these files are the whole interface. Referenced by
`PRD-roomwise.md` and `PRD-minder-room-runtime.md`.

## Rules for every format

1. Every file carries a top-level `"schema": "<name>/v<major>"`.
2. Additive changes (new optional fields) keep the major version.
   Consumers ignore fields they do not know.
3. Removing or changing the meaning of a field is a new major version.
   Producers may write both versions during a transition; consumers accept
   the versions they list and refuse others with a clear message.
4. Both repositories keep golden fixtures of each format they produce or
   consume (`tests/fixtures/schemas/`) and test against them. A fixture
   change in one repo is copied to the other in the same release.
5. Numbers that are rates are always derivable from counts stored in the
   same file. Rates are computed when read, never rounded before comparison.
6. No message text from live sessions appears in any format. Eval text
   (synthetic or public datasets) may appear only where stated.
7. Files that depend on a specific model carry a `model_fingerprint`
   (§8). Consumers compare it with the current engine and warn on mismatch.

## 1. `roomwise.sweep-manifest/v1`

Producer: Roomwise `sweep`. Consumer: Roomwise reports, training, people.

```json
{"schema": "roomwise.sweep-manifest/v1",
 "roomwise_version": "0.1.0", "git_sha": "...",
 "target": {"base_url": "http://127.0.0.1:8081/v1", "kind": "engine|proxy",
            "model": "qwen3.8-flash-next"},
 "model_fingerprint": {"...": "§8"},
 "preflight": {"server_default_thinking": true,
               "mechanism": "kwargs|effort_field|softswitch|none",
               "effort_levels_accepted": ["low", "medium", "high"],
               "thinking_budget_accepted": true},
 "cases": {"path": "seed8.jsonl", "sha256": "...", "suite": "domain-core-v1",
           "seed": 8, "count": 111},
 "grid": [{"setting": "json:off:0", "reply_format": "json", "effort": "off",
           "budget": 0}],
 "skipped_settings": [{"setting": "json:off:256", "reason": "budget without thinking"}],
 "repeats": 3, "max_tokens": 4096, "temperature": 0,
 "started_at": "ISO-8601", "finished_at": "ISO-8601"}
```

## 2. `roomwise.sweep-run/v1` (JSONL, one line per case per run file)

```json
{"schema": "roomwise.sweep-run/v1", "setting": "json:off:0", "repeat": 1,
 "id": "finance-8-00004", "answer": {}, "raw_chars": 92,
 "finish_reason": "stop", "reasoning_tokens": 0, "completion_tokens": 81,
 "prompt_tokens": 597, "latency_s": 2.1, "error": null,
 "t_start": "ISO-8601", "t_end": "ISO-8601"}
```

`t_start` and `t_end` exist so energy traces (Sinter `power-*.jsonl`) can
be joined per case later.

## 3. `roomwise.sweep-report/v1`

```json
{"schema": "roomwise.sweep-report/v1",
 "manifest_sha256": "...", "model_fingerprint": {"...": "§8"},
 "settings": {
   "json:off:0": {
     "by_family": {
       "finance/npv": {"n": 10, "passed": [0, 0, 0],
                       "outcomes": {"parse_failure": 0, "starved": 0, "truncated": 0},
                       "tokens": {"completion_p50": 31, "completion_p90": 40,
                                  "reasoning_p50": 0, "reasoning_p90": 0}}}}},
 "families": {
   "finance/npv": {"noise_floor_points": 10.0,
                   "best_setting": "work:off:0",
                   "min_sufficient_setting": "work:off:0",
                   "mcnemar_vs_best": {"json:off:0": 0.0039}}},
 "energy": null}
```

`energy` is filled by the Roomwise energy join when Sinter traces are
supplied: per setting and family, `gpu_wh`, `gco2e`, `gco2e_per_correct`,
`grid_factor_kg_per_kwh`, `scope: "gpu_only"`.

## 4. `roomwise.room-policy/v1`

Producer: Roomwise `policy`. Consumer: minder `minder-op room apply`.

```json
{"schema": "roomwise.room-policy/v1",
 "generated_at": "ISO-8601", "source_report_sha256": "...",
 "model_fingerprint": {"...": "§8"},
 "rules": [
   {"match": {"contract": "task-difficulty/v1", "label": "mechanical"},
    "room": {"reply_format": "json", "effort": "off", "budget": null,
             "max_tokens": 2048},
    "evidence": {"families": ["finance/gst_invoice", "governance/pdpa_breach"],
                 "passed": 42, "n": 42, "repeats": 3}},
   {"match": {"contract": "task-difficulty/v1", "label": "complex"},
    "room": {"reply_format": "work", "effort": "low", "budget": 1024,
             "max_tokens": 4096},
    "evidence": {"families": ["finance/npv"], "passed": 21, "n": 30, "repeats": 3}}],
 "label_map": {"finance/npv": "complex", "finance/gst_invoice": "mechanical"},
 "requires_approval": ["effort_off_for_auto"]}
```

- `match` keys are labels minder can see at runtime (a Laya contract
  label), never benchmark family names, because live requests have no
  family. `label_map` records how families were mapped to labels.
- `room.reply_format: "work"` is advisory for minder in v1 (minder does not
  rewrite prompts); minder applies `effort`, `budget` and `max_tokens`.
- `requires_approval` lists policy choices that change a minder default
  (for example thinking off in automatic mode). minder refuses them unless
  the operator passes the matching opt-in.

## 5. `roomwise.laya-dataset/v1` (JSONL)

Producer: Roomwise `labels export`. Consumer: Roomwise training.

```json
{"schema": "roomwise.laya-dataset/v1",
 "row_id": "...", "case_id": "finance-8-00004",
 "suite": "domain-core-v1", "seed": 8, "family": "finance/npv",
 "domain": "business_rules|coding|research",
 "text": "<rendered eval prompt, redacted>",
 "contract_id": "task-room", "contract_version": "v1",
 "question_id": "multi_step", "label": "yes",
 "label_source": "construction|human|outcome|judge",
 "label_detail": {"repeats": 3, "passing_setting": "work:off:0", "agreement": 1.0},
 "model_fingerprint": null,
 "split": "train|held_out"}
```

`model_fingerprint` is required for `outcome` rows and null otherwise.
`judge` rows exist only with maintainer approval and are excluded from
success measures by default.

## 6. `roomwise.laya-calibration/v1`

Producer: Roomwise `train calibrate`. Consumer: minder's Laya provider.

```json
{"schema": "roomwise.laya-calibration/v1",
 "laya_checkpoint": {"name": "...", "sha256": "..."},
 "dataset_sha256": "...",
 "temperatures": [
   {"contract": "task-difficulty/v1", "question": "difficulty",
    "domain": "business_rules", "temperature": 1.4,
    "held_out": {"n": 60, "ece_before": 0.28, "ece_after": 0.09,
                 "selective_accuracy_before": 0.74, "selective_accuracy_after": 0.78}}]}
```

One temperature per contract, question and domain. minder applies a
temperature only when the checkpoint sha256 matches the loaded checkpoint
and the request's domain is known; otherwise it ignores the file and logs
why.

## 7. `roomwise.laya-checkpoint/v1`

Producer: Roomwise `train finetune`. Consumer: minder's Laya provider.

```json
{"schema": "roomwise.laya-checkpoint/v1",
 "path": "/home/.../laya-ft-2026-10-20", "sha256": "...",
 "base": {"name": "...", "license": "Apache-2.0"},
 "dataset_sha256": "...",
 "held_out": {"by_contract": {"task-room/v1": {"accuracy": 0.0, "ece": 0.0}}},
 "room_sweep_check": {"report_sha256": "...", "beats_best_fixed": true}}
```

minder loads a checkpoint only when `room_sweep_check.beats_best_fixed` is
true, and only into shadow mode until the operator switches it on.

## 8. `model_fingerprint` (embedded object)

```json
{"model_id": "qwen3.8-flash-next",
 "weights_path": "/home/.../Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf",
 "quant": "IQ3_S",
 "engine": "strata 0.1.42-0ebe4053",
 "chat_template_sha256": "...",
 "n_ctx": 131072}
```

Two fingerprints match when `weights_path` (or its sha256 if recorded),
`engine` and `chat_template_sha256` match. A changed chat template counts
as a different model, because it changes thinking defaults (the
replication's `xhigh` incident).

## 9. `roomwise.rulebook-provenance/v1`

Producer: Roomwise `rules --json`. Consumer: minder console (optional).

```json
{"schema": "roomwise.rulebook-provenance/v1",
 "rows": [{"rule": "sg_carbon_tax.rate", "verification": "primary",
           "verified_on": "2026-09-30", "source": "..."}]}
```

minder's console shows this table when the file exists at the configured
path, and `n/a` otherwise. `rows` must not be empty: an empty table is a
producer bug, and a consumer cannot tell it from a rulebook with no
rules, so the validator refuses it.

## 10. Read-only from minder: `model_caps.json`

Owned and versioned by minder. Roomwise may read it (path given by the
user) to skip its own preflight, using only: `thinking.mechanism`,
`effort_supported`, `effort_channel`, `effort_levels`,
`thinking.thinking_budget_supported`, `thinking.thinking_budget_enforced`.
If absent, Roomwise runs its own preflight.

## 11. minder benchmark report envelope (owned by minder)

Roomwise `score` reports keep minder's benchmark report envelope
(`report_version` 1 or 2, `suite_id`, `suite_fingerprint`, `generated_at`,
`kind`, `runs`, `metrics`), so `minder-op benchmark compare` reads them.
Roomwise does not import minder's validator. Instead,
`roomwise/fixtures/minder-benchmark-report.json` is a report generated by
this code and accepted by minder's `validate_report`; Roomwise tests check
every report against its shape. When minder changes its envelope, minder
regenerates the fixture and copies it here in the same release (rule 4).
