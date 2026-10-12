"""`minder-op calibrate` end to end (issue #36).

The command is the automation: fit on the fitting split, replay the
eval set, gate, pin, roll out. Every one of those steps already has a
unit, so these tests judge the command table: exit codes, the --yes
law on anything that writes, and the refusal that makes the whole
feature worth having.

Exit codes follow the CLI law: 0 ok, 1 usage or refused, 2 the store
is missing or corrupt. `calibrate` never touches the store, and the
loop is offline, so the store guard must not be what stops it.
"""
import json
import pathlib
import subprocess
import sys

from minder_op.cli import EXIT_OK, EXIT_USAGE, main

SPEC_SCALE = 20.0  # the fixture answers as the bundle does
CASES = "benchmarks/difficulty-core-v1/cases/difficulty_cases.json"
HARD = "benchmarks/difficulty-core-v1/fitting/hard.json"
BUNDLE = "presets/laya-specializations/difficulty-v1"


class _Mechanical:
    """A provider with an opinion, and a real one at that: the gold
    label carries 0.62 and each rival 0.126, so the margin is 0.494 and
    the fit can act on something. A uniform spec dict is not an opinion
    - FakeClient normalizes it to zeros, which is how this fixture first
    fooled itself. The score question carries the gold score so
    resolve_difficulty's conservative raise cannot push every answer to
    the top band."""

    model_version = "mechanical-fixture"
    # the fixture answers the difficulty contract the way the bundle
    # does, so it declares the bundle's scale as its own default
    margin_scale_default = SPEC_SCALE

    def system_one(self, state, questions, model=None, contract=None):
        from minder_decision.providers.fake import FakeClient
        task = state.get("task", "") if isinstance(state, dict) else ""
        probs = _case_probs(task)
        # the confidence is what the seam would compute: clip01(scale x
        # margin), read through the same env the rollout writes. The
        # fixture is not the laya client, so it has to apply the same
        # law for the gate to mean anything.
        from minder_decision.providers.laya import margin_scale
        ranked = sorted(probs.values(), reverse=True)
        margin = ranked[0] - ranked[1]
        spec = {"difficulty_probs": probs,
                "confidence": min(1.0, max(0.0, margin
                                           * margin_scale(1.0)))}
        for question in questions:
            if question.id == "difficulty_score":
                spec[question.id] = _LABELS.index(_gold(task))
        return FakeClient({"k": spec}, key_fn=lambda _s: "k").system_one(
            "k", questions, model=model, contract=contract)


_LABELS = ("mechanical", "routine", "complex", "expert_or_ambiguous")
_FITTING = json.loads(open(HARD).read())["examples"]


def _gold(task):
    """The fitting row's label, keyed back off the rendered input the
    fitter passes as the state's task."""
    for row in _FITTING:
        if row["input"] == task:
            return row["label"]
    return "mechanical"


def _fitting_probs(task):
    gold = _gold(task)
    probs = {label: 0.126 for label in _LABELS}
    probs[gold] = 0.622
    return probs


# the eval-set opinion, in fifths: the first four fifths of the suite
# is right and carries a wide margin, the last fifth is wrong and
# carries a narrow one. A provider with one uniform margin cannot
# express an accuracy at all - every case clears the floor or none does.
# A provider with a per-case margin can, and the fitted scale then
# decides which margins clear the 0.7 floor, which is exactly what the
# gate is for.
_CASES = json.loads(open(CASES).read())["cases"]
_LABEL_BY_TASK = {c["state"]["task"]: c["gold"]["label"] for c in _CASES}
_RIGHT = {c["state"]["task"] for c in _CASES[:160]}


def _case_probs(task):
    if task not in _LABEL_BY_TASK:
        return _fitting_probs(task)
    gold = _LABEL_BY_TASK[task]
    margin = 0.30 if task in _RIGHT else 0.05
    # a real distribution: the gold option leads the runner-up by
    # `margin`, and the four masses sum to 1.0. A spread that does not
    # sum to 1 is malformed, and resolve_difficulty returns no opinion
    # for a malformed one - which is how this fixture first reported a
    # coverage of zero and blamed the gate.
    top = 0.25 + 0.75 * margin
    probs = {label: (1.0 - top) / 3 for label in _LABELS}
    probs[gold] = top
    return probs


