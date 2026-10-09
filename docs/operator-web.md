# minder operator web console

Local-only, read-only operator console. It reuses the `minder_op`
query/summary layer — no SQL or policy logic lives in the web package,
and there are no write endpoints.

## Two readers, one page (issue #12)

Every page leads with a plain-language answer line composed from the
page model (`minder_web/answers.py`), before any table: what this page
concludes, at what level, and which action to take first. The tables
below it are the evidence. Each page also carries a collapsed
**raw fields** tier (`templates/_raw.html`): the same rows with the
storage field names and the values exactly as stored, nothing rounded,
humanized or renamed, so a console screen can be compared line by line
against a CLI row. The tier reads the page model, never a database row,
so redaction stays a service-layer contract and a secret cannot reach it
from either tier.

The answer distinguishes three states an operator used to conflate:
capture **broken** (a configured sink that is not answering), capture
**quiet** (no session activity in the window to judge), and **no sink
configured** (nothing has been recorded yet). An empty store is never
rendered as a zero, and `n/a` never means zero.


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

`install.sh` ships a `minder-web.service` user unit (read-only,
loopback-only, `Restart=always`), so the console does not depend on
someone leaving a `nohup` process alive:

```bash
systemctl --user status  minder-web.service
systemctl --user restart minder-web.service        # after a code update
journalctl --user -u minder-web.service
minder-web --port 8765                             # or run it by hand
```

The unit is written but left stopped when the optional web extra is not
installed (`pip install --user -r requirements-web.txt`); `install.sh`
says so explicitly rather than failing silently.

Manual equivalent:

```bash
python3 -m minder_web --host 127.0.0.1 --port 8765 \
  --db ~/.local/state/minder/memory.sqlite
# stop: Ctrl-C
```

`--host` accepts only `127.0.0.1`, `::1`, or `localhost`; anything else
is refused with exit 1. This is **not** the sink: `sink.py` on 8392 is a
JSON-only sidecar for hook persistence and serves no UI (it says so if
you open it in a browser).

Check health:

```bash
curl -sS http://127.0.0.1:8765/healthz
```

## Pages

