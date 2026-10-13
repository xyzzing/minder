"""`minder-op engine status` and `engine switch`.

Moved out of cli.py under C2 (the file had crossed 400 lines again); the
behaviour is unchanged: the registry is env/systemd owned, so a
switch writes one config key through the sanctioned backup/verify
path and needs --yes. Exit codes: 0 ok, 1 usage or refused.
"""
EXIT_OK = 0
EXIT_USAGE = 1

def cmd_engine_status(args):
    from minder_op import engines
    rows = engines.status()
    if not rows:
        print("no engine registry configured")
        return EXIT_OK
    for r in rows:
        mark = "*" if r["active"] else " "
        unit = r["unit"] or "n/a"
        state = r["unit_state"] or "n/a"
        health = "healthy" if r["healthy"] else "unhealthy"
        print(f"{mark} {r['name']:<12} {r['upstream']}  unit={unit} "
              f"({state})  {health}")
    print("\n* = active engine; switch with: minder-op engine switch NAME --yes")
    return EXIT_OK


def cmd_engine_switch(args):
    from minder_op import engines
    if not args.yes:
        print("refusing to restart engine units without --yes")
        return EXIT_USAGE
    result = engines.switch(args.name)
    if result["switched"]:
        print(f"switched {result['from']} -> {result['engine']} "
              f"(target healthy, config updated; backup at "
              f"minder.json.bak)")
    else:
        print(f"already on '{result['engine']}'; nothing to do")
    return EXIT_OK
