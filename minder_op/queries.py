"""Read-only DB queries for the operator CLI (8A).

Labels for consults come from `frontier_evals` (migration 007) — the
frontier_traces INTEGER helpfulness column (003) is legacy and is never
read as a classified label. Every function raises DBError on missing or
corrupt storage; the CLI maps that to exit code 2.
"""
import json
import sqlite3
from pathlib import Path

from memory import db as _db

UNCLASSIFIED = "(unclassified)"


class DBError(Exception):
    """Missing or unreadable/corrupt DB."""


def resolve_path(path_arg):
    """--db override or the standard memory location."""
    if path_arg:
        return Path(path_arg)
    try:
        return Path(_db.db_path())
    except Exception as exc:  # noqa: BLE001
        raise DBError(f"cannot resolve memory db: {exc}") from exc


def _connect(path):
    if not Path(path).exists():
        raise DBError(f"memory db not found: {path}")
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise DBError(f"cannot open {path}: {exc}") from exc


def _rows(path, sql, params=()):
    conn = _connect(path)
    try:
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.DatabaseError as exc:
            raise DBError(f"db corrupt or wrong schema: {exc}") from exc
        finally:
            conn.close()
    except DBError:
        raise
    except sqlite3.Error as exc:
        raise DBError(str(exc)) from exc


def _one(path, sql, params=()):
    rows = _rows(path, sql, params)
    return rows[0] if rows else None


def schema_version(path):
    row = _one(path, "PRAGMA user_version")
    return int(row["user_version"]) if row else 0


def status(path):
    """Everything the status screen shows, in one pass."""
    def count(sql, params=()):
        row = _one(path, sql, params)
        return row["n"] if row else 0

    by_helpfulness = {row["label"]: row["n"] for row in _rows(
        path,
        "SELECT COALESCE(e.helpfulness, ?) AS label, COUNT(*) AS n"
        " FROM frontier_traces t LEFT JOIN frontier_evals e"
        " ON e.trace_id = t.trace_id GROUP BY label"
        " ORDER BY n DESC", (UNCLASSIFIED,))}
    by_helpfulness.setdefault(UNCLASSIFIED, 0)
    return {
        "schema_version": schema_version(path),
        "episodes": count("SELECT COUNT(*) AS n FROM episodes"),
        "lessons_verified": count(
            "SELECT COUNT(*) AS n FROM lessons"
            " WHERE status = 'verified' AND valid_to IS NULL"),
        "lessons_candidate": count(
            "SELECT COUNT(*) AS n FROM lessons WHERE status = 'candidate'"),
        "lessons_invalidated": count(
            "SELECT COUNT(*) AS n FROM lessons WHERE status = 'invalidated'"),
        "gaps_open": count(
            "SELECT COUNT(*) AS n FROM skill_gaps WHERE status = 'open'"),
        "consults_by_helpfulness": by_helpfulness,
        "classifier_shadow_rows": count(
            "SELECT COUNT(*) AS n FROM classifier_shadow"),
        "decision_traces_rows": count(
            "SELECT COUNT(*) AS n FROM decision_traces"),
    }


def episodes(path, repo=None, status_filter=None, limit=25):
    sql = "SELECT * FROM episodes WHERE 1=1"
    params = []
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    sql += " ORDER BY opened_at DESC LIMIT ?"
    params.append(int(limit))
    return _rows(path, sql, params)


def episode(path, episode_id):
    return _one(path, "SELECT * FROM episodes WHERE episode_id = ?",
                (episode_id,))


def episode_events(path, episode_id):
    return _rows(path,
                 "SELECT e.*, ee.seq AS seq FROM episode_events ee"
                 " JOIN events e ON e.event_id = ee.event_id"
                 " WHERE ee.episode_id = ? ORDER BY ee.seq", (episode_id,))


def lessons(path, status_filter=None, failure_key=None, repo=None,
            limit=50):
    sql = "SELECT * FROM lessons WHERE 1=1"
    params = []
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    else:
        # default view mirrors retrieval semantics: live verified only —
        # candidates are inert until promoted and surface via --status
        sql += " AND status = 'verified' AND valid_to IS NULL"
    if failure_key:
        sql += " AND failure_key = ?"
        params.append(failure_key)
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    sql += " ORDER BY valid_from DESC LIMIT ?"
    params.append(int(limit))
    return _rows(path, sql, params)


def lesson(path, lesson_id):
    return _one(path, "SELECT * FROM lessons WHERE lesson_id = ?",
                (lesson_id,))


def gaps(path, status_filter="open", limit=100):
    sql = "SELECT * FROM skill_gaps WHERE 1=1"
    params = []
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(int(limit))
    return _rows(path, sql, params)


