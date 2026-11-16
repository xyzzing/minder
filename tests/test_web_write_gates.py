"""The section 7.6 write gate (#89, stage 3, acceptance 11): every POST
route in the console must be registered through the guarded wrapper
that enforces the CSRF token and the actor. The gate inspects the live
app object, so a future bare @app.post fails here regardless of where
it lands.
"""
import os

import pytest

from minder_web import writes
from minder_web.app import app


def test_every_post_route_is_guarded():
    posts = [r for r in app.routes
             if getattr(r, "methods", None) and "POST" in r.methods]
    assert posts, "the console must keep at least one write path"
    unguarded = [r.path for r in posts
                 if not getattr(getattr(r, "endpoint", None),
                                "_minder_write_guard", False)]
    assert unguarded == [], (
        f"POST routes registered outside writes.post_route: {unguarded}")


def test_no_bare_post_registration_in_the_package():
    import pathlib
    import minder_web
    pkg = pathlib.Path(minder_web.__file__).parent
    offenders = []
    for py in pkg.glob("*.py"):
        if "@app.post(" in py.read_text():
            offenders.append(py.name)
    assert offenders == [], (
        f"bare POST registrations in: {offenders}; use writes.post_route")


def test_actor_prefill_lives_in_the_strings_module():
    """C2 (C3): both write forms prefill the actor from the strings
    module default (S.common.actor_default); neither template re-types
    the literal."""
    import pathlib

    import minder_web
    from minder_web import strings_base
    assert strings_base.S["common"].get("actor_default"), (
        "the actor prefill default must live in the strings module")
    pkg = pathlib.Path(minder_web.__file__).parent
    for name in ("inbox.html", "engine_confirm.html"):
        text = (pkg / "templates" / name).read_text()
        assert 'value="operator"' not in text, (
            f"{name} hardcodes the actor prefill; render"
            " S.common.actor_default")


def test_csrf_token_binds_action_and_detail():
    token = writes.csrf_token("inbox_resolve", "item|rev1")
    assert writes.csrf_ok("inbox_resolve", "item|rev1", token)
    assert not writes.csrf_ok("inbox_resolve", "item|rev2", token)
    assert not writes.csrf_ok("engine_switch", "item|rev1", token)
    assert not writes.csrf_ok("inbox_resolve", "item|rev1", "")


def test_post_route_sets_the_guard_marker():
    """The gate greps for _minder_write_guard on live routes; this
    pins that post_route itself sets it, on a throwaway app."""
    from fastapi import FastAPI

    test_app = FastAPI()

    def detail(request, form):
        return "d"

    @writes.post_route(test_app, "/w", action="a", detail_fn=detail)
    async def handler(request, form, actor):  # noqa: ARG001
        return "ok"

    assert getattr(handler, "_minder_write_guard", False) is True


class _RacerOS:
    """A stand-in for the os module minder_web.writes uses: open reaches
    the real os the first time (the corrupt file answers EEXIST as it
    does today); from the second call on, a concurrent opener has just
    (re-)created the path and EEXIST lands again - the exact window
    between the corrupt file's unlink and the exclusive re-open."""

    def __init__(self, real, path, content):
        self._real = real
        self._path = path
        self._content = content
        self.calls = 0

    def open(self, path, flags, mode=0o600):
        self.calls += 1
        if self.calls == 1:
            return self._real.open(path, flags, mode)
        self._path.write_text(self._content)
        raise FileExistsError(self._path)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_secret_replacement_survives_a_concurrent_opener(tmp_path,
                                                         monkeypatch):
    """C3: between the corrupt secret's unlink and the exclusive
    re-open, another opener can create the file again. That second
    FileExistsError is routine, so it must land on a stable honest
    outcome - the other opener's usable secret is adopted - never an
    uncaught crash that re-wedges every CSRF operation (I2)."""
    import minder
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(minder, "STATE_DIR", state)
    path = state / writes.SECRET_NAME
    path.write_text("")  # corrupt: present, but holds no usable key
    winner = "ab" * 32
    monkeypatch.setattr(writes, "os", _RacerOS(os, path, winner))
    assert writes._secret() == winner
    assert path.read_text().strip() == winner


def test_secret_replacement_fails_named_when_it_cannot_win(tmp_path,
                                                           monkeypatch):
    """C3, the pathological end: the concurrent opener keeps
    re-creating a corrupt file, so no attempt can win. The replacement
    is bounded and ends in a named OSError naming the path - never an
    uncaught FileExistsError traceback."""
    import minder
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(minder, "STATE_DIR", state)
    path = state / writes.SECRET_NAME
    path.write_text("")
    monkeypatch.setattr(writes, "os", _RacerOS(os, path, ""))
    with pytest.raises(OSError, match=r"^web_write\.secret:"):
        writes._secret()
