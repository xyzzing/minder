"""Doctor tests (operator usability #1): one command that says whether
the install is healthy and why not. Checks are read-only; the proxy
probe is loopback-only and skipped via --no-probe in tests (tests never
dial a real network service — the one probe test dials a closed
loopback port). Staleness is deterministic via now= injection.
"""
import json
import os
import time

from minder_memory import db as _db
from minder_op import doctor


def _latest_schema():
    return max(int(f.name.split("_", 1)[0]) for f in
               _db.MIGRATIONS_DIR.glob("*.sql") if f.name[0].isdigit())
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
    assert "db" in out
    assert f"schema v{_latest_schema()}" in out
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


def test_doctor_accepts_block_guard_mode(tmp_path, monkeypatch, capsys):
    """'block' is a real enabling value (the PreToolUse pre-emption), so a
    correctly armed install must not report as a flag typo."""
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SUCCESS_GUARD", "block")
    assert main(["--db", str(dbp), "doctor",
                 "--no-probe"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "MINDER_SUCCESS_GUARD=block" in out


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


def test_a_capped_log_scan_is_labelled_a_partial_view(tmp_path, monkeypatch):
    """100% is not a measurement when the scan could not see the whole
    window. The log scan caps how much of each session log it reads, so
    the invocation count can be a floor; the coverage line has to say so
    rather than report a clean verdict off a partial view."""
    def _fake_build(_db_path, now=None, window_hours=1):
        return {"coverage": {"invocations": 40, "persisted": 40,
                             "ratio": 1.0, "min_ratio": 0.95,
                             "sessions": [], "scanned": 1,
                             "truncated": True, "complete": False,
                             "window_hours": window_hours},
                "sink": {"configured": True, "reachable": True,
                         "url": "http://127.0.0.1:8392", "stats": {}},
                "stores": [], "warnings": [], "ok": True, "sandbox": {}}
    monkeypatch.setattr("minder_op.capture.build", _fake_build)
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_SHARE", str(tmp_path / "x"))
    monkeypatch.setenv("MINDER_ZCODE_CONFIG", str(tmp_path / "x.json"))
    report = doctor.run_checks(dbp, probe=False, now=time.time())
    line = [c for c in report["checks"] if c["id"] == "coverage"][0]
    assert line["status"] == "ok", line
    assert "partial view" in line["detail"], line


def test_hook_flags_check_validates_declared_values(tmp_path, monkeypatch):
    """The live hooks.json is the flag source of truth on dsh installs; the
    2026-09-25 regression shipped an illegal MINDER_SUCCESS_GUARD value in
    it while every name-only check stayed green. Doctor must validate the
    declared *values* against the same vocabularies as the env flags."""
    dbp = _mig(tmp_path)
    hooks = tmp_path / "hooks.json"
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(hooks))

    def declared(**flags):
        hooks.write_text(json.dumps({"hooks": {"PostToolUse": [
            {"matcher": "", "hooks": [{"type": "command", "command":
             "MINDER_SINK_URL=http://127.0.0.1:8392 " + " ".join(
                 f"{k}={v}" for k, v in flags.items()) +
                 " python3 hook.py"}]}]}}))

    declared(MINDER_SUCCESS_GUARD="advisroy", MINDER_ASSIST="decision_skill")
    report = doctor.run_checks(dbp, probe=False, now=time.time())
    check = next(c for c in report["checks"] if c["id"] == "hook-flags")
    assert check["status"] == "fail"
    assert "MINDER_SUCCESS_GUARD='advisroy'" in check["detail"]
    assert report["healthy"] is False

    declared(MINDER_SUCCESS_GUARD="block", MINDER_ASSIST="decision_skill",
             MINDER_DECISION="laya")
    report = doctor.run_checks(dbp, probe=False, now=time.time())
    check = next(c for c in report["checks"] if c["id"] == "hook-flags")
    assert check["status"] == "ok"
    assert "MINDER_SUCCESS_GUARD=block" in check["detail"]


def test_hook_flags_check_reports_absent_hooks_json(tmp_path, monkeypatch):
    dbp = _mig(tmp_path)
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "absent.json"))
    report = doctor.run_checks(dbp, probe=False, now=time.time())
    check = next(c for c in report["checks"] if c["id"] == "hook-flags")
    assert check["status"] == "info"


