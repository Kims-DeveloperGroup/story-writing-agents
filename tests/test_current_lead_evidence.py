"""Current lead evidence conversion contracts."""
from __future__ import annotations
from story_writing_agents.lead_preparation import current_lead_evidence
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
from tests.lead_fixtures.preparation_data import (
    lead_fixture_authoring_scope,
    read_history_hit as _read_history_hit,
)
from tests.provider_wire_fixture import (
    lead_fixture_bound_scope,
    lead_fixture_response,
    selection_plan,
)
from tests.test_artifact_proposal_orchestration import (
    ARTIFACT_FIXTURE_OUTLINE_PATH,
    ArtifactReviewFixture,
)
class CurrentLeadEvidenceTests(ArtifactReviewFixture, LeadOrchestrationTestCase):

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
        self.assertEqual(receipt.locator, ARTIFACT_FIXTURE_OUTLINE_PATH)
        self.assertEqual(receipt.content_digest, empty_digest)
        self.assertNotEqual(receipt.revision_digest, receipt.content_digest)
        def translated(selected):
            return current_lead_evidence.artifact_intent_evidence(selected, session_id=case.session_id, conversation_id=conversation_id, artifact_binding=case.runtime.bind_session_artifacts(case.session_id),
                context_service=case.runtime.agent_context_service,
                current_lead_read_snapshot=case.runtime.current_lead_read_snapshot,
            )
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
        path = case.workspace.path(ARTIFACT_FIXTURE_OUTLINE_PATH)
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
        from story_writing_agents.agent_context.sources.writing_artifact import WritingArtifactSourceAdapter
        from story_writing_agents.agent_context.manifest import ContextSubjectKind
        from story_writing_agents.orchestrator import CONTEXT_SOURCE_POLICY
        from tests import test_artifact_objectives as objectives
        text = "첫째 beat\r\nneedle 민\r\n마지막 beat\n"
        self.workspace.path(ARTIFACT_FIXTURE_OUTLINE_PATH).write_bytes(text.encode("utf-8"))
        conversation_id = self.conversation("Genuine READ evidence")
        def search(call, _provider):
            from tests.provider_wire_fixture import offered_fixture_bindings, indexed_response
            outer, inner = offered_fixture_bindings(call, action_type='search_context', response_type=None, family=1)
            return indexed_response({'action_type': 'search_context', 'artifact_directory_prefix': 'core_documents/',
                'queries': [{'search_text': 'needle', 'source_types': {'story_document': True}}]},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
        def answer(call, provider):
            scope = lead_fixture_bound_scope(call, lead_fixture_authoring_scope(call,
                activity="review", domains=("plot_structure",), paths=(ARTIFACT_FIXTURE_OUTLINE_PATH,)), record=True)
            return lead_fixture_response(call, {"response_type": "clarification_question",
                "response_text": "The selected line is visible; what should change?",
                "response_details": {"task_scope": scope, "planning_target": None}}, family=0)
        self.backend.enqueue(search, _read_history_hit, answer, control_plan=selection_plan('question.authoring', before_script_response=2))
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "Search and read the needle line before deciding.")
        self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
        receipt = next(
            item
            for item in result.evidence.receipts
            if item.acquisition is c.CollaborationEvidenceAcquisition.READ )
        self.assertIs(receipt.acquisition, c.CollaborationEvidenceAcquisition.READ)
        scope = self.runtime.agent_context_service.scope(self.session_id, ContextSubjectKind.CONVERSATION, conversation_id, LEAD_ORCHESTRATOR)
        snapshot = WritingArtifactSourceAdapter(
            WritingArtifactRepository(self.workspace.root),
            scope,
        ).capture(ARTIFACT_FIXTURE_OUTLINE_PATH, CONTEXT_SOURCE_POLICY)
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
                converted, = current_lead_evidence.artifact_intent_evidence(evidence, session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime.bind_session_artifacts(self.session_id),
                    context_service=self.runtime.agent_context_service,
                    current_lead_read_snapshot=self.runtime.current_lead_read_snapshot,
                )
                self.assertEqual(converted.source_digest, whole_sha)
                self.assertEqual(converted.boundary_digest, selected.content_digest)
                self.assertEqual((converted.start_line, converted.end_line), expected_bounds)
                from story_writing_agents.artifact_proposals import ArtifactProposalIntent, ArtifactReplaceIntent, ArtifactPatchIntent
                from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringScope
                intent = (ArtifactReplaceIntent(selected.evidence_id, "Whole replacement.\n")
                          if selected is whole else
                          ArtifactPatchIntent(selected.evidence_id, "Revised 민 line.\r\n"))
                review_scope = AuthoringScope(AuthoringActivity.REVIEW, (ARTIFACT_FIXTURE_OUTLINE_PATH,), (),
                    "Review the exact synthetic whole or ranged candidate.", "synthetic_review", 1)
                candidate = self.runtime._construct_artifact_review_candidate(session_id=self.session_id, conversation_id=conversation_id, turn_id=result.turn_id, step_id=result.step_id, intent=ArtifactProposalIntent((intent,)), evidence=evidence, authoring_scope=review_scope, artifact_binding=self.runtime.bind_session_artifacts(self.session_id))
                self.assertEqual("sha256:" + candidate.change_set.operations[0].base_digest, whole_sha)
                for invalid in (replace(selected, revision_digest=objectives.digest("wrong metadata")), replace(selected, content_digest=objectives.digest("wrong bytes"))):
                    with self.assertRaises(ValueError):
                        current_lead_evidence.artifact_intent_evidence(replace(evidence, receipts=(invalid,)), session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime.bind_session_artifacts(self.session_id),
                            context_service=self.runtime.agent_context_service,
                            current_lead_read_snapshot=self.runtime.current_lead_read_snapshot,
                        )
        # Byte-identical physical replacement is stale despite equal whole/boundary hashes.
        path = self.workspace.path(ARTIFACT_FIXTURE_OUTLINE_PATH)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with self.assertRaises(ValueError):
            current_lead_evidence.artifact_intent_evidence(result.evidence, session_id=self.session_id, conversation_id=conversation_id, artifact_binding=self.runtime.bind_session_artifacts(self.session_id),
                context_service=self.runtime.agent_context_service,
                current_lead_read_snapshot=self.runtime.current_lead_read_snapshot,
            )

