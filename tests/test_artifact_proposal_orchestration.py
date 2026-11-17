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





_INDEX = "manuscript/part-01/index.md"


_SECOND = "manuscript/part-01/second.md"


_THIRD = "manuscript/part-01/third.md"












class ArtifactProposalOrchestrationTests(ArtifactReviewFixture, LeadOrchestrationTestCase):
    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state() )



    async def test_incomplete_core_workflow_is_reviewed_only_after_confirmation(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        before_calls = len(self.backend.call_records)
        conversation_id = self.conversation("Complete missing core")
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        before = self.artifact_snapshot()
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Use restrained close-third narration.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),
            )
        , control_plan=selection_plan('artifact.changes'))

        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("The narrative rules are incomplete; propose a consistent rule." + '\nSupplied requested candidate content:\n' + json.dumps(['Use restrained close-third narration.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )

        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(
            proposed.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION, )
        self.assertEqual(
            tuple(item.specialist_id for item in proposed.proposal.assignments),
            (EXECUTION_SPECIALIST,), )
        self.assertEqual(len(self.backend.call_records) - before_calls, 2)
        self.assertEqual(self.backend.call_records[before_calls][0], LEAD_ORCHESTRATOR)
        self.assertEqual(self.artifact_snapshot(), before)

        candidate = proposed.artifact_proposal.candidate.candidate
        candidate_value = json.loads(serialize_artifact_review_candidate(candidate))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, lead_specialist_outcome("supports"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("propose"), )
        storage = self.runtime.conversation_storage_services
        context = owner_session_history.SessionHistorySourceAdapter(storage.catalog, storage.event_store, self.session_id)
        prior = context.capture().coverage
        decision = await self.runtime.decide_lead_step(
            lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM) )

        self.assertIs( decision.turn.artifact_proposal.state, ArtifactProposalState.REVIEWED )
        reviewed = decision.turn.artifact_proposal.reviewed_proposal
        self.assertIsNotNone(reviewed)
        self.assertFalse(reviewed.apply_authority_granted)
        self.assertFalse(reviewed.artifact_write_occurred)
        self.assertFalse(reviewed.git_action_occurred)
        self.assertEqual(self.artifact_snapshot(), before)
        from story_writing_agents.core_document_readiness import AuthoringTarget, require_readiness
        current_readiness = self.runtime.inspect_core_document_readiness(self.session_id)
        self.assertNotIn(ARTIFACT_FIXTURE_RULES_PATH, current_readiness.complete_documents)
        self.assertIn(ARTIFACT_FIXTURE_RULES_PATH, require_readiness(AuthoringTarget.MANUSCRIPT, current_readiness).required_documents)
        self.assertFalse(self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).exists())
        from tests.provider_wire_fixture import artifact_candidate as wire_candidate
        from tests.lead_fixtures.material_values import (
            expanded_context_payload as _expanded_context_payload,
        )
        materials = [(agent_id, (self.backend.callback_payload(call))["task_input"])
                     for agent_id, call in self.backend.call_records]
        specialist_material = next(value for agent_id, value in materials if agent_id == EXECUTION_SPECIALIST)
        specialist_candidate = next(item["artifact_candidate"] for item in specialist_material["inputs"]
                                    if item["input_source_type"] == "artifact_candidate")
        synthesis_material = next(value for agent_id, value in materials
                                  if agent_id == LEAD_ORCHESTRATOR and value["workflow_type"] == "consultation_summary")
        self.assertEqual(specialist_candidate, wire_candidate(candidate_value))
        self.assertEqual(synthesis_material["artifact_candidate"], wire_candidate(candidate_value))
        operation = specialist_candidate["artifact_change_set"]["artifact_changes"][0]
        self.assertIn(operation["change_type"], {"create_artifact", "replace_artifact_text", "patch_artifact_text", "rename_artifact", "delete_artifact"})
        self.assertEqual(specialist_candidate["candidate_digest"], candidate_value["candidate_digest"])
        self.assertEqual(
            self.artifact_events(conversation_id, ArtifactReviewedProposal),
            (reviewed,), )
        projection = project_collaboration_history(self.history(conversation_id))
        self.assertEqual(projection.current_reviewed_artifact_proposal, reviewed)
        readable = project_collaboration_readable_history(self.history(conversation_id))
        readable_keys = {field.key.value for record in readable for field in record.fields}
        self.assertTrue({
            "artifact_review.message", "artifact_review.decision", "artifact_proposal.state",
        }.issubset(readable_keys))
        review = next(record for record in readable
                      if type(record.payload) is session_context_module.ArtifactReviewSynthesisPayload)
        operation = f"synthesis_{review.payload.execution_attempt_id}"
        known = session_context_module._native_artifact_review_prose(readable, operation, LEAD_ORCHESTRATOR)
        self.assertEqual(known, {review.event_id: frozenset({"rationale"})})
        snapshot = context.capture()
        delta = snapshot.delivery(prior, known_native_operation_id=operation, native_agent_id=LEAD_ORCHESTRATOR)
        row = next(row for conversation in delta["conversations"] for row in conversation["records"]
                   if row["event_id"] == review.event_id)
        full = session_context_module._provider_record(review, frozenset())
        self.assertEqual(row, {**full, "fields": [field for field in full["fields"]
            if field["key"] != "artifact_review.message"], "details": {
                key: value for key, value in full["details"].items() if key != "rationale"}})
        self.assertEqual(next(row for conversation in snapshot.delivery()["conversations"]
            for row in conversation["records"] if row["event_id"] == review.event_id), full)
        self.assertEqual(session_context_module._native_artifact_review_prose((review,), operation, LEAD_ORCHESTRATOR), {})
        changed = replace(review, batch_id=review.batch_id + "_unrelated")
        self.assertNotIn(review.event_id, session_context_module._native_artifact_review_prose(
            tuple(changed if record.event_id == review.event_id else record for record in readable), operation, LEAD_ORCHESTRATOR))
        distinct = replace(review, payload=replace(review.payload, rationale="Distinct host rationale."))
        known = session_context_module._native_artifact_review_prose(
            tuple(distinct if record.event_id == review.event_id else record for record in readable), operation, LEAD_ORCHESTRATOR)
        self.assertEqual(known[review.event_id], frozenset())
        self.assertEqual(session_context_module._provider_record(distinct, frozenset({"artifact_review.message"}),
            known[review.event_id])["details"]["rationale"], "Distinct host rationale.")


    async def test_core_conflict_revision_and_manuscript_atomic_rename_workflows(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        rules = self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH)
        rules.write_text("Always use past tense.\n", encoding="utf-8")
        conflict_id = self.conversation("Conflicting core edit")
        before = self.artifact_snapshot()
        self.backend.enqueue(
            artifact_review_outcome(
                operations=lambda _call,
                material, *, input_payload: [{"evidence_id": material['qualified_read_ids'][ARTIFACT_FIXTURE_RULES_PATH],
                    "kind": "replace", "new_text": "Use present tense in dream passages.\n",
                }],
                evidence_status="verified",
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),

            read_queries=((ARTIFACT_FIXTURE_RULES_PATH, 'Always'),), read_prefix='core_documents/',)
        , control_plan=selection_plan('artifact.changes', before_script_response=2))
        physical_before = len(self.backend.calls)
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conflict_id,
            (f"Edit ${{{ARTIFACT_FIXTURE_RULES_PATH}}} even though the existing rule conflicts." + '\nSupplied requested candidate content:\n' + json.dumps(['Use present tense in dream passages.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        physical = self.backend.calls[physical_before:]
        self.assertEqual(len(physical), 4)
        reads = self.backend.callback_payload(physical[-1])['context_observation']['read_results']
        expected_links = ((ARTIFACT_FIXTURE_RULES_PATH, 'replace'),)
        self.assertEqual(tuple((path, kind) for path, kind, _identity
                               in self.backend.artifact_read_links), expected_links)
        by_path = {row['provenance']['source_locator']: row['evidence_id'] for row in reads}
        self.assertEqual(tuple(identity for _path, _kind, identity
                               in self.backend.artifact_read_links),
                         tuple(by_path[path] for path, _kind in expected_links))
        hits = self.backend.callback_payload(physical[1])['context_observation']['search_results']
        self.assertEqual(tuple(row['read_handle'] for row in reads),
                         tuple(row['read_handle'] for path, _query in expected_links
                               for row in hits
                               if row['provenance']['source_locator'] == path))
        self.assertIsNotNone(proposed.proposal, (
            proposed.kind, proposed.next_action, proposed.state, proposed.validation_details,
            proposed.artifact_proposal is not None,
        ))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, lead_specialist_outcome("conflict"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("revise"), )
        revised = await self.runtime.decide_lead_step(
            lead_confirmation_decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
        self.assertIs(
            revised.turn.artifact_proposal.state,
            ArtifactProposalState.REVISION_REQUIRED, )
        self.assertIsNone(revised.turn.artifact_proposal.reviewed_proposal)
        self.assertIn("review is complete", revised.turn.message)
        self.assertEqual(self.artifact_snapshot(), before)

        await self.record_book_readiness(preserve_documents=True)
        self.workspace.path(_INDEX).write_text("[next](second.md)\n", encoding="utf-8")
        self.workspace.path(_SECOND).write_text("Second scene.\n", encoding="utf-8")
        rename_id = self.conversation("Atomic manuscript rename repair")
        rename_before = self.artifact_snapshot()
        self.backend.enqueue(
            artifact_review_outcome(
                operations=lambda _call, material, *, input_payload: [
                    {"evidence_id": material['qualified_read_ids'][_INDEX],
                     "kind": "replace", "new_text": "[next](third.md)\n"},
                    {"evidence_id": material['qualified_read_ids'][_SECOND],
                     "kind": "rename", "suggested_destination": _THIRD}, ],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_INDEX, _SECOND, _THIRD), contributions=[{'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),

            read_queries=((_INDEX, 'next'), (_SECOND, 'Second')), read_prefix='manuscript/part-01/',)
        , control_plan=selection_plan('artifact.changes', before_script_response=2))
        physical_before = len(self.backend.calls)
        manuscript = await self.runtime.lead_turn(
            self.session_id,
            rename_id,
            (f"Repair ${{{_INDEX}}} and rename ${{{_SECOND}}} atomically." + '\nSupplied requested candidate content:\n' + json.dumps(['[next](third.md)\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        physical = self.backend.calls[physical_before:]
        self.assertEqual(len(physical), 4)
        reads = [row for entry in self.backend.callback_payload(physical[-1])['context_observations']
                 if entry['observation']['observation_type'] == 'context_read_results'
                 for row in entry['observation']['read_results']]
        expected_links = ((_INDEX, 'replace'), (_SECOND, 'rename'))
        self.assertEqual(len(reads), 2)
        self.assertEqual(len({row['evidence_id'] for row in reads}), 2)
        self.assertEqual(len({row['read_handle'] for row in reads}), 2)
        self.assertEqual(tuple((path, kind) for path, kind, _identity
                               in self.backend.artifact_read_links), expected_links)
        by_path = {row['provenance']['source_locator']: row['evidence_id'] for row in reads}
        self.assertEqual(tuple(identity for _path, _kind, identity
                               in self.backend.artifact_read_links),
                         tuple(by_path[path.removeprefix('manuscript/')]
                               for path, _kind in expected_links))
        hits = self.backend.callback_payload(physical[1])['context_observation']['search_results']
        self.assertEqual(tuple(row['read_handle'] for row in reads),
                         tuple(row['read_handle'] for path, _query in expected_links
                               for row in hits
                               if row['provenance']['source_locator'] ==
                                  path.removeprefix('manuscript/')))
        self.assertIs(
            manuscript.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION, )
        self.assertEqual(
            tuple(
                item.operation_kind.value
                for item in manuscript.artifact_proposal.candidate.candidate.change_set.operations
            ),
            ("replace", "rename"), )
        self.assertEqual(
            manuscript.artifact_proposal.candidate.candidate.relationship_validation.projected_dangling_edges,
            (), )
        rejected = await self.runtime.decide_lead_step(
            lead_confirmation_decision(manuscript, CollaborationConfirmationDecision.REJECT) )
        self.assertIs( rejected.turn.artifact_proposal.state, ArtifactProposalState.REJECTED )
        self.assertEqual(self.artifact_snapshot(), rename_before)

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

    async def test_stale_confirmation_and_revision_require_fresh_authority(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        stale_id = self.conversation("Stale candidate")
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "A narrative rule.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),)
        , control_plan=selection_plan('artifact.changes'))
        stale_proposal = await self.runtime.lead_turn(
            self.session_id,
            stale_id,
            ("Create the missing narrative rules." + '\nSupplied requested candidate content:\n' + json.dumps(['A narrative rule.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        call_count = len(self.backend.call_records)
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).write_text( "External current rule.\n", encoding="utf-8" )
        external = self.artifact_snapshot()
        stale = await self.runtime.decide_lead_step(
            lead_confirmation_decision(stale_proposal, CollaborationConfirmationDecision.CONFIRM) )
        self.assertIs(stale.turn.artifact_proposal.state, ArtifactProposalState.STALE)
        self.assertEqual(len(self.backend.call_records), call_count)
        self.assertIn("No provider was called", stale.turn.message)
        self.assertEqual(self.artifact_snapshot(), external)

        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink()
        revise_id = self.conversation("Revise candidate")
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "First candidate.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),
            )
        , control_plan=selection_plan('artifact.changes'))
        first = await self.runtime.lead_turn(
            self.session_id,
            revise_id,
            ("Prepare the first candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['First candidate.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        first_digest = first.artifact_proposal.candidate.candidate.candidate_digest
        before_revision_calls = len(self.backend.call_records)

        def fresh_recommendation(call, _provider):
            self.assertFalse(call.contract.factory.schema_group_protocol)
            self.assertIsNone(call.contract.schema_group_phase)
            def encode_current(call, value):
                from tests.provider_wire_fixture import (
                    result as native_result, lead_fixture_result, lead_fixture_scope_record,
                    lead_fixture_assessment, finite_fixture_selection)
                from tests.lead_fixtures.preparation_data import (
                    compact_recommended as _compact_recommended,
                )
                choices = dict(call.contract.semantic_index.policy.choices)
                self.assertEqual(choices['source_binding'], ('bound',))
                self.assertIn('current_response', choices['source_ids'])
                compact = {key: value[key] for key in (
                    'kind', 'message', 'authoring_scope', 'authoring_assessment')}
                compact['recommended_action'] = _compact_recommended(value['recommended_action'])
                native = native_result(compact, 'lead-response-v9')
                native.pop('writing_task_scope')
                native['task_scope'] = lead_fixture_scope_record(value['authoring_scope'])
                drafting = native['proposal_drafting_request']
                self.assertEqual(drafting['source_ids'], ['current_response'])
                drafting['source_ids'] = finite_fixture_selection(
                    choices['source_ids'], tuple(drafting['source_ids']))
                if choices['preparation_targets'] and 'target_artifact_paths' in drafting:
                    drafting['target_artifact_paths'] = finite_fixture_selection(
                        choices['preparation_targets'], tuple(drafting['target_artifact_paths']))
                native['authoring_assessment'] = lead_fixture_assessment(
                    native['authoring_assessment'], source_choices=choices['input_ids'] + choices['historical_user_source_ids'])
                return self._require_revision_fixture(call, lead_fixture_response(call, lead_fixture_result(native), family=0),
                    first.proposal.authoring_scope, artifact=True)
            return encode_current(
                call,
                {
                    "recommended_action": {
                        "kind": "prepare_artifact", "purpose": "Prepare a fresh candidate.",
                        "instruction": "Prepare a new candidate for the narrative rules.", "expected_result": "An exact separately reviewable candidate.",
                        "source_ids": ["current_response"], "target_paths": [ARTIFACT_FIXTURE_RULES_PATH], "specialist_ids": [], "objective_id": None,
                    },
                    "kind": "recommendation",
                    "message": "The old candidate is superseded; request a fresh candidate.",
                    "scope": "story_specific",
                    "evidence_status": "missing",
                    "evidence_ids": [artifact_evidence_status_id(_provider.callback_payload(call), ARTIFACT_FIXTURE_RULES_PATH)],
                    "proposal": None,
                    "artifact_intent": None,
                    "objective_intent": None,
                    "authoring_scope": lead_fixture_authoring_scope(
                        call,
                        activity="review",
                        paths=(ARTIFACT_FIXTURE_RULES_PATH,), domains=("tone_execution",),
                    ),
                    "authoring_assessment": asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                },
            )

        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            fresh_recommendation, )
        revised = await self.runtime.decide_lead_step(
            lead_confirmation_decision(first, CollaborationConfirmationDecision.REVISE, revision_request=(
                'Revise proposed review.\nSupplied narrative-rule candidate:\n'
                + first.artifact_proposal.candidate.candidate.change_set.operations[0].content.decode("utf-8")
                + '\nYou may give bounded artifact review judgments and prepare a separate proposal; preserve supplied facts. Execution requires separate exact confirmation.'
            )) )
        history = self.history(revise_id)
        self.assertIsNone(project_collaboration_history(history).current_artifact_state, getattr(self, '_revision_fixture_failure', None))
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
            (LEAD_ORCHESTRATOR,), )

        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Second candidate.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),)
        , control_plan=selection_plan('artifact.changes'))
        second = await self.runtime.lead_turn(
            self.session_id,
            revise_id,
            ("Prepare the revised candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['Second candidate.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_digest,
            first_digest, )
        self.assertNotEqual(
            second.confirmation_request_id, first.confirmation_request_id
        )

    async def test_revised_artifact_intent_replaces_candidate_in_the_same_step(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        conversation_id = self.conversation("Replace artifact candidate")
        self.workspace.path(ARTIFACT_FIXTURE_RULES_PATH).unlink(missing_ok=True)
        self.backend.enqueue(
            artifact_review_outcome(
                operations=[ { "kind" : "create" , "new_text" : "First candidate.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),
            )
        , control_plan=selection_plan('artifact.changes'))
        first = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("Prepare the first narrative-rule candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['First candidate.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        revision_fixture = artifact_review_outcome(
            operations=[ { "kind" : "create" , "new_text" : "Revised candidate.\n" , "suggested_path" : ARTIFACT_FIXTURE_RULES_PATH , } ],
            evidence_status="missing",
            evidence_ids=lambda call, _material, *, input_payload: [artifact_evidence_status_id(input_payload, ARTIFACT_FIXTURE_RULES_PATH)],
            authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

            authoring_scope=lambda call: lead_fixture_authoring_scope(
                call, activity='review', domains=('tone_execution',),
                paths=(ARTIFACT_FIXTURE_RULES_PATH,), contributions=[
                    {'domain': domain, 'role': 'constraint_assessor'}
                    for domain in ('tone_execution',)]),)
        def checked_revision(call, provider):
            for response_type in ('answer', 'clarification_question'):
                discussion = {'response_type': response_type, 'response_text': 'Clarify the existing artifact revision.',
                              'response_details': {'used_evidence_ids': {}}}
                self._require_revision_fixture(call, lead_fixture_response(call, discussion, family=0),
                                               first.proposal.authoring_scope, artifact=True)
            return self._require_revision_fixture(call, revision_fixture(call, provider), first.proposal.authoring_scope, artifact=True)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, checked_revision)
        replacement = await self.runtime.decide_lead_step(
            lead_confirmation_decision(first, CollaborationConfirmationDecision.REVISE) )
        second = replacement.turn
        self.assertEqual(second.step_id, first.step_id)
        self.assertIs(
            second.artifact_proposal.state, ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
            getattr(self, '_revision_fixture_failure', None))
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_id,
            first.artifact_proposal.candidate.candidate.candidate_id, )
        self.assertNotEqual(
            second.artifact_proposal.candidate.candidate.candidate_digest,
            first.artifact_proposal.candidate.candidate.candidate_digest, )
        self.assertNotEqual(
            second.confirmation_request_id, first.confirmation_request_id
        )
        states = tuple(
            item.state
            for item in self.artifact_events(
                conversation_id,
                ArtifactProposalStatePayload,
            ) )
        self.assertEqual(
            states,
            (
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
                ArtifactProposalState.SUPERSEDED,
                ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION,
            ), )
        self.assertEqual(
            len(
                self.artifact_events(
                    conversation_id,
                    ArtifactReviewCandidatePayload,
                )
            ),
            2,
        )


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


    async def test_search_read_answer_can_add_an_artifact_candidate(self) -> None:
        from tests.recommendation_fixture import task_assessment
        reference_ids = []
        core_paths = ("core_documents/character_sheets.md", ARTIFACT_FIXTURE_OUTLINE_PATH, ARTIFACT_FIXTURE_RULES_PATH, "core_documents/part_outlines.md")
        for path, content in LEAD_FIXTURE_READINESS_DOCUMENTS:
            if path in (ARTIFACT_FIXTURE_RULES_PATH, "core_documents/part_outlines.md"):
                content = content.replace(". ", ".\n", 1)
            self.workspace.path(path).write_text(content, encoding="utf-8")
        await self.record_book_readiness(preserve_documents=True)
        conversation_id = self.conversation("Search read artifact intent")
        self.backend.enqueue(_visible_outcome("answer", "A prior visible answer."), control_plan=selection_plan('answer.workflow'))
        await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Give one workflow answer.", )

        def answer_with_artifact(call, provider):
            observation = provider.callback_payload(call)["context_observation"]
            evidence_id = observation["read_results"][0]["evidence_id"]
            return artifact_review_outcome(
                operations=[{"kind": "create", "new_text": "A searched scene candidate.\n",
                    "suggested_path": "manuscript/part-02/searched.md"}],
                evidence_ids=[*reference_ids, evidence_id], evidence_status="verified",
                additional_review_domains=(),
                message="The read context supports an exact candidate.",
                authoring_scope=lead_fixture_authoring_scope(call, activity="review",
                    domains=("plot_structure", "character_world", "tone_execution"),
                    paths=("manuscript/part-02/searched.md",)),
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',),
                    actions=('bounded artifact review judgments',
                             'separate proposal preparation'))),
            )(call, provider)

        def search_prior_answer(call, _provider):
            reference_ids[:] = _reference_ids(_workflow_material(_provider.callback_payload(call)))
            return _search_history(call, _provider, text="prior visible answer")

        self.backend.enqueue(
            search_prior_answer,
            _read_history_hit,
            answer_with_artifact,  control_plan=selection_plan('artifact.changes', before_script_response=2))
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("Search and read the prior answer before proposing a scene." + "".join(f" ${{{path}}}" + ("#L1-L1" if path in core_paths[2:] else "") for path in reversed(core_paths)) + "".join(f" ${{{path}}}#L2-L2" for path in reversed(core_paths[2:])) + '\nSupplied requested candidate content:\n' + json.dumps(['A searched scene candidate.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.assertIsNotNone(result.artifact_proposal, result)
        self.assertIs(
            result.artifact_proposal.state,
            ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION, )
        call_kinds = tuple(call.call_kind for call in self.backend.calls[-4:])
        self.assertEqual(
            call_kinds,
            (
                ProviderCallKind.COLLABORATION_INITIAL,
                ProviderCallKind.COLLABORATION_INITIAL,
                ProviderCallKind.COLLABORATION_INITIAL,
            ProviderCallKind.COLLABORATION_INITIAL, ), )
        self.assertEqual([call.payload['response_contract']['schema_selection']['context_delivery']['mode']
                          for call in self.backend.calls[-4:]], ['bootstrap'] * 4)
        self.assertEqual([(call.context_witness, call.context_anchor) for call in self.backend.calls[-4:]],
                         [(None, None)] * 4)
        self.assertNotIn("context_observation", self.backend.calls[-4].payload)
        self.assertNotIn("context_observations", self.backend.calls[-4].payload)
        self.assertEqual(self.backend.callback_payload(self.backend.calls[-3])["context_observation"]["observation_type"], "context_search_results")
        self.assertEqual(self.backend.callback_payload(self.backend.calls[-1])["context_observation"]["observation_type"], "context_read_results")
        from tests.provider_wire_fixture import fixture_schema_node
        bound_call = self.backend.calls[-1]
        native_schema = bound_call.schema
        inner_by_outer = dict(bound_call.contract.outer_inner_schema_ids)
        domain_result_ids = {member.schema_id
            for member in bound_call.contract.semantic_index.contracts
            if type(member.owner) is LeadResultContractOwner}
        issued_answers = {
            member.schema_id: inner_by_outer[member.schema_id]
            for member in bound_call.contract.action_index.contracts
            if member.rule_graph.literal_at(("action_type",)) == "submit_result"
            and inner_by_outer[member.schema_id] in domain_result_ids
        }
        actions = fixture_schema_node(
            native_schema, native_schema["properties"]["agent_action"])["anyOf"]
        artifact_capabilities = []
        for variant in actions:
            native_action = fixture_schema_node(native_schema, variant)
            outer_id = fixture_schema_node(
                native_schema, native_action["properties"]["schema_id"])["enum"][0]
            if outer_id not in issued_answers:
                continue
            native_answer = fixture_schema_node(
                native_schema, native_action["properties"]["task_result"])
            inner_id = fixture_schema_node(
                native_schema, native_answer["properties"]["schema_id"])["enum"][0]
            native_details = fixture_schema_node(
                native_schema, native_answer["properties"]["response_details"])
            native_options, native_records = [native_details], []
            while native_options:
                option = fixture_schema_node(native_schema, native_options.pop())
                if "anyOf" in option:
                    native_options.extend(option["anyOf"])
                else:
                    native_records.append(option["properties"])
            artifact_capabilities.append(
                inner_id == issued_answers[outer_id]
                and any("artifact_change_proposal" in properties for properties in native_records))
        self.assertTrue(any(artifact_capabilities))
        read_evidence_id = self.backend.callback_payload(self.backend.calls[-1])["context_observation"]["read_results"][0]["evidence_id"]
        self.assertIn(
            read_evidence_id,
            result.artifact_proposal.candidate.candidate.grounding_evidence_ids, )
        attached = tuple(fact.artifact_path for fact in result.artifact_proposal.candidate.candidate.relationship_validation.review_facts if fact.reason is ArtifactReviewReason.ATTACHED_CORE_EVIDENCE)
        self.assertEqual(tuple(sorted(attached, key=lambda path: path.encode("utf-8"))), core_paths)

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





if __name__ == "__main__": unittest.main()
