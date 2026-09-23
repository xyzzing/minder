"""SQLite graph for lessons/episodes/files/tests/commits
(docs/minder-phase-2-3-frontier-coding.md P3.1). SQLite only — no graph
database service. Edges are temporal: an invalidated edge keeps its row
for history but disappears from authoritative traversal (neighbors).
Every call fails open (empty results / False / None).
"""
import json
import uuid
from datetime import datetime, timezone

from . import db as _db

# Closed, versioned type vocabularies (Phase 1 P1.4). 006's node/edge
# type columns are free text by schema; these constants are the enforced
# contract at the API layer. New types enter only here, with a version
# bump of the vocabulary.
NODE_TYPES_V1 = ("File", "Test", "Commit", "FailureSignature", "Episode",
                 "Lesson", "Skill", "Patch", "VerificationRun")
EDGE_TYPES_V1 = ("AFFECTS", "FAILED_TEST", "DERIVED_FROM", "VERIFIED_BY",
                 "MODIFIED", "APPLIES_TO", "SUPERSEDES", "CONTRADICTS",
                 "RAN", "HAS_FAILURE", "DEPENDS_ON", "SUPPORTS")


def _now():
    return datetime.now(timezone.utc).isoformat()


def upsert_node(node_type, node_id, properties=None, db_path=None):
    """Create or update a node. Returns node_id or None on failure.
    Unknown node types raise ValueError *before* any DB work — a typo
    must be loud, not silently swallowed into a free-text column."""
    if node_type not in NODE_TYPES_V1:
        raise ValueError(f"unknown node type: {node_type!r} "
                         f"(vocabulary {NODE_TYPES_V1})")
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO nodes (id, type, properties_json, created_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET"
                " type = excluded.type,"
                " properties_json = excluded.properties_json",
                (node_id, node_type,
                 json.dumps(properties or {}), _now()))
            conn.execute("COMMIT")
            return node_id
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return None


def get_node(node_id, db_path=None):
    try:
        conn = _db.connect(db_path)
        try:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?",
                               (node_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["properties"] = json.loads(out.pop("properties_json") or "{}")
            return out
        finally:
            conn.close()
    except Exception:
        return None


def link(from_id, edge_type, to_id, properties=None, valid_from=None,
         db_path=None):
    """Create an edge idempotently (unique from+type+to). Returns edge id.
    Unknown edge types raise ValueError *before* any DB work."""
    if edge_type not in EDGE_TYPES_V1:
        raise ValueError(f"unknown edge type: {edge_type!r} "
                         f"(vocabulary {EDGE_TYPES_V1})")
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO edges (id, from_id, edge_type, to_id,"
                " properties_json, created_at, valid_from, valid_to)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, NULL)"
                " ON CONFLICT(from_id, edge_type, to_id) DO NOTHING",
                (f"edge_{uuid.uuid4().hex[:12]}", from_id, edge_type, to_id,
                 json.dumps(properties or {}), _now(), valid_from or _now()))
            if cur.rowcount == 0:  # already linked — return the existing id
                row = conn.execute(
                    "SELECT id FROM edges WHERE from_id = ? AND"
                    " edge_type = ? AND to_id = ?",
                    (from_id, edge_type, to_id)).fetchone()
                conn.execute("COMMIT")
                return row["id"] if row else None
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT id FROM edges WHERE from_id = ? AND edge_type = ?"
                " AND to_id = ?", (from_id, edge_type, to_id)).fetchone()
            return row["id"] if row else None
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return None


def invalidate_edge(edge_id, reason, valid_to=None, db_path=None):
    """Mark an edge non-authoritative; the row remains for history."""
    try:
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE edges SET valid_to = ?,"
                " properties_json = json_set(properties_json, '$.reason', ?)"
                " WHERE id = ?",
                (valid_to or _now(), str(reason), edge_id))
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    except Exception:
        return False


def neighbors(node_id, edge_type=None, direction="out", db_path=None,
              include_invalid=False):
    """Adjacent nodes as [{node fields..., edge_id, edge_type}].
    Invalidated edges are excluded unless include_invalid=True."""
    try:
        conn = _db.connect(db_path)
        try:
            join_col, other_col = (
                ("e.from_id", "e.to_id") if direction == "out"
                else ("e.to_id", "e.from_id"))
            sql = (
                f"SELECT e.id AS edge_id, e.edge_type AS edge_type,"
                f" e.valid_to AS edge_valid_to, n.* FROM edges e"
                f" JOIN nodes n ON n.id = {other_col} WHERE {join_col} = ?")
            params = [node_id]
            if edge_type:
                sql += " AND e.edge_type = ?"
                params.append(edge_type)
            if not include_invalid:
                sql += " AND e.valid_to IS NULL"
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["properties"] = json.loads(d.pop("properties_json") or "{}")
                out.append(d)
            return out
        finally:
            conn.close()
    except Exception:
        return []