# --- difficulty-router check (issue #7) ---------------------------------
# The live install ran with the router enabled and never applied a band:
# 12,452 auto requests answered with thinking off, 872 difficulty_skipped
# all reason=client_effort. doctor said nothing about it.

def _router_files(tmp_path, mode, ledger_lines):
    cfg = tmp_path / "minder.json"
    cfg.write_text(json.dumps({"difficulty_router": mode}))
    ledger = tmp_path / "events.jsonl"
    ledger.write_text("".join(json.dumps(rec) + "\n" for rec in ledger_lines))
    return cfg, ledger


def _router_report(tmp_path, monkeypatch, mode, ledger_lines):
    dbp = _mig(tmp_path)
    cfg, ledger = _router_files(tmp_path, mode, ledger_lines)
    report = doctor.run_checks(dbp, probe=False, now=time.time(),
                               proxy_config=cfg, events_ledger=ledger)
    return next(c for c in report["checks"] if c["id"] == "difficulty-router")


def test_difficulty_router_warns_when_enabled_but_never_applied(tmp_path,
                                                                monkeypatch):
    """Enabled + abstentions + zero applied bands is the inert-router
    condition, and the detail has to name the cause, not just the count."""
    lines = [{"event": "difficulty_skipped", "reason": "client_effort",
              "router": "shadow"} for _ in range(3)]
    check = _router_report(tmp_path, monkeypatch, "shadow", lines)
    assert check["status"] == "warn"
    assert "3 abstention" in check["detail"]
    assert "0 applied" in check["detail"]
    assert "laya" in check["detail"]
    # an inert router is a config finding, not a broken install
    assert check["status"] != "fail"


def test_difficulty_router_reports_applied_bands(tmp_path, monkeypatch):
    lines = [{"event": "difficulty_skipped", "reason": "client_effort"},
             {"event": "difficulty_routed", "band": "routine"}]
    check = _router_report(tmp_path, monkeypatch, "active", lines)
    assert check["status"] == "ok"
    assert "1 band" in check["detail"]


def test_difficulty_router_distinguishes_no_traffic_from_inert(tmp_path,
                                                               monkeypatch):
    """M2: zero abstentions and zero applications means nothing was
    eligible, which is a different problem from being outranked."""
    check = _router_report(tmp_path, monkeypatch, "laya",
                           [{"event": "auto_effort", "effort": "off"}])
    assert check["status"] == "info"
    assert "no router-eligible request" in check["detail"]


def test_difficulty_router_names_the_cause_from_the_ledger_not_the_mode(
        tmp_path, monkeypatch):
    """The #7 message blamed a client-declared effort. After the mode
    flipped to `laya` no band applied either, and it kept saying
    `outranked` while the ledger said `below_confidence` - the floor
    working, a different repair. The cause has to come from the ledger."""
    lines = [{"event": "difficulty_skipped", "reason": "below_confidence",
              "router": "laya"} for _ in range(4)]
    check = _router_report(tmp_path, monkeypatch, "laya", lines)
    assert check["status"] == "warn"
    assert "4 below_confidence" in check["detail"]
    assert "confidence floor" in check["detail"]
    assert "outranked, not broken" not in check["detail"]


def test_difficulty_router_keeps_the_client_cause_when_it_is_the_cause(
        tmp_path, monkeypatch):
    """Not a rewrite that loses the original finding: on a store where the
    abstentions really are client effort, the line still says outranked."""
    lines = [{"event": "difficulty_skipped", "reason": "client_effort",
              "router": "laya"} for _ in range(5)]
    check = _router_report(tmp_path, monkeypatch, "laya", lines)
    assert check["status"] == "warn"
    assert "5 client_effort" in check["detail"]
    assert "outranked, not broken" in check["detail"]


