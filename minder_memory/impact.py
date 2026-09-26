"""Change-impact verification suggestions
(docs/minder-phase-2-3-frontier-coding.md P3.4). Pure lookups over the
graph: which tests exercised the changed files, which verified lessons are
at risk. Suggestions only — nothing runs and nothing is invalidated here.
"""
from . import graph
from . import db as _db


def suggest_verification(paths, repo, db_path=None, limit_tests=10,
                         limit_lessons=5):
    """{tests, lessons_at_risk, directive} — empty lists for unknown files,
    never an error."""
    tests, lessons_at_risk = [], []
    try:
        seen_tests, seen_lessons = set(), set()
        for path in paths or []:
            file_id = f"file:{path}"
            for sig in graph.neighbors(file_id, "AFFECTS", "in",
                                       db_path=db_path):
                if not sig["id"].startswith("sig:"):
                    continue
                for t in graph.neighbors(sig["id"], "FAILED_TEST",
                                         db_path=db_path):
                    test_id = t.get("properties", {}).get("test_id") or \
                        t["id"].removeprefix("test:")
                    if test_id not in seen_tests:
                        seen_tests.add(test_id)
                        tests.append(test_id)
                failure_key = sig.get("properties", {}).get("failure_key")
                if not failure_key:
                    continue
                conn = _db.connect(db_path)
                try:
                    rows = conn.execute(
                        "SELECT lesson_id FROM lessons WHERE repo = ?"
                        " AND failure_key = ? AND status = 'verified'"
                        " AND valid_to IS NULL",
                        (repo, failure_key)).fetchall()
                finally:
                    conn.close()
                for r in rows:
                    if r["lesson_id"] not in seen_lessons:
                        seen_lessons.add(r["lesson_id"])
                        lessons_at_risk.append(r["lesson_id"])
        tests = tests[:limit_tests]
        lessons_at_risk = lessons_at_risk[:limit_lessons]
        directive = ""
        if tests:
            directive = "Run: pytest " + " ".join(tests)
            if lessons_at_risk:
                directive += (". Revalidate lessons: "
                              + ", ".join(lessons_at_risk))
        elif lessons_at_risk:
            directive = ("Revalidate lessons: "
                         + ", ".join(lessons_at_risk))
        return {"tests": tests, "lessons_at_risk": lessons_at_risk,
                "directive": directive}
    except Exception:
        return {"tests": [], "lessons_at_risk": [], "directive": ""}
