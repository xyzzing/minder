"""The section 7.6 write gate (#89, stage 3, acceptance 11): every POST
route in the console must be registered through the guarded wrapper
that enforces the CSRF token and the actor. The gate inspects the live
app object, so a future bare @app.post fails here regardless of where
it lands.
"""
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
