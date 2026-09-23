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

Flag law: nothing is recorded unless MINDER_SUCCESS_GUARD=advisory
(default off = zero behaviour change, zero rows). N and the window are
MINDER_SUCCESS_GUARD_N / MINDER_SUCCESS_GUARD_WINDOW_MIN. Every call
fails open: a broken store degrades to "no advisory", never a raise.
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


def guard_enabled():
    import os
    return (os.environ.get("MINDER_SUCCESS_GUARD") or "").strip().lower() \
        == "advisory"


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
        if advisory:
            note = (f"success-loop: this exact action has produced this "
                    f"exact result {count} times in the last "
                    f"{window_min} min (exit {exit_code}). It is not "
                    "advancing the task — change approach or verify the "
                    "goal.")
        return {"advisory": note, "count": count, "signature": signature,
                "excerpt": excerpt}
    except Exception as exc:  # noqa: BLE001 — fail open, never raise
        return {"advisory": None, "count": 0, "signature": "",
                "excerpt": "", "status": f"error:{type(exc).__name__}"}


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
