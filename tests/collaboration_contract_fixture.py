"""Deterministic builders for schema-v2 collaboration contract tests."""

from __future__ import annotations

from dataclasses import replace

from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcome,
    CollaborationAssignmentOutcomeKind,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationSideEffect,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    collaboration_batch_digest,
    validate_collaboration_candidate_batch,
)


SESSION_ID = "session_a"
CONVERSATION_ID = "conversation_a"
TURN_ID = "turn_a"
STEP_ID = "step_a"
PROPOSAL_ID = "proposal_a"
REQUEST_ID = "request_a"
REQUEST_EVENT_ID = "request_event"
ATTEMPT_ID = "attempt_a"
DIGEST_A = "sha256:" + "1" * 64
DIGEST_B = "sha256:" + "2" * 64

COLLABORATION_EVENT_EXPORTS = (
    "COLLABORATION_EVENT_SCHEMA_VERSION",
    "COLLABORATION_PHYSICAL_CALL_LIMIT",
    "COLLABORATION_ROUND_LIMIT",
    "COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CollaborationActor",
    "CollaborationActorKind",
    "CollaborationAgentProfile",
    "CollaborationAppendError",
    "CollaborationAppendFailure",
    "CollaborationAppendReceipt",
    "CollaborationAppendStatus",
    "CollaborationAssignment",
    "CollaborationAssignmentDispatchPayload",
    "CollaborationAssignmentOutcome",
    "CollaborationAssignmentOutcomeKind",
    "CollaborationBudget",
    "CollaborationConfirmationDecision",
    "CollaborationConfirmationPayload",
    "CollaborationDirection",
    "CollaborationEvent",
    "CollaborationEventBatch",
    "CollaborationEventDraft",
    "CollaborationEventKind",
    "CollaborationExecutionOutcomeKind",
    "CollaborationExecutionResult",
    "CollaborationInputKind",
    "CollaborationInputRevision",
    "CollaborationInputState",
    "CollaborationInterruptionPayload",
    "CollaborationMessagePayload",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
    "CollaborationStepKind",
    "CollaborationStepState",
    "CollaborationStepStatePayload",
    "CollaborationSynthesisOutcomeKind",
    "CollaborationTerminalOutcomePayload",
    "CollaborationTerminalScope",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "collaboration_batch_digest",
    "collaboration_proposal_digest",
    "decode_collaboration_batch",
    "decode_collaboration_event",
    "decode_collaboration_event_draft",
    "decode_collaboration_proposal",
    "encode_collaboration_batch",
    "encode_collaboration_event",
    "encode_collaboration_event_draft",
    "encode_collaboration_proposal",
)

COLLABORATION_CONTEXT_EXPORTS = (
    "CONVERSATION_CONTEXT_POLICY",
    "CollaborationEventStore",
    "CollaborationHistoryProjection",
    "CollaborationNextAction",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEventStore",
    "project_collaboration_history",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
)

CONVERSATION_FACADE_EXPORTS = (
    "COLLABORATION_EVENT_SCHEMA_VERSION",
    "COLLABORATION_PHYSICAL_CALL_LIMIT",
    "COLLABORATION_ROUND_LIMIT",
    "COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES",
    "CONVERSATION_CONTEXT_POLICY",
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "CONVERSATION_METADATA_SCHEMA_VERSION",
    "CONVERSATION_SUBJECT_NORMALIZATION_VERSION",
    "MAX_CONVERSATION_SUBJECT_LENGTH",
    "CollaborationActor",
    "CollaborationActorKind",
    "CollaborationAgentProfile",
    "CollaborationAppendError",
    "CollaborationAppendFailure",
    "CollaborationAppendReceipt",
    "CollaborationAppendStatus",
    "CollaborationAssignment",
    "CollaborationAssignmentDispatchPayload",
    "CollaborationAssignmentOutcome",
    "CollaborationAssignmentOutcomeKind",
    "CollaborationBudget",
    "CollaborationConfirmationDecision",
    "CollaborationConfirmationPayload",
    "CollaborationDirection",
    "CollaborationEvent",
    "CollaborationEventBatch",
    "CollaborationEventDraft",
    "CollaborationEventKind",
    "CollaborationEventStore",
    "CollaborationExecutionOutcomeKind",
    "CollaborationExecutionResult",
    "CollaborationHistoryProjection",
    "CollaborationInputKind",
    "CollaborationInputRevision",
    "CollaborationInputState",
    "CollaborationInterruptionPayload",
    "CollaborationMessagePayload",
    "CollaborationNextAction",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
    "CollaborationStepKind",
    "CollaborationStepState",
    "CollaborationStepStatePayload",
    "CollaborationSynthesisOutcomeKind",
    "CollaborationTerminalOutcomePayload",
    "CollaborationTerminalScope",
    "ConversationCatalog",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
    "ConversationEventStore",
    "ConversationMetadata",
    "ConversationStatus",
    "collaboration_batch_digest",
    "collaboration_proposal_digest",
    "decode_collaboration_batch",
    "decode_collaboration_event",
    "decode_collaboration_event_draft",
    "decode_collaboration_proposal",
    "encode_collaboration_batch",
    "encode_collaboration_event",
    "encode_collaboration_event_draft",
    "encode_collaboration_proposal",
    "normalize_conversation_subject",
    "project_collaboration_history",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
)

