#!/usr/bin/env bash
# minder uninstall — reverses install.sh additively; ledger state is kept.
set -uo pipefail

SHARE="$HOME/.local/share/minder"
CONFIG="$HOME/.config/minder"
say() { printf '[minder] %s\n' "$*"; }

# 1. stop + remove the unit (never touch the real user session in scratch-HOME tests)
if [ "${MINDER_NO_SYSTEMD:-0}" = "1" ]; then
  say "systemd skipped (MINDER_NO_SYSTEMD=1)"
else
  systemctl --user disable --now minder-proxy.service 2>/dev/null \
    && say "unit stopped" || say "unit not active"
  rm -f "$HOME/.config/systemd/user/minder-proxy.service"
  systemctl --user daemon-reload 2>/dev/null || true
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
