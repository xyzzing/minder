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
from pathlib import Path

from minder_op import format as fmt
from minder_op import queries

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DB = 2


def trace_reviews_mod():
    """Lazy import: the memory plane is optional for the operator CLI (the
    watchdog never depends on it), so a module-level import here would make
    every `minder-op` command depend on it."""
    from minder_memory import trace_reviews
    return trace_reviews

# Kept visible where an operator will look: deferred out of 8A–8C by the
# spec (docs/operator-cli.md carries the sketches).
DEFERRED = ("deferred (8D/8E+): controlled local benchmark runner, web "
            "console, auth/multi-user, live Jev in CLI, DB-edited "
            "MINDER_ASSIST, graph visualiser, auto skill apply, log "
            "retention/prune, skill-risk denylist, 003-vs-"
            "frontier_evals label sync")

FLAG_VARS = ("MINDER_ASSIST", "MINDER_CLASSIFIER", "MINDER_DECISION",
             "MINDER_SUCCESS_GUARD")
# The wording for a number this store cannot produce (issue #20). Same
# phrase the web console uses, so a read surface never answers "0" when
# the honest answer is "no table to count".
NOT_AVAILABLE = "not available"


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
    eng = sub.add_parser("engine", help=(
        "dual-engine registry: unit state, upstream health, lifecycle "
        "switch (issue #3)"))
    eng_sub = eng.add_subparsers(dest="subcommand")
    eng_sub.add_parser("status", help=(
        "per-engine rows: active flag, systemd unit state, upstream health"))
    eng_sw = eng_sub.add_parser("switch", help=(
        "stop the current engine unit, start the target, health-check it, "
        "then flip active_engine; rolls back when the target never comes up"))
    eng_sw.add_argument("name", help="engine name from the minder.json registry")
    eng_sw.add_argument("--yes", action="store_true", help=(
        "required: the switch restarts engine units"))
    doc = sub.add_parser("doctor", help=(
        "one-shot install health report: db, schema, flags, hook "
        "wiring, event staleness, proxy, benchmarks (read-only)"))
    doc.add_argument("--no-probe", action="store_true",
                     help="skip the loopback proxy reachability probe")
    doc.add_argument("--json", action="store_true",
                     help="emit the check objects instead of text")
    cap = sub.add_parser("capture", help=(
        "capture health: hook invocations vs persisted records, store "
        "freshness, sink reachability, sandbox modes (read-only)"))
    cap.add_argument("--json", action="store_true",
                     help="emit the report object instead of text")
    cap.add_argument("--window-hours", type=int, default=1,
                     help="coverage window in hours (default 1)")
    cap.add_argument("--dsh-home", default=None,
                     help="dsh home to read sessions from (default ~/.dsh)")
    sc = sub.add_parser("scorecard", help=(
        "improvement scorecard: capture, cost, failures, learning, "
        "context, hygiene (read-only)"))
    sc.add_argument("--json", action="store_true",
                     help="emit the report object instead of text")
    sc.add_argument("--window-hours", type=int, default=24,
                     help="window in hours (default 24)")
    sc.add_argument("--dsh-home", default=None,
                     help="dsh home to read sessions from (default ~/.dsh)")

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

    sl = sub.add_parser("success-loops", help="advisory-raising success "
                        "loops (success-loop guard)")
    sl_sub = sl.add_subparsers(dest="subcommand", required=True)
    sl_ls = sl_sub.add_parser("ls")
    sl_ls.add_argument("--limit", type=int, default=25)

    tr = sub.add_parser("trace", help="offline review of completed dsh "
                        "sessions (read-only on dsh)")
    tr_sub = tr.add_subparsers(dest="subcommand", required=True)
    tr_ls = tr_sub.add_parser("ls", help="list dsh sessions and whether "
                              "each has been reviewed")
    tr_ls.add_argument("--limit", type=int, default=25)
    tr_ls.add_argument("--json", action="store_true")
    tr_rev = tr_sub.add_parser("review", help="read, evaluate and print "
                               "one session's trace")
    tr_rev.add_argument("session", help="session id, id suffix, or project "
                        "slug (omit to pick the newest)")
    tr_rev.add_argument("--rubric", help="JSON rubric: declarative required/"
                         "forbidden tools plus evaluator thresholds")
    tr_rev.add_argument("--json", action="store_true")
    tr_rev.add_argument("--no-store", action="store_true",
                        help="never write, even if MINDER_TRACE_REVIEW=on")
    tr_show = tr_sub.add_parser("show", help="render a stored review")
    tr_show.add_argument("review_id")
    tr_show.add_argument("--json", action="store_true")
    tr_fb = tr_sub.add_parser("feedback", help="append structured human "
                              "feedback to a review (needs --yes)")
    tr_fb.add_argument("review_id")
    tr_fb.add_argument("--level", required=True,
                       choices=list(trace_reviews_mod().LEVELS))
    tr_fb.add_argument("--category", required=True,
                       choices=list(trace_reviews_mod().CATEGORIES))
    tr_fb.add_argument("--target", dest="target_ref",
                       help="ds_seq, claim id, or finding id being judged")
    tr_fb.add_argument("--finding", help="finding_id this feedback judges")
    tr_fb.add_argument("--verdict", choices=["confirm", "reject"])
    tr_fb.add_argument("--comment")
    tr_fb.add_argument("--reviewer")
    tr_fb.add_argument("--json", action="store_true")
    tr_fb.add_argument("--yes", action="store_true")
    tr_reg = tr_sub.add_parser("regress", help="turn a confirmed failure "
                               "finding into a benchmark regression case")
    tr_reg.add_argument("session", help="session id, id suffix, or project "
                         "slug")
    tr_reg.add_argument("--finding", required=True, help="finding_id")
    tr_reg.add_argument("--suite", default="coding-core-v1")
    tr_reg.add_argument("--task-id")
    tr_reg.add_argument("--json", action="store_true")
    tr_reg.add_argument("--yes", action="store_true")

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
    # The closed lists come from minder_memory.lesson_decisions (C4); the
    # parser reuses them so a code the memory layer would refuse never
    # reaches it, and argparse prints the whole taxonomy on a typo.
    from minder_memory import lesson_decisions as _ld
    le_inv.add_argument("--diagnosis", choices=list(_ld.DIAGNOSES),
                       default=_ld.DEFAULT_DIAGNOSIS,
                       help="what went wrong with this lesson (issue #14;"
                            " the default names no mechanism)")
    le_inv.add_argument("--yes", action="store_true")
    le_rej = le_sub.add_parser("reject")
    le_rej.add_argument("id", help="candidate lesson id, from `lessons ls"
                                   " --status candidate`")
    le_rej.add_argument("--code", required=True,
                       choices=[c for c in _ld.DECISION_CODES
                                if c != "grounded_useful"],
                       help="why this distillation is not a lesson"
                            " (issue #14)")
    le_rej.add_argument("--note", default="")
    le_rej.add_argument("--yes", action="store_true")
    le_dec = le_sub.add_parser("decisions", help="lesson decision ledger"
                               " and counts per reason code (issue #14)")
    le_dec.add_argument("--id", help="one lesson's decisions, newest first")
    le_dec.add_argument("--limit", type=int, default=20)
    le_inj = le_sub.add_parser("injections", help="injection ledger:"
                               " per-lesson counts with the misses named"
                               " as misses (issue #20)")
    le_inj.add_argument("--limit", type=int, default=50)
    le_promote = le_sub.add_parser("promote")
    le_promote.add_argument("episode_id", nargs="?",
                            help="episode to promote from, or the candidate "
                                 "lesson id with --from-candidate")
    le_promote.add_argument("--instruction")
    le_promote.add_argument("--anti-pattern", default="")
    le_promote.add_argument("--repo", default="")
    le_promote.add_argument("--failure-key", default="")
    le_promote.add_argument("--tests-passed", action="store_true")
    le_promote.add_argument("--from-candidate", action="store_true",
                            help="adopt a frontier-distilled candidate "
                                 "lesson (id, not episode)")
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
    q = sub.add_parser("quality", help=(
        "clean-completion quality: baseline-relative junk metrics + "
        "declarative external analyzers (PRD v0.9 9D)"))
    q_sub = q.add_subparsers(dest="quality_command", required=True)
    q_assess = q_sub.add_parser("assess", help=(
        "measure PRE_DIR -> POST_DIR: diff metrics + analyzer findings, "
        "only-new-junk counts (I-2)"))
    q_assess.add_argument("pre")
    q_assess.add_argument("post")
    q_assess.add_argument("--allowed", action="append", default=[],
                          help="scope contract path pattern (repeatable)")
    q_assess.add_argument("--json", action="store_true")
    q_sub.add_parser("adapters", help=(
        "list declarative adapters: found/missing, version, pin match"))
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
    # The one deliberate schema write (issue #26). No --yes: migrations
    # are additive DDL, idempotent, and already wrapped per file by the
    # runtime's own migrate(); requiring a flag here would only train the
    # operator to type it without reading the plan the command prints.
    sub.add_parser("migrate", help=(
        "apply pending schema migrations to the memory db (idempotent; "
        "the runtime does this on connect, but a store can sit behind "
        "if nothing has reconnected since an upgrade)"))
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
    if command == "migrate":
        return _cmd_migrate(path)
    if command == "flags":
        return _cmd_flags()
    if command == "engine" and sub == "status":
        return _cmd_engine_status(args)
    if command == "engine" and sub == "switch":
        return _cmd_engine_switch(args)
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
    if command == "success-loops" and sub == "ls":
        return _cmd_success_loops_ls(args, path)
    if command == "trace" and sub in ("ls", "review", "show", "feedback",
                                      "regress"):
        from minder_op import trace as trace_cli
        return {
            "ls": trace_cli.cmd_ls, "review": trace_cli.cmd_review,
            "show": trace_cli.cmd_show, "feedback": trace_cli.cmd_feedback,
            "regress": trace_cli.cmd_regress,
        }[sub](args, path)
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
    if command == "lessons" and sub == "reject":
        return _cmd_lessons_reject(args, path)
    if command == "lessons" and sub == "decisions":
        return _cmd_lessons_decisions(args, path)
    if command == "lessons" and sub == "injections":
        return _cmd_lessons_injections(args, path)
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
    if command == "capture":
        return _cmd_capture(args, path)
    if command == "scorecard":
        return _cmd_scorecard(args, path)
    if command == "export-stats":
        return _cmd_export_stats(args)
    if command == "weekly-summary":
        return _cmd_weekly_summary(args, path)
    if command in ("benchmark", "quality"):
        # 8C lives in its own module: cli.py had crossed the C2 line and
        # this block is a self-contained command table (issue #26).
        from minder_op import benchmark_cli
        if command == "benchmark":
            return benchmark_cli.cmd_benchmark(args)
        return benchmark_cli.cmd_quality(args)
    return EXIT_USAGE


