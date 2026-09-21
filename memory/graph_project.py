"""Episode/lesson → graph projection (docs/minder-phase-2-3-frontier-coding.md
P3.2). Called additively (try/except, best-effort) from lessons.promote_lesson
and store.close_episode. Unknown entities are skipped — no invented paths.
"""
import json

from . import graph


def _sig_id(failure_key):
    return f"sig:{failure_key}"


def _parse_key(failure_key):
    """failure_key segments → (family, relpath|None); 'none' → None."""
    parts = (failure_key or "").split("|")
    family = parts[1] if len(parts) > 1 else "unknown"
    relpath = parts[3] if len(parts) > 3 and parts[3] != "none" else None
    return family, relpath


def project_episode_close(episode_id, failure_keys, db_path=None):
    """Episode --HAS_FAILURE--> FailureSignature for each distinct key."""
    try:
        graph.upsert_node("Episode", episode_id, {}, db_path=db_path)
        for key in set(failure_keys or []):
            if not key:
                continue
            graph.upsert_node("FailureSignature", _sig_id(key),
                              {"failure_key": key}, db_path=db_path)
            graph.link(episode_id, "HAS_FAILURE", _sig_id(key),
                       db_path=db_path)
        return True
    except Exception:
        return False


def project_lesson_promotion(lesson, db_path=None):
    """Full P3.2 projection for one promoted lesson. Best-effort: an error
    anywhere leaves earlier edges in place and returns False."""
    try:
        lesson_id = lesson["lesson_id"]
        failure_key = lesson.get("failure_key") or ""
        family, relpath = _parse_key(failure_key)
        graph.upsert_node("Lesson", lesson_id, {
            "repo": lesson.get("repo"), "failure_key": failure_key,
            "status": lesson.get("status")}, db_path=db_path)

        episode_id = lesson.get("source_episode")
        if episode_id:
            graph.upsert_node("Episode", episode_id, {}, db_path=db_path)
            graph.link(lesson_id, "DERIVED_FROM", episode_id,
                       db_path=db_path)
            project_episode_close(episode_id, [failure_key], db_path=db_path)
            graph.link(episode_id, "HAS_FAILURE", _sig_id(failure_key),
                       db_path=db_path)

        sig = _sig_id(failure_key)
        graph.upsert_node("FailureSignature", sig,
                          {"failure_key": failure_key, "family": family},
                          db_path=db_path)

        if relpath:
            file_id = f"file:{relpath}"
            graph.upsert_node("File", file_id, {"path": relpath},
                              db_path=db_path)
            graph.link(sig, "AFFECTS", file_id, db_path=db_path)

        verification = {}
        try:
            verification = json.loads(lesson.get("verification_json") or "{}")
        except (ValueError, TypeError):
            verification = {}

        test_ids = [t for t in (verification.get("tests") or []) if t]
        run_id = None
        if test_ids or verification.get("tests_passed"):
            run_id = (f"run:{lesson_id}")
            graph.upsert_node("VerificationRun", run_id, {
                "tests_passed": bool(verification.get("tests_passed"))},
                db_path=db_path)
        for test in test_ids:
            test_node = f"test:{test}"
            graph.upsert_node("Test", test_node, {"test_id": test},
                              db_path=db_path)
            graph.link(sig, "FAILED_TEST", test_node, db_path=db_path)
            if run_id:
                graph.link(run_id, "RAN", test_node, db_path=db_path)

        patch_paths = [p for p in (verification.get("paths") or []) if p]
        if patch_paths and run_id:
            patch_id = f"patch:{lesson_id}"
            graph.upsert_node("Patch", patch_id, {"paths": patch_paths},
                              db_path=db_path)
            for path in patch_paths:
                file_id = f"file:{path}"
                graph.upsert_node("File", file_id, {"path": path},
                                  db_path=db_path)
                graph.link(patch_id, "MODIFIED", file_id, db_path=db_path)
            graph.link(patch_id, "VERIFIED_BY", run_id, db_path=db_path)

        commit = verification.get("commit") or lesson.get("repo_version")
        if commit:
            commit_id = f"commit:{commit}"
            graph.upsert_node("Commit", commit_id, {"commit": commit},
                              db_path=db_path)
            graph.link(lesson_id, "APPLIES_TO", commit_id,
                       properties={"commit": commit}, db_path=db_path)
        return True
    except Exception:
        return False
