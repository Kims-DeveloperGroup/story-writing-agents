"""Document goal and selection policy; resources and operations are caller-owned."""
from __future__ import annotations

from ..conversations import ArtifactObjectiveFacts
from ..conversations import ArtifactObjectiveFactsV2
from ..conversations import CollaborationActorKind
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEvent as CollaborationEventV2
from ..conversations import CollaborationMessagePayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationProviderCallPayload
from ..conversations import project_collaboration_history
from ..core_document_readiness import AuthoringTarget
from ..core_document_readiness import CoreDocumentReadiness
from ..core_document_readiness import CoreDocumentReadinessNeeded
from ..core_document_readiness import require_readiness
from ..document_plan import DocumentCandidateUseIntent
from ..document_plan import DocumentNodeBinding
from ..document_plan import DocumentNodeSelection
from ..document_plan import DocumentNodeSpec
from ..document_plan import DocumentPlan
from ..document_plan import document_value_digest
from ..lead_task_transitions import LeadTaskRef
from ..runtime_resources import collaboration_event_journal as event_journal
from ..specialist_routing import AuthoringActivity
from ..specialist_routing import AuthoringIntent
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json

__all__ = ('document_goal_facts', 'bind_document_selection')


def document_goal_facts(
    session_id,
    scope_event,
    target_paths,
    *,
    objective_id=None,
    preparation=None,
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
):
    if scope_event is None:
        raise ValueError("document goal requires its actual accepted Lead scope event")
    events = read_collaboration_events(session_id, scope_event.conversation_id)
    projected = project_collaboration_history(events)
    if preparation is not None:
        request = preparation.request_event.payload
        return ArtifactObjectiveFactsV2(objective_id or event_journal.new_id("obj"), scope_event.conversation_id,
            request.request_event_id, preparation.decision_event.event_id, request.instruction,
            request.objective_target_paths, projected.offered_draft_sources)
    origins = tuple(event for event in events if event.turn_id == scope_event.turn_id
        and type(event.payload) is CollaborationMessagePayload and event.sender.kind is CollaborationActorKind.USER
        and event.recipient.kind is CollaborationActorKind.LEAD)
    if len(origins) != 1:
        raise ValueError("document goal requires one exact literal USER origin")
    return ArtifactObjectiveFacts(objective_id or event_journal.new_id("obj"), scope_event.conversation_id,
        origins[0].event_id, origins[0].payload.content, target_paths, projected.offered_draft_sources)


def bind_document_selection(
    session_id,
    selection,
    scope,
    projection,
    scope_event,
    *,
    prepare_document_selection: Callable[[str, DocumentNodeSelection], tuple[DocumentPlan, DocumentNodeSpec]],
    inspect_core_document_readiness: Callable[[str], CoreDocumentReadiness | CoreDocumentReadinessNeeded],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
    document_prior_confirmation: Callable[[str, str], str | None],
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
):
    plan, node = prepare_document_selection(session_id, selection)
    if scope is None or scope.target_paths != (node.target_path,):
        raise ValueError("selected document task must retain its exact disclosed target")
    if scope.intent is AuthoringIntent.AUTHORING and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
        readiness = inspect_core_document_readiness(session_id)
        needed = (None if node.target_kind is AuthoringTarget.GLOBAL_OUTLINE else readiness) if type(readiness) is CoreDocumentReadinessNeeded else require_readiness(node.target_kind, readiness)
        if needed is not None:
            raise ValueError(needed.message)
    if plan.goal_objective_id is None and node.target_kind is AuthoringTarget.MANUSCRIPT:
        facts = document_goal_facts(session_id, scope_event, (node.target_path,), read_collaboration_events=read_collaboration_events)
        plan = DocumentPlan.create(plan_id=plan.ref.plan_id, plan_revision=plan.ref.plan_revision,
            goal_objective_id=facts.objective_id, goal_facts_digest=document_value_digest(asdict(facts)),
            nodes=plan.nodes, remaining_target_paths=plan.remaining_target_paths,
            planning_goal=plan.planning_goal, unresolved_target=False)
    scope_digest = "sha256:" + hashlib.sha256(json.dumps(asdict(scope),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    task = next((item.ref for item in projection.available_tasks
        if (item.ref.task_id, item.ref.task_revision, item.ref.scope_digest) ==
           (scope.task_id, scope.task_revision, scope_digest)), None)
    if task is None:
        if scope_event is None or scope_event.payload.authoring_scope != scope:
            raise ValueError("a document binding needs its actual accepted scope event")
        task = LeadTaskRef(session_id=session_id, conversation_id=scope_event.conversation_id, task_id=scope.task_id, task_revision=scope.task_revision,
            scope_event_id=scope_event.event_id, scope_digest="sha256:" + hashlib.sha256(json.dumps(asdict(scope), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())
    events = read_session_authoring_events(session_id)
    prior = tuple(event.payload.document_node for event in events
        if type(event.payload) in {CollaborationProposal, CollaborationProviderCallPayload}
        and event.payload.document_node is not None
        and event.payload.document_node.task_ref.task_id == task.task_id)
    if prior:
        budget, step = prior[0].budget_lineage_ref, prior[0].budget_origin_step_id
    else:
        source = next((event for event in events if event.event_id == task.scope_event_id), scope_event)
        if source is None:
            raise ValueError("document budget requires an actual source step")
        budget, step = task, source.step_id
    conditions = document_value_digest((plan.goal_facts_digest,
        None if plan.planning_goal is None else plan.planning_goal.content_digest,
        node.preservation_conditions, task.scope_digest))
    uses = tuple(DocumentCandidateUseIntent(plan.ref, use.predecessor_node_id, node.node_id,
        use.candidate, task, use.permitted_scope, use.purpose, conditions) for use in selection.candidate_uses)
    return DocumentNodeBinding(plan.ref, node.node_id, task, budget, step, uses,
        prior_confirmation_event_id=document_prior_confirmation(session_id, plan.ref.plan_id)), (plan if plan.ref != selection.plan_ref else None)
