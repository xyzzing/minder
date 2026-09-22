"""Optional Laya-backed provider (Phase 5.5). Import-guarded: returns
None whenever the `laya` package is missing, times out, or exposes no
recognisable surface — exactly like memory.classifier_laya. CPU only,
preload at most once per process, never required by the default test
run, and never authoritative (shadow only)."""
import os
import threading

from .adapter import ModelBackedClient

DEFAULT_TIMEOUT_MS = 500

_LOCK = threading.Lock()
_CACHE = {"tried": False, "client": None}


def try_laya_client(timeout_ms=DEFAULT_TIMEOUT_MS):
    """SystemOneClient | None. One build per process; import-guarded."""
    with _LOCK:
        if _CACHE["tried"]:
            return _CACHE["client"]
        _CACHE["tried"] = True
        client = _build(timeout_ms)
        _CACHE["client"] = client
        return client


def _build(timeout_ms):
    try:
        # CPU-only law, same as memory.classifier_laya
        for var in ("CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
            os.environ.setdefault(var, "")
        import laya  # noqa: F401 — optional, never vendored
        module_obj = _probe_module(laya)
        if module_obj is None:
            return None
        callable_model = probe_model(module_obj)
        if callable_model is None:
            return None
        return ModelBackedClient(callable_model)
    except Exception:
        return None


def _probe_module(laya):
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


def probe_model(model):
    """Accept objects exposing classify/predict (or plain callables);
    None otherwise."""
    for name in ("classify", "predict"):
        fn = getattr(model, name, None)
        if callable(fn):
            return lambda state, _fn=fn: _fn(state)
    if callable(model):
        return model
    return None
