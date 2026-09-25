"""Sandbox-proof persistence client for the minder sink sidecar.

Why this exists: DSH runs command hooks through a confined shell
(bwrap/landlock) whose only writable path is the deployment workspace
root. A hook process therefore cannot write `~/.local/state/minder`
directly — it gets `EROFS` — and because every write in minder is
fail-open, the loss was completely silent: the hooks fired, exited 0,
and nothing was ever persisted (observed 2026-09-25: 50 `hook/invoked`
records in one session log, zero rows in memory.sqlite).

Loopback is *not* confined, so when `MINDER_SINK_URL` is set the writes
are delegated to the sink (a long-lived, unsandboxed sidecar) and the
local path is used only as a fallback. With `MINDER_SINK_URL` unset this
module is inert and every caller keeps its local behaviour byte-for-byte.

Contract (Law #6, unchanged): nothing here raises. A missing, slow, or
lying sink returns a falsy value and the caller falls back.
"""
import json
import os
import urllib.request

DEFAULT_TIMEOUT_MS = 400
_CT = {"Content-Type": "application/json"}
DEFAULT_HOOKS_JSON = "~/.local/share/minder/dsh/hooks.json"
# The runtime flags the hook command declares. The sink runs the same
# policy pass, so it must run with the same values — otherwise the pass
# silently changes meaning depending on which process executes it.
TRACKED_FLAGS = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION",
                 "MINDER_SUCCESS_GUARD", "MINDER_HOOK_TRACE")
_DECLARED = {"key": None, "url": None, "flags": None}


def hooks_json_path():
    """Where the dsh hook commands live (env overridable, like the share)."""
    return os.path.expanduser(os.environ.get("MINDER_HOOKS_JSON")
                              or DEFAULT_HOOKS_JSON)


def _read_declared(path=None):
    """Parse the hook command(s) once per file version: the sink URL and
    every MINDER_* flag assignment they carry."""
    target = path or hooks_json_path()
    try:
        key = (str(target), os.path.getmtime(target))
    except OSError:
        return None, {}
    if _DECLARED["key"] == key:
        return _DECLARED["url"], dict(_DECLARED["flags"] or {})
    url, flags = None, {}
    try:
        with open(target) as fh:
            blob = fh.read()
        import re
        for var, value in re.findall(r"(MINDER_[A-Z0-9_]+)=(\S+)", blob):
            value = value.strip("\"'")
            flags.setdefault(var, value)
        for chunk in blob.split("MINDER_SINK_URL=")[1:]:
            candidate = chunk.split()[0].strip("\"'")
            if candidate.startswith(("http://", "https://")):
                url = candidate.rstrip("/")
                break
    except Exception:
        url, flags = None, {}
    _DECLARED["key"], _DECLARED["url"], _DECLARED["flags"] = key, url, flags
    return url, flags


def declared_sink_url(path=None):
    """The sink URL the *hook command* actually carries, read from
    hooks.json.

    The URL is a deployment fact recorded there, not something the
    observer's environment knows: without this, `minder-op capture`,
    `doctor` and the console all report "sink not configured" while the
    hooks are in fact wired to one. The env var still wins when set, so a
    hook process keeps using exactly what it was launched with.
    Returns None when the file is absent, unreadable, still a template,
    or carries no URL."""
    return _read_declared(path)[0]


def declared_flags(path=None):
    """{MINDER_*: value} as declared by the hook command(s)."""
    return _read_declared(path)[1]


def sink_source():
    """"env", "hooks.json" or None — where sink_url() got its answer."""
    if (os.environ.get("MINDER_SINK_URL") or "").strip():
        return "env"
    return "hooks.json" if declared_sink_url() else None


def sink_url():
    """The sink base URL to use, or None when the sidecar is off.

    env `MINDER_SINK_URL` first (what a hook process was launched with),
    then the value the hook command declares in hooks.json."""
    try:
        url = (os.environ.get("MINDER_SINK_URL") or "").strip()
    except Exception:
        return None
    if not url:
        return declared_sink_url()
    return url.rstrip("/") or None


def enabled():
    return sink_url() is not None


def _timeout_s(timeout_ms=None):
    try:
        ms = int(timeout_ms if timeout_ms is not None
                 else os.environ.get("MINDER_SINK_TIMEOUT_MS",
                                     DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        ms = DEFAULT_TIMEOUT_MS
    return max(1, ms) / 1000.0


def _post(path, payload, timeout_ms=None, base=None):
    """POST one JSON payload. Returns the decoded dict, or None on any
    failure (unreachable, timeout, bad status, non-dict body)."""
    url = (base or sink_url())
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url + path, data=json.dumps(payload).encode(),
            headers=_CT, method="POST")
        with urllib.request.urlopen(req, timeout=_timeout_s(timeout_ms)) as r:
            if r.status != 200:
                return None
            body = json.loads(r.read() or b"{}")
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def persist(op, payload=None, timeout_ms=None):
    """One sink op. `{op, ...payload}`. Returns `{"ok": True, "result": …}`
    on success, None otherwise (the caller then uses its local path)."""
    body = {"op": op}
    if payload:
        body.update(payload)
    resp = _post("/persist", body, timeout_ms)
    if not resp or not resp.get("ok"):
        return None
    return resp


def append_jsonl(name, record, timeout_ms=None):
    """Append one record to a sink-managed ledger (events.jsonl,
    consults.jsonl, hook-trace.jsonl). True when the sink accepted it."""
    return persist("append_jsonl", {"name": name, "record": record},
                   timeout_ms) is not None


def write_state(task, text, timeout_ms=None):
    """Persist one session state document, keyed the way minder keys it."""
    return persist("write_state", {"task": task, "text": text},
                   timeout_ms) is not None


def record(hook_event, timeout_ms=None):
    """Run memory.from_hook.record() in the sink. Returns
    `{"status": {...}, "advisory": str|None}` or None."""
    resp = persist("record", {"hook_event": hook_event}, timeout_ms)
    return resp.get("result") if resp else None


def policy(hook_event, warden_out, timeout_ms=None):
    """Run the memory policy pass in the sink. Returns
    `{"guard": dict|None}` or None. `{}` guard means "no directive".

    The policy pass is the one op that may include real model inference,
    so it gets its own (larger) budget; the sink warms its providers at
    startup so even this is normally tens of milliseconds."""
    if timeout_ms is None:
        try:
            timeout_ms = int(os.environ.get(
                "MINDER_SINK_POLICY_TIMEOUT_MS", "1500"))
        except (TypeError, ValueError):
            timeout_ms = 1500
    resp = persist("policy", {"hook_event": hook_event,
                              "warden_out": warden_out}, timeout_ms)
    return resp.get("result") if resp else None


def stats(timeout_ms=1000):
    """Sink self-report for the console/doctor; None when unreachable."""
    url = sink_url()
    if not url:
        return None
    try:
        with urllib.request.urlopen(url + "/stats",
                                    timeout=_timeout_s(timeout_ms)) as r:
            if r.status != 200:
                return None
            body = json.loads(r.read() or b"{}")
        return body if isinstance(body, dict) else None
    except Exception:
        return None
