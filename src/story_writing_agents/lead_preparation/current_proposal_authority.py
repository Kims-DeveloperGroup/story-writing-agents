"""Qualify current proposal authority in explicit live execution phases."""
from __future__ import annotations

from ..agent_context.invocation.context_execution import AgentContextService
from ..specialist_routing import AuthoringActivity
from ..authoring_admission import AuthoringAdmissionBasis
from ..authoring_admission import AuthoringAdmissionNeeded
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import COLLABORATION_ROUND_LIMIT
from collections.abc import Callable
from ..conversations import CollaborationAgentProfile
from ..conversations import CollaborationAssignment
from ..conversations import CollaborationBudget
from ..conversations import CollaborationEvent
from ..conversations import CollaborationHistoryProjection
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationProposal
from ..conversations import CollaborationProposalBasis
from ..conversations import CollaborationStepKind
from ..core_document_readiness import CoreDocumentReadinessNeeded
from ..agents import LEAD_ORCHESTRATOR
from ..agents import SPECIALIST_IDS
from ..workspace.session_artifact_binding import SessionArtifactBinding
from ..specialist_routing import SpecialistAssignmentMode
from ..lead_preparation import authoring_authority
from ..lead_preparation import reply_contract_values
from ..specialist_routing import validate_stage_routing

__all__ = ('validate_current_premise_authority', 'qualify_current_dispatch_authority', 'prepare_current_execution_authority', 'build_current_authority_basis',)


