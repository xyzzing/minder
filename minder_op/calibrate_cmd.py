"""`minder-op calibrate` - the confidence-scale loop as one command
(issue #36).

fit: score the fitting split with the provider, sweep margin scales,
report the best one. gate: fit, then replay the eval set at that scale
and print the promotion verdict. promote: the same, and pin the record
when the verdict is PASS. status: what is pinned, what is effective,
what has drifted. apply: install the pinned scale as a systemd drop-in.
rollback: take it back off.

Two laws the command exists to enforce. prd-v0.10 6.9.3: the fitting
split and the eval set are disjoint, and the pair is refused when they
overlap - the error message names the shared case. And nothing writes
without --yes, including the live rollout, which is additive (a
drop-in, never an edit of the base unit) and reversible with
`calibrate rollback`.

Exit codes follow the CLI law: 0 ok, 1 usage or refused, 2 the store is
missing or corrupt. The loop is offline and never touches the store, so
it is exempt from the store guard.
"""
import os
import sys

from minder_decision import calibrate
from minder_core.comparator import VERDICT_PASS
from minder_op import format as fmt

EXIT_OK = 0
EXIT_USAGE = 1

DEFAULT_CASES = "benchmarks/difficulty-core-v1/cases/difficulty_cases.json"
DEFAULT_FITTING = "benchmarks/difficulty-core-v1/fitting/hard.json"
DEFAULT_BUNDLE = "presets/laya-specializations/difficulty-v1"


def difficulty_provider(name, *, bundle=None):
    """The provider under calibration, by name.

    `laya` is the real one and needs the package plus the bundle env the
    provider reads; `null` is the trivial never-acts baseline, which the
    gate refuses on purpose. Kept as one function so a test can stand a
    fixture provider in without touching the command table.
    """
    if name in ("null", "fixture"):
        # `fixture` is the seam a test stands in through this function;
        # the parser never offers it, so a typo there is a usage error.
        from minder_decision.providers.null import NullClient
        return NullClient()
    if bundle:
        # the specialization seam reads this at build time; setting it
        # here is how `--bundle` reaches the provider that answers the
        # difficulty contract
        os.environ["MINDER_LAYA_SPECIALIZATION"] = str(bundle)
    from minder_decision.providers.laya import try_laya_client
    client = try_laya_client()
    if client is None:
        raise ValueError("laya provider unavailable (package missing or no "
                         "recognised surface; MINDER_LAYA_DEVICE/"
                         "MINDER_LAYA_MODEL/MINDER_LAYA_SPECIALIZATION "
                         "configure it)")
    return client


def _provider_arg(args):
    """Resolve --provider, turning an unavailable model into a usage
    error rather than a traceback."""
    if args.provider in ("null", "none"):
        return difficulty_provider("null")
    if args.provider == "laya":
        return difficulty_provider("laya", bundle=args.bundle)
    raise ValueError(f"unknown provider {args.provider!r} "
                     "(laya, null)")


def _run_loop(args):
    """fit + replay + verdict, with the inputs every verb needs. Raises
    ValueError for anything operator-fixable: a leaked split, a missing
    provider, a bad path."""
    rows = calibrate.load_fitting_rows(args.fitting)
    return calibrate.fit_and_gate(
        cases_path=args.cases, fitting_rows=rows,
        provider=_provider_arg(args), bundle_path=args.bundle,
        floor=args.floor)


def _print_result(result, args):
    fit, gate = result["fit"], result["gate"]
    fmt.kv([("suite", calibrate.SUITE_ID),
            ("suite_fingerprint", result["suite_fingerprint"][:12]),
            ("label_status", result["label_status"] or "unknown"),
            ("fitting_rows", fit["rows"]),
            ("scales_tried", fit["scales_tried"]),
            ("floor", fit["floor"]),
            ("margin_scale", fit["scale"]),
            ("fit_acted", fit["acted"]),
            ("fit_coverage", fit["coverage"]),
            ("fit_selective_accuracy", fit["selective_accuracy"]),
            ("fit_ece", fit["ece"])])
    detail = result["candidate"].get("difficulty_detail") or {}
    cal = result["candidate"].get("calibration_detail") or {}
    fmt.kv([("eval_runs", result["candidate"]["metrics"]["comparable_runs"]),
            # the denominator names its own scale: a verdict of "18.0
            # beat it" is only checkable next to the number it beat
            ("baseline_scale", result.get("baseline_scale")),
            ("baseline_acted", (result["baseline"].get("difficulty_detail")
                                or {}).get("acted")),
            ("baseline_coverage", (result["baseline"].get(
                "difficulty_detail") or {}).get("coverage")),
            ("baseline_selective_accuracy", (result["baseline"].get(
                "difficulty_detail") or {}).get("selective_accuracy")),
            ("baseline_ece", (result["baseline"].get(
                "calibration_detail") or {}).get("ece")),
            ("eval_acted", detail.get("acted")),
            ("eval_coverage", detail.get("coverage")),
            ("selective_accuracy", detail.get("selective_accuracy")),
            ("ece", cal.get("ece"))])
    print(f"verdict: {gate['verdict']}")
    for reason in gate["reasons"]:
        print(f"  - {reason}")
    return gate


