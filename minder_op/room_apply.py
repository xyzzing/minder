"""`minder-op room apply` (issue #46, room runtime PRD M2).

Imports a roomwise.room-policy/v1 file into minder's difficulty_bands.
The policy is evidence from elsewhere (Roomwise R2); minder only
translates, lints and writes:

- schema major must be roomwise.room-policy/v1 (other majors refused)
- model_fingerprint must match the live caps (fail-closed on mismatch
  or missing caps) unless --allow-fingerprint-mismatch (logged)
- rules matching contract task-difficulty/v1 become difficulty_bands
  overrides carrying effort, budget, max_tokens; reply_format is
  advisory and is reported, never applied
- room.contract names an output-contract template (issue #47) and may
  pin its payload sha256; it needs both the policy's
  requires_approval entry and the operator's
  --opt-in prompt_contract_for_auto, then must exist in the local
  template store (presets/contracts/) with a matching digest, and the
  shared band lint must fund its est_tokens - otherwise the apply is
  refused or the rule skipped, never partially applied
- default-changing rules (thinking off in automatic mode) need their
  --opt-in, mirroring the policy's requires_approval list
- the shared band lint refuses any rule whose budget plus the answer
  reserve cannot fit under its own max_tokens ceiling
- dry run by default; --write backs up the config, writes, verifies by
  re-reading, and logs room_policy_applied with source_report_sha256
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import minder
from minder_decision.difficulty import LABELS
from minder_op.room_checks import reserve_violations

POLICY_SCHEMA = "roomwise.room-policy/v1"
CONTRACT = "task-difficulty/v1"
# the one v1 approval minder understands: thinking off in automatic
# mode flips the 2026-10-07 policy that every automatic task thinks at
# least low, so it is never silent
APPROVAL_EFFORT_OFF = "effort_off_for_auto"
# the second: injecting an output-contract template rewrites the
# prompt (a system hint + a suffix on the last user message), which
# v1 never did. Both the policy (requires_approval) and the operator
# (--opt-in) must ask for it; non-streaming automatic requests only.
APPROVAL_PROMPT_CONTRACT = "prompt_contract_for_auto"


class RoomApplyError(Exception):
    """A refusal: the policy is not applied, the config is untouched."""


def load_policy(path):
    try:
        policy = json.loads(open(path, "r", encoding="utf-8").read())
    except OSError as exc:
        raise RoomApplyError(f"cannot read policy: {exc}") from exc
    except ValueError as exc:
        raise RoomApplyError(f"policy is not JSON: {exc}") from exc
    if not isinstance(policy, dict) \
            or policy.get("schema") != POLICY_SCHEMA:
        seen = policy.get("schema") if isinstance(policy, dict) \
            else type(policy).__name__
        raise RoomApplyError(
            f"refused: policy schema must be {POLICY_SCHEMA}, got "
            f"{seen!r}")
    if not isinstance(policy.get("rules"), list) or not policy["rules"]:
        raise RoomApplyError("refused: policy carries no rules")
    return policy


def check_fingerprint(policy, caps):
    """None when the fingerprint matches; a mismatch description
    otherwise. Fail-closed: missing caps cannot verify anything."""
    want = policy.get("model_fingerprint") or {}
    if not isinstance(want, dict) or not want.get("model_id"):
        raise RoomApplyError("refused: policy has no model_fingerprint")
    if not isinstance(caps, dict):
        return "no caps file - the live model cannot be verified"
    live = caps.get("fingerprint") or {}
    if not live.get("model_id"):
        return "caps carry no model id - the live model cannot be verified"
    if want.get("model_id") != live.get("model_id"):
        return (f"model mismatch: policy measured "
                f"{want.get('model_id')!r}, live is {live.get('model_id')!r}"
                " (other fingerprint fields are not comparable here)")
    return None


def translate(policy, opt_ins=()):
    """Rules -> (overrides, skipped, reported). Pure: no I/O.

    skipped: (label, why) for rules not applied.
    reported: human lines for advisory fields and evidence.
    """
    opt_ins = set(opt_ins)
    approvals = set(policy.get("requires_approval") or [])
    overrides, skipped, reported = {}, [], []
    for rule in policy["rules"]:
        match = rule.get("match") or {}
        if match.get("contract") != CONTRACT:
            skipped.append((match.get("label"),
                            f"unknown contract {match.get('contract')!r}"))
            continue
        label = match.get("label")
        if label not in LABELS:
            skipped.append((label, "not a task-difficulty/v1 label"))
            continue
        room_cfg = rule.get("room") or {}
        if room_cfg.get("effort") in ("off", "minimal") \
                and APPROVAL_EFFORT_OFF in approvals \
                and APPROVAL_EFFORT_OFF not in opt_ins:
            skipped.append((label,
                            f"needs --opt-in {APPROVAL_EFFORT_OFF} "
                            "(thinking off in automatic mode)"))
            continue
        band = {key: room_cfg.get(key)
                for key in ("effort", "budget", "max_tokens")}
        spec = room_cfg.get("contract")
        if isinstance(spec, dict) and spec.get("name"):
            if APPROVAL_PROMPT_CONTRACT not in opt_ins:
                skipped.append((label,
                                f"needs --opt-in {APPROVAL_PROMPT_CONTRACT} "
                                "(minder rewriting prompts)"))
            elif APPROVAL_PROMPT_CONTRACT not in approvals:
                skipped.append((label, "room.contract present but the "
                                       "policy does not list "
                                       f"{APPROVAL_PROMPT_CONTRACT} in "
                                       "requires_approval"))
            else:
                band["contract"] = str(spec["name"])
                sha = spec.get("sha256")
                if isinstance(sha, str) and sha:
                    band["contract_sha256"] = sha
        if room_cfg.get("reply_format") == "work":
            reported.append(f"{label}: reply_format=work is advisory "
                            "(minder injects only opt-in contract "
                            "scaffolding, never free-form prompt "
                            "rewrites)")
        overrides[label] = band
        evidence = rule.get("evidence") or {}
        if evidence.get("families"):
            reported.append(
                f"{label}: evidence from "
                + ", ".join(evidence["families"]))
    return overrides, skipped, reported


def load_templates():
    """(templates by template_id, problems). Share-dir templates are
    the shipped set; config-dir templates override them, mirroring the
    proxy's preset precedence."""
    from minder_decision.output_contracts import load_contracts
    share = Path(minder.__file__).parent / "presets" / "contracts"
    config = minder.CFG_PATH.parent / "presets" / "contracts"
    return load_contracts([share, config])


