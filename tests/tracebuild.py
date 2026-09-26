"""Build synthetic DSH session logs for trace tests.

Plain functions, no fixtures (the `webseed`/`dshseed` pattern). A fixture
session is written in the same place and shape DSH uses —
`<root>/sessions/<project>/<session>/session.v3.jsonl.zstd` — so tests
exercise the real reader (`minder_op.dsh_sessions._decompressor`), not a
stand-in for it.

Compression prefers the Python 3.14 stdlib, falls back to the `zstd`
binary, and returns None when neither exists so the caller can skip.
"""
import json
import shutil
import subprocess

PROJECT = "--repo--"


def compressor():
    """A callable(bytes) -> bytes, or None."""
    try:
        from compression import zstd

        return zstd.compress
    except Exception:
        pass
    if shutil.which("zstd"):
        def _via_cli(blob):
            proc = subprocess.run(["zstd", "-q", "-c"], input=blob,
                                  capture_output=True, timeout=60)
            if proc.returncode != 0:
                raise RuntimeError("zstd failed")
            return proc.stdout
        return _via_cli
    return None


def write_log(path, records):
    """Write `records` as a zstd JSONL session log. None if unavailable."""
    compress = compressor()
    if compress is None:
        return None
    blob = b"".join(
        (json.dumps(r, default=str) + "\n").encode() for r in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compress(blob))
    return path


def build_session(root, session_id, records, *, project=PROJECT,
                  version="v3"):
    """Create one session dir under `root` and return its log path."""
    path = (root / "sessions" / project / session_id /
            f"session.{version}.jsonl.zstd")
    return write_log(path, records)


# --- record builders (the raw DSH vocabulary) ----------------------------

def call(*, seq, call_id, name, arguments=None, turn=1, step=1):
    """A `tool/call`. `arguments` is stored as a JSON *string* exactly as
    DSH does — passing a dict is a convenience, passing a str is the
    malformed-payload case."""
    if isinstance(arguments, (dict, list)):
        arguments = json.dumps(arguments)
    return {"type": "tool/call", "seq": seq, "time": seq * 1000,
            "data": {"turn": turn, "step": step, "callId": call_id,
                     "name": name, "arguments": arguments}}


def result(*, seq, call_id, text, turn=1, step=1, is_error=False):
    """A `tool/result`, whose text is nested three levels deep."""
    return {"type": "tool/result", "seq": seq, "time": seq * 1000,
            "data": {
                "turn": turn, "step": step,
                "message": {
                    "source": {"kind": "tool", "callId": call_id},
                    "content": [{
                        "type": "tool-result", "toolCallId": call_id,
                        "isError": is_error,
                        "content": [{"type": "text", "text": text}],
                    }],
                },
            }}


def hook(seq, *, point="PostToolUse", decision="pass", exit_code=0):
    return {"type": "hook/result", "seq": seq, "time": seq * 1000,
            "data": {"turn": 1, "point": point, "handlerId": f"h{seq}",
                     "decision": decision, "exitCode": exit_code,
                     "durationMs": 1.0}}


def bash(seq, command, text, *, exit_code=None, call_id=None):
    """A paired bash call+result — the shape most evaluator tests want."""
    call_id = call_id or f"c{seq}"
    body = text if exit_code is None else f"{text}\n[exit code: {exit_code}]"
    return [call(seq=seq, call_id=call_id, name="bash",
                 arguments={"command": command}),
            result(seq=seq + 1, call_id=call_id, text=body,
                   is_error=bool(exit_code))]
