"""The paired control-vs-treatment lesson eval (issues #88/windtunnel#16).

A 3-case generated family suite through the 9E driver, both arms, then
the paired report. The asserts pin the pair's law: the arms differ only
by the lesson row (control keeps MINDER_ASSIST=retrieve and writes the
miss rows that form the denominator), the treatment's delivery is
tier-exact, the promotion law is not bypassable (a candidate lesson is
refused), and the lesson fingerprint is content-only because lesson ids
are random per run.
"""
import json

import pytest

from minder_op import eval_pair


@pytest.fixture(scope="module", autouse=True)
def _spy_verification_runs():
    """Surface the verification pytest's own output: the underverified
    verdicts only happen under pytest, and run_task discards vout."""
    from minder_op import runner
    real = runner._run_pytest
    def spy(cmd, work_dir, env, timeout):
        rc, out, started, wall = real(cmd, work_dir, env, timeout)
        if any("junit.xml" in str(a) for a in cmd):
            print(f"VERIFY rc={rc} argv_tail={cmd[-3:]} out={out[-500:]}",
                  flush=True)
        return rc, out, started, wall
    runner._run_pytest = spy
    yield
    runner._run_pytest = real


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    out = tmp_path_factory.mktemp("eval-pair")
    return eval_pair.run_pair(out, cases=3, repeats=1)


def test_arms_differ_only_by_the_lesson(pair):
    control = pair["control"]["report"]["metrics"]
    treatment = pair["treatment"]["report"]["metrics"]
    assert control["comparable_runs"] == treatment["comparable_runs"]
    assert control["verified_completion_rate"] == 0.0
    if treatment["verified_completion_rate"] != 1.0:
        tails = [(e["task_id"], e["status"], e.get("output_tail", "")[-300:])
                 for e in pair["treatment"]["entries"]]
        raise AssertionError(f"treatment arm did not verify: {tails}")
    assert treatment["verified_completion_rate"] == 1.0


def test_control_miss_rows_form_the_denominator(pair):
    import sqlite3
    conn = sqlite3.connect(pair["control"]["store"])
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT tier, digest_injected, assist_mode,"
            " lesson_id FROM learning_injections")]
    finally:
        conn.close()
    assert rows, "control arm produced no denominator rows"
    assert all(r["lesson_id"] is None and r["digest_injected"] == 0
               for r in rows), rows


def test_treatment_delivery_is_tier_exact(pair):
    import sqlite3
    conn = sqlite3.connect(pair["treatment"]["store"])
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT tier, digest_injected FROM learning_injections"
            " WHERE lesson_id IS NOT NULL")]
    finally:
        conn.close()
    assert rows and all(r["tier"] == "exact" and r["digest_injected"] == 1
                        for r in rows), rows


def test_score_reports_paired_delta_with_limitations(pair, tmp_path):
    out = tmp_path / "pair"
    out.mkdir()
    json.dump(pair, open(out / "eval-pair-artifacts.json", "w"))
    report = eval_pair.score(out)
    assert report["schema"] == "minder.eval-pair-report/v1"
    assert report["pairs"]["n"] == 3
    assert report["pairs"]["discordant_treatment_only"] == 3
    assert 0.0 <= report["pairs"]["mcnemar_exact_p"] <= 0.25
    assert any("scripted" in lim for lim in report["limitations"])


def test_candidate_lesson_is_refused(tmp_path, monkeypatch):
    """Proven-red law: the eval path must not bypass the promotion
    law - a candidate (unverified) lesson aborts the pair."""
    from minder_memory import lessons

    def candidate_promote(*args, **kwargs):
        return {"lesson_id": "les_fake", "status": "candidate",
                "instruction": "a candidate instruction",
                "anti_pattern": "",
                "failure_key": "bash|keyerror|k|none",
                "source_episode": "ep_fake"}, "ok"

    monkeypatch.setattr(lessons, "promote_lesson", candidate_promote)
    with pytest.raises(ValueError, match="non-verified"):
        eval_pair._seed_arm(tmp_path / "c.sqlite", with_lesson=True)


def test_mcnemar_matches_windtunnel_vectors():
    """The minder mirror agrees with the vectors windtunnel's own tests
    pin (7 discordant -> 0.125; 3 -> 0.25; 20 -> ~1.9e-06; none -> 1.0)."""
    # vectors computed from windtunnel/sweep_report.py:39 verbatim
    # (the seam map's "7 -> 0.125" probe claim was wrong: b=7,c=0,n=7
    # gives tail 1/128, two-sided 0.015625)
    assert eval_pair.mcnemar_exact([(True, False)] * 7) == \
        pytest.approx(0.015625)
    assert eval_pair.mcnemar_exact([(False, True)] * 4) == \
        pytest.approx(0.125)
    assert eval_pair.mcnemar_exact([(False, True)] * 20) == \
        pytest.approx(1.9073486328125e-06)
    assert eval_pair.mcnemar_exact([(True, True)] * 5) == 1.0
