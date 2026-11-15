"""Call-local proposal, document, premise, and pinned editorial projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from ..agent_task_projection import project_agent_task_metadata
from ..conversations import (
    ArtifactObjectiveFacts,
    CollaborationActorKind,
    CollaborationInputRevision,
    CollaborationLeadJudgmentPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    authoring_scope_to_dict,
)
from ..conversations.events import (
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
    CollaborationPremiseSelectionPayload,
    CollaborationPremiseSource,
    CollaborationSharedPremise,
    LeadSharedPremiseIntent,
    shared_premises_to_dict,
)
from ..conversations.session_context import SessionContextCapacityError, SessionHistorySnapshot
from ..core_document_readiness import AuthoringTarget, DEPENDENCIES, targets_for_paths
from ..document_plan import (
    DocumentNodeSpec,
    DocumentPlan,
    DocumentPlanningGoalRef,
    document_value_digest,
)
from ..editorial_scope import (
    EditorialScope,
    EditorialScopeCoverage,
    EditorialScopeKind,
    EditorialScopeTarget,
)
from ..editorial_state import EditorialRecordRef, editorial_ref_from_dict, editorial_ref_to_dict
from ..errors import BackendProtocolError
from ..lead_premise_decisions import premise_value_to_dict
from . import authoring_authority, reply_contract_values
from ..runtime_resources import collaboration_event_journal as event_journal
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringContribution,
    AuthoringContributionRole,
    AuthoringIntent,
    artifact_domain,
    authoring_scope_rules,
    required_authoring_specialists,
    stage_plan_to_dict,
)
from ..writing_artifacts import ArtifactReviewDomain

__all__ = ('build_document_plan', 'planning_goal_plan', 'editorial_pinned_view', 'compact_document_plan_input', 'captured_premise_intents', 'proposal_disclosure', 'premise_user_impact', 'premise_control_origin', 'remap_shared_premises',)


def build_document_plan(facts, scope, inputs, basis=None, previous=None, planning_goal=None, selected_target_path=None) -> DocumentPlan:
    goal_paths = (() if selected_target_path is None else (selected_target_path,)) if facts is None else facts.target_paths
    selected = selected_target_path or next((p for p in goal_paths if targets_for_paths((p,)) == (AuthoringTarget.MANUSCRIPT,)), None)
    paths = ({target.value for target in AuthoringTarget if target is not AuthoringTarget.MANUSCRIPT}
             if facts is None else {p for p in goal_paths if p in {x.value for x in AuthoringTarget}})
    if selected is not None:
        paths.add(selected)
    required = set(targets_for_paths(tuple(paths)))
    for target in tuple(required):
        required.update(DEPENDENCIES[target])
    if not required:
        raise ValueError("a document plan requires an explicitly selected canonical target")
    ordered = tuple(target for target in AuthoringTarget if target in required)
    path_for = {target: selected if target is AuthoringTarget.MANUSCRIPT else target.value for target in ordered}
    ids = {target: "dn_" + hashlib.sha256(path_for[target].encode()).hexdigest()[:24] for target in ordered}
    units = () if basis is None else tuple(x.book_unit_id for x in basis.assessment.book_basis.whole_book_units)
    known = tuple(dict.fromkeys(item if type(item) is str else item.input_id for item in inputs))
    nodes = []
    for target in ordered:
        path = path_for[target]
        retained = None if previous is None else next((item for item in previous.nodes if item.target_path == path), None)
        if retained is not None:
            nodes.append(retained)
            continue
        owner = artifact_domain(path)
        affected = set(() if scope is None else scope.domains) | {owner}
        if target is AuthoringTarget.MANUSCRIPT:
            affected.update(ArtifactReviewDomain)
        domains = tuple(domain for domain in ArtifactReviewDomain if domain in affected)
        contributions = tuple(AuthoringContribution(domain, AuthoringContributionRole.DRAFT_OWNER
            if domain is owner else AuthoringContributionRole.CONSTRAINT_ASSESSOR) for domain in domains)
        rule = authoring_scope_rules(AuthoringActivity.DRAFT, AuthoringIntent.AUTHORING,
            domains, contributions, target_paths=(path,))
        nodes.append(DocumentNodeSpec(ids[target], target, path,
            EditorialScopeCoverage(EditorialScope(EditorialScopeKind.KNOWN, (EditorialScopeTarget(path),))),
            units, tuple(ids[x] for x in DEPENDENCIES[target]), rule, known, known,
            tuple(dict.fromkeys((*known, *((planning_goal.user_event_id,) if planning_goal is not None else
                ((facts.origin_user_event_id,) if type(facts) is ArtifactObjectiveFacts else
                 ((facts.origin_recommendation_decision_event_id,) if facts is not None else ())))))),
            "Prepare the exact document under the captured USER goal and current prerequisites."))
    plan_id = ("dp_" + (facts.objective_id if facts is not None else planning_goal.user_event_id)
               if previous is None else previous.ref.plan_id)
    remaining = tuple(path for path in goal_paths if path not in path_for.values())
    candidate = DocumentPlan.create(plan_id=plan_id,
        plan_revision=1 if previous is None else previous.ref.plan_revision,
        goal_objective_id=None if facts is None else facts.objective_id,
        goal_facts_digest=None if facts is None else document_value_digest(asdict(facts)),
        nodes=tuple(nodes), remaining_target_paths=remaining, planning_goal=planning_goal,
        unresolved_target=facts is None)
    if previous is not None and candidate != previous:
        candidate = DocumentPlan.create(plan_id=plan_id, plan_revision=previous.ref.plan_revision + 1,
            goal_objective_id=candidate.goal_objective_id, goal_facts_digest=candidate.goal_facts_digest,
            nodes=tuple(nodes), remaining_target_paths=remaining, planning_goal=planning_goal,
            unresolved_target=facts is None)
    return candidate

def planning_goal_plan(session_id, conversation_id, turn_id, outcome, inputs, events, authoring_target=None):
    if authoring_target is not AuthoringTarget.MANUSCRIPT:
        return None
    scope = outcome.authoring_scope
    if scope is None or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} or scope.target_paths:
        return None
    prior = tuple(event.payload.document_plan for event in events
        if type(event.payload) is CollaborationLeadJudgmentPayload
        and event.payload.document_plan is not None and event.payload.authoring_scope is not None
        and event.payload.authoring_scope.task_id == scope.task_id)
    if prior:
        return prior[0]
    origins = tuple(event for event in events if event.turn_id == turn_id
        and type(event.payload) is CollaborationMessagePayload and event.sender.kind is CollaborationActorKind.USER
        and event.recipient.kind is CollaborationActorKind.LEAD)
    if len(origins) != 1:
        return None
    origin = origins[0]
    goal = DocumentPlanningGoalRef(session_id, conversation_id, origin.event_id,
        "sha256:" + hashlib.sha256(origin.payload.content.encode("utf-8")).hexdigest())
    return build_document_plan(None, scope, inputs, outcome.core_document_readiness, planning_goal=goal)

def editorial_pinned_view(snapshot: SessionHistorySnapshot, refs: tuple[EditorialRecordRef, ...],
                          expected_context_digest: str | None) -> dict:
    state = snapshot.editorial_state()
    if expected_context_digest is None or state.context_digest(refs) != expected_context_digest:
        raise SessionContextHistoryChangedError()
    view = snapshot.editorial_view()
    if view is None:
        raise SessionContextCapacityError()
    needed = set(state.context_refs(refs))
    by_ref = {record.ref: record for record in state.revisions}
    todo = list(needed)
    while todo:
        ref = todo.pop()
        record = by_ref[ref]
        dependencies = set(record.context_refs) | set(record.request.relation_refs())
        for fact in state.scope_state(ref).relations if ref in {item.ref for item in state.current_records} else ():
            dependencies.update((fact.origin_ref, fact.decision.other_ref))
        for dependency in dependencies - needed:
            needed.add(dependency)
            todo.append(dependency)
    rows = [item for item in view["records"] if editorial_ref_from_dict(item["record_ref"]) in needed]
    if len(rows) != len(needed):
        raise SessionContextCapacityError()
    ids = {ref.record_id for ref in needed}
    return {**view, "records": rows, "selected_refs": [editorial_ref_to_dict(ref) for ref in refs],
        "editorial_context_digest": expected_context_digest,
        "scope_states": [item for item in view["scope_states"] if item["record_ref"]["record_id"] in ids],
        "scope_relations": [item for item in view["scope_relations"] if item["origin_ref"]["record_id"] in ids],
        "applied_facts": [item for item in view["applied_facts"] if item["record_ref"]["record_id"] in ids],
        "application_attempts": [item for item in view["application_attempts"] if item["record_ref"]["record_id"] in ids],
        "provenance_gaps": [item for item in view["provenance_gaps"]
                            if editorial_ref_from_dict(item["record_ref"]) in needed]}

def compact_document_plan_input(value: dict[str, object]) -> dict[str, object]:
    """Reference only an identical full plan already supplied in this input."""
    work = value.get("document_work_plan")
    objectives = value.get("artifact_objectives")
    if type(work) is not dict or type(objectives) is not dict:
        return value
    shared = work.get("plan")
    rows = objectives.get("objectives")
    if type(shared) is not dict or type(rows) is not list:
        return value
    reference = shared.get("ref")
    if type(reference) is not dict or set(reference) != {"plan_id", "plan_revision", "plan_digest"}:
        return value
    shared_bytes = None
    compacted = []
    changed = False
    for row in rows:
        candidate = row.get("document_plan") if type(row) is dict else None
        if (type(candidate) is dict and candidate.get("ref") == reference
                and "document_plan_ref" not in row):
            if shared_bytes is None:
                shared_bytes = json.dumps(shared, ensure_ascii=False, sort_keys=True,
                    allow_nan=False, separators=(",", ":")).encode("utf-8")
            if json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                    allow_nan=False, separators=(",", ":")).encode("utf-8") == shared_bytes:
                row = {key: item for key, item in row.items() if key != "document_plan"}
                row["document_plan_ref"] = dict(reference)
                changed = True
        compacted.append(row)
    if not changed:
        return value
    return {**value, "artifact_objectives": {**objectives, "objective": compacted}}

def captured_premise_intents(basis: CollaborationProposalBasis) -> tuple[LeadSharedPremiseIntent, ...]:
    return tuple(LeadSharedPremiseIntent(item.slot_id, item.selected_premise,
                                        tuple(source.input_revision.input_id for source in item.sources))
                 for item in basis.shared_premises)

def proposal_disclosure(proposal: CollaborationProposal, message: str) -> str:
    scope = proposal.authoring_scope
    if scope is None or scope.schema_version < 3:
        return message
    disclosure = {
        **({"stage_plan": stage_plan_to_dict(proposal.stage_plan)} if proposal.stage_plan is not None else {}),
        "writing_task_scope": authoring_scope_to_dict(scope),
        "final_reviewers": list(required_authoring_specialists(scope)),
        "shared_premises": shared_premises_to_dict(proposal.basis.shared_premises),
        "rounds_spent": proposal.budget.rounds_spent,
        "physical_calls_spent": proposal.budget.physical_calls_spent,
        "max_additional_physical_calls": proposal.budget.max_additional_physical_calls,
        "reserved_preparation_and_final_review_calls": authoring_authority.future_authoring_floor(scope, proposal.basis.inputs),
    }
    return message + "\n\nProposed responsibilities, immutable premises and bounded cost: " + json.dumps(
        project_agent_task_metadata(disclosure), ensure_ascii=False, sort_keys=True,
    )

def premise_user_impact(spec, step_id, inputs, events):
    chosen = next((event.payload.capture for event in reversed(events)
        if type(event.payload) is CollaborationPremiseSelectionPayload
        and any(item.input_id == "selected_premise" and item.locator == f"conversation/{event.event_id}" for item in inputs)), None)
    if spec is None and chosen is None:
        return reply_contract_values.LEAD_PROPOSAL_USER_IMPACT
    submitted = {event.payload.call_id for event in events
        if type(event.payload) is CollaborationProviderCallPayload
        and event.payload.state is CollaborationProviderCallState.SUBMITTED}
    admission = next((event.payload.admission for event in reversed(events)
        if type(event.payload) is CollaborationPremiseFreshWorkAdmissionPayload and event.step_id == step_id), None)
    impact = ("Receive whole provisional premise options in one terminal analysis round; a distinct USER choice and fresh authoring Confirm remain required. No drafting or artifact authority."
              if spec is not None else reply_contract_values.LEAD_PROPOSAL_USER_IMPACT)
    facts = {"recorded_conversation_submissions": len(submitted), "cost_lineages_remain_separate": True}
    if chosen is not None:
        facts.update(analysis_report_event_id=chosen.selection.report_event_id,
            analysis_submitted_call_ids=list(chosen.cost_link.analysis.submitted_call_ids),
            analysis_rounds=chosen.cost_link.analysis.rounds_spent)
    if admission is not None:
        facts["front_call_cap"] = admission.front_physical_call_limit
        facts["prior_admission_ref"] = None if admission.prior_admission_ref is None else premise_value_to_dict(admission.prior_admission_ref)
        facts["prior_analysis_report_ref"] = None if admission.prior_analysis_report_ref is None else premise_value_to_dict(admission.prior_analysis_report_ref)
        facts["prior_authoring_cost"] = None if admission.prior_authoring_source_ref is None else premise_value_to_dict(admission.prior_authoring_source_ref.cost)
    return impact + "\nRuntime historical cost receipt (no prior authority or refunds): " + json.dumps(facts, ensure_ascii=False, sort_keys=True)

def premise_control_origin(events, selection, text):
    matches = tuple(event for event in events if type(event.payload) is CollaborationPremiseDecisionPayload
        and event.payload.selection.request_id == selection.request_id)
    if not matches:
        return None
    if len(matches) != 1 or matches[0].payload.selection != selection:
        raise ValueError("premise request ID cannot change its exact USER control")
    origin = matches[0]
    messages = tuple(event for event in events if event.turn_id == origin.turn_id
        and event.step_id == origin.step_id and event.sender == event_journal.USER_ACTOR
        and type(event.payload) is CollaborationMessagePayload)
    if len(messages) != 1 or messages[0].payload.content != text:
        raise ValueError("premise request ID cannot change its exact USER text")
    return origin

def remap_shared_premises(
    basis: CollaborationProposalBasis,
    inputs: tuple[CollaborationInputRevision, ...],
) -> tuple[CollaborationSharedPremise, ...]:
    result = []
    for premise in basis.shared_premises:
        sources = []
        for source in premise.sources:
            matching = [item for item in inputs if replace(item, input_id=source.input_revision.input_id) == source.input_revision]
            if not matching:
                raise BackendProtocolError("selected inputs omit or change an immutable shared premise source")
            selected = next((item for item in matching if item.input_id == source.input_revision.input_id), matching[0])
            sources.append(CollaborationPremiseSource(selected, source.source_conversation_id))
        result.append(CollaborationSharedPremise(premise.slot_id, premise.selected_premise, tuple(sources)))
    return tuple(result)
