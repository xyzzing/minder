#!/usr/bin/env bash
# minder installer (v0.4) — zero-input, fail-closed (prd.md §9 step 5).
# Probes → CAP → fail-closed ladder → patched presets → merges (backup+verify)
# → skill placement → systemd --user unit. Honors: --upstream URL, --port N,
# --skip-dsh, --skip-zcode, --accept-l1-degraded, --no-start, --frontier-cmd CMD
set -uo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
UPSTREAM="${MINDER_UPSTREAM:-http://127.0.0.1:8080}"
PORT="${MINDER_PORT:-8390}"
SINK_PORT="${MINDER_SINK_PORT:-8392}"
SHARE="$HOME/.local/share/minder"
CONFIG="$HOME/.config/minder"
STATE="$HOME/.local/state/minder"
TS="$(date +%Y%m%d-%H%M%S)"
ACCEPT_DEGRADED=0
START_UNIT=1
DO_DSH=1
DO_ZCODE=1
FRONTIER_CMD=""
EFFORT_MODE="off"

# frontier consult channel: default to the bundled runner (fails honestly
# without a key; --frontier-cmd overrides with anything stdin/stdout)
FRONTIER_CMD="${FRONTIER_CMD:-python3 $SHARE/frontier.py}"
while [ $# -gt 0 ]; do
  case "$1" in
    --upstream) UPSTREAM="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --sink-port) SINK_PORT="$2"; shift 2 ;;
    --skip-dsh) DO_DSH=0; shift ;;
    --skip-zcode) DO_ZCODE=0; shift ;;
    --accept-l1-degraded) ACCEPT_DEGRADED=1; shift ;;
    --no-start) START_UNIT=0; shift ;;
    --frontier-cmd) FRONTIER_CMD="$2"; shift 2 ;;
    --effort-mode) EFFORT_MODE="$2"; shift 2 ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
done

say() { printf '[minder] %s\n' "$*"; }
fail() { printf '[minder] STOP: %s\n' "$*" >&2; exit 1; }

# --- [0] P-1: python >= 3.10 -------------------------------------------------
python3 -c 'import sys; assert sys.version_info >= (3, 10)' \
  || fail "P-1: python >= 3.10 required"

# --- [1] stage code ----------------------------------------------------------
mkdir -p "$SHARE" "$CONFIG" "$STATE"
cp -f "$SRC/minder.py" "$SRC/adapter.py" "$SRC/proxy.py" "$SRC/hook.py" \
      "$SRC/frontier.py" "$SRC/reflex.py" "$SRC/probe_dialect.py" "$SRC/sink.py" "$SHARE/"
cp -R "$SRC/presets" "$SHARE/"
cp -R "$SRC/zcode" "$SHARE/"
cp -R "$SRC/dsh" "$SHARE/"
cp -R "$SRC/memory" "$SHARE/"
cp -R "$SRC/decision" "$SHARE/"
cp -R "$SRC/minder_op" "$SHARE/"
cp -R "$SRC/minder_web" "$SHARE/"
cp -R "$SRC/skills" "$SHARE/"
chmod +x "$SHARE/proxy.py" "$SHARE/hook.py" "$SHARE/frontier.py" \
         "$SHARE/probe_dialect.py" "$SHARE/sink.py" 2>/dev/null || true
# hooks.json: patch the share path and the sink URL into the command hooks.
# The hook command carries the production flag set, because these are the
# only place the runtime flags are declared for dsh — and because the
# bridge reads this file once at host start, a flags-only change needs a
# dsh host restart to take effect.
sed -e "s|__MINDER_SHARE__|$SHARE|g" \
    -e "s|__MINDER_SINK_URL__|http://127.0.0.1:$SINK_PORT|g" \
    "$SRC/dsh/hooks.json" > "$SHARE/dsh/hooks.json"
say "[1] code staged at $SHARE"

