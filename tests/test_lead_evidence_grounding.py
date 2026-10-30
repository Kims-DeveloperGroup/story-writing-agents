"""Exhaustive acceptance coverage for runtime-grounded lead judgments."""

from __future__ import annotations
from tests.provider_wire_fixture import selection_plan
from story_writing_agents import authoring_admission as admission
from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment

from tests.test_lead_orchestration import (
    _exploration_outcome, _current_exploration_outcome, _read_history_hit)
from tests.provider_wire_fixture import indexed_response, offered_fixture_bindings
from story_writing_agents import lead_exploration as exploration
import hashlib

import json
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace

from story_writing_agents import StoryRuntime
from story_writing_agents.agent_context.service import (
    BoundContextProviderCallRequest, ContextProviderTerminalCode)
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.conversations import (
    CollaborationAppendError,
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationNextAction,
    CollaborationProviderCallKind,
    CollaborationStepState,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    collaboration_proposal_digest,
    encode_collaboration_event,
    validate_collaboration_candidate_batch,
)
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.writing_artifacts import (
    ReferenceAuthorizationErrorCode,
    ReferenceOperationFailure,
)

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _LeadContextProvider,
    _confirmed_premise,
    _decision,
    _live_scope,
    _provider_envelope,
    _specialist_outcome,
    _submitted_calls, _synthesis_outcome,
    _visible_outcome,
)
from tests.collaboration_contract_fixture import (
    LEAD,
    RUNTIME,
    USER,
    make_batch,
    make_draft,
    provider_call_drafts,
    store_batch,
)


_DIGEST_A = "sha256:" + "1" * 64
_DIGEST_B = "sha256:" + "2" * 64
_OPERATION = "lead_turn"
_RESULT = "outcome_turn"


def _attached(
    evidence_id: str = "reference_1",
    *,
    locator: str = "${core_documents/global_outline.md}",
    ordinal: int = 1,
    utf8_bytes: int = 10,
    ranged: bool = False,
) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id=evidence_id, acquisition=CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
        operation_id=_OPERATION, result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A, source_id=f"source_{ordinal}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT, locator=locator,
        source_state=CollaborationInputState.PRESENT, revision_digest=_DIGEST_A,
        content_digest=_DIGEST_B, utf8_bytes=utf8_bytes,
        boundary_kind="artifact_lines" if ranged else "whole", boundary_start=2 if ranged else None,
        boundary_end=3 if ranged else None,
        start_line=2 if ranged else None,
        end_line=3 if ranged else None,
        ordinal=ordinal,
    )


def _core_status(
    *,
    evidence_id: str = "status_1",
    state: CollaborationInputState = CollaborationInputState.PRESENT,
    utf8_bytes: int = 10,
) -> CollaborationEvidenceReceipt:
    absent = state is CollaborationInputState.ABSENT
    return CollaborationEvidenceReceipt(evidence_id=evidence_id,
        acquisition=CollaborationEvidenceAcquisition.CORE_STATUS, operation_id=_OPERATION,
        result_correlation_id=_RESULT, manifest_digest=_DIGEST_A, source_id=f"source_{evidence_id}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT, locator=f"core_documents/{evidence_id}.md",
        source_state=state,
        revision_digest=None if absent else _DIGEST_A,
        content_digest=None if absent else _DIGEST_B,
        utf8_bytes=0 if absent else utf8_bytes,
        ordinal=int(evidence_id.rsplit("_", 1)[1]),
    )


def _zero_hit(
    *,
    kinds: tuple[CollaborationInputKind, ...] = (
        CollaborationInputKind.WRITING_ARTIFACT,
    ),
) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id="search_1",
        acquisition=CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH,
        operation_id=_OPERATION,
        result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A,
        source_state=CollaborationInputState.ABSENT,
        action_index=1,
        query_digest=_DIGEST_A,
        query_count=1,
        authorized_source_ids=("source_a",),
        authorized_source_kinds=kinds,
        authorized_source_fingerprint=_DIGEST_B,
    )


def _evidence_for(
    scope: LeadOutcomeScope,
    status: LeadEvidenceStatus,
) -> CollaborationLeadEvidence:
    if scope is LeadOutcomeScope.WORKFLOW_META:
        receipts: tuple[CollaborationEvidenceReceipt, ...] = (
            () if status is LeadEvidenceStatus.NOT_APPLICABLE else (_core_status(),)
        )
    elif status is LeadEvidenceStatus.NOT_APPLICABLE:
        receipts = ()
    elif status is LeadEvidenceStatus.VERIFIED:
        receipts = (_attached(),)
    elif status is LeadEvidenceStatus.MISSING:
        receipts = (
            _core_status(evidence_id="status_2", state=CollaborationInputState.ABSENT,),
        )
    elif status is LeadEvidenceStatus.EMPTY:
        receipts = (_core_status(utf8_bytes=0),)
    elif status is LeadEvidenceStatus.INCOMPLETE:
        receipts = (_core_status(),)
    else:
        receipts = (
            _attached(),
            _attached("reference_2", locator="${manuscript/part-01/opening.md}#L2-L3", ordinal=2, ranged=True,),
        )
    return CollaborationLeadEvidence(scope, status, receipts)


