"""Pure schema-v2 collaboration projection and candidate validation tests."""

from __future__ import annotations

import json
from story_writing_agents.conversations import project_editorial_state, encode_collaboration_event
from tests.test_collaboration_events import editorial, editorial_history, editorial_append
import hashlib
import dataclasses
import inspect
import unittest
from dataclasses import replace
from typing import get_type_hints

from story_writing_agents.conversations import (
    COLLABORATION_READABLE_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventKind,
    CollaborationEventStore,
    CollaborationExecutionOutcomeKind,
    CollaborationHistoryProjection,
    CollaborationInterruptionPayload,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationReadableContextPolicy,
    CollaborationReadableFieldKey,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    ConversationEvent,
    ConversationEventKind,
    project_collaboration_history,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
    validate_collaboration_candidate_batch,
    validate_collaboration_transition,
)

from tests.collaboration_contract_fixture import (
    ATTEMPT_ID,
    CHARACTER_CUSTODIAN,
    CONVERSATION_ID,
    LEAD,
    PROPOSAL_ID,
    REQUEST_EVENT_ID,
    REQUEST_ID,
    RUNTIME,
    SESSION_ID,
    USER,
    awaiting_history,
    batch_from_history,
    confirmed_history,
    executed_history,
    make_assignment,
    make_batch,
    make_draft,
    make_proposal,
    proposal_state,
    provider_call_drafts,
    result_batch,
    running_history,
    store_batch,
)


def _legacy_rejection_input_fixture():
    return ConversationEvent(
        schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
        session_id=SESSION_ID,
        conversation_id=CONVERSATION_ID,
        turn_id="legacy_turn",
        sequence=1,
        timestamp="2026-08-31T00:00:00+00:00",
        sender="user",
        recipient="lead_orchestrator",
        kind=ConversationEventKind.USER_MESSAGE,
        content="Legacy content.",
    )

def _state_draft(event_id, proposal, state, **changes):
    return make_draft(
        event_id, CollaborationEventKind.STEP_STATE, RUNTIME, USER,
        proposal_state(proposal, state, **changes),
    )

def _user_message_draft(event_id, text):
    return make_draft(
        event_id, CollaborationEventKind.MESSAGE, USER, LEAD,
        CollaborationMessagePayload(text),
    )

def _assert_invalid(
    test: unittest.TestCase,
    history: tuple[CollaborationEvent, ...],
    batch: CollaborationEventBatch,
    failure: CollaborationAppendFailure = CollaborationAppendFailure.INVALID_TRANSITION,
) -> None:
    with test.assertRaises(CollaborationAppendError) as raised:
        validate_collaboration_candidate_batch(history, batch)
    test.assertIs(raised.exception.failure, failure)


def _terminal_state_batch(
    history: tuple[CollaborationEvent, ...],
    proposal: object,
    state: CollaborationStepState,
) -> CollaborationEventBatch:
    result_event = next(
        event for event in reversed(history) if event.event_id == "execution_result"
    )
    return make_batch(
        history,
        f"batch_{state.value}",
        make_draft(
            f"notice_{state.value}",
            CollaborationEventKind.MESSAGE,
            RUNTIME,
            USER,
            CollaborationMessagePayload(f"Visible {state.value} notice."),
        ),
        _state_draft(
            f'state_{state.value}',
            proposal,
            state,
            execution_result_event_id=result_event.event_id,
        ),
    )