# --- [2] locate integration targets -----------------------------------------
DSH_SETTINGS=""
ZCODE_CONFIG=""
[ -f "$HOME/.dsh/settings.yaml" ] && DSH_SETTINGS="$HOME/.dsh/settings.yaml"
[ -f "$HOME/.zcode/cli/config.json" ] && ZCODE_CONFIG="$HOME/.zcode/cli/config.json"
say "[2] targets: dsh=${DSH_SETTINGS:-none} zcode=${ZCODE_CONFIG:-none}"

# --- [3] preflight probes P-2/P-2b/P-3/P-4 -----------------------------------
export MINDER_SHARE="$SHARE"
PROBE_JSON="$(python3 - "$UPSTREAM" <<'PYEOF'
import json, os, sys
sys.path.insert(0, os.environ["MINDER_SHARE"])
import adapter
print(json.dumps(adapter.preflight(sys.argv[1])))
PYEOF
)" || PROBE_JSON='{"alive": false, "generation_ok": null, "kwargs_accepted": null, "n_ctx": null}'
ALIVE=$(python3 -c "import json,sys; print(json.loads('''$PROBE_JSON''')['alive'])" 2>/dev/null || echo False)
GEN_OK=$(python3 -c "import json,sys; print(json.loads('''$PROBE_JSON''')['generation_ok'])" 2>/dev/null || echo None)
KWARGS_OK=$(python3 -c "import json,sys; print(json.loads('''$PROBE_JSON''')['kwargs_accepted'])" 2>/dev/null || echo None)
N_CTX=$(python3 -c "import json,sys; print(json.loads('''$PROBE_JSON''')['n_ctx'] or 32768)" 2>/dev/null || echo 32768)

if [ "$ALIVE" != "True" ]; then
  say "WARN P-2: upstream $UPSTREAM unreachable — installing in mock-capable mode; CAP deferred (re-run installer when the server is up)"
else
  say "[3] P-2 alive; P-2b generation=$GEN_OK P-3 kwargs=$KWARGS_OK P-4 n_ctx=$N_CTX"
  [ "$GEN_OK" = "True" ] || fail "P-2b: upstream loads but generation fails — see preflight above"
fi

# --- [4] CAP (P-6) + fail-closed ladder --------------------------------------
CAP_STATUS="deferred"
MECHANISM="unknown"
MODEL_ID=""
BUDGET_OK="false"
if [ "$ALIVE" = "True" ]; then
  CAP_JSON="$(MINDER_SHARE="$SHARE" python3 - "$UPSTREAM" <<'PYEOF'
import json, os, sys
sys.path.insert(0, os.environ["MINDER_SHARE"])
import adapter
caps, err = adapter.run_cap(sys.argv[1])
if err is not None:
    print(json.dumps({"error": err}))
else:
    print(json.dumps(caps))
PYEOF
)"
  if echo "$CAP_JSON" | grep -q '"error"'; then
    echo "$CAP_JSON" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["error"])'
    fail "P-6: capability probe errored mid-probe (transcript above, verbatim)"
  fi
  printf '%s\n' "$CAP_JSON" > "$CONFIG/model_caps.json"
  MECHANISM=$(python3 -c "import json,sys; print(json.loads('''$CAP_JSON''')['thinking']['mechanism'])")
  MODEL_ID=$(python3 -c "import json,sys; print(json.loads('''$CAP_JSON''')['fingerprint']['model_id'] or '')")
  BUDGET_OK=$(python3 -c "import json,sys; print(str(json.loads('''$CAP_JSON''')['thinking']['thinking_budget_supported']).lower())")
  TOOL_CLEAN=$(python3 -c "import json,sys; tc=json.loads('''$CAP_JSON''').get('tool_calls') or {}; print(str(tc.get('clean', 'None')).lower() + ' ' + str(tc.get('dirty','?')) + '/' + str(tc.get('probes','?')))")
  AUTO_EFFORTS=$(python3 -c "
import json
levels = ['off'] + [l for l in (json.loads('''$CAP_JSON''').get('effort_levels') or []) if l != 'off']
print(','.join(levels))" 2>/dev/null)
  CAP_STATUS="$MECHANISM"
  case "$MECHANISM" in
    kwargs|softswitch) say "[4] CAP: mechanism=$MECHANISM budget=$BUDGET_OK model=${MODEL_ID:-unknown} — full L1" ;;
    none)
      if [ "$ACCEPT_DEGRADED" = "1" ]; then
        say "[4] CAP: mechanism=none — installing digest-only L1 (--accept-l1-degraded)"
      else
        fail "CAP: this server cannot toggle thinking (mechanism=none). Re-run with --accept-l1-degraded to install digest-only L1 honestly."
      fi ;;
  esac
  case "$TOOL_CLEAN" in
    true*) say "[4] CAP T1c: tool-calls clean ($TOOL_CLEAN)" ;;
    false*) printf '[minder] WARN: tool-call dialect dirty (%s) — tool traffic may burn tokens to unparseable output; fix the server template before trusting agent loops\n' "$TOOL_CLEAN" >&2 ;;
    *) say "[4] CAP T1c: tool-call probe not available" ;;
  esac
