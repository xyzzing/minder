"""Is a shell command a test/lint run, and is its output clean?

Issue #9: minder's lesson gate needs local verification evidence, and the
only evidence it had was an explicit `verification.tests_passed` payload
that nothing ever produced. A clean test-runner run is that evidence, so
recognition lives here: one module for the runner vocabulary (C4), pure
functions, no I/O, no minder imports.

Structural, not semantic: the runner must appear where a command name
appears (start of a segment, or after a runner wrapper), never inside an
argument. `cat pytest_notes.md` is not a test run.
"""

import re
import shlex

# runner name -> canonical label stored in the evidence row
_RUNNERS = {
    "pytest": "pytest",
    "py.test": "pytest",
    "unittest": "unittest",
    "vitest": "vitest",
    "jest": "jest",
    "mocha": "mocha",
    "playwright": "playwright",
    "karma": "karma",
    "rspec": "rspec",
}

# package-manager / task runners: `npm test`, `make test`, `turbo test`
_TASK_RUNNERS = frozenset(("npm", "pnpm", "yarn", "npx", "make", "turbo",
                           "deno", "bun"))

# wrappers that carry env or flags and then run another command
_WRAPPERS = frozenset(("env", "time", "sudo", "tox"))

# wrapper flags whose value is the NEXT token, so it is not a command name
_VALUE_FLAGS = frozenset(("-u", "--unset", "-v", "--var", "-S", "-b", "-C",
                          "-P", "-p", "--package", "--chdir", "--path",
                          "--block-size", "--split-string"))

# A file argument ending in a test filename is a run of that file.
_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test|[^.]+\.(?:test|spec))"
                           r"\.(?:py|js|jsx|ts|tsx|go|rb|rs)$")

# Commands that merely mention a runner as data, never as an action.
_NON_RUNNERS = frozenset((
    "cat", "cd", "chmod", "cp", "echo", "find", "grep", "head", "less",
    "ls", "mv", "open", "rg", "rm", "sed", "sort", "tail", "touch", "vim",
    "nano", "pwd", "du", "wc", "stat", "tree", "diff", "awk",
))

# Output that states an outcome count. A runner that reports failures did not
# verify anything, whatever its exit code looked like to the failure signs.
_COUNT_RE = re.compile(r"\b(\d+)\s+(?:failing|failed)\b", re.IGNORECASE)
_PASSED_RE = re.compile(r"\b(\d+)\s+(?:passed|passing)\b", re.IGNORECASE)
# `go test` reports per-package status with no counts at all. A line-leading
# FAIL is a failed package; FAILURES/FAILED stay out, pytest already states a
# failing count and _COUNT_RE settles those.
_GO_FAIL_RE = re.compile(r"^\s*FAIL\b", re.IGNORECASE | re.MULTILINE)


def _tokens(command):
    try:
        return [t for t in shlex.split(str(command)) if t]
    except ValueError:
        return [t for t in str(command).split() if t]


def _base(token):
    return token.rsplit("/", 1)[-1]


def _unwrap(toks):
    """Step past env/time/sudo/tox and their flags to the real command."""
    if not toks or _base(toks[0]) not in _WRAPPERS:
        return toks
    j = 1
    while j < len(toks):
        tok = toks[j]
        if tok == "--":
            j += 1
            break
        if tok in _VALUE_FLAGS:
            j += 2
            continue
        if tok.startswith("-"):
            j += 1
            continue
        break
    return _unwrap(toks[j:])


def _segment_runner(toks):
    """Runner label for one command segment, or None. Only looks at
    positions where a command name can appear, so a runner word in an
    argument (`echo pytest`) is not a run."""
    toks = _unwrap(toks)
    if not toks:
        return None
    first = _base(toks[0])
    if first in _NON_RUNNERS:
        return None
    if first in ("python", "python2", "python3", "py") and \
            toks[1:2] == ["-m"] and toks[2:3]:
        mod = _base(toks[2])
        if mod.startswith("unittest"):
            return "unittest"
        return _RUNNERS.get(mod)
    if first in ("go", "cargo") and toks[1:2] == ["test"]:
        return f"{first} test"
    if first in ("playwright", "karma") and \
            toks[1:2] and toks[1] in ("test", "start", "run"):
        return _RUNNERS[first]
    if first in _TASK_RUNNERS:
        rest, k = [], 1
        while k < len(toks):
            if toks[k] in _VALUE_FLAGS:
                k += 2
                continue
            if toks[k].startswith("-"):
                k += 1
                continue
            rest.append(toks[k])
            k += 1
        if rest and (rest[0] == "test" or rest[0].startswith("test:")):
            return "test"
        if first == "npm" and rest[:1] == ["run"] and rest[1:2] and \
                (rest[1] == "test" or rest[1].startswith("test:")):
            return "test"
        # `npx vitest run` / `pnpm jest`: the wrapped binary is the runner
        return _segment_runner(rest) if rest else None
    if first in _RUNNERS:
        return _RUNNERS[first]
    if first == "test" and len(toks) > 1:
        return None  # the shell builtin, not a runner
    if any(_TEST_FILE_RE.search(_base(t)) for t in toks[1:]):
        return "test file"
    return None


def test_runner(command):
    """Canonical runner label for a test/lint command, else None.

    Recognised: `pytest`, `python -m pytest|unittest`, `vitest`, `jest`,
    `mocha`, `playwright test`, `karma start`, `go test`, `cargo test`,
    `rspec`, and `npm|pnpm|yarn|npx|make|turbo|deno|bun test[:target]`.
    A command is a run only when the runner sits where a command name sits,
    so `cat pytest_notes.md` and `echo pytest` are not runs. Never raises.
    """
    if not command:
        return None
    text = str(command)
    # A compound command verifies if any of its segments is a test run.
    for segment in re.split(r"&&|\|\||;|\|", text):
        label = _segment_runner(_tokens(segment))
        if label:
            return label
    return None


def run_is_clean(output, runner=None):
    """True when the runner output states success and states no failure.

    Count-based runners (pytest, jest, vitest, cargo) must report a passing
    count and no failing count. `go test` reports per-package status with no
    counts: every package line says `ok`, and any `FAIL` line means it did
    not verify. An empty or result-free output is never evidence.
    """
    text = str(output or "")
    if _GO_FAIL_RE.search(text):
        return False
    if runner == "go test":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            return False
        return all(re.match(r"\s*(?:ok|---\s*PASS|\?)", ln) for ln in lines)
    failed = _COUNT_RE.search(text)
    if failed and int(failed.group(1)) > 0:
        return False
    passed = _PASSED_RE.search(text)
    return bool(passed) and int(passed.group(1)) > 0
