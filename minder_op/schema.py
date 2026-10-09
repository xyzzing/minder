"""Schema currency for the operator plane (issue #26).

The store and the code that reads it move apart: a new migration ships in
the installed share, the running runtime never reconnects, and the store
stays at the old `PRAGMA user_version`. Every symptom downstream then
looks like a different bug — `minder-op status` died with "db corrupt or
wrong schema: unable to open database file" on a machine whose database
was fine and merely behind.

This module answers the questions the CLI could not: what version is the
store at, which migrations can *this* runtime apply, and which migrations
the *installed* share ships. Those are three numbers, and the live
install proved each of them can disagree with the other two.

`migrate` is the CLI's one deliberate write to the schema. It calls the
runtime's own `minder_memory.db.migrate`, never a hand-rolled copy, so
the operator CLI cannot invent a schema the runtime disagrees with.
"""
import os
from pathlib import Path

from minder_memory import db as _db
from minder_op.queries import DBError

# The staged install share, where install.sh puts the runtime the hooks
# and services actually run. `MINDER_SHARE` is what install.sh exports;
# `MINDER_SHARE_DIR` is the override the proxy and hook read.
DEFAULT_SHARE_DIR = Path.home() / ".local" / "share" / "minder"
SHARE_DIR_VARS = ("MINDER_SHARE", "MINDER_SHARE_DIR")


def share_dir():
    """The installed runtime's root, or the standard location when no
    install put a path in the environment. A module attribute, so a test
    can pin the default instead of inheriting this machine's install."""
    for var in SHARE_DIR_VARS:
        value = os.environ.get(var)
        if value:
            return Path(value)
    return DEFAULT_SHARE_DIR


def migrations_dir():
    """Where the installed share's migration files live."""
    return share_dir() / "minder_memory" / "migrations"


def runtime_migrations_dir():
    """The migration files the code answering this call can apply.

    A module attribute, never an environment variable: `minder_memory.db`
    resolves its own package directory, so what a `migrate` call can run
    is fixed by which copy of minder was imported. That is why the
    installed share is never a fallback here — it belongs to a different
    runtime, and applying its number to this process would claim work
    this process cannot do."""
    return _db.MIGRATIONS_DIR


def _versions(directory):
    nums = []
    if Path(directory).is_dir():
        for path in Path(directory).glob("*.sql"):
            try:
                nums.append(int(path.name.split("_", 1)[0]))
            except ValueError:
                continue
    return nums


def applied_versions():
    """The migration versions this runtime can apply, in order."""
    return sorted(_versions(runtime_migrations_dir()))


def latest_version():
    """Highest migration version this runtime's own migration set ships."""
    return max(applied_versions(), default=0)


def installed_version():
    """Highest migration version the installed share ships, or 0 when no
    share is in place.

    This is what the deployed hook and proxy will apply on their next
    connect. A store ahead of it means the staged files were never
    refreshed after an upgrade — the reverse of the gap `migrate` closes,
    and not something `migrate` can fix."""
    return _highest(_versions(migrations_dir()))


def _highest(nums):
    return max(nums) if nums else 0


def schema_state(path):
    """(applied, runtime_latest): what the store holds and what the
    runtime answering can bring it to."""
    return _db_schema_version(path), latest_version()


def _db_schema_version(path):
    from minder_op import queries
    return queries.schema_version(path)


def behind(path):
    applied, latest = schema_state(path)
    return applied < latest


def migrate(path):
    """Apply pending migrations and return the versions this call applied.

    Idempotent by construction: `db.migrate` skips every version at or
    below the store's `user_version`, so a second run returns []. The
    store is opened through the runtime's connection factory, which is
    what makes the migration the runtime's own — including the per-file
    transaction that keeps a failed file from being replayed forever.

    The versions named are the ones in the range the version bump
    covered. The bump is the only record the store keeps — the runtime
    stamps `user_version` and nothing else — so this reports the bump
    rather than pretending to audit it.
    """
    before = _db_schema_version(path)
    conn = _db.connect(path)
    try:
        after = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if after == before:
        return []
    return [v for v in applied_versions() if before < v <= after]


def repair(path):
    """Close the gap the status screen named, and say what it took.

    Returns (before, applied, after, latest). A store ahead of this
    runtime's migration set is reported, not silently "fixed": inventing
    migrations to match a store would be the CLI writing a schema the
    runtime disagrees with.

    A store whose recorded version is behind the objects it already holds
    cannot be migrated at all — the migration files are plain `CREATE
    TABLE`, not `IF NOT EXISTS`, so replaying them raises mid-file and
    rolls back. That state is named instead of retried (P4): it is damage
    to `user_version`, and only correcting the number fixes it."""
    before, latest = schema_state(path)
    if before > latest:
        return before, [], before, latest
    try:
        applied = migrate(path)
    except Exception as exc:
        raise DBError(
            f"migrate could not apply to a store at v{before}: {exc}. The "
            "store's recorded version is behind the objects it already "
            "holds, so the migration files replay onto existing tables. "
            "Set the number to the highest version already present "
            "(sqlite3 shell: PRAGMA user_version = N) - do not delete the "
            "store") from exc
    return before, applied, _db_schema_version(path), latest


def describe(path):
    """One line for the status screen and doctor alike, naming the repair
    for whichever of the three numbers disagrees."""
    applied, latest = schema_state(path)
    installed = installed_version()
    if applied == latest == installed:
        return f"schema v{applied} (latest v{latest})"
    if applied < latest:
        return (f"schema v{applied}, latest v{latest} - apply it with "
                "`minder-op migrate`")
    if installed and applied > installed:
        return (f"schema v{applied} is ahead of the installed share's "
                f"v{installed} ({migrations_dir()}) - the staged files are "
                "stale; re-run install.sh")
    if applied > latest:
        return (f"schema v{applied} is ahead of this runtime's migration "
                f"set (v{latest}) - the code answering is older than the "
                "store")
    return (f"schema v{applied} current for this runtime (v{latest}), "
            f"installed share ships v{installed} - re-run install.sh so "
            "the deployed runtimes have the same files")


__all__ = ["DBError", "applied_versions", "behind", "describe",
           "installed_version", "latest_version", "migrate",
           "migrations_dir", "repair", "runtime_migrations_dir",
           "schema_state", "share_dir"]
