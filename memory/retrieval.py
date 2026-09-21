"""Verified lesson retrieval (docs/prd-memory-v1.md PR 4).

Order: (1) exact repo + failure_key, verified and still valid;
(2) same repo + same failure family. No cross-repo retrieval in v1.
Payloads are compact: instruction, anti-pattern, verification summary —
never raw tool logs. Never raises; [] on any problem.
"""
import json

from . import db as _db

_COMPACT_KEYS = ("lesson_id", "instruction", "anti_pattern", "verification",
                 "status")


def retrieve_lessons(repo, failure_key, paths=None, limit=3, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            out = []
            if repo and failure_key:
                rows = conn.execute(
                    "SELECT * FROM lessons WHERE repo = ? AND failure_key = ?"
                    " AND status = 'verified' AND valid_to IS NULL"
                    " ORDER BY valid_from DESC LIMIT ?",
                    (repo, failure_key, limit)).fetchall()
                out = [dict(r) for r in rows]
            if len(out) < limit and repo and failure_key and "|" in failure_key:
                family = failure_key.split("|")[1]
                exclude = [r["lesson_id"] for r in out]
                ph = ",".join("?" * len(exclude)) or "''"
                rows = conn.execute(
                    f"SELECT * FROM lessons WHERE repo = ? AND"
                    f" failure_key LIKE ? AND status = 'verified' AND"
                    f" valid_to IS NULL AND lesson_id NOT IN ({ph})"
                    f" ORDER BY valid_from DESC LIMIT ?",
                    (repo, f"%|{family}|%", *exclude, limit - len(out))
                ).fetchall()
                out += [dict(r) for r in rows]
            return [compact_lesson(r) for r in out[:limit]]
        finally:
            conn.close()
    except Exception:
        return []


def compact_lesson(row):
    try:
        verification = json.loads(row.get("verification_json") or "{}")
    except (ValueError, TypeError):
        verification = {}
    return {k: row.get(k) for k in _COMPACT_KEYS} | \
        {"verification": verification}
