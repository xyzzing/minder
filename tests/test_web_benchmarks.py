"""Benchmarks page (8E, PRD D3): suites and pinned baselines render;
absence renders 'not available', never a 500."""
from minder_op import benchmark as bench

import webseed
from webseed import new_db


def test_benchmarks_page_lists_repo_suite(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/benchmarks")
    assert resp.status_code == 200
    assert "coding-core-v1" in resp.text
    assert "no pinned baseline" in resp.text


def test_benchmarks_page_shows_pinned_baseline(tmp_path, monkeypatch):
    import json
    root = tmp_path / "benchmarks"
    root.mkdir()
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    manifest = {
        "manifest_version": 1, "suite_id": "suite-x", "title": "t",
        "tiers": [0],
        "tasks": [{"task_id": "t0", "tier": 0, "family": "contract",
                   "title": "t", "fixtures": [], "expected": "tests_pass",
                   "forbidden": sorted(bench.FORBIDDEN_VOCABULARY)}]}
    (root / "suite-x").mkdir()
    (root / "suite-x" / "manifest.json").write_text(json.dumps(manifest))
    report = {"report_version": 1, "suite_id": "suite-x",
              "suite_fingerprint":
                  bench.manifest_fingerprint(manifest),
              "generated_at": "2026-09-23T10:00:00+00:00",
              "kind": "candidate", "runs": [],
              "metrics": {"comparable_runs": 25,
                          "verified_completion_rate": 0.9,
                          "unsafe_executions": 0,
                          "harmful_frontier_acceptances": 0,
                          "external_prohibited_egress": 0}}
    (root / "baselines").mkdir()
    (root / "baselines" / "suite-x.json").write_text(json.dumps(report))
    dbp = new_db(tmp_path / "store")
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/benchmarks")
    assert resp.status_code == 200
    assert "suite-x" in resp.text and "90.0%" in resp.text


def test_benchmarks_missing_dir_renders_not_available(tmp_path,
                                                      monkeypatch):
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR",
                       str(tmp_path / "no-such-dir"))
    client = webseed.client_for(new_db(tmp_path), monkeypatch)
    resp = client.get("/benchmarks")
    assert resp.status_code == 200
    assert "not available" in resp.text
