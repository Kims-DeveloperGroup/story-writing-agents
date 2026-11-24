"""Existing artifact candidate creation acceptance definitions."""
from __future__ import annotations

from dataclasses import (
    asdict,
    replace,
)
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.agent_context.sources import session_history as owner_session_history
from story_writing_agents.agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
)
from story_writing_agents.artifact_proposals import serialize_artifact_review_candidate
from story_writing_agents.conversations import (
    ArtifactProposalState,
    ArtifactReviewedProposal,
    CollaborationConfirmationDecision,
    LeadOutcomeKind,
    project_collaboration_history,
    project_collaboration_readable_history,
    session_context as session_context_module,
)
from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
from story_writing_agents.writing_artifacts import ArtifactReviewReason
from tests.lead_fixtures.artifact_review import (
    ARTIFACT_FIXTURE_OUTLINE_PATH,
    ARTIFACT_FIXTURE_RULES_PATH,
    _artifact_synthesis_outcome,
    _reference_ids,
    artifact_evidence_status_id,
    artifact_review_outcome,
)
from tests.lead_fixtures.preparation_data import (
    LEAD_FIXTURE_READINESS_DOCUMENTS,
    read_history_hit as _read_history_hit,
    search_history as _search_history,
    visible_outcome as _visible_outcome,
    lead_confirmation_decision,
    lead_fixture_authoring_scope,
    lead_specialist_outcome,
)
from tests.lead_fixtures.provider_responses import workflow_material as _workflow_material
from tests.provider_wire_fixture import selection_plan
import json


class ArtifactCandidateCreationDefinitions:
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
        synchronization = projection.synchronization.assessment_for_review(reviewed)
        self.assertIsNotNone(synchronization)
        self.assertIsNone(projection.current_artifact_application_request)
        self.assertIn(ARTIFACT_FIXTURE_RULES_PATH, synchronization.census.changes)
        self.assertTrue(synchronization.blockers)
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
