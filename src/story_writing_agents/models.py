"""Structured request, reply, log, and result models."""

from __future__ import annotations

import hashlib
import json
import re
from .structured_output_validation import (
    validation_failure,
    validation_call,
)
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from .agents import ROSTER, SPECIALIST_IDS
from .conversations import (
    ConversationContextWindow,
    validate_conversation_context_window,
)
from .errors import SessionIdError, WorkspaceFormatError
from .reply_trace import ReplyTrace
from .structured_output_validation import validate_details


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SAFE_REFERENCE_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
STORY_SESSION_CONTEXT_SCHEMA_VERSION = 1
OWNER_OUTCOME_MAX_ENTRIES = 24
OWNER_OUTCOME_MAX_UTF8_BYTES = 32_768


class InputKind(StrEnum):
    PROMPT = "prompt"
    DRAFT = "draft"


class Phase(StrEnum):
    DRAFT = "draft"
    AUDIT = "audit"
    PROPOSE_REVISION = "propose_revision"
    SYNTHESIZE = "synthesize"
    FINALIZE = "finalize"


class Verdict(StrEnum):
    APPROVE = "approve"
    CONFLICT = "conflict"


class AgentReplyKind(StrEnum):
    """Closed semantic kind of one validated provider reply."""

    CANDIDATE = "candidate"
    AUDIT = "audit"
    SYNTHESIS = "synthesis"
    DEADLOCK = "deadlock"
    FINALIZATION = "finalization"


class AgentReplyNextPhase(StrEnum):
    """Closed phase selection permitted only for synthesis replies."""

    REPAIR = "repair"
    FINALIZATION = "finalization"


class DebateStatus(StrEnum):
    CONSENSUS = "consensus"
    DEADLOCK = "deadlock"
    FAILED = "failed"


class DebateContextMode(StrEnum):
    """Whether one debate includes or excludes its supplied conversation."""

    INCLUDED = "included"
    FRESH = "fresh"


class StoryFocusedContextMode(StrEnum):
    """How the focused transcript is represented in story-session context."""

    INCLUDED = "included"
    FRESH = "fresh"
    OMITTED = "omitted"


@dataclass(frozen=True, slots=True, kw_only=True)
class DebateContextRequest:
    """Immutable supplied-conversation request for one debate run."""

    conversation_id: str
    mode: DebateContextMode

    def __post_init__(self) -> None:
        """Validate identity and mode without reading context or mutating state."""

        _validate_conversation_identifier(
            self.conversation_id,
            label="conversation ID",
        )
        if not isinstance(self.mode, DebateContextMode):
            raise TypeError("debate context mode must be a DebateContextMode")


class DebateProgressPhase(StrEnum):
    """Trusted lifecycle facts emitted by one debate execution."""

    STARTED = "started"
    DRAFTED = "drafted"
    AUDIT_STARTED = "audit_started"
    AUDIT_COMPLETED = "audit_completed"
    SYNTHESIZED = "synthesized"
    ARTIFACTS_COMMITTED = "artifacts_committed"
    CONSENSUS = "consensus"
    DEADLOCK = "deadlock"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class DebateFailureKind(StrEnum):
    """Nonsensitive, stage-derived categories for terminal debate failures."""

    PREFLIGHT = "preflight"
    PROTOCOL = "protocol"
    CONTEXT = "context"
    WORKSPACE = "workspace"
    ARTIFACT_CONFLICT = "artifact_conflict"
    ARTIFACT_FAILURE = "artifact_failure"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True, kw_only=True)
