"""Instruction gate. Reference implementation from gap-trap (pytest),
adapted for minder's layout (root-level packages + root modules).

Asserts: every name a contract cites exists where the contract says it
lives, the portable core stays portable, the always-loaded files stay
small, cited commits exist, knowledge files hold no private data, and
grep gates settle the contract Never clauses a text search can.

Every check also asserts its own denominator: how many tokens it
resolved, how many files it scanned. A check that quietly measures
nothing reports the same green as one that measured everything (M2).
"""
from __future__ import annotations

import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

# ---- config ---------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
PACKAGE_DIRS = ["minder_core", "minder_memory", "minder_decision",
                "minder_trace", "minder_op", "minder_web", "dsh",
                "minder_domain_evals"]
ROOT_MODULES = ["minder.py", "hook.py", "proxy.py", "sink.py",
                "frontier.py", "adapter.py", "reflex.py",
                "probe_dialect.py"]
SOURCE_EXT = (".py",)
EXCLUDED_PARTS = {"tests", "benchmarks", "build", "__pycache__",
                  "node_modules", ".audit"}
FORBIDDEN_IN_CORE = ["minder", "dsh", "laya", "llama", "zcode", "sqlite"]
WORD_BUDGET = 3000  # current 1,984 + half, rounded up (C7)
MIN_CONTRACTS = 5
ALWAYS_LOADED = ["AGENTS.md", "AGENTS.project.md", "CLAUDE.md"]
KNOWLEDGE_FILES = [
    "agents/project/domain-context.md",
    "agents/project/glossary.md",
    "agents/project/out-of-scope.md",
    "agents/generic/agent-workflows.md",
]
DOCS_DIR = ""  # docs/ is local-private and uncommitted; nothing to scan
TEMPLATE_DIR = REPO / "minder_web" / "templates"
GREP_GATES = [
    ("SQLite: no sqlite3.connect outside db.py / queries.py",
     re.compile(r"\bsqlite3\.connect\("),
     lambda rel: rel in ("minder_memory/db.py", "minder_op/queries.py")),
    ("SQLite: no BEGIN IMMEDIATE outside db.py",
     re.compile(r"[\"']BEGIN IMMEDIATE"),
     lambda rel: rel == "minder_memory/db.py"),
    ("Processes: no shell=True / os.system / eval / exec",
     re.compile(r"shell\s*=\s*True|os\.system\s*\(|\beval\s*\(|"
                r"\bexec\s*\("),
     lambda rel: False),
    ("HTTP: no requests/httpx clients",
     re.compile(r"\b(requests|httpx)\.(get|post|put|delete|request)\("),
     lambda rel: False),
    ("Version: no version literals outside minder.py",
     re.compile(r"[\"']0\.8\.\d"),
     lambda rel: rel == "minder.py"),
]
# ---- end config -----------------------------------------------------------

FIELD_RE = re.compile(r"^(Owns|Path|Never|Gate):")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def source_files() -> list[Path]:
    out: list[Path] = []
    for d in PACKAGE_DIRS:
        out += [p for p in (REPO / d).rglob("*")
                if p.suffix in SOURCE_EXT
                and not (EXCLUDED_PARTS & set(p.relative_to(REPO).parts))]
    out += [REPO / m for m in ROOT_MODULES if (REPO / m).exists()]
    return out


def is_test_path(p: Path) -> bool:
    return bool(EXCLUDED_PARTS & set(p.relative_to(REPO).parts))


def parse_contracts(md: str) -> list[tuple[str, str]]:
    section = md.split("## Architecture contracts", 1)[1] \
        .split("\n## ", 1)[0] if "## Architecture contracts" in md else ""
    out = []
    for block in section.split("\n### ")[1:]:
        name, _, body = block.partition("\n")
        out.append((name.strip(), body))
    return out


def fold_fields(body: str) -> list[str]:
    """Fold a contract body's wrapped lines back into the field they
    continue."""
    out: list[str] = []
    open_field = False
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            open_field = False
        elif FIELD_RE.match(line):
            out.append(line)
            open_field = True
        elif open_field:
            out[-1] += " " + line
    return out


def backtick_tokens(line: str) -> list[str]:
    return [t.removesuffix("()") for t in re.findall(r"`([^`]+)`", line)]


def text_under(rel: str) -> str:
    """The text under a repo-relative path, file or directory."""
    p = REPO / rel
    if not p.exists():
        return ""
    if p.is_dir():
        return "\n".join(read(f) for f in p.rglob("*")
                         if f.suffix in SOURCE_EXT and f.is_file()
                         and not (EXCLUDED_PARTS
                                  & set(f.relative_to(REPO).parts)))
    return read(p)


CONTRACTS = parse_contracts(read(REPO / "AGENTS.project.md"))
NON_TEST_SOURCE_TEXT = "\n".join(read(p) for p in source_files()
                                 if not is_test_path(p))


