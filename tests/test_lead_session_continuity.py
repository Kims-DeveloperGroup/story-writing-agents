"""Exact session memory, recovery, and publication safety using synthetic providers."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from unittest import mock

from story_writing_agents import StoryRuntime
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
    CollaborationMessagePayload,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
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
from story_writing_agents.conversations.context import CollaborationReadableField, CollaborationReadableFieldKey
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.conversation_storage.sqlite import compose_sqlite_conversation_storage

from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests.collaboration_contract_fixture import contract_batches
from tests.sqlite_conversation_storage_fixture import provision, initialized_workspace


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _records(delivery):
    return [record for conversation in delivery["conversations"] for record in conversation["records"]]


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
        delivered = _json([call.payload["h"] for call in calls])
        recovered_fields = [field["text"] for call in calls for record in _records(call.payload["h"]) for field in record["fields"]]
        self.assertIn(plan, recovered_fields)
        for text in followups:
            self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload and event.payload.content == text for event in self.history(second)), 1)
            self.assertIn(text, delivered)
        self.assertEqual(calls[-1].scope.subject_id, second)
        self.assertEqual(calls[0].continuation_identity, calls[-1].continuation_identity)
        self.assertNotEqual(calls[0].scope, calls[-1].scope)
