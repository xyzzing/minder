"""The frontier panel's outbound text: the asks, and what leaves the machine.

Issue #10 wired the governed consult path. The consult prompt asks for the 2-3
ranked root causes and then THE single next concrete action; `ANSWER_SHAPE`
states that shape once and the prompts interpolate it, so an answer cannot be
asked for in one wording and read in another (issue #11). `minder_core
.panel_answer` implements the reading half of the same contract.

Pure text, the same kind as `verification`: no I/O, no minder imports. The
caps live here because a long answer must not smuggle a whole transcript into
an outbound request or into the lesson queue.
"""
import re


DISTILL_MAX_LINES = 6
DISTILL_MAX_CHARS = 240
# The prompt caps are what keep one escalation, whose error text the caller
# already truncated once, from becoming a paragraph-sized outbound request.
ERROR_CHARS = 1500
VERIFY_ERROR_CHARS = 800
VERIFY_RESOLUTION_CHARS = 800
VERIFY_WORDS = 60
CONSULT_WORDS = 150
# `frontier.run_panel` appends each consultant's answer under a separator;
# this is the length of that appendix.
NOTE_CHARS = 350

# The shape every ask demands and every reading reads, in one place (issue
# #11). The prompts interpolate it and `panel_answer` implements it, so neither
# side can reword the contract out from under the other.
ANSWER_SHAPE = (
    "the 2-3 most likely root causes, ranked, one line each; and "
    "Next action: THE single next concrete action most likely to break the "
    "loop (a command, a file to read, or a check to run)"
)
# The label that marks the action line. Like an attribution it is decoration
# around an action: it is stripped before the text is judged, and its presence
# is never taken as evidence that the text is one.
ACTION_LABELS = ("next action", "action", "fix", "do this")
# Causes are read for the operator, never distilled, so this cap only bounds
# what a long answer can put in front of them.
CAUSE_MAX_LINES = 4
# The label the ask actually writes, kept separate from the labels the reading
# tolerates. `panel_answer` treats a line carrying it as the action the answer
# itself named; only this one lets an unbulleted line through, since a colon on
# its own is not a marker and every cause sentence would otherwise look like a
# candidate action.
ASK_LABEL_RE = re.compile(r"^(?:\d+[.)]\s*)?(?:\*\*|__)?\s*next action"
                          r"\s*(?:\*\*|__)?\s*[:\u2013]\s*", re.IGNORECASE)




def redact(text, patterns):
    """Apply profile egress-redaction regexes (legal/privacy domains) to a
    payload field before it leaves the machine. A bad regex is skipped: the
    payload still goes out with the other patterns applied, which is the
    fail-open law, not a silent pass."""
    for pattern in patterns or []:
        try:
            text = re.sub(pattern, "[REDACTED]", text)
        except re.error:
            continue
    return text


def scrub_payload(payload, patterns):
    """The payload with egress redaction applied to its free-text fields.
    Without patterns there is nothing to do, so the payload is returned as
    is rather than copied. The patterns are passed in, not read from config:
    this module never sees configuration."""
    if not patterns:
        return payload
    out = dict(payload)
    for field in ("key", "error", "resolution", "task"):
        if isinstance(out.get(field), str):
            out[field] = redact(out[field], patterns)
    return out


def consult_prompt(payload, template=None):
    """The escalation payload → the text we ask a consultant. `kind: verify`
    gets the verification prompt; a profile template (with
    {key}/{attempts}/{error} slots) overrides the default consult prompt. A
    malformed template falls back to the default rather than to a partial
    prompt: the ask is what the answer is read against. Never raises."""
    if payload.get("kind") == "verify":
        return verify_prompt(payload)
    if template:
        try:
            return template.format(key=payload.get("key", "?"),
                                   attempts=payload.get("attempts", "?"),
                                   error=str(payload.get("error", ""))
                                   [:ERROR_CHARS])
        except (KeyError, IndexError, ValueError):
            pass
    return (
        "You are the frontier consultant for a coding agent stuck in a "
        "failure loop. A deterministic watchdog escalated after repeated "
        "failures of the same action.\n\n"
        f"Failed action: {payload.get('key', '?')}\n"
        f"Attempt count: {payload.get('attempts', '?')}\n"
        f"Last error (truncated): {str(payload.get('error', ''))[:ERROR_CHARS]}\n\n"
        f"Reply with: {ANSWER_SHAPE}. Write the second part on its own line, "
        f"starting `Next action:`. Terse. No "
        f"pleasantries, no restating the problem. Max ~{CONSULT_WORDS} words."
    )


def verify_prompt(payload):
    """The one ask of a verify consult: did the resolution address the root
    cause? The answer is a verdict line, so it is capped hard."""
    return (
        "You are verifying a resolved escalation for a coding agent. The "
        "action below failed repeatedly, was escalated for a frontier "
        "consult, and then succeeded. Judge only: does the resolution "
        "plausibly address the root cause of the failure?\n\n"
        f"Failed action: {payload.get('key', '?')} "
        f"(failed {payload.get('attempts', '?')}x)\n"
        f"Failure (truncated): "
        f"{str(payload.get('error', ''))[:VERIFY_ERROR_CHARS]}\n"
        f"Resolution (the tool output that succeeded, truncated): "
        f"{str(payload.get('resolution', ''))[:VERIFY_RESOLUTION_CHARS]}\n\n"
        "Reply exactly 'VERDICT: ADDRESSED' or 'VERDICT: NOT_ADDRESSED', "
        f"then one line why. Max {VERIFY_WORDS} words."
    )


def synthesis_prompt(payload, answers):
    """Original question + the consultants' answers → the merge prompt. The
    disagreement line is what an operator reads when the two did not agree,
    so it is asked for in a fixed shape."""
    parts = [f"Consultant {name}:\n{ans}" for name, ans in answers]
    return (
        "Two independent consultants answered a stuck coding agent's "
        "escalation. Reconcile them into ONE recommendation: "
        f"{ANSWER_SHAPE}. Write the second part on its own line, starting "
        "`Next action:`. Where the consultants disagree, prefer the one "
        "that better fits the error text and keep a final line exactly "
        f"'DISAGREEMENT: <one sentence>'. Terse, max ~{CONSULT_WORDS} words.\n\n"
        f"Failed action: {payload.get('key', '?')} "
        f"(attempt {payload.get('attempts', '?')})\n"
        f"Last error (truncated): "
        f"{str(payload.get('error', ''))[:VERIFY_ERROR_CHARS]}\n\n"
        + "\n\n".join(parts)
    )