USER = CollaborationActor(CollaborationActorKind.USER)
LEAD = CollaborationActor(CollaborationActorKind.LEAD, LEAD_ORCHESTRATOR)
RUNTIME = CollaborationActor(CollaborationActorKind.RUNTIME)
ARTIFACT = CollaborationActor(CollaborationActorKind.ARTIFACT)
GIT = CollaborationActor(CollaborationActorKind.GIT)
PLOT = CollaborationActor(CollaborationActorKind.SPECIALIST, PLOT_ARCHITECT)
CHARACTER = CollaborationActor(
    CollaborationActorKind.SPECIALIST,
    CHARACTER_CUSTODIAN,
)


def make_input(
    input_id: str = "input_outline",
    *,
    kind: CollaborationInputKind = CollaborationInputKind.CORE_DOCUMENT,
    locator: str = "${core_documents/outline.md}",
    state: CollaborationInputState = CollaborationInputState.PRESENT,
    revision_digest: str = DIGEST_A,
    start_line: int | None = 3,
    end_line: int | None = 12,
) -> CollaborationInputRevision:
    """Return one content-free input revision."""

    return CollaborationInputRevision(
        input_id=input_id,
        kind=kind,
        locator=locator,
        state=state,
        revision_digest=revision_digest,
        start_line=start_line,
        end_line=end_line,
    )


def make_assignment(
    assignment_id: str = "assignment_plot",
    *,
    specialist_id: str = PLOT_ARCHITECT,
    round_number: int = 1,
    input_ids: tuple[str, ...] = ("input_outline",),
    max_physical_calls: int = 2,
    instruction: str = "Review the exact referenced outline range.",
) -> CollaborationAssignment:
    """Return one visible specialist assignment."""

    return CollaborationAssignment(
        assignment_id=assignment_id,
        specialist_id=specialist_id,
        round=round_number,
        instruction=instruction,
        input_ids=input_ids,
        max_physical_calls=max_physical_calls,
    )


