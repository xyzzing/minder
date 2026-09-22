"""Runtime intent/grant tests (docs/minder-phase-4-7-frontier-coding.md
P6.3). adapter_preference is recorded then dropped; snapshot defaults are
safe; minder never imports sinter."""
import pathlib

from memory import runtime_intent as ri


def test_adapter_preference_recorded_then_dropped():
    intent = ri.build_inference_intent(2, "repeated failure",
                                       "qwen27b-base",
                                       adapter_preference="fix-keyerror-lora")
    assert intent["adapter_preference"] == "fix-keyerror-lora"  # recorded
    assert intent["desired_route"] == "qwen27b-base"

    grant = {"granted": True, "adapter_requested": "fix-keyerror-lora",
             "adapter_loaded": None}
    assert ri.apply_grant(intent, grant) == "qwen27b-base"  # base route

    # even a lying grant that claims an adapter was loaded cannot take effect
    assert ri.apply_grant(intent, {"granted": True,
                                   "adapter_loaded": True}) \
        == "qwen27b-base"


def test_adapter_violation_is_logged(tmp_path):
    import minder
    ledger = minder.STATE_DIR / "events.jsonl"
    before = ledger.read_text() if ledger.exists() else ""
    intent = ri.build_inference_intent(2, "why", "base",
                                       adapter_preference="sneaky-lora")
    ri.apply_grant(intent, {"granted": True, "adapter_loaded": True})
    after = ledger.read_text() if ledger.exists() else ""
    assert "ignored_adapter" in after and after.count("ignored_adapter") > \
        before.count("ignored_adapter")


def test_ungranted_intent_keeps_base_route():
    intent = ri.build_inference_intent(1, "warmup", "lean-route")
    assert ri.apply_grant(intent, {"granted": False, "reason": "hot"}) \
        == "lean-route"


def test_snapshot_missing_fields_default_safe():
    snap = ri.normalise_snapshot({})
    assert snap["health"] == "unknown"
    assert snap["free_vram_mb"] is None
    assert snap["adapters_loaded"] == ()

    class Obj:
        health = "nominal"
        free_vram_mb = 4096

    snap = ri.normalise_snapshot(Obj())
    assert snap["health"] == "nominal"
    assert snap["free_vram_mb"] == 4096
    assert snap["gpu_name"] is None  # missing fields still safe


def test_no_sinter_import():
    """minder consumes the contract as plain dicts — no package import."""
    source = pathlib.Path(ri.__file__).read_text()
    assert "import sinter" not in source
    assert "from sinter" not in source
