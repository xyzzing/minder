"""The learning_injections.skill_name drift and its repair (issue #80).

The column was added by editing shipped migration 015 in place, so every
store migrated before that edit permanently lacks it and the sanctioned
`minder-op migrate` can never repair it (migrate applies only
num > version; 015 never re-runs). The checkout's ledger writer names
the column in its INSERT, so on such a store every injection record
fails with degraded:OperationalError and fail-open drops the row - the
ledger's denominator silently loses everything.

The repair returns 015 to its shipped shape and moves the column to
020, so fresh stores and drifted stores converge. These tests pin both
directions and prove red against the pre-change tree (the drifted-store
repair fails while 020 does not exist).

The drift simulation mirrors the live store exactly: a store whose
applied set ends at the edited 015 (version 16), with 017/018's objects
absent - dropping only the column would not be that state.
"""
import sqlite3

from minder_memory import db as _db


def _column_exists(dbp):
    conn = _db.connect(dbp)
    try:
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(learning_injections)").fetchall()}
        idx = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
            " AND tbl_name = 'learning_injections'").fetchall()}
        return "skill_name" in cols, "idx_learning_injections_skill" in idx
    finally:
        conn.close()


def test_fresh_store_carries_the_column():
    # db.connect migrates to the latest schema; a fresh store must have
    # the column AND the index after the full migration set.
    import tempfile
    dbp = tempfile.mktemp(suffix=".sqlite")
    _db.connect(dbp).close()
    has_col, has_idx = _column_exists(dbp)
    assert has_col, "fresh store lacks learning_injections.skill_name"
    assert has_idx, "fresh store lacks the skill_name index"


def _objects_created_above(version):
    """Table and index names every migration numbered above `version`
    creates - so the rewind drops later migrations' objects without a
    hand-list that rots on each new file (021 broke exactly that)."""
    import re
    from minder_memory.db import MIGRATIONS_DIR
    tables, indexes = set(), set()
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            num = int(sql_file.name.split("_", 1)[0])
        except ValueError:
            continue
        if num <= version:
            continue
        sql = sql_file.read_text()
        tables.update(re.findall(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", sql))
        indexes.update(re.findall(
            r"CREATE INDEX (?:IF NOT EXISTS )?(\w+)", sql))
    return tables, indexes


def test_drifted_store_is_repaired_by_migrate(tmp_path):
    """A store whose applied set ends at the edited 015 (the live
    store's exact state: version 16, no skill_name, 017/018 absent)
    converges when migrate runs. Proven red before 020 existed."""
    dbp = tmp_path / "drifted.sqlite"
    _db.connect(dbp).close()  # build at the latest version
    later_tables, later_indexes = _objects_created_above(16)
    raw = sqlite3.connect(dbp)
    # rewind to the drifted state: drop everything migrations above the
    # stamp created (tables before their indexes), plus the column and
    # index the in-place 015 edit added
    for name in sorted(later_indexes):
        raw.execute(f"DROP INDEX IF EXISTS {name}")
    for name in sorted(later_tables):
        raw.execute(f"DROP TABLE IF EXISTS {name}")

    def drop_column(table, column):
        cols = {r[1] for r in raw.execute(f"PRAGMA table_info({table})")}
        if column in cols:  # a later migration's scan may have removed it
            raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    drop_column("lessons", "source_human_input")
    drop_column("learning_injections", "skill_name")
    raw.execute("PRAGMA user_version = 16")
    raw.commit()
    raw.close()
    _db.connect(dbp).close()  # migrate runs on connect: 017, 018, 020, 021
    has_col, has_idx = _column_exists(dbp)
    assert has_col, "migrate did not repair the drifted store's skill_name"
    assert has_idx, "migrate did not restore the skill_name index"
    # and 017/018's own objects came back too - the rewind is honest
    conn = _db.connect(dbp)
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        assert "injection_shadow" in tables
        assert "dispatch_triggers" in tables
    finally:
        conn.close()