class DurableLeadEvidenceMatrixTests(unittest.TestCase):
    """Pin the complete 2 x 6 x 5 closed semantic matrix."""

    def test_all_sixty_scope_status_kind_cells_are_closed_and_exact(self) -> None:
        normal_kinds = (LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION, LeadOutcomeKind.PROPOSAL)
        allowed_story = {
            LeadEvidenceStatus.VERIFIED: set(normal_kinds),
            LeadEvidenceStatus.MISSING: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.EMPTY: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.INCOMPLETE: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.CRITIQUE,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.CONFLICTING: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.CRITIQUE,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
        }
        accepted = 0
        rejected = 0
        for scope in (LeadOutcomeScope.STORY_SPECIFIC, LeadOutcomeScope.WORKFLOW_META):
            for status in LeadEvidenceStatus:
                evidence = _evidence_for(scope, status)
                for kind in normal_kinds:
                    expected = (
                        scope is LeadOutcomeScope.WORKFLOW_META
                        and status is LeadEvidenceStatus.NOT_APPLICABLE
                        and kind is not LeadOutcomeKind.PROPOSAL
                    ) or (
                        scope is LeadOutcomeScope.STORY_SPECIFIC
                        and kind in allowed_story.get(status, set())
                    )
                    with self.subTest(scope=scope.value, status=status.value, kind=kind.value,):
                        if expected:
                            payload = CollaborationLeadJudgmentPayload(kind, "Visible lead judgment.", evidence,)
                            self.assertIs(payload.evidence, evidence)
                            accepted += 1
                        else:
                            with self.assertRaises(ValueError):
                                CollaborationLeadJudgmentPayload(kind, "Visible lead judgment.", evidence,)
                            rejected += 1
        self.assertEqual((accepted, rejected), (23, 37))

        from story_writing_agents.specialist_routing import (
            ArtifactReviewDomain, AuthoringActivity, AuthoringContribution,
            AuthoringContributionRole, AuthoringIntent, AuthoringScope,
        )
        path = "core_documents/global_outline.md"
        domain = ArtifactReviewDomain.PLOT_STRUCTURE
        scope = AuthoringScope(
            AuthoringActivity.REVIEW, (path,), (domain,), "Report the observed outline.", "task", 1,
            schema_version=3, intent=AuthoringIntent.REVIEW_ONLY,
            contributions=(AuthoringContribution(domain, AuthoringContributionRole.CONSTRAINT_ASSESSOR),))
        unrelated_empty = _core_status(evidence_id="status_2", utf8_bytes=0)
        read = replace(_attached("read_1"), acquisition=CollaborationEvidenceAcquisition.READ,
                       source_kind=CollaborationInputKind.WRITING_ARTIFACT, locator=path,
                       ordinal=None, action_index=2, origin_action_index=1)
        for receipt, whole_source in (
            (read, True), (replace(read, boundary_kind="artifact_lines", boundary_start=2, boundary_end=3,
                                   start_line=2, end_line=3), False),
            (_attached(), True), (_attached(locator=f"${{{path}}}#L2-L3", ranged=True), False),
        ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, (unrelated_empty, receipt))
            self.assertEqual(evidence.factual_writing_read_facts(),
                             ((receipt.evidence_id, path, whole_source),))
            payload = CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.ANSWER, "The observed text is present.", evidence, scope)
            self.assertIs(payload.evidence, evidence)
            self.assertIs(payload.evidence.status, LeadEvidenceStatus.INCOMPLETE)
            self.assertIsNone(payload.core_document_readiness)
        for receipts in (
            (unrelated_empty,), (unrelated_empty, _zero_hit()),
            (unrelated_empty, replace(read, utf8_bytes=0)),
            (unrelated_empty, replace(read, source_kind=CollaborationInputKind.CONVERSATION_EVENT,
                                      locator="event_1")),
        ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, receipts)
            self.assertEqual(evidence.factual_writing_read_facts(), ())
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(LeadOutcomeKind.ANSWER, "Unsupported answer.", evidence, scope)
        inexact_reference = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE,
            (unrelated_empty, replace(_attached(), locator=f"Read ${{{path}}}")))
        with self.assertRaisesRegex(ValueError, "factual READ reference is not exact"):
            CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.ANSWER, "Unsupported reference.", inexact_reference, scope)
        read_evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, (unrelated_empty, read))
        other_domain = ArtifactReviewDomain.CHARACTER_WORLD
        for invalid_scope in (
            replace(scope, target_paths=(), domains=(other_domain,), contributions=(
                AuthoringContribution(other_domain, AuthoringContributionRole.CONSTRAINT_ASSESSOR),)),
            replace(scope, target_paths=("core_documents/part_outlines.md",)),
            replace(scope, schema_version=2, contributions=()),
            replace(scope, activity=AuthoringActivity.DRAFT, intent=AuthoringIntent.AUTHORING,
                    contributions=(AuthoringContribution(domain, AuthoringContributionRole.DRAFT_OWNER),)),
        ):
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(
                    LeadOutcomeKind.ANSWER, "Unsupported scope.", read_evidence, invalid_scope)
        for status in (LeadEvidenceStatus.MISSING, LeadEvidenceStatus.EMPTY, LeadEvidenceStatus.CONFLICTING):
            previous = _evidence_for(LeadOutcomeScope.STORY_SPECIFIC, status)
            evidence = replace(previous, receipts=previous.receipts + (read,))
            self.assertEqual(evidence.factual_writing_read_facts()[-1], (read.evidence_id, path, True))
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(LeadOutcomeKind.ANSWER, "Unsupported status.", evidence, scope)

    def test_missing_search_scope_and_conflict_distinctness_fail_closed(self) -> None:
        for kinds, accepted in (
            ((CollaborationInputKind.WRITING_ARTIFACT,), True),
            (
                (
                    CollaborationInputKind.WRITING_ARTIFACT,
                    CollaborationInputKind.CONVERSATION_EVENT,
                ),
                True,
            ),
            ((CollaborationInputKind.CONVERSATION_EVENT,), False),
            ((CollaborationInputKind.AGENT_STATE,), False),
        ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC,
                LeadEvidenceStatus.MISSING,
                (_zero_hit(kinds=kinds),),
            )
            with self.subTest(kinds=kinds):
                if accepted:
                    CollaborationLeadJudgmentPayload(
                        LeadOutcomeKind.RECOMMENDATION,
                        "No matching canonical writing artifact was found.",
                        evidence,
                    )
                else:
                    with self.assertRaises(ValueError):
                        CollaborationLeadJudgmentPayload(
                            LeadOutcomeKind.RECOMMENDATION,
                            "No matching canonical writing artifact was found.",
                            evidence,
                        )

        first = _attached()
        duplicate_content = replace(first, evidence_id="reference_2", ordinal=2)
        evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC,
            LeadEvidenceStatus.CONFLICTING,
            (first, duplicate_content),
        )
        with self.assertRaises(ValueError):
            CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.CRITIQUE,
                "The duplicated receipt cannot prove a conflict.",
                evidence,
            )


def _search(query: str, kinds: list[str]):
    def respond(call, _provider):
        assert type(call) is BoundContextProviderCallRequest
        assert call.scope.agent_id == LEAD_ORCHESTRATOR
        source_types = {
            "w": "story_document",
            "c": "conversation_history",
            "d": "current_consensus_run",
            "o": "agent_work_history",
        }
        outer, inner = offered_fixture_bindings(
            call, action_type="search_context", response_type=None, family=0)
        assert inner is None
        return indexed_response(
            {"action_type": "search_context", "queries": [{
                "search_text": query,
                "source_types": {source_types[kind]: True for kind in kinds},
            }]},
            outer_schema_id=outer, inner_schema_id=None)

    return respond


def _missing_outcome(*, proposal: bool = False, authoring_assessment=None):
    def respond(call, _provider):
        observation = _provider.callback_payload(call)["context_observation"]
        assert observation["observation_type"] == "context_search_results" and observation["zero_hit_receipt"] is not None
        value = {
                **({"authoring_assessment": admission.assessment_to_dict(authoring_assessment)}
                if authoring_assessment is not None else {}),
            "recommended_action": None if proposal else {"kind": "prepare_consultation",
                "purpose": "Check the missing evidence.",
                "instruction": "Prepare an exact consultation for the disclosed user input.",
                "expected_result": "A separate consultation confirmation request.",
                "source_ids": ["user_input"], "target_paths": [],
                "specialist_ids": [PLOT_ARCHITECT], "objective_id": None},
            "kind": "proposal" if proposal else "recommendation",
            "message": "No matching canonical writing artifact was found.",
            "scope": "story_specific",
            "evidence_status": "missing",
            "evidence_ids": [observation["zero_hit_receipt"]["evidence_id"]],
            "artifact_intent": None,
            "objective_intent": None,
            "authoring_scope": _live_scope(call, activity="review", domains=("plot_structure",)),
            "proposal": (
                {
                    "relation": "new_step",
                    "purpose": "Confirm the missing artifact before proceeding.",
                    "shared_premises": [],
                    "assignments": [
                        {
                            "agent_id": PLOT_ARCHITECT,
                            "instruction": "Assess the request without inventing facts.",
                            "input_ids": ["user_input"],
                        }
                    ],
                }
                if proposal
                else None
            ),
        }
        if not proposal:
            from tests.provider_wire_fixture import (
                result, finite_fixture_selection, lead_fixture_scope_record,
                lead_fixture_assessment, lead_fixture_result, lead_fixture_response)
            from tests.test_lead_orchestration import _compact_recommended
            compact = {'kind': value['kind'], 'message': value['message'],
                       'authoring_scope': value['authoring_scope'],
                       'recommended_action': _compact_recommended(value['recommended_action'])}
            if 'authoring_assessment' in value:
                compact['authoring_assessment'] = value['authoring_assessment']
            native = result(compact, 'lead-response-v9')
            native.pop('writing_task_scope')
            native['task_scope'] = lead_fixture_scope_record(value['authoring_scope'])
            choices = dict(call.contract.semantic_index.policy.choices)
            sources = (choices['input_ids'] if choices['source_binding'] == ('unbound',)
                       else choices['source_ids'])
            drafting = native['proposal_drafting_request']
            drafting['source_ids'] = finite_fixture_selection(
                sources, tuple(drafting['source_ids']))
            drafting['specialist_ids'] = finite_fixture_selection(
                (PLOT_ARCHITECT, 'character_custodian', EXECUTION_SPECIALIST),
                tuple(drafting['specialist_ids']))
            if 'authoring_assessment' in native:
                native['authoring_assessment'] = lead_fixture_assessment(
                    native['authoring_assessment'], source_choices=choices['input_ids'])
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        from tests.provider_wire_fixture import (
            lead_fixture_assignments, lead_fixture_bound_scope,
            lead_fixture_assessment, lead_fixture_response,
            author_positive_fixture_review_bases)
        choices = dict(call.contract.semantic_index.policy.choices)
        row = value["proposal"]["assignments"][0]
        allocated = lead_fixture_assignments(author_positive_fixture_review_bases([{
            "agent_id": row["agent_id"], "task_instructions": row["instruction"],
            "assignment_type": "review_content", "input_source_ids": row["input_ids"]}],
                candidate_choices=choices["review_candidate_ids"]),
            input_choices=choices["allocation_input_ids"],
            review_choices=choices["review_candidate_ids"],
            anchors={row["agent_id"]: row["input_ids"][0]},
            recipients={source: row["agent_id"] for source in row["input_ids"]})
        return lead_fixture_response(call, {
            "response_type": "specialist_consultation_proposal",
            "response_text": value["message"], "response_details": {
                "task_scope": lead_fixture_bound_scope(
                    call, value["authoring_scope"], record=True),
                "authoring_assessment": lead_fixture_assessment(
                    value.get("authoring_assessment"), source_choices=choices["input_ids"]),
                "specialist_consultation_proposal": {
                    "shared_premises": value["proposal"]["shared_premises"], **allocated}}},
            family=0)

    return respond


