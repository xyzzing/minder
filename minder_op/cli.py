"""minder_op CLI (docs/minder-operator-8a-8b-from-v10.md).

Exit codes: 0 ok, 1 usage/not found, 2 DB missing or corrupt.
8A commands are strictly read-only; the 8B writes (lessons invalidate /
lessons promote / gaps close) require --yes and go through the existing
memory APIs only. The CLI can never change a Warden action: hook.py
consumes only guard["digest"], and flags are env/systemd owned.
"""
import argparse
import json
import os
import sys

from minder_op import format as fmt
from minder_op import queries

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DB = 2

# Kept visible where an operator will look: deferred out of 8A–8C by the
# spec (docs/operator-cli.md carries the sketches).
DEFERRED = ("deferred (8D/8E+): controlled local benchmark runner, web "
            "console, auth/multi-user, live Jev in CLI, DB-edited "
            "MINDER_ASSIST, graph visualiser, auto skill apply, log "
            "retention/prune, skill-risk denylist, 003-vs-"
            "frontier_evals label sync")

FLAG_VARS = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION")


class OpParser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; this CLI reserves 2 for a
    missing/corrupt DB, so usage problems are exit 1."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser():
    parser = OpParser(prog="minder-op", description=(
        "minder operator CLI — read-mostly view over the memory plane. "
        + DEFERRED))
    parser.add_argument("--db", help="memory sqlite path (default: "
                                     "$MINDER_MEMORY_DB or the standard "
                                     "state location)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="one screen: schema, counts, flags")
    sub.add_parser("flags", help="show MINDER_* decision flags (read-only")
    doc = sub.add_parser("doctor", help=(
        "one-shot install health report: db, schema, flags, hook "
        "wiring, event staleness, proxy, benchmarks (read-only)"))
    doc.add_argument("--no-probe", action="store_true",
                     help="skip the loopback proxy reachability probe")
    doc.add_argument("--json", action="store_true",
                     help="emit the check objects instead of text")

    ev = sub.add_parser("events", help="raw observed events")
    ev_sub = ev.add_subparsers(dest="subcommand", required=True)
    ev_ls = ev_sub.add_parser("ls")
    ev_ls.add_argument("--failure-key")
    ev_ls.add_argument("--type", dest="event_type")
    ev_ls.add_argument("--episode")
    ev_ls.add_argument("--limit", type=int, default=25)
    ev_show = ev_sub.add_parser("show")
    ev_show.add_argument("id")

    tk = sub.add_parser("task", help="task context intake (explicit "
                                     "declaration; Phase 1)")
    tk_sub = tk.add_subparsers(dest="subcommand", required=True)
    tk_dec = tk_sub.add_parser("declare")
    tk_dec.add_argument("--domain", required=True,
                        help="coding | trading_research | "
                             "resume_application | cited_research | mixed")
    tk_dec.add_argument("--task", default="default")
    tk_dec.add_argument("--subtask")
    tk_dec.add_argument("--actor", default="operator")
    tk_dec.add_argument("--note")
    tk_stat = tk_sub.add_parser("status")
    tk_stat.add_argument("--task", default="default")
    tk_close = tk_sub.add_parser("close")
    tk_close.add_argument("--task", default="default")
    tk_close.add_argument("--reason", default="closed")

    fam = sub.add_parser("families", help="trading research protocol "
                                          "(preregistration, manifests)")
    fam_sub = fam.add_subparsers(dest="subcommand", required=True)
    fam_reg = fam_sub.add_parser("register")
    fam_reg.add_argument("--family")
    fam_reg.add_argument("--method-digest", required=True)
    fam_reg.add_argument("--metric", required=True)
    fam_reg.add_argument("--splits", required=True)
    fam_reg.add_argument("--sources", required=True,
                         help="JSON: [{digest, as_of, source}]")
    fam_reg.add_argument("--expected-trials", type=int)
    fam_reg.add_argument("--universe")
    fam_trial = fam_sub.add_parser("trial")
    fam_trial.add_argument("--family", required=True)
    fam_trial.add_argument("--config-hash", required=True)
    fam_trial.add_argument("--vintage", required=True,
                           help="JSON: {digest, as_of}")
    fam_trial.add_argument("--split", required=True,
                           choices=["train", "validation", "holdout"])
    fam_trial.add_argument("--result", required=True,
                           help="JSON summary (redacted metrics)")
    fam_ana = fam_sub.add_parser("analysis")
    fam_ana.add_argument("--family", required=True)
    fam_ana.add_argument("--method", required=True,
                         choices=["PBO/CSCV", "DSR", "PSR", "SPA", "other"])
    fam_ana.add_argument("--code-digest", required=True)
    fam_ana.add_argument("--n", type=int)
    fam_ana.add_argument("--variance", type=float)
    fam_ana.add_argument("--verdict", help="JSON summary")
    fam_status = fam_sub.add_parser("status")
    fam_status.add_argument("family")
    fam_unlock = fam_sub.add_parser("holdout-unlock")
    fam_unlock.add_argument("family")
    fam_unlock.add_argument("--actor", required=True)
    fam_unlock.add_argument("--yes", action="store_true",
                            help="confirm: records the human decision")

    rt = sub.add_parser("routes", help="domain-route shadow proposals "
                                       "(observe-only; Phase 1)")
    rt_sub = rt.add_subparsers(dest="subcommand", required=True)
    rt_eval = rt_sub.add_parser("eval")
    rt_eval.add_argument("--declared-domain")
    rt_eval.add_argument("--task", default="default")
    rt_eval.add_argument("--session")
    rt_eval.add_argument("--subject-changed", action="store_true")
    rt_eval.add_argument("--provider", default="rules",
                         choices=["rules", "laya", "null"])
    rt_ls = rt_sub.add_parser("ls")
    rt_ls.add_argument("--limit", type=int, default=25)
    rt_rep = rt_sub.add_parser("replay", help="offline replay of routing "
                               "cases; emits an 8C-schema report")
    rt_rep.add_argument("--cases", required=True)
    rt_rep.add_argument("--provider", default="rules",
                        choices=["rules", "laya", "null"],
                        help="routing provider to replay (rules is the "
                             "pinned deterministic baseline)")
    rt_rep.add_argument("--out", help="persist the replay report JSON")
    rt_rep.add_argument("--json", action="store_true")
    rt_amb = rt_sub.add_parser("ambiguity", help="read-only ambiguity-"
                               "rate proxy over local evidence")
    rt_amb.add_argument("--days", type=int, default=30)

    rs = sub.add_parser("resume", help="résumé evidence: career facts, "
                                       "approved wordings, JD intents")
    rs_sub = rs.add_subparsers(dest="subcommand", required=True)
    rs_assert = rs_sub.add_parser("assert")
    rs_assert.add_argument("--claim", required=True)
    rs_assert.add_argument("--variant", action="append", default=[])
    rs_assert.add_argument("--actor", default="user")
    rs_appr = rs_sub.add_parser("approve")
    rs_appr.add_argument("--assertion", required=True)
    rs_appr.add_argument("--phrase", required=True)
    rs_appr.add_argument("--scope", default="any",
                         help="'any' or a jd_id")
    rs_appr.add_argument("--actor", default="user")
    rs_unc = rs_sub.add_parser("uncertain")
    rs_unc.add_argument("--assertion", required=True)
    rs_unc.add_argument("--actor", default="user")
    rs_int = rs_sub.add_parser("intent")
    rs_int.add_argument("--jd", required=True)
    rs_int.add_argument("--jd-digest", required=True)
    rs_int.add_argument("--assertion", required=True)
    rs_int.add_argument("--phrase", required=True)
    rs_int.add_argument("--actor", default="user")
    rs_int.add_argument("--retention", type=int,
                        help="days; default 90 (env "
                             "MINDER_RESUME_RETENTION_DAYS)")
    rs_draft = rs_sub.add_parser("draft")
    rs_draft.add_argument("--draft", required=True)
    rs_draft.add_argument("--assertion", required=True)
    rs_draft.add_argument("--phrase", required=True)
    rs_draft.add_argument("--jd")
    rs_imp = rs_sub.add_parser("impact")
    rs_imp.add_argument("assertion")
    rs_corr = rs_sub.add_parser("correct")
    rs_corr.add_argument("assertion")
    rs_corr.add_argument("--claim", required=True)
    rs_corr.add_argument("--actor", default="user")
    rs_corr.add_argument("--yes", action="store_true")
    rs_exp = rs_sub.add_parser("expire")
    rs_exp.add_argument("--now", help="ISO now override (tests)")

    ep = sub.add_parser("episodes", help="episode records")
    ep_sub = ep.add_subparsers(dest="subcommand", required=True)
    ep_ls = ep_sub.add_parser("ls")
    ep_ls.add_argument("--repo")
    ep_ls.add_argument("--status")
    ep_ls.add_argument("--limit", type=int, default=25)
    ep_show = ep_sub.add_parser("show")
    ep_show.add_argument("id")

    le = sub.add_parser("lessons", help="lesson records (live + writes)")
    le_sub = le.add_subparsers(dest="subcommand", required=True)
    le_ls = le_sub.add_parser("ls")
    le_ls.add_argument("--status",
                       choices=["verified", "candidate", "invalidated"])
    le_ls.add_argument("--failure-key")
    le_ls.add_argument("--repo")
    le_ls.add_argument("--limit", type=int, default=50)
    le_show = le_sub.add_parser("show")
    le_show.add_argument("id")
    le_inv = le_sub.add_parser("invalidate")
    le_inv.add_argument("id")
    le_inv.add_argument("--reason", required=True)
    le_inv.add_argument("--yes", action="store_true")
    le_promote = le_sub.add_parser("promote")
    le_promote.add_argument("episode_id")
    le_promote.add_argument("--instruction", required=True)
    le_promote.add_argument("--anti-pattern", default="")
    le_promote.add_argument("--repo", default="")
    le_promote.add_argument("--failure-key", default="")
    le_promote.add_argument("--tests-passed", action="store_true")
    le_promote.add_argument("--yes", action="store_true")

    gp = sub.add_parser("gaps", help="skill gaps")
    gp_sub = gp.add_subparsers(dest="subcommand", required=True)
    gp_ls = gp_sub.add_parser("ls")
    gp_ls.add_argument("--status", default="open")
    gp_close = gp_sub.add_parser("close")
    gp_close.add_argument("id")
    gp_close.add_argument("--reason", required=True)
    gp_close.add_argument("--yes", action="store_true")

    co = sub.add_parser("consults", help="L2 frontier consult traces")
    co_sub = co.add_subparsers(dest="subcommand", required=True)
    co_ls = co_sub.add_parser("ls")
    co_ls.add_argument("--limit", type=int, default=25)
    co_show = co_sub.add_parser("show")
    co_show.add_argument("trace_id")

    de = sub.add_parser("decisions", help="decision traces (010)")
    de_sub = de.add_subparsers(dest="subcommand", required=True)
    de_ls = de_sub.add_parser("ls")
    de_ls.add_argument("--limit", type=int, default=25)

    ex = sub.add_parser("export-stats", help="summarise a training export")
    ex.add_argument("--path")
    ws = sub.add_parser("weekly-summary", help=(
        "observed workflow evidence for a window (evidence only — no "
        "productivity claims; FR-7 improvement claims need an 8C "
        "baseline)"))
    ws.add_argument("--days", type=int, default=7,
                    help="window length when --since is not given")
    ws.add_argument("--since",
                    help="ISO timestamp window start; overrides --days")
    ws.add_argument("--json", action="store_true",
                    help="emit the report object instead of text")
    bm = sub.add_parser("benchmark", help=(
        "suite manifests, report schemas, comparator, pinned baselines "
        "(8C — no execution; the 8D controlled local runner runs real "
        "tasks)"))
    bm_sub = bm.add_subparsers(dest="bench_command", required=True)
    bm_sub.add_parser("list", help="list suites + validation status")
    bm_val = bm_sub.add_parser("validate", help="validate a suite manifest")
    bm_val.add_argument("--suite", required=True)
    bm_run = bm_sub.add_parser("run", help=(
        "dry-run plan by default; with --execute AND "
        "--i-understand-this-runs-local-agent-tasks, runs allowlisted "
        "pytest verification in a fresh sandbox (8D)"))
    bm_run.add_argument("--suite", required=True)
    bm_run.add_argument("--dry-run", action="store_true",
                        help="print the execution plan; run nothing")
    bm_run.add_argument("--task", help="one task (default: all runnable)")
    bm_run.add_argument("--overlay",
                        help="directory copied over the workspace "
                             "before verification (a candidate fix)")
    bm_run.add_argument("--timeout", type=int, default=120,
                        help="per-task seconds; kill on expiry "
                             "(default 120)")
    bm_run.add_argument("--out", help="persist the run report JSON here")
    bm_run.add_argument("--json", action="store_true",
                        help="print the run report object")
    bm_run.add_argument("--keep-workspace", action="store_true",
                        help="keep the sandbox workspace for debugging")
    bm_run.add_argument("--execute", action="store_true",
                        help="required to execute (with the "
                             "acknowledgement flag)")
    bm_run.add_argument(
        "--i-understand-this-runs-local-agent-tasks",
        action="store_true",
        help="required to execute (with --execute)")
    bm_cmp = bm_sub.add_parser("compare",
                               help="compare BASELINE CANDIDATE reports")
    bm_cmp.add_argument("baseline")
    bm_cmp.add_argument("candidate")
    bm_cmp.add_argument("--json", action="store_true",
                        help="emit the verdict object instead of text")
    bm_base = bm_sub.add_parser("baseline", help="pinned baselines")
    bm_base_sub = bm_base.add_subparsers(dest="bench_subcommand",
                                         required=True)
    bm_create = bm_base_sub.add_parser(
        "create", help="pin a report as the suite baseline")
    bm_create.add_argument("report")
    bm_create.add_argument("--out",
                           help="default: <benchmarks>/baselines/"
                                "<suite_id>.json")
    bm_create.add_argument("--yes", action="store_true",
                           help="required; a baseline is never created "
                                "automatically")
    return parser


def _resolve_db(args):
    return queries.resolve_path(getattr(args, "db", None))


def _guard_db(path):
    """Return 2 (with a clean message) when the DB is missing/unreadable."""
    if not path.exists():
        print(f"error: memory db not found: {path}", file=sys.stderr)
        return EXIT_DB
    try:
        queries.schema_version(path)
    except queries.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DB
    return EXIT_OK


def _dispatch(args, path):  # noqa: PLR0911, PLR0912, PLR0915 — table walk
    command, sub = args.command, getattr(args, "subcommand", None)
    if command == "status":
        return _cmd_status(path)
    if command == "flags":
        return _cmd_flags()
    if command == "doctor":
        return _cmd_doctor(args)
    if command == "events" and sub == "ls":
        return _cmd_events_ls(args, path)
    if command == "events" and sub == "show":
        return _cmd_events_show(args, path)
    if command == "task" and sub == "declare":
        return _cmd_task_declare(args, path)
    if command == "task" and sub == "status":
        return _cmd_task_status(args, path)
    if command == "task" and sub == "close":
        return _cmd_task_close(args, path)
    if command == "families" and sub == "register":
        return _cmd_families_register(args, path)
    if command == "families" and sub == "trial":
        return _cmd_families_trial(args, path)
    if command == "families" and sub == "analysis":
        return _cmd_families_analysis(args, path)
    if command == "families" and sub == "status":
        return _cmd_families_status(args, path)
    if command == "families" and sub == "holdout-unlock":
        return _cmd_families_unlock(args, path)
    if command == "routes" and sub == "eval":
        return _cmd_routes_eval(args, path)
    if command == "routes" and sub == "ls":
        return _cmd_routes_ls(args, path)
    if command == "routes" and sub == "replay":
        return _cmd_routes_replay(args, path)
    if command == "routes" and sub == "ambiguity":
        return _cmd_routes_ambiguity(args, path)
    if command == "resume" and sub == "assert":
        return _cmd_resume_assert(args, path)
    if command == "resume" and sub == "approve":
        return _cmd_resume_approve(args, path)
    if command == "resume" and sub == "uncertain":
        return _cmd_resume_uncertain(args, path)
    if command == "resume" and sub == "intent":
        return _cmd_resume_intent(args, path)
    if command == "resume" and sub == "draft":
        return _cmd_resume_draft(args, path)
    if command == "resume" and sub == "impact":
        return _cmd_resume_impact(args, path)
    if command == "resume" and sub == "correct":
        return _cmd_resume_correct(args, path)
    if command == "resume" and sub == "expire":
        return _cmd_resume_expire(args, path)
    if command == "episodes" and sub == "ls":
        return _cmd_episodes_ls(args, path)
    if command == "episodes" and sub == "show":
        return _cmd_episode_show(args, path)
    if command == "lessons" and sub == "ls":
        return _cmd_lessons_ls(args, path)
    if command == "lessons" and sub == "show":
        return _cmd_lesson_show(args, path)
    if command == "lessons" and sub == "invalidate":
        return _cmd_lessons_invalidate(args, path)
    if command == "lessons" and sub == "promote":
        return _cmd_lessons_promote(args, path)
    if command == "gaps" and sub == "ls":
        return _cmd_gaps_ls(args, path)
    if command == "gaps" and sub == "close":
        return _cmd_gaps_close(args, path)
    if command == "consults" and sub == "ls":
        return _cmd_consults_ls(args, path)
    if command == "consults" and sub == "show":
        return _cmd_consult_show(args, path)
    if command == "decisions" and sub == "ls":
        return _cmd_decisions_ls(args, path)
    if command == "export-stats":
        return _cmd_export_stats(args)
    if command == "weekly-summary":
        return _cmd_weekly_summary(args, path)
    if command == "benchmark":
        return _cmd_benchmark(args)
    return EXIT_USAGE


# --- 8A: read commands --------------------------------------------------


def _cmd_status(path):
    from minder_op.format import kv
    info = queries.status(path)
    lines = [("db_path", str(path)),
             ("schema_version", info["schema_version"])]
    lines += [("episodes", info["episodes"]),
              ("lessons_verified", info["lessons_verified"]),
              ("lessons_candidate", info["lessons_candidate"]),
              ("lessons_invalidated", info["lessons_invalidated"]),
              ("gaps_open", info["gaps_open"])]
    for label, n in info["consults_by_helpfulness"].items():
        lines.append((f"consults[{label}]", n))
    lines += [("classifier_shadow_rows", info["classifier_shadow_rows"]),
              ("decision_traces_rows", info["decision_traces_rows"])]
    for var in FLAG_VARS:
        lines.append((f"env:{var}", os.environ.get(var) or "(unset)"))
    kv(lines)
    print()
    print(DEFERRED)
    return EXIT_OK


def _cmd_flags():
    print("decision / assist flags (environment only):")
    for var in FLAG_VARS:
        print(f"  {var}={os.environ.get(var) or '(unset)'}")
    print()
    print("Change via systemd/env; restart hook. CLI cannot persist flags.")
    print("MINDER_ASSIST: off | retrieve | block_duplicate_skill | "
          "shadow_suggest | decision_skill")
    print("  retrieve        = attach top verified lesson on a repeated,"
          " hypothesis-gated failure (digest only)")
    print("  decision_skill  = gateway may attach ONE low-risk skill"
          " digest when all ten gates pass (digest only)")
    print("MINDER_CLASSIFIER: unset | shadow   (Phase 5 log-only labels)")
    print("MINDER_DECISION:   unset | shadow   (Phase 5.5 assess loop,"
          " trace only)")
    print()
    print(DEFERRED)
    return EXIT_OK


def _cmd_episodes_ls(args, path):
    rows = queries.episodes(path, repo=args.repo,
                            status_filter=args.status, limit=args.limit)
    fmt.table([{"id": r["episode_id"], "opened": r["opened_at"],
                "status": r["status"], "repo": fmt.safe(r["repo"], 40),
                "task": fmt.safe(r["task_id"], 24)} for r in rows],
              [("id", "id"), ("opened", "opened"), ("status", "status"),
               ("repo", "repo"), ("task", "task")])
    return EXIT_OK


def _cmd_episode_show(args, path):
    ep = queries.episode(path, args.id)
    if not ep:
        print(f"not found: {args.id}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("episode_id", ep["episode_id"]), ("opened_at", ep["opened_at"]),
            ("closed_at", ep["closed_at"]), ("status", ep["status"]),
            ("repo", fmt.safe(ep["repo"], 120)),
            ("task_id", fmt.safe(ep["task_id"], 80))])
    print()
    events = queries.episode_events(path, args.id)
    fmt.table([{"seq": e["seq"], "ts": e["ts"],
                "type": e["event_type"], "tool": e["tool"],
                "failure_key": fmt.safe(e["failure_key"], 60),
                "excerpt": fmt.safe(e.get("payload_json"), 60)}
               for e in events],
              [("seq", "seq"), ("ts", "ts"), ("type", "type"),
               ("tool", "tool"), ("failure_key", "failure_key"),
               ("excerpt", "excerpt")])
    return EXIT_OK