def make_proposal(
    *,
    proposal_id: str = PROPOSAL_ID,
    proposal_revision: int = 1,
    step_kind: CollaborationStepKind = (
        CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION
    ),
    assignments: tuple[CollaborationAssignment, ...] | None = None,
    inputs: tuple[CollaborationInputRevision, ...] | None = None,
    rounds_spent: int = 0,
    physical_calls_spent: int = 0,
    synthesis_max_physical_calls: int = 1,
    profile_ids: tuple[str, ...] | None = None,
    purpose: str = "Consult specialists before recommending the next action.",
    user_impact: str = "Read-only provider work; no artifact or Git mutation.",
    side_effects: tuple[CollaborationSideEffect, ...] = (
        CollaborationSideEffect.PROVIDER_INVOCATION,
    ),
    route: str = "lead_specialist_parallel",
    route_fingerprint: str = DIGEST_A,
    context_policy_fingerprint: str = DIGEST_A,
    budget_policy_fingerprint: str = DIGEST_A,
) -> CollaborationProposal:
    """Create one canonical proposal with a recomputed digest."""

    if inputs is None:
        inputs = (
            make_input(),
            make_input(
                "input_character",
                kind=CollaborationInputKind.AGENT_STATE,
                locator="agent_states/character_custodian.json",
                start_line=None,
                end_line=None,
            ),
        )
    if assignments is None:
        if step_kind is CollaborationStepKind.SPECIALIST_ADVISORY:
            assignments = (
                make_assignment(
                    input_ids=tuple(item.input_id for item in inputs),
                    max_physical_calls=2,
                ),
            )
            synthesis_max_physical_calls = 0
        else:
            assignments = (
                make_assignment(),
                make_assignment(
                    "assignment_character",
                    specialist_id=CHARACTER_CUSTODIAN,
                    input_ids=("input_character",),
                    instruction="Review the exact character-state revision.",
                ),
            )
    if profile_ids is None:
        profile_ids = tuple(item.specialist_id for item in assignments)
        if synthesis_max_physical_calls:
            profile_ids += (LEAD_ORCHESTRATOR,)
    profiles = tuple(
        CollaborationAgentProfile(agent_id, DIGEST_A) for agent_id in profile_ids
    )
    max_additional = sum(item.max_physical_calls for item in assignments)
    max_additional += synthesis_max_physical_calls
    budget = CollaborationBudget(
        round_limit=2,
        physical_call_limit=12,
        rounds_spent=rounds_spent,
        rounds_added=1,
        physical_calls_spent=physical_calls_spent,
        max_additional_physical_calls=max_additional,
        synthesis_max_physical_calls=synthesis_max_physical_calls,
    )
    basis = CollaborationProposalBasis(
        route=route,
        route_fingerprint=route_fingerprint,
        agent_profiles=profiles,
        context_policy_fingerprint=context_policy_fingerprint,
        budget_policy_fingerprint=budget_policy_fingerprint,
        inputs=inputs,
    )
    return CollaborationProposal.create(
        step_id=STEP_ID,
        proposal_id=proposal_id,
        proposal_revision=proposal_revision,
        step_kind=step_kind,
        purpose=purpose,
        assignments=assignments,
        user_impact=user_impact,
        side_effects=side_effects,
        budget=budget,
        basis=basis,
    )


def proposal_state(
    proposal: CollaborationProposal,
    state: CollaborationStepState,
    *,
    confirmation_request_id: str | None = None,
    execution_attempt_id: str | None = None,
    execution_result_event_id: str | None = None,
) -> CollaborationStepStatePayload:
    """Return a state payload correlated to ``proposal``."""

    return CollaborationStepStatePayload(
        state=state,
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.proposal_revision,
        proposal_digest=proposal.proposal_digest,
        confirmation_request_id=confirmation_request_id,
        execution_attempt_id=execution_attempt_id,
        execution_result_event_id=execution_result_event_id,
    )


def make_draft(
    event_id: str,
    kind: CollaborationEventKind,
    sender: CollaborationActor,
    recipient: CollaborationActor,
    payload: object,
    *,
    step_id: str = STEP_ID,
    turn_id: str = TURN_ID,
) -> CollaborationEventDraft:
    """Return one canonical caller-owned event."""

    return CollaborationEventDraft(
        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
        event_id=event_id,
        session_id=SESSION_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=turn_id,
        step_id=step_id,
        kind=kind,
        sender=sender,
        recipient=recipient,
        payload=payload,  # type: ignore[arg-type]
    )


def make_batch(
    history: tuple[CollaborationEvent, ...],
    batch_id: str,
    *events: CollaborationEventDraft,
) -> CollaborationEventBatch:
    """Return one CAS candidate at the history tail."""

    return CollaborationEventBatch(
        batch_id=batch_id,
        expected_last_sequence=len(history),
        events=events,
    )


def store_batch(
    history: tuple[CollaborationEvent, ...],
    batch: CollaborationEventBatch,
) -> tuple[CollaborationEvent, ...]:
    """Materialize deterministic storage metadata after public validation."""

    validate_collaboration_candidate_batch(history, batch)
    digest = collaboration_batch_digest(batch)
    offset = len(history)
    stored = tuple(
        CollaborationEvent(
            schema_version=event.schema_version,
            event_id=event.event_id,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            step_id=event.step_id,
            sequence=offset + ordinal + 1,
            timestamp=(
                f"2026-08-31T00:{(offset + ordinal) // 60:02d}:"
                f"{(offset + ordinal) % 60:02d}+00:00"
            ),
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(batch.events),
            batch_digest=digest,
            kind=event.kind,
            sender=event.sender,
            recipient=event.recipient,
            payload=event.payload,
        )
        for ordinal, event in enumerate(batch.events)
    )
    return history + stored


