"""Operator CLI help/usage tests (8A). --help exits 0; usage problems
exit 1 (2 is reserved for a missing/corrupt DB); the deferred list is
visible from the help surface."""
import pytest

from minder_op.cli import EXIT_OK, EXIT_USAGE, main

DEFERRED_FRAGMENTS = ("web console", "log retention", "skill-risk denylist",
                      "auto skill apply")


def test_help_exits_zero(capsys):
    assert main(["--help"]) == EXIT_OK
    out = capsys.readouterr().out
    for fragment in DEFERRED_FRAGMENTS:
        assert fragment in out  # deferred list visible in help


def test_no_command_and_unknown_command_exit_usage(capsys):
    assert main([]) == EXIT_USAGE
    assert main(["bogus-command"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "error" in err or "usage" in err.lower()


def test_flags_lists_env_and_persistence_note(monkeypatch, capsys):
    monkeypatch.setenv("MINDER_ASSIST", "retrieve")
    monkeypatch.delenv("MINDER_CLASSIFIER", raising=False)
    monkeypatch.delenv("MINDER_DECISION", raising=False)
    assert main(["flags"]) == EXIT_OK  # flags never touches the DB
    out = capsys.readouterr().out
    assert "MINDER_ASSIST=retrieve" in out
    assert "MINDER_CLASSIFIER=(unset)" in out
    assert "MINDER_DECISION=(unset)" in out
    assert "CLI cannot persist flags" in out
    assert "decision_skill" in out  # full vocabulary documented


def test_promote_requires_instruction(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "m.sqlite"), "lessons", "promote",
                 "ep_x", "--yes"]) == EXIT_USAGE
    assert "instruction" in capsys.readouterr().err


def test_package_module_entrypoint():
    import subprocess
    import sys
    repo = str(pytest.__file__)  # noqa: F841 — ensure import machinery ok
    proc = subprocess.run(
        [sys.executable, "-m", "minder_op", "--help"],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert "minder-op" in proc.stdout
