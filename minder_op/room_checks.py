"""Doctor checks for the room runtime (issue #45, PRD M1 §5.5).

Three checks, each read-only and each honest about not-knowing:
1. room_budget_enforced - caps must SAY budgets are enforced (a
   measured probe, not kwarg acceptance); false/null is a warn with a
   manual step, because every budget minder sends is advisory there.
2. room_band_reserve - every configured band's budget plus the answer
   reserve must fit under its own max_tokens ceiling (defaults pass;
   a breaking band is named).
3. room_starved_share - over the last 24 h of room_outcome events, a
   starved share above 2% is a warn; no events is n/a, not zero.
"""
from __future__ import annotations

import json
import time

from minder_decision.difficulty import DEFAULT_BANDS
from minder_decision.room import ANSWER_RESERVE_TOKENS, ROOM_RESERVE_FLOOR

STARVED_WARN_SHARE = 0.02
ROOM_WINDOW_S = 24 * 3600

_ENFORCED_STEP = ("thinking budgets are advisory on this engine: every "
                  "budget minder sends can be overrun - re-run the "
                  "installer CAP against an engine that enforces them, "
                  "or accept advisory budgets knowingly")


def reserve_violations(overrides, cfg=None):
    """Labels whose band (defaults merged with overrides) cannot fund
    its budget, the answer reserve, and — when the band schedules an
    output contract (issue #47) — that contract's est_tokens under its
    own max_tokens ceiling. A contract whose est_tokens the caller did
    not supply breaks the band: an unverifiable room is treated as no
    room. Shared by the doctor check and `room apply`'s pre-write
    lint, so the two can never disagree about what breaks."""
    cfg = cfg or {}
    bands = dict(DEFAULT_BANDS)
    for label, override in (overrides or {}).items():
        if isinstance(override, dict):
            bands[label] = {**bands.get(label, {}), **override}
    reserve = cfg.get("answer_reserve_tokens", ANSWER_RESERVE_TOKENS)
    reserve = max(ROOM_RESERVE_FLOOR, reserve)
    est_by_name = cfg.get("contract_est_tokens") or {}
    breaking = []
    for label, band in sorted(bands.items()):
        budget, ceiling = band.get("budget"), band.get("max_tokens")
        if not (isinstance(budget, int) and isinstance(ceiling, int)):
            continue
        name = band.get("contract")
        if not name:
            if budget + reserve > ceiling:
                breaking.append(label)
            continue
        est = est_by_name.get(name)
        if not isinstance(est, int) or isinstance(est, bool):
            breaking.append(label)
        elif budget + reserve + est > ceiling:
            breaking.append(label)
    return breaking


def room_checks(cfg=None, caps=None, events_ledger=None, now=None):
    """Returns a list of doctor-shaped checks. Never raises on missing
    inputs - a missing caps file is itself the finding."""
    cfg = cfg or {}
    caps = caps or {}
    now = time.time() if now is None else now
    checks = []

    enforced = ((caps.get("thinking") or {})
                .get("thinking_budget_enforced"))
    if enforced is True:
        checks.append({"id": "room_budget_enforced", "status": "ok",
                       "detail": "thinking budgets measured enforced "
                                 "(CAP probe)"})
    else:
        checks.append({"id": "room_budget_enforced", "status": "warn",
                       "detail": "thinking_budget_enforced is "
                                 f"{enforced!r}; {_ENFORCED_STEP}"})

    breaking = reserve_violations(cfg.get("difficulty_bands"), cfg)
    reserve = max(ROOM_RESERVE_FLOOR,
                  cfg.get("answer_reserve_tokens", ANSWER_RESERVE_TOKENS))
    if breaking:
        checks.append({"id": "room_band_reserve", "status": "warn",
                       "detail": "band(s) " + ", ".join(breaking) +
                                 f" budget + reserve ({reserve}) exceed "
                                 "their own max_tokens ceiling"})
    else:
        checks.append({"id": "room_band_reserve", "status": "ok",
                       "detail": f"all bands leave a {reserve}-token "
                                 "answer reserve"})

    total = starved = 0
    if events_ledger is not None:
        try:
            with open(events_ledger, "r", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("event") != "room_outcome":
                        continue
                    if now - float(row.get("ts") or 0) > ROOM_WINDOW_S:
                        continue
                    total += 1
                    starved += row.get("outcome") == "starved"
        except OSError:
            total = 0
    if not total:
        checks.append({"id": "room_starved_share", "status": "info",
                       "detail": "n/a: no room_outcome events in the "
                                 "last 24 h"})
    elif starved / total > STARVED_WARN_SHARE:
        checks.append({"id": "room_starved_share", "status": "warn",
                       "detail": f"{starved} of {total} routed responses "
                                 f"starved ({starved / total:.1%} > "
                                 f"{STARVED_WARN_SHARE:.0%}) - raise the "
                                 "band budget or its max_tokens ceiling"})
    else:
        checks.append({"id": "room_starved_share", "status": "ok",
                       "detail": f"{starved} of {total} routed responses "
                                 "starved in the last 24 h"})
    return checks
