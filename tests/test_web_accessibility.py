"""Accessibility contract for the console (issue #12, I3). Pins what a
keyboard, screen-reader or touch user can actually perceive in the
rendered page: an accessible name on every control, the current page
marked in the nav, a status word on every badge, and a caption naming
what a table's rows are. Element existence is not the point - the point
is that the name or word is present where the user would read it."""
import re

from webseed import (add_consult, add_decision, add_episode, add_event,
                     add_gap, add_lesson, client_for, new_db)

CAPTIONED_PAGES = ("/episodes", "/lessons", "/gaps", "/consults",
                   "/decisions", "/events", "/sessions", "/traces")
FILTERED_PAGES = ("/events", "/sessions")


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


def test_tables_name_their_row_unit(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    for path in CAPTIONED_PAGES:
        text = client.get(path).text
        assert "<caption>" in text, f"{path} table has no caption"
        assert 'scope="col"' in text, f"{path} headers lack scope"


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
