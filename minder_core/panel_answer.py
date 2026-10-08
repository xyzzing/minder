"""Reading a frontier panel answer: which lines are actions, which are causes.

`minder_core.panel_text` states what the panel is asked for; this module
decides what came back. The distinction is load-bearing rather than tidy: the
candidate lesson queue is built from the actions, so a line that describes how
things are (a root cause) must never be stored as lesson instruction. Reading
one list, as this module used to, made "the answer carried nothing" and "the
answer carried only causes" the same result at the call site.

The answer's own structure decides first: a line the answer labelled is the
action it named, and the rest are its causes. The imperative verb list is the
fallback for an answer that ignores the shape, and the only signal left then.

Pure text judgement: no I/O, no minder imports. Never raises on malformed
input; a failed consult has no advice lines to read and says so through
`FAILURE_PREFIX`.
"""
import re

from .panel_text import (ACTION_LABELS, ASK_LABEL_RE, CAUSE_MAX_LINES,
                         DISTILL_MAX_CHARS, DISTILL_MAX_LINES)


# The two parts of ANSWER_SHAPE, as the answer may write them: a label on a
# line of its own, with its content below. Real answers copy the ask's
# wording, so "1. Ranked root causes:" and "2. Next action:" both appear.
_CAUSE_HEADING_RE = re.compile(
    r"^\s*(?:(?:\d+[.)]|[-*])\s*)?(?:\*\*|__)?\s*"
    r"(?:\S+\s+){0,4}?"          # Ranked / the / 2-3 / most likely: a prefix, not an item
    r"(?:root\s+)?causes?\b"     # the part the ask names
    r"[^\n]*[:\u2013]\s*$",      # a heading ends at its colon
    re.IGNORECASE)
_ACTION_HEADING_RE = re.compile(
    r"^(?:\d+[.)]\s*)?(?:\*\*|__)?\s*(?:"
    + "|".join(re.escape(label) for label in ACTION_LABELS)
    + r")\s*(?:\*\*|__)?\s*[:\u2013]\s*$", re.IGNORECASE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
_ACTION_LABEL_RE = re.compile(
    r"^(?:\d+[.)]\s*)?(?:\*\*|__)?\s*(?:"
    + "|".join(re.escape(label) for label in ACTION_LABELS)
    + r")\s*(?:\*\*|__)?\s*[:\u2013]\s*",
    re.IGNORECASE)
# A cause is labelled too - "Retry without mutation: the same action is
# repeatedly executed" - and the label ends where the state description
# begins. The colon is the boundary the answer itself draws, so the directive
# is judged on the text before it and never on the clause after it.
_DIRECTIVE_CLAUSE_MAX = 60
# A directive clause names something to do. A cause clause names a state, and
# says so with a copula or a consequence verb. Deliberately a small, closed
# list: an answer that describes a state usually says so in these words, and a
# word list that grows to cover every sentence stops discriminating.
_STATE_TELL_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|causes?|leaves?|fails?|deterministically|"
    r"regardless|identical|inconsistent|exhaustion|mismatch|preconditions)\b",
    re.IGNORECASE)

# A panel answer's advice is what its bullet, numbered or labelled lines say.
# A line carrying the action label counts even unbulleted: the ask names that
# label, and real answers write it as a heading line.
_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?P<action>(?:\*\*|__)?\s*\S.*)$")
_BARE_LINE_RE = re.compile(r"^\s*(?P<action>\S.*)$")
# The panel's own apparatus, which is not an answer: the merged header, the
# synthesis header, and the separator that closes the merged recommendation
# and opens each consultant's note. Everything after the separator is a note
# an operator reads, never advice to distill - a consultant's own `Next
# action:` line would otherwise join the merged one in the lesson queue.
_PANEL_NOISE_RE = re.compile(
    r"^(PANEL CONSULT:|SYNTHESIS \()", re.IGNORECASE)
_NOTES_SEPARATOR_RE = re.compile(r"^\s*-{3,}\s*$")
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