# --- 8A: read commands --------------------------------------------------


def _cmd_status(path):
    from minder_op.format import kv
    from minder_op import schema as schema_mod
    info = queries.status(path)
    applied, latest = schema_mod.schema_state(path)
    lines = [("db_path", str(path)),
             ("schema_version", info["schema_version"]),
             ("schema_latest", latest),
             ("schema_installed", schema_mod.installed_version()),
             ("schema_behind", "yes" if applied < latest else "no")]
    lines += [("episodes", info["episodes"]),
              ("lessons_verified", info["lessons_verified"]),
              ("lessons_candidate", info["lessons_candidate"]),
              ("lessons_invalidated", info["lessons_invalidated"]),
              ("gaps_open", info["gaps_open"])]
    for label, n in info["consults_by_helpfulness"].items():
        lines.append((f"consults[{label}]", n))
    lines += [("classifier_shadow_rows", info["classifier_shadow_rows"]),
              ("decision_traces_rows", info["decision_traces_rows"])]
    lines += _retrieval_lines(info["retrieval"])
    for var in FLAG_VARS:
        lines.append((f"env:{var}", os.environ.get(var) or "(unset)"))
    kv(lines)
    print()
    print(DEFERRED)
    return EXIT_OK


def _retrieval_lines(retrieval):
    """Issue #20: the hit rate as named numbers, not a percentage a reader
    has to reverse-engineer, and never a 0 for a store that predates the
    injection ledger."""
    if not retrieval["available"]:
        return [("retrieval_asked", NOT_AVAILABLE),
                ("retrieval_hits", NOT_AVAILABLE),
                ("retrieval_misses", NOT_AVAILABLE),
                ("retrieval_hit_rate", NOT_AVAILABLE)]
    rate = retrieval["rate"]
    return [("retrieval_asked", retrieval["asked"]),
            ("retrieval_hits", retrieval["hits"]),
            ("retrieval_misses", retrieval["misses"]),
            ("retrieval_hit_rate",
             NOT_AVAILABLE if rate is None else f"{rate * 100:.1f}%")]


