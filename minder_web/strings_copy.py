"""Answer and action copy for the console (issue #12).

The copy modules own everything a template renders; this module owns
the copy the Python composers use: the plain-language answer sentence
per page and the one action a page can lead a reader to. Same C3 rule -
no user data, plain hyphens, `n/a` for empty and never zero. Placeholders
use str.format braces.
"""
# The plain-language answer line (issue #12): minder_web.answers.py
# composes it from the page model. It is one block instead of a page
# section each so a reader sees every conclusion the console can state.
ANSWER = {
    "lead": "the short answer",
    "empty": "nothing to look at yet - this page has no rows. An empty "
             "store is not a zero: the evidence was never written.",
    "rows": "{count} row(s) here.",
    "rows_action": "{count} row(s) here - start with {action}.",
    "flagged": "{count} thing(s) to fix in this window - start with "
               "{action}.",
    "nothing": "nothing flagged in {window}.",
    "no_db": "no memory database to read - start the console with "
             "<code>--db</code> pointing at a minder memory sqlite.",
    "capture_broken": "capture is broken: {count} warning(s). Fix this "
                      "before trusting any other page - start with "
                      "{action}.",
    "capture_warnings": "capture has {count} warning(s) - start with "
                        "{action}.",
    "capture_ok": "capture is writing - no warnings in the last {hours}h.",
    "capture_quiet": "nothing to judge - no dsh session activity in the "
                     "last {window}h, so capture has no evidence to be "
                     "measured against.",
    "no_sink": "nothing has been recorded yet: the save path (the sink "
               "that stores captured evidence) is not configured, so no "
               "evidence reaches this console. Run install.sh to wire it.",
    "sessions_none": "no sessions to read - is <code>~/.dsh/sessions/</code>"
                     " populated?",
    "sessions_capture": "{count} session(s) - {gap} of them have no "
                        "capture link, so their evidence was never "
                        "written. Start there.",
    "sessions_ok": "{count} session(s) - all have a capture link.",
    "events_none": "no observed events - nothing has been captured yet, "
                   "so there is nothing to improve from.",
    "events_failures": "{count} observed event(s) - {failures} are tool "
                       "failures. The repeated ones are what to fix first.",
    "events_ok": "{count} observed event(s), no tool failures.",
    "difficulty_none": "no difficulty-router events in the proxy ledger.",
    "difficulty_rows": "{count} difficulty-router event(s) in the proxy "
                       "ledger.",
}

# minder_web.page_actions composes these with a count: the one thing a
# reader can do on a page, stated as an action.
PAGE_ACTIONS = {
    "episodes": "{count} episode(s) still open - open one and read its "
                "last events to see where the work stopped",
    "gaps": "{count} open skill gap(s) - the agent met a failure it has "
            "no procedure for; write or close a skill",
    "consults": "{count} consult(s) judged harmful - open one and check "
                "what it recommended",
    "decisions": "{count} decision(s) where the policy overrode the "
                 "model - that disagreement is what to review",
    "skills": "{count} skill(s) have no body file - {names} - a listed "
              "skill that cannot be loaded is never retrieved",
}

# Pages whose short answer is only "how many rows, and what to look at
# next" (minder_web.answers). One list, not a scattering of template
# names across modules.
LIST_ANSWER_KEYS = ("episodes", "lessons", "gaps", "consults",
                    "decisions", "traces", "skills", "benchmarks")
