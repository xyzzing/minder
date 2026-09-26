"""Success-loop guard (docs/success-loop-guard-design.md).

Live incident this fixes: an agent repeated the identical *successful*
curl 20+ times (a landing page saved as .pdf; `| tail -5` masked curl's
exit code) and survived a context compaction — minder's duplicate guard
governs failure loops only, so a loop of successful-but-useless actions
was invisible.

Mechanism: for tool_success events, persist a normalized action+result
signature per session; when the same signature repeats >= N times within
the window, produce a deterministic advisory note. Delivery is stderr
(model-visible) from the hook layer — advisory only, never a block.

Normalization is the crux: all numeric tokens collapse to `N` (curl
speeds, byte counts, percentages), whitespace collapses, secrets are
redacted — so outputs that differ only in progress noise share one
signature, while genuinely different results do not.

Flag law: nothing is recorded unless MINDER_SUCCESS_GUARD is `advisory`
or `block` (default off = zero behaviour change, zero rows). N and the
window are MINDER_SUCCESS_GUARD_N / MINDER_SUCCESS_GUARD_WINDOW_MIN.
Every call fails open: a broken store degrades to "no advisory", never a
raise.

Delivery has two modes, and the mode is the operator's call:

  advisory  After the fact: the note rides `additionalContext` on the
            PostToolUse stdout JSON, which the bridge injects into the
            next model request. It never changes a decision.
  block     Pre-emptive: once an action has already reached the
            threshold, the *next* identical call is stopped by the
            PreToolUse hook (exit 2, structured directive on stderr),
            and a PostToolUse repeat is likewise returned as a block. A
            permission/policy failure is a terminal constraint, not
            something to retry around.

Both modes share one ledger, so `block` is simply "advisory plus
enforcement" — switching modes never loses the counts.
"""
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db as _db
from .canonicalise import redact

DEFAULT_N = 3
DEFAULT_WINDOW_MIN = 30
EXCERPT_CAP = 160
SIGNATURE_CAP = 512

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[A-Za-z]?")   # 1.24M / 905.5k / 0
_RUN_RE = re.compile(r"(?:N\s*)+")                    # progress-bar reflow
_WS_RE = re.compile(r"\s+")


def normalize_output(text):
    """Redact, collapse numeric tokens to N, collapse whitespace, cap.
    Deterministic; the exact bytes matter to the signature."""
    out = redact(str(text or ""))
    out = _NUMBER_RE.sub("N", out)
    # curl's progress bar REFLOWS between runs — the numeric slots move.
    # Fold entire runs of numbers into one N: the shape is the signal,
    # the count of slots is noise.
    out = _RUN_RE.sub("N ", out)
    out = _WS_RE.sub(" ", out).strip()
    return out[:SIGNATURE_CAP]


def result_signature(exit_code, output):
    """sha256 of exit code + normalized output. Same action + same
    (volatile-normalized) result => same signature."""
    normalized = normalize_output(output)
    blob = f"{exit_code}|{normalized}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


GUARD_MODES = ("off", "advisory", "block")


def guard_mode():
    """The operator's chosen mode. Anything unrecognised (including a
    typo) reads as `off`, so a misconfigured flag can never silently
    start blocking tool calls."""
    import os
    value = (os.environ.get("MINDER_SUCCESS_GUARD") or "").strip().lower()
    return value if value in GUARD_MODES else "off"


def guard_enabled():
    """Whether the guard records at all. True for both delivery modes;
    the caller decides how (or whether) it is delivered."""
    return guard_mode() != "off"


def _settings():
    import os

    def _int(name, default):
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default
    return (_int("MINDER_SUCCESS_GUARD_N", DEFAULT_N),
            _int("MINDER_SUCCESS_GUARD_WINDOW_MIN", DEFAULT_WINDOW_MIN))