def awaiting_history(
    proposal: CollaborationProposal | None = None,
) -> tuple[tuple[CollaborationEvent, ...], CollaborationProposal]:
    """Return proposal disclosure plus an exact awaiting-confirmation tail."""

    proposal = make_proposal() if proposal is None else proposal
    awaiting = proposal_state(
        proposal,
        CollaborationStepState.AWAITING_CONFIRMATION,
        confirmation_request_id=REQUEST_ID,
    )
    batch = make_batch(
        (),
        "batch_proposal",
        make_draft(
            "proposal_event",
            CollaborationEventKind.MESSAGE,
            LEAD,
            USER,
            proposal,
        ),
        make_draft(
            REQUEST_EVENT_ID,
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            USER,
            awaiting,
        ),
    )
    return store_batch((), batch), proposal


def confirmed_history(
    proposal: CollaborationProposal | None = None,
) -> tuple[tuple[CollaborationEvent, ...], CollaborationProposal]:
    """Return history through exact structured confirmation."""

    history, proposal = awaiting_history(proposal)
    decision = CollaborationConfirmationPayload(
        confirmation_request_id=REQUEST_ID,
        confirmation_request_event_id=REQUEST_EVENT_ID,
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.proposal_revision,
        proposal_digest=proposal.proposal_digest,
        decision=CollaborationConfirmationDecision.CONFIRM,
    )
    batch = make_batch(
        history,
        "batch_confirmation",
        make_draft(
            "confirmation_event",
            CollaborationEventKind.CONFIRMATION,
            USER,
            RUNTIME,
            decision,
        ),
        make_draft(
            "confirmed_event",
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            USER,
            proposal_state(proposal, CollaborationStepState.CONFIRMED),
        ),
    )
    return store_batch(history, batch), proposal


def running_history(
    proposal: CollaborationProposal | None = None,
) -> tuple[tuple[CollaborationEvent, ...], CollaborationProposal]:
    """Return history through atomic running plus ordered dispatches."""

    history, proposal = confirmed_history(proposal)
    drafts: list[CollaborationEventDraft] = [
        make_draft(
            "running_event",
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            LEAD,
            proposal_state(
                proposal,
                CollaborationStepState.RUNNING,
                execution_attempt_id=ATTEMPT_ID,
            ),
        )
    ]
    actors = {
        PLOT_ARCHITECT: PLOT,
        CHARACTER_CUSTODIAN: CHARACTER,
    }
    for assignment in proposal.assignments:
        drafts.append(
            make_draft(
                f"dispatch_{assignment.assignment_id}",
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                RUNTIME,
                actors[assignment.specialist_id],
                CollaborationAssignmentDispatchPayload(
                    proposal.proposal_id,
                    proposal.proposal_revision,
                    proposal.proposal_digest,
                    ATTEMPT_ID,
                    assignment,
                ),
            )
        )
    return store_batch(history, make_batch(history, "batch_running", *drafts)), proposal


