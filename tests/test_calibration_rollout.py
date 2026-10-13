"""The guarded live rollout: drop-in, verify, rollback (issue #36).

A promoted scale still has to reach the running proxy. The live install
has 831 `below_confidence` abstentions and zero applied bands, so the
floor is the thing being changed, and changing it live is a config write
plus a restart on a box the operator is working on.

The shape this pins is the one the repo already sanctions for live
changes (`minder_op/engines.py`: backup, write, verify, restore) and the
one issue #33 taught about units: systemd applies drop-ins after the
base unit, so a drop-in wins and rewriting the base unit changes
nothing. So the rollout writes a drop-in, never the base unit, and
rollback removes exactly what it wrote.
"""

import pytest


# getattr, not a module-level import: the proven-red gate runs this file
# against pre-change code, where a missing module fails collection and
# reports no symbol instead of the assertion that shows the lifecycle
# was absent.
rollout = getattr(
    __import__("minder_op", fromlist=["rollout"]), "rollout", None)

UNIT = "minder-proxy.service"
BASE = ("# /home/op/.config/systemd/user/minder-proxy.service\n"
        "[Service]\nEnvironment=MINDER_PORT=8390\n"
        "Environment=MINDER_UPSTREAM=http://127.0.0.1:8081\n")


def _run(responses):
    """A systemctl runner returning canned (rc, out, err) per argv, and
    recording every call so a test can prove the base unit was never
    rewritten."""
    calls = []

    def run(cmd):
        calls.append(cmd)
        key = " ".join(cmd)
        if key not in responses:
            raise AssertionError(f"unexpected systemctl call: {key}")
        return responses[key]
    run.calls = calls
    return run


def _env_show(scale=None, dropin_path=None):
    """`systemctl --user show -p Environment` for the merged unit."""
    items = ["MINDER_PORT=8390", "MINDER_UPSTREAM=http://127.0.0.1:8081"]
    if scale is not None:
        items.append(f"MINDER_LAYA_MARGIN_SCALE={scale}")
    return (0, "Environment=" + " ".join(items) + "\n", "")


def _cat(dropin_path):
    out = BASE
    if dropin_path is not None:
        out += (f"\n# {dropin_path}\n[Service]\n"
                f"Environment=MINDER_LAYA_MARGIN_SCALE=18.0\n")
    return (0, out, "")


def test_apply_writes_a_drop_in_and_never_the_base_unit(tmp_path):
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    plan = rollout.plan(UNIT, unit_dir, margin_scale=18.0)
    assert plan["dropin"].endswith(f"{UNIT}.d/minder-calibration.conf")
    assert "MINDER_LAYA_MARGIN_SCALE=18.0" in plan["contents"]
    assert plan["base_unit"] == str(unit_dir / UNIT)
    # the write target is the drop-in, never the unit file itself
    assert plan["dropin"] != plan["base_unit"]
    assert plan["contents"].count("[Service]") == 1
    assert "MINDER_PORT" not in plan["contents"]
    assert "MINDER_UPSTREAM" not in plan["contents"]


def test_apply_installs_verifies_and_reports_the_effective_scale(
        tmp_path):
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    (unit_dir / UNIT).parent.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(BASE)
    plan = rollout.plan(UNIT, unit_dir, margin_scale=18.0)
    run = _run({"systemctl --user daemon-reload": (0, "", ""),
                "systemctl --user restart " + UNIT: (0, "", ""),
                "systemctl --user show " + UNIT + " -p Environment":
                    _env_show(18.0),
                "systemctl --user cat --no-pager " + UNIT:
                    _cat(plan["dropin"])})
    result = rollout.apply(plan, run=run, sleep=lambda _s: None)
    assert result["status"] == "ok"
    assert result["effective_scale"] == 18.0
    assert plan["contents"] == (
        __import__("pathlib").Path(plan["dropin"]).read_text())
    # the base unit is byte-identical: the drop-in is additive
    assert (unit_dir / UNIT).read_text() == BASE
    argv = [" ".join(cmd) for cmd in run.calls]
    assert ["systemctl --user daemon-reload",
            "systemctl --user restart " + UNIT] in [
        argv[i:i + 2] for i in range(len(argv))]
    assert not [c for c in run.calls if "edit" in c]


def test_apply_refuses_when_the_effective_scale_is_not_what_was_written(
        tmp_path):
    """Verify after write, the same law as `_set_active`. A drop-in that
    systemd did not pick up leaves the old value effective, and an
    operator who was told "installed" would be running a number that
    exists only in a file."""
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    (unit_dir / UNIT).parent.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(BASE)
    plan = rollout.plan(UNIT, unit_dir, margin_scale=18.0)
    run = _run({"systemctl --user daemon-reload": (0, "", ""),
                "systemctl --user restart " + UNIT: (0, "", ""),
                "systemctl --user show " + UNIT + " -p Environment":
                    _env_show(None),
                "systemctl --user cat --no-pager " + UNIT:
                    _cat(plan["dropin"])})
    with pytest.raises(rollout.RolloutError) as exc:
        rollout.apply(plan, run=run, sleep=lambda _s: None)
    assert "verify" in str(exc.value).lower()
    # and it rolled its own write back rather than leaving a dead drop-in
    assert not __import__("pathlib").Path(plan["dropin"]).exists()


