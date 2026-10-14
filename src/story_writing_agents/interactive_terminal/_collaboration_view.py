"""Project validated collaboration events into safe semantic terminal views."""

from __future__ import annotations
from dataclasses import replace
from ..agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST

from ..core_document_readiness import AuthoringTarget, DEPENDENCIES, CoreDocumentReadiness, CoreDocumentReadinessNeeded, project_readiness

from collections.abc import Iterable, Mapping
from enum import StrEnum
from ..editorial_state import EditorialStateProjection
from ..lead_premise_decisions import PremiseDecisionReport, PremiseReportRef, premise_value_to_dict
from ..lead_task_transitions import LeadPublicResponse, task_value_to_dict

from ..conversations import (
    project_collaboration_history,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadDiscussionPayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadExplorationRequestPayload,
    CollaborationLeadExplorationResultPayload,
    CollaborationExplorationAdoptionPayload,
    CollaborationPremiseDecisionPayload,
    CollaborationPremiseSelectionPayload,
    CollaborationPremiseFreshWorkAdmissionPayload,
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
    CollaborationProviderCallState,
    CollaborationInvocationKind,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationTerminalOutcomePayload,
    CollaborationNextAction,
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
    SpeakerAccent,
    ViewState,
)
from ._response_usage import ResponseUsage, response_footer, response_usage


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
    history_events: tuple[CollaborationEvent, ...] = (),
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
        Field("Event", event.event_id),
        Field("Turn", event.turn_id),
        Field("Kind", event.kind.value),
        Field("Actors", f"{event.sender.agent_id or event.sender.kind.value} → {event.recipient.agent_id or event.recipient.kind.value}"),
        Field("From", _actor(event.sender.kind.value, event.sender.agent_id)),
        Field("To", _actor(event.recipient.kind.value, event.recipient.agent_id)),
        Field("Step", event.step_id),
    ]
    items.extend(_payload_items(event.payload, references, paths, proposal_index, session_id=event.session_id))
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
    items = list(_event_response_items(tuple(items), event, history_events))
    return Panel(
        f"Collaboration · {event.kind.value.replace('_', ' ')}",
        tuple(items),
        state=_event_state(event.payload),
    )


def _premise_report_items(report: PremiseDecisionReport, ref: PremiseReportRef | None = None, *, show_message=True) -> tuple:
    items = [Notice("Whole provisional options require explicit USER choice; no candidate or artifact authority.", label="Premise analysis (provisional)"),
        Field("Question", report.spec.question_id), Field("Dependency slot", report.spec.slot_id),
        _message("Unresolved premise", report.spec.question),
        Field("Analysis status", report.summary.status), Field("Report digest", report.report_digest),
        Field("Submitted analysis calls", str(report.cost.physical_calls_spent)),
        Field("Analysis call identities", _joined(report.cost.submitted_call_ids)),
        Field("Analysis rounds", str(report.cost.rounds_spent))]
    if show_message:
        items.append(_message("Exact terminal premise report", report.summary.message))
    if ref is not None:
        items.extend((Field("Source conversation", ref.source_conversation_id), Field("Report event", ref.report_event_id)))
    if report.prior_authoring_cost is not None:
        items.extend((Field("Prior authoring calls", str(report.prior_authoring_cost.physical_calls_spent)),
            Field("Prior authoring call identities", _joined(report.prior_authoring_cost.submitted_call_ids)),
            Field("Prior authoring rounds", str(report.prior_authoring_cost.rounds_spent))))
    if report.prior_analysis_report_ref is not None:
        items.append(Field("Earlier analysis cost receipt", report.prior_analysis_report_ref.report_event_id + " / " + report.prior_analysis_report_ref.report_digest))
    if report.prior_admission_ref is not None:
        items.append(Field("Earlier admitted front cost head", report.prior_admission_ref.event_id + " / " + report.prior_admission_ref.digest))
    items.extend((ListView("Whole options", tuple(Record(option.option_id, title=option.option_key,
        fields=(Field("Material SHA-256", option.material_digest), Field("Rationale", option.rationale),
            Field("Every analyst disposition", "\n".join(
                f"{item.reply_event_id}: {item.disposition}; alternatives: {_joined(item.alternative_keys)}; reason: {item.reason}; residual cost: {item.residual_cost}"
                for item in option.reply_dispositions))), content=option.premise_text) for option in report.options)),
        Field("Provisional recommended option", report.recommended_option_id or "none"),
        Notice("Costs remain on distinct lineages. Earlier report/admission receipts and other failed fronts remain in history; fresh authoring needs new Confirm. Use /premise to choose, reject or defer.", label="next action")))
    return tuple(items)


