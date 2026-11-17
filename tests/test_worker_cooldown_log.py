"""The worker client's cooldown path names itself, not a KeyError
(issue #100; found live 2026-11-17 in laya-worker.log).

While the spawn cooldown is active and no worker answers, the client's
cheap liveness dial reads `_LIVE_NOTED["dial_refused"]` before ever
writing that key. The read raised KeyError, the fail-open catch turned
it into `client_error: KeyError('dial_refused')`, and the one-line-per-
reason diagnostic the path exists to write (`dial_refused`) never
appeared. A test that has never failed does not demonstrate it can
catch the bug: this one fails against the pre-change code on the log
line, not on the return value — the return value is None either way,
which is exactly why the defect survived.
"""
from minder_decision import worker


def test_cooldown_without_worker_names_dial_refused(tmp_path,
                                                    monkeypatch):
    sock = tmp_path / "subdir" / "laya-worker.sock"
    monkeypatch.setenv("MINDER_LAYA_WORKER_SOCK", str(sock))
    monkeypatch.setattr(worker, "_LIVE_NOTED", {})
    monkeypatch.setattr(worker, "_CLIENT_LOGGED", {})
    worker._note_failure()  # the cooldown a failed spawn left behind

    client = worker.WorkerDifficultyClient(spawn=False, path=sock)
    assert client.system_one({"task": "x"}) is None

    log = sock.with_name("laya-worker.log")
    assert log.exists(), "the failure diagnostic must be written"
    text = log.read_text()
    assert "client: dial_refused" in text
    assert "KeyError" not in text


def test_cooldown_dial_refused_logged_once_per_process(tmp_path,
                                                        monkeypatch):
    sock = tmp_path / "laya-worker.sock"
    monkeypatch.setenv("MINDER_LAYA_WORKER_SOCK", str(sock))
    monkeypatch.setattr(worker, "_LIVE_NOTED", {})
    monkeypatch.setattr(worker, "_CLIENT_LOGGED", {})
    worker._note_failure()

    client = worker.WorkerDifficultyClient(spawn=False, path=sock)
    for _ in range(3):
        assert client.system_one({"task": "x"}) is None

    text = sock.with_name("laya-worker.log").read_text()
    # the reason line, not the KeyError detail that happens to contain
    # the substring
    assert text.count("client: dial_refused") == 1
    assert "client_error" not in text


def test_reset_client_log_marks_clears_both_guards():
    """The test seam resets the once-per-reason marks, so a test can
    assert each reason is written exactly once per process."""
    worker._CLIENT_LOGGED["stale"] = True
    worker._LIVE_NOTED["dial_refused"] = True
    worker._reset_client_log_marks()
    assert worker._CLIENT_LOGGED == {}
    assert worker._LIVE_NOTED == {}
