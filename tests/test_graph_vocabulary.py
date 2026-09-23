"""Graph type-vocabulary tests (Phase 1 P1.4): node/edge types were free
text; they are now a closed, versioned vocabulary enforced at the API
layer. Unknown types raise ValueError *before* any DB work (the module's
fail-open return-None behaviour would hide typos); all pre-existing
call sites use vocabulary types and must stay green."""
import pytest

from memory import db as _db, graph
from memory import lessons as memory_lessons, store


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def test_unknown_edge_type_raises_before_db(tmp_path):
    dbp = _mig(tmp_path)
    with pytest.raises(ValueError):
        graph.link("a", "RELATED_TO", "b", db_path=dbp)
    with pytest.raises(ValueError):
        graph.link("a", "affects", "b", db_path=dbp)  # case-sensitive
    with pytest.raises(ValueError):
        graph.upsert_node("Thought", "n1", db_path=dbp)
    # nothing was written
    conn = _db.connect(dbp)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
    finally:
        conn.close()


def test_vocabulary_types_are_versioned_and_complete(tmp_path):
    dbp = _mig(tmp_path)
    # every type the shipped projections use is in the vocabulary
    for edge in ("AFFECTS", "FAILED_TEST", "DERIVED_FROM", "VERIFIED_BY",
                 "MODIFIED", "APPLIES_TO", "SUPERSEDES", "CONTRADICTS",
                 "RAN", "HAS_FAILURE"):
        assert edge in graph.EDGE_TYPES_V1
    for node in ("File", "Test", "Commit", "FailureSignature", "Episode",
                 "Lesson", "Patch", "VerificationRun"):
        assert node in graph.NODE_TYPES_V1
    assert graph.link("n1", "AFFECTS", "n2", db_path=dbp)
    assert graph.upsert_node("Lesson", "les_x", db_path=dbp)


def test_existing_projections_still_flow(tmp_path):
    """The seed_lesson flow exercises both projection sites (episode
    close + lesson promotion) through the validated API."""
    from test_op_list import seed_lesson
    dbp = _mig(tmp_path)
    ep_id, lesson_id = seed_lesson(dbp)
    conn = _db.connect(dbp)
    try:
        types = {r["edge_type"] for r in conn.execute(
            "SELECT DISTINCT edge_type FROM edges").fetchall()}
        assert types <= set(graph.EDGE_TYPES_V1)
        assert types  # the projection wrote real edges
        ntypes = {r["type"] for r in conn.execute(
            "SELECT DISTINCT type FROM nodes").fetchall()}
        assert ntypes <= set(graph.NODE_TYPES_V1)
    finally:
        conn.close()
