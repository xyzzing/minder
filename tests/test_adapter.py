"""CAP probe + Mode Adapter tests (AT-15, AT-7a/b/c mock-level, §6.6 table)."""
import pytest

import adapter
from mock_upstream import MockUpstream


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def resp(content="", reasoning=None):
    m = {"role": "assistant", "content": content}
    if reasoning is not None:
        m["reasoning_content"] = reasoning
    return {"choices": [{"message": m}]}


def test_score_thinking_fields():
    assert adapter.score_thinking(resp("<think>hmm</think>42")) == (
        True, len("<think>hmm</think>42"), "content")
    assert adapter.score_thinking(resp("42", reasoning="worked it out")) == (
        True, len("42" + "worked it out"), "reasoning_content")
    assert adapter.score_thinking(resp("42")) == (False, 2, "none")
    assert adapter.score_thinking(resp(""))[0] is False


def test_decide_mechanism_table():
    assert adapter.decide_mechanism(True, True, False, False, False) == "kwargs"
    assert adapter.decide_mechanism(True, True, True, True, False) == "softswitch"
    assert adapter.decide_mechanism(False, False, False, True, False) == \
        "softswitch"
    assert adapter.decide_mechanism(True, False, False, False, False) == "none"
    assert adapter.decide_mechanism(False, False, False, False, False) == "none"


def test_apply_mode_kwargs_table():
    caps = {"thinking": {"mechanism": "kwargs",
                         "thinking_budget_supported": True}}
    preset = {"upstream_params": {"chat_template_kwargs":
                                  {"enable_thinking": True,
                                   "thinking_budget": 16384}}}
    req = {"messages": [], "temperature": 0.6}
    out, degraded = adapter.apply_mode(dict(req), True, caps, preset)
    assert out["chat_template_kwargs"] == {"enable_thinking": True,
                                           "thinking_budget": 16384}
    assert degraded is False
    out, degraded = adapter.apply_mode(dict(req), False, caps, preset)
    assert out["chat_template_kwargs"] == {"enable_thinking": False}


def test_apply_mode_budget_omitted_when_unsupported():
    caps = {"thinking": {"mechanism": "kwargs",
                         "thinking_budget_supported": False}}
    preset = {"upstream_params": {"chat_template_kwargs":
                                  {"enable_thinking": True,
                                   "thinking_budget": 16384}}}
    out, _ = adapter.apply_mode({"messages": []}, True, caps, preset)
    assert "thinking_budget" not in out["chat_template_kwargs"]


def test_apply_mode_softswitch_strips_then_appends():
    caps = {"thinking": {"mechanism": "softswitch",
                         "softswitch_tokens": {"on": "/think",
                                               "off": "/no_think"}}}
    msgs = [{"role": "user", "content": "earlier /think"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "fix the parser /think"}]
    out, _ = adapter.apply_mode({"messages": [dict(m) for m in msgs]},
                                False, caps)
    assert out["messages"][2]["content"] == "fix the parser /no_think"
    out, _ = adapter.apply_mode({"messages": [dict(m) for m in msgs]},
                                True, caps)
    assert out["messages"][2]["content"] == "fix the parser /think"
    # non-final messages are never touched (§6.6)
    assert out["messages"][0]["content"] == "earlier /think"


def test_apply_mode_none_degrades_only_when_wanted():
    caps = {"thinking": {"mechanism": "none"}}
    req = {"messages": [{"role": "user", "content": "hi /no_think"}]}
    out, degraded = adapter.apply_mode(dict(req), True, caps)
    assert degraded is True
    assert out["messages"][0]["content"] == "hi /no_think"  # untouched
    out, degraded = adapter.apply_mode(dict(req), False, caps)
    assert degraded is False


def test_apply_mode_override_forces():
    caps = {"thinking": {"mechanism": "kwargs"},
            "mechanism_override": "softswitch",
            "softswitch_tokens": {"on": "/think", "off": "/no_think"}}
    out, degraded = adapter.apply_mode(
        {"messages": [{"role": "user", "content": "hi"}]}, False, caps)
    assert degraded is False
    assert out["messages"][0]["content"].endswith("/no_think")