def test_contracts_have_all_four_lines():
    assert len(CONTRACTS) >= MIN_CONTRACTS, \
        f"{len(CONTRACTS)} contracts < minimum {MIN_CONTRACTS}"
    for name, body in CONTRACTS:
        fields = fold_fields(body)
        for field in ("Owns:", "Path:", "Never:", "Gate:"):
            assert any(line.startswith(field) for line in fields), \
                f"{name} missing {field}"


def test_every_symbol_and_path_in_contracts_exists():
    checked_overall = 0
    for name, body in CONTRACTS:
        lines = [line for line in fold_fields(body)
                 if line.startswith(("Path:", "Gate:"))]
        checked = 0
        for line in lines:
            tokens = backtick_tokens(line)
            paths = [t for t in tokens if "/" in t]
            symbols = [t for t in tokens if "/" not in t]
            for token in paths:
                assert (REPO / token).exists(), \
                    f"{name}: path {token} missing"
                checked += 1
            scoped = line.startswith("Path:") and paths
            haystack = "\n".join(text_under(p) for p in paths) \
                if scoped else NON_TEST_SOURCE_TEXT
            where = ", ".join(paths) if scoped else "non-test source"
            for token in symbols:
                assert re.search(rf"\b{re.escape(token)}\b", haystack), \
                    f"{name}: symbol {token} not found in {where}"
                checked += 1
        assert checked > 0, \
            f"{name}: its Path/Gate lines name nothing the gate can check"
        checked_overall += checked
    assert checked_overall > 0, "no contract token was checked at all"


def test_core_is_portable():
    core = read(REPO / "AGENTS.md").lower()
    found = [t for t in FORBIDDEN_IN_CORE if t in core]
    assert not found, f"AGENTS.md contains project tokens: {found}"


def test_always_loaded_files_within_word_budget():
    for f in ALWAYS_LOADED:
        assert (REPO / f).exists(), \
            f"{f} is missing; the budget would fall for free"
    total = sum(len(read(REPO / f).split()) for f in ALWAYS_LOADED)
    assert total <= WORD_BUDGET, f"combined {total} words > {WORD_BUDGET}"


def test_cited_commit_hashes_exist():
    md = read(REPO / "agents/project/domain-context.md")
    hashes = sorted({h for h in
                     re.findall(r"\b[0-9a-f]{7,40}\b", md)
                     if re.search(r"\d", h)})
    assert hashes, "domain-context.md cites no commits; nothing checked"
    for h in hashes:
        r = subprocess.run(
            ["git", "cat-file", "-e", f"{h}^{{commit}}"],
            cwd=REPO, capture_output=True)
        assert r.returncode == 0, f"cited commit {h} not found in history"


def test_knowledge_files_hold_no_private_data():
    checked = 0
    for f in KNOWLEDGE_FILES:
        p = REPO / f
        if not p.exists():
            continue
        text = read(p)
        checked += 1
        assert not re.search(
            r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text), \
            f"{f} contains an IP address"
        assert not re.search(
            r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b", text), \
            f"{f} contains an email address"
    assert checked >= 3, \
        f"only {checked} knowledge files found; the scan measured nothing"


def test_docs_cite_rule_ids_that_exist():
    if not DOCS_DIR or not (REPO / DOCS_DIR).exists():
        pytest.skip("no committed docs dir (docs/ is local-private)")


def _strip_comments(code: str) -> str:
    """Blank out comments, leaving strings and line positions untouched.

    ``tokenize`` knows a ``#`` in a string from a comment; a regex does
    not.
    """
    lines = code.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return re.sub(r"(?m)^\s*#.*$", "", code)
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        end_col = tok.end[1]
        line = lines[row - 1]
        lines[row - 1] = line[:col] + " " * (end_col - col) + line[end_col:]
    return "".join(lines)


def _code_files() -> list[tuple[str, str]]:
    return [
        (p.relative_to(REPO).as_posix(), _strip_comments(read(p)))
        for p in source_files()
        if not is_test_path(p)
    ]


def test_grep_gates_scan_files_at_all():
    assert len(_code_files()) >= 30, \
        f"only {len(_code_files())} source files scanned (M2)"


@pytest.mark.parametrize("name,pattern,exempt", GREP_GATES,
                         ids=[g[0].split(":")[0] for g in GREP_GATES])
def test_grep_gate(name, pattern, exempt):
    offenders = [rel for rel, code in _code_files()
                 if not exempt(rel) and pattern.search(code)]
    assert offenders == [], f"{name}: {offenders}"


def test_console_copy_has_no_em_dash():
    """Console copy rule: plain hyphens in anything a browser renders."""
    templates = sorted(TEMPLATE_DIR.glob("*.html"))
    assert len(templates) >= 15, \
        f"only {len(templates)} templates scanned (M2)"
    for t in templates:
        assert "—" not in read(t), f"{t.name} contains an em-dash"
    for p in (REPO / "minder_web").glob("*.py"):
        assert '"—"' not in read(p), \
            f"{p.name} contains an em-dash string literal"