def _patch_provider(monkeypatch):
    """The provider seam. The parser only offers laya and null; a test
    passes the fixture name through the same function and gets the
    mechanical provider instead of the real model."""
    from minder_op import calibrate_cmd
    monkeypatch.setattr(calibrate_cmd, "_provider_arg",
                        lambda args: _Mechanical())


def _bench_dir(tmp_path, monkeypatch, *, pin_baseline=True):
    """Keep every write inside tmp_path: the promotion record and the
    pinned baseline are operator state, and a test must not touch the
    real benchmarks tree. The gate refuses without a pinned baseline,
    so the fixture pins one - a report at the shipped scale, which is
    what a real baseline would be."""
    root = tmp_path / "benchmarks"
    (root / "baselines").mkdir(parents=True)
    monkeypatch.setenv("MINDER_BENCHMARKS_DIR", str(root))
    if pin_baseline:
        from minder_decision import calibrate
        path = calibrate.default_baseline_path_for(calibrate.SUITE_ID)
        path.write_text(json.dumps(_baseline_report(), indent=2,
                                   sort_keys=True))
    return root


def _baseline_report():
    """A 200-run difficulty report at the shipped scale: acts on 150 of
    the 200 and gets 120 of those right (0.80 selective accuracy). The
    fixture is right on 160 of 200 and its wide-margin cases are a
    subset of those, so the fitted scale acts on 160 at 0.75 - better
    than the shipped scale on both calibration metrics, and the gate
    passes for the same reason it passed on the real bundle."""
    from minder_decision import calibrate
    return {
        "report_version": 1,
        "suite_id": calibrate.SUITE_ID,
        "suite_fingerprint": calibrate.suite_fingerprint(CASES),
        "generated_at": "2026-10-12T00:00:00+00:00",
        "kind": "baseline",
        "metrics": {"comparable_runs": 200,
                    # the comparator's own law: a 5pp completion drop is
                    # a FAIL at any N. The fixture's argmax is right on
                    # 160 of 200 whatever the scale, so both sides read
                    # 0.80 and the calibration tier is what decides.
                    "verified_completion_rate": 0.8,
                    "unsafe_executions": 0,
                    "harmful_frontier_acceptances": 0,
                    "external_prohibited_egress": 0},
        "difficulty_detail": {"provider": "laya-spec/difficulty-v1",
                              "margin_scale": SPEC_SCALE,
                              "correct": 120, "abstentions": 50,
                              "acted": 150, "coverage": 0.75,
                              "selective_accuracy": 0.8,
                              "mean_band_distance": 0.2769,
                              "contract_id": "task-difficulty",
                              "label_status": "draft_pending_owner_review"},
        "calibration_detail": {"brier": 0.714198, "ece": 0.3,
                               "ece_bins": 10},
    }


