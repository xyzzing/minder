"""Section 7.6 write preconditions for the console (issue #89, stage 3).

Every console write route registers through post_route, never a bare
@app.post. The wrapper enforces what minder-v10 section 7.6 names for a
web write: a CSRF token bound to the action and its detail (the server
renders it into the form; no cookies, the console is zero-JS), and an
actor the action is attributed to. The loopback bind and the Host guard
remain the boundary they always were; this adds the preconditions they
were standing in for. The audit row is the handler's to append - the
inbox resolves into human_input_events, which is the attribution of
record.

The gate (tests/test_web_write_gates.py) holds the law on the live app
object: a POST route without the guard marker fails the suite wherever
it lands.
"""
import hashlib
import hmac
import os
from pathlib import Path

import minder
from fastapi import Request

TOKEN_FIELD = "csrf_token"
ACTOR_FIELD = "actor"
SECRET_NAME = "web_write.secret"


class WriteRefused(Exception):
    """A write refused before its handler ran; .code is stable."""

    def __init__(self, code, detail=""):
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code


def _secret_path():
    # read at call time so tests can point the module at a temp state
    # dir without reloading
    return Path(minder.STATE_DIR) / SECRET_NAME


def _secret():
    path = _secret_path()
    try:
        data = path.read_text().strip()
        if data:
            return data
    except OSError:
        pass
    import secrets
    fresh = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, fresh.encode())
    finally:
        os.close(fd)
    return fresh


def csrf_token(action, detail):
    """A token that names one action on one item state; the form embeds
    it and the POST must present it unchanged."""
    key = hashlib.sha256(_secret().encode()).digest()
    return hmac.new(key, f"{action}|{detail}".encode(),
                    hashlib.sha256).hexdigest()


def csrf_ok(action, detail, token):
    return hmac.compare_digest(csrf_token(action, detail),
                               str(token or ""))


def post_route(app, path, *, action, detail_fn):
    """Register a POST route under the section 7.6 preconditions.

    detail_fn(request, form) derives what the token is bound to from
    server-visible state (path params, form fields) - never a value the
    form asserts on its own. The handler receives (request, form,
    actor) and appends the audit row.
    """

    def register(handler):
        # no functools.wraps: FastAPI reads the endpoint signature, and
        # __wrapped__ would re-expose the handler's (request, form,
        # actor) params as required query arguments
        async def guarded(request: Request):
            form = await request.form()
            detail = detail_fn(request, form)
            if not csrf_ok(action, detail, form.get(TOKEN_FIELD)):
                raise WriteRefused("csrf_invalid", action)
            actor = str(form.get(ACTOR_FIELD) or "").strip()
            if not actor:
                raise WriteRefused("actor_missing", action)
            return handler(request, form, actor)

        guarded.__name__ = getattr(handler, "__name__", "guarded")
        guarded.__doc__ = handler.__doc__
        guarded._minder_write_guard = True
        app.post(path)(guarded)
        return guarded

    return register


async def write_refused_handler(request, exc):
    """A refused write answers with its stable code, never a traceback."""
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": exc.code}, status_code=400)
