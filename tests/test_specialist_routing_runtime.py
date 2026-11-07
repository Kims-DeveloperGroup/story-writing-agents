"""Runtime routing accepts typed task facts and keeps draft/review authority separate."""
from tests.test_lead_orchestration import _live_material, _live_task
from tests.provider_wire_fixture import selection_plan
from story_writing_agents.lead_schema_groups import LeadGroupPhase
from tests.provider_wire_fixture import ( result as wire_result, lead_fixture_bound_scope, lead_fixture_assignments,
    finite_fixture_selection, lead_fixture_result, fixture_schema_node, indexed_response, lead_fixture_response,)
from tests.provider_wire_fixture import author_positive_fixture_review_bases
from story_writing_agents.agent_context.accounting import ProviderCallKind
from story_writing_agents.lead_provider_contracts import LeadResultContractOwner
from tests.provider_wire_fixture import answer as wire_answer, contract_id as wire_contract_id

from functools import partial
from tests.test_collaboration_events import editorial, editorial_request
import asyncio
import hashlib
import json
import story_writing_agents.lead_task_transitions as task
from tests.test_lead_orchestration import ( _current_exploration_outcome,
    _discussion_reply,)
import subprocess
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import authoring_admission as admission
from story_writing_agents import AgentBackend, StoryRuntime, lead_exploration as exploration, lead_premise_decisions as premise
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from story_writing_agents.specialist_routing import AuthoringActivity, AuthoringIntent, AuthoringScope, ArtifactReviewDomain, SpecialistAssignmentMode, ReviewBasis
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.interactive_terminal import _collaboration_view as view
from tests.test_lead_orchestration import _SubmittedFailure, _confirmed_premise, _premise_choice, _selected_premise_proposal, _search_history, _read_history_hit
from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase, _LeadBackend, _submitted_calls, _compact_scope, _decision, _provider_envelope,
    _workflow_material, _live_scope, _specialist_outcome, _synthesis_outcome, _visible_outcome, _exploration_outcome, _adoption_selection,
)
from tests.provider_wire_fixture import answer as wire_answer, contract_id as wire_contract_id
from tests.recommendation_fixture import DRAFT, action, recommendation, decision as recommendation_decision, task_assessment
from story_writing_agents.conversations.events import shared_premises_to_dict
from tests.test_artifact_proposal_orchestration import _artifact_outcome, _artifact_synthesis_outcome, _status_id

_ADMISSION_BRIEF = ( "Nora must recover her brother's letter before a public oath. "
    "His disappearance motivates her search; this city has no magic. "
    "Draft three outline scenes preserving that motive and rule. "
    "Invent supporting dialogue and minor places; leave the ending unresolved." )
def _fixture_assessment(*, foundation_ids=("user_input",), user_id="user_input"):
    foundation = admission.AdmissionEvidence("Story foundations", foundation_ids, "Literal supplied fixture facts.")
    direction = admission.AdmissionEvidence("Direction", (user_id,), "USER requests three outline scenes.")
    latitude = admission.AdmissionEvidence("Creative scope", (user_id,), "USER permits supporting details.")
    decision = admission.AdmissionDecision("Supporting details", admission.DecisionCoverage.DELEGATED, (user_id,), "USER explicitly delegates dialogue and minor places.",)
    return admission.AuthoringAssessment(admission.AdmissionStatus.SUFFICIENT, (foundation,), direction, latitude,
        ("supporting dialogue", "minor places"), ("no magic", "preserve motive", "unresolved ending"), (decision,), (), "Constructed assessment of the explicit synthetic fixture brief.",)

OUTLINE = "core_documents/global_outline.md"
CHARACTER = "core_documents/character_sheets.md"
ROSTER = (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)


_ADOPTION_DIRECTION = (
    "Draft three outline scenes from the exact supplied material. "
    "Preserve its stated motive and limits, without magic. "
    "You may invent supporting dialogue and minor places; leave the ending unresolved." )

_PLOT_NATIVE_INPUTS = { 'source_choices': ('user_input',), 'input_choices': ('user_input',),
    'anchors': {PLOT_ARCHITECT: 'user_input'},
    'recipients': {'user_input': PLOT_ARCHITECT}, 'family': 0,}


def scoped_proposal(*agents, authoring_assessment=None, activity='draft', paths=(OUTLINE,),
        domains=(), selected=None, revision=None, candidate_ids=None, kind='proposal',
        intent=None, mutate=None, native_inputs=None):
    def respond(call, provider):
        material = _workflow_material(provider.callback_payload(call))
        tasks = [_live_task(call, i) for i, _ in enumerate(call.contract.semantic_index.policy.task_revisions)]
        index = next((i for i, t in enumerate(tasks) if t["task_id"] == selected and (revision is None or t["task_revision"] == revision)), len(tasks)-1)
        assert revision is None or (tasks[index]["task_id"], tasks[index]["task_revision"]) == (selected, revision)
        scope = _live_scope(call, activity=activity, paths=paths, domains=domains, task_index=index)
        scope["interpretation"] = "Draft from the current synthetic brief." if activity == "draft" else "Review the supplied concrete synthetic candidate."
        scope["intent"] = "authoring" if intent is None else intent
        mode = "review" if activity == "review" else "draft"
        ids = ["user_input"] if candidate_ids is None else list(candidate_ids)
        value = { 'authoring_scope': scope, 'kind': kind,
            'message': 'The scoped work requires its disclosed specialists.',
            **({'scope': 'story_specific', 'evidence_status': 'incomplete',
                'evidence_ids': [], 'artifact_intent': None} if native_inputs is None else {}), 'proposal': {
                **({'relation': 'new_step'} if native_inputs is None else {}), 'shared_premises': [],
                **({'purpose': 'Produce the scoped synthetic material.', 'authoring_scope': scope}
                   if native_inputs is None else {}), 'assignments': [
                    {'agent_id': agent, 'instruction': 'Produce the bounded material.' if mode == 'draft'
                     else 'Assess the supplied candidate.',
                     'input_ids': list(dict.fromkeys(['user_input', *ids])), 'mode': mode}
                    for agent in agents]} if kind == 'proposal' else None}
        if kind == "question":
            value["planning_target"] = None
        if authoring_assessment is not None:
            value["authoring_assessment"] = admission.assessment_to_dict(authoring_assessment)
        statuses = provider.callback_payload(call).get("core_document_statuses", [])
        status = next((s for s in statuses if s["artifact_path"] in paths), statuses[0] if statuses else None)
        if status is not None and native_inputs is None:
            value["evidence_ids"] = [status["evidence_id"]]
        if native_inputs is not None and value['proposal'] is not None:
            value['proposal']['required_input_recipients'] = dict(native_inputs['recipients'])
        if mutate: mutate(value, material)
        encoded = _provider_envelope(call, value)
        if native_inputs is None:
            return encoded
        native_types = {'proposal': 'specialist_consultation_proposal', 'answer': 'answer',
                        'critique': 'critique', 'question': 'clarification_question',
                        'recommendation': 'proposal_drafting_recommendation'}
        body = wire_result(value, None, native_types=native_types)
        if 'schema_id' in body:
            return encoded
        if type(body.get('writing_task_scope')) is dict and 'task_scope' not in body:
            body['task_scope'] = lead_fixture_bound_scope( call, body.pop('writing_task_scope'), record=True)
        assessment = body.get('authoring_assessment')
        if type(assessment) is dict:
            for key in ('creative_scope', 'direction', 'foundations', 'decisions'):
                value = assessment.get(key)
                records = value if type(value) in (list, tuple) else (value,)
                for record in records:
                    if type(record) is dict and 'source_ids' in record:
                        cells = record['source_ids']
                        choices = native_inputs['source_choices']
                        if (type(cells) in (list, tuple) and all(type(item) is str for item in cells)
                                and len(set(cells)) == len(cells) and set(cells) <= set(choices)):
                            record['source_ids'] = finite_fixture_selection(choices, tuple(cells))
        proposal = body.get('specialist_consultation_proposal')
        if type(proposal) is dict and 'assignments' in proposal:
            choices = dict(call.contract.semantic_index.policy.choices)
            proposal['assignments'] = author_positive_fixture_review_bases(
                proposal['assignments'],
                candidate_choices=choices['review_candidate_ids'])
            allocation = lead_fixture_assignments(
                proposal['assignments'], input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'], anchors=native_inputs['anchors'],
                recipients=proposal.get('required_input_recipients'))
            proposal['assignments'] = allocation['assignments']
        response_type = native_inputs.get('lookup_response_type', native_types.get(kind, kind))
        branch_key = None
        if call.contract.schema_group_phase is not None:
            group_id = call.contract.group_workflow.active_selection.group_id
            shape = ("preliminary_review" in body, type(proposal) is dict and "stage_plan" in proposal)
            def offered_shape(member):
                schema = member.rule_graph.schema_value()
                def object_rule(node):
                    node = fixture_schema_node(schema, node)
                    return fixture_schema_node(schema, node["anyOf"][0]) if "anyOf" in node else node
                root = object_rule(schema)
                details = object_rule(root["properties"]["response_details"])
                allocation = details.get("properties", {}).get("specialist_consultation_proposal")
                return ("preliminary_review" in details.get("required", ()), allocation is not None
                    and "stage_plan" in object_rule(allocation).get("required", ()))
            branch_key, = {member.owner.variant.branch_key.partition(':partition:')[0]
                for member in call.contract.semantic_index.contracts
                if type(member.owner) is LeadResultContractOwner and member.owner.variant.group_id == group_id
                and member.rule_graph.literal_at(('response_type',)) == response_type
                and offered_shape(member) == shape}
        return lead_fixture_response(call, lead_fixture_result(body),
            family=0 if branch_key is not None else native_inputs['family'], branch_key=branch_key,
            response_type=response_type)
    return respond


