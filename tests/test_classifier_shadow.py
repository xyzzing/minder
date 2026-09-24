"""Classifier shadow-mode tests (docs/minder-phase-4-7-frontier-coding.md
P5.1). The classifier is log-only: policy outcomes are byte-identical with
and without it, inner crashes degrade, and excerpts are redacted + truncated
before any classifier sees them. These tests never require the laya
package."""
import pytest

from memory import (classifier, db as _db, from_hook, policy as memory_policy)

REPO = "/repo"


def hook_event(tool_response="KeyError: 'supplier_id'"):
    return {"session_id": "s-shadow", "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/repo/app/supplier.py"},
            "tool_response": tool_response}


def seed_repeats(dbp, ev, n=2):
    """Record n identical failures via the production path so the
    duplicate guard can fire."""
    for _ in range(n):
        from_hook.record(ev, db_path=dbp)


def _shadow_rows(dbp):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM classifier_shadow ORDER BY ts").fetchall()]
    finally:
        conn.close()


def test_null_classifier_never_raises_confidence_zero():
    null = classifier.NullClassifier()
    for event in ({}, None, {"error_excerpt": "x" * 5000},
                  {"tool": "bash", "exit_code": -1}):
        out = null.classify(event)
        assert out.failure_class == "unknown"
        assert out.confidence == 0.0
        assert out.model_version == "null"
        assert out.egress_risk == "safe"


def test_shadow_row_written_and_policy_identical(tmp_path, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    ev = hook_event()
    seed_repeats(dbp, ev)
    warden = {"action": "think", "level": 1, "digest": "[minder] warden"}

    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    out_without = memory_policy.evaluate(ev, warden, db_path=dbp)
    assert _shadow_rows(dbp) == []

    # This test exercises the shadow *mechanism* with a null inner,
    # independent of whether laya happens to be installed.
    monkeypatch.setenv("MINDER_CLASSIFIER", "shadow")
    monkeypatch.setattr(classifier, "get_classifier",
                        lambda db_path=None: classifier.ShadowClassifier(
                            None, db_path=dbp))
    out_with = memory_policy.evaluate(ev, warden, db_path=dbp)

    assert out_with == out_without  # byte-identical directive
    assert out_with["action"] == "block_duplicate"
    rows = _shadow_rows(dbp)
    assert len(rows) == 1  # logged, not consulted
    row = rows[0]
    assert row["policy_action"] == "block_duplicate"
    assert row["failure_class"] == "unknown"  # null inner: no laya here
    assert row["confidence"] == 0.0
    assert row["failure_key"] == out_with["duplicate"]["failure_key"]


def test_inner_crash_degrades_and_policy_still_runs(tmp_path, monkeypatch):
    class Broken:
        def classify(self, compact_event):
            raise RuntimeError("boom")

    dbp = tmp_path / "m.sqlite"
    shadow = classifier.ShadowClassifier(Broken(), db_path=dbp)
    out = shadow.classify({"tool": "bash", "failure_key": "k|f|s|p",
                           "error_excerpt": "x"})
    assert out.failure_class == "unknown"
    assert out.model_version == "degraded"
    rows = _shadow_rows(dbp)
    assert len(rows) == 1 and rows[0]["model_version"] == "degraded"

    # and with the broken classifier active in policy, the result matches
    ev = hook_event()
    seed_repeats(dbp, ev)
    warden = {"action": "think", "level": 1, "digest": "d"}
    monkeypatch.setenv("MINDER_CLASSIFIER", "shadow")
    monkeypatch.setattr(classifier, "get_classifier",
                        lambda db_path=None: shadow)
    out_with = memory_policy.evaluate(ev, warden, db_path=dbp)
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    out_without = memory_policy.evaluate(ev, warden, db_path=dbp)
    assert out_with == out_without


def test_excerpt_redacted_and_truncated_before_classify(tmp_path):
    seen = {}

    class Spy:
        def classify(self, compact_event):
            seen.update(compact_event)
            return classifier.Classification(
                failure_class="environment",
                recommended_action="environment_check",
                egress_risk="safe", confidence=0.42,
                model_version="spy-1")

    dbp = tmp_path / "m.sqlite"
    shadow = classifier.ShadowClassifier(Spy(), db_path=dbp)
    secret = "sk-proj-abcdefghijklmnop"
    shadow.classify({"tool": "bash", "failure_key": "k|f|s|p",
                     "error_excerpt": f"leak {secret} " + "y" * 2000})
    assert len(seen["error_excerpt"]) <= classifier.MAX_EXCERPT_CHARS
    assert secret not in seen["error_excerpt"]
    assert seen["tool"] == "bash"
    assert "same_failure_count" in seen  # whitelist keys survive
    rows = _shadow_rows(dbp)
    assert rows[0]["confidence"] == 0.42
    assert rows[0]["model_version"] == "spy-1"


def test_default_mode_requires_no_laya_and_off_by_default(tmp_path,
                                                          monkeypatch):
    # the whole module runs without laya installed; default env is off
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    assert classifier.get_classifier(db_path=tmp_path / "m.sqlite") is None
    monkeypatch.setenv("MINDER_CLASSIFIER", "off")
    assert classifier.get_classifier(db_path=tmp_path / "m.sqlite") is None
