"""Live lead-review workflows for exact artifact proposals."""
from __future__ import annotations
from story_writing_agents.agent_context.sources import session_history as owner_session_history
from tests.provider_wire_fixture import selection_plan
from tests.provider_wire_fixture import lead_fixture_bound_scope, lead_fixture_response
import json
import unittest
from dataclasses import asdict, replace
from unittest import mock
from story_writing_agents import ReplyTraceOrigin, StoryRuntime
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
from story_writing_agents.conversations import session_context as session_context_module
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.artifact_proposals import serialize_artifact_review_candidate
from story_writing_agents.writing_artifacts import ArtifactReviewReason
from story_writing_agents.conversations import ArtifactProposalState, ArtifactProposalStatePayload, ArtifactReviewCandidatePayload, ArtifactReviewFailureCode, ArtifactReviewFailurePayload, ArtifactReviewedProposal, CollaborationConfirmationDecision, CollaborationInputKind, CollaborationLeadOutcomeFailureCode, CollaborationLeadOutcomeFailurePayload, CollaborationNextAction, CollaborationStepState, CollaborationStepStatePayload, CollaborationSpecialistAssessment, LeadOutcomeKind, artifact_reviewed_proposal_digest, decode_artifact_reviewed_proposal, decode_collaboration_event, encode_artifact_reviewed_proposal, encode_collaboration_event, project_collaboration_history, project_collaboration_readable_history
from story_writing_agents.conversation_storage import ConversationStorageBackend, ConversationStorageCapability, WorkspaceConversationStorageMutationCoordinator
from story_writing_agents.conversation_storage.migration import ConversationStorageTransferOutcome, transfer_conversation_storage, verify_conversation_storage
from story_writing_agents.conversation_storage.sqlite import compose_sqlite_conversation_storage, provision_sqlite_conversation_storage
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.lead_fixtures.provider_responses import (
    LeadBackend as _LeadBackend,
    workflow_material as _workflow_material,
)
from tests.lead_fixtures.preparation_data import (
    LEAD_FIXTURE_READINESS_DOCUMENTS,
    lead_confirmation_decision,
    live_material as _live_material,
    lead_fixture_authoring_scope,
    provider_envelope as _provider_envelope,
    read_history_hit as _read_history_hit,
    search_history as _search_history,
    lead_specialist_outcome,
    visible_outcome as _visible_outcome,
)




from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_OUTLINE_PATH,
    ARTIFACT_FIXTURE_RULES_PATH,
    _reference_ids,
    artifact_evidence_status_id,
    artifact_review_outcome,
    _artifact_synthesis_outcome,
    ArtifactReviewFixture,
)





















from tests.lead_preparation.test_artifact_candidate_creation import ArtifactCandidateCreationDefinitions
from tests.lead_preparation.test_artifact_candidate_revisions import ArtifactCandidateRevisionDefinitions
from tests.lead_preparation.test_artifact_authority_boundaries import ArtifactAuthorityBoundaryDefinitions
from tests.lead_preparation.test_artifact_review_recovery import ArtifactReviewRecoveryDefinitions
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_INDEX_PATH as _INDEX,
    ARTIFACT_FIXTURE_SECOND_PATH as _SECOND,
    ARTIFACT_FIXTURE_THIRD_PATH as _THIRD,
)


class ArtifactProposalOrchestrationTests(ArtifactReviewFixture, LeadOrchestrationTestCase):
    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state() )



    test_incomplete_core_workflow_is_reviewed_only_after_confirmation = ArtifactCandidateCreationDefinitions.test_incomplete_core_workflow_is_reviewed_only_after_confirmation


    test_core_conflict_revision_and_manuscript_atomic_rename_workflows = ArtifactCandidateRevisionDefinitions.test_core_conflict_revision_and_manuscript_atomic_rename_workflows

    test_ordinary_discussion_captures_current_state_without_specialists = ArtifactAuthorityBoundaryDefinitions.test_ordinary_discussion_captures_current_state_without_specialists

    test_stale_confirmation_and_revision_require_fresh_authority = ArtifactCandidateRevisionDefinitions.test_stale_confirmation_and_revision_require_fresh_authority

    test_revised_artifact_intent_replaces_candidate_in_the_same_step = ArtifactCandidateRevisionDefinitions.test_revised_artifact_intent_replaces_candidate_in_the_same_step


    test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority = ArtifactReviewRecoveryDefinitions.test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority


    test_search_read_answer_can_add_an_artifact_candidate = ArtifactCandidateCreationDefinitions.test_search_read_answer_can_add_an_artifact_candidate

    test_provider_authority_fields_fall_back_without_candidate = ArtifactAuthorityBoundaryDefinitions.test_provider_authority_fields_fall_back_without_candidate





if __name__ == "__main__": unittest.main()
