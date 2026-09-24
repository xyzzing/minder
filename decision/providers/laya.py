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
        if module_obj is not None:
            callable_model = probe_model(module_obj)
            if callable_model is not None:
                return ModelBackedClient(callable_model)
        # legacy surface absent — try the laya >= 0.3.6 Router/Agent
        # system_one surface (the domain-routing integration)
        return _try_modern()
    except Exception:
        return _try_modern()


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


# --- laya >= 0.3.6 (Router/Agent system_one surface, Phase 2) -------------
# 0.3.6 speaks the decision gateway's protocol natively (system_one,
# choice/score/noul questions, calibrated probabilities, usage counts).
# CPU-only law: MINDER_LAYA_DEVICE defaults to cpu; weights come from
# MINDER_LAYA_MODEL (default convaiinnovations/laya, HF-cached after the
# first load — install-time network, never per-call egress).

ROUTE_INSTRUCTIONS = {
    "candidate_domain": "Which domain contract should govern this task?",
    "transition": "What transition does this boundary represent?",
    "intent_kind": "Advisory: what kind of intent is this?",
}

TRIAGE_INSTRUCTIONS = {
    "failure_kind": "What kind of failure is this? Choose the best match "
                   "from the available failure classes.",
    "needs_new_evidence": "Does resolving this failure require gathering "
                          "new evidence (running a command, reading a file, "
                          "checking the environment) rather than retrying "
                          "the same action?",
    "next_step": "Given the failure, attempt count, and available actions, "
                 "which action should be tried next?",
}

SKILL_SELECT_INSTRUCTIONS = {
    "any_skill_applies": "Does at least one of the listed skills "
                        "directly address this failure? Answer yes only "
                        "when a skill's trigger or description clearly "
                        "matches the failure.",
    "best_skill": "Which listed skill best addresses this failure? "
                  "Choose 'none' only when no skill clearly applies.",
}

DIFFICULTY_INSTRUCTIONS = {
    "difficulty": ("Classify the difficulty of the task. Grade the "
                   "REASONING the task requires — not its length or the "
                   "amount of code involved. Pick exactly one label."),
    "difficulty_score": ("Score the difficulty of the task 0-3. Grade the "
                         "REASONING the task requires — not its length or "
                         "the amount of code involved."),
}

_ALL_INSTRUCTIONS = {
    **ROUTE_INSTRUCTIONS,
    **TRIAGE_INSTRUCTIONS,
    **SKILL_SELECT_INSTRUCTIONS,
    **DIFFICULTY_INSTRUCTIONS,
}


