"""The capture canary (#73): verdict logic proven without live dsh.

The canary's whole job is to tell green from red honestly, so the tests
own the four verdict shapes from the issue and the attribution guard: a
sink record from some other session (the zcode transport writes the same
ledger) must never satisfy the dsh capture assertion.
"""
import json

import pytest


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
                 dsh_fails=False, persist=True, delegate=True,
                 unattributed=False):
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
        self.delegate = delegate
        self.calls = []

        def session_dirs(root=None, cache=True):
            ran = any(cmd[1:2] == ["headless"] for cmd in self.calls)
            return list(sessions_after if ran else sessions_before)

        def runner(cmd, timeout=None):
            self.calls.append(cmd)
            if dsh_fails and cmd[1:2] == ["headless"]:
                return _Result(1, "", "boot failed")
            if persist and cmd[1:2] == ["headless"]:
                text = self.ledger.read_text() + _ledger_line("newsess") + "\n"
                if delegate:
                    record = {"ts": 1001.0, "task": "session-childsess",
                              "event": "subagent_end",
                              "child_session_id": "childsess"}
                    if not unattributed:
                        record["parent_session_id"] = "newsess"
                    text += json.dumps(record) + "\n"
                self.ledger.write_text(text)
            return _Result(0, f"{dsh_version}\n", "")

        def probe(url, timeout=None):
            return "8390" in url or "8081" in url

        self.session_dirs = session_dirs
        self.runner = runner
        self.probe = probe

    def run(self, stored_fingerprint=None, mode="full", now=None):
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
            now=now,
            mode=mode,
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
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
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
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
                  persist=False)
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_hooks_not_persisted"


def test_foreign_session_record_never_satisfies_capture(tmp_path):
    # A record from another transport (zcode) sits in the ledger already;
    # the canary session produced none. Freshness alone would false-green.
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
                  ledger_lines=[_ledger_line("zcode-session", 9999.0)],
                  persist=False)
    verdict = env.run()
    assert verdict["verdict"] == "red"


def test_drift_with_green_capture_rebaselines(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
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
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
                  bridge_pin="0.2.1-rc.5")
    verdict = env.run()
    assert verdict["verdict"] == "green"
    assert _surface(verdict, "drift")["status"] == "ok"


def test_endpoint_down_is_red(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
                  bridge_pin="0.2.1-rc.5")
    env.probe = lambda url, timeout=None: "8390" in url
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "endpoints")["status"] == "fail"


def test_missing_bridge_pin_is_reported_not_crash(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1),
                                                _entry("newsess", 2),
                                                _entry("childsess", 3)],
                  bridge_pin=None)
    verdict = env.run()
    assert _surface(verdict, "drift")["status"] == "warn"


def test_delegation_record_satisfies_second_assertion(tmp_path):
    # #72 slice 1: the canary also asserts a subagent lifecycle record for
    # the run's child session, written by the delegation plugin.
    env = FakeEnv(tmp_path, [_entry("old", 1)],
                  [_entry("old", 1), _entry("newsess", 2),
                   _entry("childsess", 3)],
                  bridge_pin="0.2.1-rc.5")
    verdict = env.run()
    assert verdict["verdict"] == "green"
    capture = _surface(verdict, "capture")
    assert capture["status"] == "ok"
    assert "delegation" in capture["detail"]


def test_missing_delegation_record_is_red(tmp_path):
    # Delegation happened (a child session exists) but no subagent record
    # reached the ledger - the pre-plugin state, or a broken plugin.
    env = FakeEnv(tmp_path, [_entry("old", 1)],
                  [_entry("old", 1), _entry("newsess", 2)],
                  bridge_pin="0.2.1-rc.5", delegate=False)
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_no_delegation_record"


def test_unattributed_delegation_is_red(tmp_path):
    # Slice 2: a delegation record without a parent session id does not
    # close the attribution loop - the trail still cannot say who
    # delegated.
    env = FakeEnv(tmp_path, [_entry("old", 1)],
                  [_entry("old", 1), _entry("newsess", 2),
                   _entry("childsess", 3)],
                  bridge_pin="0.2.1-rc.5", unattributed=True)
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_delegation_unattributed"


