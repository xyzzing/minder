"""Deterministic evaluators over a normalized DSH trace.

Pure functions: no I/O, no model calls, no clock. The same trace always
produces byte-identical findings in a stable order, which is what makes a
finding comparable across review runs and safe to key a regression case
on.

Severity is a statement about evidence, not a score. There are no
quality scores here on purpose: a 0.48 "evidence grounding" number would
imply a calibration this layer does not have, and the explainability rule
is "no score without a rationale". Counts, severities and cited events are
the honest output.

Evaluate reuse: `memory/canonicalise.py` defines "the same failure" and
`memory/success_guard.py` defines "the same result" for the live guard.
The offline evaluators consume those definitions rather than inventing
parallel ones, so a trace finding and a live advisory can never disagree
about what a repeat is.
"""
import hashlib

from memory import canonicalise as canon
from memory import success_guard
from .normalize import EDIT_TOOLS as _EDIT_TOOLS
from .normalize import READ_TOOLS as _READ_TOOLS
from .normalize import TEST_SIGNS as _TEST_SIGNS

SEVERITIES = ("info", "low", "medium", "high", "blocker")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

DEFAULT_CONFIG = {
    # duplicate_retry severity ladder (same failure, same action)
    "duplicate_medium": 2,
    "duplicate_high": 3,
    "duplicate_blocker": 5,
    # success_loop: same action + same normalized result
    "success_loop_n": 3,
    "success_loop_window_min": 30,
    # Reviewer fatigue is a named risk: a pathological session produced 13
    # distinct looping groups. Show the worst, say what was held back.
    "max_success_loop_findings": 10,
    # no_progress: consecutive actions with no new file / todo / plan change
    "no_progress_n": 10,
    # efficiency
    "max_tool_calls": 40,
    "repeat_command_threshold": 3,
    "max_repeat_findings": 5,
}


def config(**overrides):
    """Evaluator thresholds. Unknown keys are ignored so a stale config
    can never change an evaluator's meaning silently."""
    merged = dict(DEFAULT_CONFIG)
    for key, value in (overrides or {}).items():
        if key in merged and isinstance(value, (int, float)):
            merged[key] = value
    return merged


def _tool_matches(tool, names):
    low = str(tool or "").lower()
    return any(n in low for n in names)


def _finding(*, session_id, evaluator, rule_id, severity, seqs, excerpts,
             call_ids, message, suggested_fix):
    """One finding. `finding_id` is content-derived so re-evaluating the
    same trace, or converting the finding to a regression case, is
    idempotent."""
    seqs = [s for s in seqs if s is not None]
    key = "|".join(str(p) for p in (session_id, rule_id,
                                    ",".join(str(s) for s in sorted(seqs))))
    return {
        "finding_id": "trf_" + hashlib.sha1(key.encode()).hexdigest()[:12],
        "session_id": str(session_id or ""),
        "evaluator": evaluator,
        "rule_id": rule_id,
        "severity": severity if severity in _SEVERITY_RANK else "info",
        "evidence": {
            "ds_seqs": sorted(seqs),
            "excerpts": [str(e)[:160] for e in excerpts if e][:5],
            "call_ids": [str(c) for c in call_ids if c][:5],
        },
        "message": message,
        "suggested_fix": suggested_fix,
    }


def _sorted(findings):
    """Stable reading order: findings that cite events first (by earliest
    event), then the trace-wide notes that cite nothing, since those are
    summaries rather than observations about one place."""
    def key(finding):
        seqs = finding["evidence"]["ds_seqs"]
        return (0, seqs[0], finding["rule_id"]) if seqs else (
            1, 0, finding["rule_id"])
    return sorted(findings, key=key)


