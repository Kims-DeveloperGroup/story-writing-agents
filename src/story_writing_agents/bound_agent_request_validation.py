"""Validate conversation requests against their bound agent metadata."""

from __future__ import annotations

from typing import Protocol

import story_writing_agents.provider_thread_records as provider_thread_records
from .agents import ROSTER
from .conversation_retrieval import ConversationRetrievalRequest
from .errors import BackendProtocolError, SessionIdError
from .models import AgentDefinition, ConversationRequest


class _BindingMetadata(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...


def validate_conversation_request(
    binding: _BindingMetadata, request: ConversationRequest
) -> ConversationRequest:
    """Return the canonical request for the exact binding."""
    if type(request) is not ConversationRequest:
        raise TypeError("request must be a ConversationRequest")
    definition = binding.definition
    if (
        type(definition) is not AgentDefinition
        or not isinstance(definition.agent_id, str)
        or definition.agent_id not in ROSTER
        or not isinstance(definition.backend, str)
        or not definition.backend
        or binding.vendor != definition.backend
        or not isinstance(definition.fingerprint, str)
        or provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(
            definition.fingerprint
        ) is None
    ):
        raise BackendProtocolError(
            "bound agent definition is invalid for conversation routing"
        )
    try:
        validated = ConversationRequest(
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            agent_id=request.agent_id,
            content=request.content,
            context=request.context,
            story_session_context=request.story_session_context,
        )
    except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
        raise BackendProtocolError("conversation request is invalid") from exc
    if validated.agent_id != definition.agent_id:
        raise BackendProtocolError(
            "request was routed through the wrong bound agent: "
            f"{validated.agent_id} != {definition.agent_id}"
        )
    return validated


def validate_conversation_retrieval_request(
    binding: _BindingMetadata, request: ConversationRetrievalRequest
) -> ConversationRetrievalRequest:
    """Return the canonical request for the exact binding."""
    if type(request) is not ConversationRetrievalRequest:
        raise TypeError("request must be a ConversationRetrievalRequest")
    definition = binding.definition
    if (
        type(definition) is not AgentDefinition
        or not isinstance(definition.agent_id, str)
        or definition.agent_id not in ROSTER
        or not isinstance(definition.backend, str)
        or not definition.backend
        or binding.vendor != definition.backend
        or not isinstance(definition.fingerprint, str)
        or provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(
            definition.fingerprint
        ) is None
    ):
        raise BackendProtocolError(
            "bound agent definition is invalid for conversation retrieval"
        )
    try:
        validated = ConversationRetrievalRequest(
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            agent_id=request.agent_id,
            content=request.content,
            context=request.context,
            story_session_context=request.story_session_context,
            rag_context=request.rag_context,
        )
    except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
        raise BackendProtocolError(
            "conversation retrieval request is invalid"
        ) from exc
    if validated.agent_id != definition.agent_id:
        raise BackendProtocolError(
            "request was routed through the wrong bound agent: "
            f"{validated.agent_id} != {definition.agent_id}"
        )
    return validated
