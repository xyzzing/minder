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

# The panel's text is pure judgement, so it lives in minder_core next to the
# other evidence rules: panel_text.py owns the asks and the redaction of what
# leaves the machine, panel_answer.py owns what in an answer is an action and
# what is only a cause. No I/O, no minder imports, and under the file-size law
# this file is close to (C2).
from minder_core import panel_answer as _answer  # noqa: E402
from minder_core import panel_text as _panel  # noqa: E402

MAX_ANSWER_CHARS = 4000
NOTE_CHARS = _panel.NOTE_CHARS  # per-consultant appendix length

# `warden-l2` names the warden rung that asks for a consult (issue #10);
# minder_memory/frontier_traces.py is what records the value.
CONSULT_TRIGGER = "warden-l2"

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


# The panel's text lives in minder_core/panel_text.py; these are the runner's
# names for it, kept because the runner is what tests and the docs name. The
# redaction patterns come from config, so scrub_payload keeps the cfg-shaped
# signature the caller already has.
redact = _panel.redact
build_prompt = _panel.consult_prompt
build_synthesis_prompt = _panel.synthesis_prompt


def scrub_payload(payload, cfg):
    return _panel.scrub_payload(payload, cfg.get("egress_redaction"))


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


def distill_actions(answer):
    """The actionable lines of a panel answer, deterministically (issue
    #10). The reading rules are `minder_core.panel_answer`'s: the action the
    answer itself named is kept, the causes the ask also asks for are read but
    never distilled, and the list is capped. A consult answer is untrusted
    input, so a malformed one costs the record, never the panel."""
    try:
        return _answer.action_lines(answer)
    except Exception:
        return []


def _profile_for_record(cfg):
    """The profile a consult trace is stored under. `egress_redaction` is
    the domain-profile config key (see egress_precheck); the governed
    storage profile is the one minder_memory knows."""
    try:
        from minder_memory import frontier_redaction as fr
        configured = str(cfg.get("redaction_profile") or "")
        if configured in fr.PROFILES:
            return configured
        if cfg.get("egress_redaction"):
            return fr.EXTERNAL_PROHIBITED
        return fr.INTERNAL_CODE_DEFAULT
    except Exception:
        return None


def trace_hook(cfg=None, db_path=None):
    """The governed trace record used as run_panel's on_trace (issue #10).
    Replaces the legacy un-governed `record`: a consult now stores its
    redaction profile, trigger and distilled action list, which is what
    classify_consult and distill_lesson_from_consult need to exist at all.
    Tracing must never alter the answer, so everything is swallowed."""
    def on_trace(meta):
        try:
            from minder_memory import frontier_traces
            frontier_traces.record_consult({
                "failure_key": meta.get("failure_key"),
                "episode_id": meta.get("episode_id"),
                "local_attempts": meta.get("local_attempts"),
                "redaction_profile": (meta.get("redaction_profile")
                                      or _profile_for_record(cfg or {})),
                "providers": meta.get("providers"),
                "prompt": meta.get("request_hash_source"),
                "response": meta.get("answer"),
                "distilled": meta.get("distilled_actions"),
                "trigger": meta.get("trigger") or CONSULT_TRIGGER,
            }, db_path=db_path)
        except Exception:
            pass
    return on_trace


def egress_precheck(payload, cfg):
    """P6.2: deterministic local gate evaluated before any outbound
    consult. Tests call this directly; run_panel skips outbound on
    'deny'. Fail-open to 'allow' — the Warden owns whether we consult,
    this only vetoes what may leave the machine."""
    try:
        from minder_memory import egress as memory_egress
        event = {
            "key": payload.get("key"),
            "task_id": payload.get("task") or payload.get("episode_id"),
            "redaction_profile": cfg.get("redaction_profile"),
            "error_excerpt": str(payload.get("error") or ""),
            "untrusted_content_present":
                bool(payload.get("untrusted_content_present")),
        }
        return memory_egress.assess_egress(event)
    except Exception:
        return "allow"


def run_panel(payload, cfg, post=None, get=None, forced_key=None,
              on_trace=None):
    """Consult every configured provider with a resolvable key (in parallel),
    synthesize when two or more answer, return the panel text (≤4000 chars).
    Error strings start with '(' so callers can detect total failure.
    on_trace (optional): called once with a dict of consult metadata when a
    panel completes — tracing must never alter the answer."""
    providers = resolve_providers(cfg)
    payload = scrub_payload(payload, cfg)
    if egress_precheck(payload, cfg) == "deny":
        return "(egress denied by local policy — consult not sent)"
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
                "redaction_profile": _profile_for_record(cfg),
                "providers": [p for p, _ in ok],
                "request_hash_source": prompt,
                "answer": answer,
                "distilled_actions": distill_actions(answer),
                "trigger": CONSULT_TRIGGER,
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
    answer = run_panel(payload, cfg, on_trace=trace_hook(cfg))
    if answer.startswith(_answer.FAILURE_PREFIX):
        sys.stderr.write(answer + "\n")
        return 4
    sys.stdout.write(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
