"""Graph table tests (spec P3.1). SQLite only, temporal edges, fail-open."""
from memory import graph


def test_affects_link_idempotent(tmp_path):
    dbp = tmp_path / "m.sqlite"
    graph.upsert_node("FailureSignature", "sig:a", {}, db_path=dbp)
    graph.upsert_node("File", "file:a.py", {"path": "a.py"}, db_path=dbp)
    e1 = graph.link("sig:a", "AFFECTS", "file:a.py", db_path=dbp)
    e2 = graph.link("sig:a", "AFFECTS", "file:a.py", db_path=dbp)
    assert e1 and e2 and e1 == e2
    out = graph.neighbors("sig:a", "AFFECTS", db_path=dbp)
    assert len(out) == 1 and out[0]["id"] == "file:a.py"


def test_supersedes_queryable_and_temporal(tmp_path):
    dbp = tmp_path / "m.sqlite"
    for nid in ("les:a", "les:b"):
        graph.upsert_node("Lesson", nid, {}, db_path=dbp)
    edge = graph.link("les:b", "SUPERSEDES", "les:a", db_path=dbp)
    assert edge
    assert graph.neighbors("les:b", "SUPERSEDES", db_path=dbp)[0]["id"] == \
        "les:a"
    # invalidated edge: gone from traversal, still in history
    assert graph.invalidate_edge(edge, "newer lesson", db_path=dbp)
    assert graph.neighbors("les:b", "SUPERSEDES", db_path=dbp) == []
    hist = graph.neighbors("les:b", "SUPERSEDES", db_path=dbp,
                           include_invalid=True)
    assert hist and hist[0]["edge_valid_to"]


def test_io_errors_degrade_without_raising(tmp_path):
    import os
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pytest
        pytest.skip("running as root: read-only dir not read-only")
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        nowhere = ro / "db.sqlite"
        assert graph.upsert_node("File", "f", {}, db_path=nowhere) is None
        assert graph.link("a", "AFFECTS", "b", db_path=nowhere) is None
        assert graph.neighbors("a", db_path=nowhere) == []
        assert graph.invalidate_edge("nope", "r", db_path=nowhere) is False
        assert graph.get_node("f", db_path=nowhere) is None
    finally:
        ro.chmod(0o700)


def test_direction_in_and_out(tmp_path):
    dbp = tmp_path / "m.sqlite"
    graph.upsert_node("Lesson", "l1", {}, db_path=dbp)
    graph.upsert_node("Commit", "c1", {}, db_path=dbp)
    graph.link("l1", "APPLIES_TO", "c1", db_path=dbp)
    assert graph.neighbors("l1", "APPLIES_TO", "out", db_path=dbp)[0]["id"] \
        == "c1"
    assert graph.neighbors("c1", "APPLIES_TO", "in", db_path=dbp)[0]["id"] \
        == "l1"
