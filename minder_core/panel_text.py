"""The frontier panel's text in both directions: what we ask, and what in
the answer is an action.

Issue #10 wired the governed consult path, and the candidate lesson queue is
built from a consult's distilled action list, so the answer has to be read
locally and deterministically. The consult prompt asks for the 2-3 ranked
root causes and then THE single next concrete action, so a line is only
worth keeping when it reads as an action: a cause explains the failure and
must never become lesson instruction. The ask and the reading are one
contract — change the wording of the ask and the reading rules have to move
with it, so they live in one file.

Pure text judgement, the same kind as `verification`: no I/O, no minder
imports. The caps live here because a long answer must not smuggle a whole
transcript into the lesson queue.
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

# A panel answer's advice is what its bullet or numbered lines say; the
# synthesis header, the separator and the per-consultant notes are dropped,
# never summarised.
_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?P<action>.+)$")
_PANEL_NOISE_RE = re.compile(
    r"^(PANEL CONSULT:|SYNTHESIS \(|---$|\[[a-z0-9_.-]+\]\s)", re.IGNORECASE)
# `frontier.run_panel`'s failure strings start with "(" by contract (its
# own docstring states it), so a failed consult has no advice lines to
# read. Named here because reading the answer is this module's job.
FAILURE_PREFIX = "("
# Imperative lead-ins, commands and file/check targets read as actions; a
# sentence describing state ("the lookup misses", "the fixture never seeds")
# does not.
_ACTION_LEAD_RE = re.compile(
    r"^(?:run|use|add|check|read|write|edit|create|delete|remove|rename|move|"
    r"copy|set|export|install|replace|update|insert|call|define|import|pass|"
    r"assert|test|verify|confirm|retry|restart|rebuild|re-run|rerun|try|"
    r"switch|change|make|enable|disable|bump|pin|unset|clear|flush|seed|"
    r"wrap|guard|handle|raise|catch|log|print|dump|grep|find|open|close|"
    r"commit|push|pull|fetch|merge|rebase|stash|diff|patch|apply|start|stop|"
    r"kill|reload|source|cd|chmod|chown|mkdir|touch|cat|echo|python3?|"
    r"pip|npm|pnpm|yarn|npx|make|cargo|go|git|docker|pytest|sqlite3?)\b",
    re.IGNORECASE)
# `the lookup misses on ...`, `the fixture never seeds ...`: a state
# description, not a directive.
_CAUSE_LEAD_RE = re.compile(
    r"^(?:the|this|that|these|those|it|they|there|because|since|due to|when|"
    r"while|if|likely|probably|possibly|appears?|seems?|caused by|root cause"
    r"|most likely|missing|absent|no |not )\b", re.IGNORECASE)
# A frontier model attribution is decoration on the action line, not part of
# it. `frontier_distill` strips the same shape again before writing
# instruction text; this copy exists so an attribution lead-in ("per
# deepseek-x: add a test") does not hide an action.
_ATTRIBUTION_RE = re.compile(
    r"\b(?:per|from|by|via|according to)\s+"
    r"(?:deepseek[\w.-]*|gpt[- ]?[\w.]*|o\d[\w.-]*(?:mini|preview)?|"
    r"claude[\w.-]*|qwen[\w.-]*|llama[\w.-]*|mistral[\w.-]*|gemini[\w.-]*|"
    r"kimi[\w.-]*|glm[\w.-]*)\b[:\s]*", re.IGNORECASE)


def action_lines(answer):
    """The actionable lines of a panel answer, in order, capped at
    DISTILL_MAX_LINES. Prose, the synthesis header and the per-consultant
    notes are dropped rather than summarised; a line counts only when it
    reads as an action. Never raises."""
    text = str(answer or "")
    if not text or text.lstrip().startswith(FAILURE_PREFIX):
        return []
    out = []
    for line in text.splitlines():
        match = _LINE_RE.match(line)
        if not match or _PANEL_NOISE_RE.match(line):
            continue
        action = _clean_action(match.group("action"))
        if not action:
            continue
        out.append(action)
        if len(out) >= DISTILL_MAX_LINES:
            break
    return out


def _clean_action(text):
    """The action text a line carries, or '' when it carries none. The
    attribution is stripped here rather than kept and stripped again at
    distill time: a stored action should not carry decoration that is known
    at record time to be noise."""
    bare = _unwrap_attribution(text)
    if not bare or not is_action(bare):
        return ""
    return bare[:DISTILL_MAX_CHARS]


def is_action(text):
    """True when the text reads as something an agent can do, not as a
    description of state. Never raises."""
    bare = _unwrap_attribution(text)
    if _CAUSE_LEAD_RE.match(bare):
        return False
    return bool(_ACTION_LEAD_RE.match(bare))


def unwrap_attribution(text):
    """The text with any frontier model attribution removed and whitespace
    collapsed, so an attribution lead-in cannot hide an action."""
    return _unwrap_attribution(text)


def _unwrap_attribution(text):
    return " ".join(_ATTRIBUTION_RE.sub(" ", str(text or "")).split())


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
        "Reply with: (1) the 2-3 most likely root causes, ranked, one line "
        "each; (2) THE single next concrete action most likely to break the "
        "loop (a command, a file to read, or a check to run). Terse. No "
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
        "escalation. Reconcile them into ONE recommendation: the 2-3 most "
        "likely root causes, ranked, one line each, and THE single next "
        "concrete action. Where the consultants disagree, prefer the one "
        "that better fits the error text and keep a final line exactly "
        f"'DISAGREEMENT: <one sentence>'. Terse, max ~{CONSULT_WORDS} words.\n\n"
        f"Failed action: {payload.get('key', '?')} "
        f"(attempt {payload.get('attempts', '?')})\n"
        f"Last error (truncated): "
        f"{str(payload.get('error', ''))[:VERIFY_ERROR_CHARS]}\n\n"
        + "\n\n".join(parts)
    )
