"""Structured request, reply, log, and result models."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .agents import ROSTER, SPECIALIST_IDS
from .conversations import (
    ConversationContextWindow,
    validate_conversation_context_window,
)
from .errors import SessionIdError, WorkspaceFormatError
from .session_lifecycle import SessionLeaseState, SessionSummary


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


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


class DebateStatus(StrEnum):
    CONSENSUS = "consensus"
    DEADLOCK = "deadlock"
    FAILED = "failed"


class DebateContextMode(StrEnum):
    """Whether one debate includes or excludes its selected conversation."""

    INCLUDED = "included"
    FRESH = "fresh"


@dataclass(frozen=True, slots=True, kw_only=True)
class DebateContextRequest:
    """Immutable selected-conversation request for one debate run."""

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
class PromptFileReference:
    """Content-addressed reference to a strict UTF-8 prompt component."""

    path: Path
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 hexadecimal digest")

    @classmethod
    def from_path(cls, path: Path) -> PromptFileReference:
        """Create a reference after validating the file as strict UTF-8."""

        target = Path(path)
        data = cls._read_bytes(target)
        cls._decode_utf8(target, data)
        return cls(path=target, sha256=hashlib.sha256(data).hexdigest())

    def read_text(self) -> str:
        """Read strict UTF-8 only when the current content matches ``sha256``."""

        data = self._read_bytes(self.path)
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual, self.sha256):
            raise WorkspaceFormatError(
                f"referenced prompt file changed after reference creation: {self.path}"
            )
        return self._decode_utf8(self.path, data)

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

    def __post_init__(self) -> None:
        """Validate optional context without changing legacy request fields."""

        if self.conversation_context is None:
            return
        object.__setattr__(
            self,
            "conversation_context",
            validate_conversation_context_window(
                self.conversation_context,
                session_id=self.session_id,
            ),
        )


@dataclass(frozen=True, slots=True)
class ConversationRequest:
    """One validated provider-neutral conversation turn request."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: ConversationContextWindow

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


@dataclass(frozen=True, slots=True)
class ConversationReply:
    """One correlated provider-neutral conversation turn reply."""

    session_id: str
    conversation_id: str
    turn_id: str
    content: str

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


@dataclass(frozen=True, slots=True)
class ConversationResult:
    """One successful persisted advisory conversation turn."""

    session_id: str
    conversation_id: str
    turn_id: str
    agent_id: str
    content: str
    context: ConversationContextWindow

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


@dataclass(frozen=True, slots=True)
class AgentReply:
    """Structured backend response.

    ``content`` is a candidate, critique, or proposal according to ``phase``.
    It must never contain hidden chain-of-thought. ``core_updates`` is accepted
    only from the lead orchestrator during finalization.
    """

    revision_id: str
    content: str = ""
    verdict: Verdict | None = None
    core_updates: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def candidate(cls, revision_id: str, content: str) -> AgentReply:
        return cls(revision_id=revision_id, content=content)

    @classmethod
    def approval(cls, revision_id: str, content: str = "") -> AgentReply:
        return cls(
            revision_id=revision_id,
            content=content,
            verdict=Verdict.APPROVE,
        )

    @classmethod
    def conflict(cls, revision_id: str, critique: str) -> AgentReply:
        return cls(
            revision_id=revision_id,
            content=critique,
            verdict=Verdict.CONFLICT,
        )

    @classmethod
    def finalization(
        cls,
        revision_id: str,
        core_updates: Mapping[str, str] | None = None,
    ) -> AgentReply:
        return cls(
            revision_id=revision_id,
            core_updates=MappingProxyType(dict(core_updates or {})),
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
        return result


def _validate_conversation_identifier(value: str, *, label: str) -> str:
    """Return a safe conversation identifier or raise ``SessionIdError``.

    Validation preserves the string and performs no I/O or mutation.
    """

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(
            f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
        )
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
