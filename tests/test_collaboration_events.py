"""Immutable schema-v2 collaboration values and canonical wire contracts."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import pickle
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import story_writing_agents
import story_writing_agents.conversations as conversations
import story_writing_agents.conversations.context as context_owner
import story_writing_agents.conversations.events as events_owner
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.artifact_proposals import artifact_review_specialists
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    ArtifactProposalState,
    ArtifactReviewDecision,
    ArtifactReviewFailureCode,
    ArtifactReviewReply,
    ArtifactReviewedProposal,
    CollaborationActorKind,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcome,
    CollaborationAssignmentOutcomeKind,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationDirection,
    CollaborationEvidenceAcquisition,
    CollaborationEventKind,
    CollaborationExecutionResult,
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationInvocationKind,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
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
    LeadAssignmentIntent,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadProposalIntent,
    LeadProposalRelation,
    LeadProviderOutcome,
    collaboration_batch_digest,
    collaboration_proposal_digest,
    decode_artifact_reviewed_proposal,
    decode_collaboration_batch,
    decode_collaboration_event,
    decode_collaboration_event_draft,
    decode_collaboration_proposal,
    encode_collaboration_batch,
    encode_artifact_reviewed_proposal,
    encode_collaboration_event,
    encode_collaboration_event_draft,
    encode_collaboration_proposal,
)

from tests.collaboration_contract_fixture import (
    ARTIFACT,
    ATTEMPT_ID,
    CHARACTER,
    COLLABORATION_CONTEXT_EXPORTS,
    COLLABORATION_EVENT_EXPORTS,
    DIGEST_A,
    DIGEST_B,
    GIT,
    LEAD,
    PLOT,
    REQUEST_EVENT_ID,
    REQUEST_ID,
    RUNTIME,
    USER,
    awaiting_history,
    batch_from_history,
    executed_history,
    make_draft,
    make_lead_evidence,
    make_proposal,
    replace_proposal,
    result_batch,
    running_history,
)
from tests.test_artifact_proposals import _combined_candidate


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _exception_graph(exception: BaseException) -> tuple[BaseException, ...]:
    pending = [exception]
    observed: list[BaseException] = []
    identities: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in identities:
            continue
        identities.add(id(current))
        observed.append(current)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return tuple(observed)


def _reviewed_artifact_proposal() -> ArtifactReviewedProposal:
    _base, candidate, _projected = _combined_candidate()
    mandatory = artifact_review_specialists(candidate.mandatory_review_domains)
    disclosed = artifact_review_specialists(candidate.disclosed_review_domains)
    return ArtifactReviewedProposal.create(
        proposal_id="reviewed_artifact",
        proposal_revision=1,
        candidate=candidate,
        session_id="session_a",
        conversation_id="conversation_a",
        turn_id="turn_a",
        step_id="step_a",
        base_tail_sequence=1,
        reviewed_tail_sequence=2,
        lead_judgment_event_id="artifact_judgment",
        synthesis_event_id="artifact_synthesis",
        consultation_proposal_id="artifact_consultation",
        consultation_proposal_revision=1,
        consultation_proposal_digest=DIGEST_A,
        mandatory_specialist_ids=mandatory,
        disclosed_specialist_ids=disclosed,
        reviews=tuple(
            ArtifactReviewReply(
                specialist_id=specialist_id,
                assignment_id=f"assignment_{ordinal}",
                reply_event_id=f"artifact_reply_{ordinal}",
                assessment=CollaborationSpecialistAssessment.SUPPORTS,
            )
            for ordinal, specialist_id in enumerate(disclosed, start=1)
        ),
        unresolved_input_ids=(),
        rationale="The exact specialist set supports the candidate.",
        user_impact="The proposal remains review-only.",
        limitations="No Apply, artifact-write, or Git authority is granted.",
        grounding_evidence_ids=candidate.grounding_evidence_ids,
    )


class CollaborationValueContractTests(unittest.TestCase):
    def test_constants_and_closed_enums_are_exact(self) -> None:
        self.assertEqual(COLLABORATION_EVENT_SCHEMA_VERSION, 2)
        self.assertEqual(COLLABORATION_ROUND_LIMIT, 2)
        self.assertEqual(COLLABORATION_PHYSICAL_CALL_LIMIT, 12)
        self.assertEqual(COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES, 32_768)
        expected = {
            CollaborationEventKind: (
                "message",
                "assignment_dispatch",
                "specialist_reply",
                "lead_synthesis",
                "step_state",
                "confirmation",
                "execution_result",
                "interruption",
                "terminal_outcome",
                "provider_call_accounting",
                "lead_outcome_failure",
                "artifact_review_candidate",
                "artifact_review_synthesis",
                "artifact_reviewed_proposal",
                "artifact_proposal_state",
                "artifact_review_failure",
                "artifact_application_request",
                "artifact_application_decision",
                "artifact_application_result",
                "story_commit_request",
                "story_commit_decision",
                "story_commit_result",
            ),
            CollaborationActorKind: (
                "user",
                "lead",
                "specialist",
                "runtime",
                "artifact",
                "git",
            ),
            CollaborationDirection: (
                "user_to_lead",
                "user_to_runtime",
                "lead_to_user",
                "lead_to_specialist",
                "specialist_to_lead",
                "specialist_to_user",
                "runtime_to_user",
                "runtime_to_lead",
                "runtime_to_specialist",
                "artifact_to_user",
                "artifact_to_lead",
                "git_to_user",
                "git_to_lead",
            ),
            CollaborationStepKind: (
                "lead_specialist_consultation",
                "specialist_advisory",
                "artifact_mutation",
                "git_operation",
            ),
            CollaborationSideEffect: (
                "provider_invocation",
                "artifact_mutation",
                "git_index_mutation",
                "git_commit",
                "git_remote_mutation",
                "git_merge",
            ),
            CollaborationInputKind: (
                "user_input",
                "conversation_event",
                "core_document",
                "manuscript",
                "agent_state",
                "writing_artifact",
                "artifact_candidate",
            ),
            CollaborationInputState: ("present", "absent"),
            CollaborationStepState: (
                "awaiting_confirmation",
                "reconfirmation_required",
                "confirmed",
                "running",
                "completed",
                "rejected",
                "superseded",
                "interrupted",
                "failed",
                "outcome_unknown",
                "recovery_required",
            ),
            CollaborationConfirmationDecision: ("confirm", "reject", "revise"),
            CollaborationAssignmentOutcomeKind: (
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationSynthesisOutcomeKind: (
                "not_attempted",
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationExecutionOutcomeKind: (
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationTerminalScope: ("assignment", "synthesis"),
            CollaborationInvocationKind: (
                "lead",
                "specialist_assignment",
                "lead_synthesis",
                "specialist_advisory",
            ),
            CollaborationProviderCallKind: (
                "reference_prelude",
                "initial",
                "continuation",
            ),
            CollaborationProviderCallState: ("prepared", "submitted"),
            LeadOutcomeKind: (
                "answer",
                "question",
                "critique",
                "recommendation",
                "proposal",
            ),
            LeadOutcomeScope: ("story_specific", "workflow_meta"),
            LeadEvidenceStatus: (
                "not_applicable",
                "verified",
                "missing",
                "empty",
                "incomplete",
                "conflicting",
            ),
            CollaborationEvidenceAcquisition: (
                "attached_reference",
                "core_status",
                "zero_hit_search",
                "read",
            ),
            CollaborationLeadOutcomeFailureCode: (
                "evidence_validation_rejected",
                "artifact_intent_rejected",
            ),
            ArtifactProposalState: (
                "awaiting_consultation_confirmation",
                "reviewing",
                "revision_required",
                "rejected",
                "reviewed",
                "superseded",
                "stale",
                "failed",
                "recovery_required",
            ),
            ArtifactReviewDecision: ("propose", "revise", "reject"),
            ArtifactReviewFailureCode: (
                "intent_rejected",
                "evidence_stale",
                "candidate_stale",
                "review_incomplete",
                "synthesis_rejected",
            ),
            LeadProposalRelation: (
                "new_step",
                "revise_pending",
                "continue_lineage",
            ),
            CollaborationAppendStatus: ("APPENDED", "ALREADY_COMMITTED"),
            CollaborationAppendFailure: (
                "stale_sequence",
                "batch_id_conflict",
                "event_id_conflict",
                "invalid_transition",
                "corrupt_history",
            ),
        }
        for enum_type, values in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)

    def test_all_schema_values_have_exact_fields_frozen_slots_and_nested_tuples(
        self,
    ) -> None:
        history, proposal, result = executed_history()
        batch = batch_from_history(history, "batch_result")
        lead_evidence = make_lead_evidence()
        evidence_receipt = lead_evidence.receipts[0]
        lead_judgment = CollaborationLeadJudgmentPayload(
            LeadOutcomeKind.RECOMMENDATION,
            "Review the referenced outline.",
            lead_evidence,
        )
        lead_failure = CollaborationLeadOutcomeFailurePayload(
            CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
            "invocation_a",
            "call_a",
        )
        lead_assignment = LeadAssignmentIntent(
            PLOT_ARCHITECT,
            "Review the outline causality.",
            ("input_outline",),
        )
        lead_intent = LeadProposalIntent(
            LeadProposalRelation.NEW_STEP,
            "Check outline causality.",
            (lead_assignment,),
        )
        lead_outcome = LeadProviderOutcome(
            LeadOutcomeKind.PROPOSAL,
            "A plot review would resolve the open question.",
            LeadOutcomeScope.STORY_SPECIFIC,
            LeadEvidenceStatus.VERIFIED,
            ("input_outline",),
            lead_intent,
        )
        provider_call = CollaborationProviderCallPayload(
            "invocation_a",
            "call_a",
            1,
            LEAD_ORCHESTRATOR,
            CollaborationInvocationKind.LEAD,
            CollaborationProviderCallKind.INITIAL,
            CollaborationProviderCallState.SUBMITTED,
        )
        specialist_reply = next(
            event.payload
            for event in history
            if type(event.payload) is CollaborationSpecialistReplyPayload
        )
        lead_synthesis = next(
            event.payload
            for event in history
            if type(event.payload) is CollaborationLeadSynthesisPayload
        )
        values_and_fields = (
            (USER, ("kind", "agent_id")),
            (
                evidence_receipt,
                (
                    "evidence_id",
                    "acquisition",
                    "operation_id",
                    "result_correlation_id",
                    "manifest_digest",
                    "source_id",
                    "source_kind",
                    "locator",
                    "source_state",
                    "revision_digest",
                    "content_digest",
                    "utf8_bytes",
                    "boundary_kind",
                    "boundary_start",
                    "boundary_end",
                    "start_line",
                    "end_line",
                    "ordinal",
                    "action_index",
                    "origin_action_index",
                    "query_digest",
                    "query_count",
                    "authorized_source_ids",
                    "authorized_source_kinds",
                    "authorized_source_fingerprint",
                    "artifact_path_prefix",
                ),
            ),
            (lead_evidence, ("scope", "status", "receipts")),
            (lead_judgment, ("kind", "message", "evidence")),
            (lead_failure, ("code", "invocation_id", "call_id")),
            (
                proposal.basis.inputs[0],
                (
                    "input_id",
                    "kind",
                    "locator",
                    "state",
                    "revision_digest",
                    "start_line",
                    "end_line",
                ),
            ),
            (
                proposal.basis.agent_profiles[0],
                (
                    "agent_id",
                    "definition_fingerprint",
                    "execution_fingerprint",
                ),
            ),
            (
                proposal.basis,
                (
                    "route",
                    "route_fingerprint",
                    "agent_profiles",
                    "context_policy_fingerprint",
                    "budget_policy_fingerprint",
                    "inputs",
                    "evidence",
                ),
            ),
            (
                proposal.assignments[0],
                (
                    "assignment_id",
                    "specialist_id",
                    "round",
                    "instruction",
                    "input_ids",
                    "max_physical_calls",
                ),
            ),
            (
                proposal.budget,
                (
                    "round_limit",
                    "physical_call_limit",
                    "rounds_spent",
                    "rounds_added",
                    "physical_calls_spent",
                    "max_additional_physical_calls",
                    "synthesis_max_physical_calls",
                ),
            ),
            (
                proposal,
                (
                    "step_id",
                    "proposal_id",
                    "proposal_revision",
                    "step_kind",
                    "purpose",
                    "assignments",
                    "user_impact",
                    "side_effects",
                    "budget",
                    "basis",
                    "proposal_digest",
                ),
            ),
            (
                result.assignment_outcomes[0],
                (
                    "assignment_id",
                    "outcome",
                    "submitted_physical_calls",
                    "reply_event_id",
                ),
            ),
            (
                result,
                (
                    "proposal_id",
                    "proposal_revision",
                    "proposal_digest",
                    "execution_attempt_id",
                    "assignment_outcomes",
                    "synthesis_outcome",
                    "synthesis_submitted_physical_calls",
                    "synthesis_result_event_id",
                    "overall_outcome",
                    "visible_exchange_event_ids",
                ),
            ),
            (
                batch.events[0],
                (
                    "schema_version",
                    "event_id",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "step_id",
                    "kind",
                    "sender",
                    "recipient",
                    "payload",
                ),
            ),
            (
                history[-1],
                (
                    "schema_version",
                    "event_id",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "step_id",
                    "sequence",
                    "timestamp",
                    "batch_id",
                    "batch_ordinal",
                    "batch_size",
                    "batch_digest",
                    "kind",
                    "sender",
                    "recipient",
                    "payload",
                ),
            ),
            (batch, ("batch_id", "expected_last_sequence", "events")),
            (
                lead_assignment,
                ("agent_id", "instruction", "input_ids"),
            ),
            (
                lead_intent,
                ("relation", "purpose", "assignments"),
            ),
            (
                lead_outcome,
                (
                    "kind",
                    "message",
                    "scope",
                    "evidence_status",
                    "evidence_ids",
                    "proposal",
                    "artifact_intent",
                ),
            ),
            (
                specialist_reply,
                (
                    "proposal_id",
                    "proposal_revision",
                    "proposal_digest",
                    "execution_attempt_id",
                    "assignment_id",
                    "dispatch_event_id",
                    "assessment",
                    "message",
                    "input_ids",
                ),
            ),
            (
                lead_synthesis,
                (
                    "proposal_id",
                    "proposal_revision",
                    "proposal_digest",
                    "execution_attempt_id",
                    "message",
                    "reply_event_ids",
                    "unresolved_input_ids",
                    "evidence",
                    "next_intent",
                ),
            ),
            (
                provider_call,
                (
                    "invocation_id",
                    "call_id",
                    "ordinal",
                    "role",
                    "invocation_kind",
                    "call_kind",
                    "state",
                ),
            ),
        )
        for value, expected_fields in values_and_fields:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(value)),
                    expected_fields,
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, expected_fields[0], None)
        self.assertEqual(
            proposal.participants,
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        )
        self.assertNotIn("participants", proposal.__dataclass_fields__)
        for nested in (
            proposal.assignments,
            proposal.side_effects,
            proposal.basis.agent_profiles,
            proposal.basis.inputs,
            result.assignment_outcomes,
            result.visible_exchange_event_ids,
            batch.events,
            lead_assignment.input_ids,
            lead_intent.assignments,
            specialist_reply.input_ids,
            lead_synthesis.reply_event_ids,
            lead_synthesis.unresolved_input_ids,
        ):
            self.assertIs(type(nested), tuple)

    def test_actor_direction_matrix_is_closed_and_exact(self) -> None:
        advisory = make_proposal(step_kind=CollaborationStepKind.SPECIALIST_ADVISORY)
        running, _ = running_history(advisory)
        result_candidate, result = result_batch(running, advisory)
        del result_candidate
        assignment = advisory.assignments[0]
        payloads = {
            CollaborationEventKind.MESSAGE: CollaborationMessagePayload("Visible."),
            CollaborationEventKind.ASSIGNMENT_DISPATCH: (
                CollaborationAssignmentDispatchPayload(
                    advisory.proposal_id,
                    advisory.proposal_revision,
                    advisory.proposal_digest,
                    ATTEMPT_ID,
                    assignment,
                )
            ),
            CollaborationEventKind.SPECIALIST_REPLY: (
                CollaborationSpecialistReplyPayload(
                    advisory.proposal_id,
                    advisory.proposal_revision,
                    advisory.proposal_digest,
                    ATTEMPT_ID,
                    assignment.assignment_id,
                    f"dispatch_{assignment.assignment_id}",
                    CollaborationSpecialistAssessment.SUPPORTS,
                    "Visible reply.",
                    assignment.input_ids,
                )
            ),
            CollaborationEventKind.LEAD_SYNTHESIS: CollaborationLeadSynthesisPayload(
                advisory.proposal_id,
                advisory.proposal_revision,
                advisory.proposal_digest,
                ATTEMPT_ID,
                "Visible synthesis.",
                ("reply_event",),
                (),
                make_lead_evidence(),
                None,
            ),
            CollaborationEventKind.STEP_STATE: CollaborationStepStatePayload(
                CollaborationStepState.AWAITING_CONFIRMATION,
                advisory.proposal_id,
                advisory.proposal_revision,
                advisory.proposal_digest,
                confirmation_request_id=REQUEST_ID,
            ),
            CollaborationEventKind.CONFIRMATION: CollaborationConfirmationPayload(
                REQUEST_ID,
                REQUEST_EVENT_ID,
                advisory.proposal_id,
                advisory.proposal_revision,
                advisory.proposal_digest,
                CollaborationConfirmationDecision.CONFIRM,
            ),
            CollaborationEventKind.EXECUTION_RESULT: result,
            CollaborationEventKind.INTERRUPTION: CollaborationInterruptionPayload(
                ATTEMPT_ID,
                "Interrupted.",
            ),
            CollaborationEventKind.TERMINAL_OUTCOME: (
                CollaborationTerminalOutcomePayload(
                    ATTEMPT_ID,
                    CollaborationTerminalScope.ASSIGNMENT,
                    CollaborationAssignmentOutcomeKind.SUCCEEDED,
                    1,
                    assignment.assignment_id,
                )
            ),
            CollaborationEventKind.LEAD_OUTCOME_FAILURE: (
                CollaborationLeadOutcomeFailurePayload(
                    CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                    "invocation_a",
                    "call_a",
                )
            ),
        }
        actors = {
            CollaborationActorKind.USER: USER,
            CollaborationActorKind.LEAD: LEAD,
            CollaborationActorKind.SPECIALIST: PLOT,
            CollaborationActorKind.RUNTIME: RUNTIME,
            CollaborationActorKind.ARTIFACT: ARTIFACT,
            CollaborationActorKind.GIT: GIT,
        }
        allowed = {
            CollaborationEventKind.MESSAGE: {
                (CollaborationActorKind.USER, CollaborationActorKind.LEAD),
                (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
                (CollaborationActorKind.ARTIFACT, CollaborationActorKind.LEAD),
                (CollaborationActorKind.GIT, CollaborationActorKind.USER),
                (CollaborationActorKind.GIT, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.ASSIGNMENT_DISPATCH: {
                (CollaborationActorKind.LEAD, CollaborationActorKind.SPECIALIST),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
            },
            CollaborationEventKind.SPECIALIST_REPLY: {
                (CollaborationActorKind.SPECIALIST, CollaborationActorKind.LEAD),
                (CollaborationActorKind.SPECIALIST, CollaborationActorKind.USER),
            },
            CollaborationEventKind.LEAD_SYNTHESIS: {
                (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
            },
            CollaborationEventKind.STEP_STATE: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.CONFIRMATION: {
                (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
            },
            CollaborationEventKind.EXECUTION_RESULT: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.INTERRUPTION: {
                (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            },
            CollaborationEventKind.TERMINAL_OUTCOME: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.LEAD_OUTCOME_FAILURE: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            },
        }
        for kind, payload in payloads.items():
            for sender_kind, sender in actors.items():
                for recipient_kind, recipient in actors.items():
                    pair = (sender_kind, recipient_kind)
                    with self.subTest(kind=kind.value, pair=pair):
                        if pair in allowed[kind]:
                            event = make_draft(
                                "event_a",
                                kind,
                                sender,
                                recipient,
                                payload,
                            )
                            self.assertEqual(
                                event.direction.value,
                                f"{sender_kind.value}_to_{recipient_kind.value}",
                            )
                        else:
                            with self.assertRaises(ValueError):
                                make_draft(
                                    "event_a",
                                    kind,
                                    sender,
                                    recipient,
                                    payload,
                                )

    def test_proposal_closure_budget_and_advisory_invariants_fail_closed(self) -> None:
        proposal = make_proposal()
        with self.assertRaises(ValueError):
            make_proposal(assignments=(proposal.assignments[0],) * 2)
        with self.assertRaises(ValueError):
            make_proposal(
                assignments=(
                    replace(proposal.assignments[0], input_ids=("unknown",)),
                    proposal.assignments[1],
                )
            )
        with self.assertRaises(ValueError):
            make_proposal(profile_ids=(PLOT_ARCHITECT, CHARACTER_CUSTODIAN))
        with self.assertRaises(ValueError):
            make_proposal(profile_ids=(PLOT_ARCHITECT, LEAD_ORCHESTRATOR))
        with self.assertRaises(ValueError):
            CollaborationBudget(2, 12, 2, 1, 0, 1, 0)
        with self.assertRaises(ValueError):
            CollaborationBudget(2, 12, 0, 1, 12, 1, 0)
        advisory = make_proposal(step_kind=CollaborationStepKind.SPECIALIST_ADVISORY)
        self.assertEqual(advisory.budget.synthesis_max_physical_calls, 0)
        self.assertNotIn(
            LEAD_ORCHESTRATOR,
            tuple(profile.agent_id for profile in advisory.basis.agent_profiles),
        )
        with self.assertRaises(ValueError):
            make_proposal(
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                profile_ids=(PLOT_ARCHITECT, LEAD_ORCHESTRATOR),
            )
        with self.assertRaises(ValueError):
            make_proposal(
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                side_effects=(CollaborationSideEffect.ARTIFACT_MUTATION,),
            )
        dispatch = CollaborationAssignmentDispatchPayload(
            advisory.proposal_id,
            advisory.proposal_revision,
            advisory.proposal_digest,
            ATTEMPT_ID,
            advisory.assignments[0],
        )
        with self.assertRaises(ValueError):
            make_draft(
                "dispatch",
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                RUNTIME,
                CHARACTER,
                dispatch,
            )

    def test_result_value_requires_exact_terminal_facts(self) -> None:
        _, _, result = executed_history()
        successful = result.assignment_outcomes[0]
        with self.assertRaises(ValueError):
            replace(successful, reply_event_id=None)
        with self.assertRaises(ValueError):
            CollaborationAssignmentOutcome(
                successful.assignment_id,
                CollaborationAssignmentOutcomeKind.FAILED,
                1,
                successful.reply_event_id,
            )
        with self.assertRaises(ValueError):
            replace(
                result,
                assignment_outcomes=(result.assignment_outcomes[0],) * 2,
            )
        with self.assertRaises(ValueError):
            replace(
                result,
                synthesis_outcome=CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
                synthesis_submitted_physical_calls=1,
                synthesis_result_event_id=None,
            )
        failed = CollaborationAssignmentOutcome(
            result.assignment_outcomes[0].assignment_id,
            CollaborationAssignmentOutcomeKind.FAILED,
            1,
            None,
        )
        with self.assertRaises(ValueError):
            replace(
                result,
                assignment_outcomes=(failed, result.assignment_outcomes[1]),
                overall_outcome=CollaborationExecutionOutcomeKind.SUCCEEDED,
            )

    def test_visible_text_uses_strict_utf8_byte_bounds_and_contains_no_metadata(
        self,
    ) -> None:
        exact = "é" * (COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES // 2)
        self.assertEqual(CollaborationMessagePayload(exact).content, exact)
        for invalid in (exact + "é", "\ud800", ""):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(ValueError):
                    CollaborationMessagePayload(invalid)
        proposal = make_proposal()
        self.assertEqual(
            set(proposal.__dataclass_fields__),
            {
                "step_id",
                "proposal_id",
                "proposal_revision",
                "step_kind",
                "purpose",
                "assignments",
                "user_impact",
                "side_effects",
                "budget",
                "basis",
                "proposal_digest",
            },
        )
        wire = encode_collaboration_proposal(proposal)
        for forbidden in (
            b"hidden_reasoning",
            b"chain_of_thought",
            b"private_metadata",
            b"authorization_capability",
        ):
            self.assertNotIn(forbidden, wire)
        self.assertIn(b"${core_documents/outline.md}", wire)
        self.assertNotIn(b"actual outline file bytes", wire)


class CollaborationWireContractTests(unittest.TestCase):
    def test_all_wire_values_have_stable_golden_hashes_and_round_trip(self) -> None:
        history, proposal = awaiting_history()
        batch = batch_from_history(history, "batch_proposal")
        cases = (
            (
                encode_collaboration_proposal,
                decode_collaboration_proposal,
                proposal,
                3482,
                "c2f7995f1c70516cd7ce2e7efeff5ac4e28af9b59e02b49339451e10b16e328e",
            ),
            (
                encode_collaboration_event_draft,
                decode_collaboration_event_draft,
                batch.events[0],
                491,
                "e7d231c3b5bffd2414c8a45833cfb4465723e0c95ab9c5d32c88b95deb1de1a8",
            ),
            (
                encode_collaboration_event,
                decode_collaboration_event,
                history[0],
                694,
                "4c4c7516957e8d26f2868d7e5235b6647bd15d2e7a68c8d1e65deb371a4834c5",
            ),
            (
                encode_collaboration_batch,
                decode_collaboration_batch,
                batch,
                6735,
                "3e3a1c1c77a8f534b0b18575320a7eb7994632c47939fc476ac79651f30dd911",
            ),
        )
        for encoder, decoder, value, size, digest in cases:
            with self.subTest(encoder=encoder.__name__):
                wire = encoder(value)  # type: ignore[arg-type]
                self.assertEqual(len(wire), size)
                self.assertEqual(hashlib.sha256(wire).hexdigest(), digest)
                self.assertEqual(decoder(wire), value)
                self.assertEqual(encoder(decoder(wire)), wire)
                self.assertFalse(wire.startswith(b"\xef\xbb\xbf"))
                self.assertFalse(wire.endswith(b"\n"))
                self.assertEqual(_canonical_json(json.loads(wire)), wire)
        self.assertEqual(
            collaboration_proposal_digest(proposal), proposal.proposal_digest
        )
        self.assertEqual(collaboration_batch_digest(batch), batch.batch_digest)

    def test_artifact_public_decoders_clear_content_from_exception_graphs(
        self,
    ) -> None:
        reviewed = _reviewed_artifact_proposal()
        reviewed_value = json.loads(encode_artifact_reviewed_proposal(reviewed))
        private_assessment = "PRIVATE_REVIEW_달빛"
        reviewed_value["reviews"][0]["assessment"] = private_assessment

        base_event = awaiting_history()[0][0]
        artifact_event = replace(
            base_event,
            kind=CollaborationEventKind.ARTIFACT_REVIEWED_PROPOSAL,
            sender=RUNTIME,
            recipient=USER,
            payload=reviewed,
        )
        event_value = json.loads(encode_collaboration_event(artifact_event))
        private_event_assessment = "PRIVATE_EVENT_REVIEW_별빛"
        event_value["payload"]["proposal"]["reviews"][0]["assessment"] = (
            private_event_assessment
        )
        private_timestamp = "PRIVATE_TIMESTAMP_은하"
        timestamp_value = json.loads(encode_collaboration_event(artifact_event))
        timestamp_value["timestamp"] = private_timestamp

        cases = (
            (
                decode_artifact_reviewed_proposal,
                b"\xffPRIVATE_REVIEW_UTF8_BYTES",
                "collaboration payload is not strict UTF-8 JSON",
                "PRIVATE_REVIEW_UTF8_BYTES",
            ),
            (
                decode_artifact_reviewed_proposal,
                b'{"PRIVATE_REVIEW_JSON_TEXT":',
                "collaboration payload is not strict UTF-8 JSON",
                "PRIVATE_REVIEW_JSON_TEXT",
            ),
            (
                decode_artifact_reviewed_proposal,
                _canonical_json(reviewed_value),
                "artifact review assessment is not supported",
                private_assessment,
            ),
            (
                decode_collaboration_event,
                b"\xffPRIVATE_EVENT_UTF8_BYTES",
                "collaboration payload is not strict UTF-8 JSON",
                "PRIVATE_EVENT_UTF8_BYTES",
            ),
            (
                decode_collaboration_event,
                b'{"PRIVATE_EVENT_JSON_TEXT":',
                "collaboration payload is not strict UTF-8 JSON",
                "PRIVATE_EVENT_JSON_TEXT",
            ),
            (
                decode_collaboration_event,
                _canonical_json(event_value),
                "artifact review assessment is not supported",
                private_event_assessment,
            ),
            (
                decode_collaboration_event,
                _canonical_json(timestamp_value),
                "collaboration timestamp must be an ISO-8601 timestamp",
                private_timestamp,
            ),
        )
        for decoder, payload, expected_message, private_content in cases:
            with self.subTest(
                decoder=decoder.__name__,
                expected_message=expected_message,
            ):
                with self.assertRaises(ValueError) as raised:
                    decoder(payload)
                failure = raised.exception
                self.assertIs(type(failure), ValueError)
                self.assertEqual(failure.args, (expected_message,))
                self.assertEqual(str(failure), expected_message)
                graph = _exception_graph(failure)
                self.assertEqual(graph, (failure,))
                for linked in graph:
                    self.assertIsNone(linked.__cause__)
                    self.assertIsNone(linked.__context__)
                    self.assertNotIn(private_content, str(linked))
                    self.assertNotIn(private_content, repr(linked))
                    self.assertNotIn(private_content, repr(linked.args))

    def test_generic_execution_result_event_still_decodes_exactly(self) -> None:
        history, _proposal, expected = executed_history()
        event = next(
            item
            for item in history
            if item.kind is CollaborationEventKind.EXECUTION_RESULT
        )
        decoded = decode_collaboration_event(encode_collaboration_event(event))
        self.assertEqual(decoded, event)
        self.assertIs(type(decoded.payload), CollaborationExecutionResult)
        self.assertEqual(decoded.payload, expected)

    def test_every_proposal_dimension_and_order_changes_the_digest(self) -> None:
        base = make_proposal(
            side_effects=(
                CollaborationSideEffect.PROVIDER_INVOCATION,
                CollaborationSideEffect.ARTIFACT_MUTATION,
            )
        )
        inputs = base.basis.inputs
        assignments = base.assignments
        variants = {
            "step_id": replace_proposal(base, step_id="step_b"),
            "proposal_id": replace_proposal(base, proposal_id="proposal_b"),
            "revision": replace_proposal(base, proposal_revision=2),
            "step_kind": replace_proposal(
                base,
                step_kind=CollaborationStepKind.GIT_OPERATION,
            ),
            "purpose": replace_proposal(base, purpose="Different visible purpose."),
            "assignment_order": replace_proposal(
                base,
                assignments=tuple(reversed(assignments)),
            ),
            "assignment_instruction": replace_proposal(
                base,
                assignments=(
                    replace(assignments[0], instruction="Different instruction."),
                    assignments[1],
                ),
            ),
            "assignment_calls": make_proposal(
                assignments=(
                    replace(assignments[0], max_physical_calls=3),
                    assignments[1],
                ),
                inputs=inputs,
                side_effects=base.side_effects,
            ),
            "impact": replace_proposal(base, user_impact="Different user impact."),
            "side_effect_order": replace_proposal(
                base,
                side_effects=tuple(reversed(base.side_effects)),
            ),
            "physical_spent": make_proposal(
                physical_calls_spent=2,
                side_effects=base.side_effects,
            ),
            "route": make_proposal(
                route="different_route", side_effects=base.side_effects
            ),
            "route_fingerprint": make_proposal(
                route_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "definition_fingerprint": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    agent_profiles=(
                        replace(
                            base.basis.agent_profiles[0],
                            definition_fingerprint=DIGEST_B,
                        ),
                        *base.basis.agent_profiles[1:],
                    ),
                ),
            ),
            "execution_fingerprint": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    agent_profiles=(
                        replace(
                            base.basis.agent_profiles[0],
                            execution_fingerprint=DIGEST_A,
                        ),
                        *base.basis.agent_profiles[1:],
                    ),
                ),
            ),
            "context_policy": make_proposal(
                context_policy_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "budget_policy": make_proposal(
                budget_policy_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "input_order": replace_proposal(
                base,
                basis=replace(base.basis, inputs=tuple(reversed(inputs))),
            ),
            "input_locator": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(
                            inputs[0], locator="$${core_documents/outline.md}:4-12"
                        ),
                        inputs[1],
                    ),
                ),
            ),
            "input_state": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(inputs[0], state=CollaborationInputState.ABSENT),
                        inputs[1],
                    ),
                ),
            ),
            "input_revision": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(inputs[0], revision_digest=DIGEST_B),
                        inputs[1],
                    ),
                ),
            ),
            "input_range": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(replace(inputs[0], start_line=4), inputs[1]),
                ),
            ),
        }
        self.assertEqual(len(set(variants)), len(variants))
        for dimension, variant in variants.items():
            with self.subTest(dimension=dimension):
                self.assertNotEqual(variant.proposal_digest, base.proposal_digest)
                self.assertNotEqual(
                    encode_collaboration_proposal(variant),
                    encode_collaboration_proposal(base),
                )

    def test_duplicate_extra_missing_nonfinite_and_noncanonical_json_are_rejected(
        self,
    ) -> None:
        wire = encode_collaboration_proposal(make_proposal())
        duplicate = wire.replace(
            b'{"proposal":',
            b'{"proposal":null,"proposal":',
            1,
        )
        top = json.loads(wire)
        candidates = [
            duplicate,
            b"\xef\xbb\xbf" + wire,
            b"\xff",
            wire + b"\n",
            wire.replace(b'":', b'": ', 1),
            b'{"proposal":NaN,"schema_version":2}',
            b'{"proposal":Infinity,"schema_version":2}',
        ]
        extra = copy.deepcopy(top)
        extra["extra"] = "private-sentinel"
        candidates.append(_canonical_json(extra))
        missing = copy.deepcopy(top)
        del missing["proposal"]
        candidates.append(_canonical_json(missing))
        nested_extra = copy.deepcopy(top)
        nested_extra["proposal"]["basis"]["extra"] = "private-sentinel"
        candidates.append(_canonical_json(nested_extra))
        v1 = copy.deepcopy(top)
        v1["schema_version"] = 1
        candidates.append(_canonical_json(v1))
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                with self.assertRaises(ValueError) as raised:
                    decode_collaboration_proposal(candidate)
                self.assertNotIn("private-sentinel", str(raised.exception))
        with self.assertRaises(TypeError):
            decode_collaboration_proposal("{}")  # type: ignore[arg-type]

    def test_unicode_is_not_normalized_and_tampered_digest_is_rejected(self) -> None:
        composed = replace_proposal(make_proposal(), purpose="Café review")
        decomposed = replace_proposal(make_proposal(), purpose="Cafe\u0301 review")
        self.assertNotEqual(composed.purpose, decomposed.purpose)
        self.assertNotEqual(composed.proposal_digest, decomposed.proposal_digest)
        self.assertNotEqual(
            encode_collaboration_proposal(composed),
            encode_collaboration_proposal(decomposed),
        )
        top = json.loads(encode_collaboration_proposal(composed))
        top["proposal"]["purpose"] = "Tampered"
        with self.assertRaises(ValueError):
            decode_collaboration_proposal(_canonical_json(top))


class CollaborationPackageBoundaryTests(unittest.TestCase):
    def test_leaf_and_facade_exports_identity_metadata_pickle_and_root_omission(
        self,
    ) -> None:
        expected_event_exports = (
            COLLABORATION_EVENT_EXPORTS[:1]
            + (
                "ArtifactApplicationDecisionKind",
                "ArtifactApplicationDecisionPayload",
                "ArtifactApplicationNextAction",
                "ArtifactApplicationOutcomeKind",
                "ArtifactApplicationRequestPayload",
                "ArtifactApplicationResultPayload",
                "StoryCommitDecisionPayload",
                "StoryCommitNextAction",
                "StoryCommitOutcomeKind",
                "StoryCommitRequestPayload",
                "StoryCommitResultPayload",
                "build_story_commit_request_payload",
            )
            + COLLABORATION_EVENT_EXPORTS[1:]
        )
        expected_context_exports = (
            COLLABORATION_CONTEXT_EXPORTS[:17]
            + (
                "ArtifactApplicationDecision",
                "ArtifactApplicationDecisionResult",
                "StoryCommitDecisionRequest",
                "StoryCommitDecisionResult",
            )
            + COLLABORATION_CONTEXT_EXPORTS[17:]
        )
        self.assertEqual(tuple(events_owner.__all__), expected_event_exports)
        self.assertEqual(tuple(context_owner.__all__), expected_context_exports)
        self.assertEqual(len(conversations.__all__), 134)
        self.assertNotIn("__getattr__", vars(events_owner))
        self.assertNotIn("__getattr__", vars(context_owner))
        self.assertNotIn("__getattr__", vars(conversations))
        collaboration_names = {
            name
            for name in conversations.__all__
            if name.startswith("Collaboration") or name.startswith("COLLABORATION_")
        }
        self.assertEqual(len(collaboration_names), 63)
        for owner in (events_owner, context_owner):
            for name in owner.__all__:
                if name.startswith("Collaboration") or name.startswith(
                    "COLLABORATION_"
                ):
                    with self.subTest(owner=owner.__name__, name=name):
                        self.assertIs(
                            getattr(owner, name), getattr(conversations, name)
                        )
        callable_names = {
            name
            for name in conversations.__all__
            if name.startswith("Collaboration")
            or name.startswith("collaboration_")
            or name.startswith("encode_collaboration_")
            or name.startswith("decode_collaboration_")
            or name.startswith("project_collaboration_")
            or name.startswith("validate_collaboration_")
        }
        callable_names.discard("CollaborationPayload")
        for name in callable_names:
            with self.subTest(metadata=name):
                self.assertEqual(
                    getattr(conversations, name).__module__,
                    "story_writing_agents.conversations",
                )
        root_names = {
            name for name in conversations.__all__ if name.startswith("Collaboration")
        }
        root_names |= {
            name
            for name in conversations.__all__
            if "collaboration" in name.lower()
            and name not in {"CONVERSATION_EVENT_SCHEMA_VERSION"}
        }
        for name in root_names:
            with self.subTest(root_omission=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        proposal = make_proposal()
        self.assertEqual(pickle.loads(pickle.dumps(proposal)), proposal)
        self.assertIs(
            pickle.loads(pickle.dumps(CollaborationProposal)),
            CollaborationProposal,
        )

    def test_append_receipt_error_and_public_signatures_are_exact(self) -> None:
        receipt = CollaborationAppendReceipt(
            CollaborationAppendStatus.ALREADY_COMMITTED,
            "batch_a",
            DIGEST_A,
            2,
            3,
            (
                "2026-08-31T00:00:01+00:00",
                "2026-08-31T00:00:02+00:00",
            ),
        )
        self.assertEqual(receipt.first_sequence, 2)
        for failure in CollaborationAppendFailure:
            with self.subTest(failure=failure):
                error = CollaborationAppendError(failure)
                self.assertIs(error.failure, failure)
                self.assertEqual(str(error), failure.value)
        expected_signatures = {
            collaboration_proposal_digest: ("proposal",),
            collaboration_batch_digest: ("batch",),
            encode_collaboration_proposal: ("proposal",),
            decode_collaboration_proposal: ("payload",),
            encode_collaboration_event_draft: ("event",),
            decode_collaboration_event_draft: ("payload",),
            encode_collaboration_event: ("event",),
            decode_collaboration_event: ("payload",),
            encode_collaboration_batch: ("batch",),
            decode_collaboration_batch: ("payload",),
        }
        for function, parameters in expected_signatures.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    tuple(inspect.signature(function).parameters), parameters
                )

    def test_clean_import_orders_have_no_cwd_io_or_dynamic_forwarding(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src"
        orders = (
            ("story_writing_agents", "story_writing_agents.conversations"),
            (
                "story_writing_agents.conversations.events",
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations",
            ),
            (
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations.events",
                "story_writing_agents.conversations",
            ),
        )
        for order in orders:
            with self.subTest(order=order), tempfile.TemporaryDirectory() as temporary:
                code = f"""
import importlib, pathlib, sys
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
for name in {order!r}:
    importlib.import_module(name)
import story_writing_agents
import story_writing_agents.conversations as facade
import story_writing_agents.conversations.context as context
import story_writing_agents.conversations.events as events
assert len(facade.__all__) == 134
assert len(events.__all__) == 98
assert len(context.__all__) == 28
assert facade.CollaborationProposal is events.CollaborationProposal
assert facade.CollaborationEventStore is context.CollaborationEventStore
assert not hasattr(story_writing_agents, "CollaborationProposal")
assert "__getattr__" not in vars(facade)
assert before == tuple(pathlib.Path.cwd().iterdir())
"""
                completed = subprocess.run(
                    [sys.executable, "-B", "-I", "-c", code],
                    cwd=temporary,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
