"""`minder-op benchmark` and `minder-op quality` (PRD v0.9 8C, 9D).

Manifests, schemas, the comparator and the controlled local runner behind
one command table. Nothing here launches an agent unless the operator
passed the runner's two explicit flags; `run` without them prints a plan
and refuses. Exit codes follow the CLI law: 0 ok, 1 usage or refused.
"""
import json
import os
import sys

from minder_op import format as fmt

EXIT_OK = 0
EXIT_USAGE = 1

# --- 8C: benchmark foundation (manifests/reports/comparator; no run) -----




def cmd_quality(args):
    """quality assess / quality adapters ls (PRD v0.9 9D, 6.8)."""
    from minder_quality import adapters as adapters_mod
    from minder_core import diffmetrics

    if getattr(args, "quality_command", "") == "adapters":
        for adapter in adapters_mod.load().values():
            _version, status = adapters_mod.probe(adapter)
            print(f"{adapter['id']:12s} {status:28s} "
                  f"pin={adapter.get('pin', '*')}")
        return 0

    record = diffmetrics.measure(args.pre, args.post,
                                 allowed_paths=args.allowed or None)
    pre_files = diffmetrics._py_files(args.pre)
    post_files = diffmetrics._py_files(args.post)
    touched_set = set(record["diff"]["scope_violations"])
    for rel in set(pre_files) | set(post_files):
        pre_text = pre_files[rel].read_text(errors="replace") \
            if rel in pre_files else ""
        post_text = post_files[rel].read_text(errors="replace") \
            if rel in post_files else ""
        if pre_text != post_text:
            touched_set.add(rel)
    adapter_findings, statuses = adapters_mod.assess_touched(
        args.pre, args.post, sorted(touched_set))
    record["new_findings"] += adapter_findings
    record["counts"]["blocking"] += sum(
        1 for f in adapter_findings if f["severity"] == "blocking")
    record["counts"]["advisory"] += sum(
        1 for f in adapter_findings if f["severity"] == "advisory")
    record["analyzers"].update(statuses)

    if getattr(args, "json", False):
        print(json.dumps(record, indent=2))
        return 0
    diff = record["diff"]
    print(f"quality: {args.pre} -> {args.post}")
    print(f"  files +{diff['files_added']}/-{diff['files_deleted']} "
          f"touched {diff['files_touched']}, lines +{diff['lines_added']}"
          f"/-{diff['lines_removed']} (net {diff['net_lines']})")
    if diff["scope_violations"]:
        print(f"  scope violations: {', '.join(diff['scope_violations'])}")
    for finding in record["new_findings"]:
        print(f"  [{finding['severity']}] {finding['rule']} "
              f"{finding['path']}:{finding['line']} "
              f"({finding['source']})")
    for name, status in sorted(record["analyzers"].items()):
        print(f"  analyzer {name}: {status}")
    print(f"  blocking {record['counts']['blocking']}, "
          f"advisory {record['counts']['advisory']}")
    return 0


def cmd_benchmark(args):
    from minder_op import benchmark as bench
    try:
        if args.bench_command == "list":
            return bench_list(bench)
        if args.bench_command == "validate":
            return bench_validate(bench, args.suite)
        if args.bench_command == "run":
            return bench_run(bench, args)
        if args.bench_command == "compare":
            return bench_compare(bench, args.baseline, args.candidate,
                                  args.json)
        if args.bench_command == "baseline" and \
                args.bench_subcommand == "create":
            return bench_baseline_create(bench, args.report, args.out,
                                          args.yes)
    except bench.BenchmarkError as exc:  # load/validate refused
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


def bench_list(bench):
    fmt.table([{"suite": r["suite_id"], "ver": r["manifest_version"],
                "tasks": r["tasks"], "fp": r["fingerprint"],
                "status": r["status"]} for r in bench.list_suites()],
              [("suite", "suite"), ("ver", "ver"), ("tasks", "tasks"),
               ("fp", "fp"), ("status", "status")])
    print()
    print("8C foundation: manifests + schemas + comparator; execution "
          "arrives with the 8D controlled local runner")
    return EXIT_OK


def bench_validate(bench, suite_id):
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


def bench_run(bench, args):
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
        return bench_execute(bench, args)
    if not args.dry_run:
        from minder_op import runner
        print(f"error: {runner.REQUIRED_FLAGS_MSG}", file=sys.stderr)
        return EXIT_USAGE
    manifest = bench_validate_suite(bench, args.suite)
    print(json.dumps(bench.dry_run_plan(manifest), indent=2,
                     sort_keys=True))
    return EXIT_OK


def bench_validate_suite(bench, suite_id):
    """Load + validate a suite manifest; exit cleanly with reasons."""
    manifest = bench.read_manifest(suite_id)
    errors = bench.validate_manifest(manifest, bench.suite_dir(suite_id))
    if errors:
        for error in errors:
            print(f"invalid: {error}", file=sys.stderr)
        raise bench.BenchmarkError(f"suite {suite_id} failed validation")
    return manifest


def bench_execute(bench, args):
    from minder_op import runner
    manifest = bench_validate_suite(bench, args.suite)
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
        write_json_file(args.out, report)
    if any(entry["status"] == "timeout" for entry in entries):
        # a killed run must leave a failed report behind, not just
        # terminal scrollback
        failed_path = args.out or bench.default_failed_report_path(
            report["suite_id"])
        write_json_file(failed_path, report)
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


def write_json_file(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def bench_compare(bench, baseline_path, candidate_path, as_json):
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


def bench_baseline_create(bench, report_path, out_arg, yes):
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
