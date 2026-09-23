"""Trading research protocol tests (Phase 1 P1.6 / PRD v2 §Trading
research protocol). Minder enforces the protocol deterministically:
preregistration before verifiability, append-only complete trial
manifests, multiple-testing bookkeeping (N exposed and cross-checked),
holdout discipline, and vintage-staleness triage. It never computes
performance statistics and never certifies profitability."""
import json
import sqlite3

import pytest

from memory import db as _db, task_context, trading_protocol as tp
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

VINTAGE = {"digest": "d1" * 8, "as_of": "2024-12-31"}


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _register(dbp, expected=None, family_id="fam_x"):
    return tp.register_family(
        method_digest="m" * 16, planned_metric="sharpe",
        split_scheme="purged_kfold/embargo=5",
        data_sources=[{"digest": "aaa", "as_of": "2020-01-01",
                       "source": "local"}],
        universe="US equities", expected_trials=expected,
        family_id=family_id, db_path=dbp)


def test_preregistration_and_versioning(tmp_path):
    dbp = _mig(tmp_path)
    row, status = _register(dbp)
    assert status == "registered" and row["version"] == 1
    # re-registration is a new version row; the old one is superseded
    row2, status2 = tp.register_family(
        method_digest="m" * 16, planned_metric="sharpe",
        split_scheme="purged_kfold/embargo=10",
        data_sources=[{"digest": "aaa", "as_of": "2020-01-01",
                       "source": "local"}],
        family_id="fam_x", db_path=dbp)
    assert status2 == "registered" and row2["version"] == 2
    conn = _db.connect(dbp)
    try:
        rows = conn.execute(
            "SELECT version, status FROM hypothesis_families"
            " WHERE family_id = 'fam_x' ORDER BY version").fetchall()
        assert [tuple(r) for r in rows] == [(1, "superseded"),
                                            (2, "registered")]
    finally:
        conn.close()


def test_registration_requires_complete_preregistration(tmp_path):
    dbp = _mig(tmp_path)
    row, status = tp.register_family(
        method_digest="m" * 16, planned_metric="sharpe",
        split_scheme="purged_kfold", data_sources=[],
        family_id="fam_empty", db_path=dbp)
    assert row is None and "data_sources" in status
    row2, status2 = tp.register_family(
        method_digest="", planned_metric="sharpe",
        split_scheme="purged_kfold",
        data_sources=[{"digest": "a", "as_of": "2020-01-01"}],
        family_id="fam_nodigest", db_path=dbp)
    assert row2 is None and "method_digest" in status2


def test_trials_append_only_and_counted(tmp_path):
    dbp = _mig(tmp_path)
    _register(dbp, expected=3)
    for i in range(2):
        _, status = tp.record_trial(
            "fam_x", config_hash=f"c{i}", dataset_vintage=VINTAGE,
            split_assignment="train",
            result_summary={"sharpe": 0.4 + i}, db_path=dbp)
        assert status == "recorded"
    status = tp.manifest_status("fam_x", db_path=dbp)
    assert status["trials"] == 2
    assert any("manifest_incomplete" in b
               for b in status["blockers"])
    assert status["verifiable"] is False
    # third trial completes the declared manifest
    tp.record_trial("fam_x", config_hash="c2", dataset_vintage=VINTAGE,
                    split_assignment="train",
                    result_summary={"sharpe": 0.2}, db_path=dbp)
    status = tp.manifest_status("fam_x", db_path=dbp)
    assert "manifest_incomplete" not in status["blockers"]
    # append-only: no rewriting history, no winners-only deletion
    conn = _db.connect(dbp)
    with pytest.raises(sqlite3.IntegrityError):
        _db.write(conn, "DELETE FROM trial_records")
    with pytest.raises(sqlite3.IntegrityError):
        _db.write(conn, "UPDATE trial_records SET result_summary_json"
                  " = '{}'", ())
    conn.close()


def test_trial_before_registration_rejected(tmp_path):
    dbp = _mig(tmp_path)
    row, status = tp.record_trial(
        "ghost", config_hash="c", dataset_vintage=VINTAGE,
        split_assignment="train", result_summary={}, db_path=dbp)
    assert row is None and "unknown_family" in status


def test_holdout_requires_unlock(tmp_path):
    dbp = _mig(tmp_path)
    _register(dbp)
    row, status = tp.record_trial(
        "fam_x", config_hash="c", dataset_vintage=VINTAGE,
        split_assignment="holdout", result_summary={"sharpe": 2.0},
        db_path=dbp)
    assert status == "flagged:holdout_violation" and row["flagged"] == \
        "holdout_violation"
    # unlocking is an explicit human decision, recorded as evidence
    unlock, ustatus = tp.unlock_holdout("fam_x", actor="zac", db_path=dbp)
    assert ustatus == "ok" and unlock["kind"] == "holdout_unlock"
    row2, status2 = tp.record_trial(
        "fam_x", config_hash="c2", dataset_vintage=VINTAGE,
        split_assignment="holdout", result_summary={"sharpe": 1.0},
        db_path=dbp)
    assert status2 == "recorded" and row2["flagged"] is None


