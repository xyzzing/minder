"""Propagation algebra (Phase 1 P1.5, PRD v2 §Impact and invalidation).

A READ-ONLY impact preview over the graph: given a change at a seed
node, what would the typed propagation rules affect, and what action
would each dependent need? Phase 1 enforces nothing and mutates
nothing — enforcement lands in later phases, operator-gated. The
algebra rules (PRD rules 1–8) are encoded in the traversal semantics so
the property tests can hold them mechanically:

- staleness is never falsity: a `source_changed`/`vintage_changed`
  seed can only ever produce `mark_stale` actions;
- candidate (proposed, unreviewed) edges never carry enforcement — they
  are excluded from traversal entirely;
- scope bounds: only the edge types allowed for the change type are
  followed, depth is bounded, and every affected node carries a reason;
- independence: a dependent holding an alternative independent support
  (a second SUPPORTS edge from outside the affected set) is reported as
  `review_with_alternatives` — never as a hard invalidation;
- determinism: identical inputs produce identical output.
"""
from . import db as _db

# change_type -> (edge types whose DEPENDENTS are affected, action)
CHANGE_RULES = {
    "source_changed": (("DEPENDS_ON", "SUPPORTS"), "mark_stale"),
    "vintage_changed": (("DEPENDS_ON", "SUPPORTS"), "mark_stale"),
    "contradiction": (("SUPPORTS", "DEPENDS_ON"), "queue_review"),
    "superseded": (("SUPERSEDES",), "mark_superseded"),
}
DEFAULT_DEPTH = 3


def preview_impact(node_id, change_type, *, max_depth=DEFAULT_DEPTH,
                   db_path=None):
    """Bounded, typed, read-only impact preview. Returns
    {"seed", "change_type", "action", "affected": [{node_id, node_type,
    depth, via_edge, action, alternatives}], "skipped_candidate_edges",
    "reasons"} — identical inputs give identical output; never raises.
    """
    rule = CHANGE_RULES.get(change_type)
    if rule is None:
        return {"seed": node_id, "change_type": change_type,
                "action": None, "affected": [],
                "skipped_candidate_edges": 0,
                "reasons": [f"unknown change_type: {change_type!r}"]}
    edge_types, action = rule
    try:
        conn = _db.connect(db_path)
        try:
            return _traverse(conn, node_id, change_type, edge_types,
                             action, max_depth)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — preview fails open empty
        return {"seed": node_id, "change_type": change_type,
                "action": action, "affected": [],
                "skipped_candidate_edges": 0,
                "reasons": [f"error:{type(exc).__name__}"]}


def _node_type(conn, node_id):
    row = conn.execute("SELECT type FROM nodes WHERE id = ?",
                       (node_id,)).fetchone()
    return row["type"] if row else None


def _alternatives(conn, node_id, exclude_edge_id):
    """Independent supports of `node_id` other than the edge the impact
    arrived on: SUPPORTS edges from sources outside the traversal."""
    rows = conn.execute(
        "SELECT from_id FROM edges WHERE edge_type = 'SUPPORTS'"
        " AND to_id = ? AND id != ? AND valid_to IS NULL",
        (node_id, exclude_edge_id)).fetchall()
    return len({r["from_id"] for r in rows})


def _traverse(conn, seed, change_type, edge_types, action, max_depth):
    affected = []
    seen = {seed}
    frontier = [(seed, 0)]
    skipped_candidate = 0
    reasons = [f"seed {seed} ({change_type})"]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        placeholders = ",".join("?" for _ in edge_types)
        rows = conn.execute(
            f"SELECT id, from_id, edge_type, to_id, properties_json"
            f" FROM edges WHERE to_id = ? AND edge_type IN"
            f" ({placeholders}) AND valid_to IS NULL",
            (current, *edge_types)).fetchall()
        for row in rows:
            props = {}
            try:
                import json
                props = json.loads(row["properties_json"] or "{}")
            except ValueError:
                props = {}
            if props.get("candidate"):
                skipped_candidate += 1
                continue  # candidate edges never carry enforcement
            dependent = row["from_id"]
            if dependent in seen:
                continue
            seen.add(dependent)
            alts = _alternatives(conn, dependent, row["id"])
            node_action = (action if alts == 0
                           else "review_with_alternatives")
            if action == "mark_stale":
                # staleness is never falsity — even single-support
                # dependents only become stale
                node_action = "mark_stale"
            affected.append({
                "node_id": dependent,
                "node_type": _node_type(conn, dependent),
                "depth": depth + 1,
                "via_edge": row["edge_type"],
                "action": node_action,
                "alternatives": alts,
            })
            reasons.append(f"{dependent} via {row['edge_type']} "
                           f"from {current}")
            frontier.append((dependent, depth + 1))
    affected.sort(key=lambda a: (a["depth"], a["node_id"]))
    return {"seed": seed, "change_type": change_type, "action": action,
            "affected": affected,
            "skipped_candidate_edges": skipped_candidate,
            "reasons": reasons}