def project_editorial_state(state: EditorialStateProjection) -> Panel:
    """Display selected direction separately from observed artifact authority."""
    items = []
    for record in state.current_records:
        scoped = state.scope_state(record.ref)
        coverage = scoped.coverage
        target = "; ".join(
            item.path if item.start_line is None else
            f"{item.path}:{item.start_line}-{item.end_line} @ {item.source_sha256}"
            for item in coverage.scope.targets
        ) or "unknown: " + (coverage.scope.clarification or "clarification required")
        items.extend((
            Field(f"Editorial {record.record_id} | Revision {record.revision}",
                  f"{state.effective_approval(record.ref).value}: {record.draft.text}"),
            Field("Target coverage", target),
            Field("Remaining intent", coverage.status),
        ))
        if coverage.exclusions:
            items.append(Field("Excluded coverage", "; ".join(
                f"{item.path}:{item.start_line}-{item.end_line} @ {item.source_sha256}"
                for item in coverage.exclusions)))
        if scoped.explicit_conflicts:
            items.append(Field("Explicit USER conflicts", str(len(scoped.explicit_conflicts))))
        if scoped.unclassified_overlaps:
            items.append(Field("Geometric overlaps (unclassified)",
                               ", ".join(ref.record_id for ref in scoped.unclassified_overlaps)))
        attempts = tuple(item for item in state.application_attempts
                         if item.record_ref.record_id == record.record_id)
        for attempt in attempts:
            items.append(Field(f"Historical Apply | Editorial revision {attempt.record_ref.revision}",
                               f"{attempt.phase}: {attempt.application_id}; "
                               + ("coverage verified" if attempt.verified_coverage is not None
                                  else "coverage unverified")))
    items.append(Field("Current manuscript", "not established by editorial selection or historical Apply; current source qualification remains required"))
    return Panel("Editorial direction", tuple(items))


def project_core_document_readiness(readiness: CoreDocumentReadiness | CoreDocumentReadinessNeeded) -> Panel:
    """Expose semantic status/currentness without review or execution authority."""
    items = []
    if type(readiness) is CoreDocumentReadinessNeeded:
        items.append(Notice(readiness.message, label="needed information"))
    elif type(readiness) is CoreDocumentReadiness:
        for path, reasons in readiness.remaining:
            items.append(Field(path, "Current whole-book criteria satisfied" if not reasons else "; ".join(reasons)))
        available = tuple(target.value for target in AuthoringTarget
                          if all(dependency.value in readiness.complete_documents for dependency in DEPENDENCIES[target]))
        items.append(Field("Prerequisites permit targeted work", "; ".join(available)))
        items.append(Notice("Actual supplied grounds and separate authoring admission remain required; initial outline construction and grounded analysis may proceed while extent is clarified.", label="next work"))
    else:
        raise TypeError("readiness view must be typed")
    items.append(Notice("Document completion does not grant final review, Confirm, Apply or Commit authority.", label="authority"))
    return Panel("Whole-book document readiness", tuple(items))


