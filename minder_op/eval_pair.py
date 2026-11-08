"""The paired control-vs-treatment lesson ablation, issues #88 and windtunnel#16.

Question: does a delivered verified lesson actually change outcomes?
Design (measured seam map, .sweeps/heartbeat/eval-pair-map.md): one
generated failure-family suite - N cases, each a distinct failure key
inside one family, each a minimal buggy lookup plus its visible test and
a holdout the solver never sees - run TWICE through the 9E quality
driver, whose writes are the overlay the 8D integrity law verifies:

- control: the arm's store holds the episodes and attempts, no lesson;
- treatment: the same seed plus one verified lesson;
- MINDER_ASSIST=retrieve in BOTH arms, so the control's miss rows are
  the pair's denominator - the arms differ by the lesson row, never by
  the flag;
- two separate stores: the family retrieval tier leaks a treatment
  lesson into control cases sharing one store (measured).

The producer is scripted (it applies the lesson's fix only when the
digest carried a VERIFIED LESSON), so the pair measures the lesson
CHANNEL end to end - lesson in, verified task out, under the real
integrity law. It does not measure how well a real coding agent uses
the lesson; the report states that limitation (FR-7: no lone
"improved" badge).

Scoring mirrors windtunnel's exact McNemar (sweep_report.py:39) here
because minder imports no bird; the mews tripwire cross-checks the two
implementations on shared vectors. Lesson ids are random per run, so
the eval's lesson fingerprint is content-only (failure_key +
instruction + anti_pattern).
"""
import hashlib
import json
import math
import os
import sys
from pathlib import Path

FAMILY = "keyerror"
CASES_DEFAULT = 20
REPEATS_DEFAULT = 3
THRESHOLD_ATTEMPTS = 3
LESSON_INSTRUCTION = ("give the settings lookup a .get() default before"
                      " retrying it")
LESSON_ANTI_PATTERN = "do not retry the same lookup unchanged"

_TASK_PY = '''"""The case fixture: a settings lookup without a default."""

settings = {{"present": "value"}}


def get_setting(key):
    return settings[key]
'''

_TEST_PY = '''"""Visible test: fails on the buggy lookup, passes on the fix."""
import task


def test_missing_key_returns_the_default():
    assert task.get_setting("{missing}") == "default"
'''

_HOLDOUT_PY = '''"""Holdout: a different key the solver never saw backstops gaming.
Imports via PYTHONPATH: the verifier stages task.py beside the work
copy and points the holdout run at it (driver._run_holdout)."""
import task


def test_holdout_key_returns_the_default():
    assert task.get_setting("{other}") == "default"
'''

_PRODUCER = '''"""Scripted producer (the 9E driver's argv): the pair's only
behavioural difference between arms is whether minder injected a
verified lesson - with one it applies the lesson's fix, without one it
leaves the bug. Runs under the driver's scrubbed env; minder's path and
the arm's store arrive via MEWS_MINDER_ROOT and MINDER_EVAL_DB."""
import json
import os
import sys

sys.path.insert(0, os.environ["MEWS_MINDER_ROOT"])
DBP = os.environ["MINDER_EVAL_DB"]
REPO = "."

log = []


def failure_event():
    import importlib.util
    spec = importlib.util.spec_from_file_location("task", "task.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.get_setting("missing_key")
        response = "no error"
    except Exception as exc:  # noqa: BLE001 - the failure is the signal
        response = f"{type(exc).__name__}: {exc}"
    return {"session_id": "s-producer", "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "repo": REPO,
            "tool_input": {"command": "python -c get_setting('missing_key')"},
            "tool_response": response}


def main():
    from minder_memory import from_hook, policy as memory_policy
    ev = failure_event()
    for _ in range(3):
        from_hook.record(ev, db_path=DBP)
    out = memory_policy.evaluate(
        dict(ev, hypothesis="the lookup has no default branch"),
        {"action": "think", "level": 1, "digest": "[minder] warden"},
        db_path=DBP, repo=REPO)
    digest = (out or {}).get("digest") or ""
    delivered = "VERIFIED LESSON" in digest
    if delivered:
        src = open("task.py").read()
        open("task.py", "w").write(
            src.replace("settings[key]",
                        'settings.get(key, "default")'))
        log.append("producer applied the lesson fix")
    log.append(f"assist {{(out or {{}}).get('assist')}} lesson {{delivered}}")
    print("; ".join(log))


main()
'''


def mcnemar_exact(pairs):
    """Two-sided exact McNemar over (control_pass, treatment_pass)
    pairs - the mirror of windtunnel's sweep_report.mcnemar_exact; the
    mews tripwire cross-checks the two on shared vectors."""
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if not x and y)
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1))
    return min(1.0, 2.0 * tail / 2 ** n)


def lesson_content_digest(failure_key, instruction, anti_pattern):
    """Content-only fingerprint: lesson_id is random per run."""
    core = json.dumps([failure_key, instruction, anti_pattern],
                      sort_keys=True)
    return hashlib.sha256(core.encode()).hexdigest()


