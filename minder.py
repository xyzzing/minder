#!/usr/bin/env python3
"""minder core — deterministic L0→L3 escalation state machine. Stdlib only.

Laws (prd.md §1): structural triggers only · budget sovereignty · digests add
information, not exhortation (degraded L1 excepted, breaker-armed) · detection
never blocks the hot path (callers must never let exceptions from here escape).
"""
import hashlib
import json
import os
import pathlib
import re
import time

MINDER_VERSION = "0.7"

STATE_DIR = pathlib.Path(os.environ.get(
    "MINDER_STATE_DIR", os.path.expanduser("~/.local/state/minder")))
CFG_PATH = pathlib.Path(os.environ.get(
    "MINDER_CONFIG", os.path.expanduser("~/.config/minder/minder.json")))
CAPS_PATH = pathlib.Path(os.environ.get(
    "MINDER_CAPS", os.path.expanduser("~/.config/minder/model_caps.json")))

DEFAULTS = {
    "fail_threshold": 2,       # consecutive failures of one key before L1
    "think_budget": 2,         # L1 escalations per session
    "frontier_budget": 1,      # L2 escalations per session
    "cooldown_turns": 4,       # global min turns between escalations
    "backfire_window": 2,      # turns after own escalation that count as backfire
    "backfire_trip": 3,        # backfires before the breaker trips for a key
    # §5.7 config additions
    "thinking_markers": ["<think>", "</think>"],
    "softswitch_tokens": {"on": "/think", "off": "/no_think"},
    "mechanism_override": None,   # "kwargs"|"softswitch"|none — forces the adapter
    "accept_l1_degraded": False,
    # effort scheduling (class: auto traffic; absorbed thinking-levels concept)
    "effort_mode": "off",         # off | auto | fixed
    "effort_fixed_level": "low",  # used when effort_mode == fixed
}

# §6.1 structural failure signals — never semantic, never model-name based.
FAIL_SIGNS = (
    "old_string not found", "string to replace not found", "no match found",
    "exit code 1", "exit code 2", "exit code 127", "exit code 134",
    "traceback (most recent call last)", "syntaxerror", "permission denied",
    "error:", "failed:", "command not found", "compilation failed",
)
_EDIT_TOOLS = ("edit", "write", "multiedit", "apply_patch", "fs_write", "str_replace")

DIGEST_MARKERS = {1: "[minder] ESCALATION L1", 2: "[minder] ESCALATION L2",
                  3: "[minder] ESCALATION L3"}


def cfg():
    c = dict(DEFAULTS)
    try:
        if CFG_PATH.exists():
            loaded = json.loads(CFG_PATH.read_text())
            c.update(loaded)
            # domain profile overlay (v0.4): coding | trading | legal | …
            # profile-scoped keys win over globals; explicit request config
            # (the top-level keys) still wins over the profile
            prof = loaded.get("profile")
            if prof and isinstance(loaded.get("profiles"), dict):
                overlay = loaded["profiles"].get(prof)
                if isinstance(overlay, dict):
                    c.update({k: v for k, v in overlay.items()})
    except (OSError, ValueError):
        pass
    return c


_chain_tail = None


