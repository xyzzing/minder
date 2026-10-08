"""The ask and the reading of a frontier answer are one contract (issue #11).

Answers are captured from the real panel and kept here as fixtures: minder
stores `response_hash`, not answer text, so nothing in the store can be
replayed to regenerate them.
"""
import re

import frontier

# The panel's text layer is minder_core.panel_text (the asks) plus
# minder_core.panel_answer (the reading) after this change, and inline in
# frontier.py before it. Resolve it either way, without importing a module the
# pre-change tree lacks, so proven-red reports assertion failures rather than a
# collection error.
panel_text = getattr(frontier, "_panel", None) or frontier
panel_answer = getattr(frontier, "_answer", None) or panel_text

# Root cause #1 and the next action from one real consult, verbatim.
REAL_ANSWER = (
    "1. The agent is retrying the same action unchanged despite a "
    "deterministic failure; no input/path/tool is being mutated.\n"
    "2. The truncated error is preventing correct error handling, so the "
    "agent cannot select an alternative or fix.\n"
    "3. A missing prerequisite (binary, env var, file, permission) makes the "
    "action fail identically on every retry.\n"
    "\n"
    "Next action: read the full untruncated last attempt log, e.g. "
    "`tail -n 200 /root/.codex/log/latest.log` (or equivalent agent log), and "
    "do not re-run the action until the exact failed action and full error are "
    "inspected."
)

# A second real consult, same shape, markdown emphasis on the label.
REAL_ANSWER_BOLD = (
    "1. Retry without mutation: the same action is repeatedly executed with "
    "identical inputs/preconditions, so it deterministically fails.\n"
    "2. Environment/resource exhaustion: disk full, memory pressure, stale "
    "lock, dead socket, expired creds/token, or missing file causes the action "
    "to fail regardless of retries.\n"
    "3. Watchdog/state mismatch: prior partial success left state "
    "inconsistent; the step is not idempotent and cannot proceed.\n"
    "\n"
    "**Next action:** Pull the actual failed action and full error from the "
    "watchdog log/state - e.g. `journalctl -u <agent-watchdog> -n 200 "
    "--no-pager` or read the agent's last-run state file."
)

# A third real consult, captured from the live panel (issue #11). The answer
# mirrors the ask's two parts as numbered headings and writes the action on
# the line *after* its label. A reader that only looks at the labelled line
# finds an empty one and keeps nothing.
REAL_ANSWER_HEADING = (
    "1. Ranked root causes:\n"
    "- Empty/truncated error hides real failure; agent is retrying the same "
    "command with unchanged state, so failure is deterministic.\n"
    "- Stale lockfile/artifact or flaky external dependency not reset between "
    "attempts.\n"
    "- Missing credential/env var in the agent\u2019s non-interactive shell.\n"
    "\n"
    "2. Next action:\n"
    "Run `tail -n 300 \"$(ls -t ~/.coding-agent/logs/*.log 2>/dev/null | "
    "head -1)\"` and read the full untruncated error before retrying."
)

# Two consecutive live consults, distilled through the production path, put a
# root cause in the lesson instruction. Both answers named their action with
# `Next action:`; both numbered causes open with a word the imperative verb
# list happens to contain (`Test ...`), and the named action opened with one
# it does not (`inspect ...`). The reading tested the verbs instead of the
# shape the ask had already specified.
NAMED_ACTION_LIVE = (
    "1. Test fixture/input row lacks `supplier_id`, so the lookup path never "
    "has the key to return.\n"
    "2. The schema defines the column but the migration is not applied.\n"
    "3. A cached row answers before the query runs.\n"
    "\n"
    "Next action: inspect the fixture's seed rows and the list of applied "
    "migrations."
)

CAUSES_ONLY = (
    "1. The fixture never seeds supplier_id.\n"
    "2. The lookup reads a column the schema does not define.\n"
    "3. A stale cache answers before the database is consulted."
)


def _read(answer):
    """The reading under test. `read_answer` returns causes and actions
    separately; `action_lines` is the flat reading that made a cause and an
    empty answer look the same. Call whichever the tree has, so this file
    runs on pre-change code and proves the bug there."""
    reader = getattr(panel_answer, "read_answer", None)
    if reader is None:
        actions = panel_answer.action_lines(answer)
        # The flat reading has no causes, so the split cannot be asserted
        # against it; the action half still can, and that is what fails.
        return {"causes": [], "actions": actions}
    return reader(answer)


def _actions(answer):
    return _read(answer)["actions"]


def _causes(answer):
    reader = getattr(panel_answer, "read_answer", None)
    assert reader is not None, "the reading does not separate causes at all"
    return reader(answer)["causes"]


def test_a_cause_only_answer_still_reports_its_causes():
    """The live symptom, end to end: the panel answered with causes and a
    next action, `distilled_json` stayed NULL, and the lesson was lost.
    Causes are not the bug; an empty action list against non-empty causes
    is. A cause-only answer must say so rather than look like an empty one."""
    assert _actions(CAUSES_ONLY) == []
    assert len(_causes(CAUSES_ONLY)) == 3


