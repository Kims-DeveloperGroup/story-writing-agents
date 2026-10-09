"""Bounded conversation context and transcript-access contracts."""

from __future__ import annotations

from ..authoring_admission import AdmissionSourceKind, AuthoringAdmissionBasis

from ..lead_task_transitions import (
    LeadTaskAction, LeadTaskDecision, LeadTaskRef, LeadMaterialRef,
    LeadMaterialKind, LeadTaskTransitionReceipt, LeadMaterialCapture,
    LeadDiscussionReport, LeadPublicResponse, LeadPublicResponseAcceptance,
    task_material_sha256,
)
from .events import (
    CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
    CollaborationLeadMaterialCapturePayload, CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
)

from ..lead_premise_decisions import (
    PremiseAuthoringTaskRef,
    PremiseDecisionReport, PremiseDecisionSelection, PremiseSelectionCapture,
    PremiseFreshWorkAdmission, PremiseDecisionAction, PremiseChoicePolicy, PremiseDecisionCost, PremiseReportRef, PremiseAdmissionRef,
    premise_digest, premise_value_to_dict, resolve_premise_choice,
)
from .events import (
    CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload, encode_collaboration_event,
)

from ..editorial_state import (
    EditorialStateProjection, EditorialRecordRevision, EditorialApprovalState,
    EditorialAppliedFact, EditorialApplicationAttempt, EditorialProvenanceRef, EditorialStateIntegrityError,
)
from .events import CollaborationEditorialRecordPayload, encode_collaboration_event, CollaborationEvidenceAcquisition

from ..lead_exploration import LeadInteractionMode, LeadExplorationReport
from .events import (CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload, CollaborationExplorationAdoptionPayload)

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol

from ..errors import SessionIdError
from ..reply_trace import ReplyTrace
from ..structured_output_validation import validate_details
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringScope, AuthoringIntent, SpecialistAssignmentMode, authoring_source_scope_is_eligible,
    required_authoring_specialists,
)
from .events import (
    confirmed_authoring_proposals,
    validate_authoring_capture_pin,
    authoring_capture_identity,
    ArtifactDraftSource,
    ArtifactObjectiveCandidateBinding,
    ArtifactObjectiveFacts,
    ArtifactObjectiveFactsV2,
    ArtifactObjectiveNextStep,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    ArtifactObjectiveState,
    LeadObjectiveAction,
    LeadRecommendationActionKind,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationDirection,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationInvocationKind,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationPayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewDecision,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    ArtifactReviewSynthesisPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationResultPayload,
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
    StoryCommitDecisionPayload,
    StoryCommitOutcomeKind,
    StoryCommitNextAction,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
    LeadOutcomeKind,
    LeadOutcomeScope,
    collaboration_batch_digest,
    ConversationEvent,
    ConversationEventKind,
)
from ..git.story_commit_contracts import StoryCommitDecision
from ..writing_artifacts import ArtifactRenameOperation


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _validate_non_negative_integer(value: int, *, label: str) -> int:
    """Return a non-negative non-boolean integer without mutation.

    Other types raise ``TypeError`` and negative values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class ConversationContextPolicy:
    """Fixed caps applied when selecting complete turns for provider context."""

    max_complete_turns: int
    max_utf8_bytes: int

    def __post_init__(self) -> None:
        _validate_positive_integer(
            self.max_complete_turns,
            label="maximum complete turns",
        )
        _validate_positive_integer(
            self.max_utf8_bytes,
            label="maximum UTF-8 bytes",
        )


CONVERSATION_CONTEXT_POLICY = ConversationContextPolicy(
    max_complete_turns=24,
    max_utf8_bytes=32_768,
)


@dataclass(frozen=True, slots=True)
class ConversationContextWindow:
    """A bounded chronological suffix of complete conversation turns."""

    events: tuple[ConversationEvent, ...]
    total_complete_turns: int
    retained_turns: int
    omitted_turns: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple) or any(
            not isinstance(event, ConversationEvent) for event in self.events
        ):
            raise TypeError("context events must be a tuple of ConversationEvent")
        for value, label in (
            (self.total_complete_turns, "total complete turns"),
            (self.retained_turns, "retained turns"),
            (self.omitted_turns, "omitted turns"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            _validate_non_negative_integer(value, label=label)
        if self.total_complete_turns != self.retained_turns + self.omitted_turns:
            raise ValueError("context turn counts are inconsistent")
        if not isinstance(self.truncated, bool):
            raise TypeError("context truncated flag must be a boolean")
        if self.truncated is not (self.omitted_turns > 0):
            raise ValueError("context truncated flag is inconsistent")


def validate_conversation_context_window(
    context: ConversationContextWindow,
    *,
    session_id: str,
    conversation_id: str | None = None,
    excluded_turn_id: str | None = None,
) -> ConversationContextWindow:
    """Return a canonical, identity-bound conversation context window.

    The supplied immutable window is copied into built-in event/window types and
    validated against the owning session, optional conversation, optional
    excluded turn, pairing rules, ordering, metadata, and fixed policy caps. The
    operation performs no I/O or mutation; invalid identities or content raise
    ``SessionIdError``, ``TypeError``, or ``ValueError``.
    """

    _validate_identifier(session_id, label="session ID")
    if conversation_id is not None:
        _validate_identifier(conversation_id, label="conversation ID")
    if excluded_turn_id is not None:
        _validate_identifier(excluded_turn_id, label="excluded turn ID")
    if type(context) is not ConversationContextWindow:
        raise TypeError("conversation context must be a ConversationContextWindow")

    try:
        source_events = context.events
        total_complete_turns = context.total_complete_turns
        retained_turns_metadata = context.retained_turns
        omitted_turns = context.omitted_turns
        retained_utf8_bytes_metadata = context.retained_utf8_bytes
        truncated = context.truncated
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc
    if type(source_events) is not tuple:
        raise ValueError("conversation context metadata is invalid")

    canonical_events: list[ConversationEvent] = []
    for event in source_events:
        if not isinstance(event, ConversationEvent):
            raise ValueError("conversation context contains an invalid event")
        try:
            canonical_events.append(
                ConversationEvent(
                    schema_version=event.schema_version,
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    turn_id=event.turn_id,
                    sequence=event.sequence,
                    timestamp=event.timestamp,
                    sender=event.sender,
                    recipient=event.recipient,
                    kind=event.kind,
                    content=event.content,
                )
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError("conversation context contains an invalid event") from exc

    try:
        canonical_context = ConversationContextWindow(
            events=tuple(canonical_events),
            total_complete_turns=total_complete_turns,
            retained_turns=retained_turns_metadata,
            omitted_turns=omitted_turns,
            retained_utf8_bytes=retained_utf8_bytes_metadata,
            truncated=truncated,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("conversation context metadata is invalid") from exc

    turns: dict[str, list[ConversationEvent]] = {}
    previous_sequence = 0
    retained_utf8_bytes = 0
    observed_conversation_id = conversation_id
    for event in canonical_context.events:
        if event.session_id != session_id:
            raise ValueError("conversation context contains a different session")
        if observed_conversation_id is None:
            observed_conversation_id = event.conversation_id
        elif event.conversation_id != observed_conversation_id:
            raise ValueError("conversation context contains a different conversation")
        if excluded_turn_id is not None and event.turn_id == excluded_turn_id:
            raise ValueError("conversation context contains the excluded turn")
        if event.sequence <= previous_sequence:
            raise ValueError(
                "conversation context event sequences must be strictly increasing"
            )
        previous_sequence = event.sequence
        retained_utf8_bytes += len(event.content.encode("utf-8"))
        turns.setdefault(event.turn_id, []).append(event)

    for turn_id, events in turns.items():
        if len(events) != 2:
            raise ValueError(f"conversation context turn is incomplete: {turn_id}")
        users = [
            event
            for event in events
            if event.kind is ConversationEventKind.USER_MESSAGE
        ]
        outcomes = [
            event
            for event in events
            if event.kind is not ConversationEventKind.USER_MESSAGE
        ]
        if len(users) != 1 or len(outcomes) != 1:
            raise ValueError(
                f"conversation context turn is not correctly paired: {turn_id}"
            )
        user, outcome = users[0], outcomes[0]
        if user.sequence >= outcome.sequence:
            raise ValueError(
                f"conversation context outcome precedes its user event: {turn_id}"
            )
        if (
            outcome.kind is ConversationEventKind.AGENT_REPLY
            and outcome.sender != user.recipient
        ):
            raise ValueError(
                "conversation context reply sender does not match addressed agent"
            )

    retained_turns = len(turns)
    if canonical_context.retained_turns != retained_turns:
        raise ValueError("conversation context retained-turn metadata is inconsistent")
    if canonical_context.retained_utf8_bytes != retained_utf8_bytes:
        raise ValueError("conversation context byte metadata is inconsistent")
    if retained_turns > CONVERSATION_CONTEXT_POLICY.max_complete_turns:
        raise ValueError("conversation context exceeds the retained-turn cap")
    if retained_utf8_bytes > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes:
        raise ValueError("conversation context exceeds the UTF-8 byte cap")
    if all(type(event) is ConversationEvent for event in source_events):
        return context
    return canonical_context


def select_bounded_conversation_context(
    events: tuple[ConversationEvent, ...],
) -> ConversationContextWindow:
    """Select the fixed-policy newest suffix of complete transcript turns.

    ``events`` must be an exact tuple of canonical events from one transcript
    in contiguous storage order starting at one. Unmatched user events remain
    valid transcript history but are excluded from provider context. The pure
    operation performs no I/O or mutation and raises ``TypeError`` or
    ``ValueError`` when its snapshot violates the transcript contract.
    """

    if type(events) is not tuple:
        raise TypeError("conversation events must be an exact tuple")

    transcript_identity: tuple[str, str] | None = None
    user_events: dict[str, ConversationEvent] = {}
    outcomes: dict[str, ConversationEvent] = {}
    for ordinal, event in enumerate(events, start=1):
        if type(event) is not ConversationEvent:
            raise TypeError("conversation events must be canonical events")
        try:
            canonical = ConversationEvent(
                schema_version=event.schema_version,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=event.sequence,
                timestamp=event.timestamp,
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
        except (AttributeError, SessionIdError, TypeError, ValueError) as exc:
            raise ValueError(
                "conversation transcript contains an invalid event"
            ) from exc
        if canonical != event:
            raise ValueError("conversation transcript contains a noncanonical event")

        identity = (event.session_id, event.conversation_id)
        if transcript_identity is None:
            transcript_identity = identity
        elif identity != transcript_identity:
            raise ValueError("conversation events span multiple transcripts")
        if event.sequence != ordinal:
            raise ValueError("conversation event sequences must be contiguous from one")

        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError("conversation turn has duplicate user events")
            user_events[event.turn_id] = event
            continue
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError("conversation outcome precedes its user event")
        if event.turn_id in outcomes:
            raise ValueError("conversation turn has duplicate outcomes")
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "conversation reply sender does not match the addressed agent"
            )
        outcomes[event.turn_id] = event

    complete_turns = [
        (event, outcomes[event.turn_id])
        for event in events
        if event.kind is ConversationEventKind.USER_MESSAGE
        and event.turn_id in outcomes
    ]
    retained_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
    retained_utf8_bytes = 0
    for turn in reversed(complete_turns):
        turn_utf8_bytes = sum(
            len(event.content.encode("utf-8", errors="strict")) for event in turn
        )
        if (
            len(retained_reversed) >= CONVERSATION_CONTEXT_POLICY.max_complete_turns
            or retained_utf8_bytes + turn_utf8_bytes
            > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes
        ):
            break
        retained_reversed.append(turn)
        retained_utf8_bytes += turn_utf8_bytes

    retained = tuple(reversed(retained_reversed))
    retained_events = tuple(
        sorted(
            (event for turn in retained for event in turn),
            key=lambda event: event.sequence,
        )
    )
    total_complete_turns = len(complete_turns)
    retained_turns = len(retained)
    omitted_turns = total_complete_turns - retained_turns
    return ConversationContextWindow(
        events=retained_events,
        total_complete_turns=total_complete_turns,
        retained_turns=retained_turns,
        omitted_turns=omitted_turns,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted_turns > 0,
    )


class CollaborationNextAction(StrEnum):
    """History-derived next safe action; it is evidence, never authority."""

    NONE = "none"
    SUBMIT_CONFIRMATION = "submit_confirmation"
    START_OR_RECONFIRM = "start_or_reconfirm"
    FINALIZE_RESULT = "finalize_result"
    CONTINUE_WITH_LEAD = "continue_with_lead"
    INSPECT_AND_RESOLVE_OUTCOME = "inspect_and_resolve_outcome"
    REPAIR_AUTHORITATIVE_HISTORY = "repair_authoritative_history"
    REPLY_TO_LEAD = "reply_to_lead"
    RETRY_LEAD_TURN = "retry_lead_turn"
    SUBMIT_APPLICATION_DECISION = "submit_application_decision"
    SUBMIT_COMMIT_DECISION = "submit_commit_decision"
    REPREPARE_COMMIT_REQUEST = "reprepare_commit_request"
    SUBMIT_RECOMMENDATION_DECISION = "submit_recommendation_decision"
    FINALIZE_RECOMMENDATION = "finalize_recommendation"
    CHOOSE_PREMISE = "choose_premise"


@dataclass(frozen=True, slots=True)
class ArtifactObjective:
    """Current objective facts derived from one complete owner conversation."""

    facts: ArtifactObjectiveFacts | ArtifactObjectiveFactsV2
    latest_event_id: str
    state: ArtifactObjectiveState
    next_step: ArtifactObjectiveNextStep | None
    draft_sources: tuple[ArtifactDraftSource, ...]
    candidate_binding: ArtifactObjectiveCandidateBinding | None = None
    satisfied_by_event_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.facts) not in {ArtifactObjectiveFacts, ArtifactObjectiveFactsV2}:
            raise TypeError("projected objective facts are invalid")
        _validate_identifier(self.latest_event_id, label="objective event ID")
        if type(self.state) is not ArtifactObjectiveState:
            raise TypeError("projected objective state is invalid")
        if self.next_step is not None and type(self.next_step) is not ArtifactObjectiveNextStep:
            raise TypeError("projected objective next step is invalid")
        if type(self.draft_sources) is not tuple or any(
            type(item) is not ArtifactDraftSource
            or item.conversation_id != self.facts.origin_conversation_id
            for item in self.draft_sources
        ) or len({item.event_id for item in self.draft_sources}) != len(self.draft_sources):
            raise ValueError("projected objective draft sources are invalid")
        if self.candidate_binding is not None and type(self.candidate_binding) is not ArtifactObjectiveCandidateBinding:
            raise TypeError("projected objective candidate binding is invalid")
        if self.state is ArtifactObjectiveState.CANCELLED:
            if self.next_step is not None or self.candidate_binding is not None:
                raise ValueError("cancelled objective cannot select work")
        elif self.next_step is None or (
            self.next_step is ArtifactObjectiveNextStep.PREPARE
        ) is not (self.candidate_binding is not None):
            raise ValueError("projected objective step binding is inconsistent")
        if (self.state is ArtifactObjectiveState.SATISFIED) is not (
            self.satisfied_by_event_id is not None
        ):
            raise ValueError("objective satisfaction requires the exact Apply event")
        if self.satisfied_by_event_id is not None:
            _validate_identifier(self.satisfied_by_event_id, label="objective Apply event ID")
            if self.candidate_binding is None:
                raise ValueError("satisfied objective requires its candidate")


@dataclass(frozen=True, slots=True)
class LeadTaskView:
    ref: LeadTaskRef
    label: str
    status: str
    scope: AuthoringScope
    goal: str
    source_event_ids: tuple[str, ...] = ()
    pending_control_ids: tuple[str, ...] = ()
    submitted_call_ids: tuple[str, ...] = ()

    def decision(
        self, action: LeadTaskAction, *, text: str | None = None,
        material_refs: tuple[LeadMaterialRef, ...] = (),
    ) -> LeadTaskDecision:
        return LeadTaskDecision.from_task(
            self.ref, action, text=text, material_refs=material_refs,
        )


@dataclass(frozen=True, slots=True)
class LeadMaterialChoice:
    ref: LeadMaterialRef
    label: str
    preview: str
    text: str
    acceptance: str | None = None
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LeadPublicResponseView:
    ref: LeadMaterialRef
    response: LeadPublicResponse

    @property
    def text(self) -> str:
        return self.response.response_text


@dataclass(frozen=True, slots=True)
class LeadTaskDecisionResult:
    session_id: str
    conversation_id: str
    action: LeadTaskAction
    task_ref: LeadTaskRef | None
    transition_event_id: str
    turn: LeadTurnResult | None = None


@dataclass(frozen=True, slots=True)
class CollaborationHistoryProjection:
    """Pure deterministic summary of one complete authoritative v2 history."""

    session_id: str | None
    conversation_id: str | None
    last_sequence: int
    current_turn_id: str | None
    current_step_id: str | None
    current_proposal: CollaborationProposal | None
    current_state: CollaborationStepState | None
    confirmation_request_id: str | None
    confirmation_decision: CollaborationConfirmationDecision | None
    execution_attempt_id: str | None
    execution_result_event_id: str | None
    execution_result: CollaborationExecutionResult | None
    next_safe_action: CollaborationNextAction
    rounds_spent: int = 0
    physical_calls_spent: int = 0
    current_artifact_candidate: ArtifactReviewCandidatePayload | None = None
    current_artifact_state: ArtifactProposalState | None = None
    current_reviewed_artifact_proposal: ArtifactReviewedProposal | None = None
    current_artifact_application_request: ArtifactApplicationRequestPayload | None = None
    current_artifact_application_result: ArtifactApplicationResultPayload | None = None
    current_story_commit_request: StoryCommitRequestPayload | None = None
    current_story_commit_result: StoryCommitResultPayload | None = None
    artifact_objectives: tuple[ArtifactObjective, ...] = ()
    offered_draft_sources: tuple[ArtifactDraftSource, ...] = ()
    current_recommendation_request: LeadRecommendationRequestPayload | None = None
    current_recommendation_decision: LeadRecommendationDecisionPayload | None = None
    current_recommendation_start: LeadRecommendationStartedPayload | None = None
    current_recommendation_result: LeadRecommendationResultPayload | None = None
    current_recommendation_state: LeadRecommendationState | None = None

    premise_report: PremiseDecisionReport | None = None
    premise_report_event_id: str | None = None
    premise_decision: PremiseDecisionSelection | None = None
    premise_selection: PremiseSelectionCapture | None = None
    premise_admission: PremiseFreshWorkAdmission | None = None
    premise_admission_event_id: str | None = None

    available_tasks: tuple[LeadTaskView, ...] = ()
    selectable_materials: tuple[LeadMaterialChoice, ...] = ()
    public_responses: tuple[LeadPublicResponseView, ...] = ()
    discussion_context: LeadTaskTransitionReceipt | None = None

    def __post_init__(self) -> None:
        _validate_non_negative_integer(self.last_sequence, label="last sequence")
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError(
                "collaboration projection transcript identity is incomplete"
            )
        if self.session_id is not None:
            _validate_identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _validate_identifier(self.conversation_id, label="conversation ID")
        if self.current_turn_id is not None:
            _validate_identifier(self.current_turn_id, label="turn ID")
        if self.current_step_id is not None:
            _validate_identifier(self.current_step_id, label="step ID")
        if not isinstance(self.next_safe_action, CollaborationNextAction):
            raise TypeError("next safe action must be a CollaborationNextAction")
        _validate_non_negative_integer(self.rounds_spent, label="rounds spent")
        _validate_non_negative_integer(
            self.physical_calls_spent,
            label="physical calls spent",
        )
        if (
            self.current_artifact_candidate is not None
            and type(self.current_artifact_candidate)
            is not ArtifactReviewCandidatePayload
        ):
            raise TypeError("projected artifact candidate is invalid")
        if (
            self.current_artifact_state is not None
            and type(self.current_artifact_state) is not ArtifactProposalState
        ):
            raise TypeError("projected artifact state is invalid")
        if (
            self.current_reviewed_artifact_proposal is not None
            and type(self.current_reviewed_artifact_proposal)
            is not ArtifactReviewedProposal
        ):
            raise TypeError("projected reviewed artifact proposal is invalid")
        if (
            self.current_artifact_application_request is not None
            and type(self.current_artifact_application_request)
            is not ArtifactApplicationRequestPayload
        ):
            raise TypeError("projected artifact application request is invalid")
        if (
            self.current_artifact_application_result is not None
            and type(self.current_artifact_application_result)
            is not ArtifactApplicationResultPayload
        ):
            raise TypeError("projected artifact application result is invalid")
        if self.current_story_commit_request is not None and type(self.current_story_commit_request) is not StoryCommitRequestPayload:
            raise TypeError("projected story commit request is invalid")
        if self.current_story_commit_result is not None and type(self.current_story_commit_result) is not StoryCommitResultPayload:
            raise TypeError("projected story commit result is invalid")
        for value, expected_type in (
            (self.current_recommendation_request, LeadRecommendationRequestPayload),
            (self.current_recommendation_decision, LeadRecommendationDecisionPayload),
            (self.current_recommendation_start, LeadRecommendationStartedPayload),
            (self.current_recommendation_result, LeadRecommendationResultPayload),
            (self.current_recommendation_state, LeadRecommendationState),
        ):
            if value is not None and type(value) is not expected_type:
                raise TypeError("projected recommendation state is invalid")
        if (self.current_recommendation_request is None) is not (self.current_recommendation_state is None):
            raise ValueError("recommendation state requires its exact request")
        recommendation = self.current_recommendation_request
        if recommendation is None:
            if any(item is not None for item in (self.current_recommendation_decision,
                    self.current_recommendation_start, self.current_recommendation_result)):
                raise ValueError("recommendation facts require their immutable request")
        else:
            if recommendation.tail_sequence > self.last_sequence:
                raise ValueError("recommendation tail exceeds history")
            for item in (self.current_recommendation_decision, self.current_recommendation_start,
                         self.current_recommendation_result):
                if item is not None and _recommendation_key(item) != _recommendation_key(recommendation):
                    raise ValueError("recommendation projection facts do not correlate")
            if self.current_recommendation_state is LeadRecommendationState.PENDING:
                if any(item is not None for item in (self.current_recommendation_decision,
                        self.current_recommendation_start, self.current_recommendation_result)):
                    raise ValueError("pending recommendation cannot contain execution or closure")
            elif self.current_recommendation_state is LeadRecommendationState.RUNNING:
                if (self.current_recommendation_decision is None or self.current_recommendation_start is None
                    or self.current_recommendation_result is not None
                    or self.current_recommendation_decision.decision.kind is CollaborationConfirmationDecision.REJECT):
                    raise ValueError("running recommendation requires an actual execution decision")
            elif (self.current_recommendation_result is None
                  or self.current_recommendation_result.state is not self.current_recommendation_state):
                raise ValueError("terminal recommendation requires its exact closure")
        if type(self.artifact_objectives) is not tuple or any(
            type(item) is not ArtifactObjective
            or item.facts.origin_conversation_id != self.conversation_id
            for item in self.artifact_objectives
        ) or len({item.facts.objective_id for item in self.artifact_objectives}) != len(self.artifact_objectives):
            raise ValueError("projected artifact objectives are inconsistent")
        if type(self.offered_draft_sources) is not tuple or any(
            type(item) is not ArtifactDraftSource
            or item.conversation_id != self.conversation_id
            for item in self.offered_draft_sources
        ) or len({item.event_id for item in self.offered_draft_sources}) != len(self.offered_draft_sources):
            raise ValueError("projected offered draft sources are inconsistent")


def artifact_objective_context(
    projection: CollaborationHistoryProjection,
) -> dict[str, object]:
    """Expose exact objective and draft facts without any execution capability."""

    if type(projection) is not CollaborationHistoryProjection:
        raise TypeError("objective context requires a canonical history projection")
    return {
        "conversation_id": projection.conversation_id,
        "objectives": [asdict(item) for item in projection.artifact_objectives],
        "offered_draft_sources": [asdict(item) for item in projection.offered_draft_sources],
        "instruction": (
            "Only the origin conversation can continue or cancel its objective. "
            "Draft source event IDs identify exact visible drafting context, never "
            "artifact evidence or confirmation. Use current READ/attached evidence "
            "for artifact operations. OPEN remains open after advice, consultation, "
            "review, rejection or failure. Only exact successful structured Apply "
            "makes it SATISFIED; prose and null objective_intent make no transition."
        ),
    }


@dataclass(frozen=True, slots=True)
class LeadStepDecision:
    """One exact structured decision for the authoritative confirmation tail."""

    confirmation_request_id: str
    session_id: str
    conversation_id: str
    step_id: str
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    kind: CollaborationConfirmationDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "confirmation request ID"),
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
            (self.proposal_id, "proposal ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        _validate_positive_integer(self.proposal_revision, label="proposal revision")
        if type(self.proposal_digest) is not str:
            raise TypeError("proposal digest must be exact text")
        if _SHA256_DIGEST.fullmatch(self.proposal_digest) is None:
            raise ValueError("proposal digest is invalid")
        if type(self.kind) is not CollaborationConfirmationDecision:
            raise TypeError("decision kind must be CollaborationConfirmationDecision")
        if self.kind is CollaborationConfirmationDecision.REVISE:
            if type(self.revision_request) is not str or not self.revision_request:
                raise ValueError("revise decision requires a nonempty revision request")
            try:
                encoded = self.revision_request.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError("revision request must be strict UTF-8") from exc
            if len(encoded) > 32_768:
                raise ValueError("revision request exceeds the visible text limit")
        elif self.revision_request is not None:
            raise ValueError("only revise may carry a revision request")


@dataclass(frozen=True, slots=True)
class ArtifactProposalResult:
    """Public artifact-review identity paired with one lead turn."""

    state: ArtifactProposalState
    candidate: ArtifactReviewCandidatePayload
    reviewed_proposal: ArtifactReviewedProposal | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ArtifactProposalState:
            raise TypeError("artifact proposal result state is invalid")
        if type(self.candidate) is not ArtifactReviewCandidatePayload:
            raise TypeError("artifact proposal result candidate is invalid")
        if (
            self.reviewed_proposal is not None
            and type(self.reviewed_proposal) is not ArtifactReviewedProposal
        ):
            raise TypeError("artifact proposal result reviewed proposal is invalid")
        if (self.state is ArtifactProposalState.REVIEWED) is not (
            self.reviewed_proposal is not None
        ):
            raise ValueError("artifact proposal result review identity is inconsistent")
        if self.reviewed_proposal is not None and (
            self.reviewed_proposal.candidate != self.candidate.candidate
        ):
            raise ValueError("artifact proposal result candidate drifted")


@dataclass(frozen=True, slots=True)
class ArtifactApplicationDecision:
    """One exact structured decision for the authoritative application tail."""

    session_id: str
    conversation_id: str
    step_id: str
    confirmation_request_id: str
    reviewed_proposal_id: str
    reviewed_proposal_revision: int
    reviewed_proposal_digest: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    application_id: str
    application_request_digest: str
    kind: ArtifactApplicationDecisionKind
    revision_request: str | None = None

    def __post_init__(self) -> None:
        ArtifactApplicationDecisionPayload(
            confirmation_request_id=self.confirmation_request_id,
            confirmation_request_event_id="pending",
            reviewed_proposal_id=self.reviewed_proposal_id,
            reviewed_proposal_revision=self.reviewed_proposal_revision,
            reviewed_proposal_digest=self.reviewed_proposal_digest,
            candidate_id=self.candidate_id,
            candidate_revision=self.candidate_revision,
            candidate_digest=self.candidate_digest,
            application_id=self.application_id,
            application_request_digest=self.application_request_digest,
            decision=self.kind,
            revision_request=self.revision_request,
        )
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
        ):
            _validate_identifier(value, label=label)


@dataclass(frozen=True, slots=True)
class ArtifactApplicationDecisionResult:
    """Visible exact application outcome paired with its request and decision."""

    request: ArtifactApplicationRequestPayload
    decision: ArtifactApplicationDecisionKind
    outcome: ArtifactApplicationResultPayload
    message: str
    commit_request: StoryCommitRequestPayload | None = None

    def __post_init__(self) -> None:
        if type(self.request) is not ArtifactApplicationRequestPayload:
            raise TypeError("application decision result request is invalid")
        if type(self.decision) is not ArtifactApplicationDecisionKind:
            raise TypeError("application decision result decision is invalid")
        if type(self.outcome) is not ArtifactApplicationResultPayload:
            raise TypeError("application decision result outcome is invalid")
        if type(self.message) is not str or not self.message:
            raise ValueError("application decision result message is invalid")
        if self.commit_request is not None and type(self.commit_request) is not StoryCommitRequestPayload:
            raise TypeError("application decision commit request is invalid")
        if self.commit_request is not None and (
            self.commit_request.candidate.application_id != self.outcome.application_id
            or self.commit_request.candidate.proposal_id != self.outcome.reviewed_proposal_id
            or self.commit_request.candidate.correlation_id != self.outcome.application_request_digest
        ):
            raise ValueError("application decision commit request drifted")
        if (
            self.outcome.confirmation_request_id
            != self.request.confirmation_request_id
            or self.outcome.reviewed_proposal_id
            != self.request.reviewed_proposal.proposal_id
            or self.outcome.reviewed_proposal_digest
            != self.request.reviewed_proposal.proposal_digest
            or self.outcome.application_id
            != self.request.application_request.application_id
            or self.outcome.application_request_digest
            != self.request.application_request_digest
        ):
            raise ValueError("application decision result correlation drifted")


@dataclass(frozen=True, slots=True)
class StoryCommitPreparationRequest:
    """Reprepare only the exact current known Apply result tail."""

    session_id: str
    conversation_id: str
    step_id: str
    application_result_event_id: str
    application_id: str
    application_request_digest: str
    tail_event_id: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
            (self.application_result_event_id, "application result event ID"),
            (self.application_id, "application ID"),
            (self.tail_event_id, "commit preparation tail event ID"),
        ):
            _validate_identifier(value, label=label)
        if (
            type(self.application_request_digest) is not str
            or _SHA256_DIGEST.fullmatch(self.application_request_digest) is None
        ):
            raise ValueError("application request digest is invalid")


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionRequest:
    """One exact structured decision for the current prepared commit tail."""

    session_id: str
    conversation_id: str
    step_id: str
    confirmation_request_id: str
    application_result_event_id: str
    candidate_id: str
    request_digest: str
    confirmation_id: str
    kind: StoryCommitDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        StoryCommitDecisionPayload(
            confirmation_request_id=self.confirmation_request_id,
            confirmation_request_event_id="pending",
            application_result_event_id=self.application_result_event_id,
            candidate_id=self.candidate_id,
            request_digest=self.request_digest,
            confirmation_id=self.confirmation_id,
            decision=self.kind,
            revision_request=self.revision_request,
        )
        for value, label in ((self.session_id, "session ID"), (self.conversation_id, "conversation ID"), (self.step_id, "step ID")):
            _validate_identifier(value, label=label)


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionResult:
    """Visible exact local commit outcome paired with its request."""

    request: StoryCommitRequestPayload
    decision: StoryCommitDecision
    outcome: StoryCommitResultPayload
    message: str

    def __post_init__(self) -> None:
        if type(self.request) is not StoryCommitRequestPayload or type(self.decision) is not StoryCommitDecision or type(self.outcome) is not StoryCommitResultPayload:
            raise TypeError("story commit decision result is invalid")
        if type(self.message) is not str or not self.message:
            raise ValueError("story commit decision result message is invalid")
        if self.outcome.confirmation_request_id != self.request.confirmation_request_id or self.outcome.candidate_id != self.request.candidate.candidate_id or self.outcome.request_digest != self.request.request_digest:
            raise ValueError("story commit decision result correlation drifted")


@dataclass(frozen=True, slots=True)
class LeadTurnResult:
    """Visible result of one lead turn after runtime validation and persistence."""

    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    kind: LeadOutcomeKind
    message: str
    proposal: CollaborationProposal | None
    state: CollaborationStepState | None
    confirmation_request_id: str | None
    next_action: CollaborationNextAction
    evidence: CollaborationLeadEvidence | None = None
    artifact_proposal: ArtifactProposalResult | None = None
    artifact_application_request: ArtifactApplicationRequestPayload | None = None
    story_commit_request: StoryCommitRequestPayload | None = None
    recommendation_request: LeadRecommendationRequestPayload | None = None
    trace: ReplyTrace | None = None
    validation_details: tuple[str, ...] = ()
    story_commit_preparation_request: StoryCommitPreparationRequest | None = None
    interaction_mode: LeadInteractionMode = field(default=LeadInteractionMode.ORCHESTRATION, kw_only=True)
    exploration: LeadExplorationReport | None = field(default=None, kw_only=True)

    premise_report: PremiseDecisionReport | None = field(default=None, kw_only=True)
    premise_report_ref: PremiseReportRef | None = field(default=None, kw_only=True)

    discussion: LeadDiscussionReport | None = None
    public_responses: tuple[LeadPublicResponseView, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is LeadOutcomeKind.DISCUSSION:
            for value, label in ((self.session_id, "session ID"),
                                 (self.conversation_id, "conversation ID"),
                                 (self.turn_id, "turn ID"), (self.step_id, "step ID")):
                _validate_identifier(value, label=label)
            if self.evidence is not None or self.trace is not None or self.interaction_mode is not LeadInteractionMode.ORCHESTRATION:
                raise ValueError("discussion cannot carry provider or artifact execution evidence")
            if len(self.message.encode("utf-8")) > 32768:
                raise ValueError("discussion message exceeds the current visible text bound")
            if type(self.discussion) is not LeadDiscussionReport:
                raise ValueError("discussion result requires the exact typed report")
            if self.message != self.discussion.response_text or any((
                self.proposal, self.state, self.confirmation_request_id,
                self.artifact_proposal, self.recommendation_request,
                self.premise_report, self.premise_report_ref, self.exploration,
            )):
                raise ValueError("discussion cannot carry authoring execution authority")
            if self.next_action not in {
                CollaborationNextAction.CONTINUE_WITH_LEAD,
                CollaborationNextAction.REPLY_TO_LEAD,
                CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                CollaborationNextAction.SUBMIT_COMMIT_DECISION,
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST,
            }:
                raise ValueError("discussion must retain exact pending control priority")
            application = self.artifact_application_request
            if application is not None and (
                type(application) is not ArtifactApplicationRequestPayload
                or (application.reviewed_proposal.session_id,
                    application.reviewed_proposal.conversation_id) != (
                        self.session_id, self.conversation_id)
                or self.next_action is not CollaborationNextAction.SUBMIT_APPLICATION_DECISION
                or self.story_commit_request is not None
                or self.story_commit_preparation_request is not None
            ):
                raise ValueError("discussion pending application control is inconsistent")
            commit = self.story_commit_request
            if commit is not None and (
                type(commit) is not StoryCommitRequestPayload
                or commit.candidate.session_id != self.session_id
                or self.next_action is not CollaborationNextAction.SUBMIT_COMMIT_DECISION
                or self.story_commit_preparation_request is not None
            ):
                raise ValueError("discussion pending Commit control is inconsistent")
            validate_details(self.validation_details)
            return
        if self.discussion is not None:
            raise ValueError("only a discussion result may carry a discussion report")
        if (self.premise_report is None) != (self.premise_report_ref is None):
            raise ValueError("premise report and durable reference must be paired")
        if self.premise_report is not None:
            ref = self.premise_report_ref
            if type(ref) is not PremiseReportRef or (ref.source_conversation_id, ref.report_digest, ref.question_id) != (
                self.conversation_id, self.premise_report.report_digest, self.premise_report.spec.question_id
            ):
                raise ValueError("premise report reference changed its durable identity")
        if self.premise_report is not None:
            if type(self.premise_report) is not PremiseDecisionReport or self.kind is not LeadOutcomeKind.RECOMMENDATION or any((self.proposal, self.confirmation_request_id, self.artifact_proposal, self.artifact_application_request, self.story_commit_request, self.recommendation_request)):
                raise ValueError("premise report carries no authoring or mutation authority")
        if type(self.interaction_mode) is not LeadInteractionMode:
            raise TypeError("interaction mode must be explicit LeadInteractionMode")
        if self.kind is LeadOutcomeKind.EXPLORATION:
            if self.interaction_mode is not LeadInteractionMode.EXPLORATION or type(self.exploration) is not LeadExplorationReport:
                raise ValueError("exploration result requires explicit mode and report")
            if self.message != self.exploration.message or self.state is not None or self.evidence is not None or self.next_action is not CollaborationNextAction.CONTINUE_WITH_LEAD:
                raise ValueError("exploration result must preserve its exact provisional report without ordinary authority")
        elif self.exploration is not None:
            raise ValueError("ordinary result cannot carry an exploration report")
        if self.interaction_mode is LeadInteractionMode.EXPLORATION and any((
            self.proposal, self.confirmation_request_id, self.artifact_proposal,
            self.artifact_application_request, self.story_commit_request,
            self.recommendation_request, self.story_commit_preparation_request,
        )):
            raise ValueError("exploration cannot carry authoring or mutation authority")
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.turn_id, "turn ID"),
            (self.step_id, "step ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        if type(self.kind) is not LeadOutcomeKind:
            raise TypeError("lead result kind must be LeadOutcomeKind")
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("lead result trace must be ReplyTrace or None")
        validate_details(self.validation_details)
        if type(self.message) is not str or not self.message:
            raise ValueError("lead result message must be nonempty text")
        try:
            message_bytes = self.message.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("lead result message must be strict UTF-8") from exc
        if len(message_bytes) > 32_768:
            raise ValueError("lead result message exceeds the visible text limit")
        if self.proposal is not None and self.proposal.step_id != self.step_id:
            raise ValueError("lead result proposal belongs to another step")
        if self.kind is LeadOutcomeKind.PROPOSAL:
            if type(self.proposal) is not CollaborationProposal:
                raise TypeError("proposal lead result requires a canonical proposal")
            if self.state is not CollaborationStepState.AWAITING_CONFIRMATION:
                raise ValueError("proposal lead result must await confirmation")
            if self.confirmation_request_id is None:
                raise ValueError("proposal lead result requires a confirmation request")
            if self.next_action is not CollaborationNextAction.SUBMIT_CONFIRMATION:
                raise ValueError("proposal lead result must request confirmation")
        elif self.state is CollaborationStepState.RECONFIRMATION_REQUIRED:
            if (
                type(self.proposal) is not CollaborationProposal
                or self.confirmation_request_id is None
            ):
                raise ValueError(
                    "reconfirmation result requires its unchanged proposal and request"
                )
        elif self.state is CollaborationStepState.CONFIRMED:
            if type(self.proposal) is not CollaborationProposal:
                raise ValueError("confirmed result requires its exact proposal")
            if self.confirmation_request_id is not None:
                raise ValueError("confirmed result cannot carry an active request")
        elif self.proposal is not None or self.confirmation_request_id is not None:
            raise ValueError("lead result carries authority outside a proposal state")
        if self.state is not None and type(self.state) is not CollaborationStepState:
            raise TypeError("lead result state must be CollaborationStepState or None")
        if self.kind is not LeadOutcomeKind.PROPOSAL and self.state not in {
            None,
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.REJECTED,
            CollaborationStepState.SUPERSEDED,
            CollaborationStepState.CONFIRMED,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
            CollaborationStepState.RUNNING,
            CollaborationStepState.OUTCOME_UNKNOWN,
            CollaborationStepState.RECOVERY_REQUIRED,
        }:
            raise ValueError("lead result carries a non-visible workflow state")
        if (
            self.state
            in {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RUNNING,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            and self.kind is not LeadOutcomeKind.RECOMMENDATION
        ):
            raise ValueError("workflow-state lead result must be a recommendation")
        if self.confirmation_request_id is not None:
            _validate_identifier(
                self.confirmation_request_id,
                label="confirmation request ID",
            )
        if type(self.next_action) is not CollaborationNextAction:
            raise TypeError("lead result next action must be CollaborationNextAction")
        if self.evidence is not None and type(self.evidence) is not (
            CollaborationLeadEvidence
        ):
            raise TypeError("lead result evidence must be CollaborationLeadEvidence")
        if (
            self.artifact_proposal is not None
            and type(self.artifact_proposal) is not ArtifactProposalResult
        ):
            raise TypeError("lead result artifact proposal is invalid")
        if self.artifact_proposal is not None:
            candidate = self.artifact_proposal.candidate
            if (
                self.proposal is not None
                and self.artifact_proposal.state is not ArtifactProposalState.SUPERSEDED
                and (
                    candidate.consultation_proposal_id != self.proposal.proposal_id
                    or candidate.consultation_proposal_revision
                    != self.proposal.proposal_revision
                    or candidate.consultation_proposal_digest
                    != self.proposal.proposal_digest
                )
            ):
                raise ValueError("lead result artifact consultation identity drifted")
            if self.proposal is None and self.artifact_proposal.state in {
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                ArtifactProposalState.REVIEWING,
            }:
                raise ValueError("active artifact result requires its consultation")
        if self.artifact_application_request is not None:
            if (
                type(self.artifact_application_request)
                is not ArtifactApplicationRequestPayload
                or self.artifact_proposal is None
                or self.artifact_proposal.reviewed_proposal
                != self.artifact_application_request.reviewed_proposal
                or self.next_action
                is not CollaborationNextAction.SUBMIT_APPLICATION_DECISION
            ):
                raise ValueError("lead result application request is inconsistent")
        if self.story_commit_request is not None:
            if (
                type(self.story_commit_request) is not StoryCommitRequestPayload
                or self.artifact_application_request is not None
                or self.proposal is not None or self.confirmation_request_id is not None
                or self.artifact_proposal is not None
                or self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.next_action is not CollaborationNextAction.SUBMIT_COMMIT_DECISION
                or self.state not in {None, CollaborationStepState.COMPLETED}
                or self.story_commit_request.candidate.session_id != self.session_id
            ):
                raise ValueError("lead result Commit request is inconsistent")
        if (
            self.story_commit_preparation_request is not None
            and self.next_action
            is not CollaborationNextAction.REPREPARE_COMMIT_REQUEST
        ):
            raise ValueError("commit preparation requires reprepare action")
        if self.recommendation_request is not None:
            if (
                type(self.recommendation_request) is not LeadRecommendationRequestPayload
                or self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not None
                or self.proposal is not None or self.confirmation_request_id is not None
                or self.artifact_proposal is not None
                or self.artifact_application_request is not None
                or self.story_commit_request is not None
                or self.next_action is not CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION
                or self.evidence != self.recommendation_request.basis.evidence
            ):
                raise ValueError("lead recommendation request is inconsistent")
        elif self.next_action is CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION:
            raise ValueError("recommendation control requires its exact request")
        provider_judgment = (
            self.proposal is not None
            and self.proposal.step_kind
            is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
        ) or (
            self.proposal is None
            and self.next_action
            in {
                CollaborationNextAction.REPLY_TO_LEAD,
                CollaborationNextAction.CONTINUE_WITH_LEAD,
            }
            and self.state
            in {
                None,
                CollaborationStepState.SUPERSEDED,
            }
        )
        if provider_judgment and self.evidence is None and self.kind is not LeadOutcomeKind.EXPLORATION:
            raise ValueError("provider lead result requires exact evidence")
        if (
            self.proposal is not None
            and self.proposal.step_kind
            is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and self.evidence != self.proposal.basis.evidence
        ):
            raise ValueError("lead result evidence must match its proposal")
        if self.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
            if (
                self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not None
                or self.proposal is not None
                or self.confirmation_request_id is not None
                or self.evidence is not None
                or self.recommendation_request is not None
            ):
                raise ValueError(
                    "retry lead result must be a content-free failure notice"
                )
        elif self.kind is LeadOutcomeKind.QUESTION:
            if self.next_action is not CollaborationNextAction.REPLY_TO_LEAD:
                raise ValueError("question result must expose reply-to-lead")
        elif self.state is CollaborationStepState.RECONFIRMATION_REQUIRED:
            if self.next_action is not CollaborationNextAction.SUBMIT_CONFIRMATION:
                raise ValueError("reconfirmation result must request confirmation")
        elif self.state is CollaborationStepState.CONFIRMED:
            if self.next_action is not CollaborationNextAction.START_OR_RECONFIRM:
                raise ValueError("confirmed result must expose start-or-reconfirm")
        elif self.state is CollaborationStepState.RECOVERY_REQUIRED:
            if (
                self.next_action
                is not CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            ):
                raise ValueError("recovery result must require history repair")
        elif self.state is CollaborationStepState.OUTCOME_UNKNOWN:
            if (
                self.next_action
                is not CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            ):
                raise ValueError("unknown outcome must require inspection")
        elif self.state is CollaborationStepState.RUNNING:
            if self.next_action not in {
                CollaborationNextAction.NONE,
                CollaborationNextAction.FINALIZE_RESULT,
                CollaborationNextAction.FINALIZE_RECOMMENDATION,
            }:
                raise ValueError(
                    "running result must wait or require known-result finalization"
                )
        elif self.artifact_application_request is not None or self.story_commit_request is not None or self.recommendation_request is not None:
            pass
        elif self.next_action is CollaborationNextAction.REPREPARE_COMMIT_REQUEST:
            if (
                self.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.state is not CollaborationStepState.COMPLETED
                or self.proposal is not None
                or self.artifact_application_request is not None
                or self.story_commit_request is not None
                or type(self.story_commit_preparation_request)
                is not StoryCommitPreparationRequest
                or self.story_commit_preparation_request.session_id != self.session_id
                or self.story_commit_preparation_request.conversation_id
                != self.conversation_id
                or self.story_commit_preparation_request.step_id != self.step_id
            ):
                raise ValueError("commit reprepare result is inconsistent")
        elif self.premise_report is not None and self.next_action is CollaborationNextAction.CHOOSE_PREMISE:
            if self.state not in {None, CollaborationStepState.COMPLETED, CollaborationStepState.FAILED,
                                  CollaborationStepState.INTERRUPTED}:
                raise ValueError("premise choice cannot bypass a live workflow control")
        elif self.kind is not LeadOutcomeKind.PROPOSAL and (
            self.next_action is not CollaborationNextAction.CONTINUE_WITH_LEAD
        ):
            raise ValueError("completed lead result must continue with the lead")


@dataclass(frozen=True, slots=True)
class LeadDecisionResult:
    """Decision evidence paired with the authoritative visible resulting turn."""

    session_id: str
    conversation_id: str
    step_id: str
    decision: CollaborationConfirmationDecision
    proposal: CollaborationProposal
    turn: LeadTurnResult

    def __post_init__(self) -> None:
        for value, label in (
            (self.session_id, "session ID"),
            (self.conversation_id, "conversation ID"),
            (self.step_id, "step ID"),
        ):
            if type(value) is not str:
                raise TypeError(f"{label} must be exact text")
            _validate_identifier(value, label=label)
        if type(self.decision) is not CollaborationConfirmationDecision:
            raise TypeError("decision result kind is invalid")
        if type(self.proposal) is not CollaborationProposal:
            raise TypeError("decision result proposal is invalid")
        if type(self.turn) is not LeadTurnResult:
            raise TypeError("decision result turn is invalid")
        if self.proposal.step_id != self.step_id:
            raise ValueError("decision result proposal belongs to another step")
        if (
            self.turn.session_id != self.session_id
            or self.turn.conversation_id != self.conversation_id
            or (self.turn.step_id != self.step_id and not (
                self.decision in {CollaborationConfirmationDecision.CONFIRM, CollaborationConfirmationDecision.REVISE}
                and self.turn.recommendation_request is not None
                and (self.turn.recommendation_request.source_proposal_id,
                     self.turn.recommendation_request.source_proposal_revision,
                     self.turn.recommendation_request.source_proposal_digest)
                == (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                and ((self.turn.recommendation_request.source_execution_attempt_id is not None)
                     is (self.decision is CollaborationConfirmationDecision.CONFIRM))
                and self.turn.recommendation_request.physical_calls_spent
                > self.proposal.budget.physical_calls_spent
            ))
        ):
            raise ValueError("decision result turn belongs to another step")
        if self.decision is CollaborationConfirmationDecision.CONFIRM:
            self._validate_confirm_turn()
        elif self.decision is CollaborationConfirmationDecision.REJECT:
            if (
                self.turn.state is not CollaborationStepState.REJECTED
                or self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.turn.proposal is not None
            ):
                raise ValueError("reject result must expose the exact rejected turn")
        else:
            self._validate_revise_turn()

    def _validate_confirm_turn(self) -> None:
        if self.turn.recommendation_request is not None:
            request = self.turn.recommendation_request
            if (self.proposal.step_kind is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                or (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                != (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                or request.source_execution_attempt_id is None
                or request.basis.evidence != self.proposal.basis.evidence):
                raise ValueError("only ordinary consultation synthesis can disclose a recommendation")
            return
        if (
            self.turn.state is CollaborationStepState.SUPERSEDED
            and self.turn.kind is LeadOutcomeKind.RECOMMENDATION
            and self.turn.proposal is None
            and self.turn.artifact_proposal is not None
            and self.turn.artifact_proposal.state is ArtifactProposalState.STALE
        ):
            return
        terminal_states = {
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN,
            CollaborationStepState.RECOVERY_REQUIRED,
        }
        if self.turn.state in terminal_states:
            if (
                self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                and not (self.turn.kind is LeadOutcomeKind.ANSWER
                         and self.turn.state is CollaborationStepState.COMPLETED
                         and self.proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION)
                or self.turn.proposal is not None
            ):
                raise ValueError(
                    "confirm result must expose an authoritative execution turn"
                )
            return
        replacement = self.turn.proposal
        if (
            self.turn.state is not CollaborationStepState.AWAITING_CONFIRMATION
            or self.turn.kind is not LeadOutcomeKind.PROPOSAL
            or type(replacement) is not CollaborationProposal
            or self.proposal.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.proposal_id == self.proposal.proposal_id
            or replacement.proposal_revision != self.proposal.proposal_revision + 1
            or self.proposal.budget.rounds_spent != 0
            or replacement.budget.rounds_spent != 1
            or replacement.budget.physical_calls_spent
            <= self.proposal.budget.physical_calls_spent
            or replacement.basis.route != self.proposal.basis.route
        ):
            raise ValueError(
                "confirm result must expose a terminal turn or exact round two"
            )
        original_inputs = self.proposal.basis.inputs
        replacement_inputs = replacement.basis.inputs
        added_inputs = replacement_inputs[len(original_inputs) :]
        if (
            replacement_inputs[: len(original_inputs)] != original_inputs
            or len(added_inputs) != len(self.proposal.assignments)
            or any(
                item.kind is not CollaborationInputKind.CONVERSATION_EVENT
                for item in added_inputs
            )
        ):
            raise ValueError("round-two proposal does not continue exact input lineage")

    def _validate_revise_turn(self) -> None:
        replacement = self.turn.proposal
        if self.proposal.premise_decision is not None and replacement == self.proposal and (
            self.turn.state in {CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
            and self.turn.kind is (LeadOutcomeKind.PROPOSAL if self.turn.state is CollaborationStepState.AWAITING_CONFIRMATION else LeadOutcomeKind.RECOMMENDATION)
            and self.turn.confirmation_request_id is not None
            and self.turn.next_action is CollaborationNextAction.SUBMIT_CONFIRMATION
        ):
            return  # A refused premise revision preserves the exact pending authority.
        if self.turn.kind is LeadOutcomeKind.DISCUSSION:
            ref = self.turn.discussion.associated_task_ref
            scope = self.proposal.authoring_scope
            if (scope is None or ref is None or self.turn.state is not None
                    or self.turn.proposal is not None or self.turn.confirmation_request_id is not None
                    or (ref.session_id, ref.conversation_id, ref.task_id, ref.task_revision, ref.scope_digest)
                    != (self.session_id, self.conversation_id, scope.task_id, scope.task_revision,
                        _task_scope_digest(scope))):
                raise ValueError("revision discussion must retain its exact source task without replacement authority")
            return
        if self.turn.recommendation_request is not None:
            request = self.turn.recommendation_request
            if ((request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                != (self.proposal.proposal_id, self.proposal.proposal_revision, self.proposal.proposal_digest)
                or request.source_execution_attempt_id is not None):
                raise ValueError("revised recommendation must bind its exact previous proposal")
            return
        if self.turn.next_action is CollaborationNextAction.RETRY_LEAD_TURN:
            artifact = self.turn.artifact_proposal
            if (
                self.turn.kind is not LeadOutcomeKind.RECOMMENDATION
                or self.turn.state is not None
                or replacement is not None
                or self.turn.confirmation_request_id is not None
                or self.turn.evidence is not None
                or self.turn.artifact_application_request is not None
                or (
                    artifact is not None
                    and (
                        artifact.state is not ArtifactProposalState.SUPERSEDED
                        or artifact.candidate.consultation_proposal_id
                        != self.proposal.proposal_id
                        or artifact.candidate.consultation_proposal_revision
                        != self.proposal.proposal_revision
                        or artifact.candidate.consultation_proposal_digest
                        != self.proposal.proposal_digest
                    )
                )
            ):
                raise ValueError(
                    "failed revise result must retain no replacement authority"
                )
            return
        if self.turn.state is CollaborationStepState.SUPERSEDED:
            expected_action = (
                CollaborationNextAction.REPLY_TO_LEAD
                if self.turn.kind is LeadOutcomeKind.QUESTION
                else CollaborationNextAction.CONTINUE_WITH_LEAD
            )
            if (
                self.turn.kind
                not in {
                    LeadOutcomeKind.ANSWER,
                    LeadOutcomeKind.QUESTION,
                    LeadOutcomeKind.CRITIQUE,
                    LeadOutcomeKind.RECOMMENDATION,
                }
                or replacement is not None
                or self.turn.confirmation_request_id is not None
                or self.turn.next_action is not expected_action
                or (
                    self.turn.artifact_proposal is not None
                    and self.turn.artifact_proposal.state
                    is not ArtifactProposalState.SUPERSEDED
                )
            ):
                raise ValueError(
                    "direct revise result must expose an exact superseded lead turn"
                )
            return
        if (
            self.turn.state is not CollaborationStepState.AWAITING_CONFIRMATION
            or self.turn.kind is not LeadOutcomeKind.PROPOSAL
            or type(replacement) is not CollaborationProposal
            or replacement.step_kind
            is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or replacement.proposal_id == self.proposal.proposal_id
            or replacement.proposal_revision != self.proposal.proposal_revision + 1
            or replacement.budget.rounds_spent != self.proposal.budget.rounds_spent
            or replacement.budget.physical_calls_spent
            <= self.proposal.budget.physical_calls_spent
            or (
                self.turn.artifact_proposal is not None
                and self.turn.artifact_proposal.state
                not in {
                    ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                    ArtifactProposalState.SUPERSEDED,
                }
            )
        ):
            raise ValueError("revise result must expose the exact replacement proposal")


class CollaborationReadableFieldKey(StrEnum):
    """Closed searchable fields derived from one public collaboration payload."""

    LEAD_TASK_CONTROL = "lead_task.control"
    LEAD_SELECTED_MATERIAL = "lead_task.selected_material"
    LEAD_DISCUSSION_TEXT = "lead_discussion.text"
    LEAD_PUBLIC_RESPONSE_TEXT = "lead_public_response.text"
    LEAD_PUBLIC_RESPONSE_STATUS = "lead_public_response.status"
    LEAD_PUBLIC_RESPONSE_DIAGNOSTICS = "lead_public_response.diagnostics"

    MESSAGE_CONTENT = "message.content"
    EDITORIAL_TEXT = "editorial.text"
    EDITORIAL_REASON = "editorial.reason"
    EDITORIAL_TRANSITION_REASON = "editorial.transition_reason"
    LEAD_JUDGMENT_KIND = "lead_judgment.kind"
    LEAD_JUDGMENT_MESSAGE = "lead_judgment.message"
    LEAD_EVIDENCE_SCOPE = "lead_evidence.scope"
    LEAD_EVIDENCE_STATUS = "lead_evidence.status"
    LEAD_OUTCOME_FAILURE = "lead_outcome.failure"
    SPECIALIST_REPLY_CONTENT = "specialist_reply.content"
    SPECIALIST_REPLY_ASSESSMENT = "specialist_reply.assessment"
    LEAD_SYNTHESIS_MESSAGE = "lead_synthesis.message"
    PROPOSAL_PURPOSE = "proposal.purpose"
    PROPOSAL_USER_IMPACT = "proposal.user_impact"
    PROPOSAL_ASSIGNMENT_INSTRUCTION = "proposal.assignment.instruction"
    PROPOSAL_INPUT_LOCATOR = "proposal.input.locator"
    PROPOSAL_INPUT_RANGE = "proposal.input.range"
    PROPOSAL_CALL_USAGE = "proposal.call_usage"
    PROPOSAL_ASSIGNMENT_CALL_LIMIT = "proposal.assignment.call_limit"
    PROPOSAL_SYNTHESIS_CALL_LIMIT = "proposal.synthesis.call_limit"
    DISPATCH_INSTRUCTION = "dispatch.instruction"
    DISPATCH_CALL_LIMIT = "dispatch.call_limit"
    INTERRUPTION_CONTENT = "interruption.content"
    STEP_STATE = "step.state"
    CONFIRMATION_DECISION = "confirmation.decision"
    EXECUTION_OUTCOME = "execution.outcome"
    ASSIGNMENT_OUTCOME = "execution.assignment.outcome"
    ASSIGNMENT_CALL_USAGE = "execution.assignment.call_usage"
    SYNTHESIS_OUTCOME = "execution.synthesis.outcome"
    SYNTHESIS_CALL_USAGE = "execution.synthesis.call_usage"
    TERMINAL_SCOPE = "terminal.scope"
    TERMINAL_OUTCOME = "terminal.outcome"
    TERMINAL_CALL_USAGE = "terminal.call_usage"
    ARTIFACT_REVIEW_MESSAGE = "artifact_review.message"
    ARTIFACT_REVIEW_DECISION = "artifact_review.decision"
    ARTIFACT_PROPOSAL_STATE = "artifact_proposal.state"
    ARTIFACT_REVIEW_FAILURE = "artifact_review.failure"
    ARTIFACT_OBJECTIVE_REQUEST = "artifact_objective.request"
    ARTIFACT_OBJECTIVE_ACTION = "artifact_objective.action"
    ARTIFACT_OBJECTIVE_NEXT_STEP = "artifact_objective.next_step"
    LEAD_RECOMMENDATION_PURPOSE = "lead_recommendation.purpose"
    LEAD_RECOMMENDATION_INSTRUCTION = "lead_recommendation.instruction"
    LEAD_RECOMMENDATION_RESULT = "lead_recommendation.result"
    LEAD_RECOMMENDATION_IMPACT = "lead_recommendation.impact"
    LEAD_RECOMMENDATION_DECISION = "lead_recommendation.decision"
    LEAD_RECOMMENDATION_REVISION = "lead_recommendation.revision"
    LEAD_RECOMMENDATION_STATE = "lead_recommendation.state"
    LEAD_RECOMMENDATION_SOURCES = "lead_recommendation.sources"
    LEAD_RECOMMENDATION_TARGETS = "lead_recommendation.targets"
    LEAD_RECOMMENDATION_COST = "lead_recommendation.cost"


_ASSIGNMENT_ORDINAL_FIELD_KEYS = frozenset(
    {
        CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_INSTRUCTION,
        CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_CALL_LIMIT,
        CollaborationReadableFieldKey.ASSIGNMENT_OUTCOME,
        CollaborationReadableFieldKey.ASSIGNMENT_CALL_USAGE,
    }
)
_INPUT_ORDINAL_FIELD_KEYS = frozenset(
    {
        CollaborationReadableFieldKey.PROPOSAL_INPUT_LOCATOR,
        CollaborationReadableFieldKey.PROPOSAL_INPUT_RANGE,
    }
)


@dataclass(frozen=True, slots=True)
class CollaborationReadableField:
    """One exact public text field with stable nested-payload coordinates."""

    key: CollaborationReadableFieldKey
    text: str
    assignment_ordinal: int | None = None
    input_ordinal: int | None = None

    def __post_init__(self) -> None:
        if type(self.key) is not CollaborationReadableFieldKey:
            raise TypeError(
                "readable field key must be a CollaborationReadableFieldKey"
            )
        if type(self.text) is not str:
            raise TypeError("readable field text must be a string")
        if not self.text:
            raise ValueError("readable field text must not be empty")
        try:
            self.text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("readable field text must be strict UTF-8") from exc
        for value, label in (
            (self.assignment_ordinal, "assignment ordinal"),
            (self.input_ordinal, "input ordinal"),
        ):
            if value is not None:
                _validate_non_negative_integer(value, label=label)
        if (self.key in _ASSIGNMENT_ORDINAL_FIELD_KEYS) is not (
            self.assignment_ordinal is not None
        ):
            raise ValueError("readable field assignment ordinal is inconsistent")
        if (self.key in _INPUT_ORDINAL_FIELD_KEYS) is not (
            self.input_ordinal is not None
        ):
            raise ValueError("readable field input ordinal is inconsistent")


@dataclass(frozen=True, slots=True)
class CollaborationReadableRecord:
    """One canonical v2 event plus its closed searchable public fields."""

    schema_version: int
    session_id: str
    conversation_id: str
    turn_id: str
    step_id: str
    event_id: str
    batch_id: str
    batch_ordinal: int
    batch_size: int
    batch_digest: str
    sequence: int
    timestamp: str
    sender: CollaborationActor
    recipient: CollaborationActor
    direction: CollaborationDirection
    kind: CollaborationEventKindV2
    payload: CollaborationPayload
    fields: tuple[CollaborationReadableField, ...]

    def __post_init__(self) -> None:
        event = CollaborationEventV2(
            schema_version=self.schema_version,
            event_id=self.event_id,
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            step_id=self.step_id,
            sequence=self.sequence,
            timestamp=self.timestamp,
            batch_id=self.batch_id,
            batch_ordinal=self.batch_ordinal,
            batch_size=self.batch_size,
            batch_digest=self.batch_digest,
            kind=self.kind,
            sender=self.sender,
            recipient=self.recipient,
            payload=self.payload,
        )
        if self.direction is not event.direction:
            raise ValueError("readable record direction is inconsistent")
        if type(self.fields) is not tuple or any(
            type(field) is not CollaborationReadableField for field in self.fields
        ):
            raise TypeError(
                "readable record fields must be a tuple of CollaborationReadableField"
            )
        if self.fields != _collaboration_readable_fields(event.payload):
            raise ValueError("readable record fields are inconsistent with its payload")
        identities = {
            (field.key, field.assignment_ordinal, field.input_ordinal)
            for field in self.fields
        }
        if len(identities) != len(self.fields):
            raise ValueError("readable record contains duplicate field coordinates")

    @property
    def readable_utf8_bytes(self) -> int:
        """Return the exact aggregate UTF-8 bytes of its searchable fields."""

        return sum(len(field.text.encode("utf-8")) for field in self.fields)


@dataclass(frozen=True, slots=True)
class CollaborationContextBootstrap:
    """Content-free identity and tail facts for one validated readable history."""

    schema_version: int
    session_id: str | None
    conversation_id: str | None
    total_records: int
    last_sequence: int
    tail_batch_digest: str | None

    def __post_init__(self) -> None:
        if self.schema_version != COLLABORATION_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported collaboration bootstrap schema")
        if (self.session_id is None) is not (self.conversation_id is None):
            raise ValueError("collaboration bootstrap identity is incomplete")
        if self.session_id is not None:
            _validate_identifier(self.session_id, label="session ID")
            assert self.conversation_id is not None
            _validate_identifier(self.conversation_id, label="conversation ID")
        _validate_non_negative_integer(self.total_records, label="total records")
        _validate_non_negative_integer(self.last_sequence, label="last sequence")
        empty = self.total_records == 0
        if empty is not (self.last_sequence == 0):
            raise ValueError("collaboration bootstrap sequence is inconsistent")
        if empty is not (self.tail_batch_digest is None):
            raise ValueError("collaboration bootstrap tail digest is inconsistent")
        if not empty:
            assert self.tail_batch_digest is not None
            if _SHA256_DIGEST.fullmatch(self.tail_batch_digest) is None:
                raise ValueError("collaboration bootstrap tail digest is invalid")


@dataclass(frozen=True, slots=True)
class CollaborationReadableContextPolicy:
    """Fixed caps for explicitly selected additive provider context records."""

    max_records: int = 24
    max_utf8_bytes: int = 32_768

    def __post_init__(self) -> None:
        _validate_positive_integer(self.max_records, label="maximum readable records")
        _validate_positive_integer(
            self.max_utf8_bytes,
            label="maximum readable UTF-8 bytes",
        )
        if self.max_records > 24:
            raise ValueError("maximum readable records must not exceed 24")
        if self.max_utf8_bytes > 32_768:
            raise ValueError("maximum readable UTF-8 bytes must not exceed 32768")


COLLABORATION_READABLE_CONTEXT_POLICY = CollaborationReadableContextPolicy()


@dataclass(frozen=True, slots=True)
class CollaborationReadableContext:
    """Content-free bootstrap plus an explicit bounded readable-record selection."""

    bootstrap: CollaborationContextBootstrap
    records: tuple[CollaborationReadableRecord, ...]
    requested_records: int
    retained_records: int
    omitted_records: int
    retained_utf8_bytes: int
    truncated: bool

    def __post_init__(self) -> None:
        if type(self.bootstrap) is not CollaborationContextBootstrap:
            raise TypeError("readable context bootstrap is invalid")
        if type(self.records) is not tuple or any(
            type(record) is not CollaborationReadableRecord for record in self.records
        ):
            raise TypeError(
                "readable context records must be a tuple of CollaborationReadableRecord"
            )
        for value, label in (
            (self.requested_records, "requested records"),
            (self.retained_records, "retained records"),
            (self.omitted_records, "omitted records"),
            (self.retained_utf8_bytes, "retained UTF-8 bytes"),
        ):
            _validate_non_negative_integer(value, label=label)
        if self.requested_records != self.retained_records + self.omitted_records:
            raise ValueError("readable context record counts are inconsistent")
        if self.retained_records != len(self.records):
            raise ValueError("readable context retained count is inconsistent")
        if self.retained_utf8_bytes != sum(
            record.readable_utf8_bytes for record in self.records
        ):
            raise ValueError("readable context UTF-8 byte count is inconsistent")
        if type(self.truncated) is not bool:
            raise TypeError("readable context truncated flag must be a boolean")
        if self.truncated is not (self.omitted_records > 0):
            raise ValueError("readable context truncated flag is inconsistent")
        previous_sequence = 0
        for record in self.records:
            if record.sequence <= previous_sequence:
                raise ValueError("readable context records are not in sequence order")
            if (
                record.session_id != self.bootstrap.session_id
                or record.conversation_id != self.bootstrap.conversation_id
                or record.sequence > self.bootstrap.last_sequence
            ):
                raise ValueError("readable context record is outside its bootstrap")
            previous_sequence = record.sequence


_LEGAL_COLLABORATION_TRANSITIONS: Mapping[
    CollaborationStepState | None,
    frozenset[CollaborationStepState],
] = MappingProxyType(
    {
        None: frozenset({CollaborationStepState.AWAITING_CONFIRMATION}),
        CollaborationStepState.AWAITING_CONFIRMATION: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RECONFIRMATION_REQUIRED: frozenset(
            {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.CONFIRMED: frozenset(
            {
                CollaborationStepState.RUNNING,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.RUNNING: frozenset(
            {
                CollaborationStepState.COMPLETED,
                CollaborationStepState.INTERRUPTED,
                CollaborationStepState.FAILED,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.COMPLETED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.REJECTED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.SUPERSEDED: frozenset(
            {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
        ),
        CollaborationStepState.INTERRUPTED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.FAILED: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.OUTCOME_UNKNOWN: frozenset(
            {CollaborationStepState.RECOVERY_REQUIRED}
        ),
        CollaborationStepState.RECOVERY_REQUIRED: frozenset(),
    }
)


def validate_collaboration_transition(
    previous: CollaborationStepState | None,
    candidate: CollaborationStepState,
) -> CollaborationStepState:
    """Return a legal state transition or reject it deterministically."""

    if previous is not None and not isinstance(previous, CollaborationStepState):
        raise TypeError("previous state must be a CollaborationStepState or None")
    if not isinstance(candidate, CollaborationStepState):
        raise TypeError("candidate state must be a CollaborationStepState")
    if candidate not in _LEGAL_COLLABORATION_TRANSITIONS[previous]:
        previous_value = "none" if previous is None else previous.value
        raise ValueError(
            f"illegal collaboration transition: {previous_value} -> {candidate.value}"
        )
    return candidate


def project_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
) -> CollaborationHistoryProjection:
    """Validate and deterministically project one complete schema-v2 history."""

    if type(events) is not tuple:
        raise TypeError("collaboration history must be an exact tuple")
    if not events:
        return CollaborationHistoryProjection(
            session_id=None,
            conversation_id=None,
            last_sequence=0,
            current_turn_id=None,
            current_step_id=None,
            current_proposal=None,
            current_state=None,
            confirmation_request_id=None,
            confirmation_decision=None,
            execution_attempt_id=None,
            execution_result_event_id=None,
            execution_result=None,
            next_safe_action=CollaborationNextAction.NONE,
        )
    for event in events:
        if type(event) is not CollaborationEventV2:
            raise TypeError("collaboration history must contain canonical v2 events")
    _validate_stored_collaboration_batches(events)
    task_intents: dict[str, AuthoringIntent] = {}
    for event in events:
        scope = getattr(event.payload, "authoring_scope", None)
        if scope is not None:
            prior_intent = task_intents.get(scope.task_id)
            if prior_intent is not None and prior_intent is not scope.intent:
                raise ValueError("collaboration history changed an existing task intent")
            task_intents[scope.task_id] = scope.intent
    return _project_validated_collaboration_history(events)


def project_collaboration_readable_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[CollaborationReadableRecord, ...]:
    """Return one readable record per event after full history validation.

    The projection is pure and preserves strict durable sequence order. It
    accepts only canonical schema-v2 events; schema-v1 values, partial batches,
    sequence gaps, duplicate identities, digest drift, and illegal transitions
    fail before any record is returned.
    """

    project_collaboration_history(events)
    return tuple(
        CollaborationReadableRecord(
            schema_version=event.schema_version,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            event_id=event.event_id,
            batch_id=event.batch_id,
            batch_ordinal=event.batch_ordinal,
            batch_size=event.batch_size,
            batch_digest=event.batch_digest,
            sequence=event.sequence,
            timestamp=event.timestamp,
            sender=event.sender,
            recipient=event.recipient,
            direction=event.direction,
            kind=event.kind,
            payload=event.payload,
            fields=_collaboration_readable_fields(event.payload),
        )
        for event in events
    )


def select_bounded_collaboration_context(
    events: tuple[CollaborationEventV2, ...],
    *,
    event_ids: tuple[str, ...] = (),
    policy: CollaborationReadableContextPolicy = (
        COLLABORATION_READABLE_CONTEXT_POLICY
    ),
) -> CollaborationReadableContext:
    """Return a content-free bootstrap plus explicitly selected records.

    The default empty selection never copies transcript text into provider
    context. Callers add known event identities after an authorized search or
    read. Selected records remain in authoritative sequence order and are
    retained record-atomically up to both fixed caps. Current user input is not
    accepted here and therefore remains a separate invocation input.
    """

    if type(policy) is not CollaborationReadableContextPolicy:
        raise TypeError("readable context policy is invalid")
    records = project_collaboration_readable_history(events)
    if type(event_ids) is not tuple:
        raise TypeError("readable context event IDs must be an exact tuple")
    for event_id in event_ids:
        _validate_identifier(event_id, label="readable context event ID")
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("readable context event IDs must be unique")

    bootstrap = CollaborationContextBootstrap(
        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
        session_id=records[0].session_id if records else None,
        conversation_id=records[0].conversation_id if records else None,
        total_records=len(records),
        last_sequence=records[-1].sequence if records else 0,
        tail_batch_digest=records[-1].batch_digest if records else None,
    )
    by_id = {record.event_id: record for record in records}
    unknown = tuple(event_id for event_id in event_ids if event_id not in by_id)
    if unknown:
        raise ValueError("readable context references an unknown event ID")
    selected_event_ids = set(event_ids)
    requested = tuple(
        record for record in records if record.event_id in selected_event_ids
    )

    retained: list[CollaborationReadableRecord] = []
    retained_utf8_bytes = 0
    for record in requested:
        record_bytes = record.readable_utf8_bytes
        if len(retained) == policy.max_records:
            break
        if retained_utf8_bytes + record_bytes > policy.max_utf8_bytes:
            break
        retained.append(record)
        retained_utf8_bytes += record_bytes

    omitted = len(requested) - len(retained)
    return CollaborationReadableContext(
        bootstrap=bootstrap,
        records=tuple(retained),
        requested_records=len(requested),
        retained_records=len(retained),
        omitted_records=omitted,
        retained_utf8_bytes=retained_utf8_bytes,
        truncated=omitted > 0,
    )


def validate_collaboration_candidate_batch(
    history: tuple[CollaborationEventV2, ...],
    batch: CollaborationEventBatch,
) -> CollaborationHistoryProjection | CollaborationAppendReceipt:
    """Classify one CAS candidate against history without I/O or mutation.

    A new valid candidate returns its projected history. An exact committed
    retry returns the original receipt with ``ALREADY_COMMITTED``. Every fixed
    semantic store failure is raised as ``CollaborationAppendError``.
    """

    if type(batch) is not CollaborationEventBatch:
        raise TypeError("candidate must be a CollaborationEventBatch")
    try:
        projection = project_collaboration_history(history)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.CORRUPT_HISTORY
        ) from exc

    matching_batch = _find_collaboration_batch(history, batch.batch_id)
    if matching_batch is not None:
        stored_batch, stored_events = matching_batch
        if stored_batch != batch:
            raise CollaborationAppendError(CollaborationAppendFailure.BATCH_ID_CONFLICT)
        return CollaborationAppendReceipt(
            status=CollaborationAppendStatus.ALREADY_COMMITTED,
            batch_id=stored_batch.batch_id,
            batch_digest=collaboration_batch_digest(stored_batch),
            first_sequence=stored_events[0].sequence,
            last_sequence=stored_events[-1].sequence,
            timestamps=tuple(event.timestamp for event in stored_events),
        )

    existing_event_ids = {event.event_id for event in history}
    if any(event.event_id in existing_event_ids for event in batch.events):
        raise CollaborationAppendError(CollaborationAppendFailure.EVENT_ID_CONFLICT)
    if batch.expected_last_sequence != projection.last_sequence:
        raise CollaborationAppendError(CollaborationAppendFailure.STALE_SEQUENCE)
    if history:
        first = batch.events[0]
        if (first.session_id, first.conversation_id) != (
            projection.session_id,
            projection.conversation_id,
        ):
            raise CollaborationAppendError(
                CollaborationAppendFailure.INVALID_TRANSITION
            )
    timestamp = "1970-01-01T00:00:00+00:00"
    digest = collaboration_batch_digest(batch)
    stored = tuple(
        CollaborationEventV2(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=event.event_id,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=batch.expected_last_sequence + ordinal + 1,
            timestamp=timestamp,
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(batch.events),
            batch_digest=digest,
            kind=event.kind,
            sender=event.sender,
            recipient=event.recipient,
            payload=event.payload,
        )
        for ordinal, event in enumerate(batch.events)
    )
    try:
        return project_collaboration_history(history + stored)
    except (AssertionError, KeyError, SessionIdError, TypeError, ValueError) as exc:
        raise CollaborationAppendError(
            CollaborationAppendFailure.INVALID_TRANSITION
        ) from exc


class CollaborationEventStore(Protocol):
    """The sole storage-neutral port for active schema-v2 history services."""

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one validated CAS batch or return its stable retry receipt."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read the complete canonical collaboration history in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and project the complete canonical collaboration history."""

        ...


