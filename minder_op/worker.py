"""The isolated laya decision worker, read read-only by doctor (issue #32).

`minder_op` never spawns a worker, writes its log, or touches its socket;
it reports what the socket and the log say. The judgement that lives here
is the one the log tail alone cannot support: a worker that is not up is
normal (spawn-on-demand), a failure line is only evidence about the moment
it was written, and only a dial of the socket says whether a worker is
answering now.

The live case this replaced: after `install.sh` restarted the proxy, a
worker was alive and answering while `doctor` warned off a `spawn_failed`
line left in the log from before the fix."""
from minder_decision import log_stamp
from minder_op.ages import age_text

# A worker log line older than this is not live evidence: the file is
# append-only and rotates only at 1 MB, so a fixed defect keeps its line
# for days.
LOG_FRESH_SECS = 6 * 3600


def worker_check(now, probe=None):
    """doctor id `laya-worker`: liveness from the socket, reason and age
    from the log.

    `probe` is the liveness seam (a path -> bool callable) so a test can
    state whether a worker is up without the check dialing a real socket.
    The log's last line names the failure and says how old it is; an
    unstamped line, written before stamps existed, reports the file's
    mtime as a floor rather than a measurement."""
    sock = log_stamp.socket_path()
    probe = probe or log_stamp.worker_liveness
    if probe(sock):
        return ("laya-worker", "ok", f"worker answering at {sock}")
    # spawn-on-demand: the worker exits when idle, so nothing being up is
    # a legitimate state and a worker that never failed is context.
    entry = log_stamp.latest_log_line(sock)
    if entry is None:
        return ("laya-worker", "info",
                f"no worker up at {sock} (spawn-on-demand; the first "
                "router-eligible request starts it)")
    line, stamp, stamped = entry
    age = "" if stamp is None else f" {age_text(now - stamp)} ago"
    if not stamped:
        age += " at least"
    stale = ""
    if stamp is not None and now - stamp > LOG_FRESH_SECS:
        stale = " - stale evidence, a fixed defect keeps this line"
    return ("laya-worker", "warn",
            f"no worker up at {sock}; last failure {line[:140]}{age}"
            f"{stale} - see docs/operator-cli.md")
