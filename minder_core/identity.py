"""Canonical failure identity + secret redaction + result signatures.

Lifted verbatim from minder_memory.canonicalise (failure identity) and
minder_memory.success_guard (result identity) so other projects can use
the exact functions minder's evidence ledger is keyed by, without
installing the runtime. Pure functions — no I/O, no minder imports.
"""

import hashlib
import json
import re

_API_KEY_RE = re.compile(
    r"\b(sk-proj-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"AKIA[0-9A-Z]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AIzaSy[A-Za-z0-9_-]{8,})")
_REDACTED = "***REDACTED***"

_TIMESTAMP_RES = tuple(re.compile(p) for p in (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?",
    r"\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]",
    r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
))

_WS_RE = re.compile(r"\s+")
# absolute POSIX paths: a leading / plus 1+ path segments; capture basename
_ABSPATH_RE = re.compile(r"(?<![\w.:@-])/(?:[\w.@+-]+/)*:?([\w.@+-]+)")
_LINE_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")

_FAMILY_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b",
                        re.IGNORECASE)
_EXIT_RE = re.compile(r"\bexit(?:ed with(?: code)?| code)? (\d+)\b",
                      re.IGNORECASE)
_NODEID_RE = re.compile(r"\b[\w./-]+\.(?:py|ts|js|go|rs)::[\w/\[\]-]+",
                        re.IGNORECASE)
_QUOTED_RE = re.compile(r"[\"']([\w][\w.\- /]{0,80})[\"']")
_IN_FUNC_RE = re.compile(r"\bin (\w+)\b", re.IGNORECASE)
_UNKNOWN = "none"


def redact(text):
    """Strip API-key-shaped strings from arbitrary text before storage."""
    return _API_KEY_RE.sub(_REDACTED, str(text or ""))


def normalise_error_excerpt(text, repo=None):
    """One canonical excerpt string: redacted, timestamp-free, path-normalised,
    lowercased, whitespace-collapsed."""
    out = redact(text)
    for rx in _TIMESTAMP_RES:
        out = rx.sub(" ", out)
    out = _relativise(out, repo)
    out = _WS_RE.sub(" ", out).strip().lower()
    return out


def _relativise(text, repo):
    if repo:
        prefix = str(repo).rstrip("/")
        if prefix:
            text = text.replace(prefix + "/", "")
            text = text.replace(prefix, ".")
    # remaining absolute paths → basename (documented fallback)
    return _ABSPATH_RE.sub(lambda m: m.group(1), text)


def error_family(excerpt):
    m = _FAMILY_RE.search(str(excerpt or ""))
    if m:
        return m.group(1).lower()
    m = _EXIT_RE.search(str(excerpt or ""))
    if m:
        return "exit-" + m.group(1)
    return "unknown"


def symbol_or_test_id(excerpt):
    text = str(excerpt or "")
    m = _NODEID_RE.search(text)
    if m:
        return _LINE_SUFFIX_RE.sub("", m.group(0))
    fam = error_family(text)
    if fam != "unknown":
        fam_at = _FAMILY_RE.search(text)
        start = fam_at.start() if fam_at else 0
        q = _QUOTED_RE.search(text[start:])
        if q and q.group(1).strip().lower() != _REDACTED.lower():
            return q.group(1).strip().lower()[:80]
    f = _IN_FUNC_RE.search(text)
    if f:
        return f.group(1).lower()
    return _UNKNOWN


def relpath_of(event, repo=None):
    """The file the failure is about: explicit file_path wins, else the
    first path-like token in the excerpt. Line suffixes are stripped."""
    repo = repo or event.get("repo") or ""
    p = str(event.get("file_path") or "").strip()
    if not p:
        norm = normalise_error_excerpt(event.get("error_excerpt", ""), repo)
        m = re.search(r"(?:^|[\s\"'=])([\w.@+-]+(?:/[\w.@+-]+)*\.[a-z]{1,4})",
                      norm)
        p = m.group(1) if m else ""
    if not p:
        return None
    p = _LINE_SUFFIX_RE.sub("", p)
    if repo and p.startswith(str(repo).rstrip("/")):
        p = p[len(str(repo).rstrip("/")):].lstrip("/")
    if p.startswith("/"):
        # documented fallback: no repo root to relativise against → basename
        p = p.rsplit("/", 1)[-1]
    return p or None


def canonical_action(event, repo=None):
    """The action identity: what was attempted, independent of outcome."""
    args_raw = event.get("args_json") or "{}"
    try:
        args = json.loads(args_raw)
        if not isinstance(args, dict):
            args = {"_": str(args)}
    except ValueError:
        args = {"_raw": str(args_raw)[:200]}
    return {
        "tool": str(event.get("tool") or "unknown").lower(),
        "command": _WS_RE.sub(" ", str(event.get("command") or "")).strip(),
        "file_path": relpath_of(event, repo),
        "args": {k: args[k] for k in sorted(args)},
    }


def failure_key(event, repo=None):
    """Stable key for one failure shape. Never raises (hook-path law)."""
    try:
        repo = repo or event.get("repo") or ""
        excerpt = normalise_error_excerpt(event.get("error_excerpt", ""), repo)
        return "|".join((
            str(event.get("tool") or "unknown").lower(),
            error_family(excerpt),
            symbol_or_test_id(excerpt),
            relpath_of(event, repo) or _UNKNOWN,
        ))
    except Exception:
        return "unknown|unknown|none|none"


def action_fingerprint(event, repo=None):
    """Stable hash of the attempted action. Never raises."""
    try:
        canon = canonical_action(event, repo)
        blob = json.dumps(canon, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def same_failure(a, b, repo=None):
    return failure_key(a, repo) == failure_key(b, repo)


def unchanged_retry(prev, current, repo=None):
    """True only when nothing about the attempt changed: same failure shape,
    same action, same involved content, same (absent) hypothesis. A changed
    content_hash or hypothesis means progress — never block on it."""
    return (same_failure(prev, current, repo)
            and action_fingerprint(prev, repo)
            == action_fingerprint(current, repo)
            and str(prev.get("content_hash") or "") ==
            str(current.get("content_hash") or "")
            and str(prev.get("hypothesis") or "") ==
            str(current.get("hypothesis") or ""))


# --- result identity (from minder_memory/success_guard.py) ----------------
# Numeric tokens collapse to `N` so outputs differing only in progress
# noise (curl speeds, byte counts) share one signature; genuinely
# different results do not.

SIGNATURE_CAP = 512

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[A-Za-z]?")   # 1.24M / 905.5k / 0
_RESULT_RUN_RE = re.compile(r"(?:N\s*)+")              # progress-bar reflow
_RESULT_WS_RE = re.compile(r"\s+")


def normalize_output(text):
    """Redact, collapse numeric tokens to N, collapse whitespace, cap.
    Deterministic; the exact bytes matter to the signature."""
    out = redact(str(text or ""))
    out = _NUMBER_RE.sub("N", out)
    # curl's progress bar REFLOWS between runs — the numeric slots move.
    # Fold entire runs of numbers into one N: the shape is the signal,
    # the count of slots is noise.
    out = _RESULT_RUN_RE.sub("N ", out)
    out = _RESULT_WS_RE.sub(" ", out).strip()
    return out[:SIGNATURE_CAP]


def result_signature(exit_code, output):
    """sha256 of exit code + normalized output. Same action + same
    (volatile-normalized) result => same signature."""
    normalized = normalize_output(output)
    blob = f"{exit_code}|{normalized}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
