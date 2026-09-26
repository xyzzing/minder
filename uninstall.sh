#!/usr/bin/env bash
# minder uninstall — reverses install.sh additively; ledger state is kept.
set -uo pipefail

SHARE="$HOME/.local/share/minder"
CONFIG="$HOME/.config/minder"
say() { printf '[minder] %s\n' "$*"; }

# 1. stop + remove the units (never touch the real user session in scratch-HOME tests)
if [ "${MINDER_NO_SYSTEMD:-0}" = "1" ]; then
  say "systemd skipped (MINDER_NO_SYSTEMD=1)"
else
  systemctl --user disable --now minder-doctor.timer 2>/dev/null \
    && say "doctor timer stopped" || say "doctor timer not active"
  systemctl --user disable --now minder-sink.service 2>/dev/null \
    && say "sink unit stopped" || say "sink unit not active"
  systemctl --user disable --now minder-web.service 2>/dev/null \
    && say "console unit stopped" || say "console unit not active"
  systemctl --user disable --now minder-proxy.service 2>/dev/null \
    && say "unit stopped" || say "unit not active"
  rm -f "$HOME/.config/systemd/user/minder-doctor.timer"
  rm -f "$HOME/.config/systemd/user/minder-doctor.service"
  rm -f "$HOME/.config/systemd/user/minder-sink.service"
  rm -f "$HOME/.config/systemd/user/minder-web.service"
  rm -f "$HOME/.config/systemd/user/minder-proxy.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi

# 1b. dsh profile wiring: drop the bridge loader + the insert we appended
# (the profile's own cordis.yml is never touched).
if [ -f "$SHARE/dsh/minder-bridge-loader.mjs" ] || \
   [ -f "$HOME/.dsh/profiles/web/minder-bridge-loader.mjs" ]; then
  python3 - <<'PYEOF'
import pathlib
for name in ("web", "headless"):
    profile = pathlib.Path.home() / ".dsh" / "profiles" / name
    loader = profile / "minder-bridge-loader.mjs"
    if loader.exists():
        loader.unlink()
        print(f"removed {loader}")
    patch = profile / "cordis.patch.yml"
    if not patch.exists():
        continue
    text = patch.read_text()
    # The insert we appended is exactly: marker, config/configPath,
    # defaultTimeoutMs. Cut from the marker through that last line.
    marker = "- insert:\n    - name: ./minder-bridge-loader.mjs\n"
    if marker in text:
        start = text.index(marker)
        tail = text.index("defaultTimeoutMs:", start)
        end = text.index("\n", tail) + 1
        patch.write_text((text[:start] + text[end:]).replace("\n\n\n",
                                                             "\n\n"))
        print(f"removed the bridge insert from {patch}")
PYEOF
fi

# 2. dsh: remove additive provider + plugin entry (backup-verified patcher)
if [ -f "$HOME/.dsh/settings.yaml" ] && [ -f "$SHARE/dsh/dsh_install.py" ]; then
  python3 "$SHARE/dsh/dsh_install.py" remove --settings "$HOME/.dsh/settings.yaml" \
    && say "dsh settings.yaml restored (minder provider + plugin entry removed)" \
    || say "WARN: dsh settings restore failed — a .minder-*.bak backup exists alongside"
fi

# 3. zcode: remove minder hook entries
ZCODE_CONFIG="$HOME/.zcode/cli/config.json"
if [ -f "$ZCODE_CONFIG" ]; then
  cp -f "$ZCODE_CONFIG" "$ZCODE_CONFIG.minder-uninstall.bak"
  python3 - "$ZCODE_CONFIG" <<'PYEOF'
import json, sys
path = sys.argv[1]
cfg = json.loads(open(path).read())
events = cfg.get("hooks", {}).get("events", {})
for event in list(events):
    entries = [e for e in events[event]
               if not any("minder/hook.py" in " ".join(h.get("args", []))
                          for h in e.get("hooks", []))]
    if entries:
        events[event] = entries
    else:
        del events[event]
open(path, "w").write(json.dumps(cfg, indent=2) + "\n")
json.loads(open(path).read())
print("zcode hooks cleaned")
PYEOF
  say "zcode hooks removed (backup .minder-uninstall.bak)"
fi

# 4. remove code + config (keep state ledger for audit)
rm -rf "$SHARE"
rm -rf "$CONFIG"
say "removed $SHARE and $CONFIG"
say "kept $HOME/.local/state/minder (ledger history) — delete manually if desired"
say "uninstall complete"
