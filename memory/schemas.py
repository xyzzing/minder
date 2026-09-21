"""Memory v1 schemas (docs/prd-memory-v1.md PR 0). Dataclasses only —
no I/O, no behaviour, nothing imports the rest of minder.

Strict construction: missing required fields raise TypeError and unknown
status values raise ValueError at construction time. Callers that cannot
afford to fail (the hook path) construct these inside try/except and fall
back to plain dicts — see memory/from_hook.py.
"""
from dataclasses import dataclass
from typing import Optional

EPISODE_STATUSES = {"open", "unresolved", "resolved", "candidate", "verified"}
LESSON_STATUSES = {"observation", "candidate", "verified", "invalidated"}
EVENT_TYPES = {"tool_failure", "tool_success", "verification"}


@dataclass
class AgentToolEvent:
    """One tool invocation observed by a harness hook (canonical shape).

    Only the two identity fields are required; every observation field is
    optional so partially-populated hook payloads can still be recorded.
    """
    event_type: str           # tool_failure | tool_success | verification
    tool: str
    ts: Optional[str] = None  # ISO-8601 UTC; store stamps when None
    session_id: str = ""
    task_id: str = ""
    repo: str = ""
    command: str = ""
    args_json: str = ""       # canonical JSON of the tool input (see
    #                           canonicalise.canonical_args)
    file_path: str = ""
    error_excerpt: str = ""
    content_hash: str = ""    # optional: hash of content involved/changed
    hypothesis: str = ""      # optional: agent-stated hypothesis this attempt
    payload_json: str = "{}"  # full original payload (redacted), for evidence

    def __post_init__(self):
        if not self.event_type:
            raise TypeError("AgentToolEvent: event_type is required")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"AgentToolEvent: unknown event_type "
                             f"{self.event_type!r}")
        if not self.tool:
            raise TypeError("AgentToolEvent: tool is required")


@dataclass
class FailureSignature:
    """Canonical identity of one failure — the four failure_key segments."""
    tool: str
    error_family: str
    symbol_or_test_id: str
    relpath: Optional[str] = None

    @property
    def key(self) -> str:
        return (f"{self.tool}|{self.error_family}|"
                f"{self.symbol_or_test_id}|{self.relpath or 'none'}")


@dataclass
class Episode:
    """One continuous struggle with one failure key in one task."""
    opened_at: str            # ISO-8601 UTC
    repo: str = ""
    task_id: str = ""
    episode_id: Optional[str] = None
    closed_at: Optional[str] = None
    status: str = "open"

    def __post_init__(self):
        if not self.opened_at:
            raise TypeError("Episode: opened_at is required")
        if self.status not in EPISODE_STATUSES:
            raise ValueError(f"Episode: unknown status {self.status!r}")


@dataclass
class Lesson:
    """A scoped instruction distilled from a verified episode (PR 4)."""
    instruction: str
    repo: str = ""
    failure_key: str = ""
    anti_pattern: str = ""
    verification_json: str = ""
    status: str = "observation"
    lesson_id: Optional[str] = None
    source_episode: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    expires_when: str = ""

    def __post_init__(self):
        if not self.instruction:
            raise TypeError("Lesson: instruction is required")
        if self.status not in LESSON_STATUSES:
            raise ValueError(f"Lesson: unknown status {self.status!r}")
