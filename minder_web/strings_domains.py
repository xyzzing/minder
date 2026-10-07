"""Copy for the /domains page (C3, issue #8). Merged into the Jinja
global S by app.py as S.domains; same rules as minder_web.strings:
code-owned static text, light markup allowed, plain hyphens."""

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