def project_lead_turn_result(
    result: LeadTurnResult, *, public_response_events: tuple[CollaborationEvent, ...] = (),
    elapsed: str | None = None, show_details: bool = False,
) -> Panel:
    """Project one runtime-validated visible lead result without side effects."""

    if type(result) is not LeadTurnResult:
        raise TypeError("result must be a LeadTurnResult")
    role = (
        SemanticRole.ERROR
        if result.next_action is CollaborationNextAction.RETRY_LEAD_TURN
        else SemanticRole.AGENT
    )
    items: list[Section | Field | Notice | Message | Record | ListView] = [
        _message("Lead (provisional)" if result.exploration is not None else "Lead", result.message, role=role),
        *(_reply_trace_items(result.trace) if show_details else ()),
    ]
    items.extend(
        Notice(line, label="validation", role=SemanticRole.ERROR)
        for detail in result.validation_details
        for line in detail.split("\n")
    )
    if result.discussion is not None:
        items.append(Notice("Read-only discussion; writing task and pending controls stay separately bound."))
        if result.discussion.associated_task_ref is not None:
            ref = result.discussion.associated_task_ref
            items.append(Field("Associated task", f"{ref.task_id} @ {ref.task_revision}"))
    latest = result.public_responses[-1] if result.public_responses else None
    for public in result.public_responses:
        shown_above = (public is latest and public.response.acceptance.value == "accepted"
                       and public.response.response_text == result.message)
        items.extend(_public_response_items(public.response, show_text=not shown_above, usage=_public_usage(public_response_events, public.response), show_details=show_details))
    if result.exploration is not None:
        items.extend(_exploration_items(result.exploration))
    if result.premise_report is not None:
        items.extend(_premise_report_items(result.premise_report, result.premise_report_ref,
            show_message=not (result.message == result.premise_report.summary.message
                or result.message.startswith(result.premise_report.summary.message + "\n\nRuntime cost receipt:"))))
    if result.proposal is not None:
        items.extend(_proposal_items(result.proposal, session_id=result.session_id))
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
        items.extend(_payload_items(result.recommendation_request, {}, {}, {}, session_id=result.session_id))
    items.extend(_public_response_omission_items(result, public_response_events))
    return Panel("Lead", _response_items(tuple(items), result.message,
                 response_footer(result.kind.value, _live_usage(result, public_response_events), elapsed=elapsed),
                 SpeakerAccent.CYAN), state=_event_state(result))



def _public_response_omission_items(
    result: LeadTurnResult, events: tuple[CollaborationEvent, ...],
) -> tuple[Notice, ...]:
    """Render persisted omissions only after full replay and exact call matching."""
    if not events:
        return ()
    project_collaboration_history(events)
    current = tuple(event for event in events
                    if (event.session_id, event.conversation_id, event.turn_id, event.step_id) == (
                        result.session_id, result.conversation_id, result.turn_id, result.step_id))
    submitted = tuple(event for event in current
                      if type(event.payload) is CollaborationProviderCallPayload
                      and event.payload.state.value == "submitted"
                      and event.payload.invocation_kind.value in {"lead", "lead_discussion"})
    available = {(event.payload.response.invocation_id, event.payload.response.call_id)
                 for event in current
                 if type(event.payload) is CollaborationLeadPublicResponsePayload}
    items = []
    for notice in current:
        payload = notice.payload
        if (type(payload) is not CollaborationMessagePayload
                or notice.sender.kind.value != "runtime" or notice.recipient.kind.value != "user"
                or payload.trace is not None or payload.validation_details
                or payload.validation_invocation_id is not None
                or payload.validation_call_id is not None):
            continue
        for source in submitted:
            call = source.payload
            identity = (call.invocation_id, call.call_id)
            if source.sequence >= notice.sequence or identity in available:
                continue
            for disposition in ("accepted", "rejected"):
                expected = (
                    f"Public response unavailable for {disposition} call {call.call_id}: "
                    "no bounded literal final response_text was recoverable."
                )
                if payload.content != expected:
                    continue
                diagnostics = tuple(event for event in current
                                    if type(event.payload) is CollaborationMessagePayload
                                    and event.sender.kind.value == "runtime"
                                    and event.recipient.kind.value == "user"
                                    and event.sequence < notice.sequence
                                    and event.payload.validation_details
                                    and (event.payload.validation_invocation_id,
                                         event.payload.validation_call_id) == identity)
                if disposition == "rejected" and not diagnostics:
                    continue
                items.append(Notice(
                    payload.content,
                    label=(f"Public response unavailable — call {call.ordinal} "
                           f"({call.call_kind.value}, {disposition})"),
                    role=SemanticRole.ERROR,
                ))
    return tuple(items)


