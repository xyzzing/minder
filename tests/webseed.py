"""Shared seed helpers for minder_web tests (8E). Plain functions, no
fixtures — imported by the test_web_* modules. All rows are inserted
with fixed timestamps so page content is deterministic; free-text
fields carry a marker secret / hostile payload so redaction and
escaping are asserted, not assumed.
"""
from memory import db as _db

SECRET = "sk-proj-operatorleak99999999"
HOSTILE = "<script>alert('x')</script>"
TS = "2026-09-22T10:00:00+00:00"


def new_db(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()  # migrate to v10
    return dbp


def client_for(dbp, monkeypatch):
    """TestClient bound to a temp store via MINDER_WEB_DB."""
    monkeypatch.setenv("MINDER_WEB_DB", str(dbp))
    from fastapi.testclient import TestClient
    from minder_web.app import app
    return TestClient(app)


def _ins(dbp, sql, params):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, sql, params)
    finally:
        conn.close()


def add_episode(dbp, episode_id="ep1", status="open", repo=f"/repo/{HOSTILE}",
                task="task-1", opened=TS, closed=None):
    _ins(dbp, "INSERT INTO episodes (episode_id, opened_at, closed_at,"
          " repo, task_id, status) VALUES (?, ?, ?, ?, ?, ?)",
          (episode_id, opened, closed, repo, task, status))


def add_event(dbp, event_id="ev1", episode_id="ep1",
              failure_key="bash|keyerror|k|a.py", payload="{}"):
    _ins(dbp, "INSERT INTO events (event_id, ts, event_type, session_id,"
          " task_id, repo, repo_version, tool, failure_key,"
          " action_fingerprint, payload_json, redaction_status)"
          " VALUES (?, ?, 'tool_failure', 's1', 't1', ?, '', 'bash',"
          " ?, 'fp', ?, 'redacted')",
          (event_id, TS, "/repo", failure_key, payload))
    _ins(dbp, "INSERT INTO episode_events (episode_id, event_id, seq)"
          " VALUES (?, ?, 1)", (episode_id, event_id))


def add_lesson(dbp, lesson_id="les1", status="verified",
               instruction=f"check {SECRET} defaults",
               failure_key="bash|keyerror|k|a.py", valid_from=TS,
               valid_to=None):
    _ins(dbp, "INSERT INTO lessons (lesson_id, repo, failure_key,"
          " instruction, anti_pattern, verification_json, status,"
          " source_episode, valid_from, valid_to, expires_when)"
          " VALUES (?, ?, ?, ?, '', '{}', ?, 'ep1', ?, ?, '')",
          (lesson_id, "/repo", failure_key, instruction, status,
           valid_from, valid_to))


def add_gap(dbp, gap_id="gap1"):
    _ins(dbp, "INSERT INTO skill_gaps (gap_id, ts, repo, failure_key,"
          " gap_type, sample_error, status)"
          " VALUES (?, ?, ?, ?, 'procedural', 'sample', 'open')",
          (gap_id, TS, "/repo", "bash|keyerror|k|a.py"))


def add_consult(dbp, trace_id="tr1", label="helpful", legacy=7):
    """Legacy 003 helpfulness is set to a poison INTEGER; only the
    frontier_evals label may surface."""
    _ins(dbp, "INSERT INTO frontier_traces (trace_id, ts, episode_id,"
          " failure_key, local_attempts, redaction_profile,"
          " provider_fingerprint, request_hash, response_hash,"
          " helpfulness, verification_status)"
          " VALUES (?, ?, 'ep1', ?, 2, 'internal-code', 'fp', 'rh',"
          " 'sh', ?, 'pass')", (trace_id, TS, "bash|keyerror|k|a.py",
                                legacy))
    _ins(dbp, "INSERT INTO frontier_evals (trace_id, consult_trigger,"
          " helpfulness, verification_status, distilled_json,"
          " accepted_actions_json, rejected_actions_json, classified_at)"
          " VALUES (?, 'warden_l2', ?, 'pass', '[]', '[]', '[]', ?)",
          (trace_id, label, TS))


def seed_difficulty_ledger(state_dir, events):
    """Write difficulty-router events to a proxy events.jsonl ledger.
    `events` is a list of dicts (each with at least an 'event' key);
    they are appended in order (ledger is append-only)."""
    import json as _json
    ledger = state_dir / "events.jsonl"
    with ledger.open("w") as fh:
        for ev in events:
            fh.write(_json.dumps(ev) + "\n")
    return ledger


def add_decision(dbp, decision_id="dt1",
                 failure_key=f"bash|keyerror|{HOSTILE}|a.py"):
    _ins(dbp, "INSERT INTO decision_traces (id, ts, contract_id,"
          " contract_version, session_id, failure_key, state_hash,"
          " menu_json, model_recommendation, policy_decision, override,"
          " fallback, confidence, failure_kind, needs_new_evidence,"
          " provider, model_version, latency_ms)"
          " VALUES (?, ?, 'failure-triage', 'v1', 's1', ?, 'sth',"
          " '[\"inspect\"]', 'inspect', 'inspect', '', '', 0.9,"
          " 'runtime_error', 0.0, 'null', '', 1.0)",
          (decision_id, TS, failure_key))