def test_rollback_removes_the_drop_in_and_leaves_the_base_unit(tmp_path):
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    (unit_dir / UNIT).parent.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(BASE)
    plan = rollout.plan(UNIT, unit_dir, margin_scale=18.0)
    install = _run({"systemctl --user daemon-reload": (0, "", ""),
                    "systemctl --user restart " + UNIT: (0, "", ""),
                    "systemctl --user show " + UNIT + " -p Environment":
                        _env_show(18.0),
                    "systemctl --user cat --no-pager " + UNIT:
                        _cat(plan["dropin"])})
    rollout.apply(plan, run=install, sleep=lambda _s: None)
    downgraded = _env_show(10.0)
    run = _run({"systemctl --user daemon-reload": (0, "", ""),
                "systemctl --user restart " + UNIT: (0, "", ""),
                "systemctl --user show " + UNIT + " -p Environment":
                    downgraded,
                "systemctl --user cat --no-pager " + UNIT: _cat(None)})
    result = rollout.rollback(UNIT, unit_dir, run=run,
                              sleep=lambda _s: None)
    assert result["status"] == "ok"
    assert not __import__("pathlib").Path(plan["dropin"]).exists()
    assert (unit_dir / UNIT).read_text() == BASE
    assert result["effective_scale"] == 10.0


def test_rollback_without_a_drop_in_is_a_no_not_an_error(tmp_path):
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    run = _run({})
    result = rollout.rollback(UNIT, unit_dir, run=run,
                              sleep=lambda _s: None)
    assert result["status"] == "absent"
    assert run.calls == []


def test_a_restart_that_fails_rolls_the_write_back(tmp_path):
    """I2: a live change needs a recovery path. A unit that will not
    start with the new drop-in must not be left in that state."""
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    (unit_dir / UNIT).parent.mkdir(parents=True, exist_ok=True)
    (unit_dir / UNIT).write_text(BASE)
    plan = rollout.plan(UNIT, unit_dir, margin_scale=18.0)
    run = _run({"systemctl --user daemon-reload": (0, "", ""),
                "systemctl --user restart " + UNIT:
                    (1, "", "Job for minder-proxy.service failed\n"),
                "systemctl --user show " + UNIT + " -p Environment":
                    _env_show(None),
                "systemctl --user cat --no-pager " + UNIT: _cat(None)})
    with pytest.raises(rollout.RolloutError):
        rollout.apply(plan, run=run, sleep=lambda _s: None)
    assert not __import__("pathlib").Path(plan["dropin"]).exists()
    assert (unit_dir / UNIT).read_text() == BASE


def test_the_check_reports_the_pinned_scale_versus_the_effective_one(
        tmp_path):
    """doctor id `calibration`: the promotion record says a scale was
    fitted and pinned; the unit says what the proxy will actually load.
    They disagree when a reinstall or an operator edit dropped the
    drop-in, which is the inert-flag shape the repo has already been
    bitten by twice."""
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    run = _run({"systemctl --user show " + UNIT + " -p Environment":
                    _env_show(None)})
    status = rollout.check(UNIT, pinned_scale=18.0, unit_dir=unit_dir,
                           run=run)
    assert status[0] == "calibration"
    assert status[1] == "warn"
    assert "18.0" in status[2]


def test_the_check_is_ok_when_the_effective_scale_matches_the_record(
        tmp_path):
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    run = _run({"systemctl --user show " + UNIT + " -p Environment":
                    _env_show(18.0)})
    status = rollout.check(UNIT, pinned_scale=18.0, unit_dir=unit_dir,
                           run=run)
    assert status[1] == "ok"
    assert "18.0" in status[2]


def test_no_pinned_record_is_informational_not_a_warning(tmp_path):
    """A machine that has never run the loop has nothing to compare;
    the absence of a promotion record is a fact, not a finding."""
    assert rollout is not None
    unit_dir = tmp_path / "systemd" / "user"
    status = rollout.check(UNIT, pinned_scale=None, unit_dir=unit_dir,
                           run=_run({}))
    assert status[1] == "info"
    assert "no promotion record" in status[2]


def test_the_scale_key_is_a_single_constant_shared_by_plan_and_parse():
    """M2: the writer and the reader must agree on the key name, and a
    literal typed twice is how they stop agreeing."""
    assert rollout is not None
    assert rollout.SCALE_ENV_KEY == "MINDER_LAYA_MARGIN_SCALE"
    from minder_op import unit_upstream
    text = "Environment=%s=18.0\n" % rollout.SCALE_ENV_KEY
    assert unit_upstream.parse_unit_environment(text) is None  # not upstream
    assert rollout.parse_unit_scale(text) == 18.0


def test_the_default_unit_dir_is_the_real_systemd_user_directory():
    """M2: the gate's denominator. If the default path is wrong the
    rollout writes a file systemd never reads and still reports ok."""
    assert rollout is not None
    default = rollout.default_unit_dir()
    assert default.name == "user"
    assert default.parent.name == "systemd"


def test_status_says_what_this_process_is_answering_at(tmp_path, monkeypatch):
    """The pinned scale and the unit's scale are two claims; the scale
    this process would map with is a third, and it is the only one
    readable where systemd cannot be reached. A record pinning 18.0 with
    nothing in the environment has to say the router is still answering
    at the provider's shipped scale."""
    from minder_op import rollout
    from minder_decision.providers.laya import MARGIN_SCALE_ENV

    monkeypatch.delenv(MARGIN_SCALE_ENV, raising=False)
    note = rollout.live_scale_note({"margin_scale": 18.0,
                                    "baseline_scale": 20.0})
    assert "pins margin scale 18.0" in note
    assert "20.0" in note
    # and with the promoted value in the environment, it says so
    monkeypatch.setenv(MARGIN_SCALE_ENV, "18.0")
    note = rollout.live_scale_note({"margin_scale": 18.0,
                                    "baseline_scale": 20.0})
    assert note == "this process maps at the pinned scale 18.0"
    # no record, no claim
    assert rollout.live_scale_note(None) is None
