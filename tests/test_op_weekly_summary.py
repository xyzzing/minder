"""Weekly-summary tests (8A). The summary reports observed workflow
evidence only — never a productivity claim. Determinism comes from
`now=` injection into minder_op.summary.build_weekly_summary; CLI tests
cover wiring (--days/--since/--db/--json, exit codes) with fresh rows so
no assertion depends on the wall clock.

Labels come from frontier_evals (007); the legacy 003 INTEGER column is
set to a poison value in the fixture and must never surface.
"""
import json
from datetime import datetime, timezone

from memory import db as _db
from minder_op.cli import EXIT_OK, EXIT_USAGE, main
from minder_op.summary import build_weekly_summary, render_text

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
REPO = "/repo"
KEY_A = "bash|keyerror|supplier_id|app/supplier.py"
KEY_B = "pytest|assertion|totals|tests/calc.py"
KEY_C = "bash|permission|deploy.sh|scripts/deploy.sh"


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    conn = _db.connect(dbp)
    return dbp, conn


def _ins(conn, sql, params):
    _db.write(conn, sql, params)


def _ins_decision(conn, dt_id, ts, rec, pol, override):
    _ins(conn, "INSERT INTO decision_traces (id, ts, contract_id,"
         " contract_version, session_id, failure_key, state_hash,"
         " menu_json, model_recommendation, policy_decision, override,"
         " fallback, confidence, failure_kind, needs_new_evidence,"
         " provider, model_version, latency_ms)"
         " VALUES (?, ?, 'failure-triage', 'v1', 's1', ?, 'sth',"
         " '[\"inspect\"]', ?, ?, ?, '', 0.5, 'runtime_error', 0.0,"
         " 'null', '', 1.0)", (dt_id, ts, KEY_A, rec, pol, override))