class SpecialistRoutingRuntimeTests(LeadOrchestrationTestCase):
    def _observe_failure_producers(self):
        import sys
        notes = []
        original = StoryRuntime._lead_outcome_failure_result
        def observed(runtime, *args, **kwargs):
            if len(notes) < 8:
                caller = trace = None
                try:
                    caller = sys._getframe(1)
                    failure = kwargs.get("failure")
                    trace = None if failure is None else failure.__traceback__
                    while trace is not None and trace.tb_next is not None:
                        trace = trace.tb_next
                    notes.append((caller.f_code.co_name, caller.f_lineno, kwargs["failure_code"].value,
                        None if trace is None else (trace.tb_frame.f_code.co_name, trace.tb_lineno)))
                except Exception:
                    pass
                finally:
                    del caller, trace
            return original(runtime, *args, **kwargs)
        patcher = mock.patch.object(StoryRuntime, "_lead_outcome_failure_result", new=observed)
        patcher.start()
        self.addCleanup(patcher.stop)
        return notes

    def _assert_continuation(self, result, field):
        self.assertIsNone(getattr(result, field))
        self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
    def _native_scoped(self, *agents, **values):
        mutate = values.pop('mutate', None)
        def respond(call, provider):
            choices = dict(call.contract.semantic_index.policy.choices)
            constraints = call.contract.factory.constraints
            closed_review = constraints.adoption_intent is AuthoringIntent.REVIEW_ONLY or (
                constraints.scope is not None
                and constraints.scope.intent is AuthoringIntent.REVIEW_ONLY)
            native = {'family': int(values.get('intent') == 'review_only' and not closed_review),
                      'input_choices': choices['input_ids'],
                      'source_choices': choices['input_ids'], 'anchors': {}, 'recipients': {}}
            def prepare(value, material):
                if mutate is not None:
                    mutate(value, material)
                proposal = value['proposal']
                rows = proposal['assignments']
                native['anchors'].update((row['agent_id'], row['input_ids'][0]) for row in rows)
                required = tuple(source for source in choices['required_input_ids']
                                 if value['authoring_scope']['intent'] != 'review_only'
                                 or source != 'user_input')
                recipients = {source: next(row['agent_id'] for row in rows
                                           if source in row['input_ids']) for source in required}
                proposal['required_input_recipients'] = recipients
            writer = scoped_proposal(*agents, **values, mutate=prepare, native_inputs=native)
            frame = json.loads(writer(call, provider))
            details = frame['agent_action']['task_result']['response_details']
            for premise in details['specialist_consultation_proposal']['shared_premises']:
                premise['basis_input_ids'] = finite_fixture_selection(
                    choices['input_ids'], premise['basis_input_ids'])
            return json.dumps(frame, ensure_ascii=False)
        return respond

    async def proposal_for(self, conversation, response=None, text=_ADMISSION_BRIEF, adoption=None, *, selection_group="consultation.authoring.content"):
        writer = self._native_scoped if text == _ADMISSION_BRIEF else scoped_proposal
        self.backend.enqueue(response or writer(
            PLOT_ARCHITECT, authoring_assessment=_fixture_assessment() if text == _ADMISSION_BRIEF else None,
            domains=("plot_structure",),), control_plan=selection_plan(selection_group))
        return await self.runtime.lead_turn(self.session_id, conversation, text, adoption=adoption)

    async def finish_draft(self, conversation, *, synthesis=None, **kwargs):
        proposed = await self.proposal_for(conversation, **kwargs)
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, proposed)
        for assignment in proposed.proposal.assignments:
            status = {SpecialistAssignmentMode.DRAFT: "drafted", SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT: "constraints_assessed",
                      SpecialistAssignmentMode.REVIEW: "supports"}[assignment.mode]
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome(status, "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis or _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        result = await self.runtime.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        if result.turn.kind is c.LeadOutcomeKind.RECOMMENDATION:
            history = self.history(conversation)
            c.project_collaboration_history(history)
            source = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationProposal and event.step_id == proposed.proposal.step_id)
            terminal = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationStepStatePayload and event.step_id == proposed.proposal.step_id and event.payload.proposal_digest == proposed.proposal.proposal_digest)
            self.assertEqual(source, proposed.proposal)
            self.assertIs(terminal.state, c.CollaborationStepState.COMPLETED, (result.turn.kind, result.turn.message, result.turn.validation_details, [(e.payload.assessment, e.payload.message[:500]) for e in history if type(e.payload) is c.CollaborationSpecialistReplyPayload], [(e.payload.synthesis_outcome, e.payload.synthesis_submitted_physical_calls, e.payload.overall_outcome) for e in history if type(e.payload) is c.CollaborationExecutionResult], [(e.payload.scope, e.payload.outcome, e.payload.submitted_physical_calls) for e in history if type(e.payload) is c.CollaborationTerminalOutcomePayload], getattr(self, "_synthesis_checks", None)))
        else:
            self.assertIs(result.turn.state, c.CollaborationStepState.COMPLETED)
        return proposed, result

    async def test_empty_target_adequate_brief_produces_draft_without_writes(self):
        before_invalid = self.artifact_bytes()
        rejected = await self._rejected_pair(self.conversation('Missing executable authoring target'), _ADMISSION_BRIEF,
            self._native_scoped(PLOT_ARCHITECT, paths=(), authoring_assessment=_fixture_assessment()),
            selection_group='consultation.authoring.content')
        self.assertIn('task_scope.paths', '\n'.join(rejected.validation_details)); self.assertEqual(self.artifact_bytes(), before_invalid)
        self.assertTrue(any(item.response.acceptance.value == 'rejected' for item in rejected.public_responses))
        self.assertEqual((rejected.proposal, rejected.recommendation_request, rejected.artifact_application_request,
            rejected.story_commit_request, rejected.confirmation_request_id), (None,) * 5)
        self.workspace.path(OUTLINE).write_bytes(b"")
        conversation = self.conversation()
        assessment = admission.assessment_to_dict(_fixture_assessment())
        before = self.artifact_bytes(); self._synthesis_checks = []
        def recommend(call, provider):
            material = _workflow_material(provider.callback_payload(call))
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            raw = _synthesis_outcome(
                recommendation_family=1, message=material["authoring_scope"]["interpretation"],
                recommended_action=action(source_ids=(draft_reply,), target_paths=(OUTLINE,)))(call, provider); self._synthesis_checks.append(call.contract.structural_findings(raw)); return raw
        proposed, result = await self.finish_draft(conversation, synthesis=recommend)
        self.assertEqual(proposed.proposal.participants, (PLOT_ARCHITECT,))
        self.assertIs(proposed.proposal.authoring_scope.activity, AuthoringActivity.DRAFT)
        self.assertEqual(proposed.proposal.authoring_scope.target_paths, (OUTLINE,))
        replies = [e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationSpecialistReplyPayload]
        self.assertEqual(len(replies), 1)
        self.assertIs(replies[0].assessment, c.CollaborationSpecialistAssessment.DRAFTED)
        self.assertIs(replies[0].mode, SpecialistAssignmentMode.DRAFT)
        self.assertEqual(self.artifact_bytes(), before)
        self.assertIsNone(result.turn.artifact_application_request)
        synthesis = next(e.payload for e in self.history(conversation) if type(e.payload) is c.CollaborationLeadSynthesisPayload)
        self.assertEqual(synthesis.unresolved_input_ids, ())
        from tests.test_lead_artifact_application import _application_decision
        response = _artifact_outcome(
            operations=lambda call, material, *, input_payload: [{"kind": "replace", "evidence_id": _status_id(input_payload, OUTLINE), "new_text": "Synthetic reviewed outline.\n"}],
            evidence_status="empty", evidence_ids=lambda call, material, *, input_payload: [_status_id(input_payload, OUTLINE)],
            objective_intent={"action": "start", "target_paths": {OUTLINE: True},
                              "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(proposed.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def prepare(call, provider):
            return response(call, provider)
        self.backend.enqueue(prepare)
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(result.turn))
        self.assertEqual(review.proposal.participants, (PLOT_ARCHITECT,))
        self.assertIs(review.proposal.assignments[0].mode, SpecialistAssignmentMode.REVIEW)
        self.assertEqual(review.proposal.budget.rounds_spent, 1)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(review, c.CollaborationConfirmationDecision.CONFIRM))
        await self.runtime.decide_artifact_application(_application_decision(reviewed.turn, c.ArtifactApplicationDecisionKind.REJECT))
        prefix = self.history(conversation)
        completed = c.project_collaboration_history(prefix)
        self.assertIs(completed.current_artifact_application_result.outcome, c.ArtifactApplicationOutcomeKind.REJECTED)
        self.assertEqual(completed.rounds_spent, 2)
        memory = await self.runtime.record_editorial_decision(self.session_id, conversation, editorial_request("completed_task_memory"))
        self.assertIs(memory.status, editorial.EditorialRecordStatus.RECORDED)
        unchanged = c.project_collaboration_history(self.history(conversation))
        self.assertEqual((unchanged.rounds_spent, unchanged.physical_calls_spent), (completed.rounds_spent, completed.physical_calls_spent))
        self.assertGreaterEqual(12 - completed.physical_calls_spent, 4)
        self.assertEqual(self.artifact_bytes(), before)
        before_blocked = len(self.backend.calls)
        blocked = await self.runtime.lead_turn(self.session_id, conversation, _ADMISSION_BRIEF)
        self.assertEqual(len(self.backend.calls) - before_blocked, 0, blocked)
        self.assertIsNone(blocked.proposal, blocked)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(prefix):]))
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)


        prefix = self.history(conversation)
        inspection_before = self.runtime.inspect_lead_collaboration(self.session_id, conversation)
        discussion_task = next(item for item in inspection_before.available_tasks
            if item.ref.task_id == proposed.proposal.authoring_scope.task_id)
        self.assertEqual(discussion_task.scope, proposed.proposal.authoring_scope)
        discussion_decision = task.LeadTaskDecision.from_task(discussion_task.ref, task.LeadTaskAction.DISCUSS,
            text="Inspect context before offering fresh work.")
        late_call_start = len(self.backend.calls)
        self.assertEqual(tuple(call.contract.schema_group_phase for call in self.backend.calls[:2]),
                         (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE))
        self.assertEqual(tuple(call.contract.factory.schema_group_protocol for call in self.backend.calls[:2]),
                         (True, True))
        import story_writing_agents.orchestrator as orchestrator_module
        original_factory = orchestrator_module._lead_call_contract_factory
        def bounded_history_factory(*args, **kwargs):
            self.assertEqual(kwargs["stage"], "lead.front")
            self.assertIs(kwargs["schema_group_protocol"], False)
            return original_factory(*args, **kwargs)
        def request_inspection_source(call, _provider):
            offers = call.contract.offers
            self.assertEqual(offers.allowed_actions, ("search", "read", "answer"))
            self.assertEqual(dict(offers.action_limits)["nonterminal_actions"], 3)
            observation = _provider.callback_payload(call)["context_observation"]
            self.assertEqual(observation["observation_type"], "context_search_results")
            self.assertTrue(observation["search_results"])
            self.assertIn(observation["search_results"][0]["read_handle"],
                          offers.issued_handle_aliases)
            self.assertEqual(offers.completed_read_facts, ())
            self.assertEqual(offers.delivery_read_facts, ())
            self.assertEqual(call.contract.semantic_index.policy.task_revisions, ())
            self.assertEqual(call.contract.factory.rule_seed.contract_id, "lead-discussion-response-v1")
            return lead_fixture_response(call, lead_fixture_result({
                "response_type": "clarification_question",
                "response_text": "Please provide the matching prior response so I can inspect it before offering fresh work.",
                "used_evidence_ids": {},
            }), family=0)

        self.backend.enqueue(
            partial(_search_history, text="Nora must recover her brother's letter"),
            request_inspection_source,
            _visible_outcome("answer", "A third submission is not invoked."), control_plan=None)
        # USER-selected discussion inspects the same completed history without writing authority.
        with mock.patch.object(orchestrator_module, "_lead_call_contract_factory",
                               side_effect=bounded_history_factory) as boundary_factory:
            discussed = await self.runtime.decide_lead_task(discussion_decision)
            bounded = discussed.turn
        self.assertEqual(boundary_factory.call_count, 1)
        self.assertIs(orchestrator_module._lead_call_contract_factory, original_factory)
        self.assertEqual(tuple(call.contract.factory.schema_group_protocol for call in self.backend.calls[late_call_start:]),
                         (False, False))
        self.assertEqual(tuple(call.contract.schema_group_phase for call in self.backend.calls[late_call_start:]),
                         (None, None))
        calls = _submitted_calls(self.history(conversation)[len(prefix):])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.backend.calls) - late_call_start, 2)
        self.assertIs(bounded.kind, c.LeadOutcomeKind.DISCUSSION)
        self.assertEqual(bounded.discussion.response_kind, "question")
        transition = next(event for event in self.history(conversation)[len(prefix):]
            if type(event.payload) is c.CollaborationLeadTaskTransitionPayload)
        self.assertEqual(transition.payload.receipt.origin_event_id, discussion_decision.request_id)
        self.assertIs(transition.payload.receipt.action, task.LeadTaskAction.DISCUSS)
        self.assertEqual(transition.payload.receipt.source_task_ref, discussion_task.ref)
        self.assertEqual((bounded.discussion.origin_control_event_id, bounded.discussion.associated_task_ref),
            (discussed.transition_event_id, discussion_task.ref))
        inspection_after = self.runtime.inspect_lead_collaboration(self.session_id, conversation)
        self.assertEqual(next(item.scope for item in inspection_after.available_tasks if item.ref == discussion_task.ref), discussion_task.scope)
        self.assertEqual((inspection_after.rounds_spent, inspection_after.physical_calls_spent),
            (inspection_before.rounds_spent, inspection_before.physical_calls_spent))
        self.assertIsNone(bounded.proposal)
        self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for event in self.history(conversation)[len(prefix):]))
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)

    async def test_empty_inventory_does_not_add_unaffected_owners_and_mixed_scope_does(self):
        for path in (OUTLINE, CHARACTER, "core_documents/narrative_rules.md", "core_documents/part_outlines.md"):
            self.workspace.path(path).write_bytes(b"")
        before_premise = len(self.backend.calls)
        native_findings = []
        def premise_with_extra(call, provider):
            initial = self.backend.calls[before_premise]
            self.assertIs(initial.contract.factory, call.contract.factory)
            self.assertEqual(initial.scope, call.scope)
            self.assertIs(initial.call_kind, ProviderCallKind.COLLABORATION_INITIAL)
            facts = _workflow_material(provider.callback_payload(initial))["provider_constraints"]
            spec = {"question_id": facts["offered_premise_question_id"], "slot_id": "knowledge",
                "question": "Who knows the concealed motive?", "dependent_domains": ["plot_structure"]}
            value = {"kind": "proposal", "message": "Compare unresolved options.", "authoring_scope": None,
                "proposal": {"premise_decision": spec, "assignments": [{"agent_id": PLOT_ARCHITECT,
                    "instruction": "Compare options without authoring.", "mode": "premise_analysis",
                    "input_ids": list(facts["available_assignment_input_source_ids"])}]}, "authoring_assessment": None}
            native = wire_result(value, None, native_types={"proposal": "specialist_consultation_proposal"})
            proposal = native["specialist_consultation_proposal"]
            choices = dict(call.contract.semantic_index.policy.choices)
            proposal["assignments"] = author_positive_fixture_review_bases(
                proposal["assignments"], candidate_choices=choices["review_candidate_ids"])
            proposal.update(lead_fixture_assignments(proposal["assignments"],
                input_choices=choices["allocation_input_ids"], review_choices=choices["review_candidate_ids"],
                anchors={PLOT_ARCHITECT: "user_input"},
                recipients={source: PLOT_ARCHITECT for source in facts["available_assignment_input_source_ids"]}))
            proposal["premise_decision"]["dependent_domains"] = dict.fromkeys(spec["dependent_domains"], True)
            branch, = {member.owner.variant.branch_key.partition(":partition:")[0]
                for member in call.contract.semantic_index.contracts
                if type(member.owner) is LeadResultContractOwner and member.owner.variant.group_id == "premise.analysis"
                and member.rule_graph.literal_at(("response_type",)) == "specialist_consultation_proposal"}
            raw = lead_fixture_response(call, lead_fixture_result(native), family=0, branch_key=branch)
            action = json.loads(raw)["agent_action"]
            body = action["task_result"]
            self.assertIn("authoring_assessment", body["response_details"], raw)
            self.assertIsNone(body["response_details"]["authoring_assessment"], raw)
            self.assertEqual(dict(call.contract.outer_inner_schema_ids)[action["schema_id"]], body["schema_id"])
            owner = call.contract.semantic_index.select(body["schema_id"]).owner
            self.assertIs(type(owner), LeadResultContractOwner)
            self.assertEqual(owner.variant.group_id, call.contract.group_workflow.active_selection.group_id)
            native_findings.append(call.contract.structural_findings(raw))
            return raw
        self.backend.enqueue(premise_with_extra, control_plan=selection_plan('premise.analysis'))
        unscoped = await self._lead_turn(self.conversation(), "Explore who knows the motive.")
        self.assertEqual(len(self.backend.calls) - before_premise, 2, unscoped)
        self.assertEqual(tuple(call.contract.schema_group_phase for call in self.backend.calls[before_premise:]),
                         (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE))
        self.assertEqual(tuple(agent for agent, _call in self.backend.call_records[before_premise:]),
                         (LEAD_ORCHESTRATOR, LEAD_ORCHESTRATOR))
        self.assertEqual(native_findings, [()])
        self.assertIs(unscoped.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertIsNone(unscoped.proposal.authoring_scope)
        self.assertIsNone(unscoped.proposal.basis.authoring_admission)
        self.assertEqual(tuple((item.specialist_id, item.mode) for item in unscoped.proposal.assignments),
                         ((PLOT_ARCHITECT, SpecialistAssignmentMode.PREMISE_ANALYSIS),))
        before_reject = len(self.backend.calls)
        rejected = await self.runtime.decide_lead_step(_decision(unscoped, c.CollaborationConfirmationDecision.REJECT))
        self.assertEqual(len(self.backend.calls) - before_reject, 0, rejected)
        self.assertIs(rejected.turn.state, c.CollaborationStepState.REJECTED)
        single = await self.proposal_for(self.conversation())
        self.assertEqual(single.proposal.participants, (PLOT_ARCHITECT,))
        readiness = await self.record_book_readiness()
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        expected_snapshots = []
        def mixed_with_snapshot(call, provider):
            services = self.runtime.conversation_storage_services
            expected_snapshots.append((SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture(), call))
            return self._native_scoped(*ROSTER, paths=("manuscript/part-01/opening.md",),
                domains=("plot_structure", "character_world", "tone_execution"),
                authoring_assessment=_fixture_assessment(),)(call, provider)
        mixed = await self.proposal_for(self.conversation(), response=mixed_with_snapshot)
        snapshot, observed_call = expected_snapshots[0]
        prepared_call = next(event for event in snapshot.events()
            if type(event.payload) is c.CollaborationProviderCallPayload
            and (event.conversation_id, event.payload.call_id, event.payload.state) ==
            (observed_call.scope.subject_id, observed_call.call_id, c.CollaborationProviderCallState.PREPARED))
        submitted_call = next(event for event in self.history(prepared_call.conversation_id)
            if type(event.payload) is c.CollaborationProviderCallPayload
            and (event.payload.invocation_id, event.payload.call_id, event.payload.state) ==
            (prepared_call.payload.invocation_id, observed_call.call_id, c.CollaborationProviderCallState.SUBMITTED))
        services = self.runtime.conversation_storage_services
        published = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture()
        owner = next(row for row in published.conversations if row.metadata.conversation_id == submitted_call.conversation_id)
        cutoff = next(position for position, row in enumerate(owner.records, 1) if row.event_id == submitted_call.event_id)
        snapshot = replace(snapshot, conversations=tuple(
            replace(owner, records=owner.records[:cutoff]) if row.metadata.conversation_id == submitted_call.conversation_id else row
            for row in snapshot.conversations))
        expected_basis = replace(readiness.basis, prior_assessment_refs=(snapshot.readiness_ref(snapshot.readiness_heads()[0]),),
            history_coverage=snapshot.coverage, history_metadata=tuple((row.metadata.conversation_id, row.metadata.subject, row.metadata.status.value) for row in snapshot.conversations))
        self.assertEqual(mixed.proposal.basis.core_document_readiness, expected_basis)
        self.assertEqual(mixed.proposal.participants, ROSTER)
        self.assertEqual(mixed.proposal.authoring_scope.domains, tuple(ArtifactReviewDomain))
        self.assertTrue(all(agent == LEAD_ORCHESTRATOR for agent, _ in self.backend.call_records))


        from tests.test_lead_artifact_application import _application_decision
        from tests.test_lead_story_commit import _commit_decision
        subprocess.run(['git', 'add', '--', 'core_documents', 'manuscript'], cwd=self.workspace.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'Seed lead commit test fixtures'], cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL)
        conversation = self.conversation()
        before = self.artifact_bytes()
        analysis_proposal, reported = await _confirmed_premise(self, conversation)
        judgment = next(event.payload for event in self.history(conversation) if type(event.payload) is c.CollaborationLeadJudgmentPayload and event.step_id == analysis_proposal.step_id)
        with self.assertRaisesRegex(ValueError, "premise analysis evidence requires a proposal"):
            replace(judgment, kind=c.LeadOutcomeKind.ANSWER)
        with self.assertRaisesRegex(ValueError, "cannot carry authoring scope"):
            replace(judgment, authoring_scope=single.proposal.authoring_scope)
        with self.assertRaisesRegex(ValueError, "explicit assignment modes require"):
            replace(analysis_proposal.proposal, premise_decision=None)
        report, ref = reported.premise_report, reported.premise_report_ref
        option = report.options[0]
        self.assertEqual(self.artifact_bytes(), before)
        self.assertEqual(len(option.reply_dispositions), len(analysis_proposal.proposal.assignments))
        selection = _premise_choice(reported, ("manuscript/part-01/knowledge.md",))
        delegated = replace(selection, choice_policy=premise.PremiseChoicePolicy.DELEGATED_MINOR, option_id=None, material_digest=None, allowed_choices=(premise.PremiseAllowedChoice(option.option_id, option.material_digest),))
        self.assertEqual(premise.resolve_premise_choice(delegated, report), option)
        with self.assertRaises(ValueError):
            premise.resolve_premise_choice(replace(delegated, allowed_choices=(premise.PremiseAllowedChoice(option.option_id, "f" * 64),)), report)
        prefix = self.history(conversation)
        with self.assertRaises(ValueError):
            await self.runtime.lead_turn(
                self.session_id,
                conversation,
                "Write from the selected whole premise." + "\n" + _ADOPTION_DIRECTION,
                premise_selection=replace( selection, material_digest="f" * 64, ), )
        self.assertEqual(self.history(conversation), prefix)

        text = "Write from the selected whole premise."
        path = selection.target_paths[0]
        self.backend.enqueue(_selected_premise_proposal(selection, authoring_assessment=_fixture_assessment(
            foundation_ids=(
                "selected_premise",
            ),
        )), control_plan=selection_plan('consultation.authoring.content'))
        drafted = await self.runtime.lead_turn(
            self.session_id,
            conversation,
            text + "\n" + _ADOPTION_DIRECTION,
            premise_selection=selection, )
        self.assertIs(drafted.kind, c.LeadOutcomeKind.PROPOSAL, drafted)
        chosen_prefix = self.history(conversation)
        calls_before_retry = len(self.backend.call_records)
        await self.runtime.lead_turn(
            self.session_id,
            conversation,
            text + "\n" + _ADOPTION_DIRECTION,
            premise_selection=selection, )
        restarted = type(self.runtime)(self.control_workspace, self.backend)
        await restarted.lead_turn(
            self.session_id,
            conversation,
            text + "\n" + _ADOPTION_DIRECTION,
            premise_selection=selection, )
        foreign = self.conversation()
        with self.assertRaises(ValueError):
            await self.runtime.lead_turn(
                self.session_id,
                foreign,
                text + "\n" + _ADOPTION_DIRECTION,
                premise_selection=selection, )
        self.assertEqual(self.history(foreign), ())
        with self.assertRaises(ValueError):
            await self.runtime.lead_turn(
                self.session_id,
                conversation,
                text + " Changed." + "\n" + _ADOPTION_DIRECTION,
                premise_selection=selection, )
        self.assertEqual((self.history(conversation), len(self.backend.call_records)), (chosen_prefix, calls_before_retry))
        self.assertEqual(drafted.proposal.authoring_scope.task_revision, 1)
        self.assertEqual(drafted.proposal.basis.shared_premises[0].selected_premise, option.premise_text)
        for assignment in drafted.proposal.assignments:
            if assignment.mode is SpecialistAssignmentMode.DRAFT:
                self.backend.enqueue_for(assignment.specialist_id, lambda call, provider: wire_answer({"assessment": "drafted", "message": "The selected knowledge remains intentionally uncertain.", "premise_conflicts": []}, wire_contract_id(call.payload)))
            else:
                self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("constraints_assessed"))
        def recommend(call, provider):
            reply = next(item["reply_event_id"] for item in _workflow_material(provider.callback_payload(call))["replies"] if item["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, recommended_action=action(
                    target_paths=(path,), source_ids=("user_input", "selected_premise", reply))
            )(call, provider)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, recommend)
        finished = await self.runtime.decide_lead_step(_decision(drafted, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(finished.turn.recommendation_request, finished)
        frames = [_workflow_material(call.payload) for agent, call in self.backend.call_records if wire_contract_id(call.payload) == "specialist-assignment-response-v5"]
        self.assertEqual([(frame["premise_selection"]["selected_text"], frame["shared_premises"][0]["selected_premise"]) for frame in frames], [(option.premise_text, option.premise_text)] * 2)
        response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": path, "new_text": "The selected knowledge remains intentionally uncertain.\n"}], evidence_status="incomplete", evidence_ids=lambda call, material, *, input_payload: [input_payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent=dict(action="start", target_paths={path: True}, next_step="prepare"),
            authoring_scope=lambda _: c.authoring_scope_to_dict(drafted.proposal.authoring_scope),
            authoring_assessment=asdict(_fixture_assessment(foundation_ids=("selected_premise",))),)
        def integrate(call, provider):
            return response(call, provider)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, integrate)
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(finished.turn))
        self.assertEqual(review.proposal.participants, ROSTER)
        self.assertEqual(self.artifact_bytes(), before)
        for assignment in review.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(review, c.CollaborationConfirmationDecision.CONFIRM))
        frames = [_workflow_material(call.payload) for agent, call in self.backend.call_records if agent in ROSTER][-3:]
        candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["kind"] == "artifact_candidate") for frame in frames]
        self.assertEqual(candidates, [candidates[0]] * len(ROSTER))
        self.assertEqual([frame["shared_premises"] for frame in frames], [shared_premises_to_dict(drafted.proposal.basis.shared_premises)] * len(ROSTER))
        self.assertEqual(self.artifact_bytes(), before)
        applied = await self.runtime.decide_artifact_application(_application_decision(reviewed.turn, c.ArtifactApplicationDecisionKind.APPLY))
        self.assertEqual(self.workspace.path(path).read_bytes(), b"The selected knowledge remains intentionally uncertain.\n")
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
        self.assertEqual(len(set(report.cost.submitted_call_ids)), report.cost.physical_calls_spent)
        authoring_calls = _submitted_calls(self.history(conversation)[len(prefix):])
        self.assertTrue(set(report.cost.submitted_call_ids).isdisjoint(item.call_id for item in authoring_calls))
        self.assertLessEqual(len(authoring_calls), 12)


        async def complete_selected(proposed, premise_text):
            for assignment in proposed.proposal.assignments:
                if assignment.mode is SpecialistAssignmentMode.DRAFT:
                    self.backend.enqueue_for(assignment.specialist_id, lambda call, provider: wire_answer({"assessment": "drafted", "message": premise_text, "premise_conflicts": []}, wire_contract_id(call.payload)))
                else:
                    self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("constraints_assessed"))
            self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
            return await self.runtime.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))

        reused_selection = replace(selection, request_id="reuse-report-after-apply")
        boundary = await self.runtime.lead_turn(
            self.session_id,
            conversation,
            "Draft changed whole premise freshly." + "\n" + _ADOPTION_DIRECTION,
            premise_selection=reused_selection, )
        commit = applied.commit_request or await self.runtime.reprepare_story_commit(boundary.story_commit_preparation_request)
        await self.runtime.decide_story_commit(_commit_decision(replace(applied, commit_request=commit), c.StoryCommitDecision.REJECT))
        self.backend.enqueue(_selected_premise_proposal(reused_selection, authoring_assessment=_fixture_assessment(
            foundation_ids=(
                "selected_premise",
            ),
        )), control_plan=selection_plan('consultation.authoring.content'))
        reused = await self.runtime.lead_turn(
            self.session_id,
            conversation,
            "Draft changed whole premise freshly." + "\n" + _ADOPTION_DIRECTION,
            premise_selection=reused_selection, )
        self.assertIs(reused.kind, c.LeadOutcomeKind.PROPOSAL, reused)
        self.assertEqual(reused.proposal.authoring_scope.task_revision, 1)
        self.assertNotEqual(reused.proposal.authoring_scope.task_id, drafted.proposal.authoring_scope.task_id)
        reuse_completed = await complete_selected(reused, option.premise_text)
        self.assertIs(reuse_completed.turn.state, c.CollaborationStepState.COMPLETED)
        previous = self.history(conversation)
        changed_option = report.options[1]
        successor = replace(selection, request_id="change-whole-direction", option_id=changed_option.option_id, material_digest=changed_option.material_digest, authoring_task_id=reused.proposal.authoring_scope.task_id, expected_authoring_task_revision=reused.proposal.authoring_scope.task_revision)
        self.backend.enqueue(_selected_premise_proposal(successor, authoring_assessment=_fixture_assessment(
            foundation_ids=(
                "selected_premise",
            ),
        )), control_plan=selection_plan('consultation.authoring.content'))
        producer_notes = self._observe_failure_producers()
        changed = await self.runtime.lead_turn(
            self.session_id,
            conversation,
            "Draft changed whole premise freshly." + "\n" + _ADOPTION_DIRECTION,
            premise_selection=successor, )
        self.assertIs(changed.kind, c.LeadOutcomeKind.PROPOSAL, (changed, tuple(producer_notes)))
        self.assertEqual((changed.proposal.authoring_scope.task_id, changed.proposal.authoring_scope.task_revision), (reused.proposal.authoring_scope.task_id, 2))
        self.assertEqual(changed.proposal.basis.shared_premises[0].selected_premise, changed_option.premise_text)
        self.assertNotEqual(changed.proposal.basis.shared_premises, drafted.proposal.basis.shared_premises)
        confirmed = await complete_selected(changed, changed_option.premise_text)
        self.assertIs(confirmed.turn.state, c.CollaborationStepState.COMPLETED)
        new_frames = [_workflow_material(call.payload) for agent, call in self.backend.call_records if wire_contract_id(call.payload) == "specialist-assignment-response-v5"][-2:]
        self.assertEqual([frame["premise_selection"]["selected_text"] for frame in new_frames], [changed_option.premise_text] * 2)
        self.assertEqual(self.history(conversation)[:len(previous)], previous)
        self.assertEqual(self.workspace.path(path).read_bytes(), b"The selected knowledge remains intentionally uncertain.\n")
        self.assertLessEqual(len(_submitted_calls(self.history(conversation)[len(previous):])), 12)
        # A completed draft remains active until final review and Apply.
        # Hold it before independent premise reports, retaining its limit of twelve calls.
        completed_scope = changed.proposal.authoring_scope
        completed_task, = (item for item in self.runtime.inspect_lead_collaboration(self.session_id, conversation).available_tasks
                           if (item.ref.task_id, item.ref.task_revision) == (completed_scope.task_id, completed_scope.task_revision))
        self.assertEqual(completed_task.status, "active")
        hold_history = self.history(conversation)
        hold_selection = c.project_collaboration_history(hold_history).premise_selection
        hold_artifacts = self.artifact_bytes()
        hold_call_count = len(self.backend.calls)
        held = await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task(completed_task.ref, task.LeadTaskAction.HOLD))
        self.assertEqual(held.task_ref, completed_task.ref)
        self.assertIsNone(held.turn)
        self.assertEqual(len(self.backend.calls), hold_call_count)
        self.assertEqual(c.project_collaboration_history(self.history(conversation)).premise_selection, hold_selection)
        self.assertEqual(self.history(conversation)[:len(hold_history)], hold_history)
        self.assertEqual(self.artifact_bytes(), hold_artifacts)
        for decision in (premise.PremiseDecisionAction.REJECT, premise.PremiseDecisionAction.DEFER):
            retained_choice = c.project_collaboration_history(self.history(conversation)).premise_selection
            _, later = await _confirmed_premise(self, conversation)
            ref = later.premise_report_ref
            selection = premise.PremiseDecisionSelection(
                "later-" + decision.value, decision, ref.source_conversation_id,
                ref.report_event_id, ref.report_digest, ref.question_id)
            prefix, call_count = self.history(conversation), len(self.backend.call_records)
            controlled = await self.runtime.lead_turn(
                self.session_id,
                conversation,
                "Keep this choice unresolved." + "\n" + _ADOPTION_DIRECTION,
                premise_selection=selection, )
            projection = c.project_collaboration_history(self.history(conversation))
            self.assertEqual(projection.premise_decision, selection)
            self.assertEqual(projection.premise_selection, retained_choice)
            self.assertEqual(len(self.backend.call_records), call_count)
            self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
            self.assertIsNone(controlled.proposal)
            self.assertIsNone(controlled.artifact_application_request)
            self.assertIsNone(controlled.story_commit_request)

        from story_writing_agents import specialist_routing as routing, review_contracts as review
        from story_writing_agents.agent_context.manifest import ContextSourceKind
        from story_writing_agents.agent_context.accounting import canonical_provider_json
        from tests.provider_wire_fixture import fixture_schema_node
        conversation = self.conversation("Focused motivation review and retained feedback")
        def focused_plan(value, _material):
            value["proposal"]["stage_plan"] = {
                "stage": "specialist_work", "work_kind": "deep_review", "focus_domains": ["character_world"],
                "specialist_ids": [CHARACTER_CUSTODIAN],
                "questions": [{"question_id": "motive", "question": "Does the supplied decision follow the established promise?",
                    "source_input_ids": ["user_input"], "required_condition_ids": ["user_input"]}],
                "required_condition_ids": ["user_input"], "agreed_direction": "Preserve the promise and leave the secret unresolved.",
                "unresolved_questions": ["Is the decision trigger established?"],
                "expected_result": "A bounded Character judgment, not a replacement draft or final candidate approval."}
        staged_response = scoped_proposal(CHARACTER_CUSTODIAN, activity="review",
            intent="review_only", paths=(), domains=tuple(domain.value
            for domain in ArtifactReviewDomain), mutate=focused_plan, native_inputs={
                "source_choices": ("user_input",), "input_choices": ("user_input",),
                "anchors": {CHARACTER_CUSTODIAN: "user_input"}, "recipients": {}, "family": 0})
        stage_checks = []
        def response(call, provider):
            self.assertIsNone(call.contract.factory.constraints.scope)
            packet = json.loads(staged_response(call, provider))["agent_action"]
            details = packet["task_result"]["response_details"]
            native_plan = details["specialist_consultation_proposal"]["stage_plan"]
            choices = dict(call.contract.semantic_index.policy.choices)
            native_plan["focus_domains"] = finite_fixture_selection(
                native_plan["focus_domains"], native_plan["focus_domains"])
            native_plan["specialist_ids"] = finite_fixture_selection(
                native_plan["specialist_ids"], native_plan["specialist_ids"])
            native_plan["required_condition_ids"] = finite_fixture_selection(
                choices["required_input_ids"], native_plan["required_condition_ids"])
            question = native_plan["questions"][0]
            question["source_input_ids"] = finite_fixture_selection(
                choices["allocation_input_ids"], question["source_input_ids"])
            question["required_condition_ids"] = finite_fixture_selection(
                choices["required_input_ids"], question["required_condition_ids"])
            packet.pop("schema_id")
            packet["task_result"].pop("schema_id")
            details.pop("variant_id", None)
            stage_members = {member.schema_id: member for member in call.contract.semantic_index.contracts
                             if type(member.owner) is LeadResultContractOwner}
            stage_candidates, stage_rejections = [], []
            for candidate_outer, candidate_inner in call.contract.outer_inner_schema_ids:
                if candidate_inner not in stage_members:
                    continue
                candidate_raw = indexed_response(packet, outer_schema_id=candidate_outer,
                    inner_schema_id=candidate_inner, contract=call.contract)
                candidate_findings = stage_members[candidate_inner].rule_graph.findings(
                    json.loads(candidate_raw)["agent_action"]["task_result"])
                if candidate_findings:
                    stage_rejections.append((candidate_inner, candidate_findings))
                else:
                    stage_candidates.append(candidate_raw)
            stage_checks.append((len(stage_candidates), tuple(stage_rejections),
                call.contract.structural_findings(stage_candidates[0]) if len(stage_candidates) == 1 else None))
            self.assertEqual(len(stage_candidates), 1)
            return stage_candidates[0]
        before = len(self.backend.calls)
        self.backend.enqueue(response, control_plan=selection_plan("consultation.review_only"))
        focused = await self.runtime.lead_turn(self.session_id, conversation,
            "Perform a deep motivation review of this complete supplied scene, not a rewrite: "
            "Mina promised to protect her brother and hides a letter. Preserve the promise and leave the secret unresolved.")
        self.assertEqual([(count, findings) for count, _rejected, findings in stage_checks],
                         [(1, ())], stage_checks)
        self.assertIs(focused.kind, c.LeadOutcomeKind.PROPOSAL, focused.message)
        self.assertIs(focused.proposal.authoring_scope.intent, AuthoringIntent.REVIEW_ONLY)
        self.assertEqual(focused.proposal.participants, (CHARACTER_CUSTODIAN,))
        self.assertEqual(routing.required_authoring_domains(focused.proposal.authoring_scope), tuple(ArtifactReviewDomain))
        self.assertEqual(routing.required_authoring_specialists(focused.proposal.authoring_scope), ROSTER)
        plan = focused.proposal.stage_plan
        self.assertIs(plan.work_kind, routing.SpecialistWorkKind.DEEP_REVIEW)
        self.assertEqual((plan.task_id, plan.task_revision),
            (focused.proposal.authoring_scope.task_id, focused.proposal.authoring_scope.task_revision))
        self.assertEqual(tuple(agent for agent, _ in self.backend.call_records[before:]), (LEAD_ORCHESTRATOR,) * 2)
        feedback = "CHARACTER_REVIEW_838: The promise is established; the decision trigger is absent."
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome("supports", feedback))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="A bounded Character review is available for explanation."))
        confirmed_start = len(self.backend.call_records)
        completed = await self.runtime.decide_lead_step(_decision(focused, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(completed.turn.state, c.CollaborationStepState.COMPLETED)
        self.assertIsNone(completed.turn.artifact_application_request)
        self.assertIsNone(completed.turn.story_commit_request)
        stage_inputs = [(agent, _workflow_material(call.payload))
                        for agent, call in self.backend.call_records[confirmed_start:]]
        self.assertEqual([agent for agent, _ in stage_inputs],
                         [CHARACTER_CUSTODIAN, LEAD_ORCHESTRATOR])
        expected_wire_plan = {key: value for key, value in routing.stage_plan_to_dict(plan).items()
                              if key not in {'task_id', 'task_revision'}}
        expected_wire_plan['questions'] = [{key: value for key, value in question.items()
            if key not in {'task_id', 'task_revision'}} for question in expected_wire_plan['questions']]
        self.assertEqual([material["stage_plan"] for _, material in stage_inputs],
                         [expected_wire_plan] * 2)
        specialist_calls = [agent for agent, _ in self.backend.call_records[before:] if agent != LEAD_ORCHESTRATOR]
        self.assertEqual(specialist_calls, [CHARACTER_CUSTODIAN])
        original_history = self.history(conversation)
        replies = [event for event in original_history if type(event.payload) is c.CollaborationSpecialistReplyPayload
                   and event.payload.proposal_id == focused.proposal.proposal_id]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].payload.message, feedback)
        from tests.test_agent_exchange_viewer import _assert_typed_history_phase
        self.assertNotEqual(replies[0].turn_id, focused.turn_id)
        _assert_typed_history_phase(self, original_history, replies[0].payload.provider_invocation_id,
                                   CHARACTER_CUSTODIAN, "specialist_work", focused.proposal.authoring_scope)
        explained = "Character reported that the promise is established but the decision trigger is absent."
        def explain_feedback(call, provider):
            supplied = canonical_provider_json(provider.callback_payload(call)).decode("utf-8")
            self.assertIn(feedback, supplied)
            self.assertIn(replies[0].event_id, supplied)
            facts = {item.evidence_id: item for item in
                call.contract.offers.completed_read_facts + call.contract.offers.delivery_read_facts}
            history_ids = tuple(identity for identity, item in facts.items()
                                if item.source_kind is ContextSourceKind.CONVERSATION)
            self.assertTrue(history_ids)
            members = {}
            for member in call.contract.semantic_index.contracts:
                if type(member.owner) is not LeadResultContractOwner:
                    continue
                schema = member.rule_graph.schema_value()
                details = fixture_schema_node(schema, schema["properties"]["response_details"])
                details = fixture_schema_node(schema, details.get("anyOf", (details,))[0])
                if "preliminary_review" in details["properties"]:
                    record = fixture_schema_node(schema, details["properties"]["preliminary_review"])
                    inspection = fixture_schema_node(schema, record["properties"]["inspection"])
                    key = member.owner.variant.branch_key.partition(":partition:")[0]
                    known_inspection = members.setdefault(key, inspection)
                    self.assertEqual(set(known_inspection["properties"]), set(inspection["properties"]))
            self.assertEqual(len(members), 1)
            branch, = members
            inspection = members[branch]
            preliminary_choices = tuple(target["input_id"] for target in map(json.loads,
                dict(call.contract.semantic_index.policy.choices)["preliminary_targets"])
                if target["availability"] != review.ReviewTargetAvailability.UNREAD.value)
            return lead_fixture_response(call, {"response_type": "answer", "response_text": explained,
                "response_details": {"task_scope": lead_fixture_bound_scope(call,
                    _live_scope(call, activity="review", domains=("character_world",)), record=True),
                    "preliminary_review": {"stage": "lead_preliminary_review",
                        "inspection": {identity: "inspected" if identity == "user_input" or identity in history_ids else "excluded"
                                       for identity in inspection["properties"]},
                        "findings": [{"message": explained,
                    "evidence_input_ids": finite_fixture_selection(preliminary_choices, history_ids)}],
                        "conflicts": [], "missing_information": [], "further_review_required": False, "next_action": "finish_direct"}}},
                family=0, branch_key=branch)
        before = len(self.backend.calls)
        self.backend.enqueue(partial(_search_history, text="CHARACTER_REVIEW_838"), _read_history_hit, explain_feedback,
            control_plan=selection_plan("answer.observed_facts", before_script_response=2))
        explanation = await self.runtime.lead_turn(self.session_id, conversation,
            "Explain that recorded Character feedback without asking a specialist to review it again.")
        self.assertIs(explanation.kind, c.LeadOutcomeKind.ANSWER, explanation.message)
        self.assertEqual(explanation.message, explained)
        self.assertEqual(tuple(agent for agent, _ in self.backend.call_records[before:]), (LEAD_ORCHESTRATOR,) * 4)
        self.assertEqual(self.history(conversation)[:len(original_history)], original_history)
        self.assertEqual(explanation.preliminary_review.findings[0].message, explained)
        self.assertTrue(any(target.kind is review.ReviewTargetKind.READ_SOURCE
            and target.availability is review.ReviewTargetAvailability.COMPLETED_READ
            for target in explanation.preliminary_review.targets))

    async def test_declared_substantive_direct_outcomes_fail_closed(self):
        for kind in ("answer", "recommendation", "critique"):
            with self.subTest(kind=kind):
                conversation = self.conversation()
                def forbidden(call, provider, kind=kind):
                    native = {'family': 0, 'lookup_response_type': 'specialist_consultation_proposal',
                              'source_choices': dict(call.contract.semantic_index.policy.choices)['input_ids']}
                    return scoped_proposal(PLOT_ARCHITECT, kind=kind,
                        authoring_assessment=_fixture_assessment(), native_inputs=native)(call, provider)
                result = await self._rejected_pair(conversation, _ADMISSION_BRIEF, forbidden,
                    selection_group='consultation.authoring.content')
                self._assert_continuation(result, 'proposal')
                self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)))
        conversation = self.conversation()
        self.backend.enqueue(_visible_outcome("question", "Which existing task should this status request concern?"), control_plan=selection_plan('question.workflow'))
        question = await self.runtime.lead_turn(self.session_id, conversation, "Clarify which task is active.")
        self.assertIs(question.kind, c.LeadOutcomeKind.QUESTION)

        from story_writing_agents import review_contracts as review
        from story_writing_agents.agent_context.accounting import canonical_provider_json
        from story_writing_agents.specialist_routing import required_authoring_domains, artifact_domain
        from tests.provider_wire_fixture import (fixture_schema_node, offered_fixture_bindings,
                                                indexed_response, finite_fixture_read_selection)
        from tests.test_lead_orchestration import _live_scope
        part_outline, rules = "core_documents/part_outlines.md", "core_documents/narrative_rules.md"
        documents = {
            OUTLINE: "Mina promises to protect her brother, then hides the letter. D2 reveals the secret. "
                     "The complete outline requires finding, handing off, and delivering the letter.",
            CHARACTER: "Mina keeps her promise to protect her brother. The character sheet keeps the secret until D3.",
            part_outline: "Mina finds the letter and later delivers it; no handoff event is recorded.",
            rules: "Use past tense. Preserve the stated promise and suspense constraints.",
        }
        cases = (
            ("motive", "Explain Mina's existing choice from the recorded motive; do not rewrite it.",
             (OUTLINE, CHARACTER), "Mina hides the letter to keep her promise to protect her brother.", False),
            ("style", "Compare 'Mina opens the letter' with the explicit tense rule; do not edit the text.",
             (rules,), "'opens' is present tense; the supplied rule requires past tense.", False),
            ("omission", "Compare the part outline with the complete outline; identify an omitted event.",
             (OUTLINE, part_outline), "The part outline omits the required handoff between finding and delivery.", False),
            ("impact", "Identify the evidenced scope of moving the reveal from D2 to D3; do not change anything.",
             (OUTLINE, CHARACTER), "The outline and character sheet need alignment. No manuscript was inspected, "
             "so manuscript-wide effects remain unverified.", False),
            ("conflict", "Explain the two secret-reveal dates; I have not chosen which document to change.",
             (OUTLINE, CHARACTER), "The outline reveals the secret at D2; the character sheet keeps it until D3.", True),
            ("alternatives", "Compare revealing at D2 and D3 against both suspense and the promise; choose neither.",
             (OUTLINE, CHARACTER), "D2 reveals earlier and shortens suspense; D3 prolongs suspense. "
             "Either direction must preserve Mina's promise; the user has not selected one.", True),
            ("foundations", "Give a preliminary review of all four core documents; disclose everything not inspected.",
             (OUTLINE, CHARACTER), "Only the global outline and character sheet were inspected. "
             "The part outline and narrative rules remain uninspected; whole-foundation completion is not established.", True),
        )
        retained_reviews = []
        for label, user_text, paths, finding, needs_decision in cases:
            declared_paths = tuple(documents) if label == "foundations" else paths
            marker = f"PRELIMINARY_READ_838_{label}"
            for path in paths:
                self.workspace.path(path).write_text(marker + "\n" + documents[path], encoding="utf-8")
            conversation = self.conversation(f"Grounded preliminary {label}")
            authored, read_ids = [], []
            callback_checks = []
            def search(call, _provider):
                outer, inner = offered_fixture_bindings(call, action_type="search_context",
                    response_type=None, family=1)
                return indexed_response({"action_type": "search_context", "artifact_directory_prefix": "core_documents/",
                    "queries": [{"search_text": marker, "source_types": {"story_document": True}}]},
                    outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
            def read_all(call, provider):
                observation = provider.callback_payload(call)["context_observation"]
                callback_checks.append((observation["observation_type"] == "context_search_results",
                                        "read-kind", observation["observation_type"]))
                self.assertEqual(observation["observation_type"], "context_search_results")
                handles = tuple(row["read_handle"] for row in observation["search_results"])
                callback_checks.append((len(handles) == len(paths), "handle-count", len(handles), len(paths)))
                self.assertEqual(len(handles), len(paths))
                provider.used_handles = handles
                outer, inner = offered_fixture_bindings(call, action_type="read_context", response_type=None, family=0)
                return indexed_response({"action_type": "read_context", "read_handles":
                    finite_fixture_read_selection(call.contract.offers.issued_handle_aliases, handles)},
                    outer_schema_id=outer, inner_schema_id=inner, contract=call.contract)
            def preliminary(call, provider):
                actual_input = canonical_provider_json(provider.callback_payload(call)).decode("utf-8")
                for path in paths:
                    callback_checks.append((documents[path] in actual_input, "source-present", path))
                    self.assertIn(documents[path], actual_input)
                facts = {item.evidence_id: item for item in
                         call.contract.offers.completed_read_facts + call.contract.offers.delivery_read_facts}
                by_path = {item.artifact_locator: item.evidence_id for item in facts.values()}
                callback_checks.append((set(paths).issubset(by_path), "read-paths", tuple(sorted(by_path))))
                self.assertTrue(set(paths).issubset(by_path))
                read_ids[:] = [by_path[path] for path in paths]
                candidates = {}
                for member in call.contract.semantic_index.contracts:
                    if type(member.owner) is not LeadResultContractOwner:
                        continue
                    schema = member.rule_graph.schema_value()
                    details = fixture_schema_node(schema, schema["properties"]["response_details"])
                    details = fixture_schema_node(schema, details.get("anyOf", (details,))[0])
                    if "preliminary_review" in details["properties"]:
                        record = fixture_schema_node(schema, details["properties"]["preliminary_review"])
                        inspection = fixture_schema_node(schema, record["properties"]["inspection"])
                        key = member.owner.variant.branch_key.partition(":partition:")[0]
                        known_inspection = candidates.setdefault(key, inspection)
                        callback_checks.append((set(known_inspection["properties"]) == set(inspection["properties"]),
                                                "partition-fields", key, tuple(inspection["properties"])))
                        self.assertEqual(set(known_inspection["properties"]), set(inspection["properties"]))
                callback_checks.append((len(candidates) == 1, "branch-census", tuple(candidates)))
                self.assertEqual(len(candidates), 1)
                branch, = candidates
                inspection = candidates[branch]
                preliminary_targets = tuple(map(json.loads,
                    dict(call.contract.semantic_index.policy.choices)["preliminary_targets"]))
                preliminary_choices = tuple(target["input_id"] for target in preliminary_targets
                    if target["availability"] != review.ReviewTargetAvailability.UNREAD.value)
                excluded_scope = any(target["availability"] == review.ReviewTargetAvailability.UNREAD.value and
                    target["target_id"] in declared_paths for target in preliminary_targets)
                read_selection = finite_fixture_selection(preliminary_choices, read_ids)
                native_review = {"stage": "lead_preliminary_review",
                    "inspection": {identity: "inspected" if identity == "user_input" or identity in facts else "excluded"
                                   for identity in inspection["properties"]},
                    "findings": [{"message": finding, "evidence_input_ids": read_selection}],
                    "conflicts": [{"message": finding, "evidence_input_ids": read_selection}] if label == "conflict" else [],
                    "missing_information": (["Part outline and narrative rules were not inspected."] if label == "foundations"
                        else ["The user has not selected which direction to adopt."] if needs_decision else []) + (["Only bounded READ ranges were inspected; whole-document coverage remains excluded."]
                        if excluded_scope else []),
                    "further_review_required": needs_decision,
                    "next_action": "request_information" if needs_decision else "finish_direct"}
                declared_domains = {artifact_domain(path) for path in declared_paths}
                scope = lead_fixture_bound_scope(call, _live_scope(call, activity="review", domains=tuple(domain.value for domain in ArtifactReviewDomain if domain in declared_domains),
                                                                                            paths=declared_paths), record=True)
                raw = lead_fixture_response(call, {"response_type": "answer", "response_text": finding,
                    "response_details": {"task_scope": scope, "preliminary_review": native_review}},
                    family=0, branch_key=branch)
                preliminary_findings = call.contract.structural_findings(raw)
                callback_checks.append((not preliminary_findings, "native-findings", preliminary_findings))
                self.assertEqual(preliminary_findings, ())
                authored.append(native_review)
                return raw
            original_artifacts, before = self.artifact_bytes(), len(self.backend.calls)
            self.backend.enqueue(search, read_all, preliminary,
                control_plan=selection_plan("answer.observed_facts", before_script_response=2))
            result = await self.runtime.lead_turn(self.session_id, conversation, user_text)
            self.assertTrue(all(check[0] for check in callback_checks), callback_checks)
            self.assertIs(result.kind, c.LeadOutcomeKind.ANSWER, result.message)
            self.assertEqual(result.message, finding)
            self.assertEqual(len(self.backend.calls) - before, 4)
            self.assertEqual(tuple(agent for agent, _ in self.backend.call_records[before:]), (LEAD_ORCHESTRATOR,) * 4)
            self.assertEqual(tuple(call.contract.schema_group_phase for call in self.backend.calls[before:]),
                             (LeadGroupPhase.SELECT,) * 3 + (LeadGroupPhase.GENERATE,))
            events = self.history(conversation)
            judgment_event = next(event for event in reversed(events)
                if type(event.payload) is c.CollaborationLeadJudgmentPayload
                and event.payload.provider_invocation_id == result.provider_invocation_id)
            scope = judgment_event.payload.authoring_scope
            report = result.preliminary_review
            self.assertIs(type(report), review.LeadPreliminaryReview)
            self.assertEqual((report.task_id, report.task_revision),
                             (scope.task_id, scope.task_revision))
            self.assertEqual(set(scope.target_paths), set(declared_paths))
            self.assertTrue(required_authoring_domains(scope))
            self.assertTrue(set(read_ids).issubset(report.inspected_input_ids))
            self.assertEqual(report.findings[0].message, finding)
            self.assertEqual(report.next_action.value, authored[0]["next_action"])
            targets = {item.target_id: item for item in report.targets
                       if item.kind is review.ReviewTargetKind.WRITING_ARTIFACT}
            self.assertTrue(set(paths).issubset(targets))
            for path in paths:
                self.assertEqual(targets[path].source_digest,
                                 "sha256:" + hashlib.sha256(self.workspace.path(path).read_bytes()).hexdigest())
            if label == "foundations":
                self.assertEqual({targets[path].input_id for path in declared_paths}, set(report.excluded_input_ids))
                for path in (part_outline, rules):
                    self.assertIs(targets[path].availability, review.ReviewTargetAvailability.UNREAD)
                    self.assertEqual(targets[path].source_status, "present")
                    self.assertIsNone(targets[path].content_digest)
                    self.assertIsNone(targets[path].boundary_digest)
                    self.assertFalse(targets[path].whole_source)
                self.assertFalse(report.whole_target_coverage)
                self.assertEqual(set(read_ids), set(report.inspected_input_ids) - {"user_input"})
            self.assertEqual(self.artifact_bytes(), original_artifacts)
            self.assertIsNone(result.proposal)
            self.assertIsNone(result.artifact_application_request)
            self.assertIsNone(result.story_commit_request)
            self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for event in events))
            self.assertEqual(judgment_event.payload.preliminary_review, report)
            attested = next(target for target in report.targets if target.input_id == read_ids[0])
            for field, value in (("revision", "tampered-receipt-revision"),
                                 ("content_digest", "sha256:" + hashlib.sha256(b"tampered selected bytes").hexdigest())):
                changed_target = replace(attested, **{field: value})
                changed_report = replace(report, targets=tuple(changed_target if target.input_id == attested.input_id
                    else target for target in report.targets))
                with self.assertRaises(ValueError):
                    replace(judgment_event.payload, preliminary_review=changed_report)
                changed_wire = json.loads(c.encode_collaboration_event(judgment_event))
                wire_target = next(target for target in changed_wire["payload"]["preliminary_review"]["targets"]
                                   if target["input_id"] == attested.input_id)
                wire_target[field] = value
                with self.assertRaises(ValueError):
                    c.decode_collaboration_event(canonical_provider_json(changed_wire))
            retained_reviews.append((conversation, events, report))
            restarted = StoryRuntime(self.control_workspace, backend=self.backend)
            for previous_conversation, original_events, original_report in retained_reviews:
                restored = restarted.conversation_storage_services.event_store.read(self.session_id, previous_conversation)
                self.assertEqual(restored, original_events)
                restored_judgment = next(event.payload for event in reversed(restored)
                    if type(event.payload) is c.CollaborationLeadJudgmentPayload)
                self.assertEqual(restored_judgment.preliminary_review, original_report)

    async def test_review_rejects_empty_reference_but_accepts_explicit_user_candidate(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        bad_conversation = self.conversation("Rejected empty review source")
        bad = await self._rejected_pair(bad_conversation,
            "Assess the concrete candidate ${core_documents/global_outline.md}.",
            scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("reference_1",)),
            selection_group='consultation.authoring.review')
        self.assertIsNone(bad.proposal)
        conversation = bad_conversation
        original, before = "완성 원고.\r\nCafé.  \r\n", self.artifact_bytes()
        good = await self.proposal_for(conversation, self._native_scoped(
                PLOT_ARCHITECT, activity='review', intent='review_only',
                domains=('plot_structure',)), text=original, selection_group='consultation.review_only')
        self.assertIs(good.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(good.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))
        self.assertIs(good.proposal.authoring_scope.intent, AuthoringIntent.REVIEW_ONLY)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Suggested edits remain findings until a fresh authoring request."))
        reviewed = await self.runtime.decide_lead_step(_decision(good, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual([item["text"].encode() for agent, call in self.backend.call_records if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "user_input"], [original.encode()])
        self.assertEqual(self.artifact_bytes(), before)
        self.assertTrue(all(request is None for request in (reviewed.turn.artifact_application_request, reviewed.turn.story_commit_request)))
        self.assertFalse(any(type(e.payload) in (c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2) for e in self.history(conversation)))

        self.backend.enqueue(self._native_scoped(PLOT_ARCHITECT, paths=(OUTLINE,), domains=('plot_structure',), authoring_assessment=_fixture_assessment()), control_plan=selection_plan('consultation.authoring.content'))
        drafted = await self.runtime.lead_turn(self.session_id, conversation, _ADMISSION_BRIEF)
        self.assertIsNotNone(drafted.proposal, (drafted.message, drafted.validation_details, drafted.public_responses))
        self.assertEqual((drafted.proposal.authoring_scope.intent, drafted.proposal.authoring_scope.task_revision, self.artifact_bytes()), (AuthoringIntent.AUTHORING, 1, before))
        self.assertNotEqual(drafted.proposal.authoring_scope.task_id, good.proposal.authoring_scope.task_id)
        source, owner = self.conversation("Review source A"), self.conversation("Selected review owner B")
        supplied = "Unused prefix.\n" + original + "\nUnused suffix."
        self.backend.enqueue(_current_exploration_outcome(supplied))
        await self.runtime.lead_turn(self.session_id, source, "Explore supplied review alternatives.",
                                     interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        event = next(e for e in self.history(source) if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        start = len("Unused prefix.\n")
        selection = replace(_adoption_selection(source, event, (OUTLINE,)), start_character=start, end_character=start + len(original), intent=AuthoringIntent.REVIEW_ONLY)
        selected = await self.proposal_for(owner, self._native_scoped(
            PLOT_ARCHITECT, domains=("plot_structure",), activity="review",
            intent="review_only", candidate_ids=("adopted_idea",),
            mutate=lambda value, _material: value["proposal"]["assignments"][0].update(
                input_ids=["adopted_idea"])), adoption=selection, selection_group='consultation.review_only')
        self.assertIsNotNone(selected.proposal, selected)
        self.assertEqual(selected.proposal.assignments[0].review_basis.candidate_input_ids, ("adopted_idea",))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="Selected supplied review findings."))
        result = await self.runtime.decide_lead_step(_decision(selected, c.CollaborationConfirmationDecision.CONFIRM))
        delivered = [item["conversation_event"]["capture"]["selected_text"]
                     for agent, call in self.backend.call_records
                     if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"]
                     if item["input_id"] == "adopted_idea"]
        self.assertEqual(delivered, [original])
        self.assertEqual((result.turn.message, result.turn.artifact_application_request,
                          result.turn.story_commit_request), ("Selected supplied review findings.", None, None))
        retained = self.history(owner)
        projection = c.project_collaboration_history(retained)
        self.assertIsNotNone(projection.execution_result)
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        self.assertEqual(restarted.conversation_storage_services.event_store.read(self.session_id, owner), retained)
        self.assertEqual(restarted.inspect_lead_collaboration(self.session_id, owner).execution_result,
                         projection.execution_result)
        self.assertEqual(self.artifact_bytes(), before)
        self.assertFalse(restarted.inspect_lead_collaboration(self.session_id, owner).artifact_objectives)

    async def test_wrong_task_pair_rejects_before_dispatch(self):
        conversation = self.conversation()
        proposed = await self.proposal_for(conversation)
        current = next(view.ref for view in self.runtime.inspect_lead_collaboration(
            self.session_id, conversation).available_tasks if view.ref.task_id == proposed.proposal.authoring_scope.task_id)
        stale = replace(current, task_revision=current.task_revision + 1)
        decision = task.LeadTaskDecision.from_task(stale, task.LeadTaskAction.RESUME, text=_ADMISSION_BRIEF)
        before, calls = self.history(conversation), len(self.backend.calls)
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_task(decision)
        self.assertEqual(self.history(conversation), before)
        self.assertEqual(len(self.backend.calls), calls)

    async def test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel(self):
        original_conversation = self.conversation()
        original = await self.proposal_for(original_conversation)
        prefix = self.history(original_conversation)
        selected = original.proposal.authoring_scope.task_id
        def relabel(value, material):
            value["authoring_scope"].update(activity="workflow", target_paths=[], domains=[])
        def forbidden_relabel(call, provider):
            native = {'family': 0, 'lookup_response_type': 'specialist_consultation_proposal',
                      'source_choices': dict(call.contract.semantic_index.policy.choices)['input_ids']}
            return scoped_proposal(PLOT_ARCHITECT, selected=selected, kind="answer", mutate=relabel,
                authoring_assessment=_fixture_assessment(), native_inputs=native)(call, provider)
        bad = await self._rejected_pair(self.conversation(), _ADMISSION_BRIEF, forbidden_relabel,
            selection_group='consultation.authoring.content')
        self._assert_continuation(bad, 'proposal')
        continued = await self.proposal_for(self.conversation(), self._native_scoped(
            PLOT_ARCHITECT, domains=("plot_structure",),
            authoring_assessment=_fixture_assessment(),
        ))
        self.assertIsNotNone(continued.proposal, continued)
        from story_writing_agents.authoring_task_lifecycle import task_identity_for_origin
        target_scope = continued.proposal.authoring_scope
        self.assertEqual((target_scope.task_id, target_scope.task_revision), (task_identity_for_origin(continued.step_id), 1))
        self.assertEqual(target_scope, replace(original.proposal.authoring_scope, task_id=target_scope.task_id, task_revision=target_scope.task_revision))
        self.assertEqual(self.history(original_conversation), prefix)
        self.backend.enqueue(_visible_outcome("answer", "The workflow relabel remains rejected."), control_plan=selection_plan('answer.workflow'))
        await self.runtime.lead_turn(self.session_id, bad.conversation_id, "Resolve the rejected interpretation.")
        source, foreign_focus = self.conversation("Current provisional source"), self.conversation("Foreign focus")
        self.backend.enqueue(_current_exploration_outcome())
        await self.runtime.lead_turn(self.session_id, source, "Explore alternatives.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        event = next(event for event in self.history(source) if event.kind is c.CollaborationEventKind.LEAD_EXPLORATION_RESULT)
        selection = _adoption_selection(source, event, (OUTLINE,))
        before = (self.history(source), self.history(foreign_focus), len(self.backend.calls))
        for focus, choice in (
            (foreign_focus, replace(selection, source_conversation_id="foreign-session-source")),
            (source, replace(selection, material_digest="0" * 64)),
            (source, replace(selection, result_event_id="stale-current-result")),):
            with self.assertRaises(ValueError):
                await self.runtime.lead_turn(self.session_id, focus, "Adopt the selected whole idea.", adoption=choice)
        self.assertEqual((self.history(source), self.history(foreign_focus), len(self.backend.calls)), before)

        adopted = await self.proposal_for(foreign_focus, self._native_scoped(PLOT_ARCHITECT,
            domains=("plot_structure",), candidate_ids=("adopted_idea",),
            authoring_assessment=_fixture_assessment(),), adoption=selection)
        self.assertIsNotNone(adopted.proposal, adopted)
        capture = next(e.payload.capture for e in self.history(foreign_focus)
                       if type(e.payload) is c.CollaborationExplorationAdoptionPayload)
        self.assertEqual((capture.selection.source_conversation_id, capture.fresh_task_id, capture.task_revision),
                         (source, adopted.proposal.authoring_scope.task_id, 1))
        self.assertNotEqual(capture.fresh_task_id, selected)
        self.assertEqual(self.history(source), before[0])

    async def test_draft_task_cannot_skip_current_revision_drafting_via_review_artifact_intent(self):
        conversation = self.conversation()
        pending = await self.proposal_for(conversation)
        task_id = pending.proposal.authoring_scope.task_id
        self.workspace.path(OUTLINE).unlink()
        def selected_review_scope(call):
            tasks = [_live_task(call, i) for i, _ in enumerate(call.contract.semantic_index.policy.task_revisions)]
            index = next(i for i,t in enumerate(tasks)
                if t["task_id"] == task_id)
            return _live_scope(call, activity="review", domains=("plot_structure",),
                paths=(OUTLINE,), task_index=index)
        response = _artifact_outcome(
            operations=[{"kind":"create", "suggested_path":OUTLINE,
                "new_text":"Current synthetic candidate."}], evidence_status="missing",
            evidence_ids=lambda call,m, *, input_payload:[next(x["evidence_id"]
                for x in input_payload["core_document_statuses"] if x["artifact_path"] == OUTLINE)],
            authoring_scope=selected_review_scope)
        def selected_review(call, provider):
            value = json.loads(response(call, provider))
            value["agent_action"]["task_result"]["response_details"]["authoring_assessment"] = None
            return json.dumps(value)
        result = await self._rejected_pair(self.conversation(), "Integrate that pending task into the supplied candidate.",
            selected_review, selection_group='artifact.changes')
        self._assert_continuation(result, 'artifact_proposal')

    async def test_provider_free_advisory_requires_scope_and_separate_confirmation(self):
        conversation = self.conversation()
        with self.assertRaisesRegex(ValueError, "explicit authoring scope"):
            await self.runtime.propose_specialist_advisory(self.session_id, conversation, PLOT_ARCHITECT, "Draft a synthetic outline.")
        scope = AuthoringScope(AuthoringActivity.DRAFT, (OUTLINE,), (), "Draft a synthetic outline.", "provided_task", 1)
        needed = await self.runtime.propose_specialist_advisory(
            self.session_id, conversation, PLOT_ARCHITECT, _ADMISSION_BRIEF, authoring_scope=scope,)
        self.assertIsInstance(needed, admission.AuthoringAdmissionNeeded)
        user = next(v for v in needed.offered_sources if v.source_id == "user_input")
        self.assertIs(user.kind, admission.AdmissionSourceKind.USER)
        self.assertEqual((user.original_authority, user.utf8_bytes), ("literal_user", len(_ADMISSION_BRIEF.encode())))
        assessment = _fixture_assessment()
        for structural_only in (replace(assessment, creative_scope=None), replace(assessment, allowed_creative_actions=())):
            self.assertEqual(admission.assessment_from_dict(admission.assessment_to_dict(structural_only)), structural_only)
        pins = dict( sources=(user,), request_digest=user.revision_digest, task_id=scope.task_id,
            task_revision=scope.task_revision,
            scope_digest=admission.admission_digest(c.authoring_scope_to_dict(scope)), )
        for structural_only in (replace(assessment, creative_scope=None), replace(assessment, allowed_creative_actions=())):
            self.assertIsInstance(admission.qualify_authoring_assessment(structural_only, **pins), admission.AuthoringAdmissionNeeded)
        for container, field, maximum in (('foundations', 'requirement', 256), ('decisions', 'decision', 256),
                ('direction', 'justification', 4096), ('decisions', 'explanation', 4096)):
            for text, valid in (('x' * maximum, True), ('x' * (maximum + 1), False),
                    ('한' * (maximum // 3), True), ('한' * (maximum // 3 + 1), False)):
                encoded_assessment = admission.assessment_to_dict(assessment); row = encoded_assessment[container]
                row = row[0] if isinstance(row, list) else row; row[field] = text
                if valid: self.assertIsInstance(admission.assessment_from_dict(encoded_assessment), admission.AuthoringAssessment)
                else:
                    with self.assertRaises(ValueError): admission.assessment_from_dict(encoded_assessment)
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, foundations=()), **pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, creative_scope=None), **pins),
            admission.AuthoringAdmissionNeeded, )
        missing = admission.AdmissionDecision("Ending", admission.DecisionCoverage.MISSING, (), "Choose the ending.")
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, decisions=(missing,)), **pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=0),)},
            ), admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=None),)},
            ), admission.AuthoringAdmissionNeeded, )
        with self.assertRaisesRegex(ValueError, "unqualified source"):
            admission.qualify_authoring_assessment(_fixture_assessment(foundation_ids=("unoffered",)), **pins)
        material = admission.AdmissionSource( "input_material", admission.AdmissionSourceKind.MATERIAL,
            "fixture:nonempty-material", user.revision_digest, "input_only", len(_ADMISSION_BRIEF.encode()), )
        material_assessment = _fixture_assessment(foundation_ids=(material.source_id,))
        material_pins = {**pins, "sources": (material, user)}
        self.assertIsInstance( admission.qualify_authoring_assessment(material_assessment, **material_pins),
            admission.AuthoringAdmissionBasis, )
        empty_user = admission.AdmissionSource(
            "empty_user", admission.AdmissionSourceKind.USER, "fixture:empty-user",
            "sha256:" + hashlib.sha256(b"").hexdigest(), "literal_user", 0, )
        unknown_user = replace(empty_user, utf8_bytes=None)
        empty_direction = replace( material_assessment, direction=replace(assessment.direction, source_ids=(empty_user.source_id,)), )
        empty_delegation = replace( material_assessment, decisions=(replace(assessment.decisions[0], source_ids=(empty_user.source_id,)),), )
        empty_pins = {**pins, "sources": (material, user, empty_user)}
        unknown_pins = {**pins, "sources": (material, user, unknown_user)}
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_direction, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_direction, **unknown_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_delegation, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_delegation, **unknown_pins),
            admission.AuthoringAdmissionNeeded, )
        result = await self.runtime.propose_specialist_advisory(
            self.session_id, conversation, PLOT_ARCHITECT, _ADMISSION_BRIEF,
            authoring_scope=scope, authoring_assessment=assessment, )
        self.assertEqual(self.backend.calls, [])
        self.assertIs(result.state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(result.proposal.assignments[0].mode, SpecialistAssignmentMode.DRAFT)
        basis = result.proposal.basis.authoring_admission
        source = next(v for v in result.proposal.basis.inputs if v.input_id == "user_input")
        expected_user = replace(user, source_identity=source.locator, revision_digest=source.revision_digest)
        self.assertEqual((basis.request_digest, basis.sources), (source.revision_digest, (expected_user,)))
        encoded = admission.admission_basis_to_dict(basis)
        self.assertEqual(admission.admission_basis_from_dict(encoded), basis)
        with self.assertRaises(ValueError):
            admission.admission_basis_from_dict({**encoded, "caller_authorized": True})
        with self.assertRaises(ValueError):
            admission.admission_basis_from_dict({k: v for k, v in encoded.items() if k != "sources"})

    def artifact_bytes(self): return tuple( (item.path, item.content)
            for item in WritingArtifactRepository(self.workspace.root).capture_complete_state() )

    async def test_draft_integrates_exact_all_role_review_apply_and_storage_parity(self):
        subprocess.run(['git', 'add', '--', 'core_documents', 'manuscript'], cwd=self.workspace.root, check=True)
        subprocess.run(['git', 'commit', '-m', 'Seed lead commit test fixtures'], cwd=self.workspace.root, check=True, stdout=subprocess.DEVNULL)
        from tests.test_lead_artifact_application import _application_decision
        from tests.test_lead_story_commit import _commit_decision, StoryCommitDecision
        from tests.test_artifact_proposal_orchestration import (
            WorkspaceConversationStorageMutationCoordinator, provision_sqlite_conversation_storage,
            transfer_conversation_storage, verify_conversation_storage,
            ConversationStorageBackend, ConversationStorageTransferOutcome,
            compose_sqlite_conversation_storage, )
        path = "manuscript/part-01/integrated.md"
        extra = await self._rejected_pair(self.conversation("Review-only extra null"),
            "Concrete supplied candidate, with no authoring requested.",
            self._native_scoped(PLOT_ARCHITECT, activity="review", intent="review_only",
                mutate=lambda value, material: value.update(authoring_assessment=None)),
            selection_group='consultation.review_only')
        self.assertIsNone(extra.proposal)
        self.assertTrue(any(item.response.acceptance is task.LeadPublicResponseAcceptance.REJECTED
                            for item in extra.public_responses))
        self.assertTrue(all(agent == LEAD_ORCHESTRATOR for agent, *_ in self.backend.call_records))
        self.assertEqual((extra.artifact_proposal, extra.artifact_application_request,
                          extra.story_commit_request, extra.recommendation_request,
                          extra.confirmation_request_id), (None,) * 5)
        failed_calls = len(_submitted_calls(self.history(extra.conversation_id)))
        self.backend.enqueue(_visible_outcome(
            "answer", "No authoring work is pending from that rejected request.",), control_plan=selection_plan('answer.workflow'))
        cleared = await self.runtime.lead_turn(
            self.session_id, extra.conversation_id, "Report status of this rejected request.", )
        self.assertIs(cleared.kind, c.LeadOutcomeKind.ANSWER)
        self.assertEqual(len(_submitted_calls(self.history(extra.conversation_id))), failed_calls + 2)
        self.assertNotEqual(c.project_collaboration_history(self.history(extra.conversation_id)).next_safe_action,
                            c.CollaborationNextAction.RETRY_LEAD_TURN)
        await self.record_book_readiness(seed_before_capture=True)
        conversation = self.conversation()
        before = self.artifact_bytes()
        premise = "Synthetic tidal city 한글\r\nKeep the stated limit.  "
        source_conversation = self.conversation("Provisional source A")
        source_text = "Discarded prefix.\n" + premise + "\nUnselected ending."
        self.backend.enqueue(_current_exploration_outcome(source_text))
        explored = await self.runtime.lead_turn(self.session_id, source_conversation, "Explore a provisional setting.",
                                                interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        self.assertIs(explored.kind, c.LeadOutcomeKind.EXPLORATION)
        self.assertEqual(tuple(getattr(explored, key) for key in ("proposal", "artifact_proposal", "artifact_application_request", "story_commit_request", "recommendation_request", "confirmation_request_id")), (None,) * 6)
        self.assertEqual(self.artifact_bytes(), before)
        exploration_prefix = self.history(source_conversation)
        self.assertEqual(c.project_collaboration_history(exploration_prefix).artifact_objectives, ())
        result_event = next(e for e in exploration_prefix if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        start = len("Discarded prefix.\n")
        selection = replace(_adoption_selection(source_conversation, result_event, (path,)), start_character=start, end_character=start + len(premise))
        self.assertEqual(tuple((call.call_id, call.invocation_kind) for call in _submitted_calls(exploration_prefix)), ((explored.exploration.provider_call_id, c.CollaborationInvocationKind.LEAD_EXPLORATION),))
        def mixed(value, material):
            scope = value["authoring_scope"]
            scope["contributions"] = [{"domain": domain, "role": "draft_owner" if domain == "tone_execution" else "constraint_assessor"} for domain in ("plot_structure", "character_world", "tone_execution")]
            scope["dependency_slots"] = [{"slot_id": "setting", "dependent_domains": ["tone_execution"]}]
            value["proposal"]["shared_premises"] = [{"slot_id": "setting", "selected_premise": premise, "basis_input_ids": ["adopted_idea"]}]
            for assignment in value["proposal"]["assignments"]:
                assignment["input_ids"].append("adopted_idea")
                assignment["mode"] = "draft" if assignment["agent_id"] == EXECUTION_SPECIALIST else "constraint_assessment"
        def recommend(call, provider):
            material = _workflow_material(provider.callback_payload(call))
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, recommended_action=action(
                    target_paths=(path,), source_ids=("user_input", "adopted_idea", draft_reply))
            )(call, provider)
        (drafted, draft_result,) = await self.finish_draft(
            conversation,
            text=_ADOPTION_DIRECTION,
            adoption=selection,
            response=self._native_scoped(
                *ROSTER,
                paths=(path,),
                domains=("plot_structure", "character_world", "tone_execution"),
                mutate=mixed,
                authoring_assessment=_fixture_assessment(foundation_ids=(
                    "adopted_idea",)),
            ),
            synthesis=recommend,)
        self.assertEqual(tuple(assignment.mode for assignment in drafted.proposal.assignments), (SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT, SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT, SpecialistAssignmentMode.DRAFT))
        capture = drafted.proposal.basis.shared_premises
        adoption_event = next(e for e in self.history(conversation) if type(e.payload) is c.CollaborationExplorationAdoptionPayload)
        self.assertEqual(adoption_event.payload.capture.selection, selection)
        self.assertEqual(adoption_event.payload.capture.source_event_digest,
                         hashlib.sha256(c.encode_collaboration_event(result_event)).hexdigest())
        self.assertEqual(adoption_event.payload.capture.selected_material_digest, exploration.material_sha256(premise))
        self.assertNotEqual(selection.material_digest, adoption_event.payload.capture.selected_material_digest)
        self.assertEqual(adoption_event.payload.capture.selected_text.encode("utf-8"), premise.encode("utf-8"))
        self.assertEqual((drafted.proposal.authoring_scope.task_id, drafted.proposal.authoring_scope.task_revision), (adoption_event.payload.capture.fresh_task_id, 1))
        self.assertIs(drafted.interaction_mode, exploration.LeadInteractionMode.ORCHESTRATION)
        self.assertEqual(capture[0].selected_premise, premise)
        self.assertEqual(capture[0].sources[0].source_conversation_id, conversation)
        self.assertEqual(draft_result.turn.recommendation_request.source_proposal_digest, drafted.proposal.proposal_digest)
        source = capture[0].sources[0].input_revision
        disclosures = (premise, "draft_owner", "constraint_assessor", "setting", "plot_structure", "character_world", "tone_execution",
            source.input_id, source.kind.value, source.locator, "whole input", source.state.value, source.revision_digest, conversation)
        panels = (view.project_lead_turn_result(drafted), view.project_lead_turn_result(draft_result.turn),
            view.project_collaboration_event(next(event for event in self.history(conversation)
                if event.step_id == drafted.step_id and getattr(event.payload, "state", None) is c.CollaborationStepState.AWAITING_CONFIRMATION),
                proposals={drafted.proposal.proposal_id: drafted.proposal}))
        self.assertTrue(all(json.dumps(value, ensure_ascii=False)[1:-1] in json.dumps(asdict(panel), ensure_ascii=False)
            for panel in panels for value in disclosures))
        task_id = drafted.proposal.authoring_scope.task_id
        draft_prefix = self.history(conversation)
        response = _artifact_outcome(
            operations=[{"kind":"create", "suggested_path":path, "new_text":"Summons leads to a choice; the choice causes a consequence.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call,m, *, input_payload:[input_payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent=dict(action="start", target_paths={path: True}, next_step="prepare"),
            authoring_scope=lambda _: c.authoring_scope_to_dict(drafted.proposal.authoring_scope),
            authoring_assessment=asdict(_fixture_assessment(foundation_ids=("adopted_idea",))),)
        def integrate(call, provider):
            return response(call, provider)
        self.backend.enqueue(integrate)
        proposed = await self.runtime.decide_lead_recommendation(recommendation_decision(draft_result.turn))
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, msg=proposed.validation_details)
        self.assertEqual(proposed.proposal.participants, ROSTER)
        self.assertEqual(proposed.proposal.basis.shared_premises, capture)
        self.assertEqual(proposed.proposal.budget.rounds_spent, 1)
        candidate_inputs = [x for x in proposed.proposal.basis.inputs if x.kind is c.CollaborationInputKind.ARTIFACT_CANDIDATE]
        self.assertEqual(len(candidate_inputs),1)
        for assignment in proposed.proposal.assignments:
            self.assertIs(assignment.mode, SpecialistAssignmentMode.REVIEW)
            self.assertEqual(assignment.review_basis.candidate_input_ids, (candidate_inputs[0].input_id,))
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The exact integrated candidate preserves the assigned domain."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(proposed,c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(len(_submitted_calls(self.history(conversation))), 11,
                         [(actor, call.call_kind.value) for actor, call in self.backend.call_records])
        review_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records if actor in ROSTER][-3:]
        review_candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["input_id"] == candidate_inputs[0].input_id) for frame in review_frames]
        self.assertEqual(review_candidates, [review_candidates[0]] * len(ROSTER))
        self.assertEqual([frame["shared_premises"] for frame in review_frames], [shared_premises_to_dict(capture)] * len(ROSTER))
        self.assertEqual([frame["assignment"]["review_basis"]["candidate_input_ids"] for frame in review_frames], [[candidate_inputs[0].input_id]] * len(ROSTER))
        adopted_inputs = [item for actor, call in self.backend.call_records if actor in ROSTER for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "adopted_idea"]
        self.assertEqual([item["conversation_event"]["capture"]["selected_text"].encode("utf-8") for item in adopted_inputs], [premise.encode("utf-8")] * (2 * len(ROSTER)))
        self.assertEqual(self.artifact_bytes(),before)
        self.assertIsNotNone(reviewed.turn.artifact_application_request)
        self.assertEqual(self.history(conversation)[:len(draft_prefix)],draft_prefix)
        final_proposal = reviewed.turn.artifact_proposal.reviewed_proposal
        from tests.test_agent_exchange_viewer import _assert_typed_history_phase
        self.assertIsNone(proposed.proposal.stage_plan)
        phase_history = self.history(conversation)
        phase_synthesis = next(event.payload for event in phase_history
                               if event.event_id == final_proposal.synthesis_event_id)
        phase_companions = [event.payload for event in phase_history
                            if type(event.payload) in {c.CollaborationLeadSynthesisPayload, c.ArtifactReviewSynthesisPayload}
                            and event.payload.provider_invocation_id == phase_synthesis.provider_invocation_id]
        self.assertEqual(len(phase_companions), 2)
        self.assertEqual({type(item) for item in phase_companions},
                         {c.CollaborationLeadSynthesisPayload, c.ArtifactReviewSynthesisPayload})
        _assert_typed_history_phase(self, phase_history, phase_synthesis.provider_invocation_id,
                                   LEAD_ORCHESTRATOR, "final_candidate_review", proposed.proposal.authoring_scope)
        self.assertEqual(final_proposal.final_review_evidence.reviewer_ids, ROSTER)
        self.assertEqual(final_proposal.final_review_evidence.reply_event_ids,
                         tuple(item.reply_event_id for item in final_proposal.reviews))
        qualified = self.runtime.qualify_artifact_final_review(session_id=self.session_id,
            reviewed_proposal=final_proposal, consultation=proposed.proposal, events=self.history(conversation))
        self.assertTrue(qualified.eligible)
        self.assertEqual(qualified.evidence_digest, final_proposal.final_review_evidence.evidence_digest)
        applied = await self.runtime.decide_artifact_application(_application_decision(reviewed.turn,c.ArtifactApplicationDecisionKind.APPLY))
        self.assertTrue(self.workspace.path(path).read_text().startswith("Summons leads"))
        self.assertIsNotNone(applied.commit_request)
        await self.runtime.decide_story_commit(_commit_decision(applied, StoryCommitDecision.REJECT))
        completed = self.runtime.inspect_lead_collaboration(self.session_id, conversation)
        self.assertEqual((completed.rounds_spent, completed.physical_calls_spent), (2, 11))
        submitted = _submitted_calls(self.history(conversation))
        self.assertEqual(len({(item.invocation_id, item.call_id) for item in submitted}), 11)
        producer_notes = self._observe_failure_producers()
        for failure, code in ((ConnectionError("Known adoption failure"), c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED), (asyncio.CancelledError(), c.CollaborationLeadOutcomeFailureCode.CANCELLED)):
            self.backend.enqueue(control_plan=((0, LeadGroupPhase.SELECT, _SubmittedFailure(failure)),))
            if code is c.CollaborationLeadOutcomeFailureCode.CANCELLED:
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.lead_turn(self.session_id, conversation, "Draft adopted idea.", adoption=selection)
            else:
                failed = await self.runtime.lead_turn(self.session_id, conversation, "Draft adopted idea.", adoption=selection)
                self.assertIs(failed.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
            failed_history = self.history(conversation)
            submitted = _submitted_calls(failed_history)
            closure = next(e.payload for e in reversed(failed_history) if type(e.payload) is c.CollaborationLeadOutcomeFailurePayload)
            self.assertEqual((closure.code, closure.call_id, closure.invocation_id), (code, submitted[-1].call_id, submitted[-1].invocation_id))
            self.assertIs(c.project_collaboration_history(failed_history).next_safe_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
            self.backend.enqueue(_visible_outcome("answer", "Known failure safely closed."), control_plan=selection_plan('answer.workflow'))
            resumed = await self.runtime.lead_turn(self.session_id, conversation, "Report status.")
            self.assertIs(resumed.kind, c.LeadOutcomeKind.ANSWER, (resumed.message[:500], resumed.state, resumed.next_action, len(self.backend.calls), tuple(producer_notes)))
            self.assertEqual(resumed.message, "Known failure safely closed.")
            self.assertEqual(self.history(conversation)[:len(failed_history)], failed_history)
            self.assertEqual(len(_submitted_calls(self.history(conversation))), len(submitted) + 2)
            self.assertEqual((resumed.proposal, resumed.artifact_proposal, resumed.artifact_application_request, resumed.story_commit_request), (None,) * 4)
        history = self.history(conversation)
        self.assertTrue(any(type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED for e in history))
        self.assertEqual(applied.commit_request.application_result_event_id, next(e.event_id for e in history if type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED))
        offered = []
        def status(call,provider):
            offered.extend(_live_task(call, i) for i, _ in enumerate(call.contract.semantic_index.policy.task_revisions))
            self.assertNotIn("authoring_tasks", _workflow_material(provider.callback_payload(call)))
            return _visible_outcome("answer","The previous artifact application is recorded.")(call,provider)
        self.backend.enqueue(status, control_plan=selection_plan('answer.workflow'))
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report the recorded application status.")
        self.assertNotIn(task_id,{item["task_id"] for item in offered})
        (
            self.backend.enqueue(_current_exploration_outcome(
            'Changed condition: retain the revised tide boundary.')))
        await self.runtime.lead_turn(self.session_id, source_conversation, "Explore the changed condition.",
                                     interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        owner_history, calls = self.history(conversation), len(self.backend.calls)
        self.assertEqual(restarted.inspect_lead_collaboration(self.session_id, conversation),
                         self.runtime.inspect_lead_collaboration(self.session_id, conversation))
        with self.assertRaises(ValueError):
            await restarted.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        new_owner = self.conversation("Changed-condition fresh task")
        with self.assertRaisesRegex(ValueError, "latest"):
            await restarted.lead_turn(self.session_id, new_owner, "Do not reuse the old choice.", adoption=selection)
        self.assertEqual((self.history(conversation), len(self.backend.calls)), (owner_history, calls))
        fresh_path = "core_documents/narrative_rules.md"
        rule_path = "core_documents/narrative_rules.md"
        rule_locator = "${core_documents/narrative_rules.md}"
        rule_bytes = self.workspace.path(rule_path).read_bytes()
        rule_digest = "sha256:" + hashlib.sha256(rule_bytes).hexdigest()
        late_roster = (EXECUTION_SPECIALIST,)
        style_request = (
            "Review the supplied current narrative rules as a STYLE-only candidate. "
            "Do not invent or change plot, character, world facts, or any existing rule. "
            "You may make bounded style-review judgments and prepare a separate exact "
            "replacement containing the same text plus one trailing blank line. "
            "Execution requires its own exact confirmation.\n" + rule_locator)
        style_assessment = task_assessment(foundation_ids=("reference_1",),
            actions=("bounded style-review judgments", "separate exact formatting-only candidate preparation"))
        condition_before = self.artifact_bytes()
        late_synthesis_checks = []
        def changed_recommend(call, provider):
            material = _workflow_material(provider.callback_payload(call))
            source_id = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "review")
            raw = _synthesis_outcome(
                recommendation_family=2, recommended_action=action(target_paths=(fresh_path,),
                       source_ids=("user_input", "reference_1", source_id)))(call, provider)
            late_synthesis_checks.append(call.contract.structural_findings(raw))
            return raw
        late_calls = len(self.backend.calls)
        try:
            (fresh,
                fresh_review,
            ) = await self.finish_draft(
                new_owner, text=style_request, response=self._native_scoped(
                EXECUTION_SPECIALIST,
                activity="review", paths=(fresh_path,),
                domains=("tone_execution",),
                candidate_ids=("reference_1",),
                authoring_assessment=style_assessment,
            ), selection_group="consultation.authoring.review", synthesis=changed_recommend)
        finally:
            self.assertEqual(late_synthesis_checks, [()])
        self.assertIsNotNone(fresh.proposal, fresh)
        self.assertNotEqual(fresh.proposal.authoring_scope.task_id, task_id)
        self.assertEqual((fresh.proposal.authoring_scope.task_revision, fresh.proposal.budget.rounds_spent,
                          fresh.proposal.budget.physical_calls_spent), (1, 0, 2))
        changed_response = _artifact_outcome(operations=[{"kind": "replace", "evidence_id": "reference_1",
                "new_text": rule_bytes.decode("utf-8") + "\n"}],
                           evidence_status="incomplete", evidence_ids=["reference_1"],
            objective_intent={"action": "start", "target_paths": {fresh_path: True},
                "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(fresh.proposal.authoring_scope),
            authoring_assessment=asdict(style_assessment),)
        def changed_integrate(call, provider):
            return changed_response(call, provider)
        self.backend.enqueue(changed_integrate)
        prepared = await self.runtime.decide_lead_recommendation(recommendation_decision(fresh_review.turn))
        self.assertIsNotNone(prepared.proposal, prepared)
        self.assertEqual(prepared.proposal.participants, late_roster)
        self.assertTrue(all("reference_1" in assignment.input_ids for assignment in fresh.proposal.assignments))
        rule_input = next(item for item in prepared.proposal.basis.inputs if item.input_id == "reference_1")
        rule_receipt = next(item for item in prepared.proposal.basis.evidence.receipts
                            if item.evidence_id == "reference_1")
        self.assertIs(rule_receipt.acquisition, c.CollaborationEvidenceAcquisition.ATTACHED_REFERENCE)
        self.assertEqual((rule_input.locator, rule_receipt.locator), (rule_locator, rule_locator))
        self.assertEqual(rule_receipt.revision_digest, rule_input.revision_digest)
        self.assertEqual((rule_receipt.content_digest, rule_receipt.utf8_bytes, rule_receipt.boundary_kind),
                         (rule_digest, len(rule_bytes), "whole"))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        current_review = await self.runtime.decide_lead_step(_decision(prepared,
                             c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(current_review.turn.artifact_proposal.state, c.ArtifactProposalState.REVIEWED)
        current_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records
                          if actor in ROSTER][-len(late_roster):]
        current_candidates = [next(item["artifact_candidate"] for item in frame["inputs"]
                              if "artifact_candidate" in item) for frame in current_frames]
        self.assertEqual(current_candidates, [current_candidates[0]] * len(late_roster))
        self.assertNotEqual(current_candidates[0], review_candidates[0])
        await self.runtime.decide_artifact_application(_application_decision(current_review.turn,
                                                       c.ArtifactApplicationDecisionKind.REJECT))
        live_old = self.runtime.inspect_lead_collaboration(self.session_id, conversation)
        live_new = self.runtime.inspect_lead_collaboration(self.session_id, new_owner)
        old_task = next(view for view in live_old.available_tasks if view.ref.task_id == task_id)
        new_task = next(view for view in live_new.available_tasks
                        if view.ref.task_id == fresh.proposal.authoring_scope.task_id)
        old_history, new_history = self.history(conversation), self.history(new_owner)
        old_steps = {event.step_id for event in old_history
                     if type(event.payload) is c.CollaborationProposal and event.payload.authoring_scope is not None
                     and event.payload.authoring_scope.task_id == old_task.ref.task_id}
        new_steps = {event.step_id for event in new_history
                     if type(event.payload) is c.CollaborationProposal and event.payload.authoring_scope is not None
                     and event.payload.authoring_scope.task_id == new_task.ref.task_id}
        old_rounds = sum(type(event.payload) is c.CollaborationStepStatePayload
                         and event.payload.state is c.CollaborationStepState.RUNNING
                         and event.step_id in old_steps for event in old_history)
        new_rounds = sum(type(event.payload) is c.CollaborationStepStatePayload
                         and event.payload.state is c.CollaborationStepState.RUNNING
                         and event.step_id in new_steps for event in new_history)
        self.assertEqual((old_task.ref.task_id, old_task.ref.task_revision, old_task.status, old_rounds,
                          len(old_task.submitted_call_ids)), (task_id, 1, "completed", 2, 11))
        self.assertEqual((new_task.ref.task_id, new_task.ref.task_revision, new_task.status, new_rounds,
                          len(new_task.submitted_call_ids)),
                         (fresh.proposal.authoring_scope.task_id, 1, "active", 2, 7))
        self.assertEqual(self.artifact_bytes(), condition_before)
        coordinator=WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            provision_sqlite_conversation_storage(self.control_workspace,lease=lease)
        transfer=transfer_conversation_storage(self.control_workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transfer.outcome,ConversationStorageTransferOutcome.COMPLETED)
        verified=verify_conversation_storage(self.control_workspace,source_backend=ConversationStorageBackend.FILESYSTEM,destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(verified.outcome,ConversationStorageTransferOutcome.COMPLETED)
        persisted=compose_sqlite_conversation_storage(self.control_workspace).event_store.read(self.session_id,conversation)
        self.assertEqual(persisted,history)
        self.assertEqual(compose_sqlite_conversation_storage(self.control_workspace).event_store.read(
                         self.session_id, source_conversation), self.history(source_conversation))
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in persisted),tuple(c.encode_collaboration_event(e) for e in history))
        current_final = current_review.turn.artifact_proposal.reviewed_proposal
        self.assertEqual(current_final.final_review_evidence.reviewer_ids, late_roster)
        self.assertEqual(len(self.backend.calls) - late_calls, 7)
        self.assertIn(rule_locator, {source.target_id for source in current_final.final_review_evidence.sources})
        rule_source = next(source for source in current_final.final_review_evidence.sources
                           if source.input_id == rule_input.input_id)
        self.assertEqual((rule_source.target_id, rule_source.revision, rule_source.source_digest,
                          rule_source.boundary_digest, rule_source.whole_source),
                         (rule_locator, rule_input.revision_digest, rule_receipt.revision_digest,
                          rule_receipt.content_digest, True))
        current_history = self.history(new_owner)
        qualified_current = self.runtime.qualify_artifact_final_review(session_id=self.session_id,
            reviewed_proposal=current_final, consultation=prepared.proposal, events=current_history)
        self.assertTrue(qualified_current.eligible)
        calls_before_drift = len(self.backend.calls)
        rule_file = self.workspace.path(rule_path)
        rule_file.write_bytes(rule_file.read_bytes() + b"\nChanged criterion: use a different narrative distance.\n")
        stale = self.runtime.qualify_artifact_final_review(session_id=self.session_id,
            reviewed_proposal=current_final, consultation=prepared.proposal, events=current_history)
        self.assertFalse(stale.eligible)
        self.assertTrue(set(stale.reasons) & {"source_changed", "current_read_inapplicable"})
        self.assertEqual(len(self.backend.calls), calls_before_drift)
        self.assertEqual(self.history(new_owner), current_history)

    async def test_archived_task_is_context_without_routable_authority(self):
        archived = self.conversation()
        proposal = await self.proposal_for(archived)
        before = self.history(archived)
        self.runtime.conversation_storage_services.catalog.archive(self.session_id, archived)
        offered=[]
        def answer(call,provider):
            offered.extend(_live_task(call, i) for i, _ in enumerate(call.contract.semantic_index.policy.task_revisions))
            self.assertNotIn("authoring_tasks", _workflow_material(provider.callback_payload(call)))
            return _visible_outcome("answer", "The archived conversation remains readable history.")(call,provider)
        self.backend.enqueue(answer, control_plan=selection_plan('answer.workflow'))
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report active task status.")
        self.assertNotIn(proposal.proposal.authoring_scope.task_id,{item["task_id"] for item in offered})
        self.assertEqual(self.history(archived),before)

    async def test_old_drafted_revision_cannot_authorize_revised_candidate(self):
        from story_writing_agents import ReplyTraceOrigin
        readiness = await self.record_book_readiness()
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        services = self.runtime.conversation_storage_services
        readiness_snapshot = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture()
        expected_readiness_refs = tuple(readiness_snapshot.readiness_ref(row) for row in readiness_snapshot.readiness_heads())
        conversation=self.conversation()
        assessment = asdict(_fixture_assessment())
        from tests.test_lead_artifact_application import _application_decision
        before = self.artifact_bytes()
        path = "manuscript/part-01/revised-candidate.md"
        def two_owners(value, material):
            next(item for item in value["authoring_scope"]["contributions"] if item["domain"] == "tone_execution")["role"] = "constraint_assessor"
            next(item for item in value["proposal"]["assignments"] if item["agent_id"] == EXECUTION_SPECIALIST)["mode"] = "constraint_assessment"
        def selected_profile(call, pair):
            self.assertEqual(call.contract.factory.rule_seed.contract_id, "lead-response-v9")
            self.assertFalse(call.contract.factory.schema_group_protocol)
            self.assertIsNone(call.contract.schema_group_phase)
            self.assertEqual(call.contract.semantic_index.policy.task_revisions, (pair,))
        def transition_receipt(decision):
            event = next(event for event in self.history(decision.conversation_id)
                if type(event.payload) is c.CollaborationLeadTaskTransitionPayload
                and event.payload.receipt.origin_event_id == decision.request_id)
            self.assertEqual(event.payload.receipt.origin_kind, "task_decision")
            self.assertEqual(event.payload.receipt.action, decision.action)
            self.assertEqual(event.payload.receipt.source_task_ref, decision.task_ref)
            return event
        def initial_recommendation(call, provider):
            self.assertIn("current_response", dict(call.contract.semantic_index.policy.choices)["source_ids"])
            return _synthesis_outcome(message="The draft material is available for integration; no artifact changed.",
                recommendation_family=1, recommended_action=action(source_ids=("current_response",),
                    target_paths=(path,), specialist_ids=ROSTER))(call, provider)
        original, drafted = await self.finish_draft(conversation, response=self._native_scoped(
            *ROSTER, paths=(path,), domains=("plot_structure", "character_world", "tone_execution"),
            mutate=two_owners, authoring_assessment=_fixture_assessment(),
        ), synthesis=initial_recommendation)
        actual_basis = original.proposal.basis.core_document_readiness
        self.assertIsNotNone(actual_basis)
        self.assertEqual(actual_basis, replace(readiness.basis, prior_assessment_refs=expected_readiness_refs,
            history_coverage=actual_basis.history_coverage, history_metadata=actual_basis.history_metadata))
        old_source = c.project_collaboration_history(self.history(conversation)).offered_draft_sources[-1].event_id
        task_id=original.proposal.authoring_scope.task_id
        ready = drafted.turn
        self.assertIsNotNone(ready.recommendation_request, ready)
        self.assertEqual(tuple(source.event_id for source in ready.recommendation_request.draft_sources), (old_source,))
        self.assertEqual(ready.recommendation_request.source_recommendation_event_id, old_source)
        self.assertEqual(ready.recommendation_request.draft_sources[0].content_digest, "sha256:" + hashlib.sha256("The draft material is available for integration; no artifact changed.".encode("utf-8")).hexdigest())
        response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": path, "new_text": "Synthetic chronology remains explicit.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call, material, *, input_payload: [input_payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent={"action": "start", "target_paths": {path: True}, "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(original.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def integrate(call, provider):
            return response(call, provider)
        self.backend.enqueue(integrate)
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(ready))
        self.assertIsNotNone(review.proposal, review)
        self.assertEqual(review.proposal.participants, ROSTER)
        for assignment in review.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The candidate preserves the reviewed chronology and motivation."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        reviewed = await self.runtime.decide_lead_step(_decision(review, c.CollaborationConfirmationDecision.CONFIRM))
        review_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records if actor in ROSTER][-len(ROSTER):]
        review_candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["input_id"] == frame["assignment"]["review_basis"]["candidate_input_ids"][0]) for frame in review_frames]
        self.assertEqual(review_candidates, [review_candidates[0]] * len(ROSTER))
        await self.runtime.decide_artifact_application(_application_decision(reviewed.turn, c.ArtifactApplicationDecisionKind.REJECT))
        self.assertEqual(self.artifact_bytes(), before)
        completed_prefix = self.history(conversation)
        completed = c.project_collaboration_history(completed_prefix)
        self.assertIs(completed.current_artifact_application_result.outcome, c.ArtifactApplicationOutcomeKind.REJECTED)
        self.assertEqual(completed.rounds_spent, 2)
        self.assertLess(12 - completed.physical_calls_spent, len(review.proposal.participants) + 2)
        self.assertEqual(completed.physical_calls_spent, 11)
        exhausted_task = next(item for item in self.runtime.inspect_lead_collaboration(
            self.session_id, conversation).available_tasks
            if (item.ref.task_id, item.ref.task_revision) == (task_id, 1))
        exhausted_resume = task.LeadTaskDecision.from_task(exhausted_task.ref, task.LeadTaskAction.RESUME, text=_ADMISSION_BRIEF)
        before_refusal = len(self.backend.calls)
        refused = (await self.runtime.decide_lead_task(exhausted_resume)).turn
        self.assertEqual(len(self.backend.calls) - before_refusal, 0, refused)
        self.assertIsNone(refused.proposal, refused)
        self.assertIsNone(refused.recommendation_request, refused)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(completed_prefix):]))
        positive_source = next(item for item in self.runtime.inspect_lead_collaboration(
            self.session_id, conversation).available_tasks if item.ref.task_id == task_id)
        self.assertEqual(len(positive_source.submitted_call_ids), 11)
        positive_task = task.LeadTaskDecision.from_task(positive_source.ref, task.LeadTaskAction.NEW_TASK, text=_ADMISSION_BRIEF)

        successor_material = []
        def successor(value, material):
            successor_material.append(json.dumps(material, ensure_ascii=False))
        def successor_frame(call, provider):
            receipt = transition_receipt(positive_task).payload.receipt
            self.assertEqual(receipt.source_task_ref, positive_source.ref)
            self.assertNotEqual(receipt.target_task_id, positive_source.ref.task_id)
            self.assertEqual(receipt.target_task_revision, 1)
            selected_profile(call, (receipt.target_task_id, 1))
            return self._native_scoped(
                PLOT_ARCHITECT,
                domains=("plot_structure",), selected=receipt.target_task_id,
                revision=1,
                mutate=successor,
                authoring_assessment=_fixture_assessment(),
            )(call, provider)
        self.backend.enqueue(successor_frame)
        expected_aliases = ["current_response",
            *(source.event_id for source in c.project_collaboration_history(self.history(conversation)).offered_draft_sources), "user_input"]
        positive_result = await self.runtime.decide_lead_task(positive_task)
        pending = positive_result.turn
        self.assertEqual(positive_result.transition_event_id, transition_receipt(positive_task).event_id)
        task_id = transition_receipt(positive_task).payload.receipt.target_task_id
        self.assertEqual(len(successor_material), 1, pending)
        observed_successor = json.loads(successor_material[0])
        self.assertNotIn("authoring_tasks", observed_successor)
        self.assertEqual([item["input_id"] for item in observed_successor["input_aliases"]], ["user_input"], pending)
        self.assertEqual(observed_successor["available_proposal_drafting_source_ids"], expected_aliases, pending)
        self.assertIs(pending.kind, c.LeadOutcomeKind.PROPOSAL, pending)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("drafted", "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        revised = await self.runtime.decide_lead_step(_decision(pending, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(pending.proposal, pending)
        self.assertEqual(pending.proposal.authoring_scope.task_id, task_id)
        self.assertIs(revised.turn.state, c.CollaborationStepState.COMPLETED)
        self.assertEqual(revised.proposal.authoring_scope.task_revision,1)
        self.assertEqual(len(_submitted_calls(self.history(conversation)[len(completed_prefix):])), 3)
        positive_inventory = self.runtime.inspect_lead_collaboration(self.session_id, conversation).available_tasks
        self.assertEqual(len(next(item for item in positive_inventory if item.ref == positive_source.ref).submitted_call_ids), 11)
        self.assertEqual(len(next(item for item in positive_inventory if item.ref.task_id == task_id).submitted_call_ids), 3)
        current_source = c.project_collaboration_history(self.history(conversation)).offered_draft_sources[-1].event_id
        self.assertNotEqual(current_source, old_source)
        self.assertEqual(self.history(conversation)[:len(completed_prefix)], completed_prefix)
        def cross_product(value,material):
            self.assertNotIn("authoring_tasks", material)
            self.assertNotIn("task_revision", value["authoring_scope"])
            value["authoring_scope"]["interpretation"] = ""
        cross_writer = self._native_scoped(PLOT_ARCHITECT, mutate=cross_product, authoring_assessment=_fixture_assessment())
        def cross_frame(call, provider):
            self.assertTrue(call.contract.factory.schema_group_protocol)
            self.assertIs(call.contract.schema_group_phase, LeadGroupPhase.GENERATE)
            self.assertIn(call.contract.factory.rule_seed.contract_id, ("lead-response-v6", "lead-response-v7", "lead-response-v8"))
            self.assertEqual(call.contract.group_workflow.active_selection.group_id, "consultation.authoring.content")
            raw = cross_writer(call, provider)
            bad = json.loads(raw)["agent_action"]["task_result"]["response_details"]["task_scope"]
            self.assertEqual(bad["interpretation"], "")
            self.assertTrue(call.contract.structural_findings(raw), raw)
            return raw
        invalid = await self._rejected_pair(self.conversation(), _ADMISSION_BRIEF, cross_frame,
            selection_group='consultation.authoring.content')
        self._assert_continuation(invalid, 'proposal')
        multiple = self.conversation()
        before_multiple = self.artifact_bytes()
        multi_path = "manuscript/part-01/nonfirst-successor.md"
        def multiple_recommendation(call, provider):
            self.assertIn("current_response", dict(call.contract.semantic_index.policy.choices)["source_ids"])
            return _synthesis_outcome(message="The draft material is available for integration; no artifact changed.",
                recommendation_family=1, recommended_action=action(source_ids=("current_response",),
                    target_paths=(multi_path,), specialist_ids=ROSTER))(call, provider)
        first_draft, first_drafted = await self.finish_draft(multiple, response=self._native_scoped(
            *ROSTER, paths=(multi_path,), domains=("plot_structure", "character_world", "tone_execution"),
            mutate=two_owners, authoring_assessment=_fixture_assessment(),
        ), synthesis=multiple_recommendation)
        multiple_basis = first_draft.proposal.basis.core_document_readiness
        self.assertIsNotNone(multiple_basis)
        self.assertEqual(multiple_basis, replace(readiness.basis, prior_assessment_refs=expected_readiness_refs,
            history_coverage=multiple_basis.history_coverage, history_metadata=multiple_basis.history_metadata))
        first_source = c.project_collaboration_history(self.history(multiple)).offered_draft_sources[-1].event_id
        first_ready = first_drafted.turn
        self.assertIsNotNone(first_ready.recommendation_request, first_ready)
        self.assertEqual(tuple(source.event_id for source in first_ready.recommendation_request.draft_sources), (first_source,))
        self.assertEqual(first_ready.recommendation_request.source_recommendation_event_id, first_source)
        self.assertEqual(first_ready.recommendation_request.draft_sources[0].content_digest, "sha256:" + hashlib.sha256("The draft material is available for integration; no artifact changed.".encode("utf-8")).hexdigest())
        first_response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": multi_path, "new_text": "Synthetic chronology remains explicit.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call, material, *, input_payload: [input_payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent={"action": "start", "target_paths": {multi_path: True},
                "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(
                first_draft.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def integrate_multiple(call, provider):
            return first_response(call, provider)
        self.backend.enqueue(integrate_multiple)
        first_review = await self.runtime.decide_lead_recommendation(recommendation_decision(first_ready))
        self.assertIsNotNone(first_review.proposal, first_review)
        self.assertEqual(first_review.proposal.participants, ROSTER)
        for assignment in first_review.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome("supports", "The candidate preserves the first_reviewed chronology and motivation."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        first_reviewed = await self.runtime.decide_lead_step(_decision(first_review, c.CollaborationConfirmationDecision.CONFIRM))
        multi_review_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records if actor in ROSTER][-len(ROSTER):]
        multi_review_candidates = [next(item["artifact_candidate"] for item in frame["inputs"] if item["input_id"] == frame["assignment"]["review_basis"]["candidate_input_ids"][0]) for frame in multi_review_frames]
        self.assertEqual(multi_review_candidates, [multi_review_candidates[0]] * len(ROSTER))
        await self.runtime.decide_artifact_application(_application_decision(first_reviewed.turn, c.ArtifactApplicationDecisionKind.REJECT))
        self.assertEqual(self.artifact_bytes(), before_multiple)
        multi_prefix = self.history(multiple)
        multi_completed = c.project_collaboration_history(multi_prefix)
        self.assertIs(multi_completed.current_artifact_application_result.outcome, c.ArtifactApplicationOutcomeKind.REJECTED)
        self.assertEqual(multi_completed.rounds_spent, 2)
        self.assertLess(12 - multi_completed.physical_calls_spent, len(first_review.proposal.participants) + 2)
        self.assertEqual(multi_completed.physical_calls_spent, 11)
        new_task = task.LeadTaskDecision.for_conversation(
            self.session_id, multiple, task.LeadTaskAction.NEW_TASK, text=_ADMISSION_BRIEF)
        def second_task(call, provider):
            receipt = transition_receipt(new_task).payload.receipt
            self.assertIsNone(receipt.source_task_ref)
            self.assertEqual(receipt.target_task_revision, 1)
            self.assertNotEqual(receipt.target_task_id, first_draft.proposal.authoring_scope.task_id)
            selected_profile(call, (receipt.target_task_id, 1))
            return self._native_scoped(*ROSTER,
                domains=("plot_structure", "character_world", "tone_execution"),
                selected=receipt.target_task_id, revision=1, mutate=two_owners,
                authoring_assessment=_fixture_assessment(),)(call, provider)
        self.backend.enqueue(second_task)
        before_second = len(self.backend.calls)
        opened = await self.runtime.decide_lead_task(new_task)
        second_draft = opened.turn
        self.assertEqual(len(self.backend.calls) - before_second, 1, second_draft)
        self.assertEqual(opened.transition_event_id, transition_receipt(new_task).event_id)
        self.assertIs(second_draft.kind, c.LeadOutcomeKind.PROPOSAL, second_draft)
        self.assertEqual(tuple((assignment.specialist_id, assignment.mode) for assignment in second_draft.proposal.assignments),
            ((PLOT_ARCHITECT, SpecialistAssignmentMode.DRAFT), (CHARACTER_CUSTODIAN, SpecialistAssignmentMode.DRAFT),
             (EXECUTION_SPECIALIST, SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT)))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("drafted", "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome("drafted", "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome("constraints_assessed", "Summons leads choice; choice causes consequence."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(message="The draft material is available for integration; no artifact changed."))
        second_result = await self.runtime.decide_lead_step(_decision(second_draft, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(c.project_collaboration_history(self.history(multiple)).current_state, c.CollaborationStepState.COMPLETED)
        self.assertEqual(second_result.proposal, second_draft.proposal)
        self.assertEqual(len(_submitted_calls(self.history(multiple)[len(multi_prefix):])), 5)
        multi_prefix = self.history(multiple)
        expected = [(item.proposal.authoring_scope.task_id, 2) for item in (first_draft, second_draft)]
        inventory = self.runtime.inspect_lead_collaboration(self.session_id, multiple).available_tasks
        self.assertEqual(tuple((item.ref.task_id, item.ref.task_revision) for item in inventory),
            ((first_draft.proposal.authoring_scope.task_id, 1), (second_draft.proposal.authoring_scope.task_id, 1)))
        offers = [(item.ref.task_id, item.ref.task_revision + 1) for item in inventory]
        self.assertCountEqual(offers, expected)
        selected_ref = inventory[1].ref
        nonfirst_resume = task.LeadTaskDecision.from_task(selected_ref, task.LeadTaskAction.RESUME, text=_ADMISSION_BRIEF)
        chosen = []
        def nonfirst(call, provider):
            receipt = transition_receipt(nonfirst_resume).payload.receipt
            self.assertEqual(receipt.source_task_ref, selected_ref)
            self.assertEqual((receipt.target_task_id, receipt.target_task_revision), offers[1])
            selected_profile(call, offers[1])
            chosen.append(offers[1])
            return self._native_scoped(*ROSTER, selected=offers[1][0], revision=offers[1][1],
                domains=("plot_structure", "character_world", "tone_execution"), mutate=two_owners,
                authoring_assessment=_fixture_assessment(),
            )(call, provider)
        self.backend.enqueue(nonfirst)
        nonfirst_result = await self.runtime.decide_lead_task(nonfirst_resume)
        admitted = nonfirst_result.turn
        self.assertIs(admitted.kind, c.LeadOutcomeKind.PROPOSAL, admitted)
        self.assertEqual((admitted.proposal.authoring_scope.task_id, admitted.proposal.authoring_scope.task_revision), chosen[0])
        suffix = self.history(multiple)[len(multi_prefix):]
        fresh_admission = transition_receipt(nonfirst_resume).payload.receipt
        self.assertEqual(nonfirst_result.transition_event_id, transition_receipt(nonfirst_resume).event_id)
        self.assertEqual(fresh_admission.source_task_ref, selected_ref)
        self.assertEqual((fresh_admission.target_task_id, fresh_admission.target_task_revision), chosen[0])
        self.assertEqual(fresh_admission.planning_step_id, admitted.step_id)
        self.assertEqual(len(_submitted_calls(suffix)), 1)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in suffix))
        self.assertEqual(self.history(multiple)[:len(multi_prefix)], multi_prefix)
        self.assertEqual(self.artifact_bytes(), before_multiple)
        before_reject = len(self.backend.calls)
        rejected_pending = await self.runtime.decide_lead_step(_decision(admitted, c.CollaborationConfirmationDecision.REJECT))
        self.assertIs(rejected_pending.turn.state, c.CollaborationStepState.REJECTED)
        self.assertEqual(len(self.backend.calls), before_reject)
        negative_prefix = self.history(multiple)
        negative_task_id = second_draft.proposal.authoring_scope.task_id
        negative_old_source = c.project_collaboration_history(negative_prefix).offered_draft_sources[-1].event_id

        def drifted_scope(call):
            selected_profile(call, (negative_task_id, 3))
            index = next(i for i, pair in enumerate(call.contract.semantic_index.policy.task_revisions)
                         if pair == (negative_task_id, 3))
            self.assertEqual(_live_task(call, index), {"task_id": negative_task_id, "task_revision": 3})
            return dict(c.authoring_scope_to_dict(second_draft.proposal.authoring_scope), task_revision=3,
                domains=["plot_structure", "character_world", "tone_execution"],
                contributions=[{"domain": domain, "role": "draft_owner" if domain == "plot_structure" else "constraint_assessor"}
                    for domain in ("plot_structure", "character_world", "tone_execution")])
        drifted_writer = recommendation(recommended_action=action(source_ids=("user_input",), target_paths=(OUTLINE,), specialist_ids=ROSTER),
            authoring_scope=drifted_scope, mutate=lambda value: value.update(authoring_assessment=assessment))
        def drifted_frame(call, provider):
            receipt = transition_receipt(negative_resume).payload.receipt
            self.assertEqual((receipt.target_task_id, receipt.target_task_revision), (negative_task_id, 3))
            frame = json.loads(drifted_writer(call, provider))
            details = frame["agent_action"]["task_result"]["response_details"]
            details["proposal_drafting_request"]["source_ids"] = {negative_old_source: True}
            self.assertNotIn("task", details["task_scope"])
            details["task_scope"]["intent"] = "review_only"
            raw = json.dumps(frame, ensure_ascii=False)
            self.assertTrue(call.contract.structural_findings(raw), raw)
            return raw
        direct_writer = self._native_scoped(*ROSTER,
            selected=negative_task_id, revision=3, activity="review", domains=("character_world", "tone_execution"),
            mutate=lambda value, material: value["authoring_scope"].update(contributions=c.authoring_scope_to_dict(
                second_draft.proposal.authoring_scope)["contributions"]),
            authoring_assessment=_fixture_assessment(),
        )
        def direct_frame(call, provider):
            selected_profile(call, (negative_task_id, 3))
            raw = direct_writer(call, provider)
            self.assertTrue(call.contract.structural_findings(raw), raw)
            return raw

        for negative_frame in (drifted_frame, direct_frame):
            negative_task = next(item for item in self.runtime.inspect_lead_collaboration(
                self.session_id, multiple).available_tasks if item.ref.task_id == negative_task_id)
            self.assertEqual(negative_task.ref.task_revision, 2)
            negative_resume = task.LeadTaskDecision.from_task(negative_task.ref, task.LeadTaskAction.RESUME, text=_ADMISSION_BRIEF)
            self.backend.enqueue(negative_frame)
            self.backend.enqueue(negative_frame)
            before_negative = len(self.backend.calls)
            negative_result = await self.runtime.decide_lead_task(negative_resume)
            negative_turn = negative_result.turn
            self.assertEqual(len(self.backend.calls) - before_negative, 2, negative_turn)
            self.assertIs(negative_turn.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
            self.assertEqual(negative_result.transition_event_id, transition_receipt(negative_resume).event_id)
            self.assertEqual(negative_turn.step_id, transition_receipt(negative_resume).payload.receipt.planning_step_id)
            self.assertIsNone(negative_turn.recommendation_request, negative_turn)
            self._assert_continuation(negative_turn, expect_plot=False)
        self.assertEqual(len(_submitted_calls(self.history(multiple)[len(negative_prefix):])), 4)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(multiple)[len(negative_prefix):]))
        self.assertEqual(self.history(multiple)[:len(negative_prefix)], negative_prefix)
        negative_view = self.runtime.inspect_lead_collaboration(self.session_id, multiple)
        self.assertEqual(len(next(item for item in negative_view.available_tasks if item.ref.task_id == negative_task_id).submitted_call_ids), 10)
        self.assertEqual(negative_view.rounds_spent, 1)

        self.workspace.path(OUTLINE).unlink()
        latest_ref = next(item.ref for item in self.runtime.inspect_lead_collaboration(self.session_id, conversation).available_tasks
            if (item.ref.task_id, item.ref.task_revision) == (task_id, 1))
        stale_resume = task.LeadTaskDecision.from_task(latest_ref, task.LeadTaskAction.RESUME,
            text=_ADMISSION_BRIEF + "\nUse the earlier draft for this revised candidate.")
        from story_writing_agents.agent_context.accounting import account_provider_payload
        from story_writing_agents.conversations.session_context import MAX_PROVIDER_PAYLOAD_UTF8_BYTES, MAX_PROVIDER_SCHEMA_UTF8_BYTES
        stale_measurements = []
        def stale(call, provider):
            selected_profile(call, (task_id, 2))
            payload_json, accounting = account_provider_payload(call.schema, call.payload, call_kind=call.call_kind,
                component_paths=call.component_paths, mandatory_reference_paths=call.mandatory_reference_paths)
            self.assertLessEqual(len(payload_json), MAX_PROVIDER_PAYLOAD_UTF8_BYTES)
            self.assertLessEqual(accounting.schema_utf8_bytes, MAX_PROVIDER_SCHEMA_UTF8_BYTES)
            stale_measurements.append((len(payload_json), accounting.schema_utf8_bytes))
            receipt = transition_receipt(stale_resume).payload.receipt
            self.assertEqual((receipt.target_task_id, receipt.target_task_revision), (task_id, 2))
            self.assertIn(current_source, _workflow_material(provider.callback_payload(call))["available_proposal_drafting_source_ids"])
            current = c.authoring_scope_to_dict(revised.proposal.authoring_scope)
            self.assertEqual((current["task_id"], current["task_revision"]), (task_id, 1))
            current = dict(current, task_revision=2)
            self.assertEqual((current["task_id"], current["task_revision"]), (task_id, 2))
            source_choices = dict(call.contract.semantic_index.policy.choices)
            self.assertEqual(source_choices["source_binding"], ("bound",))
            self.assertIn(current_source, source_choices["source_ids"])
            raw = recommendation(message="Exclude the earlier revision draft.",
                recommended_action=action(source_ids=(current_source,), target_paths=(OUTLINE,),
                specialist_ids=(PLOT_ARCHITECT,)), authoring_scope=current,
                mutate=lambda value: value.update(authoring_assessment=assessment),
            )(call, provider)
            frame = json.loads(raw)
            self.assertEqual(frame["agent_action"]["task_result"]["response_details"]["proposal_drafting_request"]["source_ids"],
                finite_fixture_selection(source_choices["source_ids"], (current_source,)))
            self.assertTrue(call.contract.structural_findings(raw), raw)
            return raw
        self.backend.enqueue(stale)
        self.backend.enqueue(stale)
        before_stale = len(self.backend.calls)
        stale_result = await self.runtime.decide_lead_task(stale_resume)
        rejected = stale_result.turn
        self.assertEqual(len(self.backend.calls) - before_stale, 2, rejected)
        self.assertEqual(len(stale_measurements), 2, stale_measurements)
        self.assertGreater(stale_measurements[1][0], 262_144, stale_measurements)
        self.assertEqual(stale_measurements[1][1], stale_measurements[0][1], stale_measurements)
        self.assertIs(rejected.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertEqual(stale_result.transition_event_id, transition_receipt(stale_resume).event_id)
        self.assertIsNone(rejected.recommendation_request, rejected)
        self._assert_continuation(rejected, 'artifact_proposal')

        self.session_id = "historical_input_session"
        backend = AgentBackend(self.control_workspace)
        self.addCleanup(backend.close)
        resources = backend.provision_session_resources(self.session_id)
        self.workspace = type(self.workspace)(resources.identity.worktree_root).require_ready()
        before = self.artifact_bytes()
        prior = self.conversation("Historical drafting input remains separate from fresh review proof")
        first, _ = await self.finish_draft(prior)
        original_history = self.history(prior)
        source = c.project_collaboration_history(original_history).offered_draft_sources[-1]
        fresh_scope = replace(first.proposal.authoring_scope, task_revision=2)
        view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        selected_task = next(item for item in view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        source_conversation = self.conversation("Same-session foreign public message")
        invalid = json.loads(_discussion_reply("Selected rejected whole explanation."))
        invalid["agent_action"]["task_result"]["used_evidence_ids"] = "invalid array"
        self.backend.enqueue(json.dumps(invalid))
        self.backend.enqueue(json.dumps(invalid))
        discussed = await self.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            self.session_id, source_conversation, task.LeadTaskAction.DISCUSS, text="Discuss independently.", ))
        rejected_text = discussed.turn.public_responses[-1]
        self.assertIs(rejected_text.response.acceptance, task.LeadPublicResponseAcceptance.REJECTED)
        self.assertEqual(rejected_text.ref.conversation_id, source_conversation)
        self.assertEqual(c.project_collaboration_history(self.history(source_conversation)).offered_draft_sources, ())
        self.assertEqual(c.project_collaboration_history(self.history(prior)).offered_draft_sources[-1], source)
        self.assertEqual(self.history(prior)[:len(original_history)], original_history)
        original_history = self.history(prior)
        selected_writer = recommendation(message=fresh_scope.interpretation, recommended_action=action(
            kind="prepare_consultation", source_ids=(source.event_id,)),
                authoring_scope=c.authoring_scope_to_dict(fresh_scope),
            authoring_assessment=_fixture_assessment(foundation_ids=("user_input", "selected_material_1")),
        )
        def selected_recommendation(call, provider):
            selected_profile(call, (fresh_scope.task_id, 2))
            return selected_writer(call, provider)
        self.backend.enqueue(selected_recommendation)
        selected = await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task( selected_task.ref, task.LeadTaskAction.RESUME,
                text=_ADMISSION_BRIEF + "\nUse the earlier draft with this selected explanation.", material_refs=(rejected_text.ref,), ))
        suggested = selected.turn
        request = suggested.recommendation_request
        self.assertIsNotNone(request, suggested)
        self.assertEqual((request.authoring_scope, request.draft_sources), (fresh_scope, (source,)))
        selected_admission = next(item for item in request.basis.authoring_admission.sources
            if item.source_id == "selected_material_1")
        self.assertEqual((selected_admission.kind, selected_admission.original_authority),
            (admission.AdmissionSourceKind.MATERIAL, "rejected_public_response"))
        def prepare_historical(call, provider):
            material = _workflow_material(provider.callback_payload(call))
            self.assertEqual(material["structured_recommendation_execution"]["request"]["authoring_scope"],
                {key: value for key, value in c.authoring_scope_to_dict(fresh_scope).items()
                 if key not in {"task_id", "task_revision"}})
            native = wire_result({"authoring_assessment": asdict(request.basis.authoring_admission.assessment), "kind": "proposal", "message": request.purpose,
                "authoring_scope": _compact_scope(c.authoring_scope_to_dict(fresh_scope)),
                "proposal": {"shared_premises": [], "assignments": [{"agent_id": PLOT_ARCHITECT, "mode": "draft", "instruction": request.instruction,
                    "input_ids": [item.input_id for item in request.basis.inputs]}]}}, wire_contract_id(call.payload))
            from tests.provider_wire_fixture import lead_fixture_assessment
            choices = dict(call.contract.semantic_index.policy.choices)
            native['task_scope'] = lead_fixture_bound_scope(
                call, native.pop('writing_task_scope'), record=True)
            native['authoring_assessment'] = lead_fixture_assessment(
                native['authoring_assessment'], source_choices=choices['input_ids'])
            proposal = native['specialist_consultation_proposal']
            allocation = lead_fixture_assignments(
                author_positive_fixture_review_bases(
                    proposal['assignments'],
                    candidate_choices=choices['review_candidate_ids']),
                input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'],
                anchors={PLOT_ARCHITECT: request.basis.inputs[0].input_id},
                recipients={source: PLOT_ARCHITECT for source in choices['required_input_ids']})
            proposal.update(allocation)
            return lead_fixture_response(call, lead_fixture_result(native), family=0)
        self.backend.enqueue(prepare_historical)
        prepared = await self.runtime.decide_lead_recommendation(recommendation_decision(suggested))
        self.assertEqual(prepared.proposal.authoring_scope, fresh_scope)
        self.assertEqual(prepared.proposal.budget.rounds_spent, 0)
        self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for event in self.history(prior)[len(original_history):]))
        self.assertEqual(prepared.proposal.budget.physical_calls_spent, len(_submitted_calls(self.history(prior)[len(original_history):])))
        historical_input = next(item for item in prepared.proposal.basis.inputs
                                if item.locator == "conversation/" + source.event_id
                                and item.revision_digest == source.content_digest)
        selected_input = next(item for item in prepared.proposal.basis.inputs if item.input_id == "selected_material_1")
        transition = next(event.payload.receipt for event in self.history(prior)
                          if event.event_id == selected.transition_event_id)
        capture = next(event for event in self.history(prior) if event.event_id in transition.material_capture_event_ids)
        self.assertEqual((capture.payload.capture.source, capture.payload.capture.selected_text),
                         (rejected_text.ref, "Selected rejected whole explanation."))
        self.assertEqual((capture.payload.capture.target_task_id, capture.payload.capture.target_task_revision), (fresh_scope.task_id, fresh_scope.task_revision))
        front = next(_workflow_material(call.payload) for agent, call in reversed(self.backend.call_records)
                     if agent == LEAD_ORCHESTRATOR and _workflow_material(call.payload).get("selected_task_materials"))
        expected_material = {"input_id": selected_input.input_id, "capture_event_id": capture.event_id, **{key: value for key, value in task.task_value_to_dict(capture.payload.capture).items() if key not in {'target_task_id', 'target_task_revision'}}}
        expected_material["source"]["record_type"] = expected_material["source"].pop("kind")
        self.assertEqual(front["selected_task_materials"], [expected_material], (front["selected_task_materials"], expected_material))
        self.assertEqual((selected_input.kind, selected_input.locator, selected_input.revision_digest),
                         (c.CollaborationInputKind.CONVERSATION_EVENT, "conversation/" + capture.event_id,
                          "sha256:" + rejected_text.ref.material_digest))
        self.assertEqual((selected_admission.source_identity, selected_admission.revision_digest, selected_admission.utf8_bytes),
            (selected_input.locator, selected_input.revision_digest, len(capture.payload.capture.selected_text.encode("utf-8"))))
        self.assertEqual(next(item for item in prepared.proposal.basis.authoring_admission.sources
            if item.source_id == selected_input.input_id), selected_admission)
        self.assertIn(selected_input.input_id, prepared.proposal.assignments[0].input_ids)
        self.assertEqual((historical_input.locator, historical_input.revision_digest), ("conversation/" + source.event_id, source.content_digest))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("drafted", "A fresh chronology follows the selected historical material."))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        drafted = await self.runtime.decide_lead_step(_decision(prepared, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(drafted.turn.state, c.CollaborationStepState.COMPLETED)
        self.assertEqual(next(item["conversation_event"] for agent, call in reversed(self.backend.call_records) if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if (item["input_id"], item["locator"], item["revision_digest"]) == (historical_input.input_id, historical_input.locator, historical_input.revision_digest)), json.loads(json.dumps(asdict(next(event.payload for event in original_history if event.event_id == source.event_id)))))
        delivered = next(item for agent, call in reversed(self.backend.call_records) if agent == PLOT_ARCHITECT
                         for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == selected_input.input_id)
        self.assertEqual((delivered["text"], delivered["selected_material_source"]),
                         (capture.payload.capture.selected_text, task.task_value_to_dict(capture.payload.capture.source)))
        current = c.project_collaboration_history(self.history(prior)).offered_draft_sources[-1]
        self.assertNotEqual(current.event_id, source.event_id)
        self.assertEqual(self.history(prior)[:len(original_history)], original_history)
        fresh_ready = await self.proposal_for(prior, recommendation(recommended_action=action(source_ids=(
            current.event_id,)), authoring_scope=c.authoring_scope_to_dict(fresh_scope),
            mutate=lambda value: value.update(authoring_assessment=assessment),
        ), selection_group='prepare.artifact')
        def integrate_fresh(call, provider):
            return _artifact_outcome(
                operations=lambda call, material, *, input_payload: [{"kind": "replace",
                    "evidence_id": _status_id(input_payload, OUTLINE), "new_text": DRAFT}],
                evidence_status="incomplete",
                evidence_ids=lambda call, material, *, input_payload: [
                    input_payload["core_document_statuses"][0]["evidence_id"]],
                objective_intent={"action": "start", "target_paths": {OUTLINE: True},
                    "next_step": "prepare"},
                authoring_scope=lambda call: _live_material(call)[
                    "structured_recommendation_execution"]["request"]["authoring_scope"],
                authoring_assessment=assessment,
            )(call, provider)
        self.backend.enqueue(integrate_fresh)
        fresh_review = await self.runtime.decide_lead_recommendation(recommendation_decision(fresh_ready))
        self.assertEqual(None if fresh_review.proposal is None else (fresh_review.proposal.participants, fresh_review.proposal.assignments[0].mode), ((PLOT_ARCHITECT,), SpecialistAssignmentMode.REVIEW), fresh_review)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        final = await self.runtime.decide_lead_step(_decision(fresh_review, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(final.turn.artifact_proposal, final)
        self.assertIs(final.turn.artifact_proposal.state, c.ArtifactProposalState.REVIEWED, final.turn)
        self.assertEqual(self.artifact_bytes(), before)
        pending_view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        protected = next(item for item in pending_view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        prefix, spent = self.history(prior), len(_submitted_calls(self.history(prior)))
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task(protected.ref, task.LeadTaskAction.HOLD))
        self.assertEqual(self.history(prior), prefix)
        def readonly_discussion(call, _provider):
            self.assertIsNone(call.contract.schema_group_phase)
            return lead_fixture_response(call, lead_fixture_result({"response_type": "answer",
                "response_text": "This answer grants no artifact action.", "used_evidence_ids": {}}), family=0)
        self.backend.enqueue(readonly_discussion)
        readonly = await self.runtime.decide_lead_task(task.LeadTaskDecision.from_task(
            protected.ref, task.LeadTaskAction.DISCUSS, text="Discuss without resolving Apply.", ))
        after_discussion = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        self.assertEqual(after_discussion.current_artifact_application_request, pending_view.current_artifact_application_request)
        self.assertEqual((after_discussion.rounds_spent, after_discussion.physical_calls_spent),
                         (pending_view.rounds_spent, pending_view.physical_calls_spent))
        self.assertEqual(len(_submitted_calls(self.history(prior))), spent + 1)
        self.assertIsNone(readonly.turn.proposal)
        self.assertEqual((readonly.turn.next_action, readonly.turn.artifact_application_request),
                         (c.CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
                          pending_view.current_artifact_application_request))
        self.assertEqual(self.artifact_bytes(), before)
        for failure in (_SubmittedFailure(RuntimeError("known discussion failure")),
                        _SubmittedFailure(asyncio.CancelledError("known discussion cancellation"))):
            prefix = self.history(prior)
            self.backend.enqueue(failure)
            decision = task.LeadTaskDecision.from_task(protected.ref, task.LeadTaskAction.DISCUSS, text="Inspect without changing the pending Apply.")
            if isinstance(failure.failure, asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await self.runtime.decide_lead_task(decision)
            else:
                await self.runtime.decide_lead_task(decision)
            restored_pending = StoryRuntime(self.control_workspace, self.backend).inspect_lead_collaboration(
                self.session_id, prior,)
            self.assertEqual(restored_pending.current_artifact_application_request, pending_view.current_artifact_application_request)
            self.assertEqual(len(_submitted_calls(self.history(prior)[len(prefix):])), 1)
            self.assertEqual(self.history(prior)[:len(prefix)], prefix)
            self.assertEqual(self.artifact_bytes(), before)
        await self.runtime.decide_artifact_application(_application_decision(final.turn, c.ArtifactApplicationDecisionKind.REJECT))
        captured = self.history(prior)
        from story_writing_agents.conversation_storage.archive import serialize_conversation_archive, deserialize_conversation_archive
        from tests.conversation_archive_fixture import archive_conversation, archive_from_conversations
        archive = archive_from_conversations(archive_conversation(session_id=self.session_id, conversation_id=prior, events=captured), archive_conversation(session_id=self.session_id,
            conversation_id=source_conversation,
            events=self.history(source_conversation)))
        self.assertEqual(deserialize_conversation_archive(serialize_conversation_archive(archive)), archive)
        from story_writing_agents.conversation_storage import ConversationStorageBackend, WorkspaceConversationStorageMutationCoordinator
        from story_writing_agents.conversation_storage.migration import transfer_conversation_storage
        from story_writing_agents.conversation_storage.sqlite import provision_sqlite_conversation_storage, compose_sqlite_conversation_storage
        with WorkspaceConversationStorageMutationCoordinator(self.control_workspace).borrow(timeout_seconds=2.0) as lease:
            provision_sqlite_conversation_storage(self.control_workspace, lease=lease)
        transfer_conversation_storage(self.control_workspace, source_backend=ConversationStorageBackend.FILESYSTEM, destination_backend=ConversationStorageBackend.SQLITE)
        restored = compose_sqlite_conversation_storage(self.control_workspace).event_store.read(self.session_id, prior)
        self.assertEqual(restored, captured)
        self.assertEqual(next(event.payload.basis.authoring_admission for event in restored
            if event.event_id == request.request_event_id), request.basis.authoring_admission)
        self.assertEqual(c.project_collaboration_history(restored), c.project_collaboration_history(captured))
        restarted = StoryRuntime(self.control_workspace, backend=self.backend)
        self.assertEqual(restarted.conversation_storage_services.event_store.read(self.session_id, prior), captured)


    async def test_legacy_pending_confirmation_requires_fresh_scoped_proposal_without_rewrite(self):
        from types import SimpleNamespace
        from tests import collaboration_contract_fixture as fixture
        from story_writing_agents.errors import BackendProtocolError
        conversation=self.conversation()
        batch=fixture.contract_batches(conversation)["proposal"]
        batch=replace(batch,events=tuple(replace(e,session_id=self.session_id) for e in batch.events))
        self.runtime.conversation_storage_services.event_store.append_batch(batch)
        history=self.history(conversation)
        proposal=next(e.payload for e in history if type(e.payload) is c.CollaborationProposal)
        self.assertIsNone(proposal.authoring_scope)
        state=next(e.payload for e in history if type(e.payload) is c.CollaborationStepStatePayload)
        pending=SimpleNamespace(session_id=self.session_id,conversation_id=conversation,step_id=proposal.step_id,
                                proposal=proposal,confirmation_request_id=state.confirmation_request_id)
        before=tuple(c.encode_collaboration_event(e) for e in history)
        with self.assertRaisesRegex(BackendProtocolError,"regeneration and confirmation"):
            await self.runtime.decide_lead_step(_decision(pending,c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(self.backend.calls,[])
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation)),before)
        fresh=await self.proposal_for(conversation)
        self.assertIsNotNone(fresh.proposal.authoring_scope)
        self.assertNotEqual(fresh.confirmation_request_id,pending.confirmation_request_id)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation)[:len(history)]),before)