def test_difficulty_router_reports_a_mixed_ledger_by_count(tmp_path,
                                                           monkeypatch):
    """M2: one aggregate number over a mixed ledger is the denominator
    error this check is supposed to avoid, so each reason is counted."""
    lines = ([{"event": "difficulty_skipped", "reason": "below_confidence"}
              for _ in range(3)]
             + [{"event": "difficulty_skipped", "reason": "malformed_response"}])
    check = _router_report(tmp_path, monkeypatch, "laya", lines)
    assert "3 below_confidence" in check["detail"]
    assert "1 malformed_response" in check["detail"]


def test_difficulty_router_off_is_informational(tmp_path, monkeypatch):
    check = _router_report(tmp_path, monkeypatch, "off",
                           [{"event": "difficulty_skipped"}])
    assert check["status"] == "info"
    assert "laya" in check["detail"]


# --- laya decision worker check (issue #32) ---------------------------

def _worker_report(tmp_path, monkeypatch, *, sock=True, log_lines=None,
                   log_age=None, live=None):
    """Run doctor with the worker's socket/log pointed at tmp_path and the
    liveness probe replaced by a fixed answer. The probe is the check's
    only evidence about whether the worker is up, so the test states it
    rather than dialing a socket."""
    dbp = _mig(tmp_path)
    sock_path = tmp_path / "laya-worker.sock"
    if sock:
        sock_path.write_text("")
    log = tmp_path / "laya-worker.log"
    if log_lines:
        log.write_text("\n".join(log_lines) + "\n")
        if log_age is not None:
            st = log.stat()
            age_ns = int(log_age * 1_000_000_000)
            os.utime(log, ns=(st.st_atime_ns - age_ns, st.st_mtime_ns - age_ns))
    monkeypatch.setenv("MINDER_LAYA_WORKER_SOCK", str(sock_path))
    report = doctor.run_checks(dbp, probe=False, now=time.time(),
                               probe_worker=(lambda _sock: live)
                               if live is not None else None)
    return next(c for c in report["checks"] if c["id"] == "laya-worker")


def test_live_worker_answers_ok_even_when_the_log_tail_names_a_failure(
        tmp_path, monkeypatch):
    """The live case: `install.sh` restarted the proxy, a worker is alive
    and answering, and the log still holds the `spawn_failed` line from
    before the fix. Reading the tail made a healthy install warn."""
    check = _worker_report(tmp_path, monkeypatch, live=True, log_lines=[
        "minder-decision-worker-client: spawn_failed: no worker after spawn"])
    assert check["status"] == "ok"
    assert "spawn_failed" not in check["detail"]


def test_stale_worker_failure_is_labelled_stale(tmp_path, monkeypatch):
    """A dead worker plus an old log line: the warn must say how old the
    evidence is, because the log has no timestamps and only rotates at
    1 MB, so a fixed defect would otherwise warn forever."""
    check = _worker_report(tmp_path, monkeypatch, live=False, log_age=7200,
                           log_lines=["minder-decision-worker: request error: "
                                      "BrokenPipeError(32, 'Broken pipe')"])
    assert check["status"] == "warn"
    assert "spawn_failed" not in check["detail"]
    assert "BrokenPipeError" in check["detail"]
    assert "2h" in check["detail"]
    assert "stale" in check["detail"]


def test_a_worker_that_never_failed_is_context_not_a_finding(
        tmp_path, monkeypatch):
    """Spawn-on-demand means "nothing up right now" is a normal install.
    Only a worker that left a failure behind is a warn, and then the
    reason is the log line, not the absence."""
    check = _worker_report(tmp_path, monkeypatch, sock=False, live=False)
    assert check["status"] == "info"
    assert "spawn-on-demand" in check["detail"]


def test_recent_worker_failure_warns_without_the_stale_label(
        tmp_path, monkeypatch):
    """Fresh evidence is still evidence: a worker that failed seconds ago
    is a plain warn, not a stale one."""
    check = _worker_report(tmp_path, monkeypatch, live=False, log_age=60,
                           log_lines=["minder-decision-worker-client: "
                                      "spawn_failed: no worker after spawn"])
    assert check["status"] == "warn"
    assert "spawn_failed" in check["detail"]
    assert "stale" not in check["detail"]
