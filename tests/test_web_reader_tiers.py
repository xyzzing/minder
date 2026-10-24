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

def _fully_seeded_client(tmp_path, monkeypatch):
    """A client whose every console page renders real content, not an
    empty state. Each store a page reads is seeded here: the memory DB, a
    dsh home, the proxy ledger, a pinned benchmark baseline and an engine
    registry. An unseeded page still returns 200 with no rows, and a tier
    with no rows proves nothing about the tier's markup (M2)."""
    import json

    import dshseed
    import minder
    import minder_op.benchmark as bench_mod
    import minder_web.services as services
    from minder_memory import trace_reviews

    dbp = new_db(tmp_path)
    add_episode(dbp)
    add_event(dbp)
    add_lesson(dbp)
    add_gap(dbp)
    add_consult(dbp)
    add_decision(dbp)
    review_id, status = trace_reviews.store_review(
        {"run_id": "mndr_run_x", "source": {"session_id": "session-x"}},
        [{"finding_id": "trf_abc123", "session_id": "session-x",
          "evaluator": "success_loop", "rule_id": "success-loop",
          "severity": "medium", "message": "repeated call"}],
        {"findings": 1, "highest_severity": "medium", "tool_calls": 12},
        report={"status": "ok"}, db_path=dbp)
    assert status == "ok", status

    root, wrote = dshseed.make_home(tmp_path)
    assert wrote, "the fixture session log was not written"
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text(json.dumps({
        "ts": 1_790_000_000, "task": "proxy",
        "event": "difficulty_routed",
        "label": "medium", "score": 0.4, "confidence": 0.8,
        "band": "medium", "effort": "medium", "max_tokens": 4096,
        "guardrail": 200000, "shadow": False}) + "\n")
    monkeypatch.setenv("MINDER_STATE_DIR", str(state))
    monkeypatch.setenv("MINDER_HOOKS_JSON", str(tmp_path / "no-hooks.json"))
    monkeypatch.delenv("MINDER_SINK_URL", raising=False)

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

    cfg = tmp_path / "minder.json"
    monkeypatch.setattr(minder, "CFG_PATH", cfg)
    cfg.write_text(json.dumps({
        "engines": {
            "llama": {"upstream": "http://127.0.0.1:8080",
                      "unit": "u-llama"},
            "strata": {"upstream": "http://127.0.0.1:8081",
                       "unit": "u-strata"},
        },
        "active_engine": "strata"}))
    monkeypatch.setattr(services, "_engine_rows", lambda: [
        {"name": "llama", "active": False, "upstream": "http://127.0.0.1:8080",
         "unit": "u-llama", "unit_state": "inactive", "healthy": True},
        {"name": "strata", "active": True, "upstream": "http://127.0.0.1:8081",
         "unit": "u-strata", "unit_state": "active", "healthy": True},
    ])
    return dbp, client_for(dbp, monkeypatch), review_id


# path parameter name -> a seeded id that exists in the client above
_PAGE_IDS = {"episode_id": "ep1", "lesson_id": "les1", "trace_id": "tr1",
             "session_id": "session-aaaa-bbbb"}


def _console_pages(app_module, review_id):
    """Every GET page route the app declares, with a seeded id
    substituted for each path parameter. Derived from the app, so a new
    page is visited by this gate the day it lands instead of waiting for
    a hand-kept list to be updated (M2)."""
    pages = []
    for route in app_module.app.routes:
        path = getattr(route, "path", None)
        methods = set(getattr(route, "methods", None) or ())
        if path is None or "GET" not in methods:
            continue
        if path in ("/healthz", "/static"):
            continue
        url = path
        for name, value in _PAGE_IDS.items():
            url = url.replace("{" + name + "}", value)
        if "{" in url:
            url = url.replace("{" + url.split("{")[1].split("}")[0] + "}",
                              review_id)
        if route.name == "engine_switch_confirm":
            url += "?engine=strata"
        pages.append(url)
    return pages


