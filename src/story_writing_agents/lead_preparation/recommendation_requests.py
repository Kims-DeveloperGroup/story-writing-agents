"""Recommendation request policy values and proposal-basis construction."""

from __future__ import annotations

from ..authoring_admission import AuthoringAdmissionBasis
from ..authoring_task_lifecycle import task_identity_for_origin
from ..conversations import ArtifactDraftSource
from ..conversations import ArtifactObjectiveState
from ..conversations import COLLABORATION_PHYSICAL_CALL_LIMIT
from ..conversations import CollaborationAgentProfile
from ..conversations import CollaborationConfirmationDecision
from ..conversations import CollaborationConfirmationPayload
from ..conversations import CollaborationEvent
from ..conversations import CollaborationEvent as CollaborationEventV2
from ..conversations import CollaborationEventDraft
from ..conversations import CollaborationHistoryProjection
from ..conversations import CollaborationInputKind
from ..conversations import CollaborationInputRevision
from ..conversations import CollaborationInputState
from ..conversations import CollaborationLeadEvidence
from ..conversations import CollaborationLeadJudgmentPayload
from ..conversations import CollaborationLeadMaterialCapturePayload
from ..conversations import CollaborationLeadSynthesisPayload
from ..conversations import CollaborationLeadTaskDecisionPayload
from ..conversations import CollaborationLeadTaskTransitionPayload
from ..conversations import CollaborationProposal
from ..conversations import CollaborationProposalBasis
from ..conversations import CollaborationProviderCallPayload
from ..conversations import CollaborationProviderCallState
from ..conversations import CollaborationSpecialistReplyPayload
from ..conversations import CollaborationStepState
from ..conversations import CollaborationStepStatePayload
from ..conversations import LeadRecommendationActionKind
from ..conversations import LeadRecommendationRequestPayload
from ..conversations import LeadRecommendedAction
from ..conversations import is_new_task_recommendation_source
from ..conversations.events import CollaborationSharedPremise
from ..conversations.events import authoring_capture_identity
from ..conversations.events import confirmed_authoring_proposals
from ..core_document_readiness import CoreDocumentReadinessBasis
from ..editorial_state import EditorialRecordRef
from ..lead_preparation import authoring_authority
from ..lead_preparation import authoring_source_history
from ..lead_preparation import lead_proposal_inputs
from ..lead_preparation import proposal_basis_values
from ..lead_preparation import reply_contract_values
from ..lead_task_transitions import LeadTaskAction
from ..runtime_resources import collaboration_event_journal as event_journal
from ..specialist_routing import AuthoringActivity
from ..specialist_routing import AuthoringIntent
from ..specialist_routing import AuthoringScope
from ..specialist_routing import artifact_domain
from ..specialist_routing import authoring_source_scope_is_eligible
from ..specialist_routing import required_authoring_domains
from ..specialist_routing import validate_specialist_routing
from ..structured_output_validation import carry_validation
from ..structured_output_validation import validation_call
from ..structured_output_validation import validation_failure
from dataclasses import dataclass
from dataclasses import replace
from typing import Callable

__all__ = (
    "RecommendationRequestPolicyResult",
    "prepare_recommendation_request_policy",
    "recommendation_basis",
)


@dataclass(slots=True)
class RecommendationRequestPolicyResult:
    current: ArtifactDraftSource
    drafts: list[ArtifactDraftSource]
    admission: AuthoringAdmissionBasis | None
    selected_inputs: tuple[CollaborationInputRevision, ...]
    scope: AuthoringScope
    request_event_id: str
    source_proposal: CollaborationProposal | None
    source_attempt: str | None
    lineage_spent: int
    inherited_capture: tuple[CollaborationSharedPremise, ...]
    preparation_cap: int


