"""Known collaboration result recovery, finalization, and continuation ownership."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, cast
from ..agent_context.invocation.context_execution import AgentContextService
from ..agents import LEAD_ORCHESTRATOR, ROSTER, SPECIALIST_IDS
from ..artifact_proposals import ArtifactReviewCandidate
from ..artifact_transactions.artifact_application_authority import ArtifactApplicationAuthority
from ..authoring_admission import AuthoringAdmissionBasis, AuthoringAdmissionNeeded
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations import (
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    CollaborationAgentProfile,
    CollaborationAppendReceipt,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationSideEffect,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    LeadOutcomeKind,
    LeadProposalIntent,
    LeadProposalRelation,
    LeadRecommendationRequestPayload,
    LeadRecommendationState,
    LeadTurnResult,
    project_collaboration_history,
)
from ..core_document_readiness import CoreDocumentReadinessBasis, CoreDocumentReadinessNeeded
from ..errors import BackendProtocolError
from ..lead_control_events import terminal_state_draft
from ..lead_preparation import authoring_authority, reply_contract_values
from ..models import AgentDefinition
from ..review_contracts import FinalReviewQualification
from ..runtime_resources import collaboration_event_journal as event_journal
from ..runtime_resources.provider_call_accounting import LeadCallBudgetExceeded
from ..specialist_routing import AuthoringActivity, SpecialistAssignmentMode, validate_stage_routing
from ..workspace.initialization import SessionArtifactBinding

__all__ = ("KnownResultFinalization",)


class KnownResultFinalization:
    def __init__(
        self,
        *,
        storage: ConversationStorageServices,
        artifact_authority: ArtifactApplicationAuthority,
    ) -> None:
        self._storage = storage
        self._artifact_authority = artifact_authority

    def finalize_known_result(
        self,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        *,
        context_service: AgentContextService,
        render_premise_report: Callable[..., LeadTurnResult],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        validate_exact_candidate_reviews: Callable[[str, CollaborationProposal, ArtifactReviewCandidate, tuple[CollaborationEvent, ...]], None],
        qualify_artifact_final_review: Callable[..., FinalReviewQualification],
        attach_document_plan: Callable[..., LeadTurnResult],
        bind_session_artifacts: Callable[[str], SessionArtifactBinding],
        build_contextual_definition: Callable[..., AgentDefinition],
        build_agent_profile: Callable[[str, object], CollaborationAgentProfile],
        read_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
        validate_review_material: Callable[..., None],
        qualify_readiness_targets: Callable[..., CoreDocumentReadinessBasis | CoreDocumentReadinessNeeded | None],
        qualify_authoring_inputs: Callable[..., AuthoringAdmissionBasis | AuthoringAdmissionNeeded],
    ) -> LeadTurnResult:
        if projection.current_recommendation_state is LeadRecommendationState.PENDING:
            request = projection.current_recommendation_request
            assert request is not None
            return self.recommendation_turn_result(next(event for event in events
                if event.event_id == request.request_event_id))
        proposal = projection.current_proposal
        result = projection.execution_result
        result_event_id = projection.execution_result_event_id
        if proposal is None or result is None or result_event_id is None:
            raise BackendProtocolError("known result finalization lacks authority")
        turn_id = event_journal.deterministic_id("t", result_event_id, "finalize")
        validation_details = ()
        synthesis_event = next(
            (
                event
                for event in events
                if event.event_id == result.synthesis_result_event_id
                and type(event.payload) is CollaborationLeadSynthesisPayload
            ),
            None,
        )
        synthesis_payload = (
            cast(CollaborationLeadSynthesisPayload, synthesis_event.payload)
            if synthesis_event is not None
            else None
        )
        if proposal.premise_decision is not None:
            if synthesis_payload is not None and synthesis_payload.premise_report is not None and result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
                return render_premise_report(session_id, events[-1].conversation_id, events, projection,
                    synthesis_payload.message, validation_details=validation_details,
                    provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id,
                    )
            return LeadTurnResult(session_id, events[-1].conversation_id, turn_id, proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION, "Premise analysis did not produce a confirmed choice; recorded calls remain charged. Clarify and freshly propose.",
                None, projection.current_state, None, CollaborationNextAction.CONTINUE_WITH_LEAD,
                validation_details=validation_details,
                provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id,
                )
        next_intent = (
            synthesis_payload.next_intent if synthesis_payload is not None else None
        )
        artifact_candidate = authoring_authority.artifact_candidate_for_proposal(events, proposal)
        if artifact_candidate is not None:
            return self._artifact_authority.finalize_artifact_result_locked(
                session_id=session_id,
                events=events,
                projection=projection,
                proposal=proposal,
                result=result,
                result_event_id=result_event_id,
                candidate=artifact_candidate,
                turn_id=turn_id,
                validation_details=validation_details,
                validate_exact_candidate_reviews=validate_exact_candidate_reviews,
                qualify_artifact_final_review=qualify_artifact_final_review,
                attach_document_plan=attach_document_plan,
                append_collaboration_event_batch=append_batch,
            )
        next_proposal: CollaborationProposal | None = None
        continuation_exceeds_budget = False
        if (
            result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
            and next_intent is not None
        ):
            try:
                next_proposal = self.build_continuation_proposal(
                    session_id=projection.session_id or events[-1].session_id,
                    proposal=proposal,
                    result=result,
                    next_intent=next_intent,
                    events=events,
                    projection=projection,
                    context_service=context_service,
                    bind_session_artifacts=bind_session_artifacts,
                    build_contextual_definition=build_contextual_definition,
                    build_agent_profile=build_agent_profile,
                    read_authoring_events=read_authoring_events,
                    validate_review_material=validate_review_material,
                    qualify_readiness_targets=qualify_readiness_targets,
                    qualify_authoring_inputs=qualify_authoring_inputs,
                )
            except LeadCallBudgetExceeded:
                continuation_exceeds_budget = True
        if next_proposal is not None:
            assert synthesis_event is not None
            request_id = event_journal.deterministic_id(
                "c", next_proposal.proposal_id, next_proposal.proposal_digest
            )
            synthesis_message = cast(
                CollaborationLeadSynthesisPayload,
                synthesis_event.payload,
            ).message
            notice = (
                f"{synthesis_message}\n\nA second specialist round is proposed for "
                "the unresolved points. Confirm, reject, or revise it."
            )
            drafts = (
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id("e", result_event_id, "notice"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationMessagePayload(notice, provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id),
                ),
                terminal_state_draft(
                    events=events,
                    turn_id=turn_id,
                    proposal=proposal,
                    result_event_id=result_event_id,
                    state=CollaborationStepState.COMPLETED,
                ),
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id("e", result_event_id, "proposal"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=next_proposal,
                ),
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id("e", result_event_id, "awaiting"),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.AWAITING_CONFIRMATION,
                        proposal_id=next_proposal.proposal_id,
                        proposal_revision=next_proposal.proposal_revision,
                        proposal_digest=next_proposal.proposal_digest,
                        confirmation_request_id=request_id,
                    ),
                ),
            )
            event_journal.append_drafts(
                projection.last_sequence,
                drafts,
                batch_id=event_journal.deterministic_id("b", result_event_id, "continue"),
                append_batch=append_batch,
            )
            return LeadTurnResult(
                events[-1].session_id,
                events[-1].conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.PROPOSAL,
                notice,
                next_proposal,
                CollaborationStepState.AWAITING_CONFIRMATION,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
                next_proposal.basis.evidence,
                validation_details=validation_details,
                provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id,
            )

        state = {
            CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
            CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
            CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
        }[result.overall_outcome]
        if synthesis_event is not None:
            synthesis_payload = cast(
                CollaborationLeadSynthesisPayload,
                synthesis_event.payload,
            )
            message = synthesis_payload.message
        elif result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
            reply = cast(
                CollaborationSpecialistReplyPayload,
                next(
                    event.payload
                    for event in events
                    if event.event_id == result.assignment_outcomes[0].reply_event_id
                    and type(event.payload) is CollaborationSpecialistReplyPayload
                ),
            )
            message = reply.message
        elif result.overall_outcome is CollaborationExecutionOutcomeKind.FAILED:
            message = (
                "The specialist consultation failed after preserving every known "
                "sibling result. No semantic synthesis was attempted."
            )
        else:
            message = (
                "The specialist consultation was interrupted after preserving every "
                "known result. Continue with the lead when ready."
            )
        if any(item.mode is SpecialistAssignmentMode.DRAFT for item in proposal.assignments):
            notice = "Draft material is available for Lead integration and concrete review. Story authoring is not complete; no Apply is authorized."
            combined = f"{message}\n\n{notice}"
            message = combined if len(combined.encode("utf-8")) <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES else notice
        if continuation_exceeds_budget:
            budget_notice = (
                "The provider round ended. The requested second specialist "
                "round exceeds its remaining call budget. Start a new request "
                "with the lead to continue. The completed replies and synthesis "
                "remain in the conversation history."
            )
            combined_message = f"{message}\n\n{budget_notice}"
            message = (
                combined_message
                if len(combined_message.encode("utf-8"))
                <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
                else budget_notice
            )
        final_drafts: list[CollaborationEventDraft] = [
            event_journal.draft_with_id(
                event_id=event_journal.deterministic_id("e", result_event_id, "notice"),
                session_id=events[-1].session_id,
                conversation_id=events[-1].conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=event_journal.LEAD_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationMessagePayload(message, provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id),
            )
        ]
        if state is CollaborationStepState.INTERRUPTED:
            final_drafts.append(
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id(
                        "e", result_event_id, "interruption"
                    ),
                    session_id=events[-1].session_id,
                    conversation_id=events[-1].conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.INTERRUPTION,
                    sender=event_journal.RUNTIME_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=CollaborationInterruptionPayload(
                        result.execution_attempt_id,
                        message,
                    ),
                )
            )
        final_drafts.append(
            terminal_state_draft(
                events=events,
                turn_id=turn_id,
                proposal=proposal,
                result_event_id=result_event_id,
                state=state,
            )
        )
        event_journal.append_drafts(
            projection.last_sequence,
            tuple(final_drafts),
            batch_id=event_journal.deterministic_id("b", result_event_id, "finalize"),
            append_batch=append_batch,
        )
        return LeadTurnResult(
            events[-1].session_id,
            events[-1].conversation_id,
            turn_id,
            proposal.step_id,
            (LeadOutcomeKind.ANSWER if synthesis_event is not None
             and state is CollaborationStepState.COMPLETED else LeadOutcomeKind.RECOMMENDATION),
            message,
            None,
            state,
            None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            validation_details=validation_details,
            provider_invocation_id=None if synthesis_payload is None else synthesis_payload.provider_invocation_id,
        )

    def build_continuation_proposal(
        self,
        *,
        session_id: str,
        proposal: CollaborationProposal,
        result: CollaborationExecutionResult,
        next_intent: LeadProposalIntent,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        context_service: AgentContextService,
        bind_session_artifacts: Callable[[str], SessionArtifactBinding],
        build_contextual_definition: Callable[..., AgentDefinition],
        build_agent_profile: Callable[[str, object], CollaborationAgentProfile],
        read_authoring_events: Callable[[str], tuple[CollaborationEvent, ...]],
        validate_review_material: Callable[..., None],
        qualify_readiness_targets: Callable[..., CoreDocumentReadinessBasis | CoreDocumentReadinessNeeded | None],
        qualify_authoring_inputs: Callable[..., AuthoringAdmissionBasis | AuthoringAdmissionNeeded],
    ) -> CollaborationProposal:
        if proposal.authoring_scope is not None and proposal.authoring_scope.schema_version == 3 and proposal.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}:
            raise LeadCallBudgetExceeded("round two is reserved for exact final review; clarify or freshly propose")
        if proposal.budget.rounds_spent != 0 or next_intent.relation is not (
            LeadProposalRelation.CONTINUE_LINEAGE
        ):
            raise BackendProtocolError("a third specialist round is unreachable")
        if proposal.authoring_scope is None or next_intent.authoring_scope is None:
            raise BackendProtocolError("continuation requires current authoring scope")
        if (next_intent.authoring_scope.task_id, next_intent.authoring_scope.task_revision) != (
            proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision,
        ):
            raise BackendProtocolError("continuation task revision drift")
        next_intent = replace(next_intent, authoring_scope=authoring_authority.resolve_authoring_scope(
            next_intent.authoring_scope, proposal.basis.inputs, proposal.basis.evidence,
            proposal.authoring_scope,
        ))
        reply_events = tuple(
            next(
                event
                for event in events
                if event.event_id == outcome.reply_event_id
                and type(event.payload) is CollaborationSpecialistReplyPayload
            )
            for outcome in result.assignment_outcomes
        )
        reply_inputs = tuple(
            CollaborationInputRevision(
                input_id=event.event_id,
                kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{event.event_id}",
                state=CollaborationInputState.PRESENT,
                revision_digest=reply_contract_values.specialist_reply_digest(
                    cast(CollaborationSpecialistReplyPayload, event.payload)
                ),
            )
            for event in reply_events
        )
        inputs = (*proposal.basis.inputs, *reply_inputs)
        validate_review_material(
            session_id,
            next_intent.assignments,
            tuple(inputs),
            read_authoring_events(session_id) if proposal.document_node is not None and proposal.document_node.candidate_uses else events,
        )
        validate_stage_routing(next_intent.authoring_scope, tuple(item.agent_id for item in next_intent.assignments), next_intent.stage_plan,
                source_input_ids=tuple(item.input_id for item in inputs),
                required_condition_ids=(() if next_intent.stage_plan is None else authoring_authority.stage_required_condition_ids(tuple(inputs), proposal.basis.evidence)),
                affected_paths=authoring_authority.authoring_affected_paths(tuple(inputs), proposal.basis.evidence))
        known_ids = {item.input_id for item in inputs}
        intent_ids = {
            input_id
            for assignment in next_intent.assignments
            for input_id in assignment.input_ids
        }
        specialist_ids = tuple(
            assignment.agent_id for assignment in next_intent.assignments
        )
        canonical = tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        )
        if specialist_ids != canonical or intent_ids != known_ids:
            raise BackendProtocolError(
                "round-two intent is not canonical or input-complete"
            )
        authoring_authority.validate_required_reviewers(
            tuple(inputs),
            tuple(
                CollaborationAssignment(
                    assignment_id=event_journal.deterministic_id(
                        "a",
                        result.execution_attempt_id,
                        assignment.agent_id,
                        "continuation_validation",
                    ),
                    specialist_id=assignment.agent_id,
                    round=2,
                    instruction=assignment.instruction,
                    input_ids=assignment.input_ids,
                    mode=assignment.mode,
                    review_basis=assignment.review_basis,
                    max_physical_calls=1,
                )
                for assignment in next_intent.assignments
            ),
        )
        call_limits, synthesis_calls, required_calls = authoring_authority.proposal_call_reservations(
            inputs=tuple(inputs),
            assignment_input_ids=tuple(
                assignment.input_ids for assignment in next_intent.assignments
            ),
            physical_calls_spent=projection.physical_calls_spent,
            authoring_scope=next_intent.authoring_scope,
        )
        proposal_id = event_journal.deterministic_id(
            "p", result.execution_attempt_id, "continuation"
        )
        runtime_assignments = tuple(
            CollaborationAssignment(
                assignment_id=event_journal.deterministic_id(
                    "a", proposal_id, assignment.agent_id, str(index)
                ),
                specialist_id=assignment.agent_id,
                round=2,
                instruction=assignment.instruction,
                input_ids=assignment.input_ids,
                mode=assignment.mode,
                review_basis=assignment.review_basis,
                max_physical_calls=call_limit,
            )
            for index, (assignment, call_limit) in enumerate(
                zip(next_intent.assignments, call_limits, strict=True),
                start=1,
            )
        )
        participant_ids = {LEAD_ORCHESTRATOR, *specialist_ids}
        artifact_binding = bind_session_artifacts(session_id)
        definitions = {
            agent_id: build_contextual_definition(
                artifact_binding=artifact_binding,
                session_id=session_id,
                agent_id=agent_id,
            )
            for agent_id in participant_ids
        }
        profiles = tuple(
            build_agent_profile(agent_id, definitions[agent_id])
            for agent_id in ROSTER
            if agent_id in participant_ids
        )
        route_value = {
            "participants": list(specialist_ids),
            "profiles": [
                [
                    profile.agent_id,
                    profile.definition_fingerprint,
                    profile.execution_fingerprint,
                ]
                for profile in profiles
            ],
            "route": "lead_specialist_consultation",
        }
        core_document_readiness = proposal.basis.core_document_readiness
        if next_intent.authoring_scope is not None and any(item.mode is SpecialistAssignmentMode.DRAFT for item in next_intent.assignments):
            readiness = qualify_readiness_targets(session_id, next_intent.authoring_scope.target_paths,
                retained=core_document_readiness)
            if type(readiness) is CoreDocumentReadinessNeeded:
                raise BackendProtocolError(readiness.message)
            core_document_readiness = readiness
        authoring_admission = proposal.basis.authoring_admission
        if authoring_admission is not None:
            qualified = qualify_authoring_inputs(authoring_admission.assessment,
                inputs=tuple(inputs), evidence=proposal.basis.evidence,
                scope=next_intent.authoring_scope, events=events, admission=authoring_admission)
            if (type(qualified) is not AuthoringAdmissionBasis
                or qualified.sources != authoring_admission.sources
                or qualified.request_digest != authoring_admission.request_digest):
                raise BackendProtocolError("continuation changed the admitted authoring sources")
            authoring_admission = qualified
        return CollaborationProposal.create(
            step_id=proposal.step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal.proposal_revision + 1,
            step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
            purpose=next_intent.purpose,
            authoring_scope=next_intent.authoring_scope,
            assignments=runtime_assignments,
            user_impact=reply_contract_values.LEAD_PROPOSAL_USER_IMPACT,
            side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
            budget=CollaborationBudget(
                round_limit=COLLABORATION_ROUND_LIMIT,
                physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
                rounds_spent=projection.rounds_spent,
                rounds_added=1,
                physical_calls_spent=projection.physical_calls_spent,
                max_additional_physical_calls=required_calls,
                synthesis_max_physical_calls=synthesis_calls,
            ),
            basis=CollaborationProposalBasis(
                core_document_readiness=core_document_readiness,
                authoring_admission=authoring_admission,
                route="lead_specialist_consultation",
                route_fingerprint=reply_contract_values.canonical_digest(route_value),
                agent_profiles=profiles,
                context_policy_fingerprint=(
                    context_service.policy_fingerprint
                ),
                budget_policy_fingerprint=authoring_authority.basis_budget_fingerprint(next_intent.authoring_scope),
                inputs=tuple(inputs),
                evidence=proposal.basis.evidence,
                schema_version=proposal.basis.schema_version,
                shared_premises=proposal.basis.shared_premises,
                editorial_refs=proposal.basis.editorial_refs, editorial_context_digest=proposal.basis.editorial_context_digest,
            ),
         stage_plan=next_intent.stage_plan, document_node=proposal.document_node, document_plan=proposal.document_plan)

    def recommendation_turn_result(self, event: CollaborationEvent | CollaborationEventDraft) -> LeadTurnResult:
        request = cast(LeadRecommendationRequestPayload, event.payload)
        events = self._storage.event_store.read(event.session_id, event.conversation_id)
        project_collaboration_history(events)
        source = next(item for item in events if item.event_id == request.source_recommendation_event_id)
        if reply_contract_values.recommendation_event_digest(source) != request.source_recommendation_digest:
            raise BackendProtocolError("recommendation source no longer matches its disclosure")
        validation_details = (
            reply_contract_values.execution_validation_details(
                events, request.source_execution_attempt_id, source.step_id,
            )
            if request.source_execution_attempt_id is not None else ()
        )
        return LeadTurnResult(
            event.session_id, event.conversation_id, event.turn_id, event.step_id,
            LeadOutcomeKind.RECOMMENDATION, source.payload.message, None, None, None,
            CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION,
            evidence=request.basis.evidence, recommendation_request=request,
            trace=getattr(source.payload, "trace", None),
            validation_details=validation_details,
            provider_invocation_id=getattr(source.payload, "provider_invocation_id", None),
        )