def _cmd_lessons_ls(args, path):
    rows = queries.lessons(path, status_filter=args.status,
                           failure_key=args.failure_key, repo=args.repo,
                           limit=args.limit)
    fmt.table([{"id": r["lesson_id"], "status": r["status"],
                "from": r["valid_from"], "failure_key":
                    fmt.safe(r["failure_key"], 48),
                "instruction": fmt.safe(r["instruction"], 60)}
               for r in rows],
              [("id", "id"), ("status", "status"), ("from", "from"),
               ("failure_key", "failure_key"),
               ("instruction", "instruction")])
    return EXIT_OK


def _cmd_lesson_show(args, path):
    row = queries.lesson(path, args.id)
    if not row:
        print(f"not found: {args.id}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", row["lesson_id"]), ("status", row["status"]),
            ("repo", fmt.safe(row["repo"], 120)),
            ("failure_key", fmt.safe(row["failure_key"], 120)),
            ("instruction", fmt.safe(row["instruction"], 400)),
            ("anti_pattern", fmt.safe(row["anti_pattern"], 200)),
            ("verification", fmt.safe(row["verification_json"], 200)),
            ("source_episode", row["source_episode"]),
            ("valid_from", row["valid_from"]), ("valid_to", row["valid_to"])])
    return EXIT_OK


