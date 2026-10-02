"""Exact session memory, recovery, and publication safety using synthetic providers."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents import ReplyTraceOrigin, StoryRuntime
from story_writing_agents.agent_context.continuation import ContextContinuationTransaction
from story_writing_agents.agent_context.ledger import ContextLedgerKey, ContextLedgerRecordState
from story_writing_agents.agent_context.manifest import ContextContinuationIdentity, ContextScope, ContextSubjectKind
from story_writing_agents.agent_context.service import ContextContinuationRejection
from story_writing_agents.agent_context.sources import SessionHistorySourceAdapter
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.conversations import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationDecisionPayload,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationDirection,
    CollaborationEventKind,
    CollaborationConfirmationDecision,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationStepStatePayload,
    LeadOutcomeKind,
    StoryCommitDecisionPayload,
)
from story_writing_agents.git.story_commit_contracts import StoryCommitDecision
from story_writing_agents.conversations.session_context import (
    MAX_SESSION_CONTEXT_UTF8_BYTES,
    SessionContextCapacityError,
    SessionContextHistoryChangedError,
    SessionHistorySnapshot,
)
from story_writing_agents.conversations import session_context as session_context_module
from story_writing_agents.conversations.context import CollaborationReadableField, CollaborationReadableFieldKey
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.provider_execution_workspace import ProviderExecutionWorkspace
from story_writing_agents.workspace.initialization import SessionArtifactBindingError
from story_writing_agents.conversation_storage.sqlite import compose_sqlite_conversation_storage

from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests.collaboration_contract_fixture import contract_batches
from tests.sqlite_conversation_storage_fixture import provision, initialized_workspace
from tests.test_agent_context_accounting import _property_count


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _records(delivery):
    return [record for conversation in delivery["conversations"] for record in conversation["records"]]

def _fields(delivery):
    return [field["text"] for record in _records(delivery) for field in record.get("fields", [])]


class LeadSessionContinuityTests(lead.LeadOrchestrationTestCase):
    def setUp(self):
        super().setUp()
        self.backend = lead._LeadBackend(persist_context_threads=True)
        self.runtime = StoryRuntime(self.workspace, self.backend)

    async def say(self, conversation, text, answer="Synthetic visible reply.", *, runtime=None, backend=None):
        backend = backend or self.backend
        backend.enqueue(lead._visible_outcome("answer", answer))
        return await (runtime or self.runtime).lead_turn(self.session_id, conversation, text)

    def snapshot(self):
        storage = self.runtime.conversation_storage_services
        return SessionHistorySourceAdapter(storage.catalog, storage.event_store, self.session_id).capture()

    def checkpoint(self):
        service = self.runtime._agent_context_service
        identity = service.session_continuation(self.session_id, LEAD_ORCHESTRATOR).identity
        return service._ledger.inspect(ContextLedgerKey(identity.checkpoint_scope(), "codex"))

    async def test_confirmed_plan_survives_three_followups_and_conversation_switch(self):
        first = self.conversation("Synthetic planning")
        second = self.conversation("Synthetic follow-up")
        proposed = await self.proposal(first)
        prior = self.snapshot()
        plan = "First inspect the outline.\nSecond review the character motive.\nThird draft the opening."
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, lead._synthesis_outcome(message=plan))
        await self.runtime.decide_lead_step(lead._decision(proposed, CollaborationConfirmationDecision.CONFIRM))
        self.assertEqual(self.backend.call_records[-1][1].expected_thread_id, "thread-lead-1")
        followups = (
            "Proceed in the order you recommended.",
            "Recall your last recommendation.",
            "Summarize our current discussion.",
        )
        for index, text in enumerate(followups):
            await self.say(second, text, f"Follow-up answer {index}.")
        calls = [call for agent, call in self.backend.call_records if agent == LEAD_ORCHESTRATOR]
        self.assertEqual({call.expected_thread_id for call in calls[1:]}, {"thread-lead-1"})
        delivered = [text for call in calls for text in _fields(call.payload["h"])]
        self.assertNotIn(plan, delivered)
        for text in followups:
            self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload and event.payload.content == text for event in self.history(second)), 1)
            self.assertIn(text, [call.payload["i"]["command"] for call in calls
                                 if "command" in call.payload["i"]])
        self.assertEqual(calls[-1].scope.subject_id, second)
        for call in calls[2:]:
            self.assertNotIn(call.payload["i"]["command"], _fields(call.payload["h"]))
        self.assertLessEqual(_property_count(calls[-1].payload["h"]),
                             _property_count(calls[-2].payload["h"]))
        self.assertTrue(all("timestamp" not in record and "batch_digest" not in record
                            and "sequence" not in record
                            for call in calls for record in _records(call.payload["h"])))
        judgment = next(event for event in self.history(second)
                        if type(event.payload) is CollaborationLeadJudgmentPayload)
        host_fact = next(record for record in _records(calls[3].payload["h"])
                         if record["event_id"] == judgment.event_id)
        self.assertEqual(host_fact["details"]["authoring_scope"]["task_id"],
                         judgment.payload.authoring_scope.task_id)

        snapshot = self.snapshot()
        records = tuple(record for conversation in snapshot.conversations for record in conversation.records)
        source = next(record for record in records if type(record.payload) is CollaborationLeadSynthesisPayload)
        mirror = next(record for record in records if type(record.payload) is CollaborationMessagePayload
                      and record.payload.content == plan)
        state = next(record for record in records if type(record.payload) is CollaborationStepStatePayload
                     and record.payload.execution_result_event_id is not None)
        synthesis_operation = f"synthesis_{source.payload.execution_attempt_id}"
        self.assertNotEqual((source.batch_id, source.turn_id), (mirror.batch_id, mirror.turn_id))
        self.assertEqual(session_context_module._native_synthesis_mirrors(
            records, synthesis_operation, LEAD_ORCHESTRATOR), frozenset({mirror.event_id}))
        delta = snapshot.delivery(prior.coverage, known_native_operation_id=synthesis_operation,
                                  native_agent_id=LEAD_ORCHESTRATOR)
        self.assertNotIn(plan, _fields(delta))
        synthesis_fact = next(record for record in _records(delta) if record["event_id"] == source.event_id)
        self.assertEqual(synthesis_fact["details"]["authoring_scope"]["task_id"],
                         source.payload.authoring_scope.task_id)
        changed_text = plan + "\nA runtime notice was appended."
        changed = replace(mirror, payload=replace(mirror.payload, content=changed_text),
                          fields=tuple(replace(field, text=changed_text) for field in mirror.fields))
        recovered_source = replace(source, payload=replace(source.payload, trace=replace(
            source.payload.trace, origin=ReplyTraceOrigin.RECOVERED, recovered_fields=("message",))))
        variants = (
            tuple(record for record in records if record.event_id != state.payload.execution_result_event_id),
            tuple(replace(record, conversation_id=second) if record.event_id == source.event_id else record
                  for record in records),
            tuple(recovered_source if record.event_id == source.event_id else record for record in records),
            tuple(replace(record, batch_id=record.batch_id + "_unrelated")
                  if record.event_id == mirror.event_id else record for record in records),
            tuple(changed if record.event_id == mirror.event_id else record for record in records),
        )
        for variant in variants:
            self.assertNotIn(mirror.event_id, session_context_module._native_synthesis_mirrors(
                variant, synthesis_operation, LEAD_ORCHESTRATOR))
        self.assertEqual(session_context_module._provider_record(changed, frozenset())["fields"][0]["text"],
                         changed_text)
        native = next(record for conversation in snapshot.conversations for record in conversation.records
                      if record.event_id == judgment.event_id)
        operation = f"lead_{native.turn_id}"
        self.assertTrue(session_context_module._known_native_result(native, operation, LEAD_ORCHESTRATOR))
        recovered_trace = replace(native.payload.trace, origin=ReplyTraceOrigin.RECOVERED,
                                  recovered_fields=("message",))
        recovered = replace(native, payload=replace(native.payload, trace=recovered_trace))
        self.assertFalse(session_context_module._known_native_result(recovered, operation, LEAD_ORCHESTRATOR))
        with mock.patch.object(session_context_module, "MAX_SESSION_CONTEXT_UTF8_BYTES", 1):
            with self.assertRaises(SessionContextCapacityError):
                snapshot.delivery()
        conversation = snapshot.conversations[0]
        changed = replace(conversation, records=(
            replace(conversation.records[0], timestamp="2030-01-01T00:00:00+00:00"),
            *conversation.records[1:]))
        altered = replace(snapshot, conversations=(changed, *snapshot.conversations[1:]))
        with self.assertRaises(SessionContextHistoryChangedError):
            altered.delivery(snapshot.coverage)
        for metadata in (replace(conversation.metadata, subject="Renamed synthetic planning"),
                         replace(conversation.metadata, status=type(conversation.metadata.status)("archived"))):
            altered = replace(snapshot, conversations=(
                replace(conversation, metadata=metadata), *snapshot.conversations[1:]))
            with self.assertRaises(SessionContextHistoryChangedError):
                altered.delivery(snapshot.coverage)
        empty = self.conversation("New empty conversation")
        missing = self.snapshot().delivery(snapshot.coverage)["conversations"]
        self.assertEqual([item["conversation_id"] for item in missing], [empty])
        self.assertEqual(missing[0]["records"], [])

        self.runtime.conversation_storage_services.catalog.archive(self.session_id, first)
        self.backend.context_threads.clear()
        restarted = StoryRuntime(self.workspace, self.backend)
        await self.say(second, "Recall all retained planning.", runtime=restarted)
        fresh = self.backend.calls[-1]
        self.assertIsNone(fresh.expected_thread_id)
        self.assertEqual(fresh.payload["h"]["delivery_mode"], "bootstrap")
        replay = _fields(fresh.payload["h"])
        self.assertEqual(replay.count(plan), 2)
        replay_fact = next(record for record in _records(fresh.payload["h"])
                           if record["event_id"] == source.event_id)
        self.assertEqual(replay_fact["details"]["authoring_scope"], synthesis_fact["details"]["authoring_scope"])
        self.assertIn(mirror.event_id, {record["event_id"] for record in _records(fresh.payload["h"])})
        self.assertTrue(all(text in replay for text in followups))
        self.assertEqual({item["conversation_id"]: item["status"]
                          for item in fresh.payload["h"]["conversations"]},
                         {first: "archived", second: "active", empty: "active"})

        stage = ContextContinuationTransaction.stage
        def unverified_publication(transaction, commit, abort, published, complete=None):
            return stage(transaction, commit, abort, lambda: False, complete)
        known_coverage = self.checkpoint().checkpoint_view.session_history_coverage
        with mock.patch.object(ContextContinuationTransaction, "stage", new=unverified_publication):
            await self.say(second, "Synthetic publication verification failure.", runtime=restarted)
        failed = self.checkpoint()
        self.assertIs(failed.record.state, ContextLedgerRecordState.TAINTED)
        self.assertNotEqual(failed.checkpoint_view.session_history_coverage, known_coverage)
        await self.say(second, "Retry after publication verification failure.", runtime=restarted)
        retry = self.backend.calls[-1]
        self.assertIsNone(retry.expected_thread_id)
        self.assertIn("Synthetic publication verification failure.", _fields(retry.payload["h"]))
        self.assertEqual(calls[0].continuation_identity, calls[-1].continuation_identity)
        self.assertNotEqual(calls[0].scope, calls[-1].scope)