def test_a_real_answer_reports_both_halves_of_the_shape():
    for answer in (REAL_ANSWER, REAL_ANSWER_BOLD):
        assert _actions(answer), "the stated next action was not distilled"
        assert len(_causes(answer)) == 3, "the answer's causes were discarded"



def test_a_stated_next_action_is_distilled():
    """The ask promises one next action; the reading must be able to see it
    written as a heading line, not only as a bullet."""
    actions = _actions(REAL_ANSWER)
    assert actions, "the answer states a next action and nothing was distilled"
    assert actions[0].startswith("read the full untruncated last attempt log")


def test_markdown_emphasis_on_the_label_does_not_hide_the_action():
    assert _actions(REAL_ANSWER_BOLD), "an emphasised label hid the action"
    assert _actions(REAL_ANSWER_BOLD)[0].startswith(
        "Pull the actual failed action")


def test_a_cause_labelled_with_an_imperative_verb_is_not_an_action():
    """`Retry without mutation: the same action is repeatedly executed` is a
    root cause. Its first word is a listed verb, which is exactly the trap."""
    for answer in (REAL_ANSWER, REAL_ANSWER_BOLD):
        for action in _actions(answer):
            assert "repeatedly executed with identical inputs" not in action


def test_an_action_written_below_its_own_label_heading_is_distilled():
    """The ask says to write the action on its own line starting `Next
    action:`. A real answer makes that line a heading and puts the command
    under it, so the label line is empty and the action is one line later."""
    actions = _actions(REAL_ANSWER_HEADING)
    assert len(actions) == 1, (
        f"expected the stated command, got {actions!r}")
    assert actions[0].startswith("Run `tail -n 300")
    # The part headings the answer copied from the ask are structure, not
    # causes: a cause list that starts with "Ranked root causes:" tells the
    # operator nothing.
    assert not any(c.rstrip().endswith(":") for c in _causes(
        REAL_ANSWER_HEADING)), _causes(REAL_ANSWER_HEADING)
    assert len(_causes(REAL_ANSWER_HEADING)) == 3


def test_an_answer_that_names_its_action_distills_only_that_action():
    """The ask asks for THE single next action, labelled. When the answer
    supplies one, the other lines are the ranked causes by construction -
    no verb list gets a vote on them. This is the live false positive: a
    numbered cause opening with a listed verb (`Test fixture ... lacks`) was
    distilled as the lesson instruction while the named action, opening with
    a verb the list lacks (`inspect ...`), was dropped."""
    actions = _actions(NAMED_ACTION_LIVE)
    assert actions == ["inspect the fixture's seed rows and the list of "
                       "applied migrations."], actions
    assert len(_causes(NAMED_ACTION_LIVE)) == 3, _causes(NAMED_ACTION_LIVE)


def test_a_cause_only_answer_yields_no_action():
    assert _actions(CAUSES_ONLY) == []


def test_causes_and_actions_are_distinguishable_at_the_call_site():
    """An answer that carried only causes must not look like one that carried
    nothing."""
    reader = getattr(panel_answer, "read_answer", None)
    assert reader is not None, "causes and actions are not separated"
    causes_only = reader(CAUSES_ONLY)
    assert causes_only["actions"] == []
    assert causes_only["causes"], "causes were discarded with the shape"


def test_the_ask_and_the_reading_name_one_answer_shape():
    """The prompts state the shape the reading reads. If either side rewords
    the contract, this fails rather than drifting silently."""
    shape = panel_text.ANSWER_SHAPE
    consult = panel_text.consult_prompt({"key": "k", "attempts": 3,
                                         "error": "boom"})
    synth = panel_text.synthesis_prompt({"key": "k", "attempts": 3,
                                         "error": "boom"},
                                        [("a", "1. cause\n\nNext action: x"),
                                         ("b", "1. cause\n\nNext action: y")])
    for label in ("root causes", "Next action:"):
        assert label in shape, label
        assert label in consult, label
        assert label in synth, label
    # The reader tolerates more labels than the ask writes; the ask's own
    # label is the one the shape must name.
    assert re.search(r"next action", shape, re.IGNORECASE)


def test_the_panel_notes_are_read_but_never_distilled():
    """`run_panel` appends each consultant's answer under a separator. Those
    notes are answer text an operator reads, not the merged recommendation,
    so they may be causes but must never become actions."""
    noted = ("SYNTHESIS (merged, disagreements flagged):\n"
             "Next action: run pytest tests/test_supplier.py\n---\n"
             "[probe-a] Next action: run it again with -x\n")
    read = panel_answer.read_answer(noted)
    assert read["actions"] == ["run pytest tests/test_supplier.py"]
    assert not any("again with -x" in a for a in read["actions"])
def test_the_action_label_being_stripped_cannot_invent_an_action():
    """`Next, the fixture is missing` is prose about state. Stripping a label
    must not turn it into a directive."""
    assert _actions("Next, the fixture is missing its supplier_id.") == []
    assert _actions("Next action: the fixture is missing its supplier_id.") == []
