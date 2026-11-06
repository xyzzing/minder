"""`minder-op dispatch` (issue #89, stage 1): the observe-mode intake.

status: what is configured and what the gate has said so far. ls: the
recorded triggers. poll: one pass of source -> gate -> store. The poll
write is append-only intake on the task-declare precedent, so no --yes.
Stage 2 adds the runner that turns admitted triggers into work orders;
until then auto mode admits and records, and nothing runs.
"""
import sys

from minder_op import dispatch_config
from minder_op import dispatch_gate
from minder_op import dispatch_source
from minder_op import dispatch_store
from minder_op import format as fmt

EXIT_OK = 0
EXIT_USAGE = 1


def cmd_dispatch(args, path):
    sub = getattr(args, "subcommand", None)
    if sub == "status":
        return _cmd_status(args, path)
    if sub == "ls":
        return _cmd_ls(args, path)
    if sub == "poll":
        return _cmd_poll(args, path)
    print(f"error: unknown dispatch subcommand: {sub}", file=sys.stderr)
    return EXIT_USAGE


def _config_path(args):
    return dispatch_config.config_path(getattr(args, "config", None))


def _cmd_status(args, path):
    try:
        cfg = dispatch_config.load_config(_config_path(args))
        cfg_note = str(_config_path(args))
    except dispatch_config.DispatchConfigError as exc:
        cfg = dispatch_config.default_config()
        cfg_note = f"{exc.code} ({exc})"
    counts = dispatch_store.counts(path)
    fmt.kv([
        ("config", cfg_note),
        ("repo", cfg.repo if cfg.repo else "none (nothing watched)"),
        ("accept_mode", cfg.accept_mode),
        ("allowed_senders",
         ", ".join(cfg.allowed_senders) if cfg.allowed_senders
         else "none (default deny)"),
        ("triggers_admitted", counts["admitted"]),
        ("triggers_would_accept", counts["would_accept"]),
        ("triggers_filtered", counts["filtered"]),
        ("newest_trigger_ts", dispatch_store.newest_ts(path) or "n/a"),
    ])
    return EXIT_OK


def _cmd_ls(args, path):
    rows = dispatch_store.triggers(path, limit=args.limit,
                                   decision=getattr(args, "decision", None))
    fmt.table(rows, [("ts", "ts"), ("trigger_id", "trigger_id"),
                     ("repo", "repo"), ("kind", "kind"),
                     ("sender", "sender"), ("class", "sender_class"),
                     ("decision", "decision"), ("reason", "reason")])
    return EXIT_OK


def poll_once(config, db_path, poll_fn=None):
    """One intake pass; the summary names every count it reports.

    Counts cover newly inserted rows only: a replayed poll that
    inserts nothing reports zeros, which is the restart-never-replays
    guarantee made visible.
    """
    poll_fn = poll_fn or dispatch_source.poll
    summary = {"polled": 0, "new": 0, "admitted": 0,
               "would_accept": 0, "filtered": 0, "error": None}
    polled, error = poll_fn(config.repo)
    if error is not None:
        summary["error"] = error
        return summary
    summary["polled"] = len(polled)
    for trigger in polled:
        result = dispatch_gate.decide(trigger, config)
        if dispatch_store.record_trigger(db_path, trigger, result):
            summary["new"] += 1
            summary[result["decision"]] += 1
    return summary


def _cmd_poll(args, path):
    try:
        cfg = dispatch_config.load_config(_config_path(args))
    except dispatch_config.DispatchConfigError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if cfg.repo is None:
        print("nothing watched: no repo configured (defaults deny)")
        return EXIT_OK
    summary = poll_once(cfg, path)
    if summary["error"] is not None:
        print(f"error: {summary['error']}", file=sys.stderr)
        return EXIT_USAGE
    fmt.kv([
        ("repo", cfg.repo),
        ("accept_mode", cfg.accept_mode),
        ("polled", summary["polled"]),
        ("new_triggers", summary["new"]),
        ("admitted", summary["admitted"]),
        ("would_accept", summary["would_accept"]),
        ("filtered", summary["filtered"]),
    ])
    return EXIT_OK
