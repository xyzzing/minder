#!/usr/bin/env python3
"""dsh settings.yaml patcher — additive, idempotent, fail-closed, stdlib only.

Makes exactly two additive edits to ~/.dsh/settings.yaml (prd.md plan: dsh
integration with the hooks bridge + an additive `minder` provider):
  1. a `minder` provider card under llm-pi-ai.providers (baseURL → Turnstile)
  2. a plugins entry registering @deepseek-ai/dsh-hooks-claude-code

Never touches the existing provider or agent-default-model. Full-file backup
plus post-edit verification (PyYAML when importable, structural scan otherwise)
with automatic rollback on any anomaly.
"""
import argparse
import json
import pathlib
import shutil
import sys
import time

PLUGIN_NAME = "@deepseek-ai/dsh-hooks-claude-code"
PROVIDER_NAME = "minder"


# ---------------------------------------------------------------------------
# Line scanning (block-scalar aware)
# ---------------------------------------------------------------------------

def block_scalar_mask(lines):
    """Boolean list: True where the line is INSIDE a YAML block scalar."""
    mask = [False] * len(lines)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        indent = len(lines[i]) - len(lines[i].lstrip())
        key_part = stripped.split(":")[0] if ":" in stripped else ""
        scalar_sigil = stripped.rstrip().endswith(
            ("|", ">", "|-", ">-", "|+", ">+", "|2", ">2"))
        if scalar_sigil and ":" in lines[i]:
            j = i + 1
            while j < len(lines):
                if not lines[j].strip():
                    j += 1
                    continue
                j_indent = len(lines[j]) - len(lines[j].lstrip())
                if j_indent > indent:
                    mask[j] = True
                    j += 1
                else:
                    break
            i = j
            continue
        # plain scalar continuations ("key: value" spill-overs) are not
        # tracked — dsh settings uses folded blocks only for long strings.
        i += 1
    return mask


def find_top_level(lines, key, mask):
    for idx, line in enumerate(lines):
        if mask[idx]:
            continue
        if line.rstrip() == f"{key}:" or line.rstrip().startswith(f"{key}:"):
            if not line.startswith((" ", "\t")):
                return idx
    return None


def find_indented(lines, key, parent_idx, parent_indent, mask):
    """First line after parent_idx with indent == parent_indent+2 matching key,
    before the parent block ends (dedent <= parent_indent)."""
    if parent_idx is None:
        return None
    want = parent_indent + 2
    for idx in range(parent_idx + 1, len(lines)):
        if mask[idx] or not lines[idx].strip():
            continue
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        if indent <= parent_indent:
            return None
        if indent == want and lines[idx].strip().startswith(f"{key}:"):
            return idx
    return None


def section_block(lines, start_idx, indent):
    """Lines of the map entry starting at start_idx (key at given indent)."""
    out = [start_idx]
    for idx in range(start_idx + 1, len(lines)):
        if not lines[idx].strip():
            out.append(idx)
            continue
        j_indent = len(lines[idx]) - len(lines[idx].lstrip())
        if j_indent <= indent:
            break
        out.append(idx)
    return out


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

def provider_block(provider_name, base_url, n_ctx, api_key_env,
                   max_tokens_exec=8192, max_tokens_think=32768,
                   auto_efforts=None):
    lines = [
        f"    {provider_name}:",
        f"      displayName: Minder (Turnstile escalation watchdog)",
        f"      api: openai-completions",
        f"      baseURL: {base_url}",
    ]
    if api_key_env:
        lines.append(f"      apiKeyEnv: {api_key_env}")
    lines += [
        f"      defaultContextWindow: {min(n_ctx, 32768)}",
        f"      models:",
        f"        - id: qwen-exec",
        f"          name: Qwen Exec (minder)",
        f"          contextWindow: {n_ctx}",
        f"          maxTokens: {max_tokens_exec}",
        f"        - id: qwen-think",
        f"          name: Qwen Think (minder)",
        f"          contextWindow: {n_ctx}",
        f"          maxTokens: {max_tokens_think}",
        f"        - id: qwen-auto",
        f"          name: Qwen Auto (minder)",
        f"          contextWindow: {n_ctx}",
        f"          maxTokens: {max_tokens_think}",
    ]
    if auto_efforts:
        # declared to the harness so the UI effort picker offers the
        # vocabulary CAP actually measured (ADR-0004 pattern absorption)
        lines.append(f"          reasoningEfforts: "
                     f"[{', '.join(auto_efforts)}]")
    lines += [
        f"        - id: frontier",
        f"          name: Frontier (minder)",
        f"          contextWindow: {n_ctx}",
        f"          maxTokens: {max_tokens_exec}",
    ]
    return [ln + "\n" for ln in lines]