class DebateProgressEvent:
    """Immutable nonsensitive progress fact for a single debate."""

    session_id: str
    debate_id: str
    input_kind: InputKind
    phase: DebateProgressPhase
    revision_id: str | None = None
    round_number: int = 0
    context_mode: DebateContextMode | None = None
    context_event_count: int = 0
    context_omitted_turns: int = 0
    conflicting_agents: tuple[str, ...] = ()
    manuscript_path: str | None = None
    core_updates: tuple[str, ...] = ()
    failure_kind: DebateFailureKind | None = None

    def __post_init__(self) -> None:
        """Enforce phase-specific safe metadata without I/O or mutation."""

        _validate_conversation_identifier(self.session_id, label="session ID")
        _validate_conversation_identifier(self.debate_id, label="debate ID")
        if not isinstance(self.input_kind, InputKind):
            raise TypeError("debate progress input_kind must be an InputKind")
        if not isinstance(self.phase, DebateProgressPhase):
            raise TypeError("debate progress phase must be a DebateProgressPhase")
        if self.revision_id is not None:
            _validate_conversation_identifier(
                self.revision_id,
                label="revision ID",
            )
        for value, label in (
            (self.round_number, "round_number"),
            (self.context_event_count, "context_event_count"),
            (self.context_omitted_turns, "context_omitted_turns"),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{label} must be an integer")
            if value < 0:
                raise ValueError(f"{label} must not be negative")
        if self.context_mode is not None and not isinstance(
            self.context_mode,
            DebateContextMode,
        ):
            raise TypeError("context_mode must be a DebateContextMode or None")
        if type(self.conflicting_agents) is not tuple:
            raise TypeError("conflicting_agents must be a tuple")
        if type(self.core_updates) is not tuple:
            raise TypeError("core_updates must be a tuple")
        if self.failure_kind is not None and not isinstance(
            self.failure_kind,
            DebateFailureKind,
        ):
            raise TypeError("failure_kind must be a DebateFailureKind or None")

        phase = self.phase
        if phase is DebateProgressPhase.STARTED:
            if self.revision_id is not None or self.round_number != 0:
                raise ValueError("started progress cannot carry a revision or round")
            if self.context_mode is None:
                if self.context_event_count or self.context_omitted_turns:
                    raise ValueError(
                        "legacy started progress cannot carry context counts"
                    )
            elif self.context_mode is DebateContextMode.FRESH:
                if self.context_event_count or self.context_omitted_turns:
                    raise ValueError(
                        "fresh started progress cannot carry context counts"
                    )
        elif (
            self.context_mode is not None
            or self.context_event_count
            or self.context_omitted_turns
        ):
            raise ValueError("context metadata is allowed only for started progress")

        if phase is DebateProgressPhase.DRAFTED:
            if self.revision_id != "r0" or self.round_number != 0:
                raise ValueError("drafted progress must target revision r0 and round 0")
        elif phase in {
            DebateProgressPhase.AUDIT_STARTED,
            DebateProgressPhase.AUDIT_COMPLETED,
            DebateProgressPhase.ARTIFACTS_COMMITTED,
            DebateProgressPhase.CONSENSUS,
            DebateProgressPhase.DEADLOCK,
        }:
            if self.revision_id is None:
                raise ValueError(f"{phase.value} progress requires a revision")
        elif phase is DebateProgressPhase.SYNTHESIZED:
            if self.revision_id is None or self.round_number < 1:
                raise ValueError(
                    "synthesized progress requires a revision and positive round"
                )
        elif phase is DebateProgressPhase.INTERRUPTED:
            if self.revision_id is not None or self.round_number != 0:
                raise ValueError(
                    "interrupted progress cannot carry a revision or round"
                )

        if phase is DebateProgressPhase.AUDIT_COMPLETED:
            if any(
                agent_id not in SPECIALIST_IDS
                for agent_id in self.conflicting_agents
            ):
                raise ValueError("conflicting_agents contains a non-specialist")
            canonical_conflicts = tuple(
                agent_id
                for agent_id in SPECIALIST_IDS
                if agent_id in self.conflicting_agents
            )
            if canonical_conflicts != self.conflicting_agents:
                raise ValueError(
                    "conflicting_agents must be unique and in canonical order"
                )
        elif self.conflicting_agents:
            raise ValueError(
                "conflicting_agents is allowed only for audit_completed progress"
            )

        if phase is DebateProgressPhase.ARTIFACTS_COMMITTED:
            _validate_progress_text(self.manuscript_path, label="manuscript_path")
            seen_updates: set[str] = set()
            for filename in self.core_updates:
                _validate_progress_text(filename, label="core update filename")
                if filename in seen_updates:
                    raise ValueError("core_updates must contain unique filenames")
                seen_updates.add(filename)
        elif self.manuscript_path is not None or self.core_updates:
            raise ValueError(
                "artifact metadata is allowed only for artifacts_committed progress"
            )

        if phase is DebateProgressPhase.FAILED:
            if self.failure_kind is None:
                raise ValueError("failed progress requires a failure_kind")
        elif self.failure_kind is not None:
            raise ValueError("failure_kind is allowed only for failed progress")


DebateProgressSink = Callable[[DebateProgressEvent], None]


@dataclass(frozen=True, slots=True)
class DebateMessage:
    """One public blackboard entry.

    Content is limited to user payloads, candidates, verdicts, critiques,
    proposals, and errors. Hidden reasoning is never represented by this model.
    """

    sender: str
    phase: str
    round: int
    revision: str
    kind: str
    content: str
    timestamp: str
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("debate message trace must be ReplyTrace or None")
        validate_details(self.validation_details)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.trace is None:
            value.pop("trace")
        else:
            value["trace"] = self.trace.to_dict()
        if not self.validation_details:
            value.pop("validation_details")
        else:
            value["validation_details"] = list(self.validation_details)
        return value

    @classmethod
    def from_dict(cls, value: object) -> DebateMessage:
        """Decode one stored public blackboard entry and optional trace."""

        if type(value) is not dict:
            raise TypeError("debate message must be an object")
        item = dict(value)
        raw_trace = item.get("trace")
        if raw_trace is not None:
            item["trace"] = ReplyTrace.from_dict(raw_trace)
        if "validation_details" in item:
            raw_details = item["validation_details"]
            if type(raw_details) is not list:
                raise TypeError("debate validation details must be a list")
            item["validation_details"] = tuple(raw_details)
        return cls(**item)


@dataclass(frozen=True, slots=True)
class AgentStateEntry:
    """One concise, owner-isolated backend outcome persisted for rehydration."""

    agent_id: str
    session_id: str
    debate_id: str
    phase: str
    round: int
    revision: str
    kind: str
    content: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OwnerOutcomeWindow:
    """A bounded newest suffix of one agent's session-owned outcomes."""

    entries: tuple[AgentStateEntry, ...]
    total_entries: int
    retained_entries: int
    omitted_entries: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            type(entry) is not AgentStateEntry for entry in self.entries
        ):
            raise TypeError("owner outcome entries must be AgentStateEntry values")
        for value, label in (
            (self.total_entries, "total entries"),
            (self.retained_entries, "retained entries"),
            (self.omitted_entries, "omitted entries"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            if type(value) is not int:
                raise TypeError(f"{label} must be an integer")
            if value < 0:
                raise ValueError(f"{label} must not be negative")
        if self.retained_entries != len(self.entries):
            raise ValueError("owner outcome retained-entry count is inconsistent")
        if self.total_entries != self.retained_entries + self.omitted_entries:
            raise ValueError("owner outcome entry counts are inconsistent")
        if self.retained_entries > OWNER_OUTCOME_MAX_ENTRIES:
            raise ValueError("owner outcome entry cap was exceeded")
        serialized_bytes = _serialized_owner_outcome_bytes(self.entries)
        if self.retained_utf8_bytes != serialized_bytes:
            raise ValueError("owner outcome byte accounting is inconsistent")
        if serialized_bytes > OWNER_OUTCOME_MAX_UTF8_BYTES:
            raise ValueError("owner outcome byte cap was exceeded")
        if type(self.truncated) is not bool:
            raise TypeError("owner outcome truncated must be a boolean")
        if self.truncated is not (self.omitted_entries > 0):
            raise ValueError("owner outcome truncated flag is inconsistent")


@dataclass(frozen=True, slots=True)
class BoundCoreDocumentIdentity:
    """Content identity of one core document already bound into provider rules."""

    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("core document path must be non-empty text")
        raw = Path(self.path)
        if (
            raw.is_absolute()
            or ".." in raw.parts
            or len(raw.parts) != 2
            or raw.parts[0] != "core_documents"
            or raw.as_posix() != self.path
        ):
            raise ValueError("core document path must be canonical under core_documents")
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("core document sha256 must be lowercase hexadecimal")


@dataclass(frozen=True, slots=True)
class StoryFocusedContext:
    """Focused-conversation facts carried by one substantive logical turn."""

    mode: StoryFocusedContextMode
    conversation_id: str | None
    window: ConversationContextWindow | None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, StoryFocusedContextMode):
            raise TypeError("focused context mode must be StoryFocusedContextMode")
        if self.conversation_id is not None:
            _validate_conversation_identifier(
                self.conversation_id,
                label="conversation ID",
            )
        if self.mode is StoryFocusedContextMode.INCLUDED:
            if self.conversation_id is None:
                raise ValueError("included focused context requires a conversation ID")
            if type(self.window) is not ConversationContextWindow:
                raise TypeError("included focused context requires a window")
        elif self.window is not None:
            raise ValueError("fresh or omitted focused context cannot carry a window")
        if (
            self.mode is StoryFocusedContextMode.FRESH
            and self.conversation_id is None
        ):
            raise ValueError("fresh focused context requires a conversation ID")
        if self.mode is StoryFocusedContextMode.OMITTED and self.conversation_id is not None:
            raise ValueError("omitted focused context cannot identify a conversation")


@dataclass(frozen=True, slots=True)
class StorySessionContext:
    """Authoritative bounded continuity envelope for one logical provider turn."""

    schema_version: int
    session_id: str
    agent_id: str
    owner_outcomes: OwnerOutcomeWindow
    focused_context: StoryFocusedContext
    core_documents: tuple[BoundCoreDocumentIdentity, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != STORY_SESSION_CONTEXT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported story-session context schema version")
        _validate_conversation_identifier(self.session_id, label="session ID")
        _validate_conversation_identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError("story-session context agent must be canonical")
        if type(self.owner_outcomes) is not OwnerOutcomeWindow:
            raise TypeError("owner_outcomes must be an OwnerOutcomeWindow")
        if any(
            entry.session_id != self.session_id or entry.agent_id != self.agent_id
            for entry in self.owner_outcomes.entries
        ):
            raise ValueError("owner outcomes do not match story-session ownership")
        if type(self.focused_context) is not StoryFocusedContext:
            raise TypeError("focused_context must be a StoryFocusedContext")
        if self.focused_context.window is not None:
            validate_conversation_context_window(
                self.focused_context.window,
                session_id=self.session_id,
                conversation_id=self.focused_context.conversation_id,
            )
        if type(self.core_documents) is not tuple or any(
            type(identity) is not BoundCoreDocumentIdentity
            for identity in self.core_documents
        ):
            raise TypeError("core_documents must contain bound identities")
        if len({identity.path for identity in self.core_documents}) != len(
            self.core_documents
        ):
            raise ValueError("core document identities must be unique")

    def to_dict(self) -> dict[str, Any]:
        """Project the exact schema-v1 JSON-compatible provider envelope."""

        focused: dict[str, Any] = {"mode": self.focused_context.mode.value}
        if self.focused_context.mode is StoryFocusedContextMode.INCLUDED:
            window = self.focused_context.window
            assert window is not None
            focused.update(
                {
                    "conversation_id": self.focused_context.conversation_id,
                    "total_complete_turns": window.total_complete_turns,
                    "retained_turns": window.retained_turns,
                    "omitted_turns": window.omitted_turns,
                    "retained_utf8_bytes": window.retained_utf8_bytes,
                    "truncated": window.truncated,
                    "events": [
                        {
                            "turn_id": event.turn_id,
                            "sequence": event.sequence,
                            "sender": event.sender,
                            "recipient": event.recipient,
                            "kind": event.kind.value,
                            "content": event.content,
                        }
                        for event in window.events
                    ],
                }
            )
        elif self.focused_context.mode is StoryFocusedContextMode.FRESH:
            focused["conversation_id"] = self.focused_context.conversation_id

        return {
            "schema_version": self.schema_version,
            "scope": "story_session",
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "owner_outcomes": {
                "total_entries": self.owner_outcomes.total_entries,
                "retained_entries": self.owner_outcomes.retained_entries,
                "omitted_entries": self.owner_outcomes.omitted_entries,
                "retained_utf8_bytes": self.owner_outcomes.retained_utf8_bytes,
                "truncated": self.owner_outcomes.truncated,
                "entries": [entry.to_dict() for entry in self.owner_outcomes.entries],
            },
            "focused_context": focused,
            "core_documents": [
                {"path": identity.path, "sha256": identity.sha256}
                for identity in self.core_documents
            ],
        }


def select_owner_outcome_window(
    entries: tuple[AgentStateEntry, ...],
) -> OwnerOutcomeWindow:
    """Select the newest complete entry suffix under both fixed owner caps."""

    if type(entries) is not tuple or any(
        type(entry) is not AgentStateEntry for entry in entries
    ):
        raise TypeError("owner outcomes must be an exact AgentStateEntry tuple")
    retained_reversed: list[AgentStateEntry] = []
    for entry in reversed(entries):
        candidate = tuple(reversed((*retained_reversed, entry)))
        if len(candidate) > OWNER_OUTCOME_MAX_ENTRIES:
            break
        if _serialized_owner_outcome_bytes(candidate) > OWNER_OUTCOME_MAX_UTF8_BYTES:
            break
        retained_reversed.append(entry)
    retained = tuple(reversed(retained_reversed))
    omitted = len(entries) - len(retained)
    return OwnerOutcomeWindow(
        entries=retained,
        total_entries=len(entries),
        retained_entries=len(retained),
        omitted_entries=omitted,
        retained_utf8_bytes=_serialized_owner_outcome_bytes(retained),
        truncated=omitted > 0,
    )


def focused_story_context(
    mode: StoryFocusedContextMode,
    *,
    conversation_id: str | None = None,
    context: ConversationContextWindow | None = None,
) -> StoryFocusedContext:
    """Build one canonical focused-context projection from existing contracts."""

    if mode is StoryFocusedContextMode.INCLUDED:
        if context is None or conversation_id is None:
            raise ValueError("included focused context requires context and identity")
        return StoryFocusedContext(
            mode=mode,
            conversation_id=conversation_id,
            window=context,
        )
    if context is not None:
        raise ValueError("fresh or omitted focused context cannot carry events")
    return StoryFocusedContext(
        mode=mode,
        conversation_id=(
            conversation_id if mode is StoryFocusedContextMode.FRESH else None
        ),
        window=None,
    )


def _serialized_owner_outcome_bytes(entries: tuple[AgentStateEntry, ...]) -> int:
    encoded = tuple(
        json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        for entry in entries
    )
    return sum(len(item) for item in encoded) + max(0, len(encoded) - 1)


@dataclass(frozen=True, slots=True)
class PromptFileReference:
    """Immutable content-addressed snapshot of one prompt component."""

    path: Path
    sha256: str
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if type(self.content) is not bytes:
            raise TypeError("prompt content must be bytes")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 hexadecimal digest")
        if hashlib.sha256(self.content).hexdigest() != self.sha256:
            raise ValueError("sha256 must match the immutable prompt content")
        self._decode_utf8(self.path, self.content)

    @classmethod
    def from_path(cls, path: Path) -> PromptFileReference:
        """Create a reference after validating the file as strict UTF-8."""

        target = Path(path)
        data = cls._read_bytes(target)
        cls._decode_utf8(target, data)
        return cls(
            path=target,
            sha256=hashlib.sha256(data).hexdigest(),
            content=data,
        )

    @classmethod
    def from_bytes(cls, path: Path, content: bytes) -> PromptFileReference:
        """Create a lexical source reference backed by immutable exact bytes."""

        if type(content) is not bytes:
            raise TypeError("prompt content must be bytes")
        return cls(
            path=Path(path),
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    def read_text(self) -> str:
        """Decode the captured bytes after construction verified ``sha256``."""

        return self._decode_utf8(self.path, self.content)

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise WorkspaceFormatError(
                f"cannot read referenced prompt file: {path}"
            ) from exc

    @staticmethod
    def _decode_utf8(path: Path, data: bytes) -> str:
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceFormatError(
                f"referenced prompt file is not valid UTF-8: {path}"
            ) from exc


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """File-referenced, content-addressed definition for one bound agent."""

    agent_id: str
    backend: str
    fingerprint: str
    system_prompt_file: PromptFileReference
    persona_file: PromptFileReference
    language: str
    context_document_files: tuple[PromptFileReference, ...]
    provider_managed_context: bool = field(default=False, repr=False)
    behavior_policy_components: tuple[PromptFileReference, ...] = field(
        default=(), repr=False
    )
    runtime_behavior_policy: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        if type(self.provider_managed_context) is not bool:
            raise TypeError("provider_managed_context must be a bool")
        if self.provider_managed_context and self.context_document_files:
            raise ValueError(
                "provider-managed context cannot bind context documents"
            )
        components = self.behavior_policy_components
        if type(self.runtime_behavior_policy) is not bytes:
            raise TypeError("runtime behavior policy must be captured bytes")
        if b"\x00" in self.runtime_behavior_policy:
            raise ValueError("runtime behavior policy must be NUL-free")
        self.runtime_behavior_policy.decode("utf-8", errors="strict")
        if type(components) is not tuple or any(
            type(item) is not PromptFileReference for item in components
        ):
            raise TypeError("behavior policy components must be prompt snapshots")
        if self.runtime_behavior_policy and not components:
            raise ValueError("runtime policy requires composed behavior components")
        if components:
            paths = tuple(item.path for item in components)
            if len(set(paths)) != len(paths):
                raise ValueError("behavior policy component paths must be unique")
            if self.system_prompt_file.path != components[-1].path:
                raise ValueError("composed policy must retain the domain policy path")
            expected = b"\n\n".join(item.content for item in components)
            if self.runtime_behavior_policy:
                expected += b"\n\n" + self.runtime_behavior_policy
            if self.system_prompt_file.content != expected:
                raise ValueError("system prompt does not match policy components")

    def file_references(self) -> tuple[PromptFileReference, ...]:
        """Return prompt components in their binding order."""

        return (
            self.system_prompt_file,
            self.persona_file,
            *self.context_document_files,
        )


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """Provider-neutral request for an agent whose prompt is already bound."""

    session_id: str
    debate_id: str
    agent_id: str
    phase: Phase
    round_number: int
    revision_id: str
    task: str
    user_payload: str
    candidate_payload: str | None = None
    messages: tuple[DebateMessage, ...] = ()
    agent_state: tuple[AgentStateEntry, ...] = ()
    conversation_context: ConversationContextWindow | None = None
    story_session_context: StorySessionContext | None = None

    def __post_init__(self) -> None:
        """Validate optional context without changing legacy request fields."""

        if self.conversation_context is not None:
            object.__setattr__(
                self,
                "conversation_context",
                validate_conversation_context_window(
                    self.conversation_context,
                    session_id=self.session_id,
                ),
            )
        if self.story_session_context is not None:
            if type(self.story_session_context) is not StorySessionContext:
                raise TypeError(
                    "story_session_context must be a StorySessionContext or None"
                )
            if (
                self.story_session_context.session_id != self.session_id
                or self.story_session_context.agent_id != self.agent_id
            ):
                raise ValueError("story-session context request identity mismatch")


@dataclass(frozen=True, slots=True)
class ConversationRequest:
    """One validated provider-neutral conversation turn request."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: ConversationContextWindow
    story_session_context: StorySessionContext | None = None

    def __post_init__(self) -> None:
        """Validate IDs/content and install a canonical pre-turn context.

        The context must match this session and conversation and exclude this
        turn. Initialization performs no I/O or mutation beyond replacing
        ``context`` on the frozen instance. Invalid identity, roster, content,
        or context data raises ``TypeError`` or ``ValueError``.
        """

        _validate_conversation_identifier(self.session_id, label="session ID")
        _validate_conversation_identifier(
            self.conversation_id,
            label="conversation ID",
        )
        _validate_conversation_identifier(self.turn_id, label="turn ID")
        _validate_conversation_identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError(f"conversation agent is not canonical: {self.agent_id}")
        _validate_conversation_content(
            self.content,
            label="conversation request content",
        )
        if type(self.context) is not ConversationContextWindow:
            raise TypeError("conversation context must be a ConversationContextWindow")
        object.__setattr__(
            self,
            "context",
            validate_conversation_context_window(
                self.context,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                excluded_turn_id=self.turn_id,
            ),
        )
        if self.story_session_context is not None:
            if type(self.story_session_context) is not StorySessionContext:
                raise TypeError(
                    "story_session_context must be a StorySessionContext or None"
                )
            if (
                self.story_session_context.session_id != self.session_id
                or self.story_session_context.agent_id != self.agent_id
                or (
                    self.story_session_context.focused_context.conversation_id
                    is not None
                    and self.story_session_context.focused_context.conversation_id
                    != self.conversation_id
                )
            ):
                raise ValueError("story-session context request identity mismatch")


@dataclass(frozen=True, slots=True)
class ConversationReply:
    """One correlated provider-neutral conversation turn reply."""

    session_id: str
    conversation_id: str
    turn_id: str
    content: str
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_conversation_identifier(self.session_id, label="session ID")
        _validate_conversation_identifier(
            self.conversation_id,
            label="conversation ID",
        )
        _validate_conversation_identifier(self.turn_id, label="turn ID")
        _validate_conversation_content(
            self.content,
            label="conversation reply content",
        )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("conversation reply trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True)
class ConversationResult:
    """One successful persisted advisory conversation turn."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: ConversationContextWindow
    reference_operation_id: str | None = None
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate IDs/content and install a canonical pre-turn context.

        The context must match this session and conversation and exclude this
        turn. Initialization performs no I/O or mutation beyond replacing
        ``context`` on the frozen instance. Invalid identity, roster, content,
        or context data raises ``TypeError`` or ``ValueError``.
        """

        _validate_conversation_identifier(self.session_id, label="session ID")
        _validate_conversation_identifier(
            self.conversation_id,
            label="conversation ID",
        )
        _validate_conversation_identifier(self.turn_id, label="turn ID")
        _validate_conversation_identifier(self.agent_id, label="agent ID")
        if self.agent_id not in ROSTER:
            raise ValueError(f"conversation agent is not canonical: {self.agent_id}")
        _validate_conversation_content(
            self.content,
            label="conversation result content",
        )
        if type(self.context) is not ConversationContextWindow:
            raise TypeError("conversation context must be a ConversationContextWindow")
        object.__setattr__(
            self,
            "context",
            validate_conversation_context_window(
                self.context,
                session_id=self.session_id,
                conversation_id=self.conversation_id,
                excluded_turn_id=self.turn_id,
            ),
        )
        _validate_reference_operation_identifier(self.reference_operation_id)
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("conversation result trace must be ReplyTrace or None")
        validate_details(self.validation_details)


@dataclass(frozen=True, slots=True)
class AgentReply:
    """Structured backend response.

    ``content`` is a candidate, critique, or synthesis according to ``kind``.
    Finalization identifies an already reviewed proposal and never carries
    artifact writes or authority.
    """

    kind: AgentReplyKind
    revision_id: str
    content: str = ""
    verdict: Verdict | None = None
    next_phase: AgentReplyNextPhase | None = None
    deadlocked: bool = False
    reviewed_proposal_id: str | None = None
    reviewed_proposal_digest: str | None = None
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not AgentReplyKind:
            raise validation_failure(
                TypeError('agent reply kind is invalid'),
                '$.kind',
                'expected a supported agent reply kind',
            )
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("agent reply trace must be ReplyTrace or None")
        validate_details(self.validation_details)
        if type(self.deadlocked) is not bool:
            raise validation_failure(
                TypeError('agent reply deadlock marker is invalid'),
                '$.deadlocked',
                'expected a boolean',
            )

        def require(condition: object, path: str, rule: str) -> None:
            if not condition:
                raise validation_failure(ValueError('agent reply fields do not match its kind'), path, rule)

        def without_review_identity() -> None:
            require(self.reviewed_proposal_id is None, "$.reviewed_proposal_id", "allowed only for finalization")
            require(self.reviewed_proposal_digest is None, "$.reviewed_proposal_digest", "allowed only for finalization")

        if self.kind is AgentReplyKind.CANDIDATE:
            require(bool(self.content), "$.content", "expected nonempty text")
            require(self.verdict is None, "$.verdict", "allowed only for an audit reply")
            require(self.next_phase is None, "$.next_phase", "allowed only for synthesis")
            require(not self.deadlocked, "$.deadlocked", "candidate replies cannot mark deadlock")
            without_review_identity()
        elif self.kind is AgentReplyKind.AUDIT:
            require(type(self.verdict) is Verdict, "$.verdict", "expected approve or conflict")
            require(self.next_phase is None, "$.next_phase", "allowed only for synthesis")
            require(not self.deadlocked, "$.deadlocked", "audit replies cannot mark deadlock")
            require(self.verdict is Verdict.APPROVE or bool(self.content), "$.content", "verdict conflict requires nonempty text")
            without_review_identity()
        elif self.kind is AgentReplyKind.SYNTHESIS:
            require(bool(self.content), "$.content", "expected nonempty text")
            require(self.verdict is None, "$.verdict", "allowed only for an audit reply")
            require(type(self.next_phase) is AgentReplyNextPhase, "$.next_phase", "expected repair or finalization")
            require(not self.deadlocked, "$.deadlocked", "synthesis with next_phase cannot also mark deadlock")
            without_review_identity()
        elif self.kind is AgentReplyKind.DEADLOCK:
            require(bool(self.content), "$.content", "expected nonempty text")
            require(self.verdict is None, "$.verdict", "allowed only for an audit reply")
            require(self.next_phase is None, "$.next_phase", "deadlock cannot also choose a next phase")
            require(self.deadlocked, "$.deadlocked", "deadlock replies require a true deadlock marker")
            without_review_identity()
        else:
            require(not self.content, "$.content", "finalization cannot carry new content")
            require(self.verdict is None, "$.verdict", "allowed only for an audit reply")
            require(self.next_phase is None, "$.next_phase", "finalization cannot choose a next phase")
            require(not self.deadlocked, "$.deadlocked", "finalization cannot mark deadlock")
            require(type(self.reviewed_proposal_id) is str, "$.reviewed_proposal_id", "expected text")
            require(bool(self.reviewed_proposal_id), "$.reviewed_proposal_id", "expected nonempty text")
            try:
                digest_matches = _TAGGED_SHA256.fullmatch(self.reviewed_proposal_digest or "")
            except TypeError as failure:
                raise validation_failure(
                    failure,
                    '$.reviewed_proposal_digest',
                    'expected sha256: followed by 64 lowercase hexadecimal characters',
                )
            require(digest_matches, "$.reviewed_proposal_digest", "expected sha256: followed by 64 lowercase hexadecimal characters")
        if self.kind is AgentReplyKind.FINALIZATION:
            validation_call(
                '$.reviewed_proposal_id',
                _validate_conversation_identifier,
                self.reviewed_proposal_id,
                label='reviewed proposal ID',
            )
    @classmethod
    def candidate(cls, revision_id: str, content: str) -> AgentReply:
        return cls(AgentReplyKind.CANDIDATE, revision_id, content=content)

    @classmethod
    def approval(cls, revision_id: str, content: str = "") -> AgentReply:
        return cls(
            kind=AgentReplyKind.AUDIT,
            revision_id=revision_id,
            content=content,
            verdict=Verdict.APPROVE,
        )

    @classmethod
    def conflict(cls, revision_id: str, critique: str) -> AgentReply:
        return cls(
            kind=AgentReplyKind.AUDIT,
            revision_id=revision_id,
            content=critique,
            verdict=Verdict.CONFLICT,
        )

    @classmethod
    def finalization(
        cls,
        revision_id: str,
        reviewed_proposal_id: str,
        reviewed_proposal_digest: str,
    ) -> AgentReply:
        return cls(
            kind=AgentReplyKind.FINALIZATION,
            revision_id=revision_id,
            reviewed_proposal_id=reviewed_proposal_id,
            reviewed_proposal_digest=reviewed_proposal_digest,
        )

    @classmethod
    def synthesis(
        cls,
        revision_id: str,
        content: str,
        next_phase: AgentReplyNextPhase,
    ) -> AgentReply:
        return cls(
            AgentReplyKind.SYNTHESIS,
            revision_id,
            content=content,
            next_phase=next_phase,
        )

    @classmethod
    def deadlock(cls, revision_id: str, content: str) -> AgentReply:
        return cls(
            AgentReplyKind.DEADLOCK,
            revision_id,
            content=content,
            deadlocked=True,
        )


@dataclass(frozen=True, slots=True)
class DebateResult:
    """Typed terminal state for one debate."""

    session_id: str
    debate_id: str
    input_kind: InputKind
    status: DebateStatus
    revision_id: str | None
    rounds_completed: int
    final_candidate: str | None
    latest_candidate: str | None
    manuscript_path: str | None
    core_updates: tuple[str, ...] = ()
    messages: tuple[DebateMessage, ...] = ()
    error: str | None = None
    action_required: str | None = None
    reference_operation_id: str | None = None

    def __post_init__(self) -> None:
        _validate_reference_operation_identifier(self.reference_operation_id)

    @property
    def consensus(self) -> bool:
        return self.status is DebateStatus.CONSENSUS

    def to_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "session_id": self.session_id,
            "debate_id": self.debate_id,
            "input_kind": self.input_kind.value,
            "status": self.status.value,
            "revision_id": self.revision_id,
            "rounds_completed": self.rounds_completed,
            "final_candidate": self.final_candidate,
            "latest_candidate": self.latest_candidate,
            "manuscript_path": self.manuscript_path,
            "core_updates": list(self.core_updates),
            "error": self.error,
            "action_required": self.action_required,
        }
        if include_messages:
            result["messages"] = [message.to_dict() for message in self.messages]
        if self.reference_operation_id is not None:
            result["reference_operation_id"] = self.reference_operation_id
        return result


def _validate_conversation_identifier(value: str, *, label: str) -> str:
    """Return a safe conversation identifier or raise ``SessionIdError``.

    Validation preserves the string and performs no I/O or mutation.
    """

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise validation_failure(
            SessionIdError(f'{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}'),
            '$',
            'expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}',
        )
    return value


def _validate_reference_operation_identifier(value: str | None) -> str | None:
    """Return one optional content-free reference operation identifier."""

    if value is None:
        return None
    if type(value) is not str or _SAFE_REFERENCE_OPERATION_ID.fullmatch(value) is None:
        raise ValueError("reference operation ID is invalid")
    return value


def _validate_conversation_content(value: str, *, label: str) -> str:
    """Return nonempty strict-UTF-8 conversation text without mutation.

    Non-strings raise ``TypeError``; empty or unencodable strings raise
    ``ValueError`` using the supplied diagnostic label.
    """

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _validate_progress_text(value: str | None, *, label: str) -> str:
    """Return nonempty strict-UTF-8 progress text or raise without mutation."""

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value:
        raise ValueError(f"{label} must not be empty")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value
