"""Optional laya classifier adapter (docs/minder-phase-4-7-frontier-coding.md
P5.1, slice D).

The `laya` package is NOT vendored and NOT required. try_laya_classifier
returns None when it is missing, times out, or exposes no recognisable
surface — callers then fall back to NullClassifier behaviour.

Laya 0.3.x surface: `laya.Agent(model_id_or_path, device="cpu")` then
`agent.system_one(state, questions)` — one non-autoregressive forward pass
over typed choice/score/noul questions, returning calibrated probabilities.
This adapter maps three fixed choice questions (failure_class,
recommended_action, egress_risk) onto the minder taxonomy.

Constraints:
- CPU only: no CUDA/ROCm initialisation (GPU visibility vars are cleared
  before import when not already set by the operator);
- preload at most once per process (cached; hook events never reload).
  Model loading is a one-time ~15 s cost, not a per-event cost;
- classify runs under a wall-clock timeout (default 500 ms) and degrades
  to a Null-style Classification on timeout or misbehaviour;
- no policy authority of any kind — this only ever feeds ShadowClassifier.
"""
import os
import threading
import warnings

from .classifier import (EGRESS_RISKS, FAILURE_CLASSES, RECOMMENDED_ACTIONS,
                         Classification, EventClassifier)

DEFAULT_TIMEOUT_MS = 2000

# The checkpoint ships in the standard HF cache; prefer the local snapshot
# over snapshot_download (which needs to write lock files and can fail on
# read-only caches).
_DEFAULT_MODEL_ID = "convaiinnovations/laya"
_HF_CACHE = os.path.join(
    os.environ.get("HF_HOME",
                   os.path.expanduser("~/.cache/huggingface")),
    "hub", "models--convaiinnovations--laya", "snapshots")

# Fixed triage questions — the criteria vocabulary IS the minder taxonomy,
# so a choice answer maps 1:1 and _coerce stays the safety net.
_QUESTIONS = {
    "failure_class": {
        "type": "choice",
        "instructions": ("Classify the root cause of this agent tool "
                         "failure. Use `error_excerpt` and `failure_key`."),
        "criteria": {
            "code_logic": "a bug in the code or logic being executed",
            "schema_contract": "wrong argument, field, schema or contract shape",
            "environment": "missing dependency, tool, env var, path, or external resource",
            "permissions": "forbidden, denied, or insufficient access rights",
            "test_expectation": "a test or assertion that was expected to pass but did not",
            "unknown": "none of the other options fits",
        },
    },
    "recommended_action": {
        "type": "choice",
        "instructions": ("Pick the single best next action for the agent "
                         "given this failure."),
        "criteria": {
            "inspect": "read the evidence / relevant files before acting",
            "retrieve_memory": "recall a previously verified lesson or skill",
            "think_retry": "re-derive the approach and retry with a new hypothesis",
            "environment_check": "verify the environment (deps, paths, services)",
            "block_duplicate": "stop repeating the identical failing action",
            "escalate_candidate": "escalate to a stronger model or human",
        },
    },
    "egress_risk": {
        "type": "choice",
        "instructions": ("Does this failure evidence contain content that "
                         "must not leave the machine (secrets, credentials, "
                         "external-prohibited data)?"),
        "criteria": {
            "safe": "no sensitive or external-prohibited content",
            "uncertain": "possibly sensitive, needs review",
            "block": "contains secrets or external-prohibited content",
        },
    },
}

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
        path = _resolve_model_path(laya)
        if path is None:
            return None
        # The shipped checkpoint warns about out-of-range temperatures;
        # that warning would land on the hook's stderr, so silence it here.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            agent = laya.Agent(path, device="cpu")
        # First forward pass compiles torch kernels (lazy init); do it once
        # here so the per-event wall-clock timeout covers real inference.
        _warmup(agent)
        return LayaClassifier(agent, timeout_ms=timeout_ms)
    except Exception:
        return None


