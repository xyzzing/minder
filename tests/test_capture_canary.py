"""The capture canary (#73): verdict logic proven without live dsh.

The canary's whole job is to tell green from red honestly, so the tests
own the four verdict shapes from the issue and the attribution guard: a
sink record from some other session (the zcode transport writes the same
ledger) must never satisfy the dsh capture assertion.
"""
import json


from minder_op import capture_canary


def _entry(session_id, mtime):
    return {"session_id": session_id, "log_path": None, "mtime": mtime}


def _ledger_line(session_id, ts=1000.0):
    return json.dumps({"ts": ts, "task": f"session-{session_id}",
                       "event": "hook_timing", "hook_event": "PostToolUse"})


class FakeEnv:
    """Injectable stand-ins for dsh, the network, and the filesystem."""

    def __init__(self, tmp_path, sessions_before, sessions_after,
                 ledger_lines=(), dsh_version="0.2.1-rc.2", bridge_pin=None,
                 dsh_fails=False, persist=True):
        self.tmp = tmp_path
        self.ledger = tmp_path / "events.jsonl"
        self.ledger.write_text("".join(line + "\n" for line in ledger_lines))
        self.manifest = tmp_path / "package.json"
        self.manifest.write_text(json.dumps({"dependencies": {
            "@deepseek-ai/dsh-hooks-claude-code": bridge_pin}}))
        self.fingerprint = tmp_path / "canary_state.json"
        self.dsh_version = dsh_version
        self.dsh_fails = dsh_fails
        self.persist = persist
        self.calls = []

        def session_dirs(root=None, cache=True):
            ran = any(cmd[1:2] == ["headless"] for cmd in self.calls)
            return list(sessions_after if ran else sessions_before)

        def runner(cmd, timeout=None):
            self.calls.append(cmd)
            if dsh_fails and cmd[1:2] == ["headless"]:
                return _Result(1, "", "boot failed")
            if persist and cmd[1:2] == ["headless"]:
                self.ledger.write_text(self.ledger.read_text()
                                       + _ledger_line("newsess") + "\n")
            return _Result(0, f"{dsh_version}\n", "")

        def probe(url, timeout=None):
            return "8390" in url or "8081" in url

        self.session_dirs = session_dirs
        self.runner = runner
        self.probe = probe

    def run(self, stored_fingerprint=None):
        if stored_fingerprint is not None:
            self.fingerprint.write_text(json.dumps(stored_fingerprint))
        return capture_canary.run_canary(
            dsh_home=str(self.tmp),
            profile_manifest=str(self.manifest),
            ledger_path=self.ledger,
            fingerprint_path=self.fingerprint,
            runner=self.runner,
            probe=self.probe,
            session_dirs_fn=self.session_dirs,
            settle_seconds=0.0,
        )


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _surface(verdict, surface_id):
    return next(s for s in verdict["surfaces"] if s["id"] == surface_id)


def test_all_green_writes_fingerprint(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  bridge_pin="0.2.1-rc.5")
    verdict = env.run()
    assert verdict["verdict"] == "green"
    assert _surface(verdict, "capture")["status"] == "ok"
    stored = json.loads(env.fingerprint.read_text())
    assert stored["bridge_pin"] == "0.2.1-rc.5"
    assert stored["dsh_version"] == "0.2.1-rc.2"


def test_boot_failure_is_red_and_named(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1)],
                  dsh_fails=True)
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_no_session"


def test_hooks_fired_but_not_persisted_is_red(tmp_path):
    # The session log says hooks fired; the sink gained nothing. That is
    # capture loss, the exact shape the 0.2.x bridge denial hid.
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  persist=False)
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_hooks_not_persisted"


def test_foreign_session_record_never_satisfies_capture(tmp_path):
    # A record from another transport (zcode) sits in the ledger already;
    # the canary session produced none. Freshness alone would false-green.
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  ledger_lines=[_ledger_line("zcode-session", 9999.0)],
                  persist=False)
    verdict = env.run()
    assert verdict["verdict"] == "red"


def test_drift_with_green_capture_rebaselines(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  dsh_version="0.2.2", bridge_pin="0.2.1-rc.5")
    verdict = env.run({"dsh_version": "0.2.1-rc.2",
                       "bridge_pin": "0.2.1-rc.5", "recorded_at": 1})
    assert verdict["verdict"] == "green"
    assert _surface(verdict, "drift")["status"] == "warn"
    stored = json.loads(env.fingerprint.read_text())
    assert stored["dsh_version"] == "0.2.2"


def test_drift_with_red_capture_names_both(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1)],
                  dsh_version="0.2.2", bridge_pin="0.2.1-rc.5",
                  dsh_fails=True)
    verdict = env.run({"dsh_version": "0.2.1-rc.2",
                       "bridge_pin": "0.2.1-rc.5", "recorded_at": 1})
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "drift")["status"] == "warn"
    assert _surface(verdict, "capture")["reason"] == "capture_no_session"


def test_first_run_baselines_without_drift_alarm(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  bridge_pin="0.2.1-rc.5")
    verdict = env.run()
    assert verdict["verdict"] == "green"
    assert _surface(verdict, "drift")["status"] == "ok"


def test_endpoint_down_is_red(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  bridge_pin="0.2.1-rc.5")
    env.probe = lambda url, timeout=None: "8390" in url
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "endpoints")["status"] == "fail"


def test_missing_bridge_pin_is_reported_not_crash(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2)],
                  bridge_pin=None)
    verdict = env.run()
    assert _surface(verdict, "drift")["status"] == "warn"