def test_no_page_nests_a_block_element_inside_a_table(tmp_path, monkeypatch):
    """The raw-evidence tier is a `<details>` that summarizes a table, so
    it must follow the `</table>`: emitted between `</tbody>` and
    `</table>` it is invalid HTML and the browser lifts it out of the
    table it belongs to, so the tier no longer reads as that table's
    evidence. Nothing else in the suite sees this - the tier tests match
    the rendered block with a regex, which is happy either way - and the
    ratchet counts no `.html`, so a template can be malformed and
    uncounted. Parsed, not matched: a regex over the source cannot tell a
    nested block from a following one."""
    from html.parser import HTMLParser

    import minder_web.app as app_module

    class _Nesting(HTMLParser):
        """Checks the table content model (WHATWG 4.9.1, 4.9.8): a
        `<table>` element may directly contain only `caption`, `colgroup`,
        `thead`, `tbody`, `tfoot`, `tr` and script-supporting elements,
        and a `<tr>` may directly contain only `td`/`th`. Anything else
        opened while a table (or a row) is open - the `<details>` of a raw
        -evidence tier emitted between `</tbody>` and `</table>` - makes a
        browser close the table early and lift the block out of it. Flow
        content inside a cell (`<div>`, `<p>`) is legal, so the parser
        tracks whether it is inside a cell and does not flag those."""

        TABLE_CHILDREN = ("caption", "colgroup", "thead", "tbody", "tfoot",
                          "tr", "script", "template")
        ROW_CHILDREN = ("td", "th")
        # void / self-closing, never pushed
        VOID = ("col", "br", "hr", "img", "input", "meta", "link")

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.table_depth = 0   # >0 while inside a <table>
            self.in_row = False    # inside a <tr>, not yet in a cell
            self.in_cell = False   # inside a <td>/<th>
            self.bad = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                if self.table_depth:
                    self.bad.append("table")
                self.table_depth += 1
                self.in_row = False
                self.in_cell = False
                return
            if not self.table_depth or tag in self.VOID:
                return
            if self.in_cell:
                return  # any flow content is legal inside a cell
            if self.in_row:
                if tag not in self.ROW_CHILDREN:
                    self.bad.append(tag)
                if tag in self.ROW_CHILDREN:
                    self.in_cell = True
                return
            # directly inside the table, not in a row or cell
            if tag not in self.TABLE_CHILDREN:
                self.bad.append(tag)
            if tag == "tr":
                self.in_row = True

        def handle_startendtag(self, tag, attrs):
            pass

        def handle_endtag(self, tag):
            if tag == "table" and self.table_depth:
                self.table_depth -= 1
                self.in_row = False
                self.in_cell = False
            elif tag == "tr":
                self.in_row = False
                self.in_cell = False
            elif tag in ("td", "th"):
                self.in_cell = False

    _dbp, client, review_id = _fully_seeded_client(tmp_path, monkeypatch)
    pages = _console_pages(app_module, review_id)
    assert len(pages) >= 20, (
        f"the route walk found only {len(pages)} GET pages (M2)")
    checked = 0
    for path in pages:
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.status_code)
        parser = _Nesting()
        parser.feed(resp.text)
        assert not parser.bad, (
            f"{path}: element(s) {sorted(set(parser.bad))} opened inside "
            "a <table>; the raw-evidence tier must come after </table>")
        checked += 1
    assert checked == len(pages), (
        f"only {checked} of {len(pages)} pages parsed (M2)")


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
    # the provenance tier only renders when a rulebook-provenance/v1
    # file is configured (Roomwise owns the rulebook since #44); seed
    # one or the test would check a tier the page never shows
    prov = tmp_path / "provenance.json"
    prov.write_text(json.dumps({
        "schema": "roomwise.rulebook-provenance/v1",
        "rows": [{"rule": "sg_gst.rate", "verification": "primary",
                  "verified_on": "2026-09-30", "source": "EPA",
                  "caveat": "rate reviewed annually"}]}))
    config = tmp_path / "minder.json"
    config.write_text(json.dumps(
        {"rulebook_provenance_path": str(prov)}))
    monkeypatch.setenv("MINDER_CONFIG", str(config))
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



# Each plain-table column label, mapped to the storage field the tier must
# print for it. Declared here rather than derived from the template: the
# point of the gate is that the tier carries the evidence behind a column
# a human was shown, and only a human map says which field that is.
PLAIN_TO_RAW = {
    "/sessions": {
        "project": "project_path",
        "title / session": "title",
        "last seen": "mtime",
        "turns": "turns",
        "steps": "steps",
        "tokens": "tokens_total",
        "context": "context_pressure_pct",
        "sandbox": "sandbox_mode",
        "capture": "event_count",
        # the project cell also carries the archived badge and the
        # title cell the no-projection badge, so those two fields are
        # shown and must be named too
        "archived": "archived",
        "no projection": "has_projection",
    },
    "/decisions": {
        "id": "id",
        "ts": "ts",
        "contract": "contract_id",
        "failure_key": "failure_key",
        "model": "model_recommendation",
        "policy": "policy_decision",
        "override": "override",
        "confidence": "confidence",
        "provider": "provider",
    },
    "/traces": {
        "reviewed": "ts_full",
        "session": "session_id",
        "severity": "highest_severity",
        "findings": "findings",
        "tools": "tool_calls",
        "failures": "failures",
        "tokens": "tokens_total",
        "rubric": "rubric_id",
        "evaluator": "evaluator_version",
    },
    "/domains": {
        "domain": "domain",
        "suite": "suite_id",
        "tasks": "tasks",
        "status": "status",
        # the verdict sentence is computed from three baseline fields;
        # the tier names the fields, not the sentence built from them
        "what this means": "baseline_generated_at",
        "rule": "rule",
        "grade": "verification",
        "verified on": "verified_on",
        "caveat": "caveat",
    },
}


