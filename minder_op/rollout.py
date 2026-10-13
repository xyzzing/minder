"""The guarded live rollout of a promoted scale (issue #36).

A promotion record says a scale was fitted and gated. This is the part
that makes it true on a running box: the proxy loads its environment,
so the scale arrives as a systemd drop-in.

Two laws from the repo, both earned. I2: a live change needs a recovery
path, so this follows the sanctioned engines.py shape - write, verify,
restore on a failed verify. And issue #33: systemd applies drop-ins
after the base unit, so an installer that rewrites the base unit
changes nothing while a drop-in stands. So this writes a drop-in and
never touches the base unit, and rollback removes exactly what it
wrote. Nothing here migrates or writes the ledger: rollback is a
removed file and a restart.

Reading the unit is `minder_op/unit_upstream`'s job; this module owns
the write lifecycle, which is why it is a separate file (C2)."""
import json
import os
from pathlib import Path

from minder_decision.providers.laya import MARGIN_SCALE_ENV

from minder_op.engines import _default_run

# The drop-in name. Fixed, so rollback can find what apply wrote and a
# second apply is an idempotent overwrite rather than a pile of files.
DROPIN_NAME = "minder-calibration.conf"
# The env key the scale travels in. Named here so the writer and the
# reader of the unit's Environment provably agree on it; the value
# itself lives in the provider that consumes it.
SCALE_ENV_KEY = MARGIN_SCALE_ENV
# The value the proxy must be dialling for the drop-in to count as
# loaded; read back from the merged unit, never from the file we wrote.
ENV_SHOW = "Environment"


class RolloutError(RuntimeError):
    """The rollout stopped and left the machine as it found it."""


def default_unit_dir():
    """The real systemd user unit directory. A default that points
    anywhere else writes a file systemd never reads and still reports
    ok, which is the failure this module exists to avoid."""
    override = os.environ.get("MINDER_SYSTEMD_USER_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "systemd" / "user"


def parse_unit_scale(text):
    """The last MARGIN_SCALE_ENV in a systemd Environment assignment
    list. Same merge rule as unit_upstream.parse_unit_environment: one
    line per unit, drop-ins last, a later assignment of a key wins."""
    values = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        for item in line[len("Environment="):].split():
            key, sep, value = item.partition("=")
            if sep and key == MARGIN_SCALE_ENV:
                values.append(value.strip('"\''))
    if not values:
        return None
    try:
        return float(values[-1])
    except ValueError:
        return None


def plan(unit, unit_dir=None, *, margin_scale):
    """What apply would do, without doing it. The base unit is named so
    an operator can see it is not the thing being written."""
    if margin_scale is None:
        raise RolloutError("no scale to roll out")
    scale = float(margin_scale)
    if scale <= 0.0:
        raise RolloutError(f"margin scale must be positive, got {scale}")
    unit_dir = Path(unit_dir or default_unit_dir())
    dropin_dir = unit_dir / f"{unit}.d"
    contents = (f"# minder calibration promotion (issue #36)\n"
                f"# fitted on the fitting split, gated on the eval set\n"
                f"[Service]\nEnvironment={MARGIN_SCALE_ENV}={scale}\n")
    return {"unit": unit, "unit_dir": str(unit_dir),
            "base_unit": str(unit_dir / unit),
            "dropin": str(dropin_dir / DROPIN_NAME),
            "dropin_dir": str(dropin_dir), "contents": contents,
            "margin_scale": scale}


def _show(unit, run):
    rc, out, err = run(["systemctl", "--user", "show", unit,
                        "-p", ENV_SHOW])
    if rc != 0:
        raise RolloutError(f"could not read {unit} {ENV_SHOW} "
                           f"({err.strip() or f'rc={rc}'})")
    return out


def _reload_restart(unit, run):
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "restart", unit]):
        rc, _out, err = run(cmd)
        if rc != 0:
            raise RolloutError(f"{' '.join(cmd)} failed "
                               f"({err.strip() or f'rc={rc}'})")


def apply(plan_row, *, run=None, sleep=None):
    """Install the drop-in, reload, restart, verify the effective value.

    Verify-after-write is the whole point: a drop-in systemd did not
    pick up leaves the old scale effective, and an operator told
    "installed" would be running a number that exists only in a file.
    A failed write, restart or verify removes the drop-in again, so the
    unit comes back on the value it had before."""
    run = run or _default_run
    sleep = sleep or (lambda _s: None)
    dropin = Path(plan_row["dropin"])
    before = parse_unit_scale(_show(plan_row["unit"], run))
    dropin.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if dropin.is_file():
        backup = dropin.read_text(encoding="utf-8")
    try:
        dropin.write_text(plan_row["contents"], encoding="utf-8")
        _reload_restart(plan_row["unit"], run)
        effective = parse_unit_scale(_show(plan_row["unit"], run))
        if effective is None or abs(effective
                                    - plan_row["margin_scale"]) > 1e-9:
            raise RolloutError(
                f"verify failed: {plan_row['unit']} effective scale is "
                f"{effective}, not {plan_row['margin_scale']}")
        return {"status": "ok", "dropin": str(dropin),
                "previous_scale": before, "effective_scale": effective,
                "unit": plan_row["unit"]}
    except RolloutError:
        _remove_dropin(dropin, backup, plan_row["unit"], run)
        raise


