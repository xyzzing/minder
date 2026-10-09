"""Two-reader console contract (issue #12): each page answers in plain
words first, and every list page carries a raw-evidence tier whose values
are the same redacted model values, never a raw DB row. Redaction is a
service-layer contract, so the tier must not become a leak path."""

from webseed import (add_consult, add_decision, add_episode, add_event,
                     add_gap, add_lesson, client_for, new_db)

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
