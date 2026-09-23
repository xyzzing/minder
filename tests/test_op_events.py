"""Events listing tests (operator usability #2): the raw events table
was the one surface the CLI could not show. Filters, ordering, show,
redaction, standard exit codes."""
from memory import db as _db
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

SECRET = "sk-proj-operatorleak99999999"
KEY = "bash|keyerror|supplier_id|app/supplier.py"


def _ins(dbp, sql, params):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, sql, params)
    finally:
        conn.close()


def _seed(dbp, n=3):
    for i in range(n):
        _ins(dbp, "INSERT INTO events (event_id, ts, event_type,"
             " session_id, task_id, repo, repo_version, tool,"
             " failure_key, action_fingerprint, payload_json,"
             " redaction_status)"
             f" VALUES ('ev_{i}', '2026-09-2{i}T10:00:00+00:00',"
             " 'tool_failure', 's1', 't1', '/repo', '', 'bash', ?,"
             " 'fp', '{}', 'redacted')", (KEY,))
    _ins(dbp, "INSERT INTO episodes (episode_id, opened_at, closed_at,"
          " repo, task_id, status)"
          " VALUES ('ep1', '2026-09-20T10:00:00+00:00', NULL, '/repo',"
          " 't1', 'open')", ())
    _ins(dbp, "INSERT INTO episode_events (episode_id, event_id, seq)"
          " VALUES ('ep1', 'ev_0', 1)", ())


def test_events_ls_newest_first_and_limit(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _seed(dbp, n=3)
    assert main(["--db", str(dbp), "events", "ls"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ev_2" in out and KEY in out
    assert out.index("ev_2") < out.index("ev_0")  # newest first
    assert main(["--db", str(dbp), "events", "ls",
                 "--limit", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ev_2" in out and "ev_0" not in out


def test_events_ls_filters(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _seed(dbp, n=2)
    _ins(dbp, "INSERT INTO events (event_id, ts, event_type,"
         " session_id, task_id, repo, repo_version, tool, failure_key,"
         " action_fingerprint, payload_json, redaction_status)"
         " VALUES ('ev_ok', '2026-09-23T10:00:00+00:00',"
         " 'tool_success', 's1', 't1', '/repo', '', 'bash', NULL,"
         " NULL, '{}', 'redacted')", ())
    assert main(["--db", str(dbp), "events", "ls",
                 "--type", "tool_success"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ev_ok" in out and "ev_0" not in out
    assert main(["--db", str(dbp), "events", "ls",
                 "--failure-key", KEY]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ev_0" in out and "ev_ok" not in out


def test_events_episode_filter_links_via_episode_events(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _seed(dbp, n=2)  # only ev_0 is linked to ep1
    assert main(["--db", str(dbp), "events", "ls",
                 "--episode", "ep1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ev_0" in out and "ev_1" not in out
    assert "ep1" in out  # episode column visible when filtering


def test_events_show_and_unknown(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _seed(dbp, n=1)
    assert main(["--db", str(dbp), "events", "show",
                 "ev_0"]) == EXIT_OK
    assert "failure_key" in capsys.readouterr().out
    assert main(["--db", str(dbp), "events", "show",
                 "ev_ghost"]) == EXIT_USAGE


def test_events_payload_redacted_on_display(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    _seed(dbp, n=1)
    # events is append-only, so seed the secret payload at insert
    # time (bypassing write-path redaction); display must still redact
    _ins(dbp, "INSERT INTO events (event_id, ts, event_type,"
         " session_id, task_id, repo, repo_version, tool, failure_key,"
         " action_fingerprint, payload_json, redaction_status)"
         " VALUES ('ev_sec', '2026-09-23T10:00:00+00:00',"
         " 'tool_failure', 's1', 't1', '/repo', '', 'bash', ?, 'fp',"
         " ?, 'redacted')",
         (KEY, f'{{"excerpt": "boom {SECRET}"}}'))
    for argv in (["events", "ls"], ["events", "show", "ev_0"]):
        assert main(["--db", str(dbp), *argv]) == EXIT_OK
        assert SECRET not in capsys.readouterr().out


def test_events_empty_and_missing_db(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    assert main(["--db", str(dbp), "events", "ls"]) == EXIT_OK
    assert "(none)" in capsys.readouterr().out
    assert main(["--db", str(tmp_path / "nope.sqlite"),
                 "events", "ls"]) == 2