def cmd_calibrate(args):
    verb = getattr(args, "calibrate_command", "") or ""
    if verb == "status":
        return _status(args)
    if verb in ("apply", "rollback"):
        return _rollout(verb, args)
    if verb not in ("fit", "gate", "promote"):
        print("error: calibrate needs fit, gate, promote, status, apply or "
              "rollback", file=sys.stderr)
        return EXIT_USAGE
    try:
        result = _run_loop(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if verb == "fit":
        fit = result["fit"]
        fmt.kv([("fitting_rows", fit["rows"]),
                ("floor", fit["floor"]),
                ("margin_scale", fit["scale"]),
                ("fit_acted", fit["acted"]),
                ("fit_coverage", fit["coverage"]),
                ("fit_selective_accuracy", fit["selective_accuracy"]),
                ("fit_ece", fit["ece"]),
                ("reason", fit["reason"])])
        print(f"candidate: MINDER_LAYA_MARGIN_SCALE={fit['scale']}")
        print("fit sees only the fitting split; gate it on the eval set "
              "with: minder-op calibrate gate")
        return EXIT_OK
    gate = _print_result(result, args)
    if verb == "gate":
        return EXIT_OK if gate["verdict"] == VERDICT_PASS \
            else EXIT_USAGE
    return _promote(args, result, gate)


def _promote(args, result, gate):
    plan = (f"pin margin scale {result['fit']['scale']} for "
            f"{calibrate.SUITE_ID} (bundle {args.bundle}, gate "
            f"{gate['verdict']}) -> {calibrate.promotion_path()}")
    if not getattr(args, "yes", False):
        print("PLAN (dry - nothing written):")
        print(f"  {plan}")
        print("re-run with --yes to pin the record")
        return EXIT_USAGE
    if gate["verdict"] != VERDICT_PASS:
        print(f"error: gate verdict is {gate['verdict']}, refusing to pin a "
              "scale that did not pass the promotion gate", file=sys.stderr)
        return EXIT_USAGE
    record = calibrate.promotion_record(
        scale=result["fit"]["scale"], bundle_path=args.bundle,
        cases_path=args.cases, fit=result["fit"], gate=gate,
        baseline=result["baseline"])
    path = calibrate.write_promotion(record)
    from minder_op import rollout
    fmt.kv([("promotion", str(path)),
            ("margin_scale", record["margin_scale"]),
            ("baseline_scale", record["baseline_scale"]),
            ("confidence_source", record["confidence_source"]),
            ("confidence_floor", record["confidence_floor"]),
            ("label_digest", record["label_digest"][:12]),
            ("bundle_revision", record["bundle_revision"][:12]),
            ("note", "install it with: minder-op calibrate apply --yes; "
                     "doctor reads this record")])
    note = rollout.live_scale_note(record)
    if note:
        print(note)
    return EXIT_OK


def _status(args):
    from minder_op import rollout
    record = calibrate.read_promotion()
    if record is None:
        print("no promotion record: run minder-op calibrate promote --yes")
        return EXIT_OK
    drift = calibrate.record_drift(record, cases_path=record.get("cases_path"),
                                   bundle_path=record.get("bundle_path"))
    fmt.kv([("promotion", str(calibrate.promotion_path())),
            ("suite", record.get("suite_id")),
            ("margin_scale", record.get("margin_scale")),
            ("confidence_floor", record.get("confidence_floor")),
            ("confidence_source", record.get("confidence_source")),
            ("promoted_at", record.get("promoted_at")),
            ("bundle_path", record.get("bundle_path")),
            ("gate", (record.get("gate") or {}).get("verdict")),
            ("drift", ", ".join(drift) if drift else "none")])
    _unused, status, detail = rollout.check(
        _unit(args), pinned_scale=rollout.record_scale(record))
    print(f"{status}: {detail}")
    note = rollout.live_scale_note(record)
    if note:
        print(note)
    if drift:
        print("the pinned facts moved: re-run "
              "`minder-op calibrate promote --yes`")
        return EXIT_USAGE
    return EXIT_OK


def _rollout(verb, args):
    from minder_op import rollout
    unit = _unit(args)
    if verb == "rollback":
        try:
            result = rollout.rollback(unit)
        except rollout.RolloutError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
        fmt.kv([("status", result["status"]),
                ("effective_scale", result.get("effective_scale"))])
        return EXIT_OK
    record = calibrate.read_promotion()
    scale = rollout.record_scale(record)
    if scale is None:
        print("error: no promoted margin scale to apply; run "
              "`minder-op calibrate promote --yes` first", file=sys.stderr)
        return EXIT_USAGE
    plan_row = rollout.plan(unit, margin_scale=scale)
    if not getattr(args, "yes", False):
        print("PLAN (dry - nothing written):")
        print(f"  {rollout.render_plan(plan_row)}")
        note = rollout.live_scale_note(record)
        if note:
            print(f"  {note}")
        print("additive: a drop-in beside the unit, never an edit of it; "
              "undo with `minder-op calibrate rollback`")
        return EXIT_USAGE
    try:
        result = rollout.apply(plan_row)
    except rollout.RolloutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([("status", result["status"]),
            ("dropin", plan_row["dropin"]),
            # both ends of the change: a restart decision is a decision
            # about a delta, and None here means the unit ran the
            # provider's shipped scale rather than a promoted one
            ("previous_scale", result.get("previous_scale")),
            ("effective_scale", result.get("effective_scale")),
            ("note", "confirm with: minder-op doctor; undo with "
                     "`minder-op calibrate rollback`")])
    return EXIT_OK


def _unit(args):
    import minder
    return getattr(args, "unit", None) or minder.PROXY_UNIT
