"""Content-free collaboration event drafting and ordered journal operations."""
from __future__ import annotations
import hashlib
import uuid
from ..errors import BackendProtocolError
from ..conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAppendReceipt,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationEvent as CollaborationEventV2,
    CollaborationHistoryProjection,
    CollaborationInvocationKind,
    CollaborationMessagePayload,
    CollaborationPayload,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    project_collaboration_history,
)
from typing import Callable
from ..document_plan import DocumentNodeBinding
from ..agents import LEAD_ORCHESTRATOR, SPECIALIST_IDS
from ..agent_context.accounting import ProviderCallAccounting, ProviderUsage


USER_ACTOR = CollaborationActor(CollaborationActorKind.USER)

LEAD_ACTOR = CollaborationActor(
    CollaborationActorKind.LEAD,
    LEAD_ORCHESTRATOR,
)

RUNTIME_ACTOR = CollaborationActor(CollaborationActorKind.RUNTIME)

def append_provider_call_fact(*,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    invocation_id: str,
    call_id: str,
    ordinal: int,
    call_kind: CollaborationProviderCallKind,
    state: CollaborationProviderCallState,
    role: str,
    invocation_kind: CollaborationInvocationKind,
    validation_details: tuple[str,
        ...
    ]=(),
    usage: ProviderUsage | None=None,
    accounting: ProviderCallAccounting | None=None,
    document_node: DocumentNodeBinding | None=None,
    document_goal=None,
    document_budget_task_id=None,
    read_context: Callable[[str,
            str
        ],
        CollaborationHistoryProjection
    ],
    append_batch: Callable[[CollaborationEventBatch],
        CollaborationAppendReceipt
    ]
) -> None:
    projection = read_context(
        session_id,
        conversation_id,
    )
    append_drafts(
        projection.last_sequence,
        (
            draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                sender=RUNTIME_ACTOR,
                recipient=actor_for_agent(role),
                payload=CollaborationProviderCallPayload(
                    document_node=document_node, document_goal=document_goal, document_budget_task_id=document_budget_task_id,
                    invocation_id=invocation_id,
                    call_id=call_id,
                    ordinal=ordinal,
                    role=role,
                    invocation_kind=invocation_kind,
                    call_kind=call_kind,
                    state=state,
                    validation_details=validation_details,
                    usage=usage,
                    accounting=accounting,
                ),
            ),
        ),
        append_batch=append_batch,
    )

def append_validation_messages(*,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    invocation_id: str,
    details_by_call: tuple[tuple[str,
            tuple[str,
                ...
            ]
        ],
        ...
    ],
    read_events: Callable[[str,
            str
        ],
        tuple[CollaborationEventV2,
            ...
        ]
    ],
    append_batch: Callable[[CollaborationEventBatch],
        CollaborationAppendReceipt
    ]
) -> None:
    """Append ordered bounded chunks before the established fallback message."""

    drafts = tuple(
        draft_with_id(
            event_id=deterministic_id(
                "e", invocation_id, call_id, "validation", str(index),
            ),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.MESSAGE,
            sender=RUNTIME_ACTOR,
            recipient=USER_ACTOR,
            payload=CollaborationMessagePayload(
                part,
                validation_details=(part,),
                validation_invocation_id=invocation_id,
                validation_call_id=call_id,
            ),
        )
        for call_id, details in details_by_call
        for index, part in enumerate(details)
    )
    events = read_events(
        session_id, conversation_id,
    )
    by_id = {event.event_id: event for event in events}
    existing = tuple(by_id.get(draft.event_id) for draft in drafts)
    if any(event is not None for event in existing):
        if all(
            event is not None
            and event.session_id == draft.session_id
            and event.conversation_id == draft.conversation_id
            and event.turn_id == draft.turn_id
            and event.step_id == draft.step_id
            and event.kind is draft.kind
            and event.sender == draft.sender
            and event.recipient == draft.recipient
            and event.payload == draft.payload
            for event, draft in zip(existing, drafts, strict=True)
        ):
            return
        raise BackendProtocolError("diagnostic message append is incomplete")
    projection = project_collaboration_history(events)
    append_drafts(
        projection.last_sequence,
        drafts,
        batch_id=deterministic_id("b", invocation_id, "validation"),
        append_batch=append_batch,
    )

def append_drafts(expected_last_sequence: int,
    drafts: tuple[CollaborationEventDraft,
        ...
    ],
    *,
    batch_id: str | None=None,
    append_batch: Callable[[CollaborationEventBatch],
        CollaborationAppendReceipt
    ]
) -> object:
    batch = CollaborationEventBatch(
        batch_id=new_id("b") if batch_id is None else batch_id,
        expected_last_sequence=expected_last_sequence,
        events=drafts,
    )
    return append_batch(batch)

def draft(*,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    kind: CollaborationEventKind,
    sender: CollaborationActor,
    recipient: CollaborationActor,
    payload: CollaborationPayload
) -> CollaborationEventDraft:
    return draft_with_id(
        event_id=new_id("e"),
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        kind=kind,
        sender=sender,
        recipient=recipient,
        payload=payload,
    )

def draft_with_id(*,
    event_id: str,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    kind: CollaborationEventKind,
    sender: CollaborationActor,
    recipient: CollaborationActor,
    payload: CollaborationPayload
) -> CollaborationEventDraft:
    return CollaborationEventDraft(
        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
        event_id=event_id,
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        step_id=step_id,
        kind=kind,
        sender=sender,
        recipient=recipient,
        payload=payload,
    )

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

def deterministic_id(prefix: str, *parts: str) -> str:
    value = "\x1f".join(parts).encode("utf-8", errors="strict")
    return f"{prefix}_{hashlib.sha256(value).hexdigest()[:32]}"

def actor_for_agent(agent_id: str) -> CollaborationActor:
    if agent_id == LEAD_ORCHESTRATOR:
        return LEAD_ACTOR
    if agent_id in SPECIALIST_IDS:
        return CollaborationActor(CollaborationActorKind.SPECIALIST, agent_id)
    raise ValueError("provider call role must be canonical")