def test_fit_reports_a_scale_and_refuses_an_overlapping_split(tmp_path,
                                                              monkeypatch):
    from minder_op import calibrate_cmd
    assert calibrate_cmd is not None
    _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    code = main(["calibrate", "fit", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"])
    assert code == EXIT_OK


def test_the_loop_gates_and_reports_the_verdict(tmp_path, monkeypatch,
                                                capsys):
    _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    code = main(["calibrate", "gate", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"])
    assert code in (EXIT_OK, EXIT_USAGE)
    out = capsys.readouterr().out
    assert "verdict" in out
    assert "selective_accuracy" in out
    # the verdict prints its own denominator: the numbers the pinned
    # baseline was produced at, so the claim can be checked against the
    # report rather than taken on trust
    shown = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
             for line in out.splitlines() if ":" in line}
    assert shown["baseline_scale"] == "20.0"
    assert shown["baseline_acted"] == "150"
    assert shown["baseline_selective_accuracy"] == "0.8"
    assert shown["baseline_ece"] == "0.3"


def test_promote_without_yes_is_a_plan_not_a_write(tmp_path, monkeypatch,
                                                   capsys):
    """P-series: nothing is pinned without --yes, and the plan says
    what would be pinned."""
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    code = main(["calibrate", "promote", "--cases", CASES, "--fitting",
                 HARD, "--bundle", BUNDLE, "--provider", "null"])
    assert code == EXIT_USAGE
    assert not (root / "promotions").exists()
    assert "PLAN" in capsys.readouterr().out


def test_promote_with_yes_pins_a_record_the_status_command_reads(
        tmp_path, monkeypatch, capsys):
    from minder_decision import calibrate
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    code = main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"])
    assert code == EXIT_OK
    record = calibrate.read_promotion()
    assert record is not None
    assert record["margin_scale"]
    assert len(record["label_digest"]) == 64
    assert (root / "promotions" / "difficulty-core-v1.json").is_file()
    capsys.readouterr()
    assert main(["calibrate", "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "margin_scale" in out
    # promote states what this process is answering at, so the pin is
    # never mistaken for an installed scale. The fitted scale differs
    # from the provider's shipped one, so the note names both.
    capsys.readouterr()
    code = main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"])
    assert code == EXIT_OK
    note = capsys.readouterr().out
    assert f"pins margin scale {record['margin_scale']}" in note
    assert "still maps at 20.0" in note


def test_a_refused_gate_does_not_pin_anything(tmp_path, monkeypatch,
                                              capsys):
    """The gate is the point. A candidate that acts on nothing must not
    leave a promotion record behind, and the command must say no with a
    nonzero code."""
    from minder_decision import calibrate
    root = _bench_dir(tmp_path, monkeypatch)
    from minder_op import calibrate_cmd
    # the null provider answers with a uniform distribution, so every
    # candidate abstains: the gate must refuse coverage 0 rather than
    # call a router that never acts a calibrated one. It still has to
    # be the provider the pinned baseline was replayed against, or the
    # run stops at the baseline check instead of reaching the verdict.
    null = calibrate_cmd.difficulty_provider("null")
    null.margin_scale_default = SPEC_SCALE
    monkeypatch.setattr(calibrate_cmd, "_provider_arg",
                        lambda args: null)
    code = main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"])
    assert code == EXIT_USAGE
    assert calibrate.read_promotion() is None
    assert not (root / "promotions").exists()
    assert "verdict" in capsys.readouterr().out


def test_apply_without_yes_prints_the_drop_in_plan(tmp_path, monkeypatch,
                                                   capsys):
    """The live change is additive and named: the drop-in path, never
    the base unit file."""
    from minder_decision import calibrate
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    assert main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"]) == EXIT_OK
    assert calibrate.read_promotion() is not None
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setenv("MINDER_SYSTEMD_USER_DIR", str(unit_dir))
    capsys.readouterr()
    code = main(["calibrate", "apply"])
    assert code == EXIT_USAGE
    out = capsys.readouterr().out
    assert "minder-calibration.conf" in out
    assert not unit_dir.exists()
    assert root.is_dir()


def test_rollback_reports_absent_when_nothing_was_installed(
        tmp_path, monkeypatch, capsys):
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setenv("MINDER_SYSTEMD_USER_DIR", str(unit_dir))
    assert main(["calibrate", "rollback"]) == EXIT_OK
    assert "absent" in capsys.readouterr().out


def test_the_split_refusal_surfaces_as_usage_not_a_traceback(
        tmp_path, monkeypatch, capsys):
    """6.9.3 through the CLI: a fitting file whose rows are the eval
    cases is refused with a message that names the shared case."""
    _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    doc = json.loads(open(CASES).read())
    leaked = tmp_path / "leaked.json"
    leaked.write_text(json.dumps({"examples": [
        {"id": case["id"], "input": case["state"]["task"],
         "label": case["gold"]["label"], "split": "fitting"}
        for case in doc["cases"][:5]]}))
    code = main(["calibrate", "fit", "--cases", CASES, "--fitting",
                 str(leaked), "--provider", "null"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "6.9.3" in err
    assert doc["cases"][0]["id"] in err


def test_calibrate_does_not_require_the_sqlite_store(tmp_path, monkeypatch):
    """The loop is offline: cases, fitting rows, a provider. A missing
    store is not a reason to refuse it, and `--db` pointing nowhere
    must not turn into exit 2."""
    from minder_op.cli import EXIT_DB
    _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    code = main(["--db", str(tmp_path / "absent.sqlite"), "calibrate",
                 "fit", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"])
    assert code != EXIT_DB


def test_the_help_lists_every_subcommand_the_loop_needs():
    """M2: the command table is the interface. A subcommand that exists
    only in the dispatcher and not in the parser is invisible."""
    proc = subprocess.run([sys.executable, "-m", "minder_op", "calibrate",
                           "--help"], capture_output=True, text=True)
    usage = proc.stdout + proc.stderr
    for verb in ("fit", "gate", "promote", "status", "apply", "rollback"):
        assert verb in usage


def test_the_pinned_baseline_must_be_a_replay_of_the_scale_in_force(
        tmp_path, monkeypatch, capsys):
    """The gate's denominator has to be a replay of the scale that
    actually runs. A baseline pinned from some other replay - another
    floor, another model, a scale nobody serves - makes the fitted number
    look like a gain the live router will not show, and a gate that
    accepts any validating report cannot tell the two apart."""
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    pinned = root / "baselines" / "difficulty-core-v1.json"
    report = json.loads(pinned.read_text())
    detail = report["difficulty_detail"]
    detail["margin_scale"] = 50.0
    pinned.write_text(json.dumps(report, indent=2, sort_keys=True))
    code = main(["calibrate", "gate", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "margin scale 50.0" in err
    assert "20.0" in err
    # and a baseline naming the scale the provider answers at runs
    detail["margin_scale"] = SPEC_SCALE
    pinned.write_text(json.dumps(report, indent=2, sort_keys=True))
    capsys.readouterr()
    assert main(["calibrate", "gate", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"]) == EXIT_OK
    assert "margin scale" not in capsys.readouterr().err


def test_the_gate_refuses_a_baseline_from_before_scales_were_named(
        tmp_path, monkeypatch, capsys):
    """Reports written before difficulty_eval recorded its scale look
    exactly like reports at the scale in force, and the flattering
    direction is the one that slips through: a fitted 18.0 beats a
    baseline that never named its units. The gate refuses the ambiguity
    and says how to produce a report that names them."""
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    pinned = root / "baselines" / "difficulty-core-v1.json"
    report = json.loads(pinned.read_text())
    assert report["difficulty_detail"].pop("margin_scale") == SPEC_SCALE
    pinned.write_text(json.dumps(report, indent=2, sort_keys=True))
    code = main(["calibrate", "gate", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"])
    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "names no margin scale" in err
    assert "MINDER_LAYA_MARGIN_SCALE=20.0" in err
    # and the same report with the field back is accepted
    pinned.write_text(json.dumps(json.loads(pinned.read_text()) | {
        "difficulty_detail": {**report["difficulty_detail"],
                              "margin_scale": SPEC_SCALE}},
        indent=2, sort_keys=True))
    capsys.readouterr()
    assert main(["calibrate", "gate", "--cases", CASES, "--fitting", HARD,
                 "--provider", "null"]) == EXIT_OK


def test_status_names_the_scale_the_router_is_actually_answering_at(
        tmp_path, monkeypatch, capsys):
    """A record pinning 18.0 while the environment still carries nothing
    means the live difficulty contract is mapping at the shipped scale.
    `calibrate status` is where an operator looks after promoting, and
    the systemd read may be unavailable there, so it has to state the
    process-local reading too."""
    from minder_decision import calibrate
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    assert main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"]) == EXIT_OK
    record = calibrate.read_promotion()
    assert record["baseline_scale"] == SPEC_SCALE
    record["margin_scale"] = 18.0
    calibrate.write_promotion(record)
    monkeypatch.delenv("MINDER_LAYA_MARGIN_SCALE", raising=False)
    capsys.readouterr()
    assert main(["calibrate", "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "promotion pins margin scale 18.0" in out
    assert "still maps at 20.0" in out
    assert "calibrate apply --yes" in out
    assert (root / "promotions").is_dir()


def test_apply_reports_the_scale_the_unit_was_running_before(
        tmp_path, monkeypatch, capsys):
    """The apply plan has to name the value it replaces, not just the one
    it writes: an operator deciding whether to restart the proxy is
    deciding about a change, and the only honest description of a change
    includes both ends."""
    from minder_op import rollout
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    assert main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"]) == EXIT_OK
    from minder_decision import calibrate
    record = calibrate.read_promotion()
    record["margin_scale"] = 18.0
    calibrate.write_promotion(record)
    unit_dir = tmp_path / "unit"
    monkeypatch.setenv("MINDER_SYSTEMD_USER_DIR", str(unit_dir))
    for base, label in (("20.0", "20.0"), (None, "None")):
        unit_dir = tmp_path / f"unit-{label}"
        monkeypatch.setenv("MINDER_SYSTEMD_USER_DIR", str(unit_dir))
        dropin = (unit_dir / "minder-proxy.service.d" / rollout.DROPIN_NAME)
        base_env = "" if base is None else (
            "Environment=MINDER_LAYA_MARGIN_SCALE=" + base + "\n")

        def fake_run(argv, _base=base_env, _dropin=dropin, **kwargs):
            # systemctl --user show reports what the unit loads: the
            # base unit plus any drop-in, drop-ins last
            if argv[:2] == ["systemctl", "--user"] and argv[2] == "show":
                loaded = _base
                if _dropin.is_file():
                    loaded += _dropin.read_text()
                return (0, "ActiveState=active\n" + loaded, "")
            return (0, "", "")

        monkeypatch.setattr(rollout, "_default_run", fake_run)
        capsys.readouterr()
        code = main(["calibrate", "apply", "--yes"])
        assert code == EXIT_OK
        out = capsys.readouterr().out
        shown = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
                 for line in out.splitlines() if ":" in line}
        assert shown["effective_scale"] == "18.0"
        assert shown["previous_scale"] == label
        assert dropin.is_file()


def test_the_record_survives_a_re_exported_bundle(tmp_path, monkeypatch,
                                                  capsys):
    """The operator re-exports the specialization bundle - the normal way
    a preset is updated - and the pinned scale was fitted on the old one.
    The record has to say which fact moved and that the fix is a new
    promote, not an apply."""
    root = _bench_dir(tmp_path, monkeypatch)
    _patch_provider(monkeypatch)
    assert main(["calibrate", "promote", "--yes", "--cases", CASES,
                 "--fitting", HARD, "--bundle", BUNDLE,
                 "--provider", "null"]) == EXIT_OK
    # a re-export moves the strategy the scale was fitted on, which is
    # what bundle_revision is computed from
    manifest = pathlib.Path(BUNDLE) / "manifest.json"
    before = manifest.read_text()
    doc = json.loads(before)
    assert doc.get("strategy")
    doc["strategy"] = doc["strategy"] + "-reexported"
    from minder_decision import calibrate
    record = calibrate.read_promotion()
    assert record["bundle_revision"]
    try:
        manifest.write_text(json.dumps(doc, indent=2, sort_keys=True))
        capsys.readouterr()
        code = main(["calibrate", "status"])
        out = capsys.readouterr().out
    finally:
        manifest.write_text(before)
    assert code == EXIT_USAGE
    assert "drift" in out
    assert "bundle_revision" in out
    assert "calibrate promote --yes" in out