def _cmd_migrate(path):
    """Issue #26: close the gap the status screen just named. The plan
    is printed before and after, so an operator sees which versions
    landed rather than trusting a silent exit code.

    A store ahead of the answering runtime is refused rather than
    pushed: this process can only apply the migrations beside its own
    copy of minder, and writing objects it has no files for would leave
    the store stamped at a version its own runtime cannot reproduce."""
    from minder_op.format import kv
    from minder_op import schema as schema_mod
    before, latest = schema_mod.schema_state(path)
    installed = schema_mod.installed_version()
    if before > latest:
        print(f"error: the store is at v{before}, ahead of the migrations "
              f"this runtime ships (v{latest}). Nothing here can bring it "
              "up to date: point minder-op at the runtime that wrote those "
              "migrations (the installed share: "
              f"{schema_mod.share_dir()})", file=sys.stderr)
        return EXIT_USAGE
    _before, applied, after, latest = schema_mod.repair(path)
    kv([("db_path", str(path)),
        ("schema_before", before),
        ("schema_applied", ", ".join(f"v{v}" for v in applied) or "none"),
        ("schema_version", after),
        ("schema_latest", latest),
        ("schema_installed", installed)])
    if not applied:
        print("\nno pending migration: the store is already current "
              "for the migrations this runtime ships")
    if installed and after > installed:
        print(f"\nstale staged files: the store is now at v{after}, but "
              f"the installed share ({schema_mod.migrations_dir()}) ships "
              f"only v{installed} - re-run install.sh so the deployed "
              "runtimes have the same migrations", file=sys.stderr)
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