# ---------------------------------------------------------------------------
# CAP probe against scripted mocks (AT-15)
# ---------------------------------------------------------------------------

def test_at15a_reject_kwargs_falls_through_to_softswitch():
    """(a) 400 on chat_template_kwargs ⇒ kwargs_accepted:false, T2 decides."""
    with MockUpstream("reject_kwargs") as up:
        caps, err = adapter.run_cap(up.url)
    assert err is None
    assert caps["kwargs_accepted"] is False
    assert caps["thinking"]["mechanism"] in ("softswitch", "none")


def test_at15b_reasoning_content_detected():
    """(b) reasoning only in reasoning_content ⇒ still detected as kwargs."""
    with MockUpstream("reasoning_content") as up:
        caps, err = adapter.run_cap(up.url)
    assert err is None
    assert caps["thinking"]["mechanism"] == "kwargs"
    assert caps["thinking"]["field"] == "reasoning_content"


def test_at15c_dead_server_clean_stop_with_transcript():
    """(c) probe on dead server ⇒ (None, transcript), never a guess."""
    caps, err = adapter.run_cap("http://127.0.0.1:1")
    assert caps is None
    assert err and "failed" in err


def test_cap_softswitch_mock():
    with MockUpstream("softswitch_only") as up:
        caps, err = adapter.run_cap(up.url)
    assert err is None
    assert caps["thinking"]["mechanism"] == "softswitch"
    assert caps["kwargs_accepted"] is True


# ---------------------------------------------------------------------------
# Effort scheduling (absorbed thinking-levels concept)
# ---------------------------------------------------------------------------

def msg_tool_call(name, args="{}"):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def msg_tool_result(content):
    return {"role": "tool", "content": content}


CAPS_EFFORT = {"effort_supported": True, "effort_channel": "ctk",
               "effort_levels": ["low", "medium", "high"],
               "thinking": {"mechanism": "kwargs"}}
CFG_AUTO = {"effort_mode": "auto"}
CFG_OFF = {"effort_mode": "off"}
CFG_FIXED = {"effort_mode": "fixed", "effort_fixed_level": "high"}


def test_classify_recent_activity():
    assert adapter.classify_recent_activity(None)[0] == "none"
    assert adapter.classify_recent_activity([{"role": "user",
                                              "content": "hi"}])[0] == "none"
    light = [msg_tool_call("grep_search", '{"q": "x"}'),
             msg_tool_result("matched 2 lines")]
    assert adapter.classify_recent_activity(light)[0] == "light"
    heavy = [msg_tool_call("bash", '{"command": "make -j"}'),
             msg_tool_result("x" * 4096)]
    assert adapter.classify_recent_activity(heavy)[0] == "heavy"
    big_result = [msg_tool_call("read_file"), msg_tool_result("y" * 9999)]
    assert adapter.classify_recent_activity(big_result)[0] == "heavy"


def test_schedule_effort_decision_table():
    plain = [{"role": "user", "content": "what is 2+2?"}]
    light = [msg_tool_call("read_file"), msg_tool_result("short"),
             {"role": "user", "content": "now fix it"}]
    heavy = [msg_tool_call("bash", '{"command": "pytest -q"}'),
             msg_tool_result("x" * 5000)]
    # decision ignores effort-field support — apply_auto attaches fields
    # only when the measured channel exists (binary fallback otherwise)
    assert adapter.schedule_effort(heavy, CFG_AUTO, {}) == "high"
    assert adapter.schedule_effort(heavy, CFG_AUTO,
                                   {"effort_supported": False}) == "high"
    # mode off → binary off
    assert adapter.schedule_effort(heavy, CFG_OFF, CAPS_EFFORT) == "off"
    # fixed mode
    assert adapter.schedule_effort(plain, CFG_FIXED, CAPS_EFFORT) == "high"
    # auto table
    assert adapter.schedule_effort(plain, CFG_AUTO, CAPS_EFFORT) == "off"
    assert adapter.schedule_effort(light, CFG_AUTO, CAPS_EFFORT) == "low"
    assert adapter.schedule_effort(heavy, CFG_AUTO, CAPS_EFFORT) == "high"