def _read_last_chain():
    """Chain hash of the last ledger record (cross-process continuity);
    empty string when no chained history exists yet."""
    try:
        with open(STATE_DIR / "events.jsonl", "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 4096))
            tail = f.read().rstrip(b"\n").split(b"\n")[-1]
        return json.loads(tail).get("chain", "")
    except (OSError, ValueError, IndexError):
        return ""


def log(task, event, **kw):
    """Ledger (§5.5): append-only JSONL. Privacy: never log response bodies.
    With cfg audit_chain=true each record carries sha256(prev_chain+record) —
    tamper-evident history for audit-grade domains (legal, trading)."""
    global _chain_tail
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "task": task, "event": event, **kw}
        if cfg().get("audit_chain"):
            if _chain_tail is None:  # first chained write in this process
                _chain_tail = _read_last_chain()
            rec["chain"] = hashlib.sha256(
                (_chain_tail + json.dumps(rec, sort_keys=True)).encode()
            ).hexdigest()
            _chain_tail = rec["chain"]
        with open(STATE_DIR / "events.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def tool_key(tool, args):
    tool = (tool or "").lower()
    args = args if isinstance(args, dict) else {}
    if any(t in tool for t in _EDIT_TOOLS) or tool in ("str_replace_editor", "save_file"):
        return "edit:" + str(args.get("file_path", args.get("path", args.get("notebook_path", "?"))))
    if "bash" in tool or "shell" in tool or "exec" in tool:
        return "cmd:" + " ".join(str(args.get("command", "")).split())[:200]
    # inspection tools (read/glob/grep/…): carry the target so failures on
    # different files/patterns never aggregate into one loop counter
    # (2026-09-20 field run: every read failure collapsed to "read:generic",
    # starving the frontier consult of context and cross-counting noise)
    target = (args.get("file_path") or args.get("path") or args.get("pattern")
              or args.get("query") or args.get("url") or args.get("notebook_path"))
    if target:
        return tool + ":" + str(target)[:200]
    return (tool or "unknown") + ":generic"


def is_failure(text, extra=None):
    t = str(text).lower()
    signs = FAIL_SIGNS + tuple(extra or ())
    return any(s in t for s in signs)


# Floats are masked before hashing: run durations/percentages differ between
# otherwise-identical failures and must not look like progress.
_FLOAT_NOISE = re.compile(r"\d+\.\d+")


def err_digest_hash(text):
    norm = _FLOAT_NOISE.sub("#.#", str(text)[:200])
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


def _state_path(task):
    safe = hashlib.md5(str(task).encode()).hexdigest()[:16]
    return STATE_DIR / f"{safe}.json"


def load_state(task):
    sf = _state_path(task)
    try:
        st = json.loads(sf.read_text())
    except (OSError, ValueError):
        st = {}
    st.setdefault("turn", 0)
    st.setdefault("caps_mechanism", "unknown")
    st.setdefault("think_used", 0)
    st.setdefault("frontier_used", 0)
    st.setdefault("last_esc", -10_000)
    st.setdefault("failures", {})
    st.setdefault("breaker", {})
    st.setdefault("l3_fired", False)
    return st


def save_state(task, st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(task).write_text(json.dumps(st))


def snapshot_caps(task):
    """SessionStart (§5.3): copy caps mechanism so mid-upgrade behavior is stable."""
    mech = "unknown"
    try:
        caps = json.loads(CAPS_PATH.read_text())
        mech = caps.get("thinking", {}).get("mechanism", "unknown")
    except (OSError, ValueError):
        pass
    st = load_state(task)
    st["caps_mechanism"] = mech
    save_state(task, st)
    log(task, "session_start", mechanism=mech)
    return mech


def compact_brief(task):
    """SessionStart(compact): render the watchdog's live state as a context
    brief so failure memory and the escalation channel survive the compaction
    boundary — summarizers drop failure history (compaction-trap literature);
    the ledger doesn't. Empty string when nothing is pending."""
    st = load_state(task)
    lines = []
    if st["failures"]:
        lines.append("[minder] failure memory preserved across compaction:")
        for key, rec in st["failures"].items():
            lines.append(f"- {key}: failed {rec['n']}x, escalation level "
                         f"{rec.get('level', 0)}")
    if st.get("think_used") or st.get("frontier_used"):
        lines.append(f"[minder] escalation budgets used so far: "
                     f"think {st.get('think_used', 0)}, frontier "
                     f"{st.get('frontier_used', 0)}")
    active = [k for k, r in st["failures"].items() if r.get("level", 0) >= 1]
    if active:
        # the marker line re-arms the Turnstile proxy's think-upgrade for the
        # first post-compaction retry (the original digest was summarized
        # away); it ages out of the 4-message window naturally
        lines.append("[minder] ESCALATION L1 — an escalation was in flight "
                     "at compaction for the keys above; retry them with "
                     "deliberation, not verbatim.")
    return "\n".join(lines)


# --- Digest templates (§5.6). Every template starts with the grep-able marker
# the Turnstile proxy uses as the escalation signal. ---

def digest_l1(key, n, err, mechanism, demoted=False):
    if demoted:  # backfire breaker demotion
        return (f"{DIGEST_MARKERS[1]} — repeat pattern broken.\n"
                f"- {key} has now failed {n}x.\n"
                f"- Do NOT repeat the prior attempt verbatim: change exactly one "
                f"variable, then verify the result before the next call.")
    if mechanism == "none":  # verbatim PRD §5.6 degraded template (breaker-armed)
        return (f"{DIGEST_MARKERS[1]} — this model cannot toggle reasoning server-side.\n"
                f"- FAILED {n}x: {key}\n"
                f"- last error: {str(err)[:300]}\n"
                f"- Prior attempts are VOID. First, in plain text, list 2-3 root-cause "
                f"hypotheses with a check for each. Then acquire fresh ground truth. "
                f"Only then retry.")
    return (f"{DIGEST_MARKERS[1]} — think before retrying.\n"
            f"- FAILED {n}x: {key}\n"
            f"- last error: {str(err)[:300]}\n"
            f"- Prior attempts are VOID. State a root-cause hypothesis FIRST (zero "
            f"tool calls before it). Then acquire fresh ground truth. Only then "
            f"retry with a regenerated anchor or corrected command.")


def digest_l2(key, n, err, frontier_section):
    return (f"{DIGEST_MARKERS[2]} — frontier consult for: {key}\n"
            f"- FAILED {n}x; the think-retry did not resolve it.\n"
            f"- last error: {str(err)[:300]}\n"
            f"{frontier_section}\n"
            f"Apply the guidance above, then verify with a green check before "
            f"proceeding. Never commit frontier output without a green verify.")


def digest_l3(key, think_used, frontier_used):
    return (f"{DIGEST_MARKERS[3]} — budgets exhausted. {key} still failing after "
            f"{think_used} think-retries and {frontier_used} frontier consults. "
            f"STOP retrying this action. Report the failure to the operator and "
            f"move on.")


def process(ev, c=None):
    """ev: {session_id, hook_event_name, tool_name, tool_input, tool_response}.
    Returns directive {action, level, digest, frontier_payload}. Never raises."""
    out = {"action": None, "level": 0, "digest": None, "frontier_payload": None}
    try:
        return _process(ev, c, out)
    except Exception as e:  # Law #6: detection must never break the hot path
        try:
            log(ev.get("session_id", "default"), "warden_error", error=str(e)[:200])
        except Exception:
            pass
        return out


def _process(ev, c, out):
    c = c or cfg()
    task = ev.get("session_id") or "default"
    st = load_state(task)
    st["turn"] += 1

    tool = ev.get("tool_name", "") or ""
    args = ev.get("tool_input", {}) or {}
    resp = ev.get("tool_response", "")
    text = resp if isinstance(resp, str) else json.dumps(resp, default=str)
    key = tool_key(tool, args)

    if not is_failure(text, c.get("fail_signs_extra")):
        if key in st["failures"]:
            rec = st["failures"].pop(key)
            # Budget refund (2026-09-20 field-run fix): a key that RESOLVED
            # gives back what its escalations consumed — budgets then measure
            # wasted escalation, not any escalation, so early noise can't
            # starve later real failures of the whole ladder.
            for spent in rec.get("spent", []):
                if spent == "think" and st["think_used"] > 0:
                    st["think_used"] -= 1
                elif spent == "frontier" and st["frontier_used"] > 0:
                    st["frontier_used"] -= 1
            log(task, "deescalate", key=key)
            # Verify consult (v0.4): a frontier-escalated key that resolved
            # gets one cheap panel check — did the fix address the root cause?
            if "frontier" in rec.get("spent", []) and c.get("verify_consult",
                                                            True):
                out["verify_payload"] = {
                    "task": task, "kind": "verify", "key": key,
                    "attempts": rec["n"],
                    "error": rec.get("err_excerpt", ""),
                    "resolution": str(text)[:800]}
        save_state(task, st)
        return out

    rec = st["failures"].setdefault(
        key, {"n": 0, "h": "", "level": 0, "esc_turn": -10_000})
    h = err_digest_hash(text)
    if rec["h"] and h != rec["h"]:
        # Error signature changed — that is progress, not a loop (a TDD
        # red-green cycle changes its failure every run). Restart the count
        # for this key; the escalation level and breaker memory stand.
        rec["n"] = 0
        log(task, "error_changed", key=key)
    rec["n"] += 1
    rec["h"] = h
    rec["err_excerpt"] = str(text)[:300]  # verify-consult context (local state)
    n = rec["n"]

    # Backfire breaker (§6.5): per-key memory that survives success-resets.
    br = st["breaker"].setdefault(
        key, {"backfire": 0, "tripped": False, "until": 0})
    if rec["level"] >= 1 and st["turn"] - rec["esc_turn"] <= c["backfire_window"]:
        br["backfire"] += 1
        if br["backfire"] == 1:
            log(task, "breaker_demote", key=key)
        if br["backfire"] >= c["backfire_trip"] and not br["tripped"]:
            br["tripped"] = True
            br["until"] = st["turn"] + 2 * c["cooldown_turns"]
            log(task, "breaker_trip", key=key, n=n)
    if br["tripped"] and st["turn"] >= br["until"]:
        br["tripped"] = False
        br["backfire"] = 0

    breaker_active = br["tripped"]
    cooled = (st["turn"] - st["last_esc"]) > c["cooldown_turns"]

    if n >= c["fail_threshold"] and cooled and breaker_active:
        log(task, "breaker_suppress", key=key, n=n)
    elif n >= c["fail_threshold"] and cooled:
        mechanism = st["caps_mechanism"]
        if mechanism == "unknown":
            # No SessionStart snapshot (e.g. dsh registers PostToolUse only):
            # fall back to the live caps file, then pin it for the session.
            try:
                mechanism = json.loads(CAPS_PATH.read_text()).get(
                    "thinking", {}).get("mechanism", "none")
            except (OSError, ValueError):
                mechanism = "none"
            st["caps_mechanism"] = mechanism
        if rec["level"] == 0 and st["think_used"] < c["think_budget"]:
            rec["level"] = 1
            rec.setdefault("spent", []).append("think")
            st["think_used"] += 1
            out["action"] = "think"
            out["level"] = 1
            demoted = br["backfire"] >= 1
            out["digest"] = digest_l1(key, n, text, mechanism, demoted)
            if demoted:
                br["backfire"] = 0  # demotion consumed
        elif st["frontier_used"] < c["frontier_budget"]:
            # Think budget spent (or this key already used its L1): frontier.
            rec["level"] = 2
            rec.setdefault("spent", []).append("frontier")
            st["frontier_used"] += 1
            out["action"] = "frontier"
            out["level"] = 2
            out["frontier_payload"] = {"task": task, "key": key, "attempts": n,
                                       "error": str(text)[:2000]}
        elif not st["l3_fired"]:
            st["l3_fired"] = True
            out["action"] = "alarm"
            out["level"] = 3
            out["digest"] = digest_l3(key, st["think_used"], st["frontier_used"])
            log(task, "l3_alarm", key=key, n=n)

        if out["action"] in ("think", "frontier"):
            st["last_esc"] = st["turn"]
            rec["esc_turn"] = st["turn"]
            log(task, "escalate", level=out["level"], key=key, n=n)

    save_state(task, st)
    return out


def log_consult(task, key, attempts, error, response):
    """Consult trail (audit seam): one record per L2 frontier consult."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "consults.jsonl", "a") as f:
            f.write(json.dumps({"ts": time.time(), "task": task, "key": key,
                                "attempts": attempts,
                                "error": str(error)[:2000],
                                "response": str(response)[:4000]}) + "\n")
    except OSError:
        pass


def report(limit=200, as_json=False):
    """After-action audit: the intervention timeline from the ledger +
    consult trail — the operator's answer to 'what did minder actually do?'."""
    events, consults = [], []
    try:
        with open(STATE_DIR / "events.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        pass
    try:
        with open(STATE_DIR / "consults.jsonl") as f:
            consults = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        pass
    timeline = [e for e in events if e.get("event") in (
        "escalate", "deescalate", "redigest", "breaker_demote", "breaker_trip",
        "breaker_suppress", "l1_degraded", "l3_alarm", "cap_result",
        "kwargs_rejected", "frontier_unavailable", "auto_effort",
        "session_start")][-limit:]
    out = {"interventions": timeline, "consults": consults[-limit:]}
    if as_json:
        return out
    lines = []
    for e in out["interventions"]:
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(e.get("ts", 0)))
        detail = " ".join(f"{k}={v}" for k, v in e.items()
                          if k not in ("ts", "task", "event"))
        lines.append(f"{ts} [{e.get('task', '?')}] {e.get('event')} {detail}")
    for c in out["consults"]:
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(c.get("ts", 0)))
        lines.append(f"{ts} [consult] {c.get('key')} attempts={c.get('attempts')} "
                     f"response={str(c.get('response', ''))[:80]!r}")
    return "\n".join(lines) if lines else "(no interventions recorded)"


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="minder")
    sub = ap.add_subparsers(dest="cmd")
    rp = sub.add_parser("report", help="after-action audit of interventions")
    rp.add_argument("--json", action="store_true")
    rp.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    if args.cmd == "report":
        out = report(limit=args.limit, as_json=args.json)
        print(json.dumps(out) if args.json else out)
    else:
        ev = json.load(sys.stdin)
        print(json.dumps(process(ev)))
