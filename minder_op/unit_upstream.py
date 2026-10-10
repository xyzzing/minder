"""The upstream the proxy unit will actually dial (issue #33).

`minder_op` never writes a unit or a drop-in; it reports what systemd
resolves. The registry's upstream is a claim in minder.json; the proxy
unit's Environment is what the proxy connects to, and systemd applies
drop-ins after the base unit, so an installer that rewrites the base unit
changes nothing while a drop-in stands. The live case: the installer's
default 8080 in the base unit, an operator drop-in declaring 8081, the
proxy dialling 8081, and nothing anywhere naming the divergence - so CAP
ran against the installer's value and model_caps.json described a model
the proxy never dials.

Kept out of `minder_op/engines.py` for C2: that module owns the
switching lifecycle (stop, start, health, rollback), and this owns
reading."""
from minder_op.engines import _default_run


def parse_unit_environment(text):
    """The last MINDER_UPSTREAM in a systemd Environment assignment list.

    systemd prints one `Environment=` line per unit with every assignment
    merged, drop-ins last, and a later assignment of the same key wins. So
    the last one is the effective value, and the first is what the base
    unit claimed before any drop-in overrode it."""
    values = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        for item in line[len("Environment="):].split():
            key, sep, value = item.partition("=")
            if sep and key == "MINDER_UPSTREAM":
                values.append(value.strip('"\''))
    if not values:
        return None
    return values[-1]


def base_unit_upstream(unit, run=None):
    """What the base unit file itself declares, ignoring drop-ins."""
    run = run or _default_run
    rc, out, _err = run(["systemctl", "--user", "cat", "--no-pager", unit])
    if rc != 0:
        return None
    # `cat` prints `# /path/to/unit` headers; a drop-in is in a `.d/` dir.
    in_base = True
    chunks = []
    for line in out.splitlines():
        if line.startswith("# "):
            in_base = "/.d/" not in line
        if in_base:
            chunks.append(line)
    return parse_unit_environment("\n".join(chunks))


def check(unit, configured, unit_state, run=None):
    """doctor id `engine-upstream`: the upstream the proxy unit actually
    dials, versus the one the registry says the active engine uses.

    The registry value comes from minder.json; the proxy unit's
    Environment is what the proxy connects to. A drop-in is applied after
    the base unit, so an installer that rewrites the base unit changes
    nothing while a drop-in stands, and neither file alone shows the
    effective value. When they disagree the operator is running an engine
    nothing reports, and model_caps.json describes the wrong model.

    `unit` is the proxy unit, not the engine's: the engine unit is a
    server that listens on a port and declares no upstream of its own."""
    if not unit:
        return None
    run = run or _default_run
    rc, out, err = run(["systemctl", "--user", "show", unit,
                        "-p", "Environment"])
    if rc != 0:
        return ("engine-upstream", "info",
                f"could not read {unit} Environment "
                f"({err.strip() or f'rc={rc}'}); the registry value "
                f"{configured} is unverified")
    effective = parse_unit_environment(out)
    if effective is None:
        return ("engine-upstream", "info",
                f"no MINDER_UPSTREAM in {unit} Environment "
                f"(unit {unit_state}); the proxy takes it from its own "
                f"environment, registry value {configured} unverified")
    if effective == configured:
        return ("engine-upstream", "ok",
                f"{unit} dials {effective}, matching the active engine")
    base = base_unit_upstream(unit, run)
    masked = ""
    if base and base != effective:
        masked = (f" - the base unit says {base} and a drop-in masks it; "
                  "an installer rewriting the base unit changes nothing")
    return ("engine-upstream", "warn",
            f"{unit} dials {effective}, not the active engine's "
            f"{configured}{masked} - see docs/operator-cli.md")


