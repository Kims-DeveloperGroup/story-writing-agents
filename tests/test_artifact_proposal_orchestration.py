"""Live lead-review workflows for exact artifact proposals."""

from __future__ import annotations
from tests.provider_wire_fixture import selection_plan
from tests.provider_wire_fixture import lead_fixture_bound_scope, lead_fixture_response

import json
import unittest
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import ReplyTraceOrigin, StoryRuntime
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
from story_writing_agents.conversations import session_context as session_context_module
from story_writing_agents.agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT, )
from story_writing_agents.artifact_proposals import ( serialize_artifact_review_candidate, )
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
    WorkspaceConversationStorageMutationCoordinator, )
from story_writing_agents.conversation_storage.migration import (
    ConversationStorageTransferOutcome,
    transfer_conversation_storage,
    verify_conversation_storage, )
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
    provision_sqlite_conversation_storage, )
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _READINESS_DOCUMENTS,
    _decision,
    _live_material,
    _live_scope,
    _provider_envelope,
    _read_history_hit,
    _search_history,
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


def _status_id(input_payload, locator: str) -> str:
    statuses = input_payload["core_document_statuses"]
    return next(item["evidence_id"] for item in statuses if item["artifact_path"] == locator)


def _artifact_outcome(
    *,
    operations: object,
    evidence_status: str = "verified",
    evidence_ids: object = None,
    additional_review_domains: tuple[str, ...] = (),
    message: str = "I prepared an exact candidate for specialist review.",
    objective_intent: object = None,
    authoring_assessment: object = None,
    authoring_scope=None,
    read_queries: tuple[tuple[str, str], ...] = (),
    read_prefix: str | None = None,
):
    read_phase = 0
    selected_handles = ()
    def respond(call, _provider):
        nonlocal read_phase, selected_handles
        input_payload = _provider.callback_payload(call)
        from tests.provider_wire_fixture import finite_fixture_read_selection, indexed_response
        from tests.provider_wire_fixture import offered_fixture_bindings
        if read_queries and read_phase < 2:
            if read_prefix is None:
                raise ValueError('The caller must supply the intended artifact search prefix')
            _provider.responses.insert(0, respond)
            if read_phase == 0:
                read_phase = 1
                outer, inner = offered_fixture_bindings(call, action_type='search_context',
                    response_type=None, family=1)
                return indexed_response({'action_type': 'search_context',
                    'artifact_directory_prefix': read_prefix,
                    'queries': [{'search_text': query, 'source_types': {'story_document': True}}
                                for _path, query in read_queries]}, outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
            read_phase = 2
            hits = _provider.callback_payload(call)['context_observation']['search_results']
            try:
                selected_handles = tuple(next(row['read_handle'] for row in hits
                    if row['provenance']['source_locator'] == path.removeprefix('manuscript/'))
                    for path, _query in read_queries)
            except RuntimeError as failure:
                if (type(failure) is RuntimeError
                        and type(failure.__cause__) is StopIteration):
                    from tests.recommendation_fixture import _selection_failure_note
                    _selection_failure_note(
                        failure, hits, read_queries, point='artifact137')
                raise
            _provider.used_handles = selected_handles
            outer, inner = offered_fixture_bindings(call, action_type='read_context', response_type=None, family=0)
            return indexed_response({'action_type': 'read_context', 'read_handles': finite_fixture_read_selection(
                    call.contract.offers.issued_handle_aliases, selected_handles)},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        material = _workflow_material(input_payload)
        if read_queries:
            reads = ([row for entry in input_payload['context_observations']
                      if entry['observation']['observation_type'] == 'context_read_results'
                      for row in entry['observation']['read_results']]
                     if call.contract.schema_group_phase is not None else
                     input_payload['context_observation']['read_results'])
            paths = tuple(path for path, _query in read_queries)
            locators = tuple(path.removeprefix('manuscript/') for path in paths)
            if (len(set(paths)) != len(paths) or len(set(locators)) != len(paths)
                    or len(selected_handles) != len(paths)
                    or len(set(selected_handles)) != len(paths)):
                raise ValueError('Caller paths and selected issued handles must be unambiguous')
            if type(reads) is not list or len(reads) != len(paths):
                raise ValueError('The READ must return exactly the selected issued handles')
            returned_handles = tuple(row['read_handle'] for row in reads)
            if (len(set(returned_handles)) != len(returned_handles)
                    or set(returned_handles) != set(selected_handles)):
                raise ValueError('The READ has duplicate, missing, extra or foreign handles')
            by_handle = dict(zip(returned_handles, reads, strict=True))
            qualified = {}
            for path, locator, handle in zip(paths, locators, selected_handles, strict=True):
                row = by_handle[handle]
                if (row['source']['source_type'] != 'story_document'
                        or row['provenance']['authority_type'] != 'writing_artifact_repository'
                        or row['provenance']['source_locator'] != locator):
                    raise ValueError('The READ source differs from the selected caller artifact')
                qualified[path] = row['evidence_id']
            if (set(qualified) != set(paths)
                    or len(set(qualified.values())) != len(paths)):
                raise ValueError('The READ must complete the exact caller-supplied artifact set')
            material = {**material, 'qualified_read_ids': qualified}
        resolved_operations = (
            operations(call, material, input_payload=input_payload) if callable(operations) else operations )
        if read_queries:
            by_id = {identity: path for path, identity in qualified.items()}
            _provider.backend.artifact_read_links = tuple(
                (by_id[operation['evidence_id']], operation['kind'], operation['evidence_id'])
                for operation in resolved_operations)
        resolved_evidence = (
            _reference_ids(material)
            if evidence_ids is None
            else (
                evidence_ids(call, material, input_payload=input_payload) if callable(evidence_ids) else evidence_ids
            ) )
        scope = authoring_scope(call) if callable(authoring_scope) else authoring_scope
        from copy import deepcopy
        from story_writing_agents.agent_context.service import BoundContextProviderCallRequest
        if isinstance(call, BoundContextProviderCallRequest):
            from tests.provider_wire_fixture import (
                result, lead_fixture_scope, lead_fixture_result, lead_fixture_response, lead_fixture_assessment)
            from tests.test_lead_orchestration import _compact_scope, _compact_objective
            value = {'kind': 'proposal', 'message': message, 'artifact_intent': {'operations': resolved_operations}}
            if scope is not None:
                value['authoring_scope'] = _compact_scope(scope)
            if authoring_assessment is not None:
                value['authoring_assessment'] = deepcopy(authoring_assessment)
            objective = (objective_intent(call, material) if callable(objective_intent) else objective_intent)
            if objective is not None:
                value['artifact_intent']['objective_intent'] = _compact_objective(objective)
            native = result(value, 'lead-response-v9')
            if 'authoring_assessment' in native:
                choices = dict(call.contract.semantic_index.policy.choices)
                sources = (choices['input_ids'] if choices['source_binding'] == ('unbound',) else choices['source_ids'])
                native['authoring_assessment'] = lead_fixture_assessment(
                    native['authoring_assessment'], source_choices=choices['input_ids'])
            if 'writing_task_scope' in native:
                native['task_scope'] = lead_fixture_bound_scope(call, native.pop('writing_task_scope'))
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        return _provider_envelope(
            call,
            {
                "recommended_action": None,
                "artifact_intent": { "additional_review_domains": list(additional_review_domains),
                    "operations": resolved_operations, },
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
                **({"authoring_assessment": authoring_assessment(call, material) if callable(authoring_assessment) else authoring_assessment} if authoring_assessment is not None else {}),
                **({} if scope is None else {"authoring_scope": scope}),
            },
        )

    return respond


def _artifact_synthesis_outcome(
    decision: str = "propose",
    *,
    malformed: str | None = None,
    message: str = "The exact candidate review is complete.",
):
    def respond(call, _provider):
        value = { "decision": decision, "message": message, }
        if malformed == "omit_rationale": value.pop("message")
        return _provider_envelope(call, value)

    return respond


class ArtifactProposalOrchestrationTests(LeadOrchestrationTestCase):
    def artifact_snapshot(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(
            (item.path, item.content)
            for item in WritingArtifactRepository(
                self.workspace.root
            ).capture_complete_state() )

    def artifact_events(self, conversation_id: str, payload_type: type) -> tuple:
        return tuple(
            event.payload
            for event in self.history(conversation_id)
            if type(event.payload) is payload_type )

    async def reviewed_create(
        self,
        conversation_id: str,
        *,
        assessment: str = "supports",
        decision: str = "propose",
    ):
        from tests.recommendation_fixture import task_assessment
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Use restrained close-third narration.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),
            )
        , control_plan=selection_plan('artifact.changes'))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("Complete the missing narrative rules." + '\nSupplied requested candidate content:\n' + json.dumps(['Use restrained close-third narration.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.backend.enqueue_for( EXECUTION_SPECIALIST, _specialist_outcome(assessment), )
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome(decision), )
        decided = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
        return proposed, decided

    async def test_incomplete_core_workflow_is_reviewed_only_after_confirmation(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        before_calls = len(self.backend.call_records)
        conversation_id = self.conversation("Complete missing core")
        self.workspace.path(_RULES).unlink(missing_ok=True)
        before = self.artifact_snapshot()
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Use restrained close-third narration.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("supports"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("propose"), )
        storage = self.runtime.conversation_storage_services
        context = SessionHistorySourceAdapter(storage.catalog, storage.event_store, self.session_id)
        prior = context.capture().coverage
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )

        self.assertIs( decision.turn.artifact_proposal.state, ArtifactProposalState.REVIEWED )
        reviewed = decision.turn.artifact_proposal.reviewed_proposal
        self.assertIsNotNone(reviewed)
        self.assertFalse(reviewed.apply_authority_granted)
        self.assertFalse(reviewed.artifact_write_occurred)
        self.assertFalse(reviewed.git_action_occurred)
        self.assertEqual(self.artifact_snapshot(), before)
        from tests.provider_wire_fixture import artifact_candidate as wire_candidate
        from tests.test_agent_context_accounting import _expanded_context_payload
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
        rules = self.workspace.path(_RULES)
        rules.write_text("Always use past tense.\n", encoding="utf-8")
        conflict_id = self.conversation("Conflicting core edit")
        before = self.artifact_snapshot()
        self.backend.enqueue(
            _artifact_outcome(
                operations=lambda _call,
                material, *, input_payload: [{"evidence_id": material['qualified_read_ids'][_RULES],
                    "kind": "replace", "new_text": "Use present tense in dream passages.\n",
                }],
                evidence_status="verified",
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('tone_execution',)]),

            read_queries=((_RULES, 'Always'),), read_prefix='core_documents/',)
        , control_plan=selection_plan('artifact.changes', before_script_response=2))
        physical_before = len(self.backend.calls)
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conflict_id,
            (f"Edit ${{{_RULES}}} even though the existing rule conflicts." + '\nSupplied requested candidate content:\n' + json.dumps(['Use present tense in dream passages.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        physical = self.backend.calls[physical_before:]
        self.assertEqual(len(physical), 4)
        reads = self.backend.callback_payload(physical[-1])['context_observation']['read_results']
        expected_links = ((_RULES, 'replace'),)
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
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("conflict"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _artifact_synthesis_outcome("revise"), )
        revised = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
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
            _artifact_outcome(
                operations=lambda _call, material, *, input_payload: [
                    {"evidence_id": material['qualified_read_ids'][_INDEX],
                     "kind": "replace", "new_text": "[next](third.md)\n"},
                    {"evidence_id": material['qualified_read_ids'][_SECOND],
                     "kind": "rename", "suggested_destination": _THIRD}, ],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                authoring_scope=lambda call: _live_scope(
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
        reads = self.backend.callback_payload(physical[-1])['context_observation']['read_results']
        expected_links = ((_INDEX, 'replace'), (_SECOND, 'rename'))
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
            _decision(manuscript, CollaborationConfirmationDecision.REJECT) )
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
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "A narrative rule.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
        self.workspace.path(_RULES).write_text( "External current rule.\n", encoding="utf-8" )
        external = self.artifact_snapshot()
        stale = await self.runtime.decide_lead_step(
            _decision(stale_proposal, CollaborationConfirmationDecision.CONFIRM) )
        self.assertIs(stale.turn.artifact_proposal.state, ArtifactProposalState.STALE)
        self.assertEqual(len(self.backend.call_records), call_count)
        self.assertIn("No provider was called", stale.turn.message)
        self.assertEqual(self.artifact_snapshot(), external)

        self.workspace.path(_RULES).unlink()
        revise_id = self.conversation("Revise candidate")
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "First candidate.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
                from tests.test_lead_orchestration import _compact_recommended
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
                    native['authoring_assessment'], source_choices=choices['input_ids'])
                return self._require_revision_fixture(call, lead_fixture_response(call, lead_fixture_result(native), family=0),
                    first.proposal.authoring_scope, artifact=True)
            return encode_current(
                call,
                {
                    "recommended_action": {
                        "kind": "prepare_artifact", "purpose": "Prepare a fresh candidate.",
                        "instruction": "Prepare a new candidate for the narrative rules.", "expected_result": "An exact separately reviewable candidate.",
                        "source_ids": ["current_response"], "target_paths": [_RULES], "specialist_ids": [], "objective_id": None,
                    },
                    "kind": "recommendation",
                    "message": "The old candidate is superseded; request a fresh candidate.",
                    "scope": "story_specific",
                    "evidence_status": "missing",
                    "evidence_ids": [_status_id(_provider.callback_payload(call), _RULES)],
                    "proposal": None,
                    "artifact_intent": None,
                    "objective_intent": None,
                    "authoring_scope": _live_scope(
                        call,
                        activity="review",
                        paths=(_RULES,), domains=("tone_execution",),
                    ),
                    "authoring_assessment": asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),
                },
            )

        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            fresh_recommendation, )
        revised = await self.runtime.decide_lead_step(
            _decision(first, CollaborationConfirmationDecision.REVISE, revision_request=(
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
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "Second candidate.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
        self.workspace.path(_RULES).unlink(missing_ok=True)
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "First candidate.\n" , "suggested_path" : _RULES , } ],
                evidence_status="missing",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
        revision_fixture = _artifact_outcome(
            operations=[ { "kind" : "create" , "new_text" : "Revised candidate.\n" , "suggested_path" : _RULES , } ],
            evidence_status="missing",
            evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
            authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

            authoring_scope=lambda call: _live_scope(
                call, activity='review', domains=('tone_execution',),
                paths=(_RULES,), contributions=[
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
            _decision(first, CollaborationConfirmationDecision.REVISE) )
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

    async def test_all_assessments_are_preserved_and_partial_failure_never_reviews(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        assessments = (
            "supports",
            "concern",
            "conflict",
            "insufficient_evidence", )
        for ordinal, assessment in enumerate(assessments, start=1):
            with self.subTest(assessment=assessment):
                conversation_id = self.conversation(f"Assessment {ordinal}")
                proposed, decided = await self.reviewed_create(
                    conversation_id,
                    assessment=assessment, )
                reviewed = decided.turn.artifact_proposal.reviewed_proposal
                self.assertEqual(
                    reviewed.reviews[0].assessment,
                    CollaborationSpecialistAssessment(assessment), )
                expected_unresolved = (
                    ()
                    if assessment == "supports"
                    else proposed.proposal.assignments[0].input_ids )
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

        self.workspace.path(_RULES).write_text(dict(_READINESS_DOCUMENTS)[_RULES], encoding="utf-8")
        await self.record_book_readiness(preserve_documents=True)
        failed_id = self.conversation("Partial reviewer failure")
        self.backend.enqueue(
            _artifact_outcome(
                operations=[ { "kind" : "create" , "new_text" : "New scene.\n" , "suggested_path" : "manuscript/part-02/new.md" , } ],
                evidence_status="incomplete",
                evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _OUTLINE)],
                additional_review_domains=("plot_structure",),
                authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('plot_structure', 'tone_execution'),
                    paths=('manuscript/part-02/new.md',), contributions=[
                        {'domain': domain, 'role': 'constraint_assessor'}
                        for domain in ('plot_structure', 'tone_execution')]),)
        , control_plan=selection_plan('artifact.changes'))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            failed_id,
            ("Prepare a new scene candidate." + '\nSupplied requested candidate content:\n' + json.dumps(['New scene.\n'])
             + '\nYou may give bounded artifact review judgments and prepare separate proposals; preserve supplied facts. Revision and execution require separate exact confirmation.'),
        )
        self.assertIsNotNone(proposed.proposal, (
            proposed.kind, proposed.next_action, proposed.state, proposed.validation_details,
            proposed.artifact_proposal is not None,
        ))
        for assignment in proposed.proposal.assignments:
            response = (
                (
                    lambda call, _provider: _provider_envelope(
                        call,
                        { "assessment": "supports", "assignment_id": "wrong_assignment", "input_ids": [], "message": "Malformed reviewer identity.", },
                    )
                )
                if assignment.specialist_id == PLOT_ARCHITECT
                else _specialist_outcome("supports")
            )
            self.backend.enqueue_for(assignment.specialist_id, response)
        review_call_start = len(self.backend.call_records)
        failed = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
        self.assertIs(failed.turn.artifact_proposal.state, ArtifactProposalState.FAILED)
        self.assertIsNone(failed.turn.artifact_proposal.reviewed_proposal)
        self.assertEqual(
            self.artifact_events(failed_id, ArtifactReviewedProposal),
            (), )
        failures = self.artifact_events(failed_id, ArtifactReviewFailurePayload)
        self.assertEqual(failures[-1].code, ArtifactReviewFailureCode.REVIEW_INCOMPLETE)
        candidate_value = json.loads(
            serialize_artifact_review_candidate(
                proposed.artifact_proposal.candidate.candidate
            ) )
        specialist_calls = self.backend.call_records[review_call_start:]
        self.assertCountEqual(
            tuple(agent_id for agent_id, _call in specialist_calls),
            tuple(item.specialist_id for item in proposed.proposal.assignments), )
        from tests.provider_wire_fixture import artifact_candidate as wire_candidate
        from tests.test_agent_context_accounting import _expanded_context_payload
        for agent_id, call in specialist_calls:
            with self.subTest(specialist=agent_id):
                material = (_expanded_context_payload(call.payload) if "shared_context_objects" in call.payload else call.payload)["task_input"]
                disclosed = next(item["artifact_candidate"] for item in material["inputs"]
                                 if item["input_source_type"] == "artifact_candidate")
                self.assertEqual(disclosed, wire_candidate(candidate_value))

    async def test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        await self.record_book_readiness()
        failure_events = []
        for ordinal, malformed in enumerate(("omit_rationale",), start=1):
            mismatch_id = self.conversation(f"Synthesis mismatch {ordinal}")
            self.workspace.path(_RULES).unlink(missing_ok=True)
            self.backend.enqueue(
                _artifact_outcome(
                    operations=[ { "kind" : "create" , "new_text" : "Rule candidate.\n" , "suggested_path" : _RULES , } ],
                    evidence_status="missing",
                    evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],
                    authoring_assessment=asdict(task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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
                _specialist_outcome("conflict"), )
            malformed_response = _artifact_synthesis_outcome( "propose", malformed=malformed )
            self.backend.enqueue_for(
                LEAD_ORCHESTRATOR,
                malformed_response,
                malformed_response, )
            mismatch = await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )
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

    async def test_reviewed_history_transfers_with_filesystem_sqlite_parity(
        self,
    ) -> None:
        await self.record_book_readiness()
        conversation_id = self.conversation("Artifact storage parity")
        _proposed, decided = await self.reviewed_create(conversation_id)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        filesystem_history = self.history(conversation_id)

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

    async def test_search_read_answer_can_add_an_artifact_candidate(self) -> None:
        from tests.recommendation_fixture import task_assessment
        reference_ids = []
        core_paths = ("core_documents/character_sheets.md", _OUTLINE, _RULES, "core_documents/part_outlines.md")
        for path, content in _READINESS_DOCUMENTS:
            if path in (_RULES, "core_documents/part_outlines.md"):
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
            return _artifact_outcome(
                operations=[{"kind": "create", "new_text": "A searched scene candidate.\n",
                    "suggested_path": "manuscript/part-02/searched.md"}],
                evidence_ids=[*reference_ids, evidence_id], evidence_status="verified",
                additional_review_domains=(),
                message="The read context supports an exact candidate.",
                authoring_scope=_live_scope(call, activity="review",
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
        issued_answers = {
            member.schema_id: inner_by_outer[member.schema_id]
            for member in bound_call.contract.action_index.contracts
            if member.rule_graph.literal_at(("action_type",)) == "submit_result"
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
        self.workspace.path(_RULES).unlink(missing_ok=True)
        conversation_id = self.conversation("Reject provider authority")
        before = self.artifact_snapshot()
        invalid = _artifact_outcome(
            operations=[ { "apply_authority_granted" : True , "kind" : "create" , "new_text" : "Must not survive.\n" , "suggested_path" : _RULES , } ],
            evidence_status="missing",
            evidence_ids=lambda call, _material, *, input_payload: [_status_id(input_payload, _RULES)],

                authoring_assessment=asdict(task_assessment(
                    foundation_ids=('user_input',),
                    actions=(
                        'bounded artifact review judgments', 'separate proposal preparation'))),

                authoring_scope=lambda call: _live_scope(
                    call, activity='review', domains=('tone_execution',),
                    paths=(_RULES,), contributions=[
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


    async def test_empty_core_status_evidence_requires_selected_fresh_zero_byte_source(self):
        import hashlib
        from story_writing_agents import BackendProtocolError
        from dataclasses import replace
        from story_writing_agents import conversations as c
        from tests import test_artifact_objectives as objectives
        case = objectives.ObjectiveCase()
        case.setUp()
        # Adopt synchronous cleanup from this setup-only async fixture.
        for cleanup, args, kwargs in case._cleanups: self.addCleanup(cleanup, *args, **kwargs)
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
        valid = translated(replace(evidence, receipts=(receipt,)))
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
        with self.assertRaises(BackendProtocolError): translated(evidence)
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
            from tests.provider_wire_fixture import offered_fixture_bindings, indexed_response
            outer, inner = offered_fixture_bindings(call, action_type='search_context', response_type=None, family=1)
            return indexed_response({'action_type': 'search_context', 'artifact_directory_prefix': 'core_documents/',
                'queries': [{'search_text': 'needle', 'source_types': {'story_document': True}}]},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        def answer(call, provider):
            scope = lead_fixture_bound_scope(call, _live_scope(call,
                activity="review", domains=("plot_structure",), paths=(_OUTLINE,)), record=True)
            return lead_fixture_response(call, {"response_type": "clarification_question",
                "response_text": "The selected line is visible; what should change?",
                "response_details": {"task_scope": scope}}, family=0)
        self.backend.enqueue(search, _read_history_hit, answer, control_plan=selection_plan('question.authoring', before_script_response=2))
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Search and read the needle line before deciding.")
        self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
        receipt = next(
            item
            for item in result.evidence.receipts
            if item.acquisition is c.CollaborationEvidenceAcquisition.READ )
        self.assertIs(receipt.acquisition, c.CollaborationEvidenceAcquisition.READ)
        scope = self.runtime._agent_context_service.scope(self.session_id, ContextSubjectKind.CONVERSATION, conversation_id, LEAD_ORCHESTRATOR)
        snapshot = WritingArtifactSourceAdapter(
            WritingArtifactRepository(self.workspace.root),
            scope,
        ).capture(_OUTLINE, CONTEXT_SOURCE_POLICY)
        whole_sha = snapshot.descriptor.content_digest
        self.assertEqual(whole_sha, objectives.digest(text))
        self.assertNotEqual(snapshot.descriptor.revision, whole_sha)
        # Full-span READ keeps receipt identity; the artifact bridge normalizes whole bounds.
        ranged = replace(receipt, boundary_kind="artifact_lines", boundary_start=2, boundary_end=2, start_line=2, end_line=2, content_digest=objectives.digest("needle 민\r\n"), utf8_bytes=len("needle 민\r\n".encode("utf-8")))
        whole = replace(ranged, boundary_start=1, boundary_end=3, start_line=1, end_line=3,
                        content_digest=whole_sha, utf8_bytes=len(text.encode("utf-8")))
        for selected, expected_bounds in ((whole, (None, None)), (ranged, (2, 2))):
            with self.subTest(boundary="full_span" if selected is whole else "partial"):
                evidence = replace(result.evidence, receipts=(selected,))
                converted, = self.runtime._artifact_intent_evidence(evidence, session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime._session_artifact_binding(self.session_id))
                self.assertEqual(converted.source_digest, whole_sha)
                self.assertEqual(converted.boundary_digest, selected.content_digest)
                self.assertEqual((converted.start_line, converted.end_line), expected_bounds)
                from story_writing_agents.artifact_proposals import ArtifactProposalIntent, ArtifactReplaceIntent, ArtifactPatchIntent
                from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope
                intent = (ArtifactReplaceIntent(selected.evidence_id, "Whole replacement.\n")
                          if selected is whole else
                          ArtifactPatchIntent(selected.evidence_id, "Revised 민 line.\r\n"))
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


if __name__ == "__main__": unittest.main()