def test_apply_auto_channels():
    req = {"messages": [{"role": "user", "content": "hi"}]}
    out, degraded = adapter.apply_auto(dict(req), "high", CAPS_EFFORT)
    assert out["chat_template_kwargs"] == {"enable_thinking": True,
                                           "reasoning_effort": "high"}
    assert degraded is False
    caps_field = dict(CAPS_EFFORT, effort_channel="field")
    out, _ = adapter.apply_auto(dict(req), "low", caps_field)
    assert out["reasoning_effort"] == "low"
    assert out["chat_template_kwargs"] == {"enable_thinking": True}
    out, _ = adapter.apply_auto(dict(req), "off", CAPS_EFFORT)
    assert out["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in out["chat_template_kwargs"]
    caps_none = {"effort_supported": True, "effort_channel": "ctk",
                 "thinking": {"mechanism": "none"}}
    out, degraded = adapter.apply_auto(dict(req), "high", caps_none)
    assert degraded is True and "chat_template_kwargs" not in out
    caps_soft = {"effort_supported": True, "effort_channel": "ctk",
                 "thinking": {"mechanism": "softswitch",
                              "softswitch_tokens": {"on": "/think",
                                                    "off": "/no_think"}}}
    out, _ = adapter.apply_auto(dict(req), "off", caps_soft)
    assert out["messages"][0]["content"].endswith("/no_think")


def test_cap_effort_probe_supported():
    with MockUpstream("default") as up:
        caps, err = adapter.run_cap(up.url)
    assert err is None
    assert caps["effort_supported"] is True
    assert caps["effort_channel"] == "ctk"


def test_cap_effort_probe_unsupported():
    with MockUpstream("reject_effort") as up:
        caps, err = adapter.run_cap(up.url)
    assert err is None
    assert caps["effort_supported"] is False
    assert caps["effort_channel"] is None
    assert caps["thinking"]["mechanism"] == "kwargs"  # mechanism unaffected


def test_preflight_against_mock():
    with MockUpstream("default") as up:
        pf = adapter.preflight(up.url)
    assert pf["alive"] is True
    assert pf["generation_ok"] is True
    assert pf["kwargs_accepted"] is True


def test_preflight_dead_server():
    pf = adapter.preflight("http://127.0.0.1:1")
    assert pf == {"alive": False, "generation_ok": None,
                  "kwargs_accepted": None, "n_ctx": None}


# ---------------------------------------------------------------------------
# T1c — tool-call dialect cleanliness (2026-09-19 incident class)
# ---------------------------------------------------------------------------

def test_score_toolcall_verdicts():
    clean = {"choices": [{"finish_reason": "tool_calls", "message": {
        "content": "", "tool_calls": [
            {"function": {"name": "bash",
                          "arguments": "{\"command\": \"ls\"}"}}]}}]}
    assert adapter.score_toolcall(clean) == (True, "finish=tool_calls calls=1 chars=0")
    markup = {"choices": [{"finish_reason": "length", "message": {
        "content": "", "tool_calls": [
            {"function": {"name": "bash",
                          "arguments": "{\"a\": 1}</tool_call><tool_call>"}}]}}]}
    clean_flag, detail = adapter.score_toolcall(markup)
    assert clean_flag is False and "markup bleed" in detail
    burn = {"choices": [{"finish_reason": "length",
                         "message": {"content": ""}}]}
    clean_flag, detail = adapter.score_toolcall(burn)
    assert clean_flag is False and "burn" in detail
    bad_json = {"choices": [{"finish_reason": "tool_calls", "message": {
        "content": "", "tool_calls": [
            {"function": {"name": "bash", "arguments": "not json"}}]}}]}
    clean_flag, detail = adapter.score_toolcall(bad_json)
    assert clean_flag is False and "not JSON" in detail


def test_cap_t1c_clean_on_default_mock():
    with MockUpstream("default") as up:
        caps, err = adapter.run_cap(up.url, cfg={"tool_probe_runs": 2})
    assert err is None
    assert caps["tool_calls"]["clean"] is True
    assert caps["tool_calls"]["probes"] == 2
    assert caps["tool_calls"]["dirty"] == 0


def test_cap_t1c_dirty_flagged():
    with MockUpstream("default") as up:
        def post(body, t=120):
            if body.get("tools"):
                return 200, {"choices": [{"finish_reason": "length",
                                          "message": {"content": "",
                                                      "tool_calls": [
                    {"function": {"name": "bash",
                                  "arguments": "{\"a\":1}</tool_call>"}}]}}]}
            return adapter.chat(up.url, body, timeout=t)
        caps, err = adapter.run_cap(up.url, cfg={"tool_probe_runs": 3},
                                    post=post)
    assert err is None
    assert caps["tool_calls"]["clean"] is False
    assert caps["tool_calls"]["dirty"] == 3


def test_pick_effort_level_maps_vocabulary():
    # Qwen3.8 embedded vocabulary: semantic high → xhigh
    assert adapter.pick_effort_level("high", ["low", "medium", "xhigh"]) == "xhigh"
    assert adapter.pick_effort_level("low", ["low", "medium", "xhigh"]) == "low"
    # full vocabularies pass through
    assert adapter.pick_effort_level("high", ["off", "low", "high", "max"]) == "high"
    # nothing measured → None; weird vocabulary → closest available
    assert adapter.pick_effort_level("high", []) is None
    assert adapter.pick_effort_level("high", ["medium"]) == "medium"


def test_apply_auto_maps_effort_onto_measured_vocabulary():
    # 2026-09-20 incident regression: "high" 500'd on a template that only
    # accepts xhigh/medium/low — the adapter must never emit an unmeasured name
    caps = dict(CAPS_EFFORT, effort_levels=["low", "medium", "xhigh"])
    out, _ = adapter.apply_auto({"messages": []}, "high", caps)
    assert out["chat_template_kwargs"]["reasoning_effort"] == "xhigh"
    # unmeasured vocabulary (stale caps) → no effort attached, binary only
    out, _ = adapter.apply_auto({"messages": []}, "high",
                                dict(CAPS_EFFORT, effort_levels=[]))
    assert "reasoning_effort" not in out["chat_template_kwargs"]
    assert out["chat_template_kwargs"]["enable_thinking"] is True


def test_cap_measures_effort_vocabulary():
    """run_cap records which effort names the server actually accepts (200)."""
    with MockUpstream("default") as up:
        real = adapter.chat

        def post(body, t=120):
            lvl = (body.get("chat_template_kwargs") or {}).get("reasoning_effort")
            if lvl and lvl not in ("low", "medium", "xhigh"):
                return 500, {"error": {"message":
                                       f"Unexpected reasoning effort {lvl}"}}
            return real(up.url, body, timeout=t)
        caps, err = adapter.run_cap(up.url, cfg={"tool_probe_runs": 1},
                                    post=post)
    assert err is None
    assert caps["effort_levels"] == ["low", "medium", "xhigh"]
    assert caps["effort_supported"] is True and caps["effort_channel"] == "ctk"


# ---------------------------------------------------------------------------
# v0.5 — client/UI effort pass-through over the measured vocabulary
# ---------------------------------------------------------------------------

def test_apply_auto_client_effort_vocabulary():
    caps = {"thinking": {"mechanism": "kwargs"}, "effort_supported": True,
            "effort_channel": "ctk",
            "effort_levels": ["low", "medium", "xhigh"]}
    out, degraded = adapter.apply_auto({}, "medium", caps)
    assert degraded is False
    assert out["chat_template_kwargs"] == {"enable_thinking": True,
                                           "reasoning_effort": "medium"}
    out, _ = adapter.apply_auto({}, "xhigh", caps)
    assert out["chat_template_kwargs"]["reasoning_effort"] == "xhigh"
    # measured-vocabulary member the scheduler never emits still maps
    out, _ = adapter.apply_auto({}, "max", {"thinking": {"mechanism":
                                                        "kwargs"},
                                            "effort_supported": True,
                                            "effort_channel": "field",
                                            "effort_levels": ["max"]})
    assert out["reasoning_effort"] == "max"
    # off/minimal keep thinking off and scrub any stale level
    out, _ = adapter.apply_auto({"reasoning_effort": "low"}, "minimal", caps)
    assert out["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in out
    out, _ = adapter.apply_auto({}, "off", caps)
    assert out["chat_template_kwargs"] == {"enable_thinking": False}
