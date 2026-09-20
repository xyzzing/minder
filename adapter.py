#!/usr/bin/env python3
"""adapter.py — Capability Probe (CAP, prd.md §2.1) + Mode Adapter (§6.6).

Pure functions over parsed requests/responses are separated from network I/O
(§7) so the probe logic is unit-testable without a server. Law #9: capabilities
are measured, never assumed — model names are recorded as fingerprint only.
"""
import json
import re
import time
import urllib.error
import urllib.request

MINDER_VERSION = "0.4"
PROBE_PROMPT = "Briefly: what is 7 times 6?"
PROBE_MAX_TOKENS = 256
TOOL_PROBE_PROMPT = "List the files in /tmp using the list_files tool."
TOOL_PROBE_MAX_TOKENS = 128
TOOL_PROBE_RUNS = 8
DEFAULT_MARKERS = ["<think>", "</think>"]
# Tool-call dialect bleed: the fusion-tune failure class where Hermes-style
# markup (<function=…>) mixes into JSON-style tool calls and the server's
# parser funnels the runaway into tool_calls[0].arguments (2026-09-19 incident).
TOOL_MARKUP = ("</tool_call>", "<tool_call>", "<function=", "</function>",
               "</parameter>")
HEAVY_TOOLS = ("bash", "shell", "exec", "write", "edit", "apply_patch",
               "fs_write", "multiedit", "str_replace", "notebook")
HEAVY_RESULT_CHARS = 2048
RECENT_TOOL_WINDOW = 8

PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List files in a directory",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}}},
    },
}]

# ---------------------------------------------------------------------------
# HTTP helpers (thin, injectable for tests via `post_json`/`get_json` args)
# ---------------------------------------------------------------------------

def http_json(method, url, payload=None, timeout=60):
    """Returns (status, parsed_json_or_raw_bytes). Raises on transport error."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
    try:
        return status, json.loads(body)
    except (ValueError, TypeError):
        return status, body


def chat(base_url, body, timeout=120):
    return http_json("POST", base_url.rstrip("/") + "/v1/chat/completions", body,
                     timeout=timeout)


def _msg_content(resp):
    """(content, reasoning_content) — reasoning may live in either field (A8)."""
    try:
        m = resp["choices"][0]["message"]
        return m.get("content") or "", m.get("reasoning_content") or ""
    except (KeyError, IndexError, TypeError):
        return "", ""


# ---------------------------------------------------------------------------
# CAP scoring — pure functions
# ---------------------------------------------------------------------------

def score_thinking(resp, markers=None):
    """(shows_thinking, total_chars, field) — reasoning may surface in content
    or reasoning_content (A8). Length is corroborating evidence only (§2.1 T1).
    field names where thinking was detected; "none" if nowhere."""
    markers = markers or DEFAULT_MARKERS
    content, reasoning = _msg_content(resp)
    chars = len(content) + len(reasoning)
    if reasoning.strip():
        return True, chars, "reasoning_content"
    low = content.lower()
    if any(m.lower() in low for m in markers):
        return True, chars, "content"
    return False, chars, "none"


def score_toolcall(resp):
    """(clean, detail) — T1c: tool-call dialect cleanliness with tools present.

    The fusion-tune failure class (2026-09-19): under some templates the model
    mixes Hermes-style markup into JSON tool calls; the server funnels the
    runaway into tool_calls[0].arguments, content stays empty and generation
    burns to max_tokens. clean iff no markup bleed, every tool-call parses to
    a JSON object, and the turn didn't end in an empty length-burn."""
    try:
        choice = resp["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason") or "?"
    except (KeyError, IndexError, TypeError):
        return False, "unparseable response"
    content = str(msg.get("content") or "")
    calls = msg.get("tool_calls") or []
    args_blob = "".join(str((c.get("function") or {}).get("arguments") or "")
                        for c in calls)
    markup = [m for m in TOOL_MARKUP
              if m in args_blob or m in content]
    if markup:
        return False, f"markup bleed {markup[:2]} (finish={finish})"
    for c in calls:
        raw = (c.get("function") or {}).get("arguments")
        if isinstance(raw, str):
            try:
                if not isinstance(json.loads(raw), dict):
                    return False, f"args not an object: {raw[:60]}"
            except ValueError:
                return False, f"args not JSON: {raw[:60]}"
    if finish == "length" and not content.strip():
        return False, "empty length-burn (no content, no usable call)"
    return True, f"finish={finish} calls={len(calls)} chars={len(content)}"


