"""gaps retriage (issue #79 acceptance 3): open gaps whose excerpt
carries no structured failure signal - content words only, or a zero
exit marker - close as capture artifacts; envelope, traceback, and
nonzero-marker excerpts stay open."""
from minder_memory import db as _db
from minder_op.cli import EXIT_OK, EXIT_USAGE, main

REPO = "/repo"

CONTENT_ONLY = ("<path>/repo/jobhunter/sources/base.py</path>\n"
                '"""Shared HTTP helpers."""\n'
                "class SourceError(Exception):\n"
                "    USER_AGENT = 'jobhunter/1.0 (error: retry once)'\n")
ENVELOPE = ('Error: cannot edit "/repo/web/app.py": file changed since '
            'it was read - re-read the file')
TRACEBACK = ("[stderr]\nTraceback (most recent call last):\n"
             '  File "x.py", line 3, in <module>\n'
             "ValueError: yerr\n")
MARKER_ZERO = ("reading a file whose own text says error: a lot\n"
               "[status: completed, exit code: 0]")
MARKER_TWO = ("no pdf yet\n[status: completed, exit code: 2]")


def seed_gap(dbp, gap_id, excerpt):
    conn = _db.connect(dbp)
    try:
        _db.write(conn, "INSERT INTO skill_gaps (gap_id, ts, repo,"
                  " failure_key, gap_type, sample_error, status)"
                  " VALUES (?, '2026-09-22T00:00:00', ?,"
                  " 'read|unknown|none|none', 'procedural', ?, 'open')",
                  (gap_id, REPO, excerpt))
    finally:
        conn.close()
    return gap_id


def _gap_row(dbp, gap_id):
    conn = _db.connect(dbp)
    try:
        return dict(conn.execute(
            "SELECT * FROM skill_gaps WHERE gap_id = ?",
            (gap_id,)).fetchone())
    finally:
        conn.close()


def _seed_all(dbp):
    _db.connect(dbp).close()  # materialize schema (migration 019)
    return {
        "content": seed_gap(dbp, "gap_cap_content", CONTENT_ONLY),
        "envelope": seed_gap(dbp, "gap_cap_env", ENVELOPE),
        "traceback": seed_gap(dbp, "gap_cap_tb", TRACEBACK),
        "marker0": seed_gap(dbp, "gap_cap_m0", MARKER_ZERO),
        "marker2": seed_gap(dbp, "gap_cap_m2", MARKER_TWO),
    }


def test_retriage_closes_content_only_and_zero_marker_only(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed_all(dbp)
    assert main(["--db", str(dbp), "gaps", "retriage", "--yes"]) == EXIT_OK
    assert _gap_row(dbp, "gap_cap_content")["status"] == "closed"
    assert _gap_row(dbp, "gap_cap_content")["close_reason"] == \
        "capture-artifact"
    assert _gap_row(dbp, "gap_cap_m0")["status"] == "closed"
    for kept in ("gap_cap_env", "gap_cap_tb", "gap_cap_m2"):
        assert _gap_row(dbp, kept)["status"] == "open"


def test_retriage_dry_run_closes_nothing(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed_all(dbp)
    assert main(["--db", str(dbp), "gaps", "retriage",
                 "--dry-run"]) == EXIT_OK
    for gap_id in ("gap_cap_content", "gap_cap_m0", "gap_cap_env",
                   "gap_cap_tb", "gap_cap_m2"):
        assert _gap_row(dbp, gap_id)["status"] == "open"


def test_retriage_without_yes_is_refused(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed_all(dbp)
    assert main(["--db", str(dbp), "gaps", "retriage"]) == EXIT_USAGE
    assert _gap_row(dbp, "gap_cap_content")["status"] == "open"


def test_retriage_is_idempotent(tmp_path):
    dbp = tmp_path / "m.sqlite"
    _seed_all(dbp)
    assert main(["--db", str(dbp), "gaps", "retriage", "--yes"]) == EXIT_OK
    assert main(["--db", str(dbp), "gaps", "retriage", "--yes"]) == EXIT_OK
    # the already-closed rows are not re-counted as closed
    conn = _db.connect(dbp)
    try:
        n = conn.execute("SELECT COUNT(*) FROM skill_gaps"
                         " WHERE status = 'closed'").fetchone()[0]
    finally:
        conn.close()
    assert n == 2
