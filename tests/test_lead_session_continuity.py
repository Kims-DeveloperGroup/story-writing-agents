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

    async def test_artifact_synthesis_shares_thread_and_restores_all_visible_review_fields(self):
        conversation = self.conversation("Synthetic artifact review")
        proposed, decided = await artifact.ArtifactProposalOrchestrationTests.reviewed_create(self, conversation)
        reviewed = decided.turn.artifact_proposal.reviewed_proposal
        self.assertIsNotNone(reviewed)
        lead_calls = [call for agent, call in self.backend.call_records if agent == LEAD_ORCHESTRATOR]
        self.assertEqual(lead_calls[-1].expected_thread_id, "thread-lead-1")
        self.assertEqual(lead_calls[0].continuation_identity, lead_calls[-1].continuation_identity)
        self.backend.context_threads.clear()
        await self.say(conversation, "Recall the proposed edit and its limitations.")
        delivery = self.backend.calls[-1].payload["h"]
        self.assertEqual(delivery["delivery_mode"], "bootstrap")
        details = [record["details"] for record in _records(delivery)]
        recovered = next(item for item in details if "operations" in item and "rationale" in item)
        self.assertEqual(recovered["rationale"], reviewed.rationale)
        self.assertEqual(recovered["user_impact"], reviewed.user_impact)
        self.assertEqual(recovered["limitations"], reviewed.limitations)
        self.assertEqual(recovered["operations"][0]["content"], "Use restrained close-third narration.\n")
        self.assertTrue(all(record["timestamp"] for record in _records(delivery)))
        self.assertFalse(reviewed.apply_authority_granted)
        self.assertFalse(reviewed.artifact_write_occurred)
        self.assertFalse(reviewed.git_action_occurred)
        self.assertFalse(self.workspace.path(artifact._RULES).exists())

    async def test_more_than_sixteen_compatible_deltas_keep_one_thread(self):
        conversation = self.conversation()
        for index in range(19):
            await self.say(conversation, f"Synthetic input {index}.", f"Synthetic answer {index}.")
        self.assertIsNone(self.backend.calls[0].expected_thread_id)
        self.assertEqual({call.expected_thread_id for call in self.backend.calls[1:]}, {"thread-lead-1"})
        self.assertEqual([call.payload["h"]["delivery_mode"] for call in self.backend.calls], ["bootstrap"] + ["delta"] * 18)

    async def test_restart_recovers_over_thirty_active_and_archived_records_exactly(self):
        active, archived = self.conversation("Active"), self.conversation("Archived")
        messages = []
        for index in range(10):
            text, reply = f"Retained input {index} 한🙂.", f"Retained reply {index}.\r\nNext line."
            messages.extend((text, reply))
            await self.say(active if index % 2 else archived, text, reply)
        self.runtime.conversation_storage_services.catalog.archive(self.session_id, archived)
        before = self.snapshot()
        self.assertGreater(sum(len(item.records) for item in before.conversations), 30)
        backend = lead._LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)
        await self.say(active, "Restore everything exactly.", runtime=runtime, backend=backend)
        delivery = backend.calls[0].payload["h"]
        fields = [field["text"] for record in _records(delivery) for field in record["fields"]]
        for message in messages:
            self.assertEqual(fields.count(message), 1)
        self.assertEqual({item["conversation_id"]: item["status"] for item in delivery["conversations"]}, {active: "active", archived: "archived"})
        expected = {record.event_id: record.timestamp for item in before.conversations for record in item.records}
        self.assertEqual({record["event_id"]: record["timestamp"] for record in _records(delivery) if record["event_id"] in expected}, expected)
        await self.say(active, "Next safe turn.", runtime=runtime, backend=backend)
        self.assertEqual(backend.calls[1].expected_thread_id, "thread-lead-1")
        self.assertEqual(backend.calls[1].payload["h"]["delivery_mode"], "delta")

    async def test_current_directive_and_core_state_refresh_without_granting_authority(self):
        conversation = self.conversation()
        await self.say(conversation, "Observe the first state.")
        self.runtime.apply_directive(self.session_id, "[SYS: LANG=EN]")
        exact = "Revised current core state.\r\nPreserve newline. 한🙂\n"
        self.workspace.path("core_documents/global_outline.md").write_bytes(exact.encode())
        await self.say(conversation, "Observe the current state.")
        state = self.backend.calls[-1].payload["h"]["current_state"]
        outline = next(item for item in state["core_documents"] if item["path"] == "core_documents/global_outline.md")
        self.assertEqual(outline["content"], exact)
        self.assertEqual(state["runtime_overrides"], self.workspace.load_session_overrides(self.session_id))
        self.assertTrue(state["runtime_overrides"])
        self.assertIn("grant no current execution authority", self.backend.calls[-1].payload["h"]["instruction"])
        self.assertFalse(any(type(event.payload) is lead.CollaborationProposal for event in self.history(conversation)))

    async def test_changed_valid_snapshot_prefix_bootstraps_once_then_advances(self):
        conversation = self.conversation()
        await self.say(conversation, "Seed accepted prefix.")
        capture = SessionHistorySourceAdapter.capture

        def changed(owner):
            snapshot = capture(owner)
            history = snapshot.conversations[0]
            records = (replace(history.records[0], batch_digest="sha256:" + "9" * 64),) + history.records[1:]
            return replace(snapshot, conversations=(replace(history, records=records),) + snapshot.conversations[1:])

        with mock.patch.object(SessionHistorySourceAdapter, "capture", new=changed):
            await self.say(conversation, "Recover changed accepted prefix.")
            await self.say(conversation, "Continue recovered prefix.")
        self.assertEqual([call.payload["h"]["delivery_mode"] for call in self.backend.calls], ["bootstrap", "bootstrap", "delta"])
        self.assertIsNone(self.backend.calls[1].expected_thread_id)
        self.assertEqual(self.backend.calls[2].expected_thread_id, "thread-lead-2")

    async def test_corrupt_canonical_history_fails_closed_without_provider_work(self):
        conversation = self.conversation()
        await self.say(conversation, "Seed valid history.")
        storage = self.runtime.conversation_storage_services
        events = self.history(conversation)
        before = len(self.backend.calls)
        with mock.patch.object(type(storage.event_store), "read", return_value=(events[-1],) + events[:-1]):
            with self.assertRaises(ValueError):
                SessionHistorySourceAdapter(storage.catalog, storage.event_store, self.session_id).capture()
        self.assertEqual(len(self.backend.calls), before)

    async def test_proven_preexecution_rejection_replaces_once_without_duplicate_input(self):
        for rejection in ContextContinuationRejection:
            with self.subTest(rejection=rejection):
                conversation = self.conversation(str(rejection))
                await self.say(conversation, "Seed compatible continuation.")
                start = len(self.backend.calls)
                self.backend.enqueue(lead._SubmittedFailure(RuntimeError("synthetic proven rejection"), continuation_rejection=rejection))
                self.backend.enqueue(lead._visible_outcome("answer", "Recovered exact reply."))
                result = await self.runtime.lead_turn(self.session_id, conversation, "Safely recover this exact input.")
                self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
                self.assertEqual(len(self.backend.calls) - start, 2)
                self.assertIsNone(self.backend.calls[-1].expected_thread_id)
                self.assertEqual(self.backend.calls[-1].payload["h"]["delivery_mode"], "bootstrap")
                self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload and event.payload.content == "Safely recover this exact input." for event in self.history(conversation)), 1)

    async def test_second_proven_rejection_is_not_retried_again(self):
        conversation = self.conversation()
        for _ in range(2):
            self.backend.enqueue(lead._SubmittedFailure(RuntimeError("synthetic proven rejection"), continuation_rejection=ContextContinuationRejection.UNAVAILABLE))
        result = await self.runtime.lead_turn(self.session_id, conversation, "A bounded recovery attempt.")
        self.assertIs(result.next_action, lead.CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(sum(type(event.payload) is CollaborationMessagePayload for event in self.history(conversation)), 1)

    async def test_ambiguous_failures_never_replay_and_next_user_turn_bootstraps(self):
        for timed_out, uncertain in ((False, False), (True, False), (False, True)):
            with self.subTest(timed_out=timed_out, uncertain=uncertain):
                conversation = self.conversation()
                await self.say(conversation, "Seed prior safe reply.")
                start = len(self.backend.calls)
                self.backend.enqueue(lead._SubmittedFailure(RuntimeError("thread not found; context capacity exceeded"), timed_out=timed_out, submission_uncertain=uncertain))
                result = await self.runtime.lead_turn(self.session_id, conversation, "An ambiguous turn.")
                self.assertIs(result.state, lead.CollaborationStepState.OUTCOME_UNKNOWN)
                self.assertEqual(len(self.backend.calls) - start, 1)
                blocked = await self.runtime.lead_turn(self.session_id, conversation, "Do not replay unresolved work.")
                self.assertIs(blocked.state, lead.CollaborationStepState.OUTCOME_UNKNOWN)
                self.assertEqual(len(self.backend.calls) - start, 1)
                await self.say(self.conversation("Distinct safe work"), "A new explicit user turn.")
                self.assertIsNone(self.backend.calls[-1].expected_thread_id)
                self.assertEqual(self.backend.calls[-1].payload["h"]["delivery_mode"], "bootstrap")

    async def test_reference_present_oversize_preserves_capacity_error_before_provider(self):
        conversation = self.conversation()
        self.backend.enqueue(lead._visible_outcome("answer"))
        with mock.patch.object(self.runtime, "_session_context_state", return_value=_json({"core": "🙂" * MAX_SESSION_CONTEXT_UTF8_BYTES})):
            with self.assertRaises(SessionContextCapacityError) as raised:
                await self.runtime.lead_turn(self.session_id, conversation, "Inspect ${core_documents/global_outline.md}.")
        self.assertEqual(str(raised.exception), "exact session context exceeds the delivery capacity")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.reference_calls, [])
        self.assertIsNone(self.checkpoint().checkpoint_view.provider_thread_id)

    async def test_generated_reference_observation_capacity_stops_before_substantive_submission(self):
        conversation = self.conversation()
        self.backend.enqueue(lead._visible_outcome("answer"))
        prepare = lead._LeadContextProvider.prepare_references

        async def oversized(provider, request):
            result = await prepare(provider, request)
            return replace(result, context=({"ordinal": 1, "content": "🙂" * MAX_SESSION_CONTEXT_UTF8_BYTES},))

        with mock.patch.object(lead._LeadContextProvider, "prepare_references", new=oversized):
            with self.assertRaises(SessionContextCapacityError):
                await self.runtime.lead_turn(self.session_id, conversation, "Inspect ${core_documents/global_outline.md}.")
        self.assertEqual(len(self.backend.reference_calls), 1)
        self.assertEqual(self.backend.calls, [])
        self.assertIs(self.checkpoint().record.state, ContextLedgerRecordState.TAINTED)
        submitted = [event.payload for event in self.history(conversation) if type(event.payload) is CollaborationProviderCallPayload and event.payload.state is CollaborationProviderCallState.SUBMITTED]
        self.assertEqual(len(submitted), 1)

    async def test_session_lock_covers_capture_provider_publication_and_commit(self):
        first, second = self.conversation("First"), self.conversation("Second")
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def blocked(call, provider):
            order.append("first_provider")
            entered.set()
            await release.wait()
            return lead._visible_outcome("answer", "First terminal result.")(call, provider)

        self.backend.enqueue(blocked)
        storage = self.runtime.conversation_storage_services
        append = type(storage.event_store).append_batch
        commit = self.runtime._agent_context_service._ledger.commit_attempt
        capture = SessionHistorySourceAdapter.capture
        held = []

        def check_lock():
            transaction = held[-1]
            transaction.require_active()
            self.assertTrue(transaction._lock.locked())

        def captured(owner):
            held.append(self.runtime._lead_continuations[self.session_id])
            check_lock()
            order.append("capture")
            return capture(owner)

        def appended(store, batch):
            if any(type(event.payload) is CollaborationLeadJudgmentPayload for event in batch.events):
                check_lock()
                order.append("publication")
            return append(store, batch)

        def committed(*args, **kwargs):
            check_lock()
            self.assertEqual(order[-1], "publication")
            order.append("commit")
            return commit(*args, **kwargs)

        with mock.patch.object(SessionHistorySourceAdapter, "capture", new=captured), mock.patch.object(type(storage.event_store), "append_batch", new=appended), mock.patch.object(type(self.runtime._agent_context_service._ledger), "commit_attempt", side_effect=committed):
            one = asyncio.create_task(self.runtime.lead_turn(self.session_id, first, "First serialized turn."))
            await asyncio.wait_for(entered.wait(), 5)
            self.backend.enqueue(lead._visible_outcome("answer"))
            two = asyncio.create_task(self.runtime.lead_turn(self.session_id, second, "Second serialized turn."))
            await asyncio.sleep(0)
            self.assertEqual(order, ["capture", "first_provider"])
            independent = ContextContinuationTransaction(ContextContinuationIdentity(self.runtime._agent_context_service._workspace_id, "independent_session", LEAD_ORCHESTRATOR))
            async with asyncio.timeout(1):
                async with independent:
                    independent.require_active()
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 10)
        self.assertEqual(order, ["capture", "first_provider", "publication", "commit", "capture", "publication", "commit"])
        fields = [field["text"] for record in _records(self.backend.calls[-1].payload["h"]) for field in record["fields"]]
        self.assertIn("First terminal result.", fields)

    async def test_separate_runtime_instances_serialize_session_while_other_session_progresses(self):
        first, second = self.conversation(), self.conversation()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(call, provider):
            entered.set()
            await release.wait()
            return lead._visible_outcome("answer", "Published across runtime instances.")(call, provider)

        self.backend.enqueue(blocked)
        one = asyncio.create_task(self.runtime.lead_turn(self.session_id, first, "First runtime turn."))
        await asyncio.wait_for(entered.wait(), 5)
        restarted = StoryRuntime(self.workspace, self.backend)
        self.backend.enqueue(lead._visible_outcome("answer"))
        two = asyncio.create_task(restarted.lead_turn(self.session_id, second, "Second runtime turn."))
        await asyncio.sleep(0)
        self.assertEqual(len(self.backend.calls), 1)
        independent_backend = lead._LeadBackend(persist_context_threads=True)
        independent = StoryRuntime(self.workspace, independent_backend)
        foreign = independent.conversation_storage_services.catalog.create("independent_session", "Independent work")
        independent_backend.enqueue(lead._visible_outcome("answer"))
        try:
            result = await asyncio.wait_for(independent.lead_turn("independent_session", foreign.conversation_id, "Proceed independently."), 5)
            self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
            self.assertFalse(two.done())
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(one, two), 10)
        self.assertEqual(self.backend.calls[-1].expected_thread_id, "thread-lead-1")
        texts = [field["text"] for record in _records(self.backend.calls[-1].payload["h"]) for field in record["fields"]]
        self.assertIn("Published across runtime instances.", texts)
        self.assertNotIn("Proceed independently.", texts)

    async def test_failed_coverage_commit_retains_published_result_and_releases_lock(self):
        conversation = self.conversation()
        ledger = self.runtime._agent_context_service._ledger
        self.backend.enqueue(lead._visible_outcome("answer", "Published before failed coverage."))
        with mock.patch.object(type(ledger), "commit_attempt", side_effect=OSError("synthetic coverage persistence failure")):
            with self.assertRaises(OSError):
                await self.runtime.lead_turn(self.session_id, conversation, "Commit failure input.")
        self.assertIs(self.checkpoint().record.state, ContextLedgerRecordState.TAINTED)
        backend = lead._LeadBackend(persist_context_threads=True)
        runtime = StoryRuntime(self.workspace, backend)
        await asyncio.wait_for(self.say(conversation, "Recover published history.", runtime=runtime, backend=backend), 5)
        fields = [field["text"] for record in _records(backend.calls[0].payload["h"]) for field in record["fields"]]
        self.assertEqual(fields.count("Published before failed coverage."), 1)
        self.assertEqual(backend.calls[0].payload["h"]["delivery_mode"], "bootstrap")

    async def test_terminal_append_failure_never_commits_ready_and_releases_lock(self):
        conversation = self.conversation()
        storage = self.runtime.conversation_storage_services
        append = type(storage.event_store).append_batch

        def fail_terminal(store, batch):
            if any(type(event.payload) is CollaborationLeadJudgmentPayload for event in batch.events):
                raise OSError("synthetic terminal publication failure")
            return append(store, batch)

        self.backend.enqueue(lead._visible_outcome("answer", "Unpublished provider reply."))
        with mock.patch.object(type(storage.event_store), "append_batch", new=fail_terminal):
            with self.assertRaises(Exception):
                await self.runtime.lead_turn(self.session_id, conversation, "Publication failure input.")
        self.assertIs(self.checkpoint().record.state, ContextLedgerRecordState.TAINTED)
        self.assertFalse(any(type(event.payload) is CollaborationLeadJudgmentPayload for event in self.history(conversation)))
        self.assertEqual(len(self.backend.calls), 1)
        transaction = self.runtime._agent_context_service.session_continuation(self.session_id, LEAD_ORCHESTRATOR)
        async with asyncio.timeout(1):
            async with transaction:
                transaction.require_active()