def _find_collaboration_batch(
    events: tuple[CollaborationEventV2, ...],
    batch_id: str,
) -> tuple[CollaborationEventBatch, tuple[CollaborationEventV2, ...]] | None:
    offset = 0
    while offset < len(events):
        size = events[offset].batch_size
        stored_events = events[offset : offset + size]
        if stored_events[0].batch_id == batch_id:
            return (
                CollaborationEventBatch(
                    batch_id=batch_id,
                    expected_last_sequence=offset,
                    events=tuple(event.to_draft() for event in stored_events),
                ),
                stored_events,
            )
        offset += size
    return None


def _validate_stored_collaboration_batches(
    events: tuple[CollaborationEventV2, ...],
) -> None:
    identity = (events[0].session_id, events[0].conversation_id)
    event_ids: set[str] = set()
    batch_ids: set[str] = set()
    offset = 0
    while offset < len(events):
        first = events[offset]
        if (first.session_id, first.conversation_id) != identity:
            raise ValueError("collaboration history spans multiple transcripts")
        if first.sequence != offset + 1:
            raise ValueError("collaboration sequences must be contiguous from one")
        if first.batch_ordinal != 0:
            raise ValueError("collaboration batch must begin at ordinal zero")
        if first.batch_id in batch_ids:
            raise ValueError("collaboration batch ID is reused noncontiguously")
        batch_ids.add(first.batch_id)
        end = offset + first.batch_size
        if end > len(events):
            raise ValueError("collaboration history contains a partial batch")
        group = events[offset:end]
        for ordinal, event in enumerate(group):
            if (event.session_id, event.conversation_id) != identity:
                raise ValueError("collaboration batch crosses transcript identity")
            if event.turn_id != first.turn_id:
                raise ValueError("collaboration batch crosses turn identity")
            if event.sequence != offset + ordinal + 1:
                raise ValueError("collaboration sequence metadata is not contiguous")
            if (
                event.batch_id != first.batch_id
                or event.batch_size != first.batch_size
                or event.batch_ordinal != ordinal
                or event.batch_digest != first.batch_digest
            ):
                raise ValueError("collaboration batch metadata is inconsistent")
            if event.event_id in event_ids:
                raise ValueError("collaboration event ID is reused")
            event_ids.add(event.event_id)
        reconstructed = CollaborationEventBatch(
            batch_id=first.batch_id,
            expected_last_sequence=offset,
            events=tuple(event.to_draft() for event in group),
        )
        if collaboration_batch_digest(reconstructed) != first.batch_digest:
            raise ValueError("collaboration batch digest does not match its events")
        offset = end


def _proposal_key(
    proposal: CollaborationProposal,
) -> tuple[str, int, str]:
    return (
        proposal.proposal_id,
        proposal.proposal_revision,
        proposal.proposal_digest,
    )


def _payload_proposal_key(payload: object) -> tuple[str, int, str] | None:
    if isinstance(
        payload,
        (
            CollaborationAssignmentDispatchPayload,
            CollaborationSpecialistReplyPayload,
            CollaborationLeadSynthesisPayload,
            CollaborationStepStatePayload,
            CollaborationConfirmationPayload,
            CollaborationExecutionResult,
        ),
    ):
        return (
            payload.proposal_id,
            payload.proposal_revision,
            payload.proposal_digest,
        )
    return None


def _next_action(
    state: CollaborationStepState | None,
    result: CollaborationExecutionResult | None,
) -> CollaborationNextAction:
    if state in (
        CollaborationStepState.AWAITING_CONFIRMATION,
        CollaborationStepState.RECONFIRMATION_REQUIRED,
    ):
        return CollaborationNextAction.SUBMIT_CONFIRMATION
    if state is CollaborationStepState.CONFIRMED:
        return CollaborationNextAction.START_OR_RECONFIRM
    if state is CollaborationStepState.RUNNING and result is not None:
        return CollaborationNextAction.FINALIZE_RESULT
    if state in (
        CollaborationStepState.FAILED,
        CollaborationStepState.INTERRUPTED,
        CollaborationStepState.REJECTED,
        CollaborationStepState.SUPERSEDED,
    ):
        return CollaborationNextAction.CONTINUE_WITH_LEAD
    if state is CollaborationStepState.OUTCOME_UNKNOWN:
        return CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
    if state is CollaborationStepState.RECOVERY_REQUIRED:
        return CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
    return CollaborationNextAction.NONE


_ARTIFACT_STATE_TRANSITIONS: Mapping[
    ArtifactProposalState | None, frozenset[ArtifactProposalState]
] = MappingProxyType(
    {
        None: frozenset({ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION}),
        ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION: frozenset(
            {
                ArtifactProposalState.REVIEWING,
                ArtifactProposalState.REJECTED,
                ArtifactProposalState.SUPERSEDED,
                ArtifactProposalState.STALE,
                ArtifactProposalState.FAILED,
                ArtifactProposalState.RECOVERY_REQUIRED,
            }
        ),
        ArtifactProposalState.REVIEWING: frozenset(
            {
                ArtifactProposalState.REVISION_REQUIRED,
                ArtifactProposalState.REJECTED,
                ArtifactProposalState.REVIEWED,
                ArtifactProposalState.STALE,
                ArtifactProposalState.FAILED,
                ArtifactProposalState.RECOVERY_REQUIRED,
            }
        ),
        ArtifactProposalState.REVISION_REQUIRED: frozenset(),
        ArtifactProposalState.REJECTED: frozenset(),
        ArtifactProposalState.REVIEWED: frozenset(),
        ArtifactProposalState.SUPERSEDED: frozenset(),
        ArtifactProposalState.STALE: frozenset(),
        ArtifactProposalState.FAILED: frozenset(),
        ArtifactProposalState.RECOVERY_REQUIRED: frozenset(),
    }
)


def _retained_collaboration_batch(
    events: tuple[CollaborationEventV2, ...], offset: int,
) -> tuple[CollaborationEventV2, ...]:
    """Group retained rows after full physical envelope/batch validation."""
    end = offset + 1
    while end < len(events) and events[end].batch_id == events[offset].batch_id:
        end += 1
    return events[offset:end]


def _project_artifact_review_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[
    dict[str, ArtifactReviewCandidatePayload],
    dict[str, ArtifactProposalState],
    dict[str, ArtifactReviewedProposal],
]:
    """Validate artifact-review lineage without owning consultation mechanics."""

    candidates: dict[str, ArtifactReviewCandidatePayload] = {}
    states: dict[str, ArtifactProposalState] = {}
    reviewed: dict[str, ArtifactReviewedProposal] = {}
    synthesis_by_id: dict[str, ArtifactReviewSynthesisPayload] = {}
    all_events = {event.event_id: event for event in events}
    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        candidate_events = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewCandidatePayload
        )
        artifact_states = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactProposalStatePayload
        )
        artifact_syntheses = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewSynthesisPayload
        )
        reviewed_events = tuple(
            event for event in batch if type(event.payload) is ArtifactReviewedProposal
        )
        failure_events = tuple(
            event
            for event in batch
            if type(event.payload) is ArtifactReviewFailurePayload
        )
        if any(
            len(group) > 1
            for group in (
                candidate_events,
                artifact_states,
                artifact_syntheses,
                reviewed_events,
                failure_events,
            )
        ):
            raise ValueError("artifact review batch contains duplicate authority")

        if candidate_events:
            event = candidate_events[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewCandidatePayload)
            proposals = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationProposal
            )
            judgments = tuple(
                item
                for item in batch
                if type(item.payload) is CollaborationLeadJudgmentPayload
            )
            core_awaiting = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationStepStatePayload
                and item.payload.state is CollaborationStepState.AWAITING_CONFIRMATION
            )
            artifact_awaiting = tuple(
                item.payload
                for item in artifact_states
                if isinstance(item.payload, ArtifactProposalStatePayload)
                and item.payload.state
                is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
            )
            previous_candidate = candidates.get(event.step_id)
            if (
                (
                    previous_candidate is not None
                    and states.get(event.step_id)
                    is not ArtifactProposalState.SUPERSEDED
                )
                or len(proposals) != 1
                or len(judgments) != 1
                or len(core_awaiting) != 1
                or len(artifact_awaiting) != 1
            ):
                raise ValueError("artifact candidate must be disclosed atomically")
            proposal = proposals[0]
            judgment = judgments[0]
            if proposal.authoring_scope and proposal.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY:
                raise ValueError("review-only disclosure cannot create an artifact candidate")
            if (
                proposal.step_kind
                is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
                or payload.lead_judgment_event_id != judgment.event_id
                or payload.consultation_proposal_id != proposal.proposal_id
                or payload.consultation_proposal_revision != proposal.proposal_revision
                or payload.consultation_proposal_digest != proposal.proposal_digest
                or payload.candidate.grounding_evidence_ids
                != tuple(
                    item.evidence_id for item in judgment.payload.evidence.receipts
                )
                or tuple(item.specialist_id for item in proposal.assignments)
                != payload.disclosed_specialist_ids
            ):
                raise ValueError("artifact candidate disclosure drifted")
            candidate_inputs = tuple(
                item
                for item in proposal.basis.inputs
                if item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE
            )
            if (
                len(candidate_inputs) != 1
                or candidate_inputs[0].revision_digest
                != payload.candidate.candidate_digest
            ):
                raise ValueError("artifact candidate input identity drifted")
            required_assignment_inputs = {
                candidate_inputs[0].input_id,
                *(
                    item.input_id
                    for item in proposal.basis.inputs
                    if item.input_id in payload.candidate.grounding_evidence_ids
                ),
            }
            if any(
                not required_assignment_inputs.issubset(assignment.input_ids)
                for assignment in proposal.assignments
            ):
                raise ValueError("artifact reviewer lacks candidate evidence")
            if proposal.authoring_scope is not None and any(
                assignment.mode is not SpecialistAssignmentMode.REVIEW
                or assignment.review_basis is None
                or assignment.review_basis.candidate_input_ids != (candidate_inputs[0].input_id,)
                for assignment in proposal.assignments
            ):
                raise ValueError("scoped artifact reviewers require the exact final candidate basis")
            state = artifact_awaiting[0]
            if state.confirmation_request_id != core_awaiting[
                0
            ].confirmation_request_id or _artifact_candidate_key(
                state
            ) != _artifact_candidate_key(payload):
                raise ValueError("artifact confirmation identity drifted")
            if previous_candidate is not None:
                states.pop(event.step_id)
                reviewed.pop(event.step_id, None)
            candidates[event.step_id] = payload

        for state_event in artifact_states:
            state = state_event.payload
            assert isinstance(state, ArtifactProposalStatePayload)
            candidate = candidates.get(state_event.step_id)
            if candidate is None or _artifact_candidate_key(state) != (
                candidate.candidate.candidate_id,
                candidate.candidate.candidate_revision,
                candidate.candidate.candidate_digest,
                candidate.consultation_proposal_id,
                candidate.consultation_proposal_revision,
                candidate.consultation_proposal_digest,
            ):
                raise ValueError("artifact state does not match its candidate")
            previous = states.get(state_event.step_id)
            if state.state not in _ARTIFACT_STATE_TRANSITIONS[previous]:
                raise ValueError("illegal artifact proposal state transition")
            if state.state is ArtifactProposalState.REVIEWING:
                core_running = tuple(
                    item.payload
                    for item in batch
                    if type(item.payload) is CollaborationStepStatePayload
                    and item.payload.state is CollaborationStepState.RUNNING
                )
                if (
                    len(core_running) != 1
                    or state.execution_attempt_id
                    != core_running[0].execution_attempt_id
                ):
                    raise ValueError("artifact review must start with consultation")
            if state.state is ArtifactProposalState.REVIEWED:
                if len(reviewed_events) != 1:
                    raise ValueError("reviewed state requires exact reviewed proposal")
                proposal = reviewed_events[0].payload
                assert isinstance(proposal, ArtifactReviewedProposal)
                if (
                    state.reviewed_proposal_id != proposal.proposal_id
                    or state.reviewed_proposal_digest != proposal.proposal_digest
                ):
                    raise ValueError("reviewed artifact state identity drifted")
            elif reviewed_events:
                raise ValueError("reviewed proposal requires reviewed state")
            states[state_event.step_id] = state.state

        if artifact_syntheses:
            event = artifact_syntheses[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewSynthesisPayload)
            candidate = candidates.get(event.step_id)
            normal = tuple(
                item.payload
                for item in batch
                if type(item.payload) is CollaborationLeadSynthesisPayload
            )
            if (
                candidate is None
                or len(normal) != 1
                or _artifact_candidate_key(payload)
                != _artifact_candidate_key(candidate)
                or payload.execution_attempt_id != normal[0].execution_attempt_id
                or payload.reply_event_ids != normal[0].reply_event_ids
                or payload.unresolved_input_ids != normal[0].unresolved_input_ids
                or payload.message != normal[0].message
            ):
                raise ValueError("artifact synthesis does not match consultation")
            replies = tuple(all_events[item] for item in payload.reply_event_ids)
            if (
                len(replies) != len(candidate.disclosed_specialist_ids)
                or any(
                    type(item.payload) is not CollaborationSpecialistReplyPayload
                    for item in replies
                )
                or tuple(item.sender.agent_id for item in replies)
                != candidate.disclosed_specialist_ids
            ):
                raise ValueError("artifact synthesis review attribution drifted")
            synthesis_by_id[event.event_id] = payload

        if reviewed_events:
            event = reviewed_events[0]
            payload = event.payload
            assert isinstance(payload, ArtifactReviewedProposal)
            candidate = candidates.get(event.step_id)
            synthesis = synthesis_by_id.get(payload.synthesis_event_id)
            if (
                candidate is None
                or payload.candidate != candidate.candidate
                or synthesis is None
                or synthesis.decision is not ArtifactReviewDecision.PROPOSE
                or payload.consultation_proposal_id
                != candidate.consultation_proposal_id
                or payload.consultation_proposal_revision
                != candidate.consultation_proposal_revision
                or payload.consultation_proposal_digest
                != candidate.consultation_proposal_digest
                or payload.lead_judgment_event_id != candidate.lead_judgment_event_id
                or payload.unresolved_input_ids != synthesis.unresolved_input_ids
                or payload.rationale != synthesis.rationale
                or payload.user_impact != synthesis.user_impact
                or payload.limitations != synthesis.limitations
                or tuple(item.reply_event_id for item in payload.reviews)
                != synthesis.reply_event_ids
            ):
                raise ValueError("reviewed artifact proposal is not fully derived")
            reviewed[event.step_id] = payload

        if failure_events and reviewed_events:
            raise ValueError("artifact review failure cannot coexist with success")
        offset += len(batch)
    return candidates, states, reviewed


def _artifact_candidate_key(
    value: ArtifactReviewCandidatePayload
    | ArtifactReviewSynthesisPayload
    | ArtifactProposalStatePayload,
) -> tuple[str, int, str, str, int, str]:
    if type(value) is ArtifactReviewCandidatePayload:
        return (
            value.candidate.candidate_id,
            value.candidate.candidate_revision,
            value.candidate.candidate_digest,
            value.consultation_proposal_id,
            value.consultation_proposal_revision,
            value.consultation_proposal_digest,
        )
    return (
        value.candidate_id,
        value.candidate_revision,
        value.candidate_digest,
        value.consultation_proposal_id,
        value.consultation_proposal_revision,
        value.consultation_proposal_digest,
    )


def _recommendation_key(payload: object) -> tuple[str, str, str, int, str]:
    if type(payload) is LeadRecommendationDecisionPayload:
        payload = payload.decision
    return (
        payload.request_event_id, payload.request_id, payload.action_id,
        payload.action_revision, payload.action_digest,
    )


def _recommendation_origin(
    events: tuple[CollaborationEventV2, ...], turn_id: str, step_id: str,
) -> tuple[CollaborationEventV2, CollaborationEventV2, CollaborationEventV2] | None:
    """Recognize an exact request/structured-choice/start chain, never prose."""

    starts = tuple(event for event in events if event.turn_id == turn_id
                   and event.step_id == step_id
                   and type(event.payload) is LeadRecommendationStartedPayload)
    if len(starts) != 1:
        return None
    start = starts[0]
    by_id = {event.event_id: event for event in events}
    request = by_id.get(start.payload.request_event_id)
    decision = by_id.get(start.payload.decision_event_id)
    if (
        request is None or type(request.payload) is not LeadRecommendationRequestPayload
        or decision is None or type(decision.payload) is not LeadRecommendationDecisionPayload
        or start.payload.start_event_id != start.event_id
        or _recommendation_key(start.payload) != _recommendation_key(request.payload)
        or _recommendation_key(decision.payload) != _recommendation_key(request.payload)
        or decision.payload.decision.kind not in {
            CollaborationConfirmationDecision.CONFIRM, CollaborationConfirmationDecision.REVISE,
        }
        or request.payload.request_event_id != request.event_id
        or request.payload.tail_sequence != request.sequence
        or decision.payload.decision.request_tail_sequence != request.sequence
        or decision.sequence != request.sequence + 1
        or start.sequence != decision.sequence + 1
        or start.batch_id != decision.batch_id
        or start.turn_id != decision.turn_id or start.step_id != decision.step_id
        or start.step_id != request.step_id
        or decision.payload.decision.session_id != start.session_id
        or decision.payload.decision.conversation_id != start.conversation_id
    ):
        return None
    return request, decision, start


def _is_fresh_drafting_preparation(request, source, events) -> bool:
    scope = request.authoring_scope
    if (
        scope is None or scope.schema_version != 3
        or scope.intent is not AuthoringIntent.AUTHORING
        or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        or request.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
        or type(source.payload) is not CollaborationLeadJudgmentPayload
        or request.source_execution_attempt_id is not None
    ):
        return False
    previous = None
    prior = tuple(event for event in events if event.sequence < source.sequence)
    for event in prior:
        if type(event.payload) not in {
            CollaborationProposal, CollaborationLeadJudgmentPayload, LeadRecommendationRequestPayload,
        }:
            continue
        candidate = event.payload.authoring_scope
        if (candidate is not None and candidate.activity is not AuthoringActivity.WORKFLOW
                and candidate.task_id == scope.task_id
                and (previous is None or candidate.task_revision >= previous.task_revision)):
            previous = candidate
    reviewed = {event.payload.proposal_id: event.payload for event in prior
                if type(event.payload) is ArtifactReviewedProposal}
    proposals = {event.payload.proposal_id: event.payload for event in prior
                 if type(event.payload) is CollaborationProposal}
    for event in prior:
        result = event.payload
        if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
        }:
            continue
        review = reviewed.get(result.reviewed_proposal_id)
        proposal = None if review is None else proposals.get(review.consultation_proposal_id)
        if (previous is not None and review is not None
                and review.proposal_digest == result.reviewed_proposal_digest
                and proposal is not None and proposal.authoring_scope is not None
                and (proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision)
                == (previous.task_id, previous.task_revision)):
            previous = None
    return (
        scope.task_id == source.step_id and scope.task_revision == 1
        if previous is None
        else scope.task_revision == previous.task_revision + 1
    )


