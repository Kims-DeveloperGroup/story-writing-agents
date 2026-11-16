"""Existing runtime acceptance cases for CurrentLeadInputsTests."""
from __future__ import annotations

from tests.lead_fixtures.artifact_review import ArtifactReviewFixture
from story_writing_agents import StoryRuntime
from story_writing_agents.conversation_storage import ConversationStorageBackend, ConversationStorageCapability, WorkspaceConversationStorageMutationCoordinator
from story_writing_agents.conversation_storage.migration import ConversationStorageTransferOutcome, transfer_conversation_storage, verify_conversation_storage
from story_writing_agents.conversation_storage.sqlite import compose_sqlite_conversation_storage, provision_sqlite_conversation_storage
from story_writing_agents.conversations import ArtifactProposalState, project_collaboration_history
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase


class CurrentLeadInputsTests(ArtifactReviewFixture, LeadOrchestrationTestCase):
    async def test_reviewed_history_transfers_with_filesystem_sqlite_parity(
        self,
    ) -> None:
        from story_writing_agents.structured_output_validation import failure_findings
        try:
            await self.record_book_readiness()
        except Exception as failure:
            pending, seen, findings = [failure], set(), []
            # The original exception plus at most eight cause/context links.
            for _ in range(9):
                if not pending: break
                current = pending.pop()
                if id(current) in seen: continue
                seen.add(id(current))
                findings.extend((item.path, item.requirement) for item in failure_findings(current))
                pending.extend(link for link in (current.__context__, current.__cause__) if link is not None)
            raise AssertionError(("book_readiness_schema_findings", tuple(findings))) from failure
        conversation_id = self.conversation("Artifact storage parity")
        _proposed, decided = await self.reviewed_create(conversation_id)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        filesystem_history = self.history(conversation_id)
        from story_writing_agents.review_contracts import FinalReviewEvidence
        evidence = reviewed.final_review_evidence
        self.assertIs(type(evidence), FinalReviewEvidence)
        self.assertEqual((evidence.candidate_id, evidence.candidate_revision, evidence.candidate_digest),
                         (reviewed.candidate.candidate_id, reviewed.candidate.candidate_revision,
                          reviewed.candidate.candidate_digest))
        self.assertEqual(evidence.reviewer_ids, tuple(item.specialist_id for item in reviewed.reviews))
        self.assertEqual(evidence.reply_event_ids, tuple(item.reply_event_id for item in reviewed.reviews))
        self.assertTrue(set(evidence.reply_event_ids).issubset({event.event_id for event in filesystem_history}))
        qualified = self.runtime.qualify_artifact_final_review(session_id=self.session_id,
            reviewed_proposal=reviewed, consultation=_proposed.proposal, events=filesystem_history)
        self.assertTrue(qualified.eligible)
        self.assertEqual(qualified.evidence_digest, evidence.evidence_digest)
        self.assertFalse(reviewed.apply_authority_granted)
        self.assertFalse(reviewed.artifact_write_occurred)
        self.assertFalse(reviewed.git_action_occurred)

        coordinator = WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            preflight = provision_sqlite_conversation_storage(
                self.control_workspace,
                lease=lease, )
        self.assertIs(preflight.capability, ConversationStorageCapability.READY)
        transferred = transfer_conversation_storage(
            self.control_workspace,
            source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE, )
        self.assertIs(transferred.outcome, ConversationStorageTransferOutcome.COMPLETED)
        verified = verify_conversation_storage(
            self.control_workspace,
            source_backend=ConversationStorageBackend.FILESYSTEM,
            destination_backend=ConversationStorageBackend.SQLITE, )
        self.assertIs(verified.outcome, ConversationStorageTransferOutcome.COMPLETED)

        sqlite_services = compose_sqlite_conversation_storage(self.control_workspace)
        sqlite_history = sqlite_services.event_store.read(
            self.session_id,
            conversation_id, )
        self.assertEqual(sqlite_history, filesystem_history)
        projection = project_collaboration_history(sqlite_history)
        self.assertEqual(projection.current_reviewed_artifact_proposal, reviewed)
        self.assertIs(projection.current_artifact_state, ArtifactProposalState.REVIEWED)
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        restored = restarted.conversation_storage_services.event_store.read(self.session_id, conversation_id)
        self.assertEqual(restored, filesystem_history)
        qualified_after_restart = restarted.qualify_artifact_final_review(session_id=self.session_id,
            reviewed_proposal=projection.current_reviewed_artifact_proposal,
            consultation=_proposed.proposal, events=restored)
        self.assertEqual(qualified_after_restart, qualified)
