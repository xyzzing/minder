#!/usr/bin/env python3
"""Fit the winning strategy on the fitting corpus and export the bundle.

The sweep (run_specialization_sweep.py) selected whitened_prototypes;
this refits it deterministically and exports the portable artifact the
minder seam loads. Fidelity gates run inside the export: probes are
replayed in a fresh process and any drift rejects the export.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
OUT = REPO / "presets" / "laya-specializations" / "difficulty-v1"
METHOD = "whitened_prototypes"


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
    from laya_studio.artifact import export_specialization
    from laya_studio.backends import create_backend
    from laya_studio.strategies import create_strategy

    task, rows = load_rows()
    print("loading backend ...", flush=True)
    backend = create_backend("pytorch", "convaiinnovations/laya", "cpu")
    print(f"fitting {METHOD} ...", flush=True)
    fitted = create_strategy(METHOD).fit(
        task, rows["specialization"], rows["validation"], backend)
    probes = [x.input for x in rows["validation"][:10]]
    path = export_specialization(
        OUT, name="difficulty-v1", task=task, fitted=fitted, backend=backend,
        probes=probes,
    )
    print(f"exported: {path}")
    manifest = json.loads((Path(path) / "manifest.json").read_text())
    print(json.dumps({k: manifest[k] for k in sorted(manifest)[:12]},
                     indent=2, default=str)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