def test_every_plain_column_has_a_raw_field_behind_it(tmp_path, monkeypatch):
    """Issue #12: the raw tier is the technical reader's way to check the
    console against a CLI row, so it must cover every column of the plain
    table it summarizes. A tier that lists nine fields under a table with
    nine columns can still be missing the one that matters: /decisions
    shows a contract column built from contract_id and contract_version
    while its tier named neither, and /sessions shows context pressure,
    the session title and the log format with no raw field for any of
    them. Checked by parsing the rendered page: the plain table's header
    labels in order, zipped against the tier's field names in order.

    The map from label to field is declared above, not inferred from the
    template, because which storage field stands behind a human label is
    a human decision. A label missing from the map fails, so adding a
    column to a covered page without naming its field fails too."""
    from html.parser import HTMLParser

    class _Cols(HTMLParser):
        """The plain table's `<th scope="col">` texts, and the field names
        the raw tier declares in its `<summary>`. The tier sits outside the
        plain table (slice 1) and nests its own table, so anything inside a
        `<details>` is skipped entirely: the tier's header cells are its
        storage names, not the plain columns, and mixing them in would make
        the gate pass on the tier's own headers."""

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.plain_cols = []
            self.tier_fields = []
            self.in_th = 0
            self.in_details = 0

        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.in_details += 1
            elif self.in_details:
                return
            elif tag == "th" and dict(attrs).get("scope") == "col":
                self.in_th += 1

        def handle_endtag(self, tag):
            if tag == "details" and self.in_details:
                self.in_details -= 1
            elif tag == "th" and self.in_th and not self.in_details:
                self.in_th -= 1

        def handle_data(self, data):
            if self.in_details:
                return
            text = (data or "").strip()
            if text and self.in_th:
                self.plain_cols.append(text)

    class _Summary(HTMLParser):
        """Every raw tier on the page, in order: the field names each one
        declares in its `<summary>` line, `raw fields: a, b, c`. Read from
        the rendered page, so the gate checks what the reader is told the
        tier contains, not the template source. A page with two plain
        tables has two tiers, so all of them are collected."""

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tiers = []
            self.in_raw = 0
            self.in_summary = 0

        def handle_starttag(self, tag, attrs):
            if tag == "details" and "raw" in (
                    dict(attrs).get("class") or ""):
                self.in_raw += 1
                self.tiers.append([])
            elif tag == "summary" and self.in_raw:
                self.in_summary += 1

        def handle_endtag(self, tag):
            if tag == "details" and self.in_raw:
                self.in_raw -= 1
            elif tag == "summary" and self.in_summary:
                self.in_summary -= 1

        def handle_data(self, data):
            if not self.in_summary or not self.tiers:
                return
            names = (data or "").split(":", 1)[-1]
            for name in names.split(","):
                name = name.strip()
                if name:
                    self.tiers[-1].append(name)

    _dbp, client, review_id = _fully_seeded_client(tmp_path, monkeypatch)
    for path, mapping in PLAIN_TO_RAW.items():
        text = client.get(path).text
        plain = _Cols()
        plain.feed(text)
        tier = _Summary()
        tier.feed(text)
        columns = plain.plain_cols
        fields = set()
        for tier_fields in tier.tiers:
            fields.update(tier_fields)
        assert columns, f"{path}: the plain table has no column headers"
        assert fields, f"{path}: no raw tier summary rendered"
        assert len(columns) >= 4, (
            f"{path}: the plain table exposed only {columns} (M2)")
        for column in columns:
            assert column in mapping, (
                f"{path}: column {column!r} is not in PLAIN_TO_RAW; name "
                "the storage field behind it (M2)")
            field = mapping[column]
            assert field in fields, (
                f"{path}: the plain table shows {column!r} but its raw "
                f"tier has no {field} field; the tier covers "
                f"{sorted(fields)}")
