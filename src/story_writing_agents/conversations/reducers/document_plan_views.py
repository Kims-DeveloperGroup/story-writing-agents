"""Project immutable document plan views and lineage costs."""

from __future__ import annotations

import hashlib

from dataclasses import replace

from ..event_contracts.artifact_apply import (
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewSynthesisPayload,
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
from ..event_contracts.execution import CollaborationMessagePayload
from ..event_contracts.lead_outcomes import CollaborationLeadJudgmentPayload
from ..event_vocabulary import (
    ArtifactApplicationOutcomeKind,
    ArtifactReviewDecision,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    CollaborationActorKind,
    CollaborationConfirmationDecision,
    CollaborationProviderCallState,
    CollaborationStepState,
)
from ..events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationRequestPayload,
)
from ..views.collaboration_history_values import ArtifactObjective
from ...document_plan import (
    DocumentCandidateRef,
    DocumentNodeBinding,
    DocumentNodeNextAction,
    DocumentNodeState,
    DocumentNodeView,
    DocumentPlanView,
)
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
)
from ...writing_artifacts import ArtifactRenameOperation


def document_lineage_cost(events: tuple[CollaborationEvent, ...], binding: DocumentNodeBinding,
                          *, current_step_id: str | None = None) -> tuple[int, int, int]:
    """Count existing submitted records, never replenish a plan/node allowance."""
    steps = {binding.budget_origin_step_id}
    plan_steps = set(steps)
    by_id = {event.event_id: event for event in events}
    for event in events:
        value = event.payload
        if type(value) is CollaborationProviderCallPayload and value.document_goal is not None and value.document_goal.plan_id == binding.plan_ref.plan_id:
            plan_steps.add(event.step_id)
            if value.document_budget_task_id == binding.budget_lineage_ref.task_id:
                steps.add(event.step_id)
        if ((type(value) is CollaborationLeadJudgmentPayload and value.document_plan is not None)
                or (type(value) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2} and value.document_plan is not None)):
            if value.document_plan.ref.plan_id == binding.plan_ref.plan_id:
                plan_steps.add(event.step_id)
        if type(value) is CollaborationLeadTaskTransitionPayload and value.receipt.planning_step_id is not None:
            origin = by_id.get(value.receipt.origin_event_id)
            selection = (origin.payload.document_selection if origin is not None
                and type(origin.payload) is CollaborationLeadTaskDecisionPayload else None)
            if selection is not None and selection.plan_ref.plan_id == binding.plan_ref.plan_id:
                plan_steps.add(value.receipt.planning_step_id)
                if value.receipt.target_task_id == binding.budget_lineage_ref.task_id:
                    steps.add(value.receipt.planning_step_id)
        if (type(value) in {CollaborationProposal, CollaborationLeadJudgmentPayload}
                and value.authoring_scope is not None
                and value.authoring_scope.task_id == binding.budget_lineage_ref.task_id):
            steps.add(event.step_id); plan_steps.add(event.step_id)
        node = value.document_node if type(value) in {CollaborationProposal, LeadRecommendationRequestPayload, CollaborationProviderCallPayload, CollaborationLeadJudgmentPayload} else None
        if node is not None:
            if node.plan_ref.plan_id == binding.plan_ref.plan_id:
                plan_steps.add(event.step_id)
            if (node.budget_lineage_ref == binding.budget_lineage_ref
                    and node.budget_origin_step_id == binding.budget_origin_step_id):
                steps.add(event.step_id)
    if current_step_id is not None:
        steps.add(current_step_id); plan_steps.add(current_step_id)
    calls = {(event.conversation_id, event.payload.invocation_id, event.payload.call_id) for event in events
        if event.step_id in steps and type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED}
    total = {(event.conversation_id, event.payload.invocation_id, event.payload.call_id) for event in events
        if event.step_id in plan_steps and type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED}
    rounds = {(event.conversation_id, event.payload.execution_attempt_id) for event in events if event.step_id in steps
        and type(event.payload) is CollaborationStepStatePayload
        and event.payload.state is CollaborationStepState.RUNNING}
    return len(calls), len(rounds), len(total)


