"""Exact authoring-objective facts, source capture and document-window policies."""

from ..conversations import (
    ArtifactDraftSource, ArtifactObjective, ArtifactObjectiveCandidateBinding,
    ArtifactObjectiveFacts, ArtifactObjectiveFactsV2, ArtifactObjectiveNextStep,
    ArtifactObjectivePayload, ArtifactObjectivePayloadV2, ArtifactObjectiveState,
)
from ..writing_artifacts import ArtifactRenameOperation
from ..artifact_proposals import ArtifactReviewCandidate
from ..specialist_routing import AuthoringActivity, AuthoringIntent
from ..conversations import (
    CollaborationActorKind, CollaborationEvent, CollaborationEventDraft, CollaborationEventKind,
    CollaborationHistoryProjection, CollaborationMessagePayload,
)
from ..document_plan import DocumentNodeBinding, DocumentPlan, DocumentPlanView
from ..conversations import (
    LeadObjectiveAction, LeadObjectiveIntent, LeadOutcomeKind, LeadProviderOutcome,
    LeadRecommendationRequestPayload,
)
from .recommendation_preparation import RecommendationCapability
from dataclasses import asdict
from . import authoring_authority
from typing import cast
from ..document_plan import document_value_digest
from ..runtime_resources import collaboration_event_journal as event_journal
from . import proposal_basis_values
from dataclasses import replace
from . import reply_contract_values
from ..structured_output_validation import validation_failure

__all__ = (
    'objective_goal_exists',
    'prepared_document_objective',
    'ordinary_objective_outcome',
    'objective_facts',
    'objective_capture_inputs',
    'objective_document_window',
    'objective_drafts_for_values',
)


def objective_goal_exists(
    plan_override: DocumentPlan | None,
    events: tuple[CollaborationEvent, ...],
    *,
    session_events: tuple[CollaborationEvent, ...] | None = None,
) -> bool:
    goal_exists = False
    if plan_override is not None and plan_override.goal_objective_id is not None:
        goals = tuple(event.payload.facts for event in (events if session_events is None else session_events)
            if type(event.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
            and event.payload.facts.objective_id == plan_override.goal_objective_id)
        if goals:
            if (any(facts != goals[0] for facts in goals)
                    or document_value_digest(asdict(goals[0])) != plan_override.goal_facts_digest):
                raise ValueError("document plan differs from its existing exact session goal")
            goal_exists = True
    return goal_exists


def prepared_document_objective(
    outcome: LeadProviderOutcome,
    judgment: CollaborationEventDraft,
    facts: ArtifactObjectiveFacts | ArtifactObjectiveFactsV2,
    plan_override: DocumentPlan,
    *,
    candidate_event: CollaborationEventDraft | None = None,
    preparation: RecommendationCapability | None = None,
) -> tuple[CollaborationEventDraft, ...]:
    if document_value_digest(asdict(facts)) != plan_override.goal_facts_digest:
        raise ValueError("prepared document goal differs from its exact source capture")
    binding = None
    if candidate_event is not None:
        candidate = candidate_event.payload.candidate
        paths = {path for operation in candidate.change_set.operations for path in
            ((operation.source_path, operation.destination_path) if type(operation) is ArtifactRenameOperation else (operation.path,))}
        if paths == set(facts.target_paths):
            binding = ArtifactObjectiveCandidateBinding(candidate_event.event_id, candidate.candidate_id,
                candidate.candidate_revision, candidate.candidate_digest)
    next_step = (ArtifactObjectiveNextStep.PREPARE if binding is not None else
        ArtifactObjectiveNextStep.CONSULT if outcome.kind is LeadOutcomeKind.PROPOSAL else ArtifactObjectiveNextStep.CLARIFY)
    selected_sources = () if preparation is None else preparation.request_event.payload.draft_sources
    return (event_journal.draft(session_id=judgment.session_id, conversation_id=judgment.conversation_id,
        turn_id=judgment.turn_id, step_id=judgment.step_id, kind=CollaborationEventKind.ARTIFACT_OBJECTIVE,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=(ArtifactObjectivePayloadV2 if type(facts) is ArtifactObjectiveFactsV2 else ArtifactObjectivePayload)(
            facts, LeadObjectiveAction.START, None, judgment.event_id, next_step, selected_sources, binding,
            document_plan=plan_override, document_node=outcome.document_node)),)


def ordinary_objective_outcome(
    outcome: LeadProviderOutcome,
    projection: CollaborationHistoryProjection,
    *,
    preparation: RecommendationCapability | None = None,
) -> LeadProviderOutcome | None:
    intent = outcome.objective_intent
    scope = outcome.authoring_scope
    if intent is None and scope is not None and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} and scope.target_paths:
        if outcome.document_node is not None and preparation is None:
            return None
        prior = tuple(item for item in projection.artifact_objectives
            if item.state is ArtifactObjectiveState.OPEN and item.facts.target_paths == scope.target_paths)
        next_step = ArtifactObjectiveNextStep.CLARIFY if outcome.kind is LeadOutcomeKind.QUESTION else ArtifactObjectiveNextStep.CONSULT
        if outcome.kind not in {LeadOutcomeKind.QUESTION, LeadOutcomeKind.PROPOSAL}:
            return None
        intent = (LeadObjectiveIntent(LeadObjectiveAction.CONTINUE, objective_id=prior[0].facts.objective_id,
            next_step=next_step) if len(prior) == 1 else LeadObjectiveIntent(LeadObjectiveAction.START,
            request="Retain the exact original USER goal.", target_paths=scope.target_paths, next_step=next_step))
        outcome = replace(outcome, objective_intent=intent)
    if intent is None:
        return None
    return outcome


