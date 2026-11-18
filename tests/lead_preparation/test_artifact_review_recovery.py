"""Existing artifact review recovery acceptance definitions."""
from __future__ import annotations

from dataclasses import asdict
from story_writing_agents import StoryRuntime
from story_writing_agents.agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
)
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    ArtifactReviewFailurePayload,
    ArtifactReviewedProposal,
    CollaborationConfirmationDecision,
    artifact_reviewed_proposal_digest,
    decode_artifact_reviewed_proposal,
    decode_collaboration_event,
    encode_artifact_reviewed_proposal,
    encode_collaboration_event,
    project_collaboration_history,
)
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_RULES_PATH,
    _artifact_synthesis_outcome,
    artifact_evidence_status_id,
    artifact_review_outcome,
)
from tests.lead_fixtures.preparation_data import (
    lead_confirmation_decision,
    lead_fixture_authoring_scope,
    lead_specialist_outcome,
)
from tests.lead_fixtures.provider_responses import LeadBackend as _LeadBackend
from tests.provider_wire_fixture import selection_plan
import json


class ArtifactReviewRecoveryDefinitions:
    async def test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        failure_events = []
        for ordinal, malformed in enumerate(("omit_rationale",), start=1):
            mismatch_id = self.conversation(f"Synthesis mismatch {ordinal}")
            self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
            self.backend.enqueue(
                artifact_review_outcome(
                    operations=[ { "kind" : "create" , "new_text" : "Rule candidate.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                    evidence_status="missing",
                    evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                    authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),)
            , control_plan=selection_plan('artifact.changes'))
            proposed = await self.runtime.lead_turn(
                self.session_id,
                mismatch_id,
                ("Prepare a rule candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['Rule candidate.\n'])
                 + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
            )
            self.backend.enqueue_for(
                EXECUTION_SPECIALIST,
                lead_specialist_outcome("conflict"), )
            malformed_response = _artifact_synthesis_outcome( "propose", malformed=malformed )
            self.backend.enqueue_for(
                LEAD_ORCHESTRATOR,
                malformed_response, )
            self.backend.enqueue_for(LEAD_ORCHESTRATOR, malformed_response)
            mismatch = await self.runtime.decide_lead_step(
                lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
            self.assertIs(
                mismatch.turn.artifact_proposal.state,
                ArtifactProposalState.FAILED, )
            self.assertIsNone(mismatch.turn.artifact_proposal.reviewed_proposal)
            failure_events.extend(
                event
                for event in self.history(mismatch_id)
                if type(event.payload) is ArtifactReviewFailurePayload )

        restart_id = self.conversation("Restart reviewed proposal")
        _proposed, decided = await self.reviewed_create(restart_id)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        encoded = encode_artifact_reviewed_proposal(reviewed)
        self.assertEqual(decode_artifact_reviewed_proposal(encoded), reviewed)
        self.assertEqual( artifact_reviewed_proposal_digest(reviewed), reviewed.proposal_digest )

        restart_backend = _LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]
        projection = project_collaboration_history(
            restarted.conversation_storage_services.event_store.read(
                self.session_id,
                restart_id,
            ) )
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
            key: reviewed_value[key] for key in reversed(tuple(reviewed_value)) }
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
            ArtifactReviewFailurePayload, }
        durable_events = [
            event
            for event in (*self.history(restart_id), *failure_events)
            if type(event.payload) in durable_types ]
        self.assertEqual(
            {type(event.payload) for event in durable_events},
            durable_types, )
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
