from tests.provider_wire_fixture import selected_fixture_calls, capture_sample
"""Runtime routing accepts typed task facts and keeps draft/review authority separate."""
from tests.provider_wire_fixture import ( result as wire_result, lead_fixture_bound_scope, lead_fixture_assignments,
    finite_fixture_selection, lead_fixture_result, lead_fixture_response,)
from tests.provider_wire_fixture import author_positive_fixture_review_bases
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
from tests.recommendation_fixture import DRAFT, action, recommendation, decision as recommendation_decision
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
        intent=None, mutate=None, native_inputs=None, select=False):
    def respond(call, provider, *, select=select):
        material = _workflow_material(call.payload)
        tasks = material["authoring_tasks"]
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
        if authoring_assessment is not None:
            value["authoring_assessment"] = admission.assessment_to_dict(authoring_assessment)
        statuses = call.payload.get("core_document_statuses", [])
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
        return lead_fixture_response(call, lead_fixture_result(body),
                                     family=native_inputs['family'],
                                     response_type=native_types.get(kind, kind), select=select)
    return respond


class SpecialistRoutingRuntimeTests(LeadOrchestrationTestCase):
    def _assert_continuation(self, result, field):
        self.assertIsNone(getattr(result, field))
        self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
    def _native_scoped(self, *agents, **values):
        mutate = values.pop('mutate', None)
        def respond(call, provider, *, select=False):
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
            writer = scoped_proposal(*agents, **values, mutate=prepare, native_inputs=native,
                                     select=select)
            frame = json.loads(writer(call, provider))
            if select:
                return json.dumps(frame, ensure_ascii=False)
            details = frame['agent_action']['task_result']['response_details']
            for premise in details['specialist_consultation_proposal']['shared_premises']:
                premise['basis_input_ids'] = finite_fixture_selection(
                    choices['input_ids'], premise['basis_input_ids'])
            return json.dumps(frame, ensure_ascii=False)
        return respond

    async def proposal_for(self, conversation, response=None, text=_ADMISSION_BRIEF,
        adoption=None, select=False):
        writer = self._native_scoped if text == _ADMISSION_BRIEF else scoped_proposal
        response = response or writer(
            PLOT_ARCHITECT, authoring_assessment=_fixture_assessment() if text == _ADMISSION_BRIEF else None,
            domains=("plot_structure",),)
        first = (partial(response, select=True), response) if select else (response,)
        self.backend.enqueue(*first)
        return await self.runtime.lead_turn(self.session_id, conversation, text, adoption=adoption)

    async def finish_draft(self, conversation, *, synthesis=None, **kwargs):
        front_start = len(self.backend.calls)
        if kwargs.get('adoption') is not None:
            kwargs['select'] = True
        proposed = await self.proposal_for(conversation, **kwargs)
        if kwargs.get('adoption') is not None:
            self.assertEqual(len(self.backend.calls) - front_start, 2)
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, proposed)
        for assignment in proposed.proposal.assignments:
            status = {SpecialistAssignmentMode.DRAFT: "drafted", SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT: "constraints_assessed"}[assignment.mode]
            self.backend.enqueue_for(assignment.specialist_id, _specialist_outcome(status, "Summons leads choice; choice causes consequence."))
        if synthesis is None:
            self.backend.enqueue_for(LEAD_ORCHESTRATOR,
                _synthesis_outcome(
                message="The draft material is available for integration; no artifact changed."))
        else:
            self.backend.enqueue_for(LEAD_ORCHESTRATOR, partial(synthesis, select=True), synthesis)
        result = await self.runtime.decide_lead_step(_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        if result.turn.kind is c.LeadOutcomeKind.RECOMMENDATION:
            history = self.history(conversation)
            c.project_collaboration_history(history)
            source = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationProposal and event.step_id == proposed.proposal.step_id)
            terminal = next(event.payload for event in reversed(history) if type(event.payload) is c.CollaborationStepStatePayload and event.step_id == proposed.proposal.step_id and event.payload.proposal_digest == proposed.proposal.proposal_digest)
            self.assertEqual(source, proposed.proposal)
            self.assertIs(terminal.state, c.CollaborationStepState.COMPLETED)
        else:
            self.assertIs(result.turn.state, c.CollaborationStepState.COMPLETED)
        return proposed, result

    async def test_empty_target_adequate_brief_produces_draft_without_writes(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        conversation = self.conversation()
        assessment = admission.assessment_to_dict(_fixture_assessment())
        before = self.artifact_bytes()
        def recommend(call, provider, *, select=False):
            material = _workflow_material(call.payload)
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, message=material["authoring_scope"]["interpretation"],
                recommended_action=action(source_ids=(draft_reply,), target_paths=(OUTLINE,)),
                select=select)(call, provider)
        proposed, result = await self.finish_draft(conversation, synthesis=recommend, select=True)
        self.assertEqual(proposed.proposal.participants, (PLOT_ARCHITECT,))
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
            operations=lambda call, material: [{"kind": "replace", "evidence_id": _status_id(call, OUTLINE), "new_text": "Synthetic reviewed outline.\n"}],
            evidence_status="empty", evidence_ids=lambda call, material: [_status_id(call, OUTLINE)],
            objective_intent={"action": "start", "target_paths": {OUTLINE: True},
                              "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(proposed.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def prepare(call, provider, *, select=False):
            return response(call, provider, select=select)
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
        def successor(value, material):
            self.assertEqual([(t["task_id"], t["task_revision"]) for t in material["authoring_tasks"]], [(proposed.proposal.authoring_scope.task_id, 2)])
            self.assertEqual(material["available_proposal_drafting_source_ids"], [])
        blocked = await self.proposal_for(conversation, self._native_scoped(
            PLOT_ARCHITECT, selected=proposed.proposal.authoring_scope.task_id,
            domains=("plot_structure",), revision=2, activity="review", mutate=successor,
            authoring_assessment=_fixture_assessment(),
        ), select=True)
        self.assertIsNone(blocked.proposal, blocked)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(prefix):]))
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)


        prefix = self.history(conversation)
        late_call_start = len(self.backend.calls)
        def request_inspection_source(call, _provider):
            offers = call.contract.offers
            self.assertEqual(offers.allowed_actions, ("answer",))
            self.assertEqual(dict(offers.action_limits)["nonterminal_actions"], 0)
            observation = call.payload["context_observation"]
            self.assertEqual(observation["observation_type"], "context_search_results")
            self.assertTrue(observation["search_results"])
            self.assertIn(observation["search_results"][0]["read_handle"],
                          offers.issued_handle_aliases)
            self.assertEqual(offers.completed_read_facts, ())
            self.assertEqual(offers.delivery_read_facts, ())
            return lead_fixture_response(call, {
                "response_type": "clarification_question",
                "response_text": "Please provide the matching prior response so I can inspect it before offering fresh work.",
                "response_details": {"task_scope": lead_fixture_bound_scope(call, _live_scope(
                    call, activity="draft", domains=("plot_structure",), paths=(OUTLINE,)),
                    record=True)},
            }, family=0, branch_key=call.contract.factory.rule_seed.contract_id + ":family:1")

        self.backend.enqueue(
            partial(_search_history, text="Nora must recover her brother's letter"),
            request_inspection_source,
            _visible_outcome("answer", "A third submission is not invoked."))
        bounded = await self.runtime.lead_turn(self.session_id, conversation, "Inspect context before offering fresh work.")
        calls = _submitted_calls(self.history(conversation)[len(prefix):])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.backend.calls) - late_call_start, 2)
        self.assertIs(bounded.kind, c.LeadOutcomeKind.QUESTION)
        self.assertIsNone(bounded.proposal)
        self.assertFalse(any(event.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for event in self.history(conversation)[len(prefix):]))
        self.assertEqual(self.history(conversation)[:len(prefix)], prefix)

    async def test_empty_inventory_does_not_add_unaffected_owners_and_mixed_scope_does(self):
        for path in (OUTLINE, CHARACTER, "core_documents/narrative_rules.md", "core_documents/part_outlines.md"):
            self.workspace.path(path).write_bytes(b"")
        def premise_with_extra(call, provider):
            facts = _workflow_material(call.payload)["provider_constraints"]
            spec = {"question_id": facts["offered_premise_question_id"], "slot_id": "knowledge",
                "question": "Who knows the concealed motive?", "dependent_domains": ["plot_structure"]}
            value = {"kind": "proposal", "message": "Compare unresolved options.", "authoring_scope": None,
                "proposal": {"premise_decision": spec, "assignments": [{"agent_id": PLOT_ARCHITECT,
                    "instruction": "Compare options without authoring.", "mode": "premise_analysis",
                    "input_ids": list(facts["available_assignment_input_source_ids"])}]}, "authoring_assessment": None}
            return wire_answer(value, wire_contract_id(call.payload))
        self.backend.enqueue(premise_with_extra)
        refused = await self.runtime.lead_turn(self.session_id, self.conversation(), "Explore who knows the motive.")
        self.assertIsNone(refused.proposal)
        single = await self.proposal_for(self.conversation(), select=True)
        self.assertEqual(single.proposal.participants, (PLOT_ARCHITECT,))
        readiness = await self.record_book_readiness()
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        expected_snapshots = []
        def mixed_with_snapshot(call, provider, *, select=False):
            services = self.runtime.conversation_storage_services
            if not select:
                expected_snapshots.append((SessionHistorySourceAdapter(services.catalog,
                    services.event_store, self.session_id).capture(), call))
            return self._native_scoped(*ROSTER, paths=("manuscript/part-01/opening.md",),
                domains=("plot_structure", "character_world", "tone_execution"),
                authoring_assessment=_fixture_assessment(),)(call, provider, select=select)
        mixed = await self.proposal_for(self.conversation(), response=mixed_with_snapshot,
            select=True)
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
        )))
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
        def recommend(call, provider, *, select=False):
            reply = next(item["reply_event_id"] for item in _workflow_material(call.payload)["replies"] if item["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, recommended_action=action(
                    target_paths=(path,), source_ids=("user_input", "selected_premise", reply))
            , select=select)(call, provider)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, partial(recommend, select=True), recommend)
        finished = await self.runtime.decide_lead_step(_decision(drafted, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(finished.turn.recommendation_request, finished)
        frames = [_workflow_material(call.payload) for agent, call in self.backend.call_records if wire_contract_id(call.payload) == "specialist-assignment-response-v5"]
        self.assertEqual([(frame["premise_selection"]["selected_text"], frame["shared_premises"][0]["selected_premise"]) for frame in frames], [(option.premise_text, option.premise_text)] * 2)
        response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": path, "new_text": "The selected knowledge remains intentionally uncertain.\n"}], evidence_status="incomplete", evidence_ids=lambda call, material: [call.payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent=dict(action="start", target_paths={path: True}, next_step="prepare"),
            authoring_scope=lambda _: c.authoring_scope_to_dict(drafted.proposal.authoring_scope),
            authoring_assessment=asdict(_fixture_assessment(foundation_ids=("selected_premise",))),)
        def integrate(call, provider, *, select=False):
            return response(call, provider, select=select)
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
        )))
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
        )))
        changed = await self.runtime.lead_turn(
            self.session_id,
            conversation,
            "Draft changed whole premise freshly." + "\n" + _ADOPTION_DIRECTION,
            premise_selection=successor, )
        self.assertIs(changed.kind, c.LeadOutcomeKind.PROPOSAL, changed)
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

    async def test_declared_substantive_direct_outcomes_fail_closed(self):
        for kind in ("answer", "recommendation", "critique"):
            with self.subTest(kind=kind):
                conversation = self.conversation()
                result = await self.proposal_for(conversation, scoped_proposal(PLOT_ARCHITECT, kind=kind,
                    authoring_assessment=_fixture_assessment(),))
                self._assert_continuation(result, 'proposal')
                self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)))
        conversation = self.conversation()
        self.backend.enqueue(_visible_outcome("question", "Which existing task should this status request concern?"))
        question = await self.runtime.lead_turn(self.session_id, conversation, "Clarify which task is active.")
        self.assertIs(question.kind, c.LeadOutcomeKind.QUESTION)

    async def test_review_rejects_empty_reference_but_accepts_explicit_user_candidate(self):
        self.workspace.path(OUTLINE).write_bytes(b"")
        bad_conversation = self.conversation("Rejected empty review source")
        bad = await self.proposal_for(
            bad_conversation, scoped_proposal(PLOT_ARCHITECT, activity="review", candidate_ids=("reference_1",)),
            text="Assess the concrete candidate ${core_documents/global_outline.md}.", )
        self.assertIsNone(bad.proposal)
        conversation = self.conversation()
        original, before = "완성 원고.\r\nCafé.  \r\n", self.artifact_bytes()
        good = await self.proposal_for(conversation, self._native_scoped(
                PLOT_ARCHITECT, activity='review', intent='review_only',
                domains=('plot_structure',)), text=original, select=True)
        self.assertIs(good.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertEqual(good.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))
        self.assertIs(good.proposal.authoring_scope.intent, AuthoringIntent.REVIEW_ONLY)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR,
            _synthesis_outcome(
            message="Suggested edits remain findings until a fresh authoring request."))
        reviewed = await self.runtime.decide_lead_step(_decision(good, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual([item["text"].encode() for agent, call in self.backend.call_records if agent == PLOT_ARCHITECT for item in _workflow_material(call.payload)["inputs"] if item["input_id"] == "user_input"], [original.encode()])
        self.assertEqual(self.artifact_bytes(), before)
        self.assertTrue(all(request is None for request in (reviewed.turn.artifact_application_request, reviewed.turn.story_commit_request)))
        self.assertFalse(any(type(e.payload) in (c.ArtifactObjectivePayload, c.ArtifactObjectivePayloadV2) for e in self.history(conversation)))

        self.backend.enqueue(_visible_outcome("answer", "The rejected empty review remains unexecuted."))
        await self.runtime.lead_turn(self.session_id, bad_conversation, "Resolve the failed interpretation.")
        source, owner = self.conversation("Review source A"), self.conversation("Selected review owner B")
        supplied = "Unused prefix.\n" + original + "\nUnused suffix."
        self.backend.enqueue(*selected_fixture_calls(_current_exploration_outcome, supplied))
        await self.runtime.lead_turn(self.session_id, source, "Explore supplied review alternatives.",
                                     interaction_mode=exploration.LeadInteractionMode.EXPLORATION)
        event = next(e for e in self.history(source) if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        start = len("Unused prefix.\n")
        selection = replace(_adoption_selection(source, event, (OUTLINE,)), start_character=start, end_character=start + len(original), intent=AuthoringIntent.REVIEW_ONLY)
        selected = await self.proposal_for(owner, self._native_scoped(
            PLOT_ARCHITECT, domains=("plot_structure",), activity="review",
            intent="review_only", candidate_ids=("adopted_idea",),
            mutate=lambda value, _material: value["proposal"]["assignments"][0].update(
                input_ids=["adopted_idea"])), adoption=selection)
        self.assertIsNotNone(selected.proposal, selected)
        self.assertEqual(selected.proposal.assignments[0].review_basis.candidate_input_ids, ("adopted_idea",))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR,
            _synthesis_outcome(message="Selected supplied review findings."))
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
        def wrong_pair(value, material): value["authoring_scope"]["task_revision"] += 1
        result = await self.proposal_for(
            self.conversation(), scoped_proposal(PLOT_ARCHITECT, mutate=wrong_pair,
                authoring_assessment=_fixture_assessment(),
            ) )
        self._assert_continuation(result, 'proposal')

    async def test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel(self):
        original_conversation = self.conversation()
        original = await self.proposal_for(original_conversation, select=True)
        prefix = self.history(original_conversation)
        selected = original.proposal.authoring_scope.task_id
        def relabel(value, material):
            value["authoring_scope"].update(activity="workflow", target_paths=[], domains=[])
        bad = await self.proposal_for(self.conversation(), scoped_proposal(PLOT_ARCHITECT,
            selected=selected, kind="answer", mutate=relabel,
            authoring_assessment=_fixture_assessment(),
        ))
        self._assert_continuation(bad, 'proposal')
        continued = await self.proposal_for(self.conversation(), self._native_scoped(
            PLOT_ARCHITECT, domains=("plot_structure",), selected=selected,
            authoring_assessment=_fixture_assessment(),
        ), select=True)
        self.assertIsNotNone(continued.proposal, continued)
        self.assertEqual(continued.proposal.authoring_scope.task_id, selected)
        self.assertEqual(self.history(original_conversation), prefix)
        self.backend.enqueue(_visible_outcome("answer", "The workflow relabel remains rejected."))
        await self.runtime.lead_turn(self.session_id, bad.conversation_id, "Resolve the rejected interpretation.")
        source, foreign_focus = self.conversation("Current provisional source"), self.conversation("Foreign focus")
        self.backend.enqueue(*selected_fixture_calls(_current_exploration_outcome))
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
        pending = await self.proposal_for(conversation, select=True)
        task_id = pending.proposal.authoring_scope.task_id
        self.workspace.path(OUTLINE).unlink()
        def selected_review_scope(call):
            material = _workflow_material(call.payload)
            index = next(i for i,t in enumerate(material["authoring_tasks"])
                if t["task_id"] == task_id)
            return _live_scope(call, activity="review", domains=("plot_structure",),
                paths=(OUTLINE,), task_index=index)
        response = _artifact_outcome(
            operations=[{"kind":"create", "suggested_path":OUTLINE,
                "new_text":"Current synthetic candidate."}], evidence_status="missing",
            evidence_ids=lambda call,m:[next(x["evidence_id"]
                for x in call.payload["core_document_statuses"] if x["artifact_path"] == OUTLINE)],
            authoring_scope=selected_review_scope)
        def selected_review(call, provider):
            value = json.loads(response(call, provider))
            value["agent_action"]["task_result"]["response_details"]["authoring_assessment"] = None
            return json.dumps(value)
        self.backend.enqueue(selected_review)
        result = await self.runtime.lead_turn(self.session_id, self.conversation(), "Integrate that pending task into the supplied candidate.")
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
        pins = dict(
            sources=(user,), request_digest=user.revision_digest, task_id=scope.task_id,
            task_revision=scope.task_revision,
            scope_digest=admission.admission_digest(c.authoring_scope_to_dict(scope)), )
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
        self.assertIsInstance(
            admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=0),)},
            ), admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=None),)},
            ), admission.AuthoringAdmissionNeeded, )
        with self.assertRaisesRegex(ValueError, "unqualified source"):
            admission.qualify_authoring_assessment(_fixture_assessment(foundation_ids=("unoffered",)), **pins)
        material = admission.AdmissionSource(
            "input_material", admission.AdmissionSourceKind.MATERIAL,
            "fixture:nonempty-material", user.revision_digest, "input_only", len(_ADMISSION_BRIEF.encode()),
        )
        material_assessment = _fixture_assessment(foundation_ids=(material.source_id,))
        material_pins = {**pins, "sources": (material, user)}
        self.assertIsInstance(
            admission.qualify_authoring_assessment(material_assessment, **material_pins),
            admission.AuthoringAdmissionBasis, )
        empty_user = admission.AdmissionSource(
            "empty_user", admission.AdmissionSourceKind.USER, "fixture:empty-user",
            "sha256:" + hashlib.sha256(b"").hexdigest(), "literal_user", 0, )
        unknown_user = replace(empty_user, utf8_bytes=None)
        empty_direction = replace( material_assessment, direction=replace(assessment.direction, source_ids=(empty_user.source_id,)), )
        empty_delegation = replace( material_assessment, decisions=(replace(assessment.decisions[0], source_ids=(empty_user.source_id,)),), )
        empty_pins = {**pins, "sources": (material, user, empty_user)}
        unknown_pins = {**pins, "sources": (material, user, unknown_user)}
        self.assertIsInstance(
            admission.qualify_authoring_assessment(empty_direction, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(empty_direction, **unknown_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(empty_delegation, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(empty_delegation, **unknown_pins),
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

    def artifact_bytes(self):
        return tuple(
            (item.path, item.content)
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
        extra = await self.proposal_for(
            self.conversation("Review-only extra null"),
            scoped_proposal(PLOT_ARCHITECT, activity="review", intent="review_only",
                mutate=lambda value, material: value.update(authoring_assessment=None)),
            text="Concrete supplied candidate, with no authoring requested.", )
        self.assertIsNone(extra.proposal)
        self.assertTrue(any(item.response.acceptance is task.LeadPublicResponseAcceptance.REJECTED
                            for item in extra.public_responses))
        self.assertTrue(all(agent == LEAD_ORCHESTRATOR for agent, *_ in self.backend.call_records))
        self.assertEqual((extra.artifact_proposal, extra.artifact_application_request,
                          extra.story_commit_request, extra.recommendation_request,
                          extra.confirmation_request_id), (None,) * 5)
        failed_calls = len(_submitted_calls(self.history(extra.conversation_id)))
        self.backend.enqueue(_visible_outcome(
            "answer", "No authoring work is pending from that rejected request.",))
        cleared = await self.runtime.lead_turn(
            self.session_id, extra.conversation_id, "Report status of this rejected request.", )
        self.assertIs(cleared.kind, c.LeadOutcomeKind.ANSWER)
        self.assertEqual(len(_submitted_calls(self.history(extra.conversation_id))), failed_calls + 1)
        self.assertNotEqual(c.project_collaboration_history(self.history(extra.conversation_id)).next_safe_action,
                            c.CollaborationNextAction.RETRY_LEAD_TURN)
        await self.record_book_readiness(seed_before_capture=True)
        conversation = self.conversation()
        before = self.artifact_bytes()
        premise = "Synthetic tidal city 한글\r\nKeep the stated limit.  "
        source_conversation = self.conversation("Provisional source A")
        source_text = "Discarded prefix.\n" + premise + "\nUnselected ending."
        self.backend.enqueue(*selected_fixture_calls(_current_exploration_outcome, source_text))
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
        self.assertEqual(tuple((call.call_id, call.invocation_kind) for call in _submitted_calls(exploration_prefix)), tuple((call.call_id, c.CollaborationInvocationKind.LEAD_EXPLORATION)
                  for call in self.backend.calls[-2:]))
        self.assertEqual(self.backend.calls[-1].call_id, explored.exploration.provider_call_id)
        def mixed(value, material):
            scope = value["authoring_scope"]
            scope["contributions"] = [{"domain": domain, "role": "draft_owner" if domain == "tone_execution" else "constraint_assessor"} for domain in ("plot_structure", "character_world", "tone_execution")]
            scope["dependency_slots"] = [{"slot_id": "setting", "dependent_domains": ["tone_execution"]}]
            value["proposal"]["shared_premises"] = [{"slot_id": "setting", "selected_premise": premise, "basis_input_ids": ["adopted_idea"]}]
            for assignment in value["proposal"]["assignments"]:
                assignment["input_ids"].append("adopted_idea")
                assignment["mode"] = "draft" if assignment["agent_id"] == EXECUTION_SPECIALIST else "constraint_assessment"
        def recommend(call, provider, *, select=False):
            material = _workflow_material(call.payload)
            draft_reply = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, recommended_action=action(
                    target_paths=(path,), source_ids=("user_input", "adopted_idea", draft_reply))
            , select=select)(call, provider)
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
            evidence_status="incomplete", evidence_ids=lambda call,m:[call.payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent=dict(action="start", target_paths={path: True}, next_step="prepare"),
            authoring_scope=lambda _: c.authoring_scope_to_dict(drafted.proposal.authoring_scope),
            authoring_assessment=asdict(_fixture_assessment(foundation_ids=("adopted_idea",))),)
        def integrate(call, provider, *, select=False):
            return response(call, provider, select=select)
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
        applied = await self.runtime.decide_artifact_application(_application_decision(reviewed.turn,c.ArtifactApplicationDecisionKind.APPLY))
        self.assertTrue(self.workspace.path(path).read_text().startswith("Summons leads"))
        self.assertIsNotNone(applied.commit_request)
        await self.runtime.decide_story_commit(_commit_decision(applied, StoryCommitDecision.REJECT))
        completed = self.runtime.inspect_lead_collaboration(self.session_id, conversation)
        self.assertEqual((completed.rounds_spent, completed.physical_calls_spent), (2, 11))
        submitted = _submitted_calls(self.history(conversation))
        self.assertEqual(len({(item.invocation_id, item.call_id) for item in submitted}), 11)
        for failure, code in ((ConnectionError("Known adoption failure"), c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED), (asyncio.CancelledError(), c.CollaborationLeadOutcomeFailureCode.CANCELLED)):
            self.backend.enqueue(_SubmittedFailure(failure))
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
            self.backend.enqueue(_visible_outcome("answer", "Known failure safely closed."))
            resumed = await self.runtime.lead_turn(self.session_id, conversation, "Report status.")
            self.assertIs(resumed.kind, c.LeadOutcomeKind.ANSWER)
            self.assertEqual(resumed.message, "Known failure safely closed.")
            self.assertEqual(self.history(conversation)[:len(failed_history)], failed_history)
            self.assertEqual(len(_submitted_calls(self.history(conversation))), len(submitted) + 1)
            self.assertEqual((resumed.proposal, resumed.artifact_proposal, resumed.artifact_application_request, resumed.story_commit_request), (None,) * 4)
        history = self.history(conversation)
        self.assertTrue(any(type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED for e in history))
        self.assertEqual(applied.commit_request.application_result_event_id, next(e.event_id for e in history if type(e.payload) is c.ArtifactApplicationResultPayload and e.payload.outcome is c.ArtifactApplicationOutcomeKind.APPLIED))
        offered = []
        def status(call,provider):
            offered.extend(_workflow_material(call.payload)["authoring_tasks"])
            return _visible_outcome("answer","The previous artifact application is recorded.")(call,provider)
        self.backend.enqueue(status)
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report the recorded application status.")
        self.assertNotIn(task_id,{item["task_id"] for item in offered})
        (
            self.backend.enqueue(*selected_fixture_calls(_current_exploration_outcome,
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
        latest = next(e for e in reversed(self.history(source_conversation))
                      if type(e.payload) is c.CollaborationLeadExplorationResultPayload)
        fresh_path = "manuscript/part-01/changed-condition.md"
        fresh_selection = _adoption_selection(source_conversation, latest, (fresh_path,))
        condition_before = self.artifact_bytes()
        def changed_recommend(call, provider, *, select=False):
            material = _workflow_material(call.payload)
            source_id = next(reply["reply_event_id"] for reply in material["replies"] if reply["mode"] == "draft")
            return _synthesis_outcome(
                recommendation_family=1, recommended_action=action(target_paths=(fresh_path,),
                source_ids=("user_input", "adopted_idea", source_id)), select=select)(call,
                provider)
        (fresh,
            fresh_draft,
        ) = await self.finish_draft(
            new_owner, text=_ADOPTION_DIRECTION, response=self._native_scoped(
            *ROSTER,
            paths=(fresh_path,),
            domains=("plot_structure", "character_world", "tone_execution"),
            candidate_ids=("adopted_idea",),
            authoring_assessment=_fixture_assessment(foundation_ids=(
                "adopted_idea",
            )),
        ), adoption=fresh_selection, synthesis=changed_recommend)
        self.assertIsNotNone(fresh.proposal, fresh)
        self.assertNotEqual(fresh.proposal.authoring_scope.task_id, task_id)
        self.assertEqual((fresh.proposal.authoring_scope.task_revision, fresh.proposal.budget.rounds_spent,
                          fresh.proposal.budget.physical_calls_spent), (1, 0, 1))
        changed_response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": fresh_path,
                           "new_text": "The changed tide preserves the current consequence.\n"}],
                           evidence_status="incomplete", evidence_ids=lambda call, material:
                           [call.payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent={"action": "start", "target_paths": {fresh_path: True},
                "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(fresh.proposal.authoring_scope),
            authoring_assessment=asdict(_fixture_assessment(foundation_ids=("adopted_idea",))),)
        def changed_integrate(call, provider, *, select=False):
            return changed_response(call, provider, select=select)
        self.backend.enqueue(changed_integrate)
        prepared = await self.runtime.decide_lead_recommendation(recommendation_decision(fresh_draft.turn))
        self.assertIsNotNone(prepared.proposal, prepared)
        self.assertEqual(prepared.proposal.participants, ROSTER)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome())
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _artifact_synthesis_outcome())
        current_review = await self.runtime.decide_lead_step(_decision(prepared,
                             c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(current_review.turn.artifact_proposal.state, c.ArtifactProposalState.REVIEWED)
        current_frames = [_workflow_material(call.payload) for actor, call in self.backend.call_records
                          if actor in ROSTER][-3:]
        current_candidates = [next(item["artifact_candidate"] for item in frame["inputs"]
                              if "artifact_candidate" in item) for frame in current_frames]
        self.assertEqual(current_candidates, [current_candidates[0]] * len(ROSTER))
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
                         (fresh.proposal.authoring_scope.task_id, 1, "active", 2, 11))
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

    async def test_archived_task_is_context_without_routable_authority(self):
        archived = self.conversation()
        proposal = await self.proposal_for(archived, select=True)
        before = self.history(archived)
        self.runtime.conversation_storage_services.catalog.archive(self.session_id, archived)
        offered=[]
        def answer(call,provider):
            offered.extend(_workflow_material(call.payload)["authoring_tasks"])
            return _visible_outcome("answer", "The archived conversation remains readable history.")(call,provider)
        self.backend.enqueue(answer)
        await self.runtime.lead_turn(self.session_id,self.conversation(),"Report active task status.")
        self.assertNotIn(proposal.proposal.authoring_scope.task_id,{item["task_id"] for item in offered})
        self.assertEqual(self.history(archived),before)

    async def test_old_drafted_revision_cannot_authorize_revised_candidate(self):
        cooffer_profiles = []
        def capture_response(label, producer):
            def respond(call, provider):
                frame = producer(call, provider)
                cooffer_profiles.append((label, call, frame, len(self.backend.finalizers)))
                return frame
            return respond
        def publish_profile(expected):
            label, call, witness, baseline = cooffer_profiles[-1]
            self.assertEqual((label, len(cooffer_profiles)),
                             (expected, 1 if expected == 'ready' else 2))
            self.assertTrue(capture_sample(call, label, witness=witness,
                                          finalizers=self.backend.finalizers[baseline:]))
        readiness = await self.record_book_readiness()
        conversation=self.conversation()
        assessment = asdict(_fixture_assessment())
        from tests.test_lead_artifact_application import _application_decision
        before = self.artifact_bytes()
        path = "manuscript/part-01/revised-candidate.md"
        def two_owners(value, material):
            next(item for item in value["authoring_scope"]["contributions"] if item["domain"] == "tone_execution")["role"] = "constraint_assessor"
            next(item for item in value["proposal"]["assignments"] if item["agent_id"] == EXECUTION_SPECIALIST)["mode"] = "constraint_assessment"
        original, _ = await self.finish_draft(conversation, response=self._native_scoped(
            *ROSTER, domains=("plot_structure", "character_world", "tone_execution"),
            mutate=two_owners, authoring_assessment=_fixture_assessment(),
        ), select=True)
        self.assertIsNone(original.proposal.basis.core_document_readiness)
        old_source = c.project_collaboration_history(self.history(conversation)).offered_draft_sources[-1].event_id
        task_id=original.proposal.authoring_scope.task_id
        drifted = await self.proposal_for(conversation, recommendation(recommended_action=action(source_ids=(
            old_source,), target_paths=(OUTLINE,), specialist_ids=ROSTER),
            authoring_scope=lambda call: dict(next(t["current_scope"] for t in _workflow_material(call.payload)[
            "authoring_tasks"] if t["task_id"] == task_id and t["task_revision"] == 1), domains=["plot_structure",
            "character_world", "tone_execution"], contributions=[{"domain": domain,
            "role": "draft_owner" if domain == "plot_structure" else "constraint_assessor"} for domain in (
            "plot_structure", "character_world", "tone_execution")]),
            mutate=lambda value: value.update(authoring_assessment=assessment),
        ))
        self.assertIsNone(drifted.recommendation_request, drifted)
        self._assert_continuation(drifted, "proposal")
        ready = await self.proposal_for(conversation, capture_response('ready',
            recommendation(recommended_action=action(
            source_ids=(old_source,), target_paths=(path,), specialist_ids=ROSTER),
            authoring_scope=c.authoring_scope_to_dict(original.proposal.authoring_scope),
            mutate=lambda value: value.update(authoring_assessment=assessment),
        )))
        self.assertIsNotNone(ready.recommendation_request, ready)
        publish_profile('ready')
        response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": path, "new_text": "Synthetic chronology remains explicit.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call, material: [call.payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent={"action": "start", "target_paths": {path: True}, "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(original.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def integrate(call, provider, *, select=False):
            return response(call, provider, select=select)
        self.backend.enqueue(capture_response('prepared', integrate))
        review = await self.runtime.decide_lead_recommendation(recommendation_decision(ready))
        self.assertIsNotNone(review.proposal, review)
        self.assertEqual(review.proposal.participants, ROSTER)
        publish_profile('prepared')
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
        direct_review = await self.proposal_for(conversation, scoped_proposal(*ROSTER,
            selected=task_id, revision=2, activity="review", domains=("character_world", "tone_execution"),
            mutate=lambda value, material: value["authoring_scope"].update(contributions=c.authoring_scope_to_dict(
                original.proposal.authoring_scope)["contributions"]),
            authoring_assessment=_fixture_assessment(),
        ))
        self._assert_continuation(direct_review, "proposal")
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in self.history(conversation)[len(completed_prefix):]))
        def successor(value, material):
            self.assertEqual([(t["task_id"], t["task_revision"]) for t in material["authoring_tasks"]], [(task_id, 2)])
            self.assertEqual(material["available_proposal_drafting_source_ids"], [])
        (pending, revised,) = await self.finish_draft(conversation, response=self._native_scoped(
            PLOT_ARCHITECT,
            domains=("plot_structure",), selected=task_id,
            revision=2,
            mutate=successor,
            authoring_assessment=_fixture_assessment(),
        ), select=True)
        self.assertIsNotNone(pending.proposal, pending)
        self.assertEqual(pending.proposal.authoring_scope.task_id, task_id)
        self.assertIs(revised.turn.state, c.CollaborationStepState.COMPLETED)
        self.assertEqual(revised.proposal.authoring_scope.task_revision,2)
        self.assertEqual(self.history(conversation)[:len(completed_prefix)], completed_prefix)
        def cross_product(value,material):
            offered=material["authoring_tasks"]
            self.assertTrue(any(t["task_revision"]==2 for t in offered))
            self.assertEqual(value["authoring_scope"]["task_revision"],1)
            value["authoring_scope"]["task_revision"]=2
        invalid=await self.proposal_for(self.conversation(),scoped_proposal(PLOT_ARCHITECT,mutate=cross_product,
            authoring_assessment=_fixture_assessment(),
        ))
        self._assert_continuation(invalid, 'proposal')
        multiple = self.conversation()
        before_multiple = self.artifact_bytes()
        multi_path = "manuscript/part-01/nonfirst-successor.md"
        first_draft, _ = await self.finish_draft(multiple, response=self._native_scoped(
            *ROSTER, domains=("plot_structure", "character_world", "tone_execution"),
            mutate=two_owners, authoring_assessment=_fixture_assessment(),
        ), select=True)
        first_source = c.project_collaboration_history(self.history(multiple)).offered_draft_sources[-1].event_id
        def second_task(call, provider):
            fresh = next(t for t in _workflow_material(call.payload)["authoring_tasks"] if t.get("current_scope") is None)
            return self._native_scoped(*ROSTER,
                domains=("plot_structure", "character_world", "tone_execution"),
                selected=fresh["task_id"], mutate=two_owners,
                authoring_assessment=_fixture_assessment(),)(call, provider)
        second_draft, _ = await self.finish_draft(multiple, response=second_task, select=True)
        first_ready = await self.proposal_for(multiple, recommendation(recommended_action=action(
            source_ids=(first_source,), target_paths=(multi_path,), specialist_ids=ROSTER),
            authoring_scope=c.authoring_scope_to_dict(first_draft.proposal.authoring_scope),
            mutate=lambda value: value.update(authoring_assessment=assessment),
        ))
        self.assertIsNotNone(first_ready.recommendation_request, first_ready)
        first_response = _artifact_outcome(operations=[{"kind": "create", "suggested_path": multi_path, "new_text": "Synthetic chronology remains explicit.\n"}],
            evidence_status="incomplete", evidence_ids=lambda call, material: [call.payload["core_document_statuses"][0]["evidence_id"]],
            objective_intent={"action": "start", "target_paths": {multi_path: True},
                "next_step": "prepare"},
            authoring_scope=lambda _: c.authoring_scope_to_dict(
                first_draft.proposal.authoring_scope),
            authoring_assessment=assessment,)
        def integrate_multiple(call, provider, *, select=False):
            return first_response(call, provider, select=select)
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
        expected = [(item.proposal.authoring_scope.task_id, 2) for item in (first_draft, second_draft)]
        chosen = []
        def nonfirst(call, provider):
            offers = [(t["task_id"], t["task_revision"]) for t in _workflow_material(call.payload)["authoring_tasks"]]
            self.assertCountEqual(offers, expected)
            chosen.append(offers[1])
            return self._native_scoped(*ROSTER, selected=offers[1][0], revision=offers[1][1],
                domains=("plot_structure", "character_world", "tone_execution"), mutate=two_owners,
                authoring_assessment=_fixture_assessment(),
            )(call, provider)
        admitted = await self.proposal_for(multiple, nonfirst)
        self.assertIs(admitted.kind, c.LeadOutcomeKind.PROPOSAL, admitted)
        self.assertEqual((admitted.proposal.authoring_scope.task_id, admitted.proposal.authoring_scope.task_revision), chosen[0])
        suffix = self.history(multiple)[len(multi_prefix):]
        fresh_admission = next(e.payload.admission for e in suffix if type(e.payload) is c.CollaborationPremiseFreshWorkAdmissionPayload)
        self.assertCountEqual([(t.task_id, t.task_revision) for t in fresh_admission.offered_authoring_tasks], expected)
        self.assertIsNone(fresh_admission.selected_authoring_task)
        self.assertEqual(fresh_admission.front_physical_call_limit, 2)
        self.assertEqual(len(_submitted_calls(suffix)), 1)
        self.assertFalse(any(e.kind is c.CollaborationEventKind.ASSIGNMENT_DISPATCH for e in suffix))
        self.assertEqual(self.history(multiple)[:len(multi_prefix)], multi_prefix)
        self.assertEqual(self.artifact_bytes(), before_multiple)
        self.workspace.path(OUTLINE).unlink()
        def stale(call, provider):
            self.assertIn(old_source, _workflow_material(call.payload)["available_proposal_drafting_source_ids"])
            current = next(t["current_scope"] for t in _workflow_material(call.payload)["authoring_tasks"] if t["task_id"] == task_id and t["task_revision"] == 2)
            self.assertEqual((current["task_id"], current["task_revision"]), (task_id, 2))
            return recommendation(message="Exclude the earlier revision draft.",
                recommended_action=action(source_ids=(old_source,), target_paths=(OUTLINE,),
                specialist_ids=(PLOT_ARCHITECT,)), authoring_scope=current,
                mutate=lambda value: value.update(authoring_assessment=assessment),
            )(call, provider)
        self.backend.enqueue(stale)
        rejected=await self.runtime.lead_turn(self.session_id,conversation,
            _ADMISSION_BRIEF + "\nUse the earlier draft for this revised candidate.")
        self.assertIsNone(rejected.recommendation_request, rejected)
        self._assert_continuation(rejected, 'artifact_proposal')

        self.session_id = "historical_input_session"
        backend = AgentBackend(self.control_workspace)
        self.addCleanup(backend.close)
        resources = backend.provision_session_resources(self.session_id)
        self.workspace = type(self.workspace)(resources.identity.worktree_root).require_ready()
        before = self.artifact_bytes()
        prior = self.conversation("Historical drafting input remains separate from fresh review proof")
        first, _ = await self.finish_draft(prior, select=True)
        original_history = self.history(prior)
        source = c.project_collaboration_history(original_history).offered_draft_sources[-1]
        fresh_scope = replace(first.proposal.authoring_scope, task_revision=2)
        view = self.runtime.inspect_lead_collaboration(self.session_id, prior)
        selected_task = next(item for item in view.available_tasks if item.ref.task_id == fresh_scope.task_id)
        source_conversation = self.conversation("Same-session foreign public message")
        invalid = json.loads(_discussion_reply("Selected rejected whole explanation."))
        invalid["agent_action"]["task_result"]["used_evidence_ids"] = "invalid array"
        self.backend.enqueue(json.dumps(invalid), json.dumps(invalid))
        discussed = await self.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            self.session_id, source_conversation, task.LeadTaskAction.DISCUSS, text="Discuss independently.", ))
        rejected_text = discussed.turn.public_responses[-1]
        self.assertIs(rejected_text.response.acceptance, task.LeadPublicResponseAcceptance.REJECTED)
        self.assertEqual(rejected_text.ref.conversation_id, source_conversation)
        self.assertEqual(c.project_collaboration_history(self.history(source_conversation)).offered_draft_sources, ())
        self.assertEqual(c.project_collaboration_history(self.history(prior)).offered_draft_sources[-1], source)
        self.assertEqual(self.history(prior)[:len(original_history)], original_history)
        original_history = self.history(prior)
        self.backend.enqueue(recommendation(message=fresh_scope.interpretation,
            recommended_action=action(
            kind="prepare_consultation", source_ids=(source.event_id,)),
                authoring_scope=c.authoring_scope_to_dict(fresh_scope),
            authoring_assessment=_fixture_assessment(foundation_ids=("user_input", "selected_material_1")),
        ))
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
        def prepare_historical(call, provider, *, select=False):
            material = _workflow_material(call.payload)
            self.assertEqual(material["structured_recommendation_execution"]["request"]["authoring_scope"], c.authoring_scope_to_dict(fresh_scope))
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
            return lead_fixture_response(call, lead_fixture_result(native), family=0, select=select)
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
        self.assertEqual((capture.payload.capture.target_task_id, capture.payload.capture.target_task_revision),
                         (fresh_scope.task_id, fresh_scope.task_revision))
        front = next(_workflow_material(call.payload) for agent, call in reversed(self.backend.call_records)
                     if agent == LEAD_ORCHESTRATOR and _workflow_material(call.payload).get("selected_task_materials"))
        self.assertEqual(front["selected_task_materials"], [{"input_id": selected_input.input_id,
                         "capture_event_id": capture.event_id, **task.task_value_to_dict(capture.payload.capture)}])
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
        ))
        def integrate_fresh(call, provider, *, select=False):
            return _artifact_outcome(
                operations=lambda call, material: [{"kind": "replace",
                    "evidence_id": _status_id(call, OUTLINE), "new_text": DRAFT}],
                evidence_status="incomplete",
                evidence_ids=lambda call, material: [
                    call.payload["core_document_statuses"][0]["evidence_id"]],
                objective_intent={"action": "start", "target_paths": {OUTLINE: True},
                    "next_step": "prepare"},
                authoring_scope=lambda call: _workflow_material(call.payload)[
                    "structured_recommendation_execution"]["request"]["authoring_scope"],
                authoring_assessment=assessment,
             select=select)(call, provider)
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
        self.backend.enqueue(_discussion_reply("This answer grants no artifact action."))
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
        fresh=await self.proposal_for(conversation, select=True)
        self.assertIsNotNone(fresh.proposal.authoring_scope)
        self.assertNotEqual(fresh.confirmation_request_id,pending.confirmation_request_id)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation)[:len(history)]),before)
