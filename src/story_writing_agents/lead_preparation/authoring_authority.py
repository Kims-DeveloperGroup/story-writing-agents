"""Pure authoring authority, review-floor, and call/reference reservation rules."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import cast

from ..agent_context.contracts.planning_inputs import MandatoryReferenceReservation
from ..agent_context.manifest import ContextAuthorityKind, ContextProvenance
from ..agent_context.sources.capture_values import writing_artifact_source_id
from ..artifact_proposals import ArtifactReviewCandidate
from ..conversations import (
    ArtifactApplicationOutcomeKind,
    ArtifactApplicationResultPayload,
    ArtifactObjectiveNextStep,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    ArtifactObjectiveState,
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    CollaborationAssignment,
    CollaborationAssignmentDispatchPayload,
    CollaborationEvent,
    CollaborationEvidenceAcquisition,
    CollaborationExecutionResult,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    LeadObjectiveAction,
    LeadProviderOutcome,
    LeadRecommendationRequestPayload,
    project_collaboration_history,
)
from ..conversations.context import ArtifactObjective, document_lineage_cost
from ..conversations.events import (
    authoring_capture_identity,
    authoring_scope_is_view,
    confirmation_task_bindings,
    confirmed_authoring_proposals,
    validate_authoring_capture_pin,
)
from ..errors import BackendProtocolError
from ..lead_provider_contracts import PREPARATION_INPUT_IDS, preparation_original_input_ids
from ..lead_task_transitions import LeadTaskAction
from ..runtime_resources.provider_call_accounting import LeadCallBudgetExceeded
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
    SpecialistAssignmentMode,
    SpecialistStagePlan,
    artifact_domain,
    required_authoring_domains,
    required_authoring_specialists,
    required_drafting_specialists,
    specialists_for_domains,
    stage_is_focused_review,
)
from ..structured_output_validation import validation_failure
from ..writing_artifacts import (
    ArtifactRenameOperation,
    ArtifactReviewDomain,
    parse_artifact_references,
)
from ..writing_artifacts.runtime_references import RuntimeReferenceOperation
from . import reply_contract_values


def artifact_candidate_for_proposal(
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
) -> ArtifactReviewCandidatePayload | None:
    matches = tuple(
        event.payload
        for event in events
        if type(event.payload) is ArtifactReviewCandidatePayload
        and event.payload.consultation_proposal_id == proposal.proposal_id
        and event.payload.consultation_proposal_revision
        == proposal.proposal_revision
        and event.payload.consultation_proposal_digest == proposal.proposal_digest
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise BackendProtocolError("artifact candidate identity is ambiguous")
    return cast(ArtifactReviewCandidatePayload, matches[0])


def validate_objective_routing(
    outcome: LeadProviderOutcome,
    projection: CollaborationHistoryProjection,
    events: tuple[CollaborationEvent, ...],
    task_events: tuple[CollaborationEvent, ...],
) -> None:
    """Correlate objective and selected draft scopes through durable history."""

    intent = outcome.objective_intent
    scope = outcome.authoring_scope
    if intent is None or intent.action is LeadObjectiveAction.CANCEL or scope is None:
        return
    previous = next((item for item in projection.artifact_objectives
                     if item.facts.objective_id == intent.objective_id), None)
    selected_ids = resolved_objective_source_ids(
        outcome,
        projection,
        events,
        previous,
    )
    by_id = {event.event_id: event for event in events}
    related_scopes: list[AuthoringScope] = []
    for event in events:
        payload = event.payload
        if (type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
            and payload.facts.objective_id == intent.objective_id):
            judgment = by_id.get(payload.lead_result_event_id)
            if judgment is not None and judgment.payload.authoring_scope is not None:
                related_scopes.append(judgment.payload.authoring_scope)
    for event_id in selected_ids:
        source = by_id.get(event_id)
        if source is not None and type(source.payload) in {
            CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload,
        } and source.payload.authoring_scope is not None:
            related_scopes.append(source.payload.authoring_scope)
    if scope.activity is AuthoringActivity.WORKFLOW:
        return
    if any(item.activity is not AuthoringActivity.WORKFLOW and item.task_id != scope.task_id
           for item in related_scopes):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'objective continuation and selected drafts require their existing authoring task',
            ),
            '$.objective_intent',
            'objective continuation and selected drafts require their existing authoring task',
        )
    current_task = authoring_tasks(task_events).get(scope.task_id)
    requires_drafting = any(
        item is not None and item.activity in {
            AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
        }
        for item in (scope, current_task, *related_scopes)
    )
    if (intent.next_step is ArtifactObjectiveNextStep.PREPARE
        and selected_ids and requires_drafting):
        # Fresh user-supplied candidates need exact review, while an
        # existing authoring task cannot shed its drafting obligations.
        validate_draft_provenance(scope, task_events)


def resolved_objective_source_ids(
    outcome: LeadProviderOutcome,
    projection: CollaborationHistoryProjection,
    events: tuple[CollaborationEvent, ...],
    previous: ArtifactObjective | None,
) -> tuple[str, ...]:
    """Resolve non-authoritative draft provenance from durable local history."""

    intent = outcome.objective_intent
    if intent is None:
        return ()
    if intent.draft_source_event_ids:
        return intent.draft_source_event_ids
    selected = [] if previous is None else [
        item.event_id for item in previous.draft_sources
    ]
    if intent.action is LeadObjectiveAction.CANCEL:
        return tuple(selected)
    scope = outcome.authoring_scope
    if scope is None:
        return tuple(selected)
    by_id = {event.event_id: event for event in events}
    for source in projection.offered_draft_sources:
        event = by_id.get(source.event_id)
        if (
            source.event_id not in selected
            and event is not None
            and type(event.payload) in {
                CollaborationLeadJudgmentPayload,
                CollaborationLeadSynthesisPayload,
            }
            and event.payload.authoring_scope is not None
            and event.payload.authoring_scope.task_id == scope.task_id
        ):
            selected.append(source.event_id)
    return tuple(selected)


def validate_objective_selection(
    outcome: LeadProviderOutcome,
    projection: CollaborationHistoryProjection,
    candidate: ArtifactReviewCandidate | None,
) -> None:
    intent = outcome.objective_intent
    if intent is None:
        return
    objective = next((
        item for item in projection.artifact_objectives
        if item.facts.objective_id == intent.objective_id
    ), None)
    if intent.action is not LeadObjectiveAction.START and (
        objective is None or objective.state is not ArtifactObjectiveState.OPEN
        or objective.facts.origin_conversation_id != projection.conversation_id
    ):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('objective ID is not an offered OPEN owner objective'),
            '$.objective_intent.objective_id',
            'objective ID is not an offered OPEN owner objective',
        )
    offered = {item.event_id: item for item in projection.offered_draft_sources}
    if any(event_id not in offered for event_id in intent.draft_source_event_ids):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'objective draft source is not offered by this conversation',
            ),
            '$.objective_intent',
            'objective draft source is not offered by this conversation',
        )
    if intent.next_step is ArtifactObjectiveNextStep.PREPARE:
        if candidate is None:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('objective preparation requires an exact candidate'),
                '$.artifact_intent.operations',
                'objective preparation requires an exact candidate',
            )
        target_paths = intent.target_paths if objective is None else objective.facts.target_paths
        paths = {
            path for operation in candidate.change_set.operations
            for path in (
                (operation.source_path, operation.destination_path)
                if type(operation) is ArtifactRenameOperation else (operation.path,)
            )
        }
        if paths != set(target_paths):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'objective candidate must match its complete frozen target scope',
                ),
                '$.artifact_intent.operations',
                'objective candidate must match its complete frozen target scope',
            )


def artifact_state_payload(
    candidate: ArtifactReviewCandidatePayload,
    state: ArtifactProposalState,
    *,
    confirmation_request_id: str | None = None,
    execution_attempt_id: str | None = None,
    reviewed_proposal: ArtifactReviewedProposal | None = None,
) -> ArtifactProposalStatePayload:
    return ArtifactProposalStatePayload(
        state=state,
        candidate_id=candidate.candidate.candidate_id,
        candidate_revision=candidate.candidate.candidate_revision,
        candidate_digest=candidate.candidate.candidate_digest,
        consultation_proposal_id=candidate.consultation_proposal_id,
        consultation_proposal_revision=candidate.consultation_proposal_revision,
        consultation_proposal_digest=candidate.consultation_proposal_digest,
        confirmation_request_id=confirmation_request_id,
        execution_attempt_id=execution_attempt_id,
        reviewed_proposal_id=(
            None if reviewed_proposal is None else reviewed_proposal.proposal_id
        ),
        reviewed_proposal_digest=(
            None if reviewed_proposal is None else reviewed_proposal.proposal_digest
        ),
    )


def routing_path(locator: str) -> str | None:
    try:
        references = parse_artifact_references(locator).references
        path = references[0].path if len(references) == 1 else locator
        return path if artifact_domain(path) is not None else None
    except (TypeError, ValueError):
        return None


def authoring_affected_paths(
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
) -> tuple[str, ...]:
    locators = [item.locator for item in inputs if item.kind in {
        CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
        CollaborationInputKind.WRITING_ARTIFACT,
    }]
    if evidence is not None:
        locators.extend(item.locator for item in evidence.receipts
                        if item.locator is not None)
    return tuple(dict.fromkeys(path for locator in locators
                               if (path := routing_path(locator)) is not None))


def has_present_references(inputs: tuple[CollaborationInputRevision, ...]) -> bool:
    return any(item.state is CollaborationInputState.PRESENT and item.kind in {
        CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
        CollaborationInputKind.WRITING_ARTIFACT,
    } for item in inputs)


def final_review_floor(scope: AuthoringScope,
                        inputs: tuple[CollaborationInputRevision, ...],
                        *, preparation: bool) -> int:
    read = int(has_present_references(inputs))
    return (len(required_authoring_specialists(scope)) + 1 + 2 * int(preparation)) * (1 + read)


def future_authoring_floor(scope: AuthoringScope | None,
                            inputs: tuple[CollaborationInputRevision, ...]) -> int:
    if scope is None or scope.schema_version < 3 or scope.intent is not AuthoringIntent.AUTHORING or (
        scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
    ):
        return 0
    return final_review_floor(scope, inputs, preparation=True)


def basis_budget_fingerprint(scope: AuthoringScope | None) -> str:
    if scope is not None and scope.schema_version == 3:
        return "sha256:" + hashlib.sha256(b"scope-v3:read-aware-full-union-final-review:two-rounds:twelve-calls").hexdigest()
    return reply_contract_values.BUDGET_POLICY_FINGERPRINT


def resolve_authoring_scope(
    scope: AuthoringScope,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
    previous: AuthoringScope | None = None, *, allow_provisional: bool = False, allow_view: bool = False,
) -> AuthoringScope:
    if scope.schema_version == 3:
        scope = replace(scope, domains=required_authoring_domains(scope))
    if allow_view:
        return scope
    if previous is not None and scope.intent is not previous.intent:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('existing task intent cannot change'),
            '$.authoring_scope.intent',
            'preserve intent; requested authoring needs a fresh task',
        )
    if previous is not None and previous.schema_version == 3 and scope.task_id == previous.task_id:
        if scope.task_revision not in {previous.task_revision, previous.task_revision + 1}:
            raise BackendProtocolError("authoring task revision must remain current or advance exactly once")
        if scope.task_revision == previous.task_revision:
            if scope != previous and not allow_provisional:
                comparable = replace(scope, activity=previous.activity, interpretation=previous.interpretation)
                if comparable != previous:
                    raise BackendProtocolError("changed brief, roles or sources require a fresh task revision")
    if scope.activity is AuthoringActivity.WORKFLOW:
        if previous is not None and previous.activity is not AuthoringActivity.WORKFLOW:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    (
                        'selected story task cannot become workflow-only; use a fresh '
                        'status/clarification task'
                    ),
                ),
                '$.authoring_scope.activity',
                (
                    'selected story task cannot become workflow-only; use a fresh '
                    'status/clarification task'
                ),
            )
        return scope
    if scope.schema_version == 3:
        return scope
    paths = tuple(dict.fromkeys((
        *scope.target_paths,
        *(previous.target_paths if previous is not None else ()),
    )))
    domains = set(scope.domains)
    if previous is not None:
        domains.update(previous.domains)
    resolved = replace(scope, target_paths=paths, domains=tuple(
        item for item in ArtifactReviewDomain if item in domains
    ))
    return replace(resolved, domains=required_authoring_domains(resolved))


def authoring_tasks(
    events: tuple[CollaborationEvent, ...],
) -> dict[str, AuthoringScope]:
    tasks: dict[str, AuthoringScope] = {}
    confirmations = confirmation_task_bindings(events)
    pinned = set()
    for event in events:
        if event.event_id in confirmations:
            pinned.add(confirmations[event.event_id])
        payload = event.payload
        if authoring_scope_is_view(payload, pinned):
            continue
        if type(payload) in {CollaborationProposal, CollaborationLeadJudgmentPayload,
                             LeadRecommendationRequestPayload} and (
            payload.authoring_scope is not None
            and payload.authoring_scope.activity is not AuthoringActivity.WORKFLOW
        ):
            scope = payload.authoring_scope
            prior = tasks.get(scope.task_id)
            if prior is not None and scope.intent is not prior.intent:
                raise BackendProtocolError("authoring history changed an existing task intent")
            if prior is None:
                tasks[scope.task_id] = scope
                continue
            selected = scope if scope.task_revision >= prior.task_revision else prior
            if selected.schema_version == 3:
                if scope.task_revision == prior.task_revision and selected.activity is AuthoringActivity.REVIEW and prior.activity in {
                    AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                }:
                    selected = replace(selected, activity=prior.activity)
                tasks[scope.task_id] = selected
                continue
            authoring_activity = next((activity for activity in (prior.activity, scope.activity)
                if activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}), selected.activity)
            # Same-task facts are monotonic across revisions and conversations:
            # changing the latest phase to review never erases authorship.
            tasks[scope.task_id] = replace(
                selected,
                activity=authoring_activity,
                target_paths=tuple(dict.fromkeys((*prior.target_paths, *scope.target_paths))),
                domains=tuple(item for item in ArtifactReviewDomain
                              if item in (*prior.domains, *scope.domains)),
            )
    reviewed = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                if type(event.payload) is ArtifactReviewedProposal}
    proposals = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                 if type(event.payload) is CollaborationProposal}
    for event in events:
        result = event.payload
        if type(result) is not ArtifactApplicationResultPayload or result.outcome not in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
        }:
            continue
        review = reviewed.get((event.conversation_id, result.reviewed_proposal_id))
        if review is None or review.proposal_digest != result.reviewed_proposal_digest:
            continue
        proposal = proposals.get((event.conversation_id, review.consultation_proposal_id))
        if proposal is None or proposal.authoring_scope is None:
            continue
        scope = proposal.authoring_scope
        current = tasks.get(scope.task_id)
        if current is not None and current.task_revision <= scope.task_revision:
            tasks.pop(scope.task_id)
    for event in events:
        if type(event.payload) is not CollaborationLeadTaskTransitionPayload:
            continue
        receipt = event.payload.receipt
        ref = receipt.source_task_ref
        if ref is None or receipt.action not in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD, LeadTaskAction.NEW_TASK}:
            continue
        current = tasks.get(ref.task_id)
        if current is not None and current.task_revision <= ref.task_revision:
            tasks.pop(ref.task_id)
    return tasks


def completed_authoring_source(
    scope: AuthoringScope, events: tuple[CollaborationEvent, ...],
) -> tuple[CollaborationProposal, CollaborationEvent, CollaborationHistoryProjection]:
    confirmed = confirmed_authoring_proposals(scope, events)
    for event in reversed(events):
        if type(event.payload) is not CollaborationLeadSynthesisPayload:
            continue
        source = next((item for item in confirmed if (
            item.proposal_id, item.proposal_revision, item.proposal_digest
        ) == (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest)), None)
        if (
            source is None or source.authoring_scope is None
            or source.authoring_scope.schema_version != 3
            or source.authoring_scope.intent is not AuthoringIntent.AUTHORING
            or source.authoring_scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        ):
            continue
        result_event = next((
            item for item in events
            if item.batch_id == event.batch_id
            and type(item.payload) is CollaborationExecutionResult
            and (
                item.payload.proposal_id,
                item.payload.proposal_revision,
                item.payload.proposal_digest,
                item.payload.execution_attempt_id,
                item.payload.synthesis_result_event_id,
            ) == (
                source.proposal_id,
                source.proposal_revision,
                source.proposal_digest,
                event.payload.execution_attempt_id,
                event.event_id,
            )
            and item.payload.overall_outcome.value == "succeeded"
        ), None)
        if result_event is None:
            continue
        terminal_event = next((
            item for item in events
            if item.sequence >= result_event.sequence
            and item.conversation_id == event.conversation_id
            and type(item.payload) is CollaborationStepStatePayload
            and item.payload.state is CollaborationStepState.COMPLETED
            and item.payload.execution_result_event_id == result_event.event_id
            and (
                item.payload.proposal_id,
                item.payload.proposal_revision,
                item.payload.proposal_digest,
            ) == (
                source.proposal_id,
                source.proposal_revision,
                source.proposal_digest,
            )
        ), None)
        if terminal_event is None:
            continue
        # An ordinary synthesis result is finalized in a later batch;
        # direct synthesis recommendations publish their terminal atomically.
        prefix = tuple(item for item in events if item.sequence <= max(
            prior.sequence for prior in events
            if prior.batch_id == terminal_event.batch_id
        ))
        projected = project_collaboration_history(prefix)
        rounds = sum(1 for item in prefix if item.step_id == source.step_id
                     and type(item.payload) is CollaborationStepStatePayload
                     and item.payload.state is CollaborationStepState.RUNNING)
        spent = sum(1 for item in prefix if item.step_id == source.step_id
                    and type(item.payload) is CollaborationProviderCallPayload
                    and item.payload.state is CollaborationProviderCallState.SUBMITTED)
        if source.document_node is not None:
            spent, _, _ = document_lineage_cost(prefix, source.document_node, current_step_id=source.step_id)
            if source.document_node.budget_lineage_ref.conversation_id != event.conversation_id:
                declaration = next(item for item in prefix if item.payload is source)
                declared_calls, _, _ = document_lineage_cost(
                    tuple(item for item in prefix if item.sequence <= declaration.sequence),
                    source.document_node, current_step_id=source.step_id)
                spent += max(0, source.budget.physical_calls_spent - declared_calls)
        if rounds != 1 or authoring_capture_identity(scope, source.basis) != authoring_capture_identity(source.authoring_scope, source.basis):
            raise BackendProtocolError("completed authoring source capture or round is not current")
        projected = replace(projected, current_step_id=source.step_id, current_proposal=source,
                            current_state=CollaborationStepState.COMPLETED,
                            rounds_spent=rounds, physical_calls_spent=spent)
        return source, event, projected
    raise reply_contract_values.LeadOutcomeValidationError("completed draft source is unavailable here; focus its source conversation to prepare final review, or clarify and freshly propose")


def validate_final_review_handoff(
    proposal: CollaborationProposal, events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
) -> None:
    scope = proposal.authoring_scope
    if scope is None or scope.schema_version < 3 or scope.activity is not AuthoringActivity.REVIEW:
        return
    sources = tuple(item for item in confirmed_authoring_proposals(scope, events)
                    if item.authoring_scope is not None and item.authoring_scope.activity in {
                        AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
                    })
    if not sources:
        return  # Fresh user-supplied candidate review has no draft lineage to inherit.
    requests = [event.payload for event in events if event.step_id == proposal.step_id
                and type(event.payload) is LeadRecommendationRequestPayload
                and event.payload.authoring_scope is not None
                and event.payload.authoring_scope.schema_version == 3]
    if not requests:
        raise BackendProtocolError("draft-derived final review requires its confirmed recommendation handoff")
    request = requests[-1]
    source = next((item for item in sources if (
        item.proposal_id, item.proposal_revision, item.proposal_digest
    ) == (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)), None)
    if source is None or request.source_execution_attempt_id is None:
        raise BackendProtocolError("final review is detached from its confirmed draft source")
    if authoring_capture_identity(scope, proposal.basis) != authoring_capture_identity(
        source.authoring_scope, source.basis
    ) or authoring_capture_identity(request.authoring_scope, request.basis) != authoring_capture_identity(
        source.authoring_scope, source.basis
    ):
        raise BackendProtocolError("final review must inherit the exact source capture")
    if projection.rounds_spent != 1 or projection.physical_calls_spent < request.physical_calls_spent:
        raise BackendProtocolError("final review must inherit physical calls and the single completed round")


def validate_draft_provenance(
    scope: AuthoringScope,
    events: tuple[CollaborationEvent, ...],
    *, pending_attempt_id: str | None = None,
    pending_replies: tuple[CollaborationSpecialistReplyPayload, ...] = (),
) -> None:
    proposals = {(event.conversation_id, event.payload.proposal_id): event.payload for event in events
                 if type(event.payload) is CollaborationProposal}
    drafted: set[str] = set()
    confirmed = confirmed_authoring_proposals(scope, events) if scope.schema_version == 3 else ()
    for event in events:
        reply = event.payload
        if type(reply) is not CollaborationSpecialistReplyPayload or (
            reply.mode is not SpecialistAssignmentMode.DRAFT
            or reply.assessment is not CollaborationSpecialistAssessment.DRAFTED
        ):
            continue
        proposal = proposals.get((event.conversation_id, reply.proposal_id))
        if proposal is None or proposal.authoring_scope is None:
            continue
        if (reply.proposal_revision, reply.proposal_digest) != (
            proposal.proposal_revision, proposal.proposal_digest,
        ):
            continue
        prior = proposal.authoring_scope
        if (prior.task_id, prior.task_revision) == (scope.task_id, scope.task_revision):

            if scope.schema_version == 3:
                if proposal not in confirmed or authoring_capture_identity(scope, proposal.basis) != authoring_capture_identity(prior, proposal.basis):
                    continue
                dispatch = next((item for item in events if item.conversation_id == event.conversation_id
                                 and item.event_id == reply.dispatch_event_id
                                 and type(item.payload) is CollaborationAssignmentDispatchPayload), None)
                result = next((item.payload for item in events if item.conversation_id == event.conversation_id
                               and type(item.payload) is CollaborationExecutionResult
                               and item.payload.execution_attempt_id == reply.execution_attempt_id
                               and item.payload.proposal_digest == proposal.proposal_digest
                               and item.payload.overall_outcome.value == "succeeded"), None)
                if dispatch is None or (result is None and reply.execution_attempt_id != pending_attempt_id) or dispatch.payload.execution_attempt_id != reply.execution_attempt_id or (
                    dispatch.payload.assignment.assignment_id != reply.assignment_id
                    or dispatch.payload.assignment.mode is not SpecialistAssignmentMode.DRAFT
                ):
                    continue
                validate_authoring_capture_pin(proposal, events)
            drafted.add(cast(str, event.sender.agent_id))
    if pending_attempt_id is not None:
        for reply in pending_replies:
            if reply.execution_attempt_id != pending_attempt_id or reply.mode is not SpecialistAssignmentMode.DRAFT or reply.assessment is not CollaborationSpecialistAssessment.DRAFTED:
                continue
            proposal = next((item for item in confirmed if (
                item.proposal_id, item.proposal_revision, item.proposal_digest
            ) == (reply.proposal_id, reply.proposal_revision, reply.proposal_digest)), None)
            dispatch = next((event.payload for event in events
                             if event.event_id == reply.dispatch_event_id
                             and type(event.payload) is CollaborationAssignmentDispatchPayload), None)
            if proposal is None or dispatch is None or dispatch.execution_attempt_id != pending_attempt_id or (
                dispatch.assignment.assignment_id != reply.assignment_id
                or dispatch.assignment.mode is not SpecialistAssignmentMode.DRAFT
                or proposal.authoring_scope is None
                or authoring_capture_identity(scope, proposal.basis) != authoring_capture_identity(proposal.authoring_scope, proposal.basis)
            ):
                continue
            drafted.add(dispatch.assignment.specialist_id)
    if not set(required_drafting_specialists(scope)).issubset(drafted):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'artifact authoring requires current-task drafts from every declared draft owner',
            ),
            '$.artifact_intent.operations',
            'artifact authoring requires current-task drafts from every affected specialist',
        )


def official_authoring_scope(scope: AuthoringScope | None) -> bool:
    return scope is not None and scope.intent is AuthoringIntent.AUTHORING


def stage_required_condition_ids(
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
) -> tuple[str, ...]:
    """Separate READ evidence and exact preparation provenance from task conditions."""
    read_ids = {receipt.evidence_id for receipt in (() if evidence is None else evidence.receipts)
                if receipt.acquisition is CollaborationEvidenceAcquisition.READ}
    original_ids = tuple(item.input_id for item in inputs if item.input_id not in read_ids)
    provenance = tuple(item for item in inputs if item.input_id in PREPARATION_INPUT_IDS)
    if not provenance:
        return original_ids
    if (len(provenance) != len(PREPARATION_INPUT_IDS)
            or {item.input_id for item in provenance} != set(PREPARATION_INPUT_IDS)
            or any(item.kind is not CollaborationInputKind.CONVERSATION_EVENT for item in provenance)):
        raise ValueError("stage preparation provenance requires the exact runtime event pair")
    return preparation_original_input_ids(original_ids)


def validate_required_reviewers(
    inputs: tuple[CollaborationInputRevision, ...],
    assignments: tuple[CollaborationAssignment, ...],
    stage_plan: SpecialistStagePlan | None = None,
) -> None:
    specialist_ids = tuple(item.specialist_id for item in assignments)
    required_reviewers = {
        reviewer
        for item in inputs
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}
        for reviewer in (core_reviewer(item.locator),)
        if reviewer is not None
    }
    if stage_is_focused_review(stage_plan):
        required_reviewers.intersection_update(specialist_ids)
    if not required_reviewers.issubset(specialist_ids):
        raise ValueError("proposal omits a mandatory closed-fact reviewer")
    assignment_inputs = {
        item.specialist_id: frozenset(item.input_ids) for item in assignments
    }
    if any(
        (not stage_is_focused_review(stage_plan) or reviewer in specialist_ids) and (
        reviewer not in assignment_inputs
        or item.input_id not in assignment_inputs[reviewer])
        for item in inputs
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}
        for reviewer in (core_reviewer(item.locator),)
        if reviewer is not None
    ):
        raise ValueError("mandatory reviewer omits its closed-fact input")


def proposal_call_reservations(
    *,
    inputs: tuple[CollaborationInputRevision, ...],
    assignment_input_ids: tuple[tuple[str, ...], ...],
    physical_calls_spent: int,
    synthesis_required: bool = True,
    authoring_scope: AuthoringScope | None = None,
) -> tuple[tuple[int, ...], int, int]:
    reference_input_ids = {
        item.input_id
        for item in inputs
        if (authoring_scope is None or authoring_scope.schema_version < 3 or item.state is CollaborationInputState.PRESENT)
        and item.kind
        in {
            CollaborationInputKind.CORE_DOCUMENT,
            CollaborationInputKind.MANUSCRIPT,
            CollaborationInputKind.WRITING_ARTIFACT,
        }
    }
    assignment_call_limits = tuple(
        1 + int(any(input_id in reference_input_ids for input_id in input_ids))
        for input_ids in assignment_input_ids
    )
    future_floor = future_authoring_floor(authoring_scope, inputs)
    remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - physical_calls_spent - future_floor
    synthesis_minimum = (
        1 + int(bool(reference_input_ids)) if synthesis_required else 0
    )
    minimum_required_calls = sum(assignment_call_limits) + synthesis_minimum
    if minimum_required_calls >= remaining:
        raise LeadCallBudgetExceeded(
            "declared preparation and full final review cannot fit the remaining physical-call budget; clarify or freshly propose"
        )
    allocated_limits = list(assignment_call_limits)
    unallocated = remaining - minimum_required_calls
    while unallocated and any(
        limit < reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT for limit in allocated_limits
    ):
        for index, limit in enumerate(allocated_limits):
            if not unallocated:
                break
            if limit < reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT:
                allocated_limits[index] += 1
                unallocated -= 1
    synthesis_calls = (
        synthesis_minimum
        + min(
            reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT - synthesis_minimum,
            unallocated,
        )
        if synthesis_required
        else 0
    )
    required_calls = sum(allocated_limits) + synthesis_calls
    return tuple(allocated_limits), synthesis_calls, required_calls


def core_reviewer(locator: str) -> str | None:
    try:
        batch = parse_artifact_references(locator)
    except Exception:
        return None
    if (
        batch.source_text != locator
        or batch.provider_text != locator
        or len(batch.references) != 1
        or batch.references[0].token != locator
    ):
        return None
    domain = artifact_domain(batch.references[0].path)
    return None if domain is None else specialists_for_domains((domain,))[0]


def reference_reservations(
    operation: RuntimeReferenceOperation,
) -> tuple[str | None, tuple[MandatoryReferenceReservation, ...]]:
    if not operation.reference_proofs:
        return None, ()
    if operation.operation_id is None:
        raise BackendProtocolError("reference operation identity is missing")
    return (
        operation.operation_id,
        tuple(
            MandatoryReferenceReservation(
                ordinal=proof.ordinal,
                reference_id=f"reference_{proof.ordinal}",
                source_id=writing_artifact_source_id(proof.reference.path),
                revision=f"sha256:{proof.source.source_sha256}",
                provenance=ContextProvenance(
                    ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                    proof.reference.token,
                ),
                content_digest=f"sha256:{proof.selected_sha256}",
                utf8_bytes=proof.selected_utf8_bytes,
            )
            for proof in operation.reference_proofs
        ),
    )