def _project_lead_recommendation_history(
    events: tuple[CollaborationEventV2, ...],
    task_origins: tuple[CollaborationEventV2, ...] = (),
) -> tuple[
    LeadRecommendationRequestPayload | None, LeadRecommendationDecisionPayload | None,
    LeadRecommendationStartedPayload | None, LeadRecommendationResultPayload | None,
    LeadRecommendationState | None,
]:
    """Replay immutable recommendation disclosure and exact bounded action closure."""

    request = None
    decision = None
    start = None
    result = None
    state = None
    request_event = None
    decision_event = None
    by_id: dict[str, CollaborationEventV2] = {}
    request_ids: set[str] = set()
    action_revisions: dict[str, int] = {}
    attempt_ids: set[str] = set()
    preparation_calls: set[str] = set()
    preparation_invocation: str | None = None
    for event in events:
        payload = event.payload
        if type(payload) is LeadRecommendationRequestPayload:
            source = by_id.get(payload.source_recommendation_event_id)
            source_valid = source is not None and (
                (type(source.payload) is CollaborationLeadJudgmentPayload
                 and source.payload.kind is LeadOutcomeKind.RECOMMENDATION)
                or (type(source.payload) is CollaborationLeadSynthesisPayload
                    and source.payload.next_intent is None)
            )
            if (
                not source_valid or source.batch_id != event.batch_id
                or payload.request_event_id != event.event_id
                or payload.tail_sequence != event.sequence
                or event.batch_ordinal != event.batch_size - 1
                or payload.request_id in request_ids
                or payload.action_revision != action_revisions.get(payload.action_id, 0) + 1
                or state in {LeadRecommendationState.PENDING, LeadRecommendationState.RUNNING}
            ):
                raise ValueError("recommendation request is not a fresh exact published tail")
            if payload.source_recommendation_digest != "sha256:" + hashlib.sha256(source.payload.message.encode("utf-8")).hexdigest():
                raise ValueError("recommendation source message digest drifted")
            if payload.basis.evidence != source.payload.evidence:
                raise ValueError("recommendation must retain its exact source evidence")
            if payload.authoring_scope is not None:
                source_scope = source.payload.authoring_scope
                scope = payload.authoring_scope
                if (source_scope is None
                    or scope.intent is not source_scope.intent
                        or (scope.task_id, scope.task_revision) != (source_scope.task_id, source_scope.task_revision)
                    or not set(source_scope.domains).issubset(scope.domains)
                    or not set(source_scope.target_paths).issubset(scope.target_paths)):
                    raise ValueError("recommendation must retain its exact source authoring task and obligations")
                prior_request = next((prior.payload for prior in reversed(tuple(by_id.values()))
                    if type(prior.payload) is LeadRecommendationRequestPayload
                    and prior.payload.action_id == payload.action_id), None)
                if prior_request is not None and prior_request.authoring_scope is not None:
                    prior_scope = prior_request.authoring_scope
                    if (scope.task_id != prior_scope.task_id
                        or scope.task_revision != prior_scope.task_revision + 1
                        or scope.intent is not prior_scope.intent
                        or not set(prior_scope.domains).issubset(scope.domains)
                        or not set(prior_scope.target_paths).issubset(scope.target_paths)):
                        raise ValueError("recommendation revision must retain its authoring task and obligations")
            fresh_consultation = _is_fresh_drafting_preparation(payload, source, tuple(by_id.values()))
            source_proposal = (payload.source_proposal_id, payload.source_proposal_revision,
                               payload.source_proposal_digest)
            if type(source.payload) is CollaborationLeadSynthesisPayload:
                if (source_proposal != (source.payload.proposal_id, source.payload.proposal_revision,
                                       source.payload.proposal_digest)
                    or payload.source_execution_attempt_id != source.payload.execution_attempt_id
                    or event.step_id == source.step_id
                    or any(type(prior.payload) is ArtifactReviewSynthesisPayload
                           and prior.payload.execution_attempt_id == source.payload.execution_attempt_id
                           for prior in by_id.values())):
                    raise ValueError("synthesis recommendation must bind its exact ordinary consultation result")
            else:
                revisions = tuple(prior for prior in by_id.values()
                    if prior.turn_id == source.turn_id and prior.step_id == source.step_id
                    and type(prior.payload) is CollaborationConfirmationPayload
                    and prior.payload.decision is CollaborationConfirmationDecision.REVISE)
                expected_source = (None, None, None) if not revisions else (
                    revisions[0].payload.proposal_id, revisions[0].payload.proposal_revision,
                    revisions[0].payload.proposal_digest)
                if not fresh_consultation and (payload.authoring_scope is not None and payload.authoring_scope.schema_version == 3 and payload.authoring_scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                } and not revisions):
                    prior_proposal = next((prior.payload for prior in by_id.values()
                                           if type(prior.payload) is CollaborationProposal and (
                                               prior.payload.proposal_id, prior.payload.proposal_revision, prior.payload.proposal_digest
                                           ) == source_proposal), None)
                    prior_synthesis = next((prior for prior in by_id.values()
                                            if type(prior.payload) is CollaborationLeadSynthesisPayload
                                            and prior.payload.execution_attempt_id == payload.source_execution_attempt_id
                                            and (prior.payload.proposal_id, prior.payload.proposal_revision, prior.payload.proposal_digest) == source_proposal), None)
                    confirmed = any(type(prior.payload) is CollaborationConfirmationPayload
                                    and prior.payload.decision is CollaborationConfirmationDecision.CONFIRM
                                    and (prior.payload.proposal_id, prior.payload.proposal_revision, prior.payload.proposal_digest) == source_proposal
                                    for prior in by_id.values())
                    completed = any(type(prior.payload) is CollaborationExecutionResult
                                    and prior.payload.execution_attempt_id == payload.source_execution_attempt_id
                                    and prior.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
                                    and (prior.payload.proposal_id, prior.payload.proposal_revision, prior.payload.proposal_digest) == source_proposal
                                    for prior in by_id.values())
                    if prior_proposal is None or prior_proposal.authoring_scope is None or prior_proposal.authoring_scope.schema_version != 3 or (
                        prior_synthesis is None or not confirmed or not completed
                        or authoring_capture_identity(payload.authoring_scope, payload.basis) != authoring_capture_identity(
                            prior_proposal.authoring_scope, prior_proposal.basis
                        )
                    ):
                        raise ValueError("v3 front recommendation requires exact completed confirmed authoring source")
                elif (len(revisions) > 1 or source_proposal != expected_source
                      or payload.source_execution_attempt_id is not None
                      or (revisions and event.step_id == source.step_id)):
                    raise ValueError("front recommendation source proposal correlation is not exact")
            if payload.action_id in action_revisions and (
                request is None or payload.action_id != request.action_id
                or decision is None or decision.decision.kind is not CollaborationConfirmationDecision.REVISE
                or result is None or result.state is not LeadRecommendationState.SUPERSEDED
                or result.lead_result_event_id != source.event_id
            ):
                raise ValueError("only exact Revise planning can advance an existing action revision")
            for draft in payload.draft_sources:
                if sum(item.kind is CollaborationInputKind.CONVERSATION_EVENT
                       and item.input_id == draft.event_id
                       and item.locator == f"conversation/{draft.event_id}"
                       and item.revision_digest == draft.content_digest
                       for item in payload.basis.inputs) != 1:
                    raise ValueError("recommendation draft lineage must match its exact disclosed input")
            for draft in payload.draft_sources:
                offered = by_id.get(draft.event_id)
                if (
                    offered is None or draft.conversation_id != event.conversation_id
                    or not (
                        (type(offered.payload) is CollaborationLeadJudgmentPayload
                         and offered.payload.evidence.scope is LeadOutcomeScope.STORY_SPECIFIC
                         and offered.payload.kind is not LeadOutcomeKind.PROPOSAL)
                        or type(offered.payload) is CollaborationLeadSynthesisPayload
                        or (type(offered.payload) is CollaborationLeadJudgmentPayload
                            and offered.payload.kind is LeadOutcomeKind.RECOMMENDATION
                            and (offered.event_id == source.event_id or any(
                                type(prior.payload) is LeadRecommendationRequestPayload
                                and prior.payload.source_recommendation_event_id == offered.event_id
                                for prior in by_id.values()
                            )))
                    )
                    or draft.content_digest != "sha256:" + hashlib.sha256(offered.payload.message.encode("utf-8")).hexdigest()
                ):
                    raise ValueError("recommendation draft source was not exactly offered")
                selected_scope = getattr(offered.payload, "authoring_scope", None)
                scope = payload.authoring_scope
                if (
                    scope is not None and scope.schema_version == 3 and not authoring_source_scope_is_eligible(
                    selected_scope, scope, allow_prior_revision_input=fresh_consultation,
                )
                ):
                    raise ValueError("recommendation draft source belongs to another task or revision")
            if type(source.payload) is CollaborationLeadSynthesisPayload and not any(
                prior.sequence > source.sequence and prior.sequence < event.sequence
                and prior.batch_id == event.batch_id and prior.step_id == source.step_id
                and type(prior.payload) is CollaborationStepStatePayload
                and prior.payload.state is CollaborationStepState.COMPLETED
                and (completion := by_id.get(prior.payload.execution_result_event_id)) is not None
                and type(completion.payload) is CollaborationExecutionResult
                and completion.payload.execution_attempt_id == source.payload.execution_attempt_id
                for prior in by_id.values()
            ):
                raise ValueError("synthesis recommendation must follow its terminal consultation")
            request_ids.add(payload.request_id)
            action_revisions[payload.action_id] = payload.action_revision
            request, request_event = payload, event
            decision = decision_event = start = result = None
            state = LeadRecommendationState.PENDING
        elif type(payload) is LeadRecommendationDecisionPayload:
            choice = payload.decision
            if (
                request is None or request_event is None
                or state is not LeadRecommendationState.PENDING or decision is not None
                or _recommendation_key(payload) != _recommendation_key(request)
                or choice.session_id != event.session_id or choice.conversation_id != event.conversation_id
                or choice.request_tail_sequence != request.tail_sequence
                or event.sequence != request_event.sequence + 1
                or event.batch_ordinal != 0 or event.step_id != request_event.step_id
            ):
                raise ValueError("recommendation decision does not bind the exact pending tail")
            decision, decision_event = payload, event
        elif type(payload) is LeadRecommendationStartedPayload:
            if (
                request is None or decision is None or start is not None
                or state is not LeadRecommendationState.PENDING
                or payload.execution_attempt_id in attempt_ids
                or _recommendation_origin((*by_id.values(), event), event.turn_id, event.step_id) is None
                or event.batch_ordinal != event.batch_size - 1
            ):
                raise ValueError("recommendation start lacks one exact structured decision")
            attempt_ids.add(payload.execution_attempt_id)
            start, state = payload, LeadRecommendationState.RUNNING
            preparation_calls = set()
            preparation_invocation = None
        elif type(payload) is LeadRecommendationResultPayload:
            if request is None or request_event is None or result is not None or _recommendation_key(payload) != _recommendation_key(request):
                raise ValueError("recommendation result lacks one exact open request")
            if payload.superseding_user_event_id is not None:
                user = by_id.get(payload.superseding_user_event_id)
                task_user = next((item for item in task_origins
                                  if item.event_id == payload.superseding_user_event_id), None)
                if user is None and task_user is not None:
                    decision_value = task_user.payload.decision
                    if (task_user.sender.kind is not CollaborationActorKind.USER
                            or decision_value.action is LeadTaskAction.DISCUSS
                            or decision_value.request_id != task_user.event_id
                            or (task_user.session_id, task_user.conversation_id, task_user.turn_id, task_user.batch_id)
                            != (event.session_id, event.conversation_id, event.turn_id, event.batch_id)
                            or (decision_value.task_ref is not None and (
                                request.authoring_scope is None
                                or (decision_value.task_ref.task_id, decision_value.task_ref.task_revision)
                                != (request.authoring_scope.task_id, request.authoring_scope.task_revision)))):
                        raise ValueError("task supersession requires exact disclosed USER action and source")
                    user = task_user
                if (
                    decision is not None or start is not None
                    or state is not LeadRecommendationState.PENDING
                    or payload.state is not LeadRecommendationState.SUPERSEDED
                    or payload.decision_event_id is not None or payload.start_event_id is not None
                    or payload.lead_result_event_id is not None
                    or user is None or type(user.payload) not in {CollaborationMessagePayload, CollaborationLeadTaskDecisionPayload}
                    or (type(user.payload) is not CollaborationLeadTaskDecisionPayload and user.direction is not CollaborationDirection.USER_TO_LEAD)
                    or user.batch_id != event.batch_id or user.turn_id != event.turn_id
                ):
                    raise ValueError("ordinary supersession must bind its real new user event")
            else:
                if decision_event is None or payload.decision_event_id != decision_event.event_id:
                    raise ValueError("recommendation result decision is not exact")
                if start is None:
                    expected = (LeadRecommendationState.REJECTED
                                if decision.decision.kind is CollaborationConfirmationDecision.REJECT
                                else LeadRecommendationState.STALE)
                    if (payload.state is not expected or payload.start_event_id is not None
                        or payload.lead_result_event_id is not None
                        or event.batch_id != decision_event.batch_id):
                        raise ValueError("unstarted recommendation may only reject or become stale")
                else:
                    lead = by_id.get(payload.lead_result_event_id)
                    started_event = by_id.get(start.start_event_id)
                    if (
                        payload.start_event_id != start.start_event_id
                        or started_event is None
                        or event.turn_id != started_event.turn_id
                        or event.step_id != started_event.step_id or event.step_id != request_event.step_id
                        or lead is None or lead.turn_id != event.turn_id or lead.step_id != event.step_id
                        or lead.sequence <= started_event.sequence
                        or type(lead.payload) not in {CollaborationLeadJudgmentPayload, CollaborationLeadOutcomeFailurePayload}
                    ):
                        raise ValueError("recommendation closure lacks its exact preparation result")
                    if type(lead.payload) is CollaborationLeadOutcomeFailurePayload:
                        if payload.state not in {LeadRecommendationState.FAILED, LeadRecommendationState.INTERRUPTED, LeadRecommendationState.SUPERSEDED} or lead.sequence != event.sequence - 1:
                            raise ValueError("failed preparation must close after its exact Lead failure tail")
                    else:
                        expected = (LeadRecommendationState.SUPERSEDED
                                    if decision.decision.kind is CollaborationConfirmationDecision.REVISE
                                    else LeadRecommendationState.COMPLETED)
                        if payload.state is not expected or lead.batch_id != event.batch_id:
                            raise ValueError("accepted preparation must co-batch action closure")
                        if decision.decision.kind is CollaborationConfirmationDecision.REVISE:
                            replacements = tuple(item for item in events
                                if item.batch_id == event.batch_id
                                and type(item.payload) is LeadRecommendationRequestPayload
                                and item.sequence > event.sequence
                                and item.payload.source_recommendation_event_id == lead.event_id
                                and item.payload.action_id == request.action_id
                                and item.payload.action_revision == request.action_revision + 1)
                            if lead.payload.kind is not LeadOutcomeKind.RECOMMENDATION or len(replacements) != 1:
                                raise ValueError("Revise may only prepare a fresh recommendation")
                        elif lead.payload.kind not in {LeadOutcomeKind.PROPOSAL, LeadOutcomeKind.QUESTION}:
                            raise ValueError("confirmed preparation cannot produce another recommendation")
                        if (decision.decision.kind is CollaborationConfirmationDecision.CONFIRM
                            and lead.payload.kind is LeadOutcomeKind.PROPOSAL):
                            proposals = tuple(item for item in by_id.values()
                                if item.batch_id == event.batch_id and item.step_id == event.step_id
                                and type(item.payload) is CollaborationProposal)
                            candidates = tuple(item for item in by_id.values()
                                if item.batch_id == event.batch_id and item.step_id == event.step_id
                                and type(item.payload) is ArtifactReviewCandidatePayload)
                            if len(proposals) != 1:
                                raise ValueError("confirmed preparation requires its exact separate proposal")
                            if request.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT:
                                if len(candidates) != 1 or not any(
                                    item.batch_id == event.batch_id and item.step_id == event.step_id
                                    and type(item.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                                    and item.payload.candidate_binding is not None
                                    and item.payload.candidate_binding.candidate_event_id == candidates[0].event_id
                                    for item in by_id.values()
                                ):
                                    raise ValueError("artifact preparation must bind its exact candidate and objective")
                            elif candidates:
                                raise ValueError("consultation preparation cannot create an artifact candidate")
            result, state = payload, payload.state
        elif state is LeadRecommendationState.RUNNING and type(payload) in {
            CollaborationProviderCallPayload, CollaborationProposal, ArtifactReviewCandidatePayload,
            ArtifactObjectivePayload, ArtifactObjectivePayloadV2,
        }:
            assert request is not None and request_event is not None and start is not None and decision is not None
            started_event = by_id[start.start_event_id]
            if (event.turn_id, event.step_id) != (started_event.turn_id, started_event.step_id):
                raise ValueError("preparation work must belong to its exact start")
            if type(payload) is CollaborationProviderCallPayload:
                if payload.invocation_kind is not CollaborationInvocationKind.LEAD or payload.role != started_event.recipient.agent_id:
                    raise ValueError("recommendation preparation cannot invoke specialists")
                if preparation_invocation not in {None, payload.invocation_id}:
                    raise ValueError("one recommendation start authorizes one Lead invocation")
                preparation_invocation = payload.invocation_id
                if payload.state is CollaborationProviderCallState.SUBMITTED:
                    preparation_calls.add(payload.call_id)
                    if len(preparation_calls) > request.max_preparation_physical_calls:
                        raise ValueError("preparation exceeds its disclosed call reservation")
            elif decision.decision.kind is CollaborationConfirmationDecision.REVISE:
                raise ValueError("Revise cannot prepare candidates, consultation proposals or objective transitions")
            elif type(payload) is ArtifactReviewCandidatePayload:
                paths = {path for operation in payload.candidate.change_set.operations
                         for path in ((operation.source_path, operation.destination_path)
                                      if type(operation) is ArtifactRenameOperation else (operation.path,))}
                if request.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT or paths != set(request.target_paths):
                    raise ValueError("artifact preparation changed its frozen scope")
            elif type(payload) is CollaborationProposal and request.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
                if (payload.purpose != request.purpose
                    or tuple(item.specialist_id for item in payload.assignments) != request.specialist_ids):
                    raise ValueError("consultation preparation changed its frozen purpose or specialists")
                expected_inputs = (*request.basis.inputs, *tuple(CollaborationInputRevision(
                    input_id=alias, kind=CollaborationInputKind.CONVERSATION_EVENT,
                    locator=f"conversation/{origin.event_id}", state=CollaborationInputState.PRESENT,
                    revision_digest="sha256:" + hashlib.sha256(json.dumps(asdict(origin.payload),
                        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                ) for origin, alias in ((request_event, "recommendation_request"),
                                       (decision_event, "recommendation_decision"))))
                if payload.basis.inputs != expected_inputs:
                    raise ValueError("consultation preparation changed its disclosed input boundaries")
            elif type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
                if (payload.action is LeadObjectiveAction.CANCEL
                    or (request.objective_id is not None and payload.facts.objective_id != request.objective_id)
                    or (request.objective_id is None and (
                        request.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                        or type(payload) is not ArtifactObjectivePayloadV2
                        or payload.action is not LeadObjectiveAction.START))
                    or payload.draft_sources != request.draft_sources):
                    raise ValueError("preparation changed its frozen objective or draft lineage")
        elif (state is LeadRecommendationState.PENDING and type(payload) is CollaborationMessagePayload
              and event.direction is CollaborationDirection.USER_TO_LEAD):
            if not any(item.batch_id == event.batch_id and item.sequence > event.sequence
                       and type(item.payload) is LeadRecommendationResultPayload
                       and item.payload.superseding_user_event_id == event.event_id
                       for item in events):
                raise ValueError("ordinary input must atomically supersede the pending recommendation")
        elif state is LeadRecommendationState.PENDING and type(payload) in {
            CollaborationProviderCallPayload, CollaborationLeadJudgmentPayload,
            CollaborationAssignmentDispatchPayload,
        }:
            raise ValueError("pending recommendation must be decided or superseded before advancement")
        elif state is LeadRecommendationState.RUNNING and (
            type(payload) is CollaborationAssignmentDispatchPayload
            or (type(payload) is CollaborationStepStatePayload
                and payload.state is CollaborationStepState.AWAITING_CONFIRMATION)
        ):
            raise ValueError("preparation must close before its separate specialist request")
        by_id[event.event_id] = event
    if decision is not None and start is None and result is None:
        raise ValueError("recommendation decision must co-batch its start or terminal result")
    return request, decision, start, result, state


def _lead_abandonment_is_safe(
    events: tuple[CollaborationEventV2, ...], turn_id: str, step_id: str,
) -> bool:
    """Exclude execution/write authority before exposing read-only retry."""

    task_ids: set[str] = set()
    controls = tuple(event for event in events
                     if type(event.payload) is CollaborationLeadTaskTransitionPayload
                     and (event.turn_id, event.step_id) == (turn_id, step_id))
    if controls:
        try:
            _project_task_context(events, project_collaboration_history(()))
        except (TypeError, ValueError):
            return False
        for control in controls:
            receipt = control.payload.receipt
            task_ids.update((control.event_id, receipt.origin_event_id))
            task_ids.update(receipt.material_capture_event_ids)
        if any(control.payload.receipt.action is LeadTaskAction.DISCUSS for control in controls):
            current = tuple(event for event in events
                            if (event.turn_id, event.step_id) == (turn_id, step_id))
            allowed = {
                CollaborationLeadTaskDecisionPayload, CollaborationLeadTaskTransitionPayload,
                CollaborationLeadMaterialCapturePayload, CollaborationMessagePayload,
                CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
                CollaborationLeadPublicResponsePayload,
            }
            return all(type(event.payload) in allowed for event in current) and all(
                event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                for event in current if type(event.payload) is CollaborationProviderCallPayload
            )
    try:
        _validate_premise_history(events)
    except (ValueError, TypeError, KeyError):
        return False
    safe_premise_ids = set()
    for captured in events:
        if captured.turn_id == turn_id and type(captured.payload) is CollaborationPremiseSelectionPayload:
            capture = captured.payload.capture
            control = next((item for item in events if item.event_id == capture.selection_event_id
                and item.turn_id == turn_id and type(item.payload) is CollaborationPremiseDecisionPayload
                and item.payload.selection == capture.selection), None)
            if control is not None:
                safe_premise_ids.update((captured.event_id, control.event_id))
        elif captured.turn_id == turn_id and type(captured.payload) is CollaborationPremiseFreshWorkAdmissionPayload:
            safe_premise_ids.add(captured.event_id)
    states: dict[str, CollaborationStepState] = {}
    pending_writes: set[str] = set()
    unsafe_write = False
    has_proposal = False
    origin = _recommendation_origin(events, turn_id, step_id)
    origin_event_ids = set() if origin is None else {item.event_id for item in origin}
    user_event_ids = {
        event.event_id for event in events if event.turn_id == turn_id
        and type(event.payload) is CollaborationMessagePayload
        and event.direction is CollaborationDirection.USER_TO_LEAD
    }
    for event in events:
        payload = event.payload
        if type(payload) is CollaborationStepStatePayload:
            states[event.step_id] = payload.state
        if type(payload) in {ArtifactApplicationRequestPayload, StoryCommitRequestPayload}:
            pending_writes.add(payload.confirmation_request_id)
        elif type(payload) in {ArtifactApplicationResultPayload, StoryCommitResultPayload}:
            pending_writes.discard(payload.confirmation_request_id)
            if payload.outcome.value in {"outcome_unknown", "recovery_required"}:
                unsafe_write = True
        if (event.turn_id == turn_id
            and type(payload) is CollaborationProviderCallPayload
            and payload.invocation_kind not in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}):
            return False
        if event.turn_id == turn_id and event.event_id not in origin_event_ids and event.event_id not in safe_premise_ids and event.event_id not in task_ids and type(payload) not in {
            CollaborationMessagePayload,
CollaborationLeadExplorationRequestPayload, CollaborationExplorationAdoptionPayload, CollaborationProviderCallPayload,
            CollaborationLeadOutcomeFailurePayload,
        } and not (
            (type(payload) is CollaborationStepStatePayload
             and payload.state is CollaborationStepState.SUPERSEDED)
            or (type(payload) is CollaborationConfirmationPayload
                and payload.decision is CollaborationConfirmationDecision.REVISE)
            or (type(payload) is ArtifactProposalStatePayload
                and payload.state is ArtifactProposalState.SUPERSEDED)
            or (type(payload) is LeadRecommendationResultPayload
                and payload.state is LeadRecommendationState.SUPERSEDED
                and payload.superseding_user_event_id in user_event_ids
                and payload.decision_event_id is None and payload.start_event_id is None)
        ):
            return False
        if event.step_id == step_id and type(payload) is CollaborationProposal:
            has_proposal = True
    if has_proposal and states.get(step_id) is not CollaborationStepState.SUPERSEDED:
        return False
    return not pending_writes and not unsafe_write and not any(
        state in {
            CollaborationStepState.CONFIRMED, CollaborationStepState.RUNNING,
            CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED,
        }
        for state in states.values()
    )


def _validate_exploration_history(events: tuple[CollaborationEventV2, ...]) -> None:
    prior: dict[str, CollaborationEventV2] = {}
    exploration_turns: set[str] = set()
    for event in events:
        payload = event.payload
        if event.turn_id in exploration_turns and type(payload) not in {CollaborationMessagePayload, CollaborationLeadExplorationRequestPayload, CollaborationLeadExplorationResultPayload, CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload, CollaborationStepStatePayload}:
            raise ValueError('exploration history cannot grant normal workflow authority')
        if type(payload) is CollaborationLeadExplorationRequestPayload:
            user = prior.get(payload.user_event_id)
            if user is None or type(user.payload) is not CollaborationMessagePayload or user.sender.kind.value != 'user' or ((user.session_id, user.conversation_id, user.turn_id) != (event.session_id, event.conversation_id, event.turn_id)):
                raise ValueError('exploration request requires its exact current user input')
            if event.sender.kind.value != 'user':
                raise ValueError('only the user selects exploration')
            exploration_turns.add(event.turn_id)
        elif type(payload) is CollaborationLeadExplorationResultPayload:
            request = prior.get(payload.report.request_event_id)
            if request is None or type(request.payload) is not CollaborationLeadExplorationRequestPayload or (request.session_id, request.conversation_id, request.turn_id, request.step_id) != (event.session_id, event.conversation_id, event.turn_id, event.step_id):
                raise ValueError('exploration report has no matching explicit request')
            if event.sender.kind.value != 'lead' or event.sender.agent_id != 'lead_orchestrator':
                raise ValueError('exploration report requires the Lead actor')
        elif type(payload) is CollaborationExplorationAdoptionPayload:
            capture = payload.capture
            user = prior.get(capture.user_event_id)
            source = prior.get(capture.selection.result_event_id)
            if user is None or user.sender.kind.value != 'user' or (user.session_id, user.conversation_id, user.turn_id) != (event.session_id, event.conversation_id, event.turn_id) or (event.sender.kind.value != 'user'):
                raise ValueError('adoption requires its exact explicit user input')
            if capture.selection.source_conversation_id == event.conversation_id:
                if source is None or type(source.payload) is not CollaborationLeadExplorationResultPayload or source.session_id != event.session_id:
                    raise ValueError('adoption requires an available typed idea result')
                ideas = tuple(i for i in source.payload.report.ideas if i.idea_id == capture.selection.idea_id)
                if len(ideas) != 1:
                    raise ValueError('adoption idea is unavailable or ambiguous')
                capture.validate_source(ideas[0].material_text, hashlib.sha256(encode_collaboration_event(source)).hexdigest())
                reports = tuple(item for item in prior.values() if type(item.payload) is CollaborationLeadExplorationResultPayload)
                if not reports or reports[-1].event_id != source.event_id:
                    raise ValueError('adoption cannot silently reselect a stale result')
            if any((type(item.payload) is CollaborationExplorationAdoptionPayload and item.payload.capture.fresh_task_id == capture.fresh_task_id for item in prior.values())) or any((getattr(item.payload, 'authoring_scope', None) is not None and item.payload.authoring_scope.task_id == capture.fresh_task_id for item in prior.values())):
                raise ValueError('adoption cannot reuse a previous task')
        if type(payload) is CollaborationProposal and payload.authoring_scope is not None:
            capture_events = tuple((item for item in prior.values() if type(item.payload) is CollaborationExplorationAdoptionPayload and item.payload.capture.fresh_task_id == payload.authoring_scope.task_id))
            if capture_events:
                adopted = capture_events[0]
                capture = adopted.payload.capture
                inputs = tuple((i for i in payload.basis.inputs if i.input_id == 'adopted_idea'))
                if any(('adopted_idea' not in assignment.input_ids for assignment in payload.assignments)):
                    raise ValueError('every adopted-task assignment must retain the exact chosen idea')
                if len(inputs) != 1 or inputs[0].locator != f'conversation/{adopted.event_id}' or inputs[0].revision_digest != f'sha256:{capture.selected_material_digest}':
                    raise ValueError('adopted task must preserve its exact selected idea input')
                scope = payload.authoring_scope
                if scope.task_revision == 1 and (not any((type(item.payload) is CollaborationProposal and item.payload.authoring_scope is not None and (item.payload.authoring_scope.task_id == scope.task_id) for item in prior.values()))):
                    activities = ({AuthoringActivity.REVIEW} if capture.selection.intent is AuthoringIntent.REVIEW_ONLY
                              else {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
                    if scope.activity not in activities or scope.intent is not capture.selection.intent or scope.target_paths != capture.selection.target_paths or (payload.budget.rounds_spent != 0):
                        raise ValueError('adoption requires fresh work with the exact selected intent and targets')
        prior[event.event_id] = event


def _premise_source_cost(source, past):
    cost = source.cost
    proposals = tuple(event.payload for event in past if type(event.payload) is CollaborationProposal
        and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)
        == (cost.proposal_id, cost.proposal_revision, cost.proposal_digest))
    if len(proposals) != 1:
        raise ValueError("premise prior authoring source is unavailable")
    proposal = proposals[0]
    scope = proposal.authoring_scope
    if scope is None or scope.intent is not AuthoringIntent.AUTHORING or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or (scope.task_id, scope.task_revision) != (source.task.task_id, source.task.task_revision):
        raise ValueError("premise cost source is not the exact authoring draft")
    scopes = tuple(getattr(event.payload, "authoring_scope", None) for event in past)
    latest = max((item.task_revision for item in scopes if item is not None and item.task_id == scope.task_id), default=0)
    if latest != scope.task_revision or proposal not in confirmed_authoring_proposals(scope, past):
        raise ValueError("premise prior authoring source is stale or unconfirmed")
    result = next((event for event in past if type(event.payload) is CollaborationExecutionResult
        and event.payload.proposal_digest == proposal.proposal_digest
        and event.payload.execution_attempt_id == cost.execution_attempt_id
        and event.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED), None)
    if result is None or not any(type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.COMPLETED
        and event.payload.execution_result_event_id == result.event_id for event in past):
        raise ValueError("premise prior authoring source lacks successful terminal proof")
    steps = {proposal.step_id, *(event.step_id for event in past
        if type(event.payload) is LeadRecommendationRequestPayload
        and (event.payload.source_proposal_id, event.payload.source_proposal_revision, event.payload.source_proposal_digest)
        == (proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest))}
    calls = tuple(event.payload.call_id for event in past if event.step_id in steps
        and type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED)
    rounds = sum(event.step_id in steps and type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.RUNNING for event in past)
    if cost.submitted_call_ids != calls or cost.rounds_spent != rounds or cost.step_id != proposal.step_id:
        raise ValueError("premise prior costs cannot reset source calls or rounds")


def _validate_premise_history(events):
    if not any(type(event.payload) in {
        CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
        CollaborationPremiseFreshWorkAdmissionPayload,
    } or (type(event.payload) is CollaborationProposal and event.payload.premise_decision is not None)
       or (type(event.payload) is CollaborationLeadSynthesisPayload and event.payload.premise_report is not None)
       or (type(event.payload) is CollaborationSpecialistReplyPayload and event.payload.premise_conflicts)
       for event in events):
        return (None, None, None, None, None, None)
    prior = {}
    admissions = {}
    admission_steps = {}
    decisions = {}
    reports = []
    captures = []
    latest_decision = None
    latest_admission = None
    for event in events:
        payload = event.payload
        needs_past = type(payload) in {
            CollaborationPremiseFreshWorkAdmissionPayload, CollaborationPremiseDecisionPayload,
            CollaborationPremiseSelectionPayload,
        LeadRecommendationRequestPayload, } or (type(payload) is CollaborationProposal and (
            payload.premise_decision is not None or event.step_id in admission_steps
            or (payload.authoring_scope is not None and any(
                old.payload.capture.authoring_task_id == payload.authoring_scope.task_id for old in captures)))) or (type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None) or (type(payload) is CollaborationSpecialistReplyPayload and payload.premise_conflicts)
        past = tuple(prior.values()) if needs_past else ()
        if type(payload) is CollaborationPremiseFreshWorkAdmissionPayload:
            item = payload.admission
            origin = prior.get(item.source_user_event_id)
            if origin is None or origin.sender.kind.value != "user" or type(origin.payload) not in {CollaborationMessagePayload, CollaborationPremiseDecisionPayload} or (origin.session_id, origin.conversation_id, origin.turn_id) != (event.session_id, event.conversation_id, event.turn_id) or item.provider_step_id != event.step_id:
                raise ValueError("fresh-work admission has no exact current USER origin")
            if item.source_user_event_digest != "sha256:" + hashlib.sha256(encode_collaboration_event(origin)).hexdigest():
                raise ValueError("fresh-work admission USER identity changed")
            if item.provider_invocation_id in admissions or any(type(old.payload) is CollaborationProviderCallPayload and old.payload.invocation_id == item.provider_invocation_id for old in past):
                raise ValueError("fresh-work admission must precede its fresh invocation")
            if item.prior_admission_ref is not None:
                parent = prior.get(item.prior_admission_ref.event_id)
                if parent is None or type(parent.payload) is not CollaborationPremiseFreshWorkAdmissionPayload or premise_digest(parent.payload.admission) != item.prior_admission_ref.digest:
                    raise ValueError("fresh-work prior admission identity changed")
                if latest_admission is None or parent.event_id != latest_admission.event_id:
                    raise ValueError("fresh-work admission cannot omit a newer cost head")
            elif latest_admission is not None:
                raise ValueError("fresh-work admission cannot erase earlier admitted spend")
            if item.prior_analysis_report_ref is not None:
                ref = item.prior_analysis_report_ref
                source = prior.get(ref.report_event_id)
                if source is None or type(source.payload) is not CollaborationLeadSynthesisPayload or source.payload.premise_report is None or source.conversation_id != event.conversation_id or ref.source_conversation_id != event.conversation_id or (source.payload.premise_report.report_digest, source.payload.premise_report.spec.question_id) != (ref.report_digest, ref.question_id):
                    raise ValueError("fresh-work prior analysis report changed")
            if item.prior_authoring_source_ref is not None:
                _premise_source_cost(item.prior_authoring_source_ref, past)
            same_turn_captures = tuple(
                old.payload.capture for old in captures
                if (old.turn_id, old.step_id) == (event.turn_id, event.step_id)
            )
            if item.selected_authoring_task is None:
                if same_turn_captures:
                    raise ValueError("fresh-work admission omitted its exact selected premise task")
            elif (
                len(same_turn_captures) != 1
                or item.selected_authoring_task != PremiseAuthoringTaskRef(
                    same_turn_captures[0].authoring_task_id,
                    same_turn_captures[0].authoring_task_revision,
                )
            ):
                raise ValueError("fresh-work selected task lacks its exact current USER capture")
            admissions[item.provider_invocation_id] = event
            admission_steps[event.step_id] = event
            latest_admission = event
        elif type(payload) is CollaborationProviderCallPayload and payload.invocation_id in admissions:
            source = admissions[payload.invocation_id]
            if (event.session_id, event.conversation_id, event.turn_id, event.step_id, payload.role, payload.invocation_kind) != (source.session_id, source.conversation_id, source.turn_id, source.step_id, "lead_orchestrator", CollaborationInvocationKind.LEAD):
                raise ValueError("fresh-work call drifts from its admission")
        elif type(payload) is CollaborationProposal and payload.premise_decision is not None:
            spec = payload.premise_decision
            origin = prior.get(spec.source_user_event_id)
            if origin is None or origin.sender.kind.value != "user" or origin.conversation_id != event.conversation_id:
                raise ValueError("premise question requires actual USER origin")
            if any(type(old.payload) is CollaborationProposal and old.payload.premise_decision is not None and old.payload.premise_decision.question_id == spec.question_id and old.step_id != event.step_id for old in past):
                raise ValueError("completed premise question cannot restart in a fresh lineage")
            if spec.admission_ref is not None:
                admitted = prior.get(spec.admission_ref.event_id)
                if admitted is None or type(admitted.payload) is not CollaborationPremiseFreshWorkAdmissionPayload or premise_digest(admitted.payload.admission) != spec.admission_ref.digest or admitted.step_id != event.step_id or admitted.payload.admission.offered_question_id != spec.question_id or admitted.payload.admission.source_user_event_id != spec.source_user_event_id:
                    raise ValueError("premise question changed its offered admission")
                source_task = (None if admitted.payload.admission.prior_authoring_source_ref is None
                    else admitted.payload.admission.prior_authoring_source_ref.task)
                if spec.source_authoring_task != source_task:
                    raise ValueError("premise question changed its actual prior authoring source")
            elif spec.source_authoring_task is not None:
                raise ValueError("premise question cannot invent a prior authoring source")
        elif type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is not None:
            report = payload.premise_report
            previous = reports[-1] if reports else None
            ref = report.prior_analysis_report_ref
            if previous is None:
                if ref is not None:
                    raise ValueError("premise report invents earlier analysis costs")
            elif ref is None or (ref.source_conversation_id, ref.report_event_id, ref.report_digest, ref.question_id) != (
                event.conversation_id, previous.event_id, previous.payload.premise_report.report_digest,
                previous.payload.premise_report.spec.question_id):
                raise ValueError("premise report cannot erase or change earlier analysis cost identity")
            proposal = next((old.payload for old in past if type(old.payload) is CollaborationProposal and old.payload.proposal_digest == payload.proposal_digest), None)
            if proposal is None or proposal.premise_decision != report.spec or report.source_conversation_id != event.conversation_id or report.step_id != event.step_id:
                raise ValueError("premise report changed its exact proposal question")
            admitted = None if report.spec.admission_ref is None else prior.get(report.spec.admission_ref.event_id)
            current_admission = None if admitted is None else admitted.payload.admission
            expected_parent = (current_admission.prior_admission_ref if current_admission is not None else
                None if latest_admission is None else PremiseAdmissionRef(latest_admission.event_id, premise_digest(latest_admission.payload.admission)))
            expected_authoring = (None if current_admission is None or current_admission.prior_authoring_source_ref is None
                else current_admission.prior_authoring_source_ref.cost)
            if report.prior_admission_ref != expected_parent or report.prior_authoring_cost != expected_authoring:
                raise ValueError("premise report changed captured prior cost heads")
            refs = tuple(prior.get(ref.event_id) for ref in report.source_reply_refs)
            if any(old is None or type(old.payload) is not CollaborationSpecialistReplyPayload or old.payload.premise_analysis is None for old in refs):
                raise ValueError("premise report invents an analyst reply")
            for ref, old, assignment in zip(report.source_reply_refs, refs, proposal.assignments, strict=True):
                reply = old.payload
                reply_value = {"assessment": reply.assessment.value,
                    "assignment_id": reply.assignment_id, "message": reply.message,
                    "input_ids": list(reply.input_ids), "mode": reply.mode.value,
                    "premise_analysis": premise_value_to_dict(reply.premise_analysis)}
                actual_digest = "sha256:" + hashlib.sha256(json.dumps(reply_value,
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
                if ref.specialist_id != assignment.specialist_id or ref.reply_digest != actual_digest:
                    raise ValueError("premise analyst owner or exact reply material changed")
                if reply.assignment_id != assignment.assignment_id or reply.proposal_digest != proposal.proposal_digest or reply.execution_attempt_id != report.execution_attempt_id:
                    raise ValueError("premise analyst source identity changed")
                for option in report.options:
                    disposition = next(item for item in option.reply_dispositions if item.reply_event_id == ref.event_id)
                    if not set(disposition.alternative_keys) <= {item.alternative_key for item in reply.premise_analysis.alternatives}:
                        raise ValueError("premise option invents an analyst alternative")
                    if disposition.disposition == "unavailable" and reply.assessment is not CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE and reply.premise_analysis.alternatives:
                        raise ValueError("premise option hides available analysis")
            actual = tuple(old.payload.call_id for old in past if old.step_id == event.step_id and type(old.payload) is CollaborationProviderCallPayload and old.payload.state is CollaborationProviderCallState.SUBMITTED)
            if report.cost.submitted_call_ids != actual:
                raise ValueError("premise report cost differs from actual submissions")
            if report.cost.rounds_spent != sum(type(old.payload) is CollaborationStepStatePayload
                    and old.payload.state is CollaborationStepState.RUNNING and old.step_id == event.step_id for old in past):
                raise ValueError("premise report rounds differ from durable RUNNING facts")
            result = next((old for old in events if old.batch_id == event.batch_id
                and type(old.payload) is CollaborationExecutionResult
                and old.payload.synthesis_result_event_id == event.event_id
                and (old.payload.proposal_id, old.payload.proposal_revision, old.payload.proposal_digest,
                     old.payload.execution_attempt_id) == (report.proposal_id, report.proposal_revision,
                     report.proposal_digest, report.execution_attempt_id)), None)
            if result is None or not any(old.batch_id == event.batch_id
                    and type(old.payload) is CollaborationStepStatePayload
                    and old.payload.execution_result_event_id == result.event_id
                    and old.payload.state in {CollaborationStepState.COMPLETED, CollaborationStepState.FAILED,
                        CollaborationStepState.INTERRUPTED} for old in events):
                raise ValueError("premise report requires its exact co-batched result and terminal state")
            reports.append(event)
        elif type(payload) is CollaborationPremiseDecisionPayload:
            selection = payload.selection
            messages = tuple(old for old in past if old.turn_id == event.turn_id and old.step_id == event.step_id
                and old.sender.kind.value == "user" and type(old.payload) is CollaborationMessagePayload
                and old.batch_id == event.batch_id)
            if len(messages) != 1:
                raise ValueError("premise USER control requires its exact co-batched message origin")
            if selection.request_id in decisions:
                raise ValueError("premise USER request accepts only one exact durable intake")
            source = prior.get(selection.report_event_id)
            if not reports or source is not reports[-1] or source.conversation_id != event.conversation_id or selection.source_conversation_id != event.conversation_id:
                raise ValueError("premise USER decision selects a stale or foreign report")
            report = source.payload.premise_report
            if (selection.report_digest, selection.question_id) != (report.report_digest, report.spec.question_id):
                raise ValueError("premise USER decision changed report identity")
            if selection.action is PremiseDecisionAction.CHOOSE:
                actual = {(item.option_id, item.material_digest) for item in report.options}
                allowed = ({(selection.option_id, selection.material_digest)} if selection.choice_policy is PremiseChoicePolicy.USER_CHOICE
                    else {(item.option_id, item.material_digest) for item in selection.allowed_choices})
                if not allowed <= actual:
                    raise ValueError("premise USER decision changed actual whole option material")
            if any(old.payload.selection.report_event_id == source.event_id and old.payload.selection.action is PremiseDecisionAction.REJECT for old in decisions.values()):
                raise ValueError("rejected premise report cannot become a choice")
            result = next((old for old in past if type(old.payload) is CollaborationExecutionResult and old.payload.synthesis_result_event_id == source.event_id and old.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED), None)
            if result is None or not any(type(old.payload) is CollaborationStepStatePayload and old.payload.state is CollaborationStepState.COMPLETED and old.payload.execution_result_event_id == result.event_id for old in past):
                raise ValueError("premise selection requires successful terminal analysis")
            decisions[selection.request_id] = event
            latest_decision = selection
        elif type(payload) is CollaborationPremiseSelectionPayload:
            capture = payload.capture
            origin = prior.get(capture.selection_event_id)
            if origin is None or type(origin.payload) is not CollaborationPremiseDecisionPayload or origin.payload.selection != capture.selection or origin.batch_id != event.batch_id or (origin.turn_id, origin.step_id) != (event.turn_id, event.step_id):
                raise ValueError("premise capture requires its co-batched exact USER choice")
            source = prior[capture.selection.report_event_id]
            report = source.payload.premise_report
            option = resolve_premise_choice(capture.selection, report)
            if (capture.option_id, capture.material_digest, capture.selected_text, capture.slot_id, capture.dependent_domains) != (option.option_id, option.material_digest, option.premise_text, report.spec.slot_id, report.spec.dependent_domains):
                raise ValueError("premise capture changed whole option bytes or domains")
            if capture.cost_link.analysis != report.cost or capture.cost_link.prior_authoring != report.prior_authoring_cost or capture.cost_link.prior_admission_ref != report.prior_admission_ref:
                raise ValueError("premise capture erased original analysis costs")
            scopes = tuple(getattr(old.payload, "authoring_scope", None) for old in past)
            same = tuple(item for item in scopes if item is not None and item.task_id == capture.authoring_task_id)
            if capture.selection.authoring_task_id is None:
                if same or any(old.payload.capture.authoring_task_id == capture.authoring_task_id for old in captures):
                    raise ValueError("premise selection cannot reuse a previous task")
            elif not same or max(item.task_revision for item in same) != capture.selection.expected_authoring_task_revision:
                raise ValueError("premise successor choice has a stale task revision")
            captures.append(event)
        if type(payload) is LeadRecommendationRequestPayload and payload.authoring_scope is not None:
            admitted_event = admission_steps.get(event.step_id)
            if admitted_event is not None:
                admitted = admitted_event.payload.admission
                scope = payload.authoring_scope
                pair = PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                source = prior.get(payload.source_recommendation_event_id)
                if (source is None or not _is_fresh_drafting_preparation(payload, source, past)
                        or pair not in admitted.offered_authoring_tasks
                        or (admitted.selected_authoring_task is not None
                            and pair != admitted.selected_authoring_task)):
                    raise ValueError("fresh-work recommendation changed its exact prospectively admitted task")
        if type(payload) is CollaborationProposal and payload.authoring_scope is not None:
            scope = payload.authoring_scope
            admitted = next((old.payload.admission for old in reversed(past)
                if type(old.payload) is CollaborationPremiseFreshWorkAdmissionPayload and old.step_id == event.step_id), None)
            if admitted is not None and (
                PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                not in admitted.offered_authoring_tasks
                or (
                    admitted.selected_authoring_task is not None
                    and PremiseAuthoringTaskRef(scope.task_id, scope.task_revision)
                    != admitted.selected_authoring_task
                )
            ):
                raise ValueError("fresh-work proposal changed its prospectively admitted task")
            if admitted is not None and (scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    or scope.intent is not AuthoringIntent.AUTHORING or payload.budget.rounds_spent):
                raise ValueError("fresh-work authoring requires fresh owner drafts and Confirm")
            relevant = tuple(old for old in captures if old.payload.capture.authoring_task_id == scope.task_id and old.payload.capture.authoring_task_revision <= scope.task_revision)
            if relevant:
                chosen = relevant[-1]
                capture = chosen.payload.capture
                selected = tuple(item for item in payload.basis.inputs if item.input_id == "selected_premise")
                if len(selected) != 1 or selected[0].kind is not CollaborationInputKind.CONVERSATION_EVENT or selected[0].locator != f"conversation/{chosen.event_id}" or selected[0].revision_digest != "sha256:" + capture.material_digest or selected[0].start_line is not None or selected[0].end_line is not None or any("selected_premise" not in item.input_ids for item in payload.assignments):
                    raise ValueError("every chosen-premise writer/reviewer needs the exact current whole capture")
                if not set(capture.dependent_domains) <= set(scope.domains):
                    raise ValueError("chosen premise cannot omit an affected specialist domain")
                matching = tuple(item for item in payload.basis.shared_premises if item.slot_id == capture.slot_id)
                if len(matching) != 1 or matching[0].selected_premise != capture.selected_text or not any(item.input_revision == selected[0] for item in matching[0].sources):
                    raise ValueError("chosen premise must bind its disclosed dependency slot")
                if scope.task_revision == capture.authoring_task_revision and not any(type(old.payload) is CollaborationProposal and old.payload.authoring_scope is not None and (old.payload.authoring_scope.task_id, old.payload.authoring_scope.task_revision) == (scope.task_id, scope.task_revision) for old in past):
                    if scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or scope.intent is not AuthoringIntent.AUTHORING or scope.target_paths != capture.selection.target_paths or payload.budget.rounds_spent:
                        raise ValueError("chosen premise starts fresh authoring drafts, never supplied-candidate review")
        if type(payload) is CollaborationSpecialistReplyPayload and payload.premise_conflicts:
            proposal = next((old.payload for old in past if type(old.payload) is CollaborationProposal
                and old.payload.proposal_digest == payload.proposal_digest), None)
            relevant = tuple(old.payload.capture for old in captures if proposal is not None
                and proposal.authoring_scope is not None
                and old.payload.capture.authoring_task_id == proposal.authoring_scope.task_id
                and old.payload.capture.authoring_task_revision <= proposal.authoring_scope.task_revision)
            if not relevant or any((conflict.slot_id, conflict.capture_digest) != (
                relevant[-1].slot_id, relevant[-1].capture_digest) for conflict in payload.premise_conflicts):
                raise ValueError("dependent draft conflict lacks the exact current USER capture")
        prior[event.event_id] = event
    current_report = reports[-1] if reports else None
    if latest_decision is not None and (current_report is None or latest_decision.report_event_id != current_report.event_id):
        latest_decision = None
    current_capture = None if not captures else captures[-1].payload.capture
    return (None if current_report is None else current_report.payload.premise_report,
            None if current_report is None else current_report.event_id,
            None if latest_decision is None else latest_decision,
            current_capture, None if latest_admission is None else latest_admission.payload.admission,
            None if latest_admission is None else latest_admission.event_id)


def _task_scope_digest(scope: AuthoringScope) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        asdict(scope), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _public_response_omission_call(
    event: CollaborationEvent, events: tuple[CollaborationEvent, ...],
) -> CollaborationProviderCallPayload | None:
    """Recognize only closed runtime display metadata bound to a real call."""
    payload = event.payload
    if (type(payload) is not CollaborationMessagePayload
            or event.sender.kind is not CollaborationActorKind.RUNTIME
            or event.recipient.kind is not CollaborationActorKind.USER
            or payload.trace is not None or payload.validation_details
            or payload.validation_invocation_id is not None
            or payload.validation_call_id is not None):
        return None
    for source in events:
        call = source.payload
        if (type(call) is not CollaborationProviderCallPayload
                or call.state is not CollaborationProviderCallState.SUBMITTED
                or call.invocation_kind not in {
                    CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_DISCUSSION,
                }
                or source.sequence >= event.sequence
                or (source.session_id, source.conversation_id, source.turn_id, source.step_id) != (
                    event.session_id, event.conversation_id, event.turn_id, event.step_id)):
            continue
        ending = f" call {call.call_id}: no bounded literal final response_text was recoverable."
        if payload.content == "Public response unavailable for accepted" + ending:
            return call
        if payload.content == "Public response unavailable for rejected" + ending and any(
            type(item.payload) is CollaborationMessagePayload
            and item.sender.kind is CollaborationActorKind.RUNTIME
            and item.recipient.kind is CollaborationActorKind.USER
            and item.sequence < event.sequence
            and (item.session_id, item.conversation_id, item.turn_id, item.step_id) == (
                event.session_id, event.conversation_id, event.turn_id, event.step_id)
            and item.payload.validation_details
            and (item.payload.validation_invocation_id, item.payload.validation_call_id) == (
                call.invocation_id, call.call_id)
            for item in events
        ):
            return call
    return None


def _validate_authoring_admission_provenance(
    proposal: CollaborationProposal, prior: dict[str, CollaborationEvent],
) -> None:
    """Validate immutable USER/material origins on original physical history."""
    basis = proposal.basis.authoring_admission
    if basis is None:
        return
    if type(basis) is not AuthoringAdmissionBasis:
        raise ValueError("authoring admission basis is invalid")
    for source in basis.sources:
        if source.kind is AdmissionSourceKind.USER:
            prefix, suffix = "conversation/", "/user_input"
            if not source.source_identity.startswith(prefix) or not source.source_identity.endswith(suffix):
                raise ValueError("authoring admission USER source identity is invalid")
            turn_id = source.source_identity[len(prefix):-len(suffix)]
            origins = tuple(event for event in prior.values()
                if event.turn_id == turn_id and type(event.payload) is CollaborationMessagePayload
                and event.sender.kind is CollaborationActorKind.USER
                and event.recipient.kind is CollaborationActorKind.LEAD)
            if len(origins) != 1:
                raise ValueError("authoring admission USER origin is missing or ambiguous")
            digest = "sha256:" + hashlib.sha256(origins[0].payload.content.encode("utf-8")).hexdigest()
            if (digest != source.revision_digest
                    or len(origins[0].payload.content.encode("utf-8")) != source.utf8_bytes):
                raise ValueError("authoring admission USER bytes changed")
        elif source.kind is AdmissionSourceKind.MATERIAL:
            text = None
            if source.source_identity.startswith("conversation/"):
                origin = prior.get(source.source_identity.removeprefix("conversation/"))
                if origin is None:
                    raise ValueError("authoring admission material origin is unavailable")
                payload = origin.payload
                if type(payload) in {CollaborationLeadMaterialCapturePayload,
                        CollaborationPremiseSelectionPayload, CollaborationExplorationAdoptionPayload}:
                    text = payload.capture.selected_text
                elif type(payload) in {CollaborationSpecialistReplyPayload, CollaborationLeadSynthesisPayload}:
                    text = payload.message
                elif type(payload) is CollaborationLeadPublicResponsePayload:
                    text = payload.response.response_text
                elif type(payload) is CollaborationLeadDiscussionPayload:
                    text = payload.report.response_text
            length = None if type(text) is not str else len(text.encode("utf-8"))
            if length != source.utf8_bytes:
                raise ValueError("authoring admission material byte observation changed")


def _project_task_context(
    events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
) -> CollaborationHistoryProjection:
    """Validate USER controls and project literal material without execution proof."""
    def _artifact_revision_source(receipt, original, history) -> bool:
        if (
            receipt is None or original is None
            or receipt.origin_kind != "revise_decision"
            or receipt.action is not LeadTaskAction.RESUME
            or receipt.material_capture_event_ids
            or receipt.source_task_ref is None
            or original.authoring_scope is None
        ):
            return False
        scope = original.authoring_scope
        ref = receipt.source_task_ref
        if (
            scope.schema_version != 3
            or scope.intent is not AuthoringIntent.AUTHORING
            or scope.activity is not AuthoringActivity.REVIEW
            or (scope.task_id, scope.task_revision) != (ref.task_id, ref.task_revision)
            or (receipt.target_task_id, receipt.target_task_revision)
            != (scope.task_id, scope.task_revision + 1)
            or receipt.planning_step_id != original.step_id
            or ref.scope_digest != "sha256:" + hashlib.sha256(
                json.dumps(asdict(scope), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        ):
            return False
        by_id = {item.event_id: item for item in history}
        source = by_id.get(ref.scope_event_id)
        origin = by_id.get(receipt.origin_event_id)
        transitions = tuple(
            item for item in history
            if type(item.payload) is CollaborationLeadTaskTransitionPayload
            and item.payload.receipt == receipt
        )
        if (
            source is None or origin is None or len(transitions) != 1
            or (source.session_id, source.conversation_id) != (ref.session_id, ref.conversation_id)
            or getattr(source.payload, "authoring_scope", None) != scope
            or type(origin.payload) is not CollaborationConfirmationPayload
            or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
            or origin.sender.kind is not CollaborationActorKind.USER
            or origin.recipient.kind is not CollaborationActorKind.RUNTIME
            or (origin.session_id, origin.conversation_id, origin.step_id)
            != (ref.session_id, ref.conversation_id, original.step_id)
            or (origin.payload.proposal_id, origin.payload.proposal_revision, origin.payload.proposal_digest)
            != (original.proposal_id, original.proposal_revision, original.proposal_digest)
            or (transitions[0].session_id, transitions[0].conversation_id,
                transitions[0].turn_id, transitions[0].step_id, transitions[0].batch_id)
            != (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id, origin.batch_id)
        ):
            return False
        request = by_id.get(origin.payload.confirmation_request_event_id)
        if (
            request is None or type(request.payload) is not CollaborationStepStatePayload
            or request.payload.state not in {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
            }
            or request.payload.confirmation_request_id != origin.payload.confirmation_request_id
            or (request.payload.proposal_id, request.payload.proposal_revision, request.payload.proposal_digest)
            != (original.proposal_id, original.proposal_revision, original.proposal_digest)
        ):
            return False
        candidates = tuple(
            item for item in history
            if (item.session_id, item.conversation_id, item.step_id)
            == (origin.session_id, origin.conversation_id, origin.step_id)
            and item.sequence < origin.sequence
            and type(item.payload) is ArtifactReviewCandidatePayload
            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                 item.payload.consultation_proposal_digest)
            == (original.proposal_id, original.proposal_revision, original.proposal_digest)
        )
        if len(candidates) != 1:
            return False
        candidate = candidates[0].payload.candidate
        state = next((
            item.payload for item in reversed(history)
            if (item.session_id, item.conversation_id, item.step_id)
            == (origin.session_id, origin.conversation_id, origin.step_id)
            and item.sequence < origin.sequence
            and type(item.payload) is ArtifactProposalStatePayload
            and (item.payload.candidate_id, item.payload.candidate_revision, item.payload.candidate_digest)
            == (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest)
            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                 item.payload.consultation_proposal_digest)
            == (original.proposal_id, original.proposal_revision, original.proposal_digest)
        ), None)
        return bool(
            state is not None
            and state.state is ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
            and state.confirmation_request_id == origin.payload.confirmation_request_id
        )

    prior: dict[str, CollaborationEvent] = {}
    tasks: dict[str, tuple[CollaborationEvent, AuthoringScope]] = {}
    goals: dict[str, str] = {}
    statuses: dict[str, str] = {}
    transitions: dict[str, CollaborationEvent] = {}
    captures: dict[str, CollaborationEvent] = {}
    choices: list[LeadMaterialChoice] = []
    public: list[LeadPublicResponseView] = []
    submitted_calls: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_prepared: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_submitted: set[tuple[str, str]] = set()
    discussion_ordinals: dict[str, int] = {}
    resumptions: dict[str, LeadTaskTransitionReceipt] = {}
    discussion: LeadTaskTransitionReceipt | None = None

    def correlated(left: CollaborationEvent, right: CollaborationEvent) -> bool:
        return (left.session_id, left.conversation_id, left.turn_id, left.step_id,
                left.batch_id) == (right.session_id, right.conversation_id,
                                  right.turn_id, right.step_id, right.batch_id)

    def source_scope(ref: LeadTaskRef) -> AuthoringScope:
        source = prior.get(ref.scope_event_id)
        scope = getattr(source.payload, "authoring_scope", None) if source is not None else None
        if source is None or scope is None or (
            source.session_id, source.conversation_id, scope.task_id,
            scope.task_revision, _task_scope_digest(scope),
        ) != (ref.session_id, ref.conversation_id, ref.task_id,
              ref.task_revision, ref.scope_digest):
            raise ValueError("task control source scope is not exact")
        latest = tasks.get(ref.task_id)
        if latest is None or latest[1].task_revision != ref.task_revision:
            raise ValueError("task control requires the current task revision")
        return scope

    for event in events:
        payload = event.payload
        if (type(payload) is CollaborationMessagePayload
                and event.sender.kind is CollaborationActorKind.RUNTIME
                and payload.content.startswith("Public response unavailable for ")
                and _public_response_omission_call(event, tuple(prior.values())) is None):
            raise ValueError("public omission notice requires exact submitted-call display facts")
        scope = getattr(payload, "authoring_scope", None)
        if type(payload) is CollaborationProposal:
            _validate_authoring_admission_provenance(payload, prior)
        if type(payload) is CollaborationProposal and scope is not None:
            planning = next((control.payload.receipt for control in reversed(tuple(transitions.values()))
                             if (control.payload.receipt.target_task_id,
                                 control.payload.receipt.target_task_revision)
                             == (scope.task_id, scope.task_revision)), None)
            if planning is not None:
                source_review_revision = False
                source_artifact_revision = False
                prepared_final_review = False
                if scope.activity is AuthoringActivity.REVIEW:
                    past = tuple(prior.values())
                    origin = prior.get(planning.origin_event_id)
                    original = next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            origin.payload.proposal_id,
                            origin.payload.proposal_revision,
                            origin.payload.proposal_digest,
                        )
                    ), None)
                    source_review_revision = bool(
                        planning.origin_kind == "revise_decision"
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and origin.payload.decision is CollaborationConfirmationDecision.REVISE
                        and original is not None
                        and original.authoring_scope is not None
                        and original.authoring_scope.activity is AuthoringActivity.REVIEW
                        and planning.source_task_ref is not None
                        and (
                            original.authoring_scope.task_id,
                            original.authoring_scope.task_revision,
                        ) == (
                            planning.source_task_ref.task_id,
                            planning.source_task_ref.task_revision,
                        )
                        and scope.task_id == original.authoring_scope.task_id
                        and scope.task_revision == original.authoring_scope.task_revision + 1
                        and event.step_id == planning.planning_step_id == origin.step_id == original.step_id
                        and payload.proposal_revision == original.proposal_revision + 1
                        and not planning.material_capture_event_ids
                        and _task_scope_digest(original.authoring_scope) == planning.source_task_ref.scope_digest
                        and not any(
                            item.batch_id == event.batch_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                        and not any(
                            type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (
                                original.proposal_id, original.proposal_revision, original.proposal_digest,
                            )
                            for item in past
                        )
                    )
                    request = next((
                        item.payload for item in reversed(past)
                        if item.step_id == event.step_id
                        and type(item.payload) is LeadRecommendationRequestPayload
                        and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                    ), None)
                    source = None if request is None else next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            request.source_proposal_id,
                            request.source_proposal_revision,
                            request.source_proposal_digest,
                        )
                    ), None)
                    prepared_final_review = bool(
                        request is not None
                        and request.source_execution_attempt_id is not None
                        and request.authoring_scope is not None
                        and request.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and (request.authoring_scope.task_id, request.authoring_scope.task_revision)
                        == (scope.task_id, scope.task_revision)
                        and source is not None
                        and source.authoring_scope is not None
                        and source.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and source in confirmed_authoring_proposals(scope, past)
                        and authoring_capture_identity(scope, payload.basis)
                        == authoring_capture_identity(source.authoring_scope, source.basis)
                        and any(
                            item.batch_id == event.batch_id
                            and item.turn_id == event.turn_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                    )
                    source_artifact_revision = _artifact_revision_source(planning, original, past)
                    if source_artifact_revision:
                        direct_replacement = bool(
                            event.step_id == planning.planning_step_id
                            and payload.proposal_id != original.proposal_id
                            and payload.proposal_revision == original.proposal_revision + 1
                        )
                        request_event = next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationRequestPayload
                            and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                        ), None)
                        preparation_start = None if request_event is None else next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationStartedPayload
                            and item.payload.request_event_id == request_event.event_id
                        ), None)
                        preparation_decision = None if preparation_start is None else prior.get(
                            preparation_start.payload.decision_event_id
                        )
                        preparation_source = None if request_event is None else prior.get(
                            request_event.payload.source_recommendation_event_id
                        )
                        prepared_replacement = bool(
                            request_event is not None
                            and request_event.payload.authoring_scope == scope
                            and (request_event.payload.source_proposal_id,
                                 request_event.payload.source_proposal_revision,
                                 request_event.payload.source_proposal_digest)
                            == (original.proposal_id, original.proposal_revision, original.proposal_digest)
                            and request_event.payload.source_execution_attempt_id is None
                            and preparation_source is not None
                            and preparation_source.step_id == planning.planning_step_id
                            and type(preparation_source.payload) is CollaborationLeadJudgmentPayload
                            and preparation_source.payload.kind is LeadOutcomeKind.RECOMMENDATION
                            and preparation_source.payload.authoring_scope == scope
                            and preparation_start is not None
                            and preparation_decision is not None
                            and type(preparation_decision.payload) is LeadRecommendationDecisionPayload
                            and preparation_decision.payload.decision.kind is CollaborationConfirmationDecision.CONFIRM
                            and any(
                                item.batch_id == event.batch_id
                                and item.step_id == event.step_id
                                and type(item.payload) is LeadRecommendationResultPayload
                                and item.payload.state is LeadRecommendationState.COMPLETED
                                and item.payload.request_event_id == request_event.event_id
                                and item.payload.start_event_id == preparation_start.event_id
                                and item.payload.decision_event_id == preparation_decision.event_id
                                for item in events
                            )
                        )
                        new_candidates = tuple(
                            item for item in events
                            if item.batch_id == event.batch_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (item.payload.consultation_proposal_id,
                                 item.payload.consultation_proposal_revision,
                                 item.payload.consultation_proposal_digest)
                            == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                        )
                        source_artifact_revision = bool(
                            (direct_replacement or prepared_replacement)
                            and (scope.task_id, scope.task_revision)
                            == (planning.target_task_id, planning.target_task_revision)
                            and len(new_candidates) == 1
                        )
                if (scope.schema_version != 3 or scope.intent is not AuthoringIntent.AUTHORING
                        or (scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                            and not (source_review_revision or source_artifact_revision) and not prepared_final_review)):
                    raise ValueError("task transition requires fresh authoring scope and owner drafts")
                required = tuple(f"selected_material_{index}" for index in
                                 range(1, len(planning.material_capture_event_ids) + 1))
                for alias, identity in zip(required, planning.material_capture_event_ids, strict=True):
                    captured = captures[identity].payload.capture
                    matching = tuple(item for item in payload.basis.inputs if item.input_id == alias)
                    if (len(matching) != 1 or matching[0].kind is not CollaborationInputKind.CONVERSATION_EVENT
                            or matching[0].state is not CollaborationInputState.PRESENT
                            or matching[0].locator != f"conversation/{identity}"
                            or matching[0].revision_digest != f"sha256:{captured.material_digest}"
                            or (matching[0].start_line is not None or matching[0].end_line is not None)
                            or any(alias not in assignment.input_ids for assignment in payload.assignments)):
                        raise ValueError("task proposal must retain each exact whole selected input for every owner")
        if scope is not None and scope.activity is not AuthoringActivity.WORKFLOW:
            previous = tasks.get(scope.task_id)
            if previous is None or scope.task_revision > previous[1].task_revision or (
                scope.task_revision == previous[1].task_revision
                and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
            ):
                tasks[scope.task_id] = event, scope
                statuses.setdefault(scope.task_id, "active")
            if scope.task_id not in goals:
                user = next((item for item in prior.values()
                             if (item.turn_id, item.step_id) == (event.turn_id, event.step_id)
                             and type(item.payload) is CollaborationMessagePayload
                             and item.sender.kind is CollaborationActorKind.USER), None)
                goals[scope.task_id] = scope.interpretation if user is None else user.payload.content
            pending = resumptions.get(scope.task_id)
            if (
                type(payload) is CollaborationProposal
                and pending is not None
                and (scope.task_id, scope.task_revision)
                == (pending.target_task_id, pending.target_task_revision)
            ):
                statuses[scope.task_id] = "active"
        if (type(payload) is CollaborationProviderCallPayload
                and payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION):
            control = next((item for item in transitions.values()
                            if item.payload.receipt.action is LeadTaskAction.DISCUSS
                            and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)), None)
            if control is None:
                raise ValueError("discussion call requires prior exact USER discussion receipt")
            key = payload.invocation_id, payload.call_id
            if payload.state is CollaborationProviderCallState.PREPARED:
                if key in discussion_prepared or payload.ordinal != discussion_ordinals.get(payload.invocation_id, 0) + 1:
                    raise ValueError("discussion preparation requires unique ordered physical call")
                discussion_prepared[key] = event
                discussion_ordinals[payload.invocation_id] = payload.ordinal
            else:
                prepared = discussion_prepared.get(key)
                if (prepared is None or key in discussion_submitted
                        or (prepared.session_id, prepared.conversation_id, prepared.turn_id, prepared.step_id,
                            prepared.payload.ordinal, prepared.payload.call_kind)
                        != (event.session_id, event.conversation_id, event.turn_id, event.step_id,
                            payload.ordinal, payload.call_kind)):
                    raise ValueError("discussion submission requires exact prepared call identity")
                discussion_submitted.add(key)
                if sum(identity[0] == payload.invocation_id for identity in discussion_submitted) > 6:
                    raise ValueError("discussion exceeds existing five-call front plus one correction")
        if type(payload) is CollaborationProviderCallPayload and payload.state is CollaborationProviderCallState.SUBMITTED:
            submitted_calls[(payload.invocation_id, payload.call_id)] = event
        if type(payload) is CollaborationLeadTaskDecisionPayload:
            decision = payload.decision
            if event.sender.kind is not CollaborationActorKind.USER or (
                event.event_id, event.session_id, event.conversation_id,
            ) != (decision.request_id, decision.session_id, decision.conversation_id):
                raise ValueError("task decision must be exact USER intake")
            if decision.task_ref is not None:
                source_scope(decision.task_ref)
                if decision.action in {LeadTaskAction.RESUME, LeadTaskAction.HOLD,
                                       LeadTaskAction.DISCARD} and statuses.get(
                    decision.task_ref.task_id,
                ) in {"discarded", "completed"}:
                    raise ValueError("retired task cannot regain workflow authority")
        elif type(payload) is CollaborationLeadMaterialCapturePayload:
            capture = payload.capture
            origin = prior.get(capture.origin_event_id)
            source = prior.get(capture.source.event_id)
            if origin is None or type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                raise ValueError("material capture requires exact USER choice")
            if not correlated(origin, event) or capture.source not in origin.payload.decision.material_refs:
                raise ValueError("material capture is detached from USER selection")
            if capture.source.conversation_id == event.conversation_id:
                if source is None or (source.session_id, source.conversation_id) != (
                    capture.source.session_id, capture.source.conversation_id,
                ):
                    raise ValueError("selected material source is unavailable")
                selected = next((item for item in choices if item.ref == capture.source), None)
                if selected is None or selected.text != capture.selected_text:
                    raise ValueError("selected material event/type/bytes changed")
            elif capture.source.session_id != event.session_id:
                raise ValueError("foreign material must belong to the bound session")
            # Foreign source existence/type/bytes are revalidated against the exact
            # SessionHistorySnapshot at intake, context delivery, and Confirm.

            captures[event.event_id] = event
        elif type(payload) is CollaborationLeadTaskTransitionPayload:
            receipt = payload.receipt
            origin = prior.get(receipt.origin_event_id)
            recommendation_origin = (origin is not None
                                     and type(origin.payload) is LeadRecommendationDecisionPayload
                                     and receipt.origin_kind == "revise_decision")
            same_origin_batch = (origin is not None
                                 and (origin.session_id, origin.conversation_id, origin.turn_id, origin.batch_id)
                                 == (event.session_id, event.conversation_id, event.turn_id, event.batch_id))
            if (origin is None or (not correlated(origin, event)
                    and not (recommendation_origin and same_origin_batch
                             and receipt.planning_step_id == event.step_id))):
                raise ValueError("task transition requires exact co-batched USER origin")
            if receipt.origin_kind == "task_decision":
                if type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                    raise ValueError("task transition origin type is invalid")
                decision = origin.payload.decision
                if (decision.action, decision.task_ref) != (receipt.action, receipt.source_task_ref):
                    raise ValueError("task transition changed the USER control")
            else:
                if recommendation_origin:
                    decision_value = origin.payload.decision
                    requested = prior.get(decision_value.request_event_id)
                    if (decision_value.kind is not CollaborationConfirmationDecision.REVISE
                            or requested is None or type(requested.payload) is not LeadRecommendationRequestPayload
                            or origin.step_id != requested.step_id
                            or (receipt.action is LeadTaskAction.RESUME and (
                                receipt.source_task_ref is None or requested.payload.authoring_scope is None
                                or (receipt.source_task_ref.task_id, receipt.source_task_ref.task_revision)
                                != (requested.payload.authoring_scope.task_id,
                                    requested.payload.authoring_scope.task_revision)))
                            or (receipt.action is LeadTaskAction.DISCUSS
                                and (receipt.source_task_ref is not None or requested.payload.authoring_scope is not None))):
                        raise ValueError("recommendation revision origin and current source are not exact")
                elif (type(origin.payload) is not CollaborationConfirmationPayload
                        or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
                        or receipt.action is not LeadTaskAction.RESUME):
                    raise ValueError("discussion revision requires exact structured USER Revise")
                decision = None
            if receipt.source_task_ref is not None:
                source_scope(receipt.source_task_ref)
            if receipt.origin_message_event_id is not None:
                message = prior.get(receipt.origin_message_event_id)
                if message is None or not correlated(message, event) or (
                    type(message.payload) is not CollaborationMessagePayload
                    or message.sender.kind is not CollaborationActorKind.USER
                ):
                    raise ValueError("task control requires its exact USER message")
                expected = decision.text if decision is not None else message.payload.content
                expected_text = (decision.text if decision is not None else
                                 origin.payload.decision.revision_request if recommendation_origin else message.payload.content)
                if message.payload.content != expected_text:
                    raise ValueError("task transition must preserve exact USER intent text")
            if receipt.action is LeadTaskAction.RESUME:
                ref = receipt.source_task_ref
                if ref is None or (receipt.target_task_id, receipt.target_task_revision) != (
                    ref.task_id, ref.task_revision + 1,
                ):
                    raise ValueError("resume requires an exact fresh task successor")
                resumptions[ref.task_id] = receipt
            elif receipt.action is LeadTaskAction.NEW_TASK:
                if receipt.target_task_revision != 1 or receipt.target_task_id in tasks:
                    raise ValueError("new task requires a new identity at revision one")
            selected_captures = tuple(captures.get(identity) for identity in receipt.material_capture_event_ids)
            if any(item is None or not correlated(item, event) for item in selected_captures):
                raise ValueError("task transition requires exact co-batched material captures")
            if decision is not None and tuple(item.payload.capture.source for item in selected_captures) != decision.material_refs:
                raise ValueError("task transition omitted or reordered USER materials")
            for item in selected_captures:
                if (item.payload.capture.target_task_id, item.payload.capture.target_task_revision) != (
                    receipt.target_task_id, receipt.target_task_revision,
                ):
                    raise ValueError("task capture target is detached from planning")
            ref = receipt.source_task_ref
            if ref is not None:
                if receipt.action is LeadTaskAction.HOLD:
                    statuses[ref.task_id] = "held"
                elif receipt.action is LeadTaskAction.DISCARD:
                    statuses[ref.task_id] = "discarded"
                elif receipt.action is LeadTaskAction.NEW_TASK and statuses.get(ref.task_id) == "active":
                    statuses[ref.task_id] = "held"
            discussion = receipt if receipt.action in {
                LeadTaskAction.DISCUSS, LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK,
            } else None
            transitions[event.event_id] = event
        elif type(payload) is CollaborationLeadPublicResponsePayload:
            response = payload.response
            submitted = submitted_calls.get((response.invocation_id, response.call_id))
            if submitted is None or (submitted.session_id, submitted.conversation_id,
                                     submitted.turn_id, submitted.step_id,
                                     submitted.payload.ordinal, submitted.payload.call_kind.value) != (
                event.session_id, event.conversation_id, event.turn_id, event.step_id,
                response.ordinal, response.call_kind,
            ):
                raise ValueError("public response requires exact actual submitted-call facts")
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.PUBLIC_RESPONSE,
                                  material_digest=response.material_digest)
            public.append(LeadPublicResponseView(ref, response))
            choices.append(LeadMaterialChoice(ref, "Public response (" + response.acceptance.value + ")",
                                              response.response_text[:240], response.response_text,
                                              response.acceptance.value, response.call_id))
        elif type(payload) is CollaborationLeadDiscussionPayload:
            report = payload.report
            origin = transitions.get(report.origin_control_event_id)
            if origin is None or origin.session_id != event.session_id or origin.conversation_id != event.conversation_id:
                raise ValueError("discussion requires exact durable USER control")
            receipt = origin.payload.receipt
            if (receipt.action is LeadTaskAction.DISCUSS
                    and (origin.turn_id, origin.step_id) != (event.turn_id, event.step_id)):
                raise ValueError("pure discussion result must close its exact current USER control")
            if (receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                    and receipt.planning_step_id != event.step_id):
                raise ValueError("clarification must preserve its exact planning lineage")
            if report.associated_task_ref != origin.payload.receipt.source_task_ref:
                raise ValueError("discussion changed its task association")
            calls = [item for item in prior.values() if type(item.payload) is CollaborationProviderCallPayload
                     and item.payload.state is CollaborationProviderCallState.SUBMITTED
                     and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)]
            if not calls:
                raise ValueError("discussion requires actual submitted provider work")
            discussion = origin.payload.receipt
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.DISCUSSION,
                                  material_digest=task_material_sha256(report.response_text))
            choices.append(LeadMaterialChoice(ref, "Discussion (provisional)", report.response_text[:240], report.response_text))
        elif type(payload) is CollaborationSpecialistReplyPayload:
            if payload.assessment is CollaborationSpecialistAssessment.DRAFTED:
                text = payload.message
                ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                      event_id=event.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior specialist draft (input only)", text[:240], text))
        elif (type(payload) is CollaborationExecutionResult
              and payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
              and payload.synthesis_result_event_id is not None):
            source = prior.get(payload.synthesis_result_event_id)
            source_value = None if source is None else source.payload
            if (type(source_value) is CollaborationLeadSynthesisPayload
                    and source_value.premise_report is None and source_value.authoring_scope is not None
                    and source_value.authoring_scope.intent is AuthoringIntent.AUTHORING
                    and source_value.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    and (payload.proposal_id, payload.proposal_revision, payload.proposal_digest,
                         payload.execution_attempt_id)
                    == (source_value.proposal_id, source_value.proposal_revision,
                        source_value.proposal_digest, source_value.execution_attempt_id)):
                text = source_value.message
                ref = LeadMaterialRef(session_id=source.session_id, conversation_id=source.conversation_id,
                                      event_id=source.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior integrated draft (input only)", text[:240], text))
        if type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
        }:
            review = next((item.payload for item in prior.values()
                           if type(item.payload) is ArtifactReviewedProposal
                           and (item.payload.proposal_id, item.payload.proposal_digest) == (
                               payload.reviewed_proposal_id, payload.reviewed_proposal_digest,
                           )), None)
            source = None if review is None else next((item.payload for item in prior.values()
                       if type(item.payload) is CollaborationProposal
                       and item.payload.proposal_id == review.consultation_proposal_id), None)
            if source is not None and source.authoring_scope is not None:
                statuses[source.authoring_scope.task_id] = "completed"
        prior[event.event_id] = event
    if (discussion is not None and discussion.target_task_id is not None
            and discussion.target_task_id in tasks
            and tasks[discussion.target_task_id][1].task_revision == discussion.target_task_revision):
        discussion = None
    protected = projection.current_state in {
        CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
        CollaborationStepState.RECOVERY_REQUIRED,
    }
    views = []
    for task_id, (event, scope) in tasks.items():
        status = statuses.get(task_id, "active")
        if protected and projection.current_proposal is not None and projection.current_proposal.authoring_scope is not None and (
            projection.current_proposal.authoring_scope.task_id == task_id
        ):
            status = "protected"
        ref = LeadTaskRef(session_id=event.session_id, conversation_id=event.conversation_id,
                          task_id=task_id, task_revision=scope.task_revision,
                          scope_event_id=event.event_id, scope_digest=_task_scope_digest(scope))
        task_steps = {item.step_id for item in events
                      if getattr(item.payload, "authoring_scope", None) is not None
                      and item.payload.authoring_scope.task_id == task_id}
        task_steps.update(item.payload.receipt.planning_step_id for item in events
                          if type(item.payload) is CollaborationLeadTaskTransitionPayload
                          and item.payload.receipt.target_task_id == task_id)
        submitted = tuple(dict.fromkeys(item.payload.call_id for item in events
                          if type(item.payload) is CollaborationProviderCallPayload
                          and item.payload.state is CollaborationProviderCallState.SUBMITTED
                          and item.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                          and item.step_id in task_steps))
        user_sources = tuple(item for item in events
                             if (item.turn_id, item.step_id) == (event.turn_id, event.step_id)
                             and type(item.payload) is CollaborationMessagePayload
                             and item.sender.kind is CollaborationActorKind.USER)
        goal = goals[task_id]
        pending_ids = ()
        if (projection.current_proposal is not None
                and projection.current_proposal.authoring_scope is not None
                and projection.current_proposal.authoring_scope.task_id == task_id
                and projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION,
                                                 CollaborationStepState.RECONFIRMATION_REQUIRED}
                and projection.confirmation_request_id is not None):
            pending_ids = (projection.confirmation_request_id,)
        views.append(LeadTaskView(ref, goal[:160], status, scope, goal,
                                  tuple(item.event_id for item in user_sources), pending_ids, submitted))
    discussion_steps = {(event.turn_id, event.step_id) for event in transitions.values()
                        if event.payload.receipt.action is LeadTaskAction.DISCUSS}
    protected_kinds = {
        CollaborationEventKindV2.ASSIGNMENT_DISPATCH, CollaborationEventKindV2.EXECUTION_RESULT,
        CollaborationEventKindV2.ARTIFACT_REVIEW_CANDIDATE,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_REQUEST,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_RESULT,
        CollaborationEventKindV2.STORY_COMMIT_REQUEST, CollaborationEventKindV2.STORY_COMMIT_RESULT,
    }
    for event in events:
        if (event.turn_id, event.step_id) not in discussion_steps:
            continue
        if event.kind in protected_kinds or type(event.payload) in {
            CollaborationProposal, CollaborationLeadJudgmentPayload,
        }:
            raise ValueError("pure discussion cannot publish execution or write authority")
        if type(event.payload) is CollaborationStepStatePayload and event.payload.state not in {
            CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED,
        }:
            raise ValueError("discussion state may record only failure or uncertainty bookkeeping")
    uncertain = False
    for event in events:
        call = event.payload
        if (type(call) is not CollaborationProviderCallPayload
                or call.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION):
            continue
        following = tuple(later for later in events
                          if (later.turn_id, later.step_id) == (event.turn_id, event.step_id)
                          and later.sequence > event.sequence)
        submitted = tuple(later.payload for later in following
                          if type(later.payload) is CollaborationProviderCallPayload
                          and (later.payload.invocation_id, later.payload.call_id)
                          == (call.invocation_id, call.call_id)
                          and later.payload.state is CollaborationProviderCallState.SUBMITTED)
        if submitted:
            call = submitted[-1]
        reports = tuple(later for later in following
                        if type(later.payload) is CollaborationLeadDiscussionPayload)
        states = tuple(later.payload.state for later in following
                       if type(later.payload) is CollaborationStepStatePayload)
        failures = tuple(later.payload for later in following
                         if type(later.payload) is CollaborationLeadOutcomeFailurePayload
                         and (later.payload.invocation_id, later.payload.call_id)
                         == (call.invocation_id, call.call_id))
        known_failure = bool(failures and call.state is CollaborationProviderCallState.SUBMITTED
                             and (not states or states[-1] in {
                                 CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
                             }))
        if not reports and not known_failure:
            uncertain = True
            break
    return replace(projection, available_tasks=tuple(views), selectable_materials=tuple(choices),
                   public_responses=tuple(public), discussion_context=discussion,
                   next_safe_action=(CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                     if uncertain else projection.next_safe_action),
                   last_sequence=events[-1].sequence if events else projection.last_sequence)