def _public_response_items(
    response: LeadPublicResponse, *, show_text: bool = True,
    usage: ResponseUsage | None = None, show_details: bool = True,
) -> tuple[Field | Message | Notice, ...]:
    items = [Field("Public response disposition", response.acceptance.value),
             Field("Provider call", f"{response.call_kind} #{response.ordinal} ({response.call_id})"),
             (_message("Literal final response_text", response.response_text, metadata=response_footer(response.acceptance.value, usage or ResponseUsage())) if show_text else
              Notice("This call's accepted text is shown in the Lead response above."))]
    if response.repair_of_call_id is not None:
        items.append(Field("Repairs call", response.repair_of_call_id))
    if response.validation_details:
        items.append(Notice("\n".join(response.validation_details),
                            label="Call validation", role=SemanticRole.ERROR))
    items.append(Notice("Selectable literal input; no draft, verified evidence or write authority."))
    if usage is not None and not show_text:
        items.append(Notice(response_footer(response.acceptance.value, usage), label="Response metadata"))
    return tuple(items)


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
    *, show_details: bool = False,
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
                    *(_reply_trace_items(reply.trace) if show_details else ()),
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
                (_event_response_items(tuple(result_items), reply_event, events)
                 if reply_event is not None else tuple(result_items)),
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


def _exploration_items(report: object) -> tuple[Section | Field | Notice | Message | Record | ListView, ...]:
    return (
        Notice("Ideas and top message are provisional. Adoption starts fresh author drafting; no execution or writing is authorized.", label="Exploration (provisional)", role=SemanticRole.INFO),
        *(Section(f"Provisional idea {index + 1}", (Field("Kind", idea.kind.value), _message("Provisional material", idea.material_text), Field("Idea identity", idea.idea_id), Field("Material SHA-256", idea.material_digest))) for index, idea in enumerate(report.ideas)),
        *(Section("Grounded artifact READ observation", (_message("Observation", observation.text), *(Record("Qualified READ", fields=(Field("Evidence", receipt.evidence_id), Field("Source", receipt.source_id), Field("Locator", receipt.locator), Field("Revision", receipt.revision), Field("Source digest", receipt.source_digest), Field("Boundary digest", receipt.boundary_digest), Field("Range", "whole input" if receipt.start_line is None else f"{receipt.start_line}-{receipt.end_line}"))) for receipt in observation.receipts))) for observation in report.grounded_observations),
    )