def ensure_provider_model(settings_text, model_id, n_ctx,
                          max_tokens=32768, provider_name=PROVIDER_NAME):
    """Add one model to an existing minder provider card (idempotent) —
    upgrades installs made before the model existed."""
    lines = settings_text.splitlines(keepends=True)
    if not lines or not lines[-1].endswith("\n"):
        if lines:
            lines[-1] = lines[-1] + "\n"
    mask = block_scalar_mask(lines)
    prov_idx = _providers_idx(lines, mask)
    if prov_idx is None:
        return settings_text, "no-providers"
    child = find_indented(lines, provider_name, prov_idx, 2, mask)
    if child is None:
        return settings_text, "no-provider"
    for idx in section_block(lines, child, 4):
        if mask[idx]:
            continue
        if lines[idx].strip() == f"- id: {model_id}":
            return settings_text, "already-present"
    # insert before the `frontier` entry (keeps exec/think/auto/frontier
    # ordering); frontier-absent cards append at the card end.
    card = section_block(lines, child, 4)
    insert_at = card[-1] + 1
    for idx in card:
        if lines[idx].strip() == "- id: frontier":
            insert_at = idx
            break
    entry = [f"        - id: {model_id}\n",
             f"          name: {model_id} (minder)\n",
             f"          contextWindow: {n_ctx}\n",
             f"          maxTokens: {max_tokens}\n"]
    lines[insert_at:insert_at] = entry
    return "".join(lines), "inserted"


def ensure_provider_efforts(settings_text, efforts,
                            provider_name=PROVIDER_NAME):
    """Declare `reasoningEfforts` on the qwen-auto model entry (idempotent) —
    upgrades installs made before the effort vocabulary was surfaced."""
    lines = settings_text.splitlines(keepends=True)
    if not lines or not lines[-1].endswith("\n"):
        if lines:
            lines[-1] = lines[-1] + "\n"
    mask = block_scalar_mask(lines)
    prov_idx = _providers_idx(lines, mask)
    if prov_idx is None:
        return settings_text, "no-providers"
    child = find_indented(lines, provider_name, prov_idx, 2, mask)
    if child is None:
        return settings_text, "no-provider"
    declared = f"reasoningEfforts: [{', '.join(efforts)}]"
    entry = None
    last_field = None
    for idx in section_block(lines, child, 4):
        if mask[idx]:
            continue
        if lines[idx].strip() == "- id: qwen-auto":
            entry = last_field = idx
            continue
        if entry is None:
            continue
        if lines[idx].strip().startswith("reasoningEfforts:"):
            if lines[idx].strip() == declared:
                return settings_text, "already-present"
            # A block-style mapping ("reasoningEfforts:" + indented keys)
            # spans multiple lines — overwriting just this line orphans the
            # keys and produces invalid YAML. Replace the whole block, not
            # one line.
            block_end = idx
            while block_end + 1 < len(lines) and (
                    not lines[block_end + 1].strip() or
                    len(lines[block_end + 1]) -
                    len(lines[block_end + 1].lstrip()) > 10):
                block_end += 1
            lines[idx:block_end + 1] = [" " * 10 + declared + "\n"]
            return "".join(lines), "updated"
        if lines[idx].strip().startswith("- id:"):
            # next model entry starts — insert before it
            lines[idx:idx] = [" " * 10 + declared + "\n"]
            return "".join(lines), "inserted"
        last_field = idx
    if entry is None:
        return settings_text, "no-qwen-auto"
    lines[last_field + 1:last_field + 1] = [" " * 10 + declared + "\n"]
    return "".join(lines), "inserted"


