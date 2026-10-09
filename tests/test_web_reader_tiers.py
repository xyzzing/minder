"""Two-reader console contract (issue #12): each page answers in plain
words first, and every list page carries a raw-evidence tier whose values
are the same redacted model values, never a raw DB row. Redaction is a
service-layer contract, so the tier must not become a leak path."""

from webseed import (add_consult, add_decision, add_episode, add_event,
                     add_gap, add_lesson, client_for, new_db)

import re

# One raw field name per list page. The tier is only honest if the
# column's own name is printed next to its value, so a console screenshot
# can be compared against a CLI row.
RAW_FIELD_BY_PAGE = {
    "/episodes": "opened_at",
    "/lessons": "valid_from",
    "/gaps": "gap_type",
    "/consults": "local_attempts",
    "/decisions": "confidence",
    "/events": "failure_key",
}

LIST_PAGES = tuple(RAW_FIELD_BY_PAGE)

# The short answer must name the first thing to look at, and it must do
# that when the store is empty too: an empty store is a real state, not a
# zero to be hidden.
ANSWER_LEAD = "the short answer"


def _seeded_client(tmp_path, monkeypatch):
    dbp = new_db(tmp_path)
    add_episode(dbp)
    add_event(dbp)
    add_lesson(dbp)
    add_gap(dbp)
    add_consult(dbp)
    add_decision(dbp)
    return client_for(dbp, monkeypatch)


def test_list_pages_open_with_a_raw_tier_summary(tmp_path, monkeypatch):
    client = _seeded_client(tmp_path, monkeypatch)
    for path in LIST_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "raw fields" in resp.text, f"{path} has no raw-evidence tier"


def test_raw_tier_carries_field_name_and_exact_value(tmp_path, monkeypatch):
    client = _seeded_client(tmp_path, monkeypatch)
    for path, field in RAW_FIELD_BY_PAGE.items():
        text = client.get(path).text
        assert field in text, f"{path} tier omits the field name {field}"
    # the exact stored timestamp surfaces in the tier, not only the
    # humanized "Sep 22" the plain table shows
    assert "2026-09-22T10:00:00+00:00" in client.get("/episodes").text


def test_raw_tier_never_leaks_a_secret(tmp_path, monkeypatch):
    from webseed import SECRET
    client = _seeded_client(tmp_path, monkeypatch)
    for path in LIST_PAGES:
        text = client.get(path).text
        assert SECRET not in text, f"{path} raw tier leaked a secret"


def test_overview_answers_in_plain_words_before_its_first_table(
        tmp_path, monkeypatch):
    client = _seeded_client(tmp_path, monkeypatch)
    text = client.get("/").text
    assert ANSWER_LEAD in text
    assert text.index(ANSWER_LEAD) < text.index("<table")


def test_pages_still_answer_when_the_store_is_empty(tmp_path, monkeypatch):
    # degraded rendering is the console's whole premise: an empty store
    # answers in words, it does not 500 and it does not print a bare table.
    # The capture report reads the live machine, so pin the whole
    # environment to an empty one: no sink, no dsh sessions, no stores.
    # A sink that is merely down is a real fault and must say so, so the
    # empty-store state is a sink that was never configured at all.
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MINDER_DSH_HOME", str(tmp_path / "dsh"))
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "no-hooks.json"))
    client = client_for(new_db(tmp_path), monkeypatch)
    for path in ("/", "/scorecard") + LIST_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert ANSWER_LEAD in resp.text, path
    # the empty store is named as an empty store, not as a clean bill
    assert "nothing to look at yet" in client.get("/episodes").text
    # and the landing page says why it is empty: nothing has been wired
    # up to record, which is a different fact from a broken recorder
    assert "nothing has been recorded yet" in client.get("/").text
    # a sink that is configured but not answering is a fault, and is
    # never described as an empty store
    monkeypatch.setenv("MINDER_SINK_URL", "http://127.0.0.1:1")
    assert "capture is broken" in client.get("/").text


def test_capture_raw_tier_reads_the_page_model(tmp_path, monkeypatch):
    """The raw tier calls .get() on each row, so a mapping it renders
    must be rows, not a bare dict. The sandbox block is the one tier fed
    from a mapping in the capture report: with a dict it renders no cells
    and no values, which is a silent loss of the evidence tier rather
    than a crash. Seed one session so the tier has a value to show."""
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "no-hooks.json"))
    import dshseed
    root, _ = dshseed.make_home(tmp_path)
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    client = client_for(new_db(tmp_path), monkeypatch)
    text = client.get("/capture").text
    assert "raw fields" in text
    assert "workspace-write" in text

