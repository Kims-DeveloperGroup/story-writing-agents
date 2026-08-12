"""Structured request, reply, log, and result models."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import WorkspaceFormatError


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


class SessionLeaseState(StrEnum):
    """Observed ownership and liveness state for a provider-session lease."""

    AVAILABLE = "available"
    OWNED = "owned"
    IN_USE = "in_use"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Immutable provider-session lifecycle status."""

    session_id: str
    active: bool
    selected: bool
    lease_state: SessionLeaseState
    lease_hostname: str | None
    lease_pid: int | None
    created_at: str
    updated_at: str


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
