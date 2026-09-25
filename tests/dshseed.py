"""Fixture builder for a synthetic dsh home.

The console and the capture/scorecard read models consume dsh's own
surfaces; tests must never read the developer's real `~/.dsh`. This module
writes the same shapes we verified against dsh 0.1.7-rc.2, so the readers
are exercised on realistic input:

  <root>/sessions/<project-dir>/session-<id>/session.v{3,4}.jsonl.zstd
  <root>/storages/session_projcache/sessions/session-<id>.json
  <root>/storages/workspace.json
  <root>/dsh-usage/usage-ledger.json
"""
import json
import os


def zstd_compress(blob):
    """Compress with whichever zstd is available; None when none is.

    Python 3.14 ships `compression.zstd`; older interpreters fall back to
    the `zstandard` module or the CLI. The caller skips log-based
    assertions when this returns None."""
    try:
        from compression import zstd  # type: ignore
        return zstd.compress(blob)
    except Exception:
        pass
    try:
        import zstandard  # type: ignore
        return zstandard.ZstdCompressor().compress(blob)
    except Exception:
        pass
    try:
        import subprocess
        proc = subprocess.run(["zstd", "-q", "-c"], input=blob,
                              capture_output=True, timeout=30)
        return proc.stdout if proc.returncode == 0 else None
    except Exception:
        return None


def project_dir_for(path):
    """dsh's directory encoding for a cwd."""
    return "--" + str(path).strip("/").replace("/", "-") + "--"


def _ts(epoch_ms):
    return epoch_ms


def write_session(root, session_id, cwd="/home/dev/proj", *,
                  fmt="v4", base_ms=1_790_000_000_000, steps=1,
                  tool_calls=("bash",), hook_ms=5800.0, hook_exit=0,
                  tool_failure=False, sandbox="workspace-write",
                  preset="workspace-write", title=None):
    """Write one session directory with a readable log. Returns the dir."""
    directory = (root / "sessions" / project_dir_for(cwd) / session_id)
    directory.mkdir(parents=True, exist_ok=True)
    records = [{"type": "session", "version": int(fmt[1:]),
                "id": session_id, "createdAt": base_ms, "cwd": cwd,
                "isSeeded": False, "delegationDepth": 0,
                "agentPreset": "standard"},
               {"type": "permission/preset", "seq": 0, "time": base_ms,
                "data": {"preset": preset}},
               {"type": "sandbox/mode", "seq": 1, "time": base_ms + 1,
                "data": {"mode": sandbox}}]
    seq = 2
    when = base_ms + 1000
    for _ in range(steps):
        records.append({"type": "turn/start", "seq": seq, "time": when,
                        "data": {}})
        seq += 1
        for tool in tool_calls:
            records.append({"type": "step/start", "seq": seq,
                            "time": when, "data": {}})
            seq += 1
            records.append({"type": "tool/call", "seq": seq, "time": when,
                            "data": {"name": tool, "callId": f"c{seq}"}})
            seq += 1
            records.append({"type": "hook/invoked", "seq": seq,
                            "time": when,
                            "data": {"turn": 1, "point": "PostToolUse",
                                     "dialect": "claude-code",
                                     "handlerId": f"h{seq}", "matcher": ""}})
            seq += 1
            records.append({"type": "hook/result", "seq": seq,
                            "time": when + int(hook_ms),
                            "data": {"turn": 1, "point": "PostToolUse",
                                     "handlerId": f"h{seq}",
                                     "decision": "pass",
                                     "exitCode": hook_exit,
                                     "durationMs": hook_ms}})
            seq += 1
            content = ("boom\n[exit code: 1]" if tool_failure
                       else "ok\n[exit code: 0]")
            records.append({"type": "tool/result", "seq": seq,
                            "time": when, "data": {"content": content}})
            seq += 1
            when += 2000
        records.append({"type": "turn/end", "seq": seq, "time": when,
                        "data": {}})
        seq += 1
    blob = ("\n".join(json.dumps(r) for r in records) + "\n").encode()
    packed = zstd_compress(blob)
    if packed is None:
        return None
    (directory / f"session.{fmt}.jsonl.zstd").write_bytes(packed)
    if title:
        write_projection(root, session_id, cwd=cwd, title=title,
                         base_ms=base_ms)
    return directory


