"""Validate collaboration execution results and atomic event families."""

from __future__ import annotations

from dataclasses import replace

from ..event_contracts.artifact_reviews import (
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
)
from ..event_contracts.authoring_records import (
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
)
from ..event_contracts.evidence import CollaborationLeadEvidence
from ..event_contracts.execution import (
    CollaborationAssignmentDispatchPayload,
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
)
from ..event_vocabulary import (
    ArtifactProposalState,
    CollaborationActorKind,
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalScope,
    LeadOutcomeScope,
)
from ..events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationLeadGroupTracePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationActionKind,
    LeadRecommendationRequestPayload,
)
from .lead_control_gaps import (
    closed_pure_discussion_control_gap_is_safe as _closed_pure_discussion_control_gap_is_safe,
)
from .task_source_history import task_scope_digest as _task_scope_digest
from ...lead_task_transitions import LeadTaskAction
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringScope,
)


def validate_collaboration_execution_result(
    *,
    event: CollaborationEventV2,
    result: CollaborationExecutionResult,
    proposal: CollaborationProposal,
    attempt_id: str | None,
    dispatches: dict[str, CollaborationEventV2],
    replies: dict[str, CollaborationEventV2],
    event_by_id: dict[str, CollaborationEventV2],
    batch: tuple[CollaborationEventV2, ...],
) -> None:
    if result.execution_attempt_id != attempt_id:
        raise ValueError("execution result attempt does not match running")
    expected_ids = tuple(item.assignment_id for item in proposal.assignments)
    actual_ids = tuple(item.assignment_id for item in result.assignment_outcomes)
    if actual_ids != expected_ids:
        raise ValueError("execution result outcomes must match proposal order exactly")
    if proposal.premise_decision is not None:
        suffix = batch[event.batch_ordinal + 1:]
        expected_terminal_state = {
            CollaborationExecutionOutcomeKind.SUCCEEDED: CollaborationStepState.COMPLETED,
            CollaborationExecutionOutcomeKind.FAILED: CollaborationStepState.FAILED,
            CollaborationExecutionOutcomeKind.CANCELLED: CollaborationStepState.INTERRUPTED,
        }.get(result.overall_outcome)
        if (
            proposal.step_kind is not CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            or type(proposal.basis.evidence) is not CollaborationLeadEvidence
            or proposal.basis.evidence.scope is not LeadOutcomeScope.PREMISE_ANALYSIS
            or expected_terminal_state is None
            or len(suffix) != 1
            or type(suffix[0].payload) is not CollaborationStepStatePayload
            or suffix[0].payload.state is not expected_terminal_state
            or suffix[0].payload.execution_result_event_id != event.event_id
            or suffix[0].step_id != event.step_id
        ):
            raise ValueError("analysis result requires its exact co-batched terminal state")
        if result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
            analysis_synthesis_event = event_by_id.get(result.synthesis_result_event_id)
            if (
                result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.SUCCEEDED
                or analysis_synthesis_event is None
                or type(analysis_synthesis_event.payload) is not CollaborationLeadSynthesisPayload
                or analysis_synthesis_event.payload.premise_report is None
                or analysis_synthesis_event.payload.premise_report.spec != proposal.premise_decision
            ):
                raise ValueError("successful analysis requires its exact typed report")
        elif result.synthesis_result_event_id is not None:
            raise ValueError("failed or cancelled analysis cannot publish a premise report")
    elif event is not batch[-1]:
        suffix = batch[event.batch_ordinal + 1:]
        if (len(suffix) != 2
            or type(suffix[0].payload) is not CollaborationStepStatePayload
            or suffix[0].payload.state is not CollaborationStepState.COMPLETED
            or suffix[0].payload.execution_result_event_id != event.event_id
            or suffix[0].step_id != event.step_id
            or type(suffix[1].payload) is not LeadRecommendationRequestPayload
            or suffix[1].payload.source_recommendation_event_id != result.synthesis_result_event_id
            or result.overall_outcome is not CollaborationExecutionOutcomeKind.SUCCEEDED):
            raise ValueError("execution result must be final or precede its exact synthesis recommendation closure")
    preceding = batch[: event.batch_ordinal]
    batch_event_ids = {item.event_id for item in preceding}
    successful_reply_ids: list[str] = []
    for assignment, outcome in zip(
        proposal.assignments,
        result.assignment_outcomes,
        strict=True,
    ):
        dispatch = dispatches.get(assignment.assignment_id)
        if dispatch is None:
            raise ValueError("execution result assignment was not dispatched")
        if outcome.submitted_physical_calls > assignment.max_physical_calls:
            raise ValueError("assignment result exceeds its call allocation")
        reply = replies.get(assignment.assignment_id)
        if outcome.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED:
            if reply is None or outcome.reply_event_id != reply.event_id:
                raise ValueError(
                    "successful assignment result requires its validated reply"
                )
            if reply.event_id not in batch_event_ids:
                raise ValueError(
                    "successful reply must precede its result in the same batch"
                )
            successful_reply_ids.append(reply.event_id)
        elif reply is not None:
            raise ValueError("failed or cancelled assignment cannot have a reply")
    actual_reply_ids = [
        item.event_id
        for item in preceding
        if item.kind is CollaborationEventKindV2.SPECIALIST_REPLY
    ]
    if actual_reply_ids != successful_reply_ids:
        raise ValueError("result-batch specialist replies must use proposal order")
    if (
        result.synthesis_submitted_physical_calls
        > proposal.budget.synthesis_max_physical_calls
    ):
        raise ValueError("execution result exceeds synthesis call allocation")
    if proposal.budget.synthesis_max_physical_calls == 0:
        if (
            result.synthesis_outcome
            is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
            or result.synthesis_submitted_physical_calls != 0
        ):
            raise ValueError("zero synthesis allocation forbids synthesis submission")
    elif (
        proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
        and all(
            item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
            for item in result.assignment_outcomes
        )
        and result.synthesis_outcome is CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
    ):
        raise ValueError(
            "successful consultation assignments require synthesis outcome"
        )
    if result.synthesis_result_event_id is not None:
        synthesis_event = event_by_id.get(result.synthesis_result_event_id)
        if (
            synthesis_event is None
            or synthesis_event.kind is not CollaborationEventKindV2.LEAD_SYNTHESIS
            or type(synthesis_event.payload) is not CollaborationLeadSynthesisPayload
        ):
            raise ValueError("synthesis result must reference a typed synthesis event")
        if result.synthesis_result_event_id not in batch_event_ids:
            raise ValueError(
                "synthesis result must precede execution result in the same batch"
            )

    terminal_events = [
        item
        for item in preceding
        if type(item.payload) is CollaborationTerminalOutcomePayload
    ]
    expected_terminal_count = len(result.assignment_outcomes)
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        expected_terminal_count += 1
    if len(terminal_events) != expected_terminal_count:
        raise ValueError("result batch must contain every required terminal outcome")
    for terminal_event, outcome in zip(
        terminal_events[: len(result.assignment_outcomes)],
        result.assignment_outcomes,
        strict=True,
    ):
        terminal = terminal_event.payload
        assert isinstance(terminal, CollaborationTerminalOutcomePayload)
        if (
            terminal.scope is not CollaborationTerminalScope.ASSIGNMENT
            or terminal.assignment_id != outcome.assignment_id
            or terminal.outcome is not outcome.outcome
            or terminal.submitted_physical_calls != outcome.submitted_physical_calls
        ):
            raise ValueError(
                "assignment terminal outcomes must match result order and facts"
            )
        if outcome.reply_event_id is not None:
            reply_event = event_by_id[outcome.reply_event_id]
            if reply_event.sequence >= terminal_event.sequence:
                raise ValueError("successful reply must precede its terminal outcome")
    if result.synthesis_outcome is not CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED:
        synthesis_terminal = terminal_events[-1].payload
        assert isinstance(synthesis_terminal, CollaborationTerminalOutcomePayload)
        synthesis_kind = CollaborationAssignmentOutcomeKind(
            result.synthesis_outcome.value
        )
        if (
            synthesis_terminal.scope is not CollaborationTerminalScope.SYNTHESIS
            or synthesis_terminal.assignment_id is not None
            or synthesis_terminal.outcome is not synthesis_kind
            or synthesis_terminal.submitted_physical_calls
            != result.synthesis_submitted_physical_calls
        ):
            raise ValueError("synthesis terminal outcome does not match result facts")
        if result.synthesis_result_event_id is not None:
            synthesis_event = event_by_id[result.synthesis_result_event_id]
            if synthesis_event.sequence >= terminal_events[-1].sequence:
                raise ValueError("synthesis message must precede its terminal outcome")

    for item in preceding:
        if item.kind in (
            CollaborationEventKindV2.SPECIALIST_REPLY,
            CollaborationEventKindV2.LEAD_SYNTHESIS,
            CollaborationEventKindV2.ARTIFACT_REVIEW_SYNTHESIS,
            CollaborationEventKindV2.SYNCHRONIZATION,
            CollaborationEventKindV2.TERMINAL_OUTCOME,
        ):
            continue
        raise ValueError("result batch contains an unrelated external exchange")
    expected_visible_ids = tuple(item.event_id for item in preceding)
    if result.visible_exchange_event_ids != expected_visible_ids:
        raise ValueError(
            "visible exchange event IDs must exactly match result-batch order"
        )
    used = (
        sum(item.submitted_physical_calls for item in result.assignment_outcomes)
        + result.synthesis_submitted_physical_calls
    )
    if used > proposal.budget.max_additional_physical_calls:
        raise ValueError("execution result exceeds the reserved whole-step budget")
    if (
        proposal.budget.physical_calls_spent + used
        > proposal.budget.physical_call_limit
    ):
        raise ValueError("execution result exceeds the lineage call ceiling")