def _remove_dropin(dropin, backup, unit, run):
    """Put the unit back: restore the previous drop-in if there was
    one, otherwise delete the file. Then reload and restart so the
    effective value matches the files."""
    try:
        if backup is not None:
            dropin.write_text(backup, encoding="utf-8")
        elif dropin.is_file():
            dropin.unlink()
        directory = dropin.parent
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
        _reload_restart(unit, run)
    except (OSError, RolloutError):
        # the write-back is best effort; the caller still gets the
        # original failure, and doctor's `calibration` line reports the
        # divergence if this one silently did not land
        pass


def rollback(unit, unit_dir=None, *, run=None, sleep=None):
    """Remove the promotion drop-in and restart. No migration, no
    ledger state: the previous value is whatever the unit had before."""
    run = run or _default_run
    sleep = sleep or (lambda _s: None)
    unit_dir = Path(unit_dir or default_unit_dir())
    dropin = unit_dir / f"{unit}.d" / DROPIN_NAME
    if not dropin.is_file():
        return {"status": "absent", "dropin": str(dropin), "unit": unit}
    _remove_dropin(dropin, None, unit, run)
    return {"status": "ok", "dropin": str(dropin), "unit": unit,
            "effective_scale": parse_unit_scale(_show(unit, run))}


def check(unit, *, pinned_scale, unit_dir=None, run=None):
    """doctor id `calibration`: the pinned scale versus the effective
    one. The repo has been bitten twice by a config that names a thing
    the running process never loaded, and a promotion record is exactly
    such a claim."""
    run = run or _default_run
    if pinned_scale is None:
        return ("calibration", "info",
                "no promotion record - the margin scale is the shipped "
                "constant; `minder-op calibrate promote` pins a fitted one")
    unit_dir = Path(unit_dir or default_unit_dir())
    dropin = unit_dir / f"{unit}.d" / DROPIN_NAME
    try:
        effective = parse_unit_scale(_show(unit, run))
    except RolloutError as exc:
        return ("calibration", "info", f"{exc}; pinned scale "
                                       f"{pinned_scale} unverified")
    if effective is None:
        missing = "" if dropin.is_file() else f" (no drop-in at {dropin})"
        return ("calibration", "warn",
                f"promotion pins margin scale {pinned_scale} but {unit} "
                f"declares no {MARGIN_SCALE_ENV}{missing} - the proxy is "
                "running the shipped scale, not the fitted one")
    if abs(effective - float(pinned_scale)) <= 1e-9:
        return ("calibration", "ok",
                f"{unit} loads margin scale {effective}, matching the "
                "promotion record")
    return ("calibration", "warn",
            f"{unit} loads margin scale {effective}, not the promoted "
            f"{pinned_scale} - re-run `minder-op calibrate apply --yes`")


def record_scale(record):
    """The scale a promotion record pins, or None when there is no
    record. Kept here so doctor and the CLI read the same key."""
    if not isinstance(record, dict):
        return None
    return record.get("margin_scale")


def effective_scale(provider_scale_default=None):
    """The margin scale this process would map with: the promoted value
    if the environment carries one, otherwise the provider's constant.
    The rollout half of the CLI needs it to say what a rollback would
    fall back to, and restating the shipped constant here is how writer
    and reader stop agreeing."""
    from minder_decision.providers.laya import margin_scale
    if provider_scale_default is None:
        from minder_decision.providers.laya import CALIBRATION_MARGIN_SCALE
        provider_scale_default = CALIBRATION_MARGIN_SCALE
    return margin_scale(float(provider_scale_default))


def live_scale_note(record):
    """One line saying what the difficulty router is answering at right
    now, against what the record pins.

    `calibrate status` and `calibrate apply` run in a shell that knows
    nothing about the unit's environment, and on this box the user bus
    is unreachable, so the process view is the only reading available
    here. It is labelled as such: the pinned scale is the claim, the
    effective scale is what this process would use, and the unit is what
    doctor checks."""
    pinned = record_scale(record)
    if pinned is None:
        return None
    default = (record or {}).get("baseline_scale")
    effective = effective_scale(default)
    if abs(effective - float(pinned)) <= 1e-9:
        return (f"this process maps at the pinned scale {effective}")
    fell_back = (f"this process still maps at {effective} - the shipped "
                 f"scale for the provider under calibration")
    return (f"promotion pins margin scale {pinned}, but {fell_back}: the "
            "difficulty contract is answering at the old scale until "
            "`minder-op calibrate apply --yes` runs")


def render_plan(plan_row):
    return json.dumps(plan_row, indent=2, sort_keys=True)


def promotion_check(unit=None):
    """doctor id `calibration`: the pinned margin scale, whether the
    facts it was fitted on still hold, and what the unit loads.

    The record and the unit are two different claims, and this install
    has been bitten twice by a config naming a thing the running
    process never loaded, so the check states both numbers. A
    promotion whose label digest or bundle revision moved describes a
    suite and an artifact that no longer exist, which is a finding even
    when the scale itself is loaded."""
    from minder_decision import calibrate
    import minder
    unit = unit or minder.PROXY_UNIT
    record = calibrate.read_promotion()
    if not record:
        return check(unit, pinned_scale=None)
    drift = calibrate.record_drift(record,
                                   cases_path=record.get("cases_path"),
                                   bundle_path=record.get("bundle_path"))
    status = check(unit, pinned_scale=record_scale(record))
    if drift:
        return (status[0], "warn",
                f"{status[2]}; promotion invalidated by "
                + ", ".join(sorted(drift)) + " - re-run "
                "`minder-op calibrate promote --yes`")
    return status