else
  say "[4] CAP deferred — presets installed unpatched; proxy will run without mechanism translation until CAP is re-run"
fi

# --- [5] patched presets + minder.json ---------------------------------------
mkdir -p "$CONFIG/presets"
cp -f "$SRC/presets/"*.json "$CONFIG/presets/"
python3 - "$CONFIG" "$MODEL_ID" "$BUDGET_OK" <<'PYEOF'
import json, sys
from pathlib import Path
config, model_id, budget_ok = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
for name in ("qwen3-exec", "qwen3-think"):
    p = Path(config) / "presets" / f"{name}.json"
    if not p.exists():
        continue
    preset = json.loads(p.read_text())
    if model_id:
        preset["upstream_model"] = model_id
    ctk = preset["upstream_params"].get("chat_template_kwargs", {})
    if "thinking_budget" in ctk and not budget_ok:
        del ctk["thinking_budget"]  # §5.4: omit kwarg when unsupported
    p.write_text(json.dumps(preset, indent=2) + "\n")
PYEOF
python3 - "$CONFIG" "$ACCEPT_DEGRADED" "$FRONTIER_CMD" "$EFFORT_MODE" <<'PYEOF'
import json, sys
from pathlib import Path
config, degraded, frontier_cmd, effort_mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cfg = {"accept_l1_degraded": degraded == "1"}
p = Path(config) / "minder.json"
existing = {}
if p.exists():
    try:
        existing = json.loads(p.read_text())
    except ValueError:
        pass
# safety consent is re-asked on every install (conservative direction);
# preference knobs are first-run defaults — explicit user values survive
existing.update(cfg)
existing.setdefault("effort_mode", effort_mode)
existing.setdefault("frontier_command", frontier_cmd)
# frontier endpoint defaults — written only when absent so explicit user
# values survive reinstalls (frontier.py carries the same fallbacks)
for k, v in (("frontier_base_url", "https://api.deepseek.com"),
             ("frontier_model", "deepseek-chat"),
             ("frontier_key_env", "DEEPSEEK_API_KEY"),
             ("frontier_timeout", 60)):
    existing.setdefault(k, v)
# the panel: DeepSeek primary + OpenAI as the independent second opinion.
# The openai entry stays inert until OPENAI_API_KEY exists — no edit needed.
existing.setdefault("frontier_providers", [
    {"name": "deepseek", "base_url": "https://api.deepseek.com",
     "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    {"name": "openai", "base_url": "https://api.openai.com",
     "model": "", "key_env": "OPENAI_API_KEY"},
])
p.write_text(json.dumps(existing, indent=2) + "\n")
PYEOF
# frontier key store: hook processes don't inherit login-env secrets; keep the
# keys in a 600 file they can read. Seed from the env when present, else TODO.
if [ ! -f "$CONFIG/frontier.env" ]; then
  umask 177
  {
    if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
      printf 'DEEPSEEK_API_KEY=%s\n' "$DEEPSEEK_API_KEY"
    else
      printf '# minder frontier key store (chmod 600). Add one or both:\n'
      printf '# DEEPSEEK_API_KEY=sk-...\n'
      printf '# OPENAI_API_KEY=sk-proj-...   <- enables the cross-check panel\n'
      printf '# L2 consults degrade honestly until at least one is set.\n'
    fi
  } > "$CONFIG/frontier.env"
  umask 022
