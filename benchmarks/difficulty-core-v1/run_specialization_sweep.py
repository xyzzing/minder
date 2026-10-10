#!/usr/bin/env python3
"""Run the laya-studio specialization sweep for difficulty-core-v1.

Fits every applicable strategy on the fitting corpus (fitting/, never
the eval set), scores each on the validation split (accuracy, macro F1,
Brier, ECE, per-row CPU latency), and prints a selection table. This is
offline tooling: results land in experiments/ (gitignored) and the
exported bundle is what the repo tracks.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "experiments" / "difficulty-sweep"

METHODS = [
    "baseline",
    "prompt_only",
    "nearest_prototype",
    "contrastive_vector",
    "multiclass_centroids",
    "whitened_prototypes",
    "residual_embedding_transform",
    "multi_vector_steering",
    "activation_steering",
    "activation_steering@11",
]


def load_rows():
    import yaml
    from laya_studio.schemas import Example, TaskSpec

    task = TaskSpec.model_validate(
        yaml.safe_load((HERE / "fitting" / "task.yaml").read_text()))
    rows = {}
    for split in ("specialization", "validation"):
        data = json.loads((HERE / "fitting" / f"{split}.json").read_text())
        rows[split] = [Example.model_validate(r) for r in data["examples"]]
    return task, rows


def main():
    from laya_studio.backends import create_backend
    from laya_studio.metrics import classification_metrics
    from laya_studio.strategies import StrategyNotApplicable, create_strategy

    task, rows = load_rows()
    OUT.mkdir(parents=True, exist_ok=True)
    print("backend: loading laya on cpu ...", flush=True)
    started = time.perf_counter()
    backend = create_backend("pytorch", "convaiinnovations/laya", "cpu")
    print(f"loaded in {time.perf_counter() - started:.1f}s", flush=True)

    labels = task.decision.labels
    expected = [x.label for x in rows["validation"]]
    inputs = [x.input for x in rows["validation"]]
    results = []
    for method in METHODS:
        line = {"method": method}
        try:
            fit_start = time.perf_counter()
            fitted = create_strategy(method).fit(
                task, rows["specialization"], rows["validation"], backend)
            line["fit_seconds"] = round(time.perf_counter() - fit_start, 2)
            # cold latency: clear the backend's embedding cache so the
            # measurement reflects production, where every request pays
            # one encoder forward
            backend._embedding_cache.clear()
            pred_start = time.perf_counter()
            preds = fitted.predict(inputs)
            per_row_ms = (time.perf_counter() - pred_start) * 1000 / len(inputs)
            probs = [p["probabilities"] for p in preds]
            m = classification_metrics(expected, probs, labels)
            line.update(
                validation_accuracy=round(m["accuracy"], 4),
                macro_f1=round(m["macro_f1"], 4),
                brier=round(m["brier_score"], 4),
                ece=round(m["ece"], 4),
                cold_per_row_ms=round(per_row_ms, 1),
                decision_component=fitted.artifact.decision_component,
            )
            print(json.dumps(line), flush=True)
        except StrategyNotApplicable as exc:
            line["error"] = f"not applicable: {exc}"
            print(json.dumps(line), flush=True)
        except Exception as exc:  # noqa: BLE001 - sweep continues
            line["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(line), flush=True)
        results.append(line)
        (OUT / "sweep_results.json").write_text(
            json.dumps({"suite": "difficulty-core-v1", "results": results},
                       indent=2) + "\n")

    print(f"wrote {OUT / 'sweep_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
