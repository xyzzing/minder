"""Lesson invalidation and supersession
(docs/minder-phase-2-3-frontier-coding.md P3.3/P3.5).

v1 commit comparison is deliberately not git ancestry: exact string
inequality plus an explicit invalidation event. History stays in the
graph (CONTRADICTS / SUPERSEDES edges remain queryable). Every call is
fail-open.
"""
import hashlib

from . import db as _db
from . import graph
from .store import _now


def supersede_lesson(old_id, new_id, db_path=None):
    """new supersedes old: old gets valid_to + invalidated, plus a
    SUPERSEDES edge (new -> old) so history stays queryable."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE lessons SET valid_to = ?, status = 'invalidated'"
                " WHERE lesson_id = ?", (_now(), old_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        graph.upsert_node("Lesson", old_id, {}, db_path=db_path)
        graph.upsert_node("Lesson", new_id, {}, db_path=db_path)
        graph.link(new_id, "SUPERSEDES", old_id, db_path=db_path)
        return True
    except Exception:
        return False


def invalidate_lessons_for_change(change_event, db_path=None):
    """change_event: {repo, paths[], commit, reason}.

    Invalidates verified lessons whose failure signature AFFECTS any of
    the changed paths and marks the commit node changed. Returns the
    number of lessons invalidated (0 on any failure)."""
    try:
        repo = change_event.get("repo")
        paths = [p for p in (change_event.get("paths") or []) if p]
        commit = change_event.get("commit")
        reason = str(change_event.get("reason") or "change")
        if not repo or not paths:
            return 0
        conn = _db.connect(db_path)
        invalidated = 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = _now()
            for path in paths:
                file_id = f"file:{path}"
                keys = []
                for edge in graph.neighbors(file_id, "AFFECTS", "in",
                                            db_path=db_path):
                    node_id = edge["id"]
                    if node_id.startswith("sig:"):
                        keys.append(node_id[4:])
                for key in keys:
                    rows = conn.execute(
                        "SELECT lesson_id FROM lessons WHERE repo = ?"
                        " AND failure_key = ? AND status = 'verified'"
                        " AND valid_to IS NULL", (repo, key)).fetchall()
                    for row in rows:
                        conn.execute(
                            "UPDATE lessons SET valid_to = ?,"
                            " status = 'invalidated' WHERE lesson_id = ?",
                            (now, row["lesson_id"]))
                        invalidated += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        # mark the commit node changed (graph side, best-effort)
        if commit:
            graph.upsert_node("Commit", f"commit:{commit}",
                              {"changed": True, "reason": reason},
                              db_path=db_path)
        return invalidated
    except Exception:
        return 0


def mark_contradiction(lesson_a, lesson_b, reason="divergent_verified_"
                                                     "instructions",
                       db_path=None):
    """Two verified lessons, same repo+failure_key, different instruction
    hashes: link CONTRADICTS and mark the older one needs_revalidation so
    retrieval serves at most one authoritative lesson."""
    try:
        conn = _db.connect(db_path)
        try:
            a = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                             (lesson_a,)).fetchone()
            b = conn.execute("SELECT * FROM lessons WHERE lesson_id = ?",
                             (lesson_b,)).fetchone()
        finally:
            conn.close()
        if not a or not b:
            return False
        if (a["repo"] or "") != (b["repo"] or "") or \
                (a["failure_key"] or "") != (b["failure_key"] or ""):
            return False
        ha = hashlib.sha256((a["instruction"] or "").encode()).hexdigest()
        hb = hashlib.sha256((b["instruction"] or "").encode()).hexdigest()
        if ha == hb:
            return False  # identical instructions are not a contradiction
        older, newer = (a, b) if (a["valid_from"] or "") <= \
            (b["valid_from"] or "") else (b, a)
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE lessons SET status = 'needs_revalidation'"
                         " WHERE lesson_id = ?", (older["lesson_id"],))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        graph.upsert_node("Lesson", newer["lesson_id"], {}, db_path=db_path)
        graph.upsert_node("Lesson", older["lesson_id"], {}, db_path=db_path)
        graph.link(newer["lesson_id"], "CONTRADICTS", older["lesson_id"],
                   properties={"reason": str(reason)}, db_path=db_path)
        return True
    except Exception:
        return False
