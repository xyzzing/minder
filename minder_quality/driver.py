"""Harness driver (PRD v0.9 9E): point any CLI coding agent at minder's
benchmark tasks and grade it by the same integrity law as overlays.

A driver is operator config: {"id", "argv", "timeout_s", "env_allow"}
in minder.json -> benchmark_drivers. Per run minder stages the task's
fixtures WITHOUT the holdout, runs the driver argv (no shell - the F5
law) with cwd = the task directory and {prompt_file} substituted, then
treats the resulting directory as the overlay: 9A's integrity verdict,
9B's quality record. The driver may reach loopback only; minder cannot
enforce that for arbitrary binaries, so the report says
"network_isolation": "not_enforced" honestly unless the operator
supplies a namespace wrapper.
"""
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from minder_core import diffmetrics, integrity
from minder_op import runner

PROMPT_ARG = "{prompt_file}"


def _stage_task_dir(suite_dir, task, work_root, include_holdout=False):
    """The task directory: fixtures + prompt.md, holdout only when the
    caller is the verifier (never the driver)."""
    task_dir = work_root / task["task_id"]
    task_dir.mkdir(parents=True)
    for rel in task.get("fixtures") or []:
        src = (suite_dir / rel).resolve()
        if not src.is_file():
            raise runner.RunnerError(f"fixture disappeared: {rel}")
        dest = task_dir / Path(rel).name
        shutil.copyfile(src, dest)
    prompt = suite_dir / "tasks" / task["task_id"] / "prompt.md"
    if not prompt.is_file():
        prompt = suite_dir / task.get("prompt", "prompt.md")
    if prompt.is_file():
        shutil.copyfile(prompt, task_dir / "prompt.md")
    return task_dir


def _driver_env(workspace, env_allow):
    """Like runner._child_env but tolerant of a reused work dir (a
    task's repeats share one)."""
    workspace = Path(workspace)
    tmp = workspace / ".tmp"
    home = workspace / ".home"
    tmp.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    env = {key: os.environ[key] for key in runner._KEEP_ENV
           if key in os.environ}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                "MINDER_NO_SYSTEMD": "1", "TMPDIR": str(tmp),
                "HOME": str(home)})
    for key in env_allow or []:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def run_task(suite_dir, task, driver, work_root, repeats=1):
    """One task under the driver, `repeats` times. Returns run entries
    with run_index, integrity verdict, and the quality record."""
    entries = []
    for index in range(max(1, repeats)):
        work = work_root / f"run{index}"
        work.mkdir(parents=True, exist_ok=True)
        task_dir = _stage_task_dir(suite_dir, task, work)
        pre_files = integrity.tree_files(task_dir)
        prompt_file = task_dir / "prompt.md"
        argv = [arg.replace(PROMPT_ARG, str(prompt_file))
                for arg in driver["argv"]]
        env = _driver_env(work, driver.get("env_allow"))
        timeout = driver.get("timeout_s", 600)
        timed_out = False
        proc = subprocess.Popen(
            argv, cwd=str(task_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            out, _ = proc.communicate()
        # the driver's writes ARE the overlay: verify in place
        junit_dir = work / ".minder"
        junit_dir.mkdir(exist_ok=True)
        entry_files = (task.get("runner") or {}).get("entry") or []
        junit_path = junit_dir / "junit.xml"
        fixtures = task.get("fixtures") or []
        extra = [] if any(str(f).endswith("conftest.py")
                          for f in fixtures) else ["--noconftest"]
        rc, _vout, vstart, _ = runner._run_pytest(
            runner._hardened_argv(task_dir, entry_files, junit_path,
                                  extra),
            task_dir, _driver_env(work, None), 120)
        counts, junit_ids = integrity.parse_junit(junit_path,
                                                  started=vstart) \
            or (None, None)
        post_files = integrity.tree_files(task_dir)
        expected_ids = _collect_pristine(suite_dir, task, work)
        expected_tests = (task.get("runner") or {}).get("expected_tests")
        if timed_out:
            verdict = {"status": "timeout", "tamper_reasons": [],
                       "tests": {}, "holdout": {"status": "absent"}}
        else:
            holdout_status = _run_holdout(suite_dir, task, work, task_dir)
            verdict = integrity.assess(
                pre_files, post_files,
                task.get("protected"),
                junit_counts=counts, junit_ids=junit_ids,
                expected_ids=expected_ids or None,
                expected_tests=expected_tests,
                allow_skips=(task.get("runner") or {}).get("allow_skips",
                                                           0),
                holdout_status=holdout_status,
                run_status="verified" if rc == 0 else "failed")
        quality = diffmetrics.measure(_seed_tree(suite_dir, task, work),
                                      task_dir,
                                      allowed_paths=task.get(
                                          "allowed_paths"))
        entry = {"task_id": task["task_id"], "run_index": index,
                 "status": verdict["status"],
                 "exit_code": rc if rc is not None else -1,
                 "integrity": verdict, "quality": quality,
                 "output_tail": (out or "")[-500:]}
        entries.append(entry)
        shutil.rmtree(work, ignore_errors=True)
    return entries


def _seed_tree(suite_dir, task, work):
    """The pre-state the driver started from: fixtures only."""
    seed = work / ".seed"
    seed.mkdir(exist_ok=True)
    for rel in task.get("fixtures") or []:
        src = (suite_dir / rel).resolve()
        if src.is_file():
            dest = seed / Path(rel).name
            shutil.copyfile(src, dest)
    return seed


def _collect_pristine(suite_dir, task, work):
    """Test ids the pristine fixture set collects (the driver never saw
    them differ)."""
    seed = _seed_tree(suite_dir, task, work)
    entry_files = (task.get("runner") or {}).get("entry") or []
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider", *entry_files],
            cwd=str(seed), capture_output=True, text=True, timeout=60)
        return [line.strip() for line in (proc.stdout or "").splitlines()
                if "::" in line]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _run_holdout(suite_dir, task, work, task_dir):
    """Holdout in a fresh dir the driver cannot address; task module
    importable via PYTHONPATH."""
    holdout_files = task.get("holdout") or []
    if not holdout_files:
        return "absent"
    holdout_dir = work / ".minder" / "holdout"
    holdout_dir.mkdir(parents=True, exist_ok=True)
    for rel in holdout_files:
        src = (suite_dir / rel).resolve()
        if not src.is_file():
            return "fail"
        shutil.copyfile(src, holdout_dir / Path(rel).name)
    env = _driver_env(work, None)
    env["PYTHONPATH"] = str(task_dir)
    junit = holdout_dir / "junit.xml"
    rc, _out, started, _ = runner._run_pytest(
        runner._hardened_argv(holdout_dir,
                              [Path(r).name for r in holdout_files],
                              junit, ["--noconftest"]),
        holdout_dir, env, 120)
    parsed = integrity.parse_junit(junit, started=started)
    if rc == 0 and parsed and parsed[0]["collected"] > 0:
        return "pass"
    return "fail"
