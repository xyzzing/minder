"""SQLite event/episode store tests (docs/prd-memory-v1.md PR 2)."""
import json
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from minder_memory import db as mdb
from minder_memory import store

REPO = "/home/operator/projects/minder"


def ev(**kw):
    base = {"event_type": "tool_failure", "tool": "bash", "session_id": "s1",
            "task_id": "t1", "repo": REPO, "command": "make lint",
            "failure_key": "bash|exit-2|none|makefile",
            "action_fingerprint": "abc123", "payload_json": '{"n": 1}'}
    base.update(kw)
    return base


def test_record_and_read_back(tmp_path):
    dbp = tmp_path / "mem.sqlite"
    eid, status = store.record_event(ev(), db_path=dbp)
    assert status == "ok" and eid
    rows = store.list_events("bash|exit-2|none|makefile", db_path=dbp)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == eid
    assert row["tool"] == "bash" and row["repo"] == REPO
    assert row["action_fingerprint"] == "abc123"
    assert json.loads(row["payload_json"]) == {"n": 1}
    assert row["redaction_status"] == "redacted"
    assert row["event_type"] == "tool_failure"


def test_episode_lifecycle(tmp_path):
    dbp = tmp_path / "mem.sqlite"
    ep_id, status = store.open_episode(ev(), db_path=dbp)
    assert status == "ok" and ep_id
    e1, s1 = store.add_attempt(ep_id, ev(), db_path=dbp)
    e2, s2 = store.add_attempt(ep_id, ev(command="make lint -v"), db_path=dbp)
    assert (s1, s2) == ("ok", "ok") and e1 != e2
    closed, status = store.close_episode(ep_id, "unresolved", db_path=dbp)
    assert closed == "unresolved" and status == "ok"
    ep = store.get_episode(ep_id, db_path=dbp)
    assert ep["status"] == "unresolved" and ep["closed_at"]
    evs = store.episode_events(ep_id, db_path=dbp)
    assert [e["event_id"] for e in evs] == [e1, e2]
    assert [e["seq"] for e in store.episode_events(ep_id, db_path=dbp)] == [1, 2]


def test_events_append_only(tmp_path):
    dbp = tmp_path / "mem.sqlite"
    eid, _ = store.record_event(ev(), db_path=dbp)
    conn = mdb.connect(dbp)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE events SET tool = 'hax' WHERE event_id = ?",
                     (eid,))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM events WHERE event_id = ?", (eid,))
    conn.close()
    assert store.event_by_id(eid, db_path=dbp)["tool"] == "bash"


def test_unwritable_path_degrades_not_crashes(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: read-only dir not read-only")
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        eid, status = store.record_event(ev(), db_path=ro / "mem.sqlite")
        assert eid is None and status.startswith("degraded:")
        assert store.count_attempts("bash|x|none|none", db_path=ro /
                                    "mem.sqlite") == -1
        assert store.list_events("x", db_path=ro / "mem.sqlite") == []
    finally:
        ro.chmod(0o700)


def test_concurrent_writers_do_not_corrupt(tmp_path):
    dbp = tmp_path / "mem.sqlite"
    store.record_event(ev(), db_path=dbp)  # init schema before the race
    worker = textwrap.dedent("""
        import sys
        sys.path.insert(0, {root!r})
        from minder_memory import store
        for i in range(20):
            store.record_event({{**{base!r}, "payload_json": '{{"w": %s}}' % i}},
                               db_path={dbp!r})
    """).format(root=Path_root(), base=ev(), dbp=str(dbp))
    procs = [subprocess.Popen([sys.executable, "-c", worker],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    for p in procs:
        _, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    n = store.count_attempts("bash|exit-2|none|makefile", db_path=dbp)
    assert n == 41  # 1 seed + 2x20


def Path_root():
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent)


def test_minder_memory_db_env_used_when_no_path_given(tmp_path, monkeypatch):
    env_db = tmp_path / "env" / "mem.sqlite"
    monkeypatch.setenv("MINDER_MEMORY_DB", str(env_db))
    eid, status = store.record_event(ev(), db_path=None)
    assert status == "ok"
    assert env_db.exists()
    assert store.list_events("bash|exit-2|none|makefile",
                             db_path=None)[0]["event_id"] == eid


def test_jsonl_ledger_untouched_by_store(tmp_path):
    """The store is additive: events.jsonl is the audit ledger's business."""
    dbp = tmp_path / "mem.sqlite"
    store.record_event(ev(), db_path=dbp)
    assert not (tmp_path / "events.jsonl").exists()
