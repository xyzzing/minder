"""Operator status tests (8A): missing DB exits 2; a migrated empty DB
reports zeros with exit 0; the status screen carries schema version,
counts, and the env-owned flags."""
from memory import db as _db
from minder_op.cli import EXIT_OK, EXIT_USAGE, main


def _latest_schema():
    import pathlib as _p
    versions = [int(f.name.split("_", 1)[0]) for f in
                _db.MIGRATIONS_DIR.glob("*.sql")
                if f.name[0].isdigit()]
    return max(versions)


def test_missing_db_exits_2(tmp_path, capsys):
    code = main(["--db", str(tmp_path / "nope.sqlite"), "status"])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_corrupt_db_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"this is not a sqlite database" * 40)
    code = main(["--db", str(bad), "status"])
    assert code == 2
    assert "corrupt" in capsys.readouterr().err or "error" in \
        capsys.readouterr().err


def test_empty_migrated_db_reports_zeros(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MINDER_ASSIST", raising=False)
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()  # migrate to v10
    code = main(["--db", str(dbp), "status"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "schema_version" in out and str(_latest_schema()) in out
    assert "episodes" in out and "gaps_open" in out
    assert "consults[(unclassified)]" in out
    assert "decision_traces_rows" in out
    assert "MINDER_ASSIST" in out and "(unset)" in out


def test_status_shows_env_flags(tmp_path, capsys, monkeypatch):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.setenv("MINDER_DECISION", "shadow")
    main(["--db", str(dbp), "status"])
    out = " ".join(capsys.readouterr().out.split())  # padding-agnostic
    assert "env:MINDER_ASSIST : retrieve" in out
    assert "env:MINDER_DECISION : shadow" in out


def test_unknown_db_directory_exits_2(tmp_path, capsys):
    deep = tmp_path / "a" / "b" / "m.sqlite"
    code = main(["--db", str(deep), "episodes", "ls"])
    assert code == 2
    # and a usage problem is 1, not 2
    assert main(["--db", str(tmp_path / "m.sqlite"), "lessons"]) == \
        EXIT_USAGE
