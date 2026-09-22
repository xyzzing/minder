"""Offline training export (docs/minder-phase-4-7-frontier-coding.md P7.1).

Produces a DATASET, not a deployed adapter. A verified lesson/episode pair
becomes a training candidate only when every gate holds:

1. episode verified, tests_passed evidence (the lesson's verification);
2. lesson verified, not invalidated (status/valid_to), not superseded
   (no incoming SUPERSEDES graph edge);
3. the failure family has >= min_family_count verified episodes, or
   force=True (operator escape hatch, tests only);
4. the trajectory is a compact inspect -> hypothesis -> patch -> tests
   walk, every field redacted via canonicalise.redact;
5. no frontier raw response anywhere — accepted_actions may appear;
6. harmful frontier consults exclude the episode;
7. environment/permissions-only families are excluded.

Output: rows in training_candidates (migration 009) plus JSONL under
memory/exports/ (gitignored; pass out_dir in tests). The held-out split is
deterministic (hash of candidate_id) with at least one held-out candidate
whenever any exist. This module never calls llama-server and never writes
GGUF or systemd units.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import db as _db
from . import graph
from . import store
from .canonicalise import redact

ENV_ONLY_FAMILIES = ("permissionerror", "permissions", "environment",
                     "connectionrefusederror", "filenotfounderror",
                     "timeouterror")

DETAIL_CAP = 400
MAX_STEPS = 8
HELD_OUT_MODULUS = 5  # ~20% of candidates


def export_training_candidates(db_path=None, min_family_count=5,
                               force=False, out_dir=None):
    """Returns a list of candidate dicts (also persisted and written as
    JSONL). Never raises; any problem yields an empty/partial export."""
    try:
        candidates = []
        for row in _verified_pairs(db_path):
            cand = _build_candidate(row, db_path, min_family_count, force)
            if cand:
                candidates.append(cand)
        _assign_splits(candidates)
        _persist(candidates, db_path)
        path = _write_jsonl(candidates, out_dir)
        for cand in candidates:
            cand["export_path"] = str(path)
        return candidates
    except Exception:
        return []


def _now():
    return datetime.now(timezone.utc).isoformat()


def _verified_pairs(db_path):
    conn = _db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT l.lesson_id, l.failure_key, l.instruction,"
            " l.verification_json, l.source_episode AS episode_id, l.repo"
            " FROM lessons l JOIN episodes e"
            " ON e.episode_id = l.source_episode"
            " WHERE l.status = 'verified' AND l.valid_to IS NULL"
            " AND e.status = 'verified' ORDER BY l.lesson_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _family_of(failure_key):
    parts = str(failure_key or "").split("|")
    return parts[1] if len(parts) >= 3 else None


def _build_candidate(row, db_path, min_family_count, force):
    try:
        verification = json.loads(row.get("verification_json") or "{}")
    except ValueError:
        return None
    if not verification.get("tests_passed"):
        return None
    family = _family_of(row.get("failure_key"))
    if not family or family in ENV_ONLY_FAMILIES:
        return None
    if _superseded(row["lesson_id"], db_path):
        return None
    if _has_harmful_consult(row["episode_id"], db_path):
        return None
    if not force and _family_count(family, db_path) < min_family_count:
        return None
    trajectory = _trajectory(row, db_path)
    if not trajectory:
        return None
    candidate_id = "tc_" + hashlib.sha256(
        f"{row['episode_id']}|{row['lesson_id']}".encode()).hexdigest()[:12]
    trace_id = _decision_trace_id(row["failure_key"], db_path)
    return {"candidate_id": candidate_id,
            "episode_id": row["episode_id"],
            "lesson_id": row["lesson_id"],
            "failure_family": family,
            "trajectory": trajectory,
            "trajectory_json": json.dumps(
                {"steps": trajectory, "decision_trace_id": trace_id}),
            "decision_trace_id": trace_id,
            "split": "train",
            "created_at": _now()}


def _decision_trace_id(failure_key, db_path):
    """Phase 7 additive: link the latest DecisionTrace for this failure
    key when one exists. A trace NEVER rescues an unverified episode —
    every other gate has already run by the time this is called."""
    try:
        from decision import trace as dtrace
        row = dtrace.latest_for_failure_key(failure_key, db_path=db_path)
        return row.get("id") if row else None
    except Exception:
        return None


def _superseded(lesson_id, db_path):
    try:
        incoming = graph.neighbors(lesson_id, edge_type="SUPERSEDES",
                                   direction="in", db_path=db_path)
        return bool(incoming)
    except Exception:
        return False  # status/valid_to filters already exclude the normal path


def _has_harmful_consult(episode_id, db_path):
    conn = _db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM frontier_traces ft"
            " JOIN frontier_evals fe ON fe.trace_id = ft.trace_id"
            " WHERE ft.episode_id = ? AND fe.helpfulness = 'harmful'"
            " LIMIT 1", (episode_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _family_count(family, db_path):
    conn = _db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT e.episode_id) AS n FROM episodes e"
            " JOIN episode_events ee ON ee.episode_id = e.episode_id"
            " JOIN events ev ON ev.event_id = ee.event_id"
            " WHERE e.status = 'verified' AND ev.failure_key LIKE ?",
            (f"%|{family}|%",)).fetchone()
        return row["n"]
    finally:
        conn.close()


def _cap(text):
    return redact(str(text or ""))[:DETAIL_CAP]


def _trajectory(row, db_path):
    """inspect -> hypothesis -> patch -> tests, redacted and compact. Raw
    frontier responses have no column anywhere and are never read here;
    accepted frontier actions may appear as an extra step."""
    events = store.episode_events(row["episode_id"], db_path=db_path)
    failures = [e for e in events if e.get("event_type") == "tool_failure"]
    if not failures:
        return None
    successes = [e for e in events if e.get("event_type") == "tool_success"]
    steps = [
        {"step": "inspect",
         "detail": _cap(failures[0].get("error_excerpt")
                        or failures[0].get("failure_key"))},
        {"step": "hypothesis", "detail": _cap(row.get("instruction"))},
        {"step": "patch",
         "detail": _cap(_action_summary(failures[-1]))},
        {"step": "tests",
         "detail": _cap(f"tests passed after fix ({successes[0]['failure_key']})"
                        if successes else
                        "tests passed after fix, verified locally")},
    ]
    for action in _accepted_actions(row["episode_id"], db_path):
        if len(steps) >= MAX_STEPS:
            break
        steps.append({"step": "accepted_action", "detail": _cap(action)})
    return steps


def _action_summary(event):
    from .canonicalise import canonical_action
    canon = canonical_action(event, event.get("repo"))
    bits = [canon.get("tool")]
    if canon.get("command"):
        bits.append(canon["command"])
    if canon.get("file_path"):
        bits.append(str(canon["file_path"]))
    return " ".join(b for b in bits if b)


def _accepted_actions(episode_id, db_path):
    conn = _db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT fe.accepted_actions_json AS actions"
            " FROM frontier_evals fe JOIN frontier_traces ft"
            " ON ft.trace_id = fe.trace_id"
            " WHERE ft.episode_id = ? AND fe.accepted_actions_json IS NOT NULL"
            " ORDER BY fe.classified_at LIMIT 1", (episode_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["actions"]:
        return []
    try:
        actions = json.loads(row["actions"])
        return [str(a) for a in actions] if isinstance(actions, list) else []
    except ValueError:
        return []


def _assign_splits(candidates):
    for cand in candidates:
        digest = int(hashlib.sha256(
            cand["candidate_id"].encode()).hexdigest(), 16)
        cand["split"] = "held_out" if digest % HELD_OUT_MODULUS == 0 \
            else "train"
    if candidates and not any(c["split"] == "held_out"
                              for c in candidates):
        candidates[-1]["split"] = "held_out"


def _persist(candidates, db_path):
    if not candidates:
        return
    conn = _db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for cand in candidates:
            conn.execute(
                "INSERT OR REPLACE INTO training_candidates (candidate_id,"
                " episode_id, lesson_id, failure_family, trajectory_json,"
                " split, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cand["candidate_id"], cand["episode_id"], cand["lesson_id"],
                 cand["failure_family"], cand["trajectory_json"],
                 cand["split"], cand["created_at"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def default_out_dir():
    return Path(__file__).resolve().parent / "exports"


def _write_jsonl(candidates, out_dir):
    out_dir = Path(out_dir) if out_dir else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "training_candidates.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for cand in candidates:
            record = {k: v for k, v in cand.items()
                      if k not in ("trajectory_json", "export_path")}
            handle.write(json.dumps(record, default=str) + "\n")
    return path
