"""Read side of the injection ledger (issue #13, read surface #20).

`minder_op/queries.py` is the sanctioned sqlite read surface; this module
is its ledger section, so the ledger's three reads live together and the
catch-all queries module stays inside C2. It is a separate module rather
than a section because the ledger has its own degradation rule: a store
predating migration 015 has no table, and every read here has to say
`not available` instead of a zero that reads as "nothing was injected".

Read-only, like everything in this package. The write side is
`minder_memory/injections.py`.
"""
from minder_op.errors import DBError
from minder_op.queries import _count, _one, _rows


def lesson_injections(path, lesson_id, limit=20):
    """Injection ledger for one lesson (issue #13): the newest decisions
    that put this lesson in front of an agent."""
    return _rows(path,
                 "SELECT injection_id, ts, session_id, failure_key, repo,"
                 " tier, digest_injected, chars_injected, assist_mode"
                 " FROM learning_injections WHERE lesson_id = ?"
                 " ORDER BY ts DESC LIMIT ?", (lesson_id, int(limit)))


def injection_section(db_path, win):
    """Injection-ledger counts for one window (issue #13, rate #20).

    `injected` alone is a vanity number; `missed` is the denominator that
    makes it mean something, and `unused_verified` names a lesson an
    operator promoted that never reached an agent. A store predating
    migration 015 degrades to available False, never to zeros."""
    start, until = win
    try:
        rows = _rows(db_path,
                     "SELECT SUM(CASE WHEN lesson_id IS NOT NULL THEN 1"
                     " ELSE 0 END) AS injected,"
                     " SUM(CASE WHEN lesson_id IS NULL THEN 1 ELSE 0 END)"
                     " AS missed,"
                     " SUM(CASE WHEN lesson_id IS NOT NULL AND ts >= ?"
                     " AND ts < ? THEN 1 ELSE 0 END) AS injected_in_window"
                     " FROM learning_injections", (start, until))
        row = rows[0] if rows else {}
        injected = int(row.get("injected") or 0)
        missed = int(row.get("missed") or 0)
        unused = _count(
            db_path, "SELECT COUNT(*) AS n FROM lessons l WHERE"
            " l.status = 'verified' AND l.valid_to IS NULL AND NOT EXISTS"
            " (SELECT 1 FROM learning_injections i"
            " WHERE i.lesson_id = l.lesson_id)", ())
        return {"available": True,
                "injected_total": injected,
                "injected_in_window": int(row.get("injected_in_window") or 0),
                "missed_total": missed,
                "unused_verified": int(unused),
                # Issue #20: the plane-level rate #16's per-lesson impact
                # split needs a base rate to be read against. `asked` is
                # every decision row, so the misses are named rather than
                # inferred, and an empty ledger has no rate at all.
                "asked_total": injected + missed,
                "hit_rate": (round(injected / (injected + missed), 4)
                             if injected + missed else None)}
    except DBError:  # pre-015 store: degrade, don't crash
        return {"available": False, "injected_total": None,
                "injected_in_window": None, "missed_total": None,
                "unused_verified": None}


def retrieval_hit_rate(path):
    """The plane-level retrieval question, in the ledger's own words: of
    the injection decisions that asked for a lesson, how many got one
    (issue #20).

    The miss count is a named number, not something a reader subtracts
    from a total - the same reason #13 put the nothing rows in the table.
    `asked` counts every decision row, hits are the rows that carried a
    lesson, and `rate` is None whenever there is nothing to divide by, so
    an empty ledger is not reported as a 0 % hit rate.

    A store predating migration 015 has no table at all: `available` is
    False and every number is None. That is the difference between "this
    store never retrieved" and "this store cannot say", and a zero is the
    wrong answer to the second one."""
    try:
        row = _one(path,
                   "SELECT COUNT(*) AS asked,"
                   " SUM(CASE WHEN lesson_id IS NOT NULL THEN 1 ELSE 0 END)"
                   " AS hits FROM learning_injections")
    except DBError as exc:
        # Only a missing table degrades to "not available"; a genuinely
        # unreadable store stays the caller's error to report.
        if "missing an object" not in str(exc):
            raise
        return {"available": False, "asked": None, "hits": None,
                "misses": None, "rate": None}
    asked = int(row["asked"] or 0)
    hits = int(row["hits"] or 0)
    return {"available": True, "asked": asked, "hits": hits,
            "misses": asked - hits,
            "rate": round(hits / asked, 3) if asked else None}
