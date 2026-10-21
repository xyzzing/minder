"""Every skill the harness has, not only the ones registered in the
index (issue #40).

`skills/index.json` is the only source the gap detector and the console
read, so an installed-pack skill - a folder under `skills/` plus a
`skills-lock.json` entry - is invisible: `/skills` lists the index's few
rows and calls that the whole set, and `check_skill_gap()` files a gap
for a failure an installed skill already covers. The 2026-10-12 triage
counted 607 open rows; this module is the evidence that says how much of
that set was already covered.

Three sources, unioned by skill name:

- `skills/index.json` - the curated triggers. Only these entries can
  suppress a gap, because a trigger is a claim about matching.
- `skills/*/SKILL.md` front matter - what is actually installed,
  including installed-pack skills that have no index entry.
- `skills-lock.json` - where a folder came from (provenance). It is a
  tool's file, not minder's, and nothing read it before this module.

Triggers are never invented: a folder without an index entry has no
triggers, so it is visible everywhere and suppresses nothing. That keeps
the runtime's matching behaviour exactly as it was while making the
installed set countable.
"""
import json
import os
from pathlib import Path

# `MINDER_SKILLS_INDEX` is the one override, resolved per call like every
# other path in the project, so a test or a staged install can point the
# inventory at a different tree without patching this module.
INDEX_ENV = "MINDER_SKILLS_INDEX"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "skills"
DEFAULT_INDEX = DEFAULT_ROOT / "index.json"
LOCK_NAME = "skills-lock.json"

# how a row became known to minder, and where its folder came from.
# `source` is what the page shows: the closed vocabulary below when
# nothing more specific is known, otherwise the provenance a lock file
# records, which can be any string a tool wrote.
SOURCE_INDEX = "index"
SOURCE_INSTALLED = "installed"


def _how(indexed, origin):
    """One word for how this skill is known: registered in the index,
    installed but unregistered, or installed from a named pack."""
    if origin not in ("", SOURCE_INDEX, SOURCE_INSTALLED):
        return origin
    return SOURCE_INDEX if indexed else SOURCE_INSTALLED


# The index's `body` paths are repo-relative (they are written by hand
# and read by skill_load the same way), so they resolve against the
# project root rather than against wherever the index file happens to be.
PROJECT_ROOT = DEFAULT_ROOT.parent


def index_path():
    """The index file to read: `MINDER_SKILLS_INDEX` or the repo default."""
    env = os.environ.get(INDEX_ENV)
    return Path(env) if env else DEFAULT_INDEX


def _body_path(value, root):
    """Resolve an index `body` value: absolute paths as given, relative
    ones against the project root (how skill_load reads them) and, when
    that misses, against the skills root - which is where a fixture or a
    staged install puts its bodies."""
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    for base in (PROJECT_ROOT, root):
        candidate = base / path
        if candidate.is_file():
            return candidate
    return PROJECT_ROOT / path


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _front_matter(text):
    """The leading `---` block of a SKILL.md as flat key: value lines.

    A deliberately small reader: skills are written by hand and by
    installers, and the only fields minder needs are scalars. A block
    that never closes, or a file with no block, returns {} - the folder
    is still installed, it just says nothing about itself."""
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() in ("---", "..."):
            return out
        if ":" not in line or line[:1].isspace():
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key:
            out[key] = value.strip().strip('"\'')
    return out


def _installed_folders(skills_root):
    """One entry per `skills/*/SKILL.md`, keyed by folder name, with its
    front matter. A symlinked folder (an installed pack linked in) is a
    real folder here - `is_dir` follows it."""
    out = {}
    root = Path(skills_root)
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for folder in children:
        if not folder.is_dir():
            continue
        body = folder / "SKILL.md"
        if not body.is_file():
            continue
        try:
            text = body.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        out[folder.name] = {"front_matter": _front_matter(text),
                            "body_path": body}
    return out


def _lock_provenance(lock_path):
    """name -> the source string a lock file records for it. Missing or
    malformed lock: {} - a folder is not uninstalled because the tool's
    bookkeeping file is absent."""
    data = _read_json(lock_path)
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, dict):
        return {}
    out = {}
    for name, entry in skills.items():
        if isinstance(entry, dict) and entry.get("source"):
            out[str(name)] = str(entry["source"])
    return out


def inventory(index=None, skills_root=None, lock_path=None):
    """One row per skill the harness has, indexed or not. Never raises:
    every source degrades to empty, and the union of what is left is
    still an honest count.

    `index` overrides the index file to read, otherwise the resolver
    applies (env override, then the repo default).

    Row keys: name, description, triggers (list), risk_level, body_ok,
    source (how it is known: index / installed / the lock's pack name),
    origin (the lock's provenance or "index"), indexed (bool),
    body_path."""
    ipath = Path(index) if index else index_path()
    root = Path(skills_root) if skills_root else ipath.parent
    # the lock file belongs next to the skills tree it describes, so it
    # follows the root being inventoried rather than this module's
    # install location - a fixture or a staged install has its own
    lpath = Path(lock_path) if lock_path else root.parent / LOCK_NAME
    entries = _read_json(ipath)
    entries = entries if isinstance(entries, list) else []
    folders = _installed_folders(root)
    provenance = _lock_provenance(lpath)

    rows = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        body = entry.get("body")
        body_path = _body_path(body, root)
        folder = folders.get(name, {})
        rows.append({
            "name": name,
            "description": str(entry.get("description")
                               or folder.get("front_matter", {})
                               .get("description") or ""),
            "triggers": [str(t) for t in (entry.get("triggers") or [])],
            "risk_level": str(entry.get("risk_level") or "?"),
            "body_ok": bool(body_path and body_path.is_file()),
            "origin": provenance.get(name) or SOURCE_INDEX,
            "source": _how(True, provenance.get(name) or SOURCE_INDEX),
            "indexed": True,
            "body_path": str(body_path) if body_path else None,
        })
    for name, folder in folders.items():
        if name in seen:
            continue
        seen.add(name)
        front = folder["front_matter"]
        rows.append({
            "name": front.get("name") or name,
            "description": front.get("description", ""),
            # no triggers: an unindexed folder is visible, never matched,
            # because a trigger is a claim about matching and only the
            # index makes that claim (issue #40 keeps registration out)
            "triggers": [],
            "risk_level": front.get("risk_level", "?"),
            "body_ok": folder["body_path"].is_file(),
            "origin": provenance.get(name) or SOURCE_INSTALLED,
            "source": _how(False, provenance.get(name)
                           or SOURCE_INSTALLED),
            "indexed": False,
            "body_path": str(folder["body_path"]),
        })
    return sorted(rows, key=lambda r: r["name"])
