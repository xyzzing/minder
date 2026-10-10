"""The proxy's difficulty router, read read-only by doctor (issue #7).

`minder_op` never writes the proxy config or the ledger; it reports what
they say. A router that is enabled but has never applied a band is a
config finding the operator can act on, and this module owns the
vocabulary and the denominators for that judgement."""
import json
from pathlib import Path

# The proxy's own config file. The ledger path is derived from the state
# dir the same way the console derives it (env over `~/.local/state/minder`)
# so a systemd unit or a test can point doctor at a different store.
DEFAULT_PROXY_CONFIG = Path.home() / ".config" / "minder" / "minder.json"
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "minder"
LEDGER_NAME = "events.jsonl"
ROUTER_MODES = ("off", "shadow", "active", "lower", "laya")


def router_state(proxy_config, events_ledger):
    """(mode, applied, abstained) from the proxy config and the ledger.

    The ledger is a jsonl of `minder.log` records; only `type` and
    `router` are read. Counts are totals over the whole file, which is
    what the question needs: "has this router ever applied a band".
    Returns None for the mode when the config is missing or unreadable."""
    mode = None
    try:
        cfg = json.loads(Path(proxy_config).read_text())
        mode = str(cfg.get("difficulty_router") or "off").strip().lower()
    except (OSError, ValueError):
        mode = None
    applied = abstained = 0
    try:
        with open(events_ledger) as fh:
            for line in fh:
                if '"difficulty_' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                # `minder.log` writes the event name under "event"
                if rec.get("event") == "difficulty_routed":
                    applied += 1
                elif rec.get("event") == "difficulty_skipped":
                    abstained += 1
    except OSError:
        pass
    return mode, applied, abstained


def router_check(proxy_config, events_ledger):
    """doctor id `difficulty-router`: an enabled router that has never
    applied a band is a config finding, not a health finding (issue #7).

    The live measurement: 12,452 auto requests answered with thinking
    off, 872 `difficulty_skipped` all `reason=client_effort`, zero
    applied bands. The router was enabled and structurally inert, because
    a client-declared effort outranks it in every mode but `laya`. A
    denominator check (M2): `skipped=0, routed=0` means no eligible
    request, which is a different problem."""
    mode, applied, abstained = router_state(proxy_config, events_ledger)
    if mode in (None, "off"):
        return ("difficulty-router", "info",
                "difficulty router off (config: "
                + " | ".join(ROUTER_MODES) + ")")
    if applied:
        return ("difficulty-router", "ok",
                f"mode {mode}: {applied} band(s) applied, "
                f"{abstained} abstention(s)")
    if not abstained:
        return ("difficulty-router", "info",
                f"mode {mode}: no router-eligible request recorded yet")
    return ("difficulty-router", "warn",
            f"mode {mode}: {abstained} abstention(s), 0 applied - the "
            "router is outranked, not broken; a client-declared effort "
            "wins in every mode but `laya` - see docs/operator-cli.md")