def objective_facts(
    intent: LeadObjectiveIntent,
    previous: ArtifactObjective | None,
    projection: CollaborationHistoryProjection,
    events: tuple[CollaborationEvent, ...],
    judgment: CollaborationEventDraft,
    *,
    preparation: RecommendationCapability | None = None,
) -> ArtifactObjectiveFacts | ArtifactObjectiveFactsV2:
    if previous is None:
        origin = next((
            event for event in reversed(events)
            if event.turn_id == judgment.turn_id
            and event.sender.kind is CollaborationActorKind.USER
            and event.recipient.kind is CollaborationActorKind.LEAD
            and type(event.payload) is CollaborationMessagePayload
        ), None)
        if preparation is not None:
            request = cast(LeadRecommendationRequestPayload, preparation.request_event.payload)
            facts = ArtifactObjectiveFactsV2(
                objective_id=event_journal.new_id("obj"), origin_conversation_id=judgment.conversation_id,
                origin_recommendation_request_event_id=request.request_event_id,
                origin_recommendation_decision_event_id=preparation.decision_event.event_id,
                request=request.instruction, target_paths=request.objective_target_paths,
                offered_draft_sources=projection.offered_draft_sources,
            )
        elif origin is None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('new objective lacks its exact origin user event'),
                '$.objective_intent',
                'new objective lacks its exact origin user event',
            )
        else:
            facts = ArtifactObjectiveFacts(
                objective_id=event_journal.new_id("obj"),
                origin_conversation_id=judgment.conversation_id,
                origin_user_event_id=origin.event_id,
                request=origin.payload.content, target_paths=intent.target_paths,
                offered_draft_sources=projection.offered_draft_sources,
            )
    else:
        facts = previous.facts
    return facts


def objective_capture_inputs(
    outcome: LeadProviderOutcome,
    projection: CollaborationHistoryProjection,
    events: tuple[CollaborationEvent, ...],
    previous: ArtifactObjective | None,
    candidate_event: CollaborationEventDraft | None,
    candidate: ArtifactReviewCandidate | None,
    *,
    preparation: RecommendationCapability | None = None,
) -> tuple[tuple[ArtifactDraftSource, ...], ArtifactObjectiveCandidateBinding | None]:
    offered = {item.event_id: item for item in projection.offered_draft_sources}
    selected = tuple(
        offered[event_id]
        for event_id in authoring_authority.resolved_objective_source_ids(
            outcome,
            projection,
            events,
            previous,
        )
    )
    if preparation is not None:
        selected = preparation.request_event.payload.draft_sources
    binding = None
    if candidate_event is not None:
        assert candidate is not None
        binding = ArtifactObjectiveCandidateBinding(
            candidate_event.event_id, candidate.candidate_id,
            candidate.candidate_revision, candidate.candidate_digest,
        )
    return selected, binding


def objective_document_window(
    views: tuple[DocumentPlanView, ...],
    document_node: DocumentNodeBinding,
    facts: ArtifactObjectiveFacts | ArtifactObjectiveFactsV2,
) -> DocumentPlan:
    current_plans = tuple(view.plan for view in views
        if view.plan.ref == document_node.plan_ref and view.plan.goal_objective_id == facts.objective_id)
    if len(current_plans) != 1:
        raise ValueError("objective continuation requires its exact active document window")
    plan = current_plans[0]
    return plan


def objective_drafts_for_values(
    outcome: LeadProviderOutcome,
    intent: LeadObjectiveIntent,
    previous: ArtifactObjective | None,
    facts: ArtifactObjectiveFacts | ArtifactObjectiveFactsV2,
    selected: tuple[ArtifactDraftSource, ...],
    binding: ArtifactObjectiveCandidateBinding | None,
    plan: DocumentPlan | None,
    judgment: CollaborationEventDraft,
) -> tuple[CollaborationEventDraft, ...]:
    if (plan is None and outcome.authoring_scope is not None and outcome.document_node is None
            and outcome.authoring_scope.intent is AuthoringIntent.AUTHORING
            and outcome.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
        plan = proposal_basis_values.build_document_plan(facts, outcome.authoring_scope,
            tuple(item.evidence_id for item in judgment.payload.evidence.receipts), outcome.core_document_readiness)
    return (event_journal.draft(
        session_id=judgment.session_id, conversation_id=judgment.conversation_id,
        turn_id=judgment.turn_id, step_id=judgment.step_id,
        kind=CollaborationEventKind.ARTIFACT_OBJECTIVE,
        sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
        payload=(ArtifactObjectivePayloadV2 if type(facts) is ArtifactObjectiveFactsV2
                 else ArtifactObjectivePayload)(
            facts=facts, action=intent.action,
            previous_objective_event_id=None if previous is None else previous.latest_event_id,
            lead_result_event_id=judgment.event_id, next_step=intent.next_step,
            draft_sources=selected, candidate_binding=binding, document_plan=plan, document_node=outcome.document_node,
        ),
    ),)