def seed_full(conn):
    """Deterministic v10 fixture at fixed timestamps. In-window is
    [2026-09-15T12:00, 2026-09-22T12:00) for a 7-day window at NOW."""
    for ep_id, opened, closed, status in [
            ("ep_in_1", "2026-09-16T10:00:00+00:00",
             "2026-09-16T11:00:00+00:00", "verified"),
            ("ep_in_2", "2026-09-17T10:00:00+00:00",
             "2026-09-17T11:00:00+00:00", "candidate"),
            ("ep_old", "2026-09-01T10:00:00+00:00",
             "2026-09-01T11:00:00+00:00", "verified"),
            ("ep_open", "2026-09-21T10:00:00+00:00", None, "open")]:
        _ins(conn, "INSERT INTO episodes (episode_id, opened_at, closed_at,"
             " repo, task_id, status) VALUES (?, ?, ?, ?, ?, ?)",
             (ep_id, opened, closed, REPO, "task-" + ep_id, status))
    fails = [(KEY_A, "2026-09-16T09:00:00+00:00"),
             (KEY_A, "2026-09-17T09:00:00+00:00"),
             (KEY_A, "2026-09-18T09:00:00+00:00"),
             (KEY_B, "2026-09-16T09:30:00+00:00"),
             (KEY_B, "2026-09-17T09:30:00+00:00"),
             (KEY_C, "2026-09-19T09:00:00+00:00"),
             (KEY_A, "2026-09-01T09:00:00+00:00")]  # outside window
    for i, (key, ts) in enumerate(fails):
        _ins(conn, "INSERT INTO events (event_id, ts, event_type,"
             " session_id, task_id, repo, repo_version, tool, failure_key,"
             " action_fingerprint, payload_json, redaction_status)"
             f" VALUES ('ev_w{i}', ?, 'tool_failure', 's1', 't1', ?, '',"
             " 'bash', ?, 'fp', '{}', 'redacted')", (ts, REPO, key))
    for lid, vfrom, vto, status in [
            ("les_v", "2026-09-16T10:00:00+00:00", None, "verified"),
            ("les_c", "2026-09-17T10:00:00+00:00", None, "candidate"),
            ("les_old", "2026-09-01T10:00:00+00:00", None, "verified"),
            ("les_inv", "2026-09-16T10:00:00+00:00",
             "2026-09-20T10:00:00+00:00", "invalidated")]:
        _ins(conn, "INSERT INTO lessons (lesson_id, repo, failure_key,"
             " instruction, anti_pattern, verification_json, status,"
             " source_episode, valid_from, valid_to, expires_when)"
             " VALUES (?, ?, ?, 'do x', '', '{}', ?, 'ep_x', ?, ?, '')",
             (lid, REPO, KEY_A, status, vfrom, vto))
    for gid, ts, status in [("gap_w1", "2026-09-18T10:00:00+00:00", "open"),
                            ("gap_w2", "2026-09-19T10:00:00+00:00", "closed"),
                            ("gap_old", "2026-09-01T10:00:00+00:00", "open")]:
        _ins(conn, "INSERT INTO skill_gaps (gap_id, ts, repo, failure_key,"
             " gap_type, sample_error, status)"
             " VALUES (?, ?, ?, ?, 'procedural', 'sample', ?)",
             (gid, ts, REPO, KEY_A, status))
    for tid, ts, label in [("tr_help", "2026-09-16T12:00:00+00:00",
                            "helpful"),
                           ("tr_harm", "2026-09-17T12:00:00+00:00",
                            "harmful"),
                           ("tr_none", "2026-09-18T12:00:00+00:00", None),
                           ("tr_old", "2026-09-01T12:00:00+00:00",
                            "helpful")]:
        _ins(conn, "INSERT INTO frontier_traces (trace_id, ts, episode_id,"
             " failure_key, local_attempts, redaction_profile,"
             " provider_fingerprint, request_hash, response_hash,"
             " helpfulness, verification_status)"
             " VALUES (?, ?, 'ep_in_1', ?, 2, 'internal-code', 'fp',"
             " 'rh', 'sh', 7, 'pass')", (tid, ts, KEY_A))  # 7 = legacy 003
        if label:
            _ins(conn, "INSERT INTO frontier_evals (trace_id,"
                 " consult_trigger, helpfulness, verification_status,"
                 " distilled_json, accepted_actions_json,"
                 " rejected_actions_json, classified_at)"
                 " VALUES (?, 'warden_l2', ?, 'pass', '[]', '[]', '[]',"
                 " ?)", (tid, label, ts))
    _ins_decision(conn, "dt_agree", "2026-09-18T13:00:00+00:00",
                  "inspect", "inspect", "")
    _ins_decision(conn, "dt_over", "2026-09-18T14:00:00+00:00",
                  "think", "inspect", "policy")


# --- deterministic content (now= injected) -------------------------------


def test_full_fixture_text_report(tmp_path, capsys):
    dbp, conn = _mig(tmp_path)
    try:
        seed_full(conn)
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    render_text(report)
    out = capsys.readouterr().out
    assert "observed workflow evidence" in out
    assert "no productivity claim" in out
    assert "improv" not in out.lower()  # claim words never appear
    assert "benchmarks" in out and "not available" in out  # 8C pending
    for fragment in ("2026-09-15T12:00:00+00:00",
                     "2026-09-22T12:00:00+00:00"):
        assert fragment in out  # window bounds visible
    # episodes: 3 opened in window, 1 open now, 1 verified + 1 candidate
    # closed -> verified resolution 1/2
    assert "1/2" in out
    # failures: top key shown with its count
    assert KEY_A in out and "x3" in out
    # frontier labels: helpful, harmful, unclassified — never the 003 "7"
    assert "helpful" in out and "harmful" in out and "unclassified" in out
    # decision shadow section present with the override count
    assert "override" in out


