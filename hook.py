#!/usr/bin/env python3
"""minder hook — stdin JSON event → escalation directive. Stdlib only.

One script, two transports (prd.md §5.1/§5.2 + dsh hooks-bridge semantics):
  zcode (default): always exit 0; block directive as
      {"decision": "block", "reason": <digest>} on stdout.
  dsh:             Claude-Code hooks-bridge command hook — block = exit 2 with
      the digest on stderr (the bridge's verified model-visible channel);
      non-block = exit 0, silent. Non-blocking model-visible notes ride
      exit-0 stdout as hookSpecificOutput.additionalContext.

Hook points: SessionStart, PostToolUse, and (success-loop block mode
only) PreToolUse for pre-emptive stops.

Detection never blocks the hot path (Law #6): every failure mode exits 0 and
lets the tool result through untouched.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minder
import reflex
try:
    from memory import from_hook as memory_from_hook
    from memory import policy as memory_policy
    from memory import sink as memory_sink
except Exception:  # memory is optional; the watchdog never depends on it
    memory_from_hook = None
    memory_policy = None
    memory_sink = None

FRONTIER_TIMEOUT = int(os.environ.get("MINDER_FRONTIER_TIMEOUT", "60"))
DEFAULT_HOOK_BUDGET_MS = 750


def _sink_enabled():
    try:
        return memory_sink is not None and memory_sink.enabled()
    except Exception:
        return False


def _budget_ms():
    try:
        return max(50, int(os.environ.get("MINDER_HOOK_BUDGET_MS",
                                          DEFAULT_HOOK_BUDGET_MS)))
    except (TypeError, ValueError):
        return DEFAULT_HOOK_BUDGET_MS


def _policy_wanted(ev, out):
    """Whether the memory policy pass can possibly change anything.

    Its four effects are all failure-oriented, so a clean tool call with
    no Warden directive has nothing to consult a model about. Keeping this
    gate matters: the pass costs ~1 s of inference even when warm, on
    every single tool call."""
    try:
        if isinstance(out, dict) and (out.get("digest")
                                      or out.get("action")):
            return True
        resp = ev.get("tool_response", "")
        text = resp if isinstance(resp, str) else json.dumps(resp,
                                                             default=str)
        if minder.is_failure(text):
            return True
        if (ev.get("hook_event_name") or "") != "PostToolUse":
            return True
        return False
    except Exception:
        return True  # never widen the fast path at the cost of a guard


_PHASES = []


def _phase(name, started):
    try:
        _PHASES.append({"name": name,
                        "ms": round((time.perf_counter() - started) * 1000, 1)})
    except Exception:
        pass


def _log_timing(session, ev, t0, extra=None):
    """One `hook_timing` ledger record per invocation. This is the only
    place the per-tool-call cost is observable: the harness log records
    the total, this records where it went."""
    try:
        rec = {"hook_event": ev.get("hook_event_name"),
               "tool": ev.get("tool_name"),
               "total_ms": round((time.perf_counter() - t0) * 1000, 1),
               "budget_ms": _budget_ms(),
               "phases": _PHASES,
               "sink": _sink_enabled()}
        if extra:
            rec.update(extra)
        minder.log(session, "hook_timing", **rec)
    except Exception:
        pass


def _load_minder_env():
    """Load operator flags from ~/.config/minder/minder.env (KEY=VALUE) into
    os.environ at hook startup, so the decision loop and success-loop guard
    can be switched on persistently (mirrors the frontier.env key store).
    Fail-open (Law #6): never raises, never blocks the hot path; a missing
    or malformed file is simply ignored. Real environment variables always
    win — this only fills in what is not already set."""
    try:
        path = os.path.expanduser(
            os.environ.get("MINDER_ENV",
                           os.path.join("~/.config/minder", "minder.env")))
        if not os.path.isfile(path):
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                os.environ.setdefault(key, value.strip())
    except Exception:
        pass


_load_minder_env()


def trace_invocation(ev):
    """MINDER_HOOK_TRACE=1 → one line per hook invocation; makes the
    detection tier observable during bring-up without waiting for an
    escalation (which is the only other thing that ever logs)."""
    if os.environ.get("MINDER_HOOK_TRACE") != "1":
        return
    rec = {"ts": time.time(),
           "hook_event_name": ev.get("hook_event_name"),
           "tool_name": ev.get("tool_name"),
           "session": str(ev.get("session_id", ""))[:40]}
    try:
        if _sink_enabled() and memory_sink.append_jsonl("hook-trace.jsonl",
                                                        rec):
            return
        minder.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(minder.STATE_DIR / "hook-trace.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def capture_probe(session):
    """SessionStart: record whether the watchdog can actually persist.

    A confined hook cannot write STATE_DIR (DSH's file sandbox binds it
    read-only) and every write is fail-open, so a broken capture path is
    otherwise invisible: the hook still exits 0 and nothing is recorded.
    This probe turns that into an explicit, queryable row."""
    verdict = {"state_dir": str(minder.STATE_DIR), "sink": _sink_enabled()}
    try:
        minder.STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe = minder.STATE_DIR / ".capture-probe"
        probe.write_text(str(time.time()))
        probe.unlink()
        verdict["state_writable"] = True
        verdict["direct_write_error"] = None
    except Exception as e:
        verdict["state_writable"] = False
        verdict["direct_write_error"] = f"{type(e).__name__}: {e}"[:200]
    if _sink_enabled():
        try:
            verdict["sink_reachable"] = memory_sink.append_jsonl(
                "hook-trace.jsonl",
                {"ts": time.time(), "hook_event_name": "CaptureProbe",
                 "tool_name": None, "session": str(session)[:40]})
        except Exception:
            verdict["sink_reachable"] = False
    else:
        verdict["sink_reachable"] = None
    minder.log(session, "capture_probe", **verdict)
    return verdict


def frontier_cmd():
    """Command resolution: MINDER_FRONTIER_CMD env overrides; else the
    `frontier_command` key in minder.json (what install.sh wires)."""
    return (os.environ.get("MINDER_FRONTIER_CMD")
            or minder.cfg().get("frontier_command"))


def run_frontier(payload):
    """Runs the frontier consult (if configured) and records the trail.
    Returns the L2 frontier section (possibly a degraded note)."""
    cmd = frontier_cmd()
    task = payload.get("task", "default")
    key = payload.get("key", "?")
    attempts = payload.get("attempts", 0)
    error = payload.get("error", "")
    if not cmd:
        minder.log(task, "frontier_unavailable")
        minder.log_consult(task, key, attempts, error,
                           "(no frontier command configured)")
        return ("FRONTIER RESPONSE: (no frontier command configured — proceed "
                "with your own root-cause analysis; state a hypothesis first.)")
    try:
        r = subprocess.run(cmd, shell=True,
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=FRONTIER_TIMEOUT)
        answer = (r.stdout or "").strip()[:4000]
        if not answer:
            answer = (f"(command returned nothing; stderr: "
                      f"{r.stderr.strip()[:200]})")
        minder.log_consult(task, key, attempts, error, answer)
        return "FRONTIER RESPONSE:\n" + answer
    except Exception as e:
        minder.log_consult(task, key, attempts, error, f"(failed: {e})")
        return f"FRONTIER CALL FAILED: {e} (proceed with your own diagnosis.)"


def run_verify(payload):
    """Verify consult: frontier checks the resolution of an escalated key.
    Audit-only (consults.jsonl + verify_consult event) — never blocks,
    never feeds back into the digest of a successful turn."""
    cmd = frontier_cmd()
    task = payload.get("task", "default")
    key = payload.get("key", "?")
    if not cmd:
        minder.log_consult(task, key, payload.get("attempts", 0),
                           payload.get("error", ""),
                           "VERIFY: (no frontier command configured)")
        return
    try:
        r = subprocess.run(cmd, shell=True,
                           input=json.dumps(payload), capture_output=True,
                           text=True, timeout=FRONTIER_TIMEOUT)
        answer = (r.stdout or "").strip()[:4000] or \
            f"(no output; stderr: {(r.stderr or '').strip()[:200]})"
    except Exception as e:
        answer = f"(verify failed: {e})"
    minder.log_consult(task, key, payload.get("attempts", 0),
                       payload.get("error", ""), "VERIFY: " + answer)
    minder.log(task, "verify_consult", key=key,
               verdict=("ADDRESSED" if "ADDRESSED" in answer
                        and "NOT_ADDRESSED" not in answer else
                        ("NOT_ADDRESSED" if "NOT_ADDRESSED" in answer
                         else "unclear")))


def emit(digest, transport):
    if not digest:
        if transport != "dsh":
            sys.stdout.write(json.dumps({}))
        return 0
    if transport == "dsh":
        sys.stderr.write(digest + "\n")
        sys.stderr.flush()
        return 2
    sys.stdout.write(json.dumps({"decision": "block", "reason": digest}))
    return 0


def emit_context(event_name, text, transport):
    """Attach model-visible context WITHOUT blocking the action.

    On dsh the bridge reads `hookSpecificOutput.additionalContext` from
    exit-0 stdout and injects it into the next model request. stderr on a
    successful exit is NOT model-visible — the bridge only keeps it as a
    bounded, log-only `stderrSummary` — so an advisory written there is
    silently dropped (which is exactly what happened to the success-loop
    advisory before this). zcode has no additionalContext contract, so it
    keeps the stderr channel it has always used.
    """
    if not text:
        return 0
    if transport != "dsh":
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
        return 0
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name, "additionalContext": text}}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["zcode", "dsh"], default="zcode")
    ap.add_argument("--session-start", action="store_true")
    ap.add_argument("--pre-tool", action="store_true")
    args = ap.parse_args(argv)

    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}
    if not isinstance(ev, dict):
        ev = {}

    trace_invocation(ev)

    t0 = time.perf_counter()
    session = ev.get("session_id") or "default"
    if args.session_start or ev.get("hook_event_name") == "SessionStart":
        started = time.perf_counter()
        try:
            minder.snapshot_caps(session)
        except Exception:
            pass
        _phase("snapshot_caps", started)
        started = time.perf_counter()
        try:
            capture_probe(session)
        except Exception:
            pass
        _phase("capture_probe", started)
        out = {}
        if str(ev.get("source", "")) == "compact":
            # compaction survival: failure memory + the escalation marker
            # re-enter context (both bridges honor additionalContext on
            # SessionStart); ledger `compact_brief` makes it observable
            try:
                brief = minder.compact_brief(session)
                if brief:
                    out = {"hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": brief}}
                    minder.log(session, "compact_brief")
            except Exception:
                pass
        _log_timing(session, ev, t0)
        if out or args.transport != "dsh":
            sys.stdout.write(json.dumps(out))
        return 0

    # PreToolUse (block mode only): the success-loop ledger already knows
    # which actions have looped in this session, so stop the repeat before
    # it runs rather than advising after the fact for the 79th time.
    #
    # Deliberately BEFORE from_hook.record(): a pre-tool payload carries no
    # result, so recording it would insert a bogus zero-output observation
    # into success_observations and corrupt the very counts used here.
    if args.pre_tool or ev.get("hook_event_name") == "PreToolUse":
        directive = None
        if memory_from_hook is not None:
            try:
                directive = memory_from_hook.blocked_action_directive(ev)
            except Exception:
                directive = None
        if directive:
            try:
                minder.log(session, "loop_block", point="PreToolUse")
            except Exception:
                pass
            _log_timing(session, ev, t0, extra={"loop": "block-pre"})
            return emit(directive, args.transport)
        _log_timing(session, ev, t0)
        return emit(None, args.transport)

    # Memory v1 (PR 5): record the observation BEFORE the Warden runs, so
    # the duplicate guard reads fresh counts. Fail-open: directive never
    # depends on this succeeding. In a confined hook the DB is read-only,
    # so the record is delegated to the sink when one is configured.
    started = time.perf_counter()
    if memory_from_hook is not None and not args.session_start:
        recorded = None
        if _sink_enabled():
            try:
                recorded = memory_sink.record(ev)
            except Exception:
                recorded = None
        if recorded is None:
            try:
                memory_from_hook.record(ev)
            except Exception:
                pass
    _phase("record", started)

    started = time.perf_counter()
    try:
        out = minder.process(ev)
    except Exception:
        _log_timing(session, ev, t0)
        return 0
    _phase("warden", started)

    # Verify consult: a frontier-escalated key just resolved — one cheap
    # out-of-band check that the fix addressed the root cause (v0.4).
    if isinstance(out, dict) and out.get("verify_payload"):
        started = time.perf_counter()
        try:
            run_verify(out["verify_payload"])
        except Exception:
            pass
        _phase("verify_consult", started)

    digest = out.get("digest")
    if digest:
        # reflex tier (advisory): one CPU micro-classification enriches the
        # digest with a root-cause hint; any failure → unchanged digest
        started = time.perf_counter()
        try:
            hint = reflex.digest_hint(
                str(ev.get("tool_response", ""))[:2000])
            if hint:
                digest += "\n" + hint
        except Exception:
            pass
        _phase("reflex", started)
    if out.get("action") == "frontier" and out.get("frontier_payload"):
        started = time.perf_counter()
        section = run_frontier(out["frontier_payload"])
        fp = out["frontier_payload"]
        digest = minder.digest_l2(fp.get("key", "?"), fp.get("attempts", 0),
                                  fp.get("error", ""), section)
        _phase("frontier", started)
    # Duplicate guard (memory v1 PR 3): a verbatim repeat after threshold
    # replaces the directive with a block that demands a new hypothesis.
    # Fail-open: any problem leaves the Warden digest untouched.
    #
    # The policy pass is where the laya model used to be built per tool
    # call (~4.5 s of a measured 6.1 s hook). With a sink configured it
    # runs there (warm, once per session) and the hook never builds a
    # model; if the sink is unreachable the pass is skipped rather than
    # paying the build cost — set MINDER_SINK_FALLBACK=local to restore
    # the old always-local behaviour.
    #
    # Every effect of this pass is failure-oriented (duplicate guard,
    # skill/shadow assist, classifier_shadow, decision_traces), so a
    # clean tool call — the overwhelming majority — skips it entirely
    # instead of paying for a model opinion nobody consults.
    started = time.perf_counter()
    policy_state = "skipped"
    elapsed_ms = (started - t0) * 1000
    if memory_policy is not None and elapsed_ms > _budget_ms():
        # The budget is a soft deadline: past it the pass is skipped and
        # the skip is recorded, never silently absorbed.
        policy_state = "budget-skipped"
    elif memory_policy is not None and _policy_wanted(ev, out):
        guard = None
        handled = False
        if _sink_enabled():
            try:
                res = memory_sink.policy(ev, out)
            except Exception:
                res = None
            if res is not None:
                guard = res.get("guard")
                handled = True
                policy_state = "sink"
            elif (os.environ.get("MINDER_SINK_FALLBACK") or "").lower() == "local":
                handled = False
                policy_state = "sink-miss"
            else:
                handled = True  # skip the expensive local model build
                policy_state = "sink-miss-skipped"
        if not handled:
            try:
                guard = memory_policy.evaluate(ev, out)
                policy_state = "local"
            except Exception:
                guard = None
                policy_state = "local-error"
        if guard:
            digest = guard["digest"]
    _phase("policy", started)
    # Success-loop guard (see memory/success_guard.py).
    #   advisory — the note rides PostToolUse additionalContext, the channel
    #              the bridge actually injects into the next request.
    #   block    — the structured directive becomes the block reason, so a
    #              repeat is stopped and the model is told what must change.
    # Flag-gated (MINDER_SUCCESS_GUARD, default off = byte-inert) and
    # fail-open: any problem leaves the Warden outcome untouched.
    loop_state = None
    advisory_context = None
    if memory_from_hook is not None:
        try:
            directive = memory_from_hook.success_directive()
            if directive:
                digest = directive
                loop_state = "block"
            else:
                advisory_context = memory_from_hook.success_advisory()
                if advisory_context:
                    loop_state = "advisory"
        except Exception:
            pass
    _log_timing(session, ev, t0, extra={"action": out.get("action"),
                                        "level": out.get("level"),
                                        "policy": policy_state,
                                        "loop": loop_state})
    if advisory_context:
        return emit_context("PostToolUse", advisory_context, args.transport)
    return emit(digest, args.transport)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
