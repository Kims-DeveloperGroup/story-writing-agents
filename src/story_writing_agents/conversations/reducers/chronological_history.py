"""Recursive chronological collaboration history interpretation."""

from __future__ import annotations

from dataclasses import replace

from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.authoring_records import (
    CollaborationEditorialRecordPayload,
    CollaborationExplorationAdoptionPayload,
    CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
)
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationInterruptionPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
)
from ..event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
)
from ..event_contracts.story_commits import (
    StoryCommitDecisionPayload,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..event_vocabulary import (
    ArtifactProposalState,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationTerminalScope,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from ..events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadGroupTracePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    authoring_capture_identity,
    confirmed_authoring_proposals,
    validate_authoring_capture_pin,
)
from .artifact_objective_history import (
    project_artifact_objective_history as _project_artifact_objective_history,
)
from .synchronization_repair_history import (
    has_started_synchronization_repair,
    has_verified_synchronization_review_after,
)
from .artifact_operation_history import (
    artifact_operation_next_action as _artifact_operation_next_action,
    project_artifact_operation_history as _project_artifact_operation_history,
)
from .artifact_review_history import (
    project_artifact_review_history as _project_artifact_review_history,
)
from .collaboration_transitions import (
    next_action as _next_action,
    validate_collaboration_transition,
)
from .document_plan_views import (
    document_lineage_cost,
    project_document_plan_views,
)
from .editorial_event_evidence import (
    validate_editorial_event_headers as _validate_editorial_event_headers,
)
from .synchronization_history import project_synchronization_history
from ..event_contracts.synchronization import SynchronizationPayload
from .execution_batch_history import (
    validate_collaboration_co_batch as _validate_collaboration_co_batch,
    validate_collaboration_execution_result as _validate_collaboration_execution_result,
)
from .exploration_history import (
    validate_exploration_history as _validate_exploration_history,
)
from .history_projection import CollaborationHistoryProjection
from .lead_control_gaps import (
    closed_pure_discussion_control_gap_is_safe as _closed_pure_discussion_control_gap_is_safe,
)
from .premise_history import validate_premise_history as _validate_premise_history
from .proposal_keys import (
    payload_proposal_key as _payload_proposal_key,
    proposal_key as _proposal_key,
)
from .recommendation_history import (
    is_fresh_drafting_preparation as _is_fresh_drafting_preparation,
    project_lead_recommendation_history as _project_lead_recommendation_history,
    recommendation_origin as _recommendation_origin,
)
from .stored_batches import (
    retained_collaboration_batch as _retained_collaboration_batch,
    validate_stored_collaboration_batches as _validate_stored_collaboration_batches,
)
from .task_context import project_task_context as _project_task_context
from ..views.collaboration_history_values import CollaborationNextAction
from ...lead_premise_decisions import PremiseDecisionAction
from ...lead_task_transitions import LeadTaskAction
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    required_authoring_specialists,
)


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
    projection = _project_validated_collaboration_history(events)
    synchronization = project_synchronization_history((events,), session_complete=False)
    if synchronization.assessments or synchronization.obligations.heads or not synchronization.complete:
        projection = replace(projection, synchronization=synchronization)
    if any(type(event.payload) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload,
            ArtifactObjectivePayloadV2, CollaborationProposal} and event.payload.document_plan is not None for event in events):
        return replace(projection, document_plans=project_document_plan_views(events, projection.artifact_objectives))
    return projection


def _lead_abandonment_is_safe(
    events: tuple[CollaborationEventV2, ...], turn_id: str, step_id: str,
    *, control_history: tuple[CollaborationEventV2, ...],
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
        _validate_premise_history(events, control_history=control_history)
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
        return _project_task_context(events, base, _source_history=_control_history or events)
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
    premise_state = _validate_premise_history(events, control_history=_control_history or events)
    artifact_candidates, artifact_states, artifact_reviewed = (
        _project_artifact_review_history(events)
    )
    application_request, application_result, commit_request, commit_result = (
        _project_artifact_operation_history(events, _control_history=_control_history)
    )
    recommendation_request, recommendation_decision, recommendation_start, recommendation_result, recommendation_state = (
        _project_lead_recommendation_history(events, _task_origins, control_history=_control_history or events)
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
            if type(event.payload) is CollaborationLeadGroupTracePayload:
                continue
            current_step_id = event.step_id
            payload = event.payload
            proposal = proposals.get(event.step_id)

            if type(payload) is CollaborationProposal:
                if payload.document_node is not None:
                    causal = tuple(item for item in events if item.sequence <= event.sequence)
                    inherited_calls, inherited_rounds, _ = document_lineage_cost(
                        causal, payload.document_node, current_step_id=event.step_id)
                    if payload.document_node.budget_lineage_ref.conversation_id != event.conversation_id:
                        # The session owner requalifies foreign source records before dispatch.
                        # This owner-local reducer retains the exact disclosed source counters.
                        inherited_calls = max(inherited_calls, payload.budget.physical_calls_spent)
                        inherited_rounds = max(inherited_rounds, payload.budget.rounds_spent)
                    spent_calls[event.step_id] = inherited_calls
                    running_attempts[event.step_id] = inherited_rounds
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
                    control_history=_control_history or events,
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
                        accounting=None,
                        validation_details=(),
                usage=None,
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
                    control_history=_control_history or events,
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
                SynchronizationPayload,
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
        and _lead_abandonment_is_safe(events, current_turn_id, current_step_id, control_history=_control_history or events)
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
    repair_history = _control_history or events
    control_tail = next((event for event in reversed(events) if type(event.payload) is not SynchronizationPayload), events[-1])
    operation_next_action = _artifact_operation_next_action(
        control_tail,
        application_request, application_result, commit_request, commit_result,
        repair_continuation_started=has_started_synchronization_repair(repair_history, application_result),
        repair_review_finished=has_verified_synchronization_review_after(
            repair_history, application_result, control_tail=control_tail,
        ),
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
    ), _source_history=_control_history or events)