def prepare_recommendation_request_policy(
    *,
    source: CollaborationEventDraft,
    action: LeadRecommendedAction,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence,
    events: tuple[CollaborationEvent, ...],
    projection: CollaborationHistoryProjection,
    previous: LeadRecommendationRequestPayload | None,
    preview_replies: tuple[CollaborationSpecialistReplyPayload, ...],
    read_collaboration_events: Callable[[str, str], tuple[CollaborationEventV2, ...]],
    read_session_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
) -> RecommendationRequestPolicyResult:
    objective = next((item for item in projection.artifact_objectives
                      if item.facts.objective_id == action.objective_id), None)
    if action.objective_id is not None and (
        objective is None or objective.state is not ArtifactObjectiveState.OPEN
        or objective.facts.origin_conversation_id != source.conversation_id
        or (action.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
            and objective.facts.target_paths != action.target_paths)
    ):
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'recommended objective scope is not an offered OPEN owner objective',
            ),
            '$.recommended_action.objective_id',
            'recommended objective scope is not an offered OPEN owner objective',
        )
    offered = {item.event_id: item for item in projection.offered_draft_sources}
    current = ArtifactDraftSource(source.conversation_id, source.event_id, reply_contract_values.recommendation_event_digest(source))
    aliases = {item.input_id: item for item in lead_proposal_inputs.materialize_lead_evidence_inputs(
        inputs, evidence, validation_path='$.recommended_action.source_ids',
    )}
    selected: list[CollaborationInputRevision] = []
    drafts: list[ArtifactDraftSource] = []
    for alias in action.source_ids:
        draft = current if alias == "current_response" else offered.get(alias)
        if draft is not None:
            drafts.append(draft)
            selected.append(CollaborationInputRevision(
                input_id=draft.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{draft.event_id}", state=CollaborationInputState.PRESENT,
                revision_digest=draft.content_digest,
            ))
        elif alias in aliases:
            selected.append(aliases[alias])
        else:
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError('recommended source alias is not runtime offered'),
                '$.recommended_action.source_ids',
                'recommended source alias is not runtime offered',
            )
    if objective is not None and not drafts:
        for draft in objective.draft_sources:
            if offered.get(draft.event_id) != draft:
                raise validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('objective draft lineage is no longer offered'),
                    '$.recommended_action.source_ids',
                    'objective draft lineage is no longer offered',
                )
            drafts.append(draft)
            selected.append(CollaborationInputRevision(
                input_id=draft.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{draft.event_id}", state=CollaborationInputState.PRESENT,
                revision_digest=draft.content_digest,
            ))
    artifact_ordinal = 0
    for index, item in enumerate(selected):
        if item.kind in {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}:
            artifact_ordinal += 1
            selected[index] = replace(item, input_id=f"reference_{artifact_ordinal}")
    admission = (source.payload.authoring_admission
                 if type(source.payload) is CollaborationLeadJudgmentPayload else None)
    if admission is None and type(source.payload) is CollaborationLeadSynthesisPayload:
        original = projection.current_proposal
        if original is not None and original.step_id == source.step_id:
            admission = original.basis.authoring_admission
    if admission is not None:
        historical_by_id = {fact.source_id: fact for fact, _text in
            authoring_source_history.historical_authoring_users(source.session_id, source.conversation_id, source.payload.authoring_scope, inputs, events=events, admission=admission, read_collaboration_events=read_collaboration_events)}
        for fact in admission.sources:
            if fact.source_id in historical_by_id:
                if historical_by_id[fact.source_id] != fact:
                    raise reply_contract_values.LeadOutcomeValidationError("recommendation historical USER source changed")
                continue
            actual = aliases.get(fact.source_id)
            if actual is None:
                raise reply_contract_values.LeadOutcomeValidationError("recommendation admission source is not offered")
            if all(item.input_id != actual.input_id for item in selected):
                selected.append(actual)
    selected_inputs = tuple(selected)
    scope = source.payload.authoring_scope
    if scope is None or scope.activity is AuthoringActivity.WORKFLOW:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'recommended preparation requires substantive authoring scope',
            ),
            '$.authoring_scope.activity',
            'recommended preparation requires substantive authoring scope',
        )
    if (
        action.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION
        and scope.schema_version == 3
        and scope.intent is AuthoringIntent.AUTHORING
        and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
    ):
        capture_events = {event.event_id: event for event in events}
        source_proposal = projection.current_proposal
        bound_capture_basis = (
            {item.input_id: item for item in source_proposal.basis.inputs}
            if (
                type(source.payload) is CollaborationLeadSynthesisPayload
                and source_proposal is not None
                and source.payload.proposal_id == source_proposal.proposal_id
                and source.step_id == source_proposal.step_id
                and source_proposal.authoring_scope == scope
            ) else None
        )
        for control in events:
            if type(control.payload) is not CollaborationLeadTaskTransitionPayload:
                continue
            receipt = control.payload.receipt
            if (
                not receipt.material_capture_event_ids
                or (receipt.target_task_id, receipt.target_task_revision)
                != (scope.task_id, scope.task_revision)
                or receipt.action not in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
            ):
                continue
            captured_aliases = tuple(
                (f"selected_material_{ordinal}", identity)
                for ordinal, identity in enumerate(receipt.material_capture_event_ids, 1)
            )
            if receipt.planning_step_id != source.step_id:
                if bound_capture_basis is None or any(
                    alias not in bound_capture_basis
                    or bound_capture_basis[alias].locator != f"conversation/{identity}"
                    for alias, identity in captured_aliases
                ):
                    continue
            origin = capture_events.get(receipt.origin_event_id)
            if (
                receipt.origin_kind != "task_decision"
                or control.sender != event_journal.RUNTIME_ACTOR
                or origin is None
                or type(origin.payload) is not CollaborationLeadTaskDecisionPayload
                or origin.sender != event_journal.USER_ACTOR
                or origin.batch_id != control.batch_id
                or (origin.session_id, origin.conversation_id, origin.turn_id, origin.step_id)
                != (control.session_id, control.conversation_id, control.turn_id, control.step_id)
                or origin.payload.decision.action is not receipt.action
                or len(origin.payload.decision.material_refs) != len(captured_aliases)
            ):
                raise reply_contract_values.LeadOutcomeValidationError("selected task inputs require their exact USER control")
            for ordinal, (alias, identity) in enumerate(captured_aliases):
                capture_event = capture_events.get(identity)
                item = aliases.get(alias)
                if (
                    capture_event is None
                    or type(capture_event.payload) is not CollaborationLeadMaterialCapturePayload
                ):
                    raise reply_contract_values.LeadOutcomeValidationError("selected task input capture is unavailable")
                captured = capture_event.payload.capture
                if (
                    capture_event.sender != event_journal.RUNTIME_ACTOR
                    or capture_event.batch_id != control.batch_id
                    or (
                        capture_event.session_id, capture_event.conversation_id,
                        capture_event.turn_id, capture_event.step_id,
                    )
                    != (control.session_id, control.conversation_id, control.turn_id, control.step_id)
                    or captured.origin_event_id != origin.event_id
                    or captured.source != origin.payload.decision.material_refs[ordinal]
                    or (captured.target_task_id, captured.target_task_revision)
                    != (scope.task_id, scope.task_revision)
                    or item is None
                    or item.kind is not CollaborationInputKind.CONVERSATION_EVENT
                    or item.state is not CollaborationInputState.PRESENT
                    or item.locator != f"conversation/{identity}"
                    or item.revision_digest != f"sha256:{captured.material_digest}"
                    or item.start_line is not None or item.end_line is not None
                    or (bound_capture_basis is not None and bound_capture_basis.get(alias) != item)
                ):
                    raise reply_contract_values.LeadOutcomeValidationError(
                        "selected task input must retain its exact whole capture"
                    )
                matching = tuple(value for value in selected if value.input_id == alias)
                if matching and matching != (item,):
                    raise reply_contract_values.LeadOutcomeValidationError(
                        "selected task input changed in recommendation sources"
                    )
                if not matching:
                    selected.append(item)
        selected_inputs = tuple(selected)
    if previous is not None and previous.authoring_scope is None:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('legacy recommendation requires fresh scoped planning'),
            '$.authoring_scope',
            'legacy recommendation requires fresh scoped planning',
        )
    if scope.schema_version == 3:
        if any(artifact_domain(path) not in required_authoring_domains(scope)
               for path in action.target_paths):
            raise reply_contract_values.LeadOutcomeValidationError("new destination domains require a fresh task revision")
    else:
        scope = replace(scope, target_paths=tuple(dict.fromkeys((*scope.target_paths, *action.target_paths))))
    scope = authoring_authority.resolve_authoring_scope(
        scope, selected_inputs, evidence,
        None if previous is None else previous.authoring_scope,
    )
    if previous is not None and scope.task_id != previous.authoring_scope.task_id:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError('recommendation revision cannot replace its authoring task'),
            '$.authoring_scope.task',
            'recommendation revision cannot replace its authoring task',
        )
    prior_events = tuple(
        event for event in events
        if event.event_id != source.event_id
        and (not hasattr(source, "sequence") or event.sequence < source.sequence)
    )
    prior_scope = authoring_authority.authoring_tasks(prior_events).get(scope.task_id)
    fresh_pair = (
        (
            scope.task_id == task_identity_for_origin(source.step_id)
            and scope.task_revision == 1
        ) or is_new_task_recommendation_source(source, scope, events)
        if prior_scope is None
        else (
            scope.task_revision == prior_scope.task_revision + 1
            or (
                (scope.task_id, scope.task_revision)
                == (prior_scope.task_id, prior_scope.task_revision)
                and not confirmed_authoring_proposals(prior_scope, prior_events)
            )
        )
    )
    fresh_consultation = (
        scope.schema_version == 3
        and scope.intent is AuthoringIntent.AUTHORING
        and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        and action.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION
        and type(source.payload) is CollaborationLeadJudgmentPayload
        and fresh_pair
        and not any(
            event.step_id == source.step_id
            and type(event.payload) is CollaborationStepStatePayload
            and event.payload.state is CollaborationStepState.RUNNING
            for event in events
        )
    )
    by_id = {event.event_id: event for event in events}
    for draft in drafts:
        selected_event = source if draft.event_id == source.event_id else by_id.get(draft.event_id)
        selected_scope = None if selected_event is None else getattr(selected_event.payload, "authoring_scope", None)
        if (not authoring_source_scope_is_eligible(
            selected_scope, scope, allow_prior_revision_input=fresh_consultation,
        )):
            raise validation_failure(
                reply_contract_values.LeadOutcomeValidationError(
                    'recommendation draft sources require their existing authoring task',
                ),
                '$.recommended_action',
                'recommendation draft sources require their existing authoring task',
            )
    if action.kind is LeadRecommendationActionKind.PREPARE_CONSULTATION:
        try:
            validation_call(
                '$.recommended_action.specialist_ids',
                validate_specialist_routing,
                scope,
                action.specialist_ids,
            )
        except (TypeError, ValueError) as validation_cause:
            raise carry_validation(
                validation_failure(
                    reply_contract_values.LeadOutcomeValidationError('recommended route omits required specialists'),
                    '$.recommended_action.specialist_ids',
                    'recommended route omits required specialists',
                ),
                validation_cause,
            ) from None
    remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - projection.physical_calls_spent
    mandatory = 1
    if remaining < mandatory:
        raise validation_failure(
            reply_contract_values.LeadOutcomeValidationError(
                'recommended preparation cannot fit its source-lineage budget',
            ),
            '$.recommended_action.source_ids',
            'recommended preparation cannot fit its source-lineage budget',
        )
    request_event_id = event_journal.new_id("e")
    source_proposal = None
    source_attempt = None
    if type(source.payload) is CollaborationLeadSynthesisPayload:
        source_proposal = projection.current_proposal
        source_attempt = source.payload.execution_attempt_id
    elif projection.current_proposal is not None and any(
        event.turn_id == source.turn_id and event.step_id == source.step_id
        and type(event.payload) is CollaborationConfirmationPayload
        and event.payload.decision is CollaborationConfirmationDecision.REVISE
        for event in events
    ):
        source_proposal = projection.current_proposal
    lineage_spent = projection.physical_calls_spent
    source_rounds = projection.rounds_spent
    if fresh_consultation:
        lineage_spent = sum(
            type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
            and event.step_id == source.step_id
            for event in events
        )
        source_rounds = 0
    if (scope.schema_version == 3 and not fresh_consultation
            and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
        preview = type(source.payload) is CollaborationLeadSynthesisPayload and projection.current_state is CollaborationStepState.RUNNING
        if preview:
            if projection.current_proposal is None or (
                projection.current_proposal.proposal_id, projection.current_proposal.proposal_revision,
                projection.current_proposal.proposal_digest, projection.execution_attempt_id
            ) != (source.payload.proposal_id, source.payload.proposal_revision,
                  source.payload.proposal_digest, source.payload.execution_attempt_id):
                raise reply_contract_values.LeadOutcomeValidationError("synthesis preview must bind the current confirmed execution")
            bound_source = projection.current_proposal
            completed_synthesis = source
            source_projection = projection
        else:
            bound_source, completed_synthesis, source_projection = authoring_authority.completed_authoring_source(scope, events)
        if source_proposal is not None and source_proposal != bound_source:
            raise reply_contract_values.LeadOutcomeValidationError("recommendation source is not the current confirmed draft")
        source_proposal = bound_source
        source_attempt = completed_synthesis.payload.execution_attempt_id
        source_rounds = source_projection.rounds_spent
        related_steps = {source.step_id, *(event.step_id for event in events
                         if type(event.payload) is LeadRecommendationRequestPayload
                         and event.payload.source_proposal_id == bound_source.proposal_id
                         and event.payload.source_proposal_revision == bound_source.proposal_revision
                         and event.payload.source_proposal_digest == bound_source.proposal_digest)}
        related_steps.discard(bound_source.step_id)
        source_rounds += sum(
            1 for event in events if event.step_id in related_steps
            and type(event.payload) is CollaborationStepStatePayload
            and event.payload.state is CollaborationStepState.RUNNING
        )
        lineage_spent = source_projection.physical_calls_spent + sum(
            1 for event in events if event.step_id in related_steps
            and type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
        )
    remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - lineage_spent
    inherited_capture = ()
    preparation_cap = min(reply_contract_values.CONTEXT_INVOCATION_CALL_LIMIT, remaining)
    if scope.schema_version == 3:
        if fresh_consultation:
            read = int(authoring_authority.has_present_references(selected_inputs))
            draft_floor = (len(action.specialist_ids) + 1) * (1 + read)
            floor = authoring_authority.future_authoring_floor(scope, selected_inputs) + draft_floor
            if remaining < floor + 1 + read:
                raise reply_contract_values.LeadOutcomeValidationError(
                    "fresh drafting preparation and full review are infeasible; clarify and freshly propose"
                )
            preparation_cap = min(preparation_cap, remaining - floor)
        elif scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
            if action.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT or source_proposal is None or source_attempt is None:
                raise reply_contract_values.LeadOutcomeValidationError("v3 authoring requires completed draft synthesis and artifact preparation; clarify or freshly propose")
            if source_rounds != 1 or source_proposal.authoring_scope is None:
                raise reply_contract_values.LeadOutcomeValidationError("final review cannot reset rounds or add round three; clarify or freshly propose")
            authoring_authority.validate_draft_provenance(scope, read_session_authoring_events(source.session_id),
                pending_attempt_id=source_attempt if preview else None,
                pending_replies=preview_replies if preview else ())
            inherited_capture = proposal_basis_values.remap_shared_premises(source_proposal.basis, selected_inputs)
            if authoring_capture_identity(scope, source_proposal.basis) != authoring_capture_identity(source_proposal.authoring_scope, source_proposal.basis):
                raise reply_contract_values.LeadOutcomeValidationError("preparation cannot change the confirmed authoring task")
            floor = authoring_authority.final_review_floor(scope, selected_inputs, preparation=False)
            minimum_preparation = 1 + int(authoring_authority.has_present_references(selected_inputs))
            if remaining < floor + minimum_preparation:
                raise reply_contract_values.LeadOutcomeValidationError("declared preparation and full final review are infeasible; clarify or freshly propose")
            preparation_cap = min(preparation_cap, remaining - floor)
        elif source_proposal is not None and source_proposal.basis.schema_version == 2:
            inherited_capture = proposal_basis_values.remap_shared_premises(source_proposal.basis, selected_inputs)
    return RecommendationRequestPolicyResult(
        current=current,
        drafts=drafts,
        admission=admission,
        selected_inputs=selected_inputs,
        scope=scope,
        request_event_id=request_event_id,
        source_proposal=source_proposal,
        source_attempt=source_attempt,
        lineage_spent=lineage_spent,
        inherited_capture=inherited_capture,
        preparation_cap=preparation_cap,
    )


def recommendation_basis(
    *,
    core_document_readiness: CoreDocumentReadinessBasis | None,
    authoring_admission: AuthoringAdmissionBasis | None,
    route: str,
    route_fingerprint: str,
    agent_profiles: tuple[CollaborationAgentProfile, ...],
    context_policy_fingerprint: str,
    authoring_scope: AuthoringScope | None,
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence,
    shared_premises: tuple[CollaborationSharedPremise, ...],
    editorial_refs: tuple[EditorialRecordRef, ...],
    editorial_context_digest: str | None,
) -> CollaborationProposalBasis:
    return CollaborationProposalBasis(
        core_document_readiness=core_document_readiness,
        authoring_admission=authoring_admission,
        route=route,
        route_fingerprint=route_fingerprint,
        agent_profiles=agent_profiles,
        context_policy_fingerprint=context_policy_fingerprint,
        budget_policy_fingerprint=authoring_authority.basis_budget_fingerprint(authoring_scope),
        inputs=inputs, evidence=evidence,
        schema_version=2 if authoring_scope is not None and authoring_scope.schema_version == 3 else 1,
        shared_premises=shared_premises,
        editorial_refs=editorial_refs, editorial_context_digest=editorial_context_digest,
    )
