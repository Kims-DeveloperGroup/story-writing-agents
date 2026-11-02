"""Canonical contextual objective history, with real storage and fake providers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents import StoryRuntime, Workspace
from story_writing_agents import conversations as c
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests import test_lead_orchestration as lead
from tests.provider_wire_fixture import selection_plan
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit
from tests.helpers import pristine_initialized_workspace

OUTLINE = "core_documents/global_outline.md"
DRAFT = "1. A lantern vanishes.\n2. 민 finds its keeper.\n3. The lantern returns.\n[A641 synthetic]\n"


def digest(text: str | bytes) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def rewrite_history(events, event_id, **changes):
    """Change a semantic fact and re-sign its batch so lineage is what fails."""
    updated = tuple(replace(e, **changes) if e.event_id == event_id else e for e in events)
    result = []
    offset = 0
    while offset < len(updated):
        group = updated[offset:offset + updated[offset].batch_size]
        batch = c.CollaborationEventBatch(group[0].batch_id, offset, tuple(e.to_draft() for e in group))
        result.extend(replace(e, batch_digest=batch.batch_digest) for e in group)
        offset += len(group)
    return tuple(result)


def _native_objective_response(call, *, response_type, message, scope, update, branch, selection_group=None):
    from tests.provider_wire_fixture import ( lead_fixture_bound_scope, lead_fixture_result, lead_fixture_response)
    fields = {'task_scope': lead_fixture_bound_scope(call, scope, record=True),
              'response_type': response_type, 'response_text': message,'document_change_request_update': update}
    return lead_fixture_response(call, lead_fixture_result(fields), family=0,
        branch_key=branch if selection_group is None else branch + ":group:" + selection_group)


class ObjectiveCase(lead.LeadOrchestrationTestCase):
    """Shared setup only; importing it does not duplicate concrete test IDs."""

    def setUp(self):
        main = pristine_initialized_workspace(self)
        self.control_workspace = main
        self.session_id = "story_session"
        ready = provision_session_resources(main.root, self.session_id)
        self.session_resources = ready
        self.workspace = Workspace(ready.identity.worktree_root).require_ready()
        self.backend = lead._LeadBackend(persist_context_threads=True, vendor="fixture", test_case=self)
        self.runtime = StoryRuntime(self.control_workspace, self.backend)
        self.objective_tasks = {}

    def projection(self, conversation_id):
        return c.project_collaboration_history(self.history(conversation_id))

    def objective(self, conversation_id):
        return self.projection(conversation_id).artifact_objectives[-1]

    def snapshots(self):
        return (
            tuple( (a.path, a.content) for a in WritingArtifactRepository( self.workspace.root ).capture_complete_state() ),
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.workspace.root),
            subprocess.check_output(["git", "diff", "--cached", "--binary"], cwd=self.workspace.root),
        )

    def question(self, *, intent=None, message=DRAFT, native_update=None, selection_group=None):
        def respond(call, _provider):
            if native_update is not None:
                return _native_objective_response(call, response_type='clarification_question', message=message,
                    scope={**self.scope(call, intent=intent, activity='draft'), 'domains': ['plot_structure']},
                    update=native_update, branch='lead-response-v8:family:1', selection_group=selection_group)
            return lead._provider_envelope(call, {"recommended_action": None,
                "scope": "story_specific", "evidence_status": "empty",
                "evidence_ids": [artifact._status_id(_provider.callback_payload(call), OUTLINE)],
                "kind": "question", "message": message, "proposal": None,
                "artifact_intent": None, "objective_intent": intent,
                "authoring_scope": self.scope(call, intent=intent, activity="draft"),
            })
        return respond

    def scope(self, call, *, intent=None, activity="review"):
        tasks = lead._workflow_material(self.backend.callback_payload(call))["authoring_tasks"]
        task_id = self.objective_tasks.get((intent or {}).get("objective_id"))
        index = next((i for i, task in enumerate(tasks) if task["task_id"] == task_id), -1)
        return lead._live_scope(call, activity=activity, paths=(OUTLINE,), task_index=index)

    async def start(self, conversation_id=None, *, message=DRAFT, objective_id=None,
                    target_path=OUTLINE, specialist_ids=(PLOT_ARCHITECT,), authoring_paths=None,
            native_continuation=False, native_inputs=None, response_text=None):
        from tests.test_specialist_routing_runtime import scoped_proposal
        from tests.recommendation_fixture import task_assessment

        native_start = (specialist_ids == (PLOT_ARCHITECT,)
                        and objective_id is None and target_path == OUTLINE and authoring_paths is None)
        conversation_id = conversation_id or self.conversation()
        paths = () if target_path is None else (target_path,)
        intent = {
            "action": "start", "request": "Prepare the approved synthetic outline.",
            "target_paths": list(paths), "next_step": "consult", "draft_source_event_ids": [], }
        if objective_id is not None:
            intent = {"action": "continue", "objective_id": objective_id,
                      "next_step": "consult", "draft_source_event_ids": []}
        elif target_path is None:
            intent = None
        def respond(call, provider):
            native = (native_inputs(call) if native_inputs is not None else {
                'input_choices': ('user_input',), 'anchors': {PLOT_ARCHITECT: 'user_input'},
                'recipients': {'user_input': PLOT_ARCHITECT},
                'source_choices': ('user_input',), 'family': 0}
                if (native_start or native_continuation) else None)
            if native_start and native_inputs is None:
                choices = dict(call.contract.semantic_index.policy.choices)
                self.assertEqual(tuple(choices['required_input_ids']), ('user_input',))
                native.update(input_choices=choices['input_ids'], source_choices=choices['input_ids'])
            def mutate(value, material):
                if native_start:
                    value['proposal'].update(objective_intent={
                        'action': 'start', 'target_paths': list(paths), 'next_step': 'consult'})
                elif native_continuation:
                    value['proposal'].update(objective_intent=lead._compact_objective(intent))
                elif native_inputs is None or intent is not None:
                    value.update(objective_intent=intent)
                if response_text is not None:
                    value['message'] = response_text
            return scoped_proposal(
                *specialist_ids, native_inputs=native,
                paths=authoring_paths if authoring_paths is not None else paths,
                domains=(('plot_structure',) if (native_start or native_continuation) else ()
                    if target_path is not None and specialist_ids == (PLOT_ARCHITECT,) else tuple(
                    {'plot_architect': 'plot_structure', 'character_custodian': 'character_world',
                     'execution_specialist': 'tone_execution'}[agent] for agent in specialist_ids)),
                selected=self.objective_tasks.get(objective_id), mutate=mutate,
                authoring_assessment=task_assessment(foundation_ids=('user_input',),
                    actions=('scene transitions', 'supporting dialogue')))(call, provider)
        self.backend.enqueue(respond,
            control_plan=selection_plan('consultation.authoring.content'))
        proposed = await self.runtime.lead_turn(self.session_id, conversation_id,
            ((f"Draft {target_path or 'a synthetic scene'} from this brief: a lantern vanishes, its keeper is found, and it returns." + " Invent scene transitions and supporting dialogue; preserve the supplied brief facts."
            + (" Explicit authoring paths: " + ", ".join(authoring_paths) if authoring_paths is not None else ""))))
        self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, proposed)
        self.assertEqual(proposed.proposal.participants, specialist_ids)
        if intent is not None:
            self.objective_tasks[self.objective(conversation_id).facts.objective_id] = proposed.proposal.authoring_scope.task_id
        for agent in specialist_ids:
            self.backend.enqueue_for(agent, lead._specialist_outcome("drafted", message))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=message))
        completed = await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        turn = completed.turn
        self.assertIs(turn.state, c.CollaborationStepState.COMPLETED, turn)
        self.assertEqual(turn.message, message + "\n\n"
            "Draft material is available for Lead integration and concrete review. "
            "Story authoring is not complete; no Apply is authorized.")
        synthesis = next(e for e in reversed(self.history(conversation_id))
                         if isinstance(e.payload, c.CollaborationLeadSynthesisPayload))
        self.assertEqual(synthesis.payload.message, message)
        source = self.projection(conversation_id).offered_draft_sources[-1]
        self.assertEqual(source.event_id, synthesis.event_id)
        self.assertEqual(source.content_digest, digest(message))
        return conversation_id, turn

    async def clarify(self, conversation_id=None, *, message="Which consequence should close the outline?"):
        conversation_id = conversation_id or self.conversation()
        self.backend.enqueue(self.question(
                intent={'action': 'start',
                    'request': 'Prepare the approved synthetic outline.','target_paths': [OUTLINE],
                    'next_step': 'clarify',
                    'draft_source_event_ids': [],}, message=message,
                native_update={'update_type': 'start_request',
                    'target_artifact_paths': [OUTLINE],'requested_workflow_step': 'ask_clarification'}, selection_group='question.authoring'),
                             control_plan=selection_plan("question.authoring"))
        turn = await self.runtime.lead_turn(self.session_id, conversation_id, f"Help me clarify the ending before drafting {OUTLINE}.")
        self.assertIs(turn.kind, c.LeadOutcomeKind.QUESTION)
        judgment = next(e.payload for e in reversed(self.history(conversation_id))
                        if isinstance(e.payload, c.CollaborationLeadJudgmentPayload))
        self.objective_tasks[self.objective(conversation_id).facts.objective_id] = judgment.authoring_scope.task_id
        return conversation_id, turn

    async def prepare(self, conversation_id, *, text=DRAFT, select=True, new=False, attached=False):
        if new:
            await self.start(conversation_id, message=text, native_continuation=True)
        objective = self.objective(conversation_id)
        source = self.projection(conversation_id).offered_draft_sources[-1]
        from tests.recommendation_fixture import action, recommendation, decision, task_assessment
        scope = next(event.payload.authoring_scope for event in self.history(conversation_id) if event.event_id == source.event_id)
        self.assertEqual((scope.schema_version, scope.activity), (3, lead.AuthoringActivity.DRAFT))
        intent = {"action": "continue", "objective_id": objective.facts.objective_id,
                  "next_step": "prepare", "draft_source_event_ids": [source.event_id] if select else []}
        evidence = (lambda call, material, *, input_payload: artifact._reference_ids(material)) if attached else (
            lambda call, _material, *, input_payload: [artifact._status_id(input_payload, OUTLINE)])
        def offer_current_task(call, provider):
            sources = ((source.event_id,) if select else ("user_input",)) + (
                tuple(evidence(call, lead._workflow_material(provider.callback_payload(call)), input_payload=provider.callback_payload(call))) if attached else ())
            return recommendation(message=scope.interpretation, authoring_scope=c.authoring_scope_to_dict(scope),
                recommended_action=action(source_ids=sources, objective_id=objective.facts.objective_id, target_paths=objective.facts.target_paths),
                authoring_assessment=task_assessment(foundation_ids=("user_input",), actions=('candidate preparation', 'separate proposal preparation')))(call, provider)
        self.backend.enqueue(offer_current_task,
            control_plan=selection_plan('prepare.artifact'))
        prompt = f"Prepare the next revision using ${{{OUTLINE}}}." if attached else "네, 그 초안 그대로 문서로 준비해 줘."
        offered = await self.runtime.lead_turn(self.session_id, conversation_id, ("Finished candidate:\n" + text + "\nYou may prepare this candidate and a separate proposal; preserve supplied facts.\n" + prompt))
        self.assertIs(offered.kind, c.LeadOutcomeKind.RECOMMENDATION, offered)
        prepare_current_task = artifact._artifact_outcome(operations=lambda call, material, *, input_payload: [{"kind": "replace",
                "evidence_id": evidence(call, material, input_payload=input_payload)[0], "new_text": text}],
            evidence_status='verified' if attached else 'empty', evidence_ids=evidence,
            objective_intent=intent,authoring_scope=c.authoring_scope_to_dict(scope),
            authoring_assessment=asdict(
                offered.recommendation_request.basis.authoring_admission.assessment),)
        self.backend.enqueue(prepare_current_task)
        result = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIs(result.kind, c.LeadOutcomeKind.PROPOSAL, result)
        self.assertIsNotNone(result.artifact_proposal)
        self.assertEqual(result.proposal.budget.rounds_spent, 1)
        self.assertLessEqual(offered.recommendation_request.physical_calls_spent + offered.recommendation_request.max_preparation_physical_calls +
            (len(result.proposal.participants) + 1) * (1 + int(attached)), 12)
        return result

    async def review(self, proposed):
        for assignment in proposed.proposal.assignments:
            self.backend.enqueue_for(assignment.specialist_id, lead._specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, artifact._artifact_synthesis_outcome("propose"))
        result = await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        self.assertIsNotNone(result.turn.artifact_application_request, result.turn)
        return result.turn

    async def apply(self, reviewed, kind=c.ArtifactApplicationDecisionKind.APPLY):
        return await self.runtime.decide_artifact_application(application._application_decision(reviewed, kind))

    async def reject_commit(self, applied):
        return await self.runtime.decide_story_commit(commit._commit_decision(applied, commit.StoryCommitDecision.REJECT))


class ArtifactObjectiveContractTests(ObjectiveCase):
    async def test_intent_variants_and_objective_values_are_closed_and_content_safe(self):
        conversation_id, _ = await self.start()
        facts = self.objective(conversation_id).facts
        self.assertNotIn(facts.request, repr(facts))
        self.assertEqual(tuple(c.LeadObjectiveAction), (c.LeadObjectiveAction.START, c.LeadObjectiveAction.CONTINUE, c.LeadObjectiveAction.CANCEL))
        valid = c.LeadObjectiveIntent(c.LeadObjectiveAction.START, request="Synthetic", target_paths=(OUTLINE,), next_step=c.ArtifactObjectiveNextStep.CLARIFY)
        for change in ({"action": "start"}, {"target_paths": ()}, {"target_paths": ("../escape.md",)}, {"target_paths": (OUTLINE, OUTLINE)}, {"objective_id": "provider_owned"}):
            with self.subTest(change=change), self.assertRaises((TypeError, ValueError)):
                replace(valid, **change)
        continuation = c.LeadObjectiveIntent(c.LeadObjectiveAction.CONTINUE, objective_id=facts.objective_id, next_step=c.ArtifactObjectiveNextStep.PREPARE)
        with self.assertRaises(ValueError):
            replace(continuation, request="Rewrite immutable origin")
        with self.assertRaises(ValueError):
            c.LeadObjectiveIntent(c.LeadObjectiveAction.CANCEL, objective_id=facts.objective_id, next_step=c.ArtifactObjectiveNextStep.CLARIFY)

    async def test_v1_objective_wire_round_trips_without_changing_legacy_bytes(self):
        conversation_id, _ = await self.start()
        events = self.history(conversation_id)
        event = next(e for e in events if isinstance(e.payload, c.ArtifactObjectivePayload))
        wire = c.encode_collaboration_event(event)
        self.assertIn(b'"payload_type":"artifact_objective_v1"', wire)
        self.assertEqual(c.decode_collaboration_event(wire), event)
        self.assertEqual(c.encode_collaboration_event(c.decode_collaboration_event(wire)), wire)
        original = tuple(c.encode_collaboration_event(e) for e in events)
        await self.prepare(conversation_id)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation_id)[:len(events)]), original)
        value = json.loads(wire)
        value["payload"]["extra_authority"] = True
        with self.assertRaises((TypeError, ValueError)):
            c.decode_collaboration_event(json.dumps(value).encode())

    async def test_history_rejects_foreign_or_changed_origin_predecessor_sources_and_targets(self):
        conversation_id, _ = await self.start()
        await self.prepare(conversation_id)
        events = self.history(conversation_id)
        transition = [e for e in events if isinstance(e.payload, c.ArtifactObjectivePayload)][-1]
        payload = transition.payload
        bad_payloads = [replace(payload, previous_objective_event_id="not_the_predecessor"),
            replace(payload, facts=replace(payload.facts, origin_user_event_id="foreign_user")),
            replace(payload, facts=replace(payload.facts, request="changed origin")),
            replace(payload, facts=replace(payload.facts, target_paths=("core_documents/narrative_rules.md",))),
            replace(payload, draft_sources=(replace(payload.draft_sources[0], content_digest=digest("altered")),)),
            replace(payload, candidate_binding=replace(payload.candidate_binding, candidate_event_id="foreign_candidate")),
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                c.project_collaboration_history(rewrite_history(events, transition.event_id, payload=payload))
        with self.assertRaises(ValueError):
            replace(transition.payload.facts, offered_draft_sources=(c.ArtifactDraftSource("another_conversation", "draft", digest(DRAFT)),))

    async def test_question_and_synthesis_sources_preserve_exact_whole_message_digests(self):
        message = "초안\r\n" + DRAFT + "\nApprove this whole message?"
        question = "확인\r\nWhich consequence should close the outline?"
        conversation_id, _ = await self.clarify(message=question)
        question_event = next(e for e in reversed(self.history(conversation_id))
                              if isinstance(e.payload, c.CollaborationLeadJudgmentPayload))
        historical = rewrite_history(self.history(conversation_id), question_event.event_id,
            payload=replace(question_event.payload, authoring_scope=None))
        historical_wire = c.encode_collaboration_event(historical[question_event.sequence - 1])
        self.assertNotIn(b'"authoring_scope"', historical_wire)
        self.assertEqual(c.encode_collaboration_event(c.decode_collaboration_event(historical_wire)), historical_wire)
        self.assertEqual(c.project_collaboration_history(historical).offered_draft_sources[-1].content_digest, digest(question))
        await self.start(conversation_id, message=message,
            objective_id=self.objective(conversation_id).facts.objective_id, native_continuation=True)
        sources = self.projection(conversation_id).offered_draft_sources
        self.assertEqual(sources[-1].content_digest, digest(message))
        self.assertNotEqual(sources[-1].content_digest, digest(message.replace("\r\n", "\n")))
        prepared = await self.prepare(conversation_id, text=message)
        reviewed = await self.review(prepared)
        events = self.history(conversation_id)
        syntheses = [e for e in events if isinstance(e.payload, c.CollaborationLeadSynthesisPayload)]
        self.assertTrue(syntheses)
        source_by_id = {s.event_id: s for s in self.projection(conversation_id).offered_draft_sources}
        for event in syntheses:
            self.assertEqual(source_by_id[event.event_id].content_digest, digest(event.payload.message))
        self.assertIsNotNone(reviewed.artifact_application_request)
        self.assertNotIn(prepared.turn_id, source_by_id)

    async def test_empty_continuation_retains_selection_and_cancel_is_terminal(self):
        from story_writing_agents import orchestrator as orchestration
        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        factory, enqueue = orchestration._lead_call_contract_factory, self.backend.enqueue
        source_front_flags, source_enqueues = [], []
        def source_factory(**values):
            if values["stage"] == "lead.front":
                source_front_flags.append(values["schema_group_protocol"])
                if values["schema_group_protocol"]:
                    self.assertIn(len(source_front_flags), (1, 2, 4))
                    values = {**values, "schema_group_protocol": False}
                else:
                    self.assertIn(len(source_front_flags), (3, 5))
            return factory(**values)
        def source_enqueue(*responses, **values):
            ordinal = len(source_enqueues)
            source_enqueues.append(ordinal)
            if ordinal in (0, 1, 3):
                self.assertEqual(tuple((cursor, phase) for cursor, phase, _response in values["control_plan"]),
                                 ((0, LeadGroupPhase.SELECT),))
                values = {**values, "control_plan": ()}
            else:
                self.assertIn(ordinal, (2, 4))
                self.assertNotIn("control_plan", values)
            return enqueue(*responses, **values)
        source_call_start = len(self.backend.calls)
        with (mock.patch.object(orchestration, "_lead_call_contract_factory", new=source_factory),
              mock.patch.object(self.backend, "enqueue", new=source_enqueue)):
            conversation_id, _ = await self.start()
            self.assertEqual(len(self.backend.calls) - source_call_start, 3)
            first = await self.prepare(conversation_id)
            self.assertEqual(len(self.backend.calls) - source_call_start, 5)
            selected = self.objective(conversation_id).draft_sources
            await self.runtime.decide_lead_step(lead._decision(first, c.CollaborationConfirmationDecision.REJECT))
            await self.prepare(conversation_id, select=False)
            self.assertEqual(len(self.backend.calls) - source_call_start, 7)
            self.assertEqual(self.objective(conversation_id).draft_sources, selected)
        self.assertIs(orchestration._lead_call_contract_factory, factory)
        self.assertEqual(self.backend.enqueue, enqueue)
        self.assertEqual(source_front_flags, [True, True, False, True, False])
        self.assertEqual(source_enqueues, [0, 1, 2, 3, 4])
        self.assertEqual(tuple(self.backend.calls[source_call_start + index].contract.schema_group_phase
                               for index in (0, 3, 4, 5, 6)), (None,) * 5)
        objective_id = self.objective(conversation_id).facts.objective_id
        self.backend.enqueue(lambda call, _provider: _native_objective_response(call, response_type='answer',
            message='Objective cancelled.', scope=lead._live_scope(call),
            update={'update_type': 'cancel_request', 'document_change_request_id': objective_id},
            branch='lead-response-v8:family:0', selection_group='answer.workflow'),
                             control_plan=selection_plan("answer.workflow"))
        cancel_call_start = len(self.backend.calls)
        cancel_event_start = len(self.history(conversation_id))
        cancelled = await self.runtime.lead_turn(self.session_id, conversation_id, "Cancel this writing objective.")
        selects = [call for call in self.backend.calls[cancel_call_start:]
                   if call.contract.group_workflow is not None and call.contract.group_workflow.phase.value == "select"]
        current = selects[-1] if selects else None
        diagnostic = None if current is None else {
            "catalog_group_ids": current.contract.group_workflow.catalog.group_ids,
            "result_contract_id": current.payload["response_contract"]["result_contract_id"],
            "remaining_provider_calls": current.contract.offers.remaining_provider_calls,
        }
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.CANCELLED, diagnostic)
        self.assertEqual(tuple(call.contract.schema_group_phase for call in self.backend.calls[cancel_call_start:]),
                         (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE))
        self.assertEqual(len(lead._submitted_calls(self.history(conversation_id)[cancel_event_start:])), 2)
        before = self.objective(conversation_id)
        bad = self.question(intent={"action": "continue", "objective_id": objective_id, "next_step": "clarify", "draft_source_event_ids": []})
        self.backend.enqueue(bad, control_plan=selection_plan("question.authoring"))
        self.backend.enqueue(bad, control_plan=())
        await self.runtime.lead_turn(self.session_id, conversation_id, "Try the closed ID.")
        self.assertEqual(self.objective(conversation_id), before)

    async def test_projection_retains_satisfaction_across_later_apply_commit_cycles(self):
        conversation_id, _ = await self.start()
        applied = await self.apply(await self.review(await self.prepare(conversation_id)))
        satisfied = self.objective(conversation_id)
        self.assertIs(satisfied.state, c.ArtifactObjectiveState.SATISFIED)
        await self.reject_commit(applied)
        next_review = await self.review(await self.prepare(conversation_id, new=True, attached=True, text=DRAFT + "Second revision.\n"))
        await self.apply(next_review, c.ArtifactApplicationDecisionKind.REJECT)
        self.assertEqual(self.projection(conversation_id).artifact_objectives[0], satisfied)
        self.assertIs(self.objective(conversation_id).state, c.ArtifactObjectiveState.OPEN)

    async def test_objective_history_round_trips_through_filesystem_sqlite_and_archive(self):
        from story_writing_agents.conversation_storage import ConversationStorageBackend, ConversationStorageCapability, WorkspaceConversationStorageMutationCoordinator
        from story_writing_agents.conversation_storage.migration import transfer_conversation_storage, ConversationStorageTransferOutcome
        from story_writing_agents.conversation_storage.sqlite import provision_sqlite_conversation_storage, compose_sqlite_conversation_storage
        from story_writing_agents.conversation_storage.archive import serialize_conversation_archive, deserialize_conversation_archive
        from tests.conversation_archive_fixture import archive_conversation, archive_from_conversations
        conversation_id, _ = await self.start()
        await self.prepare(conversation_id)
        before = self.history(conversation_id)
        archive = archive_from_conversations(archive_conversation(session_id=self.session_id, conversation_id=conversation_id, events=before))
        wire = serialize_conversation_archive(archive)
        self.assertEqual(serialize_conversation_archive(deserialize_conversation_archive(wire)), wire)
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.control_workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            result = provision_sqlite_conversation_storage(self.control_workspace, lease=lease)
        self.assertIs(result.capability, ConversationStorageCapability.READY)
        transferred = transfer_conversation_storage(self.control_workspace, source_backend=ConversationStorageBackend.FILESYSTEM, destination_backend=ConversationStorageBackend.SQLITE)
        self.assertIs(transferred.outcome, ConversationStorageTransferOutcome.COMPLETED)
        services = compose_sqlite_conversation_storage(self.control_workspace)
        after = services.event_store.read(self.session_id, conversation_id)
        self.assertEqual(after, before)
        self.assertEqual(tuple(c.encode_collaboration_event(e) for e in after), tuple(c.encode_collaboration_event(e) for e in before))
        self.assertEqual(c.project_collaboration_history(after), self.projection(conversation_id))

    async def test_readable_and_session_context_include_objectives_without_new_records(self):
        from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
        conversation_id, _ = await self.start()
        before = self.history(conversation_id)
        readable = c.project_collaboration_readable_history(before)
        objective_record = next(item for item in readable if isinstance(item.payload, c.ArtifactObjectivePayload))
        self.assertEqual({field.key: field.text for field in objective_record.fields}, {
            c.CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_REQUEST: (
                "Draft core_documents/global_outline.md from this brief: a lantern "
                "vanishes, its keeper is found, and it returns. Invent scene transitions and supporting dialogue; preserve the supplied brief facts."
            ),
            c.CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_ACTION: "start",
            c.CollaborationReadableFieldKey.ARTIFACT_OBJECTIVE_NEXT_STEP: "consult",
        })
        services = self.runtime.conversation_storage_services
        snapshot = SessionHistorySourceAdapter(services.catalog, services.event_store, self.session_id).capture()
        value = snapshot.delivery()
        self.assertIn(self.objective(conversation_id).facts.objective_id, json.dumps(value, default=str))
        self.assertNotIn("apply_authorized", json.dumps(c.artifact_objective_context(self.projection(conversation_id))))
        self.assertEqual(self.history(conversation_id), before)
