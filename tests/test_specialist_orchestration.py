"""Permanent execution acceptance coverage for confirmed specialist collaboration."""

from __future__ import annotations

from tests.provider_wire_fixture import response as wire_response, contract_id as wire_contract_id

import asyncio
import json
import unittest
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest import mock

from story_writing_agents.workspace import initialization as workspace_initialization
from story_writing_agents import ReplyTraceOrigin, StoryRuntime
from story_writing_agents.agents import ( CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR, PLOT_ARCHITECT,
)
from story_writing_agents.conversation_storage import ConversationStorageServices
from story_writing_agents.conversations import ( CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision, CollaborationEventKind,
    CollaborationExecutionOutcomeKind, CollaborationExecutionResult,
    CollaborationInputKind, CollaborationInvocationKind,
    CollaborationLeadSynthesisPayload, CollaborationMessagePayload,
    CollaborationNextAction, CollaborationProviderCallKind,
    CollaborationProviderCallPayload, CollaborationProviderCallState,
    CollaborationSpecialistAssessment, CollaborationSpecialistReplyPayload,
    CollaborationStepKind, CollaborationStepState,
    CollaborationSynthesisOutcomeKind, CollaborationTerminalOutcomePayload,
    project_collaboration_history,)
from story_writing_agents.models import AgentStateEntry
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.structured_output_validation import failure_issues
from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity, ArtifactReviewDomain, ReviewBasis
from story_writing_agents.workspace.initialization import SessionArtifactBinding

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase, LeadProviderConstraints, Rule, diagnose_json_text, _LeadBackend,
    _SubmittedFailure, _decision,
    _provider_envelope, _read_history_hit,
    _specialist_outcome, _synthesis_outcome,
    _submitted_calls, _workflow_material,
)
from tests.recommendation_fixture import task_assessment


_SYNCHRONIZATION_TIMEOUT_SECONDS = 60


_REVIEW_DIRECTION = "Check the story direction against supplied facts/material within each assigned domain."
_REVIEW_LATITUDE = "Make bounded review judgments only; preserve supplied facts and wait for separate execution confirmation."
_REVIEW_BRIEF = ("Story facts for this constructed review: a courier must recover her brother's letter before the last ferry; "
    "her brother hid it to protect a witness. Keep these facts and all supplied material fixed. " + _REVIEW_DIRECTION + "\n" + _REVIEW_LATITUDE)


def _review_assessment():
    return task_assessment(foundation_ids=("user_input",), actions=("bounded review judgments",))


