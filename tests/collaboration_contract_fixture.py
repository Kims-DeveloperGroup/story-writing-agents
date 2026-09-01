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
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
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
    CollaborationInvocationKind,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSideEffect,
    CollaborationSpecialistReplyPayload,
    CollaborationSpecialistAssessment,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
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
    "CollaborationEvidenceAcquisition",
    "CollaborationEvidenceReceipt",
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
    "CollaborationInvocationKind",
    "CollaborationLeadSynthesisPayload",
    "CollaborationLeadEvidence",
    "CollaborationLeadJudgmentPayload",
    "CollaborationLeadOutcomeFailureCode",
    "CollaborationLeadOutcomeFailurePayload",
    "CollaborationMessagePayload",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationProviderCallKind",
    "CollaborationProviderCallPayload",
    "CollaborationProviderCallState",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
    "CollaborationSpecialistAssessment",
    "CollaborationStepKind",
    "CollaborationStepState",
    "CollaborationStepStatePayload",
    "CollaborationSynthesisOutcomeKind",
    "CollaborationTerminalOutcomePayload",
    "CollaborationTerminalScope",
    "ConversationEvent",
    "ConversationEventKind",
    "LeadAssignmentIntent",
    "LeadOutcomeKind",
    "LeadOutcomeScope",
    "LeadEvidenceStatus",
    "LeadProposalIntent",
    "LeadProposalRelation",
    "LeadProviderOutcome",
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
    "COLLABORATION_READABLE_CONTEXT_POLICY",
    "CONVERSATION_CONTEXT_POLICY",
    "CollaborationContextBootstrap",
    "CollaborationEventStore",
    "CollaborationHistoryProjection",
    "CollaborationNextAction",
    "CollaborationReadableContext",
    "CollaborationReadableContextPolicy",
    "CollaborationReadableField",
    "CollaborationReadableFieldKey",
    "CollaborationReadableRecord",
    "LeadDecisionResult",
    "LeadStepDecision",
    "LeadTurnResult",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "project_collaboration_history",
    "project_collaboration_readable_history",
    "select_bounded_collaboration_context",
    "select_bounded_conversation_context",
    "validate_collaboration_candidate_batch",
    "validate_collaboration_transition",
    "validate_conversation_context_window",
)

