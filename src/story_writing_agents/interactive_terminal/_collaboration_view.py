"""Project validated collaboration events into safe semantic terminal views."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from ..conversations import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    LeadRecommendationRequestPayload,
    LeadRecommendationDecisionPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationResultPayload,
    LeadObjectiveAction,
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationDecisionResult,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
    ArtifactReviewFailurePayload,
    ArtifactReviewSynthesisPayload,
    CollaborationAssignment,
    CollaborationAssignmentDispatchPayload,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationExecutionResult,
    CollaborationInterruptionPayload,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadEvidence,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
    LeadTurnResult,
    StoryCommitDecisionPayload,
    StoryCommitDecisionResult,
    StoryCommitRequestPayload,
    StoryCommitResultPayload,
)
from ..reply_trace import ReplyTrace
from ..specialist_routing import AuthoringScope
from ..writing_artifacts import (
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
)
from ._model import (
    Field,
    ListView,
    Message,
    MessagePresentation,
    Notice,
    Panel,
    Record,
    Section,
    SemanticRole,
    ViewState,
)


_STATE_GUIDANCE = {
    CollaborationStepState.AWAITING_CONFIRMATION: (
        "Nothing has started. Confirm, reject, revise, or continue the conversation."
    ),
    CollaborationStepState.RECONFIRMATION_REQUIRED: (
        "Live start authority was lost. Review the unchanged proposal and decide again."
    ),
    CollaborationStepState.CONFIRMED: (
        "The decision is durable; this record alone cannot start work after restart."
    ),
    CollaborationStepState.RUNNING: "A fresh attempt was recorded before external work.",
    CollaborationStepState.COMPLETED: "The complete visible result is durable.",
    CollaborationStepState.REJECTED: "The proposal did not run.",
    CollaborationStepState.SUPERSEDED: (
        "This proposal is no longer confirmable; a replacement or new input took precedence."
    ),
    CollaborationStepState.INTERRUPTED: (
        "Work stopped with a known outcome. Review the visible result before continuing."
    ),
    CollaborationStepState.FAILED: "The attempt failed with inspectable terminal facts.",
    CollaborationStepState.OUTCOME_UNKNOWN: (
        "External work may have happened. Do not replay it; inspect and recover."
    ),
    CollaborationStepState.RECOVERY_REQUIRED: (
        "Authoritative history is uncertain. Normal collaboration requires recovery."
    ),
}


def project_collaboration_event(
    event: CollaborationEvent,
    *,
    show_details: bool = False,
    input_references: Mapping[str, str] | None = None,
    candidate_paths: Mapping[str, tuple[str, ...]] | None = None,
    proposals: Mapping[str, CollaborationProposal] | None = None,
) -> Panel:
    """Return one complete semantic view for one validated durable event."""

    if type(event) is not CollaborationEvent:
        raise TypeError("event must be a CollaborationEvent")
    if type(show_details) is not bool:
        raise TypeError("show_details must be a bool")
    references = {} if input_references is None else input_references
    paths = {} if candidate_paths is None else candidate_paths
    proposal_index = {} if proposals is None else proposals

    items: list[Section | Field | Notice | Message | Record | ListView] = [
        Field("Sequence", str(event.sequence)),
        Field("From", _actor(event.sender.kind.value, event.sender.agent_id)),
        Field("To", _actor(event.recipient.kind.value, event.recipient.agent_id)),
        Field("Step", event.step_id),
    ]
    items.extend(_payload_items(event.payload, references, paths, proposal_index))
    if show_details:
        items.append(
            Section(
                "Record identity",
                (
                    Field("Event", event.event_id),
                    Field("Turn", event.turn_id),
                    Field("Conversation", event.conversation_id),
                    Field("Batch", event.batch_id),
                    Field("Batch position", f"{event.batch_ordinal + 1}/{event.batch_size}"),
                    Field("Timestamp", event.timestamp),
                ),
            )
        )
    return Panel(
        f"Collaboration · {event.kind.value.replace('_', ' ')}",
        tuple(items),
        state=_event_state(event.payload),
    )


def project_lead_turn_result(result: LeadTurnResult) -> Panel:
    """Project one runtime-validated visible lead result without side effects."""

    if type(result) is not LeadTurnResult:
        raise TypeError("result must be a LeadTurnResult")
    items: list[Section | Field | Notice | Message | Record | ListView] = [
        Field("Outcome", result.kind.value),
        _message("Lead", result.message, role=SemanticRole.AGENT),
        *_reply_trace_items(result.trace),
    ]
    if result.proposal is not None:
        items.extend(_proposal_items(result.proposal))
    if result.evidence is not None:
        items.extend(_evidence_items(result.evidence))
    items.append(Field("Next action", result.next_action.value))
    if result.state is not None:
        items.extend(
            (
                Field("State", result.state.value),
                Notice(
                    _STATE_GUIDANCE[result.state],
                    label="next action",
                    role=(
                        SemanticRole.ERROR
                        if result.state
                        in {
                            CollaborationStepState.OUTCOME_UNKNOWN,
                            CollaborationStepState.RECOVERY_REQUIRED,
                        }
                        else SemanticRole.INFO
                    ),
                ),
            )
        )
    if result.artifact_proposal is not None:
        items.append(
            Field("Artifact proposal", result.artifact_proposal.state.value)
        )
    if result.artifact_application_request is not None:
        items.extend(
            _payload_items(result.artifact_application_request, {}, {}, {})
        )
    if result.story_commit_request is not None:
        items.extend(_payload_items(result.story_commit_request, {}, {}, {}))
    if result.recommendation_request is not None:
        items.extend(_payload_items(result.recommendation_request, {}, {}, {}))
    return Panel("Lead", tuple(items), state=_event_state(result))


def project_lead_error_result(
    error_message: str,
    *,
    advice: str = "Inspect /history and /exchanges before continuing.",
) -> Panel:
    """Project one visible error result when a lead turn fails before validation."""

    if type(error_message) is not str or not error_message.strip():
        raise ValueError("error_message must be nonempty text")
    items = (
        Field("Outcome", "failed"),
        _message("Lead", error_message.strip(), role=SemanticRole.ERROR),
        Field("Next action", advice),
    )
    return Panel("Lead", items, state=ViewState.ERROR)


def project_specialist_results(
    events: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
) -> tuple[Panel, ...]:
    """Join durable dispatch, reply, and outcome truth for one execution."""

    validate_collaboration_event_order(events)
    durable_proposals = tuple(
        event.payload
        for event in events
        if type(event.payload) is CollaborationProposal
        and event.step_id == proposal.step_id
        and event.payload.proposal_id == proposal.proposal_id
        and event.payload.proposal_revision == proposal.proposal_revision
        and event.payload.proposal_digest == proposal.proposal_digest
    )
    if len(durable_proposals) != 1 or durable_proposals[0] != proposal:
        raise ValueError("specialist result proposal does not match durable authority")

    results = tuple(
        event
        for event in events
        if type(event.payload) is CollaborationExecutionResult
        and event.step_id == proposal.step_id
        and event.payload.proposal_id == proposal.proposal_id
        and event.payload.proposal_revision == proposal.proposal_revision
        and event.payload.proposal_digest == proposal.proposal_digest
    )
    if not results:
        return ()
    if len(results) != 1:
        raise ValueError("specialist result has ambiguous execution authority")
    result = results[0].payload
    assert isinstance(result, CollaborationExecutionResult)
    dispatch_events = tuple(
        event
        for event in events
        if type(event.payload) is CollaborationAssignmentDispatchPayload
        and event.step_id == proposal.step_id
        and event.payload.proposal_id == proposal.proposal_id
        and event.payload.proposal_revision == proposal.proposal_revision
        and event.payload.proposal_digest == proposal.proposal_digest
        and event.payload.execution_attempt_id == result.execution_attempt_id
    )
    expected_assignments = proposal.assignments
    if (
        len(dispatch_events) != len(expected_assignments)
        or tuple(event.payload.assignment for event in dispatch_events)
        != expected_assignments
    ):
        raise ValueError("specialist dispatches do not match the exact proposal")
    outcomes = result.assignment_outcomes
    if tuple(item.assignment_id for item in outcomes) != tuple(
        item.assignment_id for item in expected_assignments
    ):
        raise ValueError("specialist outcomes do not match proposal order")
    reply_events = {
        event.event_id: event
        for event in events
        if type(event.payload) is CollaborationSpecialistReplyPayload
        and event.step_id == proposal.step_id
        and event.payload.execution_attempt_id == result.execution_attempt_id
    }
    expected_reply_ids = {
        item.reply_event_id for item in outcomes if item.reply_event_id is not None
    }
    if set(reply_events) != expected_reply_ids:
        raise ValueError("specialist replies do not match execution outcomes")

    panels: list[Panel] = []
    for assignment, dispatch_event, outcome in zip(
        expected_assignments,
        dispatch_events,
        outcomes,
        strict=True,
    ):
        dispatch = dispatch_event.payload
        assert isinstance(dispatch, CollaborationAssignmentDispatchPayload)
        if (
            dispatch.assignment != assignment
            or dispatch.execution_attempt_id != result.execution_attempt_id
        ):
            raise ValueError("specialist dispatch identity is inconsistent")
        durable_assignment = dispatch.assignment
        reply_event = reply_events.get(outcome.reply_event_id or "")
        reply = None if reply_event is None else reply_event.payload
        if reply is not None and (
            not isinstance(reply, CollaborationSpecialistReplyPayload)
            or reply.proposal_id != proposal.proposal_id
            or reply.proposal_revision != proposal.proposal_revision
            or reply.proposal_digest != proposal.proposal_digest
            or reply.execution_attempt_id != result.execution_attempt_id
            or reply.assignment_id != durable_assignment.assignment_id
            or reply.dispatch_event_id != dispatch_event.event_id
            or reply.input_ids != durable_assignment.input_ids
            or reply.mode is not durable_assignment.mode
            or reply_event is None
            or reply_event.sender.agent_id != durable_assignment.specialist_id
        ):
            raise ValueError("specialist reply identity is inconsistent")

        result_items: list[Section | Field | Notice | Message | Record | ListView] = [
            Field("Round", str(durable_assignment.round)),
            Field(
                "Mode",
                (
                    "legacy"
                    if durable_assignment.mode is None
                    else durable_assignment.mode.value
                ),
            ),
            _message("Request", durable_assignment.instruction),
            Field("Status", outcome.outcome.value),
            Field("Provider calls", str(outcome.submitted_physical_calls)),
        ]
        if reply is not None:
            assert isinstance(reply, CollaborationSpecialistReplyPayload)
            result_items.extend(
                (
                    Field("Assessment", reply.assessment.value),
                    _message(
                        "Specialist answer",
                        reply.message,
                        role=SemanticRole.AGENT,
                    ),
                    *_reply_trace_items(reply.trace),
                )
            )
        else:
            result_items.append(
                Notice(
                    "No specialist answer was recorded for this terminal outcome.",
                    label="specialist result",
                    role=SemanticRole.WARNING,
                )
            )
        panels.append(
            Panel(
                durable_assignment.specialist_id,
                tuple(result_items),
                state=(
                    ViewState.COMPLETED
                    if reply is not None
                    else (
                        ViewState.CANCELLED
                        if outcome.outcome.value == "cancelled"
                        else ViewState.ERROR
                    )
                ),
            )
        )
    return tuple(panels)


def project_artifact_application_decision_result(
    result: ArtifactApplicationDecisionResult,
) -> Panel:
    """Project one exact artifact decision result and optional commit request."""

    if type(result) is not ArtifactApplicationDecisionResult:
        raise TypeError("result must be an ArtifactApplicationDecisionResult")
    items: list[Section | Field | Notice | Message | Record | ListView] = [
        _message("Lead", result.message),
        *_payload_items(result.outcome, {}, {}, {}),
    ]
    if result.commit_request is not None:
        items.extend(_payload_items(result.commit_request, {}, {}, {}))
    return Panel("Artifact application", tuple(items), state=_event_state(result.outcome))


def project_story_commit_decision_result(
    result: StoryCommitDecisionResult,
) -> Panel:
    """Project one exact local commit decision result without repository I/O."""

    if type(result) is not StoryCommitDecisionResult:
        raise TypeError("result must be a StoryCommitDecisionResult")
    return Panel(
        "Local story commit",
        (
            _message("Lead", result.message),
            *_payload_items(result.outcome, {}, {}, {}),
        ),
        state=_event_state(result.outcome),
    )


def validate_collaboration_event_order(
    events: tuple[CollaborationEvent, ...],
) -> None:
    """Reject mixed, duplicate, or out-of-order durable projections."""

    if type(events) is not tuple or any(type(item) is not CollaborationEvent for item in events):
        raise TypeError("events must be a tuple of CollaborationEvent values")
    if not events:
        return
    scope = (events[0].session_id, events[0].conversation_id)
    previous = 0
    for event in events:
        if (event.session_id, event.conversation_id) != scope:
            raise ValueError("collaboration events must share one session and conversation")
        if event.sequence <= previous:
            raise ValueError("collaboration events must be in strict durable order")
        previous = event.sequence


def collaboration_view_context(
    events: tuple[CollaborationEvent, ...],
) -> tuple[
    dict[str, str],
    dict[str, tuple[str, ...]],
    dict[str, CollaborationProposal],
]:
    """Derive visible source and candidate-path context from the same history."""

    validate_collaboration_event_order(events)
    references: dict[str, str] = {}
    candidates: dict[str, tuple[str, ...]] = {}
    proposals: dict[str, CollaborationProposal] = {}
    for event in events:
        payload = event.payload
        if type(payload) is CollaborationProposal:
            existing_proposal = proposals.setdefault(payload.proposal_id, payload)
            if existing_proposal != payload:
                raise ValueError("collaboration proposal identity changed in history")
            for item in payload.basis.inputs:
                reference = _range_reference(
                    item.locator,
                    item.start_line,
                    item.end_line,
                )
                existing = references.setdefault(item.input_id, reference)
                if existing != reference:
                    raise ValueError("collaboration input identity changed in history")
        if type(payload) is ArtifactReviewCandidatePayload:
            candidates[payload.candidate.candidate_id] = _operation_path_tuple(
                payload.candidate.change_set.operations
            )
    return references, candidates, proposals


def _payload_items(
    payload: object,
    input_references: Mapping[str, str],
    candidate_paths: Mapping[str, tuple[str, ...]],
    proposals: Mapping[str, CollaborationProposal],
) -> tuple[Section | Field | Notice | Message | Record | ListView, ...]:
    if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2}:
        return (
            _message("Artifact objective", payload.facts.request),
            Field("Action", payload.action.value),
            Field("Target paths", _joined(payload.facts.target_paths)),
            Field("Next step", payload.next_step.value if payload.next_step else "none"),
            Field("Objective", payload.facts.objective_id),
            Field("Owner conversation", payload.facts.origin_conversation_id),
            Field("Draft sources", _joined(tuple(item.event_id for item in payload.draft_sources))),
            Notice(
                "Review, Apply, and Commit have separate decisions. "
                "Only a successful Apply result completes this authoring task.",
                label="objective state",
            ),
        )
    if type(payload) is LeadRecommendationRequestPayload:
        return (
            *_authoring_scope_items(payload.authoring_scope),
            Field("Preparation", payload.kind.value),
            _message("Purpose", payload.purpose), _message("Instruction", payload.instruction),
            _message("Expected result", payload.expected_result),
            Field("Sources", _joined(tuple(item.locator for item in payload.basis.inputs))),
            Field("Targets", _joined(payload.target_paths)),
            Field("Specialists", _joined(payload.specialist_ids)),
            Field("Cost", f"At most {payload.max_preparation_physical_calls} provider calls; "
                          f"{payload.physical_calls_spent} already spent in this source lineage."),
            Notice(payload.user_impact, label="impact"),
        )
    if type(payload) is LeadRecommendationDecisionPayload:
        return (Field("Recommendation decision", payload.decision.kind.value),
                *((_message("Revision", payload.decision.revision_request),)
                  if payload.decision.revision_request is not None else ()))
    if type(payload) is LeadRecommendationStartedPayload:
        return (Field("Preparation", "running"), Field("Attempt", payload.execution_attempt_id))
    if type(payload) is LeadRecommendationResultPayload:
        return (Field("Preparation", payload.state.value), _message("Result", payload.message))
    if type(payload) is CollaborationMessagePayload:
        return (
            _message("Message", payload.content),
            *_reply_trace_items(payload.trace),
        )
    if type(payload) is CollaborationLeadJudgmentPayload:
        return (
            *_authoring_scope_items(payload.authoring_scope),
            Field("Outcome", payload.kind.value),
            _message("Lead", payload.message, role=SemanticRole.AGENT),
            *_evidence_items(payload.evidence),
            *_reply_trace_items(payload.trace),
        )
    if type(payload) is CollaborationProposal:
        return _proposal_items(payload)
    if type(payload) is CollaborationAssignmentDispatchPayload:
        return (
            *_proposal_identity(payload),
            Field("Attempt", payload.execution_attempt_id),
            Section("Assignment", _assignment_items(payload.assignment)),
        )
    if type(payload) is CollaborationSpecialistReplyPayload:
        items: list[Field | Notice | Message] = [
            *_proposal_identity(payload),
            Field("Attempt", payload.execution_attempt_id),
            Field("Assignment", payload.assignment_id),
            Field("Assessment", payload.assessment.value),
            Field("Mode", "legacy" if payload.mode is None else payload.mode.value),
            Field("Inputs read", _joined(payload.input_ids)),
            _message("Specialist reply", payload.message, role=SemanticRole.AGENT),
            *_reply_trace_items(payload.trace),
        ]
        if payload.assessment.value == "conflict":
            items.extend(
                (
                    Field(
                        "Affected sources",
                        _joined(
                            tuple(
                                input_references.get(item, item)
                                for item in payload.input_ids
                            )
                        ),
                    ),
                    Notice(
                        "The Lead must preserve this conflict, synthesize it, and propose the next exact action.",
                        label="next action",
                        role=SemanticRole.WARNING,
                    ),
                )
            )
        return tuple(items)
    if type(payload) is CollaborationLeadSynthesisPayload:
        return (
            *_authoring_scope_items(payload.authoring_scope),
            *_proposal_identity(payload),
            Field("Attempt", payload.execution_attempt_id),
            Field("Replies", _joined(payload.reply_event_ids)),
            Field("Unresolved inputs", _joined(payload.unresolved_input_ids)),
            _message("Lead synthesis", payload.message, role=SemanticRole.AGENT),
            *_evidence_items(payload.evidence),
            *_reply_trace_items(payload.trace),
        )
    if type(payload) is CollaborationStepStatePayload:
        fields: list[Field | Notice] = [
            Field("State", payload.state.value),
            *_proposal_identity(payload),
        ]
        if payload.confirmation_request_id is not None:
            fields.append(Field("Confirmation request", payload.confirmation_request_id))
        if payload.execution_attempt_id is not None:
            fields.append(Field("Attempt", payload.execution_attempt_id))
        if payload.execution_result_event_id is not None:
            fields.append(Field("Result event", payload.execution_result_event_id))
        proposal = proposals.get(payload.proposal_id)
        if payload.state in {
            CollaborationStepState.AWAITING_CONFIRMATION,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
        } and proposal is not None:
            fields.append(_confirmation_summary(proposal))
        fields.append(Notice(_STATE_GUIDANCE[payload.state], label="next action"))
        return tuple(fields)
    if type(payload) is CollaborationConfirmationPayload:
        return (
            Field("Confirmation request", payload.confirmation_request_id),
            Field("Request event", payload.confirmation_request_event_id),
            *_proposal_identity(payload),
            Field("Decision", payload.decision.value),
        )
    if type(payload) is CollaborationExecutionResult:
        outcomes = tuple(
            Record(
                item.assignment_id,
                fields=(
                    Field("Outcome", item.outcome.value),
                    Field("Provider calls", str(item.submitted_physical_calls)),
                    Field("Reply event", item.reply_event_id or "none"),
                ),
            )
            for item in payload.assignment_outcomes
        )
        return (
            *_proposal_identity(payload),
            Field("Attempt", payload.execution_attempt_id),
            Field("Overall outcome", payload.overall_outcome.value),
            ListView("Assignment outcomes", outcomes),
            Field("Synthesis outcome", payload.synthesis_outcome.value),
            Field("Synthesis provider calls", str(payload.synthesis_submitted_physical_calls)),
            Field("Visible exchanges", _joined(payload.visible_exchange_event_ids)),
        )
    if type(payload) is CollaborationInterruptionPayload:
        return (Field("Attempt", payload.execution_attempt_id), _message("Interruption", payload.content))
    if type(payload) is CollaborationTerminalOutcomePayload:
        return (
            Field("Attempt", payload.execution_attempt_id),
            Field("Scope", payload.scope.value),
            Field("Assignment", payload.assignment_id or "none"),
            Field("Outcome", payload.outcome.value),
            Field("Provider calls", str(payload.submitted_physical_calls)),
        )
    if type(payload) is CollaborationProviderCallPayload:
        return (
            Field("Invocation", payload.invocation_id),
            Field("Call", payload.call_id),
            Field("Ordinal", str(payload.ordinal)),
            Field("Role", payload.role),
            Field("Invocation kind", payload.invocation_kind.value),
            Field("Call kind", payload.call_kind.value),
            Field("State", payload.state.value),
        )
    if type(payload) is CollaborationLeadOutcomeFailurePayload:
        return (
            Field("Failure", payload.code.value),
            Field("Invocation", payload.invocation_id),
            Field("Call", payload.call_id),
            Notice("Lead outcome is unavailable. Review evidence and retry safely.", label="next action", role=SemanticRole.ERROR),
        )
    if type(payload) is ArtifactReviewCandidatePayload:
        return (
            Field("Artifact state", "proposed"),
            Field("Candidate", payload.candidate.candidate_id),
            Field("Candidate revision", str(payload.candidate.candidate_revision)),
            Field("Candidate digest", payload.candidate.candidate_digest),
            Field("Mandatory specialists", _joined(payload.mandatory_specialist_ids)),
            Field("Disclosed specialists", _joined(payload.disclosed_specialist_ids)),
            _operation_list(payload.candidate.change_set.operations, "Proposed changes"),
        )
    if type(payload) is ArtifactReviewSynthesisPayload:
        items = [
            Field("Candidate", payload.candidate_id),
            Field("Decision", payload.decision.value),
            Field("Replies", _joined(payload.reply_event_ids)),
            Field("Unresolved inputs", _joined(payload.unresolved_input_ids)),
            _message("Review synthesis", payload.message, role=SemanticRole.AGENT),
            Field("Rationale", payload.rationale),
            Field("User impact", payload.user_impact),
            Field("Limitations", payload.limitations),
        ]
        if payload.decision.value != "propose":
            items.extend(
                (
                    Field(
                        "Affected paths",
                        _joined(candidate_paths.get(payload.candidate_id, ())),
                    ),
                    Notice(
                        "Continue with the Lead to revise or reject this exact candidate before application.",
                        label="next action",
                        role=SemanticRole.WARNING,
                    ),
                )
            )
        return tuple(items)
    if type(payload) is ArtifactReviewedProposal:
        return (
            Field("Artifact state", "reviewed"),
            Field("Reviewed proposal", payload.proposal_id),
            Field("Revision", str(payload.proposal_revision)),
            Field("Digest", payload.proposal_digest),
            Field("Reviewers", _joined(payload.disclosed_specialist_ids)),
            Field("Unresolved inputs", _joined(payload.unresolved_input_ids)),
            Field("Rationale", payload.rationale),
            Field("User impact", payload.user_impact),
            Field("Limitations", payload.limitations),
            Field("Artifact write occurred", _yes_no(payload.artifact_write_occurred)),
            Field("Apply authority granted", _yes_no(payload.apply_authority_granted)),
            Field("Git action occurred", _yes_no(payload.git_action_occurred)),
            _operation_list(payload.candidate.change_set.operations, "Reviewed changes"),
        )
    if type(payload) is ArtifactProposalStatePayload:
        return (
            Field("State", payload.state.value),
            Field("Candidate", payload.candidate_id),
            Field("Candidate revision", str(payload.candidate_revision)),
            Field("Reviewed proposal", payload.reviewed_proposal_id or "none"),
            Notice("Inspect this exact artifact state before the next confirmed action.", label="next action"),
        )
    if type(payload) is ArtifactReviewFailurePayload:
        return (
            Field("Failure", payload.code.value),
            Field("Candidate", payload.candidate_id or "unavailable"),
            Notice("No artifact application was authorized. Review and revise the proposal.", label="next action", role=SemanticRole.ERROR),
        )
    if type(payload) is ArtifactApplicationRequestPayload:
        return (
            Field("Confirmation request", payload.confirmation_request_id),
            Field("Reviewed proposal", payload.reviewed_proposal.proposal_id),
            Field("Reviewed proposal digest", payload.reviewed_proposal.proposal_digest),
            Field("Application request digest", payload.application_request_digest),
            Field("Affected paths", _operation_paths(payload.application_request.change_set.operations)),
            Field("Choices", _values(payload.choices)),
            Field("Side effects", _values(payload.side_effects)),
            Field("Prohibited", _values(payload.prohibited_side_effects)),
            Field("User impact", payload.reviewed_proposal.user_impact),
            _operation_list(payload.application_request.change_set.operations, "Changes awaiting application"),
            Notice("Confirming applies artifacts only; it does not stage or commit Git changes.", label="impact"),
        )
    if type(payload) is ArtifactApplicationDecisionPayload:
        return (
            Field("Confirmation request", payload.confirmation_request_id),
            Field("Reviewed proposal", payload.reviewed_proposal_id),
            Field("Candidate", payload.candidate_id),
            Field("Application", payload.application_id),
            Field("Decision", payload.decision.value),
            Field("Revision request", payload.revision_request or "none"),
        )
    if type(payload) is ArtifactApplicationResultPayload:
        return (
            Field(
                "Artifact state",
                "applied" if payload.outcome.value == "applied" else payload.outcome.value,
            ),
            Field("Application", payload.application_id),
            Field("Outcome", payload.outcome.value),
            Field("Next action", payload.next_action.value),
            Field("Affected paths", _joined(payload.result.affected_paths) if payload.result else "none"),
            Field("Git index", "not staged"),
            Notice("Artifact application is separate from Git staging and commit.", label="boundary"),
        )
    if type(payload) is StoryCommitRequestPayload:
        return (
            Field("Artifact state", "applied"),
            Field("Git index", "not staged; staging is inside the confirmed commit"),
            Field("Confirmation request", payload.confirmation_request_id),
            Field("Candidate", payload.candidate.candidate_id),
            Field("Branch", payload.candidate.branch),
            Field("Paths", _joined(tuple(item.path for item in payload.candidate.paths))),
            Field("Proposed message", payload.candidate.proposed_message),
            Field("User impact", _joined(payload.candidate.impact_boundary)),
            Field("Choices", _values(payload.choices)),
            Field("Side effects", _values(payload.side_effects)),
            Field("Prohibited", _values(payload.prohibited_side_effects)),
            Notice("This confirmation creates one local commit only; paired remote push requires another confirmation.", label="Git boundary"),
        )
    if type(payload) is StoryCommitDecisionPayload:
        return (
            Field("Confirmation request", payload.confirmation_request_id),
            Field("Candidate", payload.candidate_id),
            Field("Decision", payload.decision.value),
            Field("Revision request", payload.revision_request or "none"),
        )
    if type(payload) is StoryCommitResultPayload:
        result = payload.result
        return (
            Field(
                "Artifact state",
                "committed" if payload.outcome.value == "committed" else "applied",
            ),
            Field("Git index", "clean after commit" if payload.outcome.value == "committed" else "not staged"),
            Field("Candidate", payload.candidate_id),
            Field("Outcome", payload.outcome.value),
            Field("Next action", payload.next_action.value),
            Field("Branch", result.branch if result else "none"),
            Field("Local commit", result.commit_id if result and result.commit_id else "none"),
            Notice("No paired remote push is implied by this local commit result.", label="Git boundary"),
        )
    raise TypeError("unsupported collaboration payload")


def _authoring_scope_items(scope: AuthoringScope | None) -> tuple[Field | Notice, ...]:
    if scope is None:
        return ()
    return (
        Field("Activity", scope.activity.value),
        Field("Task", f"{scope.task_id} revision {scope.task_revision}"),
        Field("Targets", _joined(scope.target_paths)),
        Field("Domains", _joined(tuple(item.value for item in scope.domains))),
        Notice(scope.interpretation, label="interpretation"),
    )


def _proposal_items(payload: CollaborationProposal) -> tuple[Section | Field | Notice | ListView, ...]:
    assignments = tuple(
        Record(
            item.assignment_id,
            title=item.specialist_id,
            fields=(
                Field("Round", str(item.round)),
                Field("Mode", "legacy" if item.mode is None else item.mode.value),
                *( () if item.review_basis is None else (
                    Field("Review candidates", _joined(item.review_basis.candidate_input_ids)),
                    Field("Review criteria", item.review_basis.criteria),
                )),
                Field("Inputs", _joined(item.input_ids)),
                Field("Maximum provider calls", str(item.max_physical_calls)),
            ),
            content=item.instruction,
        )
        for item in payload.assignments
    )
    sources = tuple(
        Record(
            item.input_id,
            fields=(
                Field("Kind", item.kind.value),
                Field("Reference", _range_reference(item.locator, item.start_line, item.end_line)),
                Field("State", item.state.value),
                Field("Revision digest", item.revision_digest),
            ),
        )
        for item in payload.basis.inputs
    )
    budget = payload.budget
    return (
        *_authoring_scope_items(payload.authoring_scope),
        Field("Proposal", payload.proposal_id),
        Field("Revision", str(payload.proposal_revision)),
        Field("Digest", payload.proposal_digest),
        Field("Purpose", payload.purpose),
        Field("User impact", payload.user_impact),
        Field("Side effects", _values(payload.side_effects)),
        ListView("Assignments", assignments),
        ListView("Required sources", sources),
        Section(
            "Bounded cost",
            (
                Field("Round", f"{budget.rounds_spent + 1}/{budget.round_limit}"),
                Field("Calls already spent", str(budget.physical_calls_spent)),
                Field("Maximum additional calls", str(budget.max_additional_physical_calls)),
                Field("Physical call limit", str(budget.physical_call_limit)),
            ),
        ),
        Notice("Choose the exact Confirm, Reject, or Revise control. Free-form text does not authorize work.", label="confirmation"),
    )


def _assignment_items(assignment: CollaborationAssignment) -> tuple[Field | Notice, ...]:
    return (
        Field("Assignment", assignment.assignment_id),
        Field("Specialist", assignment.specialist_id),
        Field("Mode", "legacy" if assignment.mode is None else assignment.mode.value),
        *( () if assignment.review_basis is None else (
            Field("Review candidates", _joined(assignment.review_basis.candidate_input_ids)),
            Field("Review criteria", assignment.review_basis.criteria),
        )),
        Field("Round", str(assignment.round)),
        Field("Inputs", _joined(assignment.input_ids)),
        Field("Maximum provider calls", str(assignment.max_physical_calls)),
        Notice(assignment.instruction, label="instruction"),
    )


def _confirmation_summary(proposal: CollaborationProposal) -> Section:
    budget = proposal.budget
    return Section(
        "Confirmation summary",
        (
            Field(
                "Affected agents",
                _joined(tuple(item.specialist_id for item in proposal.assignments)),
            ),
            Field(
                "Affected sources",
                _joined(
                    tuple(
                        _range_reference(item.locator, item.start_line, item.end_line)
                        for item in proposal.basis.inputs
                    )
                ),
            ),
            Field("Bounded round", f"{budget.rounds_spent + 1}/{budget.round_limit}"),
            Field(
                "Bounded provider calls",
                f"{budget.max_additional_physical_calls} additional; {budget.physical_call_limit} total limit",
            ),
            Field("Side effects", _values(proposal.side_effects)),
            Field("User impact", proposal.user_impact),
        ),
    )


def _operation_list(operations: Iterable[object], title: str) -> ListView:
    records = tuple(_operation_record(operation) for operation in operations)
    return ListView(title, records)


def _operation_record(operation: object) -> Record:
    if type(operation) is ArtifactCreateOperation:
        return Record(operation.path, fields=(Field("Operation", "create"), Field("Projected digest", operation.projected_digest), Field("New UTF-8 bytes", str(len(operation.content)))))
    if type(operation) is ArtifactPatchOperation:
        changed = sum(len(item.old_text) + len(item.new_text) for item in operation.hunks)
        return Record(operation.path, fields=(Field("Operation", "patch"), Field("Base digest", operation.base_digest), Field("Projected digest", operation.projected_digest), Field("Hunks", str(len(operation.hunks))), Field("Bounded changed bytes", str(changed))))
    if type(operation) is ArtifactReplaceOperation:
        return Record(operation.path, fields=(Field("Operation", "replace"), Field("Base digest", operation.base_digest), Field("Projected digest", operation.projected_digest), Field("New UTF-8 bytes", str(len(operation.content)))))
    if type(operation) is ArtifactRenameOperation:
        return Record(operation.source_path, fields=(Field("Operation", "rename"), Field("Destination", operation.destination_path), Field("Digest", operation.base_digest)))
    if type(operation) is ArtifactDeleteOperation:
        return Record(operation.path, fields=(Field("Operation", "delete"), Field("Base digest", operation.base_digest)))
    raise TypeError("unsupported artifact change operation")


def _operation_paths(operations: Iterable[object]) -> str:
    return _joined(_operation_path_tuple(operations))


def _operation_path_tuple(operations: Iterable[object]) -> tuple[str, ...]:
    paths: list[str] = []
    for operation in operations:
        if type(operation) is ArtifactRenameOperation:
            paths.extend((operation.source_path, operation.destination_path))
        else:
            path = getattr(operation, "path", None)
            if type(path) is not str:
                raise TypeError("unsupported artifact change operation")
            paths.append(path)
    return tuple(paths)


def _proposal_identity(payload: object) -> tuple[Field, Field, Field]:
    return (
        Field("Proposal", getattr(payload, "proposal_id")),
        Field("Proposal revision", str(getattr(payload, "proposal_revision"))),
        Field("Proposal digest", getattr(payload, "proposal_digest")),
    )


def _evidence_items(evidence: CollaborationLeadEvidence) -> tuple[Field, ...]:
    return (
        Field("Evidence scope", evidence.scope.value),
        Field("Evidence status", evidence.status.value),
        Field("Evidence receipts", str(len(evidence.receipts))),
    )


def _event_state(payload: object) -> ViewState:
    if type(payload) in {ArtifactObjectivePayload, ArtifactObjectivePayloadV2} and payload.action is LeadObjectiveAction.CANCEL:
        return ViewState.CANCELLED
    if isinstance(payload, (CollaborationLeadOutcomeFailurePayload, ArtifactReviewFailurePayload)):
        return ViewState.ERROR
    state = getattr(payload, "state", None)
    if state in {CollaborationStepState.FAILED, CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED}:
        return ViewState.ERROR
    if state is CollaborationStepState.INTERRUPTED or isinstance(payload, CollaborationInterruptionPayload):
        return ViewState.CANCELLED
    if state in {CollaborationStepState.COMPLETED, CollaborationStepState.CONFIRMED}:
        return ViewState.COMPLETED
    return ViewState.OPEN


def _message(
    sender: str,
    content: str,
    *,
    role: SemanticRole = SemanticRole.DEFAULT,
) -> Message:
    return Message(
        sender,
        content,
        role=role,
        presentation=MessagePresentation.CHAT_BLOCK,
    )


def _reply_trace_items(trace: ReplyTrace | None) -> tuple[Section, ...]:
    if trace is None:
        return ()
    return (
        Section(
            "Reply construction",
            (
                Field("Role", trace.role),
                Field("Action", trace.action),
                Field("Context", _joined(trace.context_sources)),
                Field("Recovered fields", _joined(trace.recovered_fields)),
                Field("Origin", trace.origin.value),
            ),
        ),
    )


def _actor(kind: str, agent_id: str | None) -> str:
    return f"{kind}:{agent_id}" if agent_id is not None else kind


def _range_reference(locator: str, start: int | None, end: int | None) -> str:
    base = locator if locator.startswith("${") and locator.endswith("}") else f"${{{locator}}}"
    if start is None:
        return base
    suffix = f"#L{start}" if start == end else f"#L{start}-L{end}"
    return f"{base}{suffix}"


def _values(values: Iterable[StrEnum]) -> str:
    return _joined(tuple(value.value for value in values))


def _joined(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


__all__ = (
    "collaboration_view_context",
    "project_artifact_application_decision_result",
    "project_collaboration_event",
    "project_lead_error_result",
    "project_lead_turn_result",
    "project_specialist_results",
    "project_story_commit_decision_result",
    "validate_collaboration_event_order",
)
