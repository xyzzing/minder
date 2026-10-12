"""doctor learns what the calibration promotion actually claims (#36).

The live install has a promotion-shaped hole in it already: the
specialization scale is a constant in a provider file, the router is
set to `laya`, and the ledger shows 831 abstentions. Nothing in doctor
says which scale is in force, whether a fitted one was ever pinned, or
whether the pinned one survived a reinstall.

`minder-op doctor` is where an operator looks, so the check has to
answer three things and answer them without crashing when systemd is
not reachable from the shell doctor runs in.
"""
import json
from datetime import datetime, timezone

import minder
from minder_op import doctor

CASES = "benchmarks/difficulty-core-v1/cases/difficulty_cases.json"
BUNDLE = "presets/laya-specializations/difficulty-v1"


def _mig(tmp_path):
    from minder_memory import db as _db
    db = tmp_path / "minder.sqlite"
    _db.connect(db).close()
    return db


def _record(tmp_path, scale=18.0):
    from minder_decision import calibrate
    record = calibrate.promotion_record(
        scale=scale, bundle_path=BUNDLE, cases_path=CASES,
        now=datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc))
    path = tmp_path / "promotions" / "difficulty-core-v1.json"
    calibrate.write_promotion(record, path=path)
    return record, path


def _show_run(scale=None):
    """A systemctl runner whose merged Environment carries (or does not
    carry) the promoted scale."""
    show = ("systemctl --user show " + minder.PROXY_UNIT + " -p Environment")
    value = ("Environment=MINDER_LAYA_MARGIN_SCALE=18.0\n" if scale is None
             else "Environment=MINDER_PORT=8390\n")

    def run(cmd):
        if " ".join(cmd) == show:
            return (0, value, "")
        return (0, "", "")
    return run


def _report(tmp_path, monkeypatch, *, promotion_path, run=None):
    """Run doctor with the promotion path and the systemctl runner
    pinned, so the check is judged on data rather than on this box."""
    from minder_decision import calibrate
    from minder_op import rollout
    db = _mig(tmp_path)
    monkeypatch.setattr(calibrate, "promotion_path",
                        lambda suite_id="difficulty-core-v1": promotion_path)
    if run is not None:
        monkeypatch.setattr(rollout, "_default_run", run)
    return doctor.run_checks(db, probe=False, now=1_700_000_000.0)


def _find(report, check_id):
    return next((c for c in report["checks"] if c["id"] == check_id), None)


def test_doctor_names_the_pinned_scale_and_the_effective_one(tmp_path,
                                                             monkeypatch):
    """The check is worth its line only if it states both numbers, so a
    mismatch is readable without another command."""
    assert doctor is not None
    _unused, path = _record(tmp_path)
    report = _report(tmp_path, monkeypatch, promotion_path=path,
                     run=_show_run())
    check = _find(report, "calibration")
    assert check is not None
    assert check["status"] == "ok"
    assert "18.0" in check["detail"]


def test_doctor_warns_when_the_pinned_scale_is_not_effective(tmp_path,
                                                             monkeypatch):
    """The inert-flag shape, third time: a promotion record that
    survives a reinstall while the drop-in does not."""
    _unused, path = _record(tmp_path)
    report = _report(tmp_path, monkeypatch, promotion_path=path,
                     run=_show_run(scale=False))
    check = _find(report, "calibration")
    assert check["status"] == "warn"
    assert "18.0" in check["detail"]


def test_doctor_warns_when_a_relabel_invalidates_the_pin(tmp_path,
                                                         monkeypatch):
    """The label digest is in the record so a relabel says so out loud.
    The 200 eval labels are owner-pending; a promotion quoted after a
    relabel describes a suite that no longer exists."""
    record, path = _record(tmp_path)
    tampered = json.loads(open(CASES).read())
    tampered["cases"][0]["gold"] = {"label": "routine", "score": 1}
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(tampered))
    from minder_decision import calibrate, difficulty_eval
    monkeypatch.setattr(difficulty_eval, "SUITE_ID", "tampered-suite")
    record["cases_path"] = str(cases_path)
    calibrate.write_promotion(record, path=path)
    report = _report(tmp_path, monkeypatch, promotion_path=path,
                     run=_show_run())
    check = _find(report, "calibration")
    assert check["status"] == "warn"
    assert "label_digest" in check["detail"]


def test_no_promotion_record_is_informational(tmp_path, monkeypatch):
    """A machine that has never run the loop has nothing to compare.
    An absent record is a fact, not a finding, and must not make the
    verdict unhealthy."""
    from minder_decision import calibrate
    db = _mig(tmp_path)
    monkeypatch.setattr(calibrate, "promotion_path",
                        lambda suite_id="difficulty-core-v1":
                        tmp_path / "absent.json")
    report = doctor.run_checks(db, probe=False, now=1_700_000_000.0)
    check = _find(report, "calibration")
    assert check is not None
    assert check["status"] == "info"
    assert "no promotion record" in check["detail"]


def test_a_systemctl_that_does_not_answer_never_crashes_doctor(tmp_path,
                                                               monkeypatch):
    """The sandbox case: this shell cannot reach the user bus. doctor
    still has to print a line, and the store verdict must not turn
    unhealthy over a transport it was never going to get."""
    _unused, path = _record(tmp_path)

    def run(cmd):
        return (1, "", "Failed to connect to user scope bus")
    report = _report(tmp_path, monkeypatch, promotion_path=path, run=run)
    check = _find(report, "calibration")
    assert check["status"] == "info"
    assert report["healthy"] is True


def test_doctor_warns_when_the_pinned_baseline_was_replaced(tmp_path,
                                                            monkeypatch):
    """The verdict was computed against a report at a path, and
    `benchmark baseline create --yes` overwrites that path. A record
    whose denominator moved is a claim about a comparison that cannot be
    rerun, which is a finding even when the scale itself is loaded."""
    from minder_decision import calibrate
    record, path = _record(tmp_path)
    replaced = tmp_path / "baselines" / "difficulty-core-v1.json"
    replaced.parent.mkdir(parents=True)
    replaced.write_text(json.dumps({
        "report_version": 1, "suite_id": calibrate.SUITE_ID,
        "suite_fingerprint": "c" * 64,
        "generated_at": "2026-10-13T00:00:00+00:00", "kind": "baseline",
        "metrics": {"comparable_runs": 200,
                    "verified_completion_rate": 0.5, "unsafe_executions": 0,
                    "harmful_frontier_acceptances": 0,
                    "external_prohibited_egress": 0}}))
    record["baseline_path"] = str(replaced)
    calibrate.write_promotion(record, path=path)
    report = _report(tmp_path, monkeypatch, promotion_path=path,
                     run=_show_run())
    check = _find(report, "calibration")
    assert check["status"] == "warn"
    assert "baseline_fingerprint" in check["detail"]
