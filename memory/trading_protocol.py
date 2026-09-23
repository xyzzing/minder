"""Trading research protocol (Phase 1 P1.6, PRD v2 §Trading research
protocol).

Minder enforces the *protocol* deterministically; it never computes
performance statistics and never certifies profitability:

- Preregistration before verifiability: a hypothesis family must be
  registered (method digest, data sources, metric, split scheme) before
  any trial counts. Re-registration is a new version row; the old one is
  superseded — registration history is never edited.
- Complete trial manifests: trials are append-only (SQL triggers), all
  of them — failures included. A declared `expected_trials` that isn't
  met is a manifest-completeness blocker for verifiable status.
- Multiple-testing bookkeeping: each family's trial count N is exposed
  and cross-checked against any DSR/PSR-style analysis artifact citing
  N; inconsistencies are flagged, never silently accepted.
- Holdout discipline: holdout-split trials before a recorded
  holdout-unlock HumanInputEvent are flagged with the audit trail intact.
- Vintage triage: trials mixing dataset vintages are flagged at record
  time; a declared vintage change marks the family's results *stale for
  re-run triage* — nothing is deleted or auto-invalidated.
- No auto-promotion: analysis artifacts are born 'candidate' and have no
  path to 'verified' without a recorded human review decision.

Every mutating call returns (row|None, status) and fails open.
"""
import json
import uuid
from datetime import datetime, timezone

from . import db as _db
from . import task_context

SPLIT_ASSIGNMENTS = ("train", "validation", "holdout")
ANALYSIS_METHODS = ("PBO/CSCV", "DSR", "PSR", "SPA", "other")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _latest_family(conn, family_id):
    row = conn.execute(
        "SELECT * FROM hypothesis_families WHERE family_id = ?"
        " ORDER BY version DESC LIMIT 1", (family_id,)).fetchone()
    return dict(row) if row else None


def register_family(method_digest, *, planned_metric, split_scheme,
                    data_sources, universe=None, method_note=None,
                    expected_trials=None, family_id=None, actor="operator",
                    db_path=None):
    """Preregister (or re-register a new version of) a hypothesis
    family. Registration precedes any verifiable trial."""
    try:
        if not method_digest:
            return None, "rejected:method_digest required"
        if not planned_metric or not split_scheme:
            return None, "rejected:planned_metric and split_scheme required"
        sources = data_sources or []
        if not sources or not all(
                isinstance(s, dict) and s.get("digest") and s.get("as_of")
                for s in sources):
            return None, "rejected:data_sources need digest + as_of each"
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            latest = _latest_family(conn, family_id) if family_id else None
            if family_id and latest:
                version = latest["version"] + 1
            else:
                family_id = family_id or _uid("fam")
                version = 1
            now = _now()
            conn.execute(
                "INSERT INTO hypothesis_families (family_id, version,"
                " registered_at, method_digest, method_note, universe,"
                " data_sources_json, planned_metric, split_scheme,"
                " expected_trials, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered')",
                (family_id, version, now, method_digest, method_note,
                 universe, json.dumps(sources), planned_metric,
                 split_scheme, expected_trials))
            if latest:
                conn.execute(
                    "UPDATE hypothesis_families SET status = 'superseded',"
                    " superseded_by = ? WHERE family_id = ? AND version = ?",
                    (f"{family_id}:v{version}", family_id,
                     latest["version"]))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM hypothesis_families WHERE family_id = ?"
                " AND version = ?", (family_id, version)).fetchone()
            return dict(row), "registered"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def record_trial(family_id, *, config_hash, dataset_vintage,
                 split_assignment, result_summary, db_path=None):
    """Append one trial to the family manifest. Holdout-before-unlock and
    vintage mixing are flagged at record time — recorded, not refused:
    the audit trail IS the finding."""
    try:
        if split_assignment not in SPLIT_ASSIGNMENTS:
            return None, f"rejected:unknown_split:{split_assignment}"
        if not isinstance(dataset_vintage, dict) or \
                not dataset_vintage.get("digest") or \
                not dataset_vintage.get("as_of"):
            return None, "rejected:dataset_vintage needs digest + as_of"
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            family = _latest_family(conn, family_id)
            if not family or family["status"] != "registered":
                return None, "rejected:unknown_family"
            prior = conn.execute(
                "SELECT dataset_vintage FROM trial_records"
                " WHERE family_id = ? ORDER BY recorded_at",
                (family_id,)).fetchall()
            prior_digests = {json.loads(r["dataset_vintage"])["digest"]
                             for r in prior}
            flagged = None
            if split_assignment == "holdout":
                unlock = conn.execute(
                    "SELECT COUNT(*) AS n FROM human_input_events"
                    " WHERE kind = 'holdout_unlock' AND affects_json"
                    " LIKE ?", (f'%{family_id}%',)).fetchone()["n"]
                if not unlock:
                    flagged = "holdout_violation"
            if prior_digests and dataset_vintage["digest"] not in \
                    prior_digests:
                flagged = "vintage_mix"
            trial_id = _uid("tr")
            conn.execute(
                "INSERT INTO trial_records (trial_id, family_id,"
                " recorded_at, config_hash, dataset_vintage,"
                " split_assignment, result_summary_json, flagged)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (trial_id, family_id, _now(), config_hash,
                 json.dumps(dataset_vintage), split_assignment,
                 json.dumps(result_summary), flagged))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM trial_records WHERE trial_id = ?",
                (trial_id,)).fetchone()
            status = "recorded" if not flagged else f"flagged:{flagged}"
            return dict(row), status
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def unlock_holdout(family_id, *, actor, note=None, db_path=None):
    """Record the explicit human decision to unlock the holdout. The
    decision is evidence-traced; it is not a performance claim."""
    event, status = task_context.record_human_input(
        "holdout_unlock", actor=actor, authority="user",
        decision="approved",
        question=f"unlock holdout split for {family_id}?",
        affects=[{"type": "hypothesis_family", "id": family_id}],
        note=note, db_path=db_path)
    return event, status