def _verify_contract_rules(overrides, templates):
    """Refusal message when a translated band names a contract template
    the local store cannot honor (unknown id or digest mismatch)."""
    from minder_decision.output_contracts import payload_digest
    for label in sorted(overrides):
        name = overrides[label].get("contract")
        if not name:
            continue
        template = templates.get(name)
        if template is None:
            return (f"refused: rule for {label} names contract template "
                    f"{name!r} which is not in the local store "
                    "(presets/contracts/)")
        expected = overrides[label].get("contract_sha256")
        actual = payload_digest(template)
        if expected and expected != actual:
            return (f"refused: contract {name!r} payload digest "
                    f"mismatch for {label}: policy pins "
                    f"{str(expected)[:12]}…, store has "
                    f"{actual[:12]}… (re-export the template or "
                    "re-pin the policy)")
    return None


def apply_policy(policy_path, opt_ins=(), write=False,
                 allow_mismatch=False, config_path=None, caps=None,
                 templates=None):
    """Returns (exit_code, message). Refusals leave the config alone."""
    config_path = config_path or \
        os.environ.get("MINDER_CONFIG") or str(minder.CFG_PATH)
    try:
        policy = load_policy(policy_path)
    except RoomApplyError as exc:
        return 1, str(exc)
    try:
        if caps is None:
            import adapter
            caps = adapter.load_caps(minder.caps_path())
    except Exception:  # noqa: BLE001 - missing caps is a finding below
        caps = None
    mismatch = check_fingerprint(policy, caps)
    if mismatch:
        if not allow_mismatch:
            return 1, f"refused: fingerprint ({mismatch}); re-run with " \
                      "--allow-fingerprint-mismatch to override (logged)"
        minder.log("room-apply", "room_policy_fingerprint_mismatch",
                   detail=mismatch)
    overrides, skipped, reported = translate(policy, opt_ins)
    if templates is None:
        templates, template_problems = load_templates()
    else:
        template_problems = []
    reported.extend(f"contract template problem: {p}"
                    for p in template_problems)
    refusal = _verify_contract_rules(overrides, templates)
    if refusal:
        return 1, refusal
    est_map = {tid: t["est_tokens"] for tid, t in templates.items()}
    breaking = reserve_violations(
        overrides, {"contract_est_tokens": est_map})
    if breaking:
        return 1, ("refused: rule(s) for " + ", ".join(breaking) +
                   " cannot fund budget + answer reserve under their "
                   "own max_tokens ceiling")
    try:
        current = json.loads(open(config_path, "r",
                                  encoding="utf-8").read())
        if not isinstance(current, dict):
            raise ValueError("config is not an object")
    except (OSError, ValueError):
        current = {}
    old_bands = current.get("difficulty_bands") or {}
    lines = ["difficulty_bands changes (dry run"
             + (", --write to apply" if not write else ", writing") + "):"]
    for label in sorted(set(old_bands) | set(overrides)):
        if old_bands.get(label) != overrides.get(label):
            lines.append(f"  {label}: {old_bands.get(label)} -> "
                         f"{overrides.get(label)}")
    for label, why in skipped:
        lines.append(f"  skipped {label}: {why}")
    lines.extend(f"  {note}" for note in reported)
    if not write:
        return 0, "\n".join(lines)
    backup = Path(config_path).parent / \
        (Path(config_path).name + ".bak-room")
    backup.write_text(open(config_path, "r", encoding="utf-8").read())
    merged = {**current, "difficulty_bands": overrides}
    tmp = Path(config_path).parent / (Path(config_path).name + ".tmp-room")
    tmp.write_text(json.dumps(merged, indent=2))
    os.replace(tmp, config_path)
    try:
        verify = json.loads(open(config_path, "r",
                                 encoding="utf-8").read())
        if verify.get("difficulty_bands") != overrides:
            raise ValueError("difficulty_bands verify failed")
    except (OSError, ValueError) as exc:
        os.replace(backup, config_path)  # restore
        return 1, f"config verify failed, restored backup: {exc}"
    minder.log("room-apply", "room_policy_applied",
               source_report_sha256=policy.get("source_report_sha256"),
               rules=len(overrides), skipped=len(skipped),
               fingerprint_mismatch=bool(mismatch))
    lines.append(f"  written to {config_path} (backup at {backup})")
    return 0, "\n".join(lines)


def cmd(argv):
    """`minder-op room apply` entry; argparse-owned by cli.py."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="minder-op room apply",
        description="Apply a roomwise.room-policy/v1 file to "
                    "difficulty_bands (dry run by default).")
    parser.add_argument("--policy", required=True,
                        help="path to the room-policy JSON")
    parser.add_argument("--opt-in", action="append", default=[],
                        choices=[APPROVAL_EFFORT_OFF,
                                 APPROVAL_PROMPT_CONTRACT],
                        help="approve a default-changing rule "
                             "(repeatable)")
    parser.add_argument("--write", action="store_true",
                        help="write the config (backup + verify); "
                             "default is a dry run")
    parser.add_argument("--allow-fingerprint-mismatch", action="store_true",
                        help="apply despite a model fingerprint "
                             "mismatch (logged)")
    args = parser.parse_args(argv)
    code, message = apply_policy(args.policy, opt_ins=args.opt_in,
                                 write=args.write,
                                 allow_mismatch=
                                 args.allow_fingerprint_mismatch)
    print(message, file=sys.stdout if code == 0 else sys.stderr)
    return code
