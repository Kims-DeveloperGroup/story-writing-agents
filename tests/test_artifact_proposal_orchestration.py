"""Live lead-review workflows for exact artifact proposals."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.artifact_proposals import (
    serialize_artifact_review_candidate,
)
from story_writing_agents.writing_artifacts import ArtifactReviewReason
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailureCode,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    CollaborationConfirmationDecision,
    CollaborationInputKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationNextAction,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSpecialistAssessment,
    LeadOutcomeKind,
    artifact_reviewed_proposal_digest,
    decode_artifact_reviewed_proposal,
    decode_collaboration_event,
    encode_artifact_reviewed_proposal,
    encode_collaboration_event,
    project_collaboration_history,
    project_collaboration_readable_history,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageBackend,
    ConversationStorageCapability,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage.migration import (
    ConversationStorageTransferOutcome,
    transfer_conversation_storage,
    verify_conversation_storage,
)
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _decision,
    _live_scope,
    _provider_envelope,
    _read_history_hit,
    _specialist_outcome,
    _visible_outcome,
    _workflow_material,
)


_OUTLINE = "core_documents/global_outline.md"
_RULES = "core_documents/narrative_rules.md"
_INDEX = "manuscript/part-01/index.md"
_SECOND = "manuscript/part-01/second.md"
_THIRD = "manuscript/part-01/third.md"


def _reference_ids(material: dict[str, object]) -> list[str]:
    aliases = material["input_aliases"]
    assert type(aliases) is list
    return [
        item["input_id"]
        for item in aliases
        if type(item) is dict
        and item.get("kind")
        in {
            CollaborationInputKind.CORE_DOCUMENT.value,
            CollaborationInputKind.MANUSCRIPT.value,
            CollaborationInputKind.WRITING_ARTIFACT.value,
        }
    ]


def _status_id(call: object, locator: str) -> str:
    payload = call.payload  # type: ignore[attr-defined]
    statuses = payload["core_statuses"]
    return next(item["evidence_id"] for item in statuses if item["locator"] == locator)


def _artifact_outcome(
    *,
    operations: object,
    evidence_status: str = "verified",
    evidence_ids: object = None,
    additional_review_domains: tuple[str, ...] = (),
    message: str = "I prepared an exact candidate for specialist review.",
    objective_intent: object = None,
):
    def respond(call, _provider):
        material = _workflow_material(call.payload)
        resolved_operations = (
            operations(call, material) if callable(operations) else operations
        )
        resolved_evidence = (
            _reference_ids(material)
            if evidence_ids is None
            else (
                evidence_ids(call, material) if callable(evidence_ids) else evidence_ids
            )
        )
        return _provider_envelope(
            call,
            {
                "recommended_action": None,
                "artifact_intent": {
                    "additional_review_domains": list(additional_review_domains),
                    "operations": resolved_operations,
                },
                "evidence_ids": resolved_evidence,
                "evidence_status": evidence_status,
                "kind": "proposal",
                "message": message,
                "proposal": None,
                "scope": "story_specific",
                "objective_intent": (
                    objective_intent(call, material)
                    if callable(objective_intent) else objective_intent
                ),
            },
        )

    return respond


def _artifact_synthesis_outcome(
    decision: str = "propose",
    *,
    malformed: str | None = None,
):
    def respond(call, _provider):
        value = {
            "decision": decision,
            "message": "The exact candidate review is complete.",
        }
        if malformed == "omit_rationale":
            value.pop("message")
        return _provider_envelope(call, value)

    return respond


class ArtifactProposalOrchestrationTests(LeadOrchestrationTestCase):
    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state()
        )

    def artifact_events(self, conversation_id: str, payload_type: type) -> tuple:
        return tuple(
            event.payload
            for event in self.history(conversation_id)
            if type(event.payload) is payload_type
        )

    async def reviewed_create(
        self,
        conversation_id: str,
        *,
        assessment: str = "supports",
        decision: str = "propose",
    ):
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "Use restrained close-third narration.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Complete the missing narrative rules.",
        )
        self.backend.enqueue_for(
            EXECUTION_SPECIALIST,
            _specialist_outcome(assessment),
        )
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome(decision),
        )
        decided = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        return proposed, decided

    async def test_incomplete_core_workflow_is_reviewed_only_after_confirmation(
        self,
    ) -> None:
        conversation_id = self.conversation("Complete missing core")
        self.workspace.path(_RULES).unlink(missing_ok=True)
        before = self.artifact_snapshot()
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "Use restrained close-third narration.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )

        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "The narrative rules are incomplete; propose a consistent rule.",
        )

        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(
            proposed.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
        )
        self.assertEqual(
            tuple(item.specialist_id for item in proposed.proposal.assignments),
            (EXECUTION_SPECIALIST,),
        )
        self.assertEqual(len(self.backend.call_records), 1)
        self.assertEqual(self.backend.call_records[0][0], LEAD_ORCHESTRATOR)
        self.assertEqual(self.artifact_snapshot(), before)

        candidate = proposed.artifact_proposal.candidate.candidate
        candidate_value = json.loads(serialize_artifact_review_candidate(candidate))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("supports"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("propose"),
        )
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(
            decision.turn.artifact_proposal.state, ArtifactProposalState.REVIEWED
        )
        reviewed = decision.turn.artifact_proposal.reviewed_proposal
        self.assertIsNotNone(reviewed)
        self.assertFalse(reviewed.apply_authority_granted)
        self.assertFalse(reviewed.artifact_write_occurred)
        self.assertFalse(reviewed.git_action_occurred)
        self.assertEqual(self.artifact_snapshot(), before)
        specialist_material = next(
            _workflow_material(call.payload)
            for agent_id, call in self.backend.call_records
            if agent_id == EXECUTION_SPECIALIST
        )
        specialist_candidate = next(
            item["candidate"]
            for item in specialist_material["inputs"]
            if item["kind"] == "artifact_candidate"
        )
        synthesis_material = next(
            _workflow_material(call.payload)
            for agent_id, call in self.backend.call_records
            if agent_id == LEAD_ORCHESTRATOR
            and _workflow_material(call.payload)["workflow"] == "lead_synthesis"
        )
        self.assertEqual(specialist_candidate, candidate_value)
        self.assertEqual(synthesis_material["artifact_candidate"], candidate_value)
        self.assertEqual(
            self.artifact_events(conversation_id, ArtifactReviewedProposal),
            (reviewed,),
        )
        projection = project_collaboration_history(self.history(conversation_id))
        self.assertEqual(projection.current_reviewed_artifact_proposal, reviewed)
        readable = project_collaboration_readable_history(self.history(conversation_id))
        readable_keys = {
            field.key.value for record in readable for field in record.fields
        }
        self.assertTrue(
            {
                "artifact_review.message",
                "artifact_review.decision",
                "artifact_proposal.state",
            }.issubset(readable_keys)
        )


    async def test_core_conflict_revision_and_manuscript_atomic_rename_workflows(
        self,
    ) -> None:
        rules = self.workspace.path(_RULES)
        rules.write_text("Always use past tense.\n", encoding="utf-8")
        conflict_id = self.conversation("Conflicting core edit")
        before = self.artifact_snapshot()
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call, material: [
                    {
                        "evidence_id": _reference_ids(material)[0],
                        "kind": "replace",
                        "new_text": "Use present tense in dream passages.\n",
                    }
                ],
                evidence_status="verified",
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conflict_id,
            f"Edit ${{{_RULES}}} even though the existing rule conflicts.",
        )
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("conflict"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("revise"),
        )
        revised = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        self.assertIs(
            revised.turn.artifact_proposal.state,
            ArtifactProposalState.REVISION_REQUIRED,
        )
        self.assertIsNone(revised.turn.artifact_proposal.reviewed_proposal)
        self.assertIn("review is complete", revised.turn.message)
        self.assertEqual(self.artifact_snapshot(), before)

        self.workspace.path(_INDEX).write_text("[next](second.md)\n", encoding="utf-8")
        self.workspace.path(_SECOND).write_text("Second scene.\n", encoding="utf-8")
        rename_id = self.conversation("Atomic manuscript rename repair")
        rename_before = self.artifact_snapshot()
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call, material: [
                    {
                        "evidence_id": _reference_ids(material)[0],
                        "kind": "replace",
                        "new_text": "[next](third.md)\n",
                    },
                    {
                        "evidence_id": _reference_ids(material)[1],
                        "kind": "rename",
                        "suggested_destination": _THIRD,
                    },
                ]
            )
        )
        manuscript = await self.runtime.lead_turn(
            self.session_id,
            rename_id,
            f"Repair ${{{_INDEX}}} and rename ${{{_SECOND}}} atomically.",
        )
        self.assertIs(
            manuscript.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
        )
        self.assertEqual(
            tuple(
                item.operation_kind.value
                for item in manuscript.artifact_proposal.candidate.candidate.change_set.operations
            ),
            ("replace", "rename"),
        )
        self.assertEqual(
            manuscript.artifact_proposal.candidate.candidate.relationship_validation.projected_dangling_edges,
            (),
        )
        rejected = await self.runtime.decide_lead_step(
            _decision(manuscript, CollaborationConfirmationDecision.REJECT)
        )
        self.assertIs(
            rejected.turn.artifact_proposal.state, ArtifactProposalState.REJECTED
        )
        self.assertEqual(self.artifact_snapshot(), rename_before)

    async def test_ordinary_discussion_captures_current_state_without_specialists(self) -> None:
        conversation_id = self.conversation("Ordinary discussion")
        calls = 0
        original = WritingArtifactRepository.capture_complete_state

        def counted_snapshot(repository):
            nonlocal calls
            calls += 1
            return original(repository)

        with mock.patch.object(
            WritingArtifactRepository,
            "capture_complete_state",
            new=counted_snapshot,
        ):
            self.backend.enqueue(
                _visible_outcome(
                    "answer",
                    "A scene goal should identify the desired change and obstacle.",
                )
            )
            result = await self.runtime.lead_turn(
                self.session_id,
                conversation_id,
                "What is a scene goal?",
            )
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.artifact_proposal)
        self.assertEqual(calls, 1)
        self.assertIn("current_state", self.backend.calls[0].payload["h"])
        payload = self.backend.calls[0].payload
        self.assertEqual(set(payload["h"]["current_state"]), {"runtime_overrides"})
        self.assertTrue(payload["core_statuses"])
        self.assertTrue(all(set(item) == {"locator", "status", "evidence_id"}
                            for item in payload["core_statuses"]))
        self.assertNotIn("answer_contract", payload["i"])
        self.assertNotIn("runtime_contract_overlay", payload["i"])
        self.assertNotIn("authoring_rules", payload["i"])
        self.assertNotIn("Outline first line.", json.dumps(dict(payload)))
        self.assertEqual(
            tuple(agent_id for agent_id, _call in self.backend.call_records),
            (LEAD_ORCHESTRATOR,),
        )

    async def test_stale_confirmation_and_revision_require_fresh_authority(
        self,
    ) -> None:
        stale_id = self.conversation("Stale candidate")
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "A narrative rule.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        stale_proposal = await self.runtime.lead_turn(
            self.session_id,
            stale_id,
            "Create the missing narrative rules.",
        )
        call_count = len(self.backend.call_records)
        self.workspace.path(_RULES).write_text(
            "External current rule.\n", encoding="utf-8"
        )
        external = self.artifact_snapshot()
        stale = await self.runtime.decide_lead_step(
            _decision(stale_proposal, CollaborationConfirmationDecision.CONFIRM)
        )
        self.assertIs(stale.turn.artifact_proposal.state, ArtifactProposalState.STALE)
        self.assertEqual(len(self.backend.call_records), call_count)
        self.assertIn("No provider was called", stale.turn.message)
        self.assertEqual(self.artifact_snapshot(), external)

        self.workspace.path(_RULES).unlink()
        revise_id = self.conversation("Revise candidate")
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "First candidate.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        first = await self.runtime.lead_turn(
            self.session_id,
            revise_id,
            "Prepare the first candidate.",
        )
        first_digest = first.artifact_proposal.candidate.candidate.candidate_digest
        before_revision_calls = len(self.backend.call_records)

        def fresh_recommendation(call, _provider):
            return _provider_envelope(
                call,
                {
                    "recommended_action": {
                        "kind": "prepare_artifact",
                        "purpose": "Prepare a fresh candidate.",
                        "instruction": "Prepare a new candidate for the narrative rules.",
                        "expected_result": "An exact separately reviewable candidate.",
                        "source_ids": ["current_response"],
                        "target_paths": [_RULES],
                        "specialist_ids": [],
                        "objective_id": None,
                    },
                    "kind": "recommendation",
                    "message": "The old candidate is superseded; request a fresh candidate.",
                    "scope": "story_specific",
                    "evidence_status": "missing",
                    "evidence_ids": [_status_id(call, _RULES)],
                    "proposal": None,
                    "artifact_intent": None,
                    "objective_intent": None,
                    "authoring_scope": _live_scope(
                        call,
                        activity="review",
                        paths=(_RULES,),
                        domains=("tone_execution",),
                    ),
                },
            )

        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            fresh_recommendation,
        )
        revised = await self.runtime.decide_lead_step(
            _decision(first, CollaborationConfirmationDecision.REVISE)
        )
        history = self.history(revise_id)
        self.assertIsNone(project_collaboration_history(history).current_artifact_state)
        self.assertIsNone(revised.turn.artifact_proposal)
        self.assertNotEqual(revised.turn.step_id, first.step_id)
        request = revised.turn.recommendation_request
        self.assertIsNotNone(request)
        self.assertEqual((request.source_proposal_id, request.source_proposal_revision,
            request.source_proposal_digest, request.source_execution_attempt_id),
            (first.proposal.proposal_id, first.proposal.proposal_revision,
             first.proposal.proposal_digest, None))
        states = [e.payload.state for e in history if e.step_id == first.step_id
                  and type(e.payload) is ArtifactProposalStatePayload]
        self.assertIs(states[-1], ArtifactProposalState.SUPERSEDED)
        old_candidates = [e.payload for e in history if e.step_id == first.step_id
                          and type(e.payload) is ArtifactReviewCandidatePayload]
        self.assertEqual(old_candidates, [first.artifact_proposal.candidate])
        self.assertEqual(old_candidates[0].candidate.candidate_digest, first_digest)
        self.assertEqual(len(self.backend.call_records), before_revision_calls + 1)
        self.assertEqual(
            tuple(
                agent_id
                for agent_id, _call in self.backend.call_records[before_revision_calls:]
            ),
            (LEAD_ORCHESTRATOR,),
        )

        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "Second candidate.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        second = await self.runtime.lead_turn(
            self.session_id,
            revise_id,
            "Prepare the revised candidate.",
        )
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_digest,
            first_digest,
        )
        self.assertNotEqual(
            second.confirmation_request_id, first.confirmation_request_id
        )

    async def test_revised_artifact_intent_replaces_candidate_in_the_same_step(
        self,
    ) -> None:
        conversation_id = self.conversation("Replace artifact candidate")
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "First candidate.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            )
        )
        first = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Prepare the first narrative-rule candidate.",
        )
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "Revised candidate.\n",
                        "suggested_path": _RULES,
                    }
                ],
                evidence_status="missing",
                evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
            ),
        )
        replacement = await self.runtime.decide_lead_step(
            _decision(first, CollaborationConfirmationDecision.REVISE)
        )
        second = replacement.turn
        self.assertEqual(second.step_id, first.step_id)
        self.assertIs(
            second.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
        )
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_id,
            first.artifact_proposal.candidate.candidate.candidate_id,
        )
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_digest,
            first.artifact_proposal.candidate.candidate.candidate_digest,
        )
        self.assertNotEqual(
            second.confirmation_request_id, first.confirmation_request_id
        )
        states = tuple(
            item.state
            for item in self.artifact_events(
                conversation_id,
                ArtifactProposalStatePayload,
            )
        )
        self.assertEqual(
            states,
            (
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                ArtifactProposalState.SUPERSEDED,
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
            ),
        )
        self.assertEqual(
            len(
                self.artifact_events(
                    conversation_id,
                    ArtifactReviewCandidatePayload,
                )
            ),
            2,
        )

    async def test_all_assessments_are_preserved_and_partial_failure_never_reviews(
        self,
    ) -> None:
        assessments = (
            "supports",
            "concern",
            "conflict",
            "insufficient_evidence",
        )
        for ordinal, assessment in enumerate(assessments, start=1):
            with self.subTest(assessment=assessment):
                conversation_id = self.conversation(f"Assessment {ordinal}")
                proposed, decided = await self.reviewed_create(
                    conversation_id,
                    assessment=assessment,
                )
                reviewed = decided.turn.artifact_proposal.reviewed_proposal
                self.assertEqual(
                    reviewed.reviews[0].assessment,
                    CollaborationSpecialistAssessment(assessment),
                )
                expected_unresolved = (
                    ()
                    if assessment == "supports"
                    else proposed.proposal.assignments[0].input_ids
                )
                self.assertEqual(reviewed.unresolved_input_ids, expected_unresolved)
                self.assertEqual(
                    reviewed.limitations,
                    (
                        "No additional limitations were reported."
                        if not expected_unresolved
                        else "Unresolved reviewer inputs remain: "
                        + ", ".join(expected_unresolved)
                        + "."
                    ),
                )

        failed_id = self.conversation("Partial reviewer failure")
        self.backend.enqueue(
            _artifact_outcome(
                operations=[
                    {
                        "kind": "create",
                        "new_text": "New scene.\n",
                        "suggested_path": "manuscript/part-02/new.md",
                    }
                ],
                evidence_status="incomplete",
                evidence_ids=lambda call, _material: [_status_id(call, _OUTLINE)],
                additional_review_domains=("plot_structure",),
            )
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            failed_id,
            "Prepare a new scene candidate.",
        )
        for assignment in proposed.proposal.assignments:
            response = (
                (
                    lambda call, _provider: _provider_envelope(
                        call,
                        {
                            "assessment": "supports",
                            "assignment_id": "wrong_assignment",
                            "input_ids": [],
                            "message": "Malformed reviewer identity.",
                        },
                    )
                )
                if assignment.specialist_id == PLOT_ARCHITECT
                else _specialist_outcome("supports")
            )
            self.backend.enqueue_for(assignment.specialist_id, response)
        review_call_start = len(self.backend.call_records)
        failed = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        self.assertIs(failed.turn.artifact_proposal.state, ArtifactProposalState.FAILED)
        self.assertIsNone(failed.turn.artifact_proposal.reviewed_proposal)
        self.assertEqual(
            self.artifact_events(failed_id, ArtifactReviewedProposal),
            (),
        )
        failures = self.artifact_events(failed_id, ArtifactReviewFailurePayload)
        self.assertEqual(failures[-1].code, ArtifactReviewFailureCode.REVIEW_INCOMPLETE)
        candidate_value = json.loads(
            serialize_artifact_review_candidate(
                proposed.artifact_proposal.candidate.candidate
            )
        )
        specialist_calls = self.backend.call_records[review_call_start:]
        self.assertCountEqual(
            tuple(agent_id for agent_id, _call in specialist_calls),
            tuple(item.specialist_id for item in proposed.proposal.assignments),
        )
        for agent_id, call in specialist_calls:
            with self.subTest(specialist=agent_id):
                material = _workflow_material(call.payload)
                disclosed = next(
                    item["candidate"]
                    for item in material["inputs"]
                    if item["kind"] == "artifact_candidate"
                )
                self.assertEqual(disclosed, candidate_value)

    async def test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority(
        self,
    ) -> None:
        failure_events = []
        for ordinal, malformed in enumerate(("omit_rationale",), start=1):
            mismatch_id = self.conversation(f"Synthesis mismatch {ordinal}")
            self.workspace.path(_RULES).unlink(missing_ok=True)
            self.backend.enqueue(
                _artifact_outcome(
                    operations=[
                        {
                            "kind": "create",
                            "new_text": "Rule candidate.\n",
                            "suggested_path": _RULES,
                        }
                    ],
                    evidence_status="missing",
                    evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
                )
            )
            proposed = await self.runtime.lead_turn(
                self.session_id,
                mismatch_id,
                "Prepare a rule candidate.",
            )
            self.backend.enqueue_for(
                EXECUTION_SPECIALIST,
                _specialist_outcome("conflict"),
            )
            malformed_response = _artifact_synthesis_outcome(
                "propose", malformed=malformed
            )
            self.backend.enqueue_for(
                LEAD_ORCHESTRATOR,
                malformed_response,
                malformed_response,
            )
            mismatch = await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )
            self.assertIs(
                mismatch.turn.artifact_proposal.state,
                ArtifactProposalState.FAILED,
            )
            self.assertIsNone(mismatch.turn.artifact_proposal.reviewed_proposal)
            failure_events.extend(
                event
                for event in self.history(mismatch_id)
                if type(event.payload) is ArtifactReviewFailurePayload
            )

        restart_id = self.conversation("Restart reviewed proposal")
        _proposed, decided = await self.reviewed_create(restart_id)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        encoded = encode_artifact_reviewed_proposal(reviewed)
        self.assertEqual(decode_artifact_reviewed_proposal(encoded), reviewed)
        self.assertEqual(
            artifact_reviewed_proposal_digest(reviewed), reviewed.proposal_digest
        )

        restart_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]
        projection = project_collaboration_history(
            restarted.conversation_storage_services.event_store.read(
                self.session_id,
                restart_id,
            )
        )
        restored = projection.current_reviewed_artifact_proposal
        self.assertEqual(restored, reviewed)
        self.assertFalse(restored.apply_authority_granted)
        self.assertFalse(restored.artifact_write_occurred)
        self.assertFalse(restored.git_action_occurred)
        self.assertIs(projection.current_artifact_state, ArtifactProposalState.REVIEWED)

        reviewed_wire = encode_artifact_reviewed_proposal(reviewed)
        reviewed_value = json.loads(reviewed_wire)
        reviewed_unknown = dict(reviewed_value)
        reviewed_unknown["private_reasoning"] = "must remain private"
        reviewed_reordered = {
            key: reviewed_value[key] for key in reversed(tuple(reviewed_value))
        }
        for malformed in (
            b"{",
            json.dumps(
                reviewed_unknown,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode(),
            json.dumps(
                reviewed_reordered,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(),
        ):
            with self.assertRaises(ValueError) as raised:
                decode_artifact_reviewed_proposal(malformed)
            self.assertNotIn("must remain private", str(raised.exception))

        durable_types = {
            ArtifactReviewCandidatePayload,
            ArtifactProposalStatePayload,
            ArtifactReviewedProposal,
            ArtifactReviewFailurePayload,
        }
        durable_events = [
            event
            for event in (*self.history(restart_id), *failure_events)
            if type(event.payload) in durable_types
        ]
        self.assertEqual(
            {type(event.payload) for event in durable_events},
            durable_types,
        )
        for event in durable_events:
            with self.subTest(payload=type(event.payload).__name__):
                wire = encode_collaboration_event(event)
                self.assertEqual(decode_collaboration_event(wire), event)
                value = json.loads(wire)
                unknown = dict(value)
                unknown["private_reasoning"] = "must remain private"
                reordered = {key: value[key] for key in reversed(tuple(value))}
                for malformed in (
                    b"{",
                    json.dumps(
                        unknown,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode(),
                    json.dumps(
                        reordered,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode(),
                ):
                    with self.assertRaises(ValueError) as raised:
                        decode_collaboration_event(malformed)
                    self.assertNotIn("must remain private", str(raised.exception))

    async def test_reviewed_history_transfers_with_filesystem_sqlite_parity(
        self,
    ) -> None:
        conversation_id = self.conversation("Artifact storage parity")
        _proposed, decided = await self.reviewed_create(conversation_id)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        filesystem_history = self.history(conversation_id)

        coordinator = WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            preflight = provision_sqlite_conversation_storage(
                self.control_workspace,
                lease=lease,
            )
        self.assertIs(preflight.capability, ConversationStorageCapability.READY)
        transferred = transfer_conversation_storage(
            self.control_workspace,
            source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE,
        )
        self.assertIs(transferred.outcome, ConversationStorageTransferOutcome.COMPLETED)
        verified = verify_conversation_storage(
            self.control_workspace,
            source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE,
        )
        self.assertIs(verified.outcome, ConversationStorageTransferOutcome.COMPLETED)

        sqlite_services = compose_sqlite_conversation_storage(self.control_workspace)
        sqlite_history = sqlite_services.event_store.read(
            self.session_id,
            conversation_id,
        )
        self.assertEqual(sqlite_history, filesystem_history)
        projection = project_collaboration_history(sqlite_history)
        self.assertEqual(projection.current_reviewed_artifact_proposal, reviewed)
        self.assertIs(projection.current_artifact_state, ArtifactProposalState.REVIEWED)

    async def test_search_read_answer_can_add_an_artifact_candidate(self) -> None:
        reference_ids = []
        core_paths = ("core_documents/character_sheets.md", _OUTLINE, _RULES, "core_documents/part_outlines.md")
        for path in (_RULES, "core_documents/part_outlines.md"):
            self.workspace.path(path).write_text("Core reference fixture.\nSecond core reference line.\n", encoding="utf-8")
        conversation_id = self.conversation("Search read artifact intent")
        self.backend.enqueue(_visible_outcome("answer", "A prior visible answer."))
        await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Give one workflow answer.",
        )

        def answer_with_artifact(call, provider):
            observation = call.payload["o"]
            evidence_id = observation[1][0][5]
            return _provider_envelope(
                call,
                {
                    "recommended_action": None,
                    "artifact_intent": {
                        "additional_review_domains": [],
                        "operations": [
                            {
                                "kind": "create",
                                "new_text": "A searched scene candidate.\n",
                                "suggested_path": "manuscript/part-02/searched.md",
                            }
                        ],
                    },
                    "evidence_ids": [*reference_ids, evidence_id],
                    "evidence_status": "verified",
                    "kind": "proposal",
                    "message": "The read context supports an exact candidate.",
                    "proposal": None,
                    "scope": "story_specific",
                "objective_intent": None,
                },
                used_handles=provider.used_handles,
            )

        def search_prior_answer(call, _provider):
            reference_ids[:] = _reference_ids(_workflow_material(call.payload))
            return json.dumps(
                {
                    "action": {
                        "kind": "search",
                        "queries": [
                            {
                                "text": "prior visible answer",
                                "source_kinds": ["conversation"],
                            }
                        ],
                    },
                },
                separators=(",", ":"),
            )

        self.backend.enqueue(
            search_prior_answer,
            _read_history_hit,
            answer_with_artifact,
        )
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Search and read the prior answer before proposing a scene." + "".join(f" ${{{path}}}" + ("#L1-L1" if path in core_paths[2:] else "") for path in reversed(core_paths)) + "".join(f" ${{{path}}}#L2-L2" for path in reversed(core_paths[2:])),
        )
        self.assertIs(
            result.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
        )
        call_kinds = tuple(call.call_kind for call in self.backend.calls[-3:])
        self.assertEqual(
            call_kinds,
            (
                ProviderCallKind.COLLABORATION_INITIAL,
                ProviderCallKind.COLLABORATION_CONTINUATION,
                ProviderCallKind.COLLABORATION_CONTINUATION,
            ),
        )
        self.assertNotIn("o", self.backend.calls[-3].payload)
        self.assertEqual(self.backend.calls[-2].payload["o"][0], "s")
        self.assertEqual(self.backend.calls[-1].payload["o"][0], "r")
        actions = self.backend.calls[-3].schema["properties"]["action"]["anyOf"]
        answer = next(variant for variant in actions
                      if variant["properties"]["kind"]["enum"] == ["answer"])
        self.assertTrue(any("artifact_intent" in variant["properties"]
                            for variant in answer["properties"]["result"]["anyOf"]))
        read_evidence_id = self.backend.calls[-1].payload["o"][1][0][5]
        self.assertIn(
            read_evidence_id,
            result.artifact_proposal.candidate.candidate.grounding_evidence_ids,
        )
        attached = tuple(fact.artifact_path for fact in result.artifact_proposal.candidate.candidate.relationship_validation.review_facts if fact.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE)
        self.assertEqual(tuple(sorted(attached, key=lambda path: path.encode("utf-8"))), core_paths)

    async def test_provider_authority_fields_fall_back_without_candidate(
        self,
    ) -> None:
        conversation_id = self.conversation("Reject provider authority")
        invalid = _artifact_outcome(
            operations=[
                {
                    "apply_authority_granted": True,
                    "kind": "create",
                    "new_text": "Must not survive.\n",
                    "suggested_path": _RULES,
                }
            ],
            evidence_status="missing",
            evidence_ids=lambda call, _material: [_status_id(call, _RULES)],
        )
        self.backend.enqueue(invalid)
        self.backend.enqueue(invalid)
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Reject provider-supplied Apply authority.",
        )
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertIsNone(result.artifact_proposal)
        self.assertEqual(
            self.artifact_events(conversation_id, ArtifactReviewCandidatePayload),
            (),
        )
        lead_failure = self.artifact_events(
            conversation_id,
            CollaborationLeadOutcomeFailurePayload,
        )
        self.assertEqual(len(lead_failure), 1)
        self.assertIs(
            lead_failure[0].code,
            CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED,
        )
        artifact_failure = self.artifact_events(
            conversation_id,
            ArtifactReviewFailurePayload,
        )
        self.assertEqual(artifact_failure, ())


    async def test_empty_core_status_evidence_requires_selected_fresh_zero_byte_source(self):
        import hashlib
        from story_writing_agents import BackendProtocolError
        from dataclasses import replace
        from story_writing_agents import conversations as c
        from tests import test_artifact_objectives as objectives
        case = objectives.ObjectiveCase()
        case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        conversation_id, turn = await case.start()
        proposal = next(e.payload for e in case.history(conversation_id)
                        if type(e.payload) is c.CollaborationProposal)
        evidence = proposal.basis.evidence
        receipt = evidence.receipts[0]
        empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
        self.assertEqual(receipt.locator, _OUTLINE)
        self.assertEqual(receipt.content_digest, empty_digest)
        self.assertNotEqual(receipt.revision_digest, receipt.content_digest)
        def translated(selected):
            return case.runtime._artifact_intent_evidence(selected, session_id=case.session_id, conversation_id=conversation_id, artifact_binding=case.runtime._session_artifact_binding(case.session_id))
        valid = translated(evidence)
        self.assertEqual(len(valid), 1)
        self.assertEqual((valid[0].source_digest, valid[0].boundary_digest), (empty_digest, empty_digest))
        self.assertEqual(translated(replace(evidence, receipts=())), ())
        for changed in (
            replace(receipt, source_state=c.CollaborationInputState.ABSENT, revision_digest=None, content_digest=None),
            replace(receipt, utf8_bytes=1, content_digest=objectives.digest("x")),
            replace(receipt, content_digest=objectives.digest("wrong")),
        ):
            with self.subTest(receipt=changed):
                self.assertEqual(translated(replace(evidence, receipts=(changed,))), ())
        for changed in (replace(receipt, source_id="foreign_source"), replace(receipt, revision_digest=objectives.digest("wrong metadata")), replace(receipt, evidence_id="status_99")):
            with self.subTest(receipt=changed), self.assertRaises(BackendProtocolError):
                translated(replace(evidence, receipts=(changed,)))
        path = case.workspace.path(_OUTLINE)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"")
        replacement.replace(path)
        self.assertEqual(path.read_bytes(), b"")
        with self.assertRaises(BackendProtocolError):
            translated(evidence)
        # Repeating an artifact path or attaching a reference is unnecessary at A1.
        prepared = await case.prepare(conversation_id)
        self.assertEqual(prepared.artifact_proposal.candidate.candidate.change_set.operations[0].operation_kind.value, "replace")

    async def test_read_evidence_translates_whole_content_and_range_boundary_digests(self):
        from dataclasses import replace
        from story_writing_agents import conversations as c
        from story_writing_agents.agent_context.sources import WritingArtifactSourceAdapter
        from story_writing_agents.agent_context.manifest import ContextSubjectKind
        from story_writing_agents.orchestrator import CONTEXT_SOURCE_POLICY
        from tests import test_artifact_objectives as objectives
        text = "첫째 beat\r\nneedle 민\r\n마지막 beat\n"
        self.workspace.path(_OUTLINE).write_bytes(text.encode("utf-8"))
        conversation_id = self.conversation("Genuine READ evidence")
        def search(call, _provider):
            return json.dumps({"action": {"kind": "search", "path_prefix": "core_documents/", "queries": [{"text": "needle", "source_kinds": ["writing_artifact"]}]}})
        def answer(call, provider):
            evidence_id = call.payload["o"][1][0][5]
            return _provider_envelope(call, {
                                                "recommended_action": None,"scope": "story_specific", "evidence_status": "verified", "evidence_ids": [evidence_id], "kind": "question", "message": "The selected line is visible; what should change?", "proposal": None, "artifact_intent": None, "objective_intent": None,
                                                "authoring_scope": _live_scope(call, activity="review", domains=("plot_structure",), paths=(_OUTLINE,))}, used_handles=provider.used_handles)
        self.backend.enqueue(search, _read_history_hit, answer)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Search and read the needle line before deciding.")
        self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
        receipt = next(
            item
            for item in result.evidence.receipts
            if item.acquisition is c.CollaborationEvidenceAcquisition.READ
        )
        self.assertIs(receipt.acquisition, c.CollaborationEvidenceAcquisition.READ)
        scope = self.runtime._agent_context_service.scope(self.session_id, ContextSubjectKind.CONVERSATION, conversation_id, LEAD_ORCHESTRATOR)
        snapshot = WritingArtifactSourceAdapter(
            WritingArtifactRepository(self.workspace.root),
            scope,
        ).capture(_OUTLINE, CONTEXT_SOURCE_POLICY)
        whole_sha = snapshot.descriptor.content_digest
        self.assertEqual(whole_sha, objectives.digest(text))
        self.assertNotEqual(snapshot.descriptor.revision, whole_sha)
        # Whole READ is another legal READ boundary over the same owner-captured source.
        whole = replace(receipt, boundary_kind="whole", boundary_start=None, boundary_end=None, start_line=None, end_line=None, content_digest=whole_sha, utf8_bytes=len(text.encode("utf-8")))
        ranged = replace(receipt, boundary_kind="artifact_lines", boundary_start=2, boundary_end=2, start_line=2, end_line=2, content_digest=objectives.digest("needle 민\r\n"), utf8_bytes=len("needle 민\r\n".encode("utf-8")))
        for selected in (whole, ranged):
            with self.subTest(boundary=selected.boundary_kind):
                evidence = replace(result.evidence, receipts=(selected,))
                converted, = self.runtime._artifact_intent_evidence(evidence, session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime._session_artifact_binding(self.session_id))
                self.assertEqual(converted.source_digest, whole_sha)
                self.assertEqual(converted.boundary_digest, selected.content_digest)
                self.assertEqual((converted.start_line, converted.end_line), (selected.start_line, selected.end_line))
                from story_writing_agents.artifact_proposals import ArtifactProposalIntent, ArtifactReplaceIntent, ArtifactPatchIntent
                from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope
                intent = ArtifactReplaceIntent(selected.evidence_id, "Whole replacement.\n") if selected.boundary_kind == "whole" else ArtifactPatchIntent(selected.evidence_id, "Revised 민 line.\r\n")
                review_scope = AuthoringScope(AuthoringActivity.REVIEW, (_OUTLINE,), (),
                    "Review the exact synthetic whole or ranged candidate.", "synthetic_review", 1)
                candidate = self.runtime._construct_artifact_review_candidate(session_id=self.session_id, conversation_id=conversation_id, turn_id=result.turn_id, step_id=result.step_id, intent=ArtifactProposalIntent((intent,)), evidence=evidence, authoring_scope=review_scope, artifact_binding=self.runtime._session_artifact_binding(self.session_id))
                self.assertEqual("sha256:" + candidate.change_set.operations[0].base_digest, whole_sha)
                for invalid in (replace(selected, revision_digest=objectives.digest("wrong metadata")), replace(selected, content_digest=objectives.digest("wrong bytes"))):
                    with self.assertRaises(ValueError):
                        self.runtime._artifact_intent_evidence(replace(evidence, receipts=(invalid,)), session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime._session_artifact_binding(self.session_id))
        # Byte-identical physical replacement is stale despite equal whole/boundary hashes.
        path = self.workspace.path(_OUTLINE)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with self.assertRaises(ValueError):
            self.runtime._artifact_intent_evidence(result.evidence, session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime._session_artifact_binding(self.session_id))


if __name__ == "__main__":
    unittest.main()
