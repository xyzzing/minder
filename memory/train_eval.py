"""Offline export summariser (docs/minder-phase-4-7-frontier-coding.md
P7.2 stub): counts, families, held-out ratio, redaction_ok.

Research input only. There is deliberately NO training here — no QLoRA, no
Unsloth, no adapter loading, nothing that touches llama-server. See
docs/lora-offline.md.
"""
import json
from pathlib import Path

from .canonicalise import redact


def summarise_export(path):
    """Reads an export file (.jsonl from train_export, or a .json list)
    and reports basic stats. Never raises."""
    try:
        records = _read(path)
    except Exception:
        return {"count": 0, "families": {}, "held_out_ratio": 0.0,
                "redaction_ok": False, "splits": {}}
    families = {}
    splits = {}
    redaction_ok = True
    for record in records:
        family = str(record.get("failure_family") or "unknown")
        families[family] = families.get(family, 0) + 1
        split = str(record.get("split") or "train")
        splits[split] = splits.get(split, 0) + 1
        blob = json.dumps(record, default=str)
        if redact(blob) != blob:  # a secret-shaped string survived
            redaction_ok = False
    count = len(records)
    held_out = splits.get("held_out", 0)
    return {"count": count, "families": families,
            "held_out_ratio": (held_out / count) if count else 0.0,
            "redaction_ok": redaction_ok, "splits": splits}


def _read(path):
    data = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".jsonl"):
        records = []
        for line in data.splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records
    loaded = json.loads(data)
    return loaded if isinstance(loaded, list) else [loaded]