def test_every_raw_tier_renders_its_values(tmp_path, monkeypatch):
    """A tier whose rows are not dicts renders its header and no body
    cells: the macro calls .get() on each row, so a mapping or a list of
    tuples is a silent loss of the evidence tier, not a crash. The plain
    table above it still renders, so the page looks fine while the
    evidence is gone. Every field name each template declares must also
    appear inside that tier's own rendered block."""
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)
    monkeypatch.setenv("MINDER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "no-hooks.json"))
    import json
    import dshseed
    root, _ = dshseed.make_home(tmp_path)
    dshseed.write_projection(root, "session-cccc-dddd",
                             sandbox="danger-full-access")
    dshseed.write_session(root, "session-cccc-dddd", "/home/dev/proj2")
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    # the baselines tier only renders when a pinned baseline exists, so
    # seed one or the test would check a tier the page never shows
    import minder_op.benchmark as bench_mod
    bench = tmp_path / "benchmarks"
    (bench / "baselines").mkdir(parents=True)
    manifest = {"manifest_version": 1, "suite_id": "suite-x", "title": "t",
                "tiers": [0], "tasks": [
                    {"task_id": "t0", "tier": 0, "family": "contract",
                     "title": "t", "fixtures": [],
                     "expected": "tests_pass",
                     "forbidden": sorted(bench_mod.FORBIDDEN_VOCABULARY)}]}
    (bench / "suite-x").mkdir()
    (bench / "suite-x" / "manifest.json").write_text(json.dumps(manifest))
    (bench / "baselines" / "suite-x.json").write_text(json.dumps({
        "report_version": 1, "suite_id": "suite-x",
        "suite_fingerprint": bench_mod.manifest_fingerprint(manifest),
        "generated_at": "2026-09-23T10:00:00+00:00", "kind": "candidate",
        "runs": [], "metrics": {
            "comparable_runs": 25, "verified_completion_rate": 0.9,
            "unsafe_executions": 0, "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0}}))
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(bench))
    # seed the rows the tiers read: an unseeded page renders a tier with
    # no rows, which is a real state but proves nothing about the tier
    dbp = new_db(tmp_path)
    add_episode(dbp)
    add_event(dbp)
    add_lesson(dbp)
    add_gap(dbp)
    add_consult(dbp)
    add_decision(dbp)
    # the difficulty tier reads the proxy ledger, not the memory DB
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text(json.dumps({
        "ts": 1_790_000_000, "task": "proxy",
        "event": "difficulty_routed",
        "label": "medium", "score": 0.4, "confidence": 0.8,
        "band": "medium", "effort": "medium", "max_tokens": 4096,
        "guardrail": 200000, "shadow": False}) + "\n")
    from minder_memory import trace_reviews
    trace_reviews.store_review(
        {"run_id": "mndr_run_x", "source": {"session_id": "session-x"}},
        [{"finding_id": "trf_abc123", "session_id": "session-x",
          "evaluator": "success_loop", "rule_id": "success-loop",
          "severity": "medium", "message": "repeated call"}],
        {"findings": 1, "highest_severity": "medium", "tool_calls": 12},
        report={"status": "ok"}, db_path=dbp)
    client = client_for(dbp, monkeypatch)

    from pathlib import Path
    from minder_web import app as app_module
    templates = Path(app_module.__file__).parent / "templates"
    checked = 0
    for file in sorted(templates.glob("*.html")):
        if file.name in ("base.html", "_answer.html", "_raw.html"):
            continue
        # bind each declared field list to the rows expression the
        # template passes it, so a tier cannot borrow another tier's
        # rendered block just because it appears later on the page
        calls = re.findall(r"raw_tier\(\[([^\]]*)\],[^)]*\)",
                           file.read_text(encoding="utf-8"))
        if not calls:
            continue
        path = "/" + ("" if file.name == "overview.html"
                      else file.name[:-5])
        resp = client.get(path)
        assert resp.status_code == 200, path
        blocks = re.findall(r'<details class="raw">.*?</details>',
                            resp.text, re.S)
        assert len(blocks) == len(calls), (
            f"{path}: {len(calls)} tier(s) declared, "
            f"{len(blocks)} rendered")
        for fields, block in zip(calls, blocks):
            names = re.findall(r"'([^']+)'", fields)
            for field in names:
                assert f'<th scope="col">{field}</th>' in block, (
                    f"{path}: tier for {fields} omits {field}")
                checked += 1
            cells = re.findall(r"<td><code>", block)
            assert cells, (
                f"{path}: tier for {names} rendered no value cells")

    assert checked > 60, f"only {checked} tier fields checked"

