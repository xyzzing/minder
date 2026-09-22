"""Web read-model service tests (8E, PRD D1).

Empty or partial v10 stores return safe models (never raise); a
candidate lesson stays a candidate; secrets are redacted before any
template can see them. The HTTP surface is GET-only — asserted on the
route table itself.
"""
import pytest

from minder_web import services

import webseed
from webseed import SECRET, new_db


def test_overview_on_empty_db_is_safe(tmp_path):
    dbp = new_db(tmp_path)
    model = services.overview(dbp)
    assert model["db_ok"] is True
    assert model["health"]["status"] == "ok"
    assert model["summary"]["focus"] == []
    assert model["summary"]["benchmarks"] == {"status": "not available"}


def test_overview_on_missing_db_never_raises(tmp_path):
    model = services.overview(tmp_path / "nope.sqlite")
    assert model["db_ok"] is False
    assert model["summary"] is None
    assert model["focus"] == []
    assert model["health"]["status"] == "degraded"


def test_page_models_on_missing_db_are_empty(tmp_path):
    missing = tmp_path / "nope.sqlite"
    assert services.episodes_page(missing)["rows"] == []
    assert services.lessons_page(missing)["rows"] == []
    assert services.gaps_page(missing)["rows"] == []
    assert services.consults_page(missing)["rows"] == []
    assert services.decisions_page(missing)["rows"] == []
    assert services.episode_detail(missing, "x") is None
    assert services.lesson_detail(missing, "x") is None
    assert services.consult_detail(missing, "x") is None


def test_candidate_lesson_stays_candidate(tmp_path):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp, lesson_id="les_c", status="candidate",
                       instruction="candidate only")
    model = services.lessons_page(dbp, status="candidate")
    assert [r["status"] for r in model["rows"]] == ["candidate"]
    # and the default (verified) view does not show it
    assert model["rows"] not in services.lessons_page(dbp)["rows"]


def test_secrets_redacted_in_service_models(tmp_path):
    dbp = new_db(tmp_path)
    webseed.add_lesson(dbp)  # instruction embeds SECRET
    model = services.lesson_detail(dbp, "les1")
    assert SECRET not in model["lesson"]["instruction"]
    listing = services.lessons_page(dbp)
    assert all(SECRET not in (r["instruction"] or "")
               for r in listing["rows"])


def test_limit_is_bounded(tmp_path):
    assert services.episodes_page(tmp_path / "x", limit=10_000)[
        "limit"] == services.MAX_LIMIT
    assert services.episodes_page(tmp_path / "x", limit=-5)["limit"] == \
        services.DEFAULT_LIMIT
    assert services.episodes_page(tmp_path / "x", limit="junk")[
        "limit"] == services.DEFAULT_LIMIT


def test_http_surface_is_get_only():
    from minder_web.app import app
    unsafe = {"POST", "PUT", "DELETE", "PATCH"}
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            assert not methods & unsafe, (route.path, methods)