def _cmd_engine_status(args):
    from minder_op import engines
    rows = engines.status()
    if not rows:
        print("no engine registry configured")
        return EXIT_OK
    for r in rows:
        mark = "*" if r["active"] else " "
        unit = r["unit"] or "n/a"
        state = r["unit_state"] or "n/a"
        health = "healthy" if r["healthy"] else "unhealthy"
        print(f"{mark} {r['name']:<12} {r['upstream']}  unit={unit} "
              f"({state})  {health}")
    print("\n* = active engine; switch with: minder-op engine switch NAME --yes")
    return EXIT_OK


def _cmd_engine_switch(args):
    from minder_op import engines
    if not args.yes:
        print("refusing to restart engine units without --yes")
        return EXIT_USAGE
    result = engines.switch(args.name)
    if result["switched"]:
        print(f"switched {result['from']} -> {result['engine']} "
              f"(target healthy, config updated; backup at "
              f"minder.json.bak)")
    else:
        print(f"already on '{result['engine']}'; nothing to do")
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
    # Issue #13: is this lesson still doing anything? Read-only, and a
    # store without the ledger says so instead of printing a zero.
    try:
        injections = queries.lesson_injections(path, args.id, limit=5)
    except queries.DBError:
        print("\ninjection ledger: not available")
        return EXIT_OK
    print("\ninjections (most recent first)")
    fmt.table([{"when": r["ts"], "tier": r["tier"],
                "path": r["assist_mode"], "chars": r["chars_injected"],
                "session": fmt.safe(r["session_id"], 40)}
               for r in injections],
              [("when", "when"), ("tier", "tier"), ("path", "path"),
               ("chars", "chars"), ("session", "session")])
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
    from minder_memory import train_eval
    path = args.path or str(Path(__file__).resolve().parent.parent
                            / "minder_memory" / "exports"
                            / "training_candidates.jsonl")
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


