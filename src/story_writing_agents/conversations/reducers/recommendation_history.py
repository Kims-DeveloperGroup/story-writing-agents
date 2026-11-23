"""Project Lead action recommendation history."""

from __future__ import annotations

import hashlib
import json

from dataclasses import asdict

from ...authoring_task_lifecycle import task_identity_for_origin
from ..event_contracts.artifact_apply import ArtifactApplicationResultPayload
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewSynthesisPayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.authoring_records import (
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
)
from ..event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
)
from ..event_contracts.proposal_inputs import CollaborationInputRevision
from ..event_vocabulary import (
    ArtifactApplicationOutcomeKind,
    CollaborationActorKind,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationStepState,
    LeadObjectiveAction,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from ..events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventDraft,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationActionKind,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    authoring_capture_identity,
    confirmed_authoring_proposals,
)
from ...lead_task_transitions import (
    LeadTaskAction,
    LeadTaskTransitionReceipt,
)
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
    authoring_source_scope_is_eligible,
)
from ...writing_artifacts import ArtifactRenameOperation


def recommendation_key(payload: object) -> tuple[str, str, str, int, str]:
    if type(payload) is LeadRecommendationDecisionPayload:
        payload = payload.decision
    return (
        payload.request_event_id, payload.request_id, payload.action_id,
        payload.action_revision, payload.action_digest,
    )


def recommendation_origin(
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
        or recommendation_key(start.payload) != recommendation_key(request.payload)
        or recommendation_key(decision.payload) != recommendation_key(request.payload)
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


def is_new_task_recommendation_source(
    source: CollaborationEventDraft | CollaborationEventV2,
    scope: AuthoringScope,
    events: tuple[CollaborationEventV2, ...],
) -> bool:
    """Match a Lead judgment to its unique earlier USER NEW_TASK control."""
    if (
        type(scope) is not AuthoringScope
        or type(source.payload) is not CollaborationLeadJudgmentPayload
        or source.payload.authoring_scope != scope
        or scope.task_revision != 1
    ):
        return False
    if type(source) is CollaborationEventV2:
        events = tuple(event for event in events if event.sequence < source.sequence)
    owner_step = (source.session_id, source.conversation_id, source.step_id)
    transitions = tuple(event for event in events
                        if type(event.payload) is CollaborationLeadTaskTransitionPayload
                        and type(event.payload.receipt) is LeadTaskTransitionReceipt
                        and event.payload.receipt.origin_kind == "task_decision"
                        and event.payload.receipt.action is LeadTaskAction.NEW_TASK
                        and (event.payload.receipt.target_task_id,
                             event.payload.receipt.target_task_revision)
                        == (scope.task_id, scope.task_revision)
                        and event.payload.receipt.planning_step_id == source.step_id
                        and event.sender.kind is CollaborationActorKind.RUNTIME
                        and (event.session_id, event.conversation_id, event.step_id) == owner_step)
    if len(transitions) != 1:
        return False
    transition = transitions[0]
    receipt = transition.payload.receipt
    if receipt.source_task_ref is not None and receipt.source_task_ref.task_id == scope.task_id:
        return False
    origins = tuple(event for event in events if event.event_id == receipt.origin_event_id)
    if len(origins) != 1:
        return False
    origin = origins[0]
    return bool(
        type(origin.payload) is CollaborationLeadTaskDecisionPayload
        and origin.sender.kind is CollaborationActorKind.USER
        and (origin.session_id, origin.conversation_id, origin.step_id) == owner_step
        and origin.batch_id == transition.batch_id
        and origin.payload.decision.action is receipt.action
        and origin.payload.decision.task_ref == receipt.source_task_ref
    )


def is_fresh_drafting_preparation(request, source, events, *, control_history: tuple[CollaborationEventV2, ...]) -> bool:
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
    if (previous is not None and scope.task_id == previous.task_id
            and scope.task_revision == previous.task_revision
            and not confirmed_authoring_proposals(previous, prior)):
        return True
    return (
        (scope.task_id == task_identity_for_origin(source.step_id) and scope.task_revision == 1)
        or is_new_task_recommendation_source(source, scope, control_history)
        if previous is None
        else scope.task_revision == previous.task_revision + 1
    )


def consultation_objective_origin_matches(request, event, judgment, events) -> bool:
    """Qualify the same confirmed consultation objective scope in every replay gate."""
    payload = event.payload
    if (type(payload) is not ArtifactObjectivePayloadV2
            or payload.action is not LeadObjectiveAction.START
            or request.kind is not LeadRecommendationActionKind.PREPARE_CONSULTATION
            or request.objective_id is not None
            or judgment is None or type(judgment.payload) is not CollaborationLeadJudgmentPayload):
        return False
    scope = judgment.payload.authoring_scope
    expected = request.authoring_scope
    if (scope is None or expected is None
            or scope.intent is not AuthoringIntent.AUTHORING
            or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
            or (scope.intent, scope.activity, scope.task_id, scope.task_revision,
                scope.domains, scope.target_paths)
            != (expected.intent, expected.activity, expected.task_id, expected.task_revision,
                expected.domains, expected.target_paths)
            or payload.lead_result_event_id != judgment.event_id
            or judgment.sequence >= event.sequence
            or (judgment.session_id, judgment.conversation_id, judgment.turn_id,
                judgment.step_id, judgment.batch_id)
            != (event.session_id, event.conversation_id, event.turn_id,
                event.step_id, event.batch_id)
            or payload.facts.origin_recommendation_request_event_id != request.request_event_id
            or payload.facts.request != request.instruction
            or payload.facts.target_paths != request.objective_target_paths):
        return False
    if payload.document_plan is not None:
        return True
    node = payload.document_node
    original = request.document_node
    if (node is None or original is None or judgment.payload.document_node != node
            or (node.plan_ref, node.node_id) != (original.plan_ref, original.node_id)
            or (node.task_ref.session_id, node.task_ref.conversation_id,
                node.task_ref.task_id, node.task_ref.task_revision)
            != (original.task_ref.session_id, original.task_ref.conversation_id,
                original.task_ref.task_id, original.task_ref.task_revision)
            or (node.task_ref.task_id, node.task_ref.task_revision)
            != (scope.task_id, scope.task_revision)
            or len(scope.target_paths) != 1 or payload.facts.target_paths != scope.target_paths):
        return False
    return any(
        value.document_plan is not None and value.document_plan.ref == node.plan_ref
        and any(item.node_id == node.node_id and item.target_path == scope.target_paths[0]
                for item in value.document_plan.nodes)
        for prior in events if prior.sequence <= event.sequence
        for value in (prior.payload,)
        if type(value) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload,
                          ArtifactObjectivePayloadV2, CollaborationProposal}
    )