def test_full_fixture_json_report(tmp_path):
    dbp, conn = _mig(tmp_path)
    try:
        seed_full(conn)
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert report["window"] == {"since": "2026-09-15T12:00:00+00:00",
                                "until": "2026-09-22T12:00:00+00:00",
                                "days": 7}
    assert report["episodes"]["opened_in_window"] == 3
    assert report["episodes"]["open_now"] == 1
    assert report["episodes"]["closed_in_window"] == {
        "verified": 1, "candidate": 1}
    assert report["episodes"]["verified_resolution_rate"] == 0.5
    assert report["failures"]["tool_failures_in_window"] == 6
    assert report["failures"]["distinct_failure_keys"] == 3
    assert report["failures"]["repeat_failure_keys"] == 2
    assert [t["failure_key"] for t in
            report["failures"]["top_failure_keys"]] == [KEY_A, KEY_B, KEY_C]
    assert report["lessons"]["created_in_window"] == {
        "verified": 1, "candidate": 1, "invalidated": 1}
    assert report["lessons"]["invalidated_in_window"] == 1
    assert report["lessons"]["candidates_open_total"] == 1
    assert report["gaps"] == {"opened_in_window": 2, "open_now": 2}
    assert report["consults"]["total_in_window"] == 3
    assert report["consults"]["by_helpfulness"] == {
        "helpful": 1, "harmful": 1, "(unclassified)": 1}
    assert "7" not in report["consults"]["by_helpfulness"]  # 003 poison
    assert report["decisions"] == {"traces_in_window": 2,
                                   "agreements": 1, "overrides": 1}
    assert report["benchmarks"] == {"status": "not available"}
    assert "no productivity claim" in report["note"]
    assert len(report["focus"]) == 3
    assert "harmful frontier consult" in report["focus"][0]
    assert "candidate lesson" in report["focus"][1]
    assert "skill gap" in report["focus"][2]
    # cut by the max-three rule even though their triggers exist
    assert not any("repeat failure key" in f for f in report["focus"])
    assert not any("override" in f for f in report["focus"])
    # fully deterministic given the same now
    assert build_weekly_summary(dbp, days=7, now=NOW) == report


def test_since_overrides_days(tmp_path):
    dbp, conn = _mig(tmp_path)
    try:
        seed_full(conn)
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=3, since="2026-09-01T00:00:00",
                                  now=NOW)
    assert report["window"]["since"] == "2026-09-01T00:00:00+00:00"
    assert report["window"]["days"] is None
    # the "old" September fixtures are now inside the window
    assert report["episodes"]["opened_in_window"] == 4
    assert report["consults"]["total_in_window"] == 4


def test_focus_priority_beyond_top_three(tmp_path):
    """With harmful/candidates/gaps absent, lower-priority triggers
    surface in fixed order: repeat failures first, then overrides."""
    dbp, conn = _mig(tmp_path)
    try:
        for i, ts in enumerate(("2026-09-20T09:00:00+00:00",
                                "2026-09-21T09:00:00+00:00")):
            _ins(conn, "INSERT INTO events (event_id, ts, event_type,"
                 " session_id, task_id, repo, repo_version, tool,"
                 " failure_key, action_fingerprint, payload_json,"
                 " redaction_status)"
                 f" VALUES ('ev_r{i}', ?, 'tool_failure', 's', 't', ?, '',"
                 " 'bash', ?, 'fp', '{}', 'redacted')", (ts, REPO, KEY_B))
        _ins_decision(conn, "dt_over", "2026-09-21T10:00:00+00:00",
                      "think", "inspect", "policy")
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert len(report["focus"]) == 2
    assert "repeat failure key" in report["focus"][0]
    assert KEY_B in report["focus"][0]
    assert "override" in report["focus"][1]


def test_empty_db_summary_is_safe(tmp_path, capsys):
    dbp, conn = _mig(tmp_path)
    conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert report["episodes"]["opened_in_window"] == 0
    assert report["episodes"]["verified_resolution_rate"] is None
    assert report["failures"]["repeat_failure_keys"] == 0
    assert report["consults"]["total_in_window"] == 0
    assert report["decisions"] == {"traces_in_window": 0,
                                   "agreements": 0, "overrides": 0}
    assert report["focus"] == []
    render_text(report)
    out = capsys.readouterr().out
    assert "no productivity claim" in out
    assert "n/a" in out  # rate line without a claim


# --- CLI wiring (--days/--since/--db/--json, exit codes) ------------------