class CollaborationTransitionTests(unittest.TestCase):
    def test_all_legal_and_illegal_transition_pairs_are_exact(self) -> None:
        legal = {
            None: {CollaborationStepState.AWAITING_CONFIRMATION},
            CollaborationStepState.AWAITING_CONFIRMATION: {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.RECONFIRMATION_REQUIRED: {
                CollaborationStepState.CONFIRMED,
                CollaborationStepState.REJECTED,
                CollaborationStepState.SUPERSEDED,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.CONFIRMED: {
                CollaborationStepState.RUNNING,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.RUNNING: {
                CollaborationStepState.COMPLETED,
                CollaborationStepState.INTERRUPTED,
                CollaborationStepState.FAILED,
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.COMPLETED: {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.REJECTED: {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.SUPERSEDED: {
                CollaborationStepState.AWAITING_CONFIRMATION,
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.INTERRUPTED: {
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.FAILED: {
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.OUTCOME_UNKNOWN: {
                CollaborationStepState.RECOVERY_REQUIRED,
            },
            CollaborationStepState.RECOVERY_REQUIRED: set(),
        }
        for previous in (None, *CollaborationStepState):
            for candidate in CollaborationStepState:
                with self.subTest(previous=previous, candidate=candidate):
                    if candidate in legal[previous]:
                        self.assertIs(
                            validate_collaboration_transition(previous, candidate),
                            candidate,
                        )
                    else:
                        with self.assertRaises(ValueError):
                            validate_collaboration_transition(previous, candidate)
        with self.assertRaises(TypeError):
            validate_collaboration_transition("running", CollaborationStepState.FAILED)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            validate_collaboration_transition(None, "running")  # type: ignore[arg-type]

    def test_projection_is_deterministic_ordered_typed_and_history_only(self) -> None:
        history, proposal, result = executed_history()
        first = project_collaboration_history(history)
        second = project_collaboration_history(tuple(history))
        self.assertEqual(first, second)
        self.assertEqual(first.session_id, SESSION_ID)
        self.assertEqual(first.conversation_id, CONVERSATION_ID)
        self.assertEqual(first.last_sequence, len(history))
        self.assertEqual(first.current_proposal, proposal)
        self.assertIs(first.current_state, CollaborationStepState.RUNNING)
        self.assertEqual(first.execution_attempt_id, ATTEMPT_ID)
        self.assertEqual(first.execution_result_event_id, "execution_result")
        self.assertEqual(first.execution_result, result)
        self.assertIs(first.next_safe_action, CollaborationNextAction.FINALIZE_RESULT)
        self.assertEqual(first.rounds_spent, 1)
        self.assertEqual(first.physical_calls_spent, 4)
        self.assertEqual(
            tuple(event.sequence for event in history),
            tuple(range(1, len(history) + 1)),
        )
        self.assertEqual(
            tuple(
                event.event_id
                for event in history
                if event.kind is CollaborationEventKind.SPECIALIST_REPLY
            ),
            ("reply_assignment_plot", "reply_assignment_character"),
        )
        self.assertFalse(
            {
                "authority",
                "capability",
                "provider_handle",
                "private_state",
            }
            & set(first.__dataclass_fields__)
        )
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(first)),
            (
                "session_id",
                "conversation_id",
                "last_sequence",
                "current_turn_id",
                "current_step_id",
                "current_proposal",
                "current_state",
                "confirmation_request_id",
                "confirmation_decision",
                "execution_attempt_id",
                "execution_result_event_id",
                "execution_result",
                "next_safe_action",
                "rounds_spent",
                "physical_calls_spent",
                "current_artifact_candidate",
                "current_artifact_state",
                "current_reviewed_artifact_proposal",
                "current_artifact_application_request",
                "current_artifact_application_result",
                "current_story_commit_request",
                "current_story_commit_result",
                "artifact_objectives",
                "offered_draft_sources",
                "current_recommendation_request",
                "current_recommendation_decision",
                "current_recommendation_start",
                "current_recommendation_result",
                "current_recommendation_state",
                "premise_report",
                "premise_report_event_id",
                "premise_decision",
                "premise_selection",
                "premise_admission",
                "premise_admission_event_id",
                "available_tasks",
                "selectable_materials",
                "public_responses",
                "discussion_context",
            ),
        )

        for kind in (editorial.EditorialRecordKind.DIRECTION, editorial.EditorialRecordKind.PROTECTED_AMBIGUITY, editorial.EditorialRecordKind.SETUP_OBLIGATION):
            memory = editorial_history(kind)
            selected = memory[-1].payload.record
            self.assertEqual(project_editorial_state(SESSION_ID, memory).selected_records, (selected,))
            self.assertIs(selected.draft.kind, kind)
            rejected = editorial_append(memory, editorial.EditorialRecordRequest("reject_memory", editorial.EditorialAction.REJECT, "Discard explicitly.", expected_ref=selected.ref))
            self.assertEqual(project_editorial_state(SESSION_ID, rejected).selected_records, ())
            with self.assertRaises(ValueError):
                editorial_append(rejected, editorial.EditorialRecordRequest("revive_by_select", editorial.EditorialAction.SELECT, "Must not revive.", expected_ref=rejected[-1].payload.record.ref))
            revised = editorial_append(rejected, editorial.EditorialRecordRequest("revise_memory", editorial.EditorialAction.REVISE, "Fresh USER choice.", expected_ref=rejected[-1].payload.record.ref, draft=replace(selected.draft, text="Fresh explicit revision."), revision_approval=editorial.EditorialApprovalState.SELECTED))
            state = project_editorial_state(SESSION_ID, revised)
            self.assertIs(state.effective_approval(selected.ref), editorial.EditorialApprovalState.SUPERSEDED)
            self.assertEqual((state.current_records[0].revision, state.current_records[0].draft.text), (3, "Fresh explicit revision."))
            self.assertEqual(revised[:len(memory)], memory)
            with self.assertRaises(editorial.EditorialStateIntegrityError):
                editorial.EditorialStateProjection(SESSION_ID, state.revisions[1:])
        source = store_batch((), make_batch((), "citation_batch", replace(make_draft("citation", CollaborationEventKind.MESSAGE, USER, LEAD, CollaborationMessagePayload("Exact optional source.")), conversation_id="citation_conversation")))
        provenance = editorial.EditorialProvenanceRef(SESSION_ID, "citation_conversation", "citation", hashlib.sha256(encode_collaboration_event(source[0])).hexdigest())
        memory = editorial_history(provenance_refs=(provenance,))
        self.assertEqual(project_editorial_state(SESSION_ID, source + memory).provenance_gaps, ())
        missing = project_editorial_state(SESSION_ID, memory)
        self.assertEqual(missing.provenance_gaps, ((memory[-1].payload.record.ref, provenance),))
        self.assertEqual(missing.selected_records, (memory[-1].payload.record,))

        limited = editorial.bounded_editorial_view(missing, 512)
        self.assertLessEqual(len(json.dumps(limited, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), 512)
        self.assertEqual((limited["records"], limited["selected_refs"], limited["omitted_record_count"]), ([], [], 1))
        self.assertEqual(limited["omitted_provenance_gap_count"], 1)
        with self.assertRaises(ValueError):
            editorial.bounded_editorial_view(missing, 1)


class CollaborationBudgetAndOutcomeTests(unittest.TestCase):
    def test_first_second_and_third_round_boundaries_use_durable_lineage(self) -> None:
        history, first, _ = executed_history()
        history = store_batch(
            history,
            _terminal_state_batch(history, first, CollaborationStepState.COMPLETED),
        )
        second_assignments = tuple(
            replace(assignment, round=2) for assignment in first.assignments
        )
        second = make_proposal(
            proposal_id=PROPOSAL_ID,
            proposal_revision=2,
            assignments=second_assignments,
            inputs=first.basis.inputs,
            rounds_spent=1,
            physical_calls_spent=4,
        )
        request = proposal_state(
            second,
            CollaborationStepState.AWAITING_CONFIRMATION,
            confirmation_request_id="request_round_2",
        )
        batch = make_batch(
            history,
            "batch_round_2",
            make_draft(
                "proposal_round_2",
                CollaborationEventKind.MESSAGE,
                LEAD,
                USER,
                second,
            ),
            make_draft(
                "request_round_2_event",
                CollaborationEventKind.STEP_STATE,
                RUNTIME,
                USER,
                request,
            ),
        )
        projection = validate_collaboration_candidate_batch(history, batch)
        self.assertIsInstance(projection, CollaborationHistoryProjection)
        assert isinstance(projection, CollaborationHistoryProjection)
        self.assertEqual(projection.current_proposal, second)
        self.assertEqual(second.budget.rounds_spent, 1)
        with self.assertRaises(ValueError):
            make_proposal(rounds_spent=2)

        drift = make_proposal(
            proposal_id=PROPOSAL_ID,
            proposal_revision=2,
            assignments=second_assignments,
            inputs=first.basis.inputs,
            rounds_spent=1,
            physical_calls_spent=3,
        )
        drift_batch = make_batch(
            history,
            "batch_spent_drift",
            make_draft(
                "proposal_spent_drift",
                CollaborationEventKind.MESSAGE,
                LEAD,
                USER,
                drift,
            ),
            _state_draft(
                'request_spent_drift',
                drift,
                CollaborationStepState.AWAITING_CONFIRMATION,
                confirmation_request_id='request_spent_drift',
            ),
        )
        _assert_invalid(self, history, drift_batch)

    def test_twelve_calls_are_accepted_and_possible_thirteenth_is_rejected(
        self,
    ) -> None:
        inputs = make_proposal().basis.inputs
        twelve = make_proposal(
            assignments=(
                make_assignment(max_physical_calls=5),
                make_assignment(
                    "assignment_character",
                    specialist_id=CHARACTER_CUSTODIAN,
                    input_ids=("input_character",),
                    max_physical_calls=5,
                ),
            ),
            inputs=inputs,
            synthesis_max_physical_calls=1,
        )
        self.assertEqual(twelve.budget.max_additional_physical_calls, 11)
        awaiting, _ = awaiting_history(twelve)
        self.assertEqual(
            project_collaboration_history(awaiting).current_proposal,
            twelve,
        )
        with self.assertRaises(ValueError):
            make_proposal(
                assignments=(
                    make_assignment(max_physical_calls=6),
                    make_assignment(
                        "assignment_character",
                        specialist_id=CHARACTER_CUSTODIAN,
                        input_ids=("input_character",),
                        max_physical_calls=6,
                    ),
                ),
                inputs=inputs,
                synthesis_max_physical_calls=1,
            )

        history = store_batch(
            (),
            make_batch(
                (),
                "batch_user_for_calls",
                _user_message_draft(
                    'user_for_calls',
                    'Start a bounded lead turn.',
                ),
            ),
        )
        call_drafts = []
        for ordinal in range(1, 13):
            call_drafts.extend(
                provider_call_drafts(
                    invocation_id="invocation_lead_limit",
                    call_id=f"call_lead_{ordinal}",
                    role="lead_orchestrator",
                    recipient=LEAD,
                    invocation_kind=CollaborationInvocationKind.LEAD,
                    ordinal=ordinal,
                    call_kind=(
                        CollaborationProviderCallKind.INITIAL
                        if ordinal == 1
                        else CollaborationProviderCallKind.CONTINUATION
                    ),
                )
            )
        history = store_batch(
            history,
            make_batch(history, "batch_twelve_calls", *call_drafts),
        )
        projection = project_collaboration_history(history)
        self.assertEqual(projection.physical_calls_spent, 12)
        prepared_thirteen = CollaborationProviderCallPayload(
            "invocation_lead_limit",
            "call_lead_13",
            13,
            "lead_orchestrator",
            CollaborationInvocationKind.LEAD,
            CollaborationProviderCallKind.CONTINUATION,
            CollaborationProviderCallState.PREPARED,
        )
        prepared_history = store_batch(
            history,
            make_batch(
                history,
                "batch_prepared_thirteen",
                make_draft(
                    "prepared_call_lead_13",
                    CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    RUNTIME,
                    LEAD,
                    prepared_thirteen,
                ),
            ),
        )
        self.assertEqual(
            project_collaboration_history(prepared_history).physical_calls_spent,
            12,
        )
        submitted_thirteen = replace(
            prepared_thirteen,
            state=CollaborationProviderCallState.SUBMITTED,
        )
        _assert_invalid(
            self,
            prepared_history,
            make_batch(
                prepared_history,
                "batch_submitted_thirteen",
                make_draft(
                    "submitted_call_lead_13",
                    CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    RUNTIME,
                    LEAD,
                    submitted_thirteen,
                ),
            ),
        )

    def test_assignment_synthesis_step_and_lineage_usage_caps_are_enforced(
        self,
    ) -> None:
        history, proposal = running_history()
        batch, result = result_batch(history, proposal)
        drafts = list(batch.events)
        assignment_id = proposal.assignments[0].assignment_id
        terminal_index = next(
            index
            for index, event in enumerate(drafts)
            if event.event_id == f"terminal_{assignment_id}"
        )
        outcome = replace(
            result.assignment_outcomes[0],
            submitted_physical_calls=proposal.assignments[0].max_physical_calls + 1,
        )
        outcomes = (outcome, *result.assignment_outcomes[1:])
        changed_result = replace(result, assignment_outcomes=outcomes)
        terminal = drafts[terminal_index].payload
        assert isinstance(terminal, CollaborationTerminalOutcomePayload)
        drafts[terminal_index] = replace(
            drafts[terminal_index],
            payload=replace(
                terminal,
                submitted_physical_calls=outcome.submitted_physical_calls,
            ),
        )
        drafts[-1] = replace(drafts[-1], payload=changed_result)
        over = CollaborationEventBatch(
            "batch_over_assignment",
            len(history),
            tuple(drafts),
        )
        _assert_invalid(self, history, over)

    def test_partial_failure_preserves_sibling_and_prohibits_synthesis(self) -> None:
        history, proposal, result = executed_history(
            failed_assignment_id="assignment_character"
        )
        projection = project_collaboration_history(history)
        self.assertEqual(projection.execution_result, result)
        self.assertEqual(
            tuple(item.outcome for item in result.assignment_outcomes),
            (
                CollaborationAssignmentOutcomeKind.SUCCEEDED,
                CollaborationAssignmentOutcomeKind.FAILED,
            ),
        )
        self.assertIs(
            result.synthesis_outcome,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        self.assertEqual(result.synthesis_submitted_physical_calls, 0)
        self.assertIsNone(result.synthesis_result_event_id)
        self.assertIs(
            result.overall_outcome,
            CollaborationExecutionOutcomeKind.FAILED,
        )
        self.assertIn("reply_assignment_plot", result.visible_exchange_event_ids)
        self.assertNotIn("synthesis_message", result.visible_exchange_event_ids)
        history = store_batch(
            history,
            _terminal_state_batch(history, proposal, CollaborationStepState.FAILED),
        )
        self.assertIs(
            project_collaboration_history(history).current_state,
            CollaborationStepState.FAILED,
        )

    def test_zero_synthesis_advisory_is_valid_and_submission_is_rejected(self) -> None:
        advisory = make_proposal(step_kind=CollaborationStepKind.SPECIALIST_ADVISORY)
        history, _, result = executed_history(advisory)
        self.assertIs(
            result.synthesis_outcome,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        self.assertEqual(result.synthesis_submitted_physical_calls, 0)
        self.assertNotIn("synthesis_message", result.visible_exchange_event_ids)
        self.assertIs(
            project_collaboration_history(history).next_safe_action,
            CollaborationNextAction.FINALIZE_RESULT,
        )


class CollaborationConfirmationAndRecoveryTests(unittest.TestCase):
    def test_failed_read_only_attempt_closes_exactly_once_and_rejects_late_calls(self):
        codes = (CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED,
                 CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED,
                 CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT,
                 CollaborationLeadOutcomeFailureCode.CANCELLED,
                 CollaborationLeadOutcomeFailureCode.INTERRUPTED)
        for code in codes:
            with self.subTest(code=code):
                message = make_draft("request", CollaborationEventKind.MESSAGE, USER, LEAD,
                                     CollaborationMessagePayload("Retain this exact request."))
                calls = provider_call_drafts(invocation_id="invocation", call_id="call",
                                             role="lead_orchestrator", recipient=LEAD,
                                             invocation_kind=CollaborationInvocationKind.LEAD)
                bare = code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED
                prefix = store_batch((), make_batch((), "request_batch", message,
                                                    *(() if bare else calls)))
                failure = make_draft("closure", CollaborationEventKind.LEAD_OUTCOME_FAILURE, RUNTIME, USER,
                                     CollaborationLeadOutcomeFailurePayload(
                                         code, None if bare else "invocation", None if bare else "call"))
                closed = store_batch(prefix, make_batch(prefix, "closure_batch", failure))
                self.assertIs(project_collaboration_history(closed).next_safe_action,
                              CollaborationNextAction.RETRY_LEAD_TURN)
                _assert_invalid(self, closed, make_batch(closed, "duplicate_closure",
                                                        replace(failure, event_id="second_closure")))
                late = provider_call_drafts(invocation_id="late_invocation", call_id="late_call",
                                            role="lead_orchestrator", recipient=LEAD,
                                            invocation_kind=CollaborationInvocationKind.LEAD)
                _assert_invalid(self, closed, make_batch(closed, "late_call_batch", *late))
                wrong_turn = replace(failure, turn_id="foreign_turn")
                _assert_invalid(self, prefix, make_batch(prefix, "wrong_turn_batch", wrong_turn))
                if not bare:
                    for field in ("invocation_id", "call_id"):
                        with self.subTest(field=field):
                            wrong = replace(failure, payload=replace(failure.payload, **{field: "other"}))
                            _assert_invalid(self, prefix, make_batch(prefix, "wrong_identity_batch", wrong))
                if code is CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT:
                    prepared = store_batch((), make_batch((), "prepared_only", message, calls[0]))
                    _assert_invalid(self, prepared, make_batch(prepared, "unsubmitted_timeout", failure))
                if bare:
                    started = store_batch((), make_batch((), "started_request", message, calls[0]))
                    _assert_invalid(self, started, make_batch(started, "false_unsubmitted", failure))

    def test_failed_revision_result_cannot_carry_or_revive_confirmation_authority(self):
        from story_writing_agents.artifact_proposals import artifact_review_specialists
        from story_writing_agents.conversations import (
            ArtifactProposalResult, ArtifactProposalState, ArtifactReviewCandidatePayload,
            LeadDecisionResult, LeadOutcomeKind, LeadTurnResult,
        )
        from tests.test_artifact_proposals import _combined_candidate

        proposal = make_proposal()
        turn = LeadTurnResult(SESSION_ID, CONVERSATION_ID, "failed_revision", proposal.step_id,
                              LeadOutcomeKind.RECOMMENDATION, "Explicit retry is available.",
                              None, None, None, CollaborationNextAction.RETRY_LEAD_TURN)
        result = LeadDecisionResult(SESSION_ID, CONVERSATION_ID, proposal.step_id,
                                    CollaborationConfirmationDecision.REVISE, proposal, turn)
        self.assertIsNone(result.turn.confirmation_request_id)
        self.assertIsNone(result.turn.proposal)
        for changes in ({"kind": LeadOutcomeKind.ANSWER}, {"state": CollaborationStepState.SUPERSEDED},
                        {"proposal": proposal}, {"confirmation_request_id": "old_confirmation"},
                        {"evidence": proposal.basis.evidence}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                replace(result, turn=replace(turn, **changes))
        _, candidate, _ = _combined_candidate()
        artifact = ArtifactProposalResult(ArtifactProposalState.SUPERSEDED, ArtifactReviewCandidatePayload(
            candidate, "original_judgment", proposal.proposal_id, proposal.proposal_revision,
            proposal.proposal_digest, artifact_review_specialists(candidate.mandatory_review_domains),
            artifact_review_specialists(candidate.disclosed_review_domains)))
        retained = replace(result, turn=replace(turn, artifact_proposal=artifact))
        self.assertIs(retained.turn.artifact_proposal.state, ArtifactProposalState.SUPERSEDED)
        for field, value in (("consultation_proposal_id", "other_proposal"),
                             ("consultation_proposal_revision", proposal.proposal_revision + 1),
                             ("consultation_proposal_digest", "sha256:" + "f" * 64)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "no replacement authority"):
                changed = replace(artifact, candidate=replace(artifact.candidate, **{field: value}))
                replace(result, turn=replace(turn, artifact_proposal=changed))
        with self.assertRaises(ValueError):
            replace(result, turn=replace(turn, artifact_proposal=replace(artifact, state=ArtifactProposalState.STALE)))

    def test_unmatched_read_only_lead_projects_retry_without_write_authority(
        self,
    ) -> None:
        message = _user_message_draft(
                      'submitted_user_message',
                      'Request sent to the Lead.',
                  )
        calls = provider_call_drafts(
            invocation_id="lead_invocation",
            call_id="lead_call",
            role="lead_orchestrator",
            recipient=LEAD,
            invocation_kind=CollaborationInvocationKind.LEAD,
        )
        prepared = store_batch(
            (),
            make_batch((), "prepared_lead_batch", message, calls[0]),
        )
        prepared_projection = project_collaboration_history(prepared)
        self.assertIsNone(prepared_projection.current_state)
        self.assertIs(
            prepared_projection.next_safe_action,
            CollaborationNextAction.RETRY_LEAD_TURN,
        )

        submitted = store_batch(
            prepared,
            make_batch(prepared, "submitted_lead_batch", calls[1]),
        )
        projection = project_collaboration_history(submitted)
        self.assertIsNone(projection.current_state)
        self.assertIs(
            projection.next_safe_action,
            CollaborationNextAction.RETRY_LEAD_TURN,
        )
        self.assertIsNone(projection.current_proposal)
        self.assertEqual(projection.current_turn_id, submitted[-1].turn_id)
        self.assertEqual(projection.current_step_id, submitted[-1].step_id)

    def test_rejected_submitted_lead_projects_retry_and_readable_closure(
        self,
    ) -> None:
        message = _user_message_draft(
                      'rejected_user_message',
                      'Reject the returned provider result.',
                  )
        calls = provider_call_drafts(
            invocation_id="rejected_invocation",
            call_id="rejected_call",
            role="lead_orchestrator",
            recipient=LEAD,
            invocation_kind=CollaborationInvocationKind.LEAD,
        )
        prepared = store_batch(
            (),
            make_batch((), "rejected_prepared_batch", message, calls[0]),
        )
        submitted = store_batch(
            prepared,
            make_batch(prepared, "rejected_submitted_batch", calls[1]),
        )
        closed = store_batch(
            submitted,
            make_batch(
                submitted,
                "rejected_closed_batch",
                make_draft(
                    "rejected_closure",
                    CollaborationEventKind.LEAD_OUTCOME_FAILURE,
                    RUNTIME,
                    USER,
                    CollaborationLeadOutcomeFailurePayload(
                        CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                        "rejected_invocation",
                        "rejected_call",
                    ),
                ),
            ),
        )

        projection = project_collaboration_history(closed)
        self.assertIsNone(projection.current_state)
        self.assertIsNone(projection.current_proposal)
        self.assertIs(
            projection.next_safe_action,
            CollaborationNextAction.RETRY_LEAD_TURN,
        )
        self.assertEqual(projection.physical_calls_spent, 1)
        readable = project_collaboration_readable_history(closed)
        closure_record = readable[-1]
        self.assertIs(
            closure_record.kind,
            CollaborationEventKind.LEAD_OUTCOME_FAILURE,
        )
        self.assertEqual(
            tuple((field.key, field.text) for field in closure_record.fields),
            (
                (
                    CollaborationReadableFieldKey.LEAD_OUTCOME_FAILURE,
                    "provider_result_rejected",
                ),
            ),
        )

        wrong_call = make_batch(
            submitted,
            "wrong_rejected_closed_batch",
            make_draft(
                "wrong_rejected_closure",
                CollaborationEventKind.LEAD_OUTCOME_FAILURE,
                RUNTIME,
                USER,
                CollaborationLeadOutcomeFailurePayload(
                    CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
                    "rejected_invocation",
                    "other_call",
                ),
            ),
        )
        _assert_invalid(self, submitted, wrong_call)

    def test_confirmation_is_exact_tail_single_use_and_free_text_has_no_authority(
        self,
    ) -> None:
        awaiting, proposal = awaiting_history()
        yes_batch = make_batch(
            awaiting,
            "batch_free_text",
            _user_message_draft(
                'free_text_yes',
                'Yes',
            ),
        )
        with_yes = store_batch(awaiting, yes_batch)
        projection = project_collaboration_history(with_yes)
        self.assertIs(
            projection.current_state,
            CollaborationStepState.AWAITING_CONFIRMATION,
        )
        self.assertIsNone(projection.confirmation_decision)

        stale_decision = CollaborationConfirmationPayload(
            REQUEST_ID,
            REQUEST_EVENT_ID,
            proposal.proposal_id,
            proposal.proposal_revision,
            proposal.proposal_digest,
            CollaborationConfirmationDecision.CONFIRM,
        )
        stale = make_batch(
            with_yes,
            "batch_stale_confirmation",
            make_draft(
                "stale_confirmation",
                CollaborationEventKind.CONFIRMATION,
                USER,
                RUNTIME,
                stale_decision,
            ),
            _state_draft(
                'stale_confirmed',
                proposal,
                CollaborationStepState.CONFIRMED,
            ),
        )
        _assert_invalid(self, with_yes, stale)

        confirmed, proposal = confirmed_history(proposal)
        second = make_batch(
            confirmed,
            "batch_second_decision",
            make_draft(
                "second_confirmation",
                CollaborationEventKind.CONFIRMATION,
                USER,
                RUNTIME,
                stale_decision,
            ),
            _state_draft(
                'second_confirmed',
                proposal,
                CollaborationStepState.CONFIRMED,
            ),
        )
        _assert_invalid(self, confirmed, second)

        wrong = replace(stale_decision, proposal_digest="sha256:" + "9" * 64)
        wrong_batch = make_batch(
            awaiting,
            "batch_wrong_digest",
            make_draft(
                "wrong_confirmation",
                CollaborationEventKind.CONFIRMATION,
                USER,
                RUNTIME,
                wrong,
            ),
            _state_draft(
                'wrong_confirmed',
                proposal,
                CollaborationStepState.CONFIRMED,
            ),
        )
        _assert_invalid(self, awaiting, wrong_batch)

    def test_confirmed_restart_requires_fresh_reconfirmation_without_authority(
        self,
    ) -> None:
        history, proposal = confirmed_history()
        batch = make_batch(
            history,
            "batch_reconfirmation",
            _state_draft(
                'reconfirmation_event',
                proposal,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                confirmation_request_id='request_reconfirmation',
            ),
        )
        history = store_batch(history, batch)
        projection = project_collaboration_history(history)
        self.assertIs(
            projection.current_state,
            CollaborationStepState.RECONFIRMATION_REQUIRED,
        )
        self.assertEqual(projection.confirmation_request_id, "request_reconfirmation")
        self.assertEqual(projection.current_proposal, proposal)
        self.assertEqual(
            projection.current_proposal.proposal_digest,
            proposal.proposal_digest,
        )
        self.assertIs(
            projection.next_safe_action,
            CollaborationNextAction.SUBMIT_CONFIRMATION,
        )
        reused = make_batch(
            confirmed_history()[0],
            "batch_reused_request",
            _state_draft(
                'reused_request_event',
                proposal,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                confirmation_request_id=REQUEST_ID,
            ),
        )
        _assert_invalid(self, confirmed_history()[0], reused)

    def test_running_restart_finalizes_known_result_or_enters_unknown_without_replay(
        self,
    ) -> None:
        executed, _, _ = executed_history()
        self.assertIs(
            project_collaboration_history(executed).next_safe_action,
            CollaborationNextAction.FINALIZE_RESULT,
        )

        running, proposal = running_history()
        unknown_batch = make_batch(
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
            _state_draft(
                'unknown_state',
                proposal,
                CollaborationStepState.OUTCOME_UNKNOWN,
                execution_attempt_id=ATTEMPT_ID,
            ),
        )
        unknown = store_batch(running, unknown_batch)
        projection = project_collaboration_history(unknown)
        self.assertIs(projection.current_state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(
            projection.next_safe_action,
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        late_result, _ = result_batch(unknown, proposal)
        _assert_invalid(self, unknown, late_result)

        recovery = make_batch(
            unknown,
            "batch_recovery",
            _state_draft(
                'recovery_state',
                proposal,
                CollaborationStepState.RECOVERY_REQUIRED,
            ),
        )
        recovered = store_batch(unknown, recovery)
        self.assertIs(
            project_collaboration_history(recovered).next_safe_action,
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
        )


class CollaborationAtomicBatchTests(unittest.TestCase):
    def test_exact_retry_and_all_fixed_conflicts_are_classified(self) -> None:
        history, _ = awaiting_history()
        original = batch_from_history(history, "batch_proposal")
        retry = validate_collaboration_candidate_batch(history, original)
        self.assertIsInstance(retry, CollaborationAppendReceipt)
        assert isinstance(retry, CollaborationAppendReceipt)
        self.assertIs(retry.status, CollaborationAppendStatus.ALREADY_COMMITTED)
        self.assertEqual(retry.first_sequence, 1)
        self.assertEqual(retry.last_sequence, len(history))
        self.assertEqual(
            retry.timestamps,
            tuple(event.timestamp for event in history),
        )

        conflicting_event = replace(original.events[0], event_id="different_event")
        batch_conflict = CollaborationEventBatch(
            original.batch_id,
            original.expected_last_sequence,
            (conflicting_event, *original.events[1:]),
        )
        _assert_invalid(
            self,
            history,
            batch_conflict,
            CollaborationAppendFailure.BATCH_ID_CONFLICT,
        )
        event_conflict = make_batch(
            history,
            "batch_event_conflict",
            _user_message_draft(
                'proposal_event',
                'Different event with a reused ID.',
            ),
        )
        _assert_invalid(
            self,
            history,
            event_conflict,
            CollaborationAppendFailure.EVENT_ID_CONFLICT,
        )
        stale = CollaborationEventBatch(
            "batch_stale",
            0,
            (
                _user_message_draft(
                    'fresh_event',
                    'Fresh but stale.',
                ),
            ),
        )
        _assert_invalid(
            self,
            history,
            stale,
            CollaborationAppendFailure.STALE_SEQUENCE,
        )
        corrupt_event = replace(history[0], batch_digest="sha256:" + "0" * 64)
        _assert_invalid(
            self,
            (corrupt_event, *history[1:]),
            make_batch(
                history,
                "batch_after_corruption",
                _user_message_draft(
                    'after_corruption',
                    'Never appended.',
                ),
            ),
            CollaborationAppendFailure.CORRUPT_HISTORY,
        )

    def test_all_required_co_batch_families_and_split_rejections(self) -> None:
        awaiting, proposal = awaiting_history()
        self.assertEqual(
            tuple(event.kind for event in awaiting),
            (
                CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                CollaborationEventKind.MESSAGE,
                CollaborationEventKind.MESSAGE,
                CollaborationEventKind.STEP_STATE,
            ),
        )
        confirmed, _ = confirmed_history(proposal)
        self.assertEqual(
            tuple(
                event.kind
                for event in confirmed
                if event.batch_id == "batch_confirmation"
            ),
            (CollaborationEventKind.CONFIRMATION, CollaborationEventKind.STEP_STATE),
        )
        running, _ = running_history(proposal)
        running_group = tuple(
            event for event in running if event.batch_id == "batch_running"
        )
        self.assertEqual(running_group[0].kind, CollaborationEventKind.STEP_STATE)
        self.assertEqual(
            sum(
                event.kind is CollaborationEventKind.ASSIGNMENT_DISPATCH
                for event in running_group
            ),
            len(proposal.assignments),
        )
        call_states = tuple(
            event.payload.state
            for event in running_group
            if type(event.payload) is CollaborationProviderCallPayload
        )
        self.assertEqual(call_states, ())
        self.assertEqual(
            tuple(
                event.payload.state
                for event in running
                if event.batch_id == "batch_calls"
                and type(event.payload) is CollaborationProviderCallPayload
            ),
            (
                CollaborationProviderCallState.PREPARED,
                CollaborationProviderCallState.SUBMITTED,
            )
            * 3,
        )
        executed, _, _ = executed_history(proposal)
        result_group = tuple(
            event for event in executed if event.batch_id == "batch_result"
        )
        self.assertIs(result_group[-1].kind, CollaborationEventKind.EXECUTION_RESULT)
        self.assertTrue(
            all(
                event.kind
                in {
                    CollaborationEventKind.SPECIALIST_REPLY,
                    CollaborationEventKind.LEAD_SYNTHESIS,
                    CollaborationEventKind.TERMINAL_OUTCOME,
                }
                for event in result_group[:-1]
            )
        )
        completion = _terminal_state_batch(
            executed,
            proposal,
            CollaborationStepState.COMPLETED,
        )
        self.assertEqual(
            tuple(event.kind for event in completion.events),
            (CollaborationEventKind.MESSAGE, CollaborationEventKind.STEP_STATE),
        )

        running_again, proposal = running_history(proposal)
        unknown = make_batch(
            running_again,
            "batch_unknown_contract",
            make_draft(
                "unknown_interruption_contract",
                CollaborationEventKind.INTERRUPTION,
                RUNTIME,
                USER,
                CollaborationInterruptionPayload(ATTEMPT_ID, "Unknown."),
            ),
            _state_draft(
                'unknown_state_contract',
                proposal,
                CollaborationStepState.OUTCOME_UNKNOWN,
                execution_attempt_id=ATTEMPT_ID,
            ),
        )
        self.assertIsInstance(
            validate_collaboration_candidate_batch(running_again, unknown),
            CollaborationHistoryProjection,
        )

        proposal_only = CollaborationEventBatch(
            "batch_proposal_only",
            0,
            (
                next(
                    event.to_draft()
                    for event in awaiting
                    if event.event_id == "proposal_event"
                ),
            ),
        )
        _assert_invalid(self, (), proposal_only)
        confirmation_only = CollaborationEventBatch(
            "batch_confirmation_only",
            len(awaiting),
            (
                next(
                    event.to_draft()
                    for event in confirmed
                    if event.event_id == "confirmation_event"
                ),
            ),
        )
        _assert_invalid(self, awaiting, confirmation_only)
        missing_dispatch = CollaborationEventBatch(
            "batch_missing_dispatch",
            len(confirmed),
            tuple(
                event.to_draft()
                for event in running[len(confirmed) :]
                if event.event_id != "dispatch_assignment_character"
            ),
        )
        _assert_invalid(self, confirmed, missing_dispatch)
        split_result = CollaborationEventBatch(
            "batch_split_result",
            len(running),
            (result_group[-1].to_draft(),),
        )
        _assert_invalid(self, running, split_result)
        state_without_notice = CollaborationEventBatch(
            "batch_state_without_notice",
            len(executed),
            (completion.events[-1],),
        )
        _assert_invalid(self, executed, state_without_notice)
        unknown_without_interruption = CollaborationEventBatch(
            "batch_unknown_without_interruption",
            len(running_again),
            (unknown.events[-1],),
        )
        _assert_invalid(self, running_again, unknown_without_interruption)


class CollaborationReadableProjectionTests(unittest.TestCase):
    def test_every_event_projects_once_with_complete_identity_and_closed_fields(
        self,
    ) -> None:
        history, proposal, _ = executed_history()
        records = project_collaboration_readable_history(history)

        self.assertEqual(len(records), len(history))
        self.assertEqual(
            tuple(record.sequence for record in records),
            tuple(range(1, len(history) + 1)),
        )
        for event, record in zip(history, records, strict=True):
            with self.subTest(event_id=event.event_id):
                self.assertEqual(
                    (
                        record.schema_version,
                        record.session_id,
                        record.conversation_id,
                        record.turn_id,
                        record.step_id,
                        record.event_id,
                        record.batch_id,
                        record.batch_ordinal,
                        record.batch_size,
                        record.batch_digest,
                        record.sequence,
                        record.timestamp,
                        record.sender,
                        record.recipient,
                        record.direction,
                        record.kind,
                        record.payload,
                    ),
                    (
                        event.schema_version,
                        event.session_id,
                        event.conversation_id,
                        event.turn_id,
                        event.step_id,
                        event.event_id,
                        event.batch_id,
                        event.batch_ordinal,
                        event.batch_size,
                        event.batch_digest,
                        event.sequence,
                        event.timestamp,
                        event.sender,
                        event.recipient,
                        event.direction,
                        event.kind,
                        event.payload,
                    ),
                )

        running, proposal = running_history()
        unknown = store_batch(
            running,
            make_batch(
                running,
                "batch_readable_interruption",
                make_draft(
                    "readable_interruption",
                    CollaborationEventKind.INTERRUPTION,
                    RUNTIME,
                    USER,
                    CollaborationInterruptionPayload(
                        ATTEMPT_ID,
                        "Visible interruption for inspection.",
                    ),
                ),
                _state_draft(
                    'readable_unknown_state',
                    proposal,
                    CollaborationStepState.OUTCOME_UNKNOWN,
                    execution_attempt_id=ATTEMPT_ID,
                ),
            ),
        )
        completed = store_batch(
            history,
            _terminal_state_batch(
                history,
                proposal,
                CollaborationStepState.COMPLETED,
            ),
        )
        all_records = (
            records
            + project_collaboration_readable_history(unknown)
            + project_collaboration_readable_history(completed)
        )
        failure_history = store_batch(
            (),
            make_batch(
                (),
                "batch_lead_failure",
                *provider_call_drafts(
                    invocation_id="invocation_failure",
                    call_id="call_failure",
                    role="lead_orchestrator",
                    recipient=LEAD,
                    invocation_kind=CollaborationInvocationKind.LEAD,
                ),
                make_draft(
                    "lead_failure",
                    CollaborationEventKind.LEAD_OUTCOME_FAILURE,
                    RUNTIME,
                    USER,
                    CollaborationLeadOutcomeFailurePayload(
                        CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                        "invocation_failure",
                        "call_failure",
                    ),
                ),
            ),
        )
        all_records += project_collaboration_readable_history(failure_history)
        memory = project_collaboration_readable_history(editorial_history())[0]
        observed_keys = {field.key for record in all_records + (memory,) for field in record.fields}
        artifact_keys = {
            CollaborationReadableFieldKey.LEAD_TASK_CONTROL,
            CollaborationReadableFieldKey.LEAD_SELECTED_MATERIAL,
            CollaborationReadableFieldKey.LEAD_DISCUSSION_TEXT,
            CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_TEXT,
            CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_STATUS,
            CollaborationReadableFieldKey.LEAD_PUBLIC_RESPONSE_DIAGNOSTICS,

            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_PURPOSE,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_INSTRUCTION,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_RESULT,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_IMPACT,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_DECISION,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_REVISION,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_STATE,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_SOURCES,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_TARGETS,
            CollaborationReadableFieldKey.LEAD_RECOMMENDATION_COST,
            CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_REQUEST,
            CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_ACTION,
            CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_NEXT_STEP,
            CollaborationReadableFieldKey.ARTIFACT_REVIEW_MESSAGE,
            CollaborationReadableFieldKey.ARTIFACT_REVIEW_DECISION,
            CollaborationReadableFieldKey.ARTIFACT_PROPOSAL_STATE,
            CollaborationReadableFieldKey.ARTIFACT_REVIEW_FAILURE,
        }
        self.assertEqual(
            observed_keys,
            set(CollaborationReadableFieldKey).difference(artifact_keys),
        )
        self.assertEqual(
            tuple(item.value for item in CollaborationReadableFieldKey),
            (
                'lead_task.control',
                'lead_task.selected_material',
                'lead_discussion.text',
                'lead_public_response.text',
                'lead_public_response.status',
                'lead_public_response.diagnostics',
                "message.content",
                "editorial.text",
                "editorial.reason",
                "editorial.transition_reason",
                "lead_judgment.kind",
                "lead_judgment.message",
                "lead_evidence.scope",
                "lead_evidence.status",
                "lead_outcome.failure",
                "specialist_reply.content",
                "specialist_reply.assessment",
                "lead_synthesis.message",
                "proposal.purpose",
                "proposal.user_impact",
                "proposal.assignment.instruction",
                "proposal.input.locator",
                "proposal.input.range",
                "proposal.call_usage",
                "proposal.assignment.call_limit",
                "proposal.synthesis.call_limit",
                "dispatch.instruction",
                "dispatch.call_limit",
                "interruption.content",
                "step.state",
                "confirmation.decision",
                "execution.outcome",
                "execution.assignment.outcome",
                "execution.assignment.call_usage",
                "execution.synthesis.outcome",
                "execution.synthesis.call_usage",
                "terminal.scope",
                "terminal.outcome",
                "terminal.call_usage",
                "artifact_review.message",
                "artifact_review.decision",
                "artifact_proposal.state",
                "artifact_review.failure",
                "artifact_objective.request",
                "artifact_objective.action",
                "artifact_objective.next_step",
                "lead_recommendation.purpose",
                "lead_recommendation.instruction",
                "lead_recommendation.result",
                "lead_recommendation.impact",
                "lead_recommendation.decision",
                "lead_recommendation.revision",
                "lead_recommendation.state",
                "lead_recommendation.sources",
                "lead_recommendation.targets",
                "lead_recommendation.cost",
            ),
        )
        searchable_names = " ".join(item.value for item in observed_keys)
        for forbidden in (
            "authority",
            "capability",
            "diagnostic",
            "private",
            "provider_session",
            "revision_digest",
        ):
            self.assertNotIn(forbidden, searchable_names)

        self.assertEqual({field.key.value: field.text for field in memory.fields},
                         {"editorial.text": memory.payload.record.draft.text, "editorial.reason": memory.payload.record.draft.reason, "editorial.transition_reason": memory.payload.record.request.transition_reason})

    def test_default_context_is_content_free_and_selection_is_bounded_additive(
        self,
    ) -> None:
        history, _, _ = executed_history()
        empty = select_bounded_collaboration_context(history)

        self.assertEqual(empty.records, ())
        self.assertEqual(empty.requested_records, 0)
        self.assertEqual(empty.bootstrap.total_records, len(history))
        self.assertEqual(empty.bootstrap.last_sequence, len(history))
        self.assertEqual(empty.bootstrap.tail_batch_digest, history[-1].batch_digest)
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(empty.bootstrap)),
            (
                "schema_version",
                "session_id",
                "conversation_id",
                "total_records",
                "last_sequence",
                "tail_batch_digest",
            ),
        )
        self.assertNotIn(
            "current_input",
            inspect.signature(select_bounded_collaboration_context).parameters,
        )

        event_ids = (history[-1].event_id, history[0].event_id)
        selected = select_bounded_collaboration_context(
            history,
            event_ids=event_ids,
            policy=CollaborationReadableContextPolicy(
                max_records=1,
                max_utf8_bytes=COLLABORATION_READABLE_CONTEXT_POLICY.max_utf8_bytes,
            ),
        )
        self.assertEqual(tuple(item.sequence for item in selected.records), (1,))
        self.assertEqual(selected.requested_records, 2)
        self.assertEqual(selected.retained_records, 1)
        self.assertEqual(selected.omitted_records, 1)
        self.assertTrue(selected.truncated)

        for event_ids in ((history[0].event_id,) * 2, ("unknown_event",)):
            with self.subTest(event_ids=event_ids):
                with self.assertRaises(ValueError):
                    select_bounded_collaboration_context(
                        history,
                        event_ids=event_ids,
                    )

    def test_readable_projection_rejects_v1_and_every_corrupt_history_shape(
        self,
    ) -> None:
        legacy = _legacy_rejection_input_fixture()
        with self.assertRaises(TypeError):
            project_collaboration_readable_history((legacy,))  # type: ignore[arg-type]

        history, _, _ = executed_history()
        corruptions = (
            (replace(history[0], sequence=2), *history[1:]),
            (*history[:-1], replace(history[-1], event_id=history[0].event_id)),
            (*history[:-1], replace(history[-1], batch_digest="sha256:" + "0" * 64)),
        )
        for corrupted in corruptions:
            with self.subTest(last_event=corrupted[-1].event_id):
                with self.assertRaises(ValueError):
                    project_collaboration_readable_history(corrupted)
        populated = next(
            record
            for record in project_collaboration_readable_history(history)
            if record.fields
        )
        with self.assertRaises(ValueError):
            replace(populated, fields=())


class CollaborationPortAndLegacyIsolationTests(unittest.TestCase):
    def test_unused_store_protocol_has_exact_methods_and_typing(self) -> None:
        self.assertTrue(getattr(CollaborationEventStore, "_is_protocol", False))
        methods = {
            name
            for name, member in vars(CollaborationEventStore).items()
            if not name.startswith("_") and callable(member)
        }
        self.assertEqual(methods, {"append_batch", "read", "read_context"})
        self.assertEqual(
            tuple(inspect.signature(CollaborationEventStore.append_batch).parameters),
            ("self", "batch"),
        )
        self.assertEqual(
            get_type_hints(CollaborationEventStore.append_batch)["return"],
            CollaborationAppendReceipt,
        )
        self.assertEqual(
            get_type_hints(CollaborationEventStore.read)["return"],
            tuple[CollaborationEvent, ...],
        )
        self.assertIs(
            get_type_hints(CollaborationEventStore.read_context)["return"],
            CollaborationHistoryProjection,
        )

    def test_schema_v1_values_are_rejected_without_migration_or_union(self) -> None:
        legacy = _legacy_rejection_input_fixture()
        with self.assertRaises(TypeError):
            project_collaboration_history((legacy,))  # type: ignore[arg-type]
        for forbidden in ("migrate", "convert", "upgrade", "legacy"):
            self.assertFalse(
                any(
                    forbidden in name.lower()
                    for name in (
                        *CollaborationEventStore.__dict__,
                        *CollaborationHistoryProjection.__dict__,
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