def eval_duplicate_retry(events, ctx):
    """The same failing action, unchanged, retried.

    Uses `canonicalise.unchanged_retry` (same failure key, same action,
    same content, same hypothesis) so "unchanged" means exactly what the
    live duplicate guard means by it.
    """
    cfg = ctx["config"]
    session = ctx["session_id"]
    repo = ctx["repo"]
    groups = {}
    prev_by_key = {}
    for event in events:
        if event.get("event_type") != "tool_failure":
            # A failure loop can be interrupted by a success; the live
            # guard's "unchanged" test is per consecutive pair, so a
            # success resets the comparison baseline for that key.
            if event.get("failure_key"):
                prev_by_key.pop(event["failure_key"], None)
            continue
        key = event.get("failure_key") or ""
        previous = prev_by_key.get(key)
        prev_by_key[key] = event
        if previous is None or not canon.unchanged_retry(previous, event,
                                                        repo):
            continue
        group = groups.setdefault(key, {"events": [previous]})
        group["events"].append(event)

    findings = []
    for key, group in sorted(groups.items()):
        count = len(group["events"])
        severity = "medium"
        if count >= cfg["duplicate_blocker"]:
            severity = "blocker"
        elif count >= cfg["duplicate_high"]:
            severity = "high"
        elif count < cfg["duplicate_medium"]:
            continue
        tool = group["events"][0].get("tool") or "?"
        findings.append(_finding(
            session_id=session, evaluator="duplicate_retry",
            rule_id="dup-unchanged-retry", severity=severity,
            seqs=[e.get("ds_seq") for e in group["events"]],
            excerpts=[e.get("excerpt") for e in group["events"]],
            call_ids=[e.get("ds_call_id") for e in group["events"]],
            message=(f"{tool} failed {count} times with an unchanged "
                     f"failure ({key}) and no change between attempts."),
            suggested_fix=("Stop retrying this action. Change the "
                           "hypothesis, the input, or the tool before the "
                           "next attempt.")))
    return findings


def eval_success_loop(events, ctx):
    """The same action producing the same (volatile-normalized) result
    repeatedly: successful, but not advancing the task.

    This is the offline twin of the live advisory guard, and it is the
    evaluator that catches the real incident (78 identical curl calls whose
    outputs differ only in progress numbers). It uses the guard's own
    `result_signature`, so a finding here and an advisory there agree.
    """
    cfg = ctx["config"]
    session = ctx["session_id"]
    threshold = cfg["success_loop_n"]
    window_ms = cfg["success_loop_window_min"] * 60 * 1000
    groups = {}
    for event in events:
        if event.get("event_type") != "tool_success":
            continue
        fingerprint = event.get("action_fingerprint") or ""
        if not fingerprint:
            continue
        signature = success_guard.result_signature(
            event.get("exit_code") or 0, event.get("error_excerpt") or "")
        groups.setdefault((fingerprint, signature), []).append(event)

    findings = []
    suppressed = 0
    cap = cfg["max_success_loop_findings"]
    for (_fp, _sig), group in sorted(
            groups.items(), key=lambda kv: -len(kv[1])):
        if len(group) < threshold:
            continue
        # Within-window, newest first, so a session-long real loop counts
        # its whole run while sparse coincidences do not.
        group = sorted(group, key=lambda e: e.get("ts") or 0)
        newest = [g for g in group if g.get("ts")]
        windowed = group
        if len(newest) == len(group):
            cutoff = group[-1]["ts"] - window_ms
            windowed = [g for g in group if g["ts"] >= cutoff]
        if len(windowed) < threshold:
            continue
        if len(findings) >= cap:
            suppressed += 1
            continue
        tool = windowed[0].get("tool") or "?"
        command = windowed[0].get("command") or ""
        findings.append(_finding(
            session_id=session, evaluator="success_loop",
            rule_id="success-loop-same-result",
            severity="medium",
            seqs=[e.get("ds_seq") for e in windowed],
            excerpts=[e.get("result_excerpt") or e.get("excerpt")
                      for e in windowed],
            call_ids=[e.get("ds_call_id") for e in windowed],
            message=(f"{tool} produced the same result "
                     f"{len(windowed)} times in "
                     f"{cfg['success_loop_window_min']} min with no new "
                     f"evidence ({command[:80]})."),
            suggested_fix=("Verify the goal was actually reached, or "
                           "change the approach: this action is repeating "
                           "without advancing the task.")))
    if suppressed:
        findings.append(_finding(
            session_id=session, evaluator="success_loop",
            rule_id="success-loop-suppressed", severity="info",
            seqs=[], excerpts=[], call_ids=[],
            message=(f"{suppressed} further looping action(s) were not "
                     f"listed individually; this session loops in many "
                     "places at once."),
            suggested_fix=("Treat the run as a systemic planning failure "
                           "rather than reviewing each loop separately.")))
    return findings


