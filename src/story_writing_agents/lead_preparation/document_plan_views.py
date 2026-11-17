"""Document-plan view and attachment policy from invocation-local facts."""
from __future__ import annotations

from ..authoring_admission import AdmissionSourceKind
from ..conversations import ArtifactObjectivePayload
from ..conversations import ArtifactObjectivePayloadV2
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import COLLABORATION_ROUND_LIMIT
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEvent as CollaborationEventV2
from ..conversations import CollaborationLeadJudgmentPayload
from ..conversations import CollaborationProposal
from ..conversations import LeadTurnResult
from ..conversations.context import document_lineage_cost
from ..conversations.session_context import SessionHistorySnapshot
from ..core_document_readiness import AuthoringTarget
from ..core_document_readiness import CoreDocumentReadiness
from ..core_document_readiness import CoreDocumentReadinessNeeded
from ..core_document_readiness import DEPENDENCIES
from ..core_document_readiness import require_readiness
from ..document_plan import DocumentNodeNextAction
from ..document_plan import DocumentNodeState
from ..document_plan import DocumentNodeView
from ..document_plan import DocumentPlanView
from ..specialist_routing import AuthoringActivity
from ..specialist_routing import AuthoringIntent
from collections.abc import Callable
from dataclasses import replace

__all__ = ('project_document_plan_views', 'attach_document_plan')


def project_document_plan_views(
    snapshot: SessionHistorySnapshot,
    views: tuple[DocumentPlanView, ...],
    readiness: CoreDocumentReadiness | CoreDocumentReadinessNeeded,
) -> tuple[DocumentPlanView, ...]:
    facts = {record.payload.facts.objective_id: record.payload.facts for row in snapshot.conversations
             for record in row.records if type(record.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}}
    result = []
    for view in views:
        requested = set(() if view.plan.goal_objective_id not in facts else facts[view.plan.goal_objective_id].target_paths)
        requested.update(path for owner in snapshot.conversations for record in owner.records
            if type(record.payload) is CollaborationProposal and record.payload.document_node is not None
            and record.payload.document_node.plan_ref.plan_id == view.plan.ref.plan_id
            and record.payload.authoring_scope is not None
            and record.payload.authoring_scope.intent is AuthoringIntent.AUTHORING
            and record.payload.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE, AuthoringActivity.REVIEW}
            for path in record.payload.authoring_scope.target_paths)
        rows = []
        for node, row in zip(view.plan.nodes, view.nodes):
            needed = (None if node.target_kind is AuthoringTarget.GLOBAL_OUTLINE else readiness) if type(readiness) is CoreDocumentReadinessNeeded else require_readiness(node.target_kind, readiness)
            basis = None if type(readiness) is CoreDocumentReadinessNeeded else readiness.basis
            own = None if basis is None else next((item for item in basis.assessment.document_judgments
                                                   if item.document_path == node.target_path), None)
            prerequisite_paths = {target.value for target in DEPENDENCIES[node.target_kind]}
            row = replace(row, prerequisite_sources=() if basis is None else tuple(source for source in basis.sources
                if source.document_path in prerequisite_paths and source.kind is AdmissionSourceKind.READ))
            decision_ids = set() if own is None else (set(own.remaining_author_decision_ids) |
                {identity for finding in own.criteria_findings if finding.criterion_judgment != "criteria_satisfied"
                 for identity in finding.depends_on_author_decision_ids})
            missing = () if own is None else tuple(choice for choice in basis.assessment.book_basis.author_decisions
                if choice.author_decision_id in decision_ids
                and choice.author_decision_coverage not in {"supplied", "delegated"})
            if needed is not None or missing:
                reasons = (() if needed is None else needed.reasons) + tuple(
                    f"Author decision {choice.author_decision_id}: {choice.author_decision_text}. {choice.explanation}" for choice in missing)
                inputs = (() if needed is None else needed.required_documents) + tuple(choice.author_decision_id for choice in missing)
                row = replace(row, state=DocumentNodeState.BLOCKED, blocking_conditions=tuple(dict.fromkeys(reasons)),
                    required_inputs=tuple(dict.fromkeys(inputs)), next_action=DocumentNodeNextAction.REQUEST_INFORMATION)
            elif (node.target_path not in requested and row.state is not DocumentNodeState.IN_PROGRESS
                    and type(readiness) is CoreDocumentReadiness and node.target_path in readiness.complete_documents):
                source_id = own.document_read_source_id if own is not None else None
                source = None if basis is None else next((item for item in basis.sources if item.source_id == source_id), None)
                if source is not None and source.acquisition_identity is not None:
                    row = replace(row, state=DocumentNodeState.COMPLETE, blocking_conditions=(), required_inputs=(),
                        completion_event_ids=(source.acquisition_identity,), next_action=DocumentNodeNextAction.COMPLETE)
            elif row.state not in {DocumentNodeState.COMPLETE, DocumentNodeState.IN_PROGRESS, DocumentNodeState.FOLLOWUP_REQUIRED}:
                row = replace(row, state=DocumentNodeState.READY, blocking_conditions=(), required_inputs=(),
                              next_action=(DocumentNodeNextAction.CONFIRM_NODE if row.next_action is DocumentNodeNextAction.CONFIRM_NODE
                                           else DocumentNodeNextAction.PREPARE_NODE))
            if (not view.cursor_ambiguous and (view.remaining_physical_calls == 0 or view.remaining_rounds == 0)
                    and row.state is not DocumentNodeState.COMPLETE
                    and row.next_action not in {DocumentNodeNextAction.APPLY_CANDIDATE, DocumentNodeNextAction.PREPARE_COMMIT}):
                row = replace(row, state=DocumentNodeState.BLOCKED,
                    blocking_conditions=(*row.blocking_conditions, "Existing task budget is exhausted; no node reset is permitted."),
                    next_action=DocumentNodeNextAction.BUDGET_EXHAUSTED)
            rows.append(row)
        if view.cursor_ambiguous:
            rows = [replace(row, state=DocumentNodeState.BLOCKED,
                blocking_conditions=tuple(dict.fromkeys((*row.blocking_conditions,
                    "Independent confirmed document heads require explicit reconciliation; current budget is unavailable."))),
                next_action=DocumentNodeNextAction.REQUEST_INFORMATION)
                if row.state is not DocumentNodeState.COMPLETE else row for row in rows]
        active = {node.target_path: row for node, row in zip(view.plan.nodes, rows)}
        completed = tuple(path for path in view.completed_target_paths
            if path not in active or active[path].state is DocumentNodeState.COMPLETE)
        result.append(replace(view, nodes=tuple(rows), completed_target_paths=completed))
    return tuple(result)


