"""Collaboration result publication and synthesis generation-pin cleanup."""
from __future__ import annotations

import asyncio
import sys

from dataclasses import replace
from typing import Any, Callable, cast

from ..collaboration_execution_results import (
    ArtifactSynthesisResult,
    CollaborationInvocationResult,
    LeadSynthesisResult,
    required_synthesis_result,
)
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations import (
    ArtifactProposalState,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAppendReceipt,
    CollaborationAssignmentOutcome,
    CollaborationAssignmentOutcomeKind,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationProposal,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    project_collaboration_history,
)
from ..errors import BackendProtocolError
from ..lead_control_events import terminal_state_draft
from ..lead_preparation import authoring_authority, reply_contract_values
from ..runtime_resources import collaboration_event_journal as event_journal
from ..runtime_resources.generation_pin_cleanup import close_untransferred_generation_pin

__all__ = ["CollaborationResultPublication"]


class CollaborationResultPublication:
    """Publish results using call-local semantic operations and the same storage."""

    def __init__(self, *, storage: ConversationStorageServices) -> None:
        self._storage = storage

    def persist_results(
        self,
        *,
        read_events: Callable[[str, str], tuple[CollaborationEvent, ...]],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        prepare_recommendation_request: Callable[..., CollaborationEventDraft],
        **arguments: Any,
    ) -> None:
        """Persist results before releasing transferred synthesis generation."""

        synthesis_result = cast(
            CollaborationInvocationResult | None,
            arguments.get("synthesis_result"),
        )
        generation_pin = (
            None if synthesis_result is None else synthesis_result.generation_pin
        )
        artifact_binding = (
            None if synthesis_result is None else synthesis_result.artifact_binding
        )
        try:
            try:
                self._persist_collaboration_results_with_generation_pin(
                    read_events=read_events,
                    append_batch=append_batch,
                    prepare_recommendation_request=prepare_recommendation_request,
                    **arguments,
                )
            except BaseException:
                if generation_pin is not None and artifact_binding is not None:
                    artifact_binding.require_generation_pin(generation_pin)
                raise
            if generation_pin is not None:
                if artifact_binding is None:
                    raise BackendProtocolError(
                        "synthesis generation pin lacks its artifact binding"
                    )
                artifact_binding.require_generation_pin(generation_pin)
        finally:
            if generation_pin is not None:
                close_untransferred_generation_pin(
                    generation_pin,
                    failure=sys.exception(),
                )

    def _persist_collaboration_results_with_generation_pin(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        proposal: CollaborationProposal,
        attempt_id: str,
        assignment_results: tuple[CollaborationInvocationResult, ...],
        synthesis_result: CollaborationInvocationResult | None,
        read_events: Callable[[str, str], tuple[CollaborationEvent, ...]],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        prepare_recommendation_request: Callable[..., CollaborationEventDraft],
    ) -> None:
        if len(assignment_results) != len(proposal.assignments):
            raise BackendProtocolError("assignment result count is not authoritative")
        for invocation_result in (
            *assignment_results,
            *((synthesis_result,) if synthesis_result is not None else ()),
        ):
            if invocation_result.pending_validation is not None:
                invocation_id, details_by_call = invocation_result.pending_validation
                event_journal.append_validation_messages(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    invocation_id=invocation_id,
                    details_by_call=details_by_call,
                    read_events=read_events,
                    append_batch=append_batch,
                )
        assignments_succeeded = all(item.failure is None for item in assignment_results)
        if (
            assignments_succeeded
            and proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and proposal.budget.synthesis_max_physical_calls > 0
        ):
            synthesis_result = required_synthesis_result(synthesis_result)
        uncertain = any(item.submission_uncertain for item in assignment_results) or (
            synthesis_result is not None and synthesis_result.submission_uncertain
        )
        replies: list[CollaborationEventDraft] = []
        assignment_outcomes: list[CollaborationAssignmentOutcome] = []
        terminals: list[CollaborationEventDraft] = []
        for assignment, invocation_result in zip(
            proposal.assignments,
            assignment_results,
            strict=True,
        ):
            reply_event_id: str | None = None
            if invocation_result.failure is None:
                if (
                    type(invocation_result.value)
                    is not CollaborationSpecialistReplyPayload
                ):
                    raise BackendProtocolError("specialist success lacks a typed reply")
                reply_event_id = event_journal.deterministic_id(
                    "e", attempt_id, assignment.assignment_id, "reply"
                )
                replies.append(
                    event_journal.draft_with_id(
                        event_id=reply_event_id,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.SPECIALIST_REPLY,
                        sender=CollaborationActor(
                            CollaborationActorKind.SPECIALIST,
                            assignment.specialist_id,
                        ),
                        recipient=(
                            event_journal.USER_ACTOR
                            if proposal.step_kind
                            is CollaborationStepKind.SPECIALIST_ADVISORY
                            else event_journal.LEAD_ACTOR
                        ),
                        payload=replace(
                            invocation_result.value,
                            trace=invocation_result.trace,
                            provider_invocation_id=invocation_result.provider_invocation_id,
                        ),
                    )
                )
                outcome = CollaborationAssignmentOutcomeKind.SUCCEEDED
            elif isinstance(invocation_result.failure, asyncio.CancelledError):
                outcome = CollaborationAssignmentOutcomeKind.CANCELLED
            else:
                outcome = CollaborationAssignmentOutcomeKind.FAILED
            if not invocation_result.submission_uncertain:
                assignment_outcomes.append(
                    CollaborationAssignmentOutcome(
                        assignment_id=assignment.assignment_id,
                        outcome=outcome,
                        submitted_physical_calls=(
                            invocation_result.submitted_physical_calls
                        ),
                        reply_event_id=reply_event_id,
                    )
                )
                terminals.append(
                    self._terminal_outcome_draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        attempt_id=attempt_id,
                        scope=CollaborationTerminalScope.ASSIGNMENT,
                        outcome=outcome,
                        submitted_physical_calls=(
                            invocation_result.submitted_physical_calls
                        ),
                        assignment_id=assignment.assignment_id,
                    )
                )

        current_events = self._storage.event_store.read(
            session_id,
            conversation_id,
        )
        projection = project_collaboration_history(current_events)
        artifact_candidate = authoring_authority.artifact_candidate_for_proposal(
            current_events, proposal
        )
        if uncertain:
            interruption = event_journal.draft_with_id(
                event_id=event_journal.deterministic_id("e", attempt_id, "uncertain"),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.INTERRUPTION,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationInterruptionPayload(
                    attempt_id,
                    "A submitted provider outcome is uncertain; known sibling facts "
                    "were preserved and this attempt will not be replayed.",
                ),
            )
            unknown = event_journal.draft_with_id(
                event_id=event_journal.deterministic_id("e", attempt_id, "outcome_unknown"),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.OUTCOME_UNKNOWN,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=attempt_id,
                ),
            )
            artifact_unknown = (
                ()
                if artifact_candidate is None
                else (
                    event_journal.draft_with_id(
                        event_id=event_journal.deterministic_id(
                            "e", attempt_id, "artifact_recovery"
                        ),
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                        sender=event_journal.RUNTIME_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=authoring_authority.artifact_state_payload(
                            artifact_candidate,
                            ArtifactProposalState.RECOVERY_REQUIRED,
                            execution_attempt_id=attempt_id,
                        ),
                    ),
                )
            )
            event_journal.append_drafts(
                projection.last_sequence,
                (*replies, *terminals, interruption, *artifact_unknown, unknown),
                batch_id=event_journal.deterministic_id("b", attempt_id, "unknown"),
                append_batch=append_batch,
            )
            return

        synthesis_draft: CollaborationEventDraft | None = None
        recommended_action: LeadRecommendedAction | None = None
        artifact_synthesis_draft: CollaborationEventDraft | None = None
        synthesis_outcome = CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
        synthesis_calls = 0
        synthesis_event_id: str | None = None
        all_assignments_succeeded = all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in assignment_outcomes
        )
        if synthesis_result is not None:
            synthesis_calls = synthesis_result.submitted_physical_calls
            if synthesis_result.failure is None:
                value = synthesis_result.value
                if type(value) is ArtifactSynthesisResult:
                    collaboration_value = replace(
                        value.collaboration,
                        trace=synthesis_result.trace,
                        provider_invocation_id=synthesis_result.provider_invocation_id,
                    )
                    artifact_value = replace(value.artifact, provider_invocation_id=synthesis_result.provider_invocation_id)
                elif type(value) is LeadSynthesisResult:
                    collaboration_value = replace(
                        value.collaboration,
                        trace=synthesis_result.trace,
                        provider_invocation_id=synthesis_result.provider_invocation_id,
                    )
                    recommended_action = value.recommended_action
                    artifact_value = None
                else:
                    raise BackendProtocolError("lead synthesis success is not typed")
                synthesis_outcome = CollaborationSynthesisOutcomeKind.SUCCEEDED
                synthesis_event_id = event_journal.deterministic_id(
                    "e", attempt_id, "synthesis"
                )
                synthesis_draft = event_journal.draft_with_id(
                    event_id=synthesis_event_id,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    kind=CollaborationEventKind.LEAD_SYNTHESIS,
                    sender=event_journal.LEAD_ACTOR,
                    recipient=event_journal.USER_ACTOR,
                    payload=collaboration_value,
                )
                if artifact_value is not None:
                    artifact_synthesis_draft = event_journal.draft_with_id(
                        event_id=event_journal.deterministic_id(
                            "e", attempt_id, "artifact_synthesis"
                        ),
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS,
                        sender=event_journal.LEAD_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=artifact_value,
                    )
            elif isinstance(synthesis_result.failure, asyncio.CancelledError):
                synthesis_outcome = CollaborationSynthesisOutcomeKind.CANCELLED
            else:
                synthesis_outcome = CollaborationSynthesisOutcomeKind.FAILED
            terminals.append(
                self._terminal_outcome_draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=proposal.step_id,
                    attempt_id=attempt_id,
                    scope=CollaborationTerminalScope.SYNTHESIS,
                    outcome=CollaborationAssignmentOutcomeKind(synthesis_outcome.value),
                    submitted_physical_calls=synthesis_calls,
                    assignment_id=None,
                )
            )

        if (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.FAILED
                for item in assignment_outcomes
            )
            or synthesis_outcome is CollaborationSynthesisOutcomeKind.FAILED
        ):
            overall = CollaborationExecutionOutcomeKind.FAILED
        elif (
            any(
                item.outcome is CollaborationAssignmentOutcomeKind.CANCELLED
                for item in assignment_outcomes
            )
            or synthesis_outcome is CollaborationSynthesisOutcomeKind.CANCELLED
        ):
            overall = CollaborationExecutionOutcomeKind.CANCELLED
        elif all_assignments_succeeded and synthesis_outcome in {
            CollaborationSynthesisOutcomeKind.SUCCEEDED,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        }:
            overall = CollaborationExecutionOutcomeKind.SUCCEEDED
        else:
            overall = CollaborationExecutionOutcomeKind.FAILED
        visible = tuple(
            draft.event_id
            for draft in (
                *replies,
                *((synthesis_draft,) if synthesis_draft is not None else ()),
                *(
                    (artifact_synthesis_draft,)
                    if artifact_synthesis_draft is not None
                    else ()
                ),
                *terminals,
            )
        )
        result_event_id = event_journal.deterministic_id("e", attempt_id, "result")
        result = event_journal.draft_with_id(
            event_id=result_event_id,
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            kind=CollaborationEventKind.EXECUTION_RESULT,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationExecutionResult(
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_attempt_id=attempt_id,
                assignment_outcomes=tuple(assignment_outcomes),
                synthesis_outcome=synthesis_outcome,
                synthesis_submitted_physical_calls=synthesis_calls,
                synthesis_result_event_id=synthesis_event_id,
                overall_outcome=overall,
                visible_exchange_event_ids=visible,
            ),
        )
        result_drafts = (
            *replies, *((synthesis_draft,) if synthesis_draft is not None else ()),
            *((artifact_synthesis_draft,) if artifact_synthesis_draft is not None else ()),
            *terminals, result,
        )
        if recommended_action is not None and not (
            proposal.authoring_scope is not None and proposal.authoring_scope.schema_version == 3
            and result.payload.overall_outcome is not CollaborationExecutionOutcomeKind.SUCCEEDED
        ):
            assert synthesis_draft is not None
            if (
                synthesis_result is None
                or synthesis_result.artifact_binding is None
            ):
                raise BackendProtocolError(
                    "lead synthesis recommendation lacks its artifact binding"
                )
            terminal = terminal_state_draft(events=current_events, turn_id=turn_id,
                proposal=proposal, result_event_id=result_event_id, state=CollaborationStepState.COMPLETED)
            reply_inputs = tuple(CollaborationInputRevision(
                input_id=item.event_id, kind=CollaborationInputKind.CONVERSATION_EVENT,
                locator=f"conversation/{item.event_id}", state=CollaborationInputState.PRESENT,
                revision_digest=reply_contract_values.specialist_reply_digest(item.payload),
            ) for item in replies)
            recommendation = prepare_recommendation_request(
                source=synthesis_draft, action=recommended_action,
                inputs=(*proposal.basis.inputs, *reply_inputs), evidence=proposal.basis.evidence,
                preview_replies=tuple(item.payload for item in replies),
                events=current_events, projection=projection,
                artifact_binding=synthesis_result.artifact_binding,
                tail_sequence=projection.last_sequence + len(result_drafts) + 2,
                step_id=event_journal.deterministic_id("s", result_event_id, "recommendation"),
            )
            result_drafts = (*result_drafts, terminal, recommendation)
        if proposal.premise_decision is not None:
            terminal_state = {CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED}[overall]
            result_drafts = (*result_drafts, terminal_state_draft(events=current_events,
                turn_id=turn_id, proposal=proposal, result_event_id=result_event_id, state=terminal_state))
        event_journal.append_drafts(projection.last_sequence, result_drafts,
            batch_id=event_journal.deterministic_id("b", attempt_id, "result"),
            append_batch=append_batch,
        )

    def _terminal_outcome_draft(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        attempt_id: str,
        scope: CollaborationTerminalScope,
        outcome: CollaborationAssignmentOutcomeKind,
        submitted_physical_calls: int,
        assignment_id: str | None,
    ) -> CollaborationEventDraft:
        return event_journal.draft_with_id(
            event_id=event_journal.deterministic_id(
                "e", attempt_id, scope.value, assignment_id or "lead", "terminal"
            ),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=CollaborationEventKind.TERMINAL_OUTCOME,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationTerminalOutcomePayload(
                execution_attempt_id=attempt_id,
                scope=scope,
                outcome=outcome,
                submitted_physical_calls=submitted_physical_calls,
                assignment_id=assignment_id,
            ),
        )
