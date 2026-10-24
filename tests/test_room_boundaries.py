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


# ---- two-sided schema contract (issue #44) ---------------------------------
# SCHEMAS-room.md and the fixtures minder consumes are byte-identical
# in both repos. The "byte-identical both sides" invariant lapsed once
# silently; this gate holds minder's side to a recorded digest and,
# whenever the sibling repo is on this machine (MINDER_ROOMWISE_REPO or
# ../roomwise), compares the bytes directly. CI without the sibling
# still catches minder-side drift through the digest.

import hashlib

import pytest

SCHEMA_FILES = {
    "SCHEMAS-room.md":
        "13e268052e36dc720502cc3175481e9601ad098ace0b52f333f0eeec84d867c9",
    "tests/fixtures/schemas/room-policy.json":
        "93965e85ba0e0d018cf97755749d54cea4b6f41114fbde3068f9b6a70450b1d3",
    "tests/fixtures/schemas/rulebook-provenance.json":
        "a4700104650ee0a825a51c3db3f7062e8e7389a8f9d1f7e12decfbd15f53c119",
    "tests/fixtures/schemas/minder-benchmark-report.json":
        "7213b47945b479425c0cca30bc23abca4efc2071ef277bdbe4383d78be70de8a",
}


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _roomwise_root():
    import os
    root = os.environ.get("MINDER_ROOMWISE_REPO")
    if root:
        return Path(root)
    sibling = REPO.parent / "roomwise"
    return sibling if (sibling / "docs" / "SCHEMAS-room.md").exists() \
        else None


def test_schema_contract_digests_hold():
    for rel, want in SCHEMA_FILES.items():
        path = REPO / rel
        assert path.exists(), f"{rel} is missing from this side"
        assert _digest(path) == want, (
            f"{rel} changed; resync BOTH copies (minder and roomwise) "
            "and update the digests in both gates together")


def test_schema_contract_matches_roomwise_when_present():
    root = _roomwise_root()
    if root is None:
        pytest.skip("sibling roomwise repo not on this machine")
    pairs = [(REPO / "SCHEMAS-room.md",
              root / "docs" / "SCHEMAS-room.md")]
    for name in ("room-policy.json", "rulebook-provenance.json",
                 "minder-benchmark-report.json"):
        pairs.append((REPO / "tests" / "fixtures" / "schemas" / name,
                      root / "tests" / "fixtures" / "schemas" / name))
    for mine, theirs in pairs:
        assert mine.read_bytes() == theirs.read_bytes(), (
            f"{mine.name} drifted between minder and roomwise; the "
            "contract is one document, resync both sides")