def read_answer(answer):
    """The panel answer split into the two things the ask asked for: its
    causes and its actions, each in order. Returns {"causes": [...],
    "actions": [...]}.

    Splitting rather than returning one list is the point: at the call site
    "the answer carried nothing" and "the answer carried only causes" used to
    look identical, which is how a root cause ended up stored as lesson
    instruction.

    The split follows the answer's own structure, not a guess about English.
    `ANSWER_SHAPE` asks for ranked causes plus one labelled action, so when an
    answer carries that label - on its line or as a heading with the command
    below it - the labelled text is the action and every other item is one of
    the causes, whatever its first word is. A numbered cause opening with a
    listed verb ("Test fixture ... lacks the key") was previously distilled as
    the instruction while the labelled action opening with an unlisted verb
    ("inspect the seed rows") was dropped: the verb list voted on lines the
    answer had already labelled. The list only decides when the answer ignores
    the shape, and then it is the only signal left.

    Each list carries its own cap: the action cap stops a long answer
    smuggling a whole transcript into the lesson queue, and the ask only ever
    requests a handful of causes. Never raises."""
    text = str(answer or "")
    empty = {"causes": [], "actions": []}
    if not text or text.lstrip().startswith(FAILURE_PREFIX):
        return empty
    items = _answer_items(text)
    named = [_clean_named_action(item) for item, labelled in items if labelled]
    named = [action for action in named if action]
    if named:
        actions = named[:DISTILL_MAX_LINES]
        causes = [_clean_text(item) for item, labelled in items if not labelled]
        return {"causes": [c for c in causes if c][:CAUSE_MAX_LINES],
                "actions": actions}
    causes, actions = [], []
    for item, _labelled in items:
        action = _clean_action(item)
        if action:
            if len(actions) < DISTILL_MAX_LINES:
                actions.append(action)
            continue
        cause = _clean_cause(item)
        if cause and len(causes) < CAUSE_MAX_LINES:
            causes.append(cause)
        if len(actions) >= DISTILL_MAX_LINES and len(causes) >= CAUSE_MAX_LINES:
            break
    return {"causes": causes, "actions": actions}


def _answer_items(text):
    """The answer's items as (text, named_by_the_answer_itself) pairs, in
    order. A line is an item when it carries a list marker or the label the
    ask writes; a part heading ("2. Next action:") is structure, not an item,
    and opens a part whose content is the line below it. The panel's own
    apparatus - the merged header and the per-consultant notes after the
    separator - is not part of the answer at all."""
    items = []
    part = ""
    for line in text.splitlines():
        if _NOTES_SEPARATOR_RE.match(line):
            # The merged recommendation ends here; what follows is each
            # consultant's own answer, kept for the operator to read.
            break
        if _PANEL_NOISE_RE.match(line):
            continue
        if _CAUSE_HEADING_RE.match(line):
            part = ""
            continue
        if _ACTION_HEADING_RE.match(line):
            part = "action"
            continue
        item = _line_item(line)
        labelled = bool(item) and bool(ASK_LABEL_RE.match(item))
        if not item:
            if part == "action" and _BARE_LINE_RE.match(line):
                item = _LIST_MARKER_RE.sub("", line).strip()
                labelled = True
            else:
                continue
        part = ""
        items.append((item, labelled))
    return items


def action_lines(answer):
    """The actionable lines of a panel answer, in order, capped at
    DISTILL_MAX_LINES. Prose, the synthesis header and the per-consultant
    notes are dropped rather than summarised; a line counts only when it
    reads as an action. Never raises."""
    return read_answer(answer)["actions"]


def _line_item(line):
    """The advice a line carries, or '' when it carries none. A bullet or a
    number is the answer's own list marker; a line with no marker still
    carries advice when it opens with the label the ask names, because that is
    how real answers write the next action - as a heading, not a bullet."""
    match = _LINE_RE.match(line)
    if match:
        return match.group("action")
    match = _BARE_LINE_RE.match(line)
    if match and ASK_LABEL_RE.match(match.group("action")):
        return match.group("action")
    return ""


