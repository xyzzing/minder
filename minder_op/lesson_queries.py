"""Read side of the lesson queue (issue #22, C2).

`minder_op/queries.py` is the sanctioned sqlite read surface; this
module is its lesson section, the same split issue #20 made for the
injection ledger. Two sections rather than one because the lesson queue
now has its own read rule: a preference-based lesson names a
`human_input_events` row instead of an episode (issue #22), and the
queue has to name that source kind without every caller joining the
tables itself.

Read-only, like everything in this package. The write side is
`minder_memory/lessons.py`.
"""
from minder_op.queries import _one, _rows


def lessons(path, status_filter=None, failure_key=None, repo=None,
            limit=50):
    # source_kind (issue #22): the closed human_input_events kind behind a
    # preference-based lesson, so the queue can name the source a
    # rejection reason code would be recorded against. NULL for an
    # episode-sourced lesson.
    sql = ("SELECT l.*, h.kind AS source_kind FROM lessons l"
           " LEFT JOIN human_input_events h"
           " ON h.event_id = l.source_human_input WHERE 1=1")
    params = []
    if status_filter:
        sql += " AND l.status = ?"
        params.append(status_filter)
    else:
        # default view mirrors retrieval semantics: live verified only -
        # candidates are inert until promoted and surface via --status
        sql += " AND l.status = 'verified' AND l.valid_to IS NULL"
    if failure_key:
        sql += " AND l.failure_key = ?"
        params.append(failure_key)
    if repo:
        sql += " AND l.repo = ?"
        params.append(repo)
    sql += " ORDER BY l.valid_from DESC LIMIT ?"
    params.append(int(limit))
    return _rows(path, sql, params)


def lesson(path, lesson_id):
    return _one(path, "SELECT * FROM lessons WHERE lesson_id = ?",
                (lesson_id,))


def human_input_event(path, event_id):
    """One human_input_events row (issue #22): the evidence a
    preference-based lesson names. Raises DBError when the store predates
    the table, so the caller says "not available" instead of "no
    source"."""
    return _one(path, "SELECT * FROM human_input_events WHERE event_id = ?",
                (event_id,))


def lesson_decisions(path, lesson_id, limit=20):
    """Decision ledger for one lesson (issue #14): the closed reason code
    and note behind every invalidate / reject / adopt."""
    return _rows(path,
                 "SELECT decision_id, ts, action, code, note, actor"
                 " FROM lesson_decisions WHERE lesson_id = ?"
                 " ORDER BY ts DESC LIMIT ?", (lesson_id, int(limit)))


def decision_counts(path):
    """Decisions per (action, code), straight off the ledger (issue #14).
    The whole point of the taxonomy: these counts come from stored rows,
    never from parsing a free-text reason."""
    return _rows(path,
                 "SELECT action, code, COUNT(*) AS n FROM lesson_decisions"
                 " GROUP BY action, code ORDER BY action, code")
