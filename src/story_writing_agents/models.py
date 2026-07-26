"""Structured request, reply, log, and result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


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
class AgentRequest:
    """Provider-neutral backend request with payloads kept out of the system prompt."""

    session_id: str
    debate_id: str
    agent_id: str
    phase: Phase
    round_number: int
    revision_id: str
    system_prompt: str
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
