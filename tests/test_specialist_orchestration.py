"""Permanent execution acceptance coverage for confirmed specialist collaboration."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace

from story_writing_agents import StoryRuntime
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversation_storage import ConversationStorageServices
from story_writing_agents.conversations import (
    CollaborationAssignmentOutcomeKind,
    CollaborationConfirmationDecision,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationLeadSynthesisPayload,
    CollaborationNextAction,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    project_collaboration_history,
)
from story_writing_agents.models import AgentStateEntry
from story_writing_agents.specialist_routing import AuthoringScope, AuthoringActivity, ArtifactReviewDomain, ReviewBasis

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _SubmittedFailure,
    _decision,
    _provider_envelope,
    _read_history_hit,
    _specialist_outcome,
    _synthesis_outcome,
    _workflow_material,
)


def _assignments(*agent_ids: str) -> list[dict[str, object]]:
    return [
        {
            "agent_id": agent_id,
            "instruction": f"Return the bounded {agent_id} assessment.",
            "input_ids": ["user_input"],
        }
        for agent_id in agent_ids
    ]


def _raw_outcome(value: object):
    def respond(call, _provider):
        return _provider_envelope(call, value)

    return respond


def _payloads(history, payload_type):
    return tuple(
        event.payload for event in history if type(event.payload) is payload_type
    )


def _events(history, kind: CollaborationEventKind):
    return tuple(event for event in history if event.kind is kind)


class _FailingEventStore:
    """Delegating test port that fails one selected durable append family."""

    def __init__(self, delegate, *, state=None, event_kind=None) -> None:
        self.delegate = delegate
        self.state = state
        self.event_kind = event_kind
        self.enabled = False
        self.failures = 0

    def append_batch(self, batch):
        matches_state = self.state is not None and any(
            getattr(event.payload, "state", None) is self.state
            for event in batch.events
        )
        matches_kind = self.event_kind is not None and any(
            event.kind is self.event_kind for event in batch.events
        )
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
        proposed = await self.proposal(
            conversation_id,
            assignments=_assignments(
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        self.assertEqual(
            tuple(item.max_physical_calls for item in proposed.proposal.assignments),
            (4, 3, 3),
        )
        self.assertEqual(proposed.proposal.budget.synthesis_max_physical_calls, 1)
        self.assertEqual(
            sum(item.max_physical_calls for item in proposed.proposal.assignments)
            + proposed.proposal.budget.synthesis_max_physical_calls
            + proposed.proposal.budget.physical_calls_spent,
            12,
        )
        entered = 0
        all_entered = asyncio.Event()
        release = asyncio.Event()
        peak = 0
        active = 0

        async def overlapping(call, provider):
            nonlocal active, entered, peak
            entered += 1
            active += 1
            peak = max(peak, active)
            if entered == 3:
                all_entered.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            active -= 1
            return _specialist_outcome()(call, provider)

        for agent_id in (
            PLOT_ARCHITECT,
            CHARACTER_CUSTODIAN,
            EXECUTION_SPECIALIST,
        ):
            self.backend.enqueue_for(agent_id, overlapping)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())

        task = asyncio.create_task(
            self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )
        )
        await asyncio.wait_for(all_entered.wait(), timeout=2)
        self.assertEqual(peak, 3)
        release.set()
        decision = await task

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        history = self.history(conversation_id)
        running = next(
            event
            for event in history
            if getattr(event.payload, "state", None) is CollaborationStepState.RUNNING
        )
        start_batch = tuple(
            event for event in history if event.batch_id == running.batch_id
        )
        self.assertEqual(
            tuple(event.kind for event in start_batch),
            (
                CollaborationEventKind.STEP_STATE,
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
            ),
        )
        self.assertEqual(
            tuple(event.recipient.agent_id for event in start_batch[1:]),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        )
        first_call = next(
            event
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.invocation_kind
            is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT
        )
        self.assertLess(start_batch[-1].sequence, first_call.sequence)
        replies = _payloads(history, CollaborationSpecialistReplyPayload)
        self.assertEqual(
            tuple(reply.assignment_id for reply in replies),
            tuple(item.assignment_id for item in proposed.proposal.assignments),
        )
        synthesis = _payloads(history, CollaborationLeadSynthesisPayload)
        self.assertEqual(len(synthesis), 1)
        self.assertEqual(
            synthesis[0].reply_event_ids,
            tuple(
                event.event_id
                for event in _events(history, CollaborationEventKind.SPECIALIST_REPLY)
            ),
        )
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(
            result.overall_outcome, CollaborationExecutionOutcomeKind.SUCCEEDED
        )
        self.assertTrue(
            all(
                item.outcome is CollaborationAssignmentOutcomeKind.SUCCEEDED
                for item in result.assignment_outcomes
            )
        )
        self.assertIs(
            result.synthesis_outcome, CollaborationSynthesisOutcomeKind.SUCCEEDED
        )

    async def test_start_append_failure_makes_zero_specialist_calls_and_leaks_no_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Start append failure")
        proposed = await self.proposal(conversation_id)
        calls_before = len(self.backend.calls)
        original = self.runtime.conversation_storage_services
        failing = _FailingEventStore(
            original.event_store,
            state=CollaborationStepState.RUNNING,
        )
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
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

        self.assertEqual(len(self.backend.calls), calls_before)
        self.assertEqual(failing.failures, 1)
        self.assertEqual(runtime._live_execution_markers, set())
        projection = project_collaboration_history(self.history(conversation_id))
        self.assertIs(projection.current_state, CollaborationStepState.CONFIRMED)

    async def test_assignment_failure_joins_siblings_and_skips_synthesis(self) -> None:
        conversation_id = self.conversation("Partial specialist failure")
        proposed = await self.proposal(
            conversation_id,
            assignments=_assignments(
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                EXECUTION_SPECIALIST,
            ),
        )
        sibling_started = asyncio.Event()
        sibling_release = asyncio.Event()
        sibling_finishes = 0

        async def sibling(call, provider):
            nonlocal sibling_finishes
            sibling_started.set()
            await asyncio.wait_for(sibling_release.wait(), timeout=2)
            sibling_finishes += 1
            return _specialist_outcome()(call, provider)

        self.backend.enqueue_for(
            PLOT_ARCHITECT,
            _SubmittedFailure(RuntimeError("plot failed after submission")),
        )
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, sibling)
        self.backend.enqueue_for(EXECUTION_SPECIALIST, sibling)
        task = asyncio.create_task(
            self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )
        )
        await asyncio.wait_for(sibling_started.wait(), timeout=2)
        sibling_release.set()
        decision = await task

        self.assertEqual(sibling_finishes, 2)
        self.assertIs(decision.turn.state, CollaborationStepState.FAILED)
        self.assertIs(
            decision.turn.next_action,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
        )
        history = self.history(conversation_id)
        self.assertEqual(len(_payloads(history, CollaborationLeadSynthesisPayload)), 0)
        self.assertEqual(
            len(_payloads(history, CollaborationSpecialistReplyPayload)), 2
        )
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertEqual(
            tuple(item.outcome for item in result.assignment_outcomes),
            (
                CollaborationAssignmentOutcomeKind.FAILED,
                CollaborationAssignmentOutcomeKind.SUCCEEDED,
                CollaborationAssignmentOutcomeKind.SUCCEEDED,
            ),
        )
        self.assertIs(
            result.synthesis_outcome,
            CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
        )

    async def test_outer_cancellation_joins_tasks_persists_terminals_and_cleans_marker(
        self,
    ) -> None:
        conversation_id = self.conversation("Cancellation")
        proposed = await self.proposal(
            conversation_id,
            assignments=_assignments(PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
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
        task = asyncio.create_task(
            self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )
        )
        await asyncio.wait_for(both_started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(self.runtime._live_execution_markers, set())
        history = self.history(conversation_id)
        self.assertIs(
            project_collaboration_history(history).current_state,
            CollaborationStepState.RUNNING,
        )
        submitted = tuple(
            item
            for item in _payloads(history, CollaborationProviderCallPayload)
            if item.invocation_kind is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT
            and item.state is CollaborationProviderCallState.SUBMITTED
        )
        self.assertEqual(len(submitted), 2)
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(
            result.overall_outcome, CollaborationExecutionOutcomeKind.CANCELLED
        )
        self.assertEqual(
            tuple(item.outcome for item in result.assignment_outcomes),
            (
                CollaborationAssignmentOutcomeKind.CANCELLED,
                CollaborationAssignmentOutcomeKind.CANCELLED,
            ),
        )
        self.assertEqual(
            len(_payloads(history, CollaborationTerminalOutcomePayload)), 2
        )
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        finalized = await restarted.lead_turn(
            self.session_id,
            conversation_id,
            "Finalize only the already durable result.",
        )
        self.assertIs(finalized.state, CollaborationStepState.INTERRUPTED)
        self.assertEqual(restarted_backend.calls, [])

    async def test_one_and_two_assignments_run_with_exact_parallel_width(self) -> None:
        for specialist_ids in (
            (PLOT_ARCHITECT,),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        ):
            with self.subTest(specialist_ids=specialist_ids):
                conversation_id = self.conversation(
                    f"Parallel width {len(specialist_ids)}"
                )
                proposed = await self.proposal(
                    conversation_id,
                    assignments=_assignments(*specialist_ids),
                )
                entered = 0
                release = asyncio.Event()
                ready = asyncio.Event()

                async def overlapping(call, provider):
                    nonlocal entered
                    entered += 1
                    if entered == len(specialist_ids):
                        ready.set()
                    await asyncio.wait_for(release.wait(), timeout=2)
                    return _specialist_outcome()(call, provider)

                for agent_id in specialist_ids:
                    self.backend.enqueue_for(agent_id, overlapping)
                self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
                task = asyncio.create_task(
                    self.runtime.decide_lead_step(
                        _decision(
                            proposed,
                            CollaborationConfirmationDecision.CONFIRM,
                        )
                    )
                )
                await asyncio.wait_for(ready.wait(), timeout=2)
                self.assertEqual(entered, len(specialist_ids))
                release.set()
                decision = await task
                self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)

    async def test_synthesis_cancellation_preserves_specialist_result_and_never_reruns_it(self):
        conversation = self.conversation("Cancelled synthesis")
        proposed = await self.proposal(conversation)
        entered = asyncio.Event()

        async def blocked(_call, _provider):
            entered.set()
            await asyncio.Future()

        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, blocked)
        task = asyncio.create_task(self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
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

    async def test_execution_delivers_only_each_assignments_ordered_reference_subset(
        self,
    ) -> None:
        conversation_id = self.conversation("Assignment-local references")
        proposed = await self.proposal(
            conversation_id,
            text=(
                "Check ${core_documents/global_outline.md}, "
                "${core_documents/character_sheets.md}, and "
                "${manuscript/part-01/opening.md}#L2-L3."
            ),
            assignments=[
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Check the outline against the opening range.",
                    "input_ids": ["user_input", "reference_1", "reference_3"],
                },
                {
                    "agent_id": CHARACTER_CUSTODIAN,
                    "instruction": "Check the character sheet only.",
                    "input_ids": ["reference_2"],
                },
                {"agent_id": EXECUTION_SPECIALIST, "instruction": "Assess the supplied prose.",
                 "input_ids": ["reference_3"]},
            ],
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(EXECUTION_SPECIALIST, _specialist_outcome())
        self.backend.enqueue_for(CHARACTER_CUSTODIAN, _specialist_outcome("concern"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _synthesis_outcome(unresolved_input_ids=["reference_2"]),
        )

        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        invocation_inputs = {}
        for agent_id, call in self.backend.call_records:
            if agent_id not in {PLOT_ARCHITECT, CHARACTER_CUSTODIAN}:
                continue
            material = _workflow_material(call.payload)
            invocation_inputs[agent_id] = tuple(
                item["input_id"] for item in material["inputs"]
            )
        self.assertEqual(
            invocation_inputs,
            {
                PLOT_ARCHITECT: ("user_input", "reference_1", "reference_3"),
                CHARACTER_CUSTODIAN: ("reference_2",),
            },
        )
        specialist_references = {
            agent_id: contexts
            for agent_id, contexts in self.backend.reference_records
            if agent_id in {PLOT_ARCHITECT, CHARACTER_CUSTODIAN}
        }
        self.assertEqual(
            tuple(item["content"] for item in specialist_references[PLOT_ARCHITECT]),
            (
                "Outline first line.\nOutline second line.\n",
                "Opening two.\nOpening three.\n",
            ),
        )
        self.assertEqual(
            tuple(
                item["content"] for item in specialist_references[CHARACTER_CUSTODIAN]
            ),
            ("Mara wants safety.\nMara fears exposure.\n",),
        )
        synthesis_call = next(
            call
            for agent_id, call in self.backend.call_records
            if agent_id == LEAD_ORCHESTRATOR
            and _workflow_material(call.payload)["workflow"] == "lead_synthesis"
        )
        synthesis_material = _workflow_material(synthesis_call.payload)
        self.assertEqual(
            tuple(item["input_id"] for item in synthesis_material["inputs"]),
            ("user_input", "reference_1", "reference_2", "reference_3"),
        )

    async def test_strict_specialist_contract_rejects_wrong_extra_private_and_reordered_inputs(
        self,
    ) -> None:
        invalid_values = (
            {
                "assessment": "supports",
                "assignment_id": "wrong_assignment",
                "input_ids": ["user_input"],
                "message": "Wrong correlation.",
            },
            {
                "assessment": "supports",
                "assignment_id": None,
                "input_ids": ["user_input"],
                "message": "Extra field.",
                "extra": True,
            },
            {
                "assessment": "supports",
                "assignment_id": None,
                "input_ids": ["user_input"],
                "message": "Private state claim.",
                "private_state": "forbidden",
            },
            {
                "assessment": "supports",
                "assignment_id": None,
                "input_ids": ["user_input"],
                "message": "Mutation request.",
                "mutation_request": "change the outline",
            },
            {
                "assessment": "supports",
                "assignment_id": None,
                "input_ids": ["user_input"],
                "message": "Delegation request.",
                "delegate_to": CHARACTER_CUSTODIAN,
            },
        )
        for ordinal, template in enumerate(invalid_values, start=1):
            with self.subTest(ordinal=ordinal):
                conversation_id = self.conversation(f"Strict specialist {ordinal}")
                proposed = await self.proposal(conversation_id)
                value = dict(template)
                if value["assignment_id"] is None:
                    value["assignment_id"] = proposed.proposal.assignments[
                        0
                    ].assignment_id
                self.backend.enqueue_for(PLOT_ARCHITECT, _raw_outcome(value))
                decision = await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
                )
                self.assertIs(decision.turn.state, CollaborationStepState.FAILED)
                result = _payloads(
                    self.history(conversation_id), CollaborationExecutionResult
                )[0]
                self.assertIs(
                    result.assignment_outcomes[0].outcome,
                    CollaborationAssignmentOutcomeKind.FAILED,
                )
                self.assertIs(
                    result.synthesis_outcome,
                    CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
                )

        conversation_id = self.conversation("Strict input order")
        proposed = await self.proposal(
            conversation_id,
            text=(
                "Check ${core_documents/global_outline.md}#L2-L2 and "
                "${core_documents/global_outline.md}#L1-L1."
            ),
            assignments=[
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Check both sources.",
                    "input_ids": ["user_input", "reference_1", "reference_2"],
                }
            ],
        )
        assignment = proposed.proposal.assignments[0]
        self.backend.enqueue_for(
            PLOT_ARCHITECT,
            _raw_outcome(
                {
                    "assessment": "supports",
                    "assignment_id": assignment.assignment_id,
                    "input_ids": list(reversed(assignment.input_ids)),
                    "message": "Reordered evidence.",
                }
            ),
        )
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        self.assertIs(decision.turn.state, CollaborationStepState.FAILED)

    async def test_strict_synthesis_requires_exact_order_attribution_and_unresolved_ids(
        self,
    ) -> None:
        def invalid_synthesis(call, _provider):
            contract = _workflow_material(call.payload)["answer_contract"]
            assert type(contract) is dict
            return _provider_envelope(
                call,
                {
                    "message": "Malformed attribution.",
                    "next_intent": None,
                    "reply_event_ids": list(reversed(contract["reply_event_ids"])),
                    "unresolved_input_ids": ["unknown_input"],
                },
            )

        conversation_id = self.conversation("Strict synthesis")
        proposed = await self.proposal(
            conversation_id,
            assignments=_assignments(PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))
        self.backend.enqueue_for(
            CHARACTER_CUSTODIAN,
            _specialist_outcome("insufficient_evidence"),
        )
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, invalid_synthesis)

        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.FAILED)
        history = self.history(conversation_id)
        self.assertEqual(
            len(_payloads(history, CollaborationSpecialistReplyPayload)), 2
        )
        self.assertEqual(len(_payloads(history, CollaborationLeadSynthesisPayload)), 0)
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertIs(
            result.synthesis_outcome, CollaborationSynthesisOutcomeKind.FAILED
        )
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)

    async def test_synthesis_cannot_erase_non_supporting_specialist_inputs(
        self,
    ) -> None:
        conversation_id = self.conversation("Unresolved concern")
        proposed = await self.proposal(conversation_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            _synthesis_outcome(unresolved_input_ids=[]),
        )

        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.FAILED)
        result = _payloads(self.history(conversation_id), CollaborationExecutionResult)[
            0
        ]
        self.assertIs(
            result.synthesis_outcome, CollaborationSynthesisOutcomeKind.FAILED
        )

    async def test_submitted_uncertainty_enters_unknown_and_never_replays(self) -> None:
        conversation_id = self.conversation("Uncertain specialist")
        proposed = await self.proposal(conversation_id)
        self.backend.enqueue_for(
            PLOT_ARCHITECT,
            _SubmittedFailure(
                TimeoutError("transport outcome unknown"),
                timed_out=True,
                submission_uncertain=True,
            ),
        )
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        calls = len(self.backend.calls)

        self.assertIs(decision.turn.state, CollaborationStepState.OUTCOME_UNKNOWN)
        restarted_backend = _LeadBackend()
        restarted = StoryRuntime(self.control_workspace, restarted_backend)  # type: ignore[arg-type]
        recovery = await restarted.lead_turn(
            self.session_id,
            conversation_id,
            "Do not replay the uncertain attempt.",
        )
        self.assertIs(recovery.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(restarted_backend.calls, [])

    async def test_provider_free_advisory_confirms_one_specialist_without_synthesis_or_chain(
        self,
    ) -> None:
        conversation_id = self.conversation("Direct advisory")
        calls_before = len(self.backend.calls)
        proposed = await self.runtime.session(
            self.session_id
        ).propose_specialist_advisory(
            conversation_id,
            PLOT_ARCHITECT,
            "Candidate: a choice precedes its consequence. Assess the plot chronology.",
            authoring_scope=AuthoringScope(AuthoringActivity.REVIEW, (), (ArtifactReviewDomain.PLOT_STRUCTURE,),
                                          "Assess the current user-supplied candidate.", "advisory_task", 1),
            review_basis=ReviewBasis(("user_input",), "Assess chronology."),
        )

        self.assertEqual(len(self.backend.calls), calls_before)
        assert proposed.proposal is not None
        self.assertIs(
            proposed.proposal.step_kind,
            CollaborationStepKind.SPECIALIST_ADVISORY,
        )
        self.assertEqual(len(proposed.proposal.assignments), 1)
        self.assertEqual(proposed.proposal.budget.synthesis_max_physical_calls, 0)
        self.backend.enqueue_for(
            PLOT_ARCHITECT,
            _specialist_outcome("concern", "The requested plot turn needs evidence."),
        )
        decision = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(decision.turn.state, CollaborationStepState.COMPLETED)
        self.assertIn("needs evidence", decision.turn.message)
        history = self.history(conversation_id)
        self.assertEqual(
            len(_payloads(history, CollaborationSpecialistReplyPayload)), 1
        )
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
            await self.runtime.propose_specialist_advisory(
                self.session_id,
                mismatch_id,
                CHARACTER_CUSTODIAN,
                "Check ${core_documents/global_outline.md}.",
                authoring_scope=AuthoringScope(AuthoringActivity.REVIEW, (), (ArtifactReviewDomain.CHARACTER_WORLD,),
                                              "Assess the current candidate.", "advisory_other", 1),
                review_basis=ReviewBasis(("user_input",), "Assess the supplied candidate."),
            )
        self.assertFalse(self.history(mismatch_id))

    async def test_second_round_uses_reply_events_fresh_authority_and_cannot_chain_again(
        self,
    ) -> None:
        conversation_id = self.conversation("Second round")
        first = await self.proposal(conversation_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("concern"))

        def continue_lineage(call, _provider):
            material = _workflow_material(call.payload)
            contract = material["answer_contract"]
            assert type(contract) is dict
            reply_ids = list(contract["reply_event_ids"])
            known_ids = [item["input_id"] for item in material["inputs"]]
            return _provider_envelope(
                call,
                {
                    "recommended_action": None,
                    "evidence_ids": contract["evidence_ids"],
                    "evidence_status": contract["evidence_status"],
                    "message": "One issue needs a focused second review.",
                    "scope": contract["scope"],
                    "reply_event_ids": reply_ids,
                    "unresolved_input_ids": known_ids,
                    "next_intent": {
                        "relation": "continue_lineage",
                        "purpose": "Resolve the remaining plot concern.",
                        "assignments": [
                            {
                                "agent_id": PLOT_ARCHITECT,
                                "instruction": "Reassess using the first reply.",
                                "input_ids": [*known_ids, *reply_ids],
                            }
                        ],
                    },
                },
            )

        self.backend.enqueue_for(LEAD_ORCHESTRATOR, continue_lineage)
        first_decision = await self.runtime.decide_lead_step(
            _decision(first, CollaborationConfirmationDecision.CONFIRM)
        )
        second = first_decision.turn
        assert second.proposal is not None
        self.assertIs(second.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(second.proposal.step_id, first.step_id)
        self.assertEqual(
            second.proposal.proposal_revision,
            first.proposal.proposal_revision + 1,
        )
        self.assertEqual(second.proposal.budget.rounds_spent, 1)
        self.assertEqual(second.proposal.budget.rounds_added, 1)
        self.assertEqual(second.proposal.budget.physical_calls_spent, 3)
        self.assertEqual(
            tuple(item.kind for item in second.proposal.basis.inputs),
            (
                CollaborationInputKind.USER_INPUT,
                CollaborationInputKind.CONVERSATION_EVENT,
            ),
        )
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(
                _decision(first, CollaborationConfirmationDecision.CONFIRM)
            )

        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome("supports"))
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        second_decision = await self.runtime.decide_lead_step(
            _decision(second, CollaborationConfirmationDecision.CONFIRM)
        )
        self.assertIs(second_decision.turn.state, CollaborationStepState.COMPLETED)
        self.assertIsNone(second_decision.turn.proposal)
        history = self.history(conversation_id)
        syntheses = _payloads(history, CollaborationLeadSynthesisPayload)
        self.assertEqual(len(syntheses), 2)
        self.assertEqual(syntheses[0].evidence, first.proposal.basis.evidence)
        self.assertEqual(syntheses[1].evidence, second.proposal.basis.evidence)
        for synthesis in syntheses:
            self.assertTrue(
                set(synthesis.reply_event_ids).isdisjoint(
                    item.evidence_id for item in synthesis.evidence.receipts
                )
            )
        self.assertIsNone(syntheses[-1].next_intent)


class SpecialistAccountingAcceptanceTests(LeadOrchestrationTestCase):
    async def test_reference_prelude_and_search_answer_spend_every_submitted_call(
        self,
    ) -> None:
        conversation_id = self.conversation("Specialist accounting")
        self.workspace.append_agent_state(
            AgentStateEntry(
                PLOT_ARCHITECT,
                self.session_id,
                "debate",
                "audit",
                1,
                "r1",
                "outcome",
                "own plot state needle",
                "2026-09-01T00:00:00+00:00",
            )
        )
        self.workspace.append_agent_state(
            AgentStateEntry(
                CHARACTER_CUSTODIAN,
                self.session_id,
                "debate",
                "audit",
                1,
                "r1",
                "outcome",
                "sibling reply secret",
                "2026-09-01T00:00:01+00:00",
            )
        )
        proposed = await self.proposal(
            conversation_id,
            text="Check ${core_documents/global_outline.md}#L1-L2.",
            assignments=[
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Search only the bounded assignment context.",
                    "input_ids": ["user_input", "reference_1"],
                }
            ],
        )

        assignment = proposed.proposal.assignments[0]

        def answer_after_read(call, provider):
            self.assertNotIn("sibling reply secret", json.dumps(dict(call.payload)))
            return _provider_envelope(
                call,
                {
                    "assessment": "supports",
                    "assignment_id": assignment.assignment_id,
                    "input_ids": list(assignment.input_ids),
                    "message": "The bounded evidence supports the direction.",
                },
                used_handles=provider.used_handles,
            )

        def search_current_proposal(call, _provider):
            return json.dumps(
                {
                    "correlation": call.payload["c"],
                    "action": {
                        "kind": "search",
                        "path_prefix": None,
                        "queries": [
                            {
                                "text": "own plot state needle",
                                "source_kinds": ["own_agent_state"],
                            },
                            {
                                "text": "sibling reply secret",
                                "source_kinds": ["own_agent_state"],
                            },
                        ],
                    },
                },
                separators=(",", ":"),
            )

        self.backend.enqueue_for(
            PLOT_ARCHITECT,
            search_current_proposal,
            _read_history_hit,
            answer_after_read,
        )
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
            is CollaborationInvocationKind.SPECIALIST_ASSIGNMENT
        )
        self.assertEqual(
            tuple((item.call_kind, item.state) for item in specialist_calls),
            (
                (
                    CollaborationProviderCallKind.REFERENCE_PRELUDE,
                    CollaborationProviderCallState.PREPARED,
                ),
                (
                    CollaborationProviderCallKind.REFERENCE_PRELUDE,
                    CollaborationProviderCallState.SUBMITTED,
                ),
                (
                    CollaborationProviderCallKind.INITIAL,
                    CollaborationProviderCallState.PREPARED,
                ),
                (
                    CollaborationProviderCallKind.INITIAL,
                    CollaborationProviderCallState.SUBMITTED,
                ),
                (
                    CollaborationProviderCallKind.CONTINUATION,
                    CollaborationProviderCallState.PREPARED,
                ),
                (
                    CollaborationProviderCallKind.CONTINUATION,
                    CollaborationProviderCallState.SUBMITTED,
                ),
                (
                    CollaborationProviderCallKind.CONTINUATION,
                    CollaborationProviderCallState.PREPARED,
                ),
                (
                    CollaborationProviderCallKind.CONTINUATION,
                    CollaborationProviderCallState.SUBMITTED,
                ),
            ),
        )
        result = _payloads(history, CollaborationExecutionResult)[0]
        self.assertEqual(result.assignment_outcomes[0].submitted_physical_calls, 4)
        self.assertEqual(result.synthesis_submitted_physical_calls, 2)
        projection = project_collaboration_history(history)
        self.assertEqual(projection.physical_calls_spent, 8)
        self.assertLessEqual(projection.physical_calls_spent, 12)

    async def test_result_persistence_failure_cleans_marker_after_provider_completion(
        self,
    ) -> None:
        conversation_id = self.conversation("Result persistence failure")
        proposed = await self.proposal(conversation_id)
        original = self.runtime.conversation_storage_services
        failing = _FailingEventStore(
            original.event_store,
            event_kind=CollaborationEventKind.EXECUTION_RESULT,
        )
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

        with self.assertRaisesRegex(RuntimeError, "injected durable append failure"):
            await runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

        self.assertEqual(failing.failures, 1)
        self.assertEqual(runtime._live_execution_markers, set())
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.RUNNING,
        )


if __name__ == "__main__":
    unittest.main()
