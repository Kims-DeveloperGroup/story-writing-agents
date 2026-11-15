"""Live recommendation preparation capability and result projection."""

from __future__ import annotations

from dataclasses import replace
from ..conversations import (
    ArtifactObjectiveNextStep,
    CollaborationConfirmationDecision,
    CollaborationEvent,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationHistoryProjection,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    LeadObjectiveAction,
    LeadOutcomeKind,
    LeadProviderOutcome,
    LeadRecommendationActionKind,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationState,
)
from ..errors import BackendProtocolError
from . import reply_contract_values
from ..lead_provider_contracts import PREPARATION_INPUT_IDS
from ..runtime_resources import collaboration_event_journal as event_journal
from ..specialist_routing import ReviewBasis, SpecialistAssignmentMode
from ..structured_output_validation import validation_failure
from typing import cast

__all__ = ('RecommendationCapability', 'active_recommendation_origin', 'bind_recommendation_preparation', 'validate_recommendation_preparation', 'recommendation_closure_draft',)


class RecommendationCapability:
    """One live decision/start chain; never reconstructed from durable history."""

    __slots__ = ("request_event", "decision_event", "start_event", "reference_operation_id", "_consumed")

    def __init__(self, request_event: CollaborationEvent,
                 decision_event: CollaborationEvent, start_event: CollaborationEvent) -> None:
        self.request_event = request_event
        self.decision_event = decision_event
        self.start_event = start_event
        self.reference_operation_id: str | None = None
        self._consumed = False

    def __reduce__(self) -> object:
        raise TypeError("recommendation capability cannot be serialized")

    def __repr__(self) -> str:
        return "_RecommendationCapability(<content hidden>)"

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("recommendation capability is consumed")
        self._consumed = True

def active_recommendation_origin(
    events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
    turn_id: str, step_id: str,
) -> tuple[CollaborationEvent, CollaborationEvent, CollaborationEvent] | None:
    """Use the independently validated public projection's exact active origin."""
    start = projection.current_recommendation_start
    request = projection.current_recommendation_request
    decision = projection.current_recommendation_decision
    if (projection.current_recommendation_state is not LeadRecommendationState.RUNNING
        or start is None or request is None or decision is None):
        return None
    by_id = {event.event_id: event for event in events}
    origin = (by_id[start.request_event_id], by_id[start.decision_event_id], by_id[start.start_event_id])
    if (origin[2].turn_id, origin[2].step_id) != (turn_id, step_id):
        return None
    return origin

def bind_recommendation_preparation(
    outcome: LeadProviderOutcome,
    preparation: RecommendationCapability,
) -> LeadProviderOutcome:
    """Restore only runtime-owned inputs under an already verified Confirm."""
    decision = preparation.decision_event.payload.decision
    if decision.kind is not CollaborationConfirmationDecision.CONFIRM or outcome.proposal is None:
        return outcome
    request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
    candidate_ids = {item.input_id for item in request.basis.inputs}
    return replace(outcome, proposal=replace(
        outcome.proposal,
        purpose=request.purpose,
        assignments=tuple(replace(
            assignment,
            input_ids=assignment.input_ids + tuple(
                alias for alias in PREPARATION_INPUT_IDS if alias not in assignment.input_ids
            ),
            review_basis=(ReviewBasis(
                tuple(alias for alias in assignment.input_ids if alias in candidate_ids),
                assignment.instruction,
            ) if assignment.mode is SpecialistAssignmentMode.REVIEW else None),
        ) for assignment in outcome.proposal.assignments),
    ))

