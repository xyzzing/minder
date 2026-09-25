"""Read-only DSH session surfaces (sessions, projections, registry, logs).

Why: the console used to infer "sessions" from `~/.dsh/sessions/` directory
names alone, so it printed mangled paths, showed nothing about what
happened, and could only link an episode by exact `task_id` match (1 of 94
sessions). DSH already keeps far better data locally, and none of it was
being read:

  ~/.dsh/sessions/<project>/session-<uuid>/session.v{3,4}.jsonl.zstd
      the session log: tool calls, hook invocations/results, turns, the
      permission preset and the sandbox mode actually in effect
  ~/.dsh/storages/session_projcache/sessions/session-<uuid>.json
      per-session projections: cwd, title, token usage, context pressure,
      step/turn timings, goal, todos, plan
  ~/.dsh/storages/workspace.json
      workspace path/title per session id, plus the archived ids
  ~/.dsh/dsh-usage/usage-ledger.json
      per-day, per-model token totals

Everything here is read-only and fails soft: a missing or unrecognised
surface yields an empty value, never an exception. The path layout was
verified against dsh 0.1.7-rc.2.
"""
import json
import os
import time
from pathlib import Path

DEFAULT_CACHE_SECS = 5
_CACHE = {}
_EMPTY = {"rows": [], "counts": {}}


def dsh_home():
    """Root of the DSH home (MINDER_DSH_HOME > DSH_HOME > ~/.dsh)."""
    for var in ("MINDER_DSH_HOME", "DSH_HOME"):
        value = os.environ.get(var)
        if value:
            return Path(os.path.expanduser(value))
    return Path(os.path.expanduser("~/.dsh"))


def _cache_get(key, ttl):
    if ttl <= 0:
        return None
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _cache_put(key, value, ttl):
    if ttl > 0:
        _CACHE[key] = (time.time(), value)


def _ttl(cache):
    if cache is False:
        return 0
    try:
        return float(os.environ.get("MINDER_DSH_CACHE_SECS",
                                    DEFAULT_CACHE_SECS))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_SECS


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Projection cache + workspace registry
# --------------------------------------------------------------------------

def projection_cache(root=None, cache=True):
    """{session_id: {"identity": {...}, "rows": {name: value}}}.

    `rows[name]` is unwrapped from the stored `{ver, seq, val}` envelope so
    callers see plain values."""
    root = Path(root) if root else dsh_home()
    ttl = _ttl(cache)
    key = ("proj", str(root))
    hit = _cache_get(key, ttl)
    if hit is not None:
        return hit
    out = {}
    directory = root / "storages" / "session_projcache" / "sessions"
    if directory.is_dir():
        for entry in directory.glob("*.json"):
            record = _load_json(entry)
            if not isinstance(record, dict):
                continue
            record = record.get("record") or {}
            rows = {}
            for name, cell in (record.get("rows") or {}).items():
                if isinstance(cell, dict) and "val" in cell:
                    rows[name] = cell.get("val")
                else:
                    rows[name] = cell
            out[entry.stem] = {"identity": record.get("identity") or {},
                               "rows": rows}
    _cache_put(key, out, ttl)
    return out


def workspace_registry(root=None, cache=True):
    """({"session_id": {"path","title","workspace_id"}}, {"archived ids"})."""
    root = Path(root) if root else dsh_home()
    ttl = _ttl(cache)
    key = ("ws", str(root))
    hit = _cache_get(key, ttl)
    if hit is not None:
        return hit
    index, archived = {}, set()
    data = _load_json(root / "storages" / "workspace.json") or {}
    tables = (data.get("tables") or {}).get("workspaces") or {}
    for workspace_id, entry in tables.items():
        if not isinstance(entry, dict):
            continue
        for session_id in entry.get("sessionIds") or []:
            index[session_id] = {"path": entry.get("path") or "",
                                 "title": entry.get("title") or "",
                                 "workspace_id": workspace_id}
    for session_id in ((data.get("global") or {})
                       .get("archivedSessionIds") or []):
        archived.add(session_id)
    _cache_put(key, (index, archived), ttl)
    return index, archived


