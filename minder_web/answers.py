"""Plain-language page answers (issue #12).

Every console page states its conclusion in one sentence before any
table. The sentence is composed here from the page model the service
already returns, so the template stays logic-free and the copy stays in
strings_base.py (C3). A count of zero is a fact, never hidden; an unavailable
source says so and never reads as zero.
"""
from minder_web import strings_base, strings_copy
from minder_web.strings_pages import DOMAINS as _DOMAIN_STRINGS

_LEVEL_WARN = "warn"
_LEVEL_OK = "ok"


def _first_action(values):
    for value in values or ():
        text = str(value).strip()
        if text:
            return text
    return None


def _answer_table(template):
    """The answer copy for a page: the shared block, or the page's own
    block when its section file carries one (domains)."""
    section = strings_base.S.get(template)
    if isinstance(section, dict) and "answer" in section:
        return section["answer"]
    return strings_copy.ANSWER


def _text(key, facts, level, action=None, table=None):
    """Compose one answer from a strings entry. A placeholder the caller
    cannot fill falls back to a variant that needs no action clause, so a
    page can never 500 on its own summary line and never prints an
    unfilled `{...}` to the reader unless no variant fits at all.
    `table` names the strings namespace to read: the shared `answer` copy,
    or a page's own section when the sentence is specific to that page."""
    table = strings_copy.ANSWER if table is None else table
    fields = dict(facts)
    if action is not None:
        fields["action"] = action
    # order: the action variant when an action exists, then the plain one,
    # so a page that has copy for both picks the sentence it can fill
    order = (key + "_action", key) if action is not None else (key,
                                                               key + "_action")
    body = table.get(key, "")
    for name in [n for n in order if n in table]:
        try:
            body = table[name].format(**fields)
            break
        except (KeyError, IndexError):
            body = table[name]
    return {"lead": strings_copy.ANSWER["lead"], "body": body,
            "level": level}


def _rows_answer(ctx):
    count = len(ctx.get("rows") or ())
    if not count:
        return _text("empty", {"count": 0}, _LEVEL_WARN)
    return _text("rows", {"count": count}, _LEVEL_OK,
                 _first_action(ctx.get("actions")))


def _skills_answer(ctx):
    """/skills answers with two numbers, not one (issue #40): how many
    skills are installed, and how many of them have a registered trigger.
    The second is the one that decides whether a skill can ever fire, and
    a single row count conflated them - 45 installed read as 5.

    Both sentences carry the action clause: the number of skills that are
    visible but cannot fire is the page's real finding, and a reader who
    is told only how many exist cannot act on the difference."""
    table = _answer_table("skills")
    installed = int(ctx.get("installed") or 0)
    covered = int(ctx.get("covered") or 0)
    facts = {"installed": installed, "covered": covered,
             "unused": installed - covered}
    if not installed:
        return _text("empty", facts, _LEVEL_WARN, table=table)
    if not covered:
        return _text("no_cover", facts, _LEVEL_WARN,
                     _first_action(ctx.get("actions")), table=table)
    return _text("rows", facts, _LEVEL_OK,
                 _first_action(ctx.get("actions")), table=table)


def answer_for(template, ctx):
    """The page's short answer, or None when the page has no summary to
    lead with (detail pages carry their own conclusion line)."""
    if not isinstance(ctx, dict):
        return None
    if template == "overview":
        return _overview_answer(ctx)
    if template == "scorecard":
        return _scorecard_answer(ctx)
    if template == "sessions":
        return _sessions_answer(ctx)
    if template == "events":
        return _events_answer(ctx)
    if template == "difficulty":
        return _difficulty_answer(ctx)
    if template == "domains":
        return _domains_answer(ctx)
    if template == "skills":
        return _skills_answer(ctx)
    if template == "capture":
        warnings = ctx.get("warnings") or ()
        if _capture_is_down({"sink": ctx.get("sink")}):
            return _text("capture_warnings", {"count": len(warnings)},
                         _LEVEL_WARN, _first_action(warnings))
        if not warnings:
            return _text("capture_ok", {"hours": ctx.get("window_hours")},
                         _LEVEL_OK)
        return _text("capture_quiet", {"window": ctx.get("window_hours")},
                     _LEVEL_WARN)
    if template == "benchmarks":
        return _rows_answer({"rows": ctx.get("suites")
                             if isinstance(ctx.get("suites"), list) else [],
                             "actions": ()})
    if template == "engine":
        return _engine_answer(ctx)
    if template in strings_copy.LIST_ANSWER_KEYS:
        return _rows_answer(ctx)
    return None


def _engine_answer(ctx):
    """Which engine is answering, and which configured engine is not.
    An unhealthy engine is not an emergency today - the active one may be
    perfectly fine - but it is the one that will fail requests the moment
    it is switched to, so the page says so before the table."""
    rows = ctx.get("rows") or ()
    if not rows:
        return _text("engine_none", {"count": 0}, _LEVEL_WARN)
    active = next((r for r in rows if r.get("active")), None)
    broken = [str(r.get("name") or "?") for r in rows if not r.get("healthy")]
    name = str((active or {}).get("name") or strings_base.UNKNOWN)
    if broken:
        return _text("engine_unhealthy",
                     {"name": name, "count": len(rows),
                      "names": ", ".join(broken)}, _LEVEL_WARN)
    return _text("engine_active", {"name": name, "count": len(rows)},
                 _LEVEL_OK)


