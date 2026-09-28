"""Clean-completion diff metrics (PRD v0.9 9B, laws I-2/I-3).

Measures what a change ADDED: a pure function over two directory trees
(pre-state, post-state). Findings are baseline-relative by construction
- the same extraction runs on the pre-state tree and only fingerprints
absent there count as new (I-2: pre-existing junk never penalises a
run). No subprocess, no network, stdlib AST only; a module that cannot
parse is itself a blocking finding, never a crash.

This module is the adjudication input, not the adjudicator: findings
propose, the comparator decides (I-4). Node-level detectors live in
minder_core.ast_rules.
"""
import difflib
import fnmatch
import hashlib
from pathlib import Path

from minder_core import ast_rules
from minder_core.ast_rules import BLOCKING_RULES  # re-exported API
from minder_core.integrity import DEFAULT_PROTECTED, protected_diff, \
    tree_files


def _fingerprint(rule, rel, snippet):
    """Line-number-free finding identity: moved code is not new code."""
    core = f"{rule}\0{rel}\0{snippet.strip()}"
    return hashlib.sha256(core.encode()).hexdigest()[:16]


def _py_files(root):
    root = Path(root)
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p
            for p in sorted(root.rglob("*.py")) if p.is_file()}


def _line_diff(pre_text, post_text):
    added = removed = 0
    for line in difflib.ndiff(pre_text.splitlines(), post_text.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def measure(pre_dir, post_dir, allowed_paths=None, reference_dir=None,
            protected=None):
    """QualityRecord for the change pre_dir -> post_dir (PRD v0.9
    §5.3). `allowed_paths` is the scope contract; writes outside it are
    recorded, not blocked. Never raises on tree content - a
    unparseable file is a finding, a missing tree is an empty one."""
    pre_dir, post_dir = Path(pre_dir), Path(post_dir)
    pre_files = _py_files(pre_dir)
    post_files = _py_files(post_dir)
    local_tops = {p.stem for p in post_files.values()} | \
        {p.parent.name for p in post_files.values()}

    diff = {"files_touched": 0, "files_added": 0, "files_deleted": 0,
            "lines_added": 0, "lines_removed": 0}
    scope_violations = []
    post_findings = []
    for rel in sorted(set(pre_files) | set(post_files)):
        in_pre, in_post = rel in pre_files, rel in post_files
        if in_post and not in_pre:
            diff["files_added"] += 1
        elif in_pre and not in_post:
            diff["files_deleted"] += 1
        if allowed_paths and not any(
                fnmatch.fnmatch(rel, pat) or
                rel.startswith(pat.rstrip("/") + "/")
                for pat in allowed_paths):
            if in_post and (not in_pre or
                            pre_files[rel].read_bytes()
                            != post_files[rel].read_bytes()):
                scope_violations.append(rel)

        pre_text = pre_files[rel].read_text(errors="replace") if in_pre \
            else ""
        post_text = post_files[rel].read_text(errors="replace") \
            if in_post else ""
        if in_post and (not in_pre or pre_text != post_text):
            diff["files_touched"] += 1
            added, removed = _line_diff(pre_text, post_text)
            diff["lines_added"] += added
            diff["lines_removed"] += removed
        context = ast_rules._file_context(rel, pre_text) \
            if in_pre else \
            {"pre_imports": set(), "pre_def_dumps": {}}
        context["local_tops"] = local_tops
        if in_post:
            for finding in ast_rules.extract_findings(rel, post_text,
                                           context):
                post_findings.append(finding)

    # I-2: subtract the pre-state's own findings by fingerprint.
    pre_fps = set()
    for rel in pre_files:
        pre_text = pre_files[rel].read_text(errors="replace")
        context = {"local_tops": {p.stem for p in pre_files.values()}}
        for finding in ast_rules.extract_findings(rel, pre_text,
                                           context):
            pre_fps.add(_fingerprint(finding[0], finding[1], finding[3]))

    new_findings = []
    for rule, rel, lineno, snippet in post_findings:
        if _fingerprint(rule, rel, snippet) in pre_fps:
            continue
        new_findings.append({
            "rule": rule, "path": rel, "line": lineno,
            "severity": "blocking" if rule in BLOCKING_RULES
            else "advisory",
            "source": "minder_core",
            "fingerprint": _fingerprint(rule, rel, snippet)})

    # orphan files: new files outside the scope contract
    for rel in sorted(set(post_files) | {
        str(p.relative_to(post_dir)) for p in post_dir.rglob("*")
        if p.is_file()} if post_dir.is_dir() else set()):
        if rel in pre_files or Path(rel).suffix == ".pyc":
            continue
        if any(fnmatch.fnmatch(Path(rel).name, pat)
               for pat in ast_rules._ORPHAN_PATTERNS) and not (
               allowed_paths and any(
                   fnmatch.fnmatch(rel, pat) for pat in allowed_paths)):
            new_findings.append({
                "rule": "core.orphan_file", "path": rel, "line": 0,
                "severity": "advisory", "source": "minder_core",
                "fingerprint": _fingerprint("core.orphan_file", rel,
                                            rel)})

    # protected paths (I-1 mirror), hashed by real content; always new
    # by definition
    for reason in protected_diff(tree_files(pre_dir),
                                 tree_files(post_dir),
                                 protected if protected is not None
                                 else DEFAULT_PROTECTED):
        rule, _, rel = reason.partition(":")
        new_findings.append({
            "rule": "core.protected_path_edit", "path": rel, "line": 0,
            "severity": "blocking", "source": "minder_core",
            "fingerprint": _fingerprint("core.protected_path_edit",
                                        rel, reason)})

    diff["net_lines"] = diff["lines_added"] - diff["lines_removed"]
    diff["scope_violations"] = scope_violations
    if reference_dir is not None:
        ref_added, ref_removed = _tree_line_diff(Path(reference_dir),
                                                 pre_dir)
        ref_net = ref_added - ref_removed
        diff["diff_ratio_vs_reference"] = (
            round(diff["net_lines"] / ref_net, 2)
            if ref_net else None)

    counts = {"blocking": sum(1 for f in new_findings
                              if f["severity"] == "blocking"),
              "advisory": sum(1 for f in new_findings
                              if f["severity"] == "advisory")}
    return {
        "measured": True,
        "diff": diff,
        "new_findings": new_findings,
        "counts": counts,
        "analyzers": {"minder_core": "ok"},
    }


def _tree_line_diff(ref_dir, pre_dir):
    added = removed = 0
    for rel, path in _py_files(ref_dir).items():
        pre = pre_dir / rel
        a, r = _line_diff(pre.read_text(errors="replace") if pre.exists()
                          else "", path.read_text(errors="replace"))
        added += a
        removed += r
    return added, removed
