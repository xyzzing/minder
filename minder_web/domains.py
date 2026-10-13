"""Read model for the /domains page (issue #8).

Read-only, like the rest of the console: the active profile overlay,
the eval suites and their pinned baselines, the domain rulebook's
provenance grades, and the difficulty-router mode. Every source can be
missing or malformed - each section degrades to a named "not
available" instead of a 500. No SQL here and no policy: config comes
from minder.cfg(), suites from minder_op.benchmark, rules from
minder_domain_evals.rulebook.
"""
import json
import os

from minder_op import benchmark as bench
from minder_op import format as fmt

from minder_web.strings_base import NOT_AVAILABLE

# profile knobs shown on the page, in display order: (config key,
# strings_pages label key)
PROFILE_ROWS = (("l1_budget", "budget"), ("l2_budget", "budget2"),
                ("spend_guardrail_tokens", "guardrail"),
                ("frontier_budget", "frontier"))

# suite id -> domain label shown in the table; unknown ids label
# themselves
SUITE_DOMAIN = {
    "coding-core-v1": "coding",
    "domain-core-v1": "general",
    "legal-core-v1": "legal",
    "finance-core-v1": "finance",
    "routing-core-v1": "routing",
}


def _config():
    """minder's effective config; the path resolves per call (like the
    DB path) so tests and units can point MINDER_CONFIG elsewhere. A
    broken config file is a named error, not a crash."""
    try:
        import minder
        path = os.environ.get("MINDER_CONFIG") or str(minder.CFG_PATH)
        return minder.cfg(path), None
    except Exception as e:  # noqa: BLE001 - degraded, named
        return {}, f"{type(e).__name__}: {e}"


def _profile_model():
    cfg, error = _config()
    if error:
        return {"name": None, "rows": [], "available": [],
                "error": error}
    try:
        import minder
        path = os.environ.get("MINDER_CONFIG") or str(minder.CFG_PATH)
        from pathlib import Path
        raw = Path(path)
        loaded = (raw.exists() and json.loads(raw.read_text())) or {}
    except (OSError, ValueError) as e:
        loaded, error = {}, f"{type(e).__name__}: {e}"
    profiles = loaded.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    return {
        "name": loaded.get("profile"),
        "rows": [{"key": key, "label_key": label,
                  "value": cfg.get(key)}
                 for key, label in PROFILE_ROWS],
        "available": sorted(profiles),
        "error": error,
    }


def _suites_model():
    try:
        suites = bench.list_suites() or []
        baselines = {b["suite_id"]: b for b in
                     (bench.list_baselines() or [])}
        error = None
    except Exception as e:  # noqa: BLE001 - degraded, named
        return {"rows": [], "error": f"{type(e).__name__}: {e}"}
    rows = []
    for suite in suites:
        base = baselines.get(suite.get("suite_id"))
        rows.append({
            "suite_id": suite.get("suite_id"),
            "domain": SUITE_DOMAIN.get(suite.get("suite_id"),
                                       suite.get("suite_id")),
            "tasks": suite.get("tasks"),
            "status": suite.get("status"),
            "baseline": base,
        })
    return {"rows": rows, "error": error}


def _provenance_model():
    try:
        from minder_domain_evals import rulebook
        rows = rulebook.provenance_table()
        error = None
    except Exception as e:  # noqa: BLE001 - degraded, named
        return {"rows": [], "error": f"{type(e).__name__}: {e}"}
    safe = []
    for row in rows:
        row = dict(row)
        row["caveat"] = fmt.safe(row.get("caveat"), 160)
        safe.append(row)
    return {"rows": safe, "error": error}


_ROUTER_TEXT = {
    "off": "routing_off", "shadow": "routing_shadow",
    "active": "routing_active",
}


def _routing_model(cfg):
    mode = cfg.get("difficulty_router")
    if mode not in _ROUTER_TEXT:
        return {"mode": mode, "text_key": None}
    return {"mode": mode, "text_key": _ROUTER_TEXT[mode]}


def domains_page():
    """Page model for /domains; every section degrades independently."""
    cfg, _error = _config()
    return {
        "profile": _profile_model(),
        "suites": _suites_model(),
        "provenance": _provenance_model(),
        "routing": _routing_model(cfg),
        "not_available": NOT_AVAILABLE,
    }