def _cmd_capture(args, path):
    """Capture health: hook invocations vs persisted records."""
    from minder_op import capture
    report = capture.build(path, dsh_root=args.dsh_home,
                           window_hours=args.window_hours)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_capture_text(report))
    return EXIT_OK if report["ok"] else EXIT_USAGE


def _capture_text(report):
    lines = [f"minder capture health — last {report['window_hours']}h",
             f"state_dir: {report['state_dir']}", ""]
    cov = report["coverage"]
    ratio = cov["ratio"]
    lines.append(f"  coverage        "
                 f"{'n/a' if ratio is None else format(ratio, '.0%')}"
                 f"  ({cov['persisted']} persisted / {cov['invocations']} "
                 f"hook invocations in {cov['scanned']} session log(s)"
                 f"{', truncated' if cov['truncated'] else ''})")
    for session in cov["sessions"]:
        p50 = session.get("hook_p50_ms")
        lines.append(f"    {session['session_id'][:44]:44} "
                     f"{session['invocations']:>4} invocations  "
                     f"p50 {'-' if p50 is None else format(p50, '.0f')} ms")
    lines.append("")
    for store in report["stores"]:
        when = ("never" if store["age_s"] is None
                else f"{store['age']} ago")
        lines.append(f"  {store['status']:8} {store['name']:20} "
                     f"last write {when:10}  ({store['file']})")
    sink = report["sink"]
    lines.append("")
    lines.append(f"  sink            configured={sink['configured']} "
                 f"reachable={sink['reachable']} url={sink['url'] or '-'}")
    if report["sandbox"]:
        modes = ", ".join(f"{k}={v}"
                          for k, v in sorted(report["sandbox"].items()))
        lines.append(f"  sandbox modes   {modes}")
    lines.append("")
    if report["warnings"]:
        lines.append("warnings")
        for warning in report["warnings"]:
            lines.append(f"  - {warning}")
    else:
        lines.append("no warnings — capture looks healthy.")
    return "\n".join(lines)


def _cmd_scorecard(args, path):
    """The improvement scorecard (see docs/dsh-capture.md)."""
    from minder_op import scorecard
    report = scorecard.build(path, dsh_root=args.dsh_home,
                             window_hours=args.window_hours)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(scorecard.render_text(report))
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
    from minder_memory import task_context
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
    from minder_decision import routing
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
    from minder_memory import task_context
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
    from minder_memory import task_context
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
    from minder_memory import trading_protocol as tp
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
    from minder_memory import trading_protocol as tp
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
    from minder_memory import trading_protocol as tp
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
    from minder_memory import trading_protocol as tp
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
    if not args.yes:
        print("PLAN (dry — nothing written):")
        print(f"  holdout unlock for {args.family} by {args.actor} "
              "(recorded as a human_input_event)")
        print("re-run with --yes to record the decision")
        return EXIT_USAGE
    from minder_memory import task_context
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
    from minder_decision import routing
    from minder_decision.providers.null import NullClient
    provider = {"rules": routing.RulesRouteProvider,
                "null": NullClient}.get(args.provider)
    provider = provider() if provider else None
    if args.provider == "laya":
        from minder_decision.providers.laya import try_laya_client
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
    from minder_decision import routing
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
    from minder_decision import routing
    from minder_decision.providers.null import NullClient
    provider = {"rules": routing.RulesRouteProvider,
                "null": NullClient}.get(args.provider)
    provider = provider() if provider else None
    if args.provider == "laya":
        from minder_decision.providers.laya import try_laya_client
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
        from minder_op import benchmark_cli
        benchmark_cli.write_json_file(args.out, report)
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
    from minder_decision import routing
    report = routing.ambiguity_report(days=args.days, db_path=path)
    fmt.kv([(key, report[key]) for key in
            ("window_days", "sessions", "failure_events",
             "distinct_failure_keys", "family_shifts",
             "declared_boundaries")])
    print(report["note"])
    return EXIT_OK