fi
PANEL_STATE="key-pending"
{ [ -n "${DEEPSEEK_API_KEY:-}" ] || [ -n "${OPENAI_API_KEY:-}" ]; } && PANEL_STATE="key-seeded"
say "[5] patched presets + minder.json at $CONFIG (effort_mode=$EFFORT_MODE, frontier=$PANEL_STATE)"

# --- [6] zcode merge ----------------------------------------------------------
if [ "$DO_ZCODE" = "1" ] && [ -n "$ZCODE_CONFIG" ]; then
  cp -f "$ZCODE_CONFIG" "$ZCODE_CONFIG.minder-$TS.bak"
  MINDER_SHARE="$SHARE" python3 - "$ZCODE_CONFIG" <<'PYEOF'
import json, os, sys
path = sys.argv[1]
share = os.environ["MINDER_SHARE"]
cfg = json.loads(open(path).read())
events = cfg.setdefault("hooks", {}).setdefault("events", {})

HOOK_SCRIPT = os.path.join(share, "hook.py")

def has_entry(event):
    return any(HOOK_SCRIPT in a
               for e in events.get(event, [])
               for h in e.get("hooks", [])
               for a in h.get("args", []))

if not has_entry("PostToolUse"):
    events.setdefault("PostToolUse", []).append({
        "matcher": "Edit|Write|MultiEdit|Bash",
        "hooks": [{"type": "process", "command": "python3",
                   "args": [HOOK_SCRIPT, "--transport", "zcode"],
                   "enabled": True, "timeoutMs": 65000}]})
if not has_entry("SessionStart"):
    events.setdefault("SessionStart", []).append({
        "matcher": "startup|clear|compact",
        "hooks": [{"type": "process", "command": "python3",
                   "args": [HOOK_SCRIPT, "--transport", "zcode",
                            "--session-start"],
                   "enabled": True, "timeoutMs": 5000}]})
else:
    # keep the matcher current on upgrades (compaction survival needs
    # compact to fire; entries from older installs say just "startup")
    for e in events.get("SessionStart", []):
        if any(HOOK_SCRIPT in a for h in e.get("hooks", [])
               for a in h.get("args", [])):
            e["matcher"] = "startup|clear|compact"
open(path, "w").write(json.dumps(cfg, indent=2) + "\n")
json.loads(open(path).read())  # verify re-parse before declaring success
print("zcode merge ok")
PYEOF
  [ $? -eq 0 ] || fail "zcode merge failed (backup at $ZCODE_CONFIG.minder-$TS.bak)"
  say "[6] zcode hooks merged into $ZCODE_CONFIG (backup .minder-$TS.bak)"
else
  say "[6] zcode: skipped ($([ -z "$ZCODE_CONFIG" ] && echo 'no config found' || echo 'disabled'))"
fi

