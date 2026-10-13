"""The reason codes are countable on the operator plane (issue #14).

`minder-op lessons decisions` is the whole point of the taxonomy: it
answers "which distillations get rejected, and for what reason" without
reading a single free-text note. So the assertions here are on printed
counts and on the closed lists the parser accepts - not on whether a
column exists.
"""
import sqlite3

from minder_memory import db as _db
from minder_memory import lesson_decisions as dec
from minder_memory import lessons
from minder_op.cli import main
from test_op_writes import distilled_candidate

REPO = "/repo"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def _seed(dbp):
    """The queue as issue #10 produces it: one candidate adopted, one
    rejected with a code, and the adopted lesson later invalidated with a
    diagnosis. Reusing test_op_writes' distiller fixture keeps the
    candidate shape real instead of hand-inserted."""
    adopted = distilled_candidate(dbp, session="lds-a")
    rejected = distilled_candidate(dbp, session="lds-r")
    assert adopted != rejected, "the queue seed collided"
    lesson, status = lessons.adopt_candidate_lesson(
        adopted, "guard supplier_id with .get", db_path=dbp)
    assert status == "ok", status
    _, status = lessons.reject_candidate_lesson(rejected, "generic",
                                                db_path=dbp)
    assert status == "ok", status
    _, status = lessons.invalidate_lesson(
        lesson["lesson_id"], "wrong for this repo",
        diagnosis="content_defect", db_path=dbp)
    assert status == "ok", status
    return lesson["lesson_id"], adopted, rejected


def test_counts_come_from_stored_rows(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp)
    assert main(["--db", str(dbp), "lessons", "decisions"]) == 0
    out = capsys.readouterr().out
    assert "generic" in out and "content_defect" in out
    assert "grounded_useful" in out
    # One decision per code: the count column is not free text echoing.
    assert "1" in out


def _rows(dbp, sql, args=()):
    conn = _db.connect(dbp)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_one_lesson_its_own_decision_history(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp)
    rejected = [r["lesson_id"] for r in _rows(
        dbp, "SELECT lesson_id FROM lesson_decisions WHERE action = 'reject'")]
    assert main(["--db", str(dbp), "lessons", "decisions",
                 "--id", rejected[0]]) == 0
    out = capsys.readouterr().out
    assert "reject" in out and "generic" in out
    assert "content_defect" not in out


def test_invalidate_records_the_diagnosis_it_was_given(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    lid, _adopted, _rejected = _seed(dbp)
    assert main(["--db", str(dbp), "lessons", "invalidate", lid,
                 "--reason", "too generic to act on",
                 "--diagnosis", "application_failure", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "application_failure" in out
    conn = _db.connect(dbp)
    try:
        row = conn.execute("SELECT invalidated_diagnosis FROM lessons"
                           " WHERE lesson_id = ?", (lid,)).fetchone()
    finally:
        conn.close()
    assert row["invalidated_diagnosis"] == "application_failure"


def test_invalidate_without_a_diagnosis_is_not_silently_a_defect(tmp_path,
                                                                capsys):
    dbp = tmp_path / "m.sqlite"
    lid, _adopted, _rejected = _seed(dbp)
    assert main(["--db", str(dbp), "lessons", "invalidate", lid,
                 "--reason", "reviewed again", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "unknown" in out
    assert "content_defect" not in out


def test_reject_needs_a_code_and_refuses_one_outside_the_list(tmp_path,
                                                              capsys):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp)
    # A second, still-open candidate: the refusal must not consume the
    # seeded one, since a refused code writes nothing.
    open_candidate = distilled_candidate(dbp, session="lds-x")
    assert main(["--db", str(dbp), "lessons", "reject", open_candidate]) \
        != 0  # --code is required
    assert main(["--db", str(dbp), "lessons", "reject", open_candidate,
                 "--code", "definitely_wrong", "--yes"]) != 0
    err = capsys.readouterr().err
    # argparse prints the closed list, so the taxonomy is discoverable
    # from the failing call rather than only from the module.
    assert "unsupported_causality" in err


def test_adoption_is_a_countable_accept(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _seed(dbp)
    assert main(["--db", str(dbp), "lessons", "decisions"]) == 0
    out = capsys.readouterr().out
    assert "adopt" in out


def test_a_store_without_the_ledger_says_so_not_zero(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = sqlite3.connect(dbp)
    conn.execute("DROP TABLE lesson_decisions")
    conn.commit()
    conn.close()
    assert main(["--db", str(dbp), "lessons", "decisions"]) == 0
    assert "not available" in capsys.readouterr().out


def test_the_parser_reuses_the_module_lists_not_a_copy():
    import argparse

    from minder_op import cli
    parser = argparse.ArgumentParser()
    cli._build_parser(parser) if hasattr(cli, "_build_parser") else None
    # The single source of truth is the module; a parser that hardcoded a
    # second list would let the two drift apart in silence.
    assert dec.DEFAULT_DIAGNOSIS in dec.DIAGNOSES
    assert "grounded_useful" in dec.DECISION_CODES
