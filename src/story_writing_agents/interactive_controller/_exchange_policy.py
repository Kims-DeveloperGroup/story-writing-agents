"""Pure inventory and display projection for agent exchange selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..agent_exchanges import (
    AgentExchangeInventoryItem,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
)
from ..agents import AGENT_SPECS, ROSTER
from ..models import DebateMessage


DEBATE_SUBJECT_UNAVAILABLE = "[subject unavailable]"
_MAX_SUBJECT_CHARACTERS = 512
_SUBJECT_TRUNCATION_MARKER = "…"


class AgentExchangeProjectionError(ValueError):
    """Report fixed invalid or inconsistent projection input."""


@dataclass(frozen=True, slots=True)
class ExchangeScopeChoice:
    """One exact inventory-derived conversation or debate scope."""

    scope_kind: AgentExchangeScopeKind
    scope_id: str
    subject: str
    record_count: int
    last_workspace_sequence: int

    def __post_init__(self) -> None:
        if type(self.scope_kind) is not AgentExchangeScopeKind:
            raise TypeError("scope_kind must be an AgentExchangeScopeKind")
        if type(self.scope_id) is not str or not self.scope_id:
            raise ValueError("scope_id must be non-empty text")
        if type(self.subject) is not str or not self.subject:
            raise ValueError("subject must be non-empty text")
        if len(self.subject) > _MAX_SUBJECT_CHARACTERS:
            raise ValueError("subject exceeds the viewer header bound")
        if type(self.record_count) is not int or self.record_count <= 0:
            raise ValueError("record_count must be positive")
        if (
            type(self.last_workspace_sequence) is not int
            or self.last_workspace_sequence <= 0
        ):
            raise ValueError("last_workspace_sequence must be positive")

    @property
    def identity(self) -> str:
        """Return a transient picker identity without changing the full ID."""

        return f"{self.scope_kind.value}:{self.scope_id}"

    @property
    def label(self) -> str:
        """Return the exact full scope ID, kind, and subject label."""

        return f"{self.scope_kind.value.title()} {self.scope_id} — {self.subject}"


@dataclass(frozen=True, slots=True)
class ExchangeAgentChoice:
    """One canonical roster agent, with an optional retained selection."""

    agent_id: str
    display_name: str
    selection: AgentExchangeSelection | None
    record_count: int
    last_workspace_sequence: int | None

    def __post_init__(self) -> None:
        if self.agent_id not in ROSTER:
            raise ValueError("agent_id must be canonical")
        if type(self.display_name) is not str or not self.display_name:
            raise ValueError("display_name must be non-empty text")
        if self.selection is not None:
            if type(self.selection) is not AgentExchangeSelection:
                raise TypeError("selection must be an AgentExchangeSelection or None")
            if self.selection.agent_id != self.agent_id:
                raise ValueError("selection must identify the projected agent")
        if type(self.record_count) is not int or self.record_count < 0:
            raise ValueError("record_count must be non-negative")
        if self.last_workspace_sequence is not None and (
            type(self.last_workspace_sequence) is not int
            or self.last_workspace_sequence <= 0
        ):
            raise ValueError("last_workspace_sequence must be positive or None")
        if (self.selection is None) != (self.record_count == 0):
            raise ValueError("empty record facts must omit the exact selection")
        if (self.selection is None) != (self.last_workspace_sequence is None):
            raise ValueError("empty record facts must omit the last sequence")

    @property
    def has_records(self) -> bool:
        return self.selection is not None


def debate_subject_from_messages(messages: object) -> str:
    """Return the exact first initiation preview or the fixed fallback."""

    if type(messages) is not tuple or not messages:
        return DEBATE_SUBJECT_UNAVAILABLE
    first = messages[0]
    if (
        type(first) is not DebateMessage
        or first.sender != "user"
        or first.phase != "initiation"
        or first.round != 0
        or first.revision != "r0"
        or first.kind not in {"prompt", "draft"}
    ):
        return DEBATE_SUBJECT_UNAVAILABLE
    collapsed = " ".join(first.content.split())
    if not collapsed:
        return DEBATE_SUBJECT_UNAVAILABLE
    if len(collapsed) <= _MAX_SUBJECT_CHARACTERS:
        return collapsed
    return f"{collapsed[:511]}{_SUBJECT_TRUNCATION_MARKER}"


def project_exchange_scopes(
    session_id: str,
    inventory: object,
    *,
    conversation_subjects: Mapping[str, str],
    debate_messages: Mapping[str, tuple[DebateMessage, ...]],
) -> tuple[ExchangeScopeChoice, ...]:
    """Deduplicate and newest-sort one authoritative inventory snapshot."""

    if type(session_id) is not str or not session_id:
        raise ValueError("session_id must be non-empty text")
    if type(inventory) is not tuple or any(
        type(item) is not AgentExchangeInventoryItem for item in inventory
    ):
        raise TypeError("inventory must contain AgentExchangeInventoryItem values")
    if not isinstance(conversation_subjects, Mapping):
        raise TypeError("conversation_subjects must be a mapping")
    if not isinstance(debate_messages, Mapping):
        raise TypeError("debate_messages must be a mapping")

    streams: set[AgentExchangeSelection] = set()
    aggregated: dict[tuple[AgentExchangeScopeKind, str], tuple[int, int]] = {}
    for item in inventory:
        selection = item.selection
        if selection.session_id != session_id:
            raise AgentExchangeProjectionError(
                "agent exchange inventory crossed the attached session"
            )
        if selection in streams:
            raise AgentExchangeProjectionError(
                "agent exchange inventory contains duplicate streams"
            )
        streams.add(selection)
        key = (selection.scope_kind, selection.scope_id)
        record_count, last_sequence = aggregated.get(key, (0, 0))
        aggregated[key] = (
            record_count + item.record_count,
            max(last_sequence, item.last_workspace_sequence),
        )

    choices: list[ExchangeScopeChoice] = []
    for (scope_kind, scope_id), (record_count, last_sequence) in aggregated.items():
        if scope_kind is AgentExchangeScopeKind.CONVERSATION:
            subject = conversation_subjects.get(scope_id)
            if type(subject) is not str or not subject:
                raise AgentExchangeProjectionError(
                    "conversation exchange scope is absent from the complete catalog"
                )
            if len(subject) > _MAX_SUBJECT_CHARACTERS:
                raise AgentExchangeProjectionError(
                    "conversation subject exceeds the viewer header bound"
                )
        else:
            subject = debate_subject_from_messages(debate_messages.get(scope_id, ()))
        choices.append(
            ExchangeScopeChoice(
                scope_kind,
                scope_id,
                subject,
                record_count,
                last_sequence,
            )
        )
    return tuple(
        sorted(
            choices,
            key=lambda choice: (
                -choice.last_workspace_sequence,
                choice.scope_kind.value,
                choice.scope_id,
            ),
        )
    )


def project_exchange_agents(
    session_id: str,
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    inventory: object,
) -> tuple[ExchangeAgentChoice, ...]:
    """Project every canonical agent for one inventory-backed exact scope."""

    if type(session_id) is not str or not session_id:
        raise ValueError("session_id must be non-empty text")
    if type(scope_kind) is not AgentExchangeScopeKind:
        raise TypeError("scope_kind must be an AgentExchangeScopeKind")
    if type(scope_id) is not str or not scope_id:
        raise ValueError("scope_id must be non-empty text")
    if type(inventory) is not tuple or any(
        type(item) is not AgentExchangeInventoryItem for item in inventory
    ):
        raise TypeError("inventory must contain AgentExchangeInventoryItem values")

    by_agent: dict[str, AgentExchangeInventoryItem] = {}
    for item in inventory:
        selection = item.selection
        if selection.session_id != session_id:
            raise AgentExchangeProjectionError(
                "agent exchange inventory crossed the attached session"
            )
        if selection.scope_kind is not scope_kind or selection.scope_id != scope_id:
            continue
        if selection.agent_id in by_agent:
            raise AgentExchangeProjectionError(
                "agent exchange inventory contains duplicate streams"
            )
        by_agent[selection.agent_id] = item
    if not by_agent:
        raise AgentExchangeProjectionError(
            "selected agent exchange scope is absent from inventory"
        )

    choices: list[ExchangeAgentChoice] = []
    for agent_id in ROSTER:
        item = by_agent.get(agent_id)
        choices.append(
            ExchangeAgentChoice(
                agent_id=agent_id,
                display_name=AGENT_SPECS[agent_id].display_name,
                selection=None if item is None else item.selection,
                record_count=0 if item is None else item.record_count,
                last_workspace_sequence=(
                    None if item is None else item.last_workspace_sequence
                ),
            )
        )
    return tuple(choices)


__all__ = [
    "AgentExchangeProjectionError",
    "DEBATE_SUBJECT_UNAVAILABLE",
    "ExchangeAgentChoice",
    "ExchangeScopeChoice",
    "debate_subject_from_messages",
    "project_exchange_agents",
    "project_exchange_scopes",
]