# --- [7] dsh integration ------------------------------------------------------
if [ "$DO_DSH" = "1" ]; then
  if [ -n "$DSH_SETTINGS" ]; then
    python3 "$SRC/dsh/dsh_install.py" apply --settings "$DSH_SETTINGS" \
      --hooks-json "$SHARE/dsh/hooks.json" --n-ctx "$N_CTX" \
      --efforts "${AUTO_EFFORTS:-}" \
      || fail "dsh settings.yaml patch failed (backup kept alongside; nothing written on verify-fail)"
  else
    # Modern dsh keeps provider/plugin config per profile in
    # ~/.dsh/profiles/<name>/{cordis.yml,cordis.patch.yml}; settings.yaml
    # no longer exists. That must not stop the hooks bridge from landing.
    say "[7] dsh: no settings.yaml (profile layout) — skipping the legacy provider patch"
  fi
  # 7a. wire the profile (loader + patch entry + hooks.json with the runtime
  # flags and the sink URL). Independent of whether the plugin package is
  # installed: a missing package then only needs `dsh plugin add`, and
  # profile-check below says so instead of pretending everything is wired.
  if [ -d "$HOME/.dsh/profiles" ] || command -v dsh >/dev/null 2>&1; then
    DSH_PROFILE_DIR="$HOME/.dsh/profiles/web"
    [ -f "$DSH_PROFILE_DIR/cordis.patch.yml" ] || DSH_PROFILE_DIR=""
    if [ -n "$DSH_PROFILE_DIR" ]; then
      python3 "$SRC/dsh/dsh_install.py" profile-apply \
        --dsh-home "$HOME/.dsh" --profile web \
        --share "$SHARE" --sink-url "http://127.0.0.1:$SINK_PORT" \
        --hooks-json "$SHARE/dsh/hooks.json" \
        || fail "dsh profile wiring failed (loader/patch/hooks.json)"
    else
      say "[7] dsh: no ~/.dsh/profiles/web/cordis.patch.yml yet — start dsh once, then re-run install.sh"
    fi
  fi

  # 7b. install the bridge package itself (needs the dsh CLI).
  if command -v dsh >/dev/null 2>&1 && [ "${MINDER_NO_DSH_PLUGIN:-0}" != "1" ]; then
    say "[7] installing hooks bridge plugin into dsh web profile..."
    # pnpm add re-resolves floating deps — snapshot first so a broken tree can
    # be rolled back (a re-resolution once pulled a UI plugin incompatible with
    # the pinned dsh core and blocked profile boot).
    for f in package.json pnpm-lock.yaml; do
      [ -f "$HOME/.dsh/profiles/web/$f" ] && \
        cp -f "$HOME/.dsh/profiles/web/$f" "$HOME/.dsh/profiles/web/$f.minder-$TS.bak"
    done
    if dsh plugin --profile web add @deepseek-ai/dsh-hooks-claude-code@0.1.5-rc.2 2>&1 | tail -2; then
      say "[7] plugin installed (bridge version-matched to core — update both together)"
      say "    if web boot now fails on a third-party plugin: restore the .minder-*.bak"
      say "    package.json/pnpm-lock.yaml in ~/.dsh/profiles/web/ and re-run pnpm install,"
      say "    or disable the offending entry by id in cordis.patch.yml"
    else
      say "WARN: dsh plugin install failed — install '@deepseek-ai/dsh-hooks-claude-code@<core-version>' manually; the bridge entry is already wired"
    fi
  else
    say "WARN: dsh binary not found (or plugin install disabled) — bridge entry wired; finish with: dsh plugin --profile web add @deepseek-ai/dsh-hooks-claude-code@<core-version>"
  fi

  # 7c. verify every wiring point, so a half-wired profile is never silently
  # reported as done (the failure mode this whole block exists for).
  if [ -f "$HOME/.dsh/profiles/web/cordis.patch.yml" ]; then
    python3 "$SRC/dsh/dsh_install.py" profile-check \
      --dsh-home "$HOME/.dsh" --profile web \
      --share "$SHARE" --sink-url "http://127.0.0.1:$SINK_PORT" \
      --hooks-json "$SHARE/dsh/hooks.json" \
      || say "WARN: profile wiring incomplete (see above) — hooks will not mount"
  fi
  say "[7] dsh: hooks bridge wired. The bridge reads hooks.json once at host"
  say "    start — restart the dsh web host for flag changes to take effect."
else
  say "[7] dsh: skipped (--skip-dsh)"
fi

# --- [8] systemd --user unit ---------------------------------------------------
# Unit files are ALWAYS written; --no-start only skips enable/start, so the
# "unit written but not enabled" message below is actually true and the
# operator can start it themselves.
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/minder-proxy.service" <<EOF
[Unit]
Description=minder Turnstile proxy (escalation watchdog transport)
After=network.target