def plugin_block(plugin_name, hooks_json_path):
    return [ln + "\n" for ln in [
        f"  '{plugin_name}':",
        f"    enabled: true",
        f"    config:",
        f"      configPath: {hooks_json_path}",
        f"      defaultTimeoutMs: 600000",
    ]]


def existing_api_key_env(lines, mask):
    """apiKeyEnv of the first existing provider (reuses the user's credential)."""
    prov_idx = _providers_idx(lines, mask)
    if prov_idx is None:
        return None
    for idx in section_block(lines, prov_idx, 2):
        if mask[idx]:
            continue
        stripped = lines[idx].strip()
        if stripped.startswith("apiKeyEnv:"):
            return stripped.split(":", 1)[1].strip()
    return None


def add_provider(settings_text, base_url, n_ctx,
                 provider_name=PROVIDER_NAME, auto_efforts=None):
    lines = settings_text.splitlines(keepends=True)
    if not lines or not lines[-1].endswith("\n"):
        if lines:
            lines[-1] = lines[-1] + "\n"
    mask = block_scalar_mask(lines)
    if _providers_idx(lines, mask) is not None and \
            find_indented(lines, provider_name, _providers_idx(lines, mask),
                          2, mask) is not None:
        return settings_text, "already-present"
    prov_idx = _providers_idx(lines, mask)
    if prov_idx is None:
        raise SystemExit("FAIL: settings.yaml has no llm-pi-ai.providers map; "
                         "dsh provider config missing or nonstandard — report, "
                         "do not guess.")
    block = provider_block(provider_name, base_url, n_ctx,
                           existing_api_key_env(lines, mask),
                           auto_efforts=auto_efforts)
    lines[prov_idx + 1:prov_idx + 1] = block
    return "".join(lines), "inserted"


def _providers_idx(lines, mask):
    llm = find_top_level(lines, "llm-pi-ai", mask)
    if llm is None:
        return None
    return find_indented(lines, "providers", llm, 0, mask)


def register_plugin(settings_text, hooks_json_path,
                    plugin_name=PLUGIN_NAME):
    lines = settings_text.splitlines(keepends=True)
    if not lines or not lines[-1].endswith("\n"):
        if lines:
            lines[-1] = lines[-1] + "\n"
    mask = block_scalar_mask(lines)
    plug_idx = find_top_level(lines, "plugins", mask)
    if plug_idx is None:
        # create the section at the end
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(f"plugins:\n")
        lines += plugin_block(plugin_name, hooks_json_path)
        return "".join(lines), "created-section"
    for idx in section_block(lines, plug_idx, 0):
        if mask[idx]:
            continue
        if lines[idx].strip().startswith(f"'{plugin_name}':"):
            return settings_text, "already-present"
    lines[plug_idx + 1:plug_idx + 1] = plugin_block(plugin_name, hooks_json_path)
    return "".join(lines), "inserted"


def remove_provider(settings_text, provider_name=PROVIDER_NAME):
    lines = settings_text.splitlines(keepends=True)
    mask = block_scalar_mask(lines)
    prov_idx = _providers_idx(lines, mask)
    if prov_idx is None:
        return settings_text, "absent"
    child = find_indented(lines, provider_name, prov_idx, 2, mask)
    if child is None:
        return settings_text, "absent"
    span = section_block(lines, child, 4)
    del lines[span[0]:span[-1] + 1]
    return "".join(lines), "removed"


def remove_plugin(settings_text, plugin_name=PLUGIN_NAME):
    lines = settings_text.splitlines(keepends=True)
    mask = block_scalar_mask(lines)
    plug_idx = find_top_level(lines, "plugins", mask)
    if plug_idx is None:
        return settings_text, "absent"
    for idx in section_block(lines, plug_idx, 0):
        if mask[idx]:
            continue
        if lines[idx].strip().startswith(f"'{plugin_name}':"):
            span = section_block(lines, idx, 2)
            del lines[span[0]:span[-1] + 1]
            return "".join(lines), "removed"
    return settings_text, "absent"


# ---------------------------------------------------------------------------
# Verification (PyYAML when available, structural scan otherwise)
# ---------------------------------------------------------------------------

