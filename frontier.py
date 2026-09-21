#!/usr/bin/env python3
"""minder frontier — L2 consult runner (the out-of-band frontier channel).

Reads the escalation payload JSON on stdin ({task, key, attempts, error}) and
consults the configured frontier provider(s). With one provider it returns
that answer; with several (the panel) each is consulted in parallel, then the
first answering provider synthesizes both into one recommendation with
disagreements flagged — the cross-check. Providers whose API key is missing
are skipped honestly; if none answer, exit non-zero and the caller degrades
to the guided digest.

Config (minder.json):
  frontier_command      — install.sh wires this runner
  frontier_providers    — [{name, base_url, model, key_env, timeout?}, …]
                          (model empty ⇒ auto-picked from GET /v1/models)
  legacy single keys    — frontier_base_url / frontier_model / frontier_key_env
                          / frontier_timeout still work when no list is set

Keys resolve per provider from its key_env environment variable, falling
back to KEY=VALUE lines in ~/.config/minder/frontier.env (chmod 600) — the
hook-process-friendly key store. Stdlib only; HTTP injectable for tests.
"""
import json
import os
import pathlib
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minder  # noqa: E402  (config merge + STATE conventions)

MAX_ANSWER_CHARS = 4000
NOTE_CHARS = 350  # per-consultant appendix length in panel output

DEFAULTS = {
    "frontier_base_url": "https://api.deepseek.com",
    "frontier_model": "deepseek-chat",
    "frontier_key_env": "DEEPSEEK_API_KEY",
    "frontier_timeout": 60,
}

# The shipped panel: DeepSeek primary, OpenAI as the independent second
# opinion. The OpenAI entry sits inert until OPENAI_API_KEY appears in the
# key store — no config edit needed to enable the cross-check.
DEFAULT_PROVIDERS = [
    {"name": "deepseek", "base_url": "https://api.deepseek.com",
     "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    {"name": "openai", "base_url": "https://api.openai.com",
     "model": "", "key_env": "OPENAI_API_KEY"},
]

# Preferred model prefixes when a provider's model is left unset.
_MODEL_PREF = re.compile(r"gpt-5|gpt-4\.1|gpt-4o|gpt-4|o\d", re.I)


def _key_env_file():
    """Key-store path, resolved per call (test/isolation-friendly)."""
    return pathlib.Path(
        os.environ.get("MINDER_FRONTIER_ENV",
                       os.path.expanduser("~/.config/minder/frontier.env")))


def load_key_by_name(name):
    """API key from the named env var, falling back to frontier.env."""
    key = os.environ.get(name)
    if not key:
        try:
            for line in _key_env_file().read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() == name:
                        key = v.strip()
                        break
        except OSError:
            pass
    # tolerate quoted values (users paste `"sk-…"`); a quote inside the key
    # is always a paste artifact and always fails auth downstream
    if key and len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1]
    return key


def load_key(cfg):
    """Legacy single-provider key lookup (kept for compatibility)."""
    return load_key_by_name(
        cfg.get("frontier_key_env", DEFAULTS["frontier_key_env"]))


def resolve_providers(cfg):
    """Provider list from config; legacy single keys when no list is set."""
    provs = cfg.get("frontier_providers")
    if isinstance(provs, list) and provs:
        out = []
        for p in provs:
            if not isinstance(p, dict) or not (p.get("base_url")
                                               or p.get("name")):
                continue
            out.append({
                "name": p.get("name") or p.get("base_url"),
                "base_url": p.get("base_url", ""),
                "model": p.get("model") or "",
                "key_env": p.get("key_env", "DEEPSEEK_API_KEY"),
                "timeout": p.get("timeout"),
            })
        return out
    return [{"name": "frontier",
             "base_url": cfg.get("frontier_base_url",
                                 DEFAULTS["frontier_base_url"]),
             "model": cfg.get("frontier_model", DEFAULTS["frontier_model"]),
             "key_env": cfg.get("frontier_key_env",
                                DEFAULTS["frontier_key_env"]),
             "timeout": cfg.get("frontier_timeout")}]


def redact(text, patterns):
    """Apply profile egress-redaction regexes (legal/privacy domains) to a
    payload field before it leaves the machine."""
    for p in patterns or []:
        try:
            text = re.sub(p, "[REDACTED]", text)
        except re.error:
            continue
    return text


