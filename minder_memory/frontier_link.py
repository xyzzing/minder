"""Join a verified episode close back to its frontier consult (issue #10).

P4.1/P4.2 built the governed consult record, its classification and the
candidate distiller, and nothing in the runtime called them: the panel wrote
legacy traces and `frontier_evals` stayed empty, so `helpfulness` was always
NULL, `may_distill` never passed, and the candidate lesson queue never moved.

This module is that missing join, and it claims exactly one thing: when
issue #9's producer closes an episode `verified` on a clean test run, the
consult recorded against that same episode is labelled `pass`, and its
distilled actions become one candidate lesson.

Acceptance evidence, stated plainly. `classify_consult` labels `helpful`
only from `pass` plus a non-empty accepted set. Nothing here observes that an
agent followed the advice. The accepted set is the consult's own distilled
actions, taken as accepted because the episode they were about went on to
close verified — a structural trigger on the episode ledger, the same kind of
evidence the rest of minder accepts, not a judgment of the advice's content.
That is why the output is a candidate: inert for retrieval, invisible to
`retrieve_lessons`, and promotable only by an operator through
`lessons.promote_lesson`, which still demands tests evidence.

Never raises. Every failure costs only the join.
"""
import json

from . import db as _db
from . import frontier_distill
from . import frontier_policy
from . import frontier_traces

# A consult is distillable only while unclassified. `classified_at` is set by
# classify_consult, so a labelled trace is already decided and never revised
# by a later close: one consult, one label, one lesson at most.
_OPEN_CONSULTS_SQL = (
    "SELECT t.trace_id AS trace_id FROM frontier_traces t"
    " LEFT JOIN frontier_evals e ON e.trace_id = t.trace_id"
    " WHERE t.episode_id = ?"
    " AND (e.trace_id IS NULL OR e.classified_at IS NULL)"
    " ORDER BY t.ts, t.trace_id")


def link_verified_episode(episode_id, db_path=None):
    """Classify and distill the consults of a freshly verified episode.
    Returns {traces, lessons}; never raises."""
    out = {"traces": 0, "lessons": 0}
    try:
        trace_ids = _open_consults(episode_id, db_path)
        for trace_id in trace_ids:
            trace = frontier_traces.get_consult(trace_id, db_path=db_path)
            if not trace:
                continue
            actions = _distilled_actions(trace)
            if not actions:
                # Nothing to accept and nothing to build an instruction
                # from: leave the consult unclassified rather than
                # inventing a label for it.
                continue
            frontier_traces.classify_consult(
                trace_id, "pass", accepted=actions, db_path=db_path)
            out["traces"] += 1
            fresh = frontier_traces.get_consult(trace_id, db_path=db_path)
            if not frontier_policy.may_distill(fresh, episode_status="verified"):
                continue
            lesson_id = frontier_distill.distill_lesson_from_consult(
                trace_id, episode_id, db_path=db_path)
            if lesson_id:
                out["lessons"] += 1
    except Exception:
        return out
    return out


def _open_consults(episode_id, db_path):
    """Traces recorded for this episode that no close has labelled yet,
    oldest first. An episode-less consult is never adopted: it is not
    evidence about this episode."""
    try:
        conn = _db.connect(db_path)
        try:
            rows = conn.execute(_OPEN_CONSULTS_SQL, (episode_id,)).fetchall()
            return [r["trace_id"] for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _distilled_actions(trace):
    """The consult's own distilled actions, from the governed row only."""
    raw = trace.get("distilled_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(a) for a in (raw or []) if str(a).strip()]