def verify(text, expect_provider=True, expect_plugin=True):
    try:
        import yaml  # type: ignore
    except ImportError:
        return _verify_structural(text, expect_provider, expect_plugin)
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        return False, f"yaml parse error: {e}"
    if not isinstance(data, dict):
        return False, "settings.yaml is not a mapping"
    if expect_provider:
        card = (data.get("llm-pi-ai") or {}).get("providers", {})\
            .get(PROVIDER_NAME)
        if not card:
            return False, "minder provider card missing after edit"
        ids = [m.get("id") for m in card.get("models", [])]
        expected = ["qwen-exec", "qwen-think", "qwen-auto", "frontier"]
        if ids != expected:
            return False, f"minder provider models wrong: {ids} != {expected}"
    if expect_plugin:
        ent = (data.get("plugins") or {}).get(PLUGIN_NAME)
        if not ent or not ent.get("enabled") or \
                not ent.get("config", {}).get("configPath"):
            return False, "hooks bridge plugin entry missing after edit"
    return True, "ok"


def _verify_structural(text, expect_provider=True, expect_plugin=True):
    lines = text.splitlines()
    if any("\t" in ln for ln in lines):
        return False, "tab character in settings.yaml"
    mask = block_scalar_mask(lines)
    if find_top_level(lines, "llm-pi-ai", mask) is None:
        return False, "llm-pi-ai section missing"
    if find_top_level(lines, "plugins", mask) is None:
        return False, "plugins section missing"
    if expect_provider and "    minder:\n" not in text and \
            "    minder:\r\n" not in text:
        return False, "minder provider line missing"
    if expect_plugin and PLUGIN_NAME not in text:
        return False, "plugin registration missing"
    return True, "ok (structural)"


# ---------------------------------------------------------------------------
# Profile layout (dsh >= 0.1.5: ~/.dsh/profiles/<name>/{cordis.patch.yml,…})
# ---------------------------------------------------------------------------

BRIDGE_LOADER = "minder-bridge-loader.mjs"
BRIDGE_MARKER = "minder-bridge-loader"
DEFAULT_DSH_HOME = pathlib.Path.home() / ".dsh"
DEFAULT_PROFILE = "web"

BRIDGE_LOADER_JS = """// minder: file-entry wrapper — package entries carrying a cordis `inject`
// export silently never mount in the web host (observed on dsh 0.1.5-rc.2),
// so this file entry imports the bridge package and re-exports its contract.
// Keep this file next to cordis.patch.yml; remove both to uninstall minder's
// dsh detection tier.
import * as bridge from "@deepseek-ai/dsh-hooks-claude-code";
export const inject = bridge.inject;
export const Config = bridge.Config;
export const name = "minder-hooks-bridge";
export function apply(ctx, config) {
  return bridge.apply(ctx, config);
}
"""


def repo_hooks_template():
    """The hooks.json template that ships beside this file (repo or share)."""
    return pathlib.Path(__file__).resolve().parent / "hooks.json"


def discover_profile(dsh_home):
    """The profile that has a patched cordis layer, or the default.

    Newer dsh does not keep `settings.yaml` at all: provider/plugin
    configuration lives per profile. Targeting the legacy file blindly is
    how the wiring silently stopped applying."""
    profiles = pathlib.Path(dsh_home) / "profiles"
    for name in (DEFAULT_PROFILE, "headless"):
        if (profiles / name / "cordis.patch.yml").exists():
            return name
    if profiles.is_dir():
        for entry in sorted(profiles.iterdir()):
            if (entry / "cordis.patch.yml").exists():
                return entry.name
    return DEFAULT_PROFILE


def render_hooks_json(share, sink_url, template=None):
    """The hook command carries the production flag set (the bridge reads
    this file once at host start, so this is the only place the runtime
    flags are declared for dsh) plus the sink URL the confined hook needs
    to persist anything at all."""
    text = (template or repo_hooks_template()).read_text()
    if "__MINDER_SHARE__" not in text:
        raise SystemExit(
            f"FAIL: {template or repo_hooks_template()} is not the minder "
            "hooks template (no __MINDER_SHARE__ placeholder) — refusing to "
            "guess.")
    return (text.replace("__MINDER_SHARE__", str(share))
                .replace("__MINDER_SINK_URL__", str(sink_url)))


