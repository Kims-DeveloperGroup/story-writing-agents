"""Permanent execution acceptance coverage for confirmed specialist collaboration."""
from __future__ import annotations
from tests.known_result_finalization_fixture import finalize_known_result
import asyncio
import json
import unittest
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest import mock
from story_writing_agents.workspace import initialization as workspace_initialization
from story_writing_agents import ReplyTraceOrigin, StoryRuntime, agents, conversations
from story_writing_agents import lead_provider_contracts, specialist_routing, structured_output_validation
from story_writing_agents.conversation_storage import ConversationStorageServices
from story_writing_agents.errors import BackendProtocolError
from tests import test_lead_orchestration as lead
from tests import test_specialist_orchestration_accounting as accounting
from story_writing_agents.runtime_resources import collaboration_event_journal as event_journal


_SYNCHRONIZATION_TIMEOUT_SECONDS = 60

def _three_way_proposal_fixture(case, conversation_id):
    return case.proposal(conversation_id, domains=('plot_structure', 'character_world', 'tone_execution'),
        assignments=_assignments(agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN, agents.EXECUTION_SPECIALIST),
        text=accounting._REVIEW_BRIEF + '\n\nPlease check the story direction.', authoring_assessment=asdict(
        accounting._review_assessment()))

def _confirmed_execution_task_fixture(runtime, proposed):
    return asyncio.create_task(runtime.decide_lead_step(lead.lead_confirmation_decision(proposed,
        conversations.CollaborationConfirmationDecision.CONFIRM)))

def stop_after_durable_result_patch(runtime):
    return mock.patch.object(runtime.known_result_finalization, 'finalize_known_result', side_effect=RuntimeError(
        'test stopped after durable execution result'))

def _assignments(*agent_ids: str) -> list[dict[str, object]]:
    return [{'agent_id': agent_id, 'instruction': f'Return the bounded {agent_id} assessment.',
        'input_ids': ['user_input']} for agent_id in agent_ids]

def specialist_raw_outcome(value: object):
    def respond(_call, _provider):
        return json.dumps({'action': {'kind': 'answer', 'result': value}}, separators=(',', ':'))

    return respond

def _submitted_specialist_calls(history):
    return tuple(call for call in lead._submitted_calls(history) if call.invocation_kind is conversations.CollaborationInvocationKind.SPECIALIST_ASSIGNMENT)

def _events(history, kind: conversations.CollaborationEventKind):
    return tuple(event for event in history if event.kind is kind)

@contextmanager
def _fast_prevalidated_session_binding(runtime, session_id):
    """Keep concurrency tests focused on the guard/await boundary."""

    binding = runtime.bind_session_artifacts(session_id)
    with (mock.patch.object(StoryRuntime, 'bind_session_artifacts', return_value=binding),
        mock.patch.object(workspace_initialization, '_require_ready_locked_git_session_resources',
        return_value=binding.resources)):
        yield

class ExecutionInputFixture:
    def setUp(self) -> None:
        self._execution_inputs = lead._capture_execution_inputs(super().setUp)