def decide_mechanism(kwargs_accepted, t1_on, t1_off, t2_plain, t2_no_think,
                     markers=None):
    """mechanism ∈ {kwargs, softswitch, none} per §2.1 T1–T3."""
    if kwargs_accepted and t1_on and not t1_off:
        return "kwargs"
    if t2_plain and not t2_no_think:
        return "softswitch"
    return "none"


def build_caps(fingerprint, kwargs_accepted, mechanism, budget_supported,
               evidence, softswitch_tokens, markers, field):
    return {
        "fingerprint": fingerprint,
        "kwargs_accepted": bool(kwargs_accepted),
        "thinking": {
            "mechanism": mechanism,
            "markers": list(markers),
            "field": field,
            "thinking_budget_supported": bool(budget_supported),
            "evidence": evidence,
        },
        "softswitch_tokens": dict(softswitch_tokens),
        "probed_at": time.time(),
        "minder_version": MINDER_VERSION,
    }


# ---------------------------------------------------------------------------
# CAP probe (network)
# ---------------------------------------------------------------------------

def run_cap(base_url, cfg=None, post=None, get=None):
    """Run the full CAP procedure. Returns (caps, error_transcript).

    error_transcript is None on success; on probe failure it carries the
    verbatim request/response transcript for the STOP report (§2.2).
    """
    cfg = cfg or {}
    post = post or (lambda body, t=120: chat(base_url, body, timeout=t))
    get = get or (lambda path, t=30: http_json("GET", base_url.rstrip("/") + path,
                                               timeout=t))
    markers = cfg.get("thinking_markers") or DEFAULT_MARKERS
    tokens = cfg.get("softswitch_tokens") or {"on": "/think", "off": "/no_think"}
    transcript = []

    def probe_body(extra=None, user=None):
        b = {"model": "default",
             "messages": [{"role": "user", "content": user or PROBE_PROMPT}],
             "max_tokens": PROBE_MAX_TOKENS, "temperature": 0}
        if extra:
            b.update(extra)
        return b

    # 1. Fingerprint (tolerate absent fields; never parse the name for decisions)
    model_id, props_path, template_source = None, None, "unknown"
    try:
        st, models = get("/v1/models")
        if st == 200 and isinstance(models, dict):
            ids = [m.get("id") for m in models.get("data", []) if m.get("id")]
            model_id = ids[0] if ids else None
    except Exception as e:
        transcript.append(f"GET /v1/models failed: {e!r}")
    try:
        st, props = get("/props")
        if st == 200 and isinstance(props, dict):
            props_path = (props.get("model_path")
                          or props.get("default_generation_settings", {}).get("model"))
            if props.get("chat_template"):
                template_source = "embedded"
    except Exception as e:
        transcript.append(f"GET /props failed: {e!r}")
    fingerprint = {"model_id": model_id, "props_path": props_path,
                   "template_source": template_source}

    # 2. T1 — kwargs differential. A 400 here means the server rejects the
    # kwarg outright (A9) ⇒ record kwargs_accepted:false and fall through to
    # T2 (AT-15a). Only transport-level failures are probe errors (STOP).
    kwargs_accepted = True
    try:
        st, _ = post(probe_body({"chat_template_kwargs": {"enable_thinking": False}}))
        if st == 400:
            kwargs_accepted = False
    except Exception as e:
        transcript.append(f"kwargs probe failed: {e!r}")
        return None, "\n".join(transcript)

    def run_probe(extra=None, user=None, tolerated_400=False):
        """Returns (shows_thinking, chars, field) or (None, None, None) on
        probe error. A 400 can be tolerated for kwargs probes only."""
        try:
            st, resp = post(probe_body(extra, user))
            if st == 400 and tolerated_400:
                return False, 0, "none"
            if st != 200 or not isinstance(resp, dict):
                transcript.append(f"probe status={st} body={resp!r}"[:2000])
                return None, None, None
            return score_thinking(resp, markers)
        except Exception as e:
            transcript.append(f"probe failed: {e!r}")
            return None, None, None

    t1_on, on_chars, t1_field = run_probe(
        {"chat_template_kwargs": {"enable_thinking": True}},
        tolerated_400=not kwargs_accepted)
    t1_off, off_chars, _ = run_probe(
        {"chat_template_kwargs": {"enable_thinking": False}},
        tolerated_400=not kwargs_accepted)

    if not kwargs_accepted:
        # The differential T1 can't exist on a rejecting server.
        t1_on = t1_off = False

    # 3. T2 — softswitch differential
    t2_plain, _, _ = run_probe()
    t2_off, _, _ = run_probe(user=PROBE_PROMPT + " " + tokens["off"])

    if None in (t1_on, t1_off, t2_plain, t2_off):
        # Probe itself errored (server unreachable mid-probe) ⇒ STOP with
        # transcripts verbatim (§2.2). Never guess from partial evidence.
        return None, "\n".join(transcript)

    mechanism = decide_mechanism(kwargs_accepted, t1_on, t1_off, t2_plain, t2_off,
                                 markers)

    # 4. thinking_budget support (Q3/A3 override: false only on HTTP 400)
    budget_supported = False
    if kwargs_accepted:
        try:
            st, _ = post(probe_body({"chat_template_kwargs": {
                "enable_thinking": True, "thinking_budget": 512}}))
            budget_supported = (st != 400)
        except Exception as e:
            transcript.append(f"budget probe failed: {e!r}")

    # 5. effort probe — measure which channel accepts reasoning_effort
    # (chat_template_kwargs member vs OpenAI-style field), per Law #9.
    # Acceptance (HTTP 200) is the gate — a template that raises on an
    # unknown effort 500s, which is rejection, not acceptance.
    effort_supported = False
    effort_channel = None
    effort_levels = []
    for channel, extra in (
            ("ctk", {"chat_template_kwargs": {"reasoning_effort": "low"}}),
            ("field", {"reasoning_effort": "low"})):
        try:
            st, _ = post(probe_body(extra))
            if st == 200:
                effort_supported = True
                effort_channel = channel
                break
        except Exception as e:
            transcript.append(f"effort probe ({channel}) failed: {e!r}")
    # 5b. measure the effort VOCABULARY on the accepted channel — templates
    # vary (Qwen3.8 embedded: xhigh/medium/low; DeepSeek wire: off..max);
    # never assume a name the server didn't accept (2026-09-20 incident:
    # "high" 500'd on a template that only knows xhigh).
    if effort_channel:
        for level in ("minimal", "low", "medium", "high", "xhigh"):
            extra = ({ "chat_template_kwargs": {"reasoning_effort": level}}
                     if effort_channel == "ctk"
                     else {"reasoning_effort": level})
            try:
                st, _ = post(probe_body(extra))
                if st == 200:
                    effort_levels.append(level)
            except Exception:
                pass

    # 6. T1c — tool-call cleanliness with tools in the request (exec path:
    # thinking off). Dialect drift is intermittent, so this is a sampled
    # k-of-n check, not a one-shot: any dirty sample marks the server dirty.
    tool_dirty = 0
    tool_detail = ""
    tool_runs = int(cfg.get("tool_probe_runs") or TOOL_PROBE_RUNS)
    t1c_extra = {"tools": PROBE_TOOL, "max_tokens": TOOL_PROBE_MAX_TOKENS}
    if kwargs_accepted:  # exec-path shape; a 400 on a rejecting server is
        t1c_extra["chat_template_kwargs"] = {"enable_thinking": False}  # not dirt
    for _ in range(tool_runs):
        try:
            st, resp = post(probe_body(t1c_extra, user=TOOL_PROBE_PROMPT))
            if st != 200 or not isinstance(resp, dict):
                tool_dirty += 1
                tool_detail = f"status={st}"
                continue
            clean, d = score_toolcall(resp)
            if not clean:
                tool_dirty += 1
                tool_detail = d
        except Exception as e:
            tool_dirty += 1
            tool_detail = repr(e)[:80]

    caps = build_caps(fingerprint, kwargs_accepted, mechanism, budget_supported,
                      {"probe_on_chars": int(on_chars or 0),
                       "probe_off_chars": int(off_chars or 0)},
                      tokens, markers, t1_field)
    caps["effort_supported"] = effort_supported
    caps["effort_channel"] = effort_channel
    caps["effort_levels"] = effort_levels
    caps["tool_calls"] = {"clean": tool_dirty == 0,
                          "dirty": tool_dirty,
                          "probes": tool_runs,
                          "detail": tool_detail or None}
    return caps, None


