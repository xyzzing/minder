"""Capture health — "is the watchdog actually recording anything?".

The failure this exists for (2026-09-25): DSH runs command hooks inside a
file sandbox that makes the minder state directory read-only. Every hook
write is fail-open, so the hooks fired 172 times in one session, exited 0,
and persisted nothing at all — for three days — while every page of the
console looked "ok". Nothing in the system compared *hook invocations*
with *persisted records*, which is the one number that catches it.

Everything here is read-only and degrades to an explicit "not available"
rather than raising.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from minder_op import dsh_sessions

FRESH_SECS = 3600          # a store touched within the hour is live
WARN_SECS = 24 * 3600      # older than a day: the operator should look
# Coverage below this over a live window means capture is broken.
MIN_COVERAGE = 0.95
# Bound the log scan: only recent sessions can be "live", and the scan
# decompresses real session logs.
SCAN_LIMIT = 8
SCAN_MAX_BYTES = 8 * 1024 * 1024


def _state_dir():
    """Resolved per call (env overridable) so tests and systemd units can
    point the same report at a different store — mirrors how the console
    resolves the difficulty ledger."""
    env = os.environ.get("MINDER_STATE_DIR")
    if env:
        return Path(os.path.expanduser(env))
    try:
        import minder
        return Path(minder.STATE_DIR)
    except Exception:
        return Path(os.path.expanduser("~/.local/state/minder"))


def _now():
    return time.time()


def _status(age):
    if age is None:
        return "missing"
    if age <= FRESH_SECS:
        return "fresh"
    if age <= WARN_SECS:
        return "warn"
    return "stale"


def _age_text(age):
    if age is None:
        return "never"
    if age < 90:
        return f"{int(age)}s"
    if age < 48 * 3600:
        return f"{int(age // 3600)}h"
    return f"{int(age // 86400)}d"


def age_text(age):
    """Public age formatter (the console reuses it)."""
    return _age_text(age)


def _jsonl_last_ts(path):
    """(last epoch ts, mtime) for a JSONL ledger; ts may be numeric or ISO."""
    latest, mtime = None, None
    try:
        mtime = path.stat().st_mtime
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            end = fh.tell()
            fh.seek(max(0, end - 65536))
            tail = fh.read().rstrip(b"\n").split(b"\n")[-1]
        record = json.loads(tail)
        value = record.get("ts")
        if isinstance(value, (int, float)):
            latest = float(value)
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                latest = parsed.timestamp()
            except ValueError:
                latest = None
    except (OSError, ValueError, IndexError):
        pass
    return latest, mtime


def _newest_file_mtime(directory, pattern):
    newest = None
    try:
        for entry in directory.glob(pattern):
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            newest = max(newest or 0, mtime)
    except OSError:
        pass
    return newest


def _count_jsonl_since(path, event, since, field="event", known=None):
    """Split ledger records of one event type newer than `since` into
    (attributable to a known dsh session, everything else).

    The split is what keeps coverage honest: a record written for a
    session the store does not know (a synthetic probe, a deleted
    session, another harness) must not inflate "capture works"."""
    known_n = other_n = 0
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get(field) != event:
                    continue
                value = record.get("ts")
                if isinstance(value, (int, float)) and value < since:
                    continue
                if known is None or str(record.get("task") or "") in known:
                    known_n += 1
                else:
                    other_n += 1
    except OSError:
        return 0, 0
    return known_n, other_n


def _db_event_count(db_path, since_iso):
    try:
        from minder_op import queries
        rows = queries.events(db_path, limit=queries.MAX_LIMIT)
    except Exception:
        return None
    try:
        return sum(1 for r in rows if str(r.get("ts") or "") >= since_iso)
    except Exception:
        return None


def _live_hook_invocations(dsh_root, since, scan_limit=SCAN_LIMIT,
                           now=None):
    """PostToolUse hook invocations recorded in dsh's own session logs
    within the window. This is the ground truth the watchdog must keep up
    with — and the number that was invisible while capture was broken."""
    now = now if now is not None else _now()
    candidates = []
    for entry in dsh_sessions.session_dirs(dsh_root):
        if not entry.get("log_path") or not entry.get("mtime"):
            continue
        if entry["mtime"] < since:
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: e["mtime"], reverse=True)
    sessions, total = [], 0
    for entry in candidates[:scan_limit]:
        stats = dsh_sessions.log_stats(entry["log_path"],
                                       max_bytes=SCAN_MAX_BYTES)
        point = stats["hook_points"].get("PostToolUse", 0)
        total += point
        sessions.append({"session_id": entry["session_id"],
                         "invocations": point,
                         "hook_p50_ms": dsh_sessions.hook_duration_stats(
                             stats["hook_ms"])["p50_ms"]})
    return {"total": total, "sessions": sessions,
            "scanned": len(candidates[:scan_limit]),
            "truncated": len(candidates) > scan_limit,
            "window_s": int(now - since)}


def build(db_path=None, dsh_root=None, now=None, window_hours=1):
    """One read-only capture-health report.

    `now` is injectable so the model is deterministic under test, like
    `minder_op.summary`."""
    now = now if now is not None else _now()
    since = now - max(1, int(window_hours)) * 3600
    state = _state_dir()
    report = {"now": now, "window_hours": window_hours,
              "state_dir": str(state), "stores": [], "warnings": [],
              "sink": _sink_report(state),
              "coverage": {}, "sandbox": {}}

    # --- stores ---------------------------------------------------------
    events_ledger = state / "events.jsonl"
    ts, mtime = _jsonl_last_ts(events_ledger)
    report["stores"].append(_store("proxy/hook ledger", "events.jsonl",
                                   ts or mtime, now, events_ledger))
    trace = state / "hook-trace.jsonl"
    report["stores"].append(_store("hook trace", "hook-trace.jsonl",
                                   trace.stat().st_mtime
                                   if trace.exists() else None, now, trace))
    consults = state / "consults.jsonl"
    report["stores"].append(_store("consult trail", "consults.jsonl",
                                   consults.stat().st_mtime
                                   if consults.exists() else None, now,
                                   consults))
    db_ts = None
    try:
        from minder_op import queries
        db_ts = queries.last_event_ts(db_path)
    except Exception:
        db_ts = None
    if isinstance(db_ts, str):
        try:
            parsed = datetime.fromisoformat(db_ts.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            db_ts = parsed.timestamp()
        except ValueError:
            db_ts = None
    try:
        from minder_op.queries import resolve_path
        db_file = resolve_path(db_path)
    except Exception:
        db_file = db_path
    report["stores"].append(_store("memory DB events", str(db_file), db_ts,
                                   now, db_file))
    report["stores"].append(_store("session state", "*.json",
                                   _newest_file_mtime(state, "*.json"),
                                   now, state))

    # --- coverage -------------------------------------------------------
    # Only records attributable to a session dsh actually knows count: a
    # probe or a foreign session must not make a dead capture path look
    # alive. Unattributable rows are still reported, never hidden.
    known_sessions = {entry["session_id"]
                      for entry in dsh_sessions.session_dirs(dsh_root)}
    ground = _live_hook_invocations(dsh_root, since, now=now)
    persisted_ledger, persisted_other = _count_jsonl_since(
        events_ledger, "hook_timing", since, field="event",
        known=known_sessions)
    persisted_db = _db_event_count(db_path,
                                   datetime.fromtimestamp(
                                       since, timezone.utc).isoformat())
    persisted = max(persisted_ledger, persisted_db or 0)
    ratio = None
    if ground["total"] > 0:
        ratio = round(min(1.0, persisted / ground["total"]), 3)
    report["coverage"] = {
        "persisted": persisted,
        "persisted_ledger": persisted_ledger,
        "persisted_unattributed": persisted_other,
        "persisted_db": persisted_db,
        "invocations": ground["total"],
        "ratio": ratio,
        "min_ratio": MIN_COVERAGE,
        "sessions": ground["sessions"],
        "scanned": ground["scanned"],
        "truncated": ground["truncated"],
        "window_hours": window_hours,
    }

    # --- which modes can capture ---------------------------------------
    modes = {}
    for entry in dsh_sessions.session_dirs(dsh_root):
        record = (dsh_sessions.projection_cache(dsh_root)
                  .get(entry["session_id"]) or {})
        rows = record.get("rows") or {}
        mode = rows.get("sandboxMode") or (rows.get("permissions")
                                           or {}).get("sandbox")
        if mode:
            modes[mode] = modes.get(mode, 0) + 1
    report["sandbox"] = modes

    # --- warnings -------------------------------------------------------
    if not report["sink"]["configured"]:
        report["warnings"].append(
            "no sink configured (neither MINDER_SINK_URL nor the hook "
            "command in hooks.json): confined hooks cannot persist — this "
            "is how three days of capture was lost silently. Run "
            "install.sh.")
    elif not report["sink"]["reachable"]:
        report["warnings"].append(
            f"sink at {report['sink']['url']} is unreachable: hook writes "
            "are being dropped. Start it: systemctl --user start "
            "minder-sink.service")
    # A reachable sink with low coverage means the hooks are not reaching
    # it. The most common cause is that the running dsh host loaded its
    # hook command before hooks.json was rewritten (the bridge reads that
    # file exactly once), so the message names the fix rather than the
    # symptom. Ops seen at all only strengthens the wording: a sink that
    # has never been called was certainly never loaded.
    stats = (report["sink"].get("stats") or {})
    ops = stats.get("ops") or {}
    ever_called = any(int(v.get("ok", 0)) for v in ops.values())
    cov = report["coverage"]
    if (report["sink"]["reachable"] and cov["invocations"] > 0
            and cov["ratio"] is not None and cov["ratio"] < MIN_COVERAGE):
        loaded = ("no hook has ever called it" if not ever_called
                  else "only a fraction of hook invocations reach it")
        report["warnings"].insert(0,
            f"the sink at {report['sink']['url']} is up but {loaded} "
            f"({cov['persisted']} persisted / {cov['invocations']} hook "
            "invocations). The dsh web host reads hooks.json once at "
            "startup — restart it to load the hook command; if it has "
            "already restarted, check the MINDER_SINK_URL in hooks.json "
            "and `systemctl --user status minder-sink`.")
    for store in report["stores"]:
        if store["status"] == "stale":
            report["warnings"].append(
                f"{store['name']} has not been written for "
                f"{_age_text(store['age_s'])}.")
    # The policy pass runs in the sink, so the flags it was started with —
    # not the ones in the hook command — decide what the pass does. A
    # divergence is silent by construction unless it is called out here.
    # Compare ONLY the tracked policy flags: hooks.json also carries the
    # sink URL, which is deliberately absent from the sink's own view of
    # its flags (it is the address, not a behaviour switch), and flagging
    # that would be a permanent false alarm.
    flags_sink = (stats.get("flags") or {})
    tracked = _tracked_flags()
    if report["sink"]["reachable"] and flags_sink and tracked:
        hooks_flags = {v: _declared_flags().get(v) for v in tracked}
        drift = {var: (hooks_flags.get(var), flags_sink.get(var))
                 for var in tracked
                 if hooks_flags.get(var) != flags_sink.get(var)}
        if drift:
            detail = ", ".join(f"{var}: hooks={a or 'unset'} "
                               f"sink={b or 'unset'}"
                               for var, (a, b) in sorted(drift.items()))
            report["warnings"].append(
                "the sink and the hooks disagree about the policy flags "
                f"({detail}) — the memory policy pass runs inside the sink, "
                "so the hook's value is inert. Restart "
                "minder-sink.service to adopt hooks.json.")
    if cov["invocations"] > 0 and cov["ratio"] is not None \
            and cov["ratio"] < MIN_COVERAGE:
        report["warnings"].append(
            f"capture coverage {cov['ratio']:.0%} in the last "
            f"{window_hours}h ({cov['persisted']} persisted / "
            f"{cov['invocations']} hook invocations) — below the "
            f"{MIN_COVERAGE:.0%} floor.")
    if cov["invocations"] == 0 and not cov["scanned"]:
        report["warnings"].append(
            "no dsh session activity in the window — nothing to measure.")
    high_p50 = [s for s in cov["sessions"]
                if (s.get("hook_p50_ms") or 0) > 1000]
    if high_p50:
        worst = max(high_p50, key=lambda s: s["hook_p50_ms"])
        report["warnings"].append(
            f"hook p50 {worst['hook_p50_ms']:.0f} ms in "
            f"{worst['session_id'][:24]} — every tool call pays this; "
            "check the sink warm tier is running.")
    report["ok"] = not report["warnings"]
    return report


def _store(name, filename, when, now, path):
    age = (now - when) if when else None
    return {"name": name, "file": filename, "path": str(path),
            "last_ts": when, "age_s": age, "age": _age_text(age),
            "status": _status(age)}


def _declared_flags():
    """{MINDER_*: value} as declared by the hook command ({} if unknown)."""
    try:
        from memory import sink
        return sink.declared_flags() or {}
    except Exception:
        return {}


def _tracked_flags():
    """The policy flags both sides are expected to agree on."""
    try:
        from memory import sink
        return sink.TRACKED_FLAGS
    except Exception:
        return ()


def _sink_report(state):
    """Is the hook's write path configured, and actually being used?

    "Configured" means the *hook command* declares it (hooks.json) or the
    observer's env sets it — not merely the observer's env, which is how a
    fully wired install reported "sink not configured"."""
    out = {"configured": False, "url": None, "source": None,
           "declared": None, "reachable": False, "stats": None}
    try:
        from memory import sink
    except Exception:
        return out
    try:
        out["declared"] = sink.declared_sink_url()
        url = sink.sink_url()
        out["source"] = sink.sink_source()
    except Exception:
        return out
    out["url"] = url
    out["configured"] = bool(url)
    if not url:
        return out
    try:
        stats = sink.stats(timeout_ms=500)
    except Exception:
        stats = None
    if stats:
        out["reachable"] = True
        out["stats"] = stats
    return out
