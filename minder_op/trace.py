"""`minder-op trace` — offline review of completed DSH sessions.

Read-mostly, and read-only on DSH itself. The only writes are the review
row and the feedback row, both append-only, both gated: persistence needs
`MINDER_TRACE_REVIEW=on` and `--no-store` overrides it off, so the default
path is byte-inert (flag law).

Rubrics are JSON, not YAML: the runtime is stdlib-only and pulling in
PyYAML for a config file would break that invariant. A rubric is small —
a task scope, declared required/forbidden tools, and evaluator thresholds.
"""
import json
import os
import sys
from pathlib import Path

from minder_memory import trace_reviews
from minder_trace import evaluate, normalize
from . import format as fmt

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DB = 2            # matches the CLI law: missing/corrupt DB

DECLARED_RUBRIC_KEYS = ("rubric_id", "required_tools", "forbidden_tools",
                        "config", "applies_to")


def review_enabled():
    """Whether a review may be persisted. Default off = no writes."""
    return (os.environ.get("MINDER_TRACE_REVIEW") or "").strip().lower() \
        in ("on", "1", "true", "yes")


def load_rubric(path):
    """Load a JSON rubric. Returns `(rubric, error)`; a missing or broken
    rubric is an error the caller reports, never a silent default, because
    evaluating against the wrong standard is worse than not evaluating."""
    if not path:
        return {}, None
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as exc:
        return {}, f"cannot read rubric {path}: {exc}"
    except ValueError as exc:
        return {}, f"rubric {path} is not valid JSON: {exc}"
    if not isinstance(raw, dict):
        return {}, f"rubric {path} must be a JSON object"
    unknown = [k for k in raw if k not in DECLARED_RUBRIC_KEYS]
    if unknown:
        return {}, (f"rubric {path} has unknown key(s): "
                    f"{', '.join(sorted(unknown))} — refusing to guess "
                    "what they mean")
    return raw, None


def _review_one(session, rubric):
    """Load, normalize and evaluate one session. Returns
    `(run, report, findings, summary, error)`."""
    run, report = normalize.load_session(session)
    if report.get("status") == "not_found":
        return run, report, [], {}, " ".join(report.get("notes") or [])
    overrides = rubric.get("config") if isinstance(rubric.get("config"),
                                                   dict) else None
    findings = evaluate.run_all(
        run, report, config_overrides=overrides,
        required_tools=tuple(rubric.get("required_tools") or ()),
        forbidden_tools=tuple(rubric.get("forbidden_tools") or ()))
    summary = evaluate.summarize(findings, run, report)
    summary["run_id"] = run.get("run_id")
    summary["session_id"] = (run.get("source") or {}).get("session_id")
    return run, report, findings, summary, None


def _print_findings(findings, verdicts=None):
    verdicts = verdicts or {}
    if not findings:
        print("no findings")
        return
    fmt.table(
        [{"severity": f["severity"],
          "evaluator": f["evaluator"],
          "rule": f["rule_id"],
          "events": ",".join(str(s) for s in
                             f["evidence"]["ds_seqs"][:4]) or "-",
          "n": len(f["evidence"]["ds_seqs"]) or "-",
          "review": verdicts.get(f["finding_id"], ""),
          "finding": f["finding_id"],
          "message": fmt.safe(f["message"], 88)}
         for f in findings],
        [("severity", "severity"), ("evaluator", "evaluator"),
         ("rule", "rule"), ("events", "events"), ("n", "n"),
         ("review", "review"), ("finding", "finding"),
         ("message", "message")])


def _print_summary(summary, report):
    print()
    fmt.kv([
        ("findings", summary.get("findings")),
        ("highest severity", summary.get("highest_severity")),
        ("by severity", json.dumps(summary.get("by_severity") or {},
                                   sort_keys=True)),
        ("by evaluator", json.dumps(summary.get("by_evaluator") or {},
                                    sort_keys=True)),
        ("tool calls", summary.get("tool_calls")),
        ("failures", summary.get("failures")),
        ("turns", summary.get("turns")),
        ("tokens", summary.get("tokens_total")),
        ("cited events", summary.get("evidence_links")),
        ("trace status", report.get("status")),
    ])
    for note in report.get("notes") or ():
        print(f"note: {fmt.safe(note, 160)}")


# --- subcommands ----------------------------------------------------------

def cmd_ls(args, path):
    from minder_op import dsh_sessions
    sessions = dsh_sessions.session_dirs()
    if not sessions:
        print("(no dsh sessions found)")
        print(f"looked under {dsh_sessions.dsh_home()}")
        return EXIT_OK
    sessions = sorted(sessions, key=lambda s: s.get("mtime") or 0,
                      reverse=True)[:getattr(args, "limit", 25)]
    rows = []
    for entry in sessions:
        review = trace_reviews.latest_review(entry["session_id"],
                                            db_path=path)
        rows.append({
            "session": entry["session_id"],
            "project": fmt.safe(entry.get("project_dir"), 28),
            "format": entry.get("format") or "-",
            "kb": (int(entry["log_bytes"] // 1024)
                   if entry.get("log_bytes") else "-"),
            "reviewed": (review or {}).get("ts", "-")[:19],
            "findings": (str((review or {}).get("summary", {}).get(
                "findings")) if review else "-"),
        })
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2, sort_keys=True))
        return EXIT_OK
    fmt.table(rows, [("session", "session"), ("project", "project"),
                     ("format", "format"), ("kb", "kb"),
                     ("reviewed", "reviewed"), ("findings", "findings")])
    print()
    print(f"MINDER_TRACE_REVIEW={'on' if review_enabled() else 'off'}"
          " (off = `trace review` prints only; it stores nothing)")
    return EXIT_OK


