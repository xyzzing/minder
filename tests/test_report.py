"""Audit surface tests: minder.report + consults.jsonl (white spaces #2, #3)."""
import json
import os
import subprocess
import sys
from pathlib import Path

SUBPROC_TIMEOUT = int(os.environ.get("MINDER_TEST_TIMEOUT", "60"))

import minder

MINDER = Path(__file__).resolve().parent.parent / "minder.py"


def test_log_consult_appends_record(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    minder.log_consult("t1", "edit:/x/a.py", 3, "old_string not found",
                       "FRONTIER RESPONSE:\nhypothesis: anchor drift")
    rec = json.loads((tmp_path / "state" / "consults.jsonl")
                     .read_text().splitlines()[-1])
    assert rec["task"] == "t1" and rec["attempts"] == 3
    assert "anchor drift" in rec["response"]
    assert len(rec["error"]) <= 2000 and len(rec["response"]) <= 4000


def test_report_json_and_text(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir(parents=True)
    with open(tmp_path / "state" / "events.jsonl", "w") as f:
        f.write(json.dumps({"ts": 1700000000, "task": "s1",
                            "event": "escalate", "level": 1,
                            "key": "edit:/x/a.py", "n": 2}) + "\n")
        f.write(json.dumps({"ts": 1700000100, "task": "s1",
                            "event": "deescalate", "key": "edit:/x/a.py"})
                + "\n")
    with open(tmp_path / "state" / "consults.jsonl", "w") as f:
        f.write(json.dumps({"ts": 1700000200, "task": "s1",
                            "key": "edit:/x/a.py", "attempts": 4,
                            "error": "boom", "response": "advice"}) + "\n")

    out = minder.report()
    assert "escalate" in out and "edit:/x/a.py" in out
    assert "consult" in out and "advice" in out
    # noise events are filtered out (only interventions listed)
    assert "cap_missing" not in out

    as_json = minder.report(as_json=True)
    assert len(as_json["interventions"]) == 2
    assert as_json["consults"][0]["response"] == "advice"


def test_report_cli_subprocess(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "MINDER_STATE_DIR": str(tmp_path / "state")}
    (tmp_path / "state").mkdir(parents=True)
    with open(tmp_path / "state" / "events.jsonl", "w") as f:
        f.write(json.dumps({"ts": 1700000000, "task": "s1",
                            "event": "escalate", "level": 2,
                            "key": "cmd:pytest", "n": 5}) + "\n")
    r = subprocess.run([sys.executable, str(MINDER), "report", "--json"],
                       capture_output=True, text=True, env=env, timeout=SUBPROC_TIMEOUT)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["interventions"][0]["event"] == "escalate"
    assert payload["interventions"][0]["key"] == "cmd:pytest"


def test_report_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(minder, "STATE_DIR", tmp_path / "missing")
    assert minder.report() == "(no interventions recorded)"
    assert minder.report(as_json=True) == {"interventions": [], "consults": []}
