"""The dispatch intake gate (issue #89, stage 1): pure code, no model.

Who may start work is a code question in v1 - the dsh-superagent
prototype's Eval gate flattened to its deterministic half. The repo
must be the watched one, the kind must be a dispatched kind, and the
sender must be a listed human; bots are never explicit no matter what
the list says. Default deny; every refusal carries a stable reason code
the CLI and console render. Inferred admission is deliberately absent
(stricter than the prototype, honest at solo scale). Observe mode
records would_accept instead of admitted: the shadow-first posture
applied to dispatch.
"""
from minder_op import dispatch_config

SOURCE_GITHUB = "github"

KIND_ISSUE = "issue"
KIND_ISSUE_COMMENT = "issue_comment"
KIND_PULL_REQUEST = "pull_request"
KIND_OTHER = "other"
ADMITTED_KINDS = (KIND_ISSUE, KIND_ISSUE_COMMENT)

DECISION_ADMITTED = "admitted"
DECISION_WOULD_ACCEPT = "would_accept"
DECISION_FILTERED = "filtered"
DECISIONS = (DECISION_ADMITTED, DECISION_WOULD_ACCEPT, DECISION_FILTERED)

SENDER_CLASS_LISTED = "listed"
SENDER_CLASS_BOT = "bot"
SENDER_CLASS_UNLISTED = "unlisted"
SENDER_CLASS_UNKNOWN = "unknown"

REASON_REPO_NOT_WATCHED = "repo_not_watched"
REASON_KIND_NOT_ADMITTED = "kind_not_admitted"
REASON_SENDER_UNKNOWN = "sender_unknown"
REASON_BOT_SENDER = "bot_sender"
REASON_SENDER_NOT_ALLOWED = "sender_not_allowed"


def classify_sender(sender, allowed):
    """listed | bot | unlisted | unknown; a bot login is a bot even
    when the config lists it."""
    if sender is None:
        return SENDER_CLASS_UNKNOWN
    if str(sender).endswith("[bot]"):
        return SENDER_CLASS_BOT
    # GitHub logins are case-insensitive: config "Alice" must match a
    # sender "alice", not fail closed on an invisible casing trip
    folded = str(sender).casefold()
    listed = {str(name).casefold() for name in allowed}
    return (SENDER_CLASS_LISTED if folded in listed
            else SENDER_CLASS_UNLISTED)


def decide(trigger, config):
    """One trigger against the config: {decision, reason, sender_class}.

    Precedence is fixed: repo, then kind, then sender. The first
    failing check names its reason and stops, so no later check can
    walk a trigger past an earlier refusal.
    """
    sender_class = classify_sender(trigger.get("sender"),
                                   config.allowed_senders)
    if trigger.get("repo") != config.repo:
        return _result(DECISION_FILTERED, REASON_REPO_NOT_WATCHED,
                       sender_class)
    if trigger.get("kind") not in ADMITTED_KINDS:
        return _result(DECISION_FILTERED, REASON_KIND_NOT_ADMITTED,
                       sender_class)
    if sender_class == SENDER_CLASS_UNKNOWN:
        return _result(DECISION_FILTERED, REASON_SENDER_UNKNOWN,
                       sender_class)
    if sender_class == SENDER_CLASS_BOT:
        return _result(DECISION_FILTERED, REASON_BOT_SENDER, sender_class)
    if sender_class == SENDER_CLASS_UNLISTED:
        return _result(DECISION_FILTERED, REASON_SENDER_NOT_ALLOWED,
                       sender_class)
    if config.accept_mode == dispatch_config.ACCEPT_MODE_AUTO:
        return _result(DECISION_ADMITTED, None, sender_class)
    return _result(DECISION_WOULD_ACCEPT, None, sender_class)


def _result(decision, reason, sender_class):
    return {"decision": decision, "reason": reason,
            "sender_class": sender_class}
