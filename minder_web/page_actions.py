"""What to act on per page (issue #12).

Each helper reads the page model's rows - already redacted by
services._safe_row - and returns the actions a reader can take, in the
order they matter. The strings are code-owned copy (C3: they live in
minder_web.strings only when a template renders them; these are composed
from counts, so they are built here from the shared copy in
strings_copy.PAGE_ACTIONS).
"""
from minder_web import strings_copy

_A = strings_copy.PAGE_ACTIONS


def episode_actions(rows):
    """An episode still open with no closing event is work the agent
    left unfinished - the one thing to act on this page."""
    open_rows = [r for r in rows if (r.get("status") or "") == "open"]
    if not open_rows:
        return []
    return [_A["episodes"].format(count=len(open_rows))]


def gap_actions(rows):
    if not rows:
        return []
    return [_A["gaps"].format(count=len(rows))]


def consult_actions(rows):
    """A frontier answer judged harmful is the one consult worth reading:
    it means an outside model made the work worse."""
    harmful = [r for r in rows if (r.get("helpfulness") or "") == "harmful"]
    if not harmful:
        return []
    return [_A["consults"].format(count=len(harmful))]


def decision_actions(rows):
    """The policy overrode what the model recommended: that disagreement
    is where a routing rule is wrong or the evidence is missing."""
    split = [r for r in rows
             if (r.get("model_recommendation") or "")
             and (r.get("policy_decision") or "")
             and r.get("model_recommendation") != r.get("policy_decision")]
    if not split:
        return []
    return [_A["decisions"].format(count=len(split))]


def skill_actions(rows):
    """A skill whose body file is missing is inert: it is listed but
    never retrieved. That is the one actionable item on this page."""
    missing = [r["name"] for r in rows if not r["body_ok"]]
    if not missing:
        return []
    return [_A["skills"].format(count=len(missing),
                                names=", ".join(missing[:3]))]