def project_lead_recommendation_history(
    events: tuple[CollaborationEventV2, ...],
    task_origins: tuple[CollaborationEventV2, ...] = (),
    *, control_history: tuple[CollaborationEventV2, ...],
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
            fresh_consultation = is_fresh_drafting_preparation(payload, source, tuple(by_id.values()), control_history=control_history)
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
                or recommendation_key(payload) != recommendation_key(request)
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
                or recommendation_origin((*by_id.values(), event), event.turn_id, event.step_id) is None
                or event.batch_ordinal != event.batch_size - 1
            ):
                raise ValueError("recommendation start lacks one exact structured decision")
            attempt_ids.add(payload.execution_attempt_id)
            start, state = payload, LeadRecommendationState.RUNNING
            preparation_calls = set()
            preparation_invocation = None
        elif type(payload) is LeadRecommendationResultPayload:
            if request is None or request_event is None or result is not None or recommendation_key(payload) != recommendation_key(request):
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
                        replanned = (decision.decision.kind is CollaborationConfirmationDecision.CONFIRM
                            and lead.payload.kind is LeadOutcomeKind.RECOMMENDATION)
                        expected = (LeadRecommendationState.SUPERSEDED
                                    if decision.decision.kind is CollaborationConfirmationDecision.REVISE or replanned
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
                        elif replanned:
                            replacements = tuple(item.payload for item in events
                                if item.batch_id == event.batch_id and type(item.payload) is LeadRecommendationRequestPayload
                                and item.payload.source_recommendation_event_id == lead.event_id)
                            old_scope = request.authoring_scope
                            new_scope = lead.payload.authoring_scope
                            if (len(replacements) != 1 or replacements[0].action_id == request.action_id
                                    or old_scope is None or new_scope is None
                                    or new_scope.task_id != old_scope.task_id
                                    or new_scope.task_revision != old_scope.task_revision + 1):
                                raise ValueError("changed preparation requires a fresh separately confirmed recommendation")
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
                        (request.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                                and not consultation_objective_origin_matches(
                                    request, event, by_id.get(payload.lead_result_event_id), tuple(by_id.values())))
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
