#!/usr/bin/env python3
"""minder hook — stdin JSON event → escalation directive. Stdlib only.

One script, two transports (prd.md §5.1/§5.2 + dsh hooks-bridge semantics):
  zcode (default): always exit 0; block directive as
      {"decision": "block", "reason": <digest>} on stdout.
  dsh:             Claude-Code hooks-bridge command hook — block = exit 2 with
      the digest on stderr (the bridge's verified model-visible channel);
      non-block = exit 0, silent.

Detection never blocks the hot path (Law #6): every failure mode exits 0 and
lets the tool result through untouched.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minder
import reflex
try:
    from memory import from_hook as memory_from_hook
    from memory import policy as memory_policy
except Exception:  # memory is optional; the watchdog never depends on it
    memory_from_hook = None
    memory_policy = None

FRONTIER_TIMEOUT = int(os.environ.get("MINDER_FRONTIER_TIMEOUT", "60"))


def trace_invocation(ev):
    """MINDER_HOOK_TRACE=1 → one line per hook invocation; makes the
    detection tier observable during bring-up without waiting for an
    escalation (which is the only other thing that ever logs)."""
    if os.environ.get("MINDER_HOOK_TRACE") != "1":
        return
    try:
        minder.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(minder.STATE_DIR / "hook-trace.jsonl", "a") as f:
            f.write(json.dumps({
                "ts": __import__("time").time(),
                "hook_event_name": ev.get("hook_event_name"),
                "tool_name": ev.get("tool_name"),
                "session": str(ev.get("session_id", ""))[:40],
            }) + "\n")
    except Exception:
        pass


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["zcode", "dsh"], default="zcode")
    ap.add_argument("--session-start", action="store_true")
    args = ap.parse_args(argv)

    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}
    if not isinstance(ev, dict):
        ev = {}

    trace_invocation(ev)

    session = ev.get("session_id") or "default"
    if args.session_start or ev.get("hook_event_name") == "SessionStart":
        try:
            minder.snapshot_caps(session)
        except Exception:
            pass
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
        if out or args.transport != "dsh":
            sys.stdout.write(json.dumps(out))
        return 0

    # Memory v1 (PR 5): record the observation BEFORE the Warden runs, so
    # the duplicate guard reads fresh counts. Fail-open: directive never
    # depends on this succeeding.
    if memory_from_hook is not None and not args.session_start:
        try:
            memory_from_hook.record(ev)
        except Exception:
            pass

    try:
        out = minder.process(ev)
    except Exception:
        return 0

    # Verify consult: a frontier-escalated key just resolved — one cheap
    # out-of-band check that the fix addressed the root cause (v0.4).
    if isinstance(out, dict) and out.get("verify_payload"):
        try:
            run_verify(out["verify_payload"])
        except Exception:
            pass

    digest = out.get("digest")
    if digest:
        # reflex tier (advisory): one CPU micro-classification enriches the
        # digest with a root-cause hint; any failure → unchanged digest
        try:
            hint = reflex.digest_hint(
                str(ev.get("tool_response", ""))[:2000])
            if hint:
                digest += "\n" + hint
        except Exception:
            pass
    if out.get("action") == "frontier" and out.get("frontier_payload"):
        section = run_frontier(out["frontier_payload"])
        fp = out["frontier_payload"]
        digest = minder.digest_l2(fp.get("key", "?"), fp.get("attempts", 0),
                                  fp.get("error", ""), section)
    # Duplicate guard (memory v1 PR 3): a verbatim repeat after threshold
    # replaces the directive with a block that demands a new hypothesis.
    # Fail-open: any problem leaves the Warden digest untouched.
    if memory_policy is not None:
        try:
            guard = memory_policy.evaluate(ev, out)
            if guard:
                digest = guard["digest"]
        except Exception:
            pass
    return emit(digest, args.transport)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
