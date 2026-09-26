"""minder-op doctor — one read-only health report (operator usability).

Answers "is my install healthy, and why not" in a single screen:
memory DB, schema currency, flag values (typo detection against the
closed vocabularies), hook wiring, event staleness, loopback proxy
reachability (skippable with --no-probe; the probe is one GET to
127.0.0.1 only), benchmark suites, pinned baselines.

Verdict semantics: `fail` -> exit 1 (something is broken: DB missing,
corrupt, or an unknown flag value). `warn` -> still exit 0 but the
operator should look (missing wiring, stale events). `info` -> purely
contextual (proxy not running, no baseline pinned). Determinism:
`now=` injection, like the weekly summary.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from minder_op import benchmark as bench
from minder_op import queries
from minder_op.queries import DBError

FLAG_VOCAB = {
    "MINDER_ASSIST": ("off", "retrieve", "block_duplicate_skill",
                      "shadow_suggest", "decision_skill"),
    "MINDER_CLASSIFIER": ("shadow",),
    # "laya" is the production value (the real model) — omitting it made a
    # correct install report as a typo.
    "MINDER_DECISION": ("shadow", "fake", "laya"),
    # Both values enable the guard; "off" (or anything else) records
    # nothing. "block" additionally pre-empts at PreToolUse.
    "MINDER_SUCCESS_GUARD": ("advisory", "block"),
}
FRESH_SECS = 48 * 3600
STALE_SECS = 7 * 86400
DEFAULT_SHARE = Path.home() / ".local" / "share" / "minder"
DEFAULT_ZCODE_CONFIG = Path.home() / ".zcode" / "cli" / "config.json"


def _latest_schema():
    from memory import db as _db
    nums = []
    for path in _db.MIGRATIONS_DIR.glob("*.sql"):
        try:
            nums.append(int(path.name.split("_", 1)[0]))
        except ValueError:
            continue
    return max(nums) if nums else 0


def _age_text(secs):
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 48 * 3600:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def _parse_ts(text):
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _probe_proxy(port):
    """One GET to the loopback proxy. Never raises; no retries; this
    is the only network-adjacent thing doctor does, and it never
    leaves 127.0.0.1."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=1.5) as r:
            data = json.loads(r.read() or b"{}")
        aliases = data.get("data") or []
        return True, f"{len(aliases)} models/aliases answering on :{port}"
    except Exception as exc:  # noqa: BLE001 — refusal is a finding
        return False, f"not running on :{port} ({exc})"