def attach_document_plan(
    result: LeadTurnResult,
    views: tuple[DocumentPlanView, ...],
    events=None,
    projection=None,
    *,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
) -> LeadTurnResult:
    proposal = result.proposal
    if proposal is None and result.artifact_proposal is not None:
        candidate = result.artifact_proposal.candidate
        current = None if projection is None else projection.current_proposal
        if current is None or (current.proposal_id, current.proposal_revision, current.proposal_digest) != (
                candidate.consultation_proposal_id, candidate.consultation_proposal_revision, candidate.consultation_proposal_digest):
            return result
        proposal = current
    if proposal is not None and proposal.document_plan is not None:
        active = next((view for view in views if view.plan.ref.plan_id == proposal.document_plan.ref.plan_id), None)
        if active is not None and active.plan.ref != proposal.document_plan.ref:
            calls, rounds, total = document_lineage_cost(read_session_authoring_events(result.session_id),
                proposal.document_node, current_step_id=proposal.step_id)
            rows = tuple(DocumentNodeView(node.node_id, DocumentNodeState.READY, (), (), (), (),
                DocumentNodeNextAction.CONFIRM_NODE) for node in proposal.document_plan.nodes)
            pending = DocumentPlanView(proposal.document_plan, None, rows, calls, rounds, total,
                max(0, COLLABORATION_PHYSICAL_CALL_LIMIT - calls), max(0, COLLABORATION_ROUND_LIMIT - rounds),
                goal_text=active.goal_text, pending=True,
                completed_target_paths=tuple(path for path in active.completed_target_paths
                    if path in proposal.document_plan.remaining_target_paths))
            return replace(result, document_plan=pending)
    binding = (proposal.document_node if proposal is not None else
               None if result.recommendation_request is None else result.recommendation_request.document_node)
    if binding is not None:
        current = next((view for view in views if view.plan.ref == binding.plan_ref), None)
    else:
        owner_events = (read_collaboration_events(result.session_id, result.conversation_id)
            if events is None else events)
        declared_refs = {event.payload.document_plan.ref for event in owner_events
            if (event.session_id, event.conversation_id, event.turn_id, event.step_id) ==
               (result.session_id, result.conversation_id, result.turn_id, result.step_id)
            and type(event.payload) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
            and event.payload.document_plan is not None}
        if declared_refs:
            current = next((view for view in views if view.plan.ref in declared_refs), None) if len(declared_refs) == 1 else None
        else:
            current = views[0] if len(views) == 1 else None
    return result if current is None else replace(result, document_plan=current)