def _cmd_gaps_ls(args, path):
    rows = queries.gaps(path, status_filter=args.status)
    fmt.table([{"id": r["gap_id"], "ts": r["ts"], "status": r["status"],
                "type": r["gap_type"], "repo": fmt.safe(r["repo"], 30),
                "failure_key": fmt.safe(r["failure_key"], 48),
                "sample": fmt.safe(r["sample_error"], 48)} for r in rows],
              [("id", "id"), ("ts", "ts"), ("status", "status"),
               ("type", "type"), ("repo", "repo"),
               ("failure_key", "failure_key"), ("sample", "sample")])
    return EXIT_OK


def _cmd_consults_ls(args, path):
    rows = queries.consults(path, limit=args.limit)
    fmt.table([{"trace_id": r["trace_id"], "ts": r["ts"],
                "label": r["helpfulness"] or "(unclassified)",
                "verif": r["verification_status"] or "-",
                "failure_key": fmt.safe(r["failure_key"], 44),
                "providers": fmt.safe(r["provider_fingerprint"], 24)}
               for r in rows],
              [("trace_id", "trace_id"), ("ts", "ts"), ("label", "label"),
               ("verif", "verif"), ("failure_key", "failure_key"),
               ("providers", "providers")])
    return EXIT_OK


def _cmd_consult_show(args, path):
    row = queries.consult(path, args.trace_id)
    if not row:
        print(f"not found: {args.trace_id}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("trace_id", row["trace_id"]), ("ts", row["ts"]),
            ("episode_id", row["episode_id"]),
            ("failure_key", fmt.safe(row["failure_key"], 120)),
            ("local_attempts", row["local_attempts"]),
            ("redaction_profile", row["redaction_profile"]),
            ("providers", fmt.safe(row["provider_fingerprint"], 80)),
            ("request_hash", row["request_hash"]),
            ("response_hash", row["response_hash"]),
            ("helpfulness", row["helpfulness"] or "(unclassified)"),
            ("verification_status", row["verification_status"] or "-"),
            ("accepted", fmt.safe(row.get("accepted_actions_json"), 200)),
            ("rejected", fmt.safe(row.get("rejected_actions_json"), 200)),
            ("distilled", fmt.safe(row.get("distilled_json"), 200))])
    print()
    print("labels come from frontier_evals (007); raw prompt/response text "
          "is never stored — hashes only")
    return EXIT_OK