def _project_validated_collaboration_history(
    events: tuple[CollaborationEventV2, ...],
    *, _task_origins: tuple[CollaborationEventV2, ...] = (),
    _control_history: tuple[CollaborationEventV2, ...] = (),
) -> CollaborationHistoryProjection:
    task_kinds = {
        CollaborationEventKindV2.LEAD_TASK_DECISION,
        CollaborationEventKindV2.LEAD_TASK_TRANSITION,
        CollaborationEventKindV2.LEAD_MATERIAL_CAPTURE,
        CollaborationEventKindV2.LEAD_DISCUSSION,
        CollaborationEventKindV2.LEAD_PUBLIC_RESPONSE,
    }
    if any(event.kind in task_kinds for event in events):
        discussion_messages = {
            event.payload.receipt.origin_message_event_id for event in events
            if type(event.payload) is CollaborationLeadTaskTransitionPayload
            and event.payload.receipt.action is LeadTaskAction.DISCUSS
        }
        discussion_steps = {(event.turn_id, event.step_id) for event in events
                            if type(event.payload) is CollaborationLeadTaskTransitionPayload
                            and event.payload.receipt.action is LeadTaskAction.DISCUSS}
        discussion_bookkeeping = {
            CollaborationMessagePayload, CollaborationLeadJudgmentPayload,
            CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
            CollaborationStepStatePayload,
        }
        execution_events = tuple(event for event in events if (
            event.kind not in task_kinds
            and event.event_id not in discussion_messages
            and not ((event.turn_id, event.step_id) in discussion_steps
                     and type(event.payload) in discussion_bookkeeping)
            and not (type(event.payload) is CollaborationProviderCallPayload
                     and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION)
        ))
        origins = tuple(event for event in events if type(event.payload) is CollaborationLeadTaskDecisionPayload)
        base = (
            _project_validated_collaboration_history(
            execution_events, _task_origins=origins,
            _control_history=_control_history or events,
        )
            if execution_events else project_collaboration_history(())
        )
        base = replace(base, session_id=events[0].session_id,
                       conversation_id=events[0].conversation_id)
        return _project_task_context(events, base)
    _validate_editorial_event_headers(events)
    workflow_events = tuple(event for event in events if type(event.payload) is not CollaborationEditorialRecordPayload)
    if len(workflow_events) != len(events):
        # USER memory is retained at the real tail, but cannot select/reset a
        # workflow step, budget, confirmation, objective, Apply or Commit.
        projection = (_project_validated_collaboration_history(
            workflow_events, _task_origins=_task_origins, _control_history=_control_history,
        ) if workflow_events
                      else project_collaboration_history(()))
        return replace(projection, session_id=events[0].session_id, conversation_id=events[0].conversation_id,
                       last_sequence=events[-1].sequence)
    _validate_exploration_history(events)
    premise_state = _validate_premise_history(events)
    artifact_candidates, artifact_states, artifact_reviewed = (
        _project_artifact_review_history(events)
    )
    application_request, application_result, commit_request, commit_result = (
        _project_artifact_operation_history(events, _control_history=_control_history)
    )
    recommendation_request, recommendation_decision, recommendation_start, recommendation_result, recommendation_state = (
        _project_lead_recommendation_history(events, _task_origins)
    )
    proposals: dict[str, CollaborationProposal] = {}
    states: dict[str, CollaborationStepState] = {}
    request_ids: set[str] = set()
    request_events: dict[str, CollaborationEventV2] = {}
    decisions: dict[str, CollaborationConfirmationDecision] = {}
    attempt_ids: set[str] = set()
    attempts: dict[str, str] = {}
    dispatches: dict[str, CollaborationEventV2] = {}
    replies: dict[str, CollaborationEventV2] = {}
    results: dict[str, tuple[str, CollaborationExecutionResult]] = {}
    result_events: dict[str, CollaborationEventV2] = {}
    running_attempts: dict[str, int] = {}
    running_sequences: dict[str, int] = {}
    spent_calls: dict[str, int] = {}
    prepared_calls: dict[str, tuple[str, CollaborationProviderCallPayload]] = {}
    submitted_call_ids: set[str] = set()
    invocation_ordinals: dict[str, int] = {}
    invocation_facts: dict[str, tuple[str, str, str, CollaborationInvocationKind]] = {}
    provider_call_events: list[
        tuple[CollaborationEventV2, CollaborationProviderCallPayload]
    ] = []
    completed_lead_invocations: set[str] = set()
    completed_lead_turns: set[str] = set()
    blocked_lineages: set[str] = set()
    event_by_id: dict[str, CollaborationEventV2] = {}
    current_step_id: str | None = None

    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        prior_tail = events[offset - 1] if offset else None
        batch_proposals: list[CollaborationProposal] = []
        batch_inherited_reviews: list[CollaborationProposal] = []
        batch_states: list[
            tuple[CollaborationEventV2, CollaborationStepStatePayload]
        ] = []
        batch_confirmations: list[
            tuple[CollaborationEventV2, CollaborationConfirmationPayload]
        ] = []
        batch_dispatches: list[
            tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
        ] = []
        batch_replies: list[
            tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]
        ] = []
        batch_results: list[
            tuple[CollaborationEventV2, CollaborationExecutionResult]
        ] = []
        batch_interruptions: list[CollaborationEventV2] = []
        batch_judgments: list[
            tuple[CollaborationEventV2, CollaborationLeadJudgmentPayload]
        ] = []
        batch_lead_failures: list[
            tuple[CollaborationEventV2, CollaborationLeadOutcomeFailurePayload]
        ] = []

        for event in batch:
            event_by_id[event.event_id] = event
            current_step_id = event.step_id
            payload = event.payload
            proposal = proposals.get(event.step_id)

            if type(payload) is CollaborationProposal:
                validate_authoring_capture_pin(payload, tuple(prior for prior in events if prior.sequence < event.sequence))

                scope = payload.authoring_scope
                if scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                } and running_attempts.get(event.step_id, 0):
                    raise ValueError("scope3 round two is reserved for final review")
                if scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                } and any(item.step_id != event.step_id for item in confirmed_authoring_proposals(
                    scope, tuple(prior for prior in events if prior.sequence < event.sequence)
                )):
                    raise ValueError("scope3 authoring revision cannot restart its confirmed lineage")
                if scope is not None and scope.schema_version == 3 and scope.activity is AuthoringActivity.REVIEW:
                    past = tuple(prior for prior in events if prior.sequence < event.sequence)
                    drafts = tuple(item for item in confirmed_authoring_proposals(scope, past)
                                   if item.authoring_scope is not None and item.authoring_scope.schema_version == 3
                                   and item.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE})
                    if drafts:
                        request = next((prior.payload for prior in reversed(past)
                                        if prior.step_id == event.step_id and type(prior.payload) is LeadRecommendationRequestPayload), None)
                        source = None if request is None else next((item for item in drafts if (
                            item.proposal_id, item.proposal_revision, item.proposal_digest
                        ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
                        if source is None or request is None or request.authoring_scope is None or (
                            running_attempts.get(event.step_id, 0) != 1
                            or spent_calls.get(event.step_id, 0) < request.physical_calls_spent
                            or authoring_capture_identity(scope, payload.basis) != authoring_capture_identity(
                                source.authoring_scope, source.basis
                            )
                        ):
                            raise ValueError("draft-derived scope3 final review requires exact inherited recommendation handoff")
                        batch_inherited_reviews.append(payload)
                if event.step_id in blocked_lineages:
                    raise ValueError(
                        "call-bearing work cannot follow a blocked lineage"
                    )
                previous = proposals.get(event.step_id)
                if previous is not None:
                    if payload.proposal_revision <= previous.proposal_revision:
                        raise ValueError(
                            "proposal revision must increase within a lineage"
                        )
                    if states.get(event.step_id) not in (
                        CollaborationStepState.COMPLETED,
                        CollaborationStepState.REJECTED,
                        CollaborationStepState.SUPERSEDED,
                    ):
                        raise ValueError(
                            "a new proposal cannot replace an active proposal"
                        )
                if payload.budget.rounds_spent != running_attempts.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal rounds-spent does not match durable history"
                    )
                if payload.budget.physical_calls_spent != spent_calls.get(
                    event.step_id, 0
                ):
                    raise ValueError(
                        "proposal physical-calls-spent does not match results"
                    )
                proposals[event.step_id] = payload
                proposal = payload
                batch_proposals.append(payload)
                continue

            if type(payload) is CollaborationMessagePayload:
                continue

            if type(payload) is LeadRecommendationRequestPayload:
                source = event_by_id[payload.source_recommendation_event_id]
                fresh_consultation = _is_fresh_drafting_preparation(
                    payload, source, tuple(event_by_id.values()),
                )
                source_spent = spent_calls.get(source.step_id, 0)
                inherited_rounds = None
                scope = payload.authoring_scope
                if not fresh_consultation and (scope is not None and scope.schema_version == 3 and scope.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                }):
                    source_proposal = next((item for item in proposals.values() if (
                        item.proposal_id, item.proposal_revision, item.proposal_digest
                    ) == (payload.source_proposal_id, payload.source_proposal_revision, payload.source_proposal_digest)), None)
                    if (
                        source_proposal is None or source_proposal.authoring_scope is None
                        or source_proposal.authoring_scope.schema_version != 3
                        or source_proposal.authoring_scope.intent is not AuthoringIntent.AUTHORING
                        or source_proposal.authoring_scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    ):
                        raise ValueError("v3 preparation requires the exact confirmed source proposal")
                    synthesis = next((item for item in event_by_id.values()
                                      if type(item.payload) is CollaborationLeadSynthesisPayload
                                      and item.step_id == source_proposal.step_id
                                      and item.payload.execution_attempt_id == payload.source_execution_attempt_id
                                      and item.payload.proposal_digest == source_proposal.proposal_digest), None)
                    successful = any(type(prior.payload) is CollaborationExecutionResult
                                     and prior.payload.execution_attempt_id == payload.source_execution_attempt_id
                                     and prior.payload.proposal_digest == source_proposal.proposal_digest
                                     and prior.payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
                                     for prior in event_by_id.values())
                    if synthesis is None or not successful or states.get(source_proposal.step_id) is not CollaborationStepState.COMPLETED:
                        raise ValueError("v3 preparation requires completed source synthesis")
                    if authoring_capture_identity(scope, payload.basis) != authoring_capture_identity(
                        source_proposal.authoring_scope, source_proposal.basis
                    ):
                        raise ValueError("v3 preparation must inherit the exact confirmed source capture")
                    related_steps = {source.step_id, *(prior.step_id for prior in event_by_id.values()
                                     if type(prior.payload) is LeadRecommendationRequestPayload
                                     and prior.payload.source_proposal_id == source_proposal.proposal_id
                                     and prior.payload.source_proposal_revision == source_proposal.proposal_revision
                                     and prior.payload.source_proposal_digest == source_proposal.proposal_digest)}
                    related_steps.discard(source_proposal.step_id)
                    inherited_rounds = running_attempts.get(source_proposal.step_id, 0) + sum(
                        1 for prior in event_by_id.values() if prior.step_id in related_steps
                        and type(prior.payload) is CollaborationStepStatePayload
                        and prior.payload.state is CollaborationStepState.RUNNING
                    )
                    if inherited_rounds != 1:
                        raise ValueError("v3 preparation cannot reset or add a third round")
                    source_spent = spent_calls.get(source_proposal.step_id, 0) + sum(
                        1 for call_event, call in provider_call_events
                        if call_event.step_id in related_steps and call.state is CollaborationProviderCallState.SUBMITTED
                    )
                if payload.physical_calls_spent != source_spent:
                    raise ValueError("recommendation cannot reset source-lineage call accounting")
                if fresh_consultation:
                    if running_attempts.get(event.step_id, 0) != 0:
                        raise ValueError("fresh drafting preparation requires its own round-zero call accounting")
                    read = any(item.state is CollaborationInputState.PRESENT and item.kind in {
                        CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                        CollaborationInputKind.WRITING_ARTIFACT,
                    } for item in payload.basis.inputs)
                    floor = (
                    len(payload.specialist_ids) + len(required_authoring_specialists(scope)) + 4
                ) * (1 + int(read))
                    if (source_spent + payload.max_preparation_physical_calls + floor
                            > COLLABORATION_PHYSICAL_CALL_LIMIT):
                        raise ValueError("fresh preparation must reserve all current drafting and final-review calls")
                if event.step_id != source.step_id:
                    if event.step_id in spent_calls or event.step_id in proposals:
                        raise ValueError("recommendation preparation step must be fresh")
                spent_calls[event.step_id] = source_spent
                if inherited_rounds is not None:
                    if running_attempts.get(event.step_id, 0) not in {0, inherited_rounds} or event.step_id in proposals:
                        raise ValueError("preparation cannot replace an already running round")
                    running_attempts[event.step_id] = inherited_rounds
                continue

            if type(payload) is CollaborationLeadExplorationResultPayload:
                matching = tuple(call for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id and call_event.turn_id == event.turn_id
                    and call.invocation_kind is CollaborationInvocationKind.LEAD_EXPLORATION
                    and call.state is CollaborationProviderCallState.SUBMITTED
                    and call.call_id == payload.report.provider_call_id)
                if len(matching) != 1 or matching[0].invocation_id in completed_lead_invocations:
                    raise ValueError("exploration report requires one actual same-turn submitted call")
                completed_lead_invocations.add(matching[0].invocation_id)
                completed_lead_turns.add(event.turn_id)
                continue
            if type(payload) is CollaborationLeadJudgmentPayload:
                matching_calls = tuple(
                    (call_event, call)
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.turn_id == event.turn_id
                    and call.invocation_kind is CollaborationInvocationKind.LEAD
                    and call.state is CollaborationProviderCallState.SUBMITTED
                )
                if not matching_calls:
                    raise ValueError(
                        "lead judgment requires a submitted same-turn provider call"
                    )
                _call_event, matching_call = matching_calls[-1]
                invocation_id = matching_call.invocation_id
                if invocation_id in completed_lead_invocations:
                    raise ValueError("lead invocation may close only once")
                expected_operation_id = f"lead_{event.turn_id}"
                expected_result_correlation_id = f"outcome_{event.turn_id}"
                if any(
                    receipt.operation_id != expected_operation_id
                    or receipt.result_correlation_id != expected_result_correlation_id
                    for receipt in payload.evidence.receipts
                ):
                    raise ValueError(
                        "lead judgment evidence does not match its durable turn"
                    )
                completed_lead_invocations.add(invocation_id)
                completed_lead_turns.add(event.turn_id)
                batch_judgments.append((event, payload))
                continue

            if type(payload) is CollaborationProviderCallPayload:
                if event.step_id in blocked_lineages:
                    raise ValueError("provider calls cannot continue a blocked lineage")
                if (
                    payload.invocation_id in completed_lead_invocations
                    or (payload.invocation_kind is CollaborationInvocationKind.LEAD
                        and event.turn_id in completed_lead_turns)
                ):
                    raise ValueError(
                        "provider calls cannot continue a closed lead invocation"
                    )
                prepared = prepared_calls.get(payload.call_id)
                if payload.state is CollaborationProviderCallState.PREPARED:
                    if prepared is not None or payload.call_id in submitted_call_ids:
                        raise ValueError("provider call ID must be globally fresh")
                    facts = (
                        event.step_id,
                        event.turn_id,
                        payload.role,
                        payload.invocation_kind,
                    )
                    stable_facts = invocation_facts.setdefault(
                        payload.invocation_id,
                        facts,
                    )
                    if stable_facts != facts:
                        raise ValueError(
                            "provider invocation identity facts must remain stable"
                        )
                    expected_ordinal = (
                        invocation_ordinals.get(payload.invocation_id, 0) + 1
                    )
                    if payload.ordinal != expected_ordinal:
                        raise ValueError(
                            "provider call ordinals must be contiguous per invocation"
                        )
                    invocation_ordinals[payload.invocation_id] = payload.ordinal
                    prepared_calls[payload.call_id] = (event.step_id, payload)
                else:
                    if prepared is None or payload.call_id in submitted_call_ids:
                        raise ValueError(
                            "submitted provider call requires one unmatched preparation"
                        )
                    prepared_step_id, prepared_payload = prepared
                    if prepared_step_id != event.step_id or prepared_payload != replace(
                        payload,
                        state=CollaborationProviderCallState.PREPARED,
                        validation_details=(),
                    ):
                        raise ValueError(
                            "submitted provider call drifts from its preparation"
                        )
                    admission = next((prior.payload.admission for prior in event_by_id.values()
                        if type(prior.payload) is CollaborationPremiseFreshWorkAdmissionPayload
                        and prior.payload.admission.provider_invocation_id == payload.invocation_id), None)
                    if admission is not None:
                        actual_front = sum(call.state is CollaborationProviderCallState.SUBMITTED
                            and call.invocation_id == payload.invocation_id for _, call in provider_call_events)
                        if actual_front + 1 > admission.front_physical_call_limit:
                            raise ValueError("fresh-work front exceeds its immutable admission cap")
                    submitted_call_ids.add(payload.call_id)
                    spent_calls[event.step_id] = spent_calls.get(event.step_id, 0) + 1
                    if spent_calls[event.step_id] > COLLABORATION_PHYSICAL_CALL_LIMIT:
                        raise ValueError(
                            "provider call lineage exceeds the physical-call limit"
                        )
                provider_call_events.append((event, payload))
                continue

            if type(payload) is CollaborationLeadOutcomeFailurePayload:
                rejection = payload.code in {
                    CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                    CollaborationLeadOutcomeFailureCode.ARTIFACT_INTENT_REJECTED,
                    CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                }
                invocation_calls = tuple(
                    call
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.turn_id == event.turn_id
                    and call.invocation_id == payload.invocation_id
                    and call.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}
                    and (not rejection or call.state is CollaborationProviderCallState.SUBMITTED)
                )
                if event.turn_id in completed_lead_turns:
                    raise ValueError("lead turn may close only once")
                if not rejection and not _lead_abandonment_is_safe(
                    tuple(event_by_id.values()), event.turn_id, event.step_id,
                ):
                    raise ValueError("only an isolated read-only Lead attempt may be abandoned")
                if payload.code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED:
                    requests = tuple(
                        item for item in event_by_id.values()
                        if item.turn_id == event.turn_id
                        and type(item.payload) is CollaborationMessagePayload
                        and item.sender.kind.value == "user"
                        and item.recipient.kind.value == "lead"
                    )
                    origin = _recommendation_origin(tuple(event_by_id.values()), event.turn_id, event.step_id)
                    if ((len(requests) != 1 if origin is None else bool(requests)) or any(
                        item.turn_id == event.turn_id
                        for item, _call in provider_call_events
                    )):
                        raise ValueError("unsubmitted closure requires one exact unstarted request")
                elif (
                    not invocation_calls
                    or invocation_calls[-1].call_id != payload.call_id
                    or payload.invocation_id in completed_lead_invocations
                    or (
                        payload.code is CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT
                        and invocation_calls[-1].state is not CollaborationProviderCallState.SUBMITTED
                    )
                ):
                    raise ValueError("lead outcome failure does not close the exact call")
                if payload.invocation_id is not None:
                    completed_lead_invocations.add(payload.invocation_id)
                completed_lead_turns.add(event.turn_id)
                batch_lead_failures.append((event, payload))
                continue

            if type(payload) in {
                ArtifactObjectivePayload,
                ArtifactObjectivePayloadV2,
                LeadRecommendationDecisionPayload,
                LeadRecommendationStartedPayload,
                LeadRecommendationResultPayload,
                ArtifactReviewCandidatePayload,
                ArtifactReviewSynthesisPayload,
                ArtifactReviewedProposal,
                ArtifactProposalStatePayload,
                ArtifactReviewFailurePayload,
                ArtifactApplicationRequestPayload,
                ArtifactApplicationDecisionPayload,
                ArtifactApplicationResultPayload,
                StoryCommitRequestPayload,
                StoryCommitDecisionPayload,
                StoryCommitResultPayload,
            }:
                continue

            if type(payload) in {CollaborationLeadExplorationRequestPayload, CollaborationExplorationAdoptionPayload, CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload, CollaborationPremiseFreshWorkAdmissionPayload}:
                continue
            if proposal is None:
                raise ValueError(
                    "collaboration work appears before proposal disclosure"
                )
            key = _payload_proposal_key(payload)
            if key is not None and key != _proposal_key(proposal):
                raise ValueError(
                    "collaboration payload drifts from the active proposal"
                )

            if type(payload) is CollaborationStepStatePayload:
                previous_state = states.get(event.step_id)
                validate_collaboration_transition(previous_state, payload.state)
                if payload.state is CollaborationStepState.AWAITING_CONFIRMATION:
                    if not batch_proposals:
                        raise ValueError(
                            "awaiting confirmation must co-batch proposal disclosure"
                        )
                if payload.state in (
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                ):
                    assert payload.confirmation_request_id is not None
                    if payload.confirmation_request_id in request_ids:
                        raise ValueError(
                            "confirmation request ID must be globally fresh"
                        )
                    request_ids.add(payload.confirmation_request_id)
                    request_events[event.step_id] = event
                if payload.state is CollaborationStepState.RUNNING:
                    assert payload.execution_attempt_id is not None
                    if payload.execution_attempt_id in attempt_ids:
                        raise ValueError("execution attempt ID must be globally fresh")
                    attempt_ids.add(payload.execution_attempt_id)
                    attempts[event.step_id] = payload.execution_attempt_id
                    running_attempts[event.step_id] = (
                        running_attempts.get(event.step_id, 0) + 1
                    )
                    running_sequences[event.step_id] = event.sequence
                    results.pop(event.step_id, None)
                if payload.state is CollaborationStepState.OUTCOME_UNKNOWN:
                    if payload.execution_attempt_id != attempts.get(event.step_id):
                        raise ValueError(
                            "outcome-unknown attempt does not match running"
                        )
                    if event.step_id in results:
                        raise ValueError(
                            "known execution result cannot become outcome unknown"
                        )
                    blocked_lineages.add(event.step_id)
                if payload.state in (
                    CollaborationStepState.COMPLETED,
                    CollaborationStepState.FAILED,
                    CollaborationStepState.INTERRUPTED,
                ):
                    assert payload.execution_result_event_id is not None
                    result_record = result_events.get(payload.execution_result_event_id)
                    if result_record is None:
                        raise ValueError(
                            "terminal state references an unknown execution result"
                        )
                    terminal_result = result_record.payload
                    assert isinstance(terminal_result, CollaborationExecutionResult)
                    if terminal_result.execution_attempt_id != attempts.get(
                        event.step_id
                    ):
                        raise ValueError("terminal state result attempt does not match")
                    expected_state = {
                        CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                        CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                        CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
                    }[terminal_result.overall_outcome]
                    if payload.state is not expected_state:
                        raise ValueError(
                            "terminal state disagrees with execution result"
                        )
                    if payload.state in (
                        CollaborationStepState.FAILED,
                        CollaborationStepState.INTERRUPTED,
                    ):
                        blocked_lineages.add(event.step_id)
                states[event.step_id] = payload.state
                batch_states.append((event, payload))
                continue

            if type(payload) is CollaborationConfirmationPayload:
                request_event = request_events.get(event.step_id)
                if request_event is None:
                    raise ValueError("confirmation has no active request")
                request_payload = request_event.payload
                assert isinstance(request_payload, CollaborationStepStatePayload)
                if (
                    payload.confirmation_request_id
                    != request_payload.confirmation_request_id
                    or payload.confirmation_request_event_id != request_event.event_id
                ):
                    raise ValueError("confirmation does not match the active request")
                if payload.confirmation_request_id in decisions:
                    raise ValueError(
                        "confirmation request accepts at most one decision"
                    )
                if (
                    prior_tail is None
                    or prior_tail.event_id != request_event.event_id
                    or (
                        event.sequence != request_event.sequence + 1
                        and not _closed_pure_discussion_control_gap_is_safe(
                            request_event, event,
                            tuple(item for item in (_control_history or events) if item.sequence < event.sequence),
                        )
                    )
                ):
                    raise ValueError(
                        "confirmation request is not the authoritative tail"
                    )
                if event.batch_ordinal != 0:
                    raise ValueError(
                        "confirmation decision must begin its atomic batch"
                    )
                if payload.decision is CollaborationConfirmationDecision.CONFIRM:
                    if proposal is None:
                        raise ValueError("confirmation must bind an existing proposal")
                    validate_authoring_capture_pin(proposal, tuple(prior for prior in events if prior.sequence < event.sequence))
                decisions[payload.confirmation_request_id] = payload.decision
                batch_confirmations.append((event, payload))
                continue

            if type(payload) is CollaborationAssignmentDispatchPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "assignment dispatch requires durable running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError(
                        "assignment dispatch attempt does not match running"
                    )
                expected = {
                    assignment.assignment_id: assignment
                    for assignment in proposal.assignments
                }.get(payload.assignment.assignment_id)
                if expected is None or payload.assignment != expected:
                    raise ValueError(
                        "dispatch must equal the exact proposal assignment"
                    )
                if payload.assignment.assignment_id in dispatches:
                    raise ValueError("assignment may be dispatched only once")
                dispatches[payload.assignment.assignment_id] = event
                batch_dispatches.append((event, payload))
                continue

            if type(payload) is CollaborationSpecialistReplyPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "specialist reply requires the exact active running state"
                    )
                dispatch = dispatches.get(payload.assignment_id)
                if dispatch is None or dispatch.event_id != payload.dispatch_event_id:
                    raise ValueError("specialist reply does not match its dispatch")
                dispatch_payload = dispatch.payload
                assert isinstance(
                    dispatch_payload, CollaborationAssignmentDispatchPayload
                )
                if (
                    payload.execution_attempt_id
                    != dispatch_payload.execution_attempt_id
                ):
                    raise ValueError("specialist reply attempt does not match dispatch")
                if event.sender.agent_id != dispatch_payload.assignment.specialist_id:
                    raise ValueError(
                        "specialist reply sender does not match assignment"
                    )
                if (
                    event.recipient.kind.value == "user"
                    and proposal.step_kind.value != "specialist_advisory"
                ):
                    raise ValueError(
                        "specialist-to-user reply is reserved for advisory"
                    )
                if payload.assignment_id in replies:
                    raise ValueError("assignment may have at most one visible reply")
                if payload.input_ids != dispatch_payload.assignment.input_ids:
                    raise ValueError(
                        "specialist reply input IDs must match its assignment exactly"
                    )
                if payload.mode is not dispatch_payload.assignment.mode:
                    raise ValueError("specialist reply mode must match its exact assignment")
                replies[payload.assignment_id] = event
                batch_replies.append((event, payload))
                continue

            if type(payload) is CollaborationLeadSynthesisPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError("lead synthesis requires the active running state")
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("lead synthesis attempt does not match running")
                if payload.evidence != proposal.basis.evidence:
                    raise ValueError("lead synthesis evidence drifts from its proposal")
                if (payload.premise_report is None) != (proposal.premise_decision is None):
                    raise ValueError("analysis synthesis requires its typed report only")
                if (
                    payload.premise_report is not None
                    and payload.premise_report.spec != proposal.premise_decision
                ):
                    raise ValueError("premise report specification drifts from its proposal")
                if payload.authoring_scope != proposal.authoring_scope:
                    raise ValueError("lead synthesis authoring scope drifts from its proposal")
                expected_reply_ids = tuple(
                    replies[assignment.assignment_id].event_id
                    for assignment in proposal.assignments
                    if assignment.assignment_id in replies
                )
                if payload.reply_event_ids != expected_reply_ids or len(
                    expected_reply_ids
                ) != len(proposal.assignments):
                    raise ValueError(
                        "lead synthesis must attribute every specialist reply exactly"
                    )
                ordered_replies = tuple(
                    replies[assignment.assignment_id].payload
                    for assignment in proposal.assignments
                )
                if any(
                    type(reply) is not CollaborationSpecialistReplyPayload
                    for reply in ordered_replies
                ):
                    raise ValueError("lead synthesis reply attribution is not typed")
                required_unresolved: list[str] = []
                seen_required: set[str] = set()
                for reply in ordered_replies:
                    assert isinstance(reply, CollaborationSpecialistReplyPayload)
                    if reply.assessment in {
                        CollaborationSpecialistAssessment.SUPPORTS, CollaborationSpecialistAssessment.DRAFTED,
                        CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED,
                        CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED,
                    }:
                        continue
                    for input_id in reply.input_ids:
                        if input_id not in seen_required:
                            seen_required.add(input_id)
                            required_unresolved.append(input_id)
                known_input_order = tuple(
                    item.input_id for item in proposal.basis.inputs
                )
                known_input_ids = set(known_input_order)
                unresolved_order = (
                    *required_unresolved,
                    *(
                        input_id
                        for input_id in known_input_order
                        if input_id not in seen_required
                    ),
                )
                if (
                    not seen_required.issubset(payload.unresolved_input_ids)
                    or payload.unresolved_input_ids
                    != tuple(
                        input_id
                        for input_id in unresolved_order
                        if input_id in payload.unresolved_input_ids
                    )
                    or len(set(payload.unresolved_input_ids))
                    != len(payload.unresolved_input_ids)
                ):
                    raise ValueError(
                        "lead synthesis unresolved inputs violate required canonical order"
                    )
                if payload.next_intent is not None:
                    prior_scope = proposal.authoring_scope
                    next_scope = payload.next_intent.authoring_scope
                    if prior_scope is not None and (
                        next_scope is None
                        or next_scope.intent is not prior_scope.intent
                        or not set(prior_scope.target_paths).issubset(next_scope.target_paths)
                        or not set(prior_scope.domains).issubset(next_scope.domains)
                    ):
                        raise ValueError("continuation cannot omit current authoring targets or domains")
                    if proposal.budget.rounds_spent + 1 >= proposal.budget.round_limit:
                        raise ValueError(
                            "round two synthesis cannot request round three"
                        )
                    expected_next_inputs = known_input_ids | set(
                        payload.reply_event_ids
                    )
                    actual_next_inputs = {
                        input_id
                        for assignment in payload.next_intent.assignments
                        for input_id in assignment.input_ids
                    }
                    if actual_next_inputs != expected_next_inputs:
                        raise ValueError(
                            "next-round intent must close over original and reply inputs"
                        )
                continue

            if type(payload) is CollaborationExecutionResult:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "execution result requires the exact active running state"
                    )
                _validate_collaboration_execution_result(
                    event=event,
                    result=payload,
                    proposal=proposal,
                    attempt_id=attempts.get(event.step_id),
                    dispatches=dispatches,
                    replies=replies,
                    event_by_id=event_by_id,
                    batch=batch,
                )
                running_sequence = running_sequences.get(event.step_id)
                assert running_sequence is not None
                submitted_since_running = tuple(
                    call
                    for call_event, call in provider_call_events
                    if call_event.step_id == event.step_id
                    and call_event.sequence > running_sequence
                    and call_event.sequence < event.sequence
                    and call.state is CollaborationProviderCallState.SUBMITTED
                )
                for assignment, outcome in zip(
                    proposal.assignments,
                    payload.assignment_outcomes,
                    strict=True,
                ):
                    actual_calls = sum(
                        call.invocation_kind
                        in {
                            CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
                            CollaborationInvocationKind.SPECIALIST_ADVISORY,
                        }
                        and call.role == assignment.specialist_id
                        for call in submitted_since_running
                    )
                    if actual_calls != outcome.submitted_physical_calls:
                        raise ValueError(
                            "assignment result call total disagrees with durable "
                            "submitted calls"
                        )
                synthesis_calls = sum(
                    call.invocation_kind is CollaborationInvocationKind.LEAD_SYNTHESIS
                    for call in submitted_since_running
                )
                if synthesis_calls != payload.synthesis_submitted_physical_calls:
                    raise ValueError(
                        "synthesis result call total disagrees with durable "
                        "submitted calls"
                    )
                if (
                    event.step_id in results
                    and results[event.step_id][1].execution_attempt_id
                    == payload.execution_attempt_id
                ):
                    raise ValueError("execution attempt may have at most one result")
                results[event.step_id] = (event.event_id, payload)
                result_events[event.event_id] = event
                batch_results.append((event, payload))
                continue

            if type(payload) is CollaborationInterruptionPayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "interruption requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("interruption attempt does not match running")
                batch_interruptions.append(event)
                continue

            if type(payload) is CollaborationTerminalOutcomePayload:
                if states.get(event.step_id) is not CollaborationStepState.RUNNING:
                    raise ValueError(
                        "terminal outcome requires the exact active running state"
                    )
                if payload.execution_attempt_id != attempts.get(event.step_id):
                    raise ValueError("terminal outcome attempt does not match running")
                if payload.scope is CollaborationTerminalScope.ASSIGNMENT:
                    assert payload.assignment_id is not None
                    terminal_assignment = {
                        item.assignment_id: item for item in proposal.assignments
                    }.get(payload.assignment_id)
                    if terminal_assignment is None:
                        raise ValueError("terminal outcome assignment is not proposed")
                    if (
                        payload.submitted_physical_calls
                        > terminal_assignment.max_physical_calls
                    ):
                        raise ValueError(
                            "assignment exceeded its physical-call allocation"
                        )
                elif (
                    payload.submitted_physical_calls
                    > proposal.budget.synthesis_max_physical_calls
                ):
                    raise ValueError("synthesis exceeded its physical-call allocation")
                continue

            raise ValueError("unsupported collaboration payload in history")

        _validate_collaboration_co_batch(
            batch=batch,
            prior_tail=prior_tail,
            proposals=batch_proposals,
            states=batch_states,
            confirmations=batch_confirmations,
            dispatches=batch_dispatches,
            replies=batch_replies,
            results=batch_results,
            interruptions=batch_interruptions,
            active_proposals=proposals,
            task_control_history=_control_history or events,
        )
        if batch_lead_failures and (
            len(batch_lead_failures) != 1
            or batch_judgments
            or batch_proposals
            or any(
                type(event.payload) is CollaborationMessagePayload
                and event.sender.kind.value == "lead"
                for event in batch
            )
        ):
            raise ValueError(
                "lead outcome failure cannot coexist with a successful outcome"
            )
        lead_proposals = tuple(
            item
            for item in batch_proposals
            if item.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and (
                item.budget.rounds_spent == 0
                or item in batch_inherited_reviews
            )
        )
        if lead_proposals:
            if (
                len(lead_proposals) != 1
                or len(batch_judgments) != 1
                or batch_judgments[0][1].kind is not LeadOutcomeKind.PROPOSAL
                or lead_proposals[0].basis.evidence != batch_judgments[0][1].evidence
                or (lead_proposals[0].premise_decision is not None)
                != (
                    batch_judgments[0][1].evidence.scope
                    is LeadOutcomeScope.PREMISE_ANALYSIS
                )
                or lead_proposals[0].authoring_scope != batch_judgments[0][1].authoring_scope
            ):
                raise ValueError(
                    "lead proposal must co-batch its exact evidence judgment"
                )
        elif any(
            payload.kind is LeadOutcomeKind.PROPOSAL
            for _event, payload in batch_judgments
        ):
            raise ValueError("proposal judgment requires one co-batched proposal")
        offset += len(batch)

    assert current_step_id is not None
    current_proposal = proposals.get(current_step_id)
    state = states.get(current_step_id)
    current_turn_id = events[-1].turn_id
    current_lead_outcome_failed = any(
        event.step_id == current_step_id
        and event.turn_id == current_turn_id
        and type(event.payload) is CollaborationLeadOutcomeFailurePayload
        for event in events
    )
    unmatched_submitted_lead = any(
        call_event.step_id == current_step_id
        and call_event.turn_id == current_turn_id
        and call.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION}
        and call.state is CollaborationProviderCallState.SUBMITTED
        and call.invocation_id not in completed_lead_invocations
        for call_event, call in provider_call_events
    )
    pending_lead_request = (
        current_turn_id not in completed_lead_turns
        and any(
            event.turn_id == current_turn_id
            and (
                (type(event.payload) is CollaborationMessagePayload
                 and event.sender.kind.value == "user"
                 and event.recipient.kind.value == "lead")
                or (type(event.payload) is CollaborationProviderCallPayload
                    and event.payload.invocation_kind in {CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_EXPLORATION})
                or (type(event.payload) is LeadRecommendationStartedPayload
                    and _recommendation_origin(events, event.turn_id, event.step_id) is not None)
            )
            for event in events
        )
    )
    recoverable_lead = (
        (state is None or state is CollaborationStepState.SUPERSEDED)
        and (pending_lead_request or current_lead_outcome_failed)
        and _lead_abandonment_is_safe(events, current_turn_id, current_step_id)
    )
    if current_proposal is None and state is None and unmatched_submitted_lead and not recoverable_lead:
        state = CollaborationStepState.OUTCOME_UNKNOWN
    request_event = request_events.get(current_step_id)
    request_id: str | None = None
    decision: CollaborationConfirmationDecision | None = None
    if request_event is not None:
        request_payload = request_event.payload
        assert isinstance(request_payload, CollaborationStepStatePayload)
        request_id = request_payload.confirmation_request_id
        if request_id is not None:
            decision = decisions.get(request_id)
    result_pair = results.get(current_step_id)
    result_event_id = result_pair[0] if result_pair is not None else None
    projection_result: CollaborationExecutionResult | None = (
        result_pair[1] if result_pair is not None else None
    )
    artifact_objectives, offered_draft_sources = _project_artifact_objective_history(events)
    operation_next_action = _artifact_operation_next_action(
        events[-1], application_request, application_result, commit_request, commit_result,
    )
    recommendation_next_action = None
    if recommendation_state is LeadRecommendationState.PENDING:
        recommendation_next_action = CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION
    elif recommendation_state is LeadRecommendationState.RUNNING:
        assert recommendation_start is not None
        start_event = event_by_id[recommendation_start.start_event_id]
        origin_calls = tuple(
            call for event, call in provider_call_events
            if event.turn_id == start_event.turn_id and event.step_id == start_event.step_id
        )
        recommendation_next_action = (
            CollaborationNextAction.FINALIZE_RECOMMENDATION
            if start_event.turn_id in completed_lead_turns or not origin_calls
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
        if state in {None, CollaborationStepState.SUPERSEDED}:
            state = (
                CollaborationStepState.RUNNING
                if recommendation_next_action is CollaborationNextAction.FINALIZE_RECOMMENDATION
                else CollaborationStepState.OUTCOME_UNKNOWN
            )
    elif recommendation_result is not None and recommendation_result.superseding_user_event_id is None:
        result_event = next(event for event in reversed(events) if event.payload is recommendation_result)
        if result_event.turn_id == current_turn_id and current_proposal is None:
            recommendation_next_action = CollaborationNextAction.CONTINUE_WITH_LEAD
    return _project_task_context(events, CollaborationHistoryProjection(
        premise_report=premise_state[0], premise_report_event_id=premise_state[1],
        premise_decision=premise_state[2], premise_selection=premise_state[3],
        premise_admission=premise_state[4], premise_admission_event_id=premise_state[5],
        session_id=events[0].session_id,
        conversation_id=events[0].conversation_id,
        last_sequence=events[-1].sequence,
        current_turn_id=current_turn_id,
        current_step_id=current_step_id,
        current_proposal=current_proposal,
        current_state=state,
        confirmation_request_id=request_id,
        confirmation_decision=decision,
        execution_attempt_id=attempts.get(current_step_id),
        execution_result_event_id=result_event_id,
        execution_result=projection_result,
        next_safe_action=(
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            if state is CollaborationStepState.RECOVERY_REQUIRED
            or operation_next_action is CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            if state is CollaborationStepState.OUTCOME_UNKNOWN
            or operation_next_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            else operation_next_action
            if operation_next_action is not None
            else recommendation_next_action
            if recommendation_next_action is not None
            else CollaborationNextAction.RETRY_LEAD_TURN
            if recoverable_lead
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            if unmatched_submitted_lead
            else CollaborationNextAction.RETRY_LEAD_TURN
            if current_lead_outcome_failed
            else CollaborationNextAction.CONTINUE_WITH_LEAD
            if (
                premise_state[0] is not None
                and premise_state[2] is not None
                and type(events[-1].payload) is CollaborationPremiseDecisionPayload
                and events[-1].sender.kind.value == "user"
                and events[-1].payload.selection == premise_state[2]
                and events[-1].payload.selection.report_event_id == premise_state[1]
                and premise_state[2].action is PremiseDecisionAction.REJECT
                and current_proposal is None
                and state is None
            )
            else CollaborationNextAction.CHOOSE_PREMISE
            if premise_state[0] is not None
            and (
                premise_state[3] is None
                or premise_state[3].selection.report_event_id != premise_state[1]
            )
            and (premise_state[2] is None or premise_state[2].action is not PremiseDecisionAction.REJECT)
            and state not in {CollaborationStepState.RUNNING, CollaborationStepState.CONFIRMED,
                CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
            else _next_action(state, projection_result)
        ),
        rounds_spent=running_attempts.get(current_step_id, 0),
        physical_calls_spent=spent_calls.get(current_step_id, 0),
        current_artifact_candidate=artifact_candidates.get(current_step_id),
        current_artifact_state=artifact_states.get(current_step_id),
        current_reviewed_artifact_proposal=artifact_reviewed.get(current_step_id),
        current_artifact_application_request=application_request,
        current_artifact_application_result=application_result,
        current_story_commit_request=commit_request,
        current_story_commit_result=commit_result,
        artifact_objectives=artifact_objectives,
        offered_draft_sources=offered_draft_sources,
        current_recommendation_request=recommendation_request,
        current_recommendation_decision=recommendation_decision,
        current_recommendation_start=recommendation_start,
        current_recommendation_result=recommendation_result,
        current_recommendation_state=recommendation_state,
    ))


def _project_artifact_objective_history(
    events: tuple[CollaborationEventV2, ...],
) -> tuple[tuple[ArtifactObjective, ...], tuple[ArtifactDraftSource, ...]]:
    """Validate same-owner objectives after the existing authority projections."""

    objectives: dict[str, ArtifactObjective] = {}
    sources: dict[str, ArtifactDraftSource] = {}
    by_id = {event.event_id: event for event in events}
    applications: dict[str, ArtifactApplicationRequestPayload] = {}
    invalid_candidates: set[tuple[str, int, str]] = set()
    offset = 0
    while offset < len(events):
        batch = _retained_collaboration_batch(events, offset)
        offered = tuple(sources.values())
        transitions = [event for event in batch if type(event.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}]
        if len(transitions) > 1:
            raise ValueError("one Lead result may select only one objective transition")
        for event in batch:
            payload = event.payload
            if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
                facts = payload.facts
                previous = objectives.get(facts.objective_id)
                judgment = by_id.get(payload.lead_result_event_id)
                if (
                    judgment is not None
                    and type(judgment.payload) is CollaborationLeadJudgmentPayload
                    and judgment.payload.authoring_scope is not None
                    and judgment.payload.authoring_scope.intent is AuthoringIntent.REVIEW_ONLY
                ):
                    raise ValueError("review-only disclosure cannot create or advance an artifact objective")
                if (
                    facts.origin_conversation_id != event.conversation_id
                    or judgment is None or judgment not in batch
                    or judgment.sequence >= event.sequence
                    or judgment.turn_id != event.turn_id
                    or judgment.step_id != event.step_id
                    or type(judgment.payload) is not CollaborationLeadJudgmentPayload
                ):
                    raise ValueError("objective must co-batch its accepted owner Lead result")
                if payload.action is LeadObjectiveAction.START:
                    if type(facts) is ArtifactObjectiveFactsV2:
                        origin = _recommendation_origin(events, judgment.turn_id, judgment.step_id)
                        if (
                            origin is None
                            or origin[0].event_id != facts.origin_recommendation_request_event_id
                            or origin[1].event_id != facts.origin_recommendation_decision_event_id
                            or origin[1].payload.decision.kind is not CollaborationConfirmationDecision.CONFIRM
                            or origin[0].payload.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                            or origin[0].payload.objective_id is not None
                            or facts.request != origin[0].payload.instruction
                            or facts.target_paths != origin[0].payload.target_paths
                            or payload.draft_sources != origin[0].payload.draft_sources
                            or previous is not None or facts.offered_draft_sources != offered
                        ):
                            raise ValueError("objective lacks its exact structured-Confirm origin")
                        origin = origin[2]
                    else:
                        origin = by_id.get(facts.origin_user_event_id)
                    if (
                        previous is not None
                        or origin is None or origin.sequence >= judgment.sequence
                        or origin.turn_id != judgment.turn_id
                        or (type(facts) is ArtifactObjectiveFacts and (
                            origin.direction is not CollaborationDirection.USER_TO_LEAD
                            or type(origin.payload) is not CollaborationMessagePayload
                        ))
                        or facts.offered_draft_sources != offered
                    ):
                        raise ValueError("objective origin or offered draft snapshot is invalid")
                elif (
                    previous is None
                    or previous.state is not ArtifactObjectiveState.OPEN
                    or previous.latest_event_id != payload.previous_objective_event_id
                    or previous.facts != facts
                ):
                    raise ValueError("objective predecessor is foreign, superseded or closed")
                if any(source not in offered for source in payload.draft_sources):
                    raise ValueError("objective draft source was not exactly offered")
                if payload.action is LeadObjectiveAction.CANCEL:
                    if judgment.payload.kind is LeadOutcomeKind.PROPOSAL or (
                        previous is not None and payload.draft_sources != previous.draft_sources
                    ):
                        raise ValueError("objective cancellation cannot change its selected draft")
                elif payload.next_step is ArtifactObjectiveNextStep.CLARIFY:
                    if judgment.payload.kind is not LeadOutcomeKind.QUESTION:
                        raise ValueError("objective clarification requires its exact question")
                else:
                    proposals = [item for item in batch if type(item.payload) is CollaborationProposal]
                    candidates = [item for item in batch if type(item.payload) is ArtifactReviewCandidatePayload]
                    if judgment.payload.kind is not LeadOutcomeKind.PROPOSAL or len(proposals) != 1:
                        raise ValueError("objective work requires one exact consultation disclosure")
                    if payload.next_step is ArtifactObjectiveNextStep.CONSULT:
                        if candidates:
                            raise ValueError("objective consultation cannot bind artifact preparation")
                    else:
                        binding = payload.candidate_binding
                        assert binding is not None
                        if len(candidates) != 1:
                            raise ValueError("objective preparation requires one co-batched candidate")
                        candidate_event = candidates[0]
                        candidate_payload = candidate_event.payload
                        candidate = candidate_payload.candidate
                        paths = {
                            path
                            for operation in candidate.change_set.operations
                            for path in (
                                (operation.source_path, operation.destination_path)
                                if type(operation) is ArtifactRenameOperation else (operation.path,)
                            )
                        }
                        if (
                            candidate_event.event_id != binding.candidate_event_id
                            or candidate_event.sequence >= event.sequence
                            or candidate_event.step_id != event.step_id
                            or candidate_event.turn_id != event.turn_id
                            or candidate_payload.lead_judgment_event_id != judgment.event_id
                            or candidate.candidate_id != binding.candidate_id
                            or candidate.candidate_revision != binding.candidate_revision
                            or candidate.candidate_digest != binding.candidate_digest
                            or paths != set(facts.target_paths)
                        ):
                            raise ValueError("objective candidate binding or exact target scope drifted")
                objectives[facts.objective_id] = ArtifactObjective(
                    facts=facts, latest_event_id=event.event_id,
                    state=(ArtifactObjectiveState.CANCELLED if payload.action is LeadObjectiveAction.CANCEL else ArtifactObjectiveState.OPEN),
                    next_step=payload.next_step, draft_sources=payload.draft_sources,
                    candidate_binding=payload.candidate_binding,
                )
            elif type(payload) is ArtifactProposalStatePayload and payload.state in {
                ArtifactProposalState.SUPERSEDED, ArtifactProposalState.STALE,
                ArtifactProposalState.REJECTED, ArtifactProposalState.FAILED,
                ArtifactProposalState.REVISION_REQUIRED, ArtifactProposalState.RECOVERY_REQUIRED,
            }:
                invalid_candidates.add((payload.candidate_id, payload.candidate_revision, payload.candidate_digest))
            elif type(payload) is ArtifactApplicationRequestPayload:
                applications[payload.application_request.application_id] = payload
            elif type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
                ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
            }:
                request = applications.get(payload.application_id)
                if request is None or payload.result is None:
                    raise ValueError("objective Apply result lacks its validated boundary")
                candidate = request.reviewed_proposal.candidate
                key = (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest)
                for objective_id, objective in tuple(objectives.items()):
                    binding = objective.candidate_binding
                    bound_event = None if binding is None else by_id[binding.candidate_event_id]
                    bound_candidate = None if bound_event is None else bound_event.payload
                    if (
                        objective.state is ArtifactObjectiveState.OPEN
                        and binding is not None and key not in invalid_candidates
                        and key == (binding.candidate_id, binding.candidate_revision, binding.candidate_digest)
                        and bound_event is not None
                        and type(bound_candidate) is ArtifactReviewCandidatePayload
                        and bound_candidate.candidate == candidate
                        and bound_event.step_id == request.reviewed_proposal.step_id
                        and bound_candidate.lead_judgment_event_id
                        == request.reviewed_proposal.lead_judgment_event_id
                        and bound_candidate.consultation_proposal_id
                        == request.reviewed_proposal.consultation_proposal_id
                        and bound_candidate.consultation_proposal_revision
                        == request.reviewed_proposal.consultation_proposal_revision
                        and bound_candidate.consultation_proposal_digest
                        == request.reviewed_proposal.consultation_proposal_digest
                    ):
                        objectives[objective_id] = replace(
                            objective, state=ArtifactObjectiveState.SATISFIED,
                            satisfied_by_event_id=event.event_id,
                        )
            text = None
            if type(payload) is CollaborationLeadJudgmentPayload and (
                (payload.evidence.scope is LeadOutcomeScope.STORY_SPECIFIC
                 or any(type(item.payload) is LeadRecommendationRequestPayload
                        and item.payload.source_recommendation_event_id == event.event_id
                        for item in batch))
                and payload.kind in {
                    LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION,
                    LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION,
                }
            ):
                text = payload.message
            elif type(payload) is CollaborationLeadSynthesisPayload and payload.premise_report is None:
                text = payload.message
            if text is not None:
                sources[event.event_id] = ArtifactDraftSource(
                    event.conversation_id, event.event_id,
                    "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
        offset += len(batch)
    return tuple(objectives.values()), tuple(sources.values())


def _artifact_operation_next_action(
    tail: CollaborationEventV2,
    application_request: ArtifactApplicationRequestPayload | None,
    application_result: ArtifactApplicationResultPayload | None,
    commit_request: StoryCommitRequestPayload | None,
    commit_result: StoryCommitResultPayload | None,
) -> CollaborationNextAction | None:
    if (
        application_result is not None
        and application_result.outcome is ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED
    ) or (
        commit_result is not None
        and commit_result.outcome is StoryCommitOutcomeKind.RECOVERY_REQUIRED
    ):
        return CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
    if (
        application_result is not None
        and application_result.outcome is ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN
    ) or (
        commit_result is not None
        and commit_result.outcome is StoryCommitOutcomeKind.OUTCOME_UNKNOWN
    ):
        return CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
    if commit_request is not None and commit_result is None:
        return (
            CollaborationNextAction.SUBMIT_COMMIT_DECISION
            if type(tail.payload) is StoryCommitRequestPayload and tail.payload == commit_request
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
    if application_result is not None and application_result.outcome in {
        ArtifactApplicationOutcomeKind.APPLIED,
        ArtifactApplicationOutcomeKind.UNCHANGED,
    }:
        if commit_request is None:
            return (
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST
                if type(tail.payload) is ArtifactApplicationResultPayload
                and tail.payload == application_result
                else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            )
        if (
            commit_result is not None
            and commit_result.next_action is StoryCommitNextAction.REPREPARE
            and commit_result.outcome in {
                StoryCommitOutcomeKind.STALE,
                StoryCommitOutcomeKind.SUPERSEDED,
            }
        ):
            return (
                CollaborationNextAction.REPREPARE_COMMIT_REQUEST
                if type(tail.payload) is StoryCommitResultPayload
                and tail.payload == commit_result
                else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            )
    if application_request is not None and application_result is None:
        return (
            CollaborationNextAction.SUBMIT_APPLICATION_DECISION
            if type(tail.payload) is ArtifactApplicationRequestPayload and tail.payload == application_request
            else CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        )
    return None


def _closed_pure_discussion_control_gap_is_safe(
    request: CollaborationEventV2,
    decision: CollaborationEventV2,
    history: tuple[CollaborationEventV2, ...],
) -> bool:
    """Bridge only fully validated, known-closed read-only discussion rows."""
    prefix = tuple(event for event in history if event.sequence < decision.sequence)
    by_id = {event.event_id: event for event in prefix}
    if by_id.get(request.event_id) != request:
        return False
    gap = tuple(event for event in prefix if event.sequence > request.sequence)
    if not gap:
        return False
    excluded: set[str] = set()
    for control in prefix:
        if type(control.payload) is not CollaborationLeadTaskTransitionPayload:
            continue
        receipt = control.payload.receipt
        if receipt.action is not LeadTaskAction.DISCUSS:
            continue
        origin = by_id.get(receipt.origin_event_id)
        if (
            origin is None
            or origin.sender.kind is not CollaborationActorKind.USER
            or control.sender.kind is not CollaborationActorKind.RUNTIME
            or (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id)
            != (control.session_id, control.conversation_id, control.turn_id, control.step_id)
        ):
            continue
        current = tuple(
            event for event in prefix
            if (event.session_id, event.conversation_id, event.turn_id, event.step_id)
            == (control.session_id, control.conversation_id, control.turn_id, control.step_id)
        )
        call_events = tuple(
            event for event in current
            if type(event.payload) is CollaborationProviderCallPayload
        )
        calls = tuple(event.payload for event in call_events)
        if not calls or any(
            call.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
            for call in calls
        ):
            continue
        submitted = {
            (call.invocation_id, call.call_id) for call in calls
            if call.state is CollaborationProviderCallState.SUBMITTED
        }
        if any(
            (call.invocation_id, call.call_id) not in submitted for call in calls
        ) or calls[-1].state is not CollaborationProviderCallState.SUBMITTED:
            continue
        state_events = tuple(
            event for event in current
            if type(event.payload) is CollaborationStepStatePayload
        )
        states = tuple(event.payload.state for event in state_events)
        reports = tuple(
            event for event in current
            if type(event.payload) is CollaborationLeadDiscussionPayload
            and event.payload.report.origin_control_event_id == control.event_id
            and event.payload.report.associated_task_ref == receipt.source_task_ref
            and event.sequence > call_events[-1].sequence
        )
        failures = tuple(
            event.payload for event in current
            if type(event.payload) is CollaborationLeadOutcomeFailurePayload
            and event.sequence > call_events[-1].sequence
        )
        known_failure = (
            not states or (
                states[-1] in {
                    CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
                }
                and state_events[-1].sequence > call_events[-1].sequence
            )
        ) and any(
            (failure.invocation_id, failure.call_id)
            == (calls[-1].invocation_id, calls[-1].call_id)
            for failure in failures
        )
        if not reports and not known_failure:
            continue
        closure_sequence = (
            reports[-1].sequence if reports else (
                state_events[-1].sequence if state_events else max(
                    event.sequence for event in current
                    if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                    and event.sequence > call_events[-1].sequence
                    and (event.payload.invocation_id, event.payload.call_id)
                    == (calls[-1].invocation_id, calls[-1].call_id)
                )
            )
        )
        if any(
            event.sequence > closure_sequence
            and type(event.payload) is CollaborationStepStatePayload
            and event.payload.state in {
                CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            for event in current
        ):
            continue
        for event in current:
            payload = event.payload
            if event.event_id in {control.event_id, receipt.origin_event_id}:
                excluded.add(event.event_id)
            elif type(payload) is CollaborationMessagePayload:
                if event.sender.kind is not CollaborationActorKind.USER or (
                    event.event_id == receipt.origin_message_event_id
                ):
                    excluded.add(event.event_id)
            elif type(payload) in {
                CollaborationProviderCallPayload, CollaborationLeadOutcomeFailurePayload,
                CollaborationStepStatePayload, CollaborationLeadPublicResponsePayload,
            } or event in reports:
                excluded.add(event.event_id)
    return all(event.event_id in excluded for event in gap)


def _project_artifact_operation_history(
    events: tuple[CollaborationEventV2, ...],
    *, _control_history: tuple[CollaborationEventV2, ...] = (),
) -> tuple[
    ArtifactApplicationRequestPayload | None,
    ArtifactApplicationResultPayload | None,
    StoryCommitRequestPayload | None,
    StoryCommitResultPayload | None,
]:
    """Replay every exact Apply/Commit cycle and retain only the current pair."""

    reviewed: dict[str, ArtifactReviewedProposal] = {}
    request: ArtifactApplicationRequestPayload | None = None
    request_event: CollaborationEventV2 | None = None
    decision: ArtifactApplicationDecisionPayload | None = None
    decision_event: CollaborationEventV2 | None = None
    result: ArtifactApplicationResultPayload | None = None
    result_event: CollaborationEventV2 | None = None
    commit_request: StoryCommitRequestPayload | None = None
    commit_request_event: CollaborationEventV2 | None = None
    commit_decision: StoryCommitDecisionPayload | None = None
    commit_decision_event: CollaborationEventV2 | None = None
    commit_result: StoryCommitResultPayload | None = None
    commit_result_event: CollaborationEventV2 | None = None
    request_ids: set[str] = set()
    application_ids: set[str] = set()
    application_terminals = {
        ArtifactApplicationOutcomeKind.APPLIED,
        ArtifactApplicationOutcomeKind.UNCHANGED,
        ArtifactApplicationOutcomeKind.REJECTED,
        ArtifactApplicationOutcomeKind.SUPERSEDED,
        ArtifactApplicationOutcomeKind.STALE,
    }
    commit_terminals = {
        StoryCommitOutcomeKind.COMMITTED, StoryCommitOutcomeKind.REJECTED,
        StoryCommitOutcomeKind.SUPERSEDED, StoryCommitOutcomeKind.STALE,
    }
    for event in events:
        payload = event.payload
        if type(payload) is ArtifactReviewedProposal:
            reviewed[event.step_id] = payload
        elif type(payload) is ArtifactApplicationRequestPayload:
            if (
                (request is not None and (
                    result is None or result.outcome not in application_terminals
                ))
                or (commit_request is not None and (
                    commit_result is None or commit_result.outcome not in commit_terminals
                ))
                or reviewed.get(event.step_id) != payload.reviewed_proposal
                or payload.base_tail_sequence != event.sequence - event.batch_ordinal - 1
                or payload.confirmation_request_id in request_ids
                or payload.application_request.application_id in application_ids
            ):
                raise ValueError("artifact application request is not an eligible exact review tail")
            request_ids.add(payload.confirmation_request_id)
            application_ids.add(payload.application_request.application_id)
            request, request_event = payload, event
            decision = decision_event = result = result_event = None
            # Only a known terminal cycle can be retired by the next Apply request.
            commit_request = commit_request_event = None
            commit_decision = commit_decision_event = commit_result = commit_result_event = None
        elif type(payload) is ArtifactApplicationDecisionPayload:
            if (
                request is None or request_event is None or decision is not None
                or (
                    event.sequence != request_event.sequence + 1
                    and not _closed_pure_discussion_control_gap_is_safe(
                        request_event, event, _control_history,
                    )
                )
                or event.step_id != request_event.step_id
                or payload.confirmation_request_event_id != request_event.event_id
                or payload.confirmation_request_id != request.confirmation_request_id
                or payload.reviewed_proposal_id != request.reviewed_proposal.proposal_id
                or payload.reviewed_proposal_revision != request.reviewed_proposal.proposal_revision
                or payload.reviewed_proposal_digest != request.reviewed_proposal.proposal_digest
                or payload.candidate_id != request.application_request.candidate_id
                or payload.candidate_revision != request.application_request.candidate_revision
                or payload.candidate_digest != request.application_request.candidate_digest
                or payload.application_id != request.application_request.application_id
                or payload.application_request_digest != request.application_request_digest
            ):
                raise ValueError("artifact application decision does not match request")
            decision, decision_event = payload, event
        elif type(payload) is ArtifactApplicationResultPayload:
            if (
                request is None or decision is None or decision_event is None
                or result is not None
                or event.sequence != decision_event.sequence + 1
                or event.step_id != decision_event.step_id
                or payload.decision_event_id != decision_event.event_id
                or payload.confirmation_request_id != request.confirmation_request_id
                or payload.reviewed_proposal_id != request.reviewed_proposal.proposal_id
                or payload.reviewed_proposal_digest != request.reviewed_proposal.proposal_digest
                or payload.application_id != request.application_request.application_id
                or payload.application_request_digest != request.application_request_digest
                or (payload.result is not None and (
                    payload.result.provenance != request.application_request.provenance
                ))
                or (decision.decision is ArtifactApplicationDecisionKind.REJECT
                    and payload.outcome is not ArtifactApplicationOutcomeKind.REJECTED)
                or (decision.decision is ArtifactApplicationDecisionKind.REVISE
                    and payload.outcome is not ArtifactApplicationOutcomeKind.SUPERSEDED)
                or (decision.decision is ArtifactApplicationDecisionKind.APPLY
                    and payload.outcome in {
                        ArtifactApplicationOutcomeKind.REJECTED,
                        ArtifactApplicationOutcomeKind.SUPERSEDED,
                    })
            ):
                raise ValueError("artifact application result does not match decision")
            result, result_event = payload, event
        elif type(payload) is StoryCommitRequestPayload:
            predecessor = result_event if commit_request is None else commit_result_event
            if (
                result is None
                or result_event is None
                or predecessor is None
                or event.sequence != predecessor.sequence + 1
                or event.step_id != result_event.step_id
                or payload.application_result_event_id != result_event.event_id
                or result.result is None
                or result.outcome not in {
                    ArtifactApplicationOutcomeKind.APPLIED,
                    ArtifactApplicationOutcomeKind.UNCHANGED,
                }
                or payload.candidate.application_id != result.application_id
                or payload.candidate.proposal_id != result.reviewed_proposal_id
                or payload.candidate.correlation_id != result.application_request_digest
                or payload.confirmation_request_id in request_ids
                or (
                    commit_request is not None
                    and (
                        commit_result is None
                        or commit_result.next_action is not StoryCommitNextAction.REPREPARE
                    )
                )
            ):
                raise ValueError("story commit request does not follow exact application")
            request_ids.add(payload.confirmation_request_id)
            commit_request, commit_request_event = payload, event
            commit_decision = commit_decision_event = commit_result = commit_result_event = None
        elif type(payload) is StoryCommitDecisionPayload:
            if (
                commit_request is None or commit_request_event is None
                or commit_decision is not None
                or (
                    event.sequence != commit_request_event.sequence + 1
                    and not _closed_pure_discussion_control_gap_is_safe(
                        commit_request_event, event, _control_history,
                    )
                )
                or event.step_id != commit_request_event.step_id
                or payload.confirmation_request_event_id != commit_request_event.event_id
                or payload.confirmation_request_id != commit_request.confirmation_request_id
                or payload.application_result_event_id != commit_request.application_result_event_id
                or payload.candidate_id != commit_request.candidate.candidate_id
                or payload.request_digest != commit_request.request_digest
            ):
                raise ValueError("story commit decision does not match request")
            commit_decision, commit_decision_event = payload, event
        elif type(payload) is StoryCommitResultPayload:
            if (
                commit_request is None or commit_decision is None
                or commit_decision_event is None or commit_result is not None
                or event.sequence != commit_decision_event.sequence + 1
                or event.step_id != commit_decision_event.step_id
                or payload.decision_event_id != commit_decision_event.event_id
                or payload.confirmation_request_id != commit_request.confirmation_request_id
                or payload.candidate_id != commit_request.candidate.candidate_id
                or payload.request_digest != commit_request.request_digest
                or (commit_decision.decision is StoryCommitDecision.COMMIT
                    and payload.outcome in {
                        StoryCommitOutcomeKind.REJECTED, StoryCommitOutcomeKind.SUPERSEDED,
                    })
                or (commit_decision.decision is StoryCommitDecision.REJECT
                    and payload.outcome is not StoryCommitOutcomeKind.REJECTED)
                or (commit_decision.decision is StoryCommitDecision.REVISE
                    and payload.outcome is not StoryCommitOutcomeKind.SUPERSEDED)
            ):
                raise ValueError("story commit result does not match decision")
            commit_result, commit_result_event = payload, event
    return request, result, commit_request, commit_result


def _validate_collaboration_execution_result(
    *,
    event: CollaborationEventV2,
    result: CollaborationExecutionResult,
    proposal: CollaborationProposal,
    attempt_id: str | None,
    dispatches: dict[str, CollaborationEventV2],
    replies: dict[str, CollaborationEventV2],
    event_by_id: dict[str, CollaborationEventV2],
    batch: tuple[CollaborationEventV2, ...],
) -> None:
    if result.execution_attempt_id != attempt_id:
        raise ValueError("execution result attempt does not match running")
    expected_ids = tuple(item.assignment_id for item in proposal.assignments)
    actual_ids = tuple(item.assignment_id for item in result.assignment_outcomes)
    if actual_ids != expected_ids:
        raise ValueError("execution result outcomes must match proposal order exactly")
    if proposal.premise_decision is not None:
        suffix = batch[event.batch_ordinal + 1:]
        expected_terminal_state = {
            CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
            CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
            CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
        }.get(result.overall_outcome)
        if (
            proposal.step_kind is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or type(proposal.basis.evidence) is not CollaborationLeadEvidence
            or proposal.basis.evidence.scope is not LeadOutcomeScope.PREMISE_ANALYSIS
            or expected_terminal_state is None
            or len(suffix) != 1
            or type(suffix[0].payload) is not CollaborationStepStatePayload
            or suffix[0].payload.state is not expected_terminal_state
            or suffix[0].payload.execution_result_event_id != event.event_id
            or suffix[0].step_id != event.step_id
        ):
            raise ValueError("analysis result requires its exact co-batched terminal state")
        if result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
            analysis_synthesis_event = event_by_id.get(result.synthesis_result_event_id)
            if (
                result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.SUCCEEDED
                or analysis_synthesis_event is None
                or type(analysis_synthesis_event.payload) is not CollaborationLeadSynthesisPayload
                or analysis_synthesis_event.payload.premise_report is None
                or analysis_synthesis_event.payload.premise_report.spec != proposal.premise_decision
            ):
                raise ValueError("successful analysis requires its exact typed report")
        elif result.synthesis_result_event_id is not None:
            raise ValueError("failed or cancelled analysis cannot publish a premise report")
    elif event is not batch[-1]:
        suffix = batch[event.batch_ordinal + 1:]
        if (len(suffix) != 2
            or type(suffix[0].payload) is not CollaborationStepStatePayload
            or suffix[0].payload.state is not CollaborationStepState.COMPLETED
            or suffix[0].payload.execution_result_event_id != event.event_id
            or suffix[0].step_id != event.step_id
            or type(suffix[1].payload) is not LeadRecommendationRequestPayload
            or suffix[1].payload.source_recommendation_event_id != result.synthesis_result_event_id
            or result.overall_outcome is not CollaborationExecutionOutcomeKind.SUCCEEDED):
            raise ValueError("execution result must be final or precede its exact synthesis recommendation closure")
    preceding = batch[: event.batch_ordinal]
    batch_event_ids = {item.event_id for item in preceding}
    successful_reply_ids: list[str] = []
    for assignment, outcome in zip(
        proposal.assignments,
        result.assignment_outcomes,
        strict=True,
    ):
        dispatch = dispatches.get(assignment.assignment_id)
        if dispatch is None:
            raise ValueError("execution result assignment was not dispatched")
        if outcome.submitted_physical_calls > assignment.max_physical_calls:
            raise ValueError("assignment result exceeds its call allocation")
        reply = replies.get(assignment.assignment_id)
        if outcome.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED:
            if reply is None or outcome.reply_event_id != reply.event_id:
                raise ValueError(
                    "successful assignment result requires its validated reply"
                )
            if reply.event_id not in batch_event_ids:
                raise ValueError(
                    "successful reply must precede its result in the same batch"
                )
            successful_reply_ids.append(reply.event_id)
        elif reply is not None:
            raise ValueError("failed or cancelled assignment cannot have a reply")
    actual_reply_ids = [
        item.event_id
        for item in preceding
        if item.kind is CollaborationEventKindV2.SPECIALIST_REPLY
    ]
    if actual_reply_ids != successful_reply_ids:
        raise ValueError("result-batch specialist replies must use proposal order")
    if (
        result.synthesis_submitted_physical_calls
        > proposal.budget.synthesis_max_physical_calls
    ):
        raise ValueError("execution result exceeds synthesis call allocation")
    if proposal.budget.synthesis_max_physical_calls == 0:
        if (
            result.synthesis_outcome
            is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
            or result.synthesis_submitted_physical_calls != 0
        ):
            raise ValueError("zero synthesis allocation forbids synthesis submission")
    elif (
        proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
        and all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in result.assignment_outcomes
        )
        and result.synthesis_outcome is CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
    ):
        raise ValueError(
            "successful consultation assignments require synthesis outcome"
        )
    if result.synthesis_result_event_id is not None:
        synthesis_event = event_by_id.get(result.synthesis_result_event_id)
        if (
            synthesis_event is None
            or synthesis_event.kind is not CollaborationEventKindV2.LEAD_SYNTHESIS
            or type(synthesis_event.payload) is not CollaborationLeadSynthesisPayload
        ):
            raise ValueError("synthesis result must reference a typed synthesis event")
        if result.synthesis_result_event_id not in batch_event_ids:
            raise ValueError(
                "synthesis result must precede execution result in the same batch"
            )

    terminal_events = [
        item
        for item in preceding
        if type(item.payload) is CollaborationTerminalOutcomePayload
    ]
    expected_terminal_count = len(result.assignment_outcomes)
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        expected_terminal_count += 1
    if len(terminal_events) != expected_terminal_count:
        raise ValueError("result batch must contain every required terminal outcome")
    for terminal_event, outcome in zip(
        terminal_events[: len(result.assignment_outcomes)],
        result.assignment_outcomes,
        strict=True,
    ):
        terminal = terminal_event.payload
        assert isinstance(terminal, CollaborationTerminalOutcomePayload)
        if (
            terminal.scope is not CollaborationTerminalScope.ASSIGNMENT
            or terminal.assignment_id != outcome.assignment_id
            or terminal.outcome is not outcome.outcome
            or terminal.submitted_physical_calls != outcome.submitted_physical_calls
        ):
            raise ValueError(
                "assignment terminal outcomes must match result order and facts"
            )
        if outcome.reply_event_id is not None:
            reply_event = event_by_id[outcome.reply_event_id]
            if reply_event.sequence >= terminal_event.sequence:
                raise ValueError("successful reply must precede its terminal outcome")
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        synthesis_terminal = terminal_events[-1].payload
        assert isinstance(synthesis_terminal, CollaborationTerminalOutcomePayload)
        synthesis_kind = CollaborationAssignmentOutcomeKind(
            result.synthesis_outcome.value
        )
        if (
            synthesis_terminal.scope is not CollaborationTerminalScope.SYNTHESIS
            or synthesis_terminal.assignment_id is not None
            or synthesis_terminal.outcome is not synthesis_kind
            or synthesis_terminal.submitted_physical_calls
            != result.synthesis_submitted_physical_calls
        ):
            raise ValueError("synthesis terminal outcome does not match result facts")
        if result.synthesis_result_event_id is not None:
            synthesis_event = event_by_id[result.synthesis_result_event_id]
            if synthesis_event.sequence >= terminal_events[-1].sequence:
                raise ValueError("synthesis message must precede its terminal outcome")

    for item in preceding:
        if item.kind in (
            CollaborationEventKindV2.SPECIALIST_REPLY,
            CollaborationEventKindV2.LEAD_SYNTHESIS,
            CollaborationEventKindV2.ARTIFACT_REVIEW_SYNTHESIS,
            CollaborationEventKindV2.TERMINAL_OUTCOME,
        ):
            continue
        raise ValueError("result batch contains an unrelated external exchange")
    expected_visible_ids = tuple(item.event_id for item in preceding)
    if result.visible_exchange_event_ids != expected_visible_ids:
        raise ValueError(
            "visible exchange event IDs must exactly match result-batch order"
        )
    used = (
        sum(item.submitted_physical_calls for item in result.assignment_outcomes)
        + result.synthesis_submitted_physical_calls
    )
    if used > proposal.budget.max_additional_physical_calls:
        raise ValueError("execution result exceeds the reserved whole-step budget")
    if (
        proposal.budget.physical_calls_spent + used
        > proposal.budget.physical_call_limit
    ):
        raise ValueError("execution result exceeds the lineage call ceiling")


def _validate_collaboration_co_batch(
    *,
    batch: tuple[CollaborationEventV2, ...],
    prior_tail: CollaborationEventV2 | None,
    proposals: list[CollaborationProposal],
    states: list[tuple[CollaborationEventV2, CollaborationStepStatePayload]],
    confirmations: list[tuple[CollaborationEventV2, CollaborationConfirmationPayload]],
    dispatches: list[
        tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
    ],
    replies: list[tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]],
    results: list[tuple[CollaborationEventV2, CollaborationExecutionResult]],
    interruptions: list[CollaborationEventV2],
    active_proposals: dict[str, CollaborationProposal],
    task_control_history: tuple[CollaborationEventV2, ...] = (),
) -> None:
    awaiting = [
        item
        for item in states
        if item[1].state is CollaborationStepState.AWAITING_CONFIRMATION
    ]
    reconfirming = [
        item
        for item in states
        if item[1].state is CollaborationStepState.RECONFIRMATION_REQUIRED
    ]
    if proposals and len(proposals) != len(awaiting):
        raise ValueError("proposal disclosure and awaiting state must be co-batched")
    if awaiting and len(proposals) != len(awaiting):
        raise ValueError("awaiting state requires co-batched proposal disclosure")
    for event, _payload in awaiting + reconfirming:
        if event is not batch[-1]:
            raise ValueError("confirmation-request state must be final in its batch")

    if confirmations:
        if len(confirmations) != 1:
            raise ValueError("one batch may decide only one confirmation request")
        confirmation = confirmations[0][1]
        expected_state = {
            CollaborationConfirmationDecision.CONFIRM: CollaborationStepState.CONFIRMED,
            CollaborationConfirmationDecision.REJECT: CollaborationStepState.REJECTED,
            CollaborationConfirmationDecision.REVISE: CollaborationStepState.SUPERSEDED,
        }[confirmation.decision]
        matching = [item for item in states if item[1].state is expected_state]
        artifact_stale = any(
            type(item.payload) is ArtifactProposalStatePayload
            and item.payload.state is ArtifactProposalState.STALE
            for item in batch
        )
        if (
            confirmation.decision is CollaborationConfirmationDecision.CONFIRM
            and artifact_stale
        ):
            matching = [
                item
                for item in states
                if item[1].state is CollaborationStepState.SUPERSEDED
            ]
        if len(matching) != 1:
            raise ValueError("decision and resulting state must be co-batched")
    else:
        decision_states = [
            (event, payload)
            for event, payload in states
            if payload.state
            in (
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
            )
        ]
        ordinary_supersession = bool(
            len(batch) == 2
            and len(decision_states) == 1
            and decision_states[0][1].state is CollaborationStepState.SUPERSEDED
            and type(batch[0].payload) is CollaborationMessagePayload
            and batch[0].sender.kind.value == "user"
            and batch[0].recipient.kind.value == "lead"
            and batch[1] is decision_states[0][0]
            and batch[1].sender.kind.value == "runtime"
            and batch[1].recipient.kind.value == "user"
            and batch[0].step_id == batch[1].step_id
            and batch[0].turn_id == batch[1].turn_id
            and prior_tail is not None
            and prior_tail.step_id == batch[0].step_id
            and type(prior_tail.payload) is CollaborationStepStatePayload
            and prior_tail.payload.state
            in {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
            }
        )
        task_supersession = False
        if (
            task_control_history
            and len(decision_states) == 1
            and decision_states[0][1].state is CollaborationStepState.SUPERSEDED
            and prior_tail is not None
            and type(prior_tail.payload) is CollaborationStepStatePayload
            and prior_tail.payload.state in {CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
        ):
            state_event, state = decision_states[0]
            original_batch = tuple(
                item for item in task_control_history if item.batch_id == state_event.batch_id
            )
            origins = tuple(item for item in original_batch
                            if type(item.payload) is CollaborationLeadTaskDecisionPayload)
            transitions = tuple(item for item in original_batch
                                if type(item.payload) is CollaborationLeadTaskTransitionPayload)
            if len(origins) == len(transitions) == 1:
                origin, transition = origins[0], transitions[0]
                decision = origin.payload.decision
                receipt = transition.payload.receipt
                current = active_proposals.get(state_event.step_id)
                ref = decision.task_ref
                source = None if ref is None else next((
                    item for item in task_control_history if item.event_id == ref.scope_event_id
                ), None)
                source_scope = None if source is None else getattr(source.payload, "authoring_scope", None)
                reference_exact = ref is None or bool(
                    source is not None and type(source_scope) is AuthoringScope
                    and (source.session_id, source.conversation_id, source_scope.task_id,
                         source_scope.task_revision, _task_scope_digest(source_scope))
                    == (ref.session_id, ref.conversation_id, ref.task_id,
                        ref.task_revision, ref.scope_digest)
                    and (ref.session_id, ref.conversation_id)
                    == (state_event.session_id, state_event.conversation_id)
                    and source.sequence < origin.sequence
                )
                current_scope = None if current is None else current.authoring_scope
                current_source = bool(
                    ref is not None and reference_exact and current_scope is not None
                    and (ref.task_id, ref.task_revision)
                    == (current_scope.task_id, current_scope.task_revision)
                    and (
                        current_scope == source_scope
                        or (
                            source_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                            and current_scope.activity is AuthoringActivity.REVIEW
                            and replace(current_scope, activity=source_scope.activity) == source_scope
                            and any(
                                item.step_id == current.step_id
                                and type(item.payload) is LeadRecommendationRequestPayload
                                and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                                and item.payload.authoring_scope == source_scope
                                and item.payload.source_execution_attempt_id is not None
                                and item.sequence < origin.sequence
                                for item in task_control_history
                            )
                            and any(
                                item.step_id == current.step_id
                                and type(item.payload) is ArtifactReviewCandidatePayload
                                and (
                                    item.payload.consultation_proposal_id,
                                    item.payload.consultation_proposal_revision,
                                    item.payload.consultation_proposal_digest,
                                ) == (current.proposal_id, current.proposal_revision, current.proposal_digest)
                                and item.sequence < origin.sequence
                                for item in task_control_history
                            )
                        )
                    )
                )
                independent_new = bool(
                    decision.action is LeadTaskAction.NEW_TASK and reference_exact
                    and receipt.target_task_id is not None and receipt.target_task_revision == 1
                    and (current_scope is None or receipt.target_task_id != current_scope.task_id)
                )
                target_exact = (
                    receipt.target_task_id is None and receipt.target_task_revision is None
                    and receipt.planning_step_id is None
                ) if decision.action in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD} else (
                    current_source and receipt.target_task_id == ref.task_id
                    and receipt.target_task_revision == ref.task_revision + 1
                    and receipt.planning_step_id == transition.step_id
                ) if decision.action is LeadTaskAction.RESUME else (
                    independent_new and receipt.planning_step_id == transition.step_id
                )
                allowed_rows = all(
                    item in (origin, transition, state_event)
                    or (type(item.payload) is CollaborationMessagePayload
                        and item.event_id == receipt.origin_message_event_id
                        and item.sender.kind is CollaborationActorKind.USER)
                    or (type(item.payload) is CollaborationLeadMaterialCapturePayload
                        and item.event_id in receipt.material_capture_event_ids)
                    for item in original_batch
                )
                task_supersession = bool(
                    current is not None and allowed_rows and target_exact
                    and decision.action in {
                        LeadTaskAction.HOLD, LeadTaskAction.DISCARD,
                        LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK,
                    }
                    and (current_source or independent_new)
                    and origin.sender.kind is CollaborationActorKind.USER
                    and origin.recipient.kind is CollaborationActorKind.RUNTIME
                    and transition.sender.kind is CollaborationActorKind.RUNTIME
                    and transition.recipient.kind is CollaborationActorKind.USER
                    and state_event.sender.kind is CollaborationActorKind.RUNTIME
                    and state_event.recipient.kind is CollaborationActorKind.USER
                    and (origin.session_id, origin.conversation_id, origin.turn_id, origin.batch_id)
                    == (transition.session_id, transition.conversation_id, transition.turn_id, transition.batch_id)
                    == (state_event.session_id, state_event.conversation_id, state_event.turn_id, state_event.batch_id)
                    and origin.step_id == transition.step_id
                    and origin.event_id == decision.request_id
                    and receipt.origin_kind == "task_decision"
                    and receipt.origin_event_id == origin.event_id
                    and (receipt.action, receipt.source_task_ref) == (decision.action, decision.task_ref)
                    and prior_tail.step_id == state_event.step_id == current.step_id
                    and (prior_tail.payload.proposal_id, prior_tail.payload.proposal_revision,
                         prior_tail.payload.proposal_digest)
                    == (state.proposal_id, state.proposal_revision, state.proposal_digest)
                    == (current.proposal_id, current.proposal_revision, current.proposal_digest)
                    and (origin.sequence == prior_tail.sequence + 1
                         or _closed_pure_discussion_control_gap_is_safe(
                             prior_tail, origin, task_control_history,
                         ))
                )
        if decision_states and not (ordinary_supersession or task_supersession):
            raise ValueError(
                "decision-derived state requires a co-batched confirmation"
            )

    running = [
        (event, payload)
        for event, payload in states
        if payload.state is CollaborationStepState.RUNNING
    ]
    if running:
        if len(running) != 1:
            raise ValueError("one batch may start only one collaboration step")
        running_event, _running_payload = running[0]
        proposal = active_proposals[running_event.step_id]
        expected_dispatch_ids = tuple(
            item.assignment_id for item in proposal.assignments
        )
        actual = tuple(item.assignment.assignment_id for _event, item in dispatches)
        if actual != expected_dispatch_ids:
            raise ValueError(
                "running state must co-batch every dispatch in proposal order"
            )
    elif dispatches:
        raise ValueError("assignment dispatches must co-batch the running state")

    terminal_outcomes = [
        event
        for event in batch
        if type(event.payload) is CollaborationTerminalOutcomePayload
    ]
    outcome_unknown = any(
        payload.state is CollaborationStepState.OUTCOME_UNKNOWN
        for _event, payload in states
    )
    if replies or terminal_outcomes:
        if len(results) != 1 and not outcome_unknown:
            raise ValueError(
                "external result exchanges require one co-batched execution result"
            )
    syntheses = [
        event
        for event in batch
        if type(event.payload) is CollaborationLeadSynthesisPayload
    ]
    if syntheses and len(results) != 1:
        raise ValueError("lead synthesis requires one co-batched execution result")
    if len(results) > 1:
        raise ValueError("one batch may record only one execution result")

    terminal_states = [
        (event, payload)
        for event, payload in states
        if payload.state
        in (
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
        )
    ]
    for state_event, state_payload in terminal_states:
        has_notice = any(
            event.step_id == state_event.step_id
            and event.kind is CollaborationEventKindV2.MESSAGE
            and type(event.payload) is CollaborationMessagePayload
            for event in batch
        )
        if not has_notice:
            has_notice = any(
                type(event.payload) is LeadRecommendationRequestPayload
                and any(source.event_id == event.payload.source_recommendation_event_id
                        and source.step_id == state_event.step_id
                        and type(source.payload) is CollaborationLeadSynthesisPayload
                        for source in batch)
                for event in batch
            )
        premise_proposal = active_proposals.get(state_event.step_id)
        premise_result = next(
            (
                payload
                for event, payload in results
                if event.event_id == state_payload.execution_result_event_id
                and event.step_id == state_event.step_id
            ),
            None,
        )
        premise_notice = False
        if (
            premise_proposal is not None
            and premise_proposal.premise_decision is not None
            and premise_proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and premise_proposal.authoring_scope is None
            and premise_proposal.basis.evidence is not None
            and premise_proposal.basis.evidence.scope is LeadOutcomeScope.PREMISE_ANALYSIS
            and premise_result is not None
            and (
                premise_result.proposal_id,
                premise_result.proposal_revision,
                premise_result.proposal_digest,
            ) == (
                premise_proposal.proposal_id,
                premise_proposal.proposal_revision,
                premise_proposal.proposal_digest,
            )
        ):
            if premise_result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
                premise_notice = any(
                    event.event_id == premise_result.synthesis_result_event_id
                    and event.event_id in premise_result.visible_exchange_event_ids
                    and event.step_id == state_event.step_id
                    and event.kind is CollaborationEventKindV2.LEAD_SYNTHESIS
                    and event.sender.kind.value == "lead"
                    and event.recipient.kind.value == "user"
                    and type(event.payload) is CollaborationLeadSynthesisPayload
                    and event.payload.premise_report is not None
                    and event.payload.premise_report.spec == premise_proposal.premise_decision
                    and event.payload.execution_attempt_id == premise_result.execution_attempt_id
                    for event in batch
                )
            elif premise_result.overall_outcome in (
                CollaborationExecutionOutcomeKind.FAILED,
                CollaborationExecutionOutcomeKind.CANCELLED,
            ):
                premise_notice = any(
                    event.event_id in premise_result.visible_exchange_event_ids
                    and event.step_id == state_event.step_id
                    and event.kind is CollaborationEventKindV2.TERMINAL_OUTCOME
                    and event.sender.kind.value == "runtime"
                    and event.recipient.kind.value == "user"
                    and type(event.payload) is CollaborationTerminalOutcomePayload
                    and event.payload.execution_attempt_id == premise_result.execution_attempt_id
                    and event.payload.outcome.value == premise_result.overall_outcome.value
                    for event in terminal_outcomes
                )
        has_notice = has_notice or premise_notice
        if not has_notice:
            raise ValueError("terminal step state requires a co-batched visible notice")
        if (
            state_payload.state is CollaborationStepState.INTERRUPTED
            and not interruptions
        ):
            result = next(
                (
                    item
                    for _event, item in results
                    if _event.event_id == state_payload.execution_result_event_id
                ),
                None,
            )
            if result is not None and not (
                premise_notice
                and premise_result is not None
                and premise_result.overall_outcome is CollaborationExecutionOutcomeKind.CANCELLED
            ):
                raise ValueError("interrupted state requires a co-batched interruption")

    unknown_states = [
        item
        for item in states
        if item[1].state is CollaborationStepState.OUTCOME_UNKNOWN
    ]
    if unknown_states and not interruptions:
        raise ValueError(
            "outcome-unknown state requires a co-batched interruption record"
        )
    if interruptions and not any(
        payload.state
        in (
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )
        for _event, payload in states
    ):
        raise ValueError("interruption must co-batch its corresponding terminal state")

    for _event, result in results:
        terminal_payloads = [
            event.payload
            for event in batch
            if type(event.payload) is CollaborationTerminalOutcomePayload
            and event.payload.execution_attempt_id == result.execution_attempt_id
        ]
        assignment_terminal_ids = tuple(
            item.assignment_id
            for item in terminal_payloads
            if item.scope is CollaborationTerminalScope.ASSIGNMENT
        )
        expected_assignment_ids = tuple(
            item.assignment_id for item in result.assignment_outcomes
        )
        if (
            assignment_terminal_ids
            and assignment_terminal_ids != expected_assignment_ids
        ):
            raise ValueError("terminal outcomes must match result assignments in order")


def _collaboration_readable_fields(
    payload: CollaborationPayload,
) -> tuple[CollaborationReadableField, ...]:
    """Project the closed searchable field set for one typed public payload."""

    if type(payload) is CollaborationLeadTaskDecisionPayload:
        decision = payload.decision
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_TASK_CONTROL,
                                          decision.action.value + (": " + decision.text if decision.text else "")),)
    if type(payload) is CollaborationLeadTaskTransitionPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_TASK_CONTROL,
                                          payload.receipt.action.value),)
    if type(payload) is CollaborationLeadMaterialCapturePayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_SELECTED_MATERIAL,
                                          payload.capture.selected_text),)
    if type(payload) is CollaborationLeadDiscussionPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_DISCUSSION_TEXT,
                                          payload.report.response_text),)
    if type(payload) is CollaborationLeadPublicResponsePayload:
        response = payload.response
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_TEXT, response.response_text),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_STATUS, response.acceptance.value),
            *((CollaborationReadableField(CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_DIAGNOSTICS,
                                          "\n".join(response.validation_details)),) if response.validation_details else ()),
        )
    if type(payload) in {CollaborationPremiseDecisionPayload, CollaborationPremiseSelectionPayload,
                         CollaborationPremiseFreshWorkAdmissionPayload}:
        value = payload.selection if type(payload) is CollaborationPremiseDecisionPayload else (
            payload.capture if type(payload) is CollaborationPremiseSelectionPayload else payload.admission)
        return (CollaborationReadableField(CollaborationReadableFieldKey.MESSAGE_CONTENT,
            json.dumps(premise_value_to_dict(value), ensure_ascii=False, sort_keys=True)),)

    if type(payload) is CollaborationEditorialRecordPayload:
        return (
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_TEXT, text=payload.record.draft.text),
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_REASON, text=payload.record.draft.reason),
            CollaborationReadableField(key=CollaborationReadableFieldKey.EDITORIAL_TRANSITION_REASON, text=payload.record.request.transition_reason),
        )

    if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_REQUEST,
                text=payload.facts.request,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_ACTION,
                text=payload.action.value,
            ),
            *((CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_NEXT_STEP,
                text=payload.next_step.value,
            ),) if payload.next_step is not None else ()),
        )
    if type(payload) is LeadRecommendationRequestPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_PURPOSE, payload.purpose),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_INSTRUCTION, payload.instruction),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT, payload.expected_result),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_IMPACT, payload.user_impact),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_SOURCES, "\n".join(item.locator for item in payload.basis.inputs)),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_TARGETS, "\n".join((*payload.target_paths, *payload.specialist_ids))),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_COST, f"{payload.physical_calls_spent} spent; at most {payload.max_preparation_physical_calls} preparation calls"),
        )
    if type(payload) is LeadRecommendationDecisionPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_DECISION, payload.decision.kind.value),
            *((CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_REVISION, payload.decision.revision_request),)
              if payload.decision.revision_request is not None else ()),
        )
    if type(payload) is LeadRecommendationStartedPayload:
        return (CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE, LeadRecommendationState.RUNNING.value),)
    if type(payload) is LeadRecommendationResultPayload:
        return (
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE, payload.state.value),
            CollaborationReadableField(CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT, payload.message),
        )
    if type(payload) is CollaborationMessagePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.MESSAGE_CONTENT,
                text=payload.content,
            ),
        )
    if type(payload) is CollaborationLeadJudgmentPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_JUDGMENT_KIND,
                text=payload.kind.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_JUDGMENT_MESSAGE,
                text=payload.message,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_SCOPE,
                text=payload.evidence.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_STATUS,
                text=payload.evidence.status.value,
            ),
        )
    if type(payload) is CollaborationProposal:
        fields = [
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE,
                text=payload.purpose,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_USER_IMPACT,
                text=payload.user_impact,
            ),
        ]
        fields.extend(
            CollaborationReadableField(
                key=(CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_INSTRUCTION),
                text=assignment.instruction,
                assignment_ordinal=ordinal,
            )
            for ordinal, assignment in enumerate(payload.assignments)
        )
        fields.extend(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_INPUT_LOCATOR,
                text=item.locator,
                input_ordinal=ordinal,
            )
            for ordinal, item in enumerate(payload.basis.inputs)
        )
        fields.extend(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_INPUT_RANGE,
                text=(
                    "whole"
                    if item.start_line is None
                    else f"lines:{item.start_line}-{item.end_line}"
                ),
                input_ordinal=ordinal,
            )
            for ordinal, item in enumerate(payload.basis.inputs)
        )
        fields.append(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_CALL_USAGE,
                text=(
                    f"rounds_spent={payload.budget.rounds_spent};"
                    f"rounds_added={payload.budget.rounds_added};"
                    f"round_limit={payload.budget.round_limit};"
                    f"physical_calls_spent={payload.budget.physical_calls_spent};"
                    "max_additional_physical_calls="
                    f"{payload.budget.max_additional_physical_calls};"
                    f"physical_call_limit={payload.budget.physical_call_limit}"
                ),
            )
        )
        fields.extend(
            CollaborationReadableField(
                key=(CollaborationReadableFieldKey.PROPOSAL_ASSIGNMENT_CALL_LIMIT),
                text=f"max_physical_calls={assignment.max_physical_calls}",
                assignment_ordinal=ordinal,
            )
            for ordinal, assignment in enumerate(payload.assignments)
        )
        fields.append(
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.PROPOSAL_SYNTHESIS_CALL_LIMIT,
                text=(
                    f"max_physical_calls={payload.budget.synthesis_max_physical_calls}"
                ),
            )
        )
        return tuple(fields)
    if type(payload) is CollaborationAssignmentDispatchPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.DISPATCH_INSTRUCTION,
                text=payload.assignment.instruction,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.DISPATCH_CALL_LIMIT,
                text=(f"max_physical_calls={payload.assignment.max_physical_calls}"),
            ),
        )
    if type(payload) is CollaborationSpecialistReplyPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.SPECIALIST_REPLY_ASSESSMENT,
                text=payload.assessment.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.SPECIALIST_REPLY_CONTENT,
                text=payload.message,
            ),
        )
    if type(payload) is CollaborationLeadSynthesisPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_SYNTHESIS_MESSAGE,
                text=payload.message,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_SCOPE,
                text=payload.evidence.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_EVIDENCE_STATUS,
                text=payload.evidence.status.value,
            ),
        )
    if type(payload) is CollaborationStepStatePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.STEP_STATE,
                text=payload.state.value,
            ),
        )
    if type(payload) is CollaborationConfirmationPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.CONFIRMATION_DECISION,
                text=payload.decision.value,
            ),
        )
    if type(payload) is CollaborationExecutionResult:
        fields = [
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.EXECUTION_OUTCOME,
                text=payload.overall_outcome.value,
            )
        ]
        for ordinal, outcome in enumerate(payload.assignment_outcomes):
            fields.extend(
                (
                    CollaborationReadableField(
                        key=CollaborationReadableFieldKey.ASSIGNMENT_OUTCOME,
                        text=outcome.outcome.value,
                        assignment_ordinal=ordinal,
                    ),
                    CollaborationReadableField(
                        key=CollaborationReadableFieldKey.ASSIGNMENT_CALL_USAGE,
                        text=(
                            "submitted_physical_calls="
                            f"{outcome.submitted_physical_calls}"
                        ),
                        assignment_ordinal=ordinal,
                    ),
                )
            )
        fields.extend(
            (
                CollaborationReadableField(
                    key=CollaborationReadableFieldKey.SYNTHESIS_OUTCOME,
                    text=payload.synthesis_outcome.value,
                ),
                CollaborationReadableField(
                    key=CollaborationReadableFieldKey.SYNTHESIS_CALL_USAGE,
                    text=(
                        "submitted_physical_calls="
                        f"{payload.synthesis_submitted_physical_calls}"
                    ),
                ),
            )
        )
        return tuple(fields)
    if type(payload) is CollaborationInterruptionPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.INTERRUPTION_CONTENT,
                text=payload.content,
            ),
        )
    if type(payload) is CollaborationTerminalOutcomePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_SCOPE,
                text=payload.scope.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_OUTCOME,
                text=payload.outcome.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.TERMINAL_CALL_USAGE,
                text=(f"submitted_physical_calls={payload.submitted_physical_calls}"),
            ),
        )
    if type(payload) is CollaborationProviderCallPayload:
        return ()
    if type(payload) is CollaborationLeadOutcomeFailurePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.LEAD_OUTCOME_FAILURE,
                text=payload.code.value,
            ),
        )
    if type(payload) is ArtifactReviewCandidatePayload:
        return ()
    if type(payload) is ArtifactReviewSynthesisPayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_DECISION,
                text=payload.decision.value,
            ),
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_MESSAGE,
                text=payload.message,
            ),
        )
    if type(payload) is ArtifactReviewedProposal:
        return ()
    if type(payload) is ArtifactProposalStatePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_PROPOSAL_STATE,
                text=payload.state.value,
            ),
        )
    if type(payload) is ArtifactReviewFailurePayload:
        return (
            CollaborationReadableField(
                key=CollaborationReadableFieldKey.ARTIFACT_REVIEW_FAILURE,
                text=payload.code.value,
            ),
        )
    if type(payload) in {
        ArtifactApplicationRequestPayload,
        ArtifactApplicationDecisionPayload,
        ArtifactApplicationResultPayload,
        StoryCommitRequestPayload,
        StoryCommitDecisionPayload,
        StoryCommitResultPayload,
    }:
        return ()
    if type(payload) in {CollaborationLeadExplorationRequestPayload, CollaborationLeadExplorationResultPayload, CollaborationExplorationAdoptionPayload}:
        # Provisional history is inspectable, never automatic normal-context evidence.
        # Adopted bytes enter only through the explicitly captured input alias.
        return ()
    raise TypeError("unsupported collaboration payload")


