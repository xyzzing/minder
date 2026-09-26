"""Optional adapter surface for real System One providers (Phase 5.5).

Import-guarded and NEVER on the default test path: no Jev/Laya key, no
GPU, no network in CI. system_one_adapter wraps an arbitrary object that
exposes a classify/predict-style call and maps its output onto a
DecisionResponse the way classifier_laya maps onto a Classification.
"""
from .fake import FakeClient  # re-exported for convenience  # noqa: F401


def system_one_adapter(model):
    """SystemOneClient | None. Returns None when the object exposes no
    recognisable surface."""
    try:
        from .laya import probe_model
        wrapped = probe_model(model)
        return ModelBackedClient(wrapped) if wrapped is not None else None
    except Exception:
        return None


class ModelBackedClient:
    """Maps model(event_dict) -> {'failure_kind': ..., 'next_step': ...,
    'confidence': ...} onto a DecisionResponse via the FakeClient
    spec format (same coercion, no fixtures)."""

    model_version = "adapter"

    def __init__(self, model):
        self._model = model

    def system_one(self, state, questions, model=None, contract=None):
        try:
            raw = self._model(state)
            spec = dict(raw) if isinstance(raw, dict) else {}
        except Exception:
            spec = {}
        key = f"adapter:{id(self)}"
        return FakeClient({key: spec}).system_one(
            key, questions, model=model, contract=contract)