def validate_recommendation_preparation(
    outcome: LeadProviderOutcome, preparation: RecommendationCapability,
) -> None:
    request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
    decision = cast(LeadRecommendationDecisionPayload, preparation.decision_event.payload).decision
    scope = outcome.authoring_scope
    expected_scope = request.authoring_scope
    expected_revision = (None if expected_scope is None else expected_scope.task_revision
                         + int(decision.kind is CollaborationConfirmationDecision.REVISE))
    if (scope is None or expected_scope is None
        or scope.intent is not expected_scope.intent
        or scope.activity is not expected_scope.activity
        or scope.task_id != expected_scope.task_id
        or scope.task_revision != expected_revision
        or not set(expected_scope.domains).issubset(scope.domains)
        or not set(expected_scope.target_paths).issubset(scope.target_paths)):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'preparation must retain its disclosed authoring task and scope',
            ),
            '$.authoring_scope',
            'preparation must retain its disclosed authoring task and scope',
        )
    if decision.kind is CollaborationConfirmationDecision.CONFIRM and (
        scope.target_paths != expected_scope.target_paths
        or scope.domains != expected_scope.domains
    ):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'preparation must preserve its exact confirmed authoring scope',
            ),
            '$.authoring_scope',
            'preparation must preserve its exact confirmed authoring scope',
        )
    if decision.kind is CollaborationConfirmationDecision.REVISE:
        if (outcome.kind is not LeadOutcomeKind.RECOMMENDATION
            or outcome.recommended_action is None or outcome.objective_intent is not None
            or outcome.proposal is not None or outcome.artifact_intent is not None):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('Revise permits only fresh recommendation planning'),
                '$.recommended_action',
                'Revise permits only fresh recommendation planning',
            )
        return
    if outcome.kind is LeadOutcomeKind.QUESTION:
        if outcome.objective_intent is not None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'blocked preparation cannot change its frozen objective',
                ),
                '$.objective_intent',
                'blocked preparation cannot change its frozen objective',
            )
        return
    if outcome.kind is not LeadOutcomeKind.PROPOSAL or outcome.recommended_action is not None:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'confirmed preparation requires its exact proposal or a material question',
            ),
            '$.proposal.assignments',
            'confirmed preparation requires its exact proposal or a material question',
        )
    intent = outcome.objective_intent
    if request.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT:
        if outcome.artifact_intent is None or outcome.proposal is not None or intent is None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'confirmed artifact preparation requires an exact candidate objective',
                ),
                '$.objective_intent',
                'confirmed artifact preparation requires an exact candidate objective',
            )
        if (intent.next_step is not ArtifactObjectiveNextStep.PREPARE
            or (request.objective_id is None and (
                intent.action is not LeadObjectiveAction.START
                or intent.target_paths != request.target_paths))
            or (request.objective_id is not None and (
                intent.action is not LeadObjectiveAction.CONTINUE or intent.objective_id != request.objective_id))):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('confirmed artifact objective or sources changed'),
                '$.objective_intent',
                'confirmed artifact objective or sources changed',
            )
    else:
        proposal = outcome.proposal
        if (proposal is None or outcome.artifact_intent is not None
            or proposal.purpose != request.purpose
            or tuple(item.agent_id for item in proposal.assignments) != request.specialist_ids):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'confirmed consultation purpose or specialist set changed',
                ),
                '$.proposal.assignments',
                'confirmed consultation purpose or specialist set changed',
            )
        expected_ids = {item.input_id for item in request.basis.inputs} | {
            "recommendation_request", "recommendation_decision",
        }
        if {item for assignment in proposal.assignments for item in assignment.input_ids} != expected_ids:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('confirmed consultation sources changed'),
                '$.proposal.assignments',
                'confirmed consultation sources changed',
            )
        if request.objective_id is None:
            if intent is not None:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('consultation cannot start an unrelated objective'),
                    '$.objective_intent',
                    'consultation cannot start an unrelated objective',
                )
        elif (intent is None or intent.action is not LeadObjectiveAction.CONTINUE
              or intent.objective_id != request.objective_id
              or intent.next_step is not ArtifactObjectiveNextStep.CONSULT):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('confirmed consultation objective changed'),
                '$.objective_intent',
                'confirmed consultation objective changed',
            )

def recommendation_closure_draft(
    lead_result: CollaborationEvent | CollaborationEventDraft, *,
    request_event: CollaborationEvent, decision_event: CollaborationEvent, start_event: CollaborationEvent,
) -> CollaborationEventDraft:
    request = cast(LeadRecommendationRequestPayload, request_event.payload)
    decision = cast(LeadRecommendationDecisionPayload, decision_event.payload).decision
    failed = type(lead_result.payload) is CollaborationLeadOutcomeFailurePayload
    interrupted = failed and lead_result.payload.code in {
        CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED,
        CollaborationLeadOutcomeFailureCode.CANCELLED,
        CollaborationLeadOutcomeFailureCode.INTERRUPTED,
    }
    superseded_scope = (type(lead_result.payload) is CollaborationLeadJudgmentPayload
        and lead_result.payload.kind is LeadOutcomeKind.RECOMMENDATION
        and decision.kind is CollaborationConfirmationDecision.CONFIRM)
    state = (LeadRecommendationState.INTERRUPTED if interrupted else
             LeadRecommendationState.FAILED if failed else
             LeadRecommendationState.SUPERSEDED if decision.kind is CollaborationConfirmationDecision.REVISE or superseded_scope
             else LeadRecommendationState.COMPLETED)
    return event_journal.draft(
        session_id=lead_result.session_id, conversation_id=lead_result.conversation_id,
        turn_id=lead_result.turn_id, step_id=lead_result.step_id,
        kind=CollaborationEventKind.LEAD_RECOMMENDATION_RESULT,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=LeadRecommendationResultPayload(
            request_event_id=request.request_event_id, request_id=request.request_id,
            action_id=request.action_id, action_revision=request.action_revision,
            action_digest=request.action_digest, state=state,
            message=("Preparation failed; another attempt needs a fresh request and Confirm." if failed
                     else "The confirmed preparation is complete; subsequent actions have separate decisions."),
            decision_event_id=decision_event.event_id,
            start_event_id=start_event.event_id,
            lead_result_event_id=lead_result.event_id,
        ),
    )
