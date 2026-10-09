"""Issue #26: the store can sit behind the code that ships it, and the
operator CLI had no way to notice or to close the gap.

Three facts the CLI got wrong, each pinned here:
- `minder-op status` reported `ok schema v14 (latest v14)` while migration
  015 had never been applied, because doctor's "latest" counted the
  migrations beside the *running checkout*, not the ones the installed
  share actually ships.
- A DB that could not be opened at all was reported as "db corrupt or
  wrong schema", sending the operator to look for corruption in a file
  that was merely unreadable.
- Nothing in the read-only CLI could apply a pending migration, so the
  only fix was restarting a runtime that had already stopped migrating.
"""
import sqlite3

from minder_memory import db as _db
from minder_op.cli import EXIT_OK, main


def _store(tmp_path, up_to):
    """A store holding the full schema but stamped at `up_to`: what a live
    install looks like when the recorded version falls behind the objects
    the runtime already created. The tables are real, so the read commands
    answer zeros instead of dying on a missing table — which is the state
    the live store was in at v14. Not migratable: replaying a file onto
    the tables it creates raises, which is what
    test_migrate_on_a_partial_store_names_the_state pins."""
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = sqlite3.connect(str(dbp))
    try:
        conn.execute(f"PRAGMA user_version = {up_to}")
        conn.commit()
    finally:
        conn.close()
    return dbp


def _stamped(tmp_path, up_to):
    """A store whose tables stop at `up_to` and whose recorded version
    matches: the genuine behind case, where a migration has shipped but
    has never run. Built by hand because `db.connect` migrates to head."""
    dbp = tmp_path / "m.sqlite"
    dbp.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dbp))
    try:
        for path in sorted(_db.MIGRATIONS_DIR.glob("*.sql")):
            if int(path.name.split("_", 1)[0]) > up_to:
                break
            conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version = {up_to}")
        conn.commit()
    finally:
        conn.close()
    return dbp


def _share(tmp_path, versions):
    """Fake the staged install share the way install.sh writes it: the
    migration files that the running runtime would actually see."""
    mig = tmp_path / "share" / "minder_memory" / "migrations"
    mig.mkdir(parents=True, exist_ok=True)
    for v in versions:
        (mig / f"{v:03d}_x.sql").write_text("CREATE TABLE IF NOT EXISTS t;\n")
    return tmp_path / "share"


def _install_share(tmp_path, monkeypatch, versions):
    """Fake the staged install share the way install.sh writes it, and
    point the runtime at it through the variable install.sh exports."""
    monkeypatch.setenv("MINDER_SHARE", str(_share(tmp_path, versions)))


def _head_share(tmp_path, monkeypatch):
    """A share carrying exactly this runtime's migration files.

    Without it the denominator is whatever the developer machine's real
    install at ~/.local/share/minder happens to ship — a number that
    moves with the last install.sh, not with this checkout. Tests that
    name a version need that pinned; tests about the *installed* number
    set it deliberately instead."""
    versions = [int(p.name.split("_", 1)[0])
                for p in _db.MIGRATIONS_DIR.glob("*.sql")]
    _install_share(tmp_path, monkeypatch, versions)


HEAD = max(int(p.name.split("_", 1)[0]) for p in _db.MIGRATIONS_DIR.glob("*.sql"))
BELOW = HEAD - 2
ABOVE = HEAD + 1