def write_hooks_json(path, share, sink_url, template=None):
    path = pathlib.Path(path)
    text = render_hooks_json(share, sink_url, template)
    json.loads(text)  # fail before writing anything
    if path.exists() and path.read_text() == text:
        return "already-present"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(
            path.name + f".minder-{time.strftime('%Y%m%d-%H%M%S')}.bak"))
    path.write_text(text)
    return "written"


def ensure_bridge_loader(profile_dir):
    profile_dir = pathlib.Path(profile_dir)
    target = profile_dir / BRIDGE_LOADER
    if target.exists() and target.read_text() == BRIDGE_LOADER_JS:
        return "already-present"
    profile_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(BRIDGE_LOADER_JS)
    return "written"


def patch_entry(hooks_json, timeout_ms=600000):
    return ("- insert:\n"
            f"    - name: ./{BRIDGE_LOADER}\n"
            "      config:\n"
            f"        configPath: {hooks_json}\n"
            f"        defaultTimeoutMs: {timeout_ms}\n")


def ensure_patch_entry(profile_dir, hooks_json, timeout_ms=600000):
    """Append the bridge insert to cordis.patch.yml (idempotent)."""
    profile_dir = pathlib.Path(profile_dir)
    patch = profile_dir / "cordis.patch.yml"
    text = patch.read_text() if patch.exists() else "[]\n"
    if BRIDGE_MARKER in text:
        return "already-present"
    if not text.endswith("\n"):
        text += "\n"
    patch.write_text(text + "\n" + patch_entry(hooks_json, timeout_ms))
    return "written"


def check_profile(dsh_home, profile, share, sink_url, hooks_json=None):
    """Report every wiring point for the profile layout. Returns
    (ok, [(point, status, detail)])."""
    dsh_home = pathlib.Path(dsh_home)
    profile_dir = dsh_home / "profiles" / profile
    hooks_json = pathlib.Path(hooks_json or (share / "dsh" / "hooks.json"))
    points = []
    patch = profile_dir / "cordis.patch.yml"
    if not patch.exists():
        points.append(("bridge-entry", "fail",
                       f"{patch} does not exist — no profile at "
                       f"{profile_dir}?"))
    elif BRIDGE_MARKER not in patch.read_text():
        points.append(("bridge-entry", "fail",
                       f"the hooks bridge is not inserted in {patch}"))
    else:
        points.append(("bridge-entry", "ok", str(patch)))
    loader = profile_dir / BRIDGE_LOADER
    points.append(("bridge-loader",
                   "ok" if loader.exists() else "fail",
                   str(loader)))
    if not hooks_json.exists():
        points.append(("hooks-json", "fail", f"{hooks_json} missing"))
    else:
        text = hooks_json.read_text()
        try:
            obj = json.loads(text)
        except ValueError as exc:
            obj = None
            parsed = f"INVALID JSON: {exc}"
        else:
            parsed = "parses"
        has_share = str(share) in text
        has_sink = str(sink_url) in text
        flags = all(f in text for f in ("MINDER_DECISION=",
                                        "MINDER_SINK_URL=",
                                        "MINDER_ASSIST="))
        # The success-loop stop needs a pre-execution hook point: without
        # PreToolUse, MINDER_SUCCESS_GUARD=block can only ever advise after
        # the fact, and nothing else would say so.
        hook_points = set((obj.get("hooks") or {}).keys()) \
            if isinstance(obj, dict) else set()
        has_pretool = "PreToolUse" in hook_points
        status = "ok" if (has_share and has_sink and flags and has_pretool
                          and parsed == "parses") else "fail"
        points.append(("hooks-json", status,
                       f"{hooks_json} ({parsed}; share={has_share} "
                       f"sink={has_sink} flags={flags} "
                       f"pretool={has_pretool})"))
    ok = all(status == "ok" for _p, status, _d in points)
    return ok, points