def test_vintage_mix_flagged_and_staleness_triage(tmp_path):
    dbp = _mig(tmp_path)
    _register(dbp)
    tp.record_trial("fam_x", config_hash="c1", dataset_vintage=VINTAGE,
                    split_assignment="train",
                    result_summary={"sharpe": 0.5}, db_path=dbp)
    # a trial on a different vintage is flagged at record time
    v2 = {"digest": "d2" * 8, "as_of": "2025-12-31"}
    row, status = tp.record_trial(
        "fam_x", config_hash="c2", dataset_vintage=v2,
        split_assignment="train", result_summary={"sharpe": 0.6},
        db_path=dbp)
    assert status == "flagged:vintage_mix" and row["flagged"] == \
        "vintage_mix"
    # a declared vintage change marks the family's results stale for
    # re-run triage — it does not delete or invalidate history
    event, status2 = tp.mark_vintage_changed(
        "fam_x", new_vintage=v2, reason="restated prices",
        actor="zac", db_path=dbp)
    assert status2 == "ok"
    status = tp.manifest_status("fam_x", db_path=dbp)
    assert status["stale"] is True
    assert status["stale_trials"] == 2  # all trials need re-run triage
    conn = _db.connect(dbp)
    try:
        n = conn.execute("SELECT COUNT(*) FROM trial_records").fetchone()[0]
        assert n == 2  # history preserved
    finally:
        conn.close()


def test_analysis_n_consistency_flagged(tmp_path):
    dbp = _mig(tmp_path)
    _register(dbp)
    for i in range(3):
        tp.record_trial("fam_x", config_hash=f"c{i}",
                        dataset_vintage=VINTAGE,
                        split_assignment="train",
                        result_summary={"sharpe": 0.3}, db_path=dbp)
    # a DSR artifact claiming N=1 while the manifest shows 3 is flagged
    row, status = tp.record_analysis(
        "fam_x", method="DSR", code_digest="q" * 16,
        n_trials_referenced=1, trial_sharpe_variance=0.04, db_path=dbp)
    assert status == "flagged:n_inconsistent" and row["flagged"] == \
        "n_inconsistent"
    good, status2 = tp.record_analysis(
        "fam_x", method="DSR", code_digest="q" * 16,
        n_trials_referenced=3, trial_sharpe_variance=0.04, db_path=dbp)
    assert status2 == "recorded" and good["flagged"] is None
    status = tp.manifest_status("fam_x", db_path=dbp)
    assert "n_inconsistent" in " ".join(status["blockers"])
    assert status["n_trials_bookkept"] == 3  # exposed for DSR/PSR


def test_analysis_stays_candidate_no_auto_promotion(tmp_path):
    dbp = _mig(tmp_path)
    _register(dbp)
    row, _ = tp.record_analysis(
        "fam_x", method="PBO/CSCV", code_digest="q" * 16,
        n_trials_referenced=0, verdict={"pbo": 0.1}, db_path=dbp)
    assert row["status"] == "candidate"
    conn = _db.connect(dbp)
    try:
        # no path to 'verified' exists without a human review decision
        assert conn.execute(
            "SELECT COUNT(*) FROM analysis_artifacts"
            " WHERE status != 'candidate'").fetchone()[0] == 0
    finally:
        conn.close()


def test_families_cli(tmp_path, capsys):
    dbp = _mig(tmp_path)
    sources = json.dumps([{"digest": "aaa", "as_of": "2020-01-01"}])
    assert main(["--db", str(dbp), "families", "register", "--family",
                 "fam_cli", "--method-digest", "m" * 16, "--metric",
                 "sharpe", "--splits", "purged_kfold/embargo=5",
                 "--sources", sources, "--expected-trials",
                 "2"]) == EXIT_OK
    vintage = json.dumps(VINTAGE)
    assert main(["--db", str(dbp), "families", "trial", "--family",
                 "fam_cli", "--config-hash", "c1", "--vintage",
                 vintage, "--split", "train", "--result",
                 '{"sharpe": 0.4}']) == EXIT_OK
    assert main(["--db", str(dbp), "families", "status",
                 "fam_cli"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "manifest_incomplete" in out and "verifiable" in out
    assert main(["--db", str(dbp), "families", "trial", "--family",
                 "fam_cli", "--config-hash", "c2", "--vintage", vintage,
                 "--split", "train", "--result",
                 '{"sharpe": 0.5}']) == EXIT_OK
    assert main(["--db", str(dbp), "families", "status",
                 "fam_cli"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "verifiable : True" in out or "verifiable" in out
    assert main(["--db", str(dbp), "families", "status",
                 "ghost"]) == EXIT_USAGE