def observe(session_id, tool, action_fingerprint, exit_code, output, *,
            ts=None, db_path=None):
    """Record one successful observation and evaluate the loop counter.

    Returns {"advisory": str|None, "count": int, "signature": str,
    "excerpt": str} — advisory is None until the Nth repeat within the
    window. Records (and advises) regardless of the flag: the flag gates
    the CALLER (from_hook only calls this when MINDER_SUCCESS_GUARD=
    advisory), keeping the default-off path byte-inert. Never raises.
    """
    try:
        n, window_min = _settings()
        signature = result_signature(exit_code, output)
        excerpt = redact(str(output or ""))[:EXCERPT_CAP]
        now_dt = ts if isinstance(ts, datetime) else (
            datetime.fromisoformat(ts) if isinstance(ts, str) and ts
            else datetime.now(timezone.utc))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        window_start = (now_dt - timedelta(minutes=window_min)).isoformat()
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM success_observations"
                " WHERE session_id = ? AND action_fingerprint = ? AND"
                " result_signature = ? AND ts >= ?",
                (session_id, action_fingerprint, signature,
                 window_start)).fetchone()["n"] + 1
            advisory = count >= n
            conn.execute(
                "INSERT INTO success_observations (obs_id, ts,"
                " session_id, tool, action_fingerprint, result_signature,"
                " exit_code, excerpt, advisory)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_uid("so"), now_dt.isoformat(), session_id, tool,
                 action_fingerprint, signature, exit_code, excerpt,
                 1 if advisory else 0))
            conn.execute("COMMIT")
        finally:
            conn.close()
        note = None
        directive = None
        if advisory:
            note = (f"success-loop: this exact action has produced this "
                    f"exact result {count} times in the last "
                    f"{window_min} min (exit {exit_code}). It is not "
                    "advancing the task — change approach or verify the "
                    "goal.")
            directive = stop_directive(tool=tool, count=count,
                                       window_min=window_min,
                                       exit_code=exit_code,
                                       excerpt=excerpt)
        return {"advisory": note, "directive": directive, "count": count,
                "signature": signature, "excerpt": excerpt}
    except Exception as exc:  # noqa: BLE001 — fail open, never raise
        return {"advisory": None, "directive": None, "count": 0,
                "signature": "", "excerpt": "",
                "status": f"error:{type(exc).__name__}"}


def stop_directive(*, tool, count, window_min, exit_code, excerpt=""):
    """The structured block reason shown to the model when the guard
    stops a repeat.

    Deliberately NOT "you are looping, try again" — that invites a new
    verbal loop. It names the exact action and count, forbids the repeat,
    and requires the next attempt to change a named causal element. The
    exit condition is a materially different action, which is checkable
    by the same fingerprint that raised the incident.
    """
    shown = redact(str(excerpt or ""))[:EXCERPT_CAP].strip()
    lines = [
        "SYSTEM RECOVERY EVENT — EXECUTION PAUSED",
        "",
        "Reason: REPEATED_SUCCESSFUL_ACTION (no progress)",
        f"Tool: {tool or '?'}",
        f"Identical action+result observed: {count} times in "
        f"{window_min} min (exit {exit_code})",
        "New evidence produced by the repeat: none",
    ]
    if shown:
        lines.append(f"Result excerpt: {shown}")
    lines += [
        "",
        "Do not repeat this action with the same or materially "
        "equivalent arguments.",
        "",
        "Required response:",
        "1. State in one sentence why the repeated action is not "
        "advancing the task.",
        "2. Change at least one causal element before the next attempt: "
        "the hypothesis, the tool, the query, or the decomposition.",
        "3. Name the new evidence the changed action is meant to "
        "produce.",
        "4. If no valid path exists, record the evidence gap and ask "
        "for the missing input instead of retrying.",
    ]
    return "\n".join(lines)


def blocked_action(session_id, action_fingerprint, *, db_path=None,
                   window_min=None):
    """Has this exact action already looped in this session?

    Read by the PreToolUse hook, which sees only the requested action
    (never its result). The result signature is therefore not needed:
    the ledger already recorded which actions reached the threshold.

    Returns the worst offending observation row (highest count is not
    stored, so the newest advisory row wins) or None. Never raises.
    """
    try:
        if not session_id or not action_fingerprint:
            return None
        n, default_window = _settings()
        window = default_window if window_min is None else int(window_min)
        since = (datetime.now(timezone.utc)
                 - timedelta(minutes=window)).isoformat()
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS repeats, MAX(ts) AS last_ts,"
                " MAX(excerpt) AS excerpt, MAX(tool) AS tool,"
                " MAX(exit_code) AS exit_code"
                " FROM success_observations WHERE session_id = ?"
                " AND action_fingerprint = ? AND advisory = 1"
                " AND ts >= ?",
                (session_id, action_fingerprint, since)).fetchone()
        finally:
            conn.close()
        repeats = int((row["repeats"] if row else 0) or 0)
        if repeats < 1:
            return None
        return {"repeats": repeats, "tool": row["tool"],
                "excerpt": row["excerpt"], "exit_code": row["exit_code"],
                "last_ts": row["last_ts"], "threshold": n,
                "window_min": window}
    except Exception:  # noqa: BLE001 — fail open, never raise
        return None


def list_success_loops(limit=25, db_path=None):
    """Recent advisory-raising observations, newest first (operator
    view). Read-only; never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT obs_id, ts, session_id, tool,"
                " action_fingerprint, result_signature, exit_code,"
                " excerpt FROM success_observations WHERE advisory = 1"
                " ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
