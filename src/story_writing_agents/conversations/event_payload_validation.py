"""Closed conversation event direction and actor-payload correlation."""

from __future__ import annotations

from .event_contracts.control_records import CollaborationProviderCallPayload
from .event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationSpecialistReplyPayload,
)
from .event_contracts.proposal_inputs import CollaborationActor
from .event_vocabulary import (
    CollaborationActorKind,
    CollaborationDirection,
    CollaborationEventKind,
)


def collaboration_direction(
    sender: CollaborationActor,
    recipient: CollaborationActor,
) -> CollaborationDirection:
    directions = {
        (
            CollaborationActorKind.USER,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.USER_TO_LEAD,
        (
            CollaborationActorKind.USER,
            CollaborationActorKind.RUNTIME,
        ): CollaborationDirection.USER_TO_RUNTIME,
        (
            CollaborationActorKind.LEAD,
            CollaborationActorKind.USER,
        ): CollaborationDirection.LEAD_TO_USER,
        (
            CollaborationActorKind.LEAD,
            CollaborationActorKind.SPECIALIST,
        ): CollaborationDirection.LEAD_TO_SPECIALIST,
        (
            CollaborationActorKind.SPECIALIST,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.SPECIALIST_TO_LEAD,
        (
            CollaborationActorKind.SPECIALIST,
            CollaborationActorKind.USER,
        ): CollaborationDirection.SPECIALIST_TO_USER,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.USER,
        ): CollaborationDirection.RUNTIME_TO_USER,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.RUNTIME_TO_LEAD,
        (
            CollaborationActorKind.RUNTIME,
            CollaborationActorKind.SPECIALIST,
        ): CollaborationDirection.RUNTIME_TO_SPECIALIST,
        (
            CollaborationActorKind.ARTIFACT,
            CollaborationActorKind.USER,
        ): CollaborationDirection.ARTIFACT_TO_USER,
        (
            CollaborationActorKind.ARTIFACT,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.ARTIFACT_TO_LEAD,
        (
            CollaborationActorKind.GIT,
            CollaborationActorKind.USER,
        ): CollaborationDirection.GIT_TO_USER,
        (
            CollaborationActorKind.GIT,
            CollaborationActorKind.LEAD,
        ): CollaborationDirection.GIT_TO_LEAD,
    }
    try:
        return directions[(sender.kind, recipient.kind)]
    except KeyError as exc:
        raise ValueError("unsupported collaboration actor direction") from exc


def validate_collaboration_direction(
    *,
    kind: CollaborationEventKind,
    sender: CollaborationActor,
    recipient: CollaborationActor,
    payload: object,
) -> None:
    sender_kind = sender.kind
    recipient_kind = recipient.kind
    if (
        sender_kind is CollaborationActorKind.SPECIALIST
        and recipient_kind is CollaborationActorKind.SPECIALIST
    ):
        raise ValueError("specialist-to-specialist delegation is forbidden")
    allowed: dict[
        CollaborationEventKind,
        set[tuple[CollaborationActorKind, CollaborationActorKind]],
    ] = {
        CollaborationEventKind.LEAD_TASK_DECISION: {(CollaborationActorKind.USER, CollaborationActorKind.RUNTIME)},
        CollaborationEventKind.LEAD_TASK_TRANSITION: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.USER)},
        CollaborationEventKind.LEAD_MATERIAL_CAPTURE: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.USER)},
        CollaborationEventKind.LEAD_DISCUSSION: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.USER)},
        CollaborationEventKind.LEAD_PUBLIC_RESPONSE: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.USER)},

        CollaborationEventKind.ARTIFACT_OBJECTIVE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.LEAD_RECOMMENDATION_REQUEST: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.LEAD_RECOMMENDATION_DECISION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.LEAD_RECOMMENDATION_STARTED: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
        },
        CollaborationEventKind.LEAD_RECOMMENDATION_RESULT: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.LEAD_EXPLORATION_REQUEST: {(CollaborationActorKind.USER, CollaborationActorKind.LEAD)},
        CollaborationEventKind.LEAD_EXPLORATION_RESULT: {(CollaborationActorKind.LEAD, CollaborationActorKind.USER)},
        CollaborationEventKind.EXPLORATION_ADOPTION: {(CollaborationActorKind.USER, CollaborationActorKind.LEAD)},
        CollaborationEventKind.EDITORIAL_RECORD: {(CollaborationActorKind.USER, CollaborationActorKind.LEAD)},
        CollaborationEventKind.PREMISE_DECISION: {(CollaborationActorKind.USER, CollaborationActorKind.LEAD)},
        CollaborationEventKind.PREMISE_SELECTION: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.USER)},
        CollaborationEventKind.PREMISE_FRESH_WORK_ADMISSION: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD)},
        CollaborationEventKind.MESSAGE: {
            (CollaborationActorKind.USER, CollaborationActorKind.LEAD),
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.LEAD),
            (CollaborationActorKind.GIT, CollaborationActorKind.USER),
            (CollaborationActorKind.GIT, CollaborationActorKind.LEAD),
        },
        CollaborationEventKind.ASSIGNMENT_DISPATCH: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
            (CollaborationActorKind.LEAD, CollaborationActorKind.SPECIALIST),
        },
        CollaborationEventKind.SPECIALIST_REPLY: {
            (CollaborationActorKind.SPECIALIST, CollaborationActorKind.LEAD),
            (CollaborationActorKind.SPECIALIST, CollaborationActorKind.USER),
        },
        CollaborationEventKind.LEAD_SYNTHESIS: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STEP_STATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
        },
        CollaborationEventKind.CONFIRMATION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.EXECUTION_RESULT: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.INTERRUPTION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.TERMINAL_OUTCOME: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
        },
        CollaborationEventKind.LEAD_GROUP_TRACE: {(CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD)},
        CollaborationEventKind.LEAD_OUTCOME_FAILURE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_PROPOSAL_STATE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_REVIEW_FAILURE: {
            (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_REQUEST: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_DECISION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.ARTIFACT_APPLICATION_RESULT: {
            (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STORY_COMMIT_REQUEST: {
            (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
        },
        CollaborationEventKind.STORY_COMMIT_DECISION: {
            (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
        },
        CollaborationEventKind.STORY_COMMIT_RESULT: {
            (CollaborationActorKind.GIT, CollaborationActorKind.USER),
        },
    }
    if (sender_kind, recipient_kind) not in allowed[kind]:
        raise ValueError(
            f"invalid actor direction for collaboration event {kind.value!r}"
        )
    if kind is CollaborationEventKind.ASSIGNMENT_DISPATCH:
        assert isinstance(payload, CollaborationAssignmentDispatchPayload)
        if recipient.agent_id != payload.assignment.specialist_id:
            raise ValueError("dispatch recipient must match the exact assignment")
    if kind is CollaborationEventKind.SPECIALIST_REPLY:
        assert isinstance(payload, CollaborationSpecialistReplyPayload)
        if sender.agent_id is None:
            raise ValueError("specialist reply sender requires a specialist ID")
    if kind is CollaborationEventKind.PROVIDER_CALL_ACCOUNTING:
        assert isinstance(payload, CollaborationProviderCallPayload)
        if recipient.agent_id != payload.role:
            raise ValueError("provider-call recipient must match its exact role")