def _resolve_model_path(laya):
    """Prefer a complete local HF snapshot (no network, no lock writes);
    fall back to the default model id (snapshot_download)."""
    env = os.environ.get("MINDER_LAYA_MODEL")
    if env:
        return env
    if os.path.isdir(_HF_CACHE):
        for snap in sorted(os.listdir(_HF_CACHE)):
            full = os.path.join(_HF_CACHE, snap)
            if (os.path.isfile(os.path.join(full, "model.safetensors"))
                    and os.path.isfile(os.path.join(full, "rl_agent_config.json"))):
                return full
    return _DEFAULT_MODEL_ID


def _warmup(agent):
    """One throwaway system_one pass so the first real event does not pay
    for torch lazy kernel initialisation inside its timeout budget.
    Uses a real-shaped compact event (laya caches per question shape).
    Never raises."""
    try:
        agent.system_one(
            {"tool": "warmup", "exit_code": None,
             "failure_key": "warmup|n/a|n/a|n/a",
             "error_excerpt": "warmup", "same_failure_count": None,
             "previous_route": None, "untrusted_content_present": False},
            _QUESTIONS)
    except Exception:
        pass


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
    """Runs the laya Agent's system_one pass over the fixed triage
    questions and maps the choice answers onto the minder taxonomy.
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
        # Laya 0.3.x surface first: Agent.system_one(state, questions).
        # (Agent inherits a `predict` from RLAgent with a different
        # signature, so system_one must be probed before predict.)
        if hasattr(self._model, "system_one"):
            return self._model.system_one(event, _QUESTIONS)
        # Legacy direct surface (test doubles): a model exposing classify()
        # or predict() is used as-is.
        if hasattr(self._model, "classify"):
            return self._model.classify(event)
        if hasattr(self._model, "predict"):
            return self._model.predict(event)
        raise TypeError("unrecognised laya classifier surface")


def _field(raw, name, default=None):
    if isinstance(raw, dict):
        return raw.get(name, default)
    return getattr(raw, name, default)


def _answer(raw, qid):
    """One system_one answer entry: the choice label + calibrated
    confidence. None when the entry is missing or malformed."""
    answers = _field(raw, "answers")
    if not isinstance(answers, dict):
        return None
    entry = answers.get(qid)
    if not isinstance(entry, dict):
        return None
    choice = entry.get("choice")
    if not isinstance(choice, str):
        return None
    try:
        conf = float(entry.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return choice, conf


def _coerce(raw):
    # Two surfaces: laya system_one (answers.{qid}.choice/.confidence) and
    # the legacy flat-field shape used by test doubles and older adapters.
    fc = _answer(raw, "failure_class")
    if fc is not None:
        failure_class, fc_conf = fc
    else:
        failure_class = _field(raw, "failure_class")
        fc_conf = 0.0
    if failure_class not in FAILURE_CLASSES:
        failure_class = "unknown"
    ra = _answer(raw, "recommended_action")
    if ra is not None:
        action, ra_conf = ra
    else:
        action = _field(raw, "recommended_action")
        ra_conf = 0.0
    if action not in RECOMMENDED_ACTIONS:
        action = "inspect"
    er = _answer(raw, "egress_risk")
    if er is not None:
        egress, er_conf = er
    else:
        egress = _field(raw, "egress_risk")
        er_conf = 0.0
    if egress not in EGRESS_RISKS:
        egress = "safe"
    try:
        confidence = min(1.0, max(0.0, float(_field(raw, "confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    # system_one has no top-level confidence: use the triage-class
    # confidence (the label this shadow row is about).
    if confidence == 0.0:
        confidence = min(1.0, max(0.0, fc_conf))
    version = str(_field(raw, "model_version") or "laya")
    return Classification(failure_class=failure_class,
                          recommended_action=action, egress_risk=egress,
                          confidence=confidence, model_version=version)