def _referenced_outcome(*, status: str, evidence_ids: list[str], factual=False,
                        authoring_assessment=None, native_proposal=False, selection_group=None):
    def respond(call, _provider):
        aliases = _provider.callback_payload(call)["task_input"]["available_input_sources"]
        target_paths = tuple(
            item["source_locator"].removeprefix("${").removesuffix("}").split("}#", 1)[0]
            for item in aliases
            if item["input_source_id"] != "user_input"
        )
        value = {
            **({"authoring_assessment": admission.assessment_to_dict(authoring_assessment)}
               if authoring_assessment is not None else {}),
            "kind": "question" if factual else "proposal",
            "message": "The reference is empty. What candidate material should be reviewed?" if factual else "The supplied outline and manuscript candidates require review.",
            "scope": "story_specific", "evidence_status": status, "evidence_ids": evidence_ids,
            "artifact_intent": None, "objective_intent": None,
            "authoring_scope": _live_scope(
                call,
                activity="review",
                paths=target_paths,
                domains=("plot_structure", "tone_execution"),
            ),
            "proposal": None if factual else {
                "relation": "new_step", "purpose": "Review the exact attached candidates.",
                "shared_premises": [],
                "assignments": [{"agent_id": agent, "instruction": "Review the supplied candidates for the assigned domain.",
                                 "input_ids": [item["input_source_id"] for item in aliases], "mode":"review",
                                 "review_basis":{"schema_version":1,"candidate_input_ids":evidence_ids,"criteria":"Assess the exact candidate references."}}
                                for agent in (PLOT_ARCHITECT, "execution_specialist")],
            },
        }
        if factual:
            from tests.provider_wire_fixture import (
                lead_fixture_bound_scope, lead_fixture_response, lead_fixture_assessment)
            details = {"task_scope": lead_fixture_bound_scope(
                call, value["authoring_scope"], record=True)}
            if "authoring_assessment" in value:
                choices = dict(call.contract.semantic_index.policy.choices)
                details["authoring_assessment"] = lead_fixture_assessment(
                    value["authoring_assessment"], source_choices=choices["input_ids"])
            return lead_fixture_response(call, {
                "response_type": "clarification_question", "response_text": value["message"],
                "response_details": details}, family=0,
                branch_key=call.contract.factory.rule_seed.contract_id + ":family:1" + (":group:" + selection_group if selection_group is not None else ""))
        if not native_proposal:
            return _provider_envelope(call, value)
        from tests.provider_wire_fixture import (
            lead_fixture_assignments, lead_fixture_assessment, lead_fixture_response,
            lead_fixture_bound_scope)
        choices = dict(call.contract.semantic_index.policy.choices)
        rows = []
        for row in value['proposal']['assignments']:
            basis = row['review_basis']
            if basis['schema_version'] != 1:
                raise ValueError('this positive fixture authors exactly domain basis version1')
            rows.append({'agent_id': row['agent_id'], 'task_instructions': row['instruction'],
                         'assignment_type': 'review_content', 'input_source_ids': row['input_ids'],
                         'review_basis': {key: basis[key]
                                          for key in ('candidate_input_ids', 'criteria')}})
        allocated = lead_fixture_assignments(
            rows, input_choices=choices['allocation_input_ids'],
            review_choices=choices['review_candidate_ids'],
            anchors={row['agent_id']: row['input_source_ids'][0] for row in rows},
            recipients={source: PLOT_ARCHITECT for source in choices['required_input_ids']})
        return lead_fixture_response(call, {
            'response_type': 'specialist_consultation_proposal',
            'response_text': value['message'], 'response_details': {
                'task_scope': lead_fixture_bound_scope(call, value['authoring_scope'], record=True),
                'authoring_assessment': lead_fixture_assessment(
                    value.get('authoring_assessment'), source_choices=choices['input_ids']),
                'specialist_consultation_proposal': {
                    'shared_premises': value['proposal']['shared_premises'], **allocated}}},
            family=0)
    return respond


