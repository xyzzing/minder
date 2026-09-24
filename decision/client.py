"""SystemOneClient surface + provider selection (Phase 5.5 slice C).

system_one(state, questions, model=None) -> DecisionResponse. Providers:
- NullClient (default; uniform + confidence 0);
- FakeClient (fixture-driven; tests only);
- optional adapters (decision/providers/adapter.py, laya.py) — never
  required, never on the default test path.

MINDER_DECISION: unset/off -> None (no decision loop at all);
"shadow" -> NullClient (log-only glue, Phase 5.5-G); "fake" -> FakeClient
(dev/tests only — it has no opinions of its own); "laya" -> the real
laya 0.3.6 model (shadow-only; the gate still decides).
"""
import os

from .providers.fake import FakeClient
from .providers.null import NullClient


class SystemOneClient:
    """Protocol: assess one compact state against typed questions and
    return a DecisionResponse. Implementations must never raise for
    ordinary inputs and must fill every distribution the contract asks
    for (DecisionResponse.validate is the arbiter)."""

    model_version = "abstract"

    def system_one(self, state, questions, model=None):
        raise NotImplementedError


def get_decision_client():
    """MINDER_DECISION-driven provider selection. Default (unset): None —
    the whole loop is off and behaviour is byte-identical to Phase 5.

    "laya" returns the real laya 0.3.6 model when it is importable and
    cached locally; otherwise it degrades to NullClient so the shadow
    loop still runs (uniform + confidence 0) rather than silently
    disabling itself."""
    try:
        mode = (os.environ.get("MINDER_DECISION") or "").strip().lower()
        if mode == "shadow":
            return NullClient()
        if mode == "fake":
            return FakeClient()
        if mode == "laya":
            from .providers.laya import try_laya_client
            try:
                timeout_ms = int(os.environ.get("MINDER_LAYA_TIMEOUT_MS",
                                                "500"))
            except ValueError:
                timeout_ms = 500
            client = try_laya_client(timeout_ms=timeout_ms)
            return client if client is not None else NullClient()
        return None
    except Exception:
        return None