def write_caps(path, caps):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(caps, indent=2))


def load_caps(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Preflight HTTP probes (P-2, P-2b, P-3, P-4 of prd.md §2)
# ---------------------------------------------------------------------------

def preflight(base_url):
    """Returns dict with keys: alive, generation_ok, kwargs_accepted, n_ctx.
    Missing values are None — caller decides warn vs stop per the PRD table."""
    out = {"alive": False, "generation_ok": None, "kwargs_accepted": None,
           "n_ctx": None}
    try:
        st, _ = http_json("GET", base_url.rstrip("/") + "/health", timeout=10)
        out["alive"] = (st == 200)
    except Exception:
        return out
    try:
        st, resp = chat(base_url, {
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 4, "temperature": 0}, timeout=60)
        # A8/§2.1: with --reasoning-format, a tiny completion may spend all
        # tokens inside <think> and land in reasoning_content — check both
        # fields. (Deviations report: PRD P-2b said "non-empty content".)
        content, reasoning = _msg_content(resp) if isinstance(resp, dict) \
            else ("", "")
        out["generation_ok"] = (st == 200
                                and bool((content + reasoning).strip()))
    except Exception:
        out["generation_ok"] = False
    try:
        st, resp = chat(base_url, {
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "chat_template_kwargs": {"enable_thinking": False}}, timeout=60)
        out["kwargs_accepted"] = (st != 400)
    except Exception:
        out["kwargs_accepted"] = None
    try:
        st, props = http_json("GET", base_url.rstrip("/") + "/props", timeout=10)
        n_ctx = props.get("default_generation_settings", {}).get("n_ctx") \
            if isinstance(props, dict) else None
        out["n_ctx"] = int(n_ctx) if n_ctx else None
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Effort scheduling (absorbed from the thinking-levels concept) — pure
# functions over the parsed request. The proxy is the only writer of the
# thinking knob for `class: auto` traffic; one knob, one owner.
# ---------------------------------------------------------------------------

def classify_recent_activity(messages, window=RECENT_TOOL_WINDOW):
    """('none'|'light'|'heavy', evidence) from the recent message window.

    OpenAI format: assistant messages may carry `tool_calls` (name +
    JSON-string arguments); tool results are `role: "tool"` messages.
    """
    calls, results = [], []
    for m in (messages or [])[-window:]:
        role = m.get("role")
        if role == "assistant" and isinstance(m.get("tool_calls"), list):
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                name = str(fn.get("name", "")).lower()
                arg_size = len(str(fn.get("arguments", "")))
                calls.append((name, arg_size))
        elif role == "tool":
            results.append(len(str(m.get("content", ""))))
    if not calls and not results:
        return "none", {"calls": 0, "results": 0}
    heavy_call = any(any(h in name for h in HEAVY_TOOLS) for name, _ in calls)
    heavy_result = any(r >= HEAVY_RESULT_CHARS for r in results)
    if heavy_call or heavy_result:
        kind = "heavy"
    else:
        kind = "light"
    return kind, {"calls": len(calls), "results": len(results),
                  "max_result_chars": max(results, default=0)}


def schedule_effort(messages, cfg, caps=None):
    """Decide the effort level for a non-escalated `class: auto` request.

    Returns "off" | "low" | "high". Effort-field support (caps) does NOT
    gate the decision — apply_auto attaches the field only when supported
    and otherwise degrades to the binary enable_thinking at the scheduled
    level (heavy activity still thinks; simple turns still skip it).
    """
    mode = (cfg or {}).get("effort_mode", "off")
    if mode == "off":
        return "off"
    if mode == "fixed":
        level = (cfg or {}).get("effort_fixed_level", "low")
        return level if level in ("off", "low", "high") else "low"
    kind, _ = classify_recent_activity(messages)
    if kind == "none":
        return "off"    # no tool activity: a plain question, skip thinking
    if kind == "light":
        return "low"
    return "high"


_EFFORT_PREFS = {
    "high": ("high", "xhigh", "medium", "low", "minimal"),
    "low": ("low", "medium", "minimal", "xhigh", "high"),
    # client/UI pass-through names: identity first, then graceful neighbors
    "xhigh": ("xhigh", "high", "medium", "low"),
    "medium": ("medium", "low", "xhigh", "high"),
    "max": ("max", "xhigh", "high"),
    "minimal": ("minimal", "low", "medium"),
    "off": ("off", "minimal", "low"),
}


def pick_effort_level(want, accepted):
    """Semantic effort ('low'|'high') → a name the measured server accepts.

    The vocabulary is server-specific (Qwen3.8 embedded: xhigh/medium/low;
    DeepSeek wire: off..max) — CAP records what actually returned 200 and
    this maps onto it. None when nothing was accepted."""
    accepted = accepted or []
    for name in _EFFORT_PREFS.get(want, ()):
        if name in accepted:
            return name
    return accepted[0] if accepted else None


def apply_auto(req, effort, caps, preset=None):
    """Mode Adapter for `class: auto`: translate a scheduled effort level
    into the measured mechanism. Returns (req, degraded_flag).
    - effort "high"/"low" → thinking on + reasoning_effort at the measured
      channel (chat_template_kwargs member, or the OpenAI-style field),
      mapped onto the measured effort vocabulary.
    - effort "off"/None-with-want-false → thinking off (binary fallback)."""
    mechanism = (caps or {}).get("thinking", {}).get("mechanism", "none")
    override = (caps or {}).get("mechanism_override")
    if override in ("kwargs", "softswitch", "none"):
        mechanism = override
    degraded = False
    want = effort not in (None, "off", "minimal")
    if mechanism == "kwargs":
        ctk = dict(req.get("chat_template_kwargs") or {})
        ctk["enable_thinking"] = want
        if effort and (caps or {}).get("effort_supported"):
            level = pick_effort_level(effort, (caps or {}).get("effort_levels"))
            if level:
                if (caps or {}).get("effort_channel") == "ctk":
                    ctk["reasoning_effort"] = level
                elif (caps or {}).get("effort_channel") == "field":
                    req["reasoning_effort"] = level
            if effort in ("off", "minimal"):
                ctk.pop("reasoning_effort", None)
                req.pop("reasoning_effort", None)
        req["chat_template_kwargs"] = ctk
    elif mechanism == "softswitch":
        tokens = (caps or {}).get("softswitch_tokens") or {"on": "/think",
                                                           "off": "/no_think"}
        msgs = req.get("messages")
        if msgs:
            for m in reversed(msgs):
                if m.get("role") == "user":
                    base = _SOFTSWITCH_TRAILING.sub(
                        "", str(m.get("content", ""))).rstrip()
                    token = tokens["on"] if want else tokens["off"]
                    m["content"] = (base + " " + token) if base else token
                    break
    else:
        if want:
            degraded = True
    return req, degraded




# ---------------------------------------------------------------------------
# Mode Adapter (§6.6) — pure function over the parsed request
# ---------------------------------------------------------------------------

_SOFTSWITCH_TRAILING = re.compile(r"\s*/(?:no_)?think\s*$")


def apply_mode(req, want_thinking, caps, preset=None):
    """Translate intent → request mutation. Returns (req, degraded_flag).

    Runs after preset param merge, before digest append. Never touches
    non-final messages. mechanism_override (§5.7) forces the mechanism.
    """
    mechanism = (caps or {}).get("thinking", {}).get("mechanism", "none")
    override = (caps or {}).get("mechanism_override")
    if override in ("kwargs", "softswitch", "none"):
        mechanism = override
    degraded = False
    if mechanism == "kwargs":
        ctk = dict(req.get("chat_template_kwargs") or {})
        ctk["enable_thinking"] = bool(want_thinking)
        if want_thinking:
            budget = ((preset or {}).get("upstream_params", {})
                      .get("chat_template_kwargs") or {}).get("thinking_budget")
            supported = (caps or {}).get("thinking", {}).get(
                "thinking_budget_supported", False)
            if budget and supported:
                ctk["thinking_budget"] = budget
        req["chat_template_kwargs"] = ctk
    elif mechanism == "softswitch":
        tokens = (caps or {}).get("softswitch_tokens") or {"on": "/think",
                                                           "off": "/no_think"}
        msgs = req.get("messages")
        if msgs:
            for m in reversed(msgs):
                if m.get("role") == "user":
                    base = _SOFTSWITCH_TRAILING.sub(
                        "", str(m.get("content", ""))).rstrip()
                    token = tokens["on"] if want_thinking else tokens["off"]
                    m["content"] = (base + " " + token) if base else token
                    break
    else:  # "none" — no mutation; L1 becomes digest-only deliberation
        if want_thinking:
            degraded = True
    return req, degraded
