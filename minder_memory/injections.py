"""The injection ledger (issue #13).

minder's learning loop was open. Retrieval returned a lesson,
minder_memory/policy.py appended it to the Warden digest, and nothing
recorded that it happened. Two things were therefore unanswerable: an
operator looking at a lesson could not see that it keeps firing, and no
claim about whether minder helps an agent had a denominator, because the
record of what was injected, to which session, under which trigger, did
not exist.

This module writes one row per injection decision, including the
decisions that found nothing to inject. A ledger of successes alone
cannot answer "how often did we have a lesson and not use it", which is
the question retrieval tuning is actually about.

Ledger only: no policy here, no retrieval here. Every call fails open
(the package law) - a hook path must never lose a directive because the
ledger is unavailable.
"""
from . import db as _db
from .store import _now, _uid

# Retrieval tiers, in the order minder_memory.retrieval applies them.
# TIER_NONE is the no-injection case, which the ledger records too.
TIER_EXACT = "exact"
TIER_FAMILY = "family"
TIER_NONE = "n/a"

# assist_mode column: which code path made the decision.
MODE_RETRIEVE = "retrieve"
MODE_BLOCK_DUPLICATE = "block_duplicate"

# The assist_mode value for a row whose path named itself with nothing.
# Never empty, so a console cell is never blank; it is not a zero.
UNKNOWN_MODE = "unknown"


def tier_of(lesson, failure_key):
    """Which retrieval tier produced this lesson: the exact repo +
    failure_key match, or the failure-family fallback. No lesson, no
    tier."""
    if not lesson:
        return TIER_NONE
    return TIER_EXACT if lesson.get("failure_key") == failure_key \
        else TIER_FAMILY


def record_injection(session_id=None, event_id=None, failure_key=None,
                     repo=None, lesson_id=None, tier=TIER_NONE,
                     chars_injected=0, assist_mode="", db_path=None):
    """Append one injection decision. Returns (injection_id, status);
    never raises. trigger_matched stays NULL until the trigger column of
    issue #15 exists to fill it."""
    try:
        conn = _db.connect(db_path)
        try:
            iid = _uid("inj")
            _db.write(
                conn,
                "INSERT INTO learning_injections (injection_id, ts,"
                " session_id, event_id, failure_key, repo, lesson_id,"
                " tier, trigger_matched, digest_injected, chars_injected,"
                " assist_mode, redaction_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (iid, _now(), session_id, event_id, failure_key, repo,
                 lesson_id, tier, 1 if lesson_id else 0,
                 int(chars_injected or 0), assist_mode or UNKNOWN_MODE,
                 "redacted"))
            return iid, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def injections_for_lesson(lesson_id, db_path=None, limit=50):
    """Most recent injection rows for one lesson, newest first; [] on any
    problem. The operator's question is whether this lesson still fires
    and how often, so the newest slice is the useful one."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT injection_id, ts, session_id, failure_key, repo,"
                " tier, digest_injected, chars_injected, assist_mode"
                " FROM learning_injections WHERE lesson_id = ?"
                " ORDER BY ts DESC LIMIT ?", (lesson_id, int(limit))
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def injection_counts(db_path=None, limit=50):
    """Per-lesson injection counts, busiest first: how often each lesson
    was injected and when it last fired. [] on any problem."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT lesson_id, COUNT(*) AS injections,"
                " MAX(ts) AS last_ts FROM learning_injections"
                " WHERE lesson_id IS NOT NULL"
                " GROUP BY lesson_id ORDER BY injections DESC, last_ts DESC"
                " LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def injection_miss_count(db_path=None):
    """How many injection decisions had no lesson to offer - the
    denominator half of every retrieval-tuning question. None when the
    store is unreadable, so a caller never renders it as zero."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM learning_injections"
                " WHERE lesson_id IS NULL").fetchone()
            return int(row["n"]) if row else 0
        finally:
            conn.close()
    except Exception:
        return None
