"""Controlled local benchmark runner (8D).

Executes ONLY allowlisted pytest verification for manifest tasks that
declare `runner: {kind: pytest, entry: [files]}`, inside a fresh
temporary workspace per task. No shell, no DSH/GUI automation, no
network use, no writes outside the sandbox — plus the operator-directed
report path, and an auto-persisted failed report when a timeout fires.

Gating lives in the CLI: execution requires BOTH `--execute` and
`--i-understand-this-runs-local-agent-tasks`; without them `run` stays
a dry-run planner (8C).

Trust model (stated plainly): the operator owns fixture and overlay
content. Isolation here is by construction and by default — fresh
workspace, scrubbed env, argv-only subprocess, timeout kill — not a
security boundary against hostile task code; the closed
forbidden-capability vocabulary is enforced mechanically by the runner
(it never provides network, browser, broker, SSH, package install,
frontier, or anything outside the workspace), and metrics count task
verification outcomes, not undetectable sandbox escapes.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from minder_op.benchmark import BenchmarkError, manifest_fingerprint
from memory.canonicalise import redact

RUNNER_KIND = "pytest"
DEFAULT_TIMEOUT = 120
OUTPUT_TAIL = 2000

REQUIRED_FLAGS_MSG = ("real execution requires --execute and "
                      "--i-understand-this-runs-local-agent-tasks "
                      "(8D controlled local runner); without them only "
                      "--dry-run exists")

# Child env is allowlisted, not blocklisted: only generic locale/PATH
# pass through. This drops proxies, LD_LIBRARY_PATH (which poisons
# python on this machine), PYTHONPATH, and every MINDER_* runtime flag.
_KEEP_ENV = ("PATH", "LANG", "LC_ALL")


class RunnerError(Exception):
    """Runner refused (bad overlay, no runner, no workspace...)."""


def select_tasks(manifest, task_id=None):
    """Tasks to execute: the named task, or every runnable task."""
    tasks = manifest.get("tasks") or []
    if task_id:
        task = next((t for t in tasks
                     if t.get("task_id") == task_id), None)
        if not task:
            raise BenchmarkError(f"unknown task: {task_id}")
        if task.get("runner", {}).get("kind") != RUNNER_KIND:
            raise BenchmarkError(
                f"task {task_id} has no runner (declared, not "
                "executable); nothing to execute")
        return [task]
    runnable = [t for t in tasks
                if t.get("runner", {}).get("kind") == RUNNER_KIND]
    if not runnable:
        raise BenchmarkError("suite has no runnable tasks")
    return runnable


def _copy_tree(src_dir, dest_dir):
    """Copy regular files only; refuse symlinks and anything that is
    not a plain file so an overlay can never point outside."""
    for base, _dirs, files in os.walk(src_dir):
        for name in files:
            src = Path(base) / name
            if src.is_symlink() or not src.is_file():
                raise RunnerError(
                    f"overlay member is not a regular file: {src}")
            rel = src.relative_to(src_dir)
            dest = dest_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest, follow_symlinks=False)


def _stage_workspace(suite_dir, task):
    workspace = Path(tempfile.mkdtemp(prefix="minder-bench-"))
    for rel in task.get("fixtures") or []:
        src = (suite_dir / rel).resolve()
        if not src.is_file():
            raise RunnerError(f"fixture disappeared: {rel}")
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return workspace


def _child_env(workspace):
    env = {key: os.environ[key] for key in _KEEP_ENV
           if key in os.environ}
    tmp = workspace / ".tmp"
    home = workspace / ".home"
    tmp.mkdir()
    home.mkdir()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                "MINDER_NO_SYSTEMD": "1", "TMPDIR": str(tmp),
                "HOME": str(home)})
    return env


def _entry_dir(workspace, task, entry_files):
    """The workspace sub-directory the verification runs in: where the
    declared fixture matching the entry file lives."""
    for item in entry_files:
        for path in task.get("fixtures") or []:
            if isinstance(path, str) and (path == item or
                                          path.endswith("/" + item)):
                candidate = workspace / Path(path).parent
                if candidate.is_dir():
                    return candidate
    return workspace


def execute_task(suite_dir, task, overlay=None, timeout=DEFAULT_TIMEOUT,
                 keep_workspace=False):
    """Run one task's pytest verification in a fresh sandbox.

    Returns (run_entry, workspace_path_or_None). run_entry matches the
    8C report `runs` schema; status is verified | failed | timeout.
    """
    entry_spec = task.get("runner") or {}
    workspace = _stage_workspace(suite_dir, task)
    try:
        entry_files = entry_spec.get("entry") or []
        work_dir = _entry_dir(workspace, task, entry_files)
        if overlay:
            # a candidate fix is expressed relative to the task's
            # working directory, so it overlays there
            _copy_tree(Path(overlay), work_dir)
        cmd = [sys.executable, "-m", "pytest", "-q", *entry_files]
        started = time.monotonic()
        proc = subprocess.Popen(
            cmd, cwd=str(work_dir),
            env=_child_env(workspace),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
            status = "verified" if proc.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            out, _ = proc.communicate()
            status = "timeout"
        duration_ms = round((time.monotonic() - started) * 1000)
        entry = {"task_id": task.get("task_id"), "status": status,
                 "exit_code": proc.returncode,
                 "duration_ms": duration_ms,
                 "output_tail": redact((out or "")[-OUTPUT_TAIL:])}
        return entry, (workspace if keep_workspace else None)
    finally:
        if not keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def build_report(manifest, entries, timeout=DEFAULT_TIMEOUT,
                 workspace=None):
    """Assemble an 8C-schema report from executed run entries."""
    total = len(entries)
    verified = sum(1 for e in entries if e["status"] == "verified")
    report = {
        "report_version": 1,
        "suite_id": manifest.get("suite_id"),
        "suite_fingerprint": manifest_fingerprint(manifest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "candidate",
        "runs": entries,
        "metrics": {
            "comparable_runs": total,
            "verified_completion_rate": round(verified / total, 4)
            if total else 0.0,
            "unsafe_executions": 0,
            "harmful_frontier_acceptances": 0,
            "external_prohibited_egress": 0,
        },
        "benchmark_runner": {
            "mode": "local-sandbox (8D controlled local runner)",
            "timeout_s": timeout,
            "workspace": str(workspace) if workspace else None,
        },
    }
    return report