def usage_ledger(root=None, cache=True):
    """Per-day usage from dsh's own ledger ({} when unavailable)."""
    root = Path(root) if root else dsh_home()
    ttl = _ttl(cache)
    key = ("usage", str(root))
    hit = _cache_get(key, ttl)
    if hit is not None:
        return hit
    data = _load_json(root / "dsh-usage" / "usage-ledger.json") or {}
    days = data.get("days") if isinstance(data, dict) else None
    out = days if isinstance(days, dict) else {}
    _cache_put(key, out, ttl)
    return out


# --------------------------------------------------------------------------
# Session directories
# --------------------------------------------------------------------------

def project_from_dir(name):
    """Best-effort workspace path from a session directory name.

    DSH encodes the cwd as `--<path with / replaced by ->--`; the real
    path is usually available from the projection cache, so this is only
    the fallback."""
    text = str(name)
    if text.startswith("--") and text.endswith("--"):
        text = text[2:-2]
    elif text.startswith("-") or text.endswith("-"):
        text = text.strip("-")
    return text.replace("-", "/") if text.startswith("home-") else text


def session_dirs(root=None, cache=True):
    """One entry per `~/.dsh/sessions/<project>/session-<uuid>/`."""
    root = Path(root) if root else dsh_home()
    sessions_root = root / "sessions"
    out = []
    if not sessions_root.is_dir():
        return out
    for project in sorted(sessions_root.iterdir()):
        if not project.is_dir():
            continue
        for session in sorted(project.iterdir()):
            if not session.is_dir():
                continue
            logs = sorted(session.glob("session.v*.jsonl.zstd"))
            sizes = []
            for log in logs:
                try:
                    sizes.append((log, log.stat().st_size,
                                  log.stat().st_mtime))
                except OSError:
                    continue
            size = sum(item[1] for item in sizes)
            mtime = max((item[2] for item in sizes), default=None)
            fmt = None
            if logs:
                parts = logs[-1].name.split(".")
                fmt = parts[1] if len(parts) > 2 else None
            out.append({
                "session_id": session.name,
                "project_dir": project.name,
                "project_path": project_from_dir(project.name),
                "path": str(session),
                "log_path": str(logs[-1]) if logs else None,
                "format": fmt,
                "log_bytes": size,
                "mtime": mtime,
            })
    return out


# --------------------------------------------------------------------------
# Session log reading
# --------------------------------------------------------------------------

def _decompressor():
    """Best-available zstd reader. Returns a callable(path) -> bytes|None."""
    try:  # Python >= 3.14 stdlib
        from compression import zstd  # type: ignore

        def _read(path):
            with open(path, "rb") as fh:
                return zstd.decompress(fh.read())
        return _read
    except Exception:
        pass
    try:
        import zstandard  # type: ignore

        def _read(path):
            with open(path, "rb") as fh:
                return zstandard.ZstdDecompressor().decompress(
                    fh.read(), max_output_size=256 * 1024 * 1024)
        return _read
    except Exception:
        pass
    try:
        import subprocess

        def _read(path):
            proc = subprocess.run(["zstd", "-dc", path],
                                  capture_output=True, timeout=60,
                                  check=False)
            if proc.returncode != 0:
                return None
            return proc.stdout
        return _read
    except Exception:
        return None


def zstd_available():
    return _decompressor() is not None