def _validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value



def _validate_editorial_event_headers(events: tuple[CollaborationEventV2, ...]) -> None:
    requests = set()
    for event in events:
        if type(event.payload) is not CollaborationEditorialRecordPayload:
            continue
        record = event.payload.record
        if event.batch_size != 1:
            raise EditorialStateIntegrityError("USER editorial intake cannot co-batch workflow authority")
        if (record.session_id, record.conversation_id, record.event_id) != (event.session_id, event.conversation_id, event.event_id):
            raise EditorialStateIntegrityError("editorial record does not match its exact USER event")
        if record.request.request_id in requests:
            raise EditorialStateIntegrityError("editorial request identity repeats")
        requests.add(record.request.request_id)


def editorial_provenance_available(source: EditorialProvenanceRef, events: tuple[CollaborationEventV2, ...]) -> bool:
    matches = tuple(event for event in events if (event.session_id, event.conversation_id, event.event_id) ==
                    (source.session_id, source.conversation_id, source.event_id))
    if len(matches) != 1 or hashlib.sha256(encode_collaboration_event(matches[0])).hexdigest() != source.event_digest:
        return False
    if source.member_id is None:
        return True
    payload = matches[0].payload
    if type(payload) is CollaborationLeadExplorationResultPayload:
        return any(item.idea_id == source.member_id for item in payload.report.ideas)
    evidence = payload.evidence if type(payload) is CollaborationLeadJudgmentPayload else None
    return evidence is not None and any(receipt.evidence_id == source.member_id and
        receipt.acquisition is CollaborationEvidenceAcquisition.READ for receipt in evidence.receipts)


