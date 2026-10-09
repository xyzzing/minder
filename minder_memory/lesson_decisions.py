"""Closed vocabularies for lesson decisions (issue #14).

The operator queue records that a decision happened and nothing else.
`invalidate_lesson` took free text, so the two questions the queue should
answer had no answer: which distillations get rejected and for what
reason, and which adopted lessons later turned out harmful. Free text
cannot be counted - the same argument `minder_memory/trace_reviews.py`
makes for feedback categories.

Two lists, both closed, both owned here (C4):

`DIAGNOSES` - what went wrong with a lesson that was invalidated.
`DECISION_CODES` - why a candidate was rejected, or why an adopted
lesson was judged useful.

`unknown` is in `DIAGNOSES` and is the default. That is not a
placeholder: the two rules in `EVIDENCE_RULES` are limits on what an
operator may conclude from the evidence minder keeps, and an operator
who cannot name a mechanism should pick `unknown` rather than guess.
No judging happens here; every code is chosen by the operator. An
out-of-taxonomy code is refused, because a code outside the list
silently breaks every count built on it.

Append-only, and every call fails open (the package law).
"""
from . import db as _db
from .store import _now, _uid

# Why a lesson was invalidated, after it had been adopted.
DIAGNOSES = (
    "unknown",               # default: no mechanism established
    "content_defect",        # the lesson text itself was wrong
    "application_failure",   # right text, applied where it did not fit
    "external_failure",      # the run failed for a reason outside the lesson
    "no_issue",              # invalidated on reflection; it was fine
)

# Why a candidate was rejected, or why an adopted lesson was kept.
DECISION_CODES = (
    "grounded_useful",         # the only accepting code
    "unsupported_evidence",    # episode evidence does not back the claim
    "generic",                 # advice any agent already knows
    "speculative",             # asserts a cause the trace does not show
    "unsupported_causality",   # claims X fixed Y without a link
    "unseen_artifact",         # names a file or symbol the run never touched
    "redundant",               # a live lesson already says this
    "late_trigger",            # would only fire after the damage is done
    "compound",                # several instructions in one row
    "internal_status",         # restates harness state, not a lesson
    "absence_inference",       # concludes from a missing record
    "not_agent_decision",      # not something an agent acts on
)

DEFAULT_DIAGNOSIS = "unknown"

# The two constraints that decide when a diagnosis may NOT be chosen.
# Verbatim in spirit; the evaluator of issue #16 restates them.
EVIDENCE_RULES = (
    "An unsuccessful run alone does not establish a content defect: a"
    " lesson is only defective if the run applied it and the text caused"
    " the failure.",
    "A lesson not being retrieved never shows the store lacks a rule:"
    " absence of an injection row is absence of a match, not absence of"
    " content.",
)

# Actions that end a lesson's life or settle a candidate's fate.
ACTION_INVALIDATE = "invalidate"
ACTION_REJECT = "reject"
ACTION_ADOPT = "adopt"
ACTIONS = (ACTION_INVALIDATE, ACTION_REJECT, ACTION_ADOPT)

NOTE_CAP = 500


def validate_diagnosis(value):
    """`(diagnosis, None)` or `(None, error)`. Empty means the default."""
    code = str(value or "").strip().lower() or DEFAULT_DIAGNOSIS
    if code not in DIAGNOSES:
        return None, ("invalid:diagnosis must be one of "
                      + ", ".join(DIAGNOSES))
    return code, None


def validate_code(value):
    """`(code, None)` or `(None, error)`; no default, a code is required."""
    code = str(value or "").strip().lower()
    if code not in DECISION_CODES:
        return None, ("invalid:code must be one of "
                      + ", ".join(DECISION_CODES))
    return code, None


def record_decision(lesson_id, action, code, note="", actor="operator",
                    db_path=None):
    """Append one decision row. `(decision_id, status)`, never raises."""
    try:
        if action not in ACTIONS:
            return None, f"invalid:action must be one of {', '.join(ACTIONS)}"
        conn = _db.connect(db_path)
        try:
            did = _uid("lds")
            _db.write(
                conn,
                "INSERT INTO lesson_decisions (decision_id, ts, lesson_id,"
                " action, code, note, actor, redaction_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (did, _now(), str(lesson_id), action, str(code),
                 str(note or "")[:NOTE_CAP], str(actor), "redacted"))
            return did, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"


def decisions_for_lesson(lesson_id, db_path=None, limit=50):
    """Newest decisions for one lesson, or [] on any failure."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM lesson_decisions WHERE lesson_id = ?"
                " ORDER BY ts DESC, decision_id DESC LIMIT ?",
                (str(lesson_id), int(limit))).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def decision_counts(db_path=None):
    """Decisions per (action, code), newest taxonomy first. `[]` on any
    failure. Counted from stored rows, never from free text."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT action, code, COUNT(*) AS n FROM lesson_decisions"
                " GROUP BY action, code ORDER BY action, code").fetchall()
            return [{"action": r["action"], "code": r["code"],
                     "count": r["n"]} for r in rows]
        finally:
            conn.close()
    except Exception:
        return []
