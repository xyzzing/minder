"""Dispatch config (issue #89, stage 1): what the poller may watch and
who may start work. Poll cadence is the systemd timer's OnCalendar,
not a config knob: a field nothing reads would be ignored silently. Defaults deny everything - no repo, no allowed
senders, observe mode - so an unconfigured deployment watches nothing
and dispatches nothing. A malformed file is a named error, never a
silent fallback to permissive values (I1).
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import minder

CONFIG_NAME = "dispatch.json"
ACCEPT_MODE_OBSERVE = "observe"
ACCEPT_MODE_AUTO = "auto"
ACCEPT_MODES = (ACCEPT_MODE_OBSERVE, ACCEPT_MODE_AUTO)

# each segment starts alnum: rejects "a/..", "../x", "-a/b"; the
# value interpolates into a gh endpoint path, so dot segments stay out
_REPO_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


class DispatchConfigError(Exception):
    """Named config failure; .code is stable for callers and tests."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DispatchConfig:
    repo: object = None                 # owner/name, or None: unwatched
    allowed_senders: tuple = ()         # logins that count as explicit
    accept_mode: str = ACCEPT_MODE_OBSERVE


def default_config():
    return DispatchConfig()


def config_path(path=None):
    """Where the config lives: explicit path, else the state dir."""
    if path:
        return Path(path)
    return minder.STATE_DIR / CONFIG_NAME


def load_config(path):
    """Validated config, or DispatchConfigError with a stable code."""
    path = Path(path)
    if not path.exists():
        raise DispatchConfigError("config_missing", f"no config at {path}")
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise DispatchConfigError("config_invalid",
                                  f"unreadable or not json: {exc}") from exc
    if not isinstance(raw, dict):
        raise DispatchConfigError("config_invalid",
                                  "config root is not an object")

    repo = raw.get("repo")
    if repo is not None:
        repo = str(repo)
        if not _REPO_RE.match(repo):
            raise DispatchConfigError(
                "repo_invalid", f"repo must be owner/name: {repo}")

    mode = str(raw.get("accept_mode", ACCEPT_MODE_OBSERVE))
    if mode not in ACCEPT_MODES:
        raise DispatchConfigError(
            "accept_mode_invalid",
            f"accept_mode must be one of {ACCEPT_MODES}: {mode}")

    senders = raw.get("allowed_senders", [])
    if (not isinstance(senders, list)
            or not all(isinstance(s, str) and s for s in senders)):
        raise DispatchConfigError(
            "allowed_senders_invalid",
            "allowed_senders must be a list of login strings")

    return DispatchConfig(repo=repo, allowed_senders=tuple(senders),
                          accept_mode=mode)
