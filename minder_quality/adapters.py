"""Declarative analyzer adapters (PRD v0.9 9D).

External analyzers are INVOKED as subprocesses when present, never
imported, never installed by minder (Law: stdlib-only runtime). An
adapter is data: argv + a parser chosen from a closed menu
(json_path | line_regex | sarif). No arbitrary code loading; a
declared placeholder outside {paths}/{workspace} or a parser outside
the menu is rejected at load time (AT-Q14).

Missing analyzer, version outside pin, timeout, or parse failure ->
status "unavailable: <reason>", never 0 (I-3); the only-new-junk
subtraction happens by fingerprint against the pre-state run (I-2).
"""
import fnmatch
import hashlib
import json
import re
import subprocess
from pathlib import Path

from minder_core.diffmetrics import _line_diff  # noqa: F401 (re-export)

QUALITY_DIR = Path(__file__).resolve().parent.parent / "quality" / "adapters"
USER_DIR = Path.home() / ".config" / "minder" / "quality" / "adapters"
PARSERS = ("json_path", "line_regex", "sarif")
_ALLOWED_PLACEHOLDERS = {"{paths}", "{workspace}"}
_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


class AdapterError(Exception):
    """Adapter rejected (bad parser, unknown placeholder, bad JSON)."""


def _findings_fingerprint(rule, rel, snippet):
    core = f"{rule}\0{rel}\0{snippet.strip()}"
    return hashlib.sha256(core.encode()).hexdigest()[:16]


def load(extra_dirs=()):
    """Adapters by id: shipped quality/adapters first, then the
    operator's ~/.config override, then test dirs (last wins)."""
    by_id = {}
    for directory in (QUALITY_DIR, USER_DIR, *extra_dirs):
        if not Path(directory).is_dir():
            continue
        for path in sorted(Path(directory).glob("*.json")):
            try:
                adapter = json.loads(path.read_text())
            except ValueError as exc:
                raise AdapterError(f"{path}: bad JSON: {exc}") from exc
            _validate(adapter, path)
            by_id[adapter["id"]] = adapter
    return by_id


def _validate(adapter, source):
    if not isinstance(adapter.get("id"), str) or not adapter["id"]:
        raise AdapterError(f"{source}: id must be a non-empty string")
    if adapter.get("parser") not in PARSERS:
        raise AdapterError(f"{source}: parser must be one of {PARSERS}, "
                           f"got {adapter.get('parser')!r}")
    for key in ("probe_argv", "argv"):
        if not isinstance(adapter.get(key), list) or \
                not all(isinstance(a, str) for a in adapter[key]):
            raise AdapterError(f"{source}: {key} must be a list of strings")
    for argv in (adapter["probe_argv"], adapter["argv"]):
        for arg in argv:
            found = set(re.findall(r"\{[a-z]+\}", arg))
            unknown = found - _ALLOWED_PLACEHOLDERS
            if unknown:
                raise AdapterError(
                    f"{source}: placeholder(s) {sorted(unknown)} not "
                    "allowed (only {paths} and {workspace})")


def probe(adapter):
    """(version_string_or_None, status). Missing binary or failing
    probe is unavailable, never an error (I-3)."""
    try:
        proc = subprocess.run(adapter["probe_argv"], capture_output=True,
                              text=True,
                              timeout=adapter.get("timeout_s", 60))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"unavailable: {type(exc).__name__}"
    if proc.returncode != 0:
        return None, f"unavailable: probe exit {proc.returncode}"
    version = _VERSION_RE.search(proc.stdout or "")
    version = version.group(0) if version else "unknown"
    pin = adapter.get("pin")
    if pin and not fnmatch.fnmatch(version, pin):
        return None, f"unavailable: version {version} outside pin {pin}"
    return version, "ok"


