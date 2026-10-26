"""Read model for the /domains page (issue #8).

Read-only, like the rest of the console: the active profile overlay,
the eval suites and their pinned baselines, the domain rulebook's
provenance grades, and the difficulty-router mode. Every source can be
missing or malformed - each section degrades to a named "not
available" instead of a 500. No SQL here and no policy: config comes
from minder.cfg(), suites from minder_op.benchmark, rule provenance
from a Roomwise rulebook-provenance/v1 file (issue #44).
"""
import json
import os
from pathlib import Path

from minder_op import benchmark as bench
from minder_op import format as fmt

from minder_web.strings_base import NOT_AVAILABLE

PROVENANCE_SCHEMA = "roomwise.rulebook-provenance/v1"

# paths already logged as invalid: one diagnostic per bad file, like
# the proxy's escalation-marker map — a loop of page loads must not
# flood the ledger (bounded, dropped when full)
_provenance_warned: set[str] = set()
_PROVENANCE_WARN_CAP = 64

# profile knobs shown on the page, in display order: (config key,
# strings_pages label key)
PROFILE_ROWS = (("l1_budget", "budget"), ("l2_budget", "budget2"),
                ("spend_guardrail_tokens", "guardrail"),
                ("frontier_budget", "frontier"))

# suite id -> domain label shown in the table; unknown ids label
# themselves
SUITE_DOMAIN = {
    "coding-core-v1": "coding",
    "difficulty-core-v1": "difficulty",
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
        base = baselines.get(suite.get("suite_id")) or {}
        rows.append({
            "suite_id": suite.get("suite_id"),
            "domain": SUITE_DOMAIN.get(suite.get("suite_id"),
                                       suite.get("suite_id")),
            "tasks": suite.get("tasks"),
            "status": suite.get("status"),
            "baseline": base or None,
            # the plain verdict column is built from these three; the
            # raw tier names fields, so the nested dict is flattened
            # here rather than in the template (issue #12)
            "baseline_generated_at": base.get("generated_at"),
            "baseline_rate": base.get("rate"),
            "baseline_comparable_runs": base.get("comparable_runs"),
        })
    return {"rows": rows, "error": error}


def _provenance_model():
    """Rule provenance from a Roomwise rulebook-provenance/v1 file at
    cfg key rulebook_provenance_path (issue #44). Three states, three
    renders: rows (table), unreadable (warn line - the operator
    configured a path, so the page names the broken file, not the
    absence of a rulebook), absent (not-available copy). A malformed
    file also logs one diagnostic per path so a producer bug cannot
    hide behind a quiet warning."""
    global _provenance_warned
    cfg, _cfg_error = _config()
    path = cfg.get("rulebook_provenance_path")
    if not path:
        return {"rows": [], "error": None, "unreadable": None}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        # an empty rows list is a producer bug (SCHEMAS-room.md §9),
        # indistinguishable from a rulebook with no rules: refuse it
        if (not isinstance(raw, dict)
                or raw.get("schema") != PROVENANCE_SCHEMA
                or not isinstance(raw.get("rows"), list)
                or not raw["rows"]):
            raise ValueError(
                f"not a non-empty {PROVENANCE_SCHEMA} document")
        rows = raw["rows"]
    except (OSError, ValueError, TypeError) as exc:
        if str(path) not in _provenance_warned \
                and len(_provenance_warned) < _PROVENANCE_WARN_CAP:
            import minder
            minder.log("console", "rulebook_provenance_invalid",
                       path=str(path),
                       error=f"{type(exc).__name__}: {exc}")
            _provenance_warned.add(str(path))
        return {"rows": [], "error": None,
                "unreadable": f"{type(exc).__name__}: {exc}"}
    safe = []
    for row in rows:
        row = dict(row)
        row["caveat"] = fmt.safe(row.get("caveat"), 160)
        safe.append(row)
    return {"rows": safe, "error": None, "unreadable": None}


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