def _cmd_decisions_ls(args, path):
    rows = queries.decisions(path, limit=args.limit)
    fmt.table([{"id": r["id"], "ts": r["ts"],
                "contract": f"{r['contract_id']}/{r['contract_version']}",
                "model": r["model_recommendation"] or "-",
                "policy": r["policy_decision"] or "-",
                "override": r["override"] or "-",
                "conf": r["confidence"], "provider": r["provider"]}
               for r in rows],
              [("id", "id"), ("ts", "ts"), ("contract", "contract"),
               ("model", "model"), ("policy", "policy"),
               ("override", "override"), ("conf", "conf"),
               ("provider", "provider")])
    return EXIT_OK


def _cmd_export_stats(args):
    from memory import train_eval
    path = args.path or "memory/exports/training_candidates.jsonl"
    if not os.path.exists(path):
        print(f"error: export not found: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        stats = train_eval.summarise_export(path)
    except Exception as exc:  # noqa: BLE001 — never a traceback
        print(f"error: cannot summarise {path}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("path", path), ("count", stats["count"]),
            ("families", stats["families"]),
            ("held_out_ratio", round(stats["held_out_ratio"], 4)),
            ("splits", stats.get("splits", {})),
            ("redaction_ok", stats["redaction_ok"])])
    print()
    print("export is research input only — never load an adapter on the "
          "qwen27b 100k unit (docs/lora-offline.md)")
    return EXIT_OK