class LayaSystemOneClient:
    """SystemOneClient over a laya >= 0.3.6 Agent. Maps decision-contract
    questions onto laya's choice/noul question dicts and maps answers
    back onto a DecisionResponse (full distributions where laya provides
    them, one-hot where it only gives an argmax). Reports token usage on
    the response for the decision_usage ledger."""

    def __init__(self, agent, source="convaiinnovations/laya"):
        self._agent = agent
        self._source = source
        self.model_version = f"laya/{source}"

    def system_one(self, state, questions, model=None, contract=None):
        from ..types import DecisionResponse, ScoreQuestion, top_two_margin
        from .fake import FakeClient
        laya_questions = {}
        for question in questions:
            options = getattr(question, "options", None)
            instructions = _ALL_INSTRUCTIONS.get(
                question.id, f"{question.id}?")
            if isinstance(question, ScoreQuestion):
                criteria = (list(question.criteria)
                            if question.criteria
                            else [f"level {i}" for i in range(
                                question.min_value,
                                question.max_value + 1)])
                laya_questions[question.id] = {
                    "type": "score", "instructions": instructions,
                    "criteria": criteria}
                continue
            if options is None:  # Noul
                laya_questions[question.id] = {
                    "type": "noul", "instructions": instructions}
            else:
                criteria = getattr(question, "criteria", None)
                if isinstance(criteria, dict) and criteria:
                    criteria = {o: str(criteria.get(o, o)) for o in options}
                else:
                    criteria = {o: o.replace("_", " ") for o in options}
                laya_questions[question.id] = {
                    "type": "choice", "instructions": instructions,
                    "criteria": criteria}
        raw = self._agent.predict(_render_state(state), laya_questions)
        answers = raw.get("answers") or {}
        usage = raw.get("usage") or {}
        spec = {}
        confidence = 0.0
        primary = _primary_choice_id(questions)
        for question in questions:
            options = getattr(question, "options", None)
            answer = answers.get(question.id) or {}
            if isinstance(question, ScoreQuestion):
                try:
                    score = float(answer.get("score"))
                except (TypeError, ValueError):
                    score = float(question.min_value)
                spec[question.id] = min(
                    float(question.max_value),
                    max(float(question.min_value), score))
                continue
            if options is None:
                try:
                    spec[question.id] = min(1.0, max(
                        0.0, float(answer.get("noul", 0.5))))
                except (TypeError, ValueError):
                    spec[question.id] = 0.5
                continue
            probs = answer.get("probabilities")
            if not isinstance(probs, dict) and answer.get("choice"):
                probs = {answer["choice"]: 1.0}
            spec[f"{question.id}_probs"] = _match_options(probs, options)
            if question.id == primary:
                try:
                    confidence = min(1.0, max(
                        0.0, float(answer.get("confidence", 0.0))))
                except (TypeError, ValueError):
                    confidence = 0.0
        spec["confidence"] = confidence
        key = f"laya:{id(self)}"
        # key_fn=lambda s: key — FakeClient's default _state_key extracts
        # failure_key from the state, which is absent here (the state is
        # the rendered routing dict), so the fixture lookup would miss
        # and fall back to uniform. Pin the key explicitly.
        response = FakeClient({key: spec}, key_fn=lambda _s: key).system_one(
            key, questions, model=model, contract=contract)
        response.provider = "laya"
        response.model_version = self.model_version
        response.prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        response.completion_tokens = int(
            usage.get("output_tokens", 0) or 0)
        response.top_two_margin = top_two_margin(
            (response.choice_probs or {}).get(primary) or {})
        return response


def _primary_choice_id(questions):
    from ..types import ChoiceQuestion
    choice_ids = [q.id for q in questions if isinstance(q, ChoiceQuestion)]
    return choice_ids[-1] if choice_ids else None


def _match_options(probs, options):
    """Clamp laya's probabilities onto the exact option set the contract
    demands (missing -> 0.0, extras dropped) and renormalise to 1."""
    cleaned = {}
    for option in options:
        try:
            cleaned[option] = min(1.0, max(
                0.0, float((probs or {}).get(option, 0.0))))
        except (TypeError, ValueError):
            cleaned[option] = 0.0
    total = sum(cleaned.values())
    if total <= 0:
        return {option: 1.0 / len(options) for option in options}
    return {option: value / total for option, value in cleaned.items()}


def _render_state(state):
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return "; ".join(f"{key}={state[key]}"
                         for key in sorted(state) if state[key] is not None)
    return str(state)


def _resolve_model_path(repo):
    """Prefer the local HF snapshot over snapshot_download, which fails
    when the cache is read-only (the common case for agent sandboxes).
    MINDER_LAYA_MODEL can point at a local path or an HF repo id."""
    import os as _os
    if _os.path.isdir(repo):
        return repo
    # HF cache layout: models--<org>--<name>/snapshots/<hash>/
    safe = repo.replace("/", "--").replace("-", "--")
    hub_dir = _os.path.expanduser(
        f"~/.cache/huggingface/hub/models--{repo.replace('/', '--')}")
    snap_dir = _os.path.join(hub_dir, "snapshots")
    if _os.path.isdir(snap_dir):
        entries = sorted(
            (e for e in _os.listdir(snap_dir)
             if _os.path.isdir(_os.path.join(snap_dir, e))),
            reverse=True)
        if entries:
            return _os.path.join(snap_dir, entries[0])
    # fall back to laya's own loader (may need network)
    return repo


def _try_modern():
    try:
        import laya
        if not any(hasattr(laya, name)
                   for name in ("Router", "Agent", "load")):
            return None
        repo = os.environ.get("MINDER_LAYA_MODEL",
                              "convaiinnovations/laya")
        device = os.environ.get("MINDER_LAYA_DEVICE", "cpu")
        path = _resolve_model_path(repo)
        # suppress the checkpoint's temperature RuntimeWarning
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            agent = laya.Agent(path, device=device)
        return LayaSystemOneClient(agent, source=repo)
    except Exception:
        return None