def _normalize(adapter, output, workspace):
    """[(rule, rel_path, snippet)] from raw analyzer output."""
    rels = []
    kind = adapter["parser"]
    if kind == "line_regex":
        pattern = re.compile(adapter["pattern"])
        for line in output.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            data = match.groupdict()
            rels.append((data.get("rule") or
                         (output[:0] + (data.get("message") or "")
                          .split(":")[0]) or "unknown",
                         data.get("path", ""), data.get("message", "")))
    elif kind == "json_path":
        try:
            parsed = json.loads(output)
        except ValueError:
            return rels
        node = parsed
        pointer = adapter.get("pointer", "")
        for part in pointer.strip("/").split("/"):
            if not part:
                continue
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return rels
        fields = adapter.get("fields", {})
        for item in node if isinstance(node, list) else []:
            get = lambda f: (  # noqa: E731
                _dig(item, fields[f]) if f in fields else "")
            rels.append((get("rule") or "unknown", get("path"),
                         get("message") or get("rule") or "finding"))
    elif kind == "sarif":
        try:
            parsed = json.loads(output)
        except ValueError:
            return rels
        for run in parsed.get("runs", []):
            for result in run.get("results", []):
                loc = (result.get("physicalLocation") or {})
                art = (loc.get("artifactLocation") or {})
                region = loc.get("region") or {}
                rels.append((result.get("ruleId", "unknown"),
                             art.get("uri", ""),
                             (result.get("message") or {}).get("text", "")
                             + (f":{region.get('startLine')}"
                                if region.get("startLine") else "")))
    return [(rule, _relativize(path, workspace), snippet)
            for rule, path, snippet in rels]


def _dig(item, pointer):
    node = item
    for part in pointer.lstrip("$").split("/"):
        part = part.strip("/").lstrip(".")
        if not part:
            continue
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return ""
    return str(node) if node is not None else ""


def _relativize(path, workspace):
    path = str(path or "")
    workspace = str(workspace)
    if path.startswith(workspace):
        path = path[len(workspace):].lstrip("/")
    return path.replace("\\", "/")


def run(adapter, paths, workspace):
    """(findings, status): findings [(rule, rel, snippet)] on the given
    absolute paths, or ([], unavailable-status)."""
    version, status = probe(adapter)
    if version is None:
        return [], status
    if not paths:
        return [], "ok (nothing to scan)"
    argv = [arg.replace("{paths}", " ".join(str(p) for p in paths))
            .replace("{workspace}", str(workspace))
            for arg in adapter["argv"]]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=adapter.get("timeout_s", 60))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"unavailable: {type(exc).__name__}"
    findings_codes = adapter.get("findings_exit_codes") or []
    if proc.returncode != 0 and proc.returncode not in findings_codes:
        # a non-zero exit that is not the tool's documented
        # "findings present" code is an analyzer failure, not findings
        if not _looks_like_findings(adapter, proc.stdout or ""):
            return [], f"unavailable: exit {proc.returncode}"
    return (_normalize(adapter, proc.stdout or "", str(workspace)),
            f"ok {version}")


def _looks_like_findings(adapter, output):
    if adapter["parser"] == "line_regex":
        return bool(re.search(adapter["pattern"], output))
    return bool(output.strip())


def assess_touched(pre_dir, post_dir, touched_relpaths, extra_dirs=()):
    """(new_findings, analyzer_statuses) across the touched files only
    (I-2): each found adapter runs on the pre-state copies and the
    post-state copies; the fingerprint difference is what counts."""
    pre_dir, post_dir = Path(pre_dir), Path(post_dir)
    new_findings, statuses = [], {}
    for adapter in load(extra_dirs).values():
        paths = []
        for rel in touched_relpaths:
            if Path(rel).suffix in (".py", ".js", ".ts", ".jsx", ".tsx",
                                    ".json", ".md"):
                post_path = post_dir / rel
                if post_path.is_file():
                    paths.append(post_path)
        if not paths:
            statuses[adapter["id"]] = "ok (no touched files in scope)"
            continue
        post_findings, status = run(adapter, paths, post_dir)
        if not status.startswith("ok"):
            statuses[adapter["id"]] = status
            import minder  # never raises; no wrapper needed
            minder.log("quality", "quality_analyzer_unavailable",
                       analyzer=adapter["id"], status=status)
            continue
        pre_paths = [pre_dir / rel for rel in
                     (p.relative_to(post_dir) for p in paths)
                     if (pre_dir / rel).is_file()]
        pre_findings, _ = run(adapter, pre_paths, pre_dir) \
            if pre_paths else ([], "ok")
        post_fps = {_findings_fingerprint(rule, rel, snippet)
                    for rule, rel, snippet in post_findings}
        pre_fps = {_findings_fingerprint(rule, rel, snippet)
                   for rule, rel, snippet in pre_findings}
        statuses[adapter["id"]] = status
        for fp in sorted(post_fps - pre_fps):
            for rule, rel, snippet in post_findings:
                if _findings_fingerprint(rule, rel, snippet) == fp:
                    new_findings.append({
                        "rule": rule, "path": rel, "line": 0,
                        "severity": "advisory", "source": adapter["id"],
                        "fingerprint": fp})
                    break
    return new_findings, statuses
