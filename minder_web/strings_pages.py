"""Copy for the pages whose sections were too large for the main
copy module: /domains and /difficulty (C3, issue #8). app.py merges
these dicts into the Jinja global as S.domains and S.difficulty; same
rules as minder_web.strings_base: code-owned static text, light markup
allowed, plain hyphens."""

DOMAINS = {
    "title": "domains",
    "gloss": "(what minder knows about your field, and how trustworthy"
             " it is)",
    "profile": "your domain profile",
    "profile_gloss": "(a profile tunes budgets - it never changes what"
                     " the kernel does)",
    "profile_active": "active profile",
    "profile_none": "coding (default - no profile overlay is set)",
    "profile_available": "profiles installed",
    "profile_note": "profiles are set by hand in"
                    " <code>minder.json</code> (the \"profile\" key); the"
                    " console shows the effective values read-only.",
    "budget": "thinking budget (level 1)",
    "budget2": "thinking budget (level 2)",
    "guardrail": "spend guardrail (tokens per session)",
    "frontier": "second opinions (frontier escalations)",
    "router": "difficulty router",
    "suites": "eval suites",
    "suites_gloss": "(deterministic tests per domain - a suite passing"
                    " here means the oracle-graded rate held, not that"
                    " any model is good)",
    "col_domain": "domain",
    "col_suite": "suite",
    "col_tasks": "tasks",
    "col_status": "status",
    "col_verdict": "what this means",
    "suites_empty": "no suites found.",
    "answer_suites": "the domain rulebook is active ({name}) with {count}"
                     " suite(s) registered - the tables below are the"
                     " evidence behind that.",
    "answer_no_suites": "the domain rulebook is active ({name}) but no"
                        " suite is registered for it, so nothing here is"
                        " measurable yet.",
    "answer_no_profile": "no domain profile is active, so this page has"
                         " nothing to compare against - a domain suite"
                         " needs <code>minder.json</code> to name one.",
    "verdict_invalid": "manifest invalid - fix it before trusting any"
                       " run of this suite",
    "verdict_no_baseline": "no pinned baseline yet - runs are not"
                           " comparable until one is pinned",
    "verdict_baseline": "pinned baseline {date}: {rate}% verified over"
                        " {runs} comparable runs",
    "knowledge": "knowledge provenance",
    "knowledge_gloss": "(where the domain facts come from - every rule"
                       " is graded)",
    "col_rule": "rule",
    "col_verification": "grade",
    "col_verified_on": "verified on",
    "col_caveat": "caveat",
    "grade_primary": "primary",
    "grade_primary_gloss": "read from the authoritative source",
    "grade_secondary": "secondary",
    "grade_secondary_gloss": "re-verify before relying on it externally",
    "grade_convention": "convention",
    "grade_convention_gloss": "standard practice, not a published rule",
    "knowledge_empty": "rulebook not available in this install.",
    "suite_unit": "domain eval suite",
    "knowledge_unit": "domain rule",
    "routing": "routing",
    "routing_gloss": "(does the router change requests?)",
    "routing_off": "off - every request keeps its configured effort",
    "routing_shadow": "shadow - proposals are logged, nothing is rerouted"
                      " yet",
    "routing_active": "active - bands adjust effort per request",
    "note": "improvement claims need a pinned baseline comparison"
            " (<code>minder-op benchmark compare</code>), never console"
            " activity. The kernel is the same for every domain: a"
            " domain is one profile plus one rule-anchored suite.",
}


SESSIONS = {
    "title": "sessions",
    "gloss": "(dsh sessions joined with projections, workspace and episodes)",
    "note_counts": "{sessions} session(s) · {projection} with a projection"
                   " cache · {archived} archived · showing {shown} ({sort}"
                   " first).",
    "note_ids_pre": "Session ids are dsh's own (<code>~/.dsh/sessions/</code>)"
                    " - the same id the hook records and"
                    " <code>episodes.task_id</code> carries.",
    "legend": "tokens = words of compute the session used (exact number on"
              " hover); context = how full the AI's working memory was (over"
              " 80% means it starts forgetting); capture = whether minder"
              " saved this session's evidence.",
    "filter_placeholder": "filter by path, title or id",
    "filter": "filter", "reset": "reset",
    "col_project": "project", "col_title": "title / session",
    "col_last_seen": "last seen", "col_turns": "turns",
    "col_steps": "steps", "col_tokens": "tokens",
    "col_context": "context", "col_sandbox": "sandbox",
    "col_capture": "capture",
    "unit": "dsh session",
    "sandbox_explain": {
        "danger-full-access": "no file sandbox - this session could "
                              "write or delete anything the account "
                              "can reach",
        "workspace-write": "writes limited to the working directory",
        "read-only": "no writes at all",
    },
    "badge_archived": "archived",
    "badge_no_projection": "no projection", "badge_none": "none",
    "ep": "ep", "ev": "ev",
    "empty": "no sessions found - is <code>~/.dsh/sessions/</code> populated?",
    "note_none": "\"none\" means no episode or observed event is linked to"
                 " that session yet. When a session has hook invocations but"
                 " nothing captured, open <a href=\"/capture\">capture</a>,"
                 " which compares the two directly.",
}

DIFFICULTY = {
    "title": "difficulty router",
    "gloss": "(laya decision layer - proxy ledger)",
    "event_one": "event", "event_many": "events",
    "unit": "router decision",
    "in_ledger": "in ledger",
    "col_ts": "ts", "col_kind": "kind", "col_label": "label",
    "col_score": "score", "col_confidence": "confidence",
    "col_band": "band", "col_effort": "effort", "col_budget": "budget",
    "col_max_tokens": "max_tokens", "col_guardrail": "guardrail",
    "shadow": "shadow", "routed": "routed", "skipped": "skipped",
    "col_reason": "reason",
    "empty": "no difficulty events yet - the router logs here once it runs"
             " (shadow mode observes without changing the request).",
    "skipped_note": "<code>skipped</code> rows are the router abstaining,"
                    " each with its reason. <code>client_effort</code> means"
                    " a client-declared effort outranked it: the approved"
                    " precedence, and in every mode except <code>laya</code>"
                    " the reason an enabled router changes nothing.",
    "note": "read-only view of the proxy's <code>events.jsonl</code> ledger."
            " <code>shadow</code> rows are laya's difficulty opinion logged"
            " without touching the request; <code>routed</code> rows show the"
            " band actually applied (effort / thinking-budget / max-tokens"
            " ceiling / spend guardrail). Nothing here feeds policy.",
}

PAGES = {"domains": DOMAINS, "sessions": SESSIONS,
         "difficulty": DIFFICULTY}
