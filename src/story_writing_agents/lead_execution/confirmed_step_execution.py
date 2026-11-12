"""Confirmed authority, specialist execution, and restart normalization."""

from __future__ import annotations

from typing import (
    Any,
    cast,
)
import asyncio
import uuid
from collections.abc import (
    Callable,
    Coroutine,
)

from ..agent_context.progress import ContextActionProgressSink
from ..collaboration_execution_results import (
    CollaborationInvocationResult,
    blocking_lead_result,
    joined_invocation_result,
    pending_artifact_operation_result,
    required_synthesis_result,
    submitted_lead_outcome_unknown_result,
)
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversations import (
    ArtifactProposalState,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAppendReceipt,
    CollaborationAssignmentDispatchPayload,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationHistoryProjection,
    CollaborationInterruptionPayload,
    CollaborationInvocationKind,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadRecommendationState,
    LeadStepDecision,
    LeadTurnResult,
    project_collaboration_history,
)
from ..errors import (
    BackendProtocolError,
    ProviderAuthenticationRequiredError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..lead_control_events import (
    has_unstarted_lead_request,
    unmatched_lead_call_state,
)
from ..lead_preparation import (
    authoring_authority,
    reply_contract_values,
)
from ..runtime_resources import collaboration_event_journal as event_journal
from ..workspace.initialization import SessionArtifactBindingError

__all__ = ["ConfirmedStepExecution"]


class _ConfirmedStepCapability:
    """Process-local, nonserializable, single-use exact confirmed-step authority."""

    __slots__ = (
        "_consumed",
        "_secret",
        "basis_digest",
        "proposal_digest",
        "proposal_id",
        "proposal_revision",
        "session_id",
        "step_id",
        "tail_event_id",
        "tail_sequence",
        "conversation_id",
    )

    def __init__(
        self,
        *,
        session_id: str,
        conversation_id: str,
        step_id: str,
        proposal_id: str,
        proposal_revision: int,
        proposal_digest: str,
        basis_digest: str,
        tail_event_id: str,
        tail_sequence: int,
    ) -> None:
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.step_id = step_id
        self.proposal_id = proposal_id
        self.proposal_revision = proposal_revision
        self.proposal_digest = proposal_digest
        self.basis_digest = basis_digest
        self.tail_event_id = tail_event_id
        self.tail_sequence = tail_sequence
        self._secret = bytearray(uuid.uuid4().bytes + uuid.uuid4().bytes)
        self._consumed = False

    def __repr__(self) -> str:
        return "_ConfirmedStepCapability(<content hidden>)"

    def __reduce__(self) -> object:
        raise TypeError("confirmed-step capability cannot be serialized")

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("confirmed-step capability is already consumed")
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0


class ConfirmedStepExecution:
    """Own single-use authority, live execution, and restart normalization."""

    def __init__(
        self,
        *,
        storage: ConversationStorageServices,
        issued_capabilities: dict[tuple[str, str, str], Any],
        live_executions: set[tuple[str, str, str, str]],
    ) -> None:
        # The producer hands off these exact fresh containers and stops using them.
        self._storage = storage
        self._confirmed_capabilities = issued_capabilities
        self._live_execution_markers = live_executions

    def confirm_and_execute_locked(
        self,
        *,
        decision: LeadStepDecision,
        proposal: CollaborationProposal,
        tail: CollaborationEvent,
        action_progress_sink: ContextActionProgressSink | None,
        validate_proposal_authority: Callable[..., None],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        run_specialist: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        run_synthesis: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        persist_results: Callable[..., None],
        finalize_known_result: Callable[
            [str, tuple[CollaborationEvent, ...], CollaborationHistoryProjection], LeadTurnResult,
        ],
    ) -> Coroutine[Any, Any, LeadTurnResult]:
        """Issue under the held lock and return the original execution coroutine."""
        capability = _ConfirmedStepCapability(
            session_id=decision.session_id,
            conversation_id=decision.conversation_id,
            step_id=proposal.step_id,
            proposal_id=proposal.proposal_id,
            proposal_revision=proposal.proposal_revision,
            proposal_digest=proposal.proposal_digest,
            basis_digest=reply_contract_values.proposal_basis_digest(proposal),
            tail_event_id=tail.event_id,
            tail_sequence=tail.sequence,
        )
        self._confirmed_capabilities[
            (
                decision.session_id,
                decision.conversation_id,
                decision.step_id,
            )
        ] = capability
        return self._execute_confirmed_step_locked(
            session_id=decision.session_id,
            conversation_id=decision.conversation_id,
            proposal=proposal,
            capability=capability,
            action_progress_sink=action_progress_sink,
            validate_proposal_authority=validate_proposal_authority,
            append_batch=append_batch,
            run_specialist=run_specialist,
            run_synthesis=run_synthesis,
            persist_results=persist_results,
            finalize_known_result=finalize_known_result,
        )

    def discard_unexecuted_confirmation(
        self,
        session_id: str,
        conversation_id: str,
        step_id: str,
    ) -> None:
        self._confirmed_capabilities.pop(
            (session_id, conversation_id, step_id), None
        )

    async def _execute_confirmed_step_locked(
        self,
        *,
        session_id: str,
        conversation_id: str,
        proposal: CollaborationProposal,
        capability: _ConfirmedStepCapability,
        action_progress_sink: ContextActionProgressSink | None,
        validate_proposal_authority: Callable[..., None],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
        run_specialist: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        run_synthesis: Callable[..., Coroutine[Any, Any, CollaborationInvocationResult]],
        persist_results: Callable[..., None],
        finalize_known_result: Callable[
            [str, tuple[CollaborationEvent, ...], CollaborationHistoryProjection], LeadTurnResult,
        ],
    ) -> LeadTurnResult:
        """Consume exact start authority and finish one confirmed provider round."""

        events = self._storage.event_store.read(
            session_id,
            conversation_id,
        )
        projection = project_collaboration_history(events)
        if (
            projection.current_state is not CollaborationStepState.CONFIRMED
            or projection.current_proposal != proposal
            or not events
            or not self._capability_matches(capability, proposal, events[-1])
        ):
            raise BackendProtocolError(
                "confirmed collaboration authority no longer matches the durable tail"
            )
        key = (session_id, conversation_id, proposal.step_id)
        try:
            validate_proposal_authority(
                session_id=session_id,
                events=events,
                projection=projection,
                proposal=proposal,
            )
        except (
            SessionArtifactBindingError,
            WorkspaceFormatError,
            WorkspaceSecurityError,
        ):
            if self._confirmed_capabilities.get(key) is not capability:
                raise BackendProtocolError(
                    "confirmed collaboration capability is absent"
                )
            capability.consume()
            self._confirmed_capabilities.pop(key, None)
            turn_id = event_journal.new_id("t")
            artifact_candidate = authoring_authority.artifact_candidate_for_proposal(
                events,
                proposal,
            )
            artifact_recovery = (
                ()
                if artifact_candidate is None
                else (
                    event_journal.draft(
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
                        ),
                    ),
                )
            )
            event_journal.append_drafts(
                projection.last_sequence,
                (
                    *artifact_recovery,
                    event_journal.draft(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        step_id=proposal.step_id,
                        kind=CollaborationEventKind.STEP_STATE,
                        sender=event_journal.RUNTIME_ACTOR,
                        recipient=event_journal.USER_ACTOR,
                        payload=CollaborationStepStatePayload(
                            state=CollaborationStepState.RECOVERY_REQUIRED,
                            proposal_id=proposal.proposal_id,
                            proposal_revision=proposal.proposal_revision,
                            proposal_digest=proposal.proposal_digest,
                        ),
                    ),
                ),
                append_batch=append_batch,
            )
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "The confirmed step could not revalidate its exact session "
                "artifact resources. Repair them before continuing; no provider "
                "work was started or replayed.",
                turn_id=turn_id,
                step_id=proposal.step_id,
            )
        if self._confirmed_capabilities.get(key) is not capability:
            raise BackendProtocolError("confirmed collaboration capability is absent")
        capability.consume()
        self._confirmed_capabilities.pop(key, None)

        attempt_id = event_journal.deterministic_id(
            "x",
            proposal.proposal_id,
            str(proposal.proposal_revision),
            proposal.proposal_digest,
        )
        turn_id = event_journal.deterministic_id("t", attempt_id, "execution")
        dispatches = tuple(
            event_journal.draft_with_id(
                event_id=event_journal.deterministic_id(
                    "e", attempt_id, assignment.assignment_id, "dispatch"
                ),
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.ASSIGNMENT_DISPATCH,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=CollaborationActor(
                    CollaborationActorKind.SPECIALIST,
                    assignment.specialist_id,
                ),
                payload=CollaborationAssignmentDispatchPayload(
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=attempt_id,
                    assignment=assignment,
                ),
            )
            for assignment in proposal.assignments
        )
        running = event_journal.draft_with_id(
            event_id=event_journal.deterministic_id("e", attempt_id, "running"),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=proposal.step_id,
            kind=CollaborationEventKind.STEP_STATE,
            sender=event_journal.RUNTIME_ACTOR,
            recipient=event_journal.USER_ACTOR,
            payload=CollaborationStepStatePayload(
                state=CollaborationStepState.RUNNING,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                proposal_digest=proposal.proposal_digest,
                execution_attempt_id=attempt_id,
            ),
        )
        artifact_candidate = authoring_authority.artifact_candidate_for_proposal(events, proposal)
        artifact_running = (
            ()
            if artifact_candidate is None
            else (
                event_journal.draft_with_id(
                    event_id=event_journal.deterministic_id(
                        "e", attempt_id, "artifact_reviewing"
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
                        ArtifactProposalState.REVIEWING,
                        execution_attempt_id=attempt_id,
                    ),
                ),
            )
        )
        event_journal.append_drafts(
            projection.last_sequence,
            (*artifact_running, running, *dispatches),
            batch_id=event_journal.deterministic_id("b", attempt_id, "start"),
            append_batch=append_batch,
        )
        marker = (session_id, conversation_id, proposal.step_id, attempt_id)
        self._live_execution_markers.add(marker)
        try:
            tasks = tuple(
                asyncio.create_task(
                    run_specialist(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment=assignment,
                        dispatch_event_id=dispatch.event_id,
                        events=events,
                        action_progress_sink=action_progress_sink,
                    ),
                    name=(
                        f"collaboration:{assignment.specialist_id}:"
                        f"{assignment.assignment_id}"
                    ),
                )
                for assignment, dispatch in zip(
                    proposal.assignments,
                    dispatches,
                    strict=True,
                )
            )
            try:
                joined = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError as cancellation:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                joined = await asyncio.gather(*tasks, return_exceptions=True)
                results = tuple(joined_invocation_result(item) for item in joined)
                try:
                    persist_results(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment_results=results,
                        synthesis_result=None,
                    )
                except BaseException as persistence_failure:
                    cancellation.add_note(
                        "collaboration cancellation persistence failed: "
                        f"{type(persistence_failure).__name__}"
                    )
                raise cancellation
            results = tuple(joined_invocation_result(item) for item in joined)

            synthesis_result: CollaborationInvocationResult | None = None
            if (
                all(item.failure is None for item in results)
                and proposal.step_kind
                is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            ):
                replies = tuple(item.value for item in results)
                if not all(
                    type(item) is CollaborationSpecialistReplyPayload
                    for item in replies
                ):
                    raise BackendProtocolError(
                        "successful specialist result is not typed"
                    )
                try:
                    synthesis_result = required_synthesis_result(
                        await run_synthesis(
                            session_id=session_id,
                            conversation_id=conversation_id,
                            turn_id=turn_id,
                            proposal=proposal,
                            attempt_id=attempt_id,
                            replies=cast(
                                tuple[CollaborationSpecialistReplyPayload, ...],
                                replies,
                            ),
                            events=events,
                            action_progress_sink=action_progress_sink,
                        )
                    )
                except Exception as failure:
                    synthesis_result = required_synthesis_result(
                        joined_invocation_result(failure)
                    )
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                cancellation = asyncio.CancelledError()
                try:
                    persist_results(
                        session_id=session_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        proposal=proposal,
                        attempt_id=attempt_id,
                        assignment_results=results,
                        synthesis_result=synthesis_result,
                    )
                except BaseException as persistence_failure:
                    cancellation.add_note(
                        "collaboration cancellation persistence failed: "
                        f"{type(persistence_failure).__name__}"
                    )
                raise cancellation
            persist_results(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                proposal=proposal,
                attempt_id=attempt_id,
                assignment_results=results,
                synthesis_result=synthesis_result,
            )
            persisted = self._storage.event_store.read(
                session_id,
                conversation_id,
            )
            finalized_projection = project_collaboration_history(persisted)
            authentication_failure = next(
                (
                    item.failure for item in (
                        *results,
                        *((synthesis_result,) if synthesis_result is not None else ()),
                    )
                    if isinstance(item.failure, ProviderAuthenticationRequiredError)
                ),
                None,
            )
            premise_result = finalized_projection.execution_result
            if (
                proposal.premise_decision is not None
                and proposal.authoring_scope is None
                and proposal.basis.evidence is not None
                and proposal.basis.evidence.scope is LeadOutcomeScope.PREMISE_ANALYSIS
                and finalized_projection.current_proposal == proposal
                and finalized_projection.current_step_id == proposal.step_id
                and premise_result is not None
                and (
                    premise_result.proposal_id,
                    premise_result.proposal_revision,
                    premise_result.proposal_digest,
                ) == (
                    proposal.proposal_id,
                    proposal.proposal_revision,
                    proposal.proposal_digest,
                )
                and premise_result.execution_attempt_id == attempt_id
                and finalized_projection.current_state is {
                    CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
                    CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
                    CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
                }.get(premise_result.overall_outcome)
                and any(
                    event.event_id == finalized_projection.execution_result_event_id
                    and event.step_id == proposal.step_id
                    and type(event.payload) is CollaborationExecutionResult
                    and event.payload is premise_result
                    for event in persisted
                )
                and (
                    premise_result.overall_outcome is not CollaborationExecutionOutcomeKind.SUCCEEDED
                    or (
                        finalized_projection.premise_report is not None
                        and finalized_projection.premise_report.spec == proposal.premise_decision
                        and finalized_projection.premise_report_event_id == premise_result.synthesis_result_event_id
                        and finalized_projection.premise_report_event_id in premise_result.visible_exchange_event_ids
                    )
                )
            ):
                finalized = finalize_known_result(
                    session_id,
                    persisted,
                    finalized_projection,
                )
                if authentication_failure is not None:
                    raise authentication_failure
                return finalized
            if (
                finalized_projection.current_state is CollaborationStepState.RUNNING
                or finalized_projection.current_recommendation_state
                is LeadRecommendationState.PENDING
            ):
                finalized = finalize_known_result(
                    session_id,
                    persisted,
                    finalized_projection,
                )
                if authentication_failure is not None:
                    raise authentication_failure
                return finalized
            if finalized_projection.current_state is not (
                CollaborationStepState.OUTCOME_UNKNOWN
            ):
                raise BackendProtocolError("execution did not reach a durable result")
            if authentication_failure is not None:
                raise authentication_failure
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "A submitted provider outcome is uncertain. Inspect and resolve it; "
                "the runtime will not replay the attempt.",
                None,
                CollaborationStepState.OUTCOME_UNKNOWN,
                None,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            )
        finally:
            self._live_execution_markers.add(marker)

    def normalize_history(
        self,
        session_id: str,
        conversation_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        *,
        close_failed_attempt: Callable[..., LeadTurnResult | None],
        finalize_recommendation_failure: Callable[[str, str], LeadTurnResult | None],
        finalize_known_result: Callable[
            [str, tuple[CollaborationEvent, ...], CollaborationHistoryProjection], LeadTurnResult,
        ],
        append_batch: Callable[[CollaborationEventBatch], CollaborationAppendReceipt],
    ) -> LeadTurnResult | None:
        if (projection.next_safe_action is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                and any(type(event.payload) is CollaborationProviderCallPayload
                        and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION
                        for event in events)):
            return blocking_lead_result(
                session_id, conversation_id, CollaborationStepState.OUTCOME_UNKNOWN,
                "A discussion call has no known durable closure. Inspect its exact history before controls or calls.",
                step_id=projection.current_step_id, turn_id=projection.current_turn_id,
            )
        operation_result = pending_artifact_operation_result(
            session_id, conversation_id, events, projection,
        )
        if operation_result is not None:
            return operation_result
        if projection.current_recommendation_state is LeadRecommendationState.RUNNING:
            start = projection.current_recommendation_start
            assert start is not None
            start_event = next(event for event in events if event.event_id == start.start_event_id)
            origin_calls = tuple(event for event in events
                if event.turn_id == start_event.turn_id and event.step_id == start_event.step_id
                and type(event.payload) is CollaborationProviderCallPayload)
            if not origin_calls:
                close_failed_attempt(session_id, conversation_id,
                    turn_id=start_event.turn_id, step_id=start_event.step_id)
            finalized = finalize_recommendation_failure(session_id, conversation_id)
            if finalized is not None:
                return finalized
            return blocking_lead_result(session_id, conversation_id,
                CollaborationStepState.OUTCOME_UNKNOWN,
                "The recommendation preparation has no reliable closed result. Inspect its exact history; it will not be replayed.",
                turn_id=start_event.turn_id, step_id=start_event.step_id)
        if (
            projection.next_safe_action is CollaborationNextAction.RETRY_LEAD_TURN
            and (has_unstarted_lead_request(events)
                 or unmatched_lead_call_state(events) is not None)
        ):
            recovered = close_failed_attempt(session_id, conversation_id)
            if recovered is not None:
                return recovered
        if has_unstarted_lead_request(events):
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A durable user request has no provider-preparation or lead-outcome "
                "marker. Inspect and repair the request before continuing; it will "
                "not be replayed automatically.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if (
            projection.current_proposal is None
            and projection.current_state is CollaborationStepState.OUTCOME_UNKNOWN
            and projection.next_safe_action
            is CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
        ):
            return submitted_lead_outcome_unknown_result(
                session_id,
                conversation_id,
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if (
            unmatched_lead_call_state(events)
            is CollaborationProviderCallState.PREPARED
        ):
            return blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A provider call may have crossed the boundary without a durable "
                "lead outcome; inspect and repair authoritative history before "
                "continuing.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        operation_result = pending_artifact_operation_result(
            session_id, conversation_id, events, projection,
        )
        if operation_result is not None:
            return operation_result
        state = projection.current_state
        proposal = projection.current_proposal
        if state is CollaborationStepState.CONFIRMED:
            if proposal is None or not events:
                return blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Confirmed history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            key = (session_id, conversation_id, proposal.step_id)
            capability = self._confirmed_capabilities.get(key)
            tail = events[-1]
            if capability is not None and self._capability_matches(
                capability,
                proposal,
                tail,
            ):
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    tail.turn_id,
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The confirmed collaboration step is ready for the runtime's "
                    "separately owned execution phase.",
                    proposal,
                    CollaborationStepState.CONFIRMED,
                    None,
                    CollaborationNextAction.START_OR_RECONFIRM,
                    evidence=proposal.basis.evidence,
                )
            self._confirmed_capabilities.pop(key, None)
            request_id = event_journal.new_id("c")
            turn_id = event_journal.new_id("t")
            reconfirmation = event_journal.draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.RECONFIRMATION_REQUIRED,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    confirmation_request_id=request_id,
                ),
            )
            event_journal.append_drafts(projection.last_sequence, (reconfirmation,),
                append_batch=append_batch,
            )
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "The prior confirmation lost its process-local start authority. " +
                ("Regenerate this legacy proposal with an explicit authoring scope before confirming."
                 if proposal.authoring_scope is None else
                 "Reconfirm, reject, or revise the unchanged proposal."),
                proposal,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
                evidence=proposal.basis.evidence,
            )
        if state is CollaborationStepState.RUNNING:
            if proposal is None or projection.execution_attempt_id is None:
                return blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Running history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            marker = (
                session_id,
                conversation_id,
                proposal.step_id,
                projection.execution_attempt_id,
            )
            if projection.execution_result is not None:
                return finalize_known_result(
                    session_id,
                    events,
                    projection,
                )
            if marker in self._live_execution_markers:
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    projection.current_turn_id or event_journal.new_id("t"),
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The collaboration step is still owned by a live runtime "
                    "execution; wait for its durable result.",
                    None,
                    CollaborationStepState.RUNNING,
                    None,
                    CollaborationNextAction.NONE,
                )
            turn_id = event_journal.new_id("t")
            interruption = event_journal.draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.INTERRUPTION,
                sender=event_journal.RUNTIME_ACTOR,
                recipient=event_journal.USER_ACTOR,
                payload=CollaborationInterruptionPayload(
                    projection.execution_attempt_id,
                    "Runtime authority was lost after work may have started; the "
                    "attempt will not be replayed.",
                ),
            )
            unknown = event_journal.draft(
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
                    execution_attempt_id=projection.execution_attempt_id,
                ),
            )
            event_journal.append_drafts(projection.last_sequence, (interruption, unknown),
                append_batch=append_batch,
            )
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "External work may have happened. Inspect and resolve the outcome; "
                "the runtime will not replay it.",
                None,
                CollaborationStepState.OUTCOME_UNKNOWN,
                None,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            )
        if state is CollaborationStepState.OUTCOME_UNKNOWN:
            return blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "External work may have happened. Inspect and resolve the outcome "
                "before proposing new work.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if state is CollaborationStepState.RECOVERY_REQUIRED:
            return blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "Authoritative collaboration history requires repair before normal "
                "collaboration can continue.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        return None

    @classmethod
    def _capability_matches(
        cls,
        capability: _ConfirmedStepCapability,
        proposal: CollaborationProposal,
        tail: CollaborationEvent,
    ) -> bool:
        return bool(
            capability.session_id == tail.session_id
            and capability.conversation_id == tail.conversation_id
            and capability.step_id == proposal.step_id == tail.step_id
            and capability.proposal_id == proposal.proposal_id
            and capability.proposal_revision == proposal.proposal_revision
            and capability.proposal_digest == proposal.proposal_digest
            and capability.basis_digest == reply_contract_values.proposal_basis_digest(proposal)
            and capability.tail_event_id == tail.event_id
            and capability.tail_sequence == tail.sequence
        )