def _payload_items(
    payload: object,
    input_references: Mapping[str, str],
    candidate_paths: Mapping[str, tuple[str, ...]],
    proposals: Mapping[str, CollaborationProposal],
    *,
    session_id: str | None = None,
) -> tuple[Section | Field | Notice | Message | Record | ListView, ...]:
    if type(payload) is CollaborationLeadPublicResponsePayload:
        return _public_response_items(payload.response)
    if type(payload) is CollaborationLeadDiscussionPayload:
        report = payload.report
        return (Field("Discussion", report.response_kind),
                _message("Lead discussion", report.response_text),
                Notice("Provisional discussion input; no authoring or artifact authority."))
    if type(payload) is CollaborationLeadTaskDecisionPayload:
        decision = payload.decision
        items = [Field("User task action", decision.action.value)]
        if decision.task_ref is not None:
            ref = decision.task_ref
            items.append(Field("Writing task", f"{ref.task_id} @ {ref.task_revision}"))
        if decision.text is not None:
            items.append(_message("User request", decision.text, role=SemanticRole.DEFAULT))
        return tuple(items)
    if type(payload) is CollaborationLeadTaskTransitionPayload:
        receipt = payload.receipt
        items = [Field("Recorded task action", receipt.action.value)]
        if receipt.target_task_id is not None:
            items.append(Field("Fresh proposal task",
                               f"{receipt.target_task_id} @ {receipt.target_task_revision}"))
        items.append(Notice("Task control alone does not confirm or execute a proposal."))
        return tuple(items)
    if type(payload) is CollaborationLeadMaterialCapturePayload:
        capture = payload.capture
        source = capture.source
        return (Field("Selected whole input", source.kind.value),
                Field("Source session", source.session_id),
                Field("Source conversation", source.conversation_id),
                Field("Source event", source.event_id),
                Field("Material SHA256", source.material_digest),
                _message("Selected material", capture.selected_text),
                Notice("Literal input only; current owner drafting and review require fresh confirmation."))
    if type(payload) is CollaborationPremiseDecisionPayload:
        choice = payload.selection
        return (Field("USER premise decision", choice.action.value), Field("Request", choice.request_id),
            Field("Source report", choice.report_event_id), Field("Report digest", choice.report_digest),
            Field("Question", choice.question_id), Field("Choice authority", "none" if choice.choice_policy is None else choice.choice_policy.value),
            Field("Whole option", choice.option_id or "none"), Field("Material SHA-256", choice.material_digest or "none"),
            Field("Allowed whole options", "\n".join(f"{item.option_id}: {item.material_digest}" for item in choice.allowed_choices) or "none"),
            Field("Target paths", "\n".join(choice.target_paths) or "targetless authoring"),
            Notice("This USER choice is separate from proposal Confirm, candidate review, Apply and Commit.", label="authority"))
    if type(payload) is CollaborationPremiseSelectionPayload:
        capture = payload.capture
        return (Field("Whole premise option", capture.option_id), Field("Material SHA-256", capture.material_digest),
            Field("Capture digest", capture.capture_digest), _message("Exact USER-selected premise", capture.selected_text),
            Field("Fresh task", f"{capture.authoring_task_id}@{capture.authoring_task_revision}"),
            Field("Dependency slot", capture.slot_id), Field("Affected domains", _values(capture.dependent_domains)),
            Field("Original analysis calls", str(capture.cost_link.analysis.physical_calls_spent)),
            Notice("A fresh proposal and Confirm remain required; no old drafting proof or budget is reused.", label="authority"))
    if type(payload) is CollaborationPremiseFreshWorkAdmissionPayload:
        admission = payload.admission
        return (Field("Fresh-work front cap", str(admission.front_physical_call_limit)),
            Field("Exact USER origin", admission.source_user_event_id), Field("Invocation", admission.provider_invocation_id),
            Field("Prior admission cost head", "none" if admission.prior_admission_ref is None else admission.prior_admission_ref.event_id),
            Field("Prior analysis cost head", "none" if admission.prior_analysis_report_ref is None else admission.prior_analysis_report_ref.report_event_id),
            Notice("Original, READ, recovery and correction submissions share this cap. The receipt grants no execution authority and refunds no prior calls.", label="costs"))
    if type(payload) is CollaborationLeadExplorationRequestPayload:
        return (Field("Interaction mode", "exploration"), Notice("Explicit user-selected one-turn exploration.", label="Exploration (provisional)"))
    if type(payload) is CollaborationLeadExplorationResultPayload:
        return (_message("Provisional Lead message", payload.report.message), *_exploration_items(payload.report))
    if type(payload) is CollaborationExplorationAdoptionPayload:
        capture = payload.capture
        return (Field("Selected whole idea", capture.selection.idea_id), Field("Source conversation", capture.selection.source_conversation_id), Field("Source report", capture.selection.result_event_id), Field("Material SHA-256", capture.selection.material_digest), _message("Selected provisional material", capture.selected_text), Field("Target paths", "\n".join(capture.selection.target_paths)), Field("Fresh task", f"{capture.fresh_task_id}@1"), Notice("Input to fresh owner drafting; Confirm, full candidate review, Apply and Commit remain separate.", label="Adoption"))
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
            *_shared_premise_items(payload, session_id=session_id),
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
        if payload.validation_details:
            return (
                *((_message("Message", payload.content),)
                  if (len(payload.validation_details) != 1
                      or payload.content != payload.validation_details[0]) else ()),
                *(
                    Notice(line, label="validation", role=SemanticRole.ERROR)
                    for detail in payload.validation_details
                    for line in detail.split("\n")
                ),
                *_reply_trace_items(payload.trace),
            )
        return (
            _message("Message", payload.content),
            *_reply_trace_items(payload.trace),
        )
    if type(payload) is CollaborationLeadJudgmentPayload:
        return (
            *((Section("Captured whole-book judgment", project_core_document_readiness(
                project_readiness(payload.core_document_readiness, payload.core_document_readiness.sources)).items),)
              if payload.core_document_readiness is not None else ()),
            *_authoring_scope_items(payload.authoring_scope),
            Field("Outcome", payload.kind.value),
            _message("Lead", payload.message, role=SemanticRole.AGENT),
            *_evidence_items(payload.evidence),
            *_reply_trace_items(payload.trace),
        )
    if type(payload) is CollaborationProposal:
        return _proposal_items(payload, session_id=session_id)
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
        if payload.premise_analysis is not None:
            items.extend((Notice("Candidate-free provisional analysis; not a draft or approval.", label="authority"),
                ListView("Analyst alternatives", tuple(Record(item.alternative_key,
                    fields=(Field("Rationale", item.rationale), Field("Tradeoffs", "\n".join(item.tradeoffs)),
                            Field("Basis inputs", _joined(item.basis_input_ids))), content=item.premise_text)
                    for item in payload.premise_analysis.alternatives))))
        if payload.premise_conflicts:
            items.extend(Notice(f"{item.slot_id} / {item.capture_digest}: {item.reason}",
                label="Premise conflict — drafting proof held", role=SemanticRole.WARNING) for item in payload.premise_conflicts)
        return tuple(items)
    if type(payload) is CollaborationLeadSynthesisPayload:
        return (
            *(() if payload.premise_report is None else _premise_report_items(payload.premise_report)),
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
            fields.append(_confirmation_summary(proposal, session_id=session_id))
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
            *(
                Notice(line, label="validation", role=SemanticRole.ERROR)
                for detail in payload.validation_details
                for line in detail.split("\n")
            ),
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
        *(
            (
                Field("Contributions", _joined(tuple(
                    f"{item.domain.value}: {item.role.value}"
                    for item in scope.contributions
                ))),
                Field("Dependency slots", _joined(tuple(
                    f"{item.slot_id}: {_joined(tuple(domain.value for domain in item.dependent_domains))}"
                    for item in scope.dependency_slots
                ))),
            )
            if scope.schema_version == 3 else ()
        ),
        Notice(scope.interpretation, label="interpretation"),
    )