def test_dsh_missing_is_red_not_crash(tmp_path):
    # Under systemd, dsh may be off PATH: the runner raises instead of
    # returning a result. The capture surface must report a named fail,
    # never crash - the watchdog cannot itself be the crasher.
    env = FakeEnv(tmp_path, [_entry("old", 1)], [_entry("old", 1)],
                  bridge_pin="0.2.1-rc.5")
    def missing(_cmd, timeout=None):
        raise FileNotFoundError("dsh not found")
    env.runner = missing
    verdict = env.run()
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "capture")["reason"] == "capture_no_session"
    assert "dsh not found" in _surface(verdict, "capture")["detail"]


def test_fast_mode_never_probes_endpoints_or_spawns_dsh(tmp_path):
    # #94: the 30-minute timer must stay free of the inference stack. A
    # fast pass may ask dsh for its version (that is the drift check) but
    # must never run a headless session or touch the network.
    env = FakeEnv(tmp_path, [], [], bridge_pin="0.2.1-rc.5")

    def probe(_url, timeout=None):
        raise AssertionError("fast mode must not probe endpoints")
    env.probe = probe
    verdict = env.run(mode="fast")
    assert verdict["verdict"] == "green"
    assert verdict["mode"] == "fast"
    assert env.calls == [["dsh", "--version"]]
    assert _surface(verdict, "freshness")["status"] == "ok"
    assert "no dsh session activity" in _surface(verdict, "freshness")["detail"]


def test_fast_mode_unrecorded_activity_is_red(tmp_path):
    # A session the sink never saw is the silence the canary exists to
    # catch - no synthetic run required.
    env = FakeEnv(tmp_path, [_entry("s1", 500)], [_entry("s1", 500)],
                  ledger_lines=[])
    verdict = env.run(mode="fast", now=600)
    assert verdict["verdict"] == "red"
    assert _surface(verdict, "freshness")["reason"] == "capture_silent"


def test_fast_mode_recorded_activity_is_green(tmp_path):
    env = FakeEnv(tmp_path, [_entry("s1", 500)], [_entry("s1", 500)],
                  ledger_lines=[_ledger_line("s1")])
    verdict = env.run(mode="fast", now=600)
    assert verdict["verdict"] == "green"
    assert "all recorded" in _surface(verdict, "freshness")["detail"]


def test_fast_mode_ignores_sessions_older_than_window(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 100)], [_entry("old", 100)],
                  ledger_lines=[])
    verdict = env.run(mode="fast", now=100 + 1801)
    assert verdict["verdict"] == "green"
    assert "no dsh session activity" in _surface(verdict, "freshness")["detail"]


def test_fast_mode_never_rebaselines_drift(tmp_path):
    # Only a green full pass may record a version change as known-good;
    # a fast pass has no capture evidence to back it.
    env = FakeEnv(tmp_path, [], [], dsh_version="0.2.1-rc.5",
                  bridge_pin="0.2.1-rc.5")
    stored = {"dsh_version": "0.2.1-rc.4", "bridge_pin": "0.2.1-rc.5"}
    before = json.dumps(stored)
    verdict = env.run(stored_fingerprint=stored, mode="fast")
    assert verdict["verdict"] == "green"
    assert _surface(verdict, "drift")["status"] == "warn"
    assert verdict["rebaselined"] is False
    assert verdict["fingerprint"] is None
    assert env.fingerprint.read_text() == before


def test_full_run_rebaselines_after_green(tmp_path):
    env = FakeEnv(tmp_path, [_entry("old", 1)],
                  [_entry("old", 1), _entry("newsess", 2),
                   _entry("childsess", 3)],
                  bridge_pin="0.2.1-rc.5")
    stored = {"dsh_version": "0.2.1-rc.5", "bridge_pin": "0.2.1-rc.5"}
    verdict = env.run(stored_fingerprint=stored, mode="full")
    assert verdict["verdict"] == "green"
    assert verdict["rebaselined"] is True
    assert verdict["fingerprint"] == {
        "dsh_version": "0.2.1-rc.2", "bridge_pin": "0.2.1-rc.5"}


def test_unknown_mode_is_rejected(tmp_path):
    # Mode is a CLI-boundary value; a typo must fail loudly, not choose a
    # weaker check silently.
    env = FakeEnv(tmp_path, [], [], bridge_pin="0.2.1-rc.5")
    with pytest.raises(ValueError):
        env.run(mode="bogus")