def result_batch(
    history: tuple[CollaborationEvent, ...],
    proposal: CollaborationProposal,
    *,
    failed_assignment_id: str | None = None,
) -> tuple[CollaborationEventBatch, CollaborationExecutionResult]:
    """Return a complete ordered external-result batch."""

    drafts: list[CollaborationEventDraft] = []
    outcomes: list[CollaborationAssignmentOutcome] = []
    actors = {
        PLOT_ARCHITECT: PLOT,
        CHARACTER_CUSTODIAN: CHARACTER,
    }
    advisory = proposal.step_kind is CollaborationStepKind.SPECIALIST_ADVISORY
    for assignment in proposal.assignments:
        if assignment.assignment_id == failed_assignment_id:
            outcome = CollaborationAssignmentOutcome(
                assignment.assignment_id,
                CollaborationAssignmentOutcomeKind.FAILED,
                1,
                None,
            )
        else:
            reply_id = f"reply_{assignment.assignment_id}"
            drafts.append(
                make_draft(
                    reply_id,
                    CollaborationEventKind.SPECIALIST_REPLY,
                    actors[assignment.specialist_id],
                    USER if advisory else LEAD,
                    CollaborationSpecialistReplyPayload(
                        proposal.proposal_id,
                        proposal.proposal_revision,
                        proposal.proposal_digest,
                        ATTEMPT_ID,
                        assignment.assignment_id,
                        f"dispatch_{assignment.assignment_id}",
                        f"Visible reply from {assignment.specialist_id}.",
                    ),
                )
            )
            outcome = CollaborationAssignmentOutcome(
                assignment.assignment_id,
                CollaborationAssignmentOutcomeKind.SUCCEEDED,
                1,
                reply_id,
            )
        outcomes.append(outcome)
        drafts.append(
            make_draft(
                f"terminal_{assignment.assignment_id}",
                CollaborationEventKind.TERMINAL_OUTCOME,
                RUNTIME,
                LEAD,
                CollaborationTerminalOutcomePayload(
                    ATTEMPT_ID,
                    CollaborationTerminalScope.ASSIGNMENT,
                    outcome.outcome,
                    outcome.submitted_physical_calls,
                    assignment.assignment_id,
                ),
            )
        )

    if failed_assignment_id is None and proposal.budget.synthesis_max_physical_calls:
        synthesis_result_id = "synthesis_message"
        synthesis_outcome = CollaborationSynthesisOutcomeKind.SUCCEEDED
        synthesis_calls = 1
        drafts.append(
            make_draft(
                synthesis_result_id,
                CollaborationEventKind.MESSAGE,
                LEAD,
                USER,
                CollaborationMessagePayload("Visible lead synthesis."),
            )
        )
        drafts.append(
            make_draft(
                "terminal_synthesis",
                CollaborationEventKind.TERMINAL_OUTCOME,
                RUNTIME,
                LEAD,
                CollaborationTerminalOutcomePayload(
                    ATTEMPT_ID,
                    CollaborationTerminalScope.SYNTHESIS,
                    CollaborationAssignmentOutcomeKind.SUCCEEDED,
                    synthesis_calls,
                    None,
                ),
            )
        )
    else:
        synthesis_result_id = None
        synthesis_outcome = CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED
        synthesis_calls = 0
    overall = (
        CollaborationExecutionOutcomeKind.FAILED
        if failed_assignment_id is not None
        else CollaborationExecutionOutcomeKind.SUCCEEDED
    )
    result = CollaborationExecutionResult(
        proposal.proposal_id,
        proposal.proposal_revision,
        proposal.proposal_digest,
        ATTEMPT_ID,
        tuple(outcomes),
        synthesis_outcome,
        synthesis_calls,
        synthesis_result_id,
        overall,
        tuple(item.event_id for item in drafts),
    )
    drafts.append(
        make_draft(
            "execution_result",
            CollaborationEventKind.EXECUTION_RESULT,
            RUNTIME,
            LEAD,
            result,
        )
    )
    return make_batch(history, "batch_result", *drafts), result


def executed_history(
    proposal: CollaborationProposal | None = None,
    *,
    failed_assignment_id: str | None = None,
) -> tuple[
    tuple[CollaborationEvent, ...],
    CollaborationProposal,
    CollaborationExecutionResult,
]:
    """Return history through one authoritative execution result."""

    history, proposal = running_history(proposal)
    batch, result = result_batch(
        history,
        proposal,
        failed_assignment_id=failed_assignment_id,
    )
    return store_batch(history, batch), proposal, result


def replace_proposal(
    proposal: CollaborationProposal,
    **changes: object,
) -> CollaborationProposal:
    """Recreate a proposal after one deliberate field mutation."""

    values = {
        "step_id": proposal.step_id,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision,
        "step_kind": proposal.step_kind,
        "purpose": proposal.purpose,
        "assignments": proposal.assignments,
        "user_impact": proposal.user_impact,
        "side_effects": proposal.side_effects,
        "budget": proposal.budget,
        "basis": proposal.basis,
    }
    values.update(changes)
    return CollaborationProposal.create(**values)  # type: ignore[arg-type]


def batch_from_history(
    history: tuple[CollaborationEvent, ...],
    batch_id: str,
) -> CollaborationEventBatch:
    """Reconstruct an exact caller batch from stored history."""

    group = tuple(event for event in history if event.batch_id == batch_id)
    if not group:
        raise ValueError(f"unknown batch: {batch_id}")
    return CollaborationEventBatch(
        batch_id=batch_id,
        expected_last_sequence=group[0].sequence - 1,
        events=tuple(event.to_draft() for event in group),
    )


def reround_assignment(
    assignment: CollaborationAssignment,
    round_number: int,
) -> CollaborationAssignment:
    """Return the assignment with one different lineage round."""

    return replace(assignment, round=round_number)
