"""Inference intent / grant consumption (docs/minder-phase-4-7-frontier-
coding.md P6.3).

Minder states what it wants (intent); the sinter side answers with a grant
(src/sinter/contracts.py evaluate_intent). Minder never imports sinter —
the shapes here are plain dicts mirroring the contract, so the repos stay
loosely coupled.

Laws:
- adapter_preference is RECORDED on the intent and NEVER acted on: it is
  dropped by apply_grant, which returns the base route regardless;
- a grant reporting adapter_loaded truthy is a contract violation from our
  side of the wall: keep the base route and log ignored_adapter;
- missing snapshot fields default safe (health "unknown", free_vram_mb
  None) — never guess hardware into a request.

No GPU code lives here; nothing in this module talks to llama-server.
"""
import minder

BASE_ROUTE = "base"


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_inference_intent(policy_level, reason, desired_route,
                           adapter_preference=None):
    """Minder's ask. adapter_preference may be recorded for telemetry;
    apply_grant will never route by it."""
    return {
        "policy_level": policy_level,
        "reason": str(reason or ""),
        "desired_route": str(desired_route or BASE_ROUTE),
        "adapter_preference": adapter_preference,
        "task_type": "execution",
        "thinking_mode": "lean",
    }


def normalise_snapshot(snapshot):
    """Contract snapshot -> plain dict with safe defaults for missing
    sensor fields. Accepts dicts or objects; never raises."""
    out = {
        "health": "unknown",
        "free_vram_mb": None,
        "vram_available_mb": None,
        "gpu_name": None,
        "model_id": None,
        "adapters_loaded": (),
        "pacing_active": False,
    }
    try:
        for key in out:
            value = _field(snapshot, key)
            if value is not None and not (key == "adapters_loaded"
                                          and not value):
                out[key] = value
        adapters = out["adapters_loaded"]
        if isinstance(adapters, (list, tuple)):
            out["adapters_loaded"] = tuple(adapters)
        elif adapters:
            out["adapters_loaded"] = (str(adapters),)
        else:
            out["adapters_loaded"] = ()
    except Exception:
        pass
    return out


def apply_grant(intent, grant):
    """Returns the route to use (a string). The intent's adapter_preference
    is dropped unconditionally; adapter paths are not executable in minder.
    Ungranted or violated contracts keep the base route."""
    base = str(_field(intent, "desired_route") or BASE_ROUTE)
    try:
        preference = _field(intent, "adapter_preference")
        loaded = bool(_field(grant, "adapter_loaded"))
        if preference or loaded or _field(grant, "adapter_requested"):
            try:
                minder.log("runtime-intent", "ignored_adapter",
                           adapter=str(preference or
                                       _field(grant, "adapter_requested")),
                           loaded=loaded, route=base)
            except Exception:
                pass
        if _field(grant, "granted", True) is False:
            return base
        return base
    except Exception:
        return base