[Service]
Environment=MINDER_UPSTREAM=$UPSTREAM
Environment=MINDER_PORT=$PORT
ExecStart=$SHARE/proxy.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
if [ "$START_UNIT" != "1" ]; then
  say "[8] --no-start: proxy unit written, not enabled"
elif command -v systemctl >/dev/null 2>&1 && [ -z "${MINDER_NO_SYSTEMD:-}" ]; then
  systemctl --user daemon-reload || true
  systemctl --user enable minder-proxy.service || \
    say "WARN: could not enable unit — run: systemctl --user enable minder-proxy.service"
  # code was just re-staged — a running unit must not keep serving the
  # previous version (restart is a no-op when it was not running)
  systemctl --user restart minder-proxy.service || \
    say "WARN: could not start unit — run: systemctl --user restart minder-proxy.service"
  # verify proxy came up and synthesizes aliases (AT-17 live)
  OK=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -sf "http://127.0.0.1:$PORT/v1/models" 2>/dev/null | grep -q qwen-exec; then OK=1; break; fi
  done
  [ "$OK" = "1" ] && say "[8] proxy live on 127.0.0.1:$PORT — /v1/models lists minder aliases"
  [ "$OK" = "0" ] && say "WARN: proxy did not answer within 10s — check: journalctl --user -u minder-proxy.service"
else
  say "[8] systemd skipped — start manually: MINDER_UPSTREAM=$UPSTREAM MINDER_PORT=$PORT $SHARE/proxy.py &"
fi

# --- [8a] systemd --user unit: the sink (persistence sidecar) -----------------
# Hooks run inside dsh's file sandbox and cannot write $STATE, so their
# writes are delegated here over loopback. It also warms the laya models
# once per session instead of once per tool call.
#
# The runtime flags (MINDER_ASSIST/DECISION/CLASSIFIER/…) are deliberately
# NOT repeated here: the policy pass runs inside the sink, so the sink reads
# them back from the hook command in hooks.json at startup. One source of
# truth means the two can never drift into silently different behaviour
# (`minder-op capture` warns if they ever do). An explicit Environment= line
# would still win over the adopted value.
cat > "$UNIT_DIR/minder-sink.service" <<EOF
[Unit]
Description=minder sink (persistence + warm-policy sidecar for sandboxed hooks)
After=network.target

[Service]
Environment=MINDER_STATE_DIR=$STATE
ExecStart=$SHARE/sink.py --host 127.0.0.1 --port $SINK_PORT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
if [ "$START_UNIT" != "1" ]; then
  say "[8a] --no-start: sink unit written, not enabled"
elif command -v systemctl >/dev/null 2>&1 && [ -z "${MINDER_NO_SYSTEMD:-}" ]; then
  systemctl --user daemon-reload || true
  systemctl --user enable minder-sink.service || \
    say "WARN: could not enable unit — run: systemctl --user enable minder-sink.service"
  systemctl --user restart minder-sink.service || \
    say "WARN: could not start unit — run: systemctl --user restart minder-sink.service"
  OK=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -sf "http://127.0.0.1:$SINK_PORT/healthz" 2>/dev/null | grep -q '"ok"'; then OK=1; break; fi
  done
  [ "$OK" = "1" ] && say "[8a] sink live on 127.0.0.1:$SINK_PORT — sandboxed hooks can now persist"
  [ "$OK" = "0" ] && say "WARN: sink did not answer within 10s — check: journalctl --user -u minder-sink.service"
else
  say "[8a] systemd skipped — start manually: MINDER_STATE_DIR=$STATE $SHARE/sink.py --port $SINK_PORT &"
fi

# --- [8c] systemd --user unit: the operator console ---------------------------
# The console kept being started by hand with nohup, which is why it "just
# disappeared" (and why it once served stale code against new templates).
# It is read-only and loopback-only; the unit only makes its lifetime
# explicit. Requires the optional web extra — skipped with a clear message
# when fastapi/uvicorn are absent.
WEB_PORT="${MINDER_WEB_PORT:-8765}"
cat > "$UNIT_DIR/minder-web.service" <<EOF
[Unit]
Description=minder operator console (localhost, read-only)
After=network.target

