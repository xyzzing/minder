"""DSH session trace → Minder canonical events.

Pure and deterministic: no I/O beyond the read-only delegation to
`minder_op.dsh_sessions`, no model calls, never raises.

Everything downstream (evaluators, storage, CLI, web) consumes the event
dicts produced here, so the mapping decisions all live in this module:

  * `tool/call` carries `data.arguments` as a JSON *string*; it is parsed
    (raw-string fallback), never trusted to be a dict.
  * `tool/result` text is nested three levels deep
    (`data.message.content[].content[].text`) and arrives as a list of
    blocks; it is flattened, capped and redacted here.
  * Pairing is by `callId` (`data.callId` on the call,
    `data.message.source.callId` on the result). An unpaired call stays in
    the output with `paired=False` rather than being dropped — a truncated
    trace is evidence about the run, not a reason to hide it.
  * The canonical event shape matches what `memory/from_hook.to_event`
    produces, so `memory/canonicalise.py` (failure_key,
    action_fingerprint, unchanged_retry) and `memory/success_guard.py`
    (result_signature) apply to offline traces with no parallel
    normalisation and no second definition of "the same failure".
"""
import hashlib
import json

import minder
from memory import canonicalise as canon

# A single tool result can be a whole file or a full test log. This is a
# memory bound on what the review process may hold, not a display cap.
RAW_CAP = 200_000
# What a finding shows a human (the repo's EXCERPT_CAP pattern).
EXCERPT_CAP = 160

# Read-before-edit grounding: tools that count as having looked at a file.
READ_TOOLS = ("read", "cat", "view", "fs_read", "open", "head", "tail",
              "bat", "sed", "grep", "rg", "glob")
# Edit-shaped tools. The Warden's own set, so "an edit" means one thing in
# both the live guard and the offline evaluator.
EDIT_TOOLS = tuple(getattr(minder, "_EDIT_TOOLS", ())) + (
    "str_replace_editor", "save_file")
# Test-shaped commands (the `unverified_change` evidence standard).
# `python -m pytest`, `pytest`, `npm test`, `make test`, …
TEST_SIGNS = ("pytest", "unittest", "npm test", "npm run test", "pnpm test",
              "yarn test", "make test", "make check", "go test",
              "cargo test", "tox", "nox", "jest", "vitest", "ctest",
              "verify.py", "run_tests", "test_")

# Shell failure marker DSH writes into tool results ("[exit code: 1]").
_EXIT_MARKER = "[exit code:"

# Event types the run model does not need but that are perfectly normal.
# Anything NOT here and NOT explicitly handled is reported as an
# unrecognised type — that is the forward-compatibility signal for a DSH
# schema change, so it must stay quiet for routine traffic.
_IGNORED_TYPES = frozenset((
    "assistant/message", "assistant/attempt", "user/message",
    "system/message", "request/header", "request/context",
    "agent/inbox/spliced", "agent/inbox/drained", "deliverables/presented",
    "permission/preset", "permission/decision", "sandbox/mode",
    "approval/policy", "model/selection", "session/title",
    "session/title-llm-request", "session/title-llm-response",
    "session/end-seed", "usage/record", "token/usage", "auto/effort",
    "step/end", "turn/end", "agent/status",
))


def _as_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _json_or_raw(text):
    """`tool/call.arguments` is a JSON string. Parse it; a non-object or
    malformed payload degrades to a raw string, never a raise."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}, str(text or "")
    if isinstance(parsed, dict):
        return parsed, str(text or "")
    return {"_": parsed}, str(text or "")


def result_text(data):
    """Flatten a `tool/result` payload's content blocks into one string."""
    if not isinstance(data, dict):
        return ""
    parts = []
    message = data.get("message")
    if isinstance(message, dict):
        for block in _as_list(message.get("content")):
            if not isinstance(block, dict):
                if isinstance(block, str):
                    parts.append(block)
                continue
            inner = block.get("content")
            if inner is None:
                if block.get("text"):
                    parts.append(str(block["text"]))
                continue
            for item in _as_list(inner):
                if isinstance(item, dict) and item.get("text") is not None:
                    parts.append(str(item["text"]))
                elif isinstance(item, str):
                    parts.append(item)
    if not parts and data.get("content"):
        parts.append(str(data["content"]))
    return "\n".join(p for p in parts if p)


def exit_code_of(text):
    """The `[exit code: N]` marker DSH appends to shell results, or None."""
    idx = str(text or "").find(_EXIT_MARKER)
    if idx < 0:
        return None
    tail = str(text)[idx + len(_EXIT_MARKER):].strip()
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
        elif ch not in " :":
            return None
    return int(digits) if digits else None


def _is_read(tool):
    low = str(tool or "").lower()
    return any(t in low for t in READ_TOOLS)