def project_editorial_state(session_id: str, events: tuple[CollaborationEventV2, ...]) -> EditorialStateProjection:
    """Derive exact USER chains and historical Apply associations across a session."""
    if any(event.session_id != session_id for event in events):
        raise EditorialStateIntegrityError("editorial source history crosses sessions")
    if not any(type(event.payload) is CollaborationEditorialRecordPayload for event in events):
        if any(type(event.payload) in {CollaborationProposal, LeadRecommendationRequestPayload} and event.payload.basis.editorial_refs for event in events):
            raise EditorialStateIntegrityError("editorial workflow capture has no USER history")
        return EditorialStateProjection(session_id, ())
    groups = {}
    for event in events:
        groups.setdefault(event.conversation_id, []).append(event)
    for group in groups.values():
        project_collaboration_history(tuple(group))
    records = tuple(event.payload.record for event in events if type(event.payload) is CollaborationEditorialRecordPayload)
    record_events = {event.payload.record.ref: event for event in events
                     if type(event.payload) is CollaborationEditorialRecordPayload}
    for admission in record_events.values():
        record = admission.payload.record
        for ref in tuple(dict.fromkeys(record.context_refs + record.request.relation_refs())):
            source = record_events.get(ref)
            if source is None or source.event_id == admission.event_id:
                raise EditorialStateIntegrityError("editorial context names an unavailable admission")
            if source.conversation_id == admission.conversation_id:
                if source.sequence >= admission.sequence:
                    raise EditorialStateIntegrityError("editorial context names a future local source")
            else:
                prefix = tuple(row for row in groups[source.conversation_id]
                               if row.sequence <= source.sequence)
                project_collaboration_history(prefix)
    state = EditorialStateProjection(session_id, records)
    by_ref = {record.ref: record for record in records}
    requests = [record.request.request_id for record in records]
    if len(set(requests)) != len(requests):
        raise EditorialStateIntegrityError("editorial request repeats across conversations")
    gaps = tuple((record.ref, source) for record in records for source in record.draft.provenance_refs
                 if not editorial_provenance_available(source, events))
    for event in events:
        if type(event.payload) not in {CollaborationProposal, LeadRecommendationRequestPayload}:
            continue
        for ref in event.payload.basis.editorial_refs:
            if ref not in by_ref or by_ref[ref].approval_state is not EditorialApprovalState.SELECTED:
                raise EditorialStateIntegrityError("workflow cites no exact selected USER editorial revision")
    attempts = []
    for request_event in sorted(events, key=lambda row: (row.conversation_id, row.sequence)):
        request = request_event.payload
        if type(request) is not ArtifactApplicationRequestPayload:
            continue
        reviewed = request.reviewed_proposal
        group = groups[request_event.conversation_id]
        consultations = tuple(row.payload for row in group
                              if type(row.payload) is CollaborationProposal
                              and (row.payload.proposal_id, row.payload.proposal_revision,
                                   row.payload.proposal_digest) == (
                                  reviewed.consultation_proposal_id,
                                  reviewed.consultation_proposal_revision,
                                  reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial application has no exact consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None:
            raise EditorialStateIntegrityError("editorial application has no exact task scope")
        results = tuple(row for row in group
                        if type(row.payload) is ArtifactApplicationResultPayload
                        and row.payload.confirmation_request_id == request.confirmation_request_id
                        and row.payload.application_request_digest == request.application_request_digest)
        if len(results) > 1:
            raise EditorialStateIntegrityError("editorial application repeats its exact result")
        result_event = results[0] if results else None
        result = None if result_event is None else result_event.payload
        decisions = tuple(row.payload for row in group
                          if type(row.payload) is ArtifactApplicationDecisionPayload
                          and row.payload.confirmation_request_event_id == request_event.event_id)
        if result is not None:
            phase = {ArtifactApplicationOutcomeKind.APPLIED: "applied",
                     ArtifactApplicationOutcomeKind.UNCHANGED: "unchanged",
                     ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN: "uncertain",
                     ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED: "recovery_required"}.get(
                         result.outcome, "failed")
        else:
            phase = "uncertain" if any(item.decision is ArtifactApplicationDecisionKind.APPLY
                                       for item in decisions) else "pending"
        request_ref = EditorialProvenanceRef(
            session_id, request_event.conversation_id, request_event.event_id,
            hashlib.sha256(encode_collaboration_event(request_event)).hexdigest())
        result_ref = None if result_event is None else EditorialProvenanceRef(
            session_id, result_event.conversation_id, result_event.event_id,
            hashlib.sha256(encode_collaboration_event(result_event)).hexdigest())
        paths = tuple(dict.fromkeys(path for operation in request.application_request.change_set.operations
                                    for path in (getattr(operation, "path", None),
                                                 getattr(operation, "source_path", None),
                                                 getattr(operation, "destination_path", None))
                                    if path is not None))
        affected = () if result is None or result.result is None else tuple(result.result.affected_paths)
        for ref in consultation.basis.editorial_refs:
            attempts.append(EditorialApplicationAttempt(
                ref, request_ref, result_ref, scope.task_id, scope.task_revision,
                reviewed.proposal_id, reviewed.proposal_digest,
                reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                request.application_request.provenance.application_id,
                request.application_request_digest, paths, affected, phase))
    facts = []
    for event in sorted(events, key=lambda item: (item.conversation_id, item.sequence)):
        result = event.payload
        if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED}:
            continue
        local = groups[event.conversation_id]
        reviewed = tuple(item.payload for item in local if type(item.payload) is ArtifactReviewedProposal
                         and item.payload.proposal_id == result.reviewed_proposal_id
                         and item.payload.proposal_digest == result.reviewed_proposal_digest)
        if len(reviewed) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed proposal")
        reviewed = reviewed[0]
        consultations = tuple(item.payload for item in local if type(item.payload) is CollaborationProposal
            and (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest) ==
            (reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision, reviewed.consultation_proposal_digest))
        if len(consultations) != 1:
            raise EditorialStateIntegrityError("editorial association has no exact reviewed consultation")
        consultation = consultations[0]
        if not consultation.basis.editorial_refs:
            continue
        scope = consultation.authoring_scope
        if scope is None or result.result is None:
            raise EditorialStateIntegrityError("editorial application lacks exact task or application facts")
        application_ref = EditorialProvenanceRef(event.session_id, event.conversation_id, event.event_id,
                                                hashlib.sha256(encode_collaboration_event(event)).hexdigest())
        for ref in consultation.basis.editorial_refs:
            facts.append(EditorialAppliedFact(ref, scope.task_id, scope.task_revision, reviewed.proposal_id,
                reviewed.proposal_digest, reviewed.candidate.candidate_id, reviewed.candidate.candidate_digest,
                application_ref, result.application_id, result.application_request_digest, result.outcome.value,
                tuple(result.result.affected_paths)))
    return EditorialStateProjection(session_id, records, tuple(facts), gaps, tuple(attempts))

__all__ = [
    "project_editorial_state",
    "editorial_provenance_available",
    "ArtifactObjective",
    "artifact_objective_context",
    "COLLABORATION_READABLE_CONTEXT_POLICY",
    "CONVERSATION_CONTEXT_POLICY",
    "CollaborationContextBootstrap",
    "CollaborationEventStore",
    "CollaborationHistoryProjection",
    "CollaborationNextAction",
    "CollaborationReadableContext",
    "CollaborationReadableContextPolicy",
    "CollaborationReadableField",
    "CollaborationReadableFieldKey",
    "CollaborationReadableRecord",
    "LeadDecisionResult",
    "LeadStepDecision",
    "LeadTurnResult",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ArtifactProposalResult",
    "ArtifactApplicationDecision",
    "ArtifactApplicationDecisionResult",
    "StoryCommitPreparationRequest",
    "StoryCommitDecisionRequest",
    "StoryCommitDecisionResult",
    "project_collaboration_history",
    "project_collaboration_readable_history",
    "select_bounded_collaboration_context",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
    'LeadTaskView',
    'LeadMaterialChoice',
    'LeadPublicResponseView',
    'LeadTaskDecisionResult',
]
