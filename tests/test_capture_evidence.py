"""Capture evidence rules (issue #79): a tool event's outcome comes from
structured status - the harness exit marker, the response dict's status
flag, or the harness error envelope - before content words. A successful
read whose file content mentions an error class records a success, not a
phantom failure and not a skill gap.
"""
from minder_memory import db as mdb
from minder_memory import from_hook

READ_CONTENT = (
    '"""Shared HTTP and record helpers for source adapters."""\n'
    "import httpx\n"
    "\n"
    "class SourceError(Exception):\n"
    '    """Raised when a source adapter cannot parse a record."""\n'
    "\n"
    "USER_AGENT = 'jobhunter/1.0 (error: retry once)'\n"
)


def _base(tmp_resp, tool="read", command=None):
    ev = {"session_id": "s-cap", "hook_event_name": "PostToolUse",
          "tool_name": tool,
          "tool_response": tmp_resp, "repo": "/repo", "cwd": "/repo"}
    if command:
        ev["tool_input"] = {"command": command}
    else:
        ev["tool_input"] = {"file_path": "/repo/jobhunter/sources/base.py"}
    return ev


def _gap_rows(dbp):
    conn = mdb.connect(dbp)
    try:
        return [dict(r) for r in
                conn.execute("SELECT * FROM skill_gaps").fetchall()]
    finally:
        conn.close()


def _event_row(dbp, event_id):
    conn = mdb.connect(dbp)
    try:
        return dict(conn.execute("SELECT * FROM events WHERE event_id = ?",
                                 (event_id,)).fetchone())
    finally:
        conn.close()


def test_read_content_mentioning_errors_is_a_success(tmp_path):
    dbp = tmp_path / "m.sqlite"
    out = from_hook.record(_base(READ_CONTENT), db_path=dbp)
    assert out["recorded"] is False and out["event_id"] is None
    assert _gap_rows(dbp) == []


def test_exit_zero_marker_wins_over_content_words(tmp_path):
    dbp = tmp_path / "m.sqlite"
    resp = READ_CONTENT + "\n[status: completed, exit code: 0]"
    out = from_hook.record(_base(resp, tool="Bash",
                                 command="grep -rn SourceError ."),
                           db_path=dbp)
    assert out["recorded"] is False and out["event_id"] is None
    assert _gap_rows(dbp) == []


def test_nonzero_marker_is_a_failure_with_marker_evidence(tmp_path):
    dbp = tmp_path / "m.sqlite"
    resp = "no pdf yet\n[status: completed, exit code: 2]"
    out = from_hook.record(_base(resp, tool="Bash", command="./fetch"),
                           db_path=dbp)
    assert out["recorded"] is True and out["event_id"]
    row = _event_row(dbp, out["event_id"])
    assert row["event_type"] == "tool_failure"
    assert row["evidence"] == "status-marker"


def test_error_envelope_is_a_failure_with_envelope_evidence(tmp_path):
    dbp = tmp_path / "m.sqlite"
    resp = ('Error: cannot edit "/repo/web/app.py": file changed since '
            'it was read - re-read the file')
    ev = _base(resp, tool="edit", command=None)
    ev["tool_input"] = {"file_path": "/repo/web/app.py"}
    out = from_hook.record(ev, db_path=dbp)
    assert out["recorded"] is True and out["event_id"]
    row = _event_row(dbp, out["event_id"])
    assert row["event_type"] == "tool_failure"
    assert row["evidence"] == "error-envelope"


def test_repeated_envelope_failure_records_gap_with_evidence(tmp_path):
    dbp = tmp_path / "m.sqlite"
    resp = ('Error: cannot edit "/repo/web/app.py": file changed since '
            'it was read - re-read the file')
    ev = _base(resp, tool="edit", command=None)
    ev["tool_input"] = {"file_path": "/repo/web/app.py"}
    for _ in range(2):  # REPEAT_THRESHOLD: second repeat records the gap
        out = from_hook.record(ev, db_path=dbp)
    assert out["recorded"] is True
    gaps = _gap_rows(dbp)
    assert len(gaps) == 1
    assert gaps[0]["evidence"] == "error-envelope"


def test_zcode_dict_status_flag_decides(tmp_path):
    dbp = tmp_path / "m.sqlite"
    bad = _base({"content": "KeyError: 'supplier_id'", "is_error": True})
    out = from_hook.record(bad, db_path=dbp)
    assert out["recorded"] is True
    assert _event_row(dbp, out["event_id"])["evidence"] == "status-flag"
    dbp2 = tmp_path / "m2.sqlite"
    good = _base({"content": READ_CONTENT, "is_error": False})
    out = from_hook.record(good, db_path=dbp2)
    assert out["recorded"] is False and _gap_rows(dbp2) == []