def validate_current_premise_authority(
    *,
    session_id: str,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    proposal: CollaborationProposal,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> None:
    if proposal.premise_decision is None:
        if proposal.authoring_scope is None:
            raise ValueError("legacy proposal requires fresh scope and confirmation")
        task_events = read_session_authoring_events(session_id)
        current_task = authoring_authority.authoring_tasks(task_events).get(proposal.authoring_scope.task_id)
        if current_task is None or current_task.task_revision != proposal.authoring_scope.task_revision:
            raise ValueError("authoring task revision is no longer current")
        if not set(current_task.domains).issubset(proposal.authoring_scope.domains) or not set(
            current_task.target_paths
        ).issubset(proposal.authoring_scope.target_paths):
            raise ValueError("proposal omits current-task authoring scope")
        candidate = authoring_authority.artifact_candidate_for_proposal(events, proposal)
        if candidate is not None and current_task.activity in {
            AuthoringActivity.DRAFT, AuthoringActivity.REVISE,
        }:
            authoring_authority.validate_draft_provenance(current_task, task_events)
    elif projection.rounds_spent != 0:
        raise ValueError("premise analysis cannot reopen a completed analyst round")


def qualify_current_dispatch_authority(
    *,
    session_id: str,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    proposal: CollaborationProposal,
    inputs: tuple[CollaborationInputRevision, ...],
    artifact_binding: SessionArtifactBinding,
    qualify_authoring_inputs: Callable[..., AuthoringAdmissionBasis | AuthoringAdmissionNeeded],
    qualify_readiness_targets: Callable[..., object],
    validate_review_material: Callable[..., None],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> tuple[str, ...]:
    specialist_ids = tuple(
        assignment.specialist_id for assignment in proposal.assignments
    )
    canonical = tuple(
        agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
    )
    if specialist_ids != canonical:
        raise ValueError("proposal specialist order is no longer canonical")
    if (authoring_authority.official_authoring_scope(proposal.authoring_scope)
    or proposal.basis.authoring_admission is not None):
        assert proposal.authoring_scope is not None
        current_admission = qualify_authoring_inputs(
            proposal.basis.authoring_admission.assessment if proposal.basis.authoring_admission else None,
            inputs=inputs, evidence=proposal.basis.evidence, scope=proposal.authoring_scope, events=events, admission=proposal.basis.authoring_admission)
        if (type(current_admission) is not AuthoringAdmissionBasis
                or current_admission != proposal.basis.authoring_admission):
            raise ValueError("official authoring admission is missing or stale")
    if proposal.authoring_scope is not None and (authoring_authority.artifact_candidate_for_proposal(events, proposal) is not None or any(item.mode is SpecialistAssignmentMode.DRAFT for item in proposal.assignments)):
        readiness = qualify_readiness_targets(session_id, proposal.authoring_scope.target_paths,
            retained=proposal.basis.core_document_readiness)
        if type(readiness) is CoreDocumentReadinessNeeded or readiness != proposal.basis.core_document_readiness:
            raise ValueError(readiness.message if type(readiness) is CoreDocumentReadinessNeeded else "dispatch readiness basis missing or changed")
    if proposal.authoring_scope is not None:
        validate_stage_routing(proposal.authoring_scope, specialist_ids, proposal.stage_plan,
        source_input_ids=tuple(item.input_id for item in inputs),
        required_condition_ids=(() if proposal.stage_plan is None else authoring_authority.stage_required_condition_ids(tuple(inputs), proposal.basis.evidence)),
                                    affected_paths=authoring_authority.authoring_affected_paths(inputs, proposal.basis.evidence))
    validate_review_material(
        session_id,
        proposal.assignments,
        inputs,
        read_session_authoring_events(session_id) if proposal.document_node is not None and proposal.document_node.candidate_uses else events,
        artifact_binding=artifact_binding,
    )
    authoring_authority.validate_required_reviewers(inputs, proposal.assignments, stage_plan=proposal.stage_plan)
    if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
        raise ValueError("proposal no longer fits the round budget")
    return specialist_ids


def prepare_current_execution_authority(
    *,
    proposal: CollaborationProposal,
    inputs: tuple[CollaborationInputRevision, ...],
    projection: CollaborationHistoryProjection,
    specialist_ids: tuple[str, ...],
) -> tuple[tuple[int, ...], int, int, tuple[CollaborationAssignment, ...], tuple[str, ...]]:
    call_limits, synthesis_calls, required_calls = (
        authoring_authority.proposal_call_reservations(
            inputs=inputs,
            assignment_input_ids=tuple(
                assignment.input_ids for assignment in proposal.assignments
            ),
            physical_calls_spent=projection.physical_calls_spent,
            authoring_scope=proposal.authoring_scope,
            synthesis_required=(
                proposal.step_kind
                is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            ),
        )
    )
    runtime_assignments = tuple(
        CollaborationAssignment(
            assignment_id=assignment.assignment_id,
            specialist_id=assignment.specialist_id,
            round=projection.rounds_spent + 1,
            instruction=assignment.instruction,
            input_ids=assignment.input_ids,
            mode=assignment.mode,
            review_basis=assignment.review_basis,
            max_physical_calls=call_limit,
        )
        for assignment, call_limit in zip(
            proposal.assignments,
            call_limits,
            strict=True,
        )
    )
    participant_sequence = (
        (LEAD_ORCHESTRATOR, *specialist_ids)
        if proposal.step_kind
        is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
        else specialist_ids
    )
    return call_limits, synthesis_calls, required_calls, runtime_assignments, participant_sequence


def build_current_authority_basis(
    *,
    proposal: CollaborationProposal,
    inputs: tuple[CollaborationInputRevision, ...],
    projection: CollaborationHistoryProjection,
    specialist_ids: tuple[str, ...],
    profiles: tuple[CollaborationAgentProfile, ...],
    required_calls: int,
    synthesis_calls: int,
    context_service: AgentContextService,
) -> tuple[CollaborationProposalBasis, CollaborationBudget]:
    route_name = proposal.step_kind.value
    route_value = {
        "route": route_name,
        "participants": list(specialist_ids),
        "profiles": [
            [
                item.agent_id,
                item.definition_fingerprint,
                item.execution_fingerprint,
            ]
            for item in profiles
        ],
    }
    basis = CollaborationProposalBasis(
        core_document_readiness=proposal.basis.core_document_readiness,
        authoring_admission=proposal.basis.authoring_admission,
        route=route_name,
        route_fingerprint=reply_contract_values.canonical_digest(route_value),
        agent_profiles=profiles,
        context_policy_fingerprint=(
            context_service.policy_fingerprint
        ),
        budget_policy_fingerprint=authoring_authority.basis_budget_fingerprint(proposal.authoring_scope),
        inputs=inputs,
        evidence=proposal.basis.evidence,
        schema_version=proposal.basis.schema_version,
        shared_premises=proposal.basis.shared_premises,
        editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
    )
    budget = CollaborationBudget(
        round_limit=COLLABORATION_ROUND_LIMIT,
        physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
        rounds_spent=projection.rounds_spent,
        rounds_added=1,
        physical_calls_spent=projection.physical_calls_spent,
        max_additional_physical_calls=required_calls,
        synthesis_max_physical_calls=synthesis_calls,
    )
    return basis, budget
