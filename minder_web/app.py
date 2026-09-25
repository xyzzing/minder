"""minder_web — localhost, read-only operator console (8E).

Reuse rules: routes call minder_web.services, which call minder_op
query/summary functions. No SQL here, no policy, no writes: every
route is GET and there is no write endpoint in 8E.

Deployment boundary (docs/operator-web.md): bind 127.0.0.1 only; the
__main__ entrypoint refuses any non-loopback host. If the web extra is
not installed (`pip install -r requirements-web.txt`) importing this
module fails cleanly — it is never imported by hook.py/proxy.py or any
hot runtime path.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from minder_web import services

_PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))

app = FastAPI(title="minder operator console", docs_url=None,
              redoc_url=None, openapi_url=None)


def _db_path():
    # resolved per request so tests and systemd units can point the
    # same app at different stores
    import os
    from minder_op.queries import resolve_path
    return resolve_path(os.environ.get("MINDER_WEB_DB"))


def render(request, template, context):
    context["request"] = request
    context["health"] = services.health(_db_path())
    return templates.TemplateResponse(request, template + ".html",
                                      context)


@app.get("/")
def overview(request: Request):
    return render(request, "overview", services.overview(_db_path()))


@app.get("/episodes")
def episodes(request: Request, limit: int = services.DEFAULT_LIMIT):
    return render(request, "episodes",
                  services.episodes_page(_db_path(), limit))


@app.get("/episodes/{episode_id}")
def episode(request: Request, episode_id: str):
    detail = services.episode_detail(_db_path(), episode_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return render(request, "episode", detail)


@app.get("/lessons")
def lessons(request: Request, status: str = None,
            limit: int = services.DEFAULT_LIMIT):
    return render(request, "lessons",
                  services.lessons_page(_db_path(), status, limit))


@app.get("/lessons/{lesson_id}")
def lesson(request: Request, lesson_id: str):
    detail = services.lesson_detail(_db_path(), lesson_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return render(request, "lesson", detail)


@app.get("/gaps")
def gaps(request: Request):
    return render(request, "gaps", services.gaps_page(_db_path()))


@app.get("/consults")
def consults(request: Request, limit: int = services.DEFAULT_LIMIT):
    return render(request, "consults",
                  services.consults_page(_db_path(), limit))


@app.get("/consults/{trace_id}")
def consult(request: Request, trace_id: str):
    detail = services.consult_detail(_db_path(), trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return render(request, "consult", detail)


@app.get("/decisions")
def decisions(request: Request, limit: int = services.DEFAULT_LIMIT):
    return render(request, "decisions",
                  services.decisions_page(_db_path(), limit))


@app.get("/difficulty")
def difficulty(request: Request, limit: int = services.DEFAULT_LIMIT):
    # Reads the proxy's events.jsonl ledger, not the DB — no db_path.
    return render(request, "difficulty",
                  services.difficulty_page(limit))


@app.get("/events")
def events(request: Request, limit: int = services.DEFAULT_LIMIT,
           event_type: str = None, tool: str = None,
           failure_key: str = None, session: str = None):
    return render(request, "events",
                  services.events_page(_db_path(), limit, event_type, tool,
                                       failure_key, session))


@app.get("/sessions")
def sessions(request: Request, q: str = None, sort: str = None,
             limit: int = services.DEFAULT_LIMIT):
    return render(request, "sessions",
                  services.sessions_page(_db_path(), q, sort, limit))


@app.get("/sessions/{session_id}")
def session(request: Request, session_id: str):
    detail = services.session_detail_page(_db_path(), session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return render(request, "session", detail)


@app.get("/capture")
def capture(request: Request):
    return render(request, "capture", services.capture_page(_db_path()))


@app.get("/scorecard")
def scorecard(request: Request, window_hours: int = 24):
    return render(request, "scorecard",
                  services.scorecard_page(_db_path(), window_hours))


@app.get("/skills")
def skills(request: Request):
    return render(request, "skills", services.skills_page())


@app.get("/benchmarks")
def benchmarks(request: Request):
    return render(request, "benchmarks",
                  services.benchmarks_page())


@app.get("/healthz")
def healthz():
    return JSONResponse(services.health(_db_path()))


app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")),
          name="static")
