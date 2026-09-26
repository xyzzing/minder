"""Memory v1 schema tests (docs/prd-memory-v1.md PR 0)."""
import pytest

from minder_memory.schemas import (AgentToolEvent, Episode, FailureSignature,
                            Lesson)


def test_agent_tool_event_minimal():
    ev = AgentToolEvent(event_type="tool_failure", tool="bash")
    assert ev.event_type == "tool_failure" and ev.tool == "bash"
    assert ev.payload_json == "{}" and ev.ts is None


def test_agent_tool_event_missing_required_raises():
    with pytest.raises(TypeError):
        AgentToolEvent(event_type="tool_failure")  # no tool
    with pytest.raises(TypeError):
        AgentToolEvent(tool="bash")                # no event_type
    with pytest.raises(TypeError):
        AgentToolEvent(event_type="", tool="bash")


def test_agent_tool_event_unknown_type_raises():
    with pytest.raises(ValueError):
        AgentToolEvent(event_type="tool_exploded", tool="bash")


def test_failure_signature_key_format():
    sig = FailureSignature(tool="bash", error_family="keyerror",
                           symbol_or_test_id="supplier_id",
                           relpath="tests/test_x.py")
    assert sig.key == "bash|keyerror|supplier_id|tests/test_x.py"
    assert FailureSignature("bash", "keyerror", "none").key == \
        "bash|keyerror|none|none"


def test_episode_construct_and_status_enum():
    ep = Episode(opened_at="2026-09-21T00:00:00+00:00", repo="/r",
                 task_id="t1")
    assert ep.status == "open" and ep.closed_at is None
    with pytest.raises(TypeError):
        Episode(opened_at="")
    with pytest.raises(ValueError):
        Episode(opened_at="2026-09-21T00:00:00+00:00", status="forgotten")


def test_lesson_construct_and_status_enum():
    ls = Lesson(instruction="seed the supplier map before querying")
    assert ls.status == "observation"
    with pytest.raises(TypeError):
        Lesson(instruction="")
    with pytest.raises(ValueError):
        Lesson(instruction="x", status="promoted")


def test_lesson_verified_roundtrip_fields():
    ls = Lesson(instruction="x", repo="/r", failure_key="bash|keyerror|none|none",
                status="verified", source_episode="ep1",
                verification_json='{"tests_passed": true}')
    assert ls.status == "verified" and ls.valid_to is None


def test_failed_migration_rolls_back_whole(tmp_path, monkeypatch):
    """A migration that fails mid-file must not leave partial DDL with the
    old version stamped (which bricked connect() forever — fail-open to
    "no memory"). The transaction wraps the DDL and the version bump; once
    the broken file is repaired, the next connect() migrates cleanly."""
    from minder_memory import db as _db
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_ok.sql").write_text(
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY);\n"
        "CREATE TRIGGER t1_no_update BEFORE UPDATE ON t1\n"
        "BEGIN\n  SELECT RAISE(ABORT, 'append-only');\nEND;\n")
    (mig / "002_bad.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE t2 (\n")  # syntax error mid-file
    monkeypatch.setattr(_db, "MIGRATIONS_DIR", mig)
    dbp = tmp_path / "m.sqlite"
    import sqlite3

    import pytest
    with pytest.raises(sqlite3.OperationalError):
        _db.connect(dbp)
    conn = sqlite3.connect(str(dbp))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert version == 1, "version must not advance past a failed migration"
    assert "t1" in tables and "t2" not in tables, tables
    # repair the file: the next connect() completes the migration
    (mig / "002_bad.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\n")
    conn = _db.connect(dbp)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM t2").fetchone()[0] == 0
    finally:
        conn.close()