def write_projection(root, session_id, *, cwd="/home/dev/proj",
                     title="a session", base_ms=1_790_000_000_000,
                     turns=1, steps=10, tokens=1000, pressure=0.25,
                     window=1000000, sandbox="workspace-write",
                     format_version=4, retries=False, todos=None):
    directory = root / "storages" / "session_projcache" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    rows = {
        "title": {"ver": 1, "seq": 5, "val": title},
        "sandboxMode": {"ver": 1, "seq": 5, "val": sandbox},
        "permissions": {"ver": 1, "seq": 5,
                        "val": {"preset": sandbox, "sandbox": sandbox,
                                "approval": "ask"}},
        "goal": {"ver": 1, "seq": 5, "val": {"current": None}},
        "tokenUsage": {"ver": 1, "seq": 5,
                       "val": {"totals": {"uncachedInputTokens": tokens,
                                          "outputTokens": tokens,
                                          "cacheReadTokens": 0,
                                          "cacheWriteTokens": 0},
                               "last": None}},
        "contextPressure": {"ver": 1, "seq": 5,
                            "val": {"surfaceTokens": int(window * pressure),
                                    "contextWindow": window,
                                    "pressureTokens": int(window * pressure),
                                    "sampledSurfaceTokens": 0}},
        "sessionStats": {"ver": 1, "seq": 5,
                         "val": {"turns": turns, "steps": steps,
                                 "llmMs": 1000, "toolMs": 500, "ttftMs": 100,
                                 "decodeMs": 900, "decodeTokens": 42,
                                 "lastTurn": turns, "openStep": None,
                                 "pendingCalls": {}}},
        "todos": {"ver": 1, "seq": 5, "val": todos or []},
    }
    if retries:
        rows["llmRetry"] = {"ver": 1, "seq": 5,
                            "val": {"[\"p\",\"m\"]": {"retry": 2}}}
    record = {"version": 1,
              "record": {"identity": {"formatVersion": format_version,
                                      "createdAt": base_ms, "cwd": cwd,
                                      "isSeeded": False},
                         "rows": rows}}
    (directory / f"{session_id}.json").write_text(json.dumps(record))
    return directory / f"{session_id}.json"


def write_workspace(root, entries, archived=()):
    """entries: [(path, title, [session ids])]."""
    directory = root / "storages"
    directory.mkdir(parents=True, exist_ok=True)
    tables, ids = {}, []
    for path, title, sessions in entries:
        workspace_id = "ws-" + str(len(tables))
        ids.append(workspace_id)
        tables[workspace_id] = {"path": path, "title": title,
                                "sessionIds": list(sessions)}
    payload = {"unit": {"name": "workspace", "version": 2},
               "global": {"initialized": True, "workspaceIds": ids,
                          "archivedSessionIds": list(archived)},
               "tables": {"workspaces": tables}}
    (directory / "workspace.json").write_text(json.dumps(payload))
    return directory / "workspace.json"


def write_usage(root, days):
    directory = root / "dsh-usage"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "usage-ledger.json").write_text(
        json.dumps({"version": 1, "days": days}))
    return directory / "usage-ledger.json"


def make_home(tmp_path, *, session_id="session-aaaa-bbbb",
              cwd="/home/dev/proj"):
    """A minimal but complete dsh home. Returns (root, log_written)."""
    root = tmp_path / "dsh"
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    written = write_session(root, session_id, cwd)
    write_projection(root, session_id, cwd=cwd, title="fixture session")
    write_workspace(root, [(cwd, os.path.basename(cwd), [session_id])])
    write_usage(root, {"2026-09-25": {"p": {"m": {"inputTokens": 10,
                                                  "outputTokens": 5}}}})
    return root, written is not None
