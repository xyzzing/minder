"""Verification integrity (PRD v0.9 9A, laws I-1/I-3).

The verified cannot write the verifier: any change to a protected path
voids verification for the run, a run only counts when run-level
evidence (a junit report with the expected test ids) exists, and
holdout tests the solver never saw backstop visible-test gaming.

Pure stdlib. No I/O beyond reading the trees and junit file handed in;
no minder imports (same extraction contract as comparator/identity).
"""
import fnmatch
import hashlib
import xml.etree.ElementTree as ElementTree
from pathlib import Path

DEFAULT_PROTECTED = ("test_*.py", "*_test.py", "conftest.py",
                     "pytest.ini", "pyproject.toml", "setup.cfg",
                     "tox.ini", ".github/**")

STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_TAMPERED = "tampered"
STATUS_UNDERVERIFIED = "underverified"


def tree_files(root):
    """{relpath: sha256(content)} over regular files, sorted walk.

    Symlinks are refused upstream by the runner's copy step; anything
    that is not a regular file is skipped here so hashing stays total.
    """
    root = Path(root)
    out = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def tree_sha256(root):
    """One hash over the whole tree: sorted (relpath, file hash)."""
    files = tree_files(root)
    core = "".join(f"{rel}:{files[rel]}\n" for rel in sorted(files))
    return hashlib.sha256(core.encode()).hexdigest()


def _is_protected(rel, patterns):
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def protected_diff(pre_files, post_files, patterns):
    """Tamper reasons for protected paths added/modified/deleted between
    the pristine tree and the post-overlay tree. Empty list = clean."""
    patterns = list(patterns or DEFAULT_PROTECTED)
    reasons = []
    for rel in sorted(set(pre_files) | set(post_files)):
        if not _is_protected(rel, patterns):
            continue
        before = pre_files.get(rel)
        after = post_files.get(rel)
        if before is not None and after is None:
            reasons.append(f"protected_deleted:{rel}")
        elif before is None and after is not None:
            reasons.append(f"protected_added:{rel}")
        elif before != after:
            reasons.append(f"protected_modified:{rel}")
    return reasons


def parse_junit(path, started=None):
    """(counts, testcase ids) from a junit xml file, or None when the
    file is missing, unparseable, or older than the run start — a file
    the solver could have planted before pytest ran must not count.

    counts keys: collected, passed, failed, skipped, errors.
    """
    path = Path(path)
    if not path.is_file():
        return None
    if started is not None:
        try:
            if path.stat().st_mtime < started:
                return None
        except OSError:
            return None
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError):
        return None
    suite = root if root.tag == "testsuite" else \
        root.find("testsuite")
    if suite is None:
        return None
    counts = {"collected": 0, "passed": 0, "failed": 0, "skipped": 0,
              "errors": 0}
    ids = []
    for case in suite.iter("testcase"):
        counts["collected"] += 1
        name = case.get("name", "")
        classname = case.get("classname", "")
        # pytest junit classnames are dotted module paths; --collect-only
        # prints file paths. Normalize both to <path>::<name> so the id
        # sets compare (the planted-junit check depends on it).
        if classname:
            ids.append(classname.replace(".", "/") + ".py::" + name)
        else:
            ids.append(name)
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts, ids


def assess(pre_files, post_files, protected, junit_counts=None,
           junit_ids=None, expected_ids=None, expected_tests=None,
           allow_skips=0, holdout_status="absent",
           run_status=STATUS_FAILED):
    """Integrity verdict for one run, rules in precedence order
    (PRD v0.9 §5.2):

    1. a protected path was added, modified, or deleted -> tampered,
       regardless of the test outcome;
    2. junit evidence missing/unparseable/stale, ids not matching the
       pristine collection, collected < expected_tests, or skipped >
       allow_skips -> underverified (I-3: unmeasured is not zero);
    3. visible tests pass but the holdout fails -> failed (the visible
       tests were gamed or overfit);
    4. otherwise the pytest outcome stands.

    `holdout_status` is one of pass|fail|absent. `run_status` is the
    raw pytest outcome (verified|failed|timeout).
    """
    reasons = protected_diff(pre_files, post_files, protected)
    status = None
    if reasons:
        status = STATUS_TAMPERED
    else:
        underverified = False
        if junit_counts is None:
            underverified = True
        else:
            if expected_ids is not None and \
                    sorted(junit_ids or []) != sorted(expected_ids):
                underverified = True
            if expected_tests is not None and \
                    junit_counts["collected"] < expected_tests:
                underverified = True
            if junit_counts["skipped"] > (allow_skips or 0):
                underverified = True
        if underverified:
            status = STATUS_UNDERVERIFIED
        elif run_status == STATUS_VERIFIED and holdout_status == "fail":
            status = STATUS_FAILED
        else:
            status = run_status
    tests = dict(junit_counts) if junit_counts else {
        "collected": 0, "passed": 0, "failed": 0, "skipped": 0,
        "errors": 0}
    if expected_tests is not None:
        tests["expected"] = expected_tests
    return {
        "status": status,
        "tamper_reasons": reasons,
        "tests": tests,
        "holdout": {"status": holdout_status or "absent"},
    }