def validate_collaboration_co_batch(
    *,
    batch: tuple[CollaborationEventV2, ...],
    prior_tail: CollaborationEventV2 | None,
    proposals: list[CollaborationProposal],
    states: list[tuple[CollaborationEventV2, CollaborationStepStatePayload]],
    confirmations: list[tuple[CollaborationEventV2, CollaborationConfirmationPayload]],
    dispatches: list[
        tuple[CollaborationEventV2, CollaborationAssignmentDispatchPayload]
    ],
    replies: list[tuple[CollaborationEventV2, CollaborationSpecialistReplyPayload]],
    results: list[tuple[CollaborationEventV2, CollaborationExecutionResult]],
    interruptions: list[CollaborationEventV2],
    active_proposals: dict[str, CollaborationProposal],
    task_control_history: tuple[CollaborationEventV2, ...] = (),
) -> None:
    for trace_event in batch:
        if type(trace_event.payload) is not CollaborationLeadGroupTracePayload:
            continue
        trace = trace_event.payload.trace
        terminal_type = (CollaborationLeadJudgmentPayload if trace.terminal_code == "accepted"
                         else CollaborationLeadOutcomeFailurePayload)
        if not any(type(source.payload) is terminal_type
                   and (source.turn_id, source.step_id) == (trace_event.turn_id, trace_event.step_id)
                   and getattr(source.payload, "provider_invocation_id" if trace.terminal_code == "accepted" else "invocation_id", None) == trace.invocation_id
                   for source in batch):
            raise ValueError("private group trace requires its exact co-published terminal result")
    awaiting = [
        item
        for item in states
        if item[1].state is CollaborationStepState.AWAITING_CONFIRMATION
    ]
    reconfirming = [
        item
        for item in states
        if item[1].state is CollaborationStepState.RECONFIRMATION_REQUIRED
    ]
    if proposals and len(proposals) != len(awaiting):
        raise ValueError("proposal disclosure and awaiting state must be co-batched")
    if awaiting and len(proposals) != len(awaiting):
        raise ValueError("awaiting state requires co-batched proposal disclosure")
    for event, _payload in awaiting + reconfirming:
        if event is not batch[-1]:
            raise ValueError("confirmation-request state must be final in its batch")

    if confirmations:
        if len(confirmations) != 1:
            raise ValueError("one batch may decide only one confirmation request")
        confirmation = confirmations[0][1]
        expected_state = {
            CollaborationConfirmationDecision.CONFIRM: CollaborationStepState.CONFIRMED,
            CollaborationConfirmationDecision.REJECT: CollaborationStepState.REJECTED,
            CollaborationConfirmationDecision.REVISE: CollaborationStepState.SUPERSEDED,
        }[confirmation.decision]
        matching = [item for item in states if item[1].state is expected_state]
        artifact_stale = any(
            type(item.payload) is ArtifactProposalStatePayload
            and item.payload.state is ArtifactProposalState.STALE
            for item in batch
        )
        if (
            confirmation.decision is CollaborationConfirmationDecision.CONFIRM
            and artifact_stale
        ):
            matching = [
                item
                for item in states
                if item[1].state is CollaborationStepState.SUPERSEDED
            ]
        if len(matching) != 1:
            raise ValueError("decision and resulting state must be co-batched")
    else:
        decision_states = [
            (event, payload)
            for event, payload in states
            if payload.state
            in (
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
            )
        ]
        ordinary_supersession = bool(
            len(batch) == 2
            and len(decision_states) == 1
            and decision_states[0][1].state is CollaborationStepState.SUPERSEDED
            and type(batch[0].payload) is CollaborationMessagePayload
            and batch[0].sender.kind.value == "user"
            and batch[0].recipient.kind.value == "lead"
            and batch[1] is decision_states[0][0]
            and batch[1].sender.kind.value == "runtime"
            and batch[1].recipient.kind.value == "user"
            and batch[0].step_id == batch[1].step_id
            and batch[0].turn_id == batch[1].turn_id
            and prior_tail is not None
            and prior_tail.step_id == batch[0].step_id
            and type(prior_tail.payload) is CollaborationStepStatePayload
            and prior_tail.payload.state
            in {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
            }
        )
        task_supersession = False
        if (
            task_control_history
            and len(decision_states) == 1
            and decision_states[0][1].state is CollaborationStepState.SUPERSEDED
            and prior_tail is not None
            and type(prior_tail.payload) is CollaborationStepStatePayload
            and prior_tail.payload.state in {CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RECONFIRMATION_REQUIRED}
        ):
            state_event, state = decision_states[0]
            original_batch = tuple(
                item for item in task_control_history if item.batch_id == state_event.batch_id
            )
            origins = tuple(item for item in original_batch
                            if type(item.payload) is CollaborationLeadTaskDecisionPayload)
            transitions = tuple(item for item in original_batch
                                if type(item.payload) is CollaborationLeadTaskTransitionPayload)
            if len(origins) == len(transitions) == 1:
                origin, transition = origins[0], transitions[0]
                decision = origin.payload.decision
                receipt = transition.payload.receipt
                current = active_proposals.get(state_event.step_id)
                ref = decision.task_ref
                source = None if ref is None else next((
                    item for item in task_control_history if item.event_id == ref.scope_event_id
                ), None)
                source_scope = None if source is None else getattr(source.payload, "authoring_scope", None)
                reference_exact = ref is None or bool(
                    source is not None and type(source_scope) is AuthoringScope
                    and (source.session_id, source.conversation_id, source_scope.task_id,
                         source_scope.task_revision, _task_scope_digest(source_scope))
                    == (ref.session_id, ref.conversation_id, ref.task_id,
                        ref.task_revision, ref.scope_digest)
                    and (ref.session_id, ref.conversation_id)
                    == (state_event.session_id, state_event.conversation_id)
                    and source.sequence < origin.sequence
                )
                current_scope = None if current is None else current.authoring_scope
                current_source = bool(
                    ref is not None and reference_exact and current_scope is not None
                    and (ref.task_id, ref.task_revision)
                    == (current_scope.task_id, current_scope.task_revision)
                    and (
                        current_scope == source_scope
                        or (
                            source_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                            and current_scope.activity is AuthoringActivity.REVIEW
                            and replace(current_scope, activity=source_scope.activity) == source_scope
                            and any(
                                item.step_id == current.step_id
                                and type(item.payload) is LeadRecommendationRequestPayload
                                and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                                and item.payload.authoring_scope == source_scope
                                and item.payload.source_execution_attempt_id is not None
                                and item.sequence < origin.sequence
                                for item in task_control_history
                            )
                            and any(
                                item.step_id == current.step_id
                                and type(item.payload) is ArtifactReviewCandidatePayload
                                and (
                                    item.payload.consultation_proposal_id,
                                    item.payload.consultation_proposal_revision,
                                    item.payload.consultation_proposal_digest,
                                ) == (current.proposal_id, current.proposal_revision, current.proposal_digest)
                                and item.sequence < origin.sequence
                                for item in task_control_history
                            )
                        )
                    )
                )
                independent_new = bool(
                    decision.action is LeadTaskAction.NEW_TASK and reference_exact
                    and receipt.target_task_id is not None and receipt.target_task_revision == 1
                    and (current_scope is None or receipt.target_task_id != current_scope.task_id)
                )
                target_exact = (
                    receipt.target_task_id is None and receipt.target_task_revision is None
                    and receipt.planning_step_id is None
                ) if decision.action in {LeadTaskAction.HOLD, LeadTaskAction.DISCARD} else (
                    current_source and receipt.target_task_id == ref.task_id
                    and receipt.target_task_revision == ref.task_revision + 1
                    and receipt.planning_step_id == transition.step_id
                ) if decision.action is LeadTaskAction.RESUME else (
                    independent_new and receipt.planning_step_id == transition.step_id
                )
                allowed_rows = all(
                    item in (origin, transition, state_event)
                    or (type(item.payload) is CollaborationMessagePayload
                        and item.event_id == receipt.origin_message_event_id
                        and item.sender.kind is CollaborationActorKind.USER)
                    or (type(item.payload) is CollaborationLeadMaterialCapturePayload
                        and item.event_id in receipt.material_capture_event_ids)
                    for item in original_batch
                )
                task_supersession = bool(
                    current is not None and allowed_rows and target_exact
                    and decision.action in {
                        LeadTaskAction.HOLD, LeadTaskAction.DISCARD,
                        LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK,
                    }
                    and (current_source or independent_new)
                    and origin.sender.kind is CollaborationActorKind.USER
                    and origin.recipient.kind is CollaborationActorKind.RUNTIME
                    and transition.sender.kind is CollaborationActorKind.RUNTIME
                    and transition.recipient.kind is CollaborationActorKind.USER
                    and state_event.sender.kind is CollaborationActorKind.RUNTIME
                    and state_event.recipient.kind is CollaborationActorKind.USER
                    and (origin.session_id, origin.conversation_id, origin.turn_id, origin.batch_id)
                    == (transition.session_id, transition.conversation_id, transition.turn_id, transition.batch_id)
                    == (state_event.session_id, state_event.conversation_id, state_event.turn_id, state_event.batch_id)
                    and origin.step_id == transition.step_id
                    and origin.event_id == decision.request_id
                    and receipt.origin_kind == "task_decision"
                    and receipt.origin_event_id == origin.event_id
                    and (receipt.action, receipt.source_task_ref) == (decision.action, decision.task_ref)
                    and prior_tail.step_id == state_event.step_id == current.step_id
                    and (prior_tail.payload.proposal_id, prior_tail.payload.proposal_revision,
                         prior_tail.payload.proposal_digest)
                    == (state.proposal_id, state.proposal_revision, state.proposal_digest)
                    == (current.proposal_id, current.proposal_revision, current.proposal_digest)
                    and (origin.sequence == prior_tail.sequence + 1
                         or _closed_pure_discussion_control_gap_is_safe(
                             prior_tail, origin, task_control_history,
                         ))
                )
        if decision_states and not (ordinary_supersession or task_supersession):
            raise ValueError(
                "decision-derived state requires a co-batched confirmation"
            )

    running = [
        (event, payload)
        for event, payload in states
        if payload.state is CollaborationStepState.RUNNING
    ]
    if running:
        if len(running) != 1:
            raise ValueError("one batch may start only one collaboration step")
        running_event, _running_payload = running[0]
        proposal = active_proposals[running_event.step_id]
        expected_dispatch_ids = tuple(
            item.assignment_id for item in proposal.assignments
        )
        actual = tuple(item.assignment.assignment_id for _event, item in dispatches)
        if actual != expected_dispatch_ids:
            raise ValueError(
                "running state must co-batch every dispatch in proposal order"
            )
    elif dispatches:
        raise ValueError("assignment dispatches must co-batch the running state")

    terminal_outcomes = [
        event
        for event in batch
        if type(event.payload) is CollaborationTerminalOutcomePayload
    ]
    outcome_unknown = any(
        payload.state is CollaborationStepState.OUTCOME_UNKNOWN
        for _event, payload in states
    )
    if replies or terminal_outcomes:
        if len(results) != 1 and not outcome_unknown:
            raise ValueError(
                "external result exchanges require one co-batched execution result"
            )
    syntheses = [
        event
        for event in batch
        if type(event.payload) is CollaborationLeadSynthesisPayload
    ]
    if syntheses and len(results) != 1:
        raise ValueError("lead synthesis requires one co-batched execution result")
    if len(results) > 1:
        raise ValueError("one batch may record only one execution result")

    terminal_states = [
        (event, payload)
        for event, payload in states
        if payload.state
        in (
            CollaborationStepState.COMPLETED,
            CollaborationStepState.FAILED,
            CollaborationStepState.INTERRUPTED,
        )
    ]
    for state_event, state_payload in terminal_states:
        has_notice = any(
            event.step_id == state_event.step_id
            and event.kind is CollaborationEventKindV2.MESSAGE
            and type(event.payload) is CollaborationMessagePayload
            for event in batch
        )
        if not has_notice:
            has_notice = any(
                type(event.payload) is LeadRecommendationRequestPayload
                and any(source.event_id == event.payload.source_recommendation_event_id
                        and source.step_id == state_event.step_id
                        and type(source.payload) is CollaborationLeadSynthesisPayload
                        for source in batch)
                for event in batch
            )
        premise_proposal = active_proposals.get(state_event.step_id)
        premise_result = next(
            (
                payload
                for event, payload in results
                if event.event_id == state_payload.execution_result_event_id
                and event.step_id == state_event.step_id
            ),
            None,
        )
        premise_notice = False
        if (
            premise_proposal is not None
            and premise_proposal.premise_decision is not None
            and premise_proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
            and premise_proposal.authoring_scope is None
            and premise_proposal.basis.evidence is not None
            and premise_proposal.basis.evidence.scope is LeadOutcomeScope.PREMISE_ANALYSIS
            and premise_result is not None
            and (
                premise_result.proposal_id,
                premise_result.proposal_revision,
                premise_result.proposal_digest,
            ) == (
                premise_proposal.proposal_id,
                premise_proposal.proposal_revision,
                premise_proposal.proposal_digest,
            )
        ):
            if premise_result.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED:
                premise_notice = any(
                    event.event_id == premise_result.synthesis_result_event_id
                    and event.event_id in premise_result.visible_exchange_event_ids
                    and event.step_id == state_event.step_id
                    and event.kind is CollaborationEventKindV2.LEAD_SYNTHESIS
                    and event.sender.kind.value == "lead"
                    and event.recipient.kind.value == "user"
                    and type(event.payload) is CollaborationLeadSynthesisPayload
                    and event.payload.premise_report is not None
                    and event.payload.premise_report.spec == premise_proposal.premise_decision
                    and event.payload.execution_attempt_id == premise_result.execution_attempt_id
                    for event in batch
                )
            elif premise_result.overall_outcome in (
                CollaborationExecutionOutcomeKind.FAILED,
                CollaborationExecutionOutcomeKind.CANCELLED,
            ):
                premise_notice = any(
                    event.event_id in premise_result.visible_exchange_event_ids
                    and event.step_id == state_event.step_id
                    and event.kind is CollaborationEventKindV2.TERMINAL_OUTCOME
                    and event.sender.kind.value == "runtime"
                    and event.recipient.kind.value == "user"
                    and type(event.payload) is CollaborationTerminalOutcomePayload
                    and event.payload.execution_attempt_id == premise_result.execution_attempt_id
                    and event.payload.outcome.value == premise_result.overall_outcome.value
                    for event in terminal_outcomes
                )
        has_notice = has_notice or premise_notice
        if not has_notice:
            raise ValueError("terminal step state requires a co-batched visible notice")
        if (
            state_payload.state is CollaborationStepState.INTERRUPTED
            and not interruptions
        ):
            result = next(
                (
                    item
                    for _event, item in results
                    if _event.event_id == state_payload.execution_result_event_id
                ),
                None,
            )
            if result is not None and not (
                premise_notice
                and premise_result is not None
                and premise_result.overall_outcome is CollaborationExecutionOutcomeKind.CANCELLED
            ):
                raise ValueError("interrupted state requires a co-batched interruption")

    unknown_states = [
        item
        for item in states
        if item[1].state is CollaborationStepState.OUTCOME_UNKNOWN
    ]
    if unknown_states and not interruptions:
        raise ValueError(
            "outcome-unknown state requires a co-batched interruption record"
        )
    if interruptions and not any(
        payload.state
        in (
            CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )
        for _event, payload in states
    ):
        raise ValueError("interruption must co-batch its corresponding terminal state")

    for _event, result in results:
        terminal_payloads = [
            event.payload
            for event in batch
            if type(event.payload) is CollaborationTerminalOutcomePayload
            and event.payload.execution_attempt_id == result.execution_attempt_id
        ]
        assignment_terminal_ids = tuple(
            item.assignment_id
            for item in terminal_payloads
            if item.scope is CollaborationTerminalScope.ASSIGNMENT
        )
        expected_assignment_ids = tuple(
            item.assignment_id for item in result.assignment_outcomes
        )
        if (
            assignment_terminal_ids
            and assignment_terminal_ids != expected_assignment_ids
        ):
            raise ValueError("terminal outcomes must match result assignments in order")