[Service]
Environment=MINDER_STATE_DIR=$STATE
Environment=MINDER_WEB_DB=$STATE/memory.sqlite
WorkingDirectory=$SHARE
ExecStart=/usr/bin/env python3 -m minder_web --host 127.0.0.1 --port $WEB_PORT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
if ! python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  say "[8c] console unit written but NOT started — install the web extra:"
  say "     pip install --user -r $SRC/requirements-web.txt"
elif [ "$START_UNIT" != "1" ]; then
  say "[8c] --no-start: console unit written, not enabled"
elif command -v systemctl >/dev/null 2>&1 && [ -z "${MINDER_NO_SYSTEMD:-}" ]; then
  systemctl --user daemon-reload || true
  systemctl --user enable minder-web.service || \
    say "WARN: could not enable unit — run: systemctl --user enable minder-web.service"
  systemctl --user restart minder-web.service || \
    say "WARN: could not start unit — run: systemctl --user restart minder-web.service"
  OK=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -sf "http://127.0.0.1:$WEB_PORT/healthz" >/dev/null 2>&1; then OK=1; break; fi
  done
  [ "$OK" = "1" ] && say "[8c] console live at http://127.0.0.1:$WEB_PORT (read-only, loopback only)"
  [ "$OK" = "0" ] && say "WARN: console did not answer within 10s — check: journalctl --user -u minder-web.service"
else
  say "[8c] systemd skipped — start manually: minder-web --port $WEB_PORT"
fi

# --- [8b] operator shims (run minder-op / minder-web from anywhere) ------------
mkdir -p "$HOME/.local/bin"
printf '#!/usr/bin/env bash\nexec env -u LD_LIBRARY_PATH PYTHONPATH="%s" MINDER_NO_SYSTEMD=1 python3 -m minder_op "$@"\n' "$SHARE" \
  > "$HOME/.local/bin/minder-op"
# minder_web is staged into the share like every other module, so the shim and
# the unit do not depend on where the repo checkout happens to live.
printf '#!/usr/bin/env bash\nexec env -u LD_LIBRARY_PATH PYTHONPATH="%s" MINDER_NO_SYSTEMD=1 python3 -m minder_web "$@"\n' "$SHARE" \
  > "$HOME/.local/bin/minder-web"
printf '#!/usr/bin/env bash\nexec env -u LD_LIBRARY_PATH MINDER_STATE_DIR="%s" python3 "%s/sink.py" "$@"\n' "$STATE" "$SHARE" \
  > "$HOME/.local/bin/minder-sink"
chmod +x "$HOME/.local/bin/minder-op" "$HOME/.local/bin/minder-web" \
         "$HOME/.local/bin/minder-sink"
say "[8b] operator shims: $HOME/.local/bin/{minder-op,minder-web,minder-sink}"

# --- [9] summary ---------------------------------------------------------------
cat <<EOF

[minder] INSTALL SUMMARY
- code:      $SHARE
- config:    $CONFIG (caps mechanism: ${CAP_STATUS})
- state:     $STATE (ledger: events.jsonl)
- proxy:     http://127.0.0.1:$PORT → $UPSTREAM
- sink:      http://127.0.0.1:$SINK_PORT (hook persistence + warm models;
             required because dsh's file sandbox blocks hook writes)
- dsh:       pick model 'qwen-exec' (or qwen-think) from the minder provider
- zcode:     PostToolUse + SessionStart hooks active in new sessions
- frontier:  ${FRONTIER_CMD:-not configured (L2 degrades honestly)}
- operator:  minder-op status   (shims in ~/.local/bin; repo checkout also works via python3 -m minder_op)

Verify any time:
  curl -s http://127.0.0.1:$PORT/v1/chat/completions -H 'Content-Type: application/json' \\
    -d '{"model":"qwen-think","messages":[{"role":"user","content":"hi"}],"max_tokens":32}' | head -c 400
  tail -f $STATE/events.jsonl
Uninstall: $SRC/uninstall.sh
EOF
say "done"
