"""Output-contract templates (issue #47).

A contract is a per-family working-memory shape: an optional system
hint plus a suffix appended to the last user message ("write brief
working inside <work></work>, then answer with one JSON object shaped
..."). Templates are data records sharing repo-agent's RA-016
vocabulary — the status ladder, producer_profile, and model_specific
applicability — so the two stores can bridge without sharing code
(minder holds runtime_deps at 0, so no package import is possible).

Distinct from this package's `contracts.py`, which validates laya's
decision responses; this module only handles prompt-format templates.

Pure module: the loader takes explicit directories and everything else
is a function over template dicts. The proxy and `room apply` own all
logging; application is opt-in, digest-pinned, non-streaming only, and
every refusal comes back as a reason string the caller can log — no
silent skips.
"""
from __future__ import annotations

import hashlib
import json

# Shared with repo-agent RA-016 (its docs/prd-v1.3.md record schema);
# renaming locally would fork the vocabulary the bridging depends on.
STATUS_LADDER = ("candidate", "evidence_checked", "locally_verified",
                 "reusable")
# A contract reaches prompts only once evidence backs it — the same
# floor RA-019 retrieval applies to fact records.
APPLY_FLOOR = "evidence_checked"

# A suffix may carry this placeholder for the per-case answer schema;
# the eval driver fills it, the proxy cannot and must refuse to try.
ANSWER_SHAPE_PLACEHOLDER = "{answer_shape}"


def _as_int(value):
    """int(value) for real ints and int-shaped values, else None."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def status_rank(status):
    return STATUS_LADDER.index(status) if status in STATUS_LADDER else None


def payload_digest(record):
    """sha256 over the canonical JSON of the injected payload only —
    the digest room-policy files pin and templates must match."""
    canonical = json.dumps(record.get("payload") or {}, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_template(record):
    """All schema problems with the template, or an empty list."""
    errors = []
    if not isinstance(record, dict):
        return ["template is not a JSON object"]
    if not isinstance(record.get("template_id"), str) \
            or not record["template_id"]:
        errors.append("template_id must be a non-empty string")
    if record.get("status") not in STATUS_LADDER:
        errors.append(f"status must be one of {STATUS_LADDER}")
    if not isinstance(record.get("producer_profile"), str) \
            or not record["producer_profile"]:
        errors.append("producer_profile must be a non-empty string")
    applicability = record.get("applicability")
    if not isinstance(applicability, dict) \
            or not isinstance(applicability.get("model_specific"), bool):
        errors.append("applicability.model_specific must be a bool")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
    else:
        if not isinstance(payload.get("system_hint"), str):
            errors.append("payload.system_hint must be a string")
        if not isinstance(payload.get("suffix_template"), str) \
                or not payload["suffix_template"]:
            errors.append(
                "payload.suffix_template must be a non-empty string")
    est = record.get("est_tokens")
    if not isinstance(est, int) or isinstance(est, bool) or est < 0:
        errors.append("est_tokens must be a non-negative integer")
    return errors


def load_contracts(dirs):
    """Load templates from `dirs`, in precedence order — a later
    directory wins a template_id collision (config over share).
    Returns (contracts by template_id, problems); a malformed file is
    a named problem, never a crash and never a silent skip."""
    templates, problems = {}, []
    for directory in dirs:
        if not hasattr(directory, "glob"):
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                errors = validate_template(record)
            except (OSError, ValueError) as exc:
                problems.append(f"{path.name}: {exc}")
                continue
            if errors:
                problems.append(f"{path.name}: {'; '.join(errors)}")
                continue
            templates[record["template_id"]] = record
    return templates, problems


def resolve_contract(req, band, store, *, opt_in):
    """(template, None) when the request should carry the band's
    contract, (None, reason) for every refusal, (None, None) when the
    band schedules no contract at all. Digest-pinned and status-floored;
    streaming requests never get one."""
    name = band.get("contract") if isinstance(band, dict) else None
    if not name:
        return None, None
    if not opt_in:
        return None, "no-opt-in"
    req = req or {}
    if req.get("stream"):
        return None, "streaming"
    record = store.get(name)
    if record is None:
        return None, f"unknown-contract:{name}"
    errors = validate_template(record)
    if errors:
        return None, "invalid-template:" + "; ".join(errors)
    rank = status_rank(record.get("status"))
    if rank is None or rank < STATUS_LADDER.index(APPLY_FLOOR):
        return None, f"status-floor:{record.get('status')}"
    expected = band.get("contract_sha256")
    if expected and expected != payload_digest(record):
        return None, "digest-mismatch"
    if ANSWER_SHAPE_PLACEHOLDER in \
            record["payload"].get("suffix_template", ""):
        return None, "schema-placeholder"
    return record, None


def apply_contract(req, record):
    """Append the suffix to the last user message and land the system
    hint (appended to an existing system message, inserted otherwise).
    Mutates req in place; returns a summary the caller logs. A request
    whose content is not plain strings is refused unmutated — partial
    rewrites of structured content are how contracts get lost."""
    payload = record.get("payload") or {}
    suffix = payload.get("suffix_template", "")
    hint = payload.get("system_hint", "")
    messages = req.get("messages")
    if not isinstance(messages, list) or not messages:
        return {"applied": False, "why": "no-messages"}
    last = messages[-1]
    if not isinstance(last, dict) or not isinstance(last.get("content"),
                                                    str):
        return {"applied": False, "why": "content-shape"}
    chars = 0
    if suffix:
        last["content"] = last["content"] + "\n\n" + suffix
        chars += len(suffix)
    if hint:
        if messages[0].get("role") == "system" \
                and isinstance(messages[0].get("content"), str):
            messages[0]["content"] += "\n\n" + hint
        else:
            messages.insert(0, {"role": "system", "content": hint})
        chars += len(hint)
    return {"applied": True, "contract": record.get("template_id"),
            "chars_added": chars,
            "est_tokens": _as_int(record.get("est_tokens")) or 0}


def contract_room_fits(band, est_tokens, cfg=None):
    """Whether the band can fund thinking budget + answer reserve +
    the contract's working tokens under its own max_tokens ceiling."""
    cfg = cfg or {}
    budget = _as_int(band.get("budget"))
    ceiling = _as_int(band.get("max_tokens"))
    est = _as_int(est_tokens)
    if budget is None or ceiling is None or est is None:
        return False
    from .room import ANSWER_RESERVE_TOKENS, ROOM_RESERVE_FLOOR
    reserve = max(ROOM_RESERVE_FLOOR,
                  _as_int(cfg.get("answer_reserve_tokens"))
                  or ANSWER_RESERVE_TOKENS)
    return budget + reserve + est <= ceiling
