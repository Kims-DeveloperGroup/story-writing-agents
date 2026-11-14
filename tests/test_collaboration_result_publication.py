"""Existing collaboration result publication acceptance coverage."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest import mock
from story_writing_agents import StoryRuntime, agents, conversations
from story_writing_agents.conversation_storage import ConversationStorageServices
from story_writing_agents.runtime_resources import collaboration_event_journal as event_journal
from story_writing_agents.workspace import initialization as workspace_initialization
from tests import test_lead_orchestration as lead
from tests import test_specialist_orchestration_accounting as accounting
from tests.test_specialist_orchestration_execution import (
    _SYNCHRONIZATION_TIMEOUT_SECONDS,
    _confirmed_execution_task_fixture,
    _events,
    _fast_prevalidated_session_binding,
    _submitted_specialist_calls,
    _three_way_proposal_fixture,
)
class CollaborationResultPublicationTests(lead.LeadOrchestrationTestCase):
    def setUp(self) -> None:
        self._execution_inputs = lead._capture_execution_inputs(super().setUp)

    async def test_result_persistence_failure_cleans_marker_after_provider_completion(self) -> None:
        conversation_id = self.conversation("Result persistence failure")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        original = self.runtime.conversation_storage_services
        failing = accounting._FailingEventStore(original.event_store, event_kind=conversations.CollaborationEventKind.EXECUTION_RESULT)
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
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._specialist_outcome())
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())
        failing.enabled = True

        captured = []
        invoke = runtime._run_collaboration_invocation
        async def record(**arguments):
            result = await invoke(**arguments)
            captured.append(result)
            return result
        with mock.patch.object(runtime, "_run_collaboration_invocation", side_effect=record):
            with self.assertRaisesRegex(RuntimeError, "injected durable append failure"):
                await runtime.decide_lead_step(
                    lead._decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM) )

        self.assertEqual(failing.failures, 1)
        self.assertEqual(live_executions, set())
        self.assertIs(
            conversations.project_collaboration_history(self.history(conversation_id)).current_state,
            conversations.CollaborationStepState.RUNNING, )
        submitted = [event.payload for event in self.history(conversation_id) if type(event.payload) is conversations.CollaborationProviderCallPayload
            and event.payload.state is conversations.CollaborationProviderCallState.SUBMITTED
            and event.payload.invocation_kind in {conversations.CollaborationInvocationKind.SPECIALIST_ASSIGNMENT, conversations.CollaborationInvocationKind.LEAD_SYNTHESIS}]
        self.assertEqual({result.provider_invocation_id for result in captured}, {call.invocation_id for call in submitted})
        self.assertEqual(len(captured), 2)

    async def test_persistent_validation_publication_failure_keeps_result_unknown(self) -> None:
        conversation_id = self.conversation("Persistent diagnostic append failure")
        proposed = await self.proposal(conversation_id, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._specialist_outcome(message=""))
        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._SubmittedFailure(RuntimeError( 'definite provider failure')))
        with (mock.patch.object(event_journal, 'append_validation_messages',
            side_effect=RuntimeError('persistent diagnostic append failure'))):
            with (self.assertRaisesRegex(RuntimeError, 'persistent diagnostic append failure')):
                await self.runtime.decide_lead_step(
                    lead._decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)
                )
        known = self.history(conversation_id)
        self.assertIs(conversations.project_collaboration_history(known).current_state,
                      conversations.CollaborationStepState.RUNNING)
        self.assertEqual(lead._event_payloads(known, conversations.CollaborationExecutionResult), ())
        self.assertEqual(lead._event_payloads(known, conversations.CollaborationTerminalOutcomePayload), ())
        submitted = _submitted_specialist_calls(known)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(self._execution_inputs[2], set())

        restarted_backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        recovered = await restarted.lead_turn(self.session_id, conversation_id, 'Inspect the uncertain attempt.')
        self.assertIs(recovered.state, conversations.CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(lead._event_payloads(self.history(conversation_id), conversations.CollaborationExecutionResult), ())

    async def test_synthesis_cancellation_preserves_specialist_result_and_never_reruns_it(self):
        conversation = self.conversation("Cancelled synthesis")
        proposed = await self.proposal(conversation, text=accounting._REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(accounting._review_assessment()))
        entered = asyncio.Event()

        async def blocked(_call, _provider):
            entered.set()
            await asyncio.Future()

        self.backend.enqueue_for(agents.PLOT_ARCHITECT, lead._specialist_outcome())
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, blocked)
        task = asyncio.create_task(self.runtime.decide_lead_step(
            lead._decision(proposed, conversations.CollaborationConfirmationDecision.CONFIRM)))
        try:
            await asyncio.wait_for(entered.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self._execution_inputs[2], set())
        history = self.history(conversation)
        execution = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(execution.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.CANCELLED)
        self.assertEqual(execution.synthesis_submitted_physical_calls, 1)
        self.assertIs(execution.assignment_outcomes[0].outcome, conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED)
        self.assertEqual(len(lead._event_payloads(history, conversations.CollaborationSpecialistReplyPayload)), 1)
        backend = lead._LeadBackend(test_case=self)
        restarted = StoryRuntime(self.control_workspace, backend)
        result = await restarted.lead_turn(self.session_id, conversation, "Inspect the cancelled result.")
        self.assertIs(result.state, conversations.CollaborationStepState.INTERRUPTED)
        self.assertEqual(backend.calls, [])
        self.assertEqual(len(lead._event_payloads(self.history(conversation), conversations.CollaborationExecutionResult)), 1)

    async def test_confirm_starts_one_atomic_dispatch_batch_then_runs_three_in_parallel(
        self,
    ) -> None:
        conversation_id = self.conversation("Three-way parallel consultation")
        proposed = await _three_way_proposal_fixture(self, conversation_id)
        self.assertEqual(tuple((item.max_physical_calls for item in proposed.proposal.assignments)), (3, 3, 3))
        self.assertEqual(proposed.proposal.budget.synthesis_max_physical_calls, 1)
        self.assertEqual(
            sum(item.max_physical_calls for item in proposed.proposal.assignments)
            + proposed.proposal.budget.synthesis_max_physical_calls
            + proposed.proposal.budget.physical_calls_spent,
            12, )
        entered = 0
        all_entered = asyncio.Event()
        release = asyncio.Event()
        peak = 0
        active = 0
        guard_depth = 0
        maximum_guard_depth = 0
        original_open = workspace_initialization.SessionArtifactBinding.open_ready_resources

        @contextmanager
        def tracked_open(binding):
            nonlocal guard_depth, maximum_guard_depth
            guard_depth += 1
            maximum_guard_depth = max(maximum_guard_depth, guard_depth)
            try:
                with original_open(binding) as access:
                    yield access
            finally:
                guard_depth -= 1

        async def overlapping(call, provider):
            nonlocal active, entered, peak
            self.assertEqual(guard_depth, 0)
            entered += 1
            active += 1
            peak = max(peak, active)
            if entered == 3:
                all_entered.set()
            await asyncio.wait_for(release.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            self.assertEqual(guard_depth, 0)
            active -= 1
            return lead._specialist_outcome()(call, provider)

        for agent_id in ((agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN, agents.EXECUTION_SPECIALIST)):
            self.backend.enqueue_for(agent_id, overlapping)
        self.backend.enqueue_for(agents.LEAD_ORCHESTRATOR, lead._synthesis_outcome())

        with (_fast_prevalidated_session_binding(self.runtime, self.session_id),
            mock.patch.object(workspace_initialization.SessionArtifactBinding, 'open_ready_resources', tracked_open)):
            task = _confirmed_execution_task_fixture(self.runtime, proposed)
            await asyncio.wait_for(all_entered.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            self.assertEqual(peak, 3)
            release.set()
            decision = await task
        self.assertEqual(guard_depth, 0)
        self.assertEqual(maximum_guard_depth, 2)

        self.assertIs(decision.turn.state, conversations.CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        running = next((event for event in history if getattr(event.payload, 'state',
            None) is conversations.CollaborationStepState.RUNNING))
        start_batch = tuple((event for event in history if event.batch_id == running.batch_id))
        self.assertEqual(tuple((event.kind for event in start_batch)), (
            conversations.CollaborationEventKind.STEP_STATE, conversations.CollaborationEventKind.ASSIGNMENT_DISPATCH,
            conversations.CollaborationEventKind.ASSIGNMENT_DISPATCH, conversations.CollaborationEventKind.ASSIGNMENT_DISPATCH))
        self.assertEqual(tuple((event.recipient.agent_id for event in start_batch[1:])), (
            agents.PLOT_ARCHITECT, agents.CHARACTER_CUSTODIAN, agents.EXECUTION_SPECIALIST))
        first_call = next(
            event
            for event in history
            if type(event.payload) is conversations.CollaborationProviderCallPayload
            and event.payload.invocation_kind
            is conversations.CollaborationInvocationKind.SPECIALIST_ASSIGNMENT )
        self.assertLess(start_batch[-1].sequence, first_call.sequence)
        replies = lead._event_payloads(history, conversations.CollaborationSpecialistReplyPayload)
        self.assertEqual(tuple((reply.assignment_id for reply in replies)), tuple((
            item.assignment_id for item in proposed.proposal.assignments)))
        synthesis = lead._event_payloads(history, conversations.CollaborationLeadSynthesisPayload)
        self.assertEqual(len(synthesis), 1)
        self.assertEqual(synthesis[0].reply_event_ids, tuple((event.event_id for event in _events(
            history, conversations.CollaborationEventKind.SPECIALIST_REPLY))))
        result = lead._event_payloads(history, conversations.CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, conversations.CollaborationExecutionOutcomeKind.SUCCEEDED)
        self.assertTrue(
            all(
                item.outcome is conversations.CollaborationAssignmentOutcomeKind.SUCCEEDED
                for item in result.assignment_outcomes
            ) )
        self.assertIs(result.synthesis_outcome, conversations.CollaborationSynthesisOutcomeKind.SUCCEEDED)
