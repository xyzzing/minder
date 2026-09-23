"""Doctor tests (operator usability #1): one command that says whether
the install is healthy and why not. Checks are read-only; the proxy
probe is loopback-only and skipped via --no-probe in tests (tests never
dial a real network service — the one probe test dials a closed
loopback port). Staleness is deterministic via now= injection.
"""
import json
import time

from memory import db as _db
from minder_op import doctor
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

SECRET = "sk-proj-operatorleak99999999"


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def _wiring(tmp_path, share):
    """Fake a zcode install the way install.sh writes it: share dir
    with hook.py, and the share path patched into hooks config."""
    (share / "minder").mkdir(parents=True, exist_ok=True)
    (share / "minder" / "hook.py").write_text("# hook\n")
    cfg = share / "zcode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(json.dumps(
        {"hooks": {"events": [{"hooks": [
            {"type": "process", "command":
             f"python3 {share}/minder/hook.py"}]}]}}))
    return share / "minder", cfg / "config.json"


def _seed_event(dbp, ts, key="bash|keyerror|k|a.py"):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO events (event_id, ts, event_type,"
                  " session_id, task_id, repo, repo_version, tool,"
                  " failure_key, action_fingerprint, payload_json,"
                  " redaction_status)"
                  " VALUES ('ev_d', ?, 'tool_failure', 's', 't', '/r',"
                  " '', 'bash', ?, 'fp', '{}', 'redacted')", (ts, key))
    finally:
        conn.close()


def test_doctor_healthy_install(tmp_path, monkeypatch, capsys):
    dbp = _mig(tmp_path)
    share_dir, _cfg = _wiring(tmp_path, tmp_path / "home")
    monkeypatch.setenv("MINDER_SHARE", str(tmp_path / "home" / "minder"))
    monkeypatch.setenv("MINDER_ZCODE_CONFIG", str(_cfg))
    monkeypatch.delenv("MINDER_ASSIST", raising=False)
    code = main(["--db", str(dbp), "doctor", "--no-probe"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "db" in out and "schema v10" in out
    assert "flags" in out and "defaults" in out.lower()
    assert "wiring" in out
    assert "benchmark" in out and "coding-core-v1" in out
    assert "healthy" in out.lower()


def test_doctor_missing_or_corrupt_db_exits_1(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nope.sqlite"),
                 "doctor", "--no-probe"]) == EXIT_USAGE
    assert "not found" in capsys.readouterr().err
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a sqlite database" * 20)
    assert main(["--db", str(bad), "doctor", "--no-probe"]) == EXIT_USAGE
    assert "error" in capsys.readouterr().err.lower()


def test_doctor_invalid_flag_value_fails(tmp_path, monkeypatch, capsys):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_ASSIST", "bogus-mode")
    assert main(["--db", str(dbp), "doctor",
                 "--no-probe"]) == EXIT_USAGE
    assert "bogus-mode" in capsys.readouterr().out


def test_doctor_valid_flag_ok(tmp_path, monkeypatch, capsys):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_ASSIST", "decision_skill")
    main(["--db", str(dbp), "doctor", "--no-probe"])
    out = capsys.readouterr().out
    assert "decision_skill" in out


def test_doctor_warns_when_wiring_missing(tmp_path, monkeypatch, capsys):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SHARE", str(tmp_path / "absent-share"))
    monkeypatch.setenv("MINDER_ZCODE_CONFIG",
                       str(tmp_path / "absent" / "config.json"))
    assert main(["--db", str(dbp), "doctor", "--no-probe"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "warn" in out.lower() and "wiring" in out


def test_doctor_event_staleness_deterministic(tmp_path, monkeypatch):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SHARE", str(tmp_path / "x"))
    monkeypatch.setenv("MINDER_ZCODE_CONFIG", str(tmp_path / "x.json"))
    now = time.time()
    fmt = lambda secs: time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                     time.gmtime(now - secs))
    # events is append-only, so each staleness case gets its own store
    recent = _mig(tmp_path / "recent")
    _seed_event(recent, fmt(3600))
    report = doctor.run_checks(recent, probe=False, now=now)
    ev = next(c for c in report["checks"] if c["id"] == "events")
    assert ev["status"] == "ok" and "1h" in ev["detail"]

    old = _mig(tmp_path / "old")
    _seed_event(old, fmt(8 * 86400))
    report = doctor.run_checks(old, probe=False, now=now)
    ev = next(c for c in report["checks"] if c["id"] == "events")
    assert ev["status"] == "warn" and report["healthy"] is True

    empty = _mig(tmp_path / "empty")
    report = doctor.run_checks(empty, probe=False, now=now)
    ev = next(c for c in report["checks"] if c["id"] == "events")
    assert ev["status"] == "info"


def test_doctor_benchmark_and_baseline_lines(tmp_path, monkeypatch,
                                             capsys):
    dbp = _mig(tmp_path)
    empty_root = tmp_path / "bench"
    empty_root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(empty_root))
    main(["--db", str(dbp), "doctor", "--no-probe"])
    out = capsys.readouterr().out
    assert "no benchmark suites" in out
    assert "no baseline" in out


def test_doctor_proxy_probe_loopback(tmp_path, monkeypatch, capsys):
    """Closed loopback port -> 'not running' info (never a failure).
    Reachable proxy -> ok with alias count (probe function stubbed —
    no test spawns or dials a real server)."""
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_PORT", "1")  # loopback, refused
    main(["--db", str(dbp), "doctor"])
    out = capsys.readouterr().out
    assert "proxy" in out and "not running" in out
    monkeypatch.setattr(doctor, "_probe_proxy",
                        lambda port: (True, "5 aliases"))
    main(["--db", str(dbp), "doctor"])
    assert "5 aliases" in capsys.readouterr().out


def test_doctor_json_shape(tmp_path, monkeypatch):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SHARE", str(tmp_path / "x"))
    monkeypatch.setenv("MINDER_ZCODE_CONFIG", str(tmp_path / "x.json"))
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert main(["--db", str(dbp), "doctor", "--no-probe",
                     "--json"]) == EXIT_OK
    report = json.loads(buf.getvalue())
    assert isinstance(report["healthy"], bool)
    ids = {c["id"] for c in report["checks"]}
    assert {"db", "schema", "flags", "wiring", "events", "benchmark",
            "baseline"} <= ids
    for check in report["checks"]:
        assert check["status"] in ("ok", "warn", "info", "fail")
        assert check["detail"]
