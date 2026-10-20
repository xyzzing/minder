"""Domains page tests (issue #8): the read-only pack surface - active
profile, suites with plain-language verdicts, rulebook provenance,
routing mode. A missing or malformed config degrades honestly, never a
500; the seeded secret never renders."""
import json

import webseed
from webseed import HOSTILE, SECRET, new_db

_PRIMARY_RULE = "sg_gst.rate"  # shipped rulebook: verification primary


def _config(tmp_path, config):
    path = tmp_path / "minder.json"
    path.write_text(json.dumps(config))
    return str(path)


def test_domains_page_renders_profile_suites_provenance(tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", _config(tmp_path, {
        "profile": "finance",
        "profiles": {"finance": {"l1_budget": 4096}}}))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    text = resp.text
    # active profile named, its override visible as a plain value
    assert "finance" in text
    assert "4096" in text
    # suites listed with their plain-language verdicts
    assert "legal-core-v1" in text and "finance-core-v1" in text
    assert "no pinned baseline yet" in text
    # provenance grades rendered with the secondary gloss
    assert "primary" in text and "secondary" in text
    assert "re-verify before relying on it externally" in text
    assert _PRIMARY_RULE in text
    # seeded fixture secrets never reach the page
    assert SECRET not in text and HOSTILE not in text


def test_domains_page_without_config_shows_default_profile(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", str(tmp_path / "absent.json"))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    assert "coding (default" in resp.text
    assert "no pinned baseline yet" in resp.text


def test_domains_page_malformed_config_is_a_named_error(tmp_path,
                                                        monkeypatch):
    bad = tmp_path / "minder.json"
    bad.write_text("{not json")
    monkeypatch.setenv("MINDER_CONFIG", str(bad))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    resp = client.get("/domains")
    assert resp.status_code == 200
    assert "JSONDecodeError" in resp.text
    assert "coding (default" in resp.text


def test_domains_page_routing_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDER_CONFIG", _config(tmp_path, {
        "difficulty_router": "shadow"}))
    dbp = new_db(tmp_path)
    client = webseed.client_for(dbp, monkeypatch)
    text = client.get("/domains").text
    assert "shadow - proposals are logged, nothing is rerouted yet" in text


def test_domains_suites_verdict_reads_the_pinned_baseline(tmp_path,
                                                          monkeypatch):
    """The plain verdict sentence and the raw tier must both read the
    pinned baseline's own values (issue #12). The page model flattens the
    nested baseline dict into named fields so the tier can print a field
    name next to each value; a flattened field copied from the wrong
    place renders a wrong date or a wrong run count on both tiers, and no
    structural check would notice. The suite is the shipped legal one by
    id, so the sentence is the one the page really shows; its manifest is
    written here because a manifest naming fixture files is validated
    against the suite directory, and this test has redirected that
    directory to a temp root."""
    import minder_op.benchmark as bench
    root = tmp_path / "benchmarks"
    (root / "legal-core-v1").mkdir(parents=True)
    manifest = {"manifest_version": 1, "suite_id": "legal-core-v1",
                "title": "t", "tiers": [0],
                "tasks": [{"task_id": "t0", "tier": 0,
                           "family": "contract", "title": "t",
                           "fixtures": [], "expected": "tests_pass",
                           "forbidden": sorted(
                               bench.FORBIDDEN_VOCABULARY)}]}
    (root / "legal-core-v1" / "manifest.json").write_text(
        json.dumps(manifest))
    (root / "baselines").mkdir()
    (root / "baselines" / "legal-core-v1.json").write_text(json.dumps({
        "report_version": 1, "suite_id": "legal-core-v1",
        "suite_fingerprint": bench.manifest_fingerprint(manifest),
        "generated_at": "2026-09-23T10:00:00+00:00", "kind": "candidate",
        "runs": [], "metrics": {
            "comparable_runs": 25, "verified_completion_rate": 0.9,
            "unsafe_executions": 0, "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0}}))
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    dbp = new_db(tmp_path)
    text = webseed.client_for(dbp, monkeypatch).get("/domains").text
    # the plain sentence: date, rate and run count from this report.
    # human_date renders "Sep 23" and drops the year, so the year is
    # asserted where it is stored - the raw tier's exact ISO value
    assert "pinned baseline Sep 23: 90.0% verified over 25 comparable " \
        "runs" in text
    assert "2026-09-23T10:00:00+00:00" in text
    # and the raw tier names the fields those values came from
    assert "baseline_generated_at" in text
    assert "baseline_comparable_runs" in text
