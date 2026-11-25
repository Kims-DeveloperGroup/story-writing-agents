"""Existing artifact candidate revisions acceptance definitions."""
from __future__ import annotations

from dataclasses import asdict
from story_writing_agents.agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactProposalStatePayload,
    ArtifactReviewCandidatePayload,
    CollaborationConfirmationDecision,
    project_collaboration_history,
)
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_RULES_PATH,
    ARTIFACT_FIXTURE_INDEX_PATH as _INDEX,
    ARTIFACT_FIXTURE_SECOND_PATH as _SECOND,
    ARTIFACT_FIXTURE_THIRD_PATH as _THIRD,
    _artifact_synthesis_outcome,
    artifact_evidence_status_id,
    artifact_review_outcome,
)
from tests.lead_fixtures.preparation_data import (
    lead_confirmation_decision,
    lead_fixture_authoring_scope,
    lead_specialist_outcome,
)
from tests.provider_wire_fixture import (
    lead_fixture_response,
    selection_plan,
)
import json


class ArtifactCandidateRevisionDefinitions:
    async def test_core_conflict_revision_and_manuscript_atomic_rename_workflows(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        part_path = "core_documents/part_outlines.md"
        self.workspace.path(part_path).write_text("The first act finishes on the bridge.\n", encoding="utf-8")
        conflict_id = self.conversation("Conflicting core edit")
        before = self.artifact_snapshot()
        self.backend.enqueue(
            artifact_review_outcome(
                operations=lambda _call,
                material, *, input_payload: [{"evidence_id": material['qualified_read_ids'][part_path],
                    "kind": "replace", "new_text": "The first act finishes inside the archive.\n",
                }],
                evidence_status="verified",
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: lead_fixture_authoring_scope(
                    call, activity='review', domains=('plot_structure',),
                    paths=(part_path,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('plot_structure',)]),

            read_queries=((part_path, 'finishes on the bridge'),), read_prefix='core_documents/',)
        , control_plan=selection_plan('artifact.changes', before_script_response=2))
        physical_before = len(self.backend.calls)
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conflict_id,
            (f"Edit ${{{part_path}}} even though the current plot conflicts." + '\nSupplied requested candidate content:\n' + json.dumps(['The first act finishes inside the archive.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        physical = self.backend.calls[physical_before:]
        self.assertEqual(len(physical), 4)
        reads = self.backend.callback_payload(physical[-1])['context_observation']['read_results']
        expected_links = ((part_path, 'replace'),)
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
        self.backend.enqueue_for(PLOT_ARCHITECT, lead_specialist_outcome("conflict"))
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
