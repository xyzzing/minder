"""Propagation algebra property tests (Phase 1 P1.5). The preview is
read-only; these tests hold the PRD's §Impact rules mechanically under
seeded randomized graphs (no hypothesis dependency — a fixed-seed
random DAG builder plays the fuzzer):

1. scope bounds: only allowed edge types are followed, depth respected;
2. staleness is never falsity (mark_stale only, no invalidation);
3. candidate edges never carry enforcement;
4. independence: dependents with an alternative independent support get
   review_with_alternatives under contradiction (never hard action);
5. superseded propagates only along SUPERSEDES;
6. determinism: identical inputs give identical output.
"""
import random

from memory import db as _db, graph, propagation

NODE_TYPES = ("File", "Test", "Lesson")
EDGE_TYPES = ("DEPENDS_ON", "SUPPORTS", "SUPERSEDES", "AFFECTS",
              "CONTRADICTS")


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _random_graph(dbp, seed, n_nodes=8, p_edge=0.35):
    """Random DAG: edges only from lower index to higher index, so no
    cycles; ~35% of edges flagged candidate."""
    rng = random.Random(seed)
    nodes = [f"n{i}" for i in range(n_nodes)]
    for i, node in enumerate(nodes):
        graph.upsert_node(NODE_TYPES[i % len(NODE_TYPES)], node,
                          db_path=dbp)
    edges = 0
    for i in range(n_nodes):
        for j in range(i + 1, n_nodes):
            if rng.random() < p_edge:
                edge_type = rng.choice(EDGE_TYPES)
                candidate = rng.random() < 0.35
                graph.link(nodes[i], edge_type, nodes[j],
                           properties=({"candidate": True}
                                       if candidate else None),
                           db_path=dbp)
                edges += 1
    return nodes, edges


def _affected_ids(result):
    return {a["node_id"] for a in result["affected"]}


def test_scope_bounds_and_depth(tmp_path):
    """Dependents are reached only along the change type's edge types,
    and never beyond the depth bound: build a chain whose only path is
    longer than the limit."""
    dbp = _mig(tmp_path)
    # n0 <-DEPENDS_ON- n1 <-DEPENDS_ON- n2 <-DEPENDS_ON- n3 (n3 depends on n2...)
    graph.link("n1", "DEPENDS_ON", "n0", db_path=dbp)
    graph.link("n2", "DEPENDS_ON", "n1", db_path=dbp)
    graph.link("n3", "DEPENDS_ON", "n2", db_path=dbp)
    shallow = propagation.preview_impact("n0", "source_changed",
                                         max_depth=1, db_path=dbp)
    assert _affected_ids(shallow) == {"n1"}
    deep = propagation.preview_impact("n0", "source_changed",
                                      max_depth=3, db_path=dbp)
    assert _affected_ids(deep) == {"n1", "n2", "n3"}
    # an AFFECTS edge is not followed for source_changed
    graph.link("x9", "AFFECTS", "n0", db_path=dbp)
    graph.upsert_node("Lesson", "x9", db_path=dbp)
    again = propagation.preview_impact("n0", "source_changed",
                                       max_depth=3, db_path=dbp)
    assert "x9" not in _affected_ids(again)


def test_staleness_is_never_falsity(tmp_path):
    """Every action under a vintage/source change is mark_stale — no
    dependent is ever invalidated by staleness, at any depth."""
    dbp = _mig(tmp_path)
    rng = random.Random(7)
    for seed in range(12):
        nodes, edges = _random_graph(dbp, seed)
        for node in nodes[:3]:
            result = propagation.preview_impact(node, "vintage_changed",
                                                db_path=dbp)
            assert result["action"] == "mark_stale"
            assert all(a["action"] == "mark_stale"
                       for a in result["affected"])


def test_candidate_edges_never_propagate(tmp_path):
    """A dependent reachable ONLY through a candidate edge is invisible
    to enforcement traversal, and the skip is reported."""
    dbp = _mig(tmp_path)
    graph.link("cand1", "DEPENDS_ON", "seed", properties={"candidate":
                                                          True},
               db_path=dbp)
    graph.link("hard1", "DEPENDS_ON", "seed", db_path=dbp)
    result = propagation.preview_impact("seed", "source_changed",
                                        db_path=dbp)
    assert _affected_ids(result) == {"hard1"}
    assert result["skipped_candidate_edges"] == 1


def test_independence_preserved_under_contradiction(tmp_path):
    """A dependent with a second independent SUPPORT is never marked
    with the hard contradiction action — it goes to review with
    alternatives; a single-support dependent gets the hard action."""
    dbp = _mig(tmp_path)
    # single: only one support (the seed)
    graph.link("single", "SUPPORTS", "seed", db_path=dbp)
    # covered: two supports — the seed and an outside independent one
    graph.link("covered", "SUPPORTS", "seed", db_path=dbp)
    graph.link("other_source", "SUPPORTS", "covered", db_path=dbp)
    result = propagation.preview_impact("seed", "contradiction",
                                        db_path=dbp)
    actions = {a["node_id"]: a for a in result["affected"]}
    assert actions["single"]["action"] == "queue_review"
    assert actions["covered"]["action"] == "review_with_alternatives"
    assert actions["covered"]["alternatives"] >= 1


def test_superseded_follows_only_supersedes(tmp_path):
    dbp = _mig(tmp_path)
    graph.link("newer", "SUPERSEDES", "old", db_path=dbp)
    graph.link("dependent", "DEPENDS_ON", "old", db_path=dbp)
    result = propagation.preview_impact("old", "superseded",
                                        db_path=dbp)
    assert _affected_ids(result) == {"newer"}


def test_determinism_and_unknown_change_type(tmp_path):
    dbp = _mig(tmp_path)
    _random_graph(dbp, seed=99)
    a = propagation.preview_impact("n0", "contradiction", db_path=dbp)
    b = propagation.preview_impact("n0", "contradiction", db_path=dbp)
    assert a == b
    unknown = propagation.preview_impact("n0", "meteor", db_path=dbp)
    assert unknown["affected"] == [] and unknown["action"] is None


def test_fuzz_invariants_hold(tmp_path):
    """Sweep seeds x change types: scope, staleness, and candidate
    invariants hold on every random graph."""
    for seed in range(20):
        dbp = _mig(tmp_path / f"s{seed}")
        nodes, _ = _random_graph(dbp, seed)
        for change in propagation.CHANGE_RULES:
            for node in nodes:
                result = propagation.preview_impact(node, change,
                                                    db_path=dbp)
                allowed = propagation.CHANGE_RULES[change][0]
                action = propagation.CHANGE_RULES[change][1]
                for a in result["affected"]:
                    assert a["via_edge"] in allowed
                    if change in ("source_changed", "vintage_changed"):
                        assert a["action"] == "mark_stale"
                    elif a["alternatives"] == 0:
                        assert a["action"] == action
                    else:
                        assert a["action"] == "review_with_alternatives"
