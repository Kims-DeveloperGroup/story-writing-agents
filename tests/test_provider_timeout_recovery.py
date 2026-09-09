"""Read-only Lead failures preserve history and admit explicit same-chat retry."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents.agent_context.ledger import ContextLedgerKey, ContextLedgerRecordState
from story_writing_agents.agent_context.service import (
    ContextProviderFailureFacts,
    record_context_provider_failure,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    CollaborationEventKind,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationStepState,
    LeadOutcomeKind,
    project_collaboration_history,
)

from tests import test_lead_orchestration as lead
from tests.collaboration_contract_fixture import LEAD, USER, make_draft, provider_call_drafts


class ProviderTimeoutRecoveryTests(lead.LeadOrchestrationTestCase):
    def setUp(self):
        super().setUp()
        self.backend = lead._LeadBackend(persist_context_threads=True)
        self.runtime = StoryRuntime(self.workspace, self.backend)

    def failures(self, conversation):
        return [event for event in self.history(conversation)
                if type(event.payload) is CollaborationLeadOutcomeFailurePayload]

    def checkpoint(self):
        service = self.runtime._agent_context_service
        identity = service.session_continuation(self.session_id, LEAD_ORCHESTRATOR).identity
        key = ContextLedgerKey(identity.checkpoint_scope(), "codex")
        return key, service._ledger.inspect(key)

    async def answer(self, conversation, text, *, runtime=None, backend=None):
        backend = backend or self.backend
        backend.enqueue(lead._visible_outcome("answer", "Synthetic retry succeeded."))
        result = await (runtime or self.runtime).lead_turn(self.session_id, conversation, text)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        return result

    def append_legacy(self, conversation, state):
        drafts = [make_draft("legacy_request", CollaborationEventKind.MESSAGE,
                             USER, LEAD, CollaborationMessagePayload("Retained legacy request."))]
        calls = provider_call_drafts(invocation_id="legacy_invocation", call_id="legacy_call",
                                     role=LEAD_ORCHESTRATOR, recipient=LEAD,
                                     invocation_kind=CollaborationInvocationKind.LEAD)
        drafts.extend(calls[:{"bare": 0, "prepared": 1, "submitted": 2}[state]])
        self.runtime._append_drafts(0, tuple(replace(item, session_id=self.session_id,
                                                    conversation_id=conversation) for item in drafts))

    async def test_forced_timeout_restart_and_explicit_retry_preserve_both_attempts(self):
        for resumed in (False, True):
            with self.subTest(resumed=resumed):
                conversation = self.conversation()
                if resumed:
                    await self.answer(conversation, "Seed compatible provider context.")
                before = len(self.backend.calls)
                cancelled = asyncio.Event()

                async def never_finishes():
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.set()

                async def force_timeout(call, provider):
                    try:
                        await asyncio.wait_for(never_finishes(), timeout=0.01)
                    except TimeoutError as failure:
                        accounting = provider._account(call.schema, call.payload,
                                                       call_kind=call.call_kind,
                                                       component_paths=call.component_paths,
                                                       mandatory_reference_paths=call.mandatory_reference_paths)
                        record_context_provider_failure(failure, ContextProviderFailureFacts(
                            accounting, timed_out=True, taint_existing_thread=True))
                        raise

                self.backend.enqueue(force_timeout)
                original = "Original request that exceeds the timer."
                failed = await self.runtime.lead_turn(self.session_id, conversation, original)
                self.assertTrue(cancelled.is_set())
                self.assertIs(failed.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertIsNone(failed.state)
                self.assertEqual(len(self.backend.calls) - before, 1)
                closure = self.failures(conversation)
                self.assertEqual(len(closure), 1)
                self.assertIs(closure[0].payload.code, CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT)
                prefix = self.history(conversation)
                self.assertIs(self.checkpoint()[1].record.state, ContextLedgerRecordState.TAINTED)

                backend = lead._LeadBackend(persist_context_threads=True)
                backend.context_threads.update(self.backend.context_threads)
                restarted = StoryRuntime(self.workspace, backend)
                self.assertEqual(backend.calls, [])
                self.assertEqual(restarted.conversation_storage_services.event_store.read(
                    self.session_id, conversation), prefix)
                result = await self.answer(conversation, "Explicit retry after restart.",
                                           runtime=restarted, backend=backend)
                self.assertNotEqual(result.turn_id, failed.turn_id)
                self.assertEqual(len(backend.calls), 1)
                self.assertIsNone(backend.calls[0].expected_thread_id)
                self.assertEqual(backend.calls[0].payload["h"]["delivery_mode"], "bootstrap")
                history = self.history(conversation)
                self.assertEqual(history[:len(prefix)], prefix)
                texts = [event.payload.content for event in history
                         if type(event.payload) is CollaborationMessagePayload]
                self.assertEqual(texts.count(original), 1)
                self.assertEqual(texts.count("Explicit retry after restart."), 1)
                self.assertEqual(self.failures(conversation), closure)
                self.assertIn("provider_timed_out", json.dumps(backend.calls[0].payload["h"]))
                self.assertEqual({agent for agent, _ in backend.call_records}, {LEAD_ORCHESTRATOR})

    async def test_legacy_bare_prepared_and_submitted_tails_close_only_on_new_input(self):
        for state in ("bare", "prepared", "submitted"):
            with self.subTest(state=state):
                conversation = self.conversation()
                self.append_legacy(conversation, state)
                prefix = self.history(conversation)
                projection = project_collaboration_history(prefix)
                self.assertIs(projection.next_safe_action, CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertEqual(self.history(conversation), prefix)
                restarted = StoryRuntime(self.workspace, self.backend)
                before = len(self.backend.calls)
                await self.answer(conversation, "New explicit input.", runtime=restarted)
                self.assertEqual(len(self.backend.calls) - before, 1)
                self.assertEqual(self.history(conversation)[:len(prefix)], prefix)
                closure = self.failures(conversation)
                self.assertEqual(len(closure), 1)
                self.assertIs(closure[0].payload.code,
                              CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED if state == "bare"
                              else CollaborationLeadOutcomeFailureCode.INTERRUPTED)
                self.assertEqual(closure[0].payload.call_id, None if state == "bare" else "legacy_call")
                self.assertEqual(closure[0].sequence, len(prefix) + 1)

    async def test_matching_unfinished_legacy_operation_retires_native_context(self):
        await self.answer(self.conversation(), "Seed a native session context.")
        key, checkpoint = self.checkpoint()
        retained_thread = checkpoint.checkpoint_view.provider_thread_id
        conversation = self.conversation()
        self.append_legacy(conversation, "submitted")
        plan = replace(checkpoint.record.attempt.plan, attempt_id="abandoned_attempt",
                       operation_id="lead_turn_a", result_correlation_id="abandoned_result")
        self.runtime._agent_context_service._ledger.reserve_attempt(
            key, expected_generation=checkpoint.generation, plan=plan)
        restarted = StoryRuntime(self.workspace, self.backend)
        await self.answer(conversation, "Retry after interrupted native work.", runtime=restarted)
        self.assertIn(retained_thread, self.backend.tainted_thread_ids)
        self.assertIsNone(self.backend.calls[-1].expected_thread_id)
        self.assertEqual(self.backend.calls[-1].payload["h"]["delivery_mode"], "bootstrap")

    async def test_abandoned_turn_preserves_other_conversation_successful_checkpoint(self):
        failed_conversation, successful_conversation = self.conversation(), self.conversation()
        self.backend.enqueue(lead._SubmittedFailure(TimeoutError(), timed_out=True))
        await self.runtime.lead_turn(self.session_id, failed_conversation, "Failed conversation input.")
        await self.answer(successful_conversation, "Successful other conversation input.")
        _, checkpoint = self.checkpoint()
        retained_thread = checkpoint.checkpoint_view.provider_thread_id
        self.assertIsNotNone(retained_thread)
        before_taints = list(self.backend.tainted_thread_ids)
        restarted = StoryRuntime(self.workspace, self.backend)
        await self.answer(failed_conversation, "Explicit retry in failed conversation.", runtime=restarted)
        self.assertEqual(self.backend.calls[-1].expected_thread_id, retained_thread)
        self.assertEqual(self.backend.calls[-1].payload["h"]["delivery_mode"], "delta")
        self.assertEqual(self.backend.tainted_thread_ids, before_taints)

    async def test_revision_timeout_keeps_superseded_confirmation_non_authorizing(self):
        conversation = self.conversation()
        proposed = await self.proposal(conversation)
        self.backend.enqueue(lead._SubmittedFailure(TimeoutError(), timed_out=True))
        result = await self.runtime.decide_lead_step(
            lead._decision(proposed, CollaborationConfirmationDecision.REVISE))
        self.assertIs(result.turn.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.failures(conversation)), 1)
        before = len(self.backend.calls)
        restarted = StoryRuntime(self.workspace, self.backend)
        with self.assertRaisesRegex(ValueError, "no authoritative pending request"):
            await restarted.decide_lead_step(lead._decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(len(self.backend.calls), before)
        await self.answer(conversation, "New request after failed revision.", runtime=restarted)
        self.assertEqual(len(self.backend.calls), before + 1)
        self.assertEqual({agent for agent, _ in self.backend.call_records}, {LEAD_ORCHESTRATOR})

    async def test_attached_reference_cancellation_closes_and_releases_for_explicit_retry(self):
        conversation = self.conversation()
        entered = asyncio.Event()

        async def blocked(call, provider):
            entered.set()
            await asyncio.Future()

        self.backend.enqueue(blocked)
        original = "Inspect ${core_documents/global_outline.md} before answering."
        task = asyncio.create_task(self.runtime.lead_turn(self.session_id, conversation, original))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(self.backend.reference_calls), 1)
        self.assertTrue(self.backend.providers[-1]._reference_terminal)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertIs(self.failures(conversation)[0].payload.code, CollaborationLeadOutcomeFailureCode.CANCELLED)
        restarted = StoryRuntime(self.workspace, self.backend)
        async with asyncio.timeout(2):
            await self.answer(conversation, "A new request after cancellation.", runtime=restarted)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(len(self.backend.reference_calls), 1)
        self.assertIsNone(self.backend.calls[-1].expected_thread_id)
        self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload
                             and event.payload.content == original for event in self.history(conversation)), 1)

    async def test_failure_closure_write_error_keeps_history_gated(self):
        conversation = self.conversation()
        self.backend.enqueue(lead._SubmittedFailure(TimeoutError(), timed_out=True))
        store_type = type(self.runtime.conversation_storage_services.event_store)
        append = store_type.append_batch

        def fail_closure(store, batch):
            if any(type(event.payload) is CollaborationLeadOutcomeFailurePayload for event in batch.events):
                raise OSError("synthetic unavailable authoritative history")
            return append(store, batch)

        with mock.patch.object(store_type, "append_batch", new=fail_closure):
            result = await self.runtime.lead_turn(self.session_id, conversation, "Keep failed input.")
            self.assertIs(result.state, CollaborationStepState.RECOVERY_REQUIRED)
            prefix = self.history(conversation)
            blocked = await self.runtime.lead_turn(self.session_id, conversation, "Must wait for durable closure.")
            self.assertIs(blocked.state, CollaborationStepState.RECOVERY_REQUIRED)
            self.assertEqual(self.history(conversation), prefix)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.failures(conversation), [])
        await self.answer(conversation, "Retry once authoritative history is writable.")
        self.assertEqual(len(self.backend.calls), 2)

    async def test_corrupt_failed_history_cannot_admit_provider_work(self):
        conversation = self.conversation()
        self.backend.enqueue(lead._SubmittedFailure(TimeoutError(), timed_out=True))
        await self.runtime.lead_turn(self.session_id, conversation, "Original failed input.")
        events = self.history(conversation)
        store_type = type(self.runtime.conversation_storage_services.event_store)
        with mock.patch.object(store_type, "read", return_value=(events[-1],) + events[:-1]):
            result = await self.runtime.lead_turn(self.session_id, conversation, "Do not use corrupt history.")
        self.assertIs(result.state, CollaborationStepState.RECOVERY_REQUIRED)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.history(conversation), events)
