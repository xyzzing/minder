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
# The reasons the proxy writes on an abstention (minder_decision/router.py
# `opinion`). Named here so the check can say which kind it is looking at
# rather than only how many there were.
ROUTER_SKIP_CLIENT = "client_effort"
ROUTER_SKIP_CONFIDENCE = "below_confidence"
ROUTER_SKIP_MALFORMED = "malformed_response"


def router_state(proxy_config, events_ledger):
    """(mode, applied, abstained, reasons) from config and ledger.

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
    reasons = {}
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
                    reason = str(rec.get("reason") or "unrecorded")
                    reasons[reason] = reasons.get(reason, 0) + 1
    except OSError:
        pass
    return mode, applied, abstained, reasons


def router_check(proxy_config, events_ledger):
    """doctor id `difficulty-router`: an enabled router that has never
    applied a band is a config finding, not a health finding (issue #7).

    The live measurement: 12,452 auto requests answered with thinking
    off, 872 `difficulty_skipped` all `reason=client_effort`, zero
    applied bands. The router was enabled and structurally inert, because
    a client-declared effort outranks it in every mode but `laya`. A
    denominator check (M2): `skipped=0, routed=0` means no eligible
    request, which is a different problem."""
    mode, applied, abstained, reasons = router_state(
        proxy_config, events_ledger)
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
            f"mode {mode}: {abstained} abstention(s), 0 applied - "
            f"{_reason_text(reasons)} - see docs/operator-cli.md")


def _reason_text(reasons):
    """What the abstentions actually were, which decides the repair.

    The #7 message blamed a client-declared effort, and that was right for
    the store it was written against. Flipping the mode to `laya` did not
    apply a band either, and the same message kept saying `outranked`
    while the ledger said `below_confidence` - the floor, working. Naming
    the cause from the ledger rather than from the mode is what keeps the
    line true after the config changes."""
    if not reasons:
        return "the reason was not recorded"
    order = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    listed = ", ".join(f"{count} {reason}" for reason, count in order)
    top, count = order[0]
    if top == ROUTER_SKIP_CLIENT:
        cause = ("outranked, not broken: a client-declared effort wins in "
                 "every mode but `laya`")
    elif top == ROUTER_SKIP_CONFIDENCE:
        cause = ("the confidence floor declining every prior, not an "
                 "outranked router; `laya_min_confidence` is the dial and "
                 "the prior is not separating the labels")
    elif top == ROUTER_SKIP_MALFORMED:
        cause = ("the decision worker answering unparseably, not a policy "
                 "declining; check the `laya-worker` line")
    else:
        cause = "see `minder-op events ls --type difficulty_skipped`"
    return f"causes: {listed}; {cause}"
