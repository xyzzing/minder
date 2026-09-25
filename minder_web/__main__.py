"""minder-web entrypoint: localhost only, read only.

The bind guard is deliberate and hard: the console has no auth by
design, so a non-loopback host is refused outright (no unsafe flag
exists). Docs: docs/operator-web.md.
"""
import argparse
import os
import sys

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="minder-web",
        description="minder operator console — localhost, read-only")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address; loopback only "
                             "(default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db",
                        help="memory sqlite path (default: the "
                             "standard minder state location)")
    parser.add_argument("--dsh-home",
                        help="dsh home to read sessions/projections from "
                             "(default: $MINDER_DSH_HOME or ~/.dsh)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        print(f"error: refusing to bind {args.host!r}: the console is "
              "localhost-only; beyond loopback it would need auth and "
              "CSRF, which 8E deliberately does not ship",
              file=sys.stderr)
        return 1
    if args.db:
        os.environ["MINDER_WEB_DB"] = args.db
    if args.dsh_home:
        os.environ["MINDER_DSH_HOME"] = args.dsh_home
    import uvicorn
    from minder_web.app import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
