"""`laya-worker.log` — stamps, ages and liveness for the decision worker.

The worker is fail-open by contract (Law #2): a spawn or dial failure
inside the request path returns None and cannot be surfaced any other way,
so the log file beside its socket is the only trace. A trace with no
timestamp is not evidence (issue #32) - `doctor` could not tell a failure
happening now from one fixed days ago, and warned forever.

This module owns that file's format: what a stamp looks like, how to read
one back, and how to ask whether a worker is answering at all."""
from datetime import datetime, timezone
from pathlib import Path


def stamp():
    """Prefix for every line written to `laya-worker.log`."""
    return (datetime.now(timezone.utc).isoformat(timespec="seconds")
            + " ")


def parse_stamp(line):
    """The UTC epoch seconds of a stamped log line, or None.

    Lines written before stamps existed carry none, and the check has to
    be correct against a log that predates it (issue #32)."""
    head = str(line).split(" ", 1)[0]
    try:
        parsed = datetime.fromisoformat(head)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def socket_path():
    """The decision worker's socket, from the environment the caller runs
    in. Read here so the log and the socket are resolved by one module."""
    from .worker import socket_path as worker_socket_path
    return worker_socket_path()


def log_path_for(path):
    """The worker's log file, named off its socket path."""
    return Path(path).with_name("laya-worker.log")


def worker_liveness(path):
    """Whether a worker is answering on its socket right now.

    The worker is spawn-on-demand and exits when idle, so this answers "is
    one up at this moment", not "does the install have one". Never raises:
    the health check that calls it must not crash over a socket error."""
    from .worker import _dial
    conn = _dial(path, 0.3)
    if conn is None:
        return False
    try:
        conn.close()
    except OSError:
        pass
    return True


def latest_log_line(path):
    """(line, epoch_seconds or None, stamped) for the last non-empty line
    of the worker log, or None when there is no log.

    `stamped` says whether the age came from the line itself or from the
    file's mtime, which is what lets a caller label an unstamped line as
    at-least-as-old rather than exactly that old."""
    log = log_path_for(path)
    try:
        lines = [ln for ln in log.read_text(errors="replace").splitlines()
                 if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    line = lines[-1]
    ts = parse_stamp(line)
    if ts is not None:
        return line, ts, True
    try:
        return line, log.stat().st_mtime, False
    except OSError:
        return line, None, False
