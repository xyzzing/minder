"""minder_core is the extractable surface: stdlib-only, importing nothing
from minder itself. This is a load-bearing constraint, not style — other
projects should be able to lift the comparator and identity functions
without the governed runtime. The AST pin mirrors test_train_export."""
import ast
from pathlib import Path

import minder_core

CORE_DIR = Path(minder_core.__file__).resolve().parent

_ALLOWED = {"hashlib", "json", "re", "datetime", "pathlib"}


def _imports_of(path):
    """Absolute imports only: relative (`from . import x`) imports are the
    package's own submodules and always allowed."""
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.level == 0:
            imported.add(node.module.split(".")[0])
    return imported


def test_minder_core_imports_stdlib_only():
    for py in CORE_DIR.glob("*.py"):
        extra = _imports_of(py) - _ALLOWED - {"minder_core"}
        assert not extra, f"{py.name} imports non-stdlib {extra}"


def test_minder_core_never_imports_minder():
    for py in CORE_DIR.glob("*.py"):
        imported = _imports_of(py)
        for banned in ("minder", "minder_memory", "minder_decision",
                       "minder_op", "minder_web"):
            assert banned not in imported, (py.name, banned)


def test_reexported_names_are_the_same_objects():
    """The historical import locations must stay the SAME objects, or the
    evidence ledger and the comparator would fork into two laws."""
    from minder_memory import canonicalise
    from minder_memory import success_guard
    from minder_op import benchmark

    assert canonicalise.failure_key is minder_core.failure_key
    assert canonicalise.redact is minder_core.redact
    assert success_guard.result_signature is minder_core.result_signature
    assert benchmark.compare_reports is minder_core.compare_reports
    assert benchmark.VERDICT_FAIL is minder_core.VERDICT_FAIL


def test_public_surface_documents_itself():
    assert set(minder_core.__all__) == {
        "action_fingerprint", "canonical_action", "compare_reports",
        "error_family", "failure_key", "MAX_COMPLETION_DROP",
        "MIN_COMPARABLE_RUNS", "normalize_output", "redact", "relpath_of",
        "result_signature", "same_failure", "symbol_or_test_id",
        "unchanged_retry", "VERDICT_FAIL", "VERDICT_INSUFFICIENT",
        "VERDICT_NON_COMPARABLE", "VERDICT_PASS"}