def run_checks(db_path, probe=True, now=None):
    """Returns {"healthy": bool, "checks": [{id, status, detail}], ...}.
    Never raises on a missing/corrupt store — that's a `fail` finding,
    not a crash."""
    now = time.time() if now is None else now
    checks = []

    def add(cid, status, detail):
        checks.append({"id": cid, "status": status, "detail": detail})

    db_ok = True
    version = None
    try:
        version = queries.schema_version(db_path)
        add("db", "ok", f"memory db present ({db_path})")
    except DBError as exc:
        db_ok = False
        add("db", "fail", str(exc))

    if not db_ok:
        for cid in ("schema", "flags", "events"):
            add(cid, "info", f"skipped: {cid} needs a readable db")
    else:
        latest = _latest_schema()
        if version < latest:
            add("schema", "warn",
                f"schema v{version}, latest v{latest} — restart the "
                "hook (or run any minder runtime command) to migrate")
        else:
            add("schema", "ok", f"schema v{version} (latest v{latest})")

        parts = []
        bad = None
        for var, vocab in FLAG_VOCAB.items():
            value = os.environ.get(var)
            if not value:
                parts.append(f"{var}=(unset)")
            elif value in vocab:
                parts.append(f"{var}={value}")
            else:
                bad = (var, value, vocab)
        if bad:
            var, value, vocab = bad
            add("flags", "fail",
                f"{var}={value!r} is not one of {vocab} — fix the env "
                "or systemd unit")
        else:
            add("flags", "ok",
                ", ".join(parts) if any("=" in p and "(unset)" not in p
                                        for p in parts)
                else f"defaults ({', '.join(parts)})")

        last = queries.last_event_ts(db_path)
        stamp = _parse_ts(last)
        if stamp is None:
            add("events", "info", "no events recorded yet")
        else:
            age = max(now - stamp, 0)
            if age <= FRESH_SECS:
                add("events", "ok", f"last event {_age_text(age)} ago")
            elif age <= STALE_SECS:
                add("events", "info",
                    f"last event {_age_text(age)} ago")
            else:
                add("events", "warn",
                    f"last event {_age_text(age)} ago — hook silent "
                    "for over a week; is the wiring alive?")

    share = Path(os.environ.get("MINDER_SHARE") or DEFAULT_SHARE)
    zcode_cfg = Path(os.environ.get("MINDER_ZCODE_CONFIG")
                     or DEFAULT_ZCODE_CONFIG)
    share_ok = (share / "hook.py").is_file()
    try:
        hooked = zcode_cfg.is_file() and share_path_referenced(
            zcode_cfg, str(share))
    except OSError:
        hooked = False
    if share_ok and hooked:
        add("wiring", "ok", f"share installed ({share}); zcode hooks "
                            "reference it")
    elif share_ok:
        add("wiring", "warn",
            f"share installed ({share}) but {zcode_cfg} does not "
            "reference it — zcode hooks not installed?")
    else:
        add("wiring", "warn",
            f"hook share not found at {share} — run install.sh if "
            "this machine should have the live hook")

    if probe:
        try:
            port = int(os.environ.get("MINDER_PORT", "8390"))
        except ValueError:
            port = 8390
        reachable, detail = _probe_proxy(port)
        if reachable:
            add("proxy", "ok", detail)
        else:
            add("proxy", "info", detail)
    else:
        add("proxy", "info", "probe skipped (--no-probe)")

    # Capture path (added with the sink): a confined hook cannot write the
    # state dir, so without the sidecar every persistence write is dropped
    # silently. This is the check that would have caught three days of loss.
    try:
        from memory import sink as sink_mod
        sink_url = sink_mod.sink_url()
        sink_source = sink_mod.sink_source()
    except Exception:
        sink_mod, sink_url, sink_source = None, None, None
    if not sink_url:
        add("capture", "warn",
            "no sink configured (neither MINDER_SINK_URL nor the hook "
            "command in hooks.json) — dsh hooks run inside the file sandbox "
            "and cannot write the state dir; their records are dropped "
            "silently. Run install.sh.")
    elif not probe:
        add("capture", "info",
            f"sink probe skipped (--no-probe); {sink_url}")
    else:
        try:
            stats = sink_mod.stats(timeout_ms=500)
        except Exception:
            stats = None
        ops = (stats or {}).get("ops") or {}
        ok_ops = sum(int(v.get("ok", 0)) for v in ops.values())
        failed_ops = sum(int(v.get("failed", 0)) for v in ops.values())
        if not stats:
            add("capture", "fail",
                f"sink declared at {sink_url} ({sink_source}) but "
                "unreachable — hook writes are being dropped. Start it: "
                "systemctl --user start minder-sink.service")
        elif sink_source == "hooks.json" and ok_ops == 0:
            # Declared and alive, but the running dsh host loaded its hook
            # command before that URL was written: the one step operators
            # miss, so it is called out instead of reporting a bare "ok".
            add("capture", "warn",
                f"sink reachable at {sink_url} but no hook has ever called "
                "it — the dsh host reads hooks.json once at startup; "
                "restart the dsh web host to load it.")
        else:
            add("capture", "ok",
                f"sink reachable at {sink_url} via {sink_source} "
                f"({ok_ops} ok / {failed_ops} failed op(s))")

    try:
        from minder_op import capture as capture_mod
        report = capture_mod.build(db_path, now=now, window_hours=1)
        cov = report["coverage"]
        if cov["invocations"] and (cov["ratio"] or 0) < cov["min_ratio"]:
            add("coverage", "fail",
                f"hook coverage {cov['ratio']:.0%} in the last hour "
                f"({cov['persisted']} persisted / {cov['invocations']} "
                "invocations) — hooks fire but nothing is recorded")
        elif cov["invocations"]:
            add("coverage", "ok",
                f"hook coverage {cov['ratio']:.0%} "
                f"({cov['persisted']}/{cov['invocations']}) over the "
                "last hour")
        else:
            add("coverage", "info",
                "no dsh hook invocations in the last hour")
    except Exception as exc:  # never let a health check crash doctor
        add("coverage", "info", f"coverage unavailable: {exc}")

    suites = bench.list_suites()
    if not suites:
        add("benchmark", "info", "no benchmark suites found "
            "(benchmarks/ not present)")
    elif any(not row["status"].startswith("ok") for row in suites):
        bad = "; ".join(f"{row['suite_id']}: {row['status']}"
                        for row in suites
                        if not row["status"].startswith("ok"))
        add("benchmark", "warn", f"invalid manifest(s): {bad}")
    else:
        add("benchmark", "ok",
            ", ".join(f"{row['suite_id']} ({row['tasks']} tasks)"
                      for row in suites))

    baselines = bench.list_baselines()
    if not baselines:
        add("baseline", "info",
            "no baseline pinned — `minder-op benchmark baseline "
            "create --yes` enables compare verdicts")
    else:
        add("baseline", "ok",
            f"{len(baselines)} pinned: "
            + ", ".join(row["suite_id"] for row in baselines))

    return {
        "healthy": not any(c["status"] == "fail" for c in checks),
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def share_path_referenced(config_path, share_str):
    """True when the hooks config anywhere contains the share path —
    exactly what install.sh patches in (__MINDER_SHARE__)."""
    text = Path(config_path).read_text()
    return share_str in text


def render(report):
    for check in report["checks"]:
        print(f"{check['status']:<5} {check['id']:<10} {check['detail']}")
    verdict = "healthy" if report["healthy"] else "NOT healthy"
    print()
    print(f"verdict: {verdict} "
          f"({sum(c['status'] == 'fail' for c in report['checks'])} fail, "
          f"{sum(c['status'] == 'warn' for c in report['checks'])} warn, "
          f"{sum(c['status'] == 'info' for c in report['checks'])} info)")
    print("doctor is read-only; flags are env/systemd owned; the proxy "
          "probe only ever dials 127.0.0.1")
