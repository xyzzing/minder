#!/usr/bin/env python3
"""minder-proxy (Turnstile) — alias mapping + escalation-aware param merge in
front of llama-server. Stdlib only.

Pipeline per prd.md §6.8: alias resolution → preset-params merge (flat/sampler
only — mechanism fields come exclusively from the Mode Adapter) → mechanism
translation → upstream_model rewrite → byte-exact relay. Fail-open: any
internal fault forwards the request unmodified (Law #2). Digest-marker
escalation: a minder L1/L2 digest in the last 4 messages upgrades the request
to think params for that request (stateless escalation channel).
"""
import glob
import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter
import minder

UPSTREAM = os.environ.get("MINDER_UPSTREAM", "http://127.0.0.1:8080")
PORT = int(os.environ.get("MINDER_PORT", "8390"))
# Opt-in request capture (diagnostics): every parsed /chat/completions body is
# written raw to MINDER_DUMP_DIR before forwarding. Off unless set.
DUMP_DIR = os.environ.get("MINDER_DUMP_DIR")
CONFIG_DIR = pathlib.Path(os.environ.get(
    "MINDER_CONFIG_DIR", os.path.expanduser("~/.config/minder")))
SHARE_DIR = pathlib.Path(os.environ.get(
    "MINDER_SHARE_DIR", os.path.dirname(os.path.abspath(__file__))))
SCAN_GATE_BYTES = 4 * 1024 * 1024
ESC_MARKER = re.compile(r"\[minder\] ESCALATION L([12])\b")
RECENT_WINDOW = 4
# Level-aware escalation (class: auto): a digest marker carries the level.
# L1 = Standard band, L2 = Deep band (the effort table). Effort names are
# semantic — pick_effort_level translates them onto the measured vocabulary.
LEVEL_EFFORT = {1: "high", 2: "xhigh"}
LEVEL_BUDGET = {1: 2048, 2: 10240}
# PRD v2 §4.2 consequence modes: DSH signals per-request via X-Minder-Mode.
# Modes drive ONLY thinking depth + budget; samplers stay preset-driven
# (cache-stable prefixes matter more than per-mode temperature).
MODE_EFFORT = {"direct": "off", "lean": "low", "deep": "high"}
MODE_BUDGET = {"lean": 1024, "deep": 4096}
# Semantic effort names the qwen-auto channel may honor from a client/UI
# request; values outside this set (or the CAP-measured vocabulary) are
# ignored and the activity scheduler decides.
_KNOWN_EFFORTS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
SINTER_STATE = os.environ.get(
    "MINDER_SINTER_STATE",
    os.path.expanduser("~/.local/state/sinter/instance.json"))
