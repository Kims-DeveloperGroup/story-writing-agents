"""Collaboration results, including existing binding and generation-pin ownership."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)
from .reply_trace import ReplyTrace
from .workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactGenerationPin,
)
from .conversations import (
    ArtifactReviewSynthesisPayload,
    CollaborationLeadSynthesisPayload,
    LeadRecommendedAction,
)
from .agent_context.invocation.provider import context_provider_failure_facts
from .conversations import (
    ArtifactProposalResult,
    ArtifactProposalState,
    CollaborationEvent,
    CollaborationEventKind,
    CollaborationHistoryProjection,
    CollaborationNextAction,
    CollaborationStepState,
    LeadOutcomeKind,
    LeadTurnResult,
    StoryCommitPreparationRequest,
)
from .errors import BackendProtocolError
from .runtime_resources import collaboration_event_journal as event_journal


@dataclass(frozen=True, slots=True)
class CollaborationInvocationResult:
    """One decoded public value plus provider-neutral terminal call facts."""

    value: object | None
    submitted_physical_calls: int
    failure: BaseException | None = None
    submission_uncertain: bool = False
    artifact_binding: SessionArtifactBinding | None = None
    generation_pin: SessionArtifactGenerationPin | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    trace: ReplyTrace | None = None
    pending_validation: tuple[str, tuple[tuple[str, tuple[str, ...]], ...]] | None = None
    provider_invocation_id: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.trace is not None and type(self.trace) is not ReplyTrace:
            raise TypeError("collaboration invocation trace is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactSynthesisResult:
    """One provider result decoded into consultation and artifact authorities."""

    collaboration: CollaborationLeadSynthesisPayload
    artifact: ArtifactReviewSynthesisPayload


@dataclass(frozen=True, slots=True)
class LeadSynthesisResult:
    collaboration: CollaborationLeadSynthesisPayload
    recommended_action: LeadRecommendedAction | None


def blocking_lead_result(
    session_id: str,
    conversation_id: str,
    state: CollaborationStepState,
    message: str,
    *,
    step_id: str | None = None,
    turn_id: str | None = None,
) -> LeadTurnResult:
    next_action = {
        CollaborationStepState.OUTCOME_UNKNOWN: (
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        ),
        CollaborationStepState.RECOVERY_REQUIRED: (
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
        ),
    }.get(state, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
    result_state = (
        state
        if state
        in {
            CollaborationStepState.OUTCOME_UNKNOWN,
            CollaborationStepState.RECOVERY_REQUIRED,
        }
        else CollaborationStepState.RECOVERY_REQUIRED
    )
    return LeadTurnResult(
        session_id,
        conversation_id,
        turn_id or event_journal.new_id("t"),
        step_id or event_journal.new_id("s"),
        LeadOutcomeKind.RECOMMENDATION,
        message,
        None,
        result_state,
        None,
        next_action,
    )


def joined_invocation_result(item: object) -> CollaborationInvocationResult:
    """Preserve safely knowable facts from one joined assignment task."""

    if type(item) is CollaborationInvocationResult:
        return item
    if not isinstance(item, BaseException):
        item = BackendProtocolError(
            "specialist task returned an invalid invocation result"
        )
    facts = context_provider_failure_facts(item)
    return CollaborationInvocationResult(
        None,
        len(facts.submitted_accountings),
        item,
        facts.submission_uncertain,
    )


def pending_artifact_operation_result(
    session_id: str,
    conversation_id: str,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
) -> LeadTurnResult | None:
    if projection.current_artifact_application_request is None:
        return None
    action = projection.next_safe_action
    if action in {
        CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
    }:
        return blocking_lead_result(
            session_id, conversation_id,
            (CollaborationStepState.RECOVERY_REQUIRED
             if action is CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
             else CollaborationStepState.OUTCOME_UNKNOWN),
            "An artifact or Commit boundary needs inspection before ordinary "
            "work can continue. Its existing records will not be replayed.",
            turn_id=projection.current_turn_id, step_id=projection.current_step_id,
        )
    if action is CollaborationNextAction.SUBMIT_COMMIT_DECISION:
        return LeadTurnResult(
            session_id, conversation_id, events[-1].turn_id, events[-1].step_id,
            LeadOutcomeKind.RECOMMENDATION,
            "The exact local Commit request is still awaiting your structured decision.",
            None, CollaborationStepState.COMPLETED, None, action,
            story_commit_request=projection.current_story_commit_request,
        )
    if action is CollaborationNextAction.REPREPARE_COMMIT_REQUEST:
        result = projection.current_artifact_application_result
        result_events = tuple(
            event
            for event in events
            if event.kind is CollaborationEventKind.ARTIFACT_APPLICATION_RESULT
            and event.payload == result
            and event.step_id == events[-1].step_id
        )
        if result is None or len(result_events) != 1:
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "The exact Apply result is unavailable for Commit preparation.",
                turn_id=projection.current_turn_id,
                step_id=projection.current_step_id,
            )
        return LeadTurnResult(
            session_id,
            conversation_id,
            events[-1].turn_id,
            events[-1].step_id,
            LeadOutcomeKind.RECOMMENDATION,
            "The Apply result is known, but there is no current Commit "
            "request. Reprepare one exact request before Commit.",
            None,
            CollaborationStepState.COMPLETED,
            None,
            action,
            story_commit_preparation_request=StoryCommitPreparationRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                step_id=events[-1].step_id,
                application_result_event_id=result_events[0].event_id,
                application_id=result.application_id,
                application_request_digest=result.application_request_digest,
                tail_event_id=events[-1].event_id,
            ),
        )
    if action is CollaborationNextAction.SUBMIT_APPLICATION_DECISION:
        request = projection.current_artifact_application_request
        candidate = projection.current_artifact_candidate
        if candidate is None:
            return blocking_lead_result(
                session_id, conversation_id, CollaborationStepState.RECOVERY_REQUIRED,
                "The pending Apply request has no exact current candidate.",
                turn_id=projection.current_turn_id, step_id=projection.current_step_id,
            )
        return LeadTurnResult(
            session_id, conversation_id, events[-1].turn_id, events[-1].step_id,
            LeadOutcomeKind.RECOMMENDATION,
            "The exact artifact Apply request is still awaiting your structured decision.",
            None, CollaborationStepState.COMPLETED, None, action,
            artifact_proposal=ArtifactProposalResult(
                ArtifactProposalState.REVIEWED, candidate, request.reviewed_proposal,
            ),
            artifact_application_request=request,
        )
    return None


def required_synthesis_result(item: object) -> CollaborationInvocationResult:
    """Fail closed when mandatory synthesis has no exact typed result."""

    if type(item) is not CollaborationInvocationResult:
        return CollaborationInvocationResult(
            None,
            0,
            BackendProtocolError(
                "mandatory consultation synthesis returned no invocation result"
            ),
        )
    if item.failure is None and type(item.value) not in {
        LeadSynthesisResult,
        ArtifactSynthesisResult,
    }:
        return CollaborationInvocationResult(
            None,
            item.submitted_physical_calls,
            BackendProtocolError(
                "mandatory consultation synthesis returned no typed payload"
            ),
            item.submission_uncertain,
            item.artifact_binding,
            item.generation_pin,
            provider_invocation_id=item.provider_invocation_id,
        )
    return item


def submitted_lead_outcome_unknown_result(
    session_id: str,
    conversation_id: str,
    *,
    step_id: str | None,
    turn_id: str | None,
) -> LeadTurnResult:
    """Return the stable non-replay result for one unmatched submitted call."""

    return blocking_lead_result(
        session_id,
        conversation_id,
        CollaborationStepState.OUTCOME_UNKNOWN,
        "The Lead provider call was submitted, but no reliable result was "
        "recorded. Do not retry or replay it; authoritative state is "
        "preserved. Inspect and resolve the outcome, or create and focus "
        "another conversation for intentionally distinct work.",
        step_id=step_id,
        turn_id=turn_id,
    )
