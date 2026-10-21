"""The proxy unit's upstream vs the engine registry (issue #33).

Two live defects: `install.sh` rewrote the base unit's
`MINDER_UPSTREAM` to its own default while an operator drop-in declared a
different one and silently outranked it, and `minder-op doctor` compared
only the config's `active_engine`, so it agreed with the drop-in and
never saw the divergence. The effective upstream is what
`systemctl --user show -p Environment` says, drop-ins included.
"""
import pytest
import shutil

from minder_op import engines

# getattr, not a module-level import: the proven-red gate runs this file
# against pre-change code, and a missing module there fails the whole file
# at import, which reports a missing symbol instead of the assertion that
# shows the check was absent.
unit_upstream = getattr(
    __import__("minder_op", fromlist=["unit_upstream"]), "unit_upstream",
    None)


def _run(responses):
    """A systemctl runner that returns canned (rc, out, err) per argv."""
    calls = []

    def run(cmd):
        calls.append(cmd)
        key = " ".join(cmd)
        if key not in responses:
            raise AssertionError(f"unexpected systemctl call: {key}")
        return responses[key]
    run.calls = calls
    return run


SHOW = "systemctl --user show minder-proxy.service -p Environment"
CAT = "systemctl --user cat --no-pager minder-proxy.service"


def test_disagreeing_base_unit_and_drop_in_warn_naming_both(tmp_path):
    assert unit_upstream is not None
    """The live case: the installer wrote 8080 into the base unit, the
    operator's drop-in declares 8081, and nothing anywhere said so."""
    base = ("# /home/op/.config/systemd/user/minder-proxy.service\n"
            "[Service]\nEnvironment=MINDER_UPSTREAM=http://127.0.0.1:8080\n")
    run = _run({SHOW: (0, "Environment=MINDER_PORT=8390 "
                            "MINDER_UPSTREAM=http://127.0.0.1:8081\n", ""),
                CAT: (0, base, "")})
    status = unit_upstream.check("minder-proxy.service",
                                         "http://127.0.0.1:8080",
                                         "active", run=run)
    assert status[0] == "engine-upstream"
    assert status[1] == "warn"
    assert "http://127.0.0.1:8081" in status[2]
    assert "http://127.0.0.1:8080" in status[2]


def test_agreeing_values_are_ok(tmp_path):
    assert unit_upstream is not None
    run = _run({SHOW: (0, "Environment=MINDER_UPSTREAM="
                            "http://127.0.0.1:8081\n", "")})
    status = unit_upstream.check("minder-proxy.service",
                                         "http://127.0.0.1:8081",
                                         "active", run=run)
    assert status[1] == "ok"
    assert "http://127.0.0.1:8081" in status[2]


def test_a_unit_that_declares_no_upstream_is_context_not_a_finding():
    assert unit_upstream is not None
    """MINDER_UPSTREAM may live in the environment rather than the unit,
    so an absent key is not a divergence."""
    run = _run({SHOW: (0, "Environment=MINDER_PORT=8390\n", "")})
    status = unit_upstream.check("minder-proxy.service",
                                         "http://127.0.0.1:8081",
                                         "active", run=run)
    assert status[1] == "info"
    assert "no MINDER_UPSTREAM" in status[2]


def test_a_query_that_fails_is_info_not_a_warning_about_nothing():
    assert unit_upstream is not None
    """`show` fails for a unit that does not exist. The check must not
    turn its own failure into a claim about the upstream."""
    run = _run({SHOW: (4, "", "Failed to load unit: No such file\n")})
    status = unit_upstream.check("minder-proxy.service",
                                         "http://127.0.0.1:8081",
                                         "inactive", run=run)
    assert status[1] == "info"
    assert "could not read" in status[2]


def test_no_unit_is_no_check():
    assert unit_upstream is not None
    """An install with no systemd unit for the engine has nothing to
    compare; the config value is the only one."""
    assert unit_upstream.check(None, "http://127.0.0.1:8081",
                                       None) is None


def test_the_query_asks_for_environment_only():
    assert unit_upstream is not None
    """M2: the gate's input. If the argv is wrong the check silently
    reports nothing about a real divergence."""
    run = _run({SHOW: (0, "Environment=MINDER_UPSTREAM=x\n", "")})
    unit_upstream.check("minder-proxy.service", "x", "active",
                                run=run)
    assert run.calls[0] == ["systemctl", "--user", "show",
                            "minder-proxy.service", "-p", "Environment"]


def test_the_parsing_matches_systemd_own_renderer():
    """M2: the fake runner above feeds strings I wrote, so on its own the
    check only agrees with my guess about the format. `systemctl --user
    show -p Environment` is systemd's own rendering of a unit merged with
    its drop-ins, so where a minder proxy unit is actually resolvable,
    run the parser against the manager's real output and report what it
    parsed. Where the host cannot answer the question, skip with the
    reason (issue #39): the host's state is not a test failure."""
    if shutil.which("systemctl") is None:
        pytest.skip("no systemctl on this host")
    rc, out, err = engines._default_run(
        ["systemctl", "--user", "show", "minder-proxy.service",
         "-p", "Environment"])
    if rc != 0:
        pytest.skip("no minder-proxy.service in this user session "
                    f"({err.strip() or f'rc={rc}'})")
    parsed = unit_upstream.parse_unit_environment(out)
    if parsed is None:
        # a systemd user manager with no minder unit: the check's own
        # info branch, never an assertion about a value the host has
        # never declared
        pytest.skip("minder-proxy.service declares no MINDER_UPSTREAM "
                    f"(Environment: {out.strip() or 'empty'})")
    # the value systemd reports is one the unit or a drop-in declared,
    # not an artifact of splitting on the wrong separator
    assert f"MINDER_UPSTREAM={parsed}" in out
    # the unit is resolvable, so `cat` resolves it too: the parsed value
    # must match what the unit files actually declare
    crc, cout, cerr = engines._default_run(
        ["systemctl", "--user", "cat", "--no-pager", "minder-proxy.service"])
    assert crc == 0, f"cat failed for a resolvable unit: {cerr.strip()}"
    assert unit_upstream.base_unit_upstream("minder-proxy.service",
                                            engines._default_run) is not None
    assert parsed in cout


def test_the_ci_host_shape_is_reported_not_asserted():
    """The GitHub runner shape, pinned (issue #39): Ubuntu runners have a
    systemd user manager and no minder install, so the real `show` call
    answers rc 0 with an `Environment=` line that names no
    `MINDER_UPSTREAM`. The old test read that output and then asserted a
    parse succeeded, which turned the host's state into a red main. The
    host-shaped path now routes through the check, whose info branch this
    asserts: the same response the doctor would report, and the skip in
    the test above can only fire on a host that really has no unit."""
    if unit_upstream is None:  # pre-change code: fail, never vacuous-pass
        pytest.fail("unit_upstream missing: the check this gate protects "
                    "does not exist")
    runner = _run({SHOW: (0, "Environment=MINDER_PORT=8390\n", "")})
    status = unit_upstream.check("minder-proxy.service",
                                 "http://127.0.0.1:8081",
                                 "active", run=runner)
    assert status[1] == "info"
    assert "no MINDER_UPSTREAM" in status[2]