def _capture_is_down(capture):
    """Capture is broken when evidence is being lost, and the report says
    that directly: a sink that is configured but not answering is dropping
    hook writes. A sink that was never configured has nothing to drop -
    that is the empty-store state, and the console names it in its own
    words rather than as a fault (issue #12). Reading the sink's own
    fields, not its warning strings, keeps this honest if the wording
    changes."""
    if capture is None or capture.get("ok"):
        return False
    sink = capture.get("sink") or {}
    return bool(sink.get("configured")) and not sink.get("reachable")


def _evidence_through_window(ctx):
    """True when the window actually held evidence to score. The capture
    report reads the live stores and sessions, not the page's own sqlite,
    so an empty store on a busy machine is not an empty window. Each
    source names its own count, so the check reads the counts, not a
    summary of them."""
    capture = ctx.get("capture") or {}
    if (capture.get("coverage") or {}).get("invocations"):
        return True
    summary = ctx.get("summary") or {}
    for group in ("episodes", "failures", "lessons", "gaps", "consults",
                  "decisions"):
        if _has_count((summary.get(group) or {}).values()):
            return True
    return False


def _has_count(values):
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, (list, dict)) and value:
            return True
    return False


def _quiet_window(ctx):
    """No evidence to judge, and nothing actively broken. An empty store
    is a real state and must be named as one, never as an all clear."""
    if _evidence_through_window(ctx):
        return None
    capture = ctx.get("capture") or {}
    if not (capture.get("sink") or {}).get("configured"):
        return _text("no_sink", {}, _LEVEL_WARN)
    if capture and not capture.get("ok"):
        return _text("capture_quiet",
                     {"window": capture.get("window_hours")}, _LEVEL_WARN)
    return _text("empty", {"count": 0}, _LEVEL_WARN)


def _overview_answer(ctx):
    if not ctx.get("db_ok"):
        return _text("no_db", {}, _LEVEL_WARN)
    capture = ctx.get("capture")
    if _capture_is_down(capture):
        warnings = capture.get("warnings") or ()
        return _text("capture_broken", {"count": len(warnings)},
                     _LEVEL_WARN, _first_action(warnings))
    quiet = _quiet_window(ctx)
    if quiet is not None:
        return quiet
    # verdicts are the scorecard's actionable list; an unconfigured sink
    # is in it, and the quiet window above already says that in plainer
    # words, so the list only leads once there is evidence to act on
    verdicts = ctx.get("verdicts") or ()
    if verdicts:
        return _text("flagged", {"count": len(verdicts)}, _LEVEL_WARN,
                     _first_action(verdicts))
    return _text("nothing", {"window": ctx.get("window_text")}, _LEVEL_OK)


def _scorecard_answer(ctx):
    # "nothing flagged" is only reassuring when there was something to
    # score; an empty window is its own state, not an all clear
    quiet = _quiet_window({"capture": (ctx.get("evidence") or {}).get(
                               "capture")})
    if quiet is not None:
        return quiet
    focus = ctx.get("focus") or ()
    if focus:
        return _text("flagged", {"count": len(focus)}, _LEVEL_WARN,
                     _first_action(focus))
    return _text("nothing", {"window": f"{ctx.get('window_hours')}h"},
                 _LEVEL_OK)


def _sessions_answer(ctx):
    rows = ctx.get("rows") or ()
    if not rows:
        return _text("sessions_none", {}, _LEVEL_WARN)
    uncaptured = ctx.get("uncaptured")
    if uncaptured:
        return _text("sessions_capture",
                     {"count": len(rows), "gap": uncaptured}, _LEVEL_WARN)
    return _text("sessions_ok", {"count": len(rows)}, _LEVEL_OK)


def _events_answer(ctx):
    rows = ctx.get("rows") or ()
    if not rows:
        return _text("events_none", {}, _LEVEL_WARN)
    failures = ctx.get("failures")
    if failures:
        return _text("events_failures", {"count": len(rows),
                                         "failures": failures},
                     _LEVEL_WARN)
    return _text("events_ok", {"count": len(rows)}, _LEVEL_OK)


def _domains_answer(ctx):
    profile = ctx.get("profile") or {}
    suites = (ctx.get("suites") or {}).get("rows") or ()
    # the sentence names this page's own data, so the copy lives in the
    # domains string section rather than the shared answer table
    table = _DOMAIN_STRINGS
    if not profile.get("name"):
        return _text("answer_no_profile", {}, _LEVEL_WARN, table=table)
    if not suites:
        return _text("answer_no_suites", {"name": profile.get("name")},
                     _LEVEL_WARN, table=table)
    return _text("answer_suites", {"name": profile.get("name"),
                                   "count": len(suites)}, _LEVEL_OK,
                 table=table)


def _difficulty_answer(ctx):
    rows = ctx.get("rows") or ()
    if not rows:
        return _text("difficulty_none", {}, _LEVEL_WARN)
    return _text("difficulty_rows", {"count": len(rows)}, _LEVEL_OK)