HOP_HEADERS = {"transfer-encoding", "connection", "keep-alive",
               "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "upgrade"}


def load_presets():
    """Config-dir presets first, share-dir statics as fallback (§4)."""
    presets = {}
    for d in (CONFIG_DIR / "presets", SHARE_DIR / "presets"):
        for p in sorted(glob.glob(str(d / "*.json"))):
            try:
                data = json.loads(pathlib.Path(p).read_text())
                presets[data["alias"]] = data
            except (OSError, ValueError, KeyError):
                continue
    return presets


PRESETS = load_presets()
_caps_cache = {"mtime": None, "caps": None}


def get_caps():
    try:
        m = (CONFIG_DIR / "model_caps.json").stat().st_mtime
    except OSError:
        return None
    if _caps_cache["mtime"] != m:
        try:
            _caps_cache["caps"] = json.loads(
                (CONFIG_DIR / "model_caps.json").read_text())
        except (OSError, ValueError):
            _caps_cache["caps"] = None
        _caps_cache["mtime"] = m
    return _caps_cache["caps"]


def flat_params(preset):
    """Preset params that are always safe — mechanism fields excluded (Law #3:
    a preset never carries a mechanism the probe didn't confirm; the adapter
    owns `chat_template_kwargs`)."""
    params = dict(preset.get("upstream_params") or {})
    params.pop("chat_template_kwargs", None)
    for k, v in (preset.get("sampler_overrides") or {}).items():
        if v is not None:
            params[k] = v
    return params


def marker_level(req):
    """Escalation level (1 or 2) of the strongest digest in the recent
    message window, or None. L3 digests intentionally never match."""
    best = None
    for m in (req.get("messages") or [])[-RECENT_WINDOW:]:
        hit = ESC_MARKER.search(str(m.get("content", "")))
        if hit:
            lvl = int(hit.group(1))
            if best is None or lvl > best:
                best = lvl
    return best


def marker_in_window(req):
    """True iff a minder escalation digest sits in the recent message window."""
    return marker_level(req) is not None


_pacing_cache = {"mtime": None, "active": False}


def sinter_pacing():
    """Thermal/VRAM backpressure from sinter's instance.json (PRD v2 §4.2),
    mtime-cached (sinter refreshes at 1 Hz). No file / no sinter = False.
    The response is mode DOWNGRADING only — never a relay sleep."""
    try:
        m = pathlib.Path(SINTER_STATE).stat().st_mtime
    except OSError:
        return False
    if _pacing_cache["mtime"] != m:
        active = False
        try:
            tel = (json.loads(pathlib.Path(SINTER_STATE).read_text())
                   .get("telemetry") or {})
            active = bool(tel.get("pacing_active")) or \
                float(tel.get("hotspot_c") or 0) > 88.0
        except (OSError, ValueError, TypeError):
            active = False
        _pacing_cache.update(mtime=m, active=active)
    return _pacing_cache["active"]


def apply_pipeline(req, session_fp, mode=None):
    """Mutates req in place. Returns error-dict or None. Never raises past here
    in a way that blocks the request — caller falls back to raw forwarding."""
    preset = PRESETS.get(req.get("model"))
    if not preset:
        return None  # unknown model strings pass untouched (§6.8.1)
    if preset.get("class") == "frontier":
        return {"error": {"message":
                "minder: 'frontier' is consulted out-of-band by minder hooks; "
                "it is not an upstream model.", "type": "minder_frontier_alias",
                "code": "frontier_not_forwardable"}}

    level = marker_level(req)
    if level:
        # the stateless escalation channel, made auditable: one line per
        # request the digest marker upgraded to think params
        minder.log(session_fp, "escalation_upgraded", model=req.get("model"),
                   level=level)
    mode = (mode or "").lower().strip()
    if mode == "deep" and sinter_pacing():
        mode = "lean"
        minder.log(session_fp, "thermal_downgrade", model=req.get("model"))
    escalated = level is not None or preset.get("class") == "think"
    if preset.get("class") == "auto":
        return apply_auto_pipeline(req, preset, escalated, session_fp,
                                   mode=mode, level=level)
    active = PRESETS.get("qwen-think") if (escalated and
                                           PRESETS.get("qwen-think")) else preset
    want = escalated or active.get("class") == "think"
    if mode and mode in MODE_EFFORT and level is None:
        # consequence mode selects thinking depth (escalation marker wins)
        want = MODE_EFFORT[mode] != "off"

    req.update(flat_params(active))

    caps = get_caps()
    if caps is None:
        minder.log(session_fp, "caps_missing")
    else:
        req, degraded = adapter.apply_mode(req, want, caps, active)
        if degraded:
            minder.log(session_fp, "l1_degraded")
        if caps.get("kwargs_accepted") is False and \
                caps.get("thinking", {}).get("mechanism") == "kwargs":
            minder.log(session_fp, "kwargs_rejected")
        if want and mode in MODE_BUDGET and \
                caps.get("thinking", {}).get("thinking_budget_supported"):
            ctk = dict(req.get("chat_template_kwargs") or {})
            ctk["thinking_budget"] = MODE_BUDGET[mode]
            req["chat_template_kwargs"] = ctk

    um = active.get("upstream_model")
    if um:
        req["model"] = um
    return None


# Per-session escalation level, for the de-escalation audit trail (the
# marker aging out of the window used to be silent). Bounded: oldest
# sessions evicted once the map grows.
_ESC_SEEN = {}
_ESC_LOCK = threading.Lock()
_ESC_MAX = 1024


def _note_escalation(session_fp, level):
    """Track the last seen escalation level per session; log
    `escalation_downgraded` when a session's level drops or the marker
    ages out of the recent window. Never raises."""
    try:
        with _ESC_LOCK:
            prev = _ESC_SEEN.get(session_fp)
            if prev == level:
                return
            _ESC_SEEN[session_fp] = level
            if len(_ESC_SEEN) > _ESC_MAX:
                _ESC_SEEN.pop(next(iter(_ESC_SEEN)), None)
        if prev is not None and (level is None or level < prev):
            minder.log(session_fp, "escalation_downgraded", from_level=prev,
                       to_level=level)
    except Exception:
        pass


def _difficulty_state(req):
    """The task text laya sees: the first user message, redacted and
    truncated. Never the full conversation — the rubric grades required
    reasoning, not task length."""
    for m in req.get("messages") or []:
        if m.get("role") == "user":
            try:
                from minder_memory.canonicalise import redact
            except Exception:
                def redact(s):
                    return s
            return {"task": redact(str(m.get("content", "")))[:500],
                    "turn": len(req.get("messages") or [])}
    return {"task": "", "turn": len(req.get("messages") or [])}


def _difficulty_opinion(req, session_fp, cfg, level, client_effort):
    """Laya fast decision layer: task difficulty prior (never a solver).
    Returns (label, band) for an active opinion, or None — None means no
    opinion (off, shadow-logged, or fail-open) and the caller falls through
    to mode/scheduler. Precedence: this is only consulted when neither the
    escalation marker nor a client effort is present, so its opinion is
    always the one that lands (or is shadow-logged)."""
    try:
        router = (cfg.get("difficulty_router") or "off").strip().lower()
        if router not in ("shadow", "active") or level or client_effort:
            return None
        from minder_decision.contracts import task_difficulty_contract
        from minder_decision.difficulty import resolve_difficulty
        from minder_decision.client import get_decision_client
        client = get_decision_client()
        if client is None:
            return None
        contract = task_difficulty_contract()
        state = _difficulty_state(req)
        response = client.system_one(state, contract.questions,
                                     contract=contract)
        resolved = resolve_difficulty(response, contract, cfg)
        if resolved is None:
            return None
        label, band = resolved
        if router == "shadow":
            minder.log(session_fp, "difficulty_shadow", label=label,
                       score=response.score_values.get("difficulty_score"),
                       confidence=response.confidence, band=band["label"])
            return None
        return label, band
    except Exception:
        return None  # fail-open: the request proceeds unchanged (Law #2)


def _level_budget(level, cfg):
    """Level-aware thinking budget (Standard/Deep bands), cfg-overridable."""
    if not level:
        return None
    try:
        if level == 1:
            return int(cfg.get("l1_budget") or LEVEL_BUDGET[1])
        return int(cfg.get("l2_budget") or LEVEL_BUDGET[2])
    except (TypeError, ValueError):
        return LEVEL_BUDGET.get(level)


# --- spending guardrail (auto path only) ---------------------------------
#
# Session-scoped thinking-token ledger. When a session's cumulative
# reasoning tokens exceed `spend_guardrail_tokens` (0 = off), that session's
# NEXT requests are downgraded one band (deep -> standard -> fast)
# regardless of source (marker/client/laya/mode). Mirrors the sinter_pacing
# thermal-downgrade pattern. Bounded: oldest sessions evicted.
_SPEND_LEDGER = {}
_SPEND_LOCK = threading.Lock()
_SPEND_MAX = 1024
_BAND_ORDER = ("expert_or_ambiguous", "complex", "routine", "mechanical")


def _note_spend(session_fp, reasoning_tokens):
    """Accumulate reasoning tokens for a session. Never raises."""
    try:
        if not reasoning_tokens:
            return
        with _SPEND_LOCK:
            total = _SPEND_LEDGER.get(session_fp, 0) + int(reasoning_tokens)
            _SPEND_LEDGER[session_fp] = total
            if len(_SPEND_LEDGER) > _SPEND_MAX:
                _SPEND_LEDGER.pop(next(iter(_SPEND_LEDGER)), None)
    except (TypeError, ValueError):
        pass


def _spend_capped(session_fp, cfg):
    """True when the session has exceeded its thinking-token cap."""
    try:
        cap = int(cfg.get("spend_guardrail_tokens") or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return False
    with _SPEND_LOCK:
        return _SPEND_LEDGER.get(session_fp, 0) > cap


def _downgrade_band(band, session_fp, cfg):
    """Downgrade a band one rung (deep -> standard -> fast) and log it.
    Returns the downgraded band (or None if already at the floor)."""
    try:
        idx = _BAND_ORDER.index(band.get("label"))
    except ValueError:
        return None
    if idx >= len(_BAND_ORDER) - 1:
        return None
    lower = _BAND_ORDER[idx + 1]
    from minder_decision.difficulty import band_for
    new_band = band_for(lower, cfg)
    minder.log(session_fp, "spend_guardrail_downgrade",
               from_label=band.get("label"), to_label=lower)
    return new_band


def apply_auto_pipeline(req, preset, escalated, session_fp, mode=None,
                        level=None):
    """class: auto — per-request effort scheduling; precedence: escalation
    marker (level-aware) > client/UI reasoning_effort > laya difficulty
    band > X-Minder-Mode > activity classifier.
    A client-passed effort is honored only if it is a known semantic name or
    in the CAP-measured vocabulary; anything else is ignored (fail-open).
    On escalation the level selects the effort AND the thinking budget
    (level budget beats X-Minder-Mode); de-escalation is audited."""
    req.update(flat_params(preset))
    cfg = minder.cfg()
    _note_escalation(session_fp, level)
    caps = get_caps()
    client_effort = req.pop("reasoning_effort", None)
    accepted = (caps or {}).get("effort_levels") or []
    budget = None
    guardrail = None
    if level:
        effort = LEVEL_EFFORT.get(level, "high")
        budget = _level_budget(level, cfg)
        minder.log(session_fp, "auto_effort", effort=effort,
                   escalated=True, level=level, budget=budget)
    elif client_effort in _KNOWN_EFFORTS or client_effort in accepted:
        effort = client_effort
        minder.log(session_fp, "auto_effort", effort=effort,
                   escalated=False, source="client")
    else:
        opinion = _difficulty_opinion(req, session_fp, cfg, level,
                                       client_effort)
        if opinion is not None:
            label, band = opinion
            # spending guardrail: a session over its thinking-token cap is
            # downgraded one band (deep -> standard -> fast) on this request
            if band.get("guardrail") and _spend_capped(session_fp, cfg):
                downgraded = _downgrade_band(band, session_fp, cfg)
                if downgraded is not None:
                    band = downgraded
                    label = band["label"]
            from minder_decision.difficulty import apply_band
            applied = apply_band(req, band, cfg)
            effort = applied["effort"]
            budget = applied["budget"]
            guardrail = applied["guardrail"]
            minder.log(session_fp, "difficulty_routed", label=label,
                       band=band["label"], effort=effort, budget=budget,
                       max_tokens=applied["max_tokens"],
                       guardrail=guardrail)
        elif mode in MODE_EFFORT:
            effort = MODE_EFFORT[mode]
            minder.log(session_fp, "auto_effort", effort=effort,
                       escalated=False, source="mode")
        else:
            effort = adapter.schedule_effort(req.get("messages"), cfg)
            minder.log(session_fp, "auto_effort", effort=effort,
                       escalated=False)
    want = effort not in (None, "off", "minimal")
    if caps is not None:
        req, degraded = adapter.apply_auto(req, effort, caps, preset)
        if degraded:
            minder.log(session_fp, "l1_degraded")
        if want and (budget or mode in MODE_BUDGET) and \
                caps.get("thinking", {}).get("thinking_budget_supported"):
            ctk = dict(req.get("chat_template_kwargs") or {})
            ctk["thinking_budget"] = budget or MODE_BUDGET[mode]
            req["chat_template_kwargs"] = ctk
    else:
        req["chat_template_kwargs"] = {"enable_thinking": want}
    um = preset.get("upstream_model")
    if um:
        req["model"] = um
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    MAX_JSON_BUFFER = 64 * 1024 * 1024

    def _usage_line(self, ln, watch):
        """Harvest usage from one SSE data line. Returns False only when the
        line must be swallowed (watch == 'swallow' and this is the usage-only
        event we injected via stream_options)."""
        if not ln.startswith(b"data:"):
            return True
        payload = ln[5:].strip()
        if not payload or payload == b"[DONE]":
            return True
        try:
            ev = json.loads(payload)
        except ValueError:
            return True
        if not isinstance(ev, dict):
            return True
        u = ev.get("usage")
        if isinstance(u, dict) and u.get("prompt_tokens") is not None:
            self._usage = u
        return not (watch == "swallow" and ev.get("choices") == [])

    def _relay_sse(self, up, watch):
        """Byte-exact SSE relay; reassembles lines only to find the usage
        event (injected streams swallow it, passive streams forward as-is)."""
        buf = b""
        while True:
            chunk = up.read(4096)
            if not chunk:
                break
            if watch is None:
                self.wfile.write(chunk)
                self.wfile.flush()
                continue
            buf += chunk
            *lines, buf = buf.split(b"\n")
            out = bytearray()
            for ln in lines:
                if self._usage_line(ln, watch):
                    out += ln + b"\n"
            if out:
                self.wfile.write(out)
                self.wfile.flush()
        if buf:  # trailing partial line — cannot be a complete usage event
            self._usage_line(buf, watch)
            self.wfile.write(buf)
            self.wfile.flush()

    def _relay_body(self, up):
        """Buffered relay for non-streaming bodies (bounded); harvests the
        top-level usage field for the ledger. Bytes are forwarded verbatim."""
        data = bytearray()
        overflow = False
        while True:
            chunk = up.read(65536)
            if not chunk:
                break
            if not overflow:
                data += chunk
                if len(data) > self.MAX_JSON_BUFFER:
                    overflow = True
                    self.wfile.write(bytes(data))
                    self.wfile.flush()
            else:
                self.wfile.write(chunk)
                self.wfile.flush()
        if not overflow:
            body = bytes(data)
            try:
                ev = json.loads(body)
            except ValueError:
                ev = None
            if isinstance(ev, dict):
                u = ev.get("usage")
                if isinstance(u, dict) and u.get("prompt_tokens") is not None:
                    self._usage = u
            self.wfile.write(body)
            self.wfile.flush()

    def _log_usage(self):
        u = getattr(self, "_usage", None)
        if u:
            details = {}
            pd = u.get("prompt_tokens_details") or {}
            if isinstance(pd, dict) and pd.get("cached_tokens") is not None:
                details["cached_tokens"] = pd["cached_tokens"]
            cd = u.get("completion_tokens_details") or {}
            if isinstance(cd, dict) and cd.get("reasoning_tokens") is not None:
                details["reasoning_tokens"] = cd["reasoning_tokens"]
                _note_spend(getattr(self, "_fp", None),
                            cd["reasoning_tokens"])
            flat = {k: u[k] for k in ("prompt_tokens", "completion_tokens",
                                      "total_tokens") if u.get(k) is not None}
            minder.log(getattr(self, "_fp", None) or "session:passthru",
                       "token_usage", **flat, **details)

    def _relay(self, up):
        self.send_response(up.status)
        ctype = None
        for k, v in up.getheaders():
            lk = k.lower()
            if lk in HOP_HEADERS:
                continue
            if lk == "content-type":
                ctype = v
            self.send_header(k, v)
        if ctype is None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self._usage = None
        watch = getattr(self, "_usage_watch", None)
        try:
            if (ctype or "").lower().startswith("text/event-stream"):
                self._relay_sse(up, watch)
            else:
                self._relay_body(up)
        finally:
            self._log_usage()

    def _forward_raw(self, body=None):
        headers = {"Content-Type": self.headers.get("Content-Type",
                                                    "application/json")}
        r = urllib.request.Request(UPSTREAM + self.path, data=body,
                                   headers=headers, method=self.command)
        try:
            up = urllib.request.urlopen(r, timeout=600)
        except urllib.error.HTTPError as e:
            up = e
        except Exception:
            msg = json.dumps({"error": {"message":
                f"minder: upstream unreachable at {UPSTREAM} (fail-open: "
                f"transport is honest — fix upstream or bypass the proxy)",
                "type": "minder_upstream_unavailable"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        try:
            self._relay(up)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        except (ValueError, ConnectionResetError):
            return
        if self.path.rstrip("/").endswith("/chat/completions") and \
                0 < len(raw) <= SCAN_GATE_BYTES:
            try:
                req = json.loads(raw)
                if isinstance(req, dict):
                    if DUMP_DIR:
                        try:
                            pathlib.Path(DUMP_DIR).mkdir(parents=True,
                                                         exist_ok=True)
                            name = f"{time.time():.3f}-{len(raw)}.json"
                            (pathlib.Path(DUMP_DIR) / name).write_bytes(raw)
                        except OSError:
                            pass
                    first = json.dumps((req.get("messages") or [])[:1],
                                       default=str)
                    fp = "session:" + hashlib.md5(first.encode()).hexdigest()[:8]
                    err = apply_pipeline(req, fp,
                                         mode=self.headers.get("X-Minder-Mode"))
                    if err is not None:
                        body = json.dumps(err).encode()
                        self.send_response(422)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    self._fp = fp
                    if req.get("stream"):
                        so = req.get("stream_options")
                        if isinstance(so, dict) and so.get("include_usage"):
                            self._usage_watch = "passive"
                        else:
                            # ask upstream for usage; the injected usage-only
                            # event is swallowed in the relay so the client
                            # sees exactly what it asked for
                            merged = dict(so) if isinstance(so, dict) else {}
                            merged["include_usage"] = True
                            req["stream_options"] = merged
                            self._usage_watch = "swallow"
                    raw = json.dumps(req).encode()
            except (ValueError, TypeError):
                pass  # non-JSON body → pass through untouched
        self._forward_raw(raw)

    def _upstream_down(self):
        msg = json.dumps({"error": {"message":
                f"minder: upstream unreachable at {UPSTREAM}",
                "type": "minder_upstream_unavailable"}}).encode()
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            try:
                with urllib.request.urlopen(UPSTREAM + self.path,
                                            timeout=30) as up:
                    payload = json.loads(up.read())
            except Exception:
                payload = {"object": "list", "data": []}
            data = list(payload.get("data", []))
            for alias, preset in PRESETS.items():
                data.append({"id": alias, "object": "model",
                             "owned_by": "minder",
                             "class": preset.get("class", "exec")})
            payload["object"] = "list"
            payload["data"] = data
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward_raw()

    def log_message(self, *a):
        pass


def refresh_caps_in_background():
    """Law #9 at boot: re-measure against the upstream that is actually
    running — a llama-server relaunch can change template/flags out from
    under a caps file probed days ago (the 2026-09-19 blind spot). Serves
    immediately with cached caps; swaps in the fresh probe when it lands.
    Unreachable upstream ⇒ cached caps stand, no ledger spam."""
    def _run():
        try:
            fresh, _err = adapter.run_cap(UPSTREAM, minder.cfg())
        except Exception:
            return
        if not fresh:
            return
        cached = get_caps() or {}
        try:
            adapter.write_caps(CONFIG_DIR / "model_caps.json", fresh)
        except OSError:
            return
        _caps_cache["mtime"] = None  # force reload on next get_caps()
        changed = ((cached.get("fingerprint") or {}).get("model_id") !=
                   (fresh.get("fingerprint") or {}).get("model_id") or
                   (cached.get("thinking") or {}).get("mechanism") !=
                   (fresh.get("thinking") or {}).get("mechanism"))
        minder.log("proxy", "cap_refreshed" if changed else "cap_reverified",
                   mechanism=(fresh.get("thinking") or {}).get("mechanism"),
                   model_id=(fresh.get("fingerprint") or {}).get("model_id"))
        tc = fresh.get("tool_calls") or {}
        if not tc.get("clean", True):
            minder.log("proxy", "tool_dialect_dirty",
                       dirty=tc.get("dirty"), probes=tc.get("probes"))
    threading.Thread(target=_run, daemon=True).start()


def main():
    minder.log("proxy", "cap_result",
               mechanism=(get_caps() or {}).get("thinking", {}).get("mechanism"),
               model_id=(get_caps() or {}).get("fingerprint", {}).get("model_id"))
    refresh_caps_in_background()
    print(f"minder-proxy :{PORT} -> {UPSTREAM} "
          f"({len(PRESETS)} presets, mechanism="
          f"{(get_caps() or {}).get('thinking', {}).get('mechanism')})",
          file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
