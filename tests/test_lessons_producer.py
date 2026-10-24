"""Issue #42: lessons carry producer identity and model-specific
applicability. Single-model deployments see no behavior change: the
backfill defaults are 'unrecorded' + portable (0), and every existing
promote path keeps working untouched.

Design mirrors repo-agent's memory plane (its ref 98deac1): producer
identity is provenance, not a scope restriction — a lesson learned under
one engine may well hold for the next; the ones that name model-bound
behavior (prompts, thinking blocks, dialects) are the ones shelved.
"""
import pytest

from minder_memory import db, lessons


@pytest.fixture()
def store(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    conn = db.connect(db_path)
    conn.close()
    return db_path


def _seed_verified_episode(db_path):
    conn = db.connect(db_path)
    conn.execute(
        "INSERT INTO episodes (episode_id, opened_at, status)"
        " VALUES (?, ?, ?)",
        ("ep-42", "2026-10-25T00:00:00Z", "verified"),
    )
    conn.commit()
    conn.close()


def test_migration_backfills_unrecorded_and_portable(store):
    # columns exist and their defaults are honest
    conn = db.connect(store)
    cols = conn.execute("PRAGMA table_info(lessons)").fetchall()
    conn.close()
    by_name = {c[1]: c for c in cols}
    assert by_name["producer_profile"][3] == 1  # notnull
    assert by_name["model_specific"][3] == 1
    # PRAGMA dflt_value is the schema text: quotes included
    assert by_name["producer_profile"][4].strip("'") == "unrecorded"
    assert by_name["model_specific"][4].strip("'") == "0"


def test_promote_stamps_producer_columns(store):
    _seed_verified_episode(store)
    lesson, status = lessons.promote_lesson(
        "ep-42", "always verify the cache key",
        verification={"tests_passed": True},
        producer_profile="qwen3.8-flash-next@strata-r020",
        db_path=store,
    )
    assert status == "ok", status
    assert lesson["producer_profile"] == "qwen3.8-flash-next@strata-r020"
    assert lesson["model_specific"] == 0


def test_promote_model_specific_flag_roundtrips(store):
    _seed_verified_episode(store)
    lesson, status = lessons.promote_lesson(
        "ep-42", "re-issue the thinking block each turn for this model",
        verification={"tests_passed": True},
        producer_profile="qwen3.8-flash-next@strata-r020",
        model_specific=1,
        db_path=store,
    )
    assert status == "ok"
    assert lesson["model_specific"] == 1


def test_promote_without_producer_defaults_unrecorded(store):
    import minder_memory.lessons as _l
    print("LESSON FILE:", _l.__file__)
    _seed_verified_episode(store)
    lesson, status = lessons.promote_lesson(
        "ep-42", "a lesson from an unrecorded era",
        verification={"tests_passed": True}, db_path=store,
    )
    assert status == "ok"
    assert lesson["producer_profile"] == "unrecorded"
    assert lesson["model_specific"] == 0


def test_columns_survive_invalidation_lifecycle(store):
    """Minder's lesson lifecycle invalidates by UPDATE (valid_to) — the
    producer columns must survive that round trip."""
    _seed_verified_episode(store)
    lesson, status = lessons.promote_lesson(
        "ep-42", "keep", verification={"tests_passed": True},
        producer_profile="qwen@r020", model_specific=1, db_path=store,
    )
    assert status == "ok"
    lessons.invalidate_lesson(lesson["lesson_id"], "engine replaced",
                              db_path=store)
    conn = db.connect(store)
    row = conn.execute(
        "SELECT producer_profile, model_specific, status FROM lessons"
        " WHERE lesson_id = ?",
        (lesson["lesson_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "qwen@r020"
    assert row[1] == 1
    assert row[2] == "invalidated"


def test_producer_filter_query_shape(store):
    """The retrieval filter issue #42 promises: portable lessons surface
    for any profile; model_specific only for their producer."""
    _seed_verified_episode(store)
    a, _ = lessons.promote_lesson(
        "ep-42", "portable", verification={"tests_passed": True},
        producer_profile="qwen@r020", db_path=store,
    )
    b, _ = lessons.promote_lesson(
        "ep-42", "model bound", verification={"tests_passed": True},
        producer_profile="qwen@r020", model_specific=1, db_path=store,
    )
    conn = db.connect(store)
    visible = [
        r[0]
        for r in conn.execute(
            "SELECT lesson_id FROM lessons WHERE model_specific = 0"
            " OR (model_specific = 1 AND producer_profile = ?)",
            ("deepseek@v3",),
        )
    ]
    conn.close()
    assert a["lesson_id"] in visible
    assert b["lesson_id"] not in visible
