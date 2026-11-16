"""Document-node policy over invocation-local live semantic operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
import hashlib
import json

from ..conversations.events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    ArtifactReviewCandidatePayload,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationLeadJudgmentPayload,
    CollaborationProposal,
    LeadRecommendationRequestPayload,
)
from ..conversations.session_context import SessionHistorySnapshot
from ..core_document_readiness import (
    AuthoringTarget,
    CoreDocumentReadiness,
    CoreDocumentReadinessNeeded,
    require_readiness,
)
from ..document_plan import (
    DocumentNodeBinding,
    DocumentNodeNextAction,
    DocumentNodeSelection,
    DocumentNodeSpec,
    DocumentNodeState,
    DocumentNodeView,
    DocumentPlan,
    DocumentPlanView,
    document_value_digest,
)
from ..specialist_routing import AuthoringActivity, AuthoringIntent
from ..writing_artifacts import ArtifactRenameOperation
from . import authoring_authority

__all__ = (
    "document_plan_views",
    "validate_document_node",
    "automatic_document_node",
    "document_candidate_inputs",
)


def document_plan_views(
    session_id: str,
    *,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
) -> tuple[DocumentPlanView, ...]:
    snapshot = capture_editorial_snapshot(session_id)
    return snapshot.document_plans()


def validate_document_node(
    session_id: str,
    binding: DocumentNodeBinding,
    *,
    scope=None,
    proposed_plan=None,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
    prepare_document_selection: Callable[[str, DocumentNodeSelection], tuple[DocumentPlan, DocumentNodeSpec]],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
    inspect_core_document_readiness: Callable[[str], CoreDocumentReadiness | CoreDocumentReadinessNeeded],
) -> DocumentPlanView:
    if type(binding) is not DocumentNodeBinding:
        raise TypeError("document_node must be an exact untrusted node binding")
    captured_views = document_plan_views(session_id, capture_editorial_snapshot=capture_editorial_snapshot)
    causal = tuple(view for view in captured_views if view.plan.ref.plan_id == binding.plan_ref.plan_id)
    if len(causal) != 1 or causal[0].cursor_ambiguous:
        raise ValueError("document execution requires a reconciled exact causal head")
    causal_view = causal[0]
    views = tuple(view for view in captured_views if view.plan.ref == binding.plan_ref)
    if not views and proposed_plan is not None:
        current = tuple(view for view in document_plan_views(session_id, capture_editorial_snapshot=capture_editorial_snapshot)
                        if view.plan.ref.plan_id == proposed_plan.ref.plan_id)
        selected = next((node for node in proposed_plan.nodes if node.node_id == binding.node_id), None)
        if len(current) != 1 or selected is None:
            raise ValueError("proposed document window has no exact current predecessor")
        expected, _ = prepare_document_selection(session_id,
            DocumentNodeSelection(current[0].plan.ref, target_path=selected.target_path))
        if expected.goal_objective_id is None and proposed_plan.goal_objective_id is not None:
            facts = next((event.payload.facts for event in read_session_authoring_events(session_id)
                if type(event.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                and event.payload.facts.objective_id == proposed_plan.goal_objective_id), None)
            if facts is None or document_value_digest(asdict(facts)) != proposed_plan.goal_facts_digest:
                raise ValueError("resolved plan requires its exact durable original goal capture")
            expected = DocumentPlan.create(plan_id=expected.ref.plan_id, plan_revision=expected.ref.plan_revision,
                goal_objective_id=facts.objective_id, goal_facts_digest=document_value_digest(asdict(facts)),
                nodes=expected.nodes, remaining_target_paths=expected.remaining_target_paths,
                planning_goal=expected.planning_goal, unresolved_target=False)
        if expected != proposed_plan or binding.plan_ref != proposed_plan.ref:
            raise ValueError("proposed window differs from the host's exact dependency/goal projection")
        views = (replace(current[0], plan=proposed_plan, current_node_id=None, current_confirmation_event_id=None,
            nodes=tuple(DocumentNodeView(node.node_id, DocumentNodeState.BLOCKED,
                ("Pending exact node confirmation.",), (), (), (), DocumentNodeNextAction.CONFIRM_NODE)
                for node in proposed_plan.nodes)),)
    if len(views) != 1:
        raise ValueError("document node requires its current exact plan revision")
    view = views[0]
    node = next((node for node in view.plan.nodes if node.node_id == binding.node_id), None)
    events = read_session_authoring_events(session_id)
    by_id = {event.event_id: event for event in events}
    if binding.prior_confirmation_event_id != causal_view.current_confirmation_event_id:
        head = by_id.get(causal_view.current_confirmation_event_id)
        value = None if head is None else head.payload
        own = next((event.payload for event in events if type(event.payload) is CollaborationProposal
            and type(value) is CollaborationConfirmationPayload
            and (event.payload.proposal_id, event.payload.proposal_revision, event.payload.proposal_digest) ==
                (value.proposal_id, value.proposal_revision, value.proposal_digest)), None)
        if own is None or own.document_node != binding:
            raise ValueError("document prior confirmation changed; prepare a fresh exact disclosure")
    if node is None or binding.task_ref.session_id != session_id:
        raise ValueError("document node belongs to another plan/session")
    source = by_id.get(binding.task_ref.scope_event_id)
    current = authoring_authority.authoring_tasks(events).get(binding.task_ref.task_id)
    captured = None if source is None else getattr(source.payload, "authoring_scope", None)
    if (captured is None or current is None or current.task_revision != binding.task_ref.task_revision
            or (captured.task_id, captured.task_revision) != (binding.task_ref.task_id, binding.task_ref.task_revision)
            or binding.task_ref.scope_digest != "sha256:" + hashlib.sha256(json.dumps(
                asdict(captured), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            or current.target_paths != (node.target_path,)
            or scope is not None and (scope.task_id, scope.task_revision, scope.target_paths) !=
               (current.task_id, current.task_revision, (node.target_path,))):
        raise ValueError("document node requires exact current task/scope provenance")
    prior = tuple(event.payload.document_node for event in events
        if type(event.payload) in {CollaborationProposal, LeadRecommendationRequestPayload}
        and event.payload.document_node is not None
        and event.payload.document_node.plan_ref.plan_id == view.plan.ref.plan_id
        and event.payload.document_node.task_ref.task_id == binding.task_ref.task_id)
    if prior and any((old.budget_lineage_ref, old.budget_origin_step_id) !=
                     (binding.budget_lineage_ref, binding.budget_origin_step_id) for old in prior):
        raise ValueError("node/revision movement cannot reset its inherited budget")
    origin = by_id.get(binding.budget_lineage_ref.scope_event_id)
    origin_scope = None if origin is None else getattr(origin.payload, "authoring_scope", None)
    if (origin_scope is None or origin.step_id != binding.budget_origin_step_id
            or origin_scope.task_id != binding.budget_lineage_ref.task_id
            or origin_scope.task_revision != binding.budget_lineage_ref.task_revision
            or (origin.session_id, origin.conversation_id) !=
               (binding.budget_lineage_ref.session_id, binding.budget_lineage_ref.conversation_id)
            or binding.budget_lineage_ref.scope_digest != "sha256:" + hashlib.sha256(json.dumps(
                asdict(origin_scope), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            or binding.budget_lineage_ref.task_id != binding.task_ref.task_id):
        raise ValueError("document budget requires its actual task source lineage")
    admitted_scope = current if scope is None else scope
    if admitted_scope.intent is AuthoringIntent.AUTHORING and admitted_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
        readiness = inspect_core_document_readiness(session_id)
        needed = (None if node.target_kind is AuthoringTarget.GLOBAL_OUTLINE else readiness) if type(readiness) is CoreDocumentReadinessNeeded else require_readiness(node.target_kind, readiness)
        if needed is not None:
            raise ValueError(needed.message)
    for use in binding.candidate_uses:
        predecessor = next((x for x in view.plan.nodes if x.node_id == use.predecessor_node_id), None)
        event = by_id.get(use.candidate.candidate_event_id)
        candidate = event.payload.candidate if event is not None and type(event.payload) is ArtifactReviewCandidatePayload else None
        producer = next((x.payload for x in events if candidate is not None and type(x.payload) is CollaborationProposal
            and (x.payload.proposal_id, x.payload.proposal_revision, x.payload.proposal_digest) ==
                (event.payload.consultation_proposal_id, event.payload.consultation_proposal_revision, event.payload.consultation_proposal_digest)), None)
        if (predecessor is None or predecessor.node_id not in node.predecessor_node_ids or candidate is None
                or producer is None or producer.document_node is None
                or producer.document_node.plan_ref.plan_id != binding.plan_ref.plan_id
                or producer.document_node.node_id != predecessor.node_id
                or (candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest) !=
                   (use.candidate.candidate_id, use.candidate.candidate_revision, use.candidate.candidate_digest)
                or use.permitted_scope.exclusions or not use.permitted_scope.scope.targets
                or any(x.path != predecessor.target_path or x.start_line is not None for x in use.permitted_scope.scope.targets)
                or {path for op in candidate.change_set.operations for path in
                    ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))} != {predecessor.target_path}
                or use.user_conditions_digest != document_value_digest((view.plan.goal_facts_digest,
                    None if view.plan.planning_goal is None else view.plan.planning_goal.content_digest,
                    node.preservation_conditions, binding.task_ref.scope_digest))):
            raise ValueError("candidate input differs from the exact same-plan predecessor/task/purpose")
    return view


def automatic_document_node(
    session_id,
    scope,
    projection,
    *,
    session_history=None,
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
    document_prior_confirmation: Callable[[str, str], str | None],
):
    if scope is None or len(scope.target_paths) != 1:
        return None
    plans = projection.document_plans
    if not plans and session_history is not None:
        plans = tuple(view for row in session_history.conversations
            if any((type(e.payload) is CollaborationLeadJudgmentPayload and e.payload.document_plan is not None)
                   or (type(e.payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2} and e.payload.document_plan is not None)
                   for e in row.records)
            for view in row.document_plans())
    if not plans:
        return None
    history = read_session_authoring_events(session_id)
    by_id = {event.event_id: event for event in history}
    goal_tasks = {}
    for event in history:
        value = event.payload
        if type(value) is CollaborationLeadJudgmentPayload and value.document_plan is not None and value.authoring_scope is not None:
            goal_tasks[value.document_plan.ref.plan_id] = value.authoring_scope.task_id
        if type(value) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2} and value.document_plan is not None:
            judgment = by_id.get(value.lead_result_event_id)
            if judgment is not None and judgment.payload.authoring_scope is not None:
                goal_tasks[value.document_plan.ref.plan_id] = judgment.payload.authoring_scope.task_id
    allowed_goals = {key for key, task_id in goal_tasks.items() if task_id == scope.task_id or any(
        type(event.payload) is CollaborationProposal and event.payload.document_node is not None
        and event.payload.document_node.task_ref.task_id == scope.task_id
        and event.payload.document_node.plan_ref.plan_id == key for event in history)}
    choices = [(view, node) for view in plans for node in view.plan.nodes
               if node.target_path == scope.target_paths[0] and view.plan.ref.plan_id in allowed_goals]
    if len(choices) != 1:
        return None
    view, node = choices[0]
    task = next((item.ref for item in projection.available_tasks
                 if (item.ref.task_id, item.ref.task_revision) == (scope.task_id, scope.task_revision)), None)
    if task is None:
        return None
    prior = tuple(event.payload.document_node for event in history
        if type(event.payload) is CollaborationProposal and event.payload.document_node is not None
        and event.payload.document_node.plan_ref.plan_id == view.plan.ref.plan_id
        and event.payload.document_node.task_ref.task_id == task.task_id)
    if prior:
        budget, step = prior[0].budget_lineage_ref, prior[0].budget_origin_step_id
    else:
        origin = next(event for event in history if event.event_id == task.scope_event_id)
        budget, step = task, origin.step_id
    return DocumentNodeBinding(view.plan.ref, node.node_id, task, budget, step,
        prior_confirmation_event_id=document_prior_confirmation(session_id, view.plan.ref.plan_id))


def document_candidate_inputs(
    session_id,
    binding,
    inputs,
    *,
    capture_editorial_snapshot: Callable[[str], SessionHistorySnapshot],
    prepare_document_selection: Callable[[str, DocumentNodeSelection], tuple[DocumentPlan, DocumentNodeSpec]],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
    inspect_core_document_readiness: Callable[[str], CoreDocumentReadiness | CoreDocumentReadinessNeeded],
):
    if binding is None or not binding.candidate_uses:
        return inputs
    if type(binding) is DocumentNodeSelection:
        prepare_document_selection(session_id, binding)
    else:
        validate_document_node(
            session_id, binding,
            capture_editorial_snapshot=capture_editorial_snapshot,
            prepare_document_selection=prepare_document_selection,
            read_session_authoring_events=read_session_authoring_events,
            inspect_core_document_readiness=inspect_core_document_readiness,
        )
    known = {item.input_id: item for item in inputs}
    if len(known) != len(inputs):
        raise ValueError("document candidate input IDs are ambiguous")
    additions = []
    for number, use in enumerate(binding.candidate_uses, 1):
        alias = f"plan_candidate_broken_{number}"
        expected = CollaborationInputRevision(alias, CollaborationInputKind.ARTIFACT_CANDIDATE,
            f"artifact-candidate/{use.candidate.candidate_id}", CollaborationInputState.PRESENT,
            use.candidate.candidate_digest)
        if alias in known:
            if type(known[alias]) is not CollaborationInputRevision or known[alias] != expected:
                raise ValueError("document candidate alias collides with a current input")
            continue
        additions.append(expected)
    return (*inputs, *additions)
