"""routing-core-v1 replay tests (Phase 1 completion / Phase 2 enablement).

The replay is offline and observe-only; reports use the 8C report
schema so the shipped benchmark comparator works unchanged: the rules
baseline pins at 40 comparable runs, protected metrics are zero, and a
gold-label change breaks comparability (NON_COMPARABLE)."""
import json
from datetime import datetime, timezone

from decision import routing
from memory import db as _db
from minder_op import benchmark as bench
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

CASES = "benchmarks/routing-core-v1/cases/route_cases.json"
NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _mig(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _db.connect(dbp).close()
    return dbp


def test_fixtures_validate_and_are_complete():
    doc, cases, digest = routing_load_cases()
    assert len(cases) == 40
    assert len({c["id"] for c in cases}) == 40
    assert doc["label_status"] == "proposed_pending_owner_review"
    assert len(digest) == 64
    # every injection case demands abstain and names unsafe destinations
    for case in cases:
        if case["class"] == "injection":
            assert case["gold"]["decision"] == "abstain"
            assert case["unsafe_if_routed_to"]
        assert case["gold"]["decision"] in ("stay", "switch", "abstain")


def routing_load_cases():
    from decision import routing
    doc, cases, digest = routing.load_cases(CASES)
    return doc, cases, digest


def test_benchmark_validate_accepts_routing_suite(capsys, monkeypatch):
    monkeypatch.delenv("MINDER_BENCHMARKS_DIR", raising=False)
    assert main(["benchmark", "validate", "--suite",
                 "routing-core-v1"]) == EXIT_OK
    assert "routing-core-v1" in capsys.readouterr().out


def test_replay_rules_baseline_is_clean_and_deterministic(tmp_path):
    dbp = _mig(tmp_path)
    report = routing_replay(dbp)
    assert bench.validate_report(report) == []
    metrics = report["metrics"]
    assert metrics["comparable_runs"] == 40
    assert metrics["verified_completion_rate"] == 1.0
    assert metrics["unsafe_executions"] == 0
    assert metrics["harmful_frontier_acceptances"] == 0
    assert metrics["external_prohibited_egress"] == 0
    # every injection case abstained
    by_id = {r["task_id"]: r for r in report["runs"]}
    for case in routing_load_cases()[1]:
        if case["class"] == "injection":
            assert "abstained=True" in by_id[case["id"]]["output_tail"]
    # determinism: same inputs, same facts (timestamp aside)
    again = routing_replay(dbp)
    assert again["metrics"] == report["metrics"]
    assert again["runs"] == report["runs"]
    assert again["suite_fingerprint"] == report["suite_fingerprint"]


def routing_replay(dbp, cases=CASES, now=NOW):
    from decision import routing
    return routing.replay_cases(cases, db_path=dbp, now=now)


def test_replay_report_pins_and_compares(tmp_path, monkeypatch):
    """Full 8C integration: pin the baseline, compare it against itself
    (PASS at 40 runs), and prove a gold-label change is
    NON_COMPARABLE."""
    root = tmp_path / "benchmarks"
    root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    dbp = _mig(tmp_path)
    report = routing_replay(dbp)
    out = tmp_path / "routing-report.json"
    out.write_text(json.dumps(report))
    assert main(["benchmark", "baseline", "create", str(out),
                 "--yes"]) == EXIT_OK
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "compare", str(out), str(out)])
    assert code == EXIT_OK and "PASS" in buf.getvalue()

    # tamper with one gold label -> new fingerprint -> NON_COMPARABLE
    doc = json.loads(open(CASES).read())
    doc["cases"][0]["gold"]["decision"] = "switch"
    path2 = tmp_path / "tampered-cases.json"
    path2.write_text(json.dumps(doc))
    report2 = routing_replay(dbp, cases=str(path2))
    out2 = tmp_path / "routing-report2.json"
    out2.write_text(json.dumps(report2))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["benchmark", "compare", str(out), str(out2)])
    assert code == EXIT_USAGE
    assert "NON_COMPARABLE" in buf.getvalue()


def test_ambiguity_proxy_counts_family_shifts(tmp_path):
    dbp = _mig(tmp_path)
    conn = _db.connect(dbp)
    try:
        rows = [
            ("ev_a1", "2026-09-20T10:00:00+00:00", "s1",
             "bash|keyerror|a|f.py"),
            ("ev_a2", "2026-09-20T10:05:00+00:00", "s1",
             "pytest|assertion|b|g.py"),  # family shift within s1
            ("ev_b1", "2026-09-21T10:00:00+00:00", "s2",
             "bash|keyerror|c|h.py"),     # same family as s1's first
        ]
        for i, (eid, ts, sid, key) in enumerate(rows):
            _db.write(conn, "INSERT INTO events (event_id, ts,"
                      " event_type, session_id, task_id, repo,"
                      " repo_version, tool, failure_key,"
                      " action_fingerprint, payload_json,"
                      " redaction_status)"
                      " VALUES (?, ?, 'tool_failure', ?, 't', '/r', '',"
                      " 'bash', ?, 'fp', '{}', 'redacted')",
                      (eid, ts, sid, key))
    finally:
        conn.close()
    report = routing.ambiguity_report(db_path=dbp, now=NOW)
    assert report["sessions"] == 2
    assert report["failure_events"] == 3
    assert report["family_shifts"] == 1
    assert report["declared_boundaries"] == 0
    assert "proxy" in report["note"]
