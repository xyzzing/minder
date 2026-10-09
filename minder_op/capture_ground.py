"""The coverage denominator: what the harness says the hooks did.

Capture health compares two numbers, and this module owns the harder one -
the ground truth for how many PostToolUse hooks actually fired in a
window, read from dsh's own session logs. It lives apart from the report
because its whole job is to get one count right: the ledger side is a
plain `ts` filter, and a denominator measured over a different span makes
the ratio meaningless (issue #30).

Everything here is read-only and degrades to zero rather than raising.
"""
import time

from minder_op import dsh_sessions

# Bound the log scan: only recent sessions can be "live", and the scan
# decompresses real session logs. The report that warns about a partial
# view re-exports these, so the bound is declared once (C4).
SCAN_LIMIT = 8
SCAN_MAX_BYTES = 8 * 1024 * 1024


def _hook_records(path, since, max_bytes):
    """PostToolUse invocations, their durations, and the truncation flag
    for one session log, restricted to records stamped inside the window.

    Two things the old count got wrong (issue #30). The window filter is
    the main one: the ledger side of the coverage ratio filters by `ts`,
    so a denominator counting a session's whole lifetime charges the
    watchdog for hooks from before the window - the log's mtime says a
    session is live, not that every record in it is recent. The other is
    the scan cap: it kept the head of an append-only log, so the records
    it dropped were the recent ones.

    Durations come from `hook/result` records - the harness logs the cost
    there, not on the invocation - so both record types are read."""
    total = 0
    durations = []
    records, truncated = dsh_sessions.log_scan(path, max_bytes=max_bytes,
                                               tail=True)
    for record in records:
        kind = record.get("type")
        if kind not in ("hook/invoked", "hook/result"):
            continue
        when = record.get("time")
        if not isinstance(when, (int, float)) or when / 1000.0 < since:
            continue
        data = record.get("data") or {}
        if data.get("point") != "PostToolUse":
            continue
        if kind == "hook/invoked":
            total += 1
        else:
            ms = data.get("durationMs")
            if isinstance(ms, (int, float)):
                durations.append(float(ms))
    return total, durations, truncated


def window_invocations(dsh_root, since, scan_limit=SCAN_LIMIT, now=None,
                       max_bytes=SCAN_MAX_BYTES):
    """PostToolUse hook invocations recorded in dsh's own session logs
    within the window. `max_bytes` bounds one log and `scan_limit` the
    number of logs, so the result says whether it is a complete count or
    a floor."""
    now = now if now is not None else time.time()
    candidates = []
    for entry in dsh_sessions.session_dirs(dsh_root):
        if not entry.get("log_path") or not entry.get("mtime"):
            continue
        if entry["mtime"] < since:
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: e["mtime"], reverse=True)
    sessions, total = [], 0
    scanned = candidates[:scan_limit]
    log_truncated = False
    for entry in scanned:
        point, durations, truncated = _hook_records(
            entry["log_path"], since, max_bytes)
        log_truncated = log_truncated or truncated
        total += point
        sessions.append({"session_id": entry["session_id"],
                         "invocations": point,
                         "hook_p50_ms": dsh_sessions.hook_duration_stats(
                             durations)["p50_ms"]})
    return {"total": total, "sessions": sessions,
            "scanned": len(scanned),
            "truncated": len(candidates) > scan_limit or log_truncated,
            "window_s": int(now - since)}
