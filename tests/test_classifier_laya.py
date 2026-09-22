"""Laya adapter tests (docs/minder-phase-4-7-frontier-coding.md P5.1 slice
D). The suite must pass WITHOUT the laya package; only the explicitly
`laya`-marked test touches a real installation."""
import pytest

from memory import classifier, classifier_laya


def test_adapter_returns_none_without_laya(monkeypatch):
    try:
        import laya  # noqa: F401
    except ImportError:
        monkeypatch.setattr(classifier_laya, "_CACHE",
                            {"tried": False, "classifier": None})
        assert classifier_laya.try_laya_classifier() is None
        return
    pytest.skip("laya installed — covered by the marked test")


def test_try_laya_classifier_is_cached_per_process(monkeypatch):
    monkeypatch.setattr(classifier_laya, "_CACHE",
                        {"tried": False, "classifier": None})
    first = classifier_laya.try_laya_classifier()
    second = classifier_laya.try_laya_classifier()
    assert first is second  # no per-hook-event reloads


def test_surface_mapping_coerces_to_taxonomy(tmp_path):
    class FakeLaya:
        def classify(self, event):
            assert len(event["error_excerpt"]) <= classifier.MAX_EXCERPT_CHARS
            return {"failure_class": "permissions",
                    "recommended_action": "environment_check",
                    "egress_risk": "uncertain", "confidence": 0.9}

    clf = classifier_laya.LayaClassifier(FakeLaya(), timeout_ms=500)
    out = clf.classify({"tool": "bash", "failure_key": "k|f|s|p",
                        "error_excerpt": "permission denied"})
    assert out.failure_class == "permissions"
    assert out.recommended_action == "environment_check"
    assert out.egress_risk == "uncertain"
    assert out.confidence == 0.9
    assert out.model_version == "laya"


def test_garbage_and_timeout_degrade_to_null_behaviour():
    class Garbage:
        def classify(self, event):
            return {"failure_class": "nonsense", "confidence": 7.0}

    out = classifier_laya.LayaClassifier(Garbage()).classify({})
    assert out.failure_class == "unknown"
    assert out.recommended_action == "inspect"
    assert out.egress_risk == "safe"
    assert out.confidence == 1.0  # out-of-range values clamp, never pass through

    class Slow:
        def classify(self, event):
            import time
            time.sleep(2)

    out = classifier_laya.LayaClassifier(Slow(), timeout_ms=50).classify({})
    assert out.failure_class == "unknown"
    assert out.model_version == "laya-degraded"
    assert out.confidence == 0.0


@pytest.mark.laya
def test_real_laya_package_if_installed():
    laya = pytest.importorskip("laya")
    clf = classifier_laya.try_laya_classifier()
    if clf is None:
        pytest.skip("laya installed but exposes no recognised surface")
    out = clf.classify(classifier.compact_event(
        {"tool": "bash", "failure_key": "bash|connectionrefused|x|y",
         "error_excerpt": "ConnectionRefusedError: [Errno 111]"}))
    assert isinstance(out, classifier.Classification)
    assert out.failure_class in classifier.FAILURE_CLASSES
    assert 0.0 <= out.confidence <= 1.0
