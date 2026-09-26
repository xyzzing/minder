"""Deterministic evaluator tests (Slice 2).

Each evaluator is pinned by a fixture trace for the behaviour it claims,
plus a negative control: a clean session must produce ZERO findings.
Without that control an evaluator that fires on everything would look
like it was working.

The incident fixtures are the property that matters — three (and, in the
`success_loop` case, many) *successful* calls whose outputs differ only in
volatile progress numbers must collapse to one finding, which is exactly
the live DBS failure the guard was built for.
"""
import json


from trace import evaluate, normalize
from tracebuild import bash, call, hook, result

# Volatile-number-only differences: one signature (the incident's own
# property, taken verbatim from the live trace).
CURL_A = "  0 181.2k  0  0  1.24M --:--:-- 100"
CURL_B = "  0 181.2k  0  0  905.5k --:--:-- 100"
CURL_C = "  0 181.2k  0  0 573.6k --:--:-- 100"
CURL_CMD = 'curl -L -o dbs.pdf "https://www.dbs.com/sustainability/our"'


def _run(records, *, session_id="s1", repo="/repo", report=None, **ctx):
    run, rep = normalize.normalize(records, session_id=session_id, repo=repo)
    if report is not None:
        rep.update(report)
    return evaluate.run_all(run, rep, **ctx)


def _findings(findings, evaluator):
    return [f for f in findings if f["evaluator"] == evaluator]


def _only(findings, evaluator, rule_id=None):
    found = [f for f in _findings(findings, evaluator)
             if rule_id is None or f["rule_id"] == rule_id]
    assert found, [f["evaluator"] for f in findings]
    return found


def _loop_records(count, command=CURL_CMD,
                  payloads=(CURL_A, CURL_B, CURL_C)):
    records = []
    for i in range(count):
        records += bash(10 + i * 2, command, payloads[i % len(payloads)])
    return records


# --- the negative control -------------------------------------------------

def test_clean_session_produces_zero_findings():
    """A session that reads, edits and then verifies must be silent.
    Without this control a firing-on-everything evaluator looks correct."""
    records = [
        call(seq=1, call_id="c1", name="read",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=2, call_id="c1", text="old body"),
        call(seq=3, call_id="c2", name="edit",
             arguments={"file_path": "/repo/a.py", "old_string": "x",
                        "new_string": "y"}),
        result(seq=4, call_id="c2", text="patched cleanly"),
        *bash(5, "python3 -m pytest -q", "1 passed"),
    ]
    assert _run(records) == []


def test_findings_are_deterministic_and_stably_ordered():
    records = _loop_records(6)
    first = _run(records)
    second = _run(records)
    assert json.dumps(first, sort_keys=True) == json.dumps(second,
                                                           sort_keys=True)
    assert first == sorted(
        first, key=lambda f: (0, f["evidence"]["ds_seqs"][0],
                              f["rule_id"])
        if f["evidence"]["ds_seqs"] else (1, 0, f["rule_id"]))


# --- success_loop (the incident) -----------------------------------------

def test_success_loop_collapses_volatile_numbers_into_one_finding():
    findings = _run(_loop_records(3))
    found = _only(findings, "success_loop", "success-loop-same-result")
    assert len(found) == 1
    assert "3 times" in found[0]["message"]
    # every one of the repeats is cited, so the finding is checkable
    assert len(found[0]["evidence"]["ds_seqs"]) == 3


def test_success_loop_needs_repeats_not_merely_several_calls():
    """Two identical successful calls are an accident, not a loop."""
    assert _findings(_run(_loop_records(2)), "success_loop") == []


def test_success_loop_reports_the_real_incident_shape():
    """78 identical successful calls — the live DBS incident."""
    findings = _run(_loop_records(78), config_overrides={
        "max_success_loop_findings": 5})
    found = _only(findings, "success_loop", "success-loop-same-result")
    assert len(found) == 1
    assert "78 times" in found[0]["message"]


def test_genuinely_different_results_are_not_a_loop():
    records = []
    for i, text in enumerate(("first body", "second body", "third body")):
        records += bash(10 + i * 2, "cat a.py", text)
    assert _findings(_run(records), "success_loop") == []


def test_success_loop_cap_is_transparent_about_what_it_held_back():
    records = []
    seq = 10
    for group in range(4):
        for i in range(3):
            records += bash(seq, f"curl -o f{group}.pdf URL",
                            (CURL_A, CURL_B, CURL_C)[i])
            seq += 2
    findings = _run(records, config_overrides={
        "success_loop_n": 3, "max_success_loop_findings": 2})
    listed = _findings(findings, "success_loop")
    suppressed = [f for f in listed
                  if f["rule_id"] == "success-loop-suppressed"]
    assert len([f for f in listed
                if f["rule_id"] == "success-loop-same-result"]) == 2
    assert suppressed and "2 further" in suppressed[0]["message"]


# --- duplicate_retry -----------------------------------------------------

def _failing(count, text="ModuleNotFoundError: no module named 'x'",
             command="python3 -m pytest -q"):
    records = []
    for i in range(count):
        records += bash(10 + i * 2, command, text, exit_code=1)
    return records