# --- Phase 1 résumé slice: fact / wording / intent ------------------------


def _cmd_resume_assert(args, path):
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
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
    from minder_memory import resume_evidence
    now = args.now or None
    summary = resume_evidence.expire_intents(now=now, db_path=path)
    fmt.kv([("expired", summary["expired"]),
            ("note", "intent-scoped retention enforcement; career "
                     "history untouched")])
    return EXIT_OK


def _cmd_success_loops_ls(args, path):
    from minder_memory import success_guard
    rows = success_guard.list_success_loops(limit=args.limit,
                                            db_path=path)
    fmt.table([{"ts": r["ts"], "session": r["session_id"],
                "tool": r["tool"],
                "fingerprint": fmt.safe(r["action_fingerprint"], 32),
                "exit": r["exit_code"],
                "excerpt": fmt.safe(r["excerpt"], 48)} for r in rows],
              [("ts", "ts"), ("session", "session"), ("tool", "tool"),
               ("fingerprint", "fingerprint"), ("exit", "exit"),
               ("excerpt", "excerpt")])
    print()
    mode = success_guard.guard_mode()
    if mode == "block":
        print("mode=block: a repeat of an action that already looped is "
              "stopped (PreToolUse exit 2) and the model gets the directive")
    elif mode == "advisory":
        print("mode=advisory: the guard tells the model, it never blocks")
    else:
        print("mode=off: MINDER_SUCCESS_GUARD is not set, nothing is "
              "recorded; set it to advisory or block to enable")
    return EXIT_OK


# --- 8C: benchmark and quality -> minder_op/benchmark_cli.py ---


# --- 8B: explicit writes (always through memory APIs, always --yes) ------


def _refuse_without_yes(args, plan):
    if not getattr(args, "yes", False):
        print("PLAN (dry — nothing written):")
        print(f"  {plan}")
        print("re-run with --yes to apply")
        return True
    return False


def _cmd_lessons_invalidate(args, path):
    from minder_memory import lessons as memory_lessons
    if _refuse_without_yes(
            args, f"lessons invalidate {args.id} reason={args.reason!r}"
            f" diagnosis={args.diagnosis}"):
        return EXIT_USAGE
    row, status = memory_lessons.invalidate_lesson(
        args.id, args.reason, diagnosis=args.diagnosis, db_path=path)
    if not row:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", row["lesson_id"]), ("status", status),
            ("invalidated", row["valid_to"]),
            ("diagnosis", fmt.safe(row.get("invalidated_diagnosis"), 40)),
            ("reason", fmt.safe(args.reason, 200))])
    return EXIT_OK


def _cmd_lessons_reject(args, path):
    from minder_memory import lessons as memory_lessons
    if _refuse_without_yes(
            args, f"lessons reject {args.id} code={args.code}"
            f" note={args.note!r}"):
        return EXIT_USAGE
    row, status = memory_lessons.reject_candidate_lesson(
        args.id, args.code, note=args.note, db_path=path)
    if not row:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", row["lesson_id"]), ("status", status),
            ("code", args.code), ("note", fmt.safe(args.note, 200))])
    return EXIT_OK