def scrub_payload(payload, cfg):
    patterns = cfg.get("egress_redaction")
    if not patterns:
        return payload
    out = dict(payload)
    for k in ("key", "error", "resolution", "task"):
        if isinstance(out.get(k), str):
            out[k] = redact(out[k], patterns)
    return out


def build_prompt(payload, template=None):
    """Pure: escalation payload → frontier prompt text. `kind: verify` gets
    the verification prompt; a profile template (with {key}/{attempts}/{error}
    slots) overrides the default consult prompt."""
    if payload.get("kind") == "verify":
        return (
            "You are verifying a resolved escalation for a coding agent. The "
            "action below failed repeatedly, was escalated for a frontier "
            "consult, and then succeeded. Judge only: does the resolution "
            "plausibly address the root cause of the failure?\n\n"
            f"Failed action: {payload.get('key', '?')} "
            f"(failed {payload.get('attempts', '?')}x)\n"
            f"Failure (truncated): {str(payload.get('error', ''))[:800]}\n"
            f"Resolution (the tool output that succeeded, truncated): "
            f"{str(payload.get('resolution', ''))[:800]}\n\n"
            "Reply exactly 'VERDICT: ADDRESSED' or 'VERDICT: NOT_ADDRESSED', "
            "then one line why. Max 60 words."
        )
    if template:
        try:
            return template.format(key=payload.get("key", "?"),
                                   attempts=payload.get("attempts", "?"),
                                   error=str(payload.get("error", ""))[:1500])
        except (KeyError, IndexError, ValueError):
            pass  # malformed template → honest default prompt
    return (
        "You are the frontier consultant for a coding agent stuck in a "
        "failure loop. A deterministic watchdog escalated after repeated "
        "failures of the same action.\n\n"
        f"Failed action: {payload.get('key', '?')}\n"
        f"Attempt count: {payload.get('attempts', '?')}\n"
        f"Last error (truncated): {str(payload.get('error', ''))[:1500]}\n\n"
        "Reply with: (1) the 2-3 most likely root causes, ranked, one line "
        "each; (2) THE single next concrete action most likely to break the "
        "loop (a command, a file to read, or a check to run). Terse. No "
        "pleasantries, no restating the problem. Max ~150 words."
    )


def build_synthesis_prompt(payload, answers):
    """Pure: original question + consultant answers → merge prompt."""
    parts = [f"Consultant {name}:\n{ans}" for name, ans in answers]
    return (
        "Two independent consultants answered a stuck coding agent's "
        "escalation. Reconcile them into ONE recommendation: the 2-3 most "
        "likely root causes, ranked, one line each, and THE single next "
        "concrete action. Where the consultants disagree, prefer the one "
        "that better fits the error text and keep a final line exactly "
        "'DISAGREEMENT: <one sentence>'. Terse, max ~150 words.\n\n"
        f"Failed action: {payload.get('key', '?')} "
        f"(attempt {payload.get('attempts', '?')})\n"
        f"Last error (truncated): {str(payload.get('error', ''))[:800]}\n\n"
        + "\n\n".join(parts)
    )


def build_request(prompt, provider, model, api_key):
    """Pure: → (url, body_dict, headers) for an OpenAI-compatible chat call."""
    url = provider["base_url"].rstrip("/") + "/v1/chat/completions"
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            # reasoning models spend tokens on deliberation before the
            # answer — 512 truncates mid-thought (measured on deepseek-v4-pro)
            "max_tokens": 2048,
            "temperature": 0,
            "stream": False}
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    return url, body, headers


