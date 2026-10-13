"""difficulty-core-v1 suite gate.

The eval set is owner-reviewed data with a stable shape; these checks
hold no matter which provider replays it: every case validates against
the task-difficulty/v1 vocabulary, the state shape matches what the
router renders in production, and the laya-studio fitting corpus never
shares an input with the eval set (fitting on eval data is leakage, the
exact defect laya-studio's own suite loader refuses)."""
import hashlib
import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
SUITE = HERE / "benchmarks" / "difficulty-core-v1"
LABELS = ("mechanical", "routine", "complex", "expert_or_ambiguous")
ACCEPTED_RE = re.compile(r"^owner_accepted_\d{4}-\d{2}-\d{2}$")
DRAFT_STATUS = "draft_pending_owner_review"


def _doc():
    return json.loads((SUITE / "cases" / "difficulty_cases.json").read_text())


def _norm(text):
    import re as _re
    return _re.sub(r"\s+", " ", text.strip().lower())


def test_cases_validate_against_the_contract_vocabulary():
    doc = _doc()
    assert doc["contract"] == "task-difficulty/v1"
    cases = doc["cases"]
    assert doc["case_count"] == len(cases)
    assert len(cases) >= 200
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["gold"]["label"] in LABELS, case["id"]
        # integer scores bucket 1:1 onto labels (buckets sit at 0.5/1.5/2.5)
        assert case["gold"]["score"] == LABELS.index(case["gold"]["label"])
        state = case["state"]
        assert set(state) == {"task", "turn"}, case["id"]
        assert isinstance(state["task"], str) and state["task"], case["id"]
        assert isinstance(state["turn"], int) and state["turn"] >= 1
        # every class declares an owner-reviewable description
        assert case["description"], case["id"]


def test_rendered_state_matches_production_shape():
    """The case state must survive the exact production render path."""
    from minder_decision.providers.laya import _render_state
    for case in _doc()["cases"]:
        rendered = _render_state(case["state"])
        assert rendered == (
            f"task={case['state']['task']}; turn={case['state']['turn']}"
        ), case["id"]


def test_label_status_is_reviewable():
    """Either an explicit draft marker or a dated owner acceptance."""
    status = _doc()["label_status"]
    assert status == DRAFT_STATUS or ACCEPTED_RE.match(status), status


def test_fitting_corpus_never_shares_an_input_with_the_eval_set():
    eval_hashes = {_norm(c["state"]["task"]) for c in _doc()["cases"]}
    fit_dir = SUITE / "fitting"
    for name in ("specialization", "validation", "hard"):
        assert (fit_dir / f"{name}.json").exists()
    for part in sorted(fit_dir.glob("*.json")):
        data = json.loads(part.read_text())
        for ex in data["examples"]:
            assert ex["label"] in LABELS, ex["id"]
            assert ex["split"] in ("specialization", "validation", "hard"), ex["id"]
            if _norm(ex["input"]) in eval_hashes:
                pytest.fail(f"fitting row {ex['id']} duplicates the eval set")


def test_content_hashes_are_stable_across_rebuilds():
    """The builder is the source of truth; the checked-in JSON must be
    exactly what the literals produce (guards hand-edited drift)."""
    import subprocess
    import sys
    cases_file = SUITE / "cases" / "difficulty_cases.json"
    before = hashlib.sha256(cases_file.read_bytes()).hexdigest()
    proc = subprocess.run(
        [sys.executable, str(SUITE / "build_cases.py")],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    after = hashlib.sha256(cases_file.read_bytes()).hexdigest()
    assert before == after, ("checked-in cases drift from build_cases.py "
                             "literals; rerun the builder and commit")
    review = SUITE / "REVIEW.md"
    assert review.exists() and "owner review" in review.read_text()