def _cmd_weekly_summary(args, path):
    from minder_op import summary
    try:
        report = summary.build_weekly_summary(path, days=args.days,
                                              since=args.since)
    except ValueError as exc:  # bad --since / --days — usage, not crash
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary.render_text(report)
    return EXIT_OK


def _cmd_doctor(args):
    from minder_op import doctor
    try:
        path = _resolve_db(args)
    except queries.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = doctor.run_checks(path, probe=not args.no_probe)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        doctor.render(report)
    for check in report["checks"]:
        if check["status"] == "fail":
            print(f"error: {check['id']}: {check['detail']}",
                  file=sys.stderr)
    return EXIT_OK if report["healthy"] else EXIT_USAGE


def _cmd_events_ls(args, path):
    rows = queries.events(path, failure_key=args.failure_key,
                          event_type=args.event_type,
                          episode_id=args.episode, limit=args.limit)
    fmt.table([{"id": r["event_id"], "ts": r["ts"],
                "type": r["event_type"], "tool": r["tool"],
                "episode": r.get("episode_id") or "-",
                "failure_key": fmt.safe(r["failure_key"], 44),
                "fp": fmt.safe(r["action_fingerprint"], 12)}
               for r in rows],
              [("id", "id"), ("ts", "ts"), ("type", "type"),
               ("tool", "tool"), ("episode", "episode"),
               ("failure_key", "failure_key"), ("fp", "fp")])
    return EXIT_OK


def _cmd_events_show(args, path):
    row = queries.event(path, args.id)
    if not row:
        print(f"not found: {args.id}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("event_id", row["event_id"]), ("ts", row["ts"]),
            ("type", row["event_type"]), ("tool", row["tool"]),
            ("session_id", fmt.safe(row["session_id"], 40)),
            ("task_id", fmt.safe(row["task_id"], 40)),
            ("repo", fmt.safe(row["repo"], 100)),
            ("failure_key", fmt.safe(row["failure_key"], 120)),
            ("fingerprint", fmt.safe(row["action_fingerprint"], 60)),
            ("episode", row.get("episode_id") or "-"),
            ("redaction", row["redaction_status"]),
            ("payload", fmt.safe(row["payload_json"], 400))])
    return EXIT_OK


# --- Phase 1: task-context intake + trading research protocol ------------


def _cmd_task_declare(args, path):
    from memory import task_context
    previous = task_context.find_open_context(args.task, db_path=path)
    row, status = task_context.declare_task(
        args.domain, task_id=args.task, actor=args.actor,
        subtask=args.subtask, note=args.note, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("context_id", row["context_id"]),
            ("task_id", row["task_id"]),
            ("domain", row["domain"]),
            ("subtask_seq", row["subtask_seq"]),
            ("status", status),
            ("origin", row["origin"])])
    if status == "switched":
        print("previous context pinned; domain_transitions row recorded")
    # Phase 2 calibration: shadow what the router WOULD propose against
    # this human label. Observe-only — the declaration stays
    # authoritative regardless of what the provider says.
    from decision import routing
    shadow = routing.assess_route(
        declared_domain=args.domain, task_id=args.task,
        current_domain=(previous or {}).get("domain"),
        subject_changed=(status == "switched"), record=True, db_path=path)
    if shadow.get("trace_id"):
        fmt.kv([("shadow_trace", shadow["trace_id"]),
                ("shadow_candidate", shadow["candidate_domain"]),
                ("agrees", shadow["candidate_domain"] == args.domain)])
    return EXIT_OK


def _cmd_task_status(args, path):
    from memory import task_context
    state = task_context.task_status(task_id=args.task, db_path=path)
    open_row = state.get("open")
    if not open_row:
        print("(none)")
        return EXIT_OK
    fmt.kv([("context_id", open_row["context_id"]),
            ("task_id", open_row["task_id"]),
            ("domain", open_row["domain"]),
            ("subtask_seq", open_row["subtask_seq"]),
            ("opened_at", open_row["opened_at"]),
            ("origin", open_row["origin"])])
    return EXIT_OK


def _cmd_task_close(args, path):
    from memory import task_context
    row, status = task_context.close_task(task_id=args.task,
                                          reason=args.reason, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("context_id", row["context_id"]),
            ("status", status), ("reason", row["close_reason"])])
    return EXIT_OK


def _json_arg(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise ValueError(f"{what} must be valid JSON: {text!r}")


def _cmd_families_register(args, path):
    from memory import trading_protocol as tp
    try:
        sources = _json_arg(args.sources, "--sources")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    row, status = tp.register_family(
        args.method_digest, planned_metric=args.metric,
        split_scheme=args.splits, data_sources=sources,
        universe=args.universe, expected_trials=args.expected_trials,
        family_id=args.family, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("family_id", row["family_id"]), ("version", row["version"]),
            ("status", status), ("row", f"{row['family_id']}:v"
                                       f"{row['version']}")])
    return EXIT_OK


def _cmd_families_trial(args, path):
    from memory import trading_protocol as tp
    try:
        vintage = _json_arg(args.vintage, "--vintage")
        result = _json_arg(args.result, "--result")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    row, status = tp.record_trial(
        args.family, config_hash=args.config_hash,
        dataset_vintage=vintage, split_assignment=args.split,
        result_summary=result, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("trial_id", row["trial_id"]), ("status", status),
            ("flagged", row["flagged"] or "-"),
            ("split", row["split_assignment"])])
    return EXIT_OK if not row["flagged"] else EXIT_USAGE


