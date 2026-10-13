"""Accessibility contract for the console (issue #12, I3). Pins what a
keyboard, screen-reader or touch user can actually perceive in the
rendered page: an accessible name on every control, the current page
marked in the nav, a status word on every badge, and a caption naming
what a table's rows are. Element existence is not the point - the point
is that the name or word is present where the user would read it."""
import re

from test_web_reader_tiers import _console_pages, _fully_seeded_client
from webseed import (add_consult, add_decision, add_episode, add_event,
                     add_gap, add_lesson, client_for, new_db)

FILTERED_PAGES = ("/events", "/sessions")

# A `<td>` that spans the whole row is an empty-state message, not a data
# cell, so it has no row header to name.
_CELL_SPAN = re.compile(r"<td[^>]*\bcolspan=[\"']?([0-9]+)")


def _client(tmp_path, monkeypatch):
    """One seeded store, one client. Each test gets its own tmp_path, so
    the seed runs once per test; building a second client for the same
    store would insert the same ids twice."""
    dbp = new_db(tmp_path)
    add_episode(dbp)
    add_event(dbp)
    add_lesson(dbp)
    add_gap(dbp)
    add_consult(dbp)
    add_decision(dbp)
    return client_for(dbp, monkeypatch)


def _tables(client, pages):
    """Every rendered `<table>`, with its caption and its cells. Built
    from the app's own GET routes, so a new page is checked the day it
    lands instead of waiting for a hand-kept list to be updated (M2)."""
    from html.parser import HTMLParser

    class _Tables(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tables = []
            self.stack = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.stack.append({"caption": None, "rows": [],
                                   "row": None})
            elif not self.stack:
                return
            top = self.stack[-1]
            if tag == "caption":
                top["_in_caption"] = True
            elif tag == "tr":
                top["row"] = []
            elif tag in ("td", "th"):
                if top.get("_in_caption"):
                    return
                if top["row"] is None:
                    top["row"] = []
                top["row"].append({"tag": tag,
                                   "scope": dict(attrs).get("scope")})

        def handle_endtag(self, tag):
            if tag == "caption":
                if self.stack:
                    self.stack[-1].pop("_in_caption", None)
            elif tag == "tr":
                if self.stack and self.stack[-1]["row"]:
                    self.stack[-1]["rows"].append(self.stack[-1]["row"])
                    self.stack[-1]["row"] = None
            elif tag == "table" and self.stack:
                top = self.stack.pop()
                if top["caption"] is None and top.get("_caption_text"):
                    top["caption"] = top["_caption_text"]
                self.tables.append(top)

        def handle_data(self, data):
            if self.stack and self.stack[-1].get("_in_caption"):
                text = (data or "").strip()
                if text:
                    self.stack[-1]["_caption_text"] = text

    found = []
    for path in pages:
        parser = _Tables()
        parser.feed(client.get(path).text)
        for table in parser.tables:
            table["page"] = path
            found.append(table)
    return found


def test_first_focusable_thing_skips_the_navigation(tmp_path, monkeypatch):
    text = _client(tmp_path, monkeypatch).get("/").text
    body = text[text.index("<body"):]
    first_link = re.search(r'<a[^>]*href="([^"]*)"', body)
    assert first_link and first_link.group(1) == "#content", \
        "the first link in the page must be the skip link"


def test_nav_marks_the_current_page(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    text = client.get("/lessons").text
    nav = text[text.index("<nav"):text.index("</nav>")]
    assert re.search(r'<a href="/lessons"[^>]*aria-current="page"', nav), \
        "the active nav link must say it is the current page"
    other = client.get("/gaps").text
    onav = other[other.index("<nav"):other.index("</nav>")]
    assert 'aria-current="page"' in onav
    assert not re.search(r'<a href="/lessons"[^>]*aria-current="page"',
                         onav)


def test_filter_controls_have_an_accessible_name(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    for path in FILTERED_PAGES:
        text = client.get(path).text
        form = text[text.index("<form"):text.index("</form>")]
        for name in re.findall(r'<(?:select|input)[^>]*name="([^"]+)"',
                               form):
            # the accessible name is the label's `for`, which must match
            # the control's own id, not its query-string name
            cid = re.search(r'<(?:select|input)[^>]*name="' +
                            re.escape(name) + '"[^>]*id="([^"]+)"', form)
            if cid is None:
                cid = re.search(r'<(?:select|input)[^>]*id="([^"]+)"'
                                '[^>]*name="' + re.escape(name) + '"', form)
            assert cid, f"{path}: control {name} has no id to label"
            assert re.search(r'<label[^>]*for="' + re.escape(cid.group(1))
                             + '"', form), f"{path}: control {name} has " \
                "no label pointing at it"


def test_sandbox_risk_is_not_marked_as_a_good_state(tmp_path, monkeypatch):
    """A verified lesson and a session that ran with no filesystem
    sandbox must not share one badge: green currently reads "healthy"
    everywhere else in the console."""
    import dshseed
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True)
    sid = "session-9999-0000"
    cwd = "/home/dev/proj"
    dshseed.write_session(root, sid, cwd=cwd, steps=1,
                          sandbox="danger-full-access")
    # the list page reads the mode from dsh's projection cache, the same
    # source the real console uses; without it the cell is empty and
    # there is no badge to check
    dshseed.write_projection(root, sid, cwd=cwd,
                             sandbox="danger-full-access")
    monkeypatch.setenv("MINDER_DSH_HOME", str(root))
    client = _client(tmp_path, monkeypatch)
    text = client.get("/sessions").text
    assert "danger-full-access" in text
    cell = re.search(r'<span class="([^"]*)"[^>]*>danger-full-access',
                     text)
    assert cell and "badge-ok" not in cell.group(1) \
        and "badge-verified" not in cell.group(1), \
        "danger-full-access must not carry the ok badge"


def test_every_table_names_its_row_unit(tmp_path, monkeypatch):
    """Issue #12: `<caption>` on every table and `scope` on every header
    cell. A screen reader announces a cell as "row 3, column 5" unless the
    table names its row unit and its header cells declare which axis they
    head, so a caption on one table of a seven-table page is not enough.
    Checked per table, over every page the app serves, not over a
    hand-picked page list: the old version of this test looped eight list
    routes and asked only whether the page contained a `<caption>`
    somewhere, so 42 of the 63 tables could have none and the test still
    passed.

    `scope` is checked on `<th>` only, and a `<td>` carrying it is a
    failure of its own. HTML defines `scope` on the `th` element (the `td`
    element's attributes are colspan, rowspan and headers), so putting it
    on data cells is not an accessibility fix - it is invalid markup that
    validators reject, and the association it appears to add comes from the
    header cell's own scope."""
    import minder_web.app as app_module

    _dbp, client, review_id = _fully_seeded_client(tmp_path, monkeypatch)
    pages = _console_pages(app_module, review_id)
    assert len(pages) >= 20, (
        f"the route walk found only {len(pages)} GET pages (M2)")
    tables = _tables(client, pages)
    assert len(tables) >= 49, (
        f"the parse found only {len(tables)} tables across the console; "
        "the seed no longer renders the pages it claims to (M2)")
    headers = 0
    for index, table in enumerate(tables):
        where = f"{table['page']} (table #{index + 1} on that page)"
        assert table["caption"], f"{where} has no <caption>"
        for row in table["rows"]:
            for cell in row:
                if cell["tag"] == "th":
                    headers += 1
                    assert cell["scope"] in ("col", "row"), (
                        f"{where}: a <th> declares no scope")
                else:
                    assert cell["scope"] is None, (
                        f"{where}: a <td> carries scope={cell['scope']}")
    assert headers >= 200, (
        f"only {headers} header cells parsed across {len(tables)} tables; "
        "the seed is not rendering the console's headers (M2)")
    cells = sum(len(row) for t in tables for row in t["rows"])
    assert cells >= 300, (
        f"only {cells} cells parsed; the seed is not rendering the "
        "console's rows (M2)")


def test_status_words_are_not_color_only(tmp_path, monkeypatch):
    """Every badge carries its word inside the badge, so color is never
    the only channel (I3)."""
    client = _client(tmp_path, monkeypatch)
    for path in ("/episodes", "/lessons", "/consults"):
        text = client.get(path).text
        badges = re.findall(r'<span class="badge[^"]*">([^<]*)<', text)
        assert badges, f"{path} renders no status badge"
        for badge in badges:
            assert badge.strip(), f"{path}: badge with no word"
