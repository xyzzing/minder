"""The gh notification source (#89, stage 1): stable trigger ids, honest
kind mapping, sender resolution with fail-closed fetches, and named
degradation when gh itself is unreachable. No test touches the network:
the runner is injected, the same seam the canary uses.
"""
import json

from minder_op import dispatch_source

REPO = "xyzzing/minder"


def _notification(nid="111", updated="2026-11-07T10:00:00Z", ntype="Issue",
                  subject_url=None, comment_url=None, repo=REPO):
    subject_url = (subject_url or
                   f"https://api.github.com/repos/{repo}/issues/42")
    comment_url = (comment_url if comment_url is not None else subject_url)
    return {"id": str(nid), "updated_at": updated,
            "subject": {"title": "t", "type": ntype, "url": subject_url,
                        "latest_comment_url": comment_url},
            "repository": {"full_name": repo}}


def _comment(login="alice"):
    return json.dumps({"user": {"login": login}})


class FakeGh:
    """Returns canned JSON per endpoint; records every call."""

    def __init__(self, notifications, comments=None, rc_fail=None,
                 raise_on=None):
        self.notifications = json.dumps(notifications)
        self.comments = comments or {}
        self.rc_fail = rc_fail or set()
        self.raise_on = raise_on or set()
        self.calls = []

    def __call__(self, cmd, timeout=None):
        self.calls.append(cmd)
        endpoint = cmd[2] if len(cmd) > 2 else ""
        if any(flag in endpoint for flag in self.raise_on):
            raise OSError("gh unreachable")
        if endpoint in self.rc_fail:
            return _Proc(1, "", "gh failed")
        if endpoint.endswith("/notifications"):
            return _Proc(0, self.notifications, "")
        body = self.comments.get(endpoint)
        return _Proc(0, body if body is not None else "", "")


class _Proc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_trigger_ids_stable_across_replays():
    gh = FakeGh([_notification()])
    first, err1 = dispatch_source.poll(REPO, runner=gh)
    second, err2 = dispatch_source.poll(REPO, runner=gh)
    assert err1 is None and err2 is None
    assert [t["trigger_id"] for t in first] == [t["trigger_id"]
                                                for t in second]


def test_thread_update_is_a_new_trigger():
    gh = FakeGh([_notification(updated="2026-11-07T10:00:00Z")])
    first, _ = dispatch_source.poll(REPO, runner=gh)
    gh.notifications = json.dumps(
        [_notification(updated="2026-11-07T11:00:00Z")])
    second, _ = dispatch_source.poll(REPO, runner=gh)
    assert first[0]["trigger_id"] != second[0]["trigger_id"]


def test_comment_activity_is_issue_comment_kind():
    n = _notification(comment_url=(
        "https://api.github.com/repos/xyzzing/minder/issues/comments/999"))
    gh = FakeGh([n], comments={
        "https://api.github.com/repos/xyzzing/minder/issues/comments/999":
            _comment("alice")})
    triggers, _ = dispatch_source.poll(REPO, runner=gh)
    assert triggers[0]["kind"] == "issue_comment"
    assert triggers[0]["sender"] == "alice"


def test_new_issue_without_comments_is_issue_kind():
    gh = FakeGh([_notification()],
                comments={"https://api.github.com/repos/"
                          "xyzzing/minder/issues/42": _comment("alice")})
    triggers, _ = dispatch_source.poll(REPO, runner=gh)
    assert triggers[0]["kind"] == "issue"
    assert triggers[0]["sender"] == "alice"


def test_sender_fetch_failure_fails_closed():
    n = _notification(comment_url=(
        "https://api.github.com/repos/xyzzing/minder/issues/comments/999"))
    gh = FakeGh([n])
    triggers, _ = dispatch_source.poll(REPO, runner=gh)
    assert triggers[0]["sender"] is None


def test_unhandled_subject_type_is_recorded_honestly():
    gh = FakeGh([_notification(ntype="Release")])
    triggers, _ = dispatch_source.poll(REPO, runner=gh)
    assert triggers[0]["kind"] == "other"


def test_off_origin_subject_url_resolves_no_sender():
    """The subject URL reaches a gh command line; anything off the API
    origin is not fetched (defense in depth, the fields are
    GitHub-canonical today)."""
    n = _notification(comment_url=(
        "https://evil.example/repos/xyzzing/minder/issues/comments/999"))
    gh = FakeGh([n])
    triggers, _ = dispatch_source.poll(REPO, runner=gh)
    assert triggers[0]["sender"] is None
    assert not any("evil.example" in " ".join(c) for c in gh.calls)


def test_gh_failure_degrades_to_a_named_error():
    gh = FakeGh([_notification()], rc_fail={
        f"/repos/{REPO}/notifications"})
    triggers, err = dispatch_source.poll(REPO, runner=gh)
    assert triggers == []
    assert err == "gh_error"


def test_gh_unreachable_degrades_to_a_named_error():
    gh = FakeGh([_notification()], raise_on=["/notifications"])
    triggers, err = dispatch_source.poll(REPO, runner=gh)
    assert triggers == []
    assert err == "gh_unreachable"
