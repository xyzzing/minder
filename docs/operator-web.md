# minder operator web console

Local-only, read-only operator console. It reuses the `minder_op`
query/summary layer — no SQL or policy logic lives in the web package,
and there are no write endpoints.

## Deployment boundary

```text
127.0.0.1 only — the launcher refuses any other bind
read-only by default (GET routes only)
no login, because it is localhost-only
no live Jev call, no policy/flag writes
```

This is **not an auth boundary**. Do not tunnel, proxy, or forward it
beyond the machine. If a non-loopback bind is ever needed, auth and
CSRF must land first, before the server starts.

## Install

The web stack is an optional extra; nothing in the runtime
(`hook.py`, `proxy.py`, `minder_op`) imports it:

```bash
pip install --user -r requirements-web.txt # fastapi, uvicorn, jinja2, httpx
```

## Start / stop

```bash
python3 -m minder_web --host 127.0.0.1 --port 8765 \
--db ~/.local/state/minder/memory.sqlite
# stop: Ctrl-C
```

`--host` accepts only `127.0.0.1`, `::1`, or `localhost`; anything else
is refused with exit 1. No systemd unit ships until the console has
been used manually and proven useful.

Check health:

```bash
curl -sS http://127.0.0.1:8765/healthz
```

## Pages

| Route | Shows |
|---|---|
| `/` | health, env flags (display only), shared weekly summary, operator focus |
| `/episodes`, `/episodes/{id}` | episode list, timeline with redacted excerpts |
| `/lessons`, `/lessons/{id}` | verified / candidate / invalidated — badges visibly differ; default view is live verified only |
| `/gaps` | open skill gaps |
| `/consults`, `/consults/{id}` | frontier consult labels (`frontier_evals`, never the legacy 003 integer); hashes only, no raw prompt/response text |
| `/decisions` | shadow decision traces: model recommendation vs policy decision |
| `/benchmarks` | suite manifests + pinned baselines; verdicts come from `minder-op benchmark compare` |
| `/healthz` | JSON `{status, schema_version}` |

Missing stores or optional tables render "not available", never a
500. All database text is redacted at the service layer and escaped by
the template engine. The overview uses the same
`minder_op.summary.build_weekly_summary()` object as the CLI
`weekly-summary`; it reports observed workflow evidence only — no
productivity claims.
