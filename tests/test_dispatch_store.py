"""The dispatch trigger store (#89, stage 1): the migration lands with
the runtime, inserts are idempotent on the stable trigger id (a restart
never replays), the table is append-only like every evidence table, and
reads return the recorded values.
"""
import pytest

from minder_memory import db as memdb
from minder_op import dispatch_store

REPO = "xyzzing/minder"


def _trigger(tid="github:111:2026-11-07T10:00:00Z", sender="alice",
             kind="issue", ts=None):
    return {"trigger_id": tid,
            # the id is source:nid:updated_at, and updated_at itself
            # contains colons, so drop the two prefix fields, not split
            # from the right
            "ts": ts or tid.split(":", 2)[-1],
            "source": "github", "repo": REPO, "kind": kind,
            "sender": sender}


def _decision(decision="filtered", reason="sender_not_allowed",
              sender_class="unlisted"):
    return {"decision": decision, "reason": reason,
            "sender_class": sender_class}


def _store(tmp_path):
    path = tmp_path / "memory.sqlite"
    memdb.connect(path).close()
    return path


def test_migration_creates_the_table(tmp_path):
    path = _store(tmp_path)
    counts = dispatch_store.counts(path)
    assert counts == {"admitted": 0, "would_accept": 0, "filtered": 0}


def test_record_is_idempotent_on_trigger_id(tmp_path):
    path = _store(tmp_path)
    first = dispatch_store.record_trigger(path, _trigger(), _decision())
    second = dispatch_store.record_trigger(path, _trigger(), _decision())
    assert first is True
    assert second is False
    rows = dispatch_store.triggers(path)
    assert len(rows) == 1
    assert rows[0]["trigger_id"] == "github:111:2026-11-07T10:00:00Z"


def test_table_is_append_only(tmp_path):
    path = _store(tmp_path)
    dispatch_store.record_trigger(path, _trigger(), _decision())
    conn = memdb.connect(path)
    with pytest.raises(Exception):
        conn.execute("UPDATE dispatch_triggers SET decision = 'admitted'")
    conn.close()


def test_read_returns_recorded_values(tmp_path):
    path = _store(tmp_path)
    dispatch_store.record_trigger(path, _trigger(), _decision())
    dispatch_store.record_trigger(
        path, _trigger(tid="github:222:2026-11-07T11:00:00Z"),
        {"decision": "would_accept", "reason": None,
         "sender_class": "listed"})
    rows = dispatch_store.triggers(path, decision="would_accept")
    assert [r["trigger_id"] for r in rows] == [
        "github:222:2026-11-07T11:00:00Z"]
    assert dispatch_store.counts(path) == {"admitted": 0,
                                           "would_accept": 1,
                                           "filtered": 1}
    newest = dispatch_store.newest_ts(path)
    assert newest == "2026-11-07T11:00:00Z"


def test_newest_ts_is_none_on_an_empty_store(tmp_path):
    path = _store(tmp_path)
    assert dispatch_store.newest_ts(path) is None


def test_negative_limit_does_not_mean_everything(tmp_path):
    """SQLite reads a negative LIMIT as no limit; the store clamps so a
    negative --limit cannot silently return the whole ledger."""
    path = _store(tmp_path)
    for n in ("1", "2", "3"):
        dispatch_store.record_trigger(
            path, _trigger(tid=f"github:{n}:2026-11-07T10:0{n}:00Z"),
            _decision())
    assert len(dispatch_store.triggers(path, limit=-5)) == 1