class RuntimeLeadEvidenceGroundingTests(LeadOrchestrationTestCase):
    async def test_zero_hits_and_scoped_status_evidence_are_derived_locally(
        self,
    ) -> None:
        for index, kinds in enumerate((["w"], ["w", "c"], ["c"])):
            with self.subTest(kinds=kinds):
                conversation_id = self.conversation(f"Zero hit {index}")
                self.backend.enqueue(_search("phrase absent from every source", kinds),
                    _missing_outcome(authoring_assessment=_fixture_assessment()), control_plan=selection_plan('prepare.consultation.authoring', before_script_response=1))
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    _ADMISSION_BRIEF + "\n" + "Check for absent evidence.",
                )
                self.assertIs(result.kind, LeadOutcomeKind.RECOMMENDATION)
                self.assertIsNotNone(result.evidence)
                assert result.evidence is not None
                zero_hits = tuple(
                    receipt
                    for receipt in result.evidence.receipts
                    if receipt.acquisition
                    is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                )
                self.assertEqual(len(zero_hits), 1)

        conversation = self.conversation("Premise with exact range evidence")
        proposed, reported = await _confirmed_premise(self, conversation, reference=True)
        receipt, = proposed.evidence.receipts
        self.assertEqual((proposed.evidence.scope, proposed.evidence.status, receipt.acquisition),
                         (LeadOutcomeScope.PREMISE_ANALYSIS, LeadEvidenceStatus.VERIFIED, CollaborationEvidenceAcquisition.ATTACHED_REFERENCE))
        self.assertEqual((receipt.start_line, receipt.end_line, receipt.boundary_start, receipt.boundary_end), (1, 2, 1, 2))
        self.assertEqual(receipt.content_digest, "sha256:" + hashlib.sha256(b"Outline first line.\nOutline second line.\n").hexdigest())
        self.assertEqual(receipt.locator, "${core_documents/global_outline.md}#L1-L2")
        with self.assertRaises(ValueError):
            CollaborationLeadJudgmentPayload(LeadOutcomeKind.PROPOSAL, "Forged USER-only evidence.", replace(proposed.evidence, status=LeadEvidenceStatus.NOT_APPLICABLE))
        history = self.history(conversation)
        disclosure = next(event for event in history if event.payload == proposed.proposal)
        batch_events = tuple(event for event in history if event.batch_id == disclosure.batch_id)
        prefix = history[:batch_events[0].sequence - 1]
        drafts = tuple(
            replace(make_draft(event.event_id, event.kind, event.sender, event.recipient, event.payload,
                               step_id=event.step_id, turn_id=event.turn_id),
                    session_id=event.session_id, conversation_id=event.conversation_id)
            for event in batch_events)
        self.assertEqual(validate_collaboration_candidate_batch(prefix, make_batch(prefix, "valid_analysis", *drafts)).current_proposal, proposed.proposal)
        forged = tuple(replace(event, payload=replace(event.payload, evidence=replace(event.payload.evidence, scope=LeadOutcomeScope.STORY_SPECIFIC)))
                       if type(event.payload) is CollaborationLeadJudgmentPayload else event for event in drafts)
        with self.assertRaises(CollaborationAppendError):
            validate_collaboration_candidate_batch(prefix, make_batch(prefix, "forged_analysis_scope", *forged))

    async def test_durable_zero_hit_history_contains_no_search_or_source_content(
        self,
    ) -> None:
        conversation_id = self.conversation("Content-free zero hit")
        self.backend.enqueue(_search("private internal search phrase", ["w"]),
            _missing_outcome(authoring_assessment=_fixture_assessment()), control_plan=selection_plan('prepare.consultation.authoring', before_script_response=1))
        result = await self.runtime.lead_turn(self.session_id, conversation_id,
            _ADMISSION_BRIEF + "\n" + "Check canonical artifacts.",)
        self.assertIs(result.kind, LeadOutcomeKind.RECOMMENDATION)
        self.assertIsNotNone(result.recommendation_request)
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        zero_hits = tuple(
            receipt
            for receipt in result.evidence.receipts
            if receipt.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
        )
        self.assertEqual(len(zero_hits), 1)
        wire = b"\n".join(encode_collaboration_event(event) for event in self.history(conversation_id))
        for forbidden in (
            b"private internal search phrase",
            b"Outline first line",
            b"Mara wants safety",
            str(self.workspace.root).encode("utf-8"),
            b"snippet",
            b"capability",
        ):
            self.assertNotIn(forbidden, wire)
        self.assertIn(b"query_digest", wire)

    async def test_mixed_zero_hit_survives_self_appended_history_without_mutation(
        self,
    ) -> None:
        from tests.recommendation_fixture import task_assessment
        conversation_id = self.conversation("Stable mixed zero hit")
        before = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*.md")
        }
        self.backend.enqueue(
            _search("phrase absent from every source", ["w", "c"]),
            _missing_outcome(proposal=True, authoring_assessment=task_assessment(foundation_ids=('user_input',), actions=('bounded missing-evidence review judgments',))),
         control_plan=selection_plan('consultation.authoring.review', before_script_response=1))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            ("Confirm an absent artifact fact." + (
                '\nSupplied facts: Mara wants safety and fears exposure. Assess only the missing evidence claim '
                'against these facts. You may give bounded missing-evidence review judgments; do not invent an '
                'absent artifact. Execution requires separate exact confirmation.'
            )),
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        completed = await self.runtime.decide_lead_step(_decision(proposed, self._confirm_decision()))
        self.assertIs(completed.turn.state, CollaborationStepState.COMPLETED)
        after = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*.md")
        }
        self.assertEqual(after, before)
        self.assertTrue((self.workspace.root / ".git").is_file())

    @staticmethod
    def _confirm_decision():
        from story_writing_agents.conversations import (
            CollaborationConfirmationDecision,
        )

        return CollaborationConfirmationDecision.CONFIRM

    async def test_zero_hit_source_drift_blocks_confirmation_before_specialists(
        self,
    ) -> None:
        conversation_id = self.conversation("Zero hit drift")
        self.backend.enqueue(_search("phrase absent from every source", ["w"]), _missing_outcome(proposal=True,
            authoring_assessment=_fixture_assessment()), control_plan=selection_plan('consultation.authoring.review', before_script_response=1))
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            _ADMISSION_BRIEF + "\n" + "Confirm an absent writing artifact fact.",
        )
        new_artifact = self.workspace.path("manuscript/part-01/new-evidence.md")
        new_artifact.write_text("New evidence.\n", encoding="utf-8")
        calls_before = len(self.backend.calls)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(_decision(proposed, self._confirm_decision()))
        self.assertEqual(len(self.backend.calls), calls_before)

    async def test_whole_and_range_conflict_bind_proposal_and_detect_drift(
        self,
    ) -> None:
        conversation_id = self.conversation("Attached conflict")
        self.backend.enqueue(
            _referenced_outcome(
                status="conflicting",
                evidence_ids=["reference_1", "reference_2"],
                authoring_assessment=_fixture_assessment(foundation_ids=("reference_1", "reference_2")),
            native_proposal=True)
        , control_plan=selection_plan('consultation.authoring.review'))
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            (
                "Compare ${core_documents/global_outline.md} with "
                "${manuscript/part-01/opening.md}#L2-L3." + "\n" + _ADMISSION_BRIEF
            ),
        )
        assert result.evidence is not None and result.proposal is not None
        attached = tuple(
            item
            for item in result.evidence.receipts
            if item.acquisition
            is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE
        )
        self.assertEqual(tuple(item.boundary_kind for item in attached), ("whole", "artifact_lines"),)
        self.assertEqual(tuple((item.start_line, item.end_line) for item in attached), ((None, None), (2, 3)),)
        self.assertEqual(result.proposal.basis.evidence, result.evidence)
        basis = result.proposal.basis.authoring_admission
        expected_basis = (('reference_1', 'reference_2'), 'Assess the exact candidate references.')
        self.assertEqual({row.specialist_id: (row.review_basis.candidate_input_ids,
                                       row.review_basis.criteria)
                          for row in result.proposal.assignments},
                         {PLOT_ARCHITECT: expected_basis, 'execution_specialist': expected_basis})

        read_facts = tuple(v for v in basis.sources if v.kind is admission.AdmissionSourceKind.READ)
        self.assertEqual(tuple(v.source_id for v in read_facts), ("reference_1", "reference_2"))
        self.assertEqual(tuple(v.utf8_bytes for v in read_facts), tuple(v.utf8_bytes for v in attached))
        self.assertTrue(all(v.original_authority == "actual_read" for v in read_facts))
        self.assertEqual(tuple(v.source_identity for v in read_facts), tuple(
            v.operation_id + "/" + v.result_correlation_id + "/" + v.evidence_id for v in attached))

        self.assertEqual(collaboration_proposal_digest(result.proposal), result.proposal.proposal_digest,)
        self.workspace.path("manuscript/part-01/opening.md").write_text("Opening changed.\n", encoding="utf-8",)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(_decision(result, self._confirm_decision()))
        path = self.workspace.path("manuscript/part-01/exploration.md")
        path.write_text("current-exploration-evidence\nSecond line.\n", encoding="utf-8")
        observed = []
        def grounded(call, _provider):
            observed.append(_provider.callback_payload(call)["context_observation"]["read_results"][0])
            from story_writing_agents.agent_context.manifest import ContextSourceKind
            from tests.provider_wire_fixture import finite_fixture_selection
            facts = call.contract.eligibility_offers
            read_ids = tuple(dict.fromkeys(
                item.evidence_id for item in facts.completed_read_facts + facts.delivery_read_facts
                if item.source_kind is ContextSourceKind.WRITING_ARTIFACT))
            return _current_exploration_outcome(observations=({
                "text": "The current artifact READ supports this observation.",
                "evidence_input_ids": finite_fixture_selection(
                    read_ids, (observed[-1]["evidence_id"],))},))(call, _provider)
        self.backend.enqueue(_search("current-exploration-evidence", ["w"]), _read_history_hit, grounded)
        explored = await self.runtime.lead_turn(self.session_id, self.conversation("Grounded exploration"), "Explore using the current artifact.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        receipt = explored.exploration.grounded_observations[0].receipts[0]
        self.assertEqual((receipt.evidence_id, receipt.source_id), (observed[0]["evidence_id"], observed[0]["source"]["source_id"]))
        self.assertEqual((receipt.source_digest, receipt.boundary_digest, receipt.start_line, receipt.end_line), ("sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), 1, 2))
        self.assertIn("manuscript/part-01/exploration.md", receipt.locator)
        def unsupported(call, provider):
            packet = json.loads(_current_exploration_outcome()(call, provider))
            packet['agent_action']['task_result']['response_details']['grounded_observations'] = [{
                "text": "An input alias is not artifact evidence.",
                "evidence_input_ids": {"user_input": True}}]
            return json.dumps(packet, ensure_ascii=False)
        self.backend.enqueue(unsupported, unsupported)
        refused = await self.runtime.lead_turn(self.session_id, self.conversation("False evidence claim"), "Explore alternatives.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIsNone(refused.exploration)

    async def test_empty_whole_is_evidence_but_missing_and_invalid_range_fail(
        self,
    ) -> None:
        empty = self.workspace.path("manuscript/part-01/empty.md")
        empty.write_bytes(b"")
        conversation_id = self.conversation("Empty whole")
        self.backend.enqueue(_referenced_outcome(status="empty", evidence_ids=["reference_1"], factual=True, selection_group='question.authoring'), control_plan=selection_plan('question.authoring'))
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Report whether ${manuscript/part-01/empty.md} is empty.",
        )
        self.assertIs(result.kind, LeadOutcomeKind.QUESTION)
        self.assertIs(result.next_action, CollaborationNextAction.REPLY_TO_LEAD)
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        self.assertEqual(result.evidence.receipts[0].utf8_bytes, 0)
        self.assertEqual(result.evidence.receipts[0].boundary_kind, "whole")

        self.backend.enqueue(_referenced_outcome(
            status="empty", evidence_ids=["reference_1"],
            authoring_assessment=_fixture_assessment(foundation_ids=("reference_1",)),
            native_proposal=True), control_plan=selection_plan('consultation.authoring.review'))
        unqualified = await self.runtime.lead_turn(
            self.session_id, self.conversation("Empty READ foundation"),
            "Use ${manuscript/part-01/empty.md} as the sole foundation.\n" + _ADMISSION_BRIEF,
        )
        self.assertIsNone(unqualified.proposal)
        self.assertIs(unqualified.next_action, CollaborationNextAction.REPLY_TO_LEAD)
        self.assertEqual(unqualified.evidence.receipts[0].utf8_bytes, 0)
        failures = (
            (
                "Review ${manuscript/part-01/missing.md}.",
                ReferenceAuthorizationErrorCode.NOT_FOUND,
            ),
            (
                "Review ${manuscript/part-01/empty.md}#L1-L1.",
                ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS,
            ),
        )
        for index, (text, code) in enumerate(failures):
            with self.subTest(code=code.value):
                failed_id = self.conversation(f"Reference failure {index}")
                with self.assertRaises(ReferenceOperationFailure) as raised:
                    await self.runtime.lead_turn(self.session_id, failed_id, text,)
                self.assertIs(raised.exception.code, code)

    async def test_unknown_duplicate_reordered_and_prior_turn_ids_fail_once(self) -> None:
        def selected_sources(ids: list[str], *, references: bool = False,
                             expect_unoffered: bool = False):
            def respond(call, _provider):
                self.assertEqual(
                    [item["evidence_id"] for item in _provider.callback_payload(call)["core_document_statuses"]],
                    ["status_1", "status_2", "status_3", "status_4"],
                )
                offered = {item["input_source_id"] for item in _provider.callback_payload(call)["task_input"][
                    "available_input_sources"]}
                self.assertIn("user_input", offered)
                if references:
                    self.assertTrue({"reference_1", "reference_2"}.issubset(offered))
                else:
                    self.assertTrue(set(ids).isdisjoint(offered))
                assessment = _fixture_assessment(
                    foundation_ids=("reference_1", "reference_2") if references else ("user_input",),
                )
                authored_scope = _live_scope(
                    call, activity="review", domains=("plot_structure", "tone_execution"),
                    paths=("core_documents/global_outline.md", "manuscript/part-01/opening.md"))
                response = _provider_envelope(call, {
                    "authoring_assessment": admission.assessment_to_dict(assessment),
                    "authoring_scope": authored_scope,
                    "recommended_action": {
                        "kind": "prepare_consultation",
                        "purpose": "Check the exact current source selection.",
                        "instruction": "Prepare an exact consultation for the disclosed user input.",
                        "expected_result": "A separate consultation confirmation request.",
                        "source_ids": ids,
                        "target_paths": ["core_documents/global_outline.md"],
                        "specialist_ids": [PLOT_ARCHITECT, EXECUTION_SPECIALIST],
                        "objective_id": None,
                    },
                    "kind": "recommendation",
                    "message": "The current contract determines whether this source selection is admissible.",
                    "scope": "story_specific",
                    "evidence_status": "verified" if references else "missing",
                    "artifact_intent": None,
                    "objective_intent": None,
                    "proposal": None,
                })
                self.assertEqual(json.loads(response)["action"]["result"]["recommended_action"]["source_ids"], ids)
                value = json.loads(response)['action']['result']
                choices = dict(call.contract.semantic_index.policy.choices)
                if expect_unoffered:
                    self.assertTrue(all(item not in choices['source_ids'] for item in ids))
                foreign = any(identity not in choices['source_ids'] for identity in ids)
                duplicate = len(set(ids)) != len(ids)
                base_sources = ['user_input'] if foreign else ids[:1] if duplicate else ids
                from tests.recommendation_fixture import recommendation
                packet = json.loads(recommendation(
                    message=value['message'],
                    recommended_action={**value['recommended_action'],
                                        'source_ids': base_sources},
                    authoring_scope=authored_scope,
                    authoring_assessment=assessment)(call, _provider))
                source_path = ('agent_action', 'task_result', 'response_details',
                               'proposal_drafting_request', 'source_ids')
                selector = packet
                for key in source_path:
                    selector = selector[key]
                if foreign:
                    self.assertTrue(all(identity not in choices['source_ids'] for identity in ids))
                    pending = [selector]
                    while pending:
                        node = pending.pop()
                        for key, item in node.items():
                            if type(item) is dict:
                                pending.append(item)
                            else:
                                node[key] = False
                    selector[ids[0]] = True
                if duplicate:
                    self.assertEqual(ids, ['reference_1', 'reference_1'])
                    leaves = [(selector, source_path)]
                    while leaves:
                        node, path = leaves.pop()
                        if ids[0] in node:
                            duplicate_path = path
                            break
                        leaves.extend((item, (*path, key)) for key, item in node.items()
                                      if type(item) is dict)
                    else:
                        raise AssertionError('the offered source leaf must exist')

                    def encode_at(value, path):
                        pairs = list(value.items())
                        if not path:
                            pairs.append((ids[1], value[ids[0]]))
                        return '{' + ','.join(json.dumps(key) + ':' + (
                            encode_at(item, path[1:]) if path and key == path[0]
                            else json.dumps(item, ensure_ascii=False)) for key, item in pairs) + '}'

                    return encode_at(packet, duplicate_path)
                return json.dumps(packet, ensure_ascii=False)
            return respond

        for index, ids in enumerate((
            ["provider_fact"], ["reference_1", "reference_1"], ["reference_2", "reference_1"],
        )):
            with self.subTest(ids=ids):
                conversation_id = self.conversation(f"Current source selection {index}")
                self.backend.enqueue(selected_sources(ids, references=True,
                    expect_unoffered=index == 0), control_plan=selection_plan('prepare.consultation.authoring'))
                if index != 2:
                    self.backend.enqueue(selected_sources(ids, references=True,
                    expect_unoffered=index == 0))
                result = await self.runtime.lead_turn(
                    self.session_id, conversation_id,
                    _ADMISSION_BRIEF +
                        "\nUse ${core_documents/global_outline.md} ${manuscript/part-01/opening.md}#L2-L3.",
                )
                failure_payloads = tuple(event.payload for event in self.history(conversation_id)
                                         if type(event.payload) is CollaborationLeadOutcomeFailurePayload)
                self.assertEqual(len(failure_payloads), 0 if index == 2 else 1)
                self.assertEqual(len(_submitted_calls(self.history(conversation_id))), 2 if index == 2 else 3)
                if index == 2:
                    self.assertIs(result.kind, LeadOutcomeKind.RECOMMENDATION)
                    self.assertIsNotNone(result.recommendation_request, result)
                    attached = tuple(item for item in result.evidence.receipts
                                     if item.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE)
                    self.assertEqual(tuple(item.evidence_id for item in attached), ("reference_1", "reference_2"))
                    self.assertEqual(tuple(item.locator for item in attached), (
                        "${core_documents/global_outline.md}", "${manuscript/part-01/opening.md}#L2-L3",
                    ))
                    self.assertEqual(result.recommendation_request.basis.evidence, result.evidence)
                    basis = result.recommendation_request.basis.authoring_admission
                    self.assertIsNotNone(basis)
                    reads = tuple(item for item in basis.sources
                        if item.kind is admission.AdmissionSourceKind.READ)
                    self.assertEqual(tuple(item.source_id for item in reads), ("reference_1", "reference_2"))
                    self.assertEqual(tuple(item.source_identity for item in reads),
                                     tuple(item.operation_id + "/" + item.result_correlation_id + "/" +
                                         item.evidence_id
                                           for item in attached))
                    self.assertEqual(tuple(item.utf8_bytes for item in reads),
                        tuple(item.utf8_bytes for item in attached))
                    self.assertTrue(all(item.original_authority for item in reads))
                else:
                    self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
                    self.assertIs(failure_payloads[0].code,
                        CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED)
                    source_rule = ("$: duplicate object key" if index == 1
                        else "$.agent_action.task_result.response_details."
                             "proposal_drafting_request.source_ids: "
                             "contains 1 unexpected properties (names withheld)")
                    self.assertIn(source_rule, "\n".join(result.validation_details))


        prior_evidence_ids: list[str] = []

        def read_prior_turn(call, provider: _LeadContextProvider):
            return _read_history_hit(call, provider)

        def answer_prior_turn(call, provider: _LeadContextProvider):
            observation = provider.callback_payload(call)["context_observation"]
            evidence_id = observation["read_results"][0]["evidence_id"]
            prior_evidence_ids.append(evidence_id)
            from story_writing_agents.agent_context.manifest import ContextSourceKind
            from story_writing_agents.lead_provider_contracts import lead_outcome_scope_paths
            from story_writing_agents.specialist_routing import (
                ArtifactReviewDomain, AuthoringActivity)
            from tests.provider_wire_fixture import (
                finite_fixture_selection, lead_fixture_bound_scope, lead_fixture_response)
            offers = call.contract.offers
            writing_facts = tuple((item.evidence_id, item.artifact_locator, item.whole_source)
                for item in offers.completed_read_facts + offers.delivery_read_facts
                if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)
            self.assertTrue(any(identity == evidence_id
                and locator == "core_documents/global_outline.md"
                for identity, locator, _whole in writing_facts))
            eligible_paths = lead_outcome_scope_paths(
                LeadOutcomeKind.ANSWER, AuthoringActivity.REVIEW,
                (ArtifactReviewDomain.PLOT_STRUCTURE,), writing_facts, ordinary_answer=True)
            native_scope = lead_fixture_bound_scope(call, _live_scope(
                call, activity="review", domains=("plot_structure",)), record=True)
            constraints = call.contract.factory.constraints
            if constraints.scope is None and constraints.adoption_intent is None:
                native_scope['paths'] = finite_fixture_selection(eligible_paths, ())
            return lead_fixture_response(call, {
                'response_type': 'answer',
                'response_text': 'The current READ supports this answer.',
                'response_details': {'task_scope': native_scope}}, family=0,
                branch_key=call.contract.factory.rule_seed.contract_id + ':family:0:group:answer.observed_facts')

        conversation_id = self.conversation("Stale prior-turn evidence")
        self.backend.enqueue(_search("Outline", ["w"]), read_prior_turn,
                             answer_prior_turn, control_plan=selection_plan('answer.observed_facts', before_script_response=2))
        first = await self.runtime.lead_turn(self.session_id, conversation_id, "Read current evidence.")
        self.assertIs(first.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(first.message, "The current READ supports this answer.")
        self.assertEqual(len(prior_evidence_ids), 1)

        self.backend.enqueue(selected_sources(prior_evidence_ids,
                    expect_unoffered=True), control_plan=selection_plan('prepare.consultation.authoring'))
        self.backend.enqueue(selected_sources(prior_evidence_ids,
                    expect_unoffered=True))
        stale = await self.runtime.lead_turn(
            self.session_id, conversation_id,
            _ADMISSION_BRIEF + "\nReject evidence issued to the prior invocation.",
        )
        self.assertIs(stale.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(sum(event.turn_id == stale.turn_id
            and event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                             for event in self.history(conversation_id)), 1)
        failure = next(event.payload for event in self.history(conversation_id)
                       if event.turn_id == stale.turn_id and type(
                           event.payload) is CollaborationLeadOutcomeFailurePayload)
        self.assertIs(failure.code, CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED)
        self.assertIn("$.agent_action.task_result.response_details.proposal_drafting_request.source_ids: "
                      "contains 1 unexpected properties (names withheld)",
                      "\n".join(stale.validation_details))

    async def test_unread_and_truncated_search_fall_back_while_reads_attach_locally(
        self,
    ) -> None:
        from story_writing_agents import conversations as c
        from story_writing_agents import ReplyTraceOrigin
        from story_writing_agents.lead_task_transitions import LeadPublicResponseAcceptance
        from tests.test_lead_orchestration import _live_material
        from story_writing_agents.agent_context.manifest import (
            ContextSourceKind, ContextSubjectKind)
        from tests.test_lead_orchestration import _Finalizer
        from story_writing_agents.lead_provider_contracts import (
            LeadContractRejection, lead_outcome_scope_paths)
        from story_writing_agents.specialist_routing import ArtifactReviewDomain, AuthoringActivity
        from tests.provider_wire_fixture import (
            finite_fixture_read_selection, finite_fixture_selection,
            lead_fixture_bound_scope, lead_fixture_response)

        answer_returns = 0
        retained_tasks = ()

        def writing_bytes():
            return {path.relative_to(self.workspace.root): path.read_bytes()
                    for directory in ('core_documents', 'manuscript')
                    for path in (self.workspace.root / directory).rglob('*.md')}

        def check_native_delivery(conversation_id, call_start, finalizer_start,
                                  expected_returns, expected_states):
            calls = self.backend.calls[call_start:]
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(
                call.scope.session_id == self.session_id
                and call.scope.subject_kind is ContextSubjectKind.CONVERSATION
                and call.scope.subject_id == conversation_id
                and call.scope.agent_id == LEAD_ORCHESTRATOR for call in calls))
            self.assertEqual(answer_returns, expected_returns)
            self.assertEqual(len(_submitted_calls(self.history(conversation_id))), 4)
            finalizers = self.backend.finalizers[finalizer_start:]
            self.assertEqual(len(finalizers), 4)
            states = []
            for finalizer in finalizers:
                self.assertTrue(type(finalizer) is _Finalizer)
                self.assertEqual(len(finalizer.calls), 1)
                state, code = finalizer.calls[0]
                self.assertTrue(type(state) is str and state in ('accepted', 'rejected'))
                if state == 'accepted':
                    self.assertTrue(code is None)
                else:
                    self.assertTrue(
                        code is ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE)
                states.append(state)
            self.assertCountEqual(states, ("accepted", *expected_states))

        def require_terminal_fallback(result, conversation_id, call_start, original_message):
            self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
            self.assertIsNone(result.state)
            self.assertIsNone(result.confirmation_request_id)
            self.assertIsNotNone(result.trace)
            self.assertIs(result.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
            self.assertEqual(result.trace.action, 'none')
            self.assertIsNotNone(result.evidence)
            self.assertIs(result.evidence.scope, LeadOutcomeScope.WORKFLOW_META)
            self.assertIs(result.evidence.status, LeadEvidenceStatus.NOT_APPLICABLE)
            self.assertEqual(result.evidence.receipts, ())
            notice = json.loads(result.message)
            self.assertEqual(set(notice), {'code', 'language', 'task_revisions', 'state', 'paths'})
            self.assertEqual((notice['code'], notice['language'], notice['state']),
                             ('LEAD_CONTRACT_REJECTED', 'KO', None))
            tasks = _live_material(self.backend.calls[call_start])['authoring_tasks']
            task = tasks[-1]
            self.assertEqual(task['task_revision'], 1)
            offered_tasks = tuple((item['task_id'], item['task_revision']) for item in tasks)
            self.assertEqual(offered_tasks, retained_tasks + ((result.step_id, 1),))
            self.assertTrue(all(call.contract.factory.constraints.task_revisions == offered_tasks
                                for call in self.backend.calls[call_start:]))
            self.assertEqual(notice['task_revisions'],
                             [{'task_id': identity, 'task_revision': revision} for identity, revision in offered_tasks])
            self.assertTrue(notice['paths'])
            self.assertTrue(all(type(path) is str
                and path.startswith('$.agent_action.task_result') for path in notice['paths']))
            self.assertTrue(result.validation_details)
            for label in ('Original response', 'Repair response'):
                self.assertTrue(any(item.startswith(label) for item in result.validation_details))
            self.assertTrue(any('$.agent_action.task_result' in item
                                for item in result.validation_details))
            events = tuple(event for event in self.history(conversation_id)
                if (event.turn_id, event.step_id) == (result.turn_id, result.step_id))
            failures = tuple(event.payload for event in events
                if type(event.payload) is CollaborationLeadOutcomeFailurePayload)
            self.assertEqual(len(failures), 1)
            self.assertIs(failures[0].code,
                          CollaborationLeadOutcomeFailureCode.PROVIDER_RESULT_REJECTED)
            messages = tuple(event.payload for event in events
                if type(event.payload) is c.CollaborationMessagePayload
                and event.payload.content == result.message and event.payload.trace is not None
                and event.payload.trace.origin is ReplyTraceOrigin.RUNTIME_FALLBACK)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].trace, result.trace)
            public = tuple(event.payload.response for event in events
                if type(event.payload) is c.CollaborationLeadPublicResponsePayload)
            self.assertEqual(len(public), 2)
            self.assertEqual(tuple(response.response_text for response in public),
                             (original_message, 'Correction still claims unread evidence.'))
            self.assertTrue(all(response.acceptance is LeadPublicResponseAcceptance.REJECTED
                                for response in public))
            self.assertIsNone(public[0].repair_of_call_id)
            self.assertEqual(public[1].repair_of_call_id, public[0].call_id)
            self.assertNotEqual(public[0].call_id, public[1].call_id)
            for response, label in zip(
                    public, ('Original response', 'Repair response'), strict=True):
                self.assertTrue(response.validation_details)
                self.assertTrue(all(item.startswith(label) for item in response.validation_details))

        def unread_answer(call, message, domain):
            nonlocal answer_returns
            offers = call.contract.offers
            writing_facts = tuple(item for item in
                offers.completed_read_facts + offers.delivery_read_facts
                if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)
            self.assertEqual(writing_facts, ())
            eligibility = call.contract.eligibility_offers
            eligibility_writing_facts = tuple(item for item in
                eligibility.completed_read_facts + eligibility.delivery_read_facts
                if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)
            self.assertTrue(eligibility_writing_facts == ())
            scope = lead_fixture_bound_scope(call, _live_scope(
                call, activity='review', domains=(domain.value,)), record=True)
            workflow_scope = lead_fixture_bound_scope(
                call, _live_scope(call, activity='workflow'), record=True)
            stem = call.contract.factory.rule_seed.contract_id
            baseline = SimpleNamespace(contract=replace(
                call.contract.factory, schema_group_protocol=False).for_offer(offers))
            seed = json.loads(lead_fixture_response(baseline, {
                'response_type': 'answer', 'response_text': message,
                'response_details': {'task_scope': workflow_scope}},
                family=0, branch_key=stem + ':family:0'))
            self.assertFalse(baseline.contract.rule_graph.findings(seed))
            self.assertTrue(baseline.contract.decode_response(
                json.dumps(seed)).belongs_to(baseline.contract))
            question = json.loads(lead_fixture_response(baseline, {
                'response_type': 'clarification_question',
                'response_text': 'Clarify the concrete review source.',
                'response_details': {'task_scope': scope}},
                family=0, branch_key=stem + ':family:1'))
            self.assertFalse(baseline.contract.rule_graph.findings(question))
            self.assertTrue(baseline.contract.decode_response(
                json.dumps(question)).belongs_to(baseline.contract))
            negative = json.loads(lead_fixture_response(call, {
                'response_type': 'answer', 'response_text': message,
                'response_details': {'task_scope': scope}}, family=0,
                branch_key=stem + ':family:0:group:answer.workflow'))
            self.assertTrue(call.contract.rule_graph.findings(negative))
            frame = json.dumps(negative, ensure_ascii=False, separators=(',', ':'))
            with self.assertRaises(LeadContractRejection) as rejected:
                call.contract.decode_response(frame)
            self.assertIs(type(rejected.exception), LeadContractRejection)
            self.assertEqual(rejected.exception.code, 'LEAD_SCHEMA_RULE_REJECTED')
            answer_returns += 1
            return frame

        def invalid_correction(domain):
            def respond(call, _provider):
                return unread_answer(call, 'Correction still claims unread evidence.', domain)
            return respond

        def answer_with_unread_id(call, _provider):
            observation = _provider.callback_payload(call)['context_observation']
            self.assertEqual(observation['observation_type'], 'context_search_results')
            return unread_answer(call, 'A SEARCH hit is not READ evidence.',
                                 ArtifactReviewDomain.PLOT_STRUCTURE)

        def read_first(call, provider: _LeadContextProvider):
            observation = provider.callback_payload(call)['context_observation']
            handle = observation['search_results'][0]['read_handle']
            provider.used_handles = (handle,)
            outer, inner = offered_fixture_bindings(
                call, action_type='read_context', response_type=None, family=0)
            self.assertIsNone(inner)
            return indexed_response({'action_type': 'read_context', 'read_handles':
                finite_fixture_read_selection(
                    call.contract.offers.issued_handle_aliases, (handle,))},
                outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)

        read_evidence = []
        positive_message = 'The completed READ grounds this factual answer.'

        def answer_without_using_read(call, _provider):
            nonlocal answer_returns
            observation = _provider.callback_payload(call)['context_observation']
            self.assertEqual(observation['observation_type'], 'context_read_results')
            evidence_id = observation['read_results'][0]['evidence_id']
            read_evidence.append(evidence_id)
            offers = call.contract.offers
            writing_facts = tuple((item.evidence_id, item.artifact_locator, item.whole_source)
                for item in offers.completed_read_facts + offers.delivery_read_facts
                if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)
            self.assertTrue(any(identity == evidence_id
                and locator == 'core_documents/global_outline.md'
                for identity, locator, _whole in writing_facts))
            eligible_paths = lead_outcome_scope_paths(
                LeadOutcomeKind.ANSWER, AuthoringActivity.REVIEW,
                (ArtifactReviewDomain.PLOT_STRUCTURE,), writing_facts, ordinary_answer=True)
            self.assertIsNotNone(eligible_paths)
            scope = lead_fixture_bound_scope(call, _live_scope(
                call, activity='review', domains=('plot_structure',)), record=True)
            constraints = call.contract.factory.constraints
            self.assertIsNone(constraints.scope)
            self.assertIsNone(constraints.adoption_intent)
            scope['paths'] = finite_fixture_selection(eligible_paths, ())
            frame = lead_fixture_response(call, {
                'response_type': 'answer', 'response_text': positive_message,
                'response_details': {'task_scope': scope}}, family=0,
                branch_key=call.contract.factory.rule_seed.contract_id + ':family:0:group:answer.observed_facts')
            self.assertFalse(call.contract.rule_graph.findings(json.loads(frame)))
            self.assertTrue(call.contract.decode_response(frame).belongs_to(call.contract))
            answer_returns += 1
            return frame

        cases = (
            (_search('Outline', ['w']), answer_with_unread_id),
            (_search('Outline', ['w']), read_first, answer_without_using_read),
        )
        for index, scripts in enumerate(cases):
            conversation_id = self.conversation(f'Unread evidence {index}')
            before = writing_bytes()
            answer_returns = 0
            call_start = len(self.backend.calls)
            finalizer_start = len(self.backend.finalizers)
            self.backend.enqueue(*scripts, control_plan=selection_plan('answer.workflow' if index == 0 else 'answer.observed_facts', before_script_response=index + 1))
            if index == 0:
                self.backend.enqueue(invalid_correction(ArtifactReviewDomain.PLOT_STRUCTURE))
            result = await self.runtime.lead_turn(
                self.session_id, conversation_id, 'Do not accept unread evidence.')
            check_native_delivery(
                conversation_id, call_start, finalizer_start, 2 if index == 0 else 1,
                ('accepted', 'rejected', 'rejected') if index == 0
                else ('accepted', 'accepted', 'accepted'))
            if index == 0:
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
                require_terminal_fallback(
                    result, conversation_id, call_start, 'A SEARCH hit is not READ evidence.')
            else:
                self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(result.message, positive_message)
                self.assertEqual(len(read_evidence), 1)
                self.assertIsNotNone(result.evidence)
                receipts = tuple(item for item in result.evidence.receipts
                                 if item.evidence_id == read_evidence[0])
                self.assertEqual(len(receipts), 1)
                self.assertIs(receipts[0].acquisition, CollaborationEvidenceAcquisition.READ)
                self.assertIn('core_documents/global_outline.md', receipts[0].locator)
                (read_scope,) = tuple(event.payload.authoring_scope for event in self.history(conversation_id)
                    if type(event.payload) is c.CollaborationLeadJudgmentPayload
                    and (event.turn_id, event.step_id) == (result.turn_id, result.step_id))
                self.assertIsNotNone(read_scope)
                self.assertIs(read_scope.activity, AuthoringActivity.REVIEW)
                retained_tasks = ((read_scope.task_id, read_scope.task_revision),)
                self.assertEqual(retained_tasks, ((result.step_id, 1),))
            self.assertIsNone(result.proposal)
            self.assertIsNone(result.artifact_proposal)
            self.assertIsNone(result.artifact_application_request)
            self.assertIsNone(result.story_commit_request)
            self.assertIsNone(result.recommendation_request)
            self.assertEqual(writing_bytes(), before)
            self.assertFalse(any(type(event.payload) in (
                c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2)
                for event in self.history(conversation_id)))

        for index in range(12):
            self.workspace.path(f'manuscript/part-01/shared-{index}.md').write_text(
                'Shared grounding phrase.\n', encoding='utf-8')

        def truncated_answer(call, _provider):
            self.assertTrue(_provider.callback_payload(call)['context_observation']['truncated'])
            self.assertEqual(_provider.callback_payload(call)['context_observation']['observation_type'],
                             'context_search_results')
            return unread_answer(call, 'A SEARCH hit is not READ evidence.',
                                 ArtifactReviewDomain.TONE_EXECUTION)

        conversation_id = self.conversation('Truncated search')
        before = writing_bytes()
        answer_returns = 0
        call_start = len(self.backend.calls)
        finalizer_start = len(self.backend.finalizers)
        self.backend.enqueue(_search('Shared grounding phrase', ['w']), truncated_answer, control_plan=selection_plan('answer.workflow', before_script_response=1))
        self.backend.enqueue(invalid_correction(ArtifactReviewDomain.TONE_EXECUTION))
        result = await self.runtime.lead_turn(
            self.session_id, conversation_id, 'Reject truncated search evidence.')
        check_native_delivery(
            conversation_id, call_start, finalizer_start, 2,
            ('accepted', 'rejected', 'rejected'))
        self.assertIs(result.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        require_terminal_fallback(
            result, conversation_id, call_start, 'A SEARCH hit is not READ evidence.')
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.artifact_proposal)
        self.assertIsNone(result.artifact_application_request)
        self.assertIsNone(result.story_commit_request)
        self.assertIsNone(result.recommendation_request)
        self.assertEqual(writing_bytes(), before)
        self.assertFalse(any(type(event.payload) in (
            c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2)
            for event in self.history(conversation_id)))

    async def test_validation_failure_closes_once_and_restart_uses_fresh_turn(
        self,
    ) -> None:
        incomplete_id = self.conversation("Correct incomplete live shape")

        def incomplete(call, _provider):
            del call, _provider
            return json.dumps({"action": {"kind": "answer", "result": {}}}, separators=(",", ":"),)

        self.backend.enqueue(incomplete, control_plan=selection_plan('answer.workflow'))
        self.backend.enqueue(_visible_outcome("answer", "Corrected complete direct answer."))
        corrected = await self.runtime.lead_turn(
            self.session_id,
            incomplete_id,
            "Correct an incomplete direct answer in this turn.",
        )
        self.assertIs(corrected.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(corrected.message, "Corrected complete direct answer.")
        self.assertEqual(len(self.backend.calls), 3)
        self.assertEqual(
            tuple(finalizer.calls for finalizer in self.backend.finalizers),
            ([("accepted", None)],
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                    )
                ],
                [("accepted", None)],
            ),
        )
        self.assertFalse(
            any(event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE for event in self.history(incomplete_id))
        )

        conversation_id = self.conversation("Retry after exhausted correction")

        def invented(call, _provider):
            del call, _provider
            return json.dumps(
                {
                    "action": {
                        "kind": "answer",
                        "result": {
                            "message": "Provider-invented evidence must fail.",
                            "evidence_ids": ["invented"],
                        },
                    }
                },
                separators=(",", ":"),
            )

        self.backend.enqueue(invented, control_plan=selection_plan('answer.workflow'))
        self.backend.enqueue(invented)
        calls_before = len(self.backend.calls)
        failed = await self.runtime.lead_turn(self.session_id, conversation_id, "Reject invented evidence.",)
        self.assertIs(failed.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD,)
        self.assertEqual(len(self.backend.calls) - calls_before, 3)
        first_history = self.history(conversation_id)
        self.assertEqual(
            sum(event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE for event in first_history),
            1,
        )

        restart_backend = _LeadBackend(test_case=self)
        restart_backend.enqueue(_visible_outcome("answer", "Fresh retry answer."), control_plan=selection_plan('answer.workflow'))
        restarted = StoryRuntime(self.control_workspace, restart_backend)  # type: ignore[arg-type]
        fresh = await restarted.lead_turn(self.session_id, conversation_id, "Retry in a fresh turn.",)
        self.assertIs(fresh.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(restart_backend.calls), 2)
        final_history = restarted.conversation_storage_services.event_store.read(self.session_id, conversation_id,)
        self.assertEqual(
            sum(event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE for event in final_history),
            1,
        )

    def test_failure_correlation_and_post_closure_calls_fail_closed(self) -> None:
        calls = provider_call_drafts(
            invocation_id="invocation_failure", call_id="call_failure", role=LEAD_ORCHESTRATOR, recipient=LEAD,
            invocation_kind=CollaborationInvocationKind.LEAD,
        )
        failure = make_draft(
            "lead_failure", CollaborationEventKind.LEAD_OUTCOME_FAILURE, RUNTIME, USER,
            CollaborationLeadOutcomeFailurePayload(
                CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED, "invocation_failure",
                "call_failure",
            ),
        )
        history = store_batch((), make_batch((), "batch_failure", *calls, failure),)
        post_closure = provider_call_drafts(
            invocation_id="invocation_failure", call_id="call_after_failure", role=LEAD_ORCHESTRATOR,
            recipient=LEAD, invocation_kind=CollaborationInvocationKind.LEAD, ordinal=2,
            call_kind=CollaborationProviderCallKind.CONTINUATION,
        )[0]
        with self.assertRaises(CollaborationAppendError):
            validate_collaboration_candidate_batch(
                history,
                make_batch(history, "batch_post_closure", post_closure),
            )

        sibling_failure = replace(
            failure,
            event_id="sibling_failure",
            payload=replace(failure.payload, invocation_id="sibling_invocation",),
        )
        with self.assertRaises(CollaborationAppendError):
            validate_collaboration_candidate_batch(
                (),
                make_batch((), "batch_sibling_failure", *calls, sibling_failure),
            )


if __name__ == "__main__":
    unittest.main()
