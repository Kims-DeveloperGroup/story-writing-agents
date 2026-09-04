"""Focused durable-storage coverage for phase-confirmed consensus workflows."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.consensus import (
    ConsensusDecision,
    ConsensusEvent,
    ConsensusInputKind,
    ConsensusPhase,
    ConsensusPhaseDecision,
    ConsensusProviderResult,
    ConsensusRuntimeAdapter,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
    prepare_consensus_phase,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    artifact_material_handle,
    candidate_material_handle,
)
from story_writing_agents.consensus.event_store import (
    CONSENSUS_EVENT_STORE_DIRECTORY,
    ConsensusEventStore,
    ConsensusEventStoreConflict,
    ConsensusEventStoreError,
    ConsensusEventStoreOutcomeUnknown,
)
from story_writing_agents.consensus.execution import ConsensusPhaseOutputKind
from story_writing_agents.consensus.serialization import serialize_consensus_workflow

from tests.helpers import initialize_test_workspace


_IDENTITY = ConsensusExecutionIdentity("session-1", "conversation-1")
_FOREIGN_IDENTITY = ConsensusExecutionIdentity("session-foreign", "conversation-1")
_PROMPT = "1" * 64
_DRAFT = "2" * 64
_CANDIDATE = "3" * 64
_REPLY_CANDIDATE = "4" * 64
_PROMPT_MATERIAL = candidate_material_handle(_PROMPT)
_DRAFT_MATERIAL = artifact_material_handle(
    "manuscript/part-1/supplied-draft.md", _DRAFT
)
_CANDIDATE_MATERIAL = candidate_material_handle(_CANDIDATE)
_REPLY_CANDIDATE_MATERIAL = candidate_material_handle(_REPLY_CANDIDATE)


class _DurableConsensusPort:
    """Provider-free adapter port backed by the real event store."""

    def __init__(self, workspace: Workspace) -> None:
        self.store = ConsensusEventStore(workspace)
        self.counter = 0
        self.provider_calls = []

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def create_workflow(self, submission, request_event) -> None:
        self.store.create_workflow(submission, request_event)

    def load_workflow(self, session_id, conversation_id, workflow_id):
        return self.store.load_workflow(session_id, conversation_id, workflow_id)

    def append_event(
        self,
        session_id,
        conversation_id,
        workflow_id,
        expected_tail,
        event,
    ) -> None:
        self.store.append_event(
            session_id,
            conversation_id,
            workflow_id,
            expected_tail,
            event,
        )

    async def invoke_provider(self, invocation):
        self.provider_calls.append(invocation)
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.CANDIDATE,
            _REPLY_CANDIDATE,
            _REPLY_CANDIDATE_MATERIAL,
        )

    def interrupt_provider(self, *_args):
        return True


class ConsensusEventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "workspace"
        self.workspace = initialize_test_workspace(self.root)
        self.store = ConsensusEventStore(self.workspace)

    def request_and_submission(
        self,
        *,
        identity: ConsensusExecutionIdentity = _IDENTITY,
        workflow_id: str = "workflow-1",
        proposal_id: str = "proposal-1",
        input_material=_PROMPT_MATERIAL,
        input_kind: ConsensusInputKind = ConsensusInputKind.PROMPT,
    ) -> tuple[ConsensusWorkflowSubmission, ConsensusEvent]:
        request, _capability = prepare_consensus_phase(
            owner=self,
            workflow_id=workflow_id,
            phase_request_id="request-1",
            proposal_id=proposal_id,
            execution_identity=identity,
            phase=ConsensusPhase.DRAFT,
            revision_id="r0",
            input_material=input_material,
            candidate_material=None,
            base_tail_sequence=0,
        )
        submission = ConsensusWorkflowSubmission(
            execution_identity=identity,
            workflow_id=workflow_id,
            proposal_id=proposal_id,
            input_kind=input_kind,
            input_material=input_material,
        )
        return submission, ConsensusEvent("request-event", 1, request)

    @staticmethod
    def workflow_path(root: Path, identity: ConsensusExecutionIdentity, workflow_id: str) -> Path:
        digest = hashlib.sha256(
            "\0".join((identity.session_id, identity.conversation_id, workflow_id)).encode()
        ).hexdigest()
        return root / CONSENSUS_EVENT_STORE_DIRECTORY / f"{digest}.json"

    def test_create_and_load_round_trip_is_durable(self) -> None:
        submission, request_event = self.request_and_submission()
        self.store.create_workflow(submission, request_event)
        loaded = ConsensusEventStore(self.workspace).load_workflow(
            "session-1", "conversation-1", "workflow-1"
        )
        self.assertEqual(
            loaded,
            ConsensusStoredWorkflow(submission, (request_event,)),
        )
        self.assertTrue(
            self.workflow_path(self.root, _IDENTITY, "workflow-1").is_file()
        )

    def test_create_collision_preserves_existing_bytes(self) -> None:
        submission, request_event = self.request_and_submission()
        self.store.create_workflow(submission, request_event)
        target = self.workflow_path(self.root, _IDENTITY, "workflow-1")
        before = target.read_bytes()
        with self.assertRaisesRegex(ConsensusEventStoreConflict, "already exists"):
            self.store.create_workflow(submission, request_event)
        self.assertEqual(target.read_bytes(), before)

    def test_load_rejects_foreign_identity_without_mutation(self) -> None:
        submission, request_event = self.request_and_submission()
        self.store.create_workflow(submission, request_event)
        foreign, foreign_event = self.request_and_submission(identity=_FOREIGN_IDENTITY)
        target = self.workflow_path(self.root, _IDENTITY, "workflow-1")
        target.write_bytes(
            serialize_consensus_workflow(
                ConsensusStoredWorkflow(foreign, (foreign_event,))
            )
        )
        before = target.read_bytes()
        with self.assertRaisesRegex(ConsensusEventStoreError, "foreign"):
            self.store.load_workflow("session-1", "conversation-1", "workflow-1")
        self.assertEqual(target.read_bytes(), before)

    def test_compare_and_swap_persists_tail_and_rejects_stale_or_bad_sequence(self) -> None:
        submission, request_event = self.request_and_submission()
        self.store.create_workflow(submission, request_event)
        request = request_event.payload
        decision = ConsensusPhaseDecision(
            confirmation_id="confirmation-1",
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            decision=ConsensusDecision.CONFIRM,
        )
        event = ConsensusEvent("decision-event", 2, decision)
        self.store.append_event("session-1", "conversation-1", "workflow-1", 1, event)
        loaded = self.store.load_workflow("session-1", "conversation-1", "workflow-1")
        self.assertEqual(loaded.events, (request_event, event))
        with self.assertRaisesRegex(ConsensusEventStoreConflict, "tail changed"):
            self.store.append_event("session-1", "conversation-1", "workflow-1", 1, event)
        with self.assertRaisesRegex(ConsensusEventStoreConflict, "does not extend"):
            self.store.append_event(
                "session-1", "conversation-1", "workflow-1", 2,
                ConsensusEvent("bad-event", 4, decision),
            )

    def test_concurrent_compare_and_swap_allows_one_winner(self) -> None:
        submission, request_event = self.request_and_submission()
        self.store.create_workflow(submission, request_event)
        request = request_event.payload
        decision = ConsensusPhaseDecision(
            confirmation_id="confirmation-1",
            phase_request_id=request.phase_request_id,
            request_event_id=request_event.event_id,
            request_event_sequence=1,
            proposal_id=request.proposal_id,
            execution_identity=request.execution_identity,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            decision=ConsensusDecision.CONFIRM,
        )
        event = ConsensusEvent("decision-event", 2, decision)

        def append_once():
            try:
                self.store.append_event("session-1", "conversation-1", "workflow-1", 1, event)
            except ConsensusEventStoreConflict:
                return "conflict"
            return "winner"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _item: append_once(), range(2)))
        self.assertEqual(sorted(outcomes), ["conflict", "winner"])
        self.assertEqual(
            len(self.store.load_workflow("session-1", "conversation-1", "workflow-1").events),
            2,
        )

    def test_write_failures_are_outcome_unknown_and_not_replayed(self) -> None:
        submission, request_event = self.request_and_submission()
        with mock.patch.object(self.workspace, "write_text", side_effect=OSError("disk")):
            with self.assertRaises(ConsensusEventStoreOutcomeUnknown):
                self.store.create_workflow(submission, request_event)
        self.assertIsNone(
            self.store.load_workflow("session-1", "conversation-1", "workflow-1")
        )

    def test_prompt_and_artifact_material_handles_round_trip_without_content(self) -> None:
        cases = (
            ("workflow-prompt", ConsensusInputKind.PROMPT, _PROMPT_MATERIAL),
            ("workflow-draft", ConsensusInputKind.SUPPLIED_DRAFT, _DRAFT_MATERIAL),
        )
        for workflow_id, input_kind, material in cases:
            with self.subTest(input_kind=input_kind):
                submission, request_event = self.request_and_submission(
                    workflow_id=workflow_id,
                    proposal_id=f"proposal-{workflow_id}",
                    input_kind=input_kind,
                    input_material=material,
                )
                self.store.create_workflow(submission, request_event)
                loaded = self.store.load_workflow("session-1", "conversation-1", workflow_id)
                self.assertEqual(loaded.submission.input_kind, input_kind)
                self.assertEqual(loaded.submission.input_material, material)


class DurableConsensusRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_exposes_state_without_replaying_confirmation_or_provider(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_test_workspace(Path(temporary.name) / "workspace")
        port = _DurableConsensusPort(workspace)
        first = ConsensusRuntimeAdapter(port)
        submitted = first.submit_prompt(
            session_id="session-1",
            conversation_id="conversation-1",
            input_material=_PROMPT_MATERIAL,
        )
        stored = port.load_workflow("session-1", "conversation-1", submitted.submission.workflow_id)
        request_event = stored.events[-1]
        request = request_event.payload
        from story_writing_agents.consensus import ConsensusRuntimeDecision

        decision = ConsensusRuntimeDecision(
            workflow_id=request.workflow_id,
            execution_identity=request.execution_identity,
            phase_request_id=request.phase_request_id,
            proposal_id=request.proposal_id,
            revision_id=request.revision_id,
            request_digest=request.request_digest,
            request_event_id=request_event.event_id,
            request_event_sequence=request_event.sequence,
            current_tail_sequence=request_event.sequence,
            confirmation_id="confirmation-1",
            decision=ConsensusDecision.CONFIRM,
        )
        restarted = ConsensusRuntimeAdapter(_DurableConsensusPort(workspace))
        resumed = restarted.resume(
            session_id="session-1",
            conversation_id="conversation-1",
            workflow_id=submitted.submission.workflow_id,
        )
        self.assertFalse(resumed.capability_available)
        with self.assertRaisesRegex(ValueError, "authority is unavailable"):
            await restarted.confirm(decision)
        self.assertEqual(port.provider_calls, [])


if __name__ == "__main__":
    unittest.main()