CONVERSATION_FACADE_EXPORTS = (
    "COLLABORATION_EVENT_SCHEMA_VERSION",
    "COLLABORATION_PHYSICAL_CALL_LIMIT",
    "COLLABORATION_ROUND_LIMIT",
    "COLLABORATION_READABLE_CONTEXT_POLICY",
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
    "CollaborationContextBootstrap",
    "CollaborationDirection",
    "CollaborationEvidenceAcquisition",
    "CollaborationEvidenceReceipt",
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
    "CollaborationInvocationKind",
    "CollaborationLeadSynthesisPayload",
    "CollaborationLeadEvidence",
    "CollaborationLeadJudgmentPayload",
    "CollaborationLeadOutcomeFailureCode",
    "CollaborationLeadOutcomeFailurePayload",
    "CollaborationMessagePayload",
    "CollaborationNextAction",
    "CollaborationPayload",
    "CollaborationProposal",
    "CollaborationProposalBasis",
    "CollaborationProviderCallKind",
    "CollaborationProviderCallPayload",
    "CollaborationProviderCallState",
    "CollaborationReadableContext",
    "CollaborationReadableContextPolicy",
    "CollaborationReadableField",
    "CollaborationReadableFieldKey",
    "CollaborationReadableRecord",
    "CollaborationSideEffect",
    "CollaborationSpecialistReplyPayload",
    "CollaborationSpecialistAssessment",
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
    "ConversationEventKind",
    "ConversationMetadata",
    "ConversationStatus",
    "LeadAssignmentIntent",
    "LeadDecisionResult",
    "LeadOutcomeKind",
    "LeadOutcomeScope",
    "LeadEvidenceStatus",
    "LeadProposalIntent",
    "LeadProposalRelation",
    "LeadProviderOutcome",
    "LeadStepDecision",
    "LeadTurnResult",
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
    "project_collaboration_readable_history",
    "select_bounded_collaboration_context",
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


def make_lead_evidence(
    *,
    status: LeadEvidenceStatus = LeadEvidenceStatus.VERIFIED,
) -> CollaborationLeadEvidence:
    """Return one content-free, story-specific attached-reference receipt."""

    receipt = CollaborationEvidenceReceipt(
        evidence_id="input_outline",
        acquisition=CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
        operation_id=f"lead_{TURN_ID}",
        result_correlation_id=f"outcome_{TURN_ID}",
        manifest_digest=DIGEST_A,
        source_id="source_outline",
        source_kind=CollaborationInputKind.CORE_DOCUMENT,
        locator="${core_documents/outline.md}",
        source_state=CollaborationInputState.PRESENT,
        revision_digest=DIGEST_A,
        content_digest=DIGEST_B,
        utf8_bytes=10,
        boundary_kind="artifact_lines",
        boundary_start=3,
        boundary_end=12,
        start_line=3,
        end_line=12,
        ordinal=1,
    )
    return CollaborationLeadEvidence(
        LeadOutcomeScope.STORY_SPECIFIC,
        status,
        (receipt,),
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
    physical_calls_spent: int = 1,
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
    evidence: CollaborationLeadEvidence | None = None,
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
            physical_calls_spent = 0
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
    if evidence is None and step_kind is not CollaborationStepKind.SPECIALIST_ADVISORY:
        evidence = make_lead_evidence()
    profiles = tuple(
        CollaborationAgentProfile(agent_id, DIGEST_A, DIGEST_B)
        for agent_id in profile_ids
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
        evidence=evidence,
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


def provider_call_drafts(
    *,
    invocation_id: str,
    call_id: str,
    role: str,
    recipient: CollaborationActor,
    invocation_kind: CollaborationInvocationKind,
    ordinal: int = 1,
    call_kind: CollaborationProviderCallKind = (CollaborationProviderCallKind.INITIAL),
) -> tuple[CollaborationEventDraft, CollaborationEventDraft]:
    """Return exact durable preparation/submission facts for one physical call."""

    common = {
        "invocation_id": invocation_id,
        "call_id": call_id,
        "ordinal": ordinal,
        "role": role,
        "invocation_kind": invocation_kind,
        "call_kind": call_kind,
    }
    return (
        make_draft(
            f"prepared_{call_id}",
            CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
            RUNTIME,
            recipient,
            CollaborationProviderCallPayload(
                **common,
                state=CollaborationProviderCallState.PREPARED,
            ),
        ),
        make_draft(
            f"submitted_{call_id}",
            CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
            RUNTIME,
            recipient,
            CollaborationProviderCallPayload(
                **common,
                state=CollaborationProviderCallState.SUBMITTED,
            ),
        ),
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
    drafts: list[CollaborationEventDraft] = []
    if proposal.step_kind is CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION:
        if proposal.basis.evidence is None:
            raise ValueError("confirmed lead proposals require evidence")
        drafts.extend(
            provider_call_drafts(
                invocation_id="invocation_lead",
                call_id="call_lead",
                role=LEAD_ORCHESTRATOR,
                recipient=LEAD,
                invocation_kind=CollaborationInvocationKind.LEAD,
            )
        )
        drafts.append(
            make_draft(
                "judgment_event",
                CollaborationEventKind.MESSAGE,
                LEAD,
                USER,
                CollaborationLeadJudgmentPayload(
                    LeadOutcomeKind.PROPOSAL,
                    "Specialist review is recommended before the next action.",
                    proposal.basis.evidence,
                ),
            )
        )
    drafts.extend(
        (
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
    )
    batch = make_batch((), "batch_proposal", *drafts)
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
    *,
    include_synthesis_call: bool = True,
) -> tuple[tuple[CollaborationEvent, ...], CollaborationProposal]:
    """Return an atomic start followed by durable physical-call facts."""

    history, proposal = confirmed_history(proposal)
    start_drafts: list[CollaborationEventDraft] = [
        make_draft(
            "running_event",
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            USER,
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
        start_drafts.append(
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
    history = store_batch(
        history,
        make_batch(history, "batch_running", *start_drafts),
    )
    call_drafts: list[CollaborationEventDraft] = []
    for assignment in proposal.assignments:
        call_drafts.extend(
            provider_call_drafts(
                invocation_id=f"invocation_{assignment.assignment_id}",
                call_id=f"call_{assignment.assignment_id}",
                role=assignment.specialist_id,
                recipient=actors[assignment.specialist_id],
                invocation_kind=CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
            )
        )
    if include_synthesis_call and proposal.budget.synthesis_max_physical_calls:
        call_drafts.extend(
            provider_call_drafts(
                invocation_id="invocation_synthesis",
                call_id="call_synthesis",
                role=LEAD_ORCHESTRATOR,
                recipient=LEAD,
                invocation_kind=CollaborationInvocationKind.LEAD_SYNTHESIS,
            )
        )
    if call_drafts:
        history = store_batch(
            history,
            make_batch(history, "batch_calls", *call_drafts),
        )
    return history, proposal


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
    if not advisory and proposal.basis.evidence is None:
        raise ValueError("lead synthesis requires proposal evidence")
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
                        CollaborationSpecialistAssessment.SUPPORTS,
                        f"Visible reply from {assignment.specialist_id}.",
                        assignment.input_ids,
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
                CollaborationEventKind.LEAD_SYNTHESIS,
                LEAD,
                USER,
                CollaborationLeadSynthesisPayload(
                    proposal.proposal_id,
                    proposal.proposal_revision,
                    proposal.proposal_digest,
                    ATTEMPT_ID,
                    "Visible lead synthesis.",
                    tuple(
                        f"reply_{assignment.assignment_id}"
                        for assignment in proposal.assignments
                    ),
                    (),
                    proposal.basis.evidence,
                    None,
                ),
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

    history, proposal = running_history(
        proposal,
        include_synthesis_call=failed_assignment_id is None,
    )
    batch, result = result_batch(
        history,
        proposal,
        failed_assignment_id=failed_assignment_id,
    )
    return store_batch(history, batch), proposal, result


def retarget_batch(
    batch: CollaborationEventBatch,
    conversation_id: str,
    *,
    batch_id: str | None = None,
) -> CollaborationEventBatch:
    """Return one caller batch addressed to a different conversation."""

    return CollaborationEventBatch(
        batch_id=batch.batch_id if batch_id is None else batch_id,
        expected_last_sequence=batch.expected_last_sequence,
        events=tuple(
            replace(event, conversation_id=conversation_id) for event in batch.events
        ),
    )


def contract_batches(
    conversation_id: str,
) -> dict[str, CollaborationEventBatch]:
    """Return atomic collaboration families shared by store adapters."""

    awaiting, proposal = awaiting_history()
    confirmed, _ = confirmed_history(proposal)
    running, _ = running_history(proposal)
    executed, _, _ = executed_history(proposal)
    completion = make_batch(
        executed,
        "batch_completed",
        make_draft(
            "notice_completed",
            CollaborationEventKind.MESSAGE,
            RUNTIME,
            USER,
            CollaborationMessagePayload("Visible completed notice."),
        ),
        make_draft(
            "state_completed",
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            USER,
            proposal_state(
                proposal,
                CollaborationStepState.COMPLETED,
                execution_result_event_id="execution_result",
            ),
        ),
    )
    unknown = make_batch(
        running,
        "batch_unknown",
        make_draft(
            "unknown_interruption",
            CollaborationEventKind.INTERRUPTION,
            RUNTIME,
            USER,
            CollaborationInterruptionPayload(
                ATTEMPT_ID,
                "No authoritative result exists after restart.",
            ),
        ),
        make_draft(
            "unknown_state",
            CollaborationEventKind.STEP_STATE,
            RUNTIME,
            USER,
            proposal_state(
                proposal,
                CollaborationStepState.OUTCOME_UNKNOWN,
                execution_attempt_id=ATTEMPT_ID,
            ),
        ),
    )
    return {
        "proposal": retarget_batch(
            batch_from_history(awaiting, "batch_proposal"), conversation_id
        ),
        "confirmation": retarget_batch(
            batch_from_history(confirmed, "batch_confirmation"), conversation_id
        ),
        "running": retarget_batch(
            batch_from_history(running, "batch_running"), conversation_id
        ),
        "calls": retarget_batch(
            batch_from_history(running, "batch_calls"), conversation_id
        ),
        "result": retarget_batch(
            batch_from_history(executed, "batch_result"), conversation_id
        ),
        "completion": retarget_batch(completion, conversation_id),
        "unknown": retarget_batch(unknown, conversation_id),
    }


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