| Route | Shows |
|---|---|
| `/` | health, env flags (display only), **capture strip**, shared weekly summary (including the lesson-injection counts and the verified lessons that never fired, issue #13), operator focus |
| `/capture` | hook coverage (invocations vs persisted records), store freshness, sink reachability, sandbox-mode mix |
| `/sessions` | dsh sessions joined with their projection cache, workspace, tokens, sandbox mode and episode/event counts (`?q=`, `?sort=recent\|project\|tokens\|steps\|capture`) |
| `/sessions/{id}` | one session: projections, log-derived tool/hook counters and hook p50, episodes and observed events |
| `/scorecard` | the six improvement groups (capture, cost, failures, learning, context, hygiene) plus a 3-item focus list |
| `/events` | raw observed events with type/tool/failure-key/session filters and a staleness banner |
| `/episodes`, `/episodes/{id}` | episode list, timeline with redacted excerpts |
| `/lessons`, `/lessons/{id}` | verified / candidate / invalidated — badges visibly differ; default view is live verified only. A candidate came from a frontier consult labelled by its episode's verified close; adopting one is a CLI write (`lessons promote <id> --from-candidate`), not a console action. The detail page carries the lesson's injection ledger (issue #13): the decisions that put it in front of an agent, with the tier that matched and the assist path that carried it. A lesson with no rows says "never injected"; a store predating the ledger says "not available". It also carries the operator's own decision ledger (issue #14): every invalidate / reject / adopt with its closed reason code and note, newest first, and the list page shows the diagnosis code next to the status of an invalidated lesson. A store predating migration 016 says so rather than showing an empty table |
| `/gaps` | open skill gaps |
| `/consults`, `/consults/{id}` | frontier consult labels (`frontier_evals`, never the legacy 003 integer); hashes only, no raw prompt/response text |
| `/decisions` | shadow decision traces: model recommendation vs policy decision |
| `/benchmarks` | suite manifests + pinned baselines; verdicts come from `minder-op benchmark compare` |
| `/traces` | stored trace reviews: session, highest severity, finding counts, rubric and evaluator version |
| `/traces/{review_id}` | one review: findings (severity, rule, cited dsh event `seq`s, redacted excerpts, suggested fix), the summary counts, and the human feedback with its confirm/reject verdicts |
| `/engine` | the engine registry: active engine, upstream, systemd unit and unit health. The switch cell is a link, not a form |
| `/engine/switch?engine=NAME` | the confirm step for the console's one write path (issue #12): names the unit it will stop, the unit it will start and the upstream that will flip, and posts back to the same path |
| `/healthz` | JSON `{status, schema_version}` |

`/engine/switch` answers GET with that confirm view and switches only on
POST. It is a misclick guard, not a security control: the loopback bind
and the Host allowlist below are the boundary. The footer names the one
write path on every page.

Missing stores or optional tables render "not available", never a
500. All database text is redacted at the service layer and escaped by
the template engine. The overview uses the same
`minder_op.summary.build_weekly_summary()` object as the CLI
`weekly-summary`; it reports observed workflow evidence only — no
productivity claims.

`/traces` and `/traces/{id}` are the console's view of
`minder-op trace`; they mirror the CLI's *read* surface only. Confirming
a finding, recording feedback and converting a confirmed failure into a
regression case are writes, and the console has no write endpoint — a
reviewer in a browser can never change what the agent does next. When no
review has been stored yet the page says so and names the command that
creates one (`MINDER_TRACE_REVIEW=on` + `minder-op trace review`).

## Sessions, capture and identity

`/sessions` reads dsh's own session store read-only via
`minder_op/dsh_sessions.py`: `--dsh-home` / `MINDER_DSH_HOME` select it,
defaulting to `~/.dsh`. The session id shown is dsh's own
`session-<uuid>` — the same value the hook receives and
`episodes.task_id` stores — so episodes, events and logs join directly.
A missing or unrecognised surface degrades to "not available", never a
500.

The proxy ledger's `session:<md5>` key is an **agent identity**, not a
session (the harness sends no session header), so it is labelled that way
and never presented as a dsh session. Per-session token analytics come
from dsh's projection cache instead.

### Capture: how the hooks persist

dsh runs command hooks through its shell capability, which the session's
file sandbox confines to the workspace root (`workspace-write` by
default). A hook process therefore **cannot write
`~/.local/state/minder`** — it gets `EROFS` — and because every hook write
is fail-open, the loss is silent: the hooks keep exiting 0 while nothing
is stored. That is not hypothetical; it cost three days of capture, and
the Warden's escalation state lives in the same directory, so the ladder
also reset on every tool call.

`sink.py` (a loopback `systemd --user` unit, like the proxy) runs
unconfined and performs those writes on the hook's behalf over loopback.
The hook's client is active only when `MINDER_SINK_URL` is set — as
declared in the hook command in `hooks.json` — and with it unset the
hook behaves exactly as before. The sink also hosts the laya decision and
classifier models, so they are built once per session rather than once
per tool call (measured: 6.1 s → ~0.10 s per clean tool call).

Two consequences worth knowing:

- The bridge reads `hooks.json` **once at host start**, so a flag or URL
  change needs a dsh host restart to take effect.
- Because the policy pass runs inside the sink, the sink adopts the hook
  command's `MINDER_*` flags at startup (an explicit unit value still
  wins) and reports them in `GET /stats`; `minder-op capture` warns if the
  two ever disagree.

`minder-op capture` (`/capture`) exists because a broken capture path is
otherwise invisible. Coverage below 95 % means no other page can be
trusted. It compares hook invocations from dsh's own session logs against
what was persisted, reports per-store freshness, sink reachability and
the sandbox-mode mix, and counts only records attributable to a session
dsh knows — a probe cannot make a dead capture path look alive.

Ports: the console is **8765**; `sink.py` on **8392** is a JSON-only
sidecar and serves no UI (opening it in a browser returns a plain-text
pointer saying so).

The broader metric set — capture, cost, failures, learning, context,
hygiene — is `minder-op scorecard` and `/scorecard`. Three numbers
justify action on their own: coverage below 95 %, hook p50 above ~1 s,
and any repeat failure key.