def generate_suite(out_dir, family=FAMILY, cases=CASES_DEFAULT):
    """Write the generated suite: N cases, each a distinct missing-key
    symbol so each gets its own failure key inside the family."""
    suite_id = f"eval-pair-{family}-v1"
    suite_dir = Path(out_dir) / suite_id
    tasks = []
    for i in range(cases):
        rel = f"tasks/case-{i:02d}"
        task_dir = suite_dir / rel
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.py").write_text(
            _TASK_PY.format(), encoding="utf-8")
        (task_dir / "test_task.py").write_text(
            _TEST_PY.format(missing=f"missing_{i}"), encoding="utf-8")
        (task_dir / "holdout_test.py").write_text(
            _HOLDOUT_PY.format(other=f"other_{i}"), encoding="utf-8")
        tasks.append({
            "task_id": f"case-{i:02d}", "tier": 3, "family": family,
            "title": f"Eval pair case {i:02d}: missing-key default",
            "fixtures": [f"{rel}/task.py", f"{rel}/test_task.py"],
            "holdout": [f"{rel}/holdout_test.py"],
            "expected": "tests_pass",
            "forbidden": ["network", "browser", "broker", "ssh",
                          "package_install", "frontier_call",
                          "writes_outside_sandbox", "dsh_gui"],
            "allowed_paths": [f"{rel}/task.py"],
            "runner": {"kind": "pytest", "entry": ["test_task.py"],
                       "expected_tests": 1, "allow_skips": 0},
        })
    manifest = {
        "manifest_version": 1, "suite_id": suite_id,
        "title": f"Eval pair suite - {family}",
        "description": ("Generated paired-ablation suite: distinct"
                        " missing-key cases, one family."),
        "tiers": [3], "tasks": tasks,
    }
    (suite_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    return suite_dir, manifest


def _seed_arm(db_path, with_lesson):
    """One arm's store: the failed episode; the treatment also gets the
    verified lesson (the promotion law is exercised, not bypassed)."""
    from minder_memory import db as _db, lessons as memory_lessons, store
    from minder_memory.canonicalise import failure_key as _canon_fkey
    from minder_memory import from_hook

    if Path(db_path).exists():
        Path(db_path).unlink()
    _db.connect(db_path).close()
    norm = from_hook.to_event({"session_id": "s-producer",
                               "hook_event_name": "PostToolUse",
                               "tool_name": "Bash", "repo": ".",
                               "tool_input": {"command":
                                              "python -c get_setting"},
                               "tool_response": "KeyError: 'missing_key'"})
    real_key = _canon_fkey(norm, ".")
    ep_id, _ = store.open_episode(dict(norm, failure_key=real_key),
                                  db_path=db_path)
    lesson_id = None
    if with_lesson:
        conn = _db.connect(db_path)
        try:
            _db.write(conn, "INSERT INTO events (event_id, ts, event_type,"
                            " session_id, task_id, repo, repo_version, tool,"
                            " failure_key, action_fingerprint, payload_json,"
                            " redaction_status) VALUES"
                            " ('ev-verify', '2026-11-07T00:00:00+00:00',"
                            " 'verification', 's-verify', 's-verify', ?, '',"
                            " 'bash', ?, 'fp', '{\"tests_passed\": 1}',"
                            " 'redacted')", (".", real_key))
            _db.write(conn, "INSERT INTO episode_events (episode_id,"
                            " event_id, seq) VALUES (?, 'ev-verify', 1)",
                      (ep_id,))
            _db.write(conn, "UPDATE episodes SET status = 'verified',"
                            " closed_at = '2026-11-07T00:00:00+00:00'"
                            " WHERE episode_id = ?", (ep_id,))
            conn.commit()
        finally:
            conn.close()
        lesson, status = memory_lessons.promote_lesson(
            ep_id, LESSON_INSTRUCTION, anti_pattern=LESSON_ANTI_PATTERN,
            verification={"tests_passed": True}, db_path=db_path)
        if status != "ok":
            raise ValueError(f"lesson promotion failed: {status}")
        if lesson["status"] != "verified":
            raise ValueError("refusing to eval a non-verified lesson -"
                             " the promotion law is not bypassable by the"
                             " eval path")
        lesson_id = lesson["lesson_id"]
    return real_key, lesson_id


def run_pair(out_dir, family=FAMILY, cases=CASES_DEFAULT,
             repeats=REPEATS_DEFAULT, python=None):
    """Generate the suite, seed both arms, run the pair, write the
    artifacts bundle. Returns the bundle dict."""
    from minder_op import benchmark, runner
    from minder_quality import driver

    suite_dir, manifest = generate_suite(out_dir, family, cases)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    minder_root = str(Path(__file__).resolve().parent.parent)
    producer = out / "eval_pair_producer.py"
    producer.write_text(_PRODUCER, encoding="utf-8")

    stores = {"control": str(out / "control.sqlite"),
              "treatment": str(out / "treatment.sqlite")}
    fkeys, lesson_ids = {}, {}
    for arm, dbp in stores.items():
        fkeys[arm], lesson_ids[arm] = _seed_arm(dbp,
                                                arm == "treatment")

    os.environ["MEWS_MINDER_ROOT"] = minder_root
    # pin the interpreter: under an appimage harness sys.executable is
    # not a python, and shutil.which("python3") resolves through the
    # appimage's PATH to the same wrapper (both measured: children die
    # with SIGTRAP / dbus-X11 errors). The honest interpreter is the
    # system python, with sys.executable as the fallback for containers.
    interpreter = python or (
        "/usr/bin/python3" if Path("/usr/bin/python3").exists()
        else sys.executable)
    drv = {"id": "scripted-lesson-producer",
           "argv": [interpreter, str(producer),
                    "{prompt_file}"],
           "timeout_s": 120,
           "env_allow": ["MINDER_ASSIST", "MINDER_EVAL_DB",
                         "MEWS_MINDER_ROOT"]}
    old_executable = sys.executable
    if interpreter != old_executable:
        # the hardened argv and the pristine collection spawn pytest with
        # sys.executable; under an appimage harness that is not a real
        # python (measured: SIGTRAP / dbus-X11)
        sys.executable = interpreter
    try:
        entries = {}
        for arm, dbp in stores.items():
            os.environ["MINDER_ASSIST"] = "retrieve"
            os.environ["MINDER_EVAL_DB"] = dbp
            entries[arm] = []
            for task in manifest["tasks"]:
                entries[arm].extend(driver.run_task(
                    suite_dir, task, drv,
                    out / f"work-{arm}", repeats=repeats))
    finally:
        sys.executable = old_executable
        os.environ.pop("MINDER_ASSIST", None)
        os.environ.pop("MINDER_EVAL_DB", None)

    reports = {arm: runner.build_report(manifest, entries[arm], timeout=120)
               for arm in stores}
    digest = lesson_content_digest(fkeys["treatment"],
                                   LESSON_INSTRUCTION,
                                   LESSON_ANTI_PATTERN)
    bundle = {
        "suite_id": manifest["suite_id"],
        "suite_fingerprint": benchmark.manifest_fingerprint(manifest),
        "family": family, "cases": cases, "repeats": repeats,
        "lesson_content_digest": digest,
        "lesson_instruction": LESSON_INSTRUCTION,
        "driver": drv["id"], "interpreter": interpreter,
        "control": {"report": reports["control"],
                    "entries": entries["control"],
                    "store": stores["control"]},
        "treatment": {"report": reports["treatment"],
                      "entries": entries["treatment"],
                      "store": stores["treatment"],
                      "lesson_id": lesson_ids["treatment"]},
    }
    (out / "eval-pair-artifacts.json").write_text(
        json.dumps(bundle, indent=1), encoding="utf-8")
    return bundle


def score(out_dir):
    """Score a run bundle: per-arm rates, discordant pairs, exact McNemar
    p, ledger tiers, and the limitations the FR-7 law requires."""
    out = Path(out_dir)
    bundle = json.loads((out / "eval-pair-artifacts.json").read_text(
        encoding="utf-8"))
    pairs, per_case = [], []
    control = {e["task_id"]: e for e in bundle["control"]["entries"]}
    for e in bundle["treatment"]["entries"]:
        c = control.get(e["task_id"])
        if c is None or c.get("run_index") != e.get("run_index"):
            continue
        cp, tp = c["status"] == "verified", e["status"] == "verified"
        pairs.append((cp, tp))
        per_case.append({"task_id": e["task_id"],
                         "run_index": e["run_index"],
                         "control": c["status"], "treatment": e["status"]})
    arm_stats = {}
    for arm in ("control", "treatment"):
        entries = bundle[arm]["entries"]
        arm_stats[arm] = {
            "runs": len(entries),
            "verified": sum(1 for e in entries
                            if e["status"] == "verified"),
        }
        arm_stats[arm]["rate"] = (arm_stats[arm]["verified"]
                                  / len(entries) if entries else 0.0)
    p = mcnemar_exact(pairs)
    from minder_op import queries
    ledger = {arm: queries.eval_pair_ledger(Path(bundle[arm]["store"]))
              for arm in ("control", "treatment")}
    report = {
        "schema": "minder.eval-pair-report/v1",
        "suite_id": bundle["suite_id"],
        "suite_fingerprint": bundle["suite_fingerprint"],
        "lesson_content_digest": bundle["lesson_content_digest"],
        "family": bundle["family"],
        "arms": arm_stats,
        "pairs": {"n": len(pairs),
                  "discordant_treatment_only":
                      sum(1 for x, y in pairs if not x and y),
                  "discordant_control_only":
                      sum(1 for x, y in pairs if x and not y),
                  "mcnemar_exact_p": p},
        "per_case": per_case,
        "ledger": ledger,
        "limitations": [
            "the producer is scripted: this pair measures the lesson"
            " channel end to end, not how well a real coding agent uses"
            " the lesson",
            "single lesson, single generated family - no cross-family"
            " claim is licensed",
            "measurement only: nothing here promotes, adopts, or changes"
            " live behavior (FR-7)",
        ],
    }
    (out / "eval-pair-report.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    return report
