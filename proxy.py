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
ESC_MARKER = re.compile(r"\[minder\] ESCALATION L[12]\b")
RECENT_WINDOW = 4
# PRD v2 §4.2 consequence modes: DSH signals per-request via X-Minder-Mode.
# Modes drive ONLY thinking depth + budget; samplers stay preset-driven
# (cache-stable prefixes matter more than per-mode temperature).
MODE_EFFORT = {"direct": "off", "lean": "low", "deep": "high"}
MODE_BUDGET = {"lean": 1024, "deep": 4096}
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


def marker_in_window(req):
    """True iff a minder escalation digest sits in the recent message window."""
    for m in (req.get("messages") or [])[-RECENT_WINDOW:]:
        if ESC_MARKER.search(str(m.get("content", ""))):
            return True
    return False


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

    marker = marker_in_window(req)
    if marker:
        # the stateless escalation channel, made auditable: one line per
        # request the digest marker upgraded to think params
        minder.log(session_fp, "escalation_upgraded", model=req.get("model"))
    mode = (mode or "").lower().strip()
    if mode == "deep" and sinter_pacing():
        mode = "lean"
        minder.log(session_fp, "thermal_downgrade", model=req.get("model"))
    escalated = marker or preset.get("class") == "think"
    if preset.get("class") == "auto":
        return apply_auto_pipeline(req, preset, escalated, session_fp,
                                   mode=mode)
    if preset.get("class") == "auto":
        return apply_auto_pipeline(req, preset, escalated, session_fp)
    active = PRESETS.get("qwen-think") if (escalated and
                                           PRESETS.get("qwen-think")) else preset
    want = escalated or active.get("class") == "think"
    if mode and mode in MODE_EFFORT and not marker:
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


def apply_auto_pipeline(req, preset, escalated, session_fp, mode=None):
    """class: auto — per-request effort scheduling; consequence mode and
    minder escalation both override the activity classifier (marker first,
    then X-Minder-Mode, then scheduled effort)."""
    req.update(flat_params(preset))
    caps = get_caps()
    if escalated:
        effort = "high"
    elif mode in MODE_EFFORT:
        effort = MODE_EFFORT[mode]
        minder.log(session_fp, "auto_effort", effort=effort,
                   escalated=False, source="mode")
    else:
        effort = adapter.schedule_effort(req.get("messages"), minder.cfg())
        minder.log(session_fp, "auto_effort", effort=effort,
                   escalated=False)
    want = effort in ("low", "high")
    if caps is not None:
        req, degraded = adapter.apply_auto(req, effort, caps, preset)
        if degraded:
            minder.log(session_fp, "l1_degraded")
        if want and mode in MODE_BUDGET and \
                caps.get("thinking", {}).get("thinking_budget_supported"):
            ctk = dict(req.get("chat_template_kwargs") or {})
            ctk["thinking_budget"] = MODE_BUDGET[mode]
            req["chat_template_kwargs"] = ctk
    else:
        req["chat_template_kwargs"] = {"enable_thinking": want}
    um = preset.get("upstream_model")
    if um:
        req["model"] = um
    if escalated:
        minder.log(session_fp, "auto_effort", effort=effort, escalated=True)
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        while True:
            chunk = up.read(4096)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

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