def cmd_review(args, path):
    rubric, error = load_rubric(getattr(args, "rubric", None))
    if error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    run, report, findings, summary, error = _review_one(args.session,
                                                        rubric)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE

    review_id = None
    store_status = "not-stored"
    if not getattr(args, "no_store", False) and review_enabled():
        review_id, store_status = trace_reviews.store_review(
            run, findings, summary, report=report,
            rubric_id=rubric.get("rubric_id"), db_path=path)
        if review_id is None and store_status.startswith("degraded"):
            # An explicitly requested store that failed is exit 2 (the DB
            # law), not a silent success — the reviewer must know the
            # evidence did not land.
            print(f"error: could not store the review: {store_status}",
                  file=sys.stderr)
            print("(use --no-store to review without persisting)",
                  file=sys.stderr)
            return EXIT_DB

    if getattr(args, "json", False):
        print(json.dumps({
            "run_id": run.get("run_id"),
            "session_id": (run.get("source") or {}).get("session_id"),
            "report": report, "summary": summary, "findings": findings,
            "review_id": review_id, "store": store_status,
        }, indent=2, sort_keys=True))
        return EXIT_OK

    source = run.get("source") or {}
    print(f"session {source.get('session_id')}  "
          f"({source.get('trace_format') or '?'}, "
          f"{int((source.get('log_bytes') or 0) // 1024)} kB)")
    print(f"run     {run.get('run_id')}   task {run.get('task', {}).get('task_type')}"
          f"   project {fmt.safe(run.get('task', {}).get('project_path'), 60)}")
    if rubric.get("rubric_id"):
        print(f"rubric  {rubric['rubric_id']}")
    print()
    _print_findings(findings)
    _print_summary(summary, report)
    print()
    if review_id:
        print(f"stored review {review_id}")
    elif getattr(args, "no_store", False):
        print("read-only (--no-store): nothing written")
    else:
        print("read-only: set MINDER_TRACE_REVIEW=on to store this review")
    return EXIT_OK


def cmd_show(args, path):
    review = trace_reviews.get_review(args.review_id, db_path=path)
    if review is None:
        print(f"error: no review {args.review_id}", file=sys.stderr)
        return EXIT_USAGE
    findings = review.get("findings") or []
    verdicts = trace_reviews.finding_verdicts(args.review_id, db_path=path)
    feedback = trace_reviews.list_feedback(args.review_id, db_path=path)
    if getattr(args, "json", False):
        print(json.dumps({"review": review, "verdicts": verdicts,
                          "feedback": feedback},
                         indent=2, sort_keys=True))
        return EXIT_OK
    fmt.kv([
        ("review", review.get("review_id")),
        ("session", review.get("session_id")),
        ("run", review.get("run_id")),
        ("ts", review.get("ts")),
        ("status", review.get("status")),
        ("evaluator", review.get("evaluator_version")),
        ("rubric", review.get("rubric_id") or "-"),
        ("redaction", review.get("redaction_status")),
    ])
    print()
    _print_findings(findings, verdicts)
    _print_summary(review.get("summary") or {},
                   {"status": review.get("status")})
    print()
    if feedback:
        fmt.table([{"ts": f.get("ts", "")[:19], "level": f.get("level"),
                    "category": f.get("category"),
                    "target": f.get("target_ref") or "-",
                    "verdict": f.get("finding_verdict") or "-",
                    "comment": fmt.safe(f.get("comment"), 48)}
                   for f in feedback],
                  [("ts", "ts"), ("level", "level"),
                   ("category", "category"), ("target", "target"),
                   ("verdict", "verdict"), ("comment", "comment")])
    else:
        print("no feedback yet")
    return EXIT_OK


def cmd_feedback(args, path):
    review = trace_reviews.get_review(args.review_id, db_path=path)
    if review is None:
        print(f"error: no review {args.review_id}", file=sys.stderr)
        return EXIT_USAGE
    target = args.target_ref
    if args.finding:
        known = {f.get("finding_id") for f in (review.get("findings") or [])}
        if args.finding not in known:
            print(f"error: finding {args.finding} is not in review "
                  f"{args.review_id}", file=sys.stderr)
            return EXIT_USAGE
        target = args.finding
    if not getattr(args, "yes", False):
        print("dry-run (pass --yes to append this feedback):")
        print(json.dumps({
            "review_id": args.review_id, "level": args.level,
            "category": args.category, "target_ref": target,
            "finding_id": args.finding,
            "finding_verdict": getattr(args, "verdict", None),
            "comment": fmt.safe(args.comment, trace_reviews.COMMENT_CAP),
            "reviewer": args.reviewer,
        }, indent=2, sort_keys=True))
        return EXIT_OK
    feedback_id, status = trace_reviews.store_feedback(
        args.review_id, args.level, args.category, target_ref=target,
        finding_id=args.finding, finding_verdict=getattr(args, "verdict",
                                                        None),
        comment=args.comment, reviewer=args.reviewer, db_path=path)
    if feedback_id is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    print(f"recorded feedback {feedback_id} ({args.level}/"
          f"{args.category}) on review {args.review_id}")
    return EXIT_OK


def cmd_regress(args, path):
    """Slice 4: turn a confirmed failure into a benchmark regression case."""
    from . import trace_regress
    return trace_regress.run(args, path)
