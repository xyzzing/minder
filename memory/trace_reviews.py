"""Trace review storage (migration 014) — append-only evidence about a
completed DSH run, and the human feedback on it.

Follows the memory plane's contract exactly: stdlib sqlite through
`memory.db`, writes through `_db.write` (one `BEGIN IMMEDIATE`), every
public function returns a status string and NEVER raises (constraint 10 —
the CLI must always be able to print something).

What is stored and what is not: the review row keeps the *summary* and the
*findings*, not the normalized events. Events are derived from the trace on
demand, so the live event table is untouched and a re-review is always
possible from the original DSH log.
"""
import json
import uuid
from datetime import datetime, timezone

from . import db as _db
from .canonicalise import redact

# The closed feedback taxonomy (PRD §9.4). Categorised feedback is what
# makes trends, acceptance rates and regression conversion possible; free
# text alone cannot be counted.
CATEGORIES = (
    "correct",
    "partly_correct",
    "incorrect_conclusion",
    "unsupported_claim",
    "evidence_quality",
    "tool_selection",
    "tool_parameter",
    "tool_result_ignored",
    "inefficient",
    "policy_violation",
    "safety_privacy",
    "incomplete",
)
LEVELS = ("run", "event", "claim")
FINDING_VERDICTS = ("confirm", "reject")

COMMENT_CAP = 2000

# Bumped when an evaluator's behaviour changes: a finding is only
# comparable across review runs of the same evaluator version.
EVALUATOR_VERSION = "trace-eval-1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def store_review(run, findings, summary=None, *, report=None, rubric_id=None,
                 policy_version=None, db_path=None):
    """Append one review. Returns `(review_id, status)`.

    `status` is `ok`, or `degraded:<Exception>` — a storage failure must
    never cost the caller the review it just computed, so the caller prints
    either way and the status is recorded rather than raised."""
    try:
        run = run if isinstance(run, dict) else {}
        source = run.get("source") or {}
        review_id = _uid("trv")
        row = {
            "review_id": review_id,
            "run_id": str(run.get("run_id") or ""),
            "session_id": str(source.get("session_id") or ""),
            "ts": _now(),
            "status": str((report or {}).get("status") or "ok"),
            "evaluator_version": EVALUATOR_VERSION,
            "rubric_id": rubric_id,
            "policy_version": policy_version,
            "summary_json": json.dumps(summary or {}, sort_keys=True,
                                       default=str),
            "findings_json": json.dumps(list(findings or ()),
                                        sort_keys=True, default=str),
            "redaction_status": str((report or {}).get(
                "redaction_status") or "redacted"),
        }
        conn = _db.connect(db_path)
        try:
            _db.write(
                conn,
                "INSERT INTO trace_reviews (review_id, run_id, session_id,"
                " ts, status, evaluator_version, rubric_id, policy_version,"
                " summary_json, findings_json, redaction_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row["review_id"], row["run_id"], row["session_id"],
                 row["ts"], row["status"], row["evaluator_version"],
                 row["rubric_id"], row["policy_version"],
                 row["summary_json"], row["findings_json"],
                 row["redaction_status"]))
        finally:
            conn.close()
        return review_id, "ok"
    except Exception as exc:  # noqa: BLE001 — fail open, never raise
        return None, f"degraded:{type(exc).__name__}"


def _row_to_review(row):
    out = dict(row)
    for key in ("summary_json", "findings_json"):
        try:
            out[key[:-5]] = json.loads(out.pop(key) or "null")
        except (ValueError, TypeError):
            out[key[:-5]] = None
    return out


def get_review(review_id, db_path=None):
    """One stored review, or None. Read-only; never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM trace_reviews WHERE review_id = ?",
                (str(review_id),)).fetchone()
        finally:
            conn.close()
        return _row_to_review(row) if row else None
    except Exception:  # noqa: BLE001
        return None


def list_reviews(session_id=None, limit=25, db_path=None):
    """Newest-first reviews, optionally for one session. Never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM trace_reviews WHERE session_id = ?"
                    " ORDER BY ts DESC, review_id DESC LIMIT ?",
                    (str(session_id), int(limit))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trace_reviews ORDER BY ts DESC,"
                    " review_id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        return [_row_to_review(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def latest_review(session_id, db_path=None):
    """The most recent review for a session, or None."""
    found = list_reviews(session_id, limit=1, db_path=db_path)
    return found[0] if found else None


