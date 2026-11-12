"""The dispatch attention queue (#89, stage 3): pending items are the
would_accept triggers no human decision covers yet, a resolution
appends an attributed human_input_events row, the first terminal
decision stands (idempotent, conflicting refusals named), and the
revision binds the context the operator saw.
"""
import json

from minder_memory import db as memdb
from minder_op import dispatch_attention, dispatch_store
from minder_op.queries import _rows

REPO = "xyzzing/minder"


def _trigger(tid, sender="alice"):
    return {"trigger_id": tid, "ts": "2026-11-07T10:00:00Z",
            "source": "github", "repo": REPO, "kind": "issue",
            "sender": sender}


def _would():
    return {"decision": "would_accept", "reason": None,
            "sender_class": "listed"}


def _filtered():
    return {"decision": "filtered", "reason": "sender_not_allowed",
            "sender_class": "unlisted"}


def _store(tmp_path):
    path = tmp_path / "memory.sqlite"
    memdb.connect(path).close()
    return path


def _human_rows(dbp):
    return _rows(dbp, "SELECT actor, kind, decision, affects_json"
                      " FROM human_input_events")


def test_pending_lists_only_would_accept_triggers(tmp_path):
    dbp = _store(tmp_path)
    dispatch_store.record_trigger(dbp, _trigger("github:1:t1"), _would())
    dispatch_store.record_trigger(dbp, _trigger("github:2:t2"),
                                  _filtered())
    items = dispatch_attention.pending_items(dbp, "observe")
    assert [i["trigger_id"] for i in items] == ["github:1:t1"]
    assert items[0]["sender"] == "alice"
    assert items[0]["repo"] == REPO
    assert items[0]["revision"]


def test_resolve_approve_records_attribution(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    ok, code = dispatch_attention.resolve(dbp, tid, "approve",
                                          actor="zacch")
    assert ok is True and code == "ok"
    rows = _human_rows(dbp)
    assert len(rows) == 1
    assert rows[0]["actor"] == "zacch"
    assert rows[0]["decision"] == "approved"
    assert tid in (rows[0]["affects_json"] or "")


def test_resolve_observe_defers_and_leaves_the_queue(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    ok, _ = dispatch_attention.resolve(dbp, tid, "observe",
                                       actor="zacch")
    assert ok is True
    assert dispatch_attention.pending_items(dbp, "observe") == []


def test_resolve_is_idempotent(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    first = dispatch_attention.resolve(dbp, tid, "approve", actor="a")
    second = dispatch_attention.resolve(dbp, tid, "approve", actor="a")
    assert first == (True, "ok")
    assert second == (True, "already_resolved")
    assert len(_human_rows(dbp)) == 1


def test_conflicting_resolution_is_refused(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    dispatch_attention.resolve(dbp, tid, "approve", actor="a")
    ok, code = dispatch_attention.resolve(dbp, tid, "observe", actor="a")
    assert ok is False
    assert code == "already_resolved_differently"
    assert len(_human_rows(dbp)) == 1


def test_unknown_trigger_and_not_pending_are_named(tmp_path):
    dbp = _store(tmp_path)
    assert dispatch_attention.resolve(dbp, "github:nope:t", "approve",
                                      actor="a") == (False,
                                                     "unknown_trigger")
    dispatch_store.record_trigger(dbp, _trigger("github:2:t2"),
                                  _filtered())
    assert dispatch_attention.resolve(dbp, "github:2:t2", "approve",
                                      actor="a") == (False, "not_pending")


def test_unknown_choice_is_named(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    assert dispatch_attention.resolve(dbp, tid, "yes", actor="a") == (
        False, "unknown_choice")


def test_revision_binds_the_context_the_operator_saw(tmp_path):
    dbp = _store(tmp_path)
    dispatch_store.record_trigger(dbp, _trigger("github:1:t1"), _would())
    row = dispatch_store.triggers(dbp)[0]
    r_observe = dispatch_attention.item_revision(row, "observe")
    r_auto = dispatch_attention.item_revision(row, "auto")
    assert r_observe != r_auto
    assert r_observe == dispatch_attention.item_revision(row, "observe")
    ok, code = dispatch_attention.resolve(dbp, "github:1:t1", "approve",
                                          actor="a",
                                          expected_revision=r_observe)
    assert ok is True
    stale = dispatch_attention.resolve(dbp, "github:9:t9", "approve",
                                       actor="a",
                                       expected_revision="gone")
    assert stale == (False, "unknown_trigger")


def test_resolve_rejects_a_stale_revision(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    ok, code = dispatch_attention.resolve(dbp, tid, "approve", actor="a",
                                          expected_revision="stale")
    assert ok is False
    assert code == "revision_invalidated"
    assert _human_rows(dbp) == []


def test_unresolved_count_tracks_the_queue(tmp_path):
    dbp = _store(tmp_path)
    dispatch_store.record_trigger(dbp, _trigger("github:1:t1"), _would())
    dispatch_store.record_trigger(dbp, _trigger("github:2:t2"), _would())
    assert dispatch_attention.unresolved_count(dbp, "observe") == 2
    dispatch_attention.resolve(dbp, "github:1:t1", "observe", actor="a")
    assert dispatch_attention.unresolved_count(dbp, "observe") == 1


def test_affects_payload_is_structured(tmp_path):
    dbp = _store(tmp_path)
    tid = "github:1:t1"
    dispatch_store.record_trigger(dbp, _trigger(tid), _would())
    dispatch_attention.resolve(dbp, tid, "approve", actor="zacch")
    affects = json.loads(_human_rows(dbp)[0]["affects_json"])
    assert affects == [{"dispatch_trigger": tid}]
