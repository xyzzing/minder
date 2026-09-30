#!/usr/bin/env python3
"""Multi-turn conversation cache benchmark (issue #3, PRD acceptance).

Runs the same growing conversation against an OpenAI-compatible engine
under three request patterns and reports per-turn prefill cost:

  stable    append-only history, constant chat_template_kwargs — the
            pattern a prefix-caching engine rewards
  flip      alternating enable_thinking per turn — what a proxy that
            mutates thinking kwargs per request does to the cache
  shuffle   one history message reordered each turn — byte-prefix broken

Usage:
  python3 benchmarks/multiturn_cache.py --base-url http://127.0.0.1:8081 \
      --turns 10 --tail-chars 1200 --json-out strata-multiturn.json

Metrics per turn: wall seconds and usage.cached_tokens / prompt_tokens
as reported by the engine. The engine must expose prompt_tokens_details
cached_tokens (strata does; llama.cpp does with --cache-reuse).

Stdlib only. Read-only against the engine: it sends chat completions and
touches nothing else.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

SYSTEM_PROMPT = ("You are a code reviewer. Answer tersely. "
                 "Do not restate the question.")

# Turn bodies are filler prose sized to make prefill measurable; the
# engine only sees tokens, semantics are irrelevant to cache behavior.
_TURN_TEMPLATE = ("Turn {i}: consider module {m}. The cache line holds a "
                  "prefetcher block of {n} entries, the eviction pass runs "
                  "every {k} cycles, and the summary table recomputes its "
                  "worst-case path when the resident set changes. Explain "
                  "in one sentence what invariant the test must pin.")


def build_turns(n, tail_chars):
    turns = []
    for i in range(n):
        body = _TURN_TEMPLATE.format(i=i, m=f"mod_{i % 7}", n=64 * (i + 1),
                                     k=1000 + i)
        while len(body) < tail_chars:
            body += (" Add one sentence about how the failure would surface "
                     "as a stale index rather than a crash.")
        turns.append(body[:tail_chars])
    return turns


def shuffle_msgs(msgs):
    """Swap the first two completed exchanges in the outgoing request:
    everything after the swap point stops being an exact prefix."""
    if len(msgs) >= 5:
        msgs[1], msgs[2], msgs[3], msgs[4] = \
            msgs[3], msgs[4], msgs[1], msgs[2]
    return msgs


SCENARIOS = ["stable", "flip", "shuffle"]


def _metrics_totals(base_url):
    """(prompt_tokens, reused) lifetime counters, or None when the engine
    does not expose them."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/metrics",
                                    timeout=10) as r:
            totals = (json.loads(r.read()) or {}).get("totals") or {}
    except (urllib.error.URLError, ValueError):
        return None
    p, c = totals.get("prompt_tokens"), totals.get("reused")
    if isinstance(p, int) and isinstance(c, int):
        return (p, c)
    return None


def chat(base_url, messages, thinking, max_tokens=32, timeout=600):
    body = {"model": "bench", "messages": messages, "temperature": 0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    totals_before = _metrics_totals(base_url)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"engine returned {e.code}: {e.read()[:300]}")
    wall = time.monotonic() - t0
    usage = resp.get("usage") or {}
    detail = (usage.get("prompt_tokens_details") or {}) \
        if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = detail.get("cached_tokens")
    if cached is None and totals_before is not None:
        totals_after = _metrics_totals(base_url)
        if totals_after is not None:
            # counter delta: race-free where a requests[-1] read is not
            cached = totals_after[1] - totals_before[1]
    reply = ""
    try:
        reply = str(resp["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        pass
    return {"wall_s": round(wall, 3),
            "prompt_tokens": usage.get("prompt_tokens"),
            "cached_tokens": cached,
            "completion_tokens": usage.get("completion_tokens"),
            "reply": reply}


def run(base_url, turns_n, tail_chars, per_turn_fn=None):
    """Stateful replay: each scenario keeps its own history and feeds the
    engine's actual replies back, so the stored conversation state matches
    what a real harness session produces."""
    turns = build_turns(turns_n, tail_chars)
    out = {}
    for name in SCENARIOS:
        # scenario-specific system section: without it the scenarios share
        # their whole prefix and later ones inherit earlier checkpoints
        history = [{"role": "system", "content": SYSTEM_PROMPT + " "
                    + f"{name} scenario. " * 20 + "Baseline section. " * 40}]
        rows = []
        for upto in range(turns_n):
            thinking = (upto % 2 == 1) if name == "flip" else False
            msgs = history + [{"role": "user", "content": turns[upto]}]
            if name == "shuffle" and upto >= 4:
                msgs = shuffle_msgs(msgs)
            row = chat(base_url, msgs, thinking)
            row["turn"] = upto + 1
            rows.append(row)
            history.append({"role": "user", "content": turns[upto]})
            history.append({"role": "assistant",
                            "content": row["reply"] or "ok."})
            if per_turn_fn:
                per_turn_fn(name, row)
        total_prompt = sum(r["prompt_tokens"] or 0 for r in rows)
        total_cached = sum(r["cached_tokens"] or 0 for r in rows[1:])
        out[name] = {
            "turns": rows,
            "total_prompt_tokens": total_prompt,
            "cached_tokens_after_first_turn": total_cached,
            "reuse_ratio": round(total_cached / total_prompt, 3)
            if total_prompt else None,
            "sum_wall_s": round(sum(r["wall_s"] for r in rows), 3),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--tail-chars", type=int, default=1200)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    result = run(args.base_url, args.turns, args.tail_chars)
    result["_meta"] = {"base_url": args.base_url, "turns": args.turns,
                       "tail_chars": args.tail_chars,
                       "generated_at": time.strftime(
                           "%Y-%m-%dT%H:%M:%S%z")}
    for name in SCENARIOS:
        s = result[name]
        print(f"{name:>8}: reuse {s['reuse_ratio']}  "
              f"sum wall {s['sum_wall_s']}s")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