def eval_evidence_gap(events, ctx):
    """A file was edited without being read first."""
    session = ctx["session_id"]
    read_paths = set()
    findings = []
    for event in events:
        path = str(event.get("file_path") or "")
        tool = event.get("tool")
        if path and _tool_matches(tool, _READ_TOOLS):
            read_paths.add(path)
        if (path and _tool_matches(tool, _EDIT_TOOLS)
                and path not in read_paths):
            findings.append(_finding(
                session_id=session, evaluator="evidence_gap",
                rule_id="edit-without-read", severity="medium",
                seqs=[event.get("ds_seq")], excerpts=[path],
                call_ids=[event.get("ds_call_id")],
                message=(f"{tool} wrote {path} without reading it earlier "
                         "in the session."),
                suggested_fix=("Read the file before modifying it, or "
                               "state why the current content is already "
                               "known.")))
    return findings


def eval_unverified_change(events, ctx):
    """A file was edited and no test-shaped command ran afterwards.

    Deliberately a `low`, and the message says it is a heuristic: not
    every edit needs a test, so this is a prompt to check, not a verdict.
    """
    session = ctx["session_id"]
    findings = []
    for index, event in enumerate(events):
        if not _tool_matches(event.get("tool"), _EDIT_TOOLS):
            continue
        path = str(event.get("file_path") or "")
        if not path:
            continue
        later = [e.get("command") or "" for e in events[index + 1:]]
        verified = any(
            any(sign in cmd.lower() for sign in _TEST_SIGNS)
            or (path and path in cmd)
            for cmd in later)
        if verified:
            continue
        findings.append(_finding(
            session_id=session, evaluator="unverified_change",
            rule_id="edit-without-test", severity="low",
            seqs=[event.get("ds_seq")], excerpts=[path],
            call_ids=[event.get("ds_call_id")],
            message=(f"{event.get('tool')} changed {path} and no test "
                     "command ran afterwards (heuristic)."),
            suggested_fix=("Run the relevant test or verification "
                           "command, or record why none applies.")))
    return findings


def eval_efficiency(events, ctx):
    """Call volume and verbatim command repetition."""
    cfg = ctx["config"]
    session = ctx["session_id"]
    commands = {}
    for event in events:
        command = str(event.get("command") or "").strip()
        if command:
            commands.setdefault(command, []).append(event)
    findings = []
    repeats = [(cmd, group) for cmd, group in commands.items()
               if len(group) >= cfg["repeat_command_threshold"]]
    repeats.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    for command, group in repeats[:cfg["max_repeat_findings"]]:
        findings.append(_finding(
            session_id=session, evaluator="efficiency",
            rule_id="repeated-identical-command", severity="info",
            seqs=[e.get("ds_seq") for e in group],
            excerpts=[command], call_ids=[e.get("ds_call_id")
                                          for e in group],
            message=(f"The identical command ran {len(group)} times: "
                     f"{command[:90]}"),
            suggested_fix=("Reuse the earlier result, or say what changed "
                           "to justify running it again.")))
    total = len(events)
    if total > cfg["max_tool_calls"]:
        findings.append(_finding(
            session_id=session, evaluator="efficiency",
            rule_id="tool-call-budget", severity="low",
            seqs=[events[-1].get("ds_seq")] if events else [],
            excerpts=[], call_ids=[],
            message=(f"The session made {total} tool calls, above the "
                     f"{cfg['max_tool_calls']}-call budget. Count alone is "
                     "not a defect; it is a prompt to check for waste."),
            suggested_fix=("Review the trace for redundant or overlapping "
                           "calls before the next run.")))
    return findings