def project_document_plan_views(events: tuple[CollaborationEvent, ...],
                                objectives: tuple[ArtifactObjective, ...]) -> tuple[DocumentPlanView, ...]:
    """Replay exact goal/cursor/Apply facts; mutable readiness stays with runtime."""
    proposals = {(x.payload.proposal_id, x.payload.proposal_revision, x.payload.proposal_digest): x.payload
                 for x in events if type(x.payload) is CollaborationProposal}
    requests = {x.payload.application_request.application_id: x.payload for x in events
                if type(x.payload) is ArtifactApplicationRequestPayload}
    by_id = {event.event_id: event for event in events}
    source_records = {}
    for event in events:
        source_records.setdefault(event.event_id, []).append(event)
    for event in events:
        value = event.payload
        binding = value.document_node if type(value) in {CollaborationProposal, CollaborationLeadJudgmentPayload,
            LeadRecommendationRequestPayload, CollaborationProviderCallPayload, ArtifactObjectivePayload, ArtifactObjectivePayloadV2} else None
        if binding is None or binding.prior_confirmation_event_id is None:
            continue
        captured = source_records.get(binding.prior_confirmation_event_id, ())
        if not captured:
            continue  # Foreign-source completeness belongs to session capture.
        source = captured[0]
        decision = source.payload
        prior = proposals.get((decision.proposal_id, decision.proposal_revision, decision.proposal_digest)) if type(decision) is CollaborationConfirmationPayload else None
        if (len(captured) != 1 or type(decision) is not CollaborationConfirmationPayload
                or decision.decision is not CollaborationConfirmationDecision.CONFIRM
                or source.session_id != event.session_id or binding.task_ref.session_id != event.session_id
                or prior is None or prior.document_node is None
                or prior.document_node.plan_ref.plan_id != binding.plan_ref.plan_id
                or (source.conversation_id == event.conversation_id and source.sequence >= event.sequence)):
            raise ValueError("document binding prior reference is not a causal exact same-plan/session Confirm")
    declarations = {}
    goal_texts = {}
    for event in events:
        value = event.payload
        plan = value.document_plan if type(value) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload, ArtifactObjectivePayloadV2} else None
        if plan is None:
            continue
        declarations[plan.ref] = plan
        if plan.planning_goal is not None:
            goal = plan.planning_goal
            origin = by_id.get(goal.user_event_id)
            if origin is None and goal.conversation_id != event.conversation_id:
                # A transcript retains the exact foreign reference. Its session
                # snapshot, not an owner-local replay, qualifies foreign bytes.
                continue
            if (origin is None or (origin.session_id, origin.conversation_id) != (goal.session_id, goal.conversation_id)
                    or (origin.conversation_id == event.conversation_id and origin.sequence >= event.sequence)
                    or origin.sender.kind is not CollaborationActorKind.USER
                    or type(origin.payload) is not CollaborationMessagePayload
                    or goal.content_digest != "sha256:" + hashlib.sha256(origin.payload.content.encode("utf-8")).hexdigest()):
                raise ValueError("literal planning goal differs from its exact USER source")
            goal_texts[plan.ref.plan_id] = origin.payload.content
        elif type(value) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
            goal_texts[plan.ref.plan_id] = value.facts.request
    approvals = []
    for event in events:
        if type(event.payload) is CollaborationConfirmationPayload and event.payload.decision is CollaborationConfirmationDecision.CONFIRM:
            value = event.payload
            proposal = proposals.get((value.proposal_id, value.proposal_revision, value.proposal_digest))
            if proposal is not None and proposal.document_node is not None:
                approvals.append((event, proposal.document_node))
                if proposal.document_plan is not None:
                    declarations[proposal.document_plan.ref] = proposal.document_plan
    approval_by_id = {}
    for event, node in approvals:
        if event.event_id in approval_by_id or node.task_ref.session_id != event.session_id:
            raise ValueError("document confirmation identity/session is contradictory")
        approval_by_id[event.event_id] = (event, node)
    parents = {identity: set() for identity in approval_by_id}
    owners = {}
    for identity, (event, node) in approval_by_id.items():
        owners.setdefault((node.plan_ref.plan_id, event.conversation_id), []).append(event)
        previous = node.prior_confirmation_event_id
        if previous is None:
            continue
        source = by_id.get(previous)
        if source is None:
            # Owner-local replay retains foreign references. The complete
            # session owner requires their actual captured source records.
            continue
        captured = approval_by_id.get(previous)
        if (captured is None or previous == identity or source.session_id != event.session_id
                or captured[1].plan_ref.plan_id != node.plan_ref.plan_id
                or (source.conversation_id == event.conversation_id and source.sequence >= event.sequence)):
            raise ValueError("document prior confirmation must be an earlier exact same-plan/session Confirm")
        parents[identity].add(previous)
    for records in owners.values():
        ordered = sorted(records, key=lambda event: event.sequence)
        for before, after in zip(ordered, ordered[1:]):
            parents[after.event_id].add(before.event_id)
    children = {identity: set() for identity in parents}
    remaining = {identity: len(values) for identity, values in parents.items()}
    for identity, values in parents.items():
        for previous in values:
            children[previous].add(identity)
    ready = [identity for identity, count in remaining.items() if not count]
    causal_order = []
    while ready:
        identity = ready.pop()
        causal_order.append(identity)
        for child in children[identity]:
            remaining[child] -= 1
            if not remaining[child]:
                ready.append(child)
    if len(causal_order) != len(parents):
        raise ValueError("document confirmation references form a cycle")
    def precedes(older, newer):
        pending = list(parents[newer]); visited = set()
        while pending:
            identity = pending.pop()
            if identity == older:
                return True
            if identity not in visited:
                visited.add(identity); pending.extend(parents[identity])
        return False
    node_heads = {}
    for identity in causal_order:
        event, node = approval_by_id[identity]
        key = (node.plan_ref.plan_id, node.node_id)
        heads = node_heads.setdefault(key, set())
        heads.difference_update(old for old in tuple(heads) if precedes(old, identity))
        heads.add(identity)
    approved_refs = {node.plan_ref for _, node in approvals}
    plans = {}
    for plan in sorted(declarations.values(), key=lambda item: item.ref.plan_revision):
        old = plans.get(plan.ref.plan_id)
        # The first goal declaration is observable without execution authority.
        # A replacement window becomes active only through its exact Confirm.
        if old is None or plan.ref in approved_refs:
            if old is not None and old.ref.plan_revision == plan.ref.plan_revision and old.ref != plan.ref:
                raise ValueError("document plan revision has conflicting exact contents")
            plans[plan.ref.plan_id] = plan
    requested = {}
    revisions = {}
    for event in events:
        value = event.payload
        if type(value) not in {CollaborationProposal, CollaborationLeadJudgmentPayload, LeadRecommendationRequestPayload}:
            continue
        scope = value.authoring_scope
        if scope is None or scope.intent is not AuthoringIntent.AUTHORING or scope.activity not in {
                AuthoringActivity.DRAFT, AuthoringActivity.REVISE, AuthoringActivity.REVIEW}:
            continue
        for path in scope.target_paths:
            key = (scope.task_id, path)
            revisions[key] = max(revisions.get(key, 0), scope.task_revision)
        binding = value.document_node
        if binding is not None:
            requested.setdefault((binding.plan_ref.plan_id, binding.node_id), []).append((event, binding))
    def rewrite_pending(plan_id, node_id, approval):
        confirmed_event, confirmed_binding = approval
        for event, binding in requested.get((plan_id, node_id), ()):
            if binding.task_ref == confirmed_binding.task_ref:
                continue
            older = tuple(identity for identity, (_, record) in approval_by_id.items()
                if record.plan_ref.plan_id == plan_id and record.node_id == node_id and record.task_ref == binding.task_ref)
            if any(precedes(identity, confirmed_event.event_id) for identity in older):
                continue
            if event.conversation_id == confirmed_event.conversation_id:
                if event.sequence > confirmed_event.sequence:
                    return True
                continue
            previous = binding.prior_confirmation_event_id
            if previous in approval_by_id and (previous == confirmed_event.event_id or precedes(confirmed_event.event_id, previous)):
                return True
        return False
    views = []
    for plan in plans.values():
        confirmed = {}
        completed = {}
        candidates = {}
        active_ids = {identity for identity, (_, node) in approval_by_id.items() if node.plan_ref.plan_id == plan.ref.plan_id}
        heads = active_ids - {previous for identity in active_ids for previous in parents[identity]}
        cursor_ambiguous = len(heads) > 1
        current_id = next(iter(heads)) if len(heads) == 1 else None
        current_binding = None if current_id is None else approval_by_id[current_id][1]
        if current_binding is not None and current_binding.plan_ref != plan.ref:
            raise ValueError("current document cursor cannot roll back the active plan window")
        node_ambiguities = set()
        for node in plan.nodes:
            eligible = node_heads.get((plan.ref.plan_id, node.node_id), set())
            if len(eligible) == 1:
                confirmed[node.node_id] = approval_by_id[next(iter(eligible))]
            elif eligible:
                node_ambiguities.add(node.node_id)
        for event in events:
            value = event.payload
            if type(value) is ArtifactReviewCandidatePayload:
                producer = proposals.get((value.consultation_proposal_id, value.consultation_proposal_revision, value.consultation_proposal_digest))
                source = None if producer is None else producer.document_node
                if source is not None and source.plan_ref.plan_id == plan.ref.plan_id:
                    candidate = value.candidate
                    candidates.setdefault(source.node_id, []).append(DocumentCandidateRef(event.event_id,
                        candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest))
            if type(value) is ArtifactApplicationResultPayload and value.outcome in {ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED}:
                request = requests.get(value.application_id)
                if request is not None and request.reviewed_proposal.proposal_digest == value.reviewed_proposal_digest:
                    reviewed = request.reviewed_proposal
                    proposal = proposals.get((reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision, reviewed.consultation_proposal_digest))
                    node = None if proposal is None else proposal.document_node
                    if node is not None and node.plan_ref.plan_id == plan.ref.plan_id:
                        declared = declarations.get(node.plan_ref)
                        target = None if declared is None else next((item.target_path for item in declared.nodes if item.node_id == node.node_id), None)
                        paths = {path for op in reviewed.candidate.change_set.operations for path in
                            ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))}
                        if target is None or paths != {target} or proposal.authoring_scope.target_paths != (target,):
                            raise ValueError("document Apply proof differs from its declared exact node target")
                        completed.setdefault(node.node_id, []).append((event.event_id, node))
        creation = {event.step_id for event in events
                    if type(event.payload) in {CollaborationLeadJudgmentPayload, ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
                    and event.payload.document_plan is not None and event.payload.document_plan.ref.plan_id == plan.ref.plan_id}
        goal_tasks = {event.payload.authoring_scope.task_id for event in events if event.step_id in creation
                      and type(event.payload) is CollaborationLeadJudgmentPayload and event.payload.authoring_scope is not None}
        funded = {event.step_id for event in events if type(event.payload) is CollaborationProviderCallPayload
                  and event.payload.document_goal is not None and event.payload.document_goal.plan_id == plan.ref.plan_id}
        own = creation | {event.step_id for event in events if type(event.payload) is CollaborationProviderCallPayload
                  and event.payload.document_goal is not None and event.payload.document_goal.plan_id == plan.ref.plan_id
                  and event.payload.document_budget_task_id in goal_tasks}
        paths_by_node = {}
        for declaration in declarations.values():
            if declaration.ref.plan_id != plan.ref.plan_id:
                continue
            for item in declaration.nodes:
                if item.node_id in paths_by_node and paths_by_node[item.node_id] != item.target_path:
                    raise ValueError("document node identity cannot change target across windows")
                paths_by_node[item.node_id] = item.target_path
        qualified_completed = set()
        for node_id, path in paths_by_node.items():
            eligible = node_heads.get((plan.ref.plan_id, node_id), set())
            if len(eligible) != 1:
                continue
            approval = approval_by_id[next(iter(eligible))]
            binding = approval[1]
            if revisions.get((binding.task_ref.task_id, path), binding.task_ref.task_revision) != binding.task_ref.task_revision:
                continue
            if rewrite_pending(plan.ref.plan_id, node_id, approval):
                continue
            if any(record.task_ref == binding.task_ref for _, record in completed.get(node_id, ())):
                qualified_completed.add(path)
        if current_binding is not None:
            calls, rounds, total = document_lineage_cost(events, current_binding)
        else:
            calls = len({(event.conversation_id, event.payload.invocation_id, event.payload.call_id) for event in events
                if event.step_id in own and type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED})
            rounds = len({(event.conversation_id, event.payload.execution_attempt_id) for event in events if event.step_id in own
                and type(event.payload) is CollaborationStepStatePayload and event.payload.state is CollaborationStepState.RUNNING})
            total = len({(event.conversation_id, event.payload.invocation_id, event.payload.call_id) for event in events
                if event.step_id in creation | funded and type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED})
        node_actions = {}
        terminal_applications = {event.payload.application_id for event in events
                                 if type(event.payload) is ArtifactApplicationResultPayload}
        for event in events:
            value = event.payload
            owner = value if type(value) is CollaborationProposal else None
            if type(value) in {ArtifactReviewSynthesisPayload, CollaborationLeadSynthesisPayload}:
                owner = proposals.get((value.consultation_proposal_id, value.consultation_proposal_revision,
                    value.consultation_proposal_digest)) if type(value) is ArtifactReviewSynthesisPayload else proposals.get(
                    (value.proposal_id, value.proposal_revision, value.proposal_digest))
            if type(value) is ArtifactApplicationRequestPayload:
                reviewed = value.reviewed_proposal
                owner = proposals.get((reviewed.consultation_proposal_id, reviewed.consultation_proposal_revision,
                                       reviewed.consultation_proposal_digest))
            bound = None if owner is None else owner.document_node
            if bound is None or bound.plan_ref != plan.ref:
                continue
            approved = confirmed.get(bound.node_id)
            if approved is not None and bound.task_ref != approved[1].task_ref:
                continue
            if type(value) is ArtifactReviewSynthesisPayload and value.decision in {ArtifactReviewDecision.REVISE, ArtifactReviewDecision.REJECT}:
                node_actions[bound.node_id] = (DocumentNodeState.FOLLOWUP_REQUIRED, DocumentNodeNextAction.RETURN_TO_WRITER)
            elif type(value) is ArtifactApplicationRequestPayload and value.application_request.application_id not in terminal_applications:
                node_actions[bound.node_id] = (DocumentNodeState.IN_PROGRESS, DocumentNodeNextAction.APPLY_CANDIDATE)
            elif type(value) is CollaborationLeadSynthesisPayload:
                node_actions[bound.node_id] = (DocumentNodeState.IN_PROGRESS, DocumentNodeNextAction.REVIEW_CANDIDATE)
            elif type(value) is CollaborationProposal and approved is None:
                node_actions[bound.node_id] = (DocumentNodeState.READY, DocumentNodeNextAction.CONFIRM_NODE)
        if cursor_ambiguous:
            budgets = {(approval_by_id[identity][1].budget_lineage_ref,
                        approval_by_id[identity][1].budget_origin_step_id): approval_by_id[identity][1] for identity in heads}
            costs = tuple(document_lineage_cost(events, binding) for binding in budgets.values())
            calls = sum(cost[0] for cost in costs); rounds = sum(cost[1] for cost in costs)
            # Every lineage reports the same actual plan-wide submitted total.
            total = costs[0][2] if costs else total
        rows = []
        for node in plan.nodes:
            approved = confirmed.get(node.node_id)
            applied = tuple(sorted(identity for identity, binding in completed.get(node.node_id, ())
                if node.target_path in qualified_completed and approved is not None and binding.task_ref == approved[1].task_ref))
            done = bool(applied)
            state, next_action = ((DocumentNodeState.COMPLETE, DocumentNodeNextAction.COMPLETE) if done else
                node_actions.get(node.node_id, (DocumentNodeState.IN_PROGRESS, DocumentNodeNextAction.PREPARE_NODE)
                    if approved is not None else (DocumentNodeState.BLOCKED, DocumentNodeNextAction.REQUEST_INFORMATION)))
            if node.node_id in node_ambiguities:
                state, next_action = DocumentNodeState.BLOCKED, DocumentNodeNextAction.REQUEST_INFORMATION
            rows.append(DocumentNodeView(node.node_id, state, () if state is not DocumentNodeState.BLOCKED else
                ("Current source/readiness qualification required.",), (), applied,
                (approved[0].event_id,) if approved is not None and approved[1].candidate_uses else (),
                next_action,
                candidate_refs=tuple(dict.fromkeys(candidates.get(node.node_id, ())))))
            if cursor_ambiguous and not done:
                rows[-1] = replace(rows[-1], state=DocumentNodeState.BLOCKED,
                    blocking_conditions=("Independent confirmed document heads require explicit reconciliation; current budget is unavailable.",),
                    next_action=DocumentNodeNextAction.REQUEST_INFORMATION)
        views.append(DocumentPlanView(plan, None if current_binding is None else current_binding.node_id,
            tuple(rows), calls, rounds, total, 0 if cursor_ambiguous else max(0, COLLABORATION_PHYSICAL_CALL_LIMIT - calls),
            0 if cursor_ambiguous else max(0, COLLABORATION_ROUND_LIMIT - rounds), goal_text=goal_texts.get(plan.ref.plan_id),
            current_confirmation_event_id=current_id, cursor_ambiguous=cursor_ambiguous,
            completed_target_paths=tuple(sorted(qualified_completed))))
    return tuple(views)