def read_log_records(path, max_bytes=None):
    """Yield decoded JSON records from one session log (never raises)."""
    reader = _decompressor()
    if reader is None or not path:
        return
    try:
        blob = reader(str(path))
    except Exception:
        return
    if not blob:
        return
    if max_bytes is not None and len(blob) > max_bytes:
        blob = blob[:max_bytes]
    for line in blob.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def log_stats(path, max_bytes=None):
    """Derived counters for one session log: turns, steps, tool calls by
    name, hook invocations/results (with durations), failures, and the
    sandbox/permission/mode records that explain capture behaviour."""
    stats = {"turns": 0, "steps": 0, "tool_calls": 0, "tools": {},
             "hook_invocations": 0, "hook_results": 0, "hook_failures": 0,
             "hook_ms": [], "hook_points": {}, "sandbox_mode": None,
             "permission_preset": None, "model": None, "errors": 0,
             "first_ts": None, "last_ts": None, "tool_failures": 0}
    if not path:
        return stats
    for record in read_log_records(path, max_bytes=max_bytes):
        kind = record.get("type")
        when = record.get("time")
        if isinstance(when, (int, float)):
            stats["first_ts"] = stats["first_ts"] or when
            stats["last_ts"] = when
        data = record.get("data") if isinstance(record.get("data"), dict) \
            else {}
        if kind == "turn/start":
            stats["turns"] += 1
        elif kind == "step/start":
            stats["steps"] += 1
        elif kind == "tool/call":
            stats["tool_calls"] += 1
            name = str(data.get("name") or "?")
            stats["tools"][name] = stats["tools"].get(name, 0) + 1
        elif kind == "hook/invoked":
            stats["hook_invocations"] += 1
            point = str(data.get("point") or "?")
            stats["hook_points"][point] = stats["hook_points"].get(point, 0) + 1
        elif kind == "hook/result":
            stats["hook_results"] += 1
            if data.get("exitCode") not in (0, None):
                stats["hook_failures"] += 1
            ms = data.get("durationMs")
            if isinstance(ms, (int, float)):
                stats["hook_ms"].append(float(ms))
        elif kind == "sandbox/mode":
            stats["sandbox_mode"] = data.get("mode")
        elif kind == "permission/preset":
            stats["permission_preset"] = data.get("preset")
        elif kind == "model/selection":
            stats["model"] = f"{data.get('provider')}/{data.get('model')}"
        elif kind == "tool/result":
            text = str(data.get("content") or "")
            if "[exit code:" in text and "[exit code: 0]" not in text:
                stats["tool_failures"] += 1
    return stats