def eval_workflow(events, ctx):
    """Required-tool omissions, when the task context states them.

    Silent without `ctx["required_tools"]` / `ctx["forbidden_tools"]` — a
    workflow rule that was never declared cannot be violated, and inventing
    requirements from the trace would be exactly the ungrounded judgement
    this product exists to avoid.
    """
    session = ctx["session_id"]
    used = {str((e.get("tool") or "")).lower() for e in events}
    findings = []
    for tool in ctx.get("required_tools") or ():
        if not any(tool.lower() in u for u in used):
            findings.append(_finding(
                session_id=session, evaluator="workflow",
                rule_id="required-tool-missing", severity="medium",
                seqs=[], excerpts=[tool], call_ids=[],
                message=(f"The rubric requires {tool}, which was never "
                         "called in this session."),
                suggested_fix=(f"Ensure {tool} runs for this task class, "
                               "or record why it was not applicable.")))
    for tool in ctx.get("forbidden_tools") or ():
        # Match the command as well as the tool name: a shell call that
        # reaches a forbidden source is exactly what these rules are for,
        # and "forbidden tool" alone would miss it.
        needle = tool.lower()
        hits = [e for e in events
                if needle in str(e.get("tool") or "").lower()
                or needle in str(e.get("command") or "").lower()]
        if hits:
            findings.append(_finding(
                session_id=session, evaluator="workflow",
                rule_id="forbidden-tool-used", severity="high",
                seqs=[e.get("ds_seq") for e in hits],
                excerpts=[tool], call_ids=[e.get("ds_call_id")
                                           for e in hits],
                message=(f"The rubric forbids {tool}, which was called "
                         f"{len(hits)} time(s)."),
                suggested_fix=("Remove the call and use an allowed tool, "
                               "or amend the rubric if it is wrong.")))
    return findings


def eval_policy_fidelity(events, ctx):
    """Was Minder's own governance actually in the path?

    A session with failures and zero Minder hook decisions means the
    watchdog was not wired for it — the trace cannot tell us anything
    about policy compliance, and saying nothing would imply it did.
    """
    session = ctx["session_id"]
    report = ctx.get("report") or {}
    findings = []
    failures = int(report.get("failures") or 0)
    hook_results = int(report.get("hook_results") or 0)
    if failures and not hook_results:
        findings.append(_finding(
            session_id=session, evaluator="policy_fidelity",
            rule_id="minder-not-wired", severity="info",
            seqs=[e.get("ds_seq") for e in events
                  if e.get("event_type") == "tool_failure"][:5],
            excerpts=[], call_ids=[],
            message=(f"{failures} tool failure(s) with no Minder hook "
                     "decision in the trace: governance was not in the "
                     "path for this session."),
            suggested_fix=("Install the Minder hooks for this workspace "
                           "(dsh_install profile-apply) if this run should "
                           "have been governed.")))
    if int(report.get("hook_blocks") or 0):
        findings.append(_finding(
            session_id=session, evaluator="policy_fidelity",
            rule_id="minder-intervened", severity="info",
            seqs=[], excerpts=[], call_ids=[],
            message=(f"Minder blocked {report['hook_blocks']} action(s) in "
                     "this session."),
            suggested_fix=("Confirm the escalation was appropriate; a "
                           "block that the agent then worked around is a "
                           "finding in itself.")))
    return findings


def eval_no_progress(events, ctx):
    """Consecutive actions that touched nothing new.

    "Progress" is deliberately concrete and checkable: a file read that
    had not been read before, any write, a todo-list change, a plan-mode
    transition, or an approval. DSH already emits todo/plan/approval
    records, so this needs no ledger of its own — and the agent cannot
    claim progress in prose, because prose is not one of the signals.
    """
    cfg = ctx["config"]
    session = ctx["session_id"]
    threshold = cfg["no_progress_n"]
    markers = {m["seq"]: m for m in (ctx.get("timeline") or [])
               if m.get("seq") is not None}
    items = [("tool", e.get("ds_seq"), e) for e in events]
    items += [("mark", seq, marker) for seq, marker in markers.items()]
    items.sort(key=lambda i: (i[1] if i[1] is not None else 0))

    seen_paths = set()
    seen_todo = None
    run_start = None
    last_seq = None
    findings = []
    streak = 0
    for kind, seq, payload in items:
        progressed = False
        if kind == "mark":
            if payload.get("kind") == "todo":
                if payload.get("detail") != seen_todo:
                    seen_todo = payload.get("detail")
                    progressed = True
            else:
                progressed = True
        else:
            path = str(payload.get("file_path") or "")
            if payload.get("event_type") == "tool_failure":
                progressed = True  # a failure is new information
            elif _tool_matches(payload.get("tool"), _EDIT_TOOLS):
                progressed = True
            elif path and path not in seen_paths:
                seen_paths.add(path)
                progressed = True
        if progressed:
            if streak >= threshold and run_start is not None:
                findings.append(_finding(
                    session_id=session, evaluator="no_progress",
                    rule_id="no-new-artifact", severity="medium",
                    seqs=[run_start, last_seq],
                    excerpts=[], call_ids=[],
                    message=(f"{streak} consecutive actions added no new "
                             "artifact, todo change, plan change or "
                             "approval."),
                    suggested_fix=("Re-plan from the objective: state what "
                                   "evidence is missing and change the "
                                   "decomposition or the data source.")))
            streak = 0
            run_start = None
        else:
            streak += 1
            if run_start is None:
                run_start = seq
            last_seq = seq
    if streak >= threshold and run_start is not None:
        findings.append(_finding(
            session_id=session, evaluator="no_progress",
            rule_id="no-new-artifact", severity="medium",
            seqs=[run_start, last_seq], excerpts=[], call_ids=[],
            message=(f"The session ended with {streak} consecutive actions "
                     "that added no new artifact, todo change, plan change "
                     "or approval."),
            suggested_fix=("Re-plan from the objective: state what evidence "
                           "is missing and change the decomposition or the "
                           "data source.")))
    return findings