def _cmd_lessons_decisions(args, path):
    """Issue #14: the queue's history by reason code. Read-only, and a
    store predating migration 016 says so instead of printing zeros."""
    try:
        if args.id:
            rows = queries.lesson_decisions(path, args.id, limit=args.limit)
        else:
            rows = queries.decision_counts(path)
    except queries.DBError:
        print("decision ledger: not available")
        return EXIT_OK
    if args.id:
        fmt.table([{"when": r["ts"], "action": r["action"],
                    "code": r["code"], "actor": r["actor"],
                    "note": fmt.safe(r["note"], 60)} for r in rows],
                  [("when", "when"), ("action", "action"),
                   ("code", "code"), ("actor", "actor"), ("note", "note")])
        return EXIT_OK
    fmt.table([{"action": r["action"], "code": r["code"], "n": r["n"]}
               for r in rows],
              [("action", "action"), ("code", "code"), ("n", "n")])
    return EXIT_OK


def _cmd_lessons_injections(args, path):
    """Issue #20: the ledger read side. Misses are their own labelled
    line, never a bucket that reads as a lesson with an empty id, and a
    store without the table says so instead of printing a zero rate."""
    from minder_memory import injections as ledger
    counts = ledger.injection_counts(db_path=path, limit=args.limit)
    if counts["misses"] is None:
        print("injection ledger: not available")
        return EXIT_OK
    fmt.table([{"lesson": r["lesson_id"], "injections": r["injections"],
                "last": r["last_ts"]} for r in counts["lessons"]],
              [("lesson", "lesson"), ("injections", "injections"),
               ("last", "last")])
    # The nothing case, spelled out: these rows are why the hit rate is
    # below 100%, and there is no lesson id to act on.
    print(f"\nmisses: {counts['misses']} (decisions that had no lesson to"
          " offer; not a lesson)")
    return EXIT_OK


def _cmd_lessons_promote(args, path):
    from minder_memory import lessons as memory_lessons
    if args.from_candidate:
        return _promote_candidate_lesson(args, path, memory_lessons)
    if not args.episode_id:
        print("error: lessons promote needs an EPISODE_ID, or --from-candidate "
              "with a LESSON_ID", file=sys.stderr)
        return EXIT_USAGE
    if not args.instruction:
        print("error: the following arguments are required: --instruction",
              file=sys.stderr)
        return EXIT_USAGE
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
    return _report_promotion(lesson, status)


def _promote_candidate_lesson(args, path, memory_lessons):
    """The candidate queue has to be closable from the same screen that
    lists it: the id comes from `lessons ls --status candidate`, the
    instruction is the distilled text unless the operator edits it, and the
    tests evidence is the source episode's (issue #10)."""
    lesson_id = args.episode_id
    if not lesson_id:
        print("error: --from-candidate needs a LESSON_ID", file=sys.stderr)
        return EXIT_USAGE
    if args.tests_passed:
        print("error: --from-candidate takes its tests evidence from the "
              "candidate's source episode; --tests-passed is not needed",
              file=sys.stderr)
        return EXIT_USAGE
    if _refuse_without_yes(
            args, f"lessons promote candidate={lesson_id} "
                  f"instruction={args.instruction!r} (adopt as verified)"):
        return EXIT_USAGE
    lesson, status = memory_lessons.adopt_candidate_lesson(
        lesson_id, instruction=args.instruction, actor="operator",
        db_path=path)
    return _report_promotion(lesson, status)


def _report_promotion(lesson, status):
    if not lesson:
        print(f"error: {status}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("lesson_id", lesson["lesson_id"]), ("status", status),
            ("lesson_status", lesson["status"]),
            ("instruction", fmt.safe(lesson["instruction"], 200)),
            ("source_episode", lesson["source_episode"])])
    return EXIT_OK


def _cmd_gaps_close(args, path):
    from minder_memory import skills as memory_skills
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
    # migrate is absent: creating or advancing the store is the whole
    # point of the command, so guarding on an existing readable schema
    # would refuse the one case it exists for.
    needs_db = args.command not in ("flags", "export-stats", "benchmark",
                                    "quality", "doctor", "capture",
                                    "scorecard", "trace", "engine")
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