def default_post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def default_get(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def pick_model(provider, api_key, get=None):
    """Model auto-pick from GET /v1/models (Law #9: never guess a name the
    server didn't list). Preference: modern gpt/o-series prefixes, else the
    first id. None when the listing fails."""
    get = get or default_get
    try:
        st, data = get(provider["base_url"].rstrip("/") + "/v1/models",
                       {"Authorization": f"Bearer {api_key}"}, 30)
        ids = [m.get("id") for m in (data.get("data") or [])
               if isinstance(m, dict) and m.get("id")]
        for i in ids:
            if _MODEL_PREF.search(i):
                return i
        return ids[0] if ids else None
    except Exception:
        return None


def ask(prompt, provider, api_key, post=None, get=None):
    """One chat call → answer text (never raises; errors start with '(')."""
    post = post or default_post
    timeout = int(provider.get("timeout") or DEFAULTS["frontier_timeout"])
    model = provider.get("model")
    if not model:
        model = pick_model(provider, api_key, get) or "gpt-4o-mini"
    url, body, headers = build_request(prompt, provider, model, api_key)
    try:
        status, resp = post(url, body, headers, timeout)
    except Exception as e:
        return f"(call failed: {e!r})"
    if status != 200 or not isinstance(resp, dict):
        return f"(status {status}: {str(resp)[:200]})"
    try:
        msg = resp["choices"][0]["message"]
        # reasoning models put the answer in reasoning_content with empty
        # content (same field-drift as CAP's A8) — accept either
        answer = (msg.get("content") or msg.get("reasoning_content")
                  or "").strip()
    except (KeyError, IndexError, TypeError):
        return f"(response missing content: {str(resp)[:200]})"
    return answer or "(empty content)"


def run_panel(payload, cfg, post=None, get=None, forced_key=None,
              on_trace=None):
    """Consult every configured provider with a resolvable key (in parallel),
    synthesize when two or more answer, return the panel text (≤4000 chars).
    Error strings start with '(' so callers can detect total failure.
    on_trace (optional): called once with a dict of consult metadata when a
    panel completes — tracing must never alter the answer."""
    providers = resolve_providers(cfg)
    payload = scrub_payload(payload, cfg)
    template = cfg.get("frontier_prompt_template")
    prompt = build_prompt(payload, template)

    def work(p):
        key = (forced_key if (forced_key and len(providers) == 1)
               else load_key_by_name(p["key_env"]))
        if not key:
            return p, False, "(no API key)"
        return p, True, ask(prompt, p, key, post, get)

    with ThreadPoolExecutor(max_workers=4) as ex:
        outcomes = list(ex.map(work, providers))
    ok = [(p, ans) for p, good, ans in outcomes if good]
    if not ok:
        why = "; ".join(f"{p['name']}: {ans[:60]}" for p, _, ans in outcomes)
        return f"(no frontier provider answered — {why})"
    if len(ok) >= 2:
        lead = ok[0][0]
        lead_key = (forced_key if (forced_key and len(ok) == 1)
                    else load_key_by_name(lead["key_env"]))
        synth = ask(build_synthesis_prompt(payload,
                                           [(p["name"], a) for p, a in ok]),
                    lead, lead_key, post, get)
        header = "PANEL CONSULT: " + ", ".join(f"{p['name']} ✓" for p, _ in ok)
        notes = "\n---\n" + "\n".join(
            f"[{p['name']}] {a[:NOTE_CHARS]}" for p, a in ok)
        answer = (f"{header}\nSYNTHESIS (merged, disagreements flagged):\n"
                  f"{synth}{notes}")[:MAX_ANSWER_CHARS]
    else:
        answer = ok[0][1][:MAX_ANSWER_CHARS]
    if on_trace:
        try:
            on_trace({
                "failure_key": payload.get("key"),
                "episode_id": payload.get("episode_id"),
                "local_attempts": payload.get("attempts"),
                "redaction_profile": ("default" if cfg.get("egress_redaction")
                                      else None),
                "providers": [p for p, _ in ok],
                "request_hash_source": prompt,
                "answer": answer,
            })
        except Exception:
            pass
    return answer


def run(payload, cfg, api_key=None, post=None, get=None):
    """Compatibility entry: routes to the panel; `api_key` forces the key of
    a single (legacy-config) provider."""
    return run_panel(payload, cfg, post=post, get=get, forced_key=api_key)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cfg = {k: v for k, v in minder.cfg().items()
           if k.startswith("frontier_")}
    providers = resolve_providers(cfg)
    if not any(load_key_by_name(p["key_env"]) for p in providers):
        names = ", ".join(sorted({p["key_env"] for p in providers}))
        sys.stderr.write(
            f"minder frontier: no API key for any provider — add a "
            f"{names.replace(', ', '=… or ')}=… line in {_key_env_file()}\n")
        return 3
    def _trace(meta):
        """PR 7: hashed consult metadata → memory store (fail-open, additive)."""
        try:
            from memory import frontier_traces
            frontier_traces.record(
                {"key": meta.get("failure_key"),
                 "attempts": meta.get("local_attempts"),
                 "prompt": meta.get("request_hash_source"),
                 "episode_id": meta.get("episode_id")},
                meta.get("answer"), providers=meta.get("providers"),
                redaction_profile=meta.get("redaction_profile"),
                episode_id=meta.get("episode_id"))
        except Exception:
            pass

    answer = run_panel(payload, cfg, on_trace=_trace)
    if answer.startswith("("):
        sys.stderr.write(answer + "\n")
        return 4
    sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