def test_duplicate_retry_severity_ladder():
    """The second unchanged failure is already a retry; the fifth is a
    blocker. Deterministic, so a reviewer can calibrate against it."""
    assert _findings(_run(_failing(1)), "duplicate_retry") == []
    two = _only(_run(_failing(2)), "duplicate_retry")
    assert two[0]["severity"] == "medium"
    three = _only(_run(_failing(3)), "duplicate_retry")
    assert three[0]["severity"] == "high"
    five = _only(_run(_failing(5)), "duplicate_retry")
    assert five[0]["severity"] == "blocker"


def test_duplicate_retry_ignores_a_changed_action():
    """A changed command is progress, and must not be called a repeat."""
    records = []
    for i, command in enumerate(("pytest -q", "pytest -q tests/a.py",
                                 "pytest -q tests/b.py")):
        records += bash(10 + i * 2, command,
                        "ModuleNotFoundError: no module named 'x'",
                        exit_code=1)
    assert _findings(_run(records), "duplicate_retry") == []


def test_duplicate_retry_ignores_a_changed_failure():
    records = []
    for i, text in enumerate(("ModuleNotFoundError: no module named 'x'",
                              "AssertionError: 1 != 2",
                              "PermissionError: denied")):
        records += bash(10 + i * 2, "python3 -m pytest -q", text,
                        exit_code=1)
    assert _findings(_run(records), "duplicate_retry") == []


# --- evidence_gap / unverified_change ------------------------------------

def test_evidence_gap_flags_an_edit_without_a_read():
    records = [
        call(seq=1, call_id="c1", name="write",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=2, call_id="c1", text="written"),
    ]
    found = _only(_run(records), "evidence_gap", "edit-without-read")
    assert "/repo/a.py" in found[0]["message"]


def test_evidence_gap_accepts_read_then_edit():
    records = [
        call(seq=1, call_id="c1", name="read",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=2, call_id="c1", text="body"),
        call(seq=3, call_id="c2", name="edit",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=4, call_id="c2", text="ok"),
    ]
    assert _findings(_run(records), "evidence_gap") == []


def test_unverified_change_flags_an_edit_with_no_test_after_it():
    records = [
        call(seq=1, call_id="c1", name="read",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=2, call_id="c1", text="body"),
        call(seq=3, call_id="c2", name="edit",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=4, call_id="c2", text="ok"),
    ]
    found = _only(_run(records), "unverified_change", "edit-without-test")
    assert found[0]["severity"] == "low"  # a heuristic, and says so
    assert "heuristic" in found[0]["message"]


def test_unverified_change_clears_when_a_test_runs_later():
    records = [
        call(seq=1, call_id="c1", name="read",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=2, call_id="c1", text="body"),
        call(seq=3, call_id="c2", name="edit",
             arguments={"file_path": "/repo/a.py"}),
        result(seq=4, call_id="c2", text="ok"),
        *bash(5, "python3 -m pytest -q", "1 passed"),
    ]
    assert _findings(_run(records), "unverified_change") == []


# --- efficiency ----------------------------------------------------------

def test_efficiency_flags_repeated_identical_commands_as_info():
    records = []
    for i in range(3):
        records += bash(10 + i * 2, "git status", "clean")
    found = _only(_run(records), "efficiency", "repeated-identical-command")
    assert found[0]["severity"] == "info"
    assert "3 times" in found[0]["message"]


def test_efficiency_tool_budget_is_informational_not_a_verdict():
    records = []
    for i in range(6):
        records += bash(10 + i * 2, f"echo {i}", f"out {i}")
    found = _only(_run(records, config_overrides={"max_tool_calls": 5}),
                  "efficiency", "tool-call-budget")
    assert found[0]["severity"] == "low"
    assert "not a defect" in found[0]["message"]


# --- no_progress ---------------------------------------------------------

def test_no_progress_fires_on_a_streak_of_actions_that_touch_nothing_new():
    records = []
    for i in range(12):
        records += bash(10 + i * 2, f"echo step-{i}", f"out {i}")
    found = _only(_run(records), "no_progress", "no-new-artifact")
    assert "consecutive actions" in found[0]["message"]


def test_no_progress_resets_on_a_new_file_read():
    """Reading a file for the first time IS progress — that is the whole
    reason the evaluator keys on artifacts rather than on wording.

    The pre-read streak stays below the threshold, so only the reset can
    explain the absence of a finding."""
    records = []
    seq = 10
    for i in range(4):
        records += bash(seq, f"echo {i}", f"out {i}")
        seq += 2
    records += [call(seq=seq, call_id="cr", name="read",
                     arguments={"file_path": "/repo/new.py"}),
                result(seq=seq + 1, call_id="cr", text="body")]
    seq += 2
    for i in range(4):
        records += bash(seq, f"echo late-{i}", f"late {i}")
        seq += 2
    assert _findings(_run(records, config_overrides={"no_progress_n": 6}),
                     "no_progress") == []