def _tool_args_summary(tool, args):
    """The human-facing argument summary: a shell command stays a command
    (that is what a reviewer reads), anything else is canonical JSON."""
    if not isinstance(args, dict):
        return ""
    command = args.get("command")
    if isinstance(command, str) and command.strip():
        return " ".join(command.split())
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return " ".join(blob.split())


def _canonical_event(*, tool, session_id, repo, args, text, file_path,
                     call_id, seq):
    """One normalized event, in the shape canonicalise/from_hook expect."""
    if not isinstance(args, dict):
        args = {}
    redacted_args = {}
    for key, value in args.items():
        s = value if isinstance(value, str) else json.dumps(value,
                                                            default=str)
        redacted_args[key] = canon.redact(str(s))
    text = str(text or "")[:RAW_CAP]
    failed = bool(minder.is_failure(text))
    code = exit_code_of(text)
    if code not in (None, 0):
        failed = True
    excerpt = canon.redact(" ".join(text.split()))[:EXCERPT_CAP]
    try:
        args_json = json.dumps(redacted_args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_json = "{}"
    event = {
        "event_type": "tool_failure" if failed else "tool_success",
        "tool": str(tool or ""),
        "session_id": session_id,
        "task_id": session_id,
        "repo": repo,
        # Redacted here, at the boundary: `command` is stored, rendered and
        # shown in findings, so a secret in an argument must not survive in
        # the human-readable summary even though args_json was scrubbed.
        "command": canon.redact(_tool_args_summary(tool, args)),
        "args_json": args_json,
        "file_path": str(file_path or ""),
        "error_excerpt": canon.redact(text)[:RAW_CAP],
        "content_hash": "",
        "hypothesis": "",
        # display-only, redacted, capped; used by findings
        "excerpt": excerpt,
        "exit_code": code,
        "status": "error" if failed else "success",
        "ds_call_id": call_id,
        "ds_seq": seq,
    }
    event["failure_key"] = canon.failure_key(event, repo)
    event["action_fingerprint"] = canon.action_fingerprint(event, repo)
    return event


def _blank_report():
    return {
        "status": "ok",
        "records": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "paired": 0,
        "unpaired": 0,
        "failures": 0,
        "successes": 0,
        "truncated_results": 0,
        "turns": 0,
        "steps": 0,
        "hook_invocations": 0,
        "hook_results": 0,
        "hook_blocks": 0,
        "approvals_asked": 0,
        "approvals_decided": 0,
        "compactions": 0,
        "plan_mode_transitions": 0,
        "todo_writes": 0,
        "unknown_types": 0,
        "first_ts": None,
        "last_ts": None,
        "redaction_status": "redacted",
        "notes": [],
    }


def normalize(records, *, session_id="", repo="", meta=None):
    """Normalize already-decoded DSH session records.

    Returns `(run, report)`. Never raises: an unusable input degrades to a
    run with `report["status"]`, because the caller (CLI, hook, web) must
    always be able to render *something*.
    """
    report = _blank_report()
    # A detached copy: the caller's metadata dict is never mutated.
    meta = dict(meta) if isinstance(meta, dict) else {}
    events = []
    pending = {}
    order = []
    plan_events = []
    approvals = []
    timeline = []
    todo_state = None
    unknown = set()

    try:
        for index, record in enumerate(records or ()):
            if not isinstance(record, dict):
                continue
            report["records"] += 1
            kind = record.get("type")
            data = record.get("data") if isinstance(record.get("data"),
                                                    dict) else {}
            when = record.get("time")
            if isinstance(when, (int, float)):
                report["first_ts"] = report["first_ts"] or when
                report["last_ts"] = when
            seq = record.get("seq")
            if seq is None:
                seq = index

            if kind == "session":
                meta.setdefault("ds_session_id", record.get("id"))
                meta.setdefault("cwd", record.get("cwd"))
                meta.setdefault("created_at", record.get("createdAt"))
            elif kind == "tool/call":
                call_id = data.get("callId")
                args, _raw = _json_or_raw(data.get("arguments"))
                tool = data.get("name") or ""
                file_path = (args.get("file_path") or args.get("path")
                             or args.get("notebook_path") or "")
                event = _canonical_event(
                    tool=tool, session_id=session_id, repo=repo, args=args,
                    text="", file_path=file_path, call_id=call_id, seq=seq)
                event["turn"] = data.get("turn")
                event["step"] = data.get("step")
                event["paired"] = False
                event["result_excerpt"] = ""
                event["ts"] = when
                pending[call_id] = event
                order.append(event)
                report["tool_calls"] += 1
            elif kind == "tool/result":
                report["tool_results"] += 1
                call_id = ((data.get("message") or {}).get("source")
                           or {}).get("callId")
                text = result_text(data)
                if len(text) > RAW_CAP:
                    report["truncated_results"] += 1
                target = pending.pop(call_id, None)
                if target is None:
                    # A result whose call is missing (or duplicated) is
                    # still evidence; keep it as its own unpaired record.
                    report["unpaired"] += 1
                    event = _canonical_event(
                        tool="", session_id=session_id, repo=repo, args={},
                        text=text, file_path="", call_id=call_id, seq=seq)
                    event["turn"] = data.get("turn")
                    event["step"] = data.get("step")
                    event["paired"] = False
                    event["result_excerpt"] = event["excerpt"]
                    event["ts"] = when
                    order.append(event)
                    continue
                result = _canonical_event(
                    tool=target["tool"], session_id=session_id, repo=repo,
                    args={}, text=text, file_path=target["file_path"],
                    call_id=call_id, seq=target["ds_seq"])
                # Only outcome-dependent fields come from the result: the
                # action fingerprint is by definition outcome-independent
                # (canonical_action reads tool/command/args/file_path), so
                # it keeps the value computed on the call side.
                target["error_excerpt"] = result["error_excerpt"]
                target["failure_key"] = result["failure_key"]
                target["event_type"] = result["event_type"]
                target["status"] = result["status"]
                target["exit_code"] = result["exit_code"]
                target["paired"] = True
                target["result_excerpt"] = result["excerpt"]
            elif kind == "turn/start":
                report["turns"] += 1
            elif kind == "step/start":
                report["steps"] += 1
            elif kind == "hook/invoked":
                report["hook_invocations"] += 1
            elif kind == "hook/result":
                report["hook_results"] += 1
                if str(data.get("decision")) in ("block", "deny"):
                    report["hook_blocks"] += 1
            elif kind == "approval/asked":
                report["approvals_asked"] += 1
            elif kind == "approval/decided":
                report["approvals_decided"] += 1
                approvals.append({"id": data.get("id"),
                                  "outcome": data.get("outcome"),
                                  "seq": seq, "ts": when})
                timeline.append({"seq": seq, "ts": when, "kind": "approval",
                                 "detail": str(data.get("outcome") or "")})
            elif kind == "plan/mode":
                report["plan_mode_transitions"] += 1
                active = bool(data.get("active"))
                plan_events.append({"active": active, "seq": seq, "ts": when})
                timeline.append({"seq": seq, "ts": when, "kind": "plan_mode",
                                 "detail": "on" if active else "off"})
            elif kind == "todo/write":
                report["todo_writes"] += 1
                todos = data.get("todos")
                if isinstance(todos, list):
                    todo_state = {
                        "seq": seq, "ts": when,
                        "items": [{"content": canon.redact(
                            str((t or {}).get("content") or ""))[:EXCERPT_CAP],
                            "status": str((t or {}).get("status") or "")}
                            for t in todos if isinstance(t, dict)],
                    }
                    todo_state["signature"] = _todo_signature(todo_state)
                    timeline.append({"seq": seq, "ts": when, "kind": "todo",
                                     "detail": todo_state["signature"]})
            elif isinstance(kind, str) and kind.startswith("compaction/"):
                if kind == "compaction/start":
                    report["compactions"] += 1
            else:
                if isinstance(kind, str) and kind not in _IGNORED_TYPES:
                    unknown.add(kind)

        # A call with no result at all, counted once, after the walk.
        report["unpaired"] += len(pending)
        for event in order:
            if event["event_type"] == "tool_failure":
                report["failures"] += 1
            else:
                report["successes"] += 1
        report["paired"] = report["tool_calls"] - len(pending)
        if pending:
            report["notes"].append(
                f"{len(pending)} tool call(s) had no result "
                "(truncated or in-progress trace)")
        report["unknown_types"] = len(unknown)
        if unknown:
            report["notes"].append(
                "unrecognised event types ignored: "
                + ", ".join(sorted(unknown)[:8]))
    except Exception as exc:  # noqa: BLE001 — fail open, never raise
        report["status"] = f"degraded:{type(exc).__name__}"

    events = order
    run = {
        "run_id": run_id_for(str(meta.get("session_id") or session_id)),
        "source": {
            "runtime": "deepseek-harness",
            "session_id": str(meta.get("session_id") or session_id),
            "trace_format": meta.get("format"),
            "log_bytes": meta.get("log_bytes"),
        },
        "task": {
            "input": str(meta.get("title") or ""),
            "task_type": _task_type(meta.get("project_path")),
            "project_path": meta.get("project_path"),
        },
        "events": events,
        # Ordered non-tool progress signals (todo writes, plan-mode
        # transitions, approvals). Kept separate from `events` so the tool
        # timeline stays a tool timeline, while `no_progress` can still see
        # that the agent actually changed its plan or its todo list.
        "timeline": timeline,
        "output": {"final_answer": "", "citations": []},
        "execution": {
            "turns": report["turns"],
            "steps": report["steps"],
            "tool_calls": report["tool_calls"],
            "duration_ms": meta.get("llm_ms") if meta.get("llm_ms")
            is not None else None,
            "tool_ms": meta.get("tool_ms"),
            "tokens_total": meta.get("tokens_total"),
            "context_pressure_pct": meta.get("context_pressure_pct"),
            # Per-run cost is NOT attributable in this deployment: dsh's
            # usage ledger is per-day/per-model. Kept explicit rather than
            # guessed from tokens.
            "estimated_cost": None,
            "cost_note": "not attributable per run (dsh usage ledger is "
                         "per-day/per-model)",
        },
        "governance": {
            "sandbox_mode": meta.get("sandbox_mode"),
            "plan_mode_events": plan_events,
            "approvals": approvals,
            "todo_state": todo_state,
            "hook_invocations": report["hook_invocations"],
            "hook_results": report["hook_results"],
            "hook_blocks": report["hook_blocks"],
        },
    }
    return run, report


def _todo_signature(state):
    items = state.get("items") or []
    blob = "|".join(f"{i.get('status')}:{i.get('content')}" for i in items)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _task_type(project_path):
    """Task class from the workspace, for rubric applicability. Not
    inferred from the model's text — a workspace is a fact."""
    name = str(project_path or "").rstrip("/").split("/")[-1].lower()
    if not name:
        return "unknown"
    if any(t in name for t in ("minder", "code", "app", "api", "web")):
        return "coding"
    return "general"


def run_id_for(session_id):
    """Deterministic, so re-reviewing a session is idempotent."""
    return "mndr_run_" + hashlib.sha1(
        str(session_id or "").encode()).hexdigest()[:12]


def load_session(session_id=None, *, root=None, cache=True,
                 max_bytes=64 * 1024 * 1024):
    """Resolve a completed DSH session and normalize it.

    Delegates all DSH access to `minder_op.dsh_sessions` (zstd handling,
    session discovery, projection-cache join). Read-only: nothing under
    the DSH home is ever written.

    `session_id` may be an exact id, an id suffix, or a project slug; None
    picks the newest session. Returns `(run, report)`; an unknown session
    yields an empty run with `report["status"] == "not_found"` rather than
    an exception."""
    from minder_op import dsh_sessions
    try:
        entries = dsh_sessions.session_dirs(root, cache=cache)
    except Exception as exc:  # noqa: BLE001
        run, _rep = normalize([], session_id=str(session_id or ""))
        return run, dict(_blank_report(),
                         status=f"degraded:{type(exc).__name__}")
    entry = _pick_session(entries, session_id)
    if entry is None:
        report = _blank_report()
        report["status"] = "not_found"
        report["notes"].append(
            f"no session log matching {session_id!r} under "
            f"{dsh_sessions.dsh_home()}")
        run, _rep = normalize([], session_id=str(session_id or ""))
        return run, report

    meta = {}
    try:
        rows = dsh_sessions.list_sessions(root, cache=cache).get("rows") or []
        meta = next((r for r in rows
                     if r.get("session_id") == entry["session_id"]), {}) or {}
    except Exception:  # noqa: BLE001 — the join is a nicety, not a need
        meta = {}
    meta = dict(meta)
    meta["session_id"] = entry["session_id"]
    meta["format"] = entry.get("format")
    meta["log_bytes"] = entry.get("log_bytes")
    meta.setdefault("project_path", entry.get("project_path"))

    records = []
    if entry.get("log_path"):
        try:
            records = list(dsh_sessions.read_log_records(
                entry["log_path"], max_bytes=max_bytes))
        except Exception as exc:  # noqa: BLE001
            run, report = normalize([], session_id=entry["session_id"],
                                    meta=meta)
            report["status"] = f"degraded:{type(exc).__name__}"
            return run, report
    run, report = normalize(records, session_id=entry["session_id"],
                            repo=str(meta.get("project_path") or ""),
                            meta=meta)
    if not entry.get("log_path"):
        report["status"] = "degraded:no-log"
        report["notes"].append("session has no readable session log")
    run["source"]["log_path"] = entry.get("log_path")
    return run, report


def _pick_session(entries, session_id):
    """Newest-first resolution: exact id, then suffix, then slug, then
    newest overall when no id is requested."""
    ordered = sorted(entries or (), key=lambda e: e.get("mtime") or 0,
                     reverse=True)
    if not ordered:
        return None
    if not session_id:
        return ordered[0]
    want = str(session_id)
    for entry in ordered:
        if entry.get("session_id") == want:
            return entry
    for entry in ordered:
        sid = str(entry.get("session_id") or "")
        if sid.endswith(want) or want in sid:
            return entry
    for entry in ordered:
        if want in str(entry.get("project_dir") or ""):
            return entry
    return None
