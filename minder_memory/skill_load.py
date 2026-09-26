"""Progressive skill disclosure (docs/minder-phase-2-3-frontier-coding.md
P2.1). Metadata is advertised freely; a skill's full body is read only
when that specific skill matched. Missing body files degrade to
instructions=None. Nothing here ever writes SKILLS.md or fetches network
docs.
"""
import json
from pathlib import Path

from . import canonicalise as canon
from .skills import ENV_FAMILIES

DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "skills" / "index.json"

_METADATA_KEYS = ("name", "description", "triggers", "risk_level")


def _load_index(path=None):
    try:
        data = json.loads(Path(path or DEFAULT_INDEX).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _event_text(event):
    """Failure text from a canonical event or a raw hook payload."""
    excerpt = event.get("error_excerpt")
    if excerpt is None:
        excerpt = event.get("tool_response") or ""
    return str(excerpt)


def list_skill_metadata(event_or_text=None, index_path=None):
    """Every index entry as compact metadata — never body text."""
    out = []
    for entry in _load_index(index_path):
        meta = {k: entry.get(k) for k in _METADATA_KEYS}
        if meta["name"]:
            out.append(meta)
    return out


def match_skills(event, index_path=None):
    """Ordered names whose triggers hit the failure key, canonical error
    family, or excerpt (case-insensitive). Never raises."""
    if not isinstance(event, dict):
        return []
    fkey = str(event.get("failure_key") or "")
    excerpt = _event_text(event)
    family = (fkey.split("|")[1] if "|" in fkey
              else canon.error_family(excerpt))
    hay = " ".join((fkey, excerpt, family)).lower()
    matched = []
    for entry in _load_index(index_path):
        for trig in entry.get("triggers", []) or []:
            if str(trig).lower() in hay:
                matched.append(entry.get("name"))
                break
    return matched


def _read_body(rel):
    if not rel:
        return None
    try:
        return (Path(__file__).resolve().parent.parent / rel).read_text()
    except (OSError, ValueError):
        return None


def load_skill(name, index_path=None):
    """Full skill = metadata + instructions (body loaded on demand).
    Returns None for unknown names; instructions=None for bodyless skills."""
    for entry in _load_index(index_path):
        if entry.get("name") != name:
            continue
        skill = {k: entry.get(k) for k in _METADATA_KEYS}
        for extra in ("preconditions", "verification", "body"):
            if extra in entry:
                skill[extra] = entry[extra]
        skill["instructions"] = _read_body(entry.get("body"))
        return skill
    return None


def _gap_type(event):
    fkey = str(event.get("failure_key") or "")
    excerpt = _event_text(event)
    family = (fkey.split("|")[1] if "|" in fkey
              else canon.error_family(excerpt))
    return "environment" if family in ENV_FAMILIES else "unknown"


def select_skills_for_event(event, index_path=None):
    """The progressive-disclosure entry point.

    {advertised: [compact metadata], selected: [names],
     loaded: [full skills for selected only], gap: dict | None}
    Unmatched bodies are never read; the gap is returned, not recorded.
    """
    advertised = list_skill_metadata(index_path=index_path)
    selected = match_skills(event, index_path)
    loaded = [s for s in (load_skill(n, index_path) for n in selected)
              if s is not None]
    gap = None
    if not selected:
        gap = {"gap": True, "gap_type": _gap_type(event)}
    return {"advertised": advertised, "selected": selected,
            "loaded": loaded, "gap": gap}