EVALUATORS = (
    ("duplicate_retry", eval_duplicate_retry),
    ("success_loop", eval_success_loop),
    ("evidence_gap", eval_evidence_gap),
    ("unverified_change", eval_unverified_change),
    ("efficiency", eval_efficiency),
    ("workflow", eval_workflow),
    ("policy_fidelity", eval_policy_fidelity),
    ("no_progress", eval_no_progress),
)


def run_all(run, report=None, *, config_overrides=None, **ctx_extra):
    """Every evaluator over one normalized run. Never raises: a failing
    evaluator is reported, not propagated, and never blocks the others or
    the storage of the trace."""
    report = report if isinstance(report, dict) else {}
    ctx = {
        "session_id": (run.get("source") or {}).get("session_id") or "",
        "repo": (run.get("task") or {}).get("project_path") or "",
        "config": config(**(config_overrides or {})),
        "report": report,
        "timeline": run.get("timeline") or [],
    }
    ctx.update(ctx_extra)
    findings = []
    for name, evaluator in EVALUATORS:
        try:
            found = evaluator(run.get("events") or [], ctx)
        except Exception as exc:  # noqa: BLE001 — one bad evaluator must
            # not cost the reviewer the whole trace
            found = [{
                "finding_id": "trf_err_" + hashlib.sha1(
                    f"{ctx['session_id']}|{name}".encode()
                ).hexdigest()[:8],
                "session_id": ctx["session_id"], "evaluator": name,
                "rule_id": "evaluator-error", "severity": "info",
                "evidence": {"ds_seqs": [], "excerpts": [], "call_ids": []},
                "message": f"{name} could not evaluate this trace "
                           f"({type(exc).__name__}).",
                "suggested_fix": "Report this; the trace is still storage "
                                 "and reviewable.",
            }]
        findings.extend(found)
    return _sorted(findings)


def summarize(findings, run=None, report=None):
    """Counts a reviewer (and the CLI) reads first. No scores: see the
    module docstring."""
    findings = findings or ()
    by_severity = {s: 0 for s in SEVERITIES}
    by_evaluator = {}
    for finding in findings:
        severity = finding.get("severity") or "info"
        by_severity[severity] = by_severity.get(severity, 0) + 1
        evaluator = finding.get("evaluator") or "?"
        by_evaluator[evaluator] = by_evaluator.get(evaluator, 0) + 1
    report = report if isinstance(report, dict) else {}
    run = run if isinstance(run, dict) else {}
    execution = run.get("execution") or {}
    highest = "none"
    for severity in reversed(SEVERITIES):
        if by_severity.get(severity):
            highest = severity
            break
    return {
        "findings": len(findings),
        "by_severity": by_severity,
        "by_evaluator": by_evaluator,
        "highest_severity": highest,
        "tool_calls": report.get("tool_calls",
                                 execution.get("tool_calls")),
        "failures": report.get("failures"),
        "turns": report.get("turns"),
        "duration_ms": execution.get("duration_ms"),
        "tokens_total": execution.get("tokens_total"),
        "evidence_links": sum(len(f.get("evidence", {}).get("ds_seqs")
                                 or []) for f in findings),
    }


def has_blocking(findings):
    """Whether any finding is severe enough to gate a promotion."""
    return any((f.get("severity") or "info") in ("high", "blocker")
               for f in findings or ())