def _cmd_families_analysis(args, path):
    from memory import trading_protocol as tp
    try:
        verdict = _json_arg(args.verdict, "--verdict") if args.verdict \
            else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    row, status = tp.record_analysis(
        args.family, method=args.method, code_digest=args.code_digest,
        n_trials_referenced=args.n, trial_sharpe_variance=args.variance,
        verdict=verdict, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("analysis_id", row["analysis_id"]), ("status", status),
            ("flagged", row["flagged"] or "-"),
            ("artifact_status", row["status"]),
            ("note", "candidate — promotion requires a recorded human "
                     "review decision; Minder never certifies "
                     "profitability")])
    return EXIT_OK if not row["flagged"] else EXIT_USAGE


def _cmd_families_status(args, path):
    from memory import trading_protocol as tp
    status = tp.manifest_status(args.family, db_path=path)
    fmt.kv([(key, status[key]) for key in
            ("family_id", "registered", "version", "trials",
             "n_trials_bookkept", "expected_trials", "flagged_trials",
             "stale", "verifiable")])
    if status["blockers"]:
        for blocker in status["blockers"]:
            print(f"blocker: {blocker}")
    return EXIT_OK if status["registered"] else EXIT_USAGE


def _cmd_families_unlock(args, path):
    from memory import trading_protocol as tp
    if not args.yes:
        print("PLAN (dry — nothing written):")
        print(f"  holdout unlock for {args.family} by {args.actor} "
              "(recorded as a human_input_event)")
        print("re-run with --yes to record the decision")
        return EXIT_USAGE
    from memory import task_context
    event, status = task_context.record_human_input(
        "holdout_unlock", actor=args.actor, authority="user",
        decision="approved",
        question=f"unlock holdout split for {args.family}?",
        affects=[{"type": "hypothesis_family", "id": args.family}],
        db_path=path)
    if event is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("event_id", event["event_id"]), ("kind", event["kind"]),
            ("actor", event["actor"]), ("status", status),
            ("note", "holdout trials recorded after this event are not "
                     "flagged; earlier flags stay for audit")])
    return EXIT_OK


def _cmd_routes_eval(args, path):
    from decision import routing
    from decision.providers.null import NullClient
    provider = {"rules": routing.RulesRouteProvider,
                "null": NullClient}.get(args.provider)
    provider = provider() if provider else None
    if args.provider == "laya":
        from decision.providers.laya import try_laya_client
        provider = try_laya_client()
        if provider is None:
            print("error: laya provider unavailable", file=sys.stderr)
            return EXIT_USAGE
    result = routing.assess_route(
        declared_domain=args.declared_domain, task_id=args.task,
        session_id=args.session, subject_changed=args.subject_changed,
        provider=provider, record=True, db_path=path)
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
        return EXIT_USAGE
    decision = result["decision"]
    fmt.kv([("trace_id", result["trace_id"]),
            ("policy_transition", result["policy_transition"]),
            ("candidate_domain", result["candidate_domain"]),
            ("intent_kind (advisory)", result["intent_kind"]),
            ("abstained", result["abstained"]),
            ("validation", result["validation"]),
            ("override", decision.override or "-")])
    print()
    print("observe-only: a route proposal never applies itself; task "
          "contexts change only via explicit declaration")
    return EXIT_OK


def _cmd_routes_ls(args, path):
    from decision import routing
    rows = routing.list_routes(limit=args.limit, db_path=path)
    fmt.table([{"id": r["trace_id"], "ts": r["ts"],
                "declared": r["declared_domain"] or "-",
                "candidate": r["candidate_domain"] or "-",
                "transition": r["transition"],
                "conf": r["confidence"],
                "abst": r["abstained"],
                "provenance": r["provenance"],
                "validation": r["validation_result"]} for r in rows],
              [("id", "id"), ("ts", "ts"), ("declared", "declared"),
               ("candidate", "candidate"), ("transition", "transition"),
               ("conf", "conf"), ("abst", "abst"),
               ("provenance", "provenance"),
               ("validation", "validation")])
    return EXIT_OK


def _cmd_routes_replay(args, path):
    from decision import routing
    from decision.providers.null import NullClient
    provider = {"rules": routing.RulesRouteProvider,
                "null": NullClient}.get(args.provider)
    provider = provider() if provider else None
    if args.provider == "laya":
        from decision.providers.laya import try_laya_client
        provider = try_laya_client()
        if provider is None:
            print("error: laya provider unavailable (package missing or "
                  "no recognised surface; MINDER_LAYA_DEVICE/MINDER_LAYA_"
                  "MODEL configure it)", file=sys.stderr)
            return EXIT_USAGE
    try:
        report = routing.replay_cases(args.cases, provider=provider,
                                      db_path=path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.out:
        _write_json_file(args.out, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        fmt.kv([(key, report["metrics"][key]) for key in
                ("comparable_runs", "verified_completion_rate",
                 "unsafe_executions", "harmful_frontier_acceptances",
                 "external_prohibited_egress")])
        detail = report["routing_detail"]
        fmt.kv([("provider", detail["provider"]),
                ("correct", detail["correct"]),
                ("false_switches", detail["false_switches"]),
                ("missed_switches", detail["missed_switches"]),
                ("abstentions", detail["abstentions"]),
                ("unnecessary_calls", detail["unnecessary_calls"]),
                ("label_status", detail["label_status"])])
        if args.out:
            print(f"report written: {args.out}")
        print("replay is offline and observe-only; reports pin via "
              "benchmark baseline create --yes")
    return EXIT_OK


def _cmd_routes_ambiguity(args, path):
    from decision import routing
    report = routing.ambiguity_report(days=args.days, db_path=path)
    fmt.kv([(key, report[key]) for key in
            ("window_days", "sessions", "failure_events",
             "distinct_failure_keys", "family_shifts",
             "declared_boundaries")])
    print(report["note"])
    return EXIT_OK


# --- Phase 1 résumé slice: fact / wording / intent ------------------------


def _cmd_resume_assert(args, path):
    from memory import resume_evidence
    row, status = resume_evidence.assert_career_fact(
        args.claim, wording_variants=args.variant, actor=args.actor,
        db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("assertion_id", row["assertion_id"]),
            ("status", status), ("claim", row["claim_text"]),
            ("variants", row["wording_variants_json"])])
    return EXIT_OK


def _cmd_resume_approve(args, path):
    from memory import resume_evidence
    row, status = resume_evidence.approve_wording(
        args.assertion, phrase=args.phrase, actor=args.actor,
        scope=args.scope, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("wording_id", row["wording_id"]), ("status", status),
            ("phrase", row["phrase"]), ("scope", row["scope"])])
    return EXIT_OK


