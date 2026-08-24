"""Provider-free integration coverage for runtime artifact references."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, dataclass, replace

from story_writing_agents import (
    AgentReply,
    ConversationReply,
    DebateProgressEvent,
    DebateStatus,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    Phase,
    SPECIALIST_IDS,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    StoryRuntime,
    Workspace,
)
from story_writing_agents.agents import CHARACTER_CUSTODIAN
from story_writing_agents.conversation_retrieval import (
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction,
)
from story_writing_agents.conversation_search import (
    ConversationMemoryService,
    ConversationSearchScope,
)
from story_writing_agents.conversations import (
    ConversationEventKind,
)
from story_writing_agents.models import AgentDefinition, AgentRequest, ConversationRequest
from story_writing_agents.interactive_controller._runtime_policy import (
    validate_consensus_result,
    validate_conversation_retrieval_result,
)
from story_writing_agents.interactive_terminal._model import (
    LineInputState,
    MainCommandControlState,
    MainInputControlState,
)
from story_writing_agents.interactive_terminal._reference_activation import (
    ReferenceActivationKind,
    ReferenceActivationRequest,
    ReferenceInputScope,
    project_reference_input,
    replace_reference_completion,
    replace_reference_range,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
)
from story_writing_agents.writing_artifacts import (
    ArtifactReferenceDirectiveError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationState,
    ReferenceOperationFailure,
    ReferenceOperationState,
    ReferenceReadAction,
    ReferenceReadActionKind,
)
from story_writing_agents.writing_artifacts.runtime_references import (
    reference_operation_belongs_to_session,
)


_SESSION = "reference_session"
_OUTLINE = "outline-marker\noutline-two\n"
_PARTS = "part-outline-marker\n"
_CHARACTERS = "character-one\ncharacter-marker\ncharacter-three\n"
_RULES = "rules-marker\n"
_MANUSCRIPT = "manuscript-marker\n"
_SOURCE_TEXT = (
    "Use ${core_documents/global_outline.md} then "
    "${core_documents/character_sheets.md}#L2-L2 and "
    "${core_documents/narrative_rules.md} plus "
    "${manuscript/older drafts/{世界$ 章#1.md}; keep \\${literal} unchanged."
)
_EXPECTED_PATHS = (
    "core_documents/global_outline.md",
    "core_documents/character_sheets.md",
    "core_documents/narrative_rules.md",
    "manuscript/older drafts/{世界$ 章#1.md",
)
_EXPECTED_CONTENTS = (
    _OUTLINE.encode(),
    b"character-marker\n",
    _RULES.encode(),
    _MANUSCRIPT.encode(),
)


def _ui_built_source_text() -> str:
    """Build the shared four-reference prompt through terminal-owned edits."""

    state = MainCommandControlState(
        MainInputControlState(
            LineInputState(
                "Use $ then $ and $ plus $; keep \\${literal} unchanged.",
                0,
            )
        )
    )
    catalog = tuple(sorted(_EXPECTED_PATHS))
    for path, selected_range in zip(
        _EXPECTED_PATHS,
        (None, (2, 2), None, None),
    ):
        line = state.main_input.line_state.buffer
        start = line.index("$", state.main_input.line_state.cursor)
        state = MainCommandControlState(
            replace(
                state.main_input,
                line_state=LineInputState(line, start + 1),
            )
        )
        if selected_range is not None:
            state = replace_reference_range(
                state,
                ReferenceActivationRequest(
                    ReferenceActivationKind.VIEW,
                    start,
                    start + 1,
                    ReferenceInputScope(0, len(line)),
                    path=path,
                ),
                *selected_range,
            )
            continue
        projected = project_reference_input(
            line,
            start + 1,
            ReferenceInputScope(0, len(line)),
            catalog,
        )
        assert projected is not None and projected.completion is not None
        token = f"${{{path}}}"
        state = replace_reference_completion(
            state,
            projected,
            token,
        )
    return state.main_input.line_state.buffer


@dataclass(frozen=True)
class _ReadObservation:
    request: object
    contents: tuple[bytes, ...]


class _ReferenceBound:
    """Consume mandatory reads before returning deterministic provider replies."""

    def __init__(self, backend: _ReferenceBackend, definition: AgentDefinition) -> None:
        self.backend = backend
        self.definition = definition

    def _read(self, reference_read: object | None) -> None:
        if reference_read is None:
            self.backend.no_reference_provider_calls += 1
            return
        request = reference_read.request  # type: ignore[attr-defined]
        if self.backend.drift_before_next_read:
            self.backend.drift_before_next_read = False
            self.backend.workspace.path(_EXPECTED_PATHS[0]).write_text(
                "changed-after-capture\n",
                encoding="utf-8",
            )
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            request.correlation,
            request.references,
        )
        with reference_read.handle_reference_read_action(  # type: ignore[attr-defined]
            action
        ) as result:
            contents = result.contents
        reference_read.record_result_received()  # type: ignore[attr-defined]
        reference_read.accept()  # type: ignore[attr-defined]
        self.backend.reads.append(_ReadObservation(request, contents))

    async def invoke(
        self,
        request: AgentRequest,
        *,
        reference_read: object | None = None,
    ) -> AgentReply:
        self._read(reference_read)
        self.backend.agent_requests.append(request)
        if request.phase is self.backend.fail_debate_phase:
            raise RuntimeError("provider-secret-after-reference-read")
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, "candidate-r0")
        if request.phase is Phase.AUDIT:
            if request.revision_id == "r0" and request.agent_id in {
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
            }:
                return AgentReply.conflict(
                    request.revision_id,
                    f"{request.agent_id}-conflict",
                )
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.PROPOSE_REVISION:
            return AgentReply.candidate(
                request.revision_id,
                f"{request.agent_id}-proposal",
            )
        if request.phase is Phase.SYNTHESIZE:
            return AgentReply.candidate(request.revision_id, "candidate-r1")
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(
                request.revision_id,
                {
                    "global_outline.md": "updated global outline\n",
                    "character_sheets.md": "updated character sheets\n",
                    "narrative_rules.md": "updated narrative rules\n",
                },
            )
        raise AssertionError(f"unexpected phase: {request.phase}")

    async def converse(
        self,
        request: ConversationRequest,
        *,
        reference_read: object | None = None,
    ) -> ConversationReply:
        self._read(reference_read)
        self.backend.conversation_requests.append(request)
        if self.backend.fail_next_conversation:
            self.backend.fail_next_conversation = False
            raise RuntimeError("provider-secret-after-reference-read")
        return ConversationReply(
            request.session_id,
            request.conversation_id,
            request.turn_id,
            "provider-free reply",
        )

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: object,
        *,
        reference_read: object | None = None,
    ) -> ConversationRetrievalReply:
        self._read(reference_read)
        self.backend.retrieval_requests.append(request)
        for action_index in range(1, 5):
            action = ConversationRetrievalSearchAction(
                kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                action_index=action_index,
                query="Reference source",
            )
            handler.handle_conversation_retrieval_action(action)  # type: ignore[attr-defined]
            self.backend.retrieval_action_count += 1
        answer = ConversationRetrievalAnswerAction(
            kind=ConversationRetrievalActionKind.ANSWER,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=5,
            content="provider-free retrieval reply",
            citation_ids=(),
        )
        self.backend.retrieval_action_count += 1
        return handler.handle_conversation_retrieval_action(  # type: ignore[attr-defined,no-any-return]
            answer
        )


class _ReferenceBackend:
    """Provider-free backend with exact reference and request observations."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.reads: list[_ReadObservation] = []
        self.agent_requests: list[AgentRequest] = []
        self.conversation_requests: list[ConversationRequest] = []
        self.retrieval_requests: list[ConversationRetrievalRequest] = []
        self.retrieval_action_count = 0
        self.no_reference_provider_calls = 0
        self.drift_before_next_read = False
        self.fail_next_conversation = False
        self.fail_debate_phase: Phase | None = None

    async def bind(self, definition: AgentDefinition) -> _ReferenceBound:
        return _ReferenceBound(self, definition)

    def validate_provider_configuration(self, _session_id: str) -> bool:
        return True

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        return self._lifecycle(session_id, held=True)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        return self._lifecycle(session_id, held=True)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        return self._lifecycle(session_id, held=False)

    @staticmethod
    def _lifecycle(session_id: str, *, held: bool) -> SessionLifecycleState:
        return SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE
                if held
                else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="reference-test" if held else None,
            owner_pid=1 if held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )


class RuntimeReferenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Verify mandatory reads across public runtime operation families."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        for path, content in (
            ("core_documents/global_outline.md", _OUTLINE),
            ("core_documents/part_outlines.md", _PARTS),
            ("core_documents/character_sheets.md", _CHARACTERS),
            ("core_documents/narrative_rules.md", _RULES),
            ("manuscript/older drafts/{世界$ 章#1.md", _MANUSCRIPT),
        ):
            target = self.workspace.path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self.catalog = FilesystemConversationCatalog(self.workspace)
        self.store = FilesystemConversationEventStore(
            self.workspace,
            catalog=self.catalog,
        )
        self.selected = self.catalog.create(_SESSION, "Selected conversation")
        self.catalog.create(_SESSION, "Reference source")
        self.memory = ConversationMemoryService(
            ConversationSearchScope(_SESSION),
            self.catalog,
            self.store,
        )
        self.backend = _ReferenceBackend(self.workspace)
        self.runtime = StoryRuntime(
            self.workspace,
            self.backend,  # type: ignore[arg-type]
            conversation_event_store=self.store,
        )

    def assert_read_batch(self, observations: list[_ReadObservation]) -> None:
        self.assertTrue(observations)
        for observation in observations:
            references = observation.request.references  # type: ignore[attr-defined]
            self.assertEqual(tuple(item.path for item in references), _EXPECTED_PATHS)
            self.assertEqual(observation.contents, _EXPECTED_CONTENTS)
        correlations = [
            observation.request.correlation  # type: ignore[attr-defined]
            for observation in observations
        ]
        self.assertEqual(
            sorted(item.invocation_ordinal for item in correlations),
            list(range(1, len(correlations) + 1)),
        )
        self.assertEqual(len({item.invocation_id for item in correlations}), len(correlations))

    def assert_content_free_history(self, operation_id: str, expected_reads: int) -> None:
        history = self.runtime.inspect_reference_operation(operation_id)
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            [event.operation_state for event in history.operation_events],
            [
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.ACCEPTED,
            ],
        )
        self.assertEqual(len(history.invocation_events), expected_reads)
        self.assertTrue(
            all(
                events[-1].invocation_state is ReferenceInvocationState.ACCEPTED
                for events in history.invocation_events
            )
        )
        encoded = json.dumps(asdict(history))
        for marker in (
            "outline-marker",
            "character-marker",
            "rules-marker",
            "manuscript-marker",
        ):
            self.assertNotIn(marker, encoded)

    async def test_conversation_and_retrieval_share_ordered_reads_and_source_text(self) -> None:
        conversation = await self.runtime.session(_SESSION).converse(
            self.selected.conversation_id,
            PLOT_ARCHITECT,
            _SOURCE_TEXT,
        )
        self.assertIsNotNone(conversation.reference_operation_id)
        assert conversation.reference_operation_id is not None
        self.assertTrue(
            reference_operation_belongs_to_session(
                conversation.reference_operation_id,
                _SESSION,
            )
        )
        self.assertEqual(
            self.backend.conversation_requests[0].content,
            _SOURCE_TEXT.replace("\\${", "${"),
        )
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(events[0].content, _SOURCE_TEXT)
        self.assertEqual(events[0].kind, ConversationEventKind.USER_MESSAGE)
        self.assertEqual(events[1].kind, ConversationEventKind.AGENT_REPLY)
        self.assert_read_batch(self.backend.reads)
        self.assert_content_free_history(conversation.reference_operation_id, 1)

        retrieval = await self.runtime.converse_with_retrieval(
            _SESSION,
            self.selected.conversation_id,
            PLOT_ARCHITECT,
            _SOURCE_TEXT,
            memory_service=self.memory,
        )
        self.assertIsNotNone(retrieval.reference_operation_id)
        assert retrieval.reference_operation_id is not None
        self.assertTrue(
            reference_operation_belongs_to_session(
                retrieval.reference_operation_id,
                _SESSION,
            )
        )
        self.assertEqual(self.backend.retrieval_action_count, 5)
        self.assertEqual(len(self.backend.reads), 2)
        second = self.backend.reads[1].request.correlation  # type: ignore[attr-defined]
        first = self.backend.reads[0].request.correlation  # type: ignore[attr-defined]
        self.assertNotEqual(first.operation_id, second.operation_id)
        self.assert_content_free_history(retrieval.reference_operation_id, 1)
        self.assertEqual(
            validate_conversation_retrieval_result(
                retrieval,
                expected_session_id=_SESSION,
                expected_conversation_id=self.selected.conversation_id,
                expected_agent_id=PLOT_ARCHITECT,
                progress_events=(),
            ),
            retrieval,
        )
        self.assertIsNone(
            validate_conversation_retrieval_result(
                replace(retrieval, reference_operation_id=None),
                expected_session_id=_SESSION,
                expected_conversation_id=self.selected.conversation_id,
                expected_agent_id=PLOT_ARCHITECT,
                progress_events=(),
            ).reference_operation_id
        )
        foreign_ids = (
            "rr_11_different_session_" + "a" * 32,
            "rr_01_x_" + "b" * 32,
            "rr_malformed",
        )
        for operation_id in foreign_ids:
            with self.subTest(retrieval_operation_id=operation_id):
                with self.assertRaisesRegex(
                    ValueError,
                    "runtime reference operation ID is invalid",
                ):
                    validate_conversation_retrieval_result(
                        replace(retrieval, reference_operation_id=operation_id),
                        expected_session_id=_SESSION,
                        expected_conversation_id=self.selected.conversation_id,
                        expected_agent_id=PLOT_ARCHITECT,
                        progress_events=(),
                    )
        with self.assertRaisesRegex(
            ValueError,
            "retrieval result identity does not match its turn",
        ):
            validate_conversation_retrieval_result(
                replace(
                    retrieval,
                    agent_id=CHARACTER_CUSTODIAN,
                    reference_operation_id="rr_malformed",
                ),
                expected_session_id=_SESSION,
                expected_conversation_id=self.selected.conversation_id,
                expected_agent_id=PLOT_ARCHITECT,
                progress_events=(),
            )

    async def test_every_debate_provider_phase_reads_fresh_batch_before_modification(self) -> None:
        progress: list[DebateProgressEvent] = []
        source_text = _ui_built_source_text()
        self.assertEqual(source_text, _SOURCE_TEXT)
        result = await self.runtime.session(_SESSION).run_prompt(
            source_text,
            output_path="part-02/reference-result.md",
            debate_id="reference_debate",
            progress_sink=progress.append,
        )
        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertIsNotNone(result.reference_operation_id)
        assert result.reference_operation_id is not None
        self.assertTrue(
            reference_operation_belongs_to_session(
                result.reference_operation_id,
                _SESSION,
            )
        )
        self.assertEqual(len(self.backend.reads), 11)
        self.assert_read_batch(self.backend.reads)
        correlations = sorted(
            (
                item.request.correlation  # type: ignore[attr-defined]
                for item in self.backend.reads
            ),
            key=lambda item: item.invocation_ordinal,
        )
        self.assertEqual(
            [
                (item.agent_id, item.phase, item.round_number)
                for item in correlations
            ],
            [
                (EXECUTION_SPECIALIST, "draft", 0),
                *((agent_id, "audit", 0) for agent_id in SPECIALIST_IDS),
                (PLOT_ARCHITECT, "propose_revision", 1),
                (CHARACTER_CUSTODIAN, "propose_revision", 1),
                (LEAD_ORCHESTRATOR, "synthesize", 1),
                *((agent_id, "audit", 1) for agent_id in SPECIALIST_IDS),
                (LEAD_ORCHESTRATOR, "finalize", 1),
            ],
        )
        self.assertTrue(
            all(
                request.user_payload == source_text.replace("\\${", "${")
                for request in self.backend.agent_requests
            )
        )
        self.assertEqual(
            self.workspace.read_text("core_documents/global_outline.md"),
            "updated global outline\n",
        )
        self.assertEqual(
            self.workspace.read_text("core_documents/character_sheets.md"),
            "updated character sheets\n",
        )
        self.assertEqual(
            self.workspace.read_text("core_documents/narrative_rules.md"),
            "updated narrative rules\n",
        )
        self.assertEqual(
            self.workspace.read_text("manuscript/part-02/reference-result.md"),
            "candidate-r1",
        )
        inspected = self.runtime.inspect_debate(_SESSION, "reference_debate")
        self.assertEqual(
            inspected["result"]["reference_operation_id"],
            result.reference_operation_id,
        )
        self.assertEqual(inspected["messages"][0]["content"], source_text)
        terminal, artifact = validate_consensus_result(
            result,
            expected_session_id=_SESSION,
            expected_input_kind=result.input_kind,
            progress_events=tuple(progress),
        )
        self.assertEqual(terminal, progress[-1])
        self.assertEqual(artifact, progress[-2])
        without_references = replace(result, reference_operation_id=None)
        validate_consensus_result(
            without_references,
            expected_session_id=_SESSION,
            expected_input_kind=result.input_kind,
            progress_events=tuple(progress),
        )
        for operation_id in (
            "rr_11_different_session_" + "a" * 32,
            "rr_01_x_" + "b" * 32,
            "rr_malformed",
        ):
            with self.subTest(consensus_operation_id=operation_id):
                with self.assertRaisesRegex(
                    ValueError,
                    "runtime reference operation ID is invalid",
                ):
                    validate_consensus_result(
                        replace(result, reference_operation_id=operation_id),
                        expected_session_id=_SESSION,
                        expected_input_kind=result.input_kind,
                        progress_events=tuple(progress),
                    )
        with self.assertRaisesRegex(ValueError, "runtime consensus result is invalid"):
            validate_consensus_result(
                replace(
                    result,
                    session_id="foreign_session",
                    reference_operation_id="rr_malformed",
                ),
                expected_session_id=_SESSION,
                expected_input_kind=result.input_kind,
                progress_events=tuple(progress),
            )
        self.assert_content_free_history(result.reference_operation_id, 11)

    async def test_no_reference_preserves_provider_count_and_omits_identity(self) -> None:
        result = await self.runtime.converse(
            _SESSION,
            self.selected.conversation_id,
            PLOT_ARCHITECT,
            "ordinary advisory text",
        )
        self.assertIsNone(result.reference_operation_id)
        self.assertEqual(self.backend.no_reference_provider_calls, 1)
        self.assertEqual(self.backend.reads, [])

    async def test_drift_aborts_reply_and_records_content_free_terminal_outcome(self) -> None:
        self.backend.drift_before_next_read = True
        with self.assertRaises(ReferenceOperationFailure) as raised:
            await self.runtime.converse(
                _SESSION,
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                _SOURCE_TEXT,
            )
        self.assertEqual(raised.exception.code, ReferenceAuthorizationErrorCode.DRIFT)
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            [event.kind for event in events],
            [ConversationEventKind.USER_MESSAGE, ConversationEventKind.RUNTIME_ERROR],
        )
        self.assertNotIn("outline-marker", events[1].content)
        history = self.runtime.inspect_reference_operation(
            raised.exception.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            history.operation_events[-1].operation_state,
            ReferenceOperationState.TERMINATED,
        )
        self.assertEqual(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )
        self.assertEqual(
            history.invocation_events[0][-1].outcome,
            ReferenceAuthorizationErrorCode.DRIFT,
        )

    async def test_partial_unread_batch_aborts_before_conversation_mutation(self) -> None:
        with self.assertRaises(ArtifactReferenceDirectiveError):
            await self.runtime.converse(
                _SESSION,
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                "${",
            )
        self.assertEqual(self.store.read(_SESSION, self.selected.conversation_id), ())
        text = (
            "Use ${core_documents/global_outline.md} and "
            "${manuscript/part-99/missing.md}."
        )
        with self.assertRaises(ReferenceOperationFailure) as raised:
            await self.runtime.converse(
                _SESSION,
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                text,
            )
        self.assertEqual(
            raised.exception.code,
            ReferenceAuthorizationErrorCode.NOT_FOUND,
        )
        self.assertEqual(self.store.read(_SESSION, self.selected.conversation_id), ())
        self.assertEqual(self.backend.conversation_requests, [])
        self.assertEqual(self.backend.reads, [])
        history = self.runtime.inspect_reference_operation(
            raised.exception.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(history.invocation_events, ())
        self.assertEqual(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.NOT_FOUND,
        )

    async def test_provider_failure_after_read_aborts_reply_and_redacts_content(self) -> None:
        self.backend.fail_next_conversation = True
        with self.assertRaises(ReferenceOperationFailure) as raised:
            await self.runtime.session(_SESSION).converse(
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                _SOURCE_TEXT,
            )
        self.assertEqual(
            raised.exception.code,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )
        events = self.store.read(_SESSION, self.selected.conversation_id)
        self.assertEqual(
            [event.kind for event in events],
            [ConversationEventKind.USER_MESSAGE, ConversationEventKind.RUNTIME_ERROR],
        )
        surfaced = f"{raised.exception}\n{events[-1].content}"
        self.assertNotIn("provider-secret", surfaced)
        for content in _EXPECTED_CONTENTS:
            self.assertNotIn(content.decode().strip(), surfaced)
        history = self.runtime.inspect_reference_operation(
            raised.exception.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )

    async def test_debate_provider_failure_publishes_nothing_and_retains_evidence(self) -> None:
        before = {
            path: self.workspace.read_text(path)
            for path in _EXPECTED_PATHS
        }
        self.backend.fail_debate_phase = Phase.SYNTHESIZE
        result = await self.runtime.run_prompt(
            _SESSION,
            _SOURCE_TEXT,
            output_path="part-03/must-not-exist.md",
            debate_id="failed_reference_debate",
        )
        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIsNotNone(result.reference_operation_id)
        self.assertFalse(
            self.workspace.path("manuscript/part-03/must-not-exist.md").exists()
        )
        self.assertEqual(
            {path: self.workspace.read_text(path) for path in _EXPECTED_PATHS},
            before,
        )
        inspected = self.runtime.inspect_debate(
            _SESSION,
            "failed_reference_debate",
        )
        self.assertEqual(inspected["status"], "failed")
        self.assertNotIn("provider-secret", json.dumps(inspected))
        assert result.reference_operation_id is not None
        history = self.runtime.inspect_reference_operation(
            result.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            history.operation_events[-1].outcome,
            ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
        )


if __name__ == "__main__":
    unittest.main()