class SessionHistoryContractTests(unittest.TestCase):
    def test_application_and_commit_revision_text_and_timestamps_survive_projection(self):
        workspace = initialized_workspace(self)
        storage = compose_filesystem_conversation_storage(workspace)
        conversation = storage.catalog.create("session_a", "Visible user revisions")
        storage.event_store.append_batch(contract_batches(conversation.conversation_id)["proposal"])
        snapshot = SessionHistorySourceAdapter(storage.catalog, storage.event_store, "session_a").capture()
        history = snapshot.conversations[0]
        template = history.records[0]
        digest = "sha256:" + "a" * 64
        application_text = "Preserve the current tense.\r\nKeep this exact clause. 한🙂"
        commit_text = "Use a narrower commit message.\nKeep the quoted wording."
        payloads = (
            (CollaborationEventKind.ARTIFACT_APPLICATION_DECISION, ArtifactApplicationDecisionPayload(
                "application_confirmation", "application_request_event", "reviewed_proposal", 1, digest,
                "artifact_candidate", 1, digest, "application", digest,
                ArtifactApplicationDecisionKind.REVISE, application_text,
            )),
            (CollaborationEventKind.STORY_COMMIT_DECISION, StoryCommitDecisionPayload(
                "commit_request", "commit_request_event", "application_result_event", "a" * 64,
                digest, "commit_confirmation", StoryCommitDecision.REVISE, commit_text,
            )),
        )
        records = tuple(replace(
            template, sequence=index, event_id=f"revision_{index}", kind=kind, payload=payload,
            sender=CollaborationActor(CollaborationActorKind.USER),
            recipient=CollaborationActor(CollaborationActorKind.RUNTIME),
            direction=CollaborationDirection.USER_TO_RUNTIME, fields=(),
        ) for index, (kind, payload) in enumerate(payloads, 1))
        recovered = replace(snapshot, conversations=(replace(history, records=records),)).delivery()
        self.assertEqual([item["details"]["revision_request"] for item in _records(recovered)], [application_text, commit_text])
        self.assertEqual([item["timestamp"] for item in _records(recovered)], [template.timestamp] * 2)
        self.assertEqual([item["details"]["decision"] for item in _records(recovered)], ["revise", "revise"])

    def test_filesystem_and_sqlite_full_history_and_archives_have_equal_delivery(self):
        workspace = initialized_workspace(self)
        provision(workspace)
        filesystem = compose_filesystem_conversation_storage(workspace)
        sqlite = compose_sqlite_conversation_storage(workspace)
        with mock.patch("story_writing_agents.conversation_storage.filesystem.uuid4", return_value=mock.Mock(hex="a" * 32)), mock.patch("story_writing_agents.conversation_storage.sqlite.uuid4", return_value=mock.Mock(hex="a" * 32)):
            first = filesystem.catalog.create("session_a", "Synthetic retained archive")
            second = sqlite.catalog.create("session_a", "Synthetic retained archive")
        self.assertEqual(first.conversation_id, second.conversation_id)
        with mock.patch("story_writing_agents.conversation_storage.filesystem.datetime") as filesystem_clock, mock.patch("story_writing_agents.conversation_storage.sqlite.datetime") as sqlite_clock:
            for clock in (filesystem_clock, sqlite_clock):
                clock.now.return_value.isoformat.return_value = max(first.created_at, second.created_at)
            for storage in (filesystem, sqlite):
                for name in ("proposal", "confirmation", "running", "calls", "result", "completion"):
                    storage.event_store.append_batch(contract_batches(first.conversation_id)[name])
                storage.catalog.archive("session_a", first.conversation_id)
        left = SessionHistorySourceAdapter(filesystem.catalog, filesystem.event_store, "session_a").capture()
        right = SessionHistorySourceAdapter(sqlite.catalog, sqlite.event_store, "session_a").capture()
        self.assertEqual(left.delivery(), right.delivery())
        self.assertEqual(left.coverage, right.coverage)
        self.assertEqual(left.delivery(left.coverage)["conversations"][0]["records"], [])

    def test_continuation_identity_never_accepts_foreign_scope_or_consensus(self):
        identity = ContextContinuationIdentity("workspace", "session", LEAD_ORCHESTRATOR)
        conversation = ContextScope("workspace", "session", ContextSubjectKind.CONVERSATION, "one", LEAD_ORCHESTRATOR)
        self.assertTrue(identity.accepts(conversation))
        self.assertTrue(identity.accepts(replace(conversation, subject_id="two")))
        for scope in (replace(conversation, workspace_id="foreign"), replace(conversation, session_id="foreign"), replace(conversation, agent_id=PLOT_ARCHITECT), replace(conversation, subject_kind=ContextSubjectKind.DEBATE), identity.checkpoint_scope()):
            self.assertFalse(identity.accepts(scope))

    def test_exact_utf8_capacity_boundary_and_changed_prefix_fail_without_loss(self):
        workspace = initialized_workspace(self)
        storage = compose_filesystem_conversation_storage(workspace)
        conversation = storage.catalog.create("session_a", "Capacity")
        storage.event_store.append_batch(contract_batches(conversation.conversation_id)["proposal"])
        snapshot = SessionHistorySourceAdapter(storage.catalog, storage.event_store, "session_a").capture()
        history = snapshot.conversations[0]
        record = history.records[0]
        original = CollaborationReadableField(CollaborationReadableFieldKey.MESSAGE_CONTENT, "capacity fixture")

        def extended(text):
            records = tuple(
                replace(record, sequence=index + 1, event_id=f"capacity_event_{index}",
                        kind=CollaborationEventKind.MESSAGE,
                        sender=CollaborationActor(CollaborationActorKind.USER),
                        recipient=CollaborationActor(CollaborationActorKind.LEAD, LEAD_ORCHESTRATOR),
                        direction=CollaborationDirection.USER_TO_LEAD,
                        payload=CollaborationMessagePayload(value),
                        fields=(replace(original, text=value),))
                for index, value in enumerate(["x" * 30_000] * 8 + [text])
            )
            return replace(snapshot, conversations=(replace(history, records=records),))

        prefix = "한🙂\r\nExact content.\n"
        initial = extended(prefix).delivery()
        remaining = MAX_SESSION_CONTEXT_UTF8_BYTES - len(_json(initial).encode())
        exact = extended(prefix + "x" * remaining)
        delivered = exact.delivery()
        self.assertEqual(len(_json(delivered).encode()), MAX_SESSION_CONTEXT_UTF8_BYTES)
        self.assertEqual(_records(delivered)[-1]["fields"][0]["text"], prefix + "x" * remaining)
        with self.assertRaises(SessionContextCapacityError):
            extended(prefix + "x" * (remaining + 1)).delivery()
        foreign = ((conversation.conversation_id, len(history.records), "sha256:" + "f" * 64),)
        with self.assertRaises(SessionContextHistoryChangedError):
            snapshot.delivery(foreign)
