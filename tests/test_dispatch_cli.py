"""The dispatch CLI surface (#89, stage 1): status names a missing
config instead of pretending, ls renders the recorded triggers, and
poll_once runs the whole intake path (source -> gate -> store) with an
injected poller, recording each trigger exactly once.
"""
import json

from minder_memory import db as memdb
from minder_op import dispatch_cli, dispatch_config
from minder_op.cli import EXIT_OK, main


def _trigger(tid="github:111:2026-11-07T10:00:00Z", sender="alice"):
    return {"trigger_id": tid, "ts": "2026-11-07T10:00:00Z",
            "source": "github", "repo": "xyzzing/minder",
            "kind": "issue", "sender": sender}


def _config(tmp_path, **over):
    body = {"repo": "xyzzing/minder", "allowed_senders": ["alice"]}
    body.update(over)
    path = tmp_path / "dispatch.json"
    path.write_text(json.dumps(body))
    return dispatch_config.load_config(path)


def _store(tmp_path):
    path = tmp_path / "memory.sqlite"
    memdb.connect(path).close()
    return path


def test_status_names_a_missing_config(tmp_path, capsys):
    assert main(["--db", str(_store(tmp_path)), "dispatch", "status",
                 "--config", str(tmp_path / "none.json")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "config_missing" in out
    assert "observe" in out


def test_ls_renders_recorded_triggers(tmp_path, capsys):
    dbp = _store(tmp_path)
    cfg = _config(tmp_path)
    dispatch_cli.poll_once(cfg, dbp, poll_fn=lambda repo: (
        [_trigger(sender="stranger")], None))
    assert main(["--db", str(dbp), "dispatch", "ls",
                 "--config", str(tmp_path / "dispatch.json")]) == EXIT_OK
    out = capsys.readouterr().out
    assert "github:111:2026-11-07T10:00:00Z" in out
    assert "sender_not_allowed" in out


def test_poll_once_gates_and_records_each_trigger_once(tmp_path):
    dbp = _store(tmp_path)
    cfg = _config(tmp_path)
    calls = []

    def poll(repo):
        calls.append(repo)
        return ([_trigger(),
                 _trigger(tid="github:110:2026-11-07T09:00:00Z",
                          sender="dependabot[bot]")], None)

    summary = dispatch_cli.poll_once(cfg, dbp, poll_fn=poll)
    assert calls == ["xyzzing/minder"]
    assert summary == {"polled": 2, "new": 2, "admitted": 0,
                       "would_accept": 1, "filtered": 1, "error": None}
    again = dispatch_cli.poll_once(cfg, dbp, poll_fn=poll)
    assert again["new"] == 0  # a restart never replays


def test_poll_once_names_a_source_error(tmp_path):
    dbp = _store(tmp_path)
    cfg = _config(tmp_path)
    summary = dispatch_cli.poll_once(cfg, dbp, poll_fn=lambda repo: (
        [], "gh_unreachable"))
    assert summary["error"] == "gh_unreachable"
    assert summary["polled"] == 0


def test_poll_once_admits_in_auto_mode(tmp_path):
    dbp = _store(tmp_path)
    cfg = _config(tmp_path, accept_mode="auto")
    summary = dispatch_cli.poll_once(cfg, dbp,
                                     poll_fn=lambda repo: ([_trigger()],
                                                           None))
    assert summary["admitted"] == 1
    assert summary["filtered"] == 0
