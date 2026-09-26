"""Candidate skill promotion — PROPOSAL ONLY
(docs/minder-phase-2-3-frontier-coding.md P2.3).

Hard rules:
1. A candidate may exist only with >= 3 distinct verified, still-valid
   lessons sharing repo + failure-family prefix.
2. Instructions are distilled from lesson.instruction / anti_pattern only —
   frontier text is never a lesson and never becomes a skill (constraint 5).
3. Candidates are born `proposed`; nothing installs them as default skills.
4. skills/index.json / skills/bodies/ are written ONLY by
   accept_skill_candidate(actor="operator", apply=True). SKILLS.md is
   NEVER written by anything in this module.

All IO failures degrade to None / False without raising.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db as _db

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "skills" / "index.json"
BODIES_DIR = REPO_ROOT / "skills" / "bodies"

MIN_DISTINCT_LESSONS = 3


def _now():
    return datetime.now(timezone.utc).isoformat()


def _source_lessons(conn, repo, failure_family):
    rows = conn.execute(
        "SELECT * FROM lessons WHERE repo = ? AND failure_key LIKE ?"
        " AND status = 'verified' AND valid_to IS NULL"
        " ORDER BY valid_from DESC",
        (repo, f"%|{failure_family}|%")).fetchall()
    return [dict(r) for r in rows]


def _distill(lessons):
    """Instructions strictly from lesson fields; deduplicated, compact."""
    seen, lines = set(), []
    anti = []
    for lesson in lessons:
        instruction = str(lesson.get("instruction") or "").strip()
        if instruction and instruction.lower() not in seen:
            seen.add(instruction.lower())
            lines.append(instruction)
        a = str(lesson.get("anti_pattern") or "").strip()
        if a and a.lower() not in {x.lower() for x in anti}:
            anti.append(a)
    return "\n".join(f"- {line}" for line in lines), "\n".join(anti)


def propose_skill_from_lessons(repo, failure_family, name=None,
                               db_path=None):
    """Create ONE proposed candidate from >=3 verified lessons, else None."""
    try:
        conn = _db.connect(db_path)
        try:
            lessons = _source_lessons(conn, repo, failure_family)
            if len(lessons) < MIN_DISTINCT_LESSONS:
                return None
            instructions, anti_pattern = _distill(lessons)
            if not instructions:
                return None
            candidate_id = f"cand_{uuid.uuid4().hex[:12]}"
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO skill_candidates (candidate_id, name, repo,"
                " failure_family, source_lesson_ids, episode_count,"
                " instructions, anti_pattern, verification_json, status,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed',"
                " ?)",
                (candidate_id,
                 name or f"{failure_family}-playbook",
                 repo, failure_family,
                 json.dumps([ls["lesson_id"] for ls in lessons]),
                 len(lessons), instructions, anti_pattern or None,
                 json.dumps({"basis": "verified_lessons",
                             "lesson_ids": len(lessons)}),
                 _now()))
            conn.execute("COMMIT")
            return get_candidate(candidate_id, db_path)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return None


def get_candidate(candidate_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM skill_candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def list_candidates(status=None, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM skill_candidates WHERE status = ?"
                    " ORDER BY created_at DESC", (status,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM skill_candidates ORDER BY created_at"
                    " DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def accept_skill_candidate(candidate_id, actor="operator", apply=False,
                           db_path=None, index_path=None, bodies_dir=None):
    """Mark a candidate accepted; with apply=True AND actor="operator",
    also write index metadata + a body file. SKILLS.md is never touched.
    index_path/bodies_dir override the repo locations (tests, alternate
    installs). Returns the candidate dict (with 'applied' key) or None."""
    try:
        candidate = get_candidate(candidate_id, db_path)
        if not candidate:
            return None
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE skill_candidates SET status = 'accepted'"
                         " WHERE candidate_id = ?", (candidate_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        candidate["status"] = "accepted"
        candidate["applied"] = False
        if apply and actor == "operator":
            _apply_to_index(candidate, index_path=index_path,
                            bodies_dir=bodies_dir)
            candidate["applied"] = True
        return candidate
    except Exception:
        return None


def _apply_to_index(candidate, index_path=None, bodies_dir=None):
    """Index metadata + body file under skills/bodies/. Never SKILLS.md."""
    name = candidate["name"]
    body_rel = f"skills/bodies/{name}.md"
    bodies = Path(bodies_dir) if bodies_dir else BODIES_DIR
    bodies.mkdir(parents=True, exist_ok=True)
    body_path = bodies / f"{name}.md"
    if not body_path.exists():
        lines = [f"# {name}", "",
                 f"Distilled from {candidate['episode_count']} verified"
                 f" lessons ({candidate['failure_family']} family).", "",
                 candidate["instructions"]]
        if candidate.get("anti_pattern"):
            lines += ["", "## Never", "",
                      f"- {candidate['anti_pattern']}"]
        body_path.write_text("\n".join(lines) + "\n")
    index_file = Path(index_path) if index_path else INDEX_PATH
    index = []
    try:
        index = json.loads(index_file.read_text())
        if not isinstance(index, list):
            index = []
    except (OSError, ValueError):
        index = []
    if any(e.get("name") == name for e in index):
        return
    index.append({
        "name": name,
        "description": (f"Distilled playbook for {candidate['failure_family']}"
                        " failures."),
        "triggers": [candidate["failure_family"]],
        "risk_level": "medium",
        "body": body_rel,
        "preconditions": ["repo_version_known"],
        "verification": ["direct_tests"],
    })
    index_file.write_text(json.dumps(index, indent=2) + "\n")
