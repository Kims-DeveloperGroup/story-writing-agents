"""Authorized conversation-RAG runtime contracts and integration tests."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import hashlib
import inspect
import unittest
import warnings
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, fields, replace
from unittest import mock

from story_writing_agents import (
    ConversationReply,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    StoryRuntime,
    Workspace,
)
from story_writing_agents import conversation_rag_composition as rag_composition
from story_writing_agents import conversation_rag_local as rag_local
from story_writing_agents import conversation_rag_store as rag_store
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagCandidate,
    ConversationRagDocumentKey,
    ConversationRagEmbeddingIdentity,
    ConversationRagOutcome,
    ConversationRagProjectionCheckpoint,
    ConversationRagProjectionCheckpointEntry,
    ConversationRagProjectionOutcome,
    ConversationRagProjectionRequest,
    ConversationRagProjectionResult,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagProjectionStatus,
    ConversationRagReason,
    ConversationRagRetrievalRequest,
    ConversationRagRetrievalResult,
    ConversationRagSourceReference,
)
from story_writing_agents.conversation_rag_runtime import (
    CONVERSATION_RAG_RUNTIME_POLICY,
    CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
    ConversationRagRuntimeError,
    ConversationRagRuntimePolicy,
    ConversationRagRuntimeService,
    ConversationRagRuntimeState,
    ConversationRagRuntimeStateSnapshot,
)
from story_writing_agents.conversation_retrieval import (
    ConversationRagContext,
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from story_writing_agents.conversation_search import (
    ConversationMemoryService,
    ConversationSearchScope,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageComponentBinding,
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import (
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
)
from story_writing_agents.models import AgentDefinition, ConversationRequest
from tests.helpers import initialized_workspace

_SESSION = "rag_runtime_session"
_DOCUMENT_ID_DOMAIN = "story-conversation-rag-visible-turn-document-id-v1"
_REVISION_ID_DOMAIN = "story-conversation-rag-visible-turn-revision-v1"
_DEFAULT = object()


def _framed_sha256(domain: str, *values: str) -> str:
    """Independent golden oracle for the accepted length-framed identifiers."""

    digest = hashlib.sha256()
    for value in (domain, *values):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _event_fields(event: ConversationEvent) -> tuple[str, ...]:
    row = event.to_dict()
    return tuple(
        str(row[name])
        for name in (
            "schema_version",
            "session_id",
            "conversation_id",
            "turn_id",
            "sequence",
            "timestamp",
            "sender",
            "recipient",
            "kind",
            "content",
        )
    )


def _missing_state() -> ConversationRagProjectionState:
    return ConversationRagProjectionState(
        availability=ConversationRagProjectionStateAvailability.MISSING,
        checkpoint=None,
    )


class _StatePort:
    """Small restart-persistent runtime-state double."""

    def __init__(self, workspace_key: str) -> None:
        self._workspace_key = workspace_key
        self._embedding = ConversationRagEmbeddingIdentity(
            model_id="test-embedding-v1",
            dimensions=3,
        )
        self.state = _missing_state()
        self.reason = ConversationRagReason.UNAVAILABLE
        self.inspections: list[str] = []
        self.stale_calls: list[str] = []
        self.stale_failure: BaseException | None = None

    @property
    def workspace_key(self) -> str:
        return self._workspace_key

    @property
    def embedding(self) -> ConversationRagEmbeddingIdentity:
        return self._embedding

    def inspect_projection_state(
        self,
        session_id: str,
        /,
    ) -> ConversationRagRuntimeStateSnapshot:
        self.inspections.append(session_id)
        return ConversationRagRuntimeStateSnapshot(
            session_id=session_id,
            state=self.state,
            reason=self.reason,
        )

    def mark_projection_stale(self, session_id: str, /) -> None:
        self.stale_calls.append(session_id)
        if self.stale_failure is not None:
            raise self.stale_failure
        self.reason = ConversationRagReason.STALE

    def publish(self, checkpoint: ConversationRagProjectionCheckpoint) -> None:
        self.state = ConversationRagProjectionState(
            availability=ConversationRagProjectionStateAvailability.PRESENT,
            checkpoint=checkpoint,
        )
        self.reason = ConversationRagReason.NONE


class _LowLevelService:
    """Provider-neutral service double that publishes exact checkpoints."""

    def __init__(self, state: _StatePort) -> None:
        self.state = state
        self.project_calls: list[
            tuple[ConversationRagProjectionRequest, ConversationRagProjectionState]
        ] = []
        self.rebuild_calls: list[
            tuple[ConversationRagProjectionRequest, ConversationRagProjectionState]
        ] = []
        self.semantic_calls: list[
            tuple[ConversationRagRetrievalRequest, ConversationRagProjectionState]
        ] = []
        self.hybrid_calls: list[object] = []
        self.semantic_value: object = _DEFAULT
        self.projection_value: object = _DEFAULT
        self.on_projection: (
            Callable[[ConversationRagProjectionRequest], None] | None
        ) = None
        self.active_probe: Callable[[], bool] | None = None
        self.provider_active_states: list[bool] = []

    def _projection(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
    ) -> object:
        del state
        if self.active_probe is not None:
            self.provider_active_states.append(self.active_probe())
        if isinstance(self.projection_value, BaseException):
            raise self.projection_value
        if self.projection_value is not _DEFAULT:
            return self.projection_value
        entries = tuple(
            ConversationRagProjectionCheckpointEntry(
                document=document.identity,
                source_digest="sha256:"
                + hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
                fragment_count=1,
            )
            for document in request.documents
        )
        checkpoint = ConversationRagProjectionCheckpoint(
            session_id=request.session_id,
            schema_id=request.schema_id,
            embedding=request.embedding,
            max_fragment_codepoints=CONVERSATION_RAG_POLICY.max_fragment_codepoints,
            fragment_overlap_codepoints=(
                CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
            ),
            entries=entries,
            token="checkpoint-token",
        )
        result = ConversationRagProjectionResult(
            status_before=ConversationRagProjectionStatus.MISSING,
            outcome=ConversationRagProjectionOutcome.COMPLETED,
            attempted_documents=len(request.documents),
            completed_documents=len(request.documents),
            checkpoint=checkpoint,
        )
        self.state.publish(checkpoint)
        if self.on_projection is not None:
            self.on_projection(request)
        return result

    def project_documents(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        self.project_calls.append((request, state))
        return self._projection(request, state)  # type: ignore[return-value]

    def rebuild_projection(
        self,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagProjectionResult:
        self.rebuild_calls.append((request, state))
        return self._projection(request, state)  # type: ignore[return-value]

    def retrieve_semantic(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        self.semantic_calls.append((request, state))
        if self.active_probe is not None:
            self.provider_active_states.append(self.active_probe())
        value = self.semantic_value
        if callable(value):
            value = value(request, state)
        if isinstance(value, BaseException):
            raise value
        if value is not _DEFAULT:
            return value  # type: ignore[return-value]
        return ConversationRagRetrievalResult(
            request=request,
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,
            candidates=(),
            truncated=False,
        )

    def retrieve_hybrid(
        self,
        request: ConversationRagRetrievalRequest,
        state: ConversationRagProjectionState,
        /,
    ) -> ConversationRagRetrievalResult:
        self.hybrid_calls.append((request, state))
        raise AssertionError("the runtime must not recurse into hybrid retrieval")


class _TracingCoordinator:
    """Observe the fixed lease timeout and provider/lease separation."""

    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.timeouts: list[float] = []
        self.active_count = 0

    @property
    def workspace_key(self) -> str:
        return self.delegate.workspace_key  # type: ignore[no-any-return]

    def borrow(self, *, timeout_seconds: float) -> contextlib.AbstractContextManager:
        self.timeouts.append(timeout_seconds)

        @contextlib.contextmanager
        def manager() -> Iterator[object]:
            with self.delegate.borrow(timeout_seconds=timeout_seconds) as lease:  # type: ignore[attr-defined]
                self.active_count += 1
                try:
                    yield lease
                finally:
                    self.active_count -= 1

        return manager()


def _with_coordinator(
    services: ConversationStorageServices,
    coordinator: object,
) -> ConversationStorageServices:
    return ConversationStorageServices(
        catalog_binding=ConversationStorageComponentBinding(
            services.provenance,
            services.catalog,
        ),
        event_store_binding=ConversationStorageComponentBinding(
            services.provenance,
            services.event_store,
        ),
        search_binding=ConversationStorageComponentBinding(
            services.provenance,
            services.search,
        ),
        mutation_coordinator=coordinator,  # type: ignore[arg-type]
    )


class _RuntimeBound:
    """Return deterministic advisory replies and one terminal retrieval action."""

    def __init__(self, backend: _RuntimeBackend, definition: AgentDefinition) -> None:
        self.backend = backend
        self.definition = definition

    async def invoke(self, _request: object) -> object:
        raise AssertionError("debate invocation is outside conversation RAG tests")

    async def converse(self, request: ConversationRequest) -> ConversationReply:
        self.backend.conversation_requests.append(request)
        return ConversationReply(
            request.session_id,
            request.conversation_id,
            request.turn_id,
            "ordinary reply",
        )

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        self.backend.retrieval_requests.append(request)
        citation_ids = self.backend.citation_ids
        reply = handler.handle_conversation_retrieval_action(
            ConversationRetrievalAnswerAction(
                kind=ConversationRetrievalActionKind.ANSWER,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                action_index=1,
                content="retrieval reply",
                citation_ids=citation_ids,
            )
        )
        assert type(reply) is ConversationRetrievalReply
        return reply


class _RuntimeBackend:
    """Minimal provider-free backend for StoryRuntime RAG orchestration."""

    def __init__(self, citation_ids: tuple[str, ...] = ()) -> None:
        self.citation_ids = citation_ids
        self.conversation_requests: list[ConversationRequest] = []
        self.retrieval_requests: list[ConversationRetrievalRequest] = []
        self.access_held: set[str] = set()

    def validate_provider_configuration(self, _session_id: str) -> bool:
        return True

    def _lifecycle(self, session_id: str) -> SessionLifecycleState:
        held = session_id in self.access_held
        return SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE if held else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="rag-test" if held else None,
            owner_pid=1 if held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        return self._lifecycle(session_id)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        self.access_held.add(session_id)
        return self._lifecycle(session_id)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        self.access_held.discard(session_id)
        return self._lifecycle(session_id)

    async def bind(self, definition: AgentDefinition) -> _RuntimeBound:
        return _RuntimeBound(self, definition)


class _EmptyLexicalRetriever:
    def search(
        self,
        _request: ConversationRagRetrievalRequest,
        /,
    ) -> tuple[object, ...]:
        return ()


class _LocalStoreState:
    """Adapt the real local generation store to the accepted runtime state port."""

    def __init__(self, workspace_key: str, store: object) -> None:
        self._workspace_key = workspace_key
        self.store = store
        self._embedding = rag_local.LocalConversationRagEmbedder().identity

    @property
    def workspace_key(self) -> str:
        return self._workspace_key

    @property
    def embedding(self) -> ConversationRagEmbeddingIdentity:
        return self._embedding

    def inspect_projection_state(
        self,
        session_id: str,
        /,
    ) -> ConversationRagRuntimeStateSnapshot:
        snapshot = self.store.inspect(  # type: ignore[attr-defined]
            session_id,
            rag_local.LOCAL_CONVERSATION_RAG_PROVIDER_ID,
            self.embedding,
            expected_projection_schema_id=CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
        )
        reasons = {
            rag_store.ConversationRagStoreStatus.MISSING: ConversationRagReason.UNAVAILABLE,
            rag_store.ConversationRagStoreStatus.READY: ConversationRagReason.NONE,
            rag_store.ConversationRagStoreStatus.STALE: ConversationRagReason.STALE,
            rag_store.ConversationRagStoreStatus.CORRUPT: ConversationRagReason.CORRUPT,
            rag_store.ConversationRagStoreStatus.INCOMPATIBLE: ConversationRagReason.INCOMPATIBLE,
            rag_store.ConversationRagStoreStatus.PROVIDER_FAILED: ConversationRagReason.FAILED,
        }
        return ConversationRagRuntimeStateSnapshot(
            session_id=session_id,
            state=snapshot.state,
            reason=reasons[snapshot.status],
        )

    def mark_projection_stale(self, session_id: str, /) -> None:
        self.store.set_status(  # type: ignore[attr-defined]
            session_id,
            rag_store.ConversationRagStoreStatus.STALE,
        )


class ConversationRagRuntimeTests(unittest.TestCase):
    """Verify contracts, authorization, projection state, and failure policy."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        raw = compose_filesystem_conversation_storage(self.workspace)
        self.coordinator = _TracingCoordinator(raw.mutation_coordinator)
        self.services = _with_coordinator(raw, self.coordinator)
        self.state = _StatePort(self.services.provenance.workspace_key)
        self.low_level = _LowLevelService(self.state)
        self.low_level.active_probe = lambda: self.coordinator.active_count > 0
        self.runtime = ConversationRagRuntimeService(self.low_level, self.state)

    def _conversation(self, subject: str) -> str:
        return self.services.catalog.create(_SESSION, subject).conversation_id

    def _append(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        user: str,
        reply: str | None,
        runtime_error: bool = False,
    ) -> tuple[ConversationEvent, ...]:
        stored = [
            self.services.event_store.append(
                ConversationEventAppend(
                    session_id=_SESSION,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    sender="user",
                    recipient=PLOT_ARCHITECT,
                    kind=ConversationEventKind.USER_MESSAGE,
                    content=user,
                )
            )
        ]
        if reply is not None:
            stored.append(
                self.services.event_store.append(
                    ConversationEventAppend(
                        session_id=_SESSION,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        sender="runtime" if runtime_error else PLOT_ARCHITECT,
                        recipient="user",
                        kind=(
                            ConversationEventKind.RUNTIME_ERROR
                            if runtime_error
                            else ConversationEventKind.AGENT_REPLY
                        ),
                        content=reply,
                    )
                )
            )
        return tuple(stored)

    def _project(self) -> ConversationRagProjectionRequest:
        self.runtime.project_authorized(self.services, _SESSION, rebuild=True)
        return self.low_level.rebuild_calls[-1][0]

    @staticmethod
    def _reference(
        document: object,
        start: int,
        end: int,
        fragment: str,
    ) -> ConversationRagSourceReference:
        return ConversationRagSourceReference(
            document=document.identity,  # type: ignore[attr-defined]
            fragment_id=fragment,
            start_codepoint=start,
            end_codepoint=end,
        )

    def test_contract_exports_policy_signatures_state_and_sync_ports_are_exact(
        self,
    ) -> None:
        self.assertEqual(
            CONVERSATION_RAG_RUNTIME_SCHEMA_ID, "story-conversation-rag-visible-turn-v1"
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationRagRuntimePolicy)),
            (
                "max_query_codepoints",
                "max_literal_candidates",
                "max_semantic_candidates",
                "max_excerpts",
                "max_excerpt_codepoints",
                "max_context_utf8_bytes",
                "max_projected_source_utf8_bytes",
            ),
        )
        self.assertEqual(
            tuple(
                getattr(CONVERSATION_RAG_RUNTIME_POLICY, field.name)
                for field in fields(ConversationRagRuntimePolicy)
            ),
            (200, 20, 20, 20, 800, 32_768, 33_554_432),
        )
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            CONVERSATION_RAG_RUNTIME_POLICY.max_query_codepoints = 1  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ConversationRagRuntimePolicy(max_query_codepoints=199)
        self.assertEqual(
            str(ConversationRagRuntimeError()),
            "conversation RAG runtime operation failed",
        )
        self.assertIsInstance(self.state, ConversationRagRuntimeState)
        self.assertEqual(
            tuple(inspect.signature(ConversationRagRuntimeService).parameters),
            ("low_level_service", "state_port", "automatic_enabled"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(ConversationRagRuntimeService.preflight).parameters
            ),
            (
                "self",
                "conversation_storage_services",
                "session_id",
                "turn_text",
                "forced_query",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    ConversationRagRuntimeService.project_authorized
                ).parameters
            ),
            (
                "self",
                "conversation_storage_services",
                "session_id",
                "rebuild",
            ),
        )
        self.assertFalse(self.runtime.automatic_enabled)

        missing = ConversationRagRuntimeStateSnapshot(
            session_id=_SESSION,
            state=_missing_state(),
            reason=ConversationRagReason.UNAVAILABLE,
        )
        self.assertFalse(hasattr(missing, "__dict__"))
        with self.assertRaises(ValueError):
            replace(missing, reason=ConversationRagReason.NONE)

        class AsyncService(_LowLevelService):
            async def retrieve_semantic(self, request, state, /):  # type: ignore[no-untyped-def, override]
                return await super().retrieve_semantic(request, state)

        with self.assertRaisesRegex(TypeError, "low-level"):
            ConversationRagRuntimeService(AsyncService(self.state), self.state)

        class AsyncState(_StatePort):
            async def mark_projection_stale(self, session_id, /):  # type: ignore[no-untyped-def, override]
                del session_id

        with self.assertRaisesRegex(TypeError, "state port"):
            ConversationRagRuntimeService(
                self.low_level,
                AsyncState(self.services.provenance.workspace_key),
            )

    def test_projection_enumerates_only_active_complete_replies_with_golden_ids(
        self,
    ) -> None:
        active = self._conversation("Café 🙂")
        user, reply = self._append(
            active,
            "turn_visible",
            user="Question 한🙂",
            reply="Authoritative answer",
        )
        incomplete = self._conversation("Incomplete")
        self._append(incomplete, "turn_incomplete", user="pending", reply=None)
        errored = self._conversation("Errored")
        self._append(
            errored,
            "turn_error",
            user="not visible",
            reply="safe error",
            runtime_error=True,
        )
        archived = self._conversation("Archived")
        self._append(archived, "turn_archived", user="old", reply="old reply")
        self.services.catalog.archive(_SESSION, archived)

        result = self.runtime.project_authorized(
            self.services,
            _SESSION,
            rebuild=True,
        )
        self.assertEqual(len(self.low_level.rebuild_calls), 1)
        self.assertEqual(len(self.low_level.project_calls), 0)
        request = self.low_level.rebuild_calls[0][0]
        self.assertEqual(request.session_id, _SESSION)
        self.assertEqual(request.schema_id, CONVERSATION_RAG_RUNTIME_SCHEMA_ID)
        self.assertEqual(len(request.documents), 1)
        document = request.documents[0]
        source_text = (
            "Subject: Café 🙂\nUser: Question 한🙂\n"
            f"Agent[{PLOT_ARCHITECT}]: Authoritative answer"
        )
        self.assertEqual(document.text, source_text)
        expected_document_id = _framed_sha256(
            _DOCUMENT_ID_DOMAIN,
            CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
            _SESSION,
            active,
            "turn_visible",
        )
        self.assertEqual(document.identity.key.document_id, expected_document_id)
        metadata = self.services.catalog.get(_SESSION, active)
        revision_fields = (
            CONVERSATION_RAG_RUNTIME_SCHEMA_ID,
            str(metadata.schema_version),
            metadata.session_id,
            metadata.conversation_id,
            metadata.subject,
            str(metadata.subject_revision),
            *_event_fields(user),
            *_event_fields(reply),
            source_text,
        )
        self.assertEqual(
            document.identity.revision_id,
            "sha256:" + _framed_sha256(_REVISION_ID_DOMAIN, *revision_fields),
        )
        self.assertEqual(result.checkpoint.entries[0].document, document.identity)
        self.assertEqual(self.state.reason, ConversationRagReason.NONE)
        self.assertEqual(self.coordinator.timeouts, [30.0, 30.0])
        self.assertEqual(self.coordinator.active_count, 0)
        self.assertEqual(self.low_level.provider_active_states, [False])

        restarted = ConversationRagRuntimeService(self.low_level, self.state)
        context = restarted.preflight(
            self.services,
            _SESSION,
            "current turn",
            forced_query="Café",
        )
        self.assertIsInstance(context, ConversationRagContext)
        assert context is not None
        self.assertEqual(context.outcome.outcome, ConversationRagOutcome.COMPLETE)
        self.assertEqual(tuple(item.citation_id for item in context.excerpts), ("g1",))
        self.assertEqual(context.excerpts[0].content, "Café 🙂")
        self.assertEqual(len(self.low_level.semantic_calls), 1)
        self.assertEqual(self.coordinator.timeouts, [30.0, 30.0, 30.0])
        self.assertEqual(self.low_level.provider_active_states, [False, False])


class ConversationRagOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    """Verify StoryRuntime attempt framing, citation seeding, and ordering."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        raw = compose_filesystem_conversation_storage(self.workspace)
        self.coordinator = _TracingCoordinator(raw.mutation_coordinator)
        self.services = _with_coordinator(raw, self.coordinator)
        self.state = _StatePort(self.services.provenance.workspace_key)
        self.low_level = _LowLevelService(self.state)
        self.low_level.active_probe = lambda: self.coordinator.active_count > 0
        self.rag = ConversationRagRuntimeService(self.low_level, self.state)
        self.runtime = self.rag
        self.selected = self.services.catalog.create(_SESSION, "Selected")
        self.memory = ConversationMemoryService(
            ConversationSearchScope(_SESSION),
            self.services.catalog,
            self.services.event_store,
        )

    def _conversation(self, subject: str) -> str:
        return self.services.catalog.create(_SESSION, subject).conversation_id

    def _append(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        user: str,
        reply: str | None,
        runtime_error: bool = False,
    ) -> tuple[ConversationEvent, ...]:
        stored = [
            self.services.event_store.append(
                ConversationEventAppend(
                    session_id=_SESSION,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    sender="user",
                    recipient=PLOT_ARCHITECT,
                    kind=ConversationEventKind.USER_MESSAGE,
                    content=user,
                )
            )
        ]
        if reply is not None:
            stored.append(
                self.services.event_store.append(
                    ConversationEventAppend(
                        session_id=_SESSION,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        sender="runtime" if runtime_error else PLOT_ARCHITECT,
                        recipient="user",
                        kind=(
                            ConversationEventKind.RUNTIME_ERROR
                            if runtime_error
                            else ConversationEventKind.AGENT_REPLY
                        ),
                        content=reply,
                    )
                )
            )
        return tuple(stored)

    def _project(self) -> ConversationRagProjectionRequest:
        self.runtime.project_authorized(self.services, _SESSION, rebuild=True)
        return self.low_level.rebuild_calls[-1][0]

    @staticmethod
    def _reference(
        document: object,
        start: int,
        end: int,
        fragment: str,
    ) -> ConversationRagSourceReference:
        return ConversationRagSourceReference(
            document=document.identity,  # type: ignore[attr-defined]
            fragment_id=fragment,
            start_codepoint=start,
            end_codepoint=end,
        )

    def _append_source(
        self,
        subject: str,
        ordinal: int,
    ) -> str:
        conversation = self.services.catalog.create(_SESSION, subject)
        turn_id = f"source_turn_{ordinal}"
        self.services.event_store.append(
            ConversationEventAppend(
                session_id=_SESSION,
                conversation_id=conversation.conversation_id,
                turn_id=turn_id,
                sender="user",
                recipient=PLOT_ARCHITECT,
                kind=ConversationEventKind.USER_MESSAGE,
                content=f"source question {ordinal}",
            )
        )
        self.services.event_store.append(
            ConversationEventAppend(
                session_id=_SESSION,
                conversation_id=conversation.conversation_id,
                turn_id=turn_id,
                sender=PLOT_ARCHITECT,
                recipient="user",
                kind=ConversationEventKind.AGENT_REPLY,
                content=f"source answer {ordinal}",
            )
        )
        return conversation.conversation_id

    async def _retrieval(
        self,
        runtime: StoryRuntime,
        text: str,
        *,
        rag_query: str | None = None,
        turn_id: str = "current_turn",
    ):
        with mock.patch.object(
            StoryRuntime,
            "_new_conversation_turn_id",
            return_value=turn_id,
        ):
            return await runtime.converse_with_retrieval(
                _SESSION,
                self.selected.conversation_id,
                PLOT_ARCHITECT,
                text,
                memory_service=self.memory,
                rag_query=rag_query,
            )

    async def test_forced_without_service_attempts_disabled_and_invalid_precedes_io(
        self,
    ) -> None:
        backend = _RuntimeBackend()
        runtime = StoryRuntime(
            self.workspace,
            backend,  # type: ignore[arg-type]
            conversation_storage_services=self.services,
        )
        result = await self._retrieval(
            runtime,
            "visible question",
            rag_query="forced query",
        )
        request = backend.retrieval_requests[0]
        self.assertIsNotNone(request.rag_context)
        assert request.rag_context is not None
        self.assertEqual(request.rag_context, ConversationRagContext.disabled())
        self.assertEqual(result.rag_outcome, request.rag_context.outcome)

        for invalid in ("", "x" * 201):
            with self.subTest(invalid_length=len(invalid)):
                before = self.services.event_store.read(
                    _SESSION,
                    self.selected.conversation_id,
                )
                with (
                    mock.patch.object(
                        self.services.event_store,
                        "read_context",
                        wraps=self.services.event_store.read_context,
                    ) as read_context,
                    mock.patch.object(
                        self.services.event_store,
                        "append",
                        wraps=self.services.event_store.append,
                    ) as append,
                    self.assertRaises(ValueError),
                ):
                    await self._retrieval(
                        runtime,
                        "must not run",
                        rag_query=invalid,
                        turn_id=f"invalid_{len(invalid)}",
                    )
                read_context.assert_not_called()
                append.assert_not_called()
                self.assertEqual(
                    self.services.event_store.read(
                        _SESSION,
                        self.selected.conversation_id,
                    ),
                    before,
                )
        self.assertEqual(len(backend.retrieval_requests), 1)

    async def test_forced_automatic_no_attempt_and_g_citations_are_one_preflight(
        self,
    ) -> None:
        source_id = self._append_source("Semantic needle", 1)
        self.rag.project_authorized(self.services, _SESSION, rebuild=True)
        self.low_level.semantic_calls.clear()
        backend = _RuntimeBackend(("g1",))
        runtime = StoryRuntime(
            self.workspace,
            backend,  # type: ignore[arg-type]
            conversation_storage_services=self.services,
            conversation_rag_service=self.rag,
        )
        forced = await self._retrieval(
            runtime,
            "current forced question",
            rag_query="Semantic needle",
        )
        request = backend.retrieval_requests[-1]
        assert request.rag_context is not None
        self.assertEqual(
            tuple(item.citation_id for item in request.rag_context.excerpts), ("g1",)
        )
        self.assertEqual(
            request.rag_context.excerpts[0].source.citation.conversation_id, source_id
        )
        self.assertEqual(len(forced.sources), 1)
        self.assertEqual(forced.rag_outcome, request.rag_context.outcome)
        self.assertEqual(len(self.low_level.semantic_calls), 1)
        self.assertEqual(self.low_level.hybrid_calls, [])

        backend.citation_ids = ()
        no_attempt = await self._retrieval(
            runtime,
            "ordinary no attempt",
            turn_id="no_attempt",
        )
        self.assertIsNone(backend.retrieval_requests[-1].rag_context)
        self.assertIsNone(no_attempt.rag_outcome)
        self.assertEqual(len(self.low_level.semantic_calls), 1)

        automatic = ConversationRagRuntimeService(
            self.low_level,
            self.state,
            automatic_enabled=True,
        )
        auto_runtime = StoryRuntime(
            self.workspace,
            backend,  # type: ignore[arg-type]
            conversation_storage_services=self.services,
            conversation_rag_service=automatic,
        )
        long_text = ("automatic words " * 30).strip()
        automatic_result = await self._retrieval(
            auto_runtime,
            long_text,
            turn_id="automatic_turn",
        )
        auto_request = backend.retrieval_requests[-1]
        self.assertIsNotNone(auto_request.rag_context)
        self.assertIsNotNone(automatic_result.rag_outcome)
        self.assertEqual(len(self.low_level.semantic_calls[-1][0].text), 200)
        self.assertEqual(
            self.low_level.semantic_calls[-1][0].text,
            long_text[:200].rstrip(),
        )

        empty = await self._retrieval(
            auto_runtime,
            " \t\n ",
            turn_id="automatic_empty",
        )
        self.assertIsNone(backend.retrieval_requests[-1].rag_context)
        self.assertIsNone(empty.rag_outcome)

    async def test_all_twenty_preflight_citations_seed_the_handler(self) -> None:
        for ordinal in range(1, 21):
            self._append_source(f"Shared needle {ordinal:02d}", ordinal)
        backend = _RuntimeBackend(("g1", "g20"))
        runtime = StoryRuntime(
            self.workspace,
            backend,  # type: ignore[arg-type]
            conversation_storage_services=self.services,
            conversation_rag_service=self.rag,
        )
        result = await self._retrieval(
            runtime,
            "current",
            rag_query="Shared needle",
        )
        request = backend.retrieval_requests[0]
        assert request.rag_context is not None
        self.assertEqual(
            tuple(item.citation_id for item in request.rag_context.excerpts),
            tuple(f"g{ordinal}" for ordinal in range(1, 21)),
        )
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(result.rag_outcome, request.rag_context.outcome)
        self.assertEqual(self.low_level.semantic_calls, [])

    async def test_reply_commits_before_projection_and_failures_isolate_or_propagate(
        self,
    ) -> None:
        cases = ("conversation", "retrieval")
        for ordinal, operation in enumerate(cases, start=1):
            with self.subTest(operation=operation, failure="ordinary"):
                selected = self.services.catalog.create(
                    _SESSION,
                    f"Ordinary projection {operation}",
                )
                self.selected = selected
                self.low_level.projection_value = RuntimeError(
                    "projection provider secret"
                )
                backend = _RuntimeBackend()
                runtime = StoryRuntime(
                    self.workspace,
                    backend,  # type: ignore[arg-type]
                    conversation_storage_services=self.services,
                    conversation_rag_service=self.rag,
                )
                turn_id = f"ordinary_{ordinal}"
                with mock.patch.object(
                    StoryRuntime,
                    "_new_conversation_turn_id",
                    return_value=turn_id,
                ):
                    if operation == "conversation":
                        result = await runtime.converse(
                            _SESSION,
                            selected.conversation_id,
                            PLOT_ARCHITECT,
                            "question",
                        )
                    else:
                        result = await runtime.converse_with_retrieval(
                            _SESSION,
                            selected.conversation_id,
                            PLOT_ARCHITECT,
                            "question",
                            memory_service=self.memory,
                        )
                self.assertIn("reply", result.content)
                events = self.services.event_store.read(
                    _SESSION,
                    selected.conversation_id,
                )
                self.assertEqual(
                    tuple(event.kind for event in events),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.AGENT_REPLY,
                    ),
                )
                self.assertEqual(
                    tuple(event.turn_id for event in events), (turn_id, turn_id)
                )
                self.assertEqual(self.state.stale_calls[-1], _SESSION)

        for ordinal, (operation, control) in enumerate(
            (
                ("conversation", KeyboardInterrupt()),
                ("conversation", SystemExit("projection control")),
                ("retrieval", asyncio.CancelledError()),
            ),
            start=1,
        ):
            with self.subTest(operation=operation, control=type(control).__name__):
                selected = self.services.catalog.create(
                    _SESSION,
                    f"Control projection {ordinal}",
                )
                self.selected = selected
                self.low_level.projection_value = control
                backend = _RuntimeBackend()
                runtime = StoryRuntime(
                    self.workspace,
                    backend,  # type: ignore[arg-type]
                    conversation_storage_services=self.services,
                    conversation_rag_service=self.rag,
                )
                turn_id = f"control_{ordinal}"
                with (
                    mock.patch.object(
                        StoryRuntime,
                        "_new_conversation_turn_id",
                        return_value=turn_id,
                    ),
                    self.assertRaises(type(control)),
                ):
                    if operation == "conversation":
                        await runtime.converse(
                            _SESSION,
                            selected.conversation_id,
                            PLOT_ARCHITECT,
                            "question",
                        )
                    else:
                        await runtime.converse_with_retrieval(
                            _SESSION,
                            selected.conversation_id,
                            PLOT_ARCHITECT,
                            "question",
                            memory_service=self.memory,
                        )
                events = self.services.event_store.read(
                    _SESSION,
                    selected.conversation_id,
                )
                self.assertEqual(
                    tuple(event.kind for event in events),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.AGENT_REPLY,
                    ),
                )

    def test_preflight_merges_literal_first_one_semantic_per_document_and_dedups(
        self,
    ) -> None:
        first = self._conversation("Literal needle")
        self._append(first, "turn_first", user="first user", reply="first answer")
        second = self._conversation("Second subject")
        self._append(second, "turn_second", user="second user", reply="second answer")
        request = self._project()
        by_conversation = {
            document.identity.conversation_id: document
            for document in request.documents
        }
        first_document = by_conversation[first]
        second_document = by_conversation[second]
        first_start = first_document.text.index("first answer")
        second_start = second_document.text.index("second answer")
        candidates = (
            ConversationRagCandidate(
                source=self._reference(
                    first_document,
                    first_start,
                    first_start + len("first answer"),
                    "first-semantic",
                ),
                rank=1,
            ),
            ConversationRagCandidate(
                source=self._reference(
                    first_document,
                    first_start,
                    first_start + len("first"),
                    "same-document-later",
                ),
                rank=2,
            ),
            ConversationRagCandidate(
                source=self._reference(
                    second_document,
                    second_start,
                    second_start + len("second answer"),
                    "second-semantic",
                ),
                rank=3,
            ),
        )

        def semantic(
            semantic_request: ConversationRagRetrievalRequest,
            _state: ConversationRagProjectionState,
        ) -> ConversationRagRetrievalResult:
            return ConversationRagRetrievalResult(
                request=semantic_request,
                outcome=ConversationRagOutcome.COMPLETE,
                reason=ConversationRagReason.NONE,
                candidates=candidates,
                truncated=False,
            )

        self.low_level.semantic_value = semantic
        context = self.runtime.preflight(
            self.services,
            _SESSION,
            "turn",
            forced_query="Literal needle",
        )
        assert context is not None
        self.assertEqual(context.outcome.outcome, ConversationRagOutcome.COMPLETE)
        self.assertEqual(context.outcome.reason, ConversationRagReason.NONE)
        self.assertFalse(context.truncated)
        self.assertEqual(
            tuple(item.citation_id for item in context.excerpts), ("g1", "g2")
        )
        self.assertEqual(context.excerpts[0].content, "Literal needle")
        self.assertEqual(context.excerpts[1].content, "second answer")
        self.assertEqual(
            context.retained_utf8_bytes,
            sum(len(item.content.encode("utf-8")) for item in context.excerpts),
        )
        self.assertEqual(self.state.stale_calls, [])
        self.assertEqual(len(self.low_level.semantic_calls), 1)
        self.assertEqual(self.low_level.hybrid_calls, [])

    def test_rejected_semantic_identity_revision_range_and_session_mark_stale(
        self,
    ) -> None:
        conversation = self._conversation("Authoritative")
        self._append(conversation, "turn_a", user="visible user", reply="visible reply")
        request = self._project()
        document = request.documents[0]
        good_start = document.text.index("visible reply")
        variants = (
            replace(
                document.identity,
                key=ConversationRagDocumentKey(
                    session_id=_SESSION,
                    document_id="f" * 64,
                ),
            ),
            replace(document.identity, revision_id="sha256:" + "e" * 64),
            replace(
                document.identity,
                key=ConversationRagDocumentKey(
                    session_id="foreign_session",
                    document_id=document.identity.key.document_id,
                ),
            ),
        )
        references = [
            ConversationRagSourceReference(
                document=identity,
                fragment_id=f"invalid-{index}",
                start_codepoint=good_start,
                end_codepoint=good_start + len("visible reply"),
            )
            for index, identity in enumerate(variants, start=1)
        ]
        references.append(
            ConversationRagSourceReference(
                document=document.identity,
                fragment_id="out-of-range",
                start_codepoint=len(document.text),
                end_codepoint=len(document.text) + 1,
            )
        )

        for reference in references:
            with self.subTest(fragment=reference.fragment_id):
                self.state.publish(self.state.state.checkpoint)  # type: ignore[arg-type]

                def semantic(
                    semantic_request: ConversationRagRetrievalRequest,
                    _state: ConversationRagProjectionState,
                    *,
                    selected: ConversationRagSourceReference = reference,
                ) -> ConversationRagRetrievalResult:
                    return ConversationRagRetrievalResult(
                        request=semantic_request,
                        outcome=ConversationRagOutcome.COMPLETE,
                        reason=ConversationRagReason.NONE,
                        candidates=(ConversationRagCandidate(selected, 1),),
                        truncated=False,
                    )

                self.low_level.semantic_value = semantic
                context = self.runtime.preflight(
                    self.services,
                    _SESSION,
                    "turn",
                    forced_query="no-literal-match",
                )
                assert context is not None
                self.assertEqual(context.excerpts, ())
                self.assertEqual(
                    context.outcome.outcome, ConversationRagOutcome.PARTIAL
                )
                self.assertEqual(context.outcome.reason, ConversationRagReason.PARTIAL)
                self.assertTrue(context.truncated)
                self.assertEqual(self.state.stale_calls[-1], _SESSION)

    def test_ordinary_and_dynamic_async_failures_are_content_free_and_stale(
        self,
    ) -> None:
        conversation = self._conversation("Async source")
        self._append(conversation, "turn_async", user="question", reply="answer")
        self._project()

        async def delayed_result() -> ConversationRagRetrievalResult:
            raise AssertionError("must never be awaited")

        self.low_level.semantic_value = lambda _request, _state: delayed_result()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            context = self.runtime.preflight(
                self.services,
                _SESSION,
                "turn",
                forced_query="absent semantic query",
            )
            gc.collect()
        assert context is not None
        self.assertEqual(
            context.outcome.outcome, ConversationRagOutcome.LITERAL_FALLBACK
        )
        self.assertEqual(context.outcome.reason, ConversationRagReason.FAILED)
        self.assertEqual(context.excerpts, ())

        self.state.publish(self.state.state.checkpoint)  # type: ignore[arg-type]
        self.low_level.projection_value = delayed_result()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with self.assertRaises(ConversationRagRuntimeError) as raised:
                self.runtime.project_authorized(self.services, _SESSION)
            gc.collect()
        self.assertEqual(
            str(raised.exception), "conversation RAG runtime operation failed"
        )
        self.assertNotIn("must never", repr(raised.exception))
        self.assertEqual(self.state.stale_calls[-1], _SESSION)

    def test_real_local_provider_store_persists_across_runtime_and_archive(
        self,
    ) -> None:
        source = self._conversation("Durable local source")
        self._append(
            source,
            "turn_local",
            user="durable semantic needle",
            reply="local authoritative answer",
        )
        store = rag_store.ConversationRagGenerationStore(self.workspace)
        state = _LocalStoreState(self.services.provenance.workspace_key, store)
        with mock.patch.object(
            rag_composition.metadata,
            "version",
            return_value="0.1.0",
        ):
            local_service = rag_composition.compose_local_conversation_rag(
                store,
                _EmptyLexicalRetriever(),
            )
            runtime = ConversationRagRuntimeService(local_service, state)
            projected = runtime.project_authorized(
                self.services,
                _SESSION,
                rebuild=True,
            )
            self.assertEqual(len(projected.checkpoint.entries), 1)
            self.assertEqual(
                store.inspect(
                    _SESSION,
                    rag_local.LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                    state.embedding,
                    expected_projection_schema_id=(CONVERSATION_RAG_RUNTIME_SCHEMA_ID),
                ).status,
                rag_store.ConversationRagStoreStatus.READY,
            )
            legacy_view = store.inspect(
                _SESSION,
                rag_local.LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                state.embedding,
            )
            self.assertEqual(
                legacy_view.status,
                rag_store.ConversationRagStoreStatus.INCOMPATIBLE,
            )
            self.assertIs(
                legacy_view.state.availability,
                ConversationRagProjectionStateAvailability.PRESENT,
            )
            self.assertIs(
                state.inspect_projection_state(_SESSION).reason,
                ConversationRagReason.NONE,
            )

            reopened_store = rag_store.ConversationRagGenerationStore(
                Workspace(self.workspace.root)
            )
            reopened_state = _LocalStoreState(
                self.services.provenance.workspace_key,
                reopened_store,
            )
            reopened_service = rag_composition.compose_local_conversation_rag(
                reopened_store,
                _EmptyLexicalRetriever(),
            )
            restarted = ConversationRagRuntimeService(
                reopened_service,
                reopened_state,
            )
            context = restarted.preflight(
                self.services,
                _SESSION,
                "current",
                forced_query="durable semantic needle",
            )
            assert context is not None
            self.assertEqual(context.outcome.outcome, ConversationRagOutcome.COMPLETE)
            self.assertEqual(len(context.excerpts), 1)
            self.assertEqual(context.excerpts[0].content, "durable semantic needle")

            self.services.catalog.archive(_SESSION, source)
            removed = restarted.project_authorized(self.services, _SESSION)
            self.assertEqual(removed.checkpoint.entries, ())
            after_archive = restarted.preflight(
                self.services,
                _SESSION,
                "current",
                forced_query="durable semantic needle",
            )
            assert after_archive is not None
            self.assertEqual(after_archive.excerpts, ())
            self.assertNotIn("local authoritative answer", repr(after_archive))

    def test_projection_drift_and_failures_mark_stale_but_controls_propagate(
        self,
    ) -> None:
        conversation = self._conversation("Drift source")
        self._append(conversation, "turn_drift", user="question", reply="answer")
        self.low_level.on_projection = lambda _request: self.services.catalog.rename(
            _SESSION,
            conversation,
            "Changed during publication",
        )
        with self.assertRaises(ConversationRagRuntimeError):
            self.runtime.project_authorized(self.services, _SESSION)
        self.assertEqual(self.state.stale_calls, [_SESSION])
        self.assertEqual(self.coordinator.active_count, 0)

        self.low_level.on_projection = None
        self.low_level.projection_value = RuntimeError("provider projection secret")
        self.state.stale_failure = SystemExit("cleanup control")
        with self.assertRaisesRegex(SystemExit, "cleanup control"):
            self.runtime.project_authorized(self.services, _SESSION)

        self.state.stale_failure = None
        for control in (KeyboardInterrupt(), SystemExit("provider control")):
            self.low_level.projection_value = control
            with (
                self.subTest(control=type(control).__name__),
                self.assertRaises(type(control)),
            ):
                self.runtime.project_authorized(self.services, _SESSION)
        self.assertEqual(self.coordinator.active_count, 0)


if __name__ == "__main__":
    unittest.main()
