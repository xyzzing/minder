"""Optional laya classifier adapter (docs/minder-phase-4-7-frontier-coding.md
P5.1, slice D).

The `laya` package is NOT vendored and NOT required. try_laya_classifier
returns None when it is missing, times out, or exposes no recognisable
surface — callers then fall back to NullClassifier behaviour.

Constraints:
- CPU only: no CUDA/ROCm initialisation (GPU visibility vars are cleared
  before import when not already set by the operator);
- preload at most once per process (cached; hook events never reload);
- classify runs under a wall-clock timeout (default 500 ms) and degrades
  to a Null-style Classification on timeout or misbehaviour;
- no policy authority of any kind — this only ever feeds ShadowClassifier.
"""
import os
import threading

from .classifier import (EGRESS_RISKS, FAILURE_CLASSES, RECOMMENDED_ACTIONS,
                         Classification, EventClassifier)

DEFAULT_TIMEOUT_MS = 500

_LOCK = threading.Lock()
_CACHE = {"tried": False, "classifier": None}


def try_laya_classifier(timeout_ms=DEFAULT_TIMEOUT_MS):
    """EventClassifier | None. Import-guarded; one build per process."""
    with _LOCK:
        if _CACHE["tried"]:
            return _CACHE["classifier"]
        _CACHE["tried"] = True
        clf = _build(timeout_ms)
        _CACHE["classifier"] = clf
        return clf


def _build(timeout_ms):
    try:
        # CPU-only law: keep laya off the GPU unless the operator chose one
        for var in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
            os.environ.setdefault(var, "")
        import laya  # noqa: F401 — optional, never vendored
        model = _probe_model(laya)
        if model is None:
            return None
        return LayaClassifier(model, timeout_ms=timeout_ms)
    except Exception:
        return None


def _probe_model(laya):
    """Look for a plausible laya entry point; None when unrecognisable."""
    for name in ("load_classifier", "classifier", "EventClassifier",
                 "Classifier", "get_classifier"):
        obj = getattr(laya, name, None)
        if obj is None:
            continue
        try:
            return obj() if callable(obj) else obj
        except Exception:
            continue
    return None


def _run_with_timeout(fn, args, timeout_ms):
    out = {}

    def runner():
        try:
            out["value"] = fn(*args)
        except Exception as exc:
            out["error"] = exc

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    worker.join(max(timeout_ms, 1) / 1000.0)
    if worker.is_alive():
        raise TimeoutError("laya classify timed out")
    if "error" in out:
        raise out["error"]
    return out.get("value")


class LayaClassifier(EventClassifier):
    """Maps whatever the laya surface returns onto the minder taxonomy.
    Anything missing, unknown, or out of range degrades to the safe
    default (unknown / inspect / safe) — never raises."""

    model_version = "laya"

    def __init__(self, model, timeout_ms=DEFAULT_TIMEOUT_MS):
        self._model = model
        self._timeout_ms = timeout_ms

    def classify(self, compact_event):
        try:
            raw = _run_with_timeout(self._infer, (compact_event,),
                                    self._timeout_ms)
            return _coerce(raw)
        except Exception:
            return Classification(failure_class="unknown",
                                  recommended_action="inspect",
                                  egress_risk="safe", confidence=0.0,
                                  model_version="laya-degraded")

    def _infer(self, event):
        if hasattr(self._model, "classify"):
            return self._model.classify(event)
        if hasattr(self._model, "predict"):
            return self._model.predict(event)
        raise TypeError("unrecognised laya classifier surface")


def _field(raw, name, default=None):
    if isinstance(raw, dict):
        return raw.get(name, default)
    return getattr(raw, name, default)


def _coerce(raw):
    failure_class = _field(raw, "failure_class")
    if failure_class not in FAILURE_CLASSES:
        failure_class = "unknown"
    action = _field(raw, "recommended_action")
    if action not in RECOMMENDED_ACTIONS:
        action = "inspect"
    egress = _field(raw, "egress_risk")
    if egress not in EGRESS_RISKS:
        egress = "safe"
    try:
        confidence = min(1.0, max(0.0, float(_field(raw, "confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    version = str(_field(raw, "model_version") or "laya")
    return Classification(failure_class=failure_class,
                          recommended_action=action, egress_risk=egress,
                          confidence=confidence, model_version=version)