def _cmd_resume_uncertain(args, path):
    from memory import resume_evidence
    row, status = resume_evidence.mark_uncertain(
        args.assertion, actor=args.actor, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("assertion_id", row["assertion_id"]),
            ("status", status),
            ("note", "approved wordings moved to review; set an explicit "
                     "fact or wording answer before new intents")])
    return EXIT_OK


def _cmd_resume_intent(args, path):
    from memory import resume_evidence
    row, status = resume_evidence.set_intent(
        args.jd, args.jd_digest, args.assertion,
        chosen_variant=args.phrase, actor=args.actor,
        retention_days=args.retention, db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("intent_id", row["intent_id"]), ("jd_id", row["jd_id"]),
            ("chosen_variant", row["chosen_variant"]),
            ("expires_at", row["expires_at"]),
            ("status", status),
            ("note", "JD-scoped only; history and other JDs untouched")])
    return EXIT_OK


def _cmd_resume_draft(args, path):
    from memory import resume_evidence
    row, status = resume_evidence.record_draft(
        args.draft, args.assertion, phrase=args.phrase, jd_id=args.jd,
        db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("usage_id", row["usage_id"]), ("status", status),
            ("flagged", row["flagged"] or "-")])
    return EXIT_OK if not row["flagged"] else EXIT_USAGE


def _cmd_resume_impact(args, path):
    from memory import resume_evidence
    preview = resume_evidence.impact_preview(args.assertion, db_path=path)
    print(f"impact preview for {args.assertion} (read-only; corrections "
          "are human-gated):")
    fmt.kv([("drafts", len(preview["drafts"])),
            ("intents", len(preview["intents"])),
            ("wordings", len(preview["wordings"]))])
    for draft in preview["drafts"]:
        print(f"  draft: {draft['draft_id']} phrase='{draft['phrase']}'"
              f" flagged={draft['flagged'] or '-'}")
    for intent in preview["intents"]:
        print(f"  intent: {intent['jd_id']} variant="
              f"'{intent['chosen_variant']}' status={intent['status']}")
    return EXIT_OK


def _cmd_resume_correct(args, path):
    from memory import resume_evidence
    if not args.yes:
        print("PLAN (dry — nothing written):")
        print(f"  resume correct {args.assertion} -> '{args.claim}' "
              "(supersedes the assertion, flags dependent drafts and "
              "intents, records a factual_correction human event)")
        print("re-run with --yes to apply")
        return EXIT_USAGE
    row, status = resume_evidence.correct_fact(
        args.assertion, corrected_claim=args.claim, actor=args.actor,
        db_path=path)
    if row is None:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("new_assertion_id", row["assertion_id"]),
            ("status", status), ("claim", row["claim_text"]),
            ("note", "old assertion superseded (auditable); dependent "
                     "drafts and intents flagged for review")])
    return EXIT_OK


def _cmd_resume_expire(args, path):
    from memory import resume_evidence
    now = args.now or None
    summary = resume_evidence.expire_intents(now=now, db_path=path)
    fmt.kv([("expired", summary["expired"]),
            ("note", "intent-scoped retention enforcement; career "
                     "history untouched")])
    return EXIT_OK


# --- 8C: benchmark foundation (manifests/reports/comparator; no run) -----


def _cmd_benchmark(args):
    from minder_op import benchmark as bench
    try:
        if args.bench_command == "list":
            return _bench_list(bench)
        if args.bench_command == "validate":
            return _bench_validate(bench, args.suite)
        if args.bench_command == "run":
            return _bench_run(bench, args)
        if args.bench_command == "compare":
            return _bench_compare(bench, args.baseline, args.candidate,
                                  args.json)
        if args.bench_command == "baseline" and \
                args.bench_subcommand == "create":
            return _bench_baseline_create(bench, args.report, args.out,
                                          args.yes)
    except bench.BenchmarkError as exc:  # load/validate refused
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


def _bench_list(bench):
    fmt.table([{"suite": r["suite_id"], "ver": r["manifest_version"],
                "tasks": r["tasks"], "fp": r["fingerprint"],
                "status": r["status"]} for r in bench.list_suites()],
              [("suite", "suite"), ("ver", "ver"), ("tasks", "tasks"),
               ("fp", "fp"), ("status", "status")])
    print()
    print("8C foundation: manifests + schemas + comparator; execution "
          "arrives with the 8D controlled local runner")
    return EXIT_OK


def _bench_validate(bench, suite_id):
    manifest = bench.read_manifest(suite_id)
    errors = bench.validate_manifest(manifest, bench.suite_dir(suite_id))
    print(f"suite {suite_id}: fingerprint "
          f"{bench.manifest_fingerprint(manifest)}")
    if errors:
        for error in errors:
            print(f"  invalid: {error}")
        return EXIT_USAGE
    print("  ok")
    return EXIT_OK


def _bench_run(bench, args):
    execute = args.execute or args.i_understand_this_runs_local_agent_tasks
    if execute and args.dry_run:
        print("error: --dry-run and --execute are mutually exclusive",
              file=sys.stderr)
        return EXIT_USAGE
    if execute:
        if not (args.execute and
                args.i_understand_this_runs_local_agent_tasks):
            from minder_op import runner
            print(f"error: {runner.REQUIRED_FLAGS_MSG}", file=sys.stderr)
            return EXIT_USAGE
        return _bench_execute(bench, args)
    if not args.dry_run:
        from minder_op import runner
        print(f"error: {runner.REQUIRED_FLAGS_MSG}", file=sys.stderr)
        return EXIT_USAGE
    manifest = _bench_validate_suite(bench, args.suite)
    print(json.dumps(bench.dry_run_plan(manifest), indent=2,
                     sort_keys=True))
    return EXIT_OK