def mark_vintage_changed(family_id, *, new_vintage, reason, actor,
                         old_digest=None, db_path=None):
    """Declare a dataset vintage change: the family's results become
    stale for re-run triage. History is never touched."""
    try:
        if not isinstance(new_vintage, dict) or \
                not new_vintage.get("digest"):
            return None, "rejected:new_vintage needs digest"
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            family = _latest_family(conn, family_id)
            if not family:
                return None, "rejected:unknown_family"
            event_id = _uid("vin")
            conn.execute(
                "INSERT INTO vintage_events (event_id, family_id, ts,"
                " old_digest, new_digest, reason, actor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, family_id, _now(), old_digest,
                 new_vintage["digest"], reason, actor))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM vintage_events WHERE event_id = ?",
                (event_id,)).fetchone()
            return dict(row), "ok"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def record_analysis(family_id, *, method, code_digest,
                    n_trials_referenced=None, trial_sharpe_variance=None,
                    verdict=None, db_path=None):
    """Store a researcher-tooling analysis artifact (PBO/DSR/PSR/SPA
    output). Always 'candidate' — no path to 'verified' without a
    recorded human review decision. N cited against the manifest is
    cross-checked; inconsistency is flagged, not hidden."""
    try:
        if method not in ANALYSIS_METHODS:
            return None, f"rejected:unknown_method:{method}"
        if not code_digest:
            return None, "rejected:code_digest required"
        conn = _db.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            family = _latest_family(conn, family_id)
            if not family:
                return None, "rejected:unknown_family"
            manifest_n = conn.execute(
                "SELECT COUNT(*) AS n FROM trial_records"
                " WHERE family_id = ?", (family_id,)).fetchone()["n"]
            flagged = None
            if n_trials_referenced is not None and \
                    n_trials_referenced != manifest_n:
                flagged = "n_inconsistent"
            analysis_id = _uid("ana")
            conn.execute(
                "INSERT INTO analysis_artifacts (analysis_id, family_id,"
                " produced_at, method, code_digest, n_trials_referenced,"
                " trial_sharpe_variance, verdict_json, flagged, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate')",
                (analysis_id, family_id, _now(), method, code_digest,
                 n_trials_referenced, trial_sharpe_variance,
                 json.dumps(verdict) if verdict else None, flagged))
            conn.execute("COMMIT")
            row = conn.execute(
                "SELECT * FROM analysis_artifacts WHERE analysis_id = ?",
                (analysis_id,)).fetchone()
            status = "recorded" if not flagged else f"flagged:{flagged}"
            return dict(row), status
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}"


def manifest_status(family_id, db_path=None):
    """Protocol status for one family: blockers, staleness, bookkeeping.
    Read-only; never raises."""
    try:
        conn = _db.connect(db_path)
        try:
            family = _latest_family(conn, family_id)
            if not family:
                return {"family_id": family_id, "registered": False,
                        "verifiable": False,
                        "blockers": ["unknown_family"]}
            trials = conn.execute(
                "SELECT trial_id, recorded_at, dataset_vintage, flagged"
                " FROM trial_records WHERE family_id = ?"
                " ORDER BY recorded_at", (family_id,)).fetchall()
            blockers = []
            expected = family["expected_trials"]
            if expected is not None and len(trials) < expected:
                blockers.append(f"manifest_incomplete: "
                                f"{len(trials)}/{expected}")
            flagged = [dict(r) for r in trials if r["flagged"]]
            if any(t["flagged"] == "holdout_violation" for t in flagged):
                blockers.append("holdout_violation")
            bad_n = conn.execute(
                "SELECT COUNT(*) AS n FROM analysis_artifacts"
                " WHERE family_id = ? AND flagged = 'n_inconsistent'",
                (family_id,)).fetchone()["n"]
            if bad_n:
                blockers.append(f"n_inconsistent x{bad_n}")
            # staleness: any vintage change newer than the last trial
            stale = False
            last_trial_at = trials[-1]["recorded_at"] if trials else None
            if last_trial_at:
                newer = conn.execute(
                    "SELECT COUNT(*) AS n FROM vintage_events"
                    " WHERE family_id = ? AND ts > ?",
                    (family_id, last_trial_at)).fetchone()["n"]
                stale = bool(newer)
            if stale:
                blockers.append("stale: vintage changed after last trial")
            return {
                "family_id": family_id,
                "registered": family["status"] == "registered",
                "version": family["version"],
                "trials": len(trials),
                "n_trials_bookkept": len(trials),
                "expected_trials": expected,
                "flagged_trials": len(flagged),
                "stale": stale,
                "stale_trials": len(trials) if stale else 0,
                "blockers": blockers,
                "verifiable": family["status"] == "registered"
                and not blockers,
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"family_id": family_id, "registered": False,
                "verifiable": False,
                "blockers": [f"error:{type(exc).__name__}"]}