def consults(path, limit=25):
    """Consult traces with their governed labels (frontier_evals)."""
    return _rows(path,
                 "SELECT t.trace_id, t.ts, t.failure_key,"
                 " t.redaction_profile, t.provider_fingerprint,"
                 " t.episode_id, e.helpfulness, e.verification_status"
                 " FROM frontier_traces t LEFT JOIN frontier_evals e"
                 " ON e.trace_id = t.trace_id"
                 " ORDER BY t.ts DESC LIMIT ?", (int(limit),))


def consult(path, trace_id):
    return _one(path,
                "SELECT t.*, e.helpfulness, e.verification_status,"
                " e.consult_trigger, e.distilled_json,"
                " e.accepted_actions_json, e.rejected_actions_json,"
                " e.classified_at"
                " FROM frontier_traces t LEFT JOIN frontier_evals e"
                " ON e.trace_id = t.trace_id WHERE t.trace_id = ?",
                (trace_id,))


def _load_json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return value


def decisions(path, limit=25):
    return _rows(path,
                 "SELECT id, ts, contract_id, contract_version,"
                 " session_id, failure_key, model_recommendation,"
                 " policy_decision, override, confidence, provider,"
                 " model_version FROM decision_traces"
                 " ORDER BY ts DESC LIMIT ?", (int(limit),))


def events(path, failure_key=None, event_type=None, episode_id=None,
           session_id=None, tool=None, limit=25):
    """Raw observed events, newest first. The episode column comes
    from a subquery so unlinked events still list."""
    sql = ("SELECT e.*, (SELECT ee.episode_id FROM episode_events ee"
           " WHERE ee.event_id = e.event_id ORDER BY ee.seq LIMIT 1)"
           " AS episode_id FROM events e WHERE 1=1")
    params = []
    if failure_key:
        sql += " AND e.failure_key = ?"
        params.append(failure_key)
    if event_type:
        sql += " AND e.event_type = ?"
        params.append(event_type)
    if tool:
        sql += " AND e.tool = ?"
        params.append(tool)
    if session_id:
        sql += " AND e.session_id = ?"
        params.append(session_id)
    if episode_id:
        sql += (" AND e.event_id IN (SELECT event_id FROM episode_events"
                " WHERE episode_id = ?)")
        params.append(episode_id)
    sql += " ORDER BY e.ts DESC LIMIT ?"
    params.append(int(limit))
    return _rows(path, sql, params)


def event(path, event_id):
    return _one(path,
                "SELECT e.*, (SELECT ee.episode_id FROM episode_events ee"
                " WHERE ee.event_id = e.event_id ORDER BY ee.seq LIMIT 1)"
                " AS episode_id FROM events e WHERE event_id = ?",
                (event_id,))


def last_event_ts(path):
    row = _one(path, "SELECT MAX(ts) AS last FROM events")
    return row["last"] if row else None


def session_linkage(path):
    """{task_id: {"episode_ids": [...], "event_count": n}}.

    `episodes.task_id` and `events.session_id` carry the DSH session id
    (`session-<uuid>`), which is also the session directory name — this is
    the join the console's sessions view needs, computed in one pass so a
    page render never issues per-session queries."""
    out = {}
    for row in _rows(path, "SELECT episode_id, task_id FROM episodes"
                           " WHERE task_id IS NOT NULL"
                           " ORDER BY opened_at DESC"):
        task = row["task_id"]
        entry = out.setdefault(task, {"episode_ids": [], "event_count": 0})
        entry["episode_ids"].append(row["episode_id"])
    for row in _rows(path, "SELECT session_id, COUNT(*) AS n FROM events"
                           " WHERE session_id IS NOT NULL"
                           " GROUP BY session_id"):
        entry = out.setdefault(row["session_id"],
                               {"episode_ids": [], "event_count": 0})
        entry["event_count"] = int(row["n"] or 0)
    return out


def event_types(path, limit=200):
    """Distinct event types with counts, most frequent first."""
    return _rows(path, "SELECT event_type, COUNT(*) AS n FROM events"
                       " GROUP BY event_type ORDER BY n DESC LIMIT ?",
                 (int(limit),))


def event_filter_values(path):
    """Distinct values for the /events filter controls."""
    return {
        "types": [r["event_type"] for r in
                  _rows(path, "SELECT DISTINCT event_type FROM events"
                              " ORDER BY event_type")],
        "tools": [r["tool"] for r in
                  _rows(path, "SELECT DISTINCT tool FROM events"
                              " WHERE tool IS NOT NULL AND tool <> ''"
                              " ORDER BY tool")],
        "failure_keys": [r["failure_key"] for r in
                         _rows(path, "SELECT DISTINCT failure_key FROM events"
                                     " WHERE failure_key IS NOT NULL"
                                     " ORDER BY failure_key LIMIT 100")],
    }