def test_cli_weekly_summary_json(tmp_path, capsys):
    """Fresh rows stamped by wall-clock now, window relative to real
    now — the assertion never depends on the actual date."""
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    conn = _db.connect(dbp)
    try:
        _ins(conn, "INSERT INTO episodes (episode_id, opened_at, closed_at,"
             " repo, task_id, status)"
             " VALUES ('ep_cli', ?, NULL, ?, 'task_cli', 'open')",
             (datetime.now(timezone.utc).isoformat(), REPO))
    finally:
        conn.close()
    code = main(["--db", str(dbp), "weekly-summary", "--days", "7",
                 "--json"])
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["window"]["days"] == 7
    assert report["episodes"]["opened_in_window"] == 1
    assert isinstance(report["focus"], list) and len(report["focus"]) <= 3


def test_cli_weekly_summary_since_flag(tmp_path, capsys):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    code = main(["--db", str(dbp), "weekly-summary",
                 "--since", "2026-09-01T00:00:00+00:00", "--json"])
    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["window"]["since"] == "2026-09-01T00:00:00+00:00"
    assert report["window"]["days"] is None


def test_cli_weekly_summary_exit_codes(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nope.sqlite"),
                 "weekly-summary"]) == 2  # missing DB
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    assert main(["--db", str(dbp), "weekly-summary",
                 "--days", "0"]) == EXIT_USAGE
    assert main(["--db", str(dbp), "weekly-summary",
                 "--since", "not-a-date"]) == EXIT_USAGE
    assert "Traceback" not in capsys.readouterr().err
    assert main(["--db", str(dbp), "weekly-summary",
                 "--days", "3"]) == EXIT_OK  # text render ok


def test_cli_help_lists_weekly_summary(capsys):
    assert main(["--help"]) == EXIT_OK
    assert "weekly-summary" in capsys.readouterr().out


def test_summary_domain_section_and_resume_expiry(tmp_path):
    """The weekly summary now carries the domain layer: declared
    boundaries, routing traces/agreements/abstentions, and resume
    intents nearing expiry. Degrades to zeros on stores predating
    migration 011."""
    dbp, conn = _mig(tmp_path)
    try:
        _ins(conn, "INSERT INTO task_contexts (context_id, task_id,"
             " actor, domain, subtask, subtask_seq, origin,"
             " contract_version, opened_at)"
             " VALUES ('ctx1', 't1', 'operator', 'coding', NULL, 1,"
             " 'explicit_user', 'domain-route/v1', ?)",
             ("2026-09-18T09:00:00+00:00",))
        _ins(conn, "INSERT INTO route_traces (trace_id, ts, contract_id,"
             " contract_version, declared_domain, candidate_domain,"
             " transition, abstained, provenance, validation_result)"
             " VALUES ('rt1', ?, 'domain-route', 'v1', 'coding',"
             " 'coding', 'stay', 0, 'declared', 'approved')",
             ("2026-09-18T10:00:00+00:00",))
        _ins(conn, "INSERT INTO application_intents (intent_id, jd_id,"
             " jd_digest, assertion_id, chosen_variant, created_at,"
             " actor, expires_at, status)"
             " VALUES ('int1', 'jd-a', 'jdd', 'as1', 'led a workstream',"
             " ?, 'user', '2026-09-24T00:00:00+00:00', 'active')",
             ("2026-09-16T10:00:00+00:00",))
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert report["domain"]["declared_boundaries"] == 1
    assert report["domain"]["route_traces"] == 1
    assert report["domain"]["route_agreements"] == 1
    assert report["domain"]["route_abstentions"] == 0
    assert report["domain"]["resume_intents_expiring_7d"] == 1


def test_summary_domain_degrades_on_pre011_store(tmp_path):
    """A store without the domain tables (pre-011) must yield zeros
    gracefully — the read-only summary never migrates or crashes."""
    dbp, conn = _mig(tmp_path)
    try:
        conn.executescript(
            "DROP TABLE IF EXISTS route_traces;"
            " DROP TABLE IF EXISTS application_intents;"
            " DROP TABLE IF EXISTS task_contexts;")
    finally:
        conn.close()
    report = build_weekly_summary(dbp, days=7, now=NOW)
    assert report["domain"]["available"] is False
    assert report["domain"]["declared_boundaries"] == 0
    assert report["domain"]["resume_intents_expiring_7d"] == 0
