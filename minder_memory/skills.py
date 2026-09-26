"""Skill-gap records (docs/prd-memory-v1.md PR 6).

When a failure matches no known skill trigger and repeats (or is clearly
environmental), record a gap row — queryable evidence that a skill is
missing. SKILLS.md is never written and no skill is auto-promoted
(constraints 4 and the PR 6 out-of-scope list). Environment-like families
must not escalate to frontier from here; this module only records.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import canonicalise as canon
from . import db as _db
from . import store

DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "skills" / "index.json"

ENV_FAMILIES = {
    "connectionrefusederror", "connectionreseterror", "filenotfounderror",
    "modulenotfounderror", "permissionerror", "timeouterror",
    "oserror", "exit-127",
}

REPEAT_THRESHOLD = 2


def _now():
    return datetime.now(timezone.utc).isoformat()


def load_index(path=None):
    """The tiny skill index (name + triggers only). Missing file → []."""
    try:
        data = json.loads(Path(path or DEFAULT_INDEX).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def match(index, failure_key, excerpt=""):
    """First skill whose triggers appear in the key or excerpt, else None."""
    hay = f"{failure_key} {excerpt}".lower()
    for skill in index:
        for trig in skill.get("triggers", []):
            if str(trig).lower() in hay:
                return skill.get("name")
    return None


def check_skill_gap(event, db_path=None, index_path=None, attempts=None):
    """Evaluate one failure against the skill index.

    Returns {gap, skill, gap_type, recorded}. Records a skill_gaps row when
    nothing matches AND (the failure repeats or is environment-like).
    Never raises; never writes SKILLS.md; never escalates.
    """
    out = {"gap": False, "skill": None, "gap_type": None, "recorded": False}
    try:
        fkey = event.get("failure_key") or canon.failure_key(event)
        excerpt = canon.normalise_error_excerpt(event.get("error_excerpt", ""),
                                                event.get("repo"))
        index = load_index(index_path)
        skill = match(index, fkey, excerpt)
        if skill:
            out["skill"] = skill
            return out
        out["gap"] = True
        family = fkey.split("|")[1] if "|" in fkey else "unknown"
        env_like = family in ENV_FAMILIES
        if attempts is None:
            attempts = store.count_attempts(fkey, db_path=db_path)
        if env_like:
            out["gap_type"] = "environment"
        elif attempts >= REPEAT_THRESHOLD:
            out["gap_type"] = "procedural"
        else:
            out["gap_type"] = "unknown"
        if out["gap_type"] in ("environment", "procedural"):
            out["recorded"] = _record(event, fkey, out["gap_type"],
                                      db_path=db_path)
        return out
    except Exception:
        return out


def _record(event, fkey, gap_type, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO skill_gaps (gap_id, ts, repo, failure_key,"
                " gap_type, sample_error, status) VALUES (?, ?, ?, ?, ?, ?,"
                " 'open')",
                (f"gap_{uuid.uuid4().hex[:12]}", _now(),
                 event.get("repo"), fkey, gap_type,
                 canon.redact(str(event.get("error_excerpt", ""))[:300])))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return False


def list_gaps(repo=None, db_path=None, limit=100):
    try:
        conn = _db.connect(db_path)
        try:
            if repo:
                rows = conn.execute(
                    "SELECT * FROM skill_gaps WHERE repo = ?"
                    " ORDER BY ts DESC LIMIT ?", (repo, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM skill_gaps ORDER BY ts DESC LIMIT ?",
                    (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def close_gap(gap_id, reason, db_path=None):
    """Operator close (8B): mark one open gap closed via an explicit CLI
    decision. Tombstone semantics — the row stays queryable. Returns
    (gap_dict, status); never raises. No SKILLS.md is written here or
    anywhere in this module."""
    try:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "UPDATE skill_gaps SET status = 'closed'"
                      " WHERE gap_id = ? AND status = 'open'", (gap_id,))
            row = conn.execute("SELECT * FROM skill_gaps WHERE gap_id = ?",
                               (gap_id,)).fetchone()
            if not row:
                return None, "rejected:no-such-gap"
            out = dict(row)
            out["close_reason"] = str(reason)
            return out, "ok"
        finally:
            conn.close()
    except Exception as e:
        return None, f"degraded:{type(e).__name__}"
