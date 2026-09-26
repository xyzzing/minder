"""SQLite plumbing for memory v1: connection factory + migrations.
Stdlib sqlite3 only. One connection per operation (short-lived, WAL) so
hook processes never hold locks; writes go through BEGIN IMMEDIATE with a
5s busy timeout so concurrent writers queue instead of corrupting.
"""
import os
import pathlib
import sqlite3

import minder

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"


def db_path():
    """MINDER_MEMORY_DB overrides; default sits beside the JSONL ledger."""
    env = os.environ.get("MINDER_MEMORY_DB")
    if env:
        return pathlib.Path(env)
    return minder.STATE_DIR / "memory.sqlite"


def connect(path=None):
    path = pathlib.Path(path or db_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # degraded paths surface as sqlite errors in the caller
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    migrate(conn)
    return conn


def migrate(conn):
    """Apply migrations/NNN_*.sql in order, tracked via PRAGMA user_version.

    Each migration runs in ONE transaction together with its user_version
    bump (SQLite DDL is transactional). Without the wrap, a mid-file
    failure left partial tables with the OLD version stamped — so every
    later connect() re-ran the broken file, raised forever, and every
    caller failed open to "no memory". Now a failed migration rolls back
    whole and the retry starts clean."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            num = int(sql_file.name.split("_", 1)[0])
        except ValueError:
            continue
        if num > version:
            try:
                conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + sql_file.read_text()
                    + f"\nPRAGMA user_version = {num};\nCOMMIT;")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise


def write(conn, sql, params=()):
    """One BEGIN IMMEDIATE transaction; returns the cursor (rowcount/lastrowid)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(sql, params)
        conn.execute("COMMIT")
        return cur
    except Exception:
        conn.execute("ROLLBACK")
        raise
