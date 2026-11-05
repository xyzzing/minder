"""FR-O1: the work-order contract runtime at intake (issue #84).

The work order is the quality plan and the reward definition (v0.11
amendment): one per task at intake, carrying task, acceptance_criterion
and room_family under windtunnel.work-order/v1. Shape owned by
windtunnel (FR-W1, its #14); minder owns the runtime and keeps its own
validator - minder imports no bird, so the pin here is the mirrored
golden fixture (rule 4), and the mews tripwire cross-checks the real
validator against this repo's runtime.

Intake law (id stability, SCHEMAS-room.md section 12): task_id is the
stable key; criterion and family are immutable while a work order is
open - changing them means closing and declaring a new revision, never
silently rewriting the contract. Attempt grading is a later slice
(named seam); this module records and exposes the contract only.
"""
import json
from pathlib import Path

from minder_memory.work_order import (close_work_order, declare_work_order,
                                      get_work_order, validate_work_order)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schemas" / \
    "work-order.json"


def test_validator_accepts_the_documented_shape():
    """The mirrored windtunnel golden fixture (rule 4) validates clean."""
    assert validate_work_order(json.loads(FIXTURE.read_text())) == []


def test_validator_refuses_a_renamed_key():
    doc = json.loads(FIXTURE.read_text())
    del doc["acceptance_criterion"]
    assert validate_work_order(doc) == ["missing key: acceptance_criterion"]


def test_validator_refuses_unknown_schema():
    doc = json.loads(FIXTURE.read_text())
    doc["schema"] = "windtunnel.work-order/v2"
    assert validate_work_order(doc) == \
        ["unknown or unsupported schema: 'windtunnel.work-order/v2'"]


def test_validator_refuses_empty_contract_text():
    """An empty criterion is a contract with no reward definition:
    honest intake refuses it rather than recording a hollow promise."""
    doc = json.loads(FIXTURE.read_text())
    doc["acceptance_criterion"] = "   "
    assert any("acceptance_criterion" in e for e in validate_work_order(doc))


def _declare(task_id, task="Do the thing.",
             criterion="Done means: the five comparisons match.",
             family="finance/reconciliation", db_path=None):
    return declare_work_order(task, acceptance_criterion=criterion,
                              room_family=family, task_id=task_id,
                              db_path=db_path)


def test_declare_records_the_contract(tmp_path):
    row, status = declare_work_order(
        "Reconcile the Q3 member ledger against the bank statement.",
        acceptance_criterion="All 5 configured comparisons return matching"
                             " totals; every discrepancy names its source"
                             " line.",
        room_family="finance/reconciliation",
        task_id="t-1041", db_path=tmp_path / "s.db")
    assert status == "declared" and row is not None
    assert row["task_id"] == "t-1041" and row["revision"] == 1
    assert row["contract_version"] == "windtunnel.work-order/v1"
    assert row["room_family"] == "finance/reconciliation"
    assert row["closed_at"] is None


def test_declare_reuses_an_identical_open_contract(tmp_path):
    first, _ = _declare("t-1", db_path=tmp_path / "s.db")
    again, status = _declare("t-1", db_path=tmp_path / "s.db")
    assert status == "reused" and again["wo_id"] == first["wo_id"]


def test_declare_refuses_to_rewrite_a_live_criterion(tmp_path):
    _declare("t-1", db_path=tmp_path / "s.db")
    row, status = _declare("t-1", criterion="Done means: something easier.",
                           db_path=tmp_path / "s.db")
    assert row is None and "criterion_immutable" in status


def test_declare_refuses_to_rewrite_a_live_family(tmp_path):
    _declare("t-1", db_path=tmp_path / "s.db")
    row, status = _declare("t-1", family="coding/review",
                           db_path=tmp_path / "s.db")
    assert row is None and "family_immutable" in status


def test_closed_then_redeclared_bumps_revision(tmp_path):
    _declare("t-1", criterion="Done means: A.", family="f/a",
             db_path=tmp_path / "s.db")
    close_work_order(task_id="t-1", reason="superseded",
                     db_path=tmp_path / "s.db")
    row, status = _declare("t-1", criterion="Done means: B.", family="f/a",
                           db_path=tmp_path / "s.db")
    assert status == "declared" and row["revision"] == 2
    assert row["acceptance_criterion"] == "Done means: B."


def test_get_work_order_returns_the_latest(tmp_path):
    _declare("t-1", task="v1.", criterion="c1", db_path=tmp_path / "s.db")
    close_work_order(task_id="t-1", db_path=tmp_path / "s.db")
    _declare("t-1", task="v2.", criterion="c2", db_path=tmp_path / "s.db")
    row = get_work_order(task_id="t-1", db_path=tmp_path / "s.db")
    assert row["task"] == "v2." and row["revision"] == 2
    assert get_work_order(task_id="t-none",
                          db_path=tmp_path / "s.db") is None


def test_declare_refuses_blank_criterion(tmp_path):
    row, status = declare_work_order(
        "Do the thing.", acceptance_criterion="   ", room_family="f",
        task_id="t-1", db_path=tmp_path / "s.db")
    assert row is None and "acceptance_criterion" in status