def _shared_premise_items(
    payload: CollaborationProposal | LeadRecommendationRequestPayload,
    *,
    session_id: str | None = None,
) -> tuple[ListView, ...]:
    """Disclose captured choices and source facts without acquiring material."""
    scope = payload.authoring_scope
    if scope is None or scope.schema_version != 3 or payload.basis.schema_version != 2:
        return ()
    slots = {item.slot_id: item for item in scope.dependency_slots}
    return tuple(
        ListView(
            f"Shared premise · {premise.slot_id}",
            (
                Record(
                    premise.slot_id,
                    title="Selected premise",
                    fields=(
                        Field("Slot ID", premise.slot_id),
                        Field("Dependent domains", _joined(tuple(
                            item.value for item in slots[premise.slot_id].dependent_domains
                        ))),
                        *((Field("Presentation session", session_id),) if session_id is not None else ()),
                    ),
                    content=premise.selected_premise,
                ),
                *(
                    Record(
                        source.input_revision.input_id,
                        title="Captured source",
                        fields=(
                            Field("Input ID", source.input_revision.input_id),
                            Field("Kind", source.input_revision.kind.value),
                            Field("Source conversation", source.source_conversation_id or "not applicable"),
                            Field("Locator", source.input_revision.locator),
                            Field(
                                "Input range",
                                "whole input" if source.input_revision.start_line is None
                                else f"lines {source.input_revision.start_line}–{source.input_revision.end_line}",
                            ),
                            Field("State", source.input_revision.state.value),
                            Field("Revision digest", source.input_revision.revision_digest),
                        ),
                    )
                    for source in premise.sources
                ),
            ),
        )
        for premise in payload.basis.shared_premises
    )


