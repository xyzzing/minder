"""The offline replay commands (routes eval/replay, difficulty replay).

One command table for the three decision replays: each resolves a provider,
runs the offline replay, and prints the 8C-schema report. Moved out of cli.py
under C2 (the file had crossed 400 lines again); behaviour is unchanged.
Exit codes follow the CLI law: 0 ok, 1 usage or refused.
"""
import json
import sys

from minder_op import format as fmt

EXIT_OK = 0
EXIT_USAGE = 1

def cmd_routes_eval(args, path):
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


def cmd_routes_ls(args, path):
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


def cmd_routes_replay(args, path):
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


def cmd_difficulty_replay(args, path):
    from minder_decision import difficulty_eval
    provider = None
    if args.provider == "laya":
        from minder_decision.providers.laya import try_laya_client
        provider = try_laya_client()
        if provider is None:
            print("error: laya provider unavailable (package missing or "
                  "no recognised surface; MINDER_LAYA_DEVICE/MINDER_LAYA_"
                  "MODEL/MINDER_LAYA_SPECIALIZATION configure it)",
                  file=sys.stderr)
            return EXIT_USAGE
    try:
        report = difficulty_eval.replay_cases(args.cases, provider=provider,
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
        detail = report["difficulty_detail"]
        cal = report["calibration_detail"]
        fmt.kv([("provider", detail["provider"]),
                # the units of every confidence number below this
                ("margin_scale", detail.get("margin_scale")),
                ("confidence_floor", detail.get("confidence_floor")),
                ("correct", detail["correct"]),
                ("abstentions", detail["abstentions"]),
                ("acted", detail["acted"]),
                ("coverage", detail["coverage"]),
                ("selective_accuracy", detail["selective_accuracy"]),
                ("mean_band_distance", detail["mean_band_distance"]),
                ("brier", cal["brier"]),
                ("ece", cal["ece"]),
                ("label_status", detail["label_status"])])
        if args.out:
            print(f"report written: {args.out}")
        print("replay is offline; reports pin via "
              "benchmark baseline create --yes")
    return EXIT_OK