class SpecialistExecutionAcceptanceTests(ExecutionInputFixture, lead.LeadOrchestrationTestCase):


    async def test_start_append_failure_makes_zero_specialist_calls_and_leaks_no_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Start append failure")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        calls_before = len(self.backend.calls)
        original = self.runtime.conversation_storage_services
        failing = accounting._FailingEventStore(original.event_store, state=conversations.CollaborationStepState.RUNNING)
        services = ConversationStorageServices(
            original.catalog_binding,
            replace(original.event_store_binding, component=failing),
            original.search_binding,
            original.mutation_coordinator,
        )
        runtime, _, live_executions = lead._capture_execution_inputs(
            StoryRuntime, self.workspace,
            self.backend,  # type: ignore[arg-type]
            conversation_storage_services=services,
        )
        failing.enabled = True

        with self.assertRaisesRegex(RuntimeError, "injected durable append failure"):
            await runtime.decide_lead_step(
                lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM) )

        self.assertEqual(len(self.backend.calls), calls_before)
        self.assertEqual(failing.failures, 1)
        self.assertEqual(live_executions, set())
        projection = conversations.project_collaboration_history(self.history(conversation_id))
        self.assertIs(projection.current_state, conversations.CollaborationStepState.CONFIRMED)

    async def test_assignment_failure_joins_siblings_and_skips_synthesis(self) -> None:
        conversation_id = self.conversation("Partial specialist failure")
        proposed = await _three_way_proposal_fixture(self, conversation_id)
        sibling_started = asyncio.Event()
        sibling_release = asyncio.Event()
        sibling_finishes = 0

        async def sibling(call, provider):
            nonlocal sibling_finishes
            sibling_started.set()
            await asyncio.wait_for(sibling_release.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            sibling_finishes += 1
            return lead.lead_specialist_outcome()(call, provider)

        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._SubmittedFailure(RuntimeError('plot failed after submission')))
        self.backend.enqueue_for(agents.CHARACTER_CUSTODIAN, sibling)
        self.backend.enqueue_for(agents.EXECUTION_SPECIALIST, sibling)
        with _fast_prevalidated_session_binding(self.runtime, self.session_id):
            task = _confirmed_execution_task_fixture(self.runtime, proposed)
            await asyncio.wait_for(sibling_started.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            sibling_release.set()
            decision = await task

        self.assertEqual(sibling_finishes, 2)
        self.assertIs(decision.turn.state, conversations.CollaborationStepState.FAILED)
        self.assertIs(decision.turn.next_action, conversations.CollaborationNextAction.CONTINUE_WITH_LEAD)
        history = self.history(conversation_id)
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationLeadSynthesisPayload)), 0)
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationSpecialistReplyPayload)), 2)
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertEqual(tuple((item.outcome for item in result.assignment_outcomes)), (
            conversations.CollaborationAssignmentOutcomeKind.FAILED, conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED,
            conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED))
        self.assertIs(
            result.synthesis_outcome,
            conversations.CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        with self.runtime.bind_session_artifacts(
            self.session_id
        ).open_ready_resources():
            pass

    async def test_outer_cancellation_joins_tasks_persists_terminals_and_cleans_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Cancellation")
        proposed = (
            await self.proposal(conversation_id, assignments=_assignments(agents.PLOT_ARCHITECT,
            agents.CHARACTER_CUSTODIAN), text=accounting._REVIEW_BRIEF + '\n\nPlease check the story direction.',
            authoring_assessment=asdict(accounting._review_assessment()), domains=('plot_structure',
            'character_world'))
        )
        both_started = asyncio.Event()
        count = 0

        async def blocked(_call, _provider):
            nonlocal count
            count += 1
            if count == 2:
                both_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        self.backend.enqueue_for(agents.PLOT_ARCHITECT, blocked)
        self.backend.enqueue_for(agents.CHARACTER_CUSTODIAN, blocked)
        task = _confirmed_execution_task_fixture(self.runtime, proposed)
        await asyncio.wait_for(both_started.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        with self.runtime.bind_session_artifacts(
            self.session_id
        ).open_ready_resources():
            pass

        self.assertEqual(self._execution_inputs[2], set())
        history = self.history(conversation_id)
        self.assertIs(conversations.project_collaboration_history(history).current_state, conversations.CollaborationStepState.RUNNING)
        submitted = _submitted_specialist_calls(history)
        self.assertEqual(len(submitted), 2)
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, conversations.CollaborationExecutionOutcomeKind.CANCELLED)
        self.assertEqual(tuple((item.outcome for item in result.assignment_outcomes)), (
            conversations.CollaborationAssignmentOutcomeKind.CANCELLED,conversations.CollaborationAssignmentOutcomeKind.CANCELLED))
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationTerminalOutcomePayload)), 2)
        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        finalized = await restarted.lead_turn(self.session_id, conversation_id,
            'Finalize only the already durable result.')
        self.assertIs(finalized.state, conversations.CollaborationStepState.INTERRUPTED)
        self.assertEqual(restarted_backend.calls, [])

    async def test_one_and_two_assignments_run_with_exact_parallel_width(self) -> None:
        for specialist_ids, declared_domains in (((agents.PLOT_ARCHITECT,), ('plot_structure',)),
            ((agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN), ('plot_structure', 'character_world'))):
            with self.subTest(specialist_ids=specialist_ids):
                conversation_id = self.conversation(f'Parallel width {len(specialist_ids)}')
                proposed = ( await self.proposal(conversation_id, assignments=_assignments(*specialist_ids),
                    text=accounting._REVIEW_BRIEF + '\n\nPlease check the story direction.',
                    authoring_assessment=asdict(accounting._review_assessment()), domains=declared_domains))
                entered = 0
                release = asyncio.Event()
                ready = asyncio.Event()

                async def overlapping(call, provider):
                    nonlocal entered
                    entered += 1
                    if entered == len(specialist_ids):
                        ready.set()
                    await asyncio.wait_for(release.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
                    return lead.lead_specialist_outcome()(call, provider)

                for agent_id in specialist_ids:
                    self.backend.enqueue_for(agent_id, overlapping)
                self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
                task = _confirmed_execution_task_fixture(self.runtime, proposed)
                await asyncio.wait_for(ready.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
                self.assertEqual(entered, len(specialist_ids))
                release.set()
                decision = await task
                self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)


    async def test_execution_delivers_only_each_assignments_ordered_reference_subset(self) -> None:
        conversation_id = self.conversation("Assignment-local references")
        proposed = ( await self.proposal(conversation_id,
            text=accounting._REVIEW_BRIEF + '\n\n' + ('Check ${core_documents/global_outline.md}, '
            '${core_documents/character_sheets.md}, and ' '${manuscript/part-01/opening.md}#L2-L3.')
            , assignments=[{'agent_id': agents.PLOT_ARCHITECT, 'instruction': 'Check the outline against the opening range.',
            'input_ids': ['user_input', 'reference_1', 'reference_3']},
            {'agent_id': agents.CHARACTER_CUSTODIAN, 'instruction': 'Check the character sheet only.',
            'input_ids': ['reference_2']}, {'agent_id': agents.EXECUTION_SPECIALIST,
            'instruction': 'Assess the supplied prose.', 'input_ids': ['reference_3']}],
            authoring_assessment=asdict(accounting._review_assessment()), domains=('plot_structure',
            'character_world', 'tone_execution')))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome())
        self.backend.enqueue_for(agents.EXECUTION_SPECIALIST, lead.lead_specialist_outcome())
        self.backend.enqueue_for(agents.CHARACTER_CUSTODIAN, lead.lead_specialist_outcome("concern"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(unresolved_input_ids=[ 'reference_2']))

        decision = await self.runtime.decide_lead_step(
            lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        invocation_inputs = {}
        for agent_id, call in self.backend.call_records:
            if agent_id not in {agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN}:
                continue
            material = lead._workflow_material(call.payload)
            invocation_inputs[agent_id] = tuple((item['input_id'] for item in material['inputs']))
        self.assertEqual(invocation_inputs, {agents.PLOT_ARCHITECT: ('user_input', 'reference_1',
            'reference_3'), agents.CHARACTER_CUSTODIAN: ('reference_2',)})
        specialist_references = {agent_id: contexts for agent_id,
            contexts in self.backend.reference_records if agent_id in {agents.PLOT_ARCHITECT,
            agents.CHARACTER_CUSTODIAN}}
        self.assertEqual(tuple((item['content'] for item in specialist_references[agents.PLOT_ARCHITECT])),
            ('Outline first line.\nOutline second line.\n', 'Opening two.\nOpening three.\n'))
        self.assertEqual(tuple((item['content'] for item in specialist_references[
            agents.CHARACTER_CUSTODIAN])), ('Mara wants safety.\nMara fears exposure.\n',))
        synthesis_call = next(
            call
            for agent_id, call in self.backend.call_records
            if agent_id == agents.LEAD_ORCHESTRATOR
            and lead._workflow_material(call.payload)["workflow"] == "lead_synthesis" )
        synthesis_material = lead._workflow_material(synthesis_call.payload)
        self.assertEqual(tuple((item['input_id'] for item in synthesis_material['inputs'])), (
            'user_input', 'reference_1', 'reference_2', 'reference_3'))

    async def test_invalid_specialist_shapes_repair_then_fall_back_without_halting(self) -> None:
        invalid_values = (
            ({"assessment": "supports"}, "$.agent_action.task_result.response_text: required property is missing"),
            ({"message": "Missing assessment."}, "$.agent_action.task_result.assignment_status: required property is missing"),
            ({"assessment": 0, "message": "Private state claim.", "private_state": "forbidden"},
             "$.agent_action.task_result.assignment_status: expected string, got integer"), )
        for ordinal, (value, issue) in enumerate(invalid_values, start=1):
            with self.subTest(ordinal=ordinal):
                conversation_id = self.conversation(f"Strict specialist {ordinal}")
                proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
                response = specialist_raw_outcome(value)
                self.backend.enqueue_for(agents.PLOT_ARCHITECT, response)
                self.backend.enqueue_for(agents.PLOT_ARCHITECT, response)
                self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
                decision = await self.runtime.decide_lead_step(lead.lead_confirmation_decision(proposed,
                    conversations.CollaborationConfirmationDecision.CONFIRM))
                self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
                result = lead._event_payloads(self.history(conversation_id), conversations.CollaborationExecutionResult)[0]
                self.assertIs(result.assignment_outcomes[0].outcome, conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED)
                self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
                reply = lead._event_payloads(self.history(conversation_id), conversations.CollaborationSpecialistReplyPayload)[0]
                self.assertIs(reply.assessment, conversations.CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertEqual(reply.trace.action, "none")
                reports = tuple(
                    chunk for payload in lead._submitted_calls(self.history(conversation_id)) if payload.state is conversations.CollaborationProviderCallState.SUBMITTED
                    for chunk in payload.validation_details )
                self.assertTrue(all(issue in chunk for chunk in reports))
                self.assertTrue(any(chunk.startswith("Original response") for chunk in reports))
                self.assertTrue(any(chunk.startswith("Repair response") for chunk in reports))
                self.assertNotIn("private_state", repr(self.history(conversation_id)))

    async def test_specialist_and_synthesis_validation_details_are_private_and_correlated(
        self,
    ) -> None:
        conversation_id = self.conversation("Rejected synthesis details")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome())
        rejected = specialist_raw_outcome({'message': 0, 'PRIVATE_SYNTHESIS_KEY': 'PRIVATE_SYNTHESIS_VALUE'})
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, rejected)
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        decision = await self.runtime.decide_lead_step(
            lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )
        history = self.history(conversation_id)
        submitted = lead._submitted_calls(history)
        synthesis = tuple(payload for payload in submitted
                          if payload.role == agents.LEAD_ORCHESTRATOR and payload.validation_details)
        self.assertEqual(len(synthesis), 1)
        self.assertTrue(synthesis[0].validation_details[0].startswith("Original response"))
        self.assertIn('$.agent_action.task_result.response_text: expected string, got integer',
                      repr(synthesis[0].validation_details))
        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        self.assertEqual(decision.turn.validation_details, tuple((
            chunk for payload in submitted for chunk in payload.validation_details)))
        self.assertNotIn("PRIVATE_SYNTHESIS_KEY", repr(history))
        self.assertNotIn("PRIVATE_SYNTHESIS_VALUE", repr(history))



    async def test_concurrent_duplicate_domain_and_schema_reports_keep_call_order(self) -> None:
        conversation_id = self.conversation("Concurrent diagnostic correlation")
        proposed = await _three_way_proposal_fixture(self, conversation_id)
        for role in (agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN):
            self.backend.enqueue_for(role, lead.lead_specialist_outcome(message=""))
            self.backend.enqueue_for(role, lead.lead_specialist_outcome())
        self.backend.enqueue_for(agents.EXECUTION_SPECIALIST, specialist_raw_outcome({'assessment': 'supports', 'message': 0}))
        self.backend.enqueue_for(agents.EXECUTION_SPECIALIST, lead.lead_specialist_outcome())
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())

        with stop_after_durable_result_patch(self.runtime):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
                )
        known = self.history(conversation_id)
        projection = conversations.project_collaboration_history(known)
        messages_by_call: dict[tuple[str, str], list[str]] = {}
        for event in known:
            payload = event.payload
            if (type(payload) is conversations.CollaborationMessagePayload
                and payload.validation_invocation_id is not None
                and payload.validation_call_id is not None):
                key = (payload.validation_invocation_id, payload.validation_call_id)
                messages_by_call.setdefault(key, []).extend(payload.validation_details)
        submitted = lead._submitted_calls(known)
        expected = tuple((detail for payload in submitted for detail in messages_by_call.get((
            payload.invocation_id, payload.call_id), payload.validation_details)))
        self.assertEqual(len(messages_by_call), 3)
        self.assertEqual(len(expected), 3)
        empty_message_details = tuple(
            detail for detail in expected
            if detail == "Original response\n"
                         "$.agent_action.task_result.response_text: "
                         'expected pattern "[\\\\s\\\\S]"' )
        self.assertEqual(len(empty_message_details), 2)
        self.assertEqual(empty_message_details[0], empty_message_details[1])
        self.assertEqual(sum(('expected string, got integer' in detail for detail in expected)), 1)
        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize concurrent results.')
        self.assertIs(restored.state, conversations.CollaborationStepState.COMPLETED)
        self.assertEqual(restored.validation_details, expected)
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(finalize_known_result(restarted, self.session_id, known, projection), restored)

    async def test_failed_specialist_retries_transient_validation_publication(self) -> None:
        conversation_id = self.conversation("Retry failed specialist diagnostic append")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        private = "PRIVATE_SPECIALIST_PROVIDER_FAILURE_한🙂"
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(message=""))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._SubmittedFailure(RuntimeError(private)))
        original_append = event_journal.append_validation_messages
        attempts = 0

        def fail_once(**kwargs) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected diagnostic append conflict")
            original_append(**kwargs)

        with (mock.patch.object(event_journal, 'append_validation_messages', fail_once),
            stop_after_durable_result_patch(self.runtime)):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
                )

        self.assertEqual(attempts, 2)
        known = self.history(conversation_id)
        projection = conversations.project_collaboration_history(known)
        self.assertIs(projection.current_state, conversations.CollaborationStepState.RUNNING)
        result = lead._event_payloads(known, conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, conversations.CollaborationExecutionOutcomeKind.FAILED)
        self.assertIs(result.assignment_outcomes[0].outcome,
                      conversations.CollaborationAssignmentOutcomeKind.FAILED)
        self.assertEqual(result.assignment_outcomes[0].submitted_physical_calls, 2)
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED)
        submitted = _submitted_specialist_calls(known)
        self.assertEqual(len(submitted), 2)
        notices = tuple((payload for payload in lead._event_payloads(known,
            conversations.CollaborationMessagePayload) if payload.validation_details))
        self.assertEqual(len(notices), 1)
        self.assertEqual(tuple(p.validation_details for p in submitted),
                         (notices[0].validation_details, ()))
        self.assertEqual(notices[0].validation_call_id, submitted[0].call_id)
        self.assertEqual(notices[0].validation_invocation_id, submitted[0].invocation_id)
        self.assertEqual(notices[0].validation_details[0], "Original response\n"
                         "$.agent_action.task_result.response_text: "
                         'expected pattern "[\\\\s\\\\S]"')
        self.assertNotIn(private, repr(known))

        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize failed specialist result.')
        self.assertIs(restored.state, conversations.CollaborationStepState.FAILED)
        self.assertEqual(restored.validation_details, notices[0].validation_details)
        self.assertEqual(restarted_backend.calls, [])
        self.assertNotIn(private, repr(self.history(conversation_id)))

    async def test_recovered_reply_retries_transient_validation_publication(self) -> None:
        for rejected_role in (agents.PLOT_ARCHITECT, agents.LEAD_ORCHESTRATOR):
            with self.subTest(rejected_role=rejected_role):
                conversation_id = self.conversation(
                    f"Retry recovered diagnostic append {rejected_role}" )
                proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
                calls_before = len(self.backend.calls)
                if rejected_role == agents.PLOT_ARCHITECT:
                    self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(message=''))
                    self.backend.enqueue_for(
                        agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(),
                    )
                else:
                    self.backend.enqueue_for(
                        agents.PLOT_ARCHITECT, lead.lead_specialist_outcome(),
                    )
                    self.backend.enqueue_for(
                        agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=""),
                    )
                self.backend.enqueue_for(
                    agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(),
                )
                original_append = event_journal.append_validation_messages
                attempts = 0

                def fail_once(**kwargs) -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("injected diagnostic append conflict")
                    original_append(**kwargs)

                with (mock.patch.object(event_journal, 'append_validation_messages', fail_once)):
                    decision = await self.runtime.decide_lead_step(lead.lead_confirmation_decision(proposed,
                        conversations.CollaborationConfirmationDecision.CONFIRM))
                self.assertEqual(attempts, 2)
                self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
                self.assertEqual(len(self.backend.calls) - calls_before, 3)
                history = self.history(conversation_id)
                result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
                self.assertIs(result.overall_outcome, conversations.CollaborationExecutionOutcomeKind.SUCCEEDED)
                notices = tuple((payload for payload in lead._event_payloads(history,
                    conversations.CollaborationMessagePayload) if payload.validation_details))
                self.assertEqual(len(notices), 1)
                self.assertEqual(decision.turn.validation_details, notices[0].validation_details)
                self.assertTrue(notices[0].validation_details[0].startswith('Original response'))
                submitted = tuple(payload for payload in lead._submitted_calls(history) if payload.invocation_kind in {
                       conversations.CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
                       conversations.CollaborationInvocationKind.LEAD_SYNTHESIS,})
                self.assertEqual(len(submitted), 3)
                rejected = next(payload for payload in submitted if payload.role == rejected_role
                                and payload.call_id == notices[0].validation_call_id)
                self.assertEqual(notices[0].validation_invocation_id, rejected.invocation_id)


    async def test_synthesis_contract_is_semantic_and_runtime_derives_attribution(self) -> None:
        synthesis_review_candidates = []
        def minimal_synthesis(call, _provider):
            synthesis_review_candidates.append(
                dict(call.contract.semantic_index.policy.choices)['review_candidate_ids'])
            self.assertNotIn("reply_event_ids", repr(call.schema))
            self.assertNotIn("unresolved_input_ids", repr(call.schema))
            for message in ("Visible.", " \t\0"):
                self.assertEqual(lead_provider_contracts.LeadProviderConstraints(continuation=True).diagnose(json.dumps({"message": message})), ())
            return lead._provider_envelope(call, {"message": "The concerns remain visible."})
        conversation_id = self.conversation("Strict synthesis")
        proposed = (
            await self.proposal(conversation_id, assignments=_assignments(agents.PLOT_ARCHITECT,
            agents.CHARACTER_CUSTODIAN), text=accounting._REVIEW_BRIEF + '\n\nPlease check the story direction.',
            authoring_assessment=asdict(accounting._review_assessment()), domains=('plot_structure',
            'character_world'))
        )
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome("concern"))
        self.backend.enqueue_for(agents.CHARACTER_CUSTODIAN, lead.lead_specialist_outcome("insufficient_evidence"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, minimal_synthesis)
        decision = await self.runtime.decide_lead_step(lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        reply_events = _events(history, conversations.CollaborationEventKind.SPECIALIST_REPLY)
        self.assertEqual(len(reply_events), 2)
        synthesis = lead._event_payloads(history, conversations.CollaborationLeadSynthesisPayload)[0]
        synthesis_call = next(event.payload for event in history if type(event.payload) is conversations.CollaborationProviderCallPayload
            and event.payload.invocation_kind is conversations.CollaborationInvocationKind.LEAD_SYNTHESIS
            and event.payload.state is conversations.CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(synthesis.provider_invocation_id, synthesis_call.invocation_id)
        self.assertEqual(decision.turn.provider_invocation_id, synthesis_call.invocation_id)
        self.assertIs(synthesis.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(synthesis.trace.action, "synthesis")
        self.assertEqual(synthesis.reply_event_ids, tuple(event.event_id for event in reply_events))
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        duplicate = {**_assignments(agents.PLOT_ARCHITECT)[0], "mode": "review"}
        with self.assertRaises(BackendProtocolError) as caught:
            self.runtime._decode_lead_synthesis(json.dumps({"message": "Visible.", "next_intent": {
                "purpose": "Continue the review.", "assignments": [duplicate, duplicate]}}),
                proposal=proposed.proposal, attempt_id=synthesis.execution_attempt_id,
                reply_event_ids=synthesis.reply_event_ids,
                review_candidate_ids=synthesis_review_candidates[0],
                replies=tuple(event.payload for event in reply_events))
        self.assertEqual(structured_output_validation.failure_issues(caught.exception, path="$.action.result"),
                         ("$.action.result.next_intent.assignments: agent_id values must be unique before canonical ordering",))

    async def test_synthesis_derives_non_supporting_inputs_locally(self) -> None:
        conversation_id = self.conversation("Unresolved concern")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome("concern"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome(unresolved_input_ids=[]))

        decision = await self.runtime.decide_lead_step(
            lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        synthesis = lead._event_payloads(self.history(conversation_id), conversations.CollaborationLeadSynthesisPayload)[0]
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = lead._event_payloads(self.history(conversation_id), conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)

    async def test_submitted_uncertainty_enters_unknown_and_never_replays(self) -> None:
        conversation_id = self.conversation("Uncertain specialist")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._SubmittedFailure(TimeoutError(
            'transport outcome unknown'), timed_out=True, submission_uncertain=True))
        decision = await self.runtime.decide_lead_step(
            lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )
        calls = len(self.backend.calls)

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.OUTCOME_UNKNOWN)
        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        recovery = await restarted.lead_turn(self.session_id, conversation_id, 'Do not replay the uncertain attempt.')
        self.assertIs(recovery.state, conversations.CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(restarted_backend.calls, [])

    async def test_provider_free_advisory_confirms_one_specialist_without_synthesis_or_chain(
        self,
    ) -> None:
        conversation_id = self.conversation("Direct advisory")
        calls_before = len(self.backend.calls)
        proposed = await self.runtime.session( self.session_id
        ).propose_specialist_advisory( conversation_id,
            agents.PLOT_ARCHITECT,
            "Candidate: a choice precedes its consequence. Assess the plot chronology." + "\n\n" + accounting._REVIEW_LATITUDE,
            authoring_scope=specialist_routing.AuthoringScope(specialist_routing.AuthoringActivity.REVIEW, (), (specialist_routing.ArtifactReviewDomain.PLOT_STRUCTURE,),
                                          "Assess the current user-supplied candidate.", "advisory_task", 1),
            review_basis=specialist_routing.ReviewBasis(("user_input",), "Assess chronology."), authoring_assessment=accounting._review_assessment())

        self.assertEqual(len(self.backend.calls), calls_before)
        assert proposed.proposal is not None
        self.assertIs(proposed.proposal.step_kind, conversations.CollaborationStepKind.SPECIALIST_ADVISORY)
        self.assertEqual(len(proposed.proposal.assignments), 1)
        self.assertEqual(proposed.proposal.budget.synthesis_max_physical_calls, 0)
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome('concern',
            'The requested plot turn needs evidence.'))
        decision = await self.runtime.decide_lead_step(
            lead.lead_confirmation_decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        self.assertIn("needs evidence", decision.turn.message)
        history = self.history(conversation_id)
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationSpecialistReplyPayload)), 1)
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationLeadSynthesisPayload)), 0)
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(
            result.synthesis_outcome,
            conversations.CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        self.assertEqual(result.synthesis_submitted_physical_calls, 0)
        self.assertIsNone(decision.turn.proposal)

        mismatch_id = self.conversation("Advisory owner mismatch")
        with self.assertRaisesRegex(ValueError, "required story specialist|owned by another reviewer"):
            await self.runtime.propose_specialist_advisory(self.session_id, mismatch_id,
                agents.CHARACTER_CUSTODIAN, accounting._REVIEW_BRIEF + '\n\n' + 'Check ${core_documents/global_outline.md}.',
                authoring_scope=specialist_routing.AuthoringScope(specialist_routing.AuthoringActivity.REVIEW, (), (
                specialist_routing.ArtifactReviewDomain.CHARACTER_WORLD,), 'Assess the current candidate.',
                'advisory_other', 1), review_basis=specialist_routing.ReviewBasis(('user_input',),
                'Assess the supplied candidate.'), authoring_assessment=accounting._review_assessment())
        self.assertFalse(self.history(mismatch_id))

    async def test_second_round_uses_reply_events_fresh_authority_and_cannot_chain_again(self) -> None:
        conversation_id = self.conversation("Second round")
        first = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\n" + "Check ${core_documents/global_outline.md}",
            assignments=[{**_assignments(agents.PLOT_ARCHITECT)[0], "input_ids": ["user_input", "reference_1"]}], authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome("concern"))
        def continue_lineage(call, _provider):
            from tests.provider_wire_fixture import (
                indexed_response, lead_fixture_assignments, lead_fixture_result,
                offered_fixture_bindings, result as wire_result)
            material = lead._workflow_material(_provider.callback_payload(call))
            reply_ids = [item["reply_event_id"] for item in material["replies"]]
            known_ids = [item["input_id"] for item in material["inputs"]]
            self.assertIn("reference_1", known_ids)
            choices = dict(call.contract.semantic_index.policy.choices)
            self.assertTrue(call.contract.factory.constraints.continuation)
            self.assertEqual(
                call.contract.factory.constraints.scope, first.proposal.authoring_scope)
            self.assertEqual(choices['review_candidate_binding'], ('bound',))
            self.assertEqual(set(choices['review_candidate_ids']), set(known_ids))
            self.assertTrue(set(reply_ids).isdisjoint(known_ids))
            self.assertEqual(set(choices['required_input_ids']), set((*known_ids, *reply_ids)))
            self.assertEqual(choices['allocation_input_ids'], tuple(
                alias for alias in choices['input_ids'] if alias in (*known_ids, *reply_ids)))
            self.assertTrue(set((*known_ids, *reply_ids)).issubset(choices['input_ids']))
            assignment = {"agent_id": agents.PLOT_ARCHITECT, "instruction": "Reassess all sources and the first reply.",
                          "input_ids": [*known_ids, *reply_ids], "mode": "review"}
            assignment['review_basis'] = {
                'candidate_input_ids': list(known_ids),
                'criteria': 'Reassess all sources and the first reply.'}
            body = {"message": "One issue needs a second review.", "next_intent": {
                "purpose": "Resolve the remaining concern.", "assignments": [assignment]}}
            native = wire_result(body, 'consultation-summary-v4')
            follow_up = native['follow_up_consultation']
            follow_up.update(lead_fixture_assignments(
                follow_up['assignments'], input_choices=choices['allocation_input_ids'],
                review_choices=choices['review_candidate_ids'],
                anchors={agents.PLOT_ARCHITECT: known_ids[0]},
                recipients={alias: agents.PLOT_ARCHITECT for alias in choices['required_input_ids']}))
            grouped = lead_fixture_result(native)
            outer_id, inner_id = offered_fixture_bindings(
                call, action_type='submit_result', response_type='consultation_summary', family=0,
                branch_key='consultation-summary-v4:family:1', value=grouped)
            encoded = indexed_response(
                {'action_type': 'submit_result', 'task_result': grouped},
                outer_schema_id=outer_id, inner_schema_id=inner_id, contract=call.contract)
            self.assertEqual(structured_output_validation.diagnose_json_text(encoded, call.schema), ())
            self.assertEqual(json.loads(call.contract.decode_response(encoded).answer_json), body)
            facts = lead_provider_contracts.LeadProviderConstraints(input_ids=tuple(known_ids + reply_ids),
                reviewer_inputs=((agents.PLOT_ARCHITECT, "reference_1"),), scope=first.proposal.authoring_scope, continuation=True)
            self.assertEqual(facts.diagnose(json.dumps(body)), ())
            for selected in (known_ids, [alias for alias in assignment["input_ids"] if alias != "reference_1"]):
                bad = {**body, "next_intent": {**body["next_intent"], "assignments": [{**assignment, "input_ids": selected}]}}
                self.assertIn(structured_output_validation.DomainValidationRule.CONTINUATION_INPUT_COVERAGE, facts.diagnose(json.dumps(bad)))
            bad = {**body, "next_intent": {**body["next_intent"], "assignments": [{**assignment, "input_ids": ["foreign_reply"]}]}}
            self.assertIn(structured_output_validation.DomainValidationRule.CONTINUATION_INPUT_NOT_OFFERED, facts.diagnose(json.dumps(bad)))
            rejected = json.loads(json.dumps(grouped))
            rejected_rows = rejected['response_details']['follow_up_consultation']['assignments']
            rejected_rows[agents.PLOT_ARCHITECT]['input_anchor_id'] = (
                bad['next_intent']['assignments'][0]['input_ids'][0])
            self.assertTrue(structured_output_validation.diagnose_json_text(indexed_response(
                {'action_type': 'submit_result', 'task_result': rejected},
                outer_schema_id=outer_id, inner_schema_id=inner_id, contract=call.contract), call.schema))
            self.assertIn(structured_output_validation.DomainValidationRule.PURPOSE_REQUIRED, facts.diagnose(json.dumps({**body, "next_intent": {**body["next_intent"], "purpose": ""}})))
            return encoded
        private = "PRIVATE_CONTINUATION_SYNTHESIS_VALUE"
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, specialist_raw_outcome({"message": "", private: private}))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, continue_lineage)
        first_decision = await self.runtime.decide_lead_step(lead.lead_confirmation_decision(first, conversations.CollaborationConfirmationDecision.CONFIRM))
        second = first_decision.turn
        self.assertIsNotNone(second.proposal)
        self.assertIs(second.state, conversations.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(second.proposal.step_id, first.step_id)
        self.assertEqual(second.proposal.proposal_revision, first.proposal.proposal_revision + 1)
        self.assertEqual((second.proposal.budget.rounds_spent, second.proposal.budget.rounds_added,
                          second.proposal.budget.physical_calls_spent), (1, 1, 5))
        first_history = self.history(conversation_id)
        expected_details = tuple(chunk for payload in lead._submitted_calls(first_history)
            for chunk in payload.validation_details)
        self.assertTrue(expected_details)
        self.assertEqual(second.validation_details, expected_details)
        self.assertNotIn(private, repr(first_history))
        self.assertEqual(tuple(item.kind for item in second.proposal.basis.inputs),
            (conversations.CollaborationInputKind.USER_INPUT, conversations.CollaborationInputKind.CORE_DOCUMENT, conversations.CollaborationInputKind.CONVERSATION_EVENT))
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(lead.lead_confirmation_decision(first, conversations.CollaborationConfirmationDecision.CONFIRM))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead.lead_specialist_outcome("supports"))
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        second_decision = await self.runtime.decide_lead_step(lead.lead_confirmation_decision(second, conversations.CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(second_decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        self.assertIsNone(second_decision.turn.proposal)
        self.assertEqual(second_decision.turn.validation_details, ())
        syntheses = lead._event_payloads(self.history(conversation_id), conversations.CollaborationLeadSynthesisPayload)
        self.assertEqual(len(syntheses), 2)
        self.assertEqual(syntheses[0].evidence, first.proposal.basis.evidence)
        self.assertEqual(syntheses[1].evidence, second.proposal.basis.evidence)
        self.assertTrue(all(set(synthesis.reply_event_ids).isdisjoint(item.evidence_id for item in synthesis.evidence.receipts)
                            for synthesis in syntheses))
        self.assertIsNone(syntheses[-1].next_intent)


if __name__ == "__main__":
    unittest.main()
