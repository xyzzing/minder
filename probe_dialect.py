#!/usr/bin/env python3
"""probe_dialect — empirical tool-call cleanliness prober (Law #9 companion).

Replicates the shape of real harness traffic (many tools, long system prompt,
preset samplers) against an OpenAI-compatible server and classifies each
response:

  clean     finish_reason stop/tool_calls, no markup bleed, args valid JSON
  runaway   finish_reason length, empty content, markup glued into tool args
            (the "burn 8192 tokens into tool_calls[0].arguments" failure)
  dirty     tool_calls present but args carry foreign markup or invalid JSON
  thinking  reasoning_content non-empty (informational, not a failure)

Usage:
  python3 probe_dialect.py [--base-url http://127.0.0.1:8080] [--tools 46]
      [--sys-chars 16000] [--temp 0.15] [--max-tokens 256] [--repeats 3]
      [--modes off,on,none] [--json]

Exit code 0 iff every cell is clean (runaway/dirty anywhere ⇒ 1).
Stdlib only; safe to run against a live idle server.
"""
import argparse
import json
import sys
import time
import urllib.request

# Real dsh web tool names (the harness sends these 46) — names are the salient
# prior for tool-dialect drift; schemas are synthetic stand-ins.
DSH_TOOLS = [
    "agent_teams_add_member", "agent_teams_approve", "agent_teams_claim_task",
    "agent_teams_create", "agent_teams_create_task", "agent_teams_delete",
    "agent_teams_edit_plan", "agent_teams_reassign_task",
    "agent_teams_remove_member", "agent_teams_resume",
    "agent_teams_send_message", "agent_teams_status", "agent_teams_update_task",
    "ask_user_question", "backup_dsh", "bash", "create_goal", "donsetch_status",
    "edit", "exit_plan_mode", "get_goal", "glob", "grep", "housekeeper_clean",
    "housekeeper_plan", "housekeeper_report", "interrupt_agent", "job_kill",
    "job_list", "job_output", "list_agents", "list_subagent_models", "present",
    "ralph", "read", "read_image", "send_message", "skill", "subagent",
    "subagent_fork", "todo_write", "update_goal", "web_fetch", "web_search",
    "workflow", "write",
]

MARKUP = ("</tool_call>", "<tool_call>", "<function=", "</function>",
          "</parameter>", "<|im_start|>", "<|im_end|>")


def build_tools(n):
    names = (DSH_TOOLS * 4)[:n]
    tools = []
    for i, name in enumerate(names):
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"Tool {i}: {name} operation.",
                "parameters": {"type": "object",
                               "properties": {"path": {"type": "string"},
                                              "query": {"type": "string"}}},
            },
        })
    return tools


def build_system(chars):
    para = ("You are a coding agent operating inside a harness. Use tools to "
            "accomplish tasks; prefer bash for filesystem inspection, read for "
            "files, edit for patches. Be concise. Verify changes before "
            "reporting success. The workspace root is the current directory. "
            "Follow the repository conventions and keep commits atomic. ")
    text = para * (chars // len(para) + 1)
    return text[:chars]


def classify(resp, max_tokens):
    """(verdict, detail) for one non-streaming chat response."""
    try:
        msg = resp["choices"][0]["message"]
        finish = resp["choices"][0].get("finish_reason") or "?"
    except (KeyError, IndexError, TypeError):
        return "error", f"unparseable response shape: {str(resp)[:120]}"
    content = msg.get("content") or ""
    calls = msg.get("tool_calls") or []

    args_blob = ""
    for c in calls:
        fn = c.get("function") or {}
        args_blob += str(fn.get("arguments") or "")
    markup_hit = [m for m in MARKUP if m in args_blob or m in content]

    if markup_hit:
        if finish == "length" and not content.strip() and calls:
            detail = (f"runaway: finish={finish} markup={markup_hit[:2]} "
                      f"args_len={len(args_blob)}")
            return "runaway", detail
        return "dirty", f"markup {markup_hit[:2]} in {'args' if args_blob else 'content'} (finish={finish})"

    if calls:
        for c in calls:
            fn = c.get("function") or {}
            raw = fn.get("arguments")
            if isinstance(raw, str):
                try:
                    v = json.loads(raw)
                    if not isinstance(v, dict):
                        return "dirty", f"args not an object: {raw[:60]}"
                except ValueError:
                    return "dirty", f"args not JSON: {raw[:60]}"
        return "clean", f"finish={finish} calls={len(calls)}"

    if content.strip():
        return "clean", f"finish={finish} text_only chars={len(content)}"
    if finish == "length":
        return "runaway", "finish=length, no content, no calls (burn)"
    return "clean", f"finish={finish} empty response (no tools offered case)"


def probe(base_url, body, timeout=180):
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    return resp, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--tools", type=int, default=46)
    ap.add_argument("--sys-chars", type=int, default=16000)
    ap.add_argument("--temp", type=float, default=0.15)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--modes", default="off,on,none",
                    help="chat_template_kwargs enable_thinking modes to test")
    ap.add_argument("--json", action="store_true", help="machine-readable out")
    args = ap.parse_args()

    tools = build_tools(args.tools)
    system = build_system(args.sys_chars)
    results = []
    for mode in args.modes.split(","):
        for i in range(args.repeats):
            body = {
                "model": "default",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",
                     "content": "List all files in the current workspace "
                                "using the bash tool."},
                ],
                "tools": tools,
                "max_tokens": args.max_tokens,
                "temperature": args.temp,
            }
            if mode in ("off", "on"):
                body["chat_template_kwargs"] = {
                    "enable_thinking": mode == "on"}
            try:
                resp, dt = probe(args.base_url, body)
                verdict, detail = classify(resp, args.max_tokens)
                usage = (resp.get("usage") or {}).get("completion_tokens")
            except Exception as e:
                verdict, detail, dt, usage = "error", repr(e)[:120], 0.0, None
            row = {"mode": mode, "i": i, "verdict": verdict,
                   "detail": detail, "secs": round(dt, 1),
                   "completion_tokens": usage}
            results.append(row)
            print(f"[{mode:4}] #{i} {verdict:8} {str(usage):>4}tok "
                  f"{dt:5.1f}s  {detail}", file=sys.stderr)

    bad = [r for r in results if r["verdict"] in ("runaway", "dirty", "error")]
    summary = {
        "base_url": args.base_url, "tools": args.tools,
        "sys_chars": args.sys_chars, "repeats": args.repeats,
        "verdicts": {v: sum(1 for r in results if r["verdict"] == v)
                     for v in sorted({r["verdict"] for r in results})},
        "all_clean": not bad,
    }
    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=1))
    else:
        print(json.dumps(summary))
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