def test_status_names_a_schema_behind_the_installed_share(tmp_path, capsys,
                                                          monkeypatch):
    """The live failure: the store sits below the migrations the code
    ships, and the installed share is the one that moved ahead. Every
    table is present, so `status` answers zeros — the schema is behind,
    not broken, and the two numbers that describe it are different."""
    _install_share(tmp_path, monkeypatch, [1, 2, ABOVE])
    dbp = _store(tmp_path, BELOW)
    assert main(["--db", str(dbp), "status"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert "schema_behind : yes" in out
    assert f"schema_version : {BELOW}" in out
    assert f"schema_latest : {HEAD}" in out
    assert f"schema_installed : {ABOVE}" in out


def test_status_current_against_the_installed_share(tmp_path, capsys,
                                                    monkeypatch):
    _head_share(tmp_path, monkeypatch)
    dbp = _stamped(tmp_path, HEAD)
    assert main(["--db", str(dbp), "status"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert "schema_behind : no" in out
    assert f"schema_latest : {HEAD}" in out
    assert f"schema_installed : {HEAD}" in out


def test_the_two_denominators_stay_apart(tmp_path, monkeypatch):
    """The denominator bug, in both directions. Counting the migrations
    beside the running checkout let doctor certify a store as current
    against files the install does not have; reading the share as if it
    were the runtime would let `migrate` claim versions this process has
    no way to apply. Each number answers one question and cannot be
    substituted for the other."""
    from minder_op import doctor, schema
    _install_share(tmp_path, monkeypatch, [1, 2, ABOVE])
    assert schema.installed_version() == ABOVE
    assert schema.latest_version() == HEAD
    assert doctor._latest_schema() == HEAD
    # No share in the environment at all: the installed number is 0, not
    # the runtime's, so "ahead of the share" never reads as "current".
    # Both variables, because a developer machine has a real install and
    # either one could be set in the shell running the suite.
    monkeypatch.delenv("MINDER_SHARE", raising=False)
    monkeypatch.delenv("MINDER_SHARE_DIR", raising=False)
    monkeypatch.setattr(schema, "DEFAULT_SHARE_DIR", tmp_path / "no-share")
    assert schema.installed_version() == 0


def test_doctor_warns_with_the_migrate_command(tmp_path, monkeypatch):
    """A warn that names the fix beats one that names a restart: the live
    store had been told to "run any minder runtime command to migrate",
    which is exactly what stopped happening."""
    from minder_op import doctor
    _install_share(tmp_path, monkeypatch, [1, 2, ABOVE])
    dbp = _stamped(tmp_path, BELOW)
    report = doctor.run_checks(dbp, probe=False)
    schema = next(c for c in report["checks"] if c["id"] == "schema")
    assert schema["status"] == "warn"
    assert "minder-op migrate" in schema["detail"]


def test_migrate_reports_the_versions_it_applied(tmp_path, capsys):
    """`migrate` is the missing write: it runs the runtime's own migration
    code and reports which versions it applied. The store below 002 is a
    genuine gap, so the reported range is the interesting one."""
    dbp = _stamped(tmp_path, 1)
    assert main(["--db", str(dbp), "migrate"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert f"schema_version : {HEAD}" in out
    assert "schema_before : 1" in out
    applied = out.split("schema_applied : ")[1].split(" schema_version")[0]
    assert applied == ", ".join(f"v{v}" for v in range(2, HEAD + 1))
    assert sqlite3.connect(str(dbp)).execute(
        "PRAGMA user_version").fetchone()[0] == HEAD


def test_migrate_is_idempotent(tmp_path, capsys):
    """A second run applies nothing and says so; the operator has to be
    able to run it without a plan."""
    dbp = _stamped(tmp_path, HEAD)
    assert main(["--db", str(dbp), "migrate"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert "schema_applied : none" in out
    assert "already current" in out


def test_migrate_on_a_partial_store_names_the_state(tmp_path, capsys,
                                                    monkeypatch):
    """The honest failure, not a retry. A store whose tables are present
    but whose recorded version is behind replays `CREATE TABLE` onto
    objects that exist; the answer is to fix the number, and the operator
    has to be told that rather than shown a sqlite traceback."""
    from minder_op import queries, schema
    _head_share(tmp_path, monkeypatch)
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = sqlite3.connect(str(dbp))
    try:
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    try:
        schema.repair(dbp)
    except queries.DBError as exc:
        msg = str(exc)
    else:
        raise AssertionError("a partial store migrated cleanly")
    assert "already exists" in msg
    assert "user_version" in msg
    assert main(["--db", str(dbp), "migrate"]) == 2
    assert "user_version" in " ".join(capsys.readouterr().err.split())


def test_migrate_closes_a_real_gap(tmp_path, capsys, monkeypatch):
    """The end-to-end pair the live install needed and could not perform:
    status says behind, `migrate` runs the runtime's own migrations,
    status stops saying behind."""
    _head_share(tmp_path, monkeypatch)
    dbp = _stamped(tmp_path, BELOW)
    assert main(["--db", str(dbp), "status"]) == EXIT_OK
    assert "schema_behind : yes" in " ".join(capsys.readouterr().out.split())
    assert main(["--db", str(dbp), "migrate"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert f"schema_version : {HEAD}" in out
    assert f"schema_latest : {HEAD}" in out
    capsys.readouterr()
    assert main(["--db", str(dbp), "status"]) == EXIT_OK
    out = " ".join(capsys.readouterr().out.split())
    assert "schema_behind : no" in out


def test_migrate_refuses_a_store_ahead_of_the_answering_runtime(
        tmp_path, capsys, monkeypatch):
    """The live install's real shape, and the trap the obvious
    implementation walks into. The store reached v16 through the checkout;
    the installed share stopped at v14. Running the installed `migrate`
    must not stamp that store with migrations its own runtime has no files
    for - the store would then claim a version the deployed code cannot
    reproduce, and the next read would be a missing table dressed up as
    corruption. The refusal names which runtime can do the work.

    The store here is a real v16 store whose recorded version is then
    pushed above this runtime's ceiling; a store stamped high with no
    tables behind it would test the version number, not the state."""
    _head_share(tmp_path, monkeypatch)
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = sqlite3.connect(str(dbp))
    try:
        conn.execute(f"PRAGMA user_version = {ABOVE}")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("MINDER_SHARE", str(_share(tmp_path, [1, 2, HEAD - 3])))
    assert main(["--db", str(dbp), "migrate"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "ahead of the migrations this runtime ships" in err
    assert f"v{ABOVE}" in err and f"v{HEAD}" in err
    # The store is untouched: a refusal writes nothing.
    assert sqlite3.connect(str(dbp)).execute(
        "PRAGMA user_version").fetchone()[0] == ABOVE


def test_migrate_names_stale_staged_files(tmp_path, capsys, monkeypatch):
    """The other half of the same install: the runtime answering is newer
    than the installed share, so a successful migrate leaves the deployed
    runtimes behind. Applying what this runtime has is right, and saying
    the staged files need a refresh is part of the same answer."""
    _install_share(tmp_path, monkeypatch, [1, 2, HEAD - 3])
    dbp = _stamped(tmp_path, 1)
    assert main(["--db", str(dbp), "migrate"]) == EXIT_OK
    # One readouterr() per call: it drains, so a second read returns ''.
    captured = capsys.readouterr()
    out = " ".join(captured.out.split())
    assert f"schema_version : {HEAD}" in out
    assert f"schema_installed : {HEAD - 3}" in out
    # The store passed the installed share's ceiling on the way up, so the
    # same run that applied the migrations also names the stale files.
    err = " ".join(captured.err.split())
    assert "stale staged files" in err and "install.sh" in err


def test_unreadable_db_is_not_reported_as_corrupt(tmp_path, capsys):
    """Corruption and unreadability need different sentences: the first
    sends the operator to a backup, the second to permissions."""
    from minder_op import queries
    dirdb = tmp_path / "sub" / "m.sqlite"
    try:
        queries.schema_version(dirdb)
    except queries.DBError as exc:
        msg = str(exc)
    else:
        raise AssertionError("a db in an absent directory opened")
    assert "not found" in msg
    assert "corrupt" not in msg

    notdb = tmp_path / "notdb.sqlite"
    notdb.write_bytes(b"definitely not a sqlite file" * 20)
    try:
        queries.schema_version(notdb)
    except queries.DBError as exc:
        msg = str(exc)
    else:
        raise AssertionError("a non-sqlite file opened as a db")
    assert "not readable as a sqlite database" in msg
    assert "wrong schema" not in msg


def test_missing_table_is_a_schema_problem_not_corruption(tmp_path, capsys):
    """The live message blamed corruption for a missing table; the two
    diagnoses lead to different repairs."""
    from minder_op import queries
    dbp = tmp_path / "m.sqlite"
    conn = sqlite3.connect(str(dbp))
    try:
        conn.execute("CREATE TABLE unrelated (x)")
        conn.commit()
    finally:
        conn.close()
    try:
        queries.status(dbp)
    except queries.DBError as exc:
        msg = str(exc)
    else:
        raise AssertionError("a schema-less store answered status")
    assert "no such table" in msg
    assert "corrupt" not in msg


def test_doctor_reports_the_decision_worker(tmp_path, monkeypatch, capsys):
    """The laya worker is spawn-on-demand, so a healthy install with no
    live worker is normal; what is never normal is a worker that died,
    and only its log says so."""
    from minder_op import doctor
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    report = doctor.run_checks(dbp, probe=False)
    worker = next(c for c in report["checks"] if c["id"] == "laya-worker")
    assert worker["status"] == "info"
    assert "spawn" in worker["detail"]

    (state / "laya-worker.log").write_text(
        "minder-decision-worker: request error: "
        "ValueError('unsupported protocol version')\n")
    report = doctor.run_checks(dbp, probe=False)
    worker = next(c for c in report["checks"] if c["id"] == "laya-worker")
    assert worker["status"] == "warn"
    assert "unsupported protocol version" in worker["detail"]


def test_doctor_flags_a_share_older_than_the_database(tmp_path, monkeypatch):
    """The reverse gap: the store is ahead of the installed share, which
    means the staged files were never refreshed after an upgrade."""
    from minder_op import doctor
    _install_share(tmp_path, monkeypatch, [1, 2, HEAD - 3])
    dbp = _stamped(tmp_path, HEAD)
    report = doctor.run_checks(dbp, probe=False)
    check = next(c for c in report["checks"] if c["id"] == "schema")
    assert check["status"] == "warn"
    assert "install.sh" in check["detail"]