def _proposal_items(
    payload: CollaborationProposal, *, session_id: str | None = None,
) -> tuple[Section | Field | Notice | ListView, ...]:
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
        *(() if payload.premise_decision is None else (
            Notice("One terminal candidate-free premise analysis round; no drafting or automatic continuation.", label="Provisional premise analysis"),
            Field("Question identity", payload.premise_decision.question_id),
            Field("Dependency slot", payload.premise_decision.slot_id),
            Field("Affected domains", _values(payload.premise_decision.dependent_domains)),
            _message("Unresolved premise", payload.premise_decision.question),
        )),
        *_authoring_scope_items(payload.authoring_scope),
        *_shared_premise_items(payload, session_id=session_id),
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


def _confirmation_summary(
    proposal: CollaborationProposal, *, session_id: str | None = None,
) -> Section:
    budget = proposal.budget
    return Section(
        "Confirmation summary",
        (
            *(
                _authoring_scope_items(proposal.authoring_scope)
                if proposal.authoring_scope is not None
                and proposal.authoring_scope.schema_version == 3 else ()
            ),
            *_shared_premise_items(proposal, session_id=session_id),
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
    if getattr(payload, "next_action", None) is CollaborationNextAction.RETRY_LEAD_TURN:
        return ViewState.ERROR
    if state is CollaborationStepState.INTERRUPTED or isinstance(payload, CollaborationInterruptionPayload):
        return ViewState.CANCELLED
    if state in {CollaborationStepState.COMPLETED, CollaborationStepState.CONFIRMED}:
        return ViewState.COMPLETED
    return ViewState.OPEN


def _response_items(items: tuple, content: str, footer: str | None, accent: SpeakerAccent) -> tuple:
    projected = []
    for item in items:
        if type(item) is Message:
            item = replace(item, accent=accent,
                           metadata=footer if footer is not None and item.content == content else item.metadata)
        elif type(item) is Section:
            item = replace(item, items=_response_items(item.items, content, footer, accent))
        projected.append(item)
    return tuple(projected)


def _speaker_accent(role: str | None) -> SpeakerAccent:
    return {LEAD_ORCHESTRATOR: SpeakerAccent.CYAN, PLOT_ARCHITECT: SpeakerAccent.YELLOW,
            CHARACTER_CUSTODIAN: SpeakerAccent.GREEN, EXECUTION_SPECIALIST: SpeakerAccent.BLUE}.get(
                role, SpeakerAccent.MAGENTA if role is None else SpeakerAccent.CYAN)


def _live_usage(result: LeadTurnResult, events: tuple[CollaborationEvent, ...]) -> ResponseUsage:
    if result.kind.value == "discussion":
        kinds = frozenset({CollaborationInvocationKind.LEAD_DISCUSSION})
    elif result.kind.value == "exploration":
        kinds = frozenset({CollaborationInvocationKind.LEAD_EXPLORATION})
    elif result.trace is not None and result.trace.origin.value == "runtime_fallback":
        kinds = frozenset({CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_SYNTHESIS,
                           CollaborationInvocationKind.LEAD_EXPLORATION, CollaborationInvocationKind.LEAD_DISCUSSION})
    else:
        kinds = frozenset({CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_SYNTHESIS})
    return response_usage(events, session_id=result.session_id, conversation_id=result.conversation_id,
                          turn_id=result.turn_id, step_id=result.step_id,
                          invocation_id=result.provider_invocation_id, role=LEAD_ORCHESTRATOR,
                          invocation_kinds=kinds)


def _public_usage(events: tuple[CollaborationEvent, ...], response: LeadPublicResponse) -> ResponseUsage:
    matches = tuple(event for event in events if type(event.payload) is CollaborationLeadPublicResponsePayload
                    and event.payload.response == response)
    if len(matches) != 1:
        return ResponseUsage()
    event = matches[0]
    events = tuple(fact for fact in events if fact.sequence <= event.sequence)
    return response_usage(events, session_id=event.session_id, conversation_id=event.conversation_id,
                          turn_id=event.turn_id, step_id=event.step_id,
                          invocation_id=response.invocation_id, role=LEAD_ORCHESTRATOR,
                          invocation_kinds=frozenset({CollaborationInvocationKind.LEAD,
                                                    CollaborationInvocationKind.LEAD_DISCUSSION}),
                          call_id=response.call_id)


def _event_response_items(items: tuple, event: CollaborationEvent, events: tuple[CollaborationEvent, ...]) -> tuple:
    events = tuple(fact for fact in events if fact.sequence <= event.sequence)
    payload = event.payload
    role = event.sender.agent_id
    accent = _speaker_accent(role)
    content = ""
    outcome = ""
    invocation_id = getattr(payload, "provider_invocation_id", None)
    kinds = frozenset({CollaborationInvocationKind.LEAD})
    call_id = None
    if type(payload) is CollaborationLeadJudgmentPayload:
        content, outcome, role = payload.message, payload.kind.value, LEAD_ORCHESTRATOR
    elif type(payload) is CollaborationSpecialistReplyPayload:
        content, outcome = payload.message, payload.assessment.value
        kinds = frozenset({CollaborationInvocationKind.SPECIALIST_ASSIGNMENT})
    elif type(payload) is CollaborationLeadSynthesisPayload:
        content, outcome, role = payload.message, "synthesis", LEAD_ORCHESTRATOR
        kinds = frozenset({CollaborationInvocationKind.LEAD_SYNTHESIS})
    elif type(payload) is ArtifactReviewSynthesisPayload:
        content, outcome, role = payload.message, payload.decision.value, LEAD_ORCHESTRATOR
        kinds = frozenset({CollaborationInvocationKind.LEAD_SYNTHESIS})
    elif type(payload) is CollaborationLeadDiscussionPayload:
        content, outcome, role = payload.report.response_text, payload.report.response_kind, LEAD_ORCHESTRATOR
        kinds = frozenset({CollaborationInvocationKind.LEAD_DISCUSSION})
    elif type(payload) is CollaborationLeadExplorationResultPayload:
        content, outcome, role = payload.report.message, "exploration", LEAD_ORCHESTRATOR
        kinds = frozenset({CollaborationInvocationKind.LEAD_EXPLORATION})
        matched = tuple(fact.payload.invocation_id for fact in events
                        if type(fact.payload) is CollaborationProviderCallPayload
                        and fact.payload.call_id == payload.report.provider_call_id
                        and (fact.session_id, fact.conversation_id, fact.turn_id, fact.step_id)
                        == (event.session_id, event.conversation_id, event.turn_id, event.step_id))
        invocation_id = next(iter(set(matched))) if len(set(matched)) == 1 else None
    elif type(payload) is CollaborationLeadPublicResponsePayload:
        response = payload.response
        content, outcome, role = response.response_text, response.acceptance.value, LEAD_ORCHESTRATOR
        invocation_id, call_id = response.invocation_id, response.call_id
        kinds = frozenset({CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_DISCUSSION})
    elif type(payload) is CollaborationMessagePayload and (role == LEAD_ORCHESTRATOR or invocation_id is not None):
        content, outcome = payload.content, "response"
        role = payload.trace.role if payload.trace is not None else role or LEAD_ORCHESTRATOR
        kinds = frozenset({CollaborationInvocationKind.LEAD, CollaborationInvocationKind.LEAD_SYNTHESIS,
                           CollaborationInvocationKind.LEAD_EXPLORATION, CollaborationInvocationKind.LEAD_DISCUSSION})
    else:
        return _response_items(items, content, None, accent)
    usage = response_usage(events, session_id=event.session_id, conversation_id=event.conversation_id,
                           turn_id=event.turn_id, step_id=event.step_id,
                           invocation_id=invocation_id, role=role or LEAD_ORCHESTRATOR,
                           invocation_kinds=kinds, call_id=call_id)
    return _response_items(items, content, response_footer(outcome, usage), _speaker_accent(role))


def _message(
    sender: str,
    content: str,
    *,
    role: SemanticRole = SemanticRole.DEFAULT,
    metadata: str | None = None,
    accent: SpeakerAccent = SpeakerAccent.CYAN,
) -> Message:
    return Message(
        sender,
        content,
        role=role,
        presentation=MessagePresentation.CHAT_BLOCK,
        metadata=metadata,
        accent=accent,
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
