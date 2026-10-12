#!/usr/bin/env python3
"""Ratchet counter: third-party imports no manifest declares.

minder's core is stdlib-only; the web console (`minder_web/`) and the
optional laya path may import third-party packages, declared in
`pyproject.toml` `[project.optional-dependencies]` and
`requirements-web.txt`. `runtime_deps` counts the declared side; this
counts the used side. A gap between them is the failure mode: an import
that works because the package happens to be installed on one machine.

Prints one whole number: import sites in tracked, non-test source whose
top-level module is neither stdlib nor a local package nor declared.

AST-based, not a text grep, for two reasons this repo hit in practice:
`import laya  # noqa: F401` inside a function is a guarded optional
import and must count only if the package is undeclared, and prose that
names a library is not an import. A `# type: ignore` or `# noqa` comment
does not exempt a site: the gap is the finding, not the lint noise.

Local-package names are read from the tree, so a new package folder does
not need registering here.
"""
import ast
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXCLUDE_PREFIXES = ("tests/", "benchmarks/", "dsh/")


def tracked_source() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [f for f in out if not f.startswith(EXCLUDE_PREFIXES)]


def declared_modules() -> set[str]:
    """Top-level import names declared by the project's manifests.

    The distribution name is what the manifest declares; the import name
    is what the AST yields. They usually match, and where they do not
    (pillow -> PIL) the site is reported as a finding rather than passed
    silently by a guessed alias. Guessing here is how a gate starts
    lying (M2).
    """
    mods: set[str] = set()
    pyproject = REPO / "pyproject.toml"
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        for spec in project.get("dependencies", []):
            mods.add(_dist_to_module(spec))
        for specs in project.get("optional-dependencies", {}).values():
            for spec in specs:
                mods.add(_dist_to_module(spec))
    for req in REPO.glob("requirements*.txt"):
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                mods.add(_dist_to_module(line))
    return mods


def _dist_to_module(spec: str) -> str:
    for op in ("<", ">", "=", "!", "[", ";", "@", " "):
        spec = spec.split(op)[0]
    return spec.strip().replace("-", "_").lower()


def local_packages(files: list[str]) -> set[str]:
    """minder's own packages, plus the root-module names it imports
    itself with (`import minder` reaches the package root, `import
    adapter` and `import frontier` are root modules). Nothing outside
    this set is exempt."""
    tops = {f.split("/")[0] for f in files if "/" in f}
    tops |= {f[:-3] for f in files if "/" not in f}
    for path in REPO.iterdir():
        if path.is_dir() and (path / "__init__.py").is_file():
            tops.add(path.name)
    return tops - set(EXCLUDE_PREFIXES)


def import_sites(tree: ast.AST) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                mods.append(node.module.split(".")[0])
    return mods


def main() -> int:
    files = tracked_source()
    declared = declared_modules()
    local = local_packages(files)
    sites = 0
    for rel in files:
        try:
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8",
                                                    errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            # An unparseable file counts as one site: a syntax error must
            # not make the count fall (M2).
            sites += 1
            continue
        for mod in import_sites(tree):
            if mod in sys.stdlib_module_names or mod == "__future__":
                continue
            if mod in declared or mod in local:
                continue
            sites += 1
    print(sites)
    return 0


if __name__ == "__main__":
    sys.exit(main())
