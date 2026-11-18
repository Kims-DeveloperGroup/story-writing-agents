"""Existing artifact authority boundaries acceptance definitions."""
from __future__ import annotations

from dataclasses import asdict
from story_writing_agents import ReplyTraceOrigin
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.artifact_proposals import serialize_artifact_review_candidate
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationNextAction,
    LeadOutcomeKind,
)
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_RULES_PATH,
    artifact_evidence_status_id,
    artifact_review_outcome,
)
from tests.lead_fixtures.preparation_data import (
    visible_outcome as _visible_outcome,
    lead_fixture_authoring_scope,
)
from tests.provider_wire_fixture import selection_plan
from unittest import mock
import json


class ArtifactAuthorityBoundaryDefinitions:
    async def test_ordinary_discussion_captures_current_state_without_specialists(self) -> None:
        conversation_id = self.conversation("Ordinary discussion")
        message = "A scene goal should identify the desired change and obstacle."
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
                    message,
                ) , control_plan=selection_plan('answer.workflow'))
            result = await self.runtime.lead_turn(
                self.session_id,
                conversation_id,
                "What is a scene goal?", )
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(result.message, message)
        self.assertIs(result.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(tuple(item.calls for item in self.backend.finalizers),
                         ([("accepted", None)], [("accepted", None)],))
        self.assertIsNone(result.artifact_proposal)
        self.assertEqual(calls, 0)
        self.assertIn("current_session_state", self.backend.calls[0].payload["session_history"])
        payload = self.backend.calls[0].payload
        self.assertEqual(set(payload["session_history"]["current_session_state"]), {"runtime_overrides"})
        self.assertTrue(payload["core_document_statuses"])
        self.assertTrue(all(set(item) == {"artifact_path", "document_status", "evidence_id"}
                            for item in payload["core_document_statuses"]))
        self.assertNotIn("answer_contract", payload["task_input"])
        self.assertNotIn("runtime_contract_overlay", payload["task_input"])
        self.assertNotIn("authoring_rules", payload["task_input"])
        self.assertNotIn("Outline first line.", json.dumps(dict(payload)))
        self.assertEqual(
            tuple(agent_id for agent_id, _call in self.backend.call_records),
            (LEAD_ORCHESTRATOR, LEAD_ORCHESTRATOR,), )

    async def test_provider_authority_fields_fall_back_without_candidate(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        conversation_id = self.conversation("Reject provider authority")
        before = self.artifact_snapshot()
        invalid = artifact_review_outcome(
            operations=[ { "apply_authority_granted" : True , "kind" : "create" , "new_text" : "Must not survive.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
            evidence_status="missing",
            evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],

                authoring_assessment=asdict(task_assessment(
                    foundation_ids=('user_input',),
                    actions=(
                        'bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),)
        self.backend.enqueue(invalid, control_plan=selection_plan('artifact.changes'))
        self.backend.enqueue(invalid)
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Reject provider-supplied Apply authority.", )
        self.assertIs(result.next_action, CollaborationNextAction.SUBMIT_CONFIRMATION)
        self.assertIs(result.artifact_proposal.state, ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION)
        candidate = result.artifact_proposal.candidate.candidate
        self.assertNotIn(b'"apply_authority_granted"', serialize_artifact_review_candidate(candidate))
        self.assertEqual(self.artifact_snapshot(), before)
        self.assertEqual(self.artifact_events(conversation_id, ArtifactReviewCandidatePayload),
                         (result.artifact_proposal.candidate,))
        self.assertEqual(self.artifact_events(conversation_id, ArtifactReviewedProposal), ())
        self.assertEqual(self.artifact_events(conversation_id, CollaborationLeadOutcomeFailurePayload), ())
        self.assertEqual(self.artifact_events(conversation_id, ArtifactReviewFailurePayload), ())
