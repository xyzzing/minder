"""The dispatch intake gate (#89, stage 1): default-deny, bots never
explicit, stable reason codes, and a precedence that never admits by
accident. The gate is pure code (no model) so every one of these
outcomes is deterministic.
"""
from dataclasses import replace

from minder_op import dispatch_config, dispatch_gate


def _trigger(**over):
    base = {"trigger_id": "github:111:2026-11-07T10:00:00Z",
            "ts": "2026-11-07T10:00:00Z", "source": "github",
            "repo": "xyzzing/minder", "kind": "issue", "sender": "alice"}
    base.update(over)
    return base


def _config(**over):
    # the watched repo is always set: these tests own the sender check,
    # and the repo check has its own precedence test below
    base = {"repo": "xyzzing/minder"}
    base.update(over)
    return replace(dispatch_config.default_config(), **base)


def test_defaults_deny_without_allowed_senders():
    decision = dispatch_gate.decide(_trigger(), _config())
    assert decision["decision"] == "filtered"
    assert decision["reason"] == "sender_not_allowed"


def test_bot_sender_is_never_explicit():
    decision = dispatch_gate.decide(
        _trigger(sender="dependabot[bot]"),
        _config(allowed_senders=("dependabot[bot]",)))
    assert decision["decision"] == "filtered"
    assert decision["reason"] == "bot_sender"


def test_repo_not_watched_precedes_everything():
    decision = dispatch_gate.decide(
        _trigger(repo="other/repo"),
        _config(repo="xyzzing/minder", allowed_senders=("alice",)))
    assert decision["decision"] == "filtered"
    assert decision["reason"] == "repo_not_watched"


def test_pull_requests_are_not_an_admitted_kind():
    decision = dispatch_gate.decide(
        _trigger(kind="pull_request"),
        _config(repo="xyzzing/minder", allowed_senders=("alice",)))
    assert decision["decision"] == "filtered"
    assert decision["reason"] == "kind_not_admitted"


def test_unknown_sender_fails_closed():
    decision = dispatch_gate.decide(
        _trigger(sender=None),
        _config(repo="xyzzing/minder", allowed_senders=("alice",)))
    assert decision["decision"] == "filtered"
    assert decision["reason"] == "sender_unknown"


def test_sender_match_is_case_insensitive():
    """GitHub logins are case-insensitive: config "Alice", sender
    "alice" is the same person, not a fail-closed casing trip."""
    decision = dispatch_gate.decide(
        _trigger(sender="alice"),
        _config(allowed_senders=("Alice",)))
    assert decision["decision"] == "would_accept"
    assert decision["sender_class"] == "listed"


def test_listed_sender_admitted_in_auto_mode():
    decision = dispatch_gate.decide(
        _trigger(), _config(repo="xyzzing/minder",
                            allowed_senders=("alice",), accept_mode="auto"))
    assert decision["decision"] == "admitted"
    assert decision["reason"] is None
    assert decision["sender_class"] == "listed"


def test_observe_mode_records_would_accept():
    decision = dispatch_gate.decide(
        _trigger(), _config(repo="xyzzing/minder",
                            allowed_senders=("alice",)))
    assert decision["decision"] == "would_accept"
    assert decision["reason"] is None