def _clean_text(text):
    """The item with its label and attribution removed, and nothing else. Used
    for lines the answer already assigned to a part: their text is kept as
    written, because the answer, not a heuristic, decided what they are."""
    return _unwrap_attribution(_strip_action_label(text))[:DISTILL_MAX_CHARS]


def _clean_action(text):
    """The action text a line carries, or '' when it carries none. The verb
    list decides: this is a line the answer did not label, so its wording is
    all there is to go on. Never raises."""
    bare = _unwrap_attribution(_strip_action_label(text))
    if not bare or not is_action(bare):
        return ""
    return bare[:DISTILL_MAX_CHARS]


def _clean_named_action(text):
    """The action text a labelled line carries, or '' when the label is a lie.
    The answer wrote `Next action:` itself, so the verb list has no vote: real
    actions open with verbs no list holds (`inspect`, `tail`, `diff`). What is
    still refused is a label over a state description - the cause case, where
    the text after the label is a sentence about how things are."""
    bare = _unwrap_attribution(_strip_action_label(text))
    if not bare or _CAUSE_LEAD_RE.match(bare) or _is_labelled_cause(bare):
        return ""
    return bare[:DISTILL_MAX_CHARS]


def _clean_cause(text):
    """The cause text a line carries, or '' when it carries none. A cause is
    what the failure is, not what to do about it. The reading is asymmetric on
    purpose: a line the imperative test calls an action is not re-read as a
    cause, because that is how a root cause became lesson instruction. A line
    it rejects is a cause whatever its first words are - `The fixture never
    seeds supplier_id` and `A stale cache answers before the database is
    consulted` are both causes, and dropping the second would hide the very
    signal this split exists to show: causes present, action absent."""
    bare = _unwrap_attribution(_strip_action_label(text))
    if not bare or is_action(bare):
        return ""
    return bare[:DISTILL_MAX_CHARS]


_EMPHASIS_RE = re.compile(r"^(?:\*\*|__|\*|_|`)+|(?:\*\*|__|\*|_|`)+$")


def _strip_action_label(text):
    """The text with a leading action label removed, and any emphasis or list
    punctuation left around it. The label match needs the emphasis to close it
    (`**Next action:**`), so a bullet that never opened one (`** Pull the ...`)
    is trimmed here rather than left in the stored text."""
    out = _ACTION_LABEL_RE.sub("", str(text or ""), count=1).strip()
    return _EMPHASIS_RE.sub("", out).strip()


def is_action(text):
    """True when the text reads as something an agent can do, not as a
    description of state. Never raises."""
    bare = _unwrap_attribution(_strip_action_label(text))
    if _CAUSE_LEAD_RE.match(bare):
        return False
    return bool(_ACTION_LEAD_RE.match(bare)) and not _is_labelled_cause(bare)


def _is_labelled_cause(text):
    """True when a colon or dash splits an imperative label from a sentence
    about how things are. `Retry without mutation: the same action is
    repeatedly executed` is a root cause wearing an imperative first word, and
    a first-word test promotes it into lesson instruction. The boundary the
    answer drew is the evidence; the clause after it names a state."""
    parts = re.split(r"[:;\u2014\u2013]", text, maxsplit=1)
    if len(parts) == 1:
        return False
    head, tail = parts[0].strip(), parts[1]
    if not _ACTION_LEAD_RE.match(head):
        return False
    if len(head) >= _DIRECTIVE_CLAUSE_MAX:
        # No boundary inside a long directive: `read the log, e.g. ...` is one
        # instruction, not a label over a state description.
        return False
    return bool(_STATE_TELL_RE.search(tail))


def unwrap_attribution(text):
    """The text with any frontier model attribution removed and whitespace
    collapsed, so an attribution lead-in cannot hide an action."""
    return _unwrap_attribution(text)


def _unwrap_attribution(text):
    return " ".join(_ATTRIBUTION_RE.sub(" ", str(text or "")).split())