def store_feedback(review_id, level, category, *, target_ref=None,
                   finding_id=None, finding_verdict=None, comment=None,
                   reviewer=None, run_id=None, session_id=None,
                   db_path=None):
    """Append one structured feedback item. `(feedback_id, status)`.

    Validates the closed vocabulary: an unknown level, category or verdict
    is refused rather than stored, because a category outside the taxonomy
    silently breaks every count that depends on it."""
    try:
        level = str(level or "").strip().lower()
        category = str(category or "").strip().lower()
        verdict = (str(finding_verdict).strip().lower()
                   if finding_verdict else None)
        if level not in LEVELS:
            return None, f"invalid:level must be one of {', '.join(LEVELS)}"
        if category not in CATEGORIES:
            return None, ("invalid:category must be one of "
                          + ", ".join(CATEGORIES))
        if verdict is not None and verdict not in FINDING_VERDICTS:
            return None, ("invalid:finding_verdict must be confirm or "
                          "reject")
        # Both or neither: a verdict names a finding, and a finding
        # judgement is a verdict. Allowing one without the other would
        # store a row no metric can interpret.
        if bool(finding_id) != bool(verdict):
            return None, ("invalid:--finding and --verdict must be given "
                          "together")
        review = get_review(review_id, db_path)
        if review is None:
            return None, f"not_found:no review {review_id}"
        feedback_id = _uid("tfb")
        conn = _db.connect(db_path)
        try:
            _db.write(
                conn,
                "INSERT INTO trace_feedback (feedback_id, review_id, run_id,"
                " session_id, ts, level, category, target_ref, finding_id,"
                " finding_verdict, comment, reviewer, redaction_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (feedback_id, str(review_id),
                 str(run_id or review.get("run_id") or ""),
                 str(session_id or review.get("session_id") or ""),
                 _now(), level, category,
                 (str(target_ref) if target_ref is not None else None),
                 (str(finding_id) if finding_id else None), verdict,
                 (redact(str(comment))[:COMMENT_CAP] if comment else None),
                 (str(reviewer) if reviewer else None), "redacted"))
        finally:
            conn.close()
        return feedback_id, "ok"
    except Exception as exc:  # noqa: BLE001
        return None, f"degraded:{type(exc).__name__}"


def list_feedback(review_id=None, limit=50, db_path=None):
    """Newest-first feedback, optionally for one review. Never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            if review_id:
                rows = conn.execute(
                    "SELECT * FROM trace_feedback WHERE review_id = ?"
                    " ORDER BY ts DESC, feedback_id DESC LIMIT ?",
                    (str(review_id), int(limit))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trace_feedback ORDER BY ts DESC,"
                    " feedback_id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def finding_verdicts(review_id, db_path=None):
    """`{finding_id: 'confirm'|'reject'}` — the last verdict wins, since a
    reviewer may change their mind and later feedback is the newer
    evidence. Read-only; never raises."""
    out = {}
    for item in reversed(list_feedback(review_id, limit=1000,
                                       db_path=db_path)):
        fid = item.get("finding_id")
        verdict = item.get("finding_verdict")
        if fid and verdict:
            out[str(fid)] = str(verdict)
    return out


def acceptance_stats(db_path=None):
    """Confirmed vs rejected findings, for the finding-acceptance metric
    (PRD §12). Never raises."""
    counts = {"confirm": 0, "reject": 0}
    for item in list_feedback(limit=10000, db_path=db_path):
        verdict = item.get("finding_verdict")
        if verdict in counts:
            counts[verdict] += 1
    total = counts["confirm"] + counts["reject"]
    return {
        "confirmed": counts["confirm"],
        "rejected": counts["reject"],
        "total": total,
        "acceptance_rate": (round(counts["confirm"] / total, 3)
                            if total else None),
    }