def _three_way_proposal_fixture(case, conversation_id):
    return case.proposal(conversation_id, domains=('plot_structure', 'character_world', 'tone_execution'),
        assignments=_assignments(PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        text=_REVIEW_BRIEF + '\n\nPlease check the story direction.', authoring_assessment=asdict(
        _review_assessment()))

def _confirmed_execution_task_fixture(runtime, proposed):
    return asyncio.create_task(runtime.decide_lead_step(_decision(proposed,
        CollaborationConfirmationDecision.CONFIRM)))

def _stop_after_durable_result_patch(runtime):
    return mock.patch.object(runtime, '_finalize_known_result_locked', side_effect=RuntimeError(
        'test stopped after durable execution result'))

def _assignments(*agent_ids: str) -> list[dict[str, object]]:
    return [{'agent_id': agent_id, 'instruction': f'Return the bounded {agent_id} assessment.',
        'input_ids': ['user_input']} for agent_id in agent_ids]


def _raw_outcome(value: object):
    def respond(_call, _provider):
        return json.dumps({'action': {'kind': 'answer', 'result': value}}, separators=(',', ':'))

    return respond


def _payloads(history, payload_type):
    return tuple((event.payload for event in history if type(event.payload) is payload_type))


def _submitted_specialist_calls(history):
    return tuple(call for call in _submitted_calls(history) if call.invocation_kind is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT)


def _events(history, kind: CollaborationEventKind):
    return tuple(event for event in history if event.kind is kind)


@contextmanager
def _fast_prevalidated_session_binding(runtime, session_id):
    """Keep concurrency tests focused on the guard/await boundary."""

    binding = runtime._session_artifact_binding(session_id)
    with (mock.patch.object(StoryRuntime, '_session_artifact_binding', return_value=binding),
        mock.patch.object(workspace_initialization, '_require_ready_locked_git_session_resources',
        return_value=binding.resources)):
        yield


class _FailingEventStore:
    """Delegating test port that fails one selected durable append family."""

    def __init__(self, delegate, *, state=None, event_kind=None) -> None:
        self.delegate = delegate
        self.state = state
        self.event_kind = event_kind
        self.enabled = False
        self.failures = 0

    def append_batch(self, batch):
        matches_state = self.state is not None and any((getattr(event.payload, 'state',
            None) is self.state for event in batch.events))
        matches_kind = self.event_kind is not None and any((event.kind is self.event_kind for event in batch.events))
        if self.enabled and (matches_state or matches_kind):
            self.failures += 1
            raise RuntimeError("injected durable append failure")
        return self.delegate.append_batch(batch)

    def read(self, session_id, conversation_id):
        return self.delegate.read(session_id, conversation_id)

    def read_context(self, session_id, conversation_id):
        return self.delegate.read_context(session_id, conversation_id)


class SpecialistExecutionAcceptanceTests(LeadOrchestrationTestCase):
    async def test_confirm_starts_one_atomic_dispatch_batch_then_runs_three_in_parallel(
        self,
    ) -> None:
        conversation_id = self.conversation("Three-way parallel consultation")
        proposed = await _three_way_proposal_fixture(self, conversation_id)
        self.assertEqual(tuple((item.max_physical_calls for item in proposed.proposal.assignments)), (4, 3, 3))
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
        original_open = SessionArtifactBinding.open_ready_resources

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
            return _specialist_outcome()(call, provider)

        for agent_id in ((PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST)):
            self.backend.enqueue_for(agent_id, overlapping)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())

        with (_fast_prevalidated_session_binding(self.runtime, self.session_id),
            mock.patch.object(SessionArtifactBinding, 'open_ready_resources', tracked_open)):
            task = _confirmed_execution_task_fixture(self.runtime, proposed)
            await asyncio.wait_for(all_entered.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            self.assertEqual(peak, 3)
            release.set()
            decision = await task
        self.assertEqual(guard_depth, 0)
        self.assertEqual(maximum_guard_depth, 2)

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        running = next((event for event in history if getattr(event.payload, 'state',
            None) is CollaborationStepState.RUNNING))
        start_batch = tuple((event for event in history if event.batch_id == running.batch_id))
        self.assertEqual(tuple((event.kind for event in start_batch)), (
            CollaborationEventKind.STEP_STATE, CollaborationEventKind.ASSIGNMENT_DISPATCH,
            CollaborationEventKind.ASSIGNMENT_DISPATCH, CollaborationEventKind.ASSIGNMENT_DISPATCH))
        self.assertEqual(tuple((event.recipient.agent_id for event in start_batch[1:])), (
            PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST))
        first_call = next(
            event
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind
            is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT )
        self.assertLess(start_batch[-1].sequence, first_call.sequence)
        replies = _payloads(history, CollaborationSpecialistReplyPayload)
        self.assertEqual(tuple((reply.assignment_id for reply in replies)), tuple((
            item.assignment_id for item in proposed.proposal.assignments)))
        synthesis = _payloads(history, CollaborationLeadSynthesisPayload)
        self.assertEqual(len(synthesis), 1)
        self.assertEqual(synthesis[0].reply_event_ids, tuple((event.event_id for event in _events(
            history, CollaborationEventKind.SPECIALIST_REPLY))))
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, CollaborationExecutionOutcomeKind.SUCCEEDED)
        self.assertTrue(
            all(
                item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
                for item in result.assignment_outcomes
            ) )
        self.assertIs(result.synthesis_outcome, CollaborationSynthesisOutcomeKind.SUCCEEDED)

    async def test_start_append_failure_makes_zero_specialist_calls_and_leaks_no_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Start append failure")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        calls_before = len(self.backend.calls)
        original = self.runtime.conversation_storage_services
        failing = _FailingEventStore(original.event_store, state=CollaborationStepState.RUNNING)
        services = ConversationStorageServices(
            original.catalog_binding,
            replace(original.event_store_binding, component=failing),
            original.search_binding,
            original.mutation_coordinator,
        )
        runtime = StoryRuntime(
            self.workspace,
            self.backend,  # type: ignore[arg-type]
            conversation_storage_services=services,
        )
        failing.enabled = True

        with self.assertRaisesRegex(RuntimeError, "injected durable append failure"):
            await runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )

        self.assertEqual(len(self.backend.calls), calls_before)
        self.assertEqual(failing.failures, 1)
        self.assertEqual(runtime._live_execution_markers, set())
        projection = project_collaboration_history(self.history(conversation_id))
        self.assertIs(projection.current_state, CollaborationStepState.CONFIRMED)

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
            return _specialist_outcome()(call, provider)

        self.backend.enqueue_for(PLOT_ARCHITECT, _SubmittedFailure(RuntimeError('plot failed after submission')))
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, sibling)
        self.backend.enqueue_for(EXECUTION_SPECIALIST, sibling)
        with _fast_prevalidated_session_binding(self.runtime, self.session_id):
            task = _confirmed_execution_task_fixture(self.runtime, proposed)
            await asyncio.wait_for(sibling_started.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            sibling_release.set()
            decision = await task

        self.assertEqual(sibling_finishes, 2)
        self.assertIs(decision.turn.state, CollaborationStepState.FAILED)
        self.assertIs(decision.turn.next_action, CollaborationNextAction.CONTINUE_WITH_LEAD)
        history = self.history(conversation_id)
        self.assertEqual(len(_payloads(history, CollaborationLeadSynthesisPayload)), 0)
        self.assertEqual(len(_payloads(history, CollaborationSpecialistReplyPayload)), 2)
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertEqual(tuple((item.outcome for item in result.assignment_outcomes)), (
            CollaborationAssignmentOutcomeKind.FAILED, CollaborationAssignmentOutcomeKind.SUCCEEDED,
            CollaborationAssignmentOutcomeKind.SUCCEEDED))
        self.assertIs(
            result.synthesis_outcome,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        with self.runtime._session_artifact_binding(
            self.session_id
        ).open_ready_resources():
            pass

    async def test_outer_cancellation_joins_tasks_persists_terminals_and_cleans_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Cancellation")
        proposed = (
            await self.proposal(conversation_id, assignments=_assignments(PLOT_ARCHITECT,
            CHARACTER_CUSTODIAN), text=_REVIEW_BRIEF + '\n\nPlease check the story direction.',
            authoring_assessment=asdict(_review_assessment()), domains=('plot_structure',
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

        self.backend.enqueue_for(PLOT_ARCHITECT, blocked)
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, blocked)
        task = _confirmed_execution_task_fixture(self.runtime, proposed)
        await asyncio.wait_for(both_started.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        with self.runtime._session_artifact_binding(
            self.session_id
        ).open_ready_resources():
            pass

        self.assertEqual(self.runtime._live_execution_markers, set())
        history = self.history(conversation_id)
        self.assertIs(project_collaboration_history(history).current_state, CollaborationStepState.RUNNING)
        submitted = _submitted_specialist_calls(history)
        self.assertEqual(len(submitted), 2)
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, CollaborationExecutionOutcomeKind.CANCELLED)
        self.assertEqual(tuple((item.outcome for item in result.assignment_outcomes)), (
            CollaborationAssignmentOutcomeKind.CANCELLED,CollaborationAssignmentOutcomeKind.CANCELLED))
        self.assertEqual(len(_payloads(history, CollaborationTerminalOutcomePayload)), 2)
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        finalized = await restarted.lead_turn(self.session_id, conversation_id,
            'Finalize only the already durable result.')
        self.assertIs(finalized.state, CollaborationStepState.INTERRUPTED)
        self.assertEqual(restarted_backend.calls, [])

    async def test_one_and_two_assignments_run_with_exact_parallel_width(self) -> None:
        for specialist_ids, declared_domains in (((PLOT_ARCHITECT,), ('plot_structure',)),
            ((PLOT_ARCHITECT, CHARACTER_CUSTODIAN), ('plot_structure', 'character_world'))):
            with self.subTest(specialist_ids=specialist_ids):
                conversation_id = self.conversation(f'Parallel width {len(specialist_ids)}')
                proposed = ( await self.proposal(conversation_id, assignments=_assignments(*specialist_ids),
                    text=_REVIEW_BRIEF + '\n\nPlease check the story direction.',
                    authoring_assessment=asdict(_review_assessment()), domains=declared_domains))
                entered = 0
                release = asyncio.Event()
                ready = asyncio.Event()

                async def overlapping(call, provider):
                    nonlocal entered
                    entered += 1
                    if entered == len(specialist_ids):
                        ready.set()
                    await asyncio.wait_for(release.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
                    return _specialist_outcome()(call, provider)

                for agent_id in specialist_ids:
                    self.backend.enqueue_for(agent_id, overlapping)
                self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
                task = _confirmed_execution_task_fixture(self.runtime, proposed)
                await asyncio.wait_for(ready.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
                self.assertEqual(entered, len(specialist_ids))
                release.set()
                decision = await task
                self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)

    async def test_synthesis_cancellation_preserves_specialist_result_and_never_reruns_it(self):
        conversation = self.conversation("Cancelled synthesis")
        proposed = await self.proposal(conversation, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        entered = asyncio.Event()

        async def blocked(_call, _provider):
            entered.set()
            await asyncio.Future()

        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, blocked)
        task = asyncio.create_task(self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)))
        try:
            await asyncio.wait_for(entered.wait(), timeout=_SYNCHRONIZATION_TIMEOUT_SECONDS)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.runtime._live_execution_markers, set())
        history = self.history(conversation)
        execution = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(execution.synthesis_outcome, CollaborationSynthesisOutcomeKind.CANCELLED)
        self.assertEqual(execution.synthesis_submitted_physical_calls, 1)
        self.assertIs(execution.assignment_outcomes[0].outcome, CollaborationAssignmentOutcomeKind.SUCCEEDED)
        self.assertEqual(len(_payloads(history, CollaborationSpecialistReplyPayload)), 1)
        backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, backend)
        result = await restarted.lead_turn(self.session_id, conversation, "Inspect the cancelled result.")
        self.assertIs(result.state, CollaborationStepState.INTERRUPTED)
        self.assertEqual(backend.calls, [])
        self.assertEqual(len(_payloads(self.history(conversation), CollaborationExecutionResult)), 1)

    async def test_execution_delivers_only_each_assignments_ordered_reference_subset(self) -> None:
        conversation_id = self.conversation("Assignment-local references")
        proposed = ( await self.proposal(conversation_id,
            text=_REVIEW_BRIEF + '\n\n' + ('Check ${core_documents/global_outline.md}, '
            '${core_documents/character_sheets.md}, and ' '${manuscript/part-01/opening.md}#L2-L3.')
            , assignments=[{'agent_id': PLOT_ARCHITECT, 'instruction': 'Check the outline against the opening range.',
            'input_ids': ['user_input', 'reference_1', 'reference_3']},
            {'agent_id': CHARACTER_CUSTODIAN, 'instruction': 'Check the character sheet only.',
            'input_ids': ['reference_2']}, {'agent_id': EXECUTION_SPECIALIST,
            'instruction': 'Assess the supplied prose.', 'input_ids': ['reference_3']}],
            authoring_assessment=asdict(_review_assessment()), domains=('plot_structure',
            'character_world', 'tone_execution')))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome())
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome("concern"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(unresolved_input_ids=[ 'reference_2']))

        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        invocation_inputs = {}
        for agent_id, call in self.backend.call_records:
            if agent_id not in {PLOT_ARCHITECT, CHARACTER_CUSTODIAN}:
                continue
            material = _workflow_material(call.payload)
            invocation_inputs[agent_id] = tuple((item['input_id'] for item in material['inputs']))
        self.assertEqual(invocation_inputs, {PLOT_ARCHITECT: ('user_input', 'reference_1',
            'reference_3'), CHARACTER_CUSTODIAN: ('reference_2',)})
        specialist_references = {agent_id: contexts for agent_id,
            contexts in self.backend.reference_records if agent_id in {PLOT_ARCHITECT,
            CHARACTER_CUSTODIAN}}
        self.assertEqual(tuple((item['content'] for item in specialist_references[PLOT_ARCHITECT])),
            ('Outline first line.\nOutline second line.\n', 'Opening two.\nOpening three.\n'))
        self.assertEqual(tuple((item['content'] for item in specialist_references[
            CHARACTER_CUSTODIAN])), ('Mara wants safety.\nMara fears exposure.\n',))
        synthesis_call = next(
            call
            for agent_id, call in self.backend.call_records
            if agent_id == LEAD_ORCHESTRATOR
            and _workflow_material(call.payload)["workflow"] == "lead_synthesis" )
        synthesis_material = _workflow_material(synthesis_call.payload)
        self.assertEqual(tuple((item['input_id'] for item in synthesis_material['inputs'])), (
            'user_input', 'reference_1', 'reference_2', 'reference_3'))

    async def test_invalid_specialist_shapes_repair_then_fall_back_without_halting(self) -> None:
        invalid_values = (
            ({"assessment": "supports"}, "$.agent_action.task_result.response_text: required property is missing"),
            ({"message": "Missing assessment."}, "$.agent_action.task_result.assignment_status: required property is missing"),
            ({"assessment": "supports", "message": "Private state claim.", "private_state": "forbidden"},
             "$.agent_action.task_result: contains 1 unexpected properties (names withheld)"), )
        for ordinal, (value, issue) in enumerate(invalid_values, start=1):
            with self.subTest(ordinal=ordinal):
                conversation_id = self.conversation(f"Strict specialist {ordinal}")
                proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
                response = _raw_outcome(value)
                self.backend.enqueue_for(PLOT_ARCHITECT, response)
                self.backend.enqueue_for(PLOT_ARCHITECT, response)
                self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
                decision = await self.runtime.decide_lead_step(_decision(proposed,
                    CollaborationConfirmationDecision.CONFIRM))
                self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
                result = _payloads(self.history(conversation_id), CollaborationExecutionResult)[0]
                self.assertIs(result.assignment_outcomes[0].outcome, CollaborationAssignmentOutcomeKind.SUCCEEDED)
                self.assertIs(result.synthesis_outcome, CollaborationSynthesisOutcomeKind.SUCCEEDED)
                reply = _payloads(self.history(conversation_id), CollaborationSpecialistReplyPayload)[0]
                self.assertIs(reply.assessment, CollaborationSpecialistAssessment.INSUFFICIENT_EVIDENCE)
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertEqual(reply.trace.action, "none")
                reports = tuple(
                    chunk for payload in _submitted_calls(self.history(conversation_id)) if payload.state is CollaborationProviderCallState.SUBMITTED
                    for chunk in payload.validation_details )
                self.assertTrue(all(issue in chunk for chunk in reports))
                self.assertTrue(any(chunk.startswith("Original response") for chunk in reports))
                self.assertTrue(any(chunk.startswith("Repair response") for chunk in reports))
                self.assertNotIn("private_state", repr(self.history(conversation_id)))

    async def test_specialist_and_synthesis_validation_details_are_private_and_correlated(
        self,
    ) -> None:
        conversation_id = self.conversation("Rejected synthesis details")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        rejected = _raw_outcome({'message': '', 'PRIVATE_SYNTHESIS_KEY': 'PRIVATE_SYNTHESIS_VALUE'})
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, rejected)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        history = self.history(conversation_id)
        submitted = _submitted_calls(history)
        synthesis = tuple(payload for payload in submitted
                          if payload.role == LEAD_ORCHESTRATOR and payload.validation_details)
        self.assertEqual(len(synthesis), 1)
        self.assertTrue(synthesis[0].validation_details[0].startswith("Original response"))
        self.assertIn("$.agent_action.task_result.response_details: "
                      "contains 1 unexpected properties",
                      repr(synthesis[0].validation_details))
        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        self.assertEqual(decision.turn.validation_details, tuple((
            chunk for payload in submitted for chunk in payload.validation_details)))
        self.assertNotIn("PRIVATE_SYNTHESIS_KEY", repr(history))
        self.assertNotIn("PRIVATE_SYNTHESIS_VALUE", repr(history))

    async def test_restarted_finalization_restores_only_this_execution_diagnostics(self) -> None:
        conversation_id = self.conversation("Restart diagnostic finalization")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        private = "PRIVATE_RESTARTED_SPECIALIST_VALUE"
        self.backend.enqueue_for(PLOT_ARCHITECT, _raw_outcome({'message': 0,
            'assessment': 'supports', private: private}))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        with _stop_after_durable_result_patch(self.runtime):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )

        known = self.history(conversation_id)
        projection = project_collaboration_history(known)
        self.assertIs(projection.current_state, CollaborationStepState.RUNNING)
        self.assertEqual(len(_payloads(known, CollaborationExecutionResult)), 1)
        expected = tuple((chunk for payload in _submitted_calls( known) for chunk in payload.validation_details))
        self.assertTrue(expected)
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize the known result.')
        self.assertIs(restored.state, CollaborationStepState.COMPLETED)
        self.assertEqual(restored.validation_details, expected)
        self.assertEqual(restarted_backend.calls, [])
        complete = self.history(conversation_id)
        repeated = restarted._finalize_known_result_locked(self.session_id, known, projection)
        self.assertEqual(repeated, restored)
        self.assertEqual(self.history(conversation_id), complete)
        self.assertNotIn(private, repr(complete))

    async def test_domain_rejection_details_survive_restarted_finalization(self) -> None:
        for rejected_role in (PLOT_ARCHITECT, LEAD_ORCHESTRATOR):
            with self.subTest(rejected_role=rejected_role):
                conversation_id = self.conversation(
                    f"Restart domain rejection from {rejected_role}" )
                proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
                if rejected_role == PLOT_ARCHITECT:
                    self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome(message=''))
                    self.backend.enqueue_for(
                        PLOT_ARCHITECT, _specialist_outcome(),
                    )
                else:
                    self.backend.enqueue_for(
                        PLOT_ARCHITECT, _specialist_outcome(),
                    )
                    self.backend.enqueue_for(
                        LEAD_ORCHESTRATOR, _synthesis_outcome(message=""),
                    )
                self.backend.enqueue_for(
                    LEAD_ORCHESTRATOR, _synthesis_outcome(),
                )
                with _stop_after_durable_result_patch(self.runtime):
                    with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                        await self.runtime.decide_lead_step(_decision(proposed,
                            CollaborationConfirmationDecision.CONFIRM))

                known = self.history(conversation_id)
                projection = project_collaboration_history(known)
                self.assertIs(projection.current_state, CollaborationStepState.RUNNING)
                self.assertEqual(len(_payloads(known, CollaborationExecutionResult)), 1)
                submitted = _submitted_calls(known)
                expected = tuple((detail for payload in _payloads(known,
                    CollaborationMessagePayload) for detail in payload.validation_details))
                self.assertEqual(
                    tuple(p.validation_details for p in submitted if p.validation_details),
                    (expected,))
                self.assertTrue(expected)
                requirement = (
                    r'expected pattern "[\\s\\S]"'
                    if rejected_role == PLOT_ARCHITECT else
                    r'expected pattern "^(?=[^\\ud800-\\udfff]*$(?![\\s\\S]))[\\s\\S]"'
                    '\n$.agent_action.task_result.response_text: '
                    'expected at least 1 characters, got 0')
                self.assertEqual(expected[0],
                    "Original response\n$.agent_action.task_result.response_text: " + requirement)

                restarted_backend = _LeadBackend()
                restarted = StoryRuntime(
                self.control_workspace, restarted_backend,  # type: ignore[arg-type]
                )
                restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize the known result.')
                self.assertIs(restored.state, CollaborationStepState.COMPLETED)
                self.assertEqual(restored.validation_details, expected)
                self.assertEqual(restarted_backend.calls, [])
                complete = self.history(conversation_id)
                repeated = restarted._finalize_known_result_locked(self.session_id, known, projection)
                self.assertEqual(repeated, restored)
                self.assertEqual(self.history(conversation_id), complete)

    async def test_concurrent_duplicate_domain_and_schema_reports_keep_call_order(self) -> None:
        conversation_id = self.conversation("Concurrent diagnostic correlation")
        proposed = await _three_way_proposal_fixture(self, conversation_id)
        for role in (PLOT_ARCHITECT, CHARACTER_CUSTODIAN):
            self.backend.enqueue_for(role, _specialist_outcome(message=""))
            self.backend.enqueue_for(role, _specialist_outcome())
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _raw_outcome({'assessment': 'supports', 'message': 0}))
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())

        with _stop_after_durable_result_patch(self.runtime):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )
        known = self.history(conversation_id)
        projection = project_collaboration_history(known)
        messages_by_call: dict[tuple[str, str], list[str]] = {}
        for event in known:
            payload = event.payload
            if (type(payload) is CollaborationMessagePayload
                and payload.validation_invocation_id is not None
                and payload.validation_call_id is not None):
                key = (payload.validation_invocation_id, payload.validation_call_id)
                messages_by_call.setdefault(key, []).extend(payload.validation_details)
        submitted = _submitted_calls(known)
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
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize concurrent results.')
        self.assertIs(restored.state, CollaborationStepState.COMPLETED)
        self.assertEqual(restored.validation_details, expected)
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(restarted._finalize_known_result_locked(self.session_id, known, projection), restored)

    async def test_failed_specialist_retries_transient_validation_publication(self) -> None:
        conversation_id = self.conversation("Retry failed specialist diagnostic append")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        private = "PRIVATE_SPECIALIST_PROVIDER_FAILURE_한🙂"
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome(message=""))
        self.backend.enqueue_for(PLOT_ARCHITECT, _SubmittedFailure(RuntimeError(private)))
        original_append = self.runtime._append_validation_messages
        attempts = 0

        def fail_once(**kwargs) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected diagnostic append conflict")
            original_append(**kwargs)

        with (mock.patch.object(self.runtime, '_append_validation_messages', fail_once),
            _stop_after_durable_result_patch(self.runtime)):
            with self.assertRaisesRegex(RuntimeError, "durable execution result"):
                await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )

        self.assertEqual(attempts, 2)
        known = self.history(conversation_id)
        projection = project_collaboration_history(known)
        self.assertIs(projection.current_state, CollaborationStepState.RUNNING)
        result = _payloads(known, CollaborationExecutionResult)[0]
        self.assertIs(result.overall_outcome, CollaborationExecutionOutcomeKind.FAILED)
        self.assertIs(result.assignment_outcomes[0].outcome,
                      CollaborationAssignmentOutcomeKind.FAILED)
        self.assertEqual(result.assignment_outcomes[0].submitted_physical_calls, 2)
        self.assertIs(result.synthesis_outcome, CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED)
        submitted = _submitted_specialist_calls(known)
        self.assertEqual(len(submitted), 2)
        notices = tuple((payload for payload in _payloads(known,
            CollaborationMessagePayload) if payload.validation_details))
        self.assertEqual(len(notices), 1)
        self.assertEqual(tuple(p.validation_details for p in submitted),
                         (notices[0].validation_details, ()))
        self.assertEqual(notices[0].validation_call_id, submitted[0].call_id)
        self.assertEqual(notices[0].validation_invocation_id, submitted[0].invocation_id)
        self.assertEqual(notices[0].validation_details[0], "Original response\n"
                         "$.agent_action.task_result.response_text: "
                         'expected pattern "[\\\\s\\\\S]"')
        self.assertNotIn(private, repr(known))

        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        restored = await restarted.lead_turn(self.session_id, conversation_id, 'Finalize failed specialist result.')
        self.assertIs(restored.state, CollaborationStepState.FAILED)
        self.assertEqual(restored.validation_details, notices[0].validation_details)
        self.assertEqual(restarted_backend.calls, [])
        self.assertNotIn(private, repr(self.history(conversation_id)))

    async def test_recovered_reply_retries_transient_validation_publication(self) -> None:
        for rejected_role in (PLOT_ARCHITECT, LEAD_ORCHESTRATOR):
            with self.subTest(rejected_role=rejected_role):
                conversation_id = self.conversation(
                    f"Retry recovered diagnostic append {rejected_role}" )
                proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
                calls_before = len(self.backend.calls)
                if rejected_role == PLOT_ARCHITECT:
                    self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome(message=''))
                    self.backend.enqueue_for(
                        PLOT_ARCHITECT, _specialist_outcome(),
                    )
                else:
                    self.backend.enqueue_for(
                        PLOT_ARCHITECT, _specialist_outcome(),
                    )
                    self.backend.enqueue_for(
                        LEAD_ORCHESTRATOR, _synthesis_outcome(message=""),
                    )
                self.backend.enqueue_for(
                    LEAD_ORCHESTRATOR, _synthesis_outcome(),
                )
                original_append = self.runtime._append_validation_messages
                attempts = 0

                def fail_once(**kwargs) -> None:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RuntimeError("injected diagnostic append conflict")
                    original_append(**kwargs)

                with (mock.patch.object(self.runtime, '_append_validation_messages', fail_once)):
                    decision = await self.runtime.decide_lead_step(_decision(proposed,
                        CollaborationConfirmationDecision.CONFIRM))
                self.assertEqual(attempts, 2)
                self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
                self.assertEqual(len(self.backend.calls) - calls_before, 3)
                history = self.history(conversation_id)
                result = _payloads(history, CollaborationExecutionResult)[0]
                self.assertIs(result.overall_outcome, CollaborationExecutionOutcomeKind.SUCCEEDED)
                notices = tuple((payload for payload in _payloads(history,
                    CollaborationMessagePayload) if payload.validation_details))
                self.assertEqual(len(notices), 1)
                self.assertEqual(decision.turn.validation_details, notices[0].validation_details)
                self.assertTrue(notices[0].validation_details[0].startswith('Original response'))
                submitted = tuple(payload for payload in _submitted_calls(history) if payload.invocation_kind in {
                       CollaborationInvocationKind.SPECIALIST_ASSIGNMENT,
                       CollaborationInvocationKind.LEAD_SYNTHESIS,})
                self.assertEqual(len(submitted), 3)
                rejected = next(payload for payload in submitted if payload.role == rejected_role
                                and payload.call_id == notices[0].validation_call_id)
                self.assertEqual(notices[0].validation_invocation_id, rejected.invocation_id)

    async def test_persistent_validation_publication_failure_keeps_result_unknown(self) -> None:
        conversation_id = self.conversation("Persistent diagnostic append failure")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome(message=""))
        self.backend.enqueue_for(PLOT_ARCHITECT, _SubmittedFailure(RuntimeError( 'definite provider failure')))
        with (mock.patch.object(self.runtime, '_append_validation_messages',
            side_effect=RuntimeError('persistent diagnostic append failure'))):
            with (self.assertRaisesRegex(RuntimeError, 'persistent diagnostic append failure')):
                await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )
        known = self.history(conversation_id)
        self.assertIs(project_collaboration_history(known).current_state,
                      CollaborationStepState.RUNNING)
        self.assertEqual(_payloads(known, CollaborationExecutionResult), ())
        self.assertEqual(_payloads(known, CollaborationTerminalOutcomePayload), ())
        submitted = _submitted_specialist_calls(known)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(self.runtime._live_execution_markers, set())

        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(
            self.control_workspace, restarted_backend,  # type: ignore[arg-type]
        )
        recovered = await restarted.lead_turn(self.session_id, conversation_id, 'Inspect the uncertain attempt.')
        self.assertIs(recovered.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertEqual(restarted_backend.calls, [])
        self.assertEqual(_payloads(self.history(conversation_id), CollaborationExecutionResult), ())

    async def test_synthesis_contract_is_semantic_and_runtime_derives_attribution(self) -> None:
        synthesis_review_candidates = []
        def minimal_synthesis(call, _provider):
            synthesis_review_candidates.append(
                dict(call.contract.semantic_index.policy.choices)['review_candidate_ids'])
            self.assertNotIn("reply_event_ids", repr(call.schema))
            self.assertNotIn("unresolved_input_ids", repr(call.schema))
            for message in ("Visible.", " \t\0"):
                self.assertEqual(LeadProviderConstraints(continuation=True).diagnose(json.dumps({"message": message})), ())
            return _provider_envelope(call, {"message": "The concerns remain visible."})
        conversation_id = self.conversation("Strict synthesis")
        proposed = (
            await self.proposal(conversation_id, assignments=_assignments(PLOT_ARCHITECT,
            CHARACTER_CUSTODIAN), text=_REVIEW_BRIEF + '\n\nPlease check the story direction.',
            authoring_assessment=asdict(_review_assessment()), domains=('plot_structure',
            'character_world'))
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome("insufficient_evidence"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, minimal_synthesis)
        decision = await self.runtime.decide_lead_step(_decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        reply_events = _events(history, CollaborationEventKind.SPECIALIST_REPLY)
        self.assertEqual(len(reply_events), 2)
        synthesis = _payloads(history, CollaborationLeadSynthesisPayload)[0]
        synthesis_call = next(event.payload for event in history if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind is CollaborationInvocationKind.LEAD_SYNTHESIS
            and event.payload.state is CollaborationProviderCallState.SUBMITTED)
        self.assertEqual(synthesis.provider_invocation_id, synthesis_call.invocation_id)
        self.assertEqual(decision.turn.provider_invocation_id, synthesis_call.invocation_id)
        self.assertIs(synthesis.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(synthesis.trace.action, "synthesis")
        self.assertEqual(synthesis.reply_event_ids, tuple(event.event_id for event in reply_events))
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, CollaborationSynthesisOutcomeKind.SUCCEEDED)
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        duplicate = {**_assignments(PLOT_ARCHITECT)[0], "mode": "review"}
        with self.assertRaises(BackendProtocolError) as caught:
            self.runtime._decode_lead_synthesis(json.dumps({"message": "Visible.", "next_intent": {
                "purpose": "Continue the review.", "assignments": [duplicate, duplicate]}}),
                proposal=proposed.proposal, attempt_id=synthesis.execution_attempt_id,
                reply_event_ids=synthesis.reply_event_ids,
                review_candidate_ids=synthesis_review_candidates[0],
                replies=tuple(event.payload for event in reply_events))
        self.assertEqual(failure_issues(caught.exception, path="$.action.result"),
                         ("$.action.result.next_intent.assignments: agent_id values must be unique before canonical ordering",))

    async def test_synthesis_derives_non_supporting_inputs_locally(self) -> None:
        conversation_id = self.conversation("Unresolved concern")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome(unresolved_input_ids=[]))

        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        synthesis = _payloads(self.history(conversation_id), CollaborationLeadSynthesisPayload)[0]
        self.assertEqual(synthesis.unresolved_input_ids, ("user_input",))
        result = _payloads(self.history(conversation_id), CollaborationExecutionResult)[0]
        self.assertIs(result.synthesis_outcome, CollaborationSynthesisOutcomeKind.SUCCEEDED)

    async def test_submitted_uncertainty_enters_unknown_and_never_replays(self) -> None:
        conversation_id = self.conversation("Uncertain specialist")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        self.backend.enqueue_for(PLOT_ARCHITECT, _SubmittedFailure(TimeoutError(
            'transport outcome unknown'), timed_out=True, submission_uncertain=True))
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        calls = len(self.backend.calls)

        self.assertIs(decision.turn.state, CollaborationStepState.OUTCOME_UNKNOWN)
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        recovery = await restarted.lead_turn(self.session_id, conversation_id, 'Do not replay the uncertain attempt.')
        self.assertIs(recovery.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(restarted_backend.calls, [])

    async def test_provider_free_advisory_confirms_one_specialist_without_synthesis_or_chain(
        self,
    ) -> None:
        conversation_id = self.conversation("Direct advisory")
        calls_before = len(self.backend.calls)
        proposed = await self.runtime.session( self.session_id
        ).propose_specialist_advisory( conversation_id,
            PLOT_ARCHITECT,
            "Candidate: a choice precedes its consequence. Assess the plot chronology." + "\n\n" + _REVIEW_LATITUDE,
            authoring_scope=AuthoringScope(AuthoringActivity.REVIEW, (), (ArtifactReviewDomain.PLOT_STRUCTURE,),
                                          "Assess the current user-supplied candidate.", "advisory_task", 1),
            review_basis=ReviewBasis(("user_input",), "Assess chronology."), authoring_assessment=_review_assessment())

        self.assertEqual(len(self.backend.calls), calls_before)
        assert proposed.proposal is not None
        self.assertIs(proposed.proposal.step_kind, CollaborationStepKind.SPECIALIST_ADVISORY)
        self.assertEqual(len(proposed.proposal.assignments), 1)
        self.assertEqual(proposed.proposal.budget.synthesis_max_physical_calls, 0)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome('concern',
            'The requested plot turn needs evidence.'))
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        self.assertIn("needs evidence", decision.turn.message)
        history = self.history(conversation_id)
        self.assertEqual(len(_payloads(history, CollaborationSpecialistReplyPayload)), 1)
        self.assertEqual(len(_payloads(history, CollaborationLeadSynthesisPayload)), 0)
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(
            result.synthesis_outcome,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )
        self.assertEqual(result.synthesis_submitted_physical_calls, 0)
        self.assertIsNone(decision.turn.proposal)

        mismatch_id = self.conversation("Advisory owner mismatch")
        with self.assertRaisesRegex(ValueError, "required story specialist|owned by another reviewer"):
            await self.runtime.propose_specialist_advisory(self.session_id, mismatch_id,
                CHARACTER_CUSTODIAN, _REVIEW_BRIEF + '\n\n' + 'Check ${core_documents/global_outline.md}.',
                authoring_scope=AuthoringScope(AuthoringActivity.REVIEW, (), (
                ArtifactReviewDomain.CHARACTER_WORLD,), 'Assess the current candidate.',
                'advisory_other', 1), review_basis=ReviewBasis(('user_input',),
                'Assess the supplied candidate.'), authoring_assessment=_review_assessment())
        self.assertFalse(self.history(mismatch_id))

    async def test_second_round_uses_reply_events_fresh_authority_and_cannot_chain_again(self) -> None:
        conversation_id = self.conversation("Second round")
        first = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\n" + "Check ${core_documents/global_outline.md}",
            assignments=[{**_assignments(PLOT_ARCHITECT)[0], "input_ids": ["user_input", "reference_1"]}], authoring_assessment=asdict(_review_assessment()))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))
        def continue_lineage(call, _provider):
            from tests.provider_wire_fixture import (
                indexed_response, lead_fixture_assignments, lead_fixture_result,
                offered_fixture_bindings, result as wire_result)
            material = _workflow_material(call.payload)
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
            assignment = {"agent_id": PLOT_ARCHITECT, "instruction": "Reassess all sources and the first reply.",
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
                anchors={PLOT_ARCHITECT: known_ids[0]},
                recipients={alias: PLOT_ARCHITECT for alias in choices['required_input_ids']}))
            grouped = lead_fixture_result(native)
            outer_id, inner_id = offered_fixture_bindings(
                call, action_type='submit_result', response_type='consultation_summary', family=0,
                branch_key='consultation-summary-v4:family:1', value=grouped)
            encoded = indexed_response(
                {'action_type': 'submit_result', 'task_result': grouped},
                outer_schema_id=outer_id, inner_schema_id=inner_id)
            self.assertEqual(diagnose_json_text(encoded, call.schema), ())
            self.assertEqual(json.loads(call.contract.decode_response(encoded).answer_json), body)
            facts = LeadProviderConstraints(input_ids=tuple(known_ids + reply_ids),
                reviewer_inputs=((PLOT_ARCHITECT, "reference_1"),), scope=first.proposal.authoring_scope, continuation=True)
            self.assertEqual(facts.diagnose(json.dumps(body)), ())
            for selected in (known_ids, [alias for alias in assignment["input_ids"] if alias != "reference_1"]):
                bad = {**body, "next_intent": {**body["next_intent"], "assignments": [{**assignment, "input_ids": selected}]}}
                self.assertIn(Rule.CONTINUATION_INPUT_COVERAGE, facts.diagnose(json.dumps(bad)))
            bad = {**body, "next_intent": {**body["next_intent"], "assignments": [{**assignment, "input_ids": ["foreign_reply"]}]}}
            self.assertIn(Rule.CONTINUATION_INPUT_NOT_OFFERED, facts.diagnose(json.dumps(bad)))
            rejected = json.loads(json.dumps(grouped))
            rejected_rows = rejected['response_details']['follow_up_consultation']['assignments']
            rejected_rows[PLOT_ARCHITECT]['input_anchor_id'] = (
                bad['next_intent']['assignments'][0]['input_ids'][0])
            self.assertTrue(diagnose_json_text(indexed_response(
                {'action_type': 'submit_result', 'task_result': rejected},
                outer_schema_id=outer_id, inner_schema_id=inner_id), call.schema))
            self.assertIn(Rule.PURPOSE_REQUIRED, facts.diagnose(json.dumps({**body, "next_intent": {**body["next_intent"], "purpose": ""}})))
            return encoded
        private = "PRIVATE_CONTINUATION_SYNTHESIS_VALUE"
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _raw_outcome({"message": "", private: private}))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, continue_lineage)
        first_decision = await self.runtime.decide_lead_step(_decision(first, CollaborationConfirmationDecision.CONFIRM))
        second = first_decision.turn
        self.assertIsNotNone(second.proposal)
        self.assertIs(second.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(second.proposal.step_id, first.step_id)
        self.assertEqual(second.proposal.proposal_revision, first.proposal.proposal_revision + 1)
        self.assertEqual((second.proposal.budget.rounds_spent, second.proposal.budget.rounds_added,
                          second.proposal.budget.physical_calls_spent), (1, 1, 4))
        first_history = self.history(conversation_id)
        expected_details = tuple(chunk for payload in _submitted_calls(first_history)
            for chunk in payload.validation_details)
        self.assertTrue(expected_details)
        self.assertEqual(second.validation_details, expected_details)
        self.assertNotIn(private, repr(first_history))
        self.assertEqual(tuple(item.kind for item in second.proposal.basis.inputs),
            (CollaborationInputKind.USER_INPUT, CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.CONVERSATION_EVENT))
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(_decision(first, CollaborationConfirmationDecision.CONFIRM))
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        second_decision = await self.runtime.decide_lead_step(_decision(second, CollaborationConfirmationDecision.CONFIRM))
        self.assertIs(second_decision.turn.state, CollaborationStepState.COMPLETED)
        self.assertIsNone(second_decision.turn.proposal)
        self.assertEqual(second_decision.turn.validation_details, ())
        syntheses = _payloads(self.history(conversation_id), CollaborationLeadSynthesisPayload)
        self.assertEqual(len(syntheses), 2)
        self.assertEqual(syntheses[0].evidence, first.proposal.basis.evidence)
        self.assertEqual(syntheses[1].evidence, second.proposal.basis.evidence)
        self.assertTrue(all(set(synthesis.reply_event_ids).isdisjoint(item.evidence_id for item in synthesis.evidence.receipts)
                            for synthesis in syntheses))
        self.assertIsNone(syntheses[-1].next_intent)


class SpecialistAccountingAcceptanceTests(LeadOrchestrationTestCase):
    async def test_local_reference_and_search_answer_count_only_provider_calls(self) -> None:
        conversation_id = self.conversation("Specialist accounting")
        self.workspace.append_agent_state(AgentStateEntry(PLOT_ARCHITECT, self.session_id, 'debate',
            'audit', 1, 'r1', 'outcome', 'own plot state needle', '2026-09-01T00:00:00+00:00'))
        self.workspace.append_agent_state(AgentStateEntry(CHARACTER_CUSTODIAN, self.session_id,
            'debate', 'audit', 1, 'r1', 'outcome', 'sibling reply secret', '2026-09-01T00:00:01+00:00'))
        proposed = await self.proposal( conversation_id,
            text=_REVIEW_BRIEF + "\n\n" + "Check ${core_documents/global_outline.md}#L1-L2.",
            assignments=[ {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Search only the bounded assignment context.",
                    "input_ids": ["user_input", "reference_1"],}
            ], authoring_assessment=asdict(_review_assessment()))

        assignment = proposed.proposal.assignments[0]

        def answer_after_read(call, provider):
            self.assertNotIn("sibling reply secret", json.dumps(dict(call.payload)))
            return _provider_envelope(call, {'assessment': 'supports',
                'assignment_id': assignment.assignment_id, 'input_ids': list(assignment.input_ids),
                'message': 'The bounded evidence supports the direction.'}, used_handles=provider.used_handles)

        def search_current_proposal(call, _provider):
            return json.dumps({'agent_action': {'action_type': 'search_context', 'queries': [
                {'search_text': 'own plot state needle', 'source_types': ['agent_work_history']},
                {'search_text': 'sibling reply secret', 'source_types': ['agent_work_history']},
            ]}}, separators=(',', ':'))

        def read_current_proposal(call, provider):
            observation = call.payload["context_observation"]
            assert (isinstance(observation, dict) and
                    observation["observation_type"] == "context_search_results")
            handle = observation["search_results"][0]["read_handle"]
            provider.used_handles = (handle,)
            return json.dumps({'agent_action': {'action_type': 'read_context',
                'read_handles': [handle]}}, separators=(',', ':'))

        self.backend.enqueue_for(PLOT_ARCHITECT, search_current_proposal,
                                 read_current_proposal, answer_after_read)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        specialist_calls = tuple(
            payload
            for payload in _payloads(history, CollaborationProviderCallPayload)
            if payload.invocation_kind
            is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT )
        self.assertEqual(tuple(((item.call_kind, item.state) for item in specialist_calls)), ((
            CollaborationProviderCallKind.INITIAL, CollaborationProviderCallState.PREPARED), (
            CollaborationProviderCallKind.INITIAL, CollaborationProviderCallState.SUBMITTED), (
            CollaborationProviderCallKind.CONTINUATION, CollaborationProviderCallState.PREPARED), (
            CollaborationProviderCallKind.CONTINUATION, CollaborationProviderCallState.SUBMITTED), (
            CollaborationProviderCallKind.CONTINUATION, CollaborationProviderCallState.PREPARED), (
            CollaborationProviderCallKind.CONTINUATION, CollaborationProviderCallState.SUBMITTED)))
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertEqual(result.assignment_outcomes[0].submitted_physical_calls, 3)
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        projection = project_collaboration_history(history)
        self.assertEqual(projection.physical_calls_spent, 5)
        self.assertLessEqual(projection.physical_calls_spent, 12)

    async def test_result_persistence_failure_cleans_marker_after_provider_completion(self) -> None:
        conversation_id = self.conversation("Result persistence failure")
        proposed = await self.proposal(conversation_id, text=_REVIEW_BRIEF + "\n\nPlease check the story direction.", authoring_assessment=asdict(_review_assessment()))
        original = self.runtime.conversation_storage_services
        failing = _FailingEventStore(original.event_store, event_kind=CollaborationEventKind.EXECUTION_RESULT)
        services = ConversationStorageServices(
            original.catalog_binding,
            replace(original.event_store_binding, component=failing),
            original.search_binding,
            original.mutation_coordinator,
        )
        runtime = StoryRuntime(
            self.workspace,
            self.backend,  # type: ignore[arg-type]
            conversation_storage_services=services,
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
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
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM) )

        self.assertEqual(failing.failures, 1)
        self.assertEqual(runtime._live_execution_markers, set())
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.RUNNING, )
        submitted = [event.payload for event in self.history(conversation_id) if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
            and event.payload.invocation_kind in {CollaborationInvocationKind.SPECIALIST_ASSIGNMENT, CollaborationInvocationKind.LEAD_SYNTHESIS}]
        self.assertEqual({result.provider_invocation_id for result in captured}, {call.invocation_id for call in submitted})
        self.assertEqual(len(captured), 2)


if __name__ == "__main__":
    unittest.main()