def _bench_validate_suite(bench, suite_id):
    """Load + validate a suite manifest; exit cleanly with reasons."""
    manifest = bench.read_manifest(suite_id)
    errors = bench.validate_manifest(manifest, bench.suite_dir(suite_id))
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        raise bench.BenchmarkError(f"suite {suite_id} failed validation")
    return manifest


def _bench_execute(bench, args):
    from minder_op import runner
    manifest = _bench_validate_suite(bench, args.suite)
    if args.timeout < 1:
        print("error: --timeout must be >= 1 second", file=sys.stderr)
        return EXIT_USAGE
    if args.overlay and not os.path.isdir(args.overlay):
        print(f"error: overlay dir not found: {args.overlay}",
              file=sys.stderr)
        return EXIT_USAGE
    tasks = runner.select_tasks(manifest, args.task)
    suite_path = bench.suite_dir(args.suite)
    entries = []
    kept = None
    for task in tasks:
        entry, workspace = runner.execute_task(
            suite_path, task, overlay=args.overlay, timeout=args.timeout,
            keep_workspace=args.keep_workspace)
        entries.append(entry)
        kept = workspace or kept
    report = runner.build_report(manifest, entries, timeout=args.timeout,
                                 workspace=kept)
    if args.out:
        _write_json_file(args.out, report)
    if any(entry["status"] == "timeout" for entry in entries):
        # a killed run must leave a failed report behind, not just
        # terminal scrollback
        failed_path = args.out or bench.default_failed_report_path(
            report["suite_id"])
        _write_json_file(failed_path, report)
        print(f"failed report written: {failed_path}", file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        fmt.table([{"task": e["task_id"], "status": e["status"],
                    "exit": e["exit_code"],
                    "ms": e["duration_ms"]} for e in entries],
                  [("task", "task"), ("status", "status"),
                   ("exit", "exit"), ("ms", "ms")])
        print()
        fmt.kv(list(report["metrics"].items()))
        if kept:
            print(f"workspace kept: {kept}")
        if args.out:
            print(f"report written: {args.out}")
    if any(entry["status"] == "timeout" for entry in entries):
        return EXIT_USAGE  # the run itself did not complete
    return EXIT_OK


def _write_json_file(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _bench_compare(bench, baseline_path, candidate_path, as_json):
    baseline = bench.read_report(baseline_path)
    candidate = bench.read_report(candidate_path)
    verdict = bench.compare_reports(baseline, candidate)
    if as_json:
        print(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        print(f"verdict: {verdict['verdict']}")
        for reason in verdict["reasons"]:
            print(f"  - {reason}")
        print("baseline is first, candidate second; protected rules: "
              "unsafe/harmful/egress > 0, completion drop > 5pp, "
              "<20 runs, fingerprint mismatch")
    return EXIT_OK if verdict["verdict"] == bench.VERDICT_PASS \
        else EXIT_USAGE


def _bench_baseline_create(bench, report_path, out_arg, yes):
    report = bench.read_report(report_path)
    out = (os.path.abspath(out_arg) if out_arg
           else bench.default_baseline_path(report["suite_id"]))
    plan = (f"baseline create {report_path} -> {out} "
            f"(suite {report['suite_id']}, fingerprint "
            f"{report['suite_fingerprint'][:12]})")
    if not yes:
        print("PLAN (dry — nothing written):")
        print(f"  {plan}")
        print("re-run with --yes to pin the baseline")
        return EXIT_USAGE
    if os.path.exists(out):
        print(f"error: pinned baseline exists, move it aside first: "
              f"{out}", file=sys.stderr)
        return EXIT_USAGE
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    fmt.kv([("baseline", out), ("suite_id", report["suite_id"]),
            ("fingerprint", report["suite_fingerprint"]),
            ("note", "pinned — compare candidates with: minder-op "
                     "benchmark compare <baseline> <candidate>")])
    return EXIT_OK


# --- 8B: explicit writes (always through memory APIs, always --yes) ------


def _refuse_without_yes(args, plan):
    if not getattr(args, "yes", False):
        print("PLAN (dry — nothing written):")
        print(f"  {plan}")
        print("re-run with --yes to apply")
        return True
    return False


def _cmd_lessons_invalidate(args, path):
    from memory import lessons as memory_lessons
    if _refuse_without_yes(
            args, f"lessons invalidate {args.id} reason={args.reason!r}"):
        return EXIT_USAGE
    row, status = memory_lessons.invalidate_lesson(args.id, args.reason,
                                                   db_path=path)
    if not row:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", row["lesson_id"]), ("status", status),
            ("invalidated", row["valid_to"]),
            ("reason", fmt.safe(args.reason, 200))])
    return EXIT_OK


def _cmd_lessons_promote(args, path):
    from memory import lessons as memory_lessons
    if _refuse_without_yes(
            args, f"lessons promote episode={args.episode_id} "
                  f"instruction={args.instruction!r} "
                  f"tests_passed={bool(args.tests_passed)}"):
        return EXIT_USAGE
    verification = {"tests_passed": True} if args.tests_passed else {}
    lesson, status = memory_lessons.promote_lesson(
        args.episode_id, args.instruction,
        anti_pattern=args.anti_pattern, verification=verification,
        repo=args.repo, failure_key=args.failure_key, actor="operator",
        db_path=path)
    if not lesson:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", lesson["lesson_id"]), ("status", status),
            ("lesson_status", lesson["status"]),
            ("source_episode", lesson["source_episode"])])
    return EXIT_OK


def _cmd_gaps_close(args, path):
    from memory import skills as memory_skills
    if _refuse_without_yes(
            args, f"gaps close {args.id} reason={args.reason!r}"):
        return EXIT_USAGE
    row, status = memory_skills.close_gap(args.id, args.reason,
                                          db_path=path)
    if not row:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("gap_id", row["gap_id"]), ("status", status),
            ("gap_status", row["status"]),
            ("reason", fmt.safe(args.reason, 200))])
    return EXIT_OK


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help -> 0; usage error -> 1
        return int(exc.code or 0)
    if not args.command:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    path = _resolve_db(args)
    needs_db = args.command not in ("flags", "export-stats", "benchmark",
                                    "doctor")
    if needs_db:
        code = _guard_db(path)
        if code != EXIT_OK:
            return code
    try:
        return _dispatch(args, path)
    except queries.DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DB
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — CLI never tracebacks
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