def hook_duration_stats(values):
    """p50 / p90 / max for a list of hook durations in ms."""
    clean = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not clean:
        return {"n": 0, "p50_ms": None, "p90_ms": None, "max_ms": None}
    return {"n": len(clean),
            "p50_ms": round(clean[len(clean) // 2], 1),
            "p90_ms": round(clean[min(len(clean) - 1,
                                      int(len(clean) * 0.9))], 1),
            "max_ms": round(clean[-1], 1)}


# --------------------------------------------------------------------------
# The joined view
# --------------------------------------------------------------------------

def list_sessions(root=None, db_counts=None, now=None, cache=True,
                  log_scan_limit=0):
    """One row per dsh session, joined with the projection cache, the
    workspace registry, and optional DB counts.

    `log_scan_limit` bounds how many of the most recent session logs are
    decompressed for hook/tool counters (0 = none: the list page stays
    cheap and the detail page does the work)."""
    root = Path(root) if root else dsh_home()
    now = now if now is not None else time.time()
    proj = projection_cache(root, cache=cache)
    index, archived = workspace_registry(root, cache=cache)
    db_counts = db_counts or {}
    rows = []
    for entry in session_dirs(root, cache=cache):
        session_id = entry["session_id"]
        cache_record = proj.get(session_id) or {}
        identity = cache_record.get("identity") or {}
        prow = cache_record.get("rows") or {}
        workspace = index.get(session_id) or {}
        stats = prow.get("sessionStats") or {}
        tokens = (prow.get("tokenUsage") or {}).get("totals") or {}
        pressure = prow.get("contextPressure") or {}
        context_window = pressure.get("contextWindow")
        pressure_tokens = pressure.get("pressureTokens")
        pct = None
        if context_window and pressure_tokens is not None:
            try:
                pct = round(100.0 * float(pressure_tokens)
                            / float(context_window), 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        project_path = identity.get("cwd") or workspace.get("path") \
            or entry["project_path"]
        mtime = entry["mtime"]
        counts = db_counts.get(session_id) or {}
        row = {
            "session_id": session_id,
            "project_path": project_path,
            "project_title": workspace.get("title")
            or Path(project_path).name or entry["project_dir"],
            "title": prow.get("title") or "",
            "format": entry["format"],
            "log_bytes": entry["log_bytes"],
            "mtime": mtime,
            "age_s": (now - mtime) if mtime else None,
            "archived": session_id in archived,
            "sandbox_mode": (prow.get("sandboxMode")
                             or (prow.get("permissions") or {}).get(
                                 "sandbox")
                             or (prow.get("permissions") or {}).get(
                                 "preset")),
            "permissions": prow.get("permissions") or {},
            "turns": stats.get("turns"),
            "steps": stats.get("steps"),
            "llm_ms": stats.get("llmMs"),
            "tool_ms": stats.get("toolMs"),
            "tokens_total": (int(tokens.get("uncachedInputTokens", 0) or 0)
                             + int(tokens.get("outputTokens", 0) or 0)
                             + int(tokens.get("cacheReadTokens", 0) or 0)),
            "context_pressure_pct": pct,
            "goal": (prow.get("goal") or {}).get("current")
            if isinstance(prow.get("goal"), dict) else None,
            "episode_ids": list(counts.get("episode_ids") or []),
            "episode_count": len(counts.get("episode_ids") or []),
            "event_count": int(counts.get("event_count") or 0),
            "has_projection": bool(cache_record),
            "log_ok": bool(entry["log_path"]),
            "hook_invocations": None,
            "tool_calls": None,
            "hook_p50_ms": None,
        }
        if log_scan_limit:
            row["_log_path"] = entry["log_path"]
        rows.append(row)
    if log_scan_limit:
        scan = sorted((r for r in rows if r.pop("_log_path", None)),
                      key=lambda r: r["mtime"] or 0, reverse=True)[
                          :log_scan_limit]
        by_id = {r["session_id"]: r for r in rows}
        for row in scan:
            source = next((e["log_path"] for e in session_dirs(root)
                           if e["session_id"] == row["session_id"]), None)
            stats = log_stats(source)
            target = by_id[row["session_id"]]
            target["hook_invocations"] = stats["hook_invocations"]
            target["tool_calls"] = stats["tool_calls"]
            target["hook_p50_ms"] = hook_duration_stats(
                stats["hook_ms"])["p50_ms"]
    rows.sort(key=lambda r: (r["mtime"] or 0), reverse=True)
    return {"rows": rows, "counts": {"sessions": len(rows),
                                     "archived": sum(1 for r in rows
                                                     if r["archived"]),
                                     "with_projection":
                                         sum(1 for r in rows
                                             if r["has_projection"])}}


def session_detail(session_id, root=None, db_counts=None, now=None,
                   cache=True, max_bytes=64 * 1024 * 1024):
    """Everything the detail page shows for one session: the projection
    summary, the log-derived counters, and the DB linkage."""
    listing = list_sessions(root=root, db_counts=db_counts, now=now,
                            cache=cache, log_scan_limit=0)
    row = next((r for r in listing["rows"]
                if r["session_id"] == session_id), None)
    if row is None:
        return None
    entry = next((e for e in session_dirs(root, cache=cache)
                  if e["session_id"] == session_id), None)
    stats = log_stats((entry or {}).get("log_path"), max_bytes=max_bytes)
    proj = projection_cache(root, cache=cache).get(session_id) or {}
    detail = dict(row)
    detail.update({
        "stats": stats,
        # the list row leaves these unset (it does not read logs); the
        # detail page has the log open, so it fills them in
        "hook_invocations": stats["hook_invocations"],
        "tool_calls": stats["tool_calls"],
        "hook_p50_ms": hook_duration_stats(stats["hook_ms"])["p50_ms"],
        "hook": hook_duration_stats(stats["hook_ms"]),
        "tools": sorted(stats["tools"].items(), key=lambda kv: -kv[1]),
        "hook_points": sorted(stats["hook_points"].items(),
                              key=lambda kv: -kv[1]),
        "session_stats": proj.get("rows", {}).get("sessionStats") or {},
        "todos": proj.get("rows", {}).get("todos") or [],
        "plan": proj.get("rows", {}).get("plan") or {},
        "token_usage": proj.get("rows", {}).get("tokenUsage") or {},
        "log_path": (entry or {}).get("log_path"),
        "log_stats_available": bool((entry or {}).get("log_path"))
        and zstd_available(),
        "created_at": (proj.get("identity") or {}).get("createdAt"),
    })
    return detail