def test_no_progress_resets_on_a_todo_change():
    records = []
    seq = 10
    for i in range(4):
        records += bash(seq, f"echo {i}", f"out {i}")
        seq += 2
    records.append({"type": "todo/write", "seq": seq, "time": seq * 1000,
                    "data": {"todos": [{"content": "new plan",
                                        "status": "pending"}]}})
    seq += 1
    for i in range(4):
        records += bash(seq, f"echo late-{i}", f"late {i}")
        seq += 2
    assert _findings(_run(records, config_overrides={"no_progress_n": 6}),
                     "no_progress") == []


def test_no_progress_resets_on_a_failure():
    """A failure is new information about the system, not idleness."""
    records = []
    seq = 10
    for i in range(4):
        records += bash(seq, f"echo {i}", f"out {i}")
        seq += 2
    records += bash(seq, "make", "boom", exit_code=2)
    seq += 2
    for i in range(4):
        records += bash(seq, f"echo after-{i}", f"after {i}")
        seq += 2
    assert _findings(_run(records, config_overrides={"no_progress_n": 6}),
                     "no_progress") == []


def test_no_progress_fires_without_a_reset_marker():
    """Control for the three reset tests: the same shape of trace, with
    the progress marker removed, must produce the finding."""
    records = []
    seq = 10
    for i in range(8):
        records += bash(seq, f"echo {i}", f"out {i}")
        seq += 2
    assert _only(_run(records, config_overrides={"no_progress_n": 6}),
                 "no_progress", "no-new-artifact")


# --- workflow (declared requirements only) -------------------------------

def test_workflow_is_silent_without_a_declared_rule():
    """No declared requirement means nothing can be violated; inventing
    one from the trace is the ungrounded judgement this avoids."""
    records = [*bash(10, "echo hi", "hi")]
    assert _findings(_run(records), "workflow") == []


def test_workflow_flags_a_missing_required_tool():
    records = [*bash(10, "echo hi", "hi")]
    found = _only(_run(records, required_tools=("web_search",)), "workflow",
                  "required-tool-missing")
    assert "web_search" in found[0]["message"]


def test_workflow_flags_a_forbidden_tool_as_high():
    records = [*bash(10, "curl https://x", "ok")]
    found = _only(_run(records, forbidden_tools=("curl",)), "workflow",
                  "forbidden-tool-used")
    assert found[0]["severity"] == "high"


# --- policy_fidelity ----------------------------------------------------

def test_policy_fidelity_notices_minder_was_not_in_the_path():
    findings = _run(_failing(2))
    found = _only(findings, "policy_fidelity", "minder-not-wired")
    assert found[0]["severity"] == "info"


def test_policy_fidelity_is_quiet_when_hooks_are_present():
    records = _failing(2) + [hook(50, decision="pass", exit_code=0)]
    findings = _run(records)
    assert [f for f in _findings(findings, "policy_fidelity")
            if f["rule_id"] == "minder-not-wired"] == []


# --- run_all robustness, summarize --------------------------------------

def test_one_broken_evaluator_does_not_cost_the_whole_review(monkeypatch):
    def explode(_events, _ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(evaluate, "EVALUATORS",
                        (("explode", explode),
                         ("efficiency", evaluate.eval_efficiency)))
    findings = _run(_loop_records(3))
    error = _only(findings, "explode", "evaluator-error")
    assert error[0]["severity"] == "info"
    # the healthy evaluator still ran
    assert _findings(findings, "efficiency")


def test_run_all_never_raises_on_a_hostile_run():
    for junk in ({}, {"events": None}, {"events": [None, 1, "x"]},
                 {"events": [{"tool": None}]}):
        assert isinstance(evaluate.run_all(junk), list)


def test_summarize_reports_counts_and_no_invented_score():
    records = _loop_records(3)
    run, report = normalize.normalize(records, session_id="s1",
                                      repo="/repo")
    findings = evaluate.run_all(run, report)
    summary = evaluate.summarize(findings, run, report)
    assert summary["findings"] == len(findings)
    assert summary["highest_severity"] in evaluate.SEVERITIES
    assert summary["evidence_links"] >= 3
    # explainability: counts and severities, never a bare 0-1 "score"
    assert not any(isinstance(v, float) for v in summary.values())


def test_has_blocking_gates_on_high_and_blocker_only():
    run, report = normalize.normalize(_failing(5), session_id="s1",
                                      repo="/repo")
    assert evaluate.has_blocking(evaluate.run_all(run, report)) is True
    clean_run, clean_report = normalize.normalize(_loop_records(3),
                                                  session_id="s1",
                                                  repo="/repo")
    assert evaluate.has_blocking(
        evaluate.run_all(clean_run, clean_report)) is False


def test_config_ignores_unknown_and_non_numeric_overrides():
    cfg = evaluate.config(no_progress_n=4, bogus=99, success_loop_n="lots")
    assert cfg["no_progress_n"] == 4
    assert "bogus" not in cfg
    assert cfg["success_loop_n"] == evaluate.DEFAULT_CONFIG["success_loop_n"]
