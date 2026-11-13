"""The GitHub notification source (issue #89, stage 1).

Polling, not webhooks: the only trust boundary is the one gh already
owns (the operator's own credentials), and a poller reading its own
notifications can never be reached by an unsolicited packet. One poll
reads the watched repo's notification list; each notification becomes
one trigger with a stable id (notification id + updated_at), so a
restart re-polls safely and the store's idempotent insert is the replay
guard. A thread that moved (new comment means a new updated_at) is a
new trigger; nothing deletes or mutates what was already recorded.
"""
import json
import subprocess

from minder_op.dispatch_gate import (KIND_ISSUE, KIND_ISSUE_COMMENT,
                                     KIND_OTHER, KIND_PULL_REQUEST,
                                     SOURCE_GITHUB)

POLL_TIMEOUT_S = 30


def poll(repo, runner=None):
    """One poll: ([trigger, ...], error_code or None).

    Fail closed: on any failure the list is empty and the code names
    the failure. A sender fetch that fails for one notification does
    not fail the poll - that trigger carries sender None and the gate
    filters it as sender_unknown.
    """
    runner = runner or _default_runner
    endpoint = f"/repos/{repo}/notifications"
    try:
        proc = runner(["gh", "api", endpoint], timeout=POLL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return [], "gh_unreachable"
    if proc.returncode != 0:
        return [], "gh_error"
    try:
        notifications = json.loads(proc.stdout or "[]")
    except ValueError:
        return [], "gh_bad_json"
    if not isinstance(notifications, list):
        return [], "gh_bad_json"
    triggers = []
    for note in notifications:
        trigger = _trigger(repo, note, runner)
        if trigger is not None:
            triggers.append(trigger)
    return triggers, None


def _trigger(repo, note, runner):
    if not isinstance(note, dict):
        return None
    subject = note.get("subject") or {}
    nid = str(note.get("id") or "")
    updated = str(note.get("updated_at") or "")
    if not nid or not updated:
        return None
    return {
        "trigger_id": f"{SOURCE_GITHUB}:{nid}:{updated}",
        "ts": updated,
        "source": SOURCE_GITHUB,
        "repo": str((note.get("repository") or {}).get("full_name")
                    or repo),
        "kind": _kind(subject),
        "sender": _sender(subject, runner),
    }


def _kind(subject):
    """issue | issue_comment | pull_request | other, from the subject.

    A comment notification points latest_comment_url at the comment; a
    fresh issue's latest comment is the issue itself, so the path is
    what separates comment activity from a new issue.
    """
    stype = str(subject.get("type") or "")
    comment_url = str(subject.get("latest_comment_url") or "")
    if stype in ("Issue", "IssueComment"):
        if "/comments/" in comment_url:
            return KIND_ISSUE_COMMENT
        return KIND_ISSUE
    if stype == "PullRequest":
        return KIND_PULL_REQUEST
    return KIND_OTHER


def _sender(subject, runner):
    """Author login of the thread's latest activity, or None.

    One fetch, fail closed: the gate records sender_unknown rather than
    the poller guessing an identity it could not read.
    """
    url = subject.get("latest_comment_url") or subject.get("url")
    if not url:
        return None
    # defense in depth: the fields are GitHub-canonical today, but this
    # string reaches a gh command line, so pin the API origin
    if not str(url).startswith("https://api.github.com/"):
        return None
    try:
        proc = runner(["gh", "api", str(url)], timeout=POLL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        body = json.loads(proc.stdout or "")
    except ValueError:
        return None
    login = ((body or {}).get("user") or {}).get("login")
    return str(login) if login else None


def _default_runner(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, shell=False)
