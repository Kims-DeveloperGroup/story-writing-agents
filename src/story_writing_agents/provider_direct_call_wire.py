"""Outbound wire payloads and schemas for direct provider calls."""

from __future__ import annotations

from typing import Any

from .models import (
    AgentRequest,
    ConversationRequest,
    Phase,
    validate_reviewed_proposal_identity,
)
from .provider_request_mapping import project_direct_payload
from .provider_schema_projection import project_direct_schema


def phase_request_payload(request: AgentRequest) -> dict[str, Any]:
    """Return the exact provider payload, adding context only when included."""

    payload = {
        "session_id": request.session_id,
        "debate_id": request.debate_id,
        "agent_id": request.agent_id,
        "phase": request.phase.value,
        "round_number": request.round_number,
        "revision_id": request.revision_id,
        "task": request.task,
        "user_payload": request.user_payload,
        "candidate_payload": request.candidate_payload,
        "messages": [message.to_dict() for message in request.messages],
    }
    if request.review_only:
        payload["intent"] = "review_only"
    if request.expected_reviewed_proposal is not None:
        payload["expected_reviewed_proposal"] = {
            "reviewed_proposal_id": request.expected_reviewed_proposal[0],
            "reviewed_proposal_digest": request.expected_reviewed_proposal[1],
        }
    if request.story_session_context is not None:
        payload["story_session_context"] = request.story_session_context.to_dict()
    else:
        payload["agent_state"] = [entry.to_dict() for entry in request.agent_state]
    if (
        request.story_session_context is None
        and request.conversation_context is not None
    ):
        payload["conversation_context"] = [
            {
                "turn_id": event.turn_id,
                "sender": event.sender,
                "recipient": event.recipient,
                "kind": event.kind.value,
                "content": event.content,
            }
            for event in request.conversation_context.events
        ]
    return project_direct_payload(payload)


def conversation_request_payload(
    request: ConversationRequest,
) -> dict[str, Any]:
    """Project a request into the exact payload, preserving context order."""

    payload = {
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "turn_id": request.turn_id,
        "agent_id": request.agent_id,
        "content": request.content,
    }
    if request.story_session_context is not None:
        payload["story_session_context"] = request.story_session_context.to_dict()
    else:
        payload["context"] = [
            {
                "turn_id": event.turn_id,
                "sender": event.sender,
                "recipient": event.recipient,
                "kind": event.kind.value,
                "content": event.content,
            }
            for event in request.context.events
        ]
    return project_direct_payload(payload)


def conversation_output_schema() -> dict[str, Any]:
    """Return the strict semantic-only conversation-reply JSON schema."""

    properties = {"message": {"type": "string", "minLength": 1}}
    return project_direct_schema({
        "description": (
            "Answer the current conversation turn in nonempty UTF-8 response_text, "
            "grounded in the supplied context; return no runtime correlation fields."
        ),
        "type": "object",
        "properties": properties,
        "required": ["message"],
        "additionalProperties": False,
    }, "conversation")


def phase_output_schema(
    phase: Phase, *, expected_reviewed_proposal: tuple[str, str] | None = None,
    review_only: bool = False,
) -> dict[str, Any]:
    if review_only and phase not in {Phase.AUDIT, Phase.SYNTHESIZE}:
        raise ValueError("review-only provider schema cannot authorize authoring")
    if expected_reviewed_proposal is not None:
        if phase is not Phase.FINALIZE:
            raise ValueError("expected reviewed identity requires finalization")
        validate_reviewed_proposal_identity(expected_reviewed_proposal)
    if phase is Phase.AUDIT:
        properties: dict[str, Any] = {
            "verdict": {"type": "string", "enum": ["approve", "conflict"]},
            "message": {"type": "string"},
        }
        required = ["verdict", "message"]
    elif phase is Phase.SYNTHESIZE:
        properties = {
            "next": {
                "type": "string",
                "enum": ["review_completion", "deadlock"] if review_only else ["repair", "finalization", "deadlock"],
            },
            "message": {"type": "string"},
        }
        required = ["next", "message"]
    elif phase is Phase.FINALIZE:
        properties = {
            "reviewed_proposal_id": {"type": "string"},
            "reviewed_proposal_digest": {
                "type": "string", "description": "sha256: followed by exactly 64 lowercase hexadecimal digits.",
            },
        }
        required = [
            "reviewed_proposal_id",
            "reviewed_proposal_digest",
        ]
        if expected_reviewed_proposal is not None:
            properties["reviewed_proposal_id"]["enum"] = [expected_reviewed_proposal[0]]
            properties["reviewed_proposal_digest"]["enum"] = [expected_reviewed_proposal[1]]
    else:
        properties = {"message": {"type": "string"}}
        required = ["message"]
    descriptions = {
        Phase.DRAFT: (
            "Write the initial complete candidate from the authorized task and sources in"
            " response_text; return text without changing files."
        ),
        Phase.PROPOSE_REVISION: (
            "Revise the current candidate to address the supplied conflicts and operator "
            "constraints; return its complete new text without changing files."
        ),
        Phase.AUDIT: (
            "Review the exact current candidate independently: use no_conflicts_found "
            "when supported, or conflicts_found for unresolved conflicts. Explain the "
            "grounded findings in response_text."
        ),
        Phase.SYNTHESIZE: (
            "Integrate all review findings: choose revise_candidate for actionable "
            "unresolved issues, finalize_reviewed_proposal only when ready, or "
            "report_deadlock when conflicts cannot be resolved within the allowed "
            "process. Explain the choice without erasing concerns."
        ),
        Phase.FINALIZE: (
            "Confirm only the exact offered reviewed_proposal_id and "
            "reviewed_proposal_digest; this confirms an immutable reviewed candidate and "
            "does not publish, Apply, or Commit it."
        ),
    }
    if review_only:
        descriptions[phase] = (
            "Review only the exact supplied original. Preserve its bytes and every concern; "
            "report findings and optional suggestions without drafting or artifact authority. "
            + ("For this synthesis choose complete_review or report_deadlock." if phase is Phase.SYNTHESIZE else "Report only findings on this exact candidate.")
        )
    return project_direct_schema({
        "description": descriptions[phase],
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }, phase.value)


__all__ = (
    "conversation_output_schema",
    "conversation_request_payload",
    "phase_output_schema",
    "phase_request_payload",
)