def apply_profile(dsh_home, profile, share, sink_url, hooks_json=None):
    """Wire the three points; caller decides whether to `dsh plugin add`."""
    dsh_home = pathlib.Path(dsh_home)
    profile_dir = dsh_home / "profiles" / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    hooks_json = pathlib.Path(hooks_json or (share / "dsh" / "hooks.json"))
    result = {"profile": profile, "profile_dir": str(profile_dir)}
    result["hooks-json"] = write_hooks_json(hooks_json, share, sink_url)
    result["bridge-loader"] = ensure_bridge_loader(profile_dir)
    result["bridge-entry"] = ensure_patch_entry(profile_dir, hooks_json)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["apply", "remove", "check",
                                    "profile-apply", "profile-check"])
    ap.add_argument("--settings", default=None,
                    help="legacy ~/.dsh/settings.yaml (optional: modern dsh "
                         "has no such file)")
    ap.add_argument("--hooks-json", default=None,
                    help="absolute path for the bridge configPath")
    ap.add_argument("--base-url", default="http://127.0.0.1:8390/v1")
    ap.add_argument("--n-ctx", type=int, default=32768)
    ap.add_argument("--dsh-home", default=str(DEFAULT_DSH_HOME))
    ap.add_argument("--profile", default=None,
                    help="dsh profile name (default: auto-discover)")
    ap.add_argument("--share", default=str(
        pathlib.Path.home() / ".local/share/minder"))
    ap.add_argument("--sink-url", default="http://127.0.0.1:8392")
    ap.add_argument("--efforts", default="",
                    help="comma list of CAP-measured effort levels to declare "
                         "on qwen-auto (e.g. off,low,medium,xhigh); empty = "
                         "skip (Law #9: nothing assumed)")
    args = ap.parse_args()
    auto_efforts = [e.strip() for e in args.efforts.split(",") if e.strip()]

    if args.cmd in ("profile-apply", "profile-check"):
        profile = args.profile or discover_profile(args.dsh_home)
        share = pathlib.Path(args.share)
        if args.cmd == "profile-check":
            ok, points = check_profile(args.dsh_home, profile, share,
                                       args.sink_url, args.hooks_json)
            for point, status, detail in points:
                print(f"{status:4} {point:14} {detail}")
            print("OK" if ok else "FAIL: profile wiring incomplete")
            return 0 if ok else 1
        result = apply_profile(args.dsh_home, profile, share, args.sink_url,
                               args.hooks_json)
        for key, value in result.items():
            print(f"{key}={value}")
        return 0

    if not args.settings:
        print("FAIL: --settings is required for the legacy settings.yaml "
              "commands. On a modern dsh use `profile-apply` / "
              "`profile-check` instead (settings.yaml no longer exists).",
              file=sys.stderr)
        return 2

    settings = pathlib.Path(args.settings)
    original = settings.read_text()

    if args.cmd == "check":
        ok, msg = verify(original)
        print(("OK: " if ok else "FAIL: ") + msg)
        return 0 if ok else 1

    if args.cmd == "remove":
        text, _ = remove_provider(original)
        text, _ = remove_plugin(text)
        ok, msg = verify(text, expect_provider=False, expect_plugin=False)
        if not ok:
            print(f"FAIL verification after removal: {msg}; no changes written")
            return 1
        settings.write_text(text)
        print("removed minder provider + plugin entry")
        return 0

    # apply
    backup = settings.with_name(
        settings.name + f".minder-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(settings, backup)

    hooks_json = args.hooks_json or str(
        pathlib.Path.home() / ".local/share/minder/dsh/hooks.json")
    text, st1 = add_provider(original, args.base_url, args.n_ctx,
                             auto_efforts=auto_efforts)
    text, st2 = register_plugin(text, hooks_json)
    if st1 == "already-present" and auto_efforts:
        # upgrade path: older installs lack newer models (e.g. qwen-auto)
        text, _ = ensure_provider_model(text, "qwen-auto", args.n_ctx)
        text, _ = ensure_provider_efforts(text, auto_efforts)

    ok, msg = verify(text)
    if not ok:
        shutil.copy2(backup, settings)
        print(f"FAIL verification after edit ({msg}); rolled back to {backup}")
        return 1
    settings.write_text(text)
    print(f"applied: provider={st1}, plugin={st2}; backup={backup}; verify={msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
