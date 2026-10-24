"""Boundary gate for the Roomwise extraction (room runtime PRD, M0a/M0b).

M0b removed minder_domain_evals from this repository; the boundary now
settles by parsing imports rather than grepping text:

1. The extracted package stays gone: reappearing means someone
   reintroduced a second eval home (issue #44).
2. No minder runtime module imports roomwise; minder only reads
   Roomwise output files (SCHEMAS-room.md) and works without them.

Each scan check asserts its own denominator (M2): a gate that scanned
nothing must not report green.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVALS_DIR = "minder_domain_evals"
RUNTIME_ROOT_MODULES = ("minder", "proxy", "adapter", "hook", "sink",
                        "frontier", "reflex", "probe_dialect")
RUNTIME_PACKAGES = ("minder_core", "minder_memory", "minder_decision",
                    "minder_trace", "minder_op", "minder_web",
                    "minder_quality", "dsh")
FORBIDDEN_IN_RUNTIME = frozenset({"roomwise", "roomwise_train"})
MIN_RUNTIME_FILES = 50


def imported_top_levels(source: str) -> set[str]:
    """Top-level module names imported by absolute imports in `source`.
    Relative imports (`from . import x`) are package-internal and skipped."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                and node.module:
            names.add(node.module.split(".")[0])
    return names


def violations(paths, forbidden):
    found = []
    for path in paths:
        hits = imported_top_levels(path.read_text(encoding="utf-8")) & forbidden
        if hits:
            found.append(f"{path.relative_to(REPO)}: {sorted(hits)}")
    return found


def _runtime_files():
    files = [REPO / f"{name}.py" for name in RUNTIME_ROOT_MODULES
             if (REPO / f"{name}.py").exists()]
    for package in RUNTIME_PACKAGES:
        files.extend(sorted((REPO / package).rglob("*.py")))
    return files


def test_detector_flags_planted_imports():
    planted = ("import proxy\n"
               "from minder_core.identity import redact\n"
               "import roomwise.sweep\n")
    clean = "import json\nfrom . import rulebook\nfrom .families import legal\n"
    assert imported_top_levels(planted) == {"proxy", "minder_core", "roomwise"}
    assert imported_top_levels(clean) == {"json"}


def test_evals_package_stays_extracted():
    """M0b removed minder_domain_evals; Roomwise is the only eval home.
    If this fails, a second eval home reappeared in this repository."""
    assert not (REPO / EVALS_DIR).exists(), \
        f"{EVALS_DIR}/ reappeared; the Roomwise extraction (issue #44) " \
        "left exactly one eval home"


def test_runtime_imports_no_roomwise():
    files = _runtime_files()
    assert len(files) >= MIN_RUNTIME_FILES, f"scanned only {len(files)} files"
    assert violations(files, FORBIDDEN_IN_RUNTIME) == []
