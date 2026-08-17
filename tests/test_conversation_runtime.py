"""StoryRuntime advisory-conversation sequencing and failure contracts."""

from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import unicodedata
import unittest
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from typing import Any
from unittest import mock

from story_writing_agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    AgentDefinition,
    BackendProtocolError,
    ConfigurationError,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationNotFoundError,
    ConversationReply,
    ConversationRequest,
    ConversationResult,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    PLOT_ARCHITECT,
    RuntimeSession,
    SessionAccessState,
    SessionDurableState,
    SessionIdError,
    SessionLifecycleState,
    StoryRuntime,
    Workspace,
    WorkspaceFormatError,
)
from story_writing_agents.agents import CORE_DOCUMENTS

from tests.conversation_event_store_contract import (
    InMemoryConversationEventStore,
    StaticConversationCatalog,
    make_append,
)
from tests.helpers import all_manuscripts, core_snapshot, initialized_workspace


_TIMESTAMP = "2026-08-15T01:02:03+00:00"
_DELEGATE = object()


def _empty_context() -> ConversationContextWindow:
    return ConversationContextWindow(
        events=(),
        total_complete_turns=0,
        retained_turns=0,
        omitted_turns=0,
        retained_utf8_bytes=0,
        truncated=False,
    )


def _append_turn(
    store: Any,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    *,
    agent_id: str = PLOT_ARCHITECT,
    user_content: str = "prior question",
    outcome_content: str = "prior reply",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    user = store.append(
        make_append(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            recipient=agent_id,
            content=user_content,
        )
    )
    outcome = store.append(
        make_append(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            kind=outcome_kind,
            sender=(
                agent_id
                if outcome_kind is ConversationEventKind.AGENT_REPLY
                else "runtime"
            ),
            content=outcome_content,
        )
    )
    return user, outcome


def _stored_event(
    requested: ConversationEventAppend,
    **overrides: object,
) -> ConversationEvent:
    values: dict[str, object] = {
        "schema_version": CONVERSATION_EVENT_SCHEMA_VERSION,
        "session_id": requested.session_id,
        "conversation_id": requested.conversation_id,
        "turn_id": requested.turn_id,
        "sequence": 1,
        "timestamp": _TIMESTAMP,
        "sender": requested.sender,
        "recipient": requested.recipient,
        "kind": requested.kind,
        "content": requested.content,
    }
    values.update(overrides)
    return ConversationEvent(**values)  # type: ignore[arg-type]


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


def _changed_paths(
    before: dict[str, bytes | None],
    after: dict[str, bytes | None],
) -> set[str]:
    return {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }


def _transcript_path(session_id: str, conversation_id: str) -> str:
    return (
        f"discussion_logs/{session_id}/interactive/conversations/"
        f"{conversation_id}/transcript.jsonl"
    )


ConversationHandler = Callable[
    [ConversationRequest],
    ConversationReply | BaseException | Awaitable[ConversationReply],
]
AppendHook = Callable[[ConversationEventAppend], object]


class _ConversationBoundAgent:
    def __init__(
        self,
        backend: _ConversationBackend,
        definition: AgentDefinition,
    ) -> None:
        self.backend = backend
        self.definition = definition

    async def invoke(self, request: object) -> object:
        raise AssertionError("debate invocation is outside this test boundary")

    async def converse(self, request: ConversationRequest) -> ConversationReply:
        if request.agent_id != self.definition.agent_id:
            raise AssertionError("conversation used the wrong bound agent")
        self.backend.trace.append("converse")
        self.backend.requests.append(request)
        result = self.backend.handler(request)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        return result


class _ConversationBackend:
    def __init__(
        self,
        handler: ConversationHandler | None = None,
        *,
        trace: list[str] | None = None,
        configuration_error: BaseException | None = None,
        bind_error: BaseException | None = None,
        bind_hook: Callable[[AgentDefinition], None] | None = None,
        bound_factory: Callable[
            [_ConversationBackend, AgentDefinition], object
        ] | None = None,
    ) -> None:
        self.handler = handler or self._default_handler
        self.trace = trace if trace is not None else []
        self.configuration_error = configuration_error
        self.bind_error = bind_error
        self.bind_hook = bind_hook
        self.bound_factory = bound_factory or _ConversationBoundAgent
        self.lifecycle_sessions: list[str] = []
        self.access_held: set[str] = set()
        self.definitions: list[AgentDefinition] = []
        self.requests: list[ConversationRequest] = []

    @staticmethod
    def _default_handler(request: ConversationRequest) -> ConversationReply:
        return ConversationReply(
            request.session_id,
            request.conversation_id,
            request.turn_id,
            f"reply to {request.content}",
        )

    def validate_provider_configuration(self, session_id: str) -> bool:
        self.trace.append("validate_provider_configuration")
        self.lifecycle_sessions.append(session_id)
        if self.configuration_error is not None:
            raise self.configuration_error
        return True

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("get_session_lifecycle")
        return self._lifecycle(session_id)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("acquire_session_access")
        self.access_held.add(session_id)
        return self._lifecycle(session_id)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("release_session_access")
        self.access_held.discard(session_id)
        return self._lifecycle(session_id)

    def _lifecycle(self, session_id: str) -> SessionLifecycleState:
        held = session_id in self.access_held
        return SessionLifecycleState(
            session_id=session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE
                if held
                else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="conversation-test" if held else None,
            owner_pid=1 if held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    async def bind(self, definition: AgentDefinition) -> object:
        self.trace.append("bind")
        self.definitions.append(definition)
        if self.bind_hook is not None:
            self.bind_hook(definition)
        if self.bind_error is not None:
            raise self.bind_error
        return self.bound_factory(self, definition)


class _TracingStore:
    def __init__(
        self,
        delegate: Any,
        *,
        trace: list[str] | None = None,
        read_result: object = _DELEGATE,
        append_hook: AppendHook | None = None,
    ) -> None:
        self.delegate = delegate
        self.trace = trace if trace is not None else []
        self.read_result = read_result
        self.append_hook = append_hook
        self.read_calls: list[tuple[str, str]] = []
        self.append_calls: list[ConversationEventAppend] = []

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        self.trace.append("read_context")
        self.read_calls.append((session_id, conversation_id))
        if isinstance(self.read_result, BaseException):
            raise self.read_result
        if self.read_result is not _DELEGATE:
            return self.read_result  # type: ignore[return-value]
        return self.delegate.read_context(session_id, conversation_id)

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        self.trace.append(f"append:{event.kind.value}")
        self.append_calls.append(event)
        if self.append_hook is not None:
            result = self.append_hook(event)
            if isinstance(result, BaseException):
                raise result
            if result is not _DELEGATE:
                return result  # type: ignore[return-value]
        return self.delegate.append(event)

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        return self.delegate.read(session_id, conversation_id)


class _MissingConverseBound:
    async def invoke(self, request: object) -> object:
        raise AssertionError


class _SynchronousConverseBound:
    async def invoke(self, request: object) -> object:
        raise AssertionError

    def converse(self, request: ConversationRequest) -> ConversationReply:
        return ConversationReply(
            request.session_id,
            request.conversation_id,
            request.turn_id,
            "unexpected",
        )


class _UnprintableError(RuntimeError):
    def __str__(self) -> str:
        raise RuntimeError("stringification failed")


class _ConversationRuntimeCase(unittest.IsolatedAsyncioTestCase):
    def memory_fixture(
        self,
        *,
        session_id: str = "session_a",
        subject: str = "Runtime conversation",
    ) -> tuple[
        Workspace,
        StaticConversationCatalog,
        InMemoryConversationEventStore,
        str,
    ]:
        workspace = initialized_workspace(self)
        catalog = StaticConversationCatalog()
        metadata = catalog.create(session_id, subject)
        store = InMemoryConversationEventStore(catalog)
        return workspace, catalog, store, metadata.conversation_id

    async def assert_runtime_failure(
        self,
        *,
        workspace: Workspace,
        backend: object,
        store: object,
        session_id: object = "session_a",
        conversation_id: object = "conversation_1",
        agent_id: object = PLOT_ARCHITECT,
        text: object = "question",
        error: type[BaseException] = Exception,
    ) -> BaseException:
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,  # type: ignore[arg-type]
        )
        with self.assertRaises(error) as raised:
            await runtime.converse(
                session_id,  # type: ignore[arg-type]
                conversation_id,  # type: ignore[arg-type]
                agent_id,  # type: ignore[arg-type]
                text,  # type: ignore[arg-type]
            )
        return raised.exception


class ConversationResultContractTests(unittest.TestCase):
    def test_exact_frozen_slotted_no_default_result(self) -> None:
        result = ConversationResult(
            "session_a",
            "conversation_a",
            "turn_a",
            PLOT_ARCHITECT,
            "exact reply",
            _empty_context(),
        )

        result_fields = fields(ConversationResult)
        self.assertEqual(
            tuple(field.name for field in result_fields),
            (
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "content",
                "context",
            ),
        )
        self.assertTrue(
            all(
                field.default is MISSING and field.default_factory is MISSING
                for field in result_fields
            )
        )
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.content = "changed"  # type: ignore[misc]

    def test_result_revalidates_identifiers_agent_content_and_context(self) -> None:
        values: dict[str, object] = {
            "session_id": "session_a",
            "conversation_id": "conversation_a",
            "turn_id": "turn_a",
            "agent_id": PLOT_ARCHITECT,
            "content": "reply",
            "context": _empty_context(),
        }
        for field_name in ("session_id", "conversation_id", "turn_id", "agent_id"):
            changed = dict(values, **{field_name: "../unsafe"})
            with self.subTest(field=field_name):
                with self.assertRaises(SessionIdError):
                    ConversationResult(**changed)  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            ConversationResult(  # type: ignore[arg-type]
                **dict(values, agent_id="unknown_agent")
            )
        with self.assertRaises(TypeError):
            ConversationResult(**dict(values, content=None))  # type: ignore[arg-type]
        for content in ("", "invalid-\ud800"):
            with self.subTest(content=repr(content)):
                with self.assertRaises(ValueError):
                    ConversationResult(  # type: ignore[arg-type]
                        **dict(values, content=content)
                    )
        with self.assertRaises(TypeError):
            ConversationResult(**dict(values, context={}))  # type: ignore[arg-type]

        wrong_context = ConversationContextWindow(
            events=(
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "other_session",
                    "conversation_a",
                    "prior_turn",
                    1,
                    _TIMESTAMP,
                    "user",
                    PLOT_ARCHITECT,
                    ConversationEventKind.USER_MESSAGE,
                    "prior question",
                ),
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    "other_session",
                    "conversation_a",
                    "prior_turn",
                    2,
                    _TIMESTAMP,
                    PLOT_ARCHITECT,
                    "user",
                    ConversationEventKind.AGENT_REPLY,
                    "prior reply",
                ),
            ),
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=len("prior questionprior reply"),
            truncated=False,
        )
        with self.assertRaisesRegex(ValueError, "different session"):
            ConversationResult(  # type: ignore[arg-type]
                **dict(values, context=wrong_context)
            )


class ConversationRuntimeSuccessTests(_ConversationRuntimeCase):
    async def test_success_has_exact_order_result_context_and_injected_store(
        self,
    ) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        prior = _append_turn(
            memory,
            "session_a",
            conversation_id,
            "prior_turn",
            user_content="prior visible question",
            outcome_content="prior visible reply",
        )
        trace: list[str] = []
        store = _TracingStore(memory, trace=trace)
        backend = _ConversationBackend(trace=trace)
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,
        )
        text = "  current\n\tquestion\x00 한🙂  "
        fixed_uuid = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

        with mock.patch(
            "story_writing_agents.orchestrator.uuid.uuid4",
            return_value=fixed_uuid,
        ):
            result = await runtime.converse(
                "session_a",
                conversation_id,
                CHARACTER_CUSTODIAN,
                text,
            )

        self.assertEqual(
            trace,
            [
                "read_context",
                "validate_provider_configuration",
                "get_session_lifecycle",
                "acquire_session_access",
                "get_session_lifecycle",
                "bind",
                "append:user_message",
                "converse",
                "append:agent_reply",
            ],
        )
        self.assertIs(runtime.conversation_event_store, store)
        self.assertEqual(len(backend.requests), 1)
        request = backend.requests[0]
        self.assertEqual(request.context.events, prior)
        self.assertEqual(request.content, text)
        self.assertEqual(request.agent_id, CHARACTER_CUSTODIAN)
        self.assertEqual(result.context, request.context)
        self.assertEqual(result.session_id, "session_a")
        self.assertEqual(result.conversation_id, conversation_id)
        self.assertEqual(result.turn_id, "t_00112233445566778899aabbccddeeff")
        self.assertEqual(result.agent_id, CHARACTER_CUSTODIAN)
        self.assertEqual(result.content, f"reply to {text}")

        events = memory.read("session_a", conversation_id)
        self.assertEqual(events[:2], prior)
        user, reply = events[-2:]
        self.assertEqual((user.sequence, reply.sequence), (3, 4))
        self.assertEqual(user.turn_id, reply.turn_id)
        self.assertEqual(user.turn_id, result.turn_id)
        self.assertEqual(
            (user.sender, user.recipient, user.kind, user.content),
            (
                "user",
                CHARACTER_CUSTODIAN,
                ConversationEventKind.USER_MESSAGE,
                text,
            ),
        )
        self.assertEqual(
            (reply.sender, reply.recipient, reply.kind, reply.content),
            (
                CHARACTER_CUSTODIAN,
                "user",
                ConversationEventKind.AGENT_REPLY,
                result.content,
            ),
        )
        self.assertFalse(
            workspace.path("discussion_logs/session_a/interactive").exists()
        )

    async def test_default_filesystem_store_and_runtime_session_rehydrate(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        metadata = catalog.create("session_a", "Filesystem runtime")
        first_backend = _ConversationBackend()
        first_runtime = StoryRuntime(workspace, first_backend)  # type: ignore[arg-type]

        first = await first_runtime.converse(
            "session_a",
            metadata.conversation_id,
            PLOT_ARCHITECT,
            "first question",
        )
        self.assertIsInstance(
            first_runtime.conversation_event_store,
            FilesystemConversationEventStore,
        )
        self.assertRegex(first.turn_id, r"^t_[0-9a-f]{32}$")

        second_backend = _ConversationBackend()
        second_runtime = StoryRuntime(
            workspace,
            second_backend,  # type: ignore[arg-type]
        )
        bound = second_runtime.session("session_a")
        self.assertIsInstance(bound, RuntimeSession)
        second = await bound.converse(
            metadata.conversation_id,
            CHARACTER_CUSTODIAN,
            "second question",
        )

        self.assertRegex(second.turn_id, r"^t_[0-9a-f]{32}$")
        self.assertNotEqual(first.turn_id, second.turn_id)
        self.assertEqual(len(second_backend.requests), 1)
        rehydrated = second_backend.requests[0].context
        transcript = FilesystemConversationEventStore(workspace).read(
            "session_a",
            metadata.conversation_id,
        )
        self.assertEqual(rehydrated.events, transcript[:2])
        self.assertEqual(rehydrated.retained_turns, 1)
        self.assertEqual(
            tuple(event.kind for event in transcript),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )

    async def test_context_is_conversation_scoped_cross_agent_and_least_privilege(
        self,
    ) -> None:
        workspace, catalog, store, conversation_a = self.memory_fixture()
        conversation_b = catalog.create("session_a", "Other subject").conversation_id
        conversation_c = catalog.create("session_b", "Other session").conversation_id
        visible_a = _append_turn(
            store,
            "session_a",
            conversation_a,
            "visible_a",
            agent_id=PLOT_ARCHITECT,
            user_content="VISIBLE_A_USER",
            outcome_content="VISIBLE_A_REPLY",
        )
        _append_turn(
            store,
            "session_a",
            conversation_b,
            "private_b",
            user_content="OTHER_CONVERSATION_SECRET",
            outcome_content="OTHER_CONVERSATION_REPLY",
        )
        _append_turn(
            store,
            "session_b",
            conversation_c,
            "private_session",
            user_content="OTHER_SESSION_SECRET",
            outcome_content="OTHER_SESSION_REPLY",
        )
        workspace.write_text(
            "agent_states/plot_architect_state/private-test.txt",
            "OWNER_PRIVATE_SECRET",
        )
        backend = _ConversationBackend()
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,
        )

        await runtime.converse(
            "session_a",
            conversation_a,
            CHARACTER_CUSTODIAN,
            "cross-agent question",
        )

        request = backend.requests[0]
        self.assertEqual(request.context.events, visible_a)
        request_text = repr(request)
        for secret in (
            "OTHER_CONVERSATION_SECRET",
            "OTHER_SESSION_SECRET",
            "OWNER_PRIVATE_SECRET",
        ):
            self.assertNotIn(secret, request_text)
        definition = backend.definitions[0]
        self.assertEqual(definition.agent_id, CHARACTER_CUSTODIAN)
        relative_contexts = tuple(
            reference.path.relative_to(workspace.root).name
            for reference in definition.context_document_files
        )
        self.assertEqual(
            relative_contexts,
            AGENT_SPECS[CHARACTER_CUSTODIAN].context_documents,
        )
        self.assertTrue(set(relative_contexts).issubset(CORE_DOCUMENTS))
        self.assertTrue(
            all(
                "agent_states" not in reference.path.parts
                for reference in definition.file_references()
            )
        )

    async def test_archived_conversation_remains_explicitly_addressable(self) -> None:
        workspace = initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        metadata = catalog.create("session_a", "Archived subject")
        archived = catalog.archive("session_a", metadata.conversation_id)
        backend = _ConversationBackend()

        result = await StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
        ).converse(
            "session_a",
            metadata.conversation_id,
            PLOT_ARCHITECT,
            "address archived subject",
        )

        self.assertEqual(result.conversation_id, metadata.conversation_id)
        self.assertEqual(
            catalog.get("session_a", metadata.conversation_id),
            archived,
        )
        self.assertEqual(
            len(
                FilesystemConversationEventStore(workspace).read(
                    "session_a",
                    metadata.conversation_id,
                )
            ),
            2,
        )


class ConversationRuntimeConcurrencyTests(_ConversationRuntimeCase):
    async def test_same_conversation_serializes_before_id_and_context(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        trace: list[str] = []
        store = _TracingStore(memory, trace=trace)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def respond(request: ConversationRequest) -> ConversationReply:
            if request.content == "first":
                first_entered.set()
                await release_first.wait()
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                f"reply-{request.content}",
            )

        backend = _ConversationBackend(respond, trace=trace)
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,
        )
        uuid_values = (
            uuid.UUID("00000000-0000-0000-0000-000000000001"),
            uuid.UUID("00000000-0000-0000-0000-000000000002"),
        )
        second_started = asyncio.Event()

        async def second_call() -> ConversationResult:
            second_started.set()
            return await runtime.converse(
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                "second",
            )

        with mock.patch(
            "story_writing_agents.orchestrator.uuid.uuid4",
            side_effect=uuid_values,
        ) as generated:
            first_task = asyncio.create_task(
                runtime.converse(
                    "session_a",
                    conversation_id,
                    PLOT_ARCHITECT,
                    "first",
                )
            )
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            second_task = asyncio.create_task(second_call())
            await asyncio.wait_for(second_started.wait(), timeout=1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            self.assertEqual(generated.call_count, 1)
            self.assertEqual(len(store.read_calls), 1)
            self.assertEqual(len(backend.requests), 1)
            self.assertEqual(
                tuple(
                    event.kind
                    for event in memory.read("session_a", conversation_id)
                ),
                (ConversationEventKind.USER_MESSAGE,),
            )

            release_first.set()
            first, second = await asyncio.wait_for(
                asyncio.gather(first_task, second_task),
                timeout=1,
            )

        self.assertEqual(generated.call_count, 2)
        self.assertEqual(
            (first.turn_id, second.turn_id),
            (
                "t_00000000000000000000000000000001",
                "t_00000000000000000000000000000002",
            ),
        )
        events = memory.read("session_a", conversation_id)
        self.assertEqual(
            tuple((event.kind, event.content) for event in events),
            (
                (ConversationEventKind.USER_MESSAGE, "first"),
                (ConversationEventKind.AGENT_REPLY, "reply-first"),
                (ConversationEventKind.USER_MESSAGE, "second"),
                (ConversationEventKind.AGENT_REPLY, "reply-second"),
            ),
        )
        self.assertEqual(backend.requests[0].context.events, ())
        self.assertEqual(backend.requests[1].context.events, events[:2])

    async def test_different_conversations_reach_provider_concurrently(self) -> None:
        workspace, catalog, memory, first_id = self.memory_fixture()
        second_id = catalog.create("session_a", "Second subject").conversation_id
        arrivals: list[str] = []
        both_arrived = asyncio.Event()
        release = asyncio.Event()

        async def respond(request: ConversationRequest) -> ConversationReply:
            arrivals.append(request.conversation_id)
            if len(arrivals) == 2:
                both_arrived.set()
            await release.wait()
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                f"reply-{request.conversation_id}",
            )

        backend = _ConversationBackend(respond)
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=memory,
        )
        tasks = tuple(
            asyncio.create_task(
                runtime.converse(
                    "session_a",
                    conversation_id,
                    PLOT_ARCHITECT,
                    f"question-{conversation_id}",
                )
            )
            for conversation_id in (first_id, second_id)
        )

        await asyncio.wait_for(both_arrived.wait(), timeout=1)
        self.assertCountEqual(arrivals, (first_id, second_id))
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

        self.assertCountEqual(
            (result.conversation_id for result in results),
            (first_id, second_id),
        )
        for conversation_id in (first_id, second_id):
            events = memory.read("session_a", conversation_id)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                {event.conversation_id for event in events},
                {conversation_id},
            )
            matching_request = next(
                request
                for request in backend.requests
                if request.conversation_id == conversation_id
            )
            self.assertEqual(matching_request.context.events, ())


class ConversationRuntimePreflightTests(_ConversationRuntimeCase):
    async def test_invalid_inputs_and_missing_backend_stop_before_store(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        cases = (
            (None, conversation_id, PLOT_ARCHITECT, "question", SessionIdError),
            ("../unsafe", conversation_id, PLOT_ARCHITECT, "question", SessionIdError),
            ("session_a", "../unsafe", PLOT_ARCHITECT, "question", SessionIdError),
            ("session_a", conversation_id, None, "question", SessionIdError),
            ("session_a", conversation_id, "unknown_agent", "question", ValueError),
            ("session_a", conversation_id, PLOT_ARCHITECT, None, TypeError),
            ("session_a", conversation_id, PLOT_ARCHITECT, "", ValueError),
            (
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                "invalid-\ud800",
                ValueError,
            ),
        )
        for session_id, target, agent_id, text, error in cases:
            trace: list[str] = []
            store = _TracingStore(memory, trace=trace)
            backend = _ConversationBackend(trace=trace)
            with self.subTest(
                session_id=session_id,
                conversation_id=target,
                agent_id=agent_id,
                text=repr(text),
            ):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=backend,
                    store=store,
                    session_id=session_id,
                    conversation_id=target,
                    agent_id=agent_id,
                    text=text,
                    error=error,
                )
                self.assertEqual(trace, [])

        trace = []
        store = _TracingStore(memory, trace=trace)
        await self.assert_runtime_failure(
            workspace=workspace,
            backend=None,
            store=store,
            conversation_id=conversation_id,
            error=BackendProtocolError,
        )
        self.assertEqual(trace, [])

    async def test_store_surface_is_validated_before_any_store_operation(self) -> None:
        workspace = initialized_workspace(self)
        backend = _ConversationBackend()

        class MissingRead:
            def append(self, event: ConversationEventAppend) -> ConversationEvent:
                raise AssertionError("append must not run")

        class MissingAppend:
            def read_context(
                self,
                session_id: str,
                conversation_id: str,
            ) -> ConversationContextWindow:
                raise AssertionError("read must not run")

        class NonCallableRead:
            read_context = 1

            def append(self, event: ConversationEventAppend) -> ConversationEvent:
                raise AssertionError("append must not run")

        class AsyncRead:
            async def read_context(
                self,
                session_id: str,
                conversation_id: str,
            ) -> ConversationContextWindow:
                return _empty_context()

            def append(self, event: ConversationEventAppend) -> ConversationEvent:
                raise AssertionError("append must not run")

        class AsyncAppend:
            def read_context(
                self,
                session_id: str,
                conversation_id: str,
            ) -> ConversationContextWindow:
                raise AssertionError("read must not run")

            async def append(
                self,
                event: ConversationEventAppend,
            ) -> ConversationEvent:
                return _stored_event(event)

        for store in (
            MissingRead(),
            MissingAppend(),
            NonCallableRead(),
            AsyncRead(),
            AsyncAppend(),
        ):
            with self.subTest(store=type(store).__name__):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=backend,
                    store=store,
                    error=BackendProtocolError,
                )
        self.assertEqual(backend.lifecycle_sessions, [])
        self.assertEqual(backend.requests, [])

    async def test_read_context_errors_and_invalid_values_are_pre_user_failures(
        self,
    ) -> None:
        workspace, catalog, memory, conversation_id = self.memory_fixture()
        storage_error = ConversationNotFoundError("missing conversation")
        raising_store = _TracingStore(memory, read_result=storage_error)
        backend = _ConversationBackend()
        caught = await self.assert_runtime_failure(
            workspace=workspace,
            backend=backend,
            store=raising_store,
            conversation_id=conversation_id,
            error=ConversationNotFoundError,
        )
        self.assertIs(caught, storage_error)
        self.assertEqual(raising_store.append_calls, [])
        self.assertEqual(backend.lifecycle_sessions, [])

        wrong_session_id = "session_b"
        wrong_conversation_id = catalog.create(
            wrong_session_id,
            "Wrong scope",
        ).conversation_id
        _append_turn(
            memory,
            wrong_session_id,
            wrong_conversation_id,
            "wrong_scope_turn",
        )
        wrong_scope = memory.read_context(
            wrong_session_id,
            wrong_conversation_id,
        )

        class DerivedContext(ConversationContextWindow):
            pass

        derived = DerivedContext(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )
        corrupt = _empty_context()
        object.__setattr__(corrupt, "retained_turns", 1)
        for value in (object(), derived, wrong_scope, corrupt):
            store = _TracingStore(memory, read_result=value)
            candidate_backend = _ConversationBackend()
            with self.subTest(value=type(value).__name__):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=candidate_backend,
                    store=store,
                    conversation_id=conversation_id,
                    error=BackendProtocolError,
                )
                self.assertEqual(store.append_calls, [])
                self.assertEqual(candidate_backend.lifecycle_sessions, [])

        class AwaitableReadStore(_TracingStore):
            def read_context(self, session_id: str, conversation_id: str) -> Any:
                async def later() -> ConversationContextWindow:
                    return _empty_context()

                return later()

        awaitable_store = AwaitableReadStore(memory)
        await self.assert_runtime_failure(
            workspace=workspace,
            backend=_ConversationBackend(),
            store=awaitable_store,
            conversation_id=conversation_id,
            error=BackendProtocolError,
        )

    async def test_session_and_binding_preflight_fail_before_user_append(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        preflight_error = ConfigurationError("configuration unavailable")
        store = _TracingStore(memory)
        backend = _ConversationBackend(configuration_error=preflight_error)
        caught = await self.assert_runtime_failure(
            workspace=workspace,
            backend=backend,
            store=store,
            conversation_id=conversation_id,
            error=ConfigurationError,
        )
        self.assertIs(caught, preflight_error)
        self.assertEqual(store.append_calls, [])
        self.assertEqual(backend.requests, [])

        class AsyncLifecycleBackend(_ConversationBackend):
            async def validate_provider_configuration(
                self,
                session_id: str,
            ) -> bool:
                raise AssertionError("async lifecycle hook must not be called")

        for malformed_backend in (
            AsyncLifecycleBackend(),
            _ConversationBackend(),
        ):
            if type(malformed_backend) is _ConversationBackend:
                malformed_backend.acquire_session_access = 1  # type: ignore[method-assign]
            malformed_store = _TracingStore(memory)
            with self.subTest(backend=type(malformed_backend).__name__):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=malformed_backend,
                    store=malformed_store,
                    conversation_id=conversation_id,
                    error=BackendProtocolError,
                )
                self.assertEqual(malformed_store.append_calls, [])
                self.assertEqual(malformed_backend.requests, [])

        bind_error = ConfigurationError("binding configuration failed")
        binding_backend = _ConversationBackend(bind_error=bind_error)
        binding_store = _TracingStore(memory)
        caught = await self.assert_runtime_failure(
            workspace=workspace,
            backend=binding_backend,
            store=binding_store,
            conversation_id=conversation_id,
            error=ConfigurationError,
        )
        self.assertIs(caught, bind_error)
        self.assertEqual(binding_store.append_calls, [])

        for factory in (
            lambda backend, definition: _MissingConverseBound(),
            lambda backend, definition: _SynchronousConverseBound(),
        ):
            malformed_store = _TracingStore(memory)
            malformed_backend = _ConversationBackend(bound_factory=factory)
            with self.subTest(bound=type(factory).__name__):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=malformed_backend,
                    store=malformed_store,
                    conversation_id=conversation_id,
                    error=BackendProtocolError,
                )
                self.assertEqual(malformed_store.append_calls, [])

    async def test_async_generator_lifecycle_result_is_sync_preflight_failure(
        self,
    ) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        backend = _ConversationBackend()
        store = _TracingStore(memory)
        returned_generators = []

        async def deferred_validation():
            yield None

        def validate_provider_configuration(_session_id: str) -> object:
            result = deferred_validation()
            returned_generators.append(result)
            return result

        backend.validate_provider_configuration = (  # type: ignore[method-assign]
            validate_provider_configuration
        )
        caught = await self.assert_runtime_failure(
            workspace=workspace,
            backend=backend,
            store=store,
            conversation_id=conversation_id,
            error=BackendProtocolError,
        )

        self.assertEqual(
            str(caught),
            "backend validate_provider_configuration() must be synchronous",
        )
        self.assertEqual(
            store.read_calls,
            [("session_a", conversation_id)],
        )
        self.assertEqual(store.append_calls, [])
        self.assertEqual(memory.read("session_a", conversation_id), ())
        self.assertEqual(backend.definitions, [])
        self.assertEqual(backend.requests, [])
        self.assertEqual(len(returned_generators), 1)
        await returned_generators[0].aclose()

    async def test_definition_is_reverified_for_new_and_cached_bindings(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        prompt_path = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file

        def tamper_during_bind(definition: AgentDefinition) -> None:
            workspace.write_text(prompt_path, "tampered during bind\n")

        store = _TracingStore(memory)
        backend = _ConversationBackend(bind_hook=tamper_during_bind)
        await self.assert_runtime_failure(
            workspace=workspace,
            backend=backend,
            store=store,
            conversation_id=conversation_id,
            error=WorkspaceFormatError,
        )
        self.assertEqual(store.append_calls, [])
        self.assertEqual(backend.requests, [])

        workspace = initialized_workspace(self)
        catalog = StaticConversationCatalog()
        conversation_id = catalog.create("session_a", "Cached binding").conversation_id
        memory = InMemoryConversationEventStore(catalog)
        store = _TracingStore(memory)
        backend = _ConversationBackend()
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,
        )
        await runtime.converse(
            "session_a",
            conversation_id,
            PLOT_ARCHITECT,
            "cache binding",
        )
        original_build = runtime.prompts.build_definition

        def build_then_tamper(*, session_id: str, agent_id: str) -> AgentDefinition:
            definition = original_build(session_id=session_id, agent_id=agent_id)
            workspace.write_text(prompt_path, "tampered after definition\n")
            return definition

        before = memory.read("session_a", conversation_id)
        with mock.patch.object(
            runtime.prompts,
            "build_definition",
            side_effect=build_then_tamper,
        ):
            with self.assertRaises(WorkspaceFormatError):
                await runtime.converse(
                    "session_a",
                    conversation_id,
                    PLOT_ARCHITECT,
                    "must not append",
                )
        self.assertEqual(memory.read("session_a", conversation_id), before)
        self.assertEqual(len(backend.requests), 1)

    async def test_user_append_return_is_validated_before_provider(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()

        storage_error = WorkspaceFormatError("user append failed")
        raising_store = _TracingStore(
            memory,
            append_hook=lambda event: storage_error,
        )
        raising_backend = _ConversationBackend()
        caught = await self.assert_runtime_failure(
            workspace=workspace,
            backend=raising_backend,
            store=raising_store,
            conversation_id=conversation_id,
            error=WorkspaceFormatError,
        )
        self.assertIs(caught, storage_error)
        self.assertEqual(raising_backend.requests, [])
        self.assertEqual(memory.read("session_a", conversation_id), ())

        def wrong_identity(event: ConversationEventAppend) -> ConversationEvent:
            return _stored_event(event, conversation_id="other_conversation")

        def invalid_sequence(event: ConversationEventAppend) -> ConversationEvent:
            stored = _stored_event(event)
            object.__setattr__(stored, "sequence", 0)
            return stored

        class DerivedEvent(ConversationEvent):
            pass

        def derived_event(event: ConversationEventAppend) -> ConversationEvent:
            ordinary = _stored_event(event)
            return DerivedEvent(
                *(getattr(ordinary, field.name) for field in fields(ConversationEvent))
            )

        for hook in (
            lambda event: object(),
            wrong_identity,
            invalid_sequence,
            derived_event,
        ):
            store = _TracingStore(memory, append_hook=hook)
            backend = _ConversationBackend()
            with self.subTest(hook=getattr(hook, "__name__", "object")):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=backend,
                    store=store,
                    conversation_id=conversation_id,
                    error=BackendProtocolError,
                )
                self.assertEqual(backend.requests, [])
                self.assertEqual(memory.read("session_a", conversation_id), ())
                self.assertEqual(
                    tuple(event.kind for event in store.append_calls),
                    (ConversationEventKind.USER_MESSAGE,),
                )


class ConversationRuntimeFailureTests(_ConversationRuntimeCase):
    async def test_provider_exceptions_record_error_and_reraise_original(self) -> None:
        for provider_error in (
            RuntimeError("provider unavailable"),
            ConfigurationError("provider configuration failed"),
            BackendProtocolError("provider protocol failed"),
        ):
            workspace, _, memory, conversation_id = self.memory_fixture()
            backend = _ConversationBackend(lambda request, error=provider_error: error)
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=memory,
            )
            with self.subTest(error=type(provider_error).__name__):
                with self.assertRaises(type(provider_error)) as raised:
                    await runtime.converse(
                        "session_a",
                        conversation_id,
                        PLOT_ARCHITECT,
                        "durable question",
                    )
                self.assertIs(raised.exception, provider_error)
                events = memory.read("session_a", conversation_id)
                self.assertEqual(
                    tuple(event.kind for event in events),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.RUNTIME_ERROR,
                    ),
                )
                self.assertEqual(events[0].turn_id, events[1].turn_id)
                self.assertEqual(
                    events[1].content,
                    f"{type(provider_error).__name__}: {provider_error}",
                )

    async def test_reply_type_validation_and_correlation_record_one_error(self) -> None:
        class DerivedReply(ConversationReply):
            pass

        def derived(request: ConversationRequest) -> ConversationReply:
            return DerivedReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                "reply",
            )

        def tampered(request: ConversationRequest) -> ConversationReply:
            reply = ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                "reply",
            )
            object.__setattr__(reply, "content", "")
            return reply

        factories: tuple[Callable[[ConversationRequest], object], ...] = (
            lambda request: object(),
            derived,
            tampered,
            lambda request: replace(
                _request_reply(request),
                session_id="other_session",
            ),
            lambda request: replace(
                _request_reply(request),
                conversation_id="other_conversation",
            ),
            lambda request: replace(_request_reply(request), turn_id="other_turn"),
        )
        for factory in factories:
            workspace, _, memory, conversation_id = self.memory_fixture()
            backend = _ConversationBackend(factory)  # type: ignore[arg-type]
            with self.subTest(factory=getattr(factory, "__name__", "lambda")):
                await self.assert_runtime_failure(
                    workspace=workspace,
                    backend=backend,
                    store=memory,
                    conversation_id=conversation_id,
                    error=BackendProtocolError,
                )
                events = memory.read("session_a", conversation_id)
                self.assertEqual(
                    tuple(event.kind for event in events),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.RUNTIME_ERROR,
                    ),
                )
                self.assertEqual(len(backend.requests), 1)

    async def test_result_is_constructed_before_reply_persistence(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        store = _TracingStore(memory)
        backend = _ConversationBackend()
        result_error = RuntimeError("pure result construction failed")
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=store,
        )

        with mock.patch(
            "story_writing_agents.orchestrator.ConversationResult",
            side_effect=result_error,
        ):
            with self.assertRaises(RuntimeError) as raised:
                await runtime.converse(
                    "session_a",
                    conversation_id,
                    PLOT_ARCHITECT,
                    "question",
                )

        self.assertIs(raised.exception, result_error)
        self.assertEqual(
            tuple(event.kind for event in store.append_calls),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertEqual(
            tuple(event.kind for event in memory.read("session_a", conversation_id)),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )

    async def test_error_persistence_failure_wins_and_chains_provider_error(
        self,
    ) -> None:
        for invalid_return in (False, True):
            workspace, _, memory, conversation_id = self.memory_fixture()
            provider_error = RuntimeError("provider failed")
            storage_error = WorkspaceFormatError("error append failed")

            def fail_error(event: ConversationEventAppend) -> object:
                if event.kind is ConversationEventKind.RUNTIME_ERROR:
                    return object() if invalid_return else storage_error
                return _DELEGATE

            store = _TracingStore(memory, append_hook=fail_error)
            backend = _ConversationBackend(lambda request: provider_error)
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=store,
            )
            expected = BackendProtocolError if invalid_return else WorkspaceFormatError
            with self.subTest(invalid_return=invalid_return):
                with self.assertRaises(expected) as raised:
                    await runtime.converse(
                        "session_a",
                        conversation_id,
                        PLOT_ARCHITECT,
                        "question",
                    )
                if not invalid_return:
                    self.assertIs(raised.exception, storage_error)
                self.assertIs(raised.exception.__cause__, provider_error)
                self.assertEqual(
                    tuple(event.kind for event in store.append_calls),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.RUNTIME_ERROR,
                    ),
                )
                self.assertEqual(
                    tuple(
                        event.kind
                        for event in memory.read("session_a", conversation_id)
                    ),
                    (ConversationEventKind.USER_MESSAGE,),
                )

    async def test_reply_persistence_failure_has_no_false_runtime_error(self) -> None:
        for invalid_return in (False, True):
            workspace, _, memory, conversation_id = self.memory_fixture()
            storage_error = WorkspaceFormatError("reply append failed")

            def fail_reply(event: ConversationEventAppend) -> object:
                if event.kind is ConversationEventKind.AGENT_REPLY:
                    return object() if invalid_return else storage_error
                return _DELEGATE

            store = _TracingStore(memory, append_hook=fail_reply)
            backend = _ConversationBackend()
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=store,
            )
            expected = BackendProtocolError if invalid_return else WorkspaceFormatError
            with self.subTest(invalid_return=invalid_return):
                with self.assertRaises(expected) as raised:
                    await runtime.converse(
                        "session_a",
                        conversation_id,
                        PLOT_ARCHITECT,
                        "question",
                    )
                if not invalid_return:
                    self.assertIs(raised.exception, storage_error)
                self.assertIsNone(raised.exception.__cause__)
                self.assertEqual(
                    tuple(event.kind for event in store.append_calls),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.AGENT_REPLY,
                    ),
                )
                self.assertEqual(
                    tuple(
                        event.kind
                        for event in memory.read("session_a", conversation_id)
                    ),
                    (ConversationEventKind.USER_MESSAGE,),
                )

    async def test_diagnostic_redacts_normalized_json_and_workspace_variants(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = root / "actual-workspace"
        alias = root / "workspace-alias"
        workspace = Workspace(target).initialize()
        alias.symlink_to(target, target_is_directory=True)
        catalog = StaticConversationCatalog()
        conversation_id = catalog.create("session_a", "Sanitizer").conversation_id
        memory = InMemoryConversationEventStore(catalog)
        context_user = 'context\u200bvalue "quoted" 한'
        context_reply = 'reply\u202evalue "quoted" 한'
        _append_turn(
            memory,
            "session_a",
            conversation_id,
            "prior_turn",
            user_content=context_user,
            outcome_content=context_reply,
        )
        current = 'secret\u200bvalue "quoted" 한'

        def normalized(value: str) -> str:
            replaced = "".join(
                " "
                if unicodedata.category(character) in {"Cc", "Cf"}
                else character
                for character in value
            )
            return " ".join(replaced.split())

        sensitive_variants: set[str] = set()
        for value in (current, context_user, context_reply):
            sensitive_variants.update(
                {
                    value,
                    normalized(value),
                    json.dumps(value, ensure_ascii=False)[1:-1],
                    json.dumps(value, ensure_ascii=True)[1:-1],
                }
            )
        detail = " | ".join(
            (
                *sorted(sensitive_variants),
                str(alias),
                str(target.resolve()),
                "control\x00format\u200bmarker\u202e",
                "X" * 6_000,
            )
        )
        provider_error = RuntimeError(detail)
        backend = _ConversationBackend(lambda request: provider_error)
        runtime = StoryRuntime(
            str(alias),
            backend,  # type: ignore[arg-type]
            conversation_event_store=memory,
        )

        with self.assertRaises(RuntimeError) as raised:
            await runtime.converse(
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                current,
            )

        self.assertIs(raised.exception, provider_error)
        diagnostic = memory.read("session_a", conversation_id)[-1].content
        for sensitive in (*sensitive_variants, str(alias), str(target.resolve())):
            if sensitive:
                self.assertNotIn(sensitive, diagnostic)
        self.assertNotIn("secret value", diagnostic)
        self.assertNotIn("context value", diagnostic)
        self.assertTrue(
            all(
                unicodedata.category(character) not in {"Cc", "Cf"}
                for character in diagnostic
            )
        )
        self.assertEqual(diagnostic, " ".join(diagnostic.split()))
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("<workspace>", diagnostic)
        self.assertEqual(len(diagnostic), 4_096)
        self.assertTrue(diagnostic.endswith(" …[truncated]"))

    async def test_dynamic_exception_name_is_sanitized_as_diagnostic_text(
        self,
    ) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        current = "type\u200bcurrent secret"
        context_user = "type\u202econtext secret"
        context_reply = "type reply secret"
        _append_turn(
            memory,
            "session_a",
            conversation_id,
            "prior_turn",
            user_content=context_user,
            outcome_content=context_reply,
        )
        exception_type = type(
            (
                f"Provider_{current}_{context_user}_{context_reply}"
                "_control\x01format\u2060"
            ),
            (RuntimeError,),
            {},
        )
        provider_error = exception_type("visible diagnostic")
        backend = _ConversationBackend(lambda request: provider_error)
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=memory,
        )

        with self.assertRaises(exception_type) as raised:
            await runtime.converse(
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                current,
            )

        self.assertIs(raised.exception, provider_error)
        diagnostic = memory.read("session_a", conversation_id)[-1].content
        for sensitive in (current, context_user, context_reply):
            self.assertNotIn(sensitive, diagnostic)
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("visible diagnostic", diagnostic)
        self.assertTrue(
            all(
                unicodedata.category(character) not in {"Cc", "Cf"}
                for character in diagnostic
            )
        )

    async def test_unprintable_and_non_utf8_diagnostics_use_safe_fallback(self) -> None:
        cases: list[tuple[RuntimeError, str]] = [
            (
                _UnprintableError("ignored"),
                "_UnprintableError: unavailable diagnostic",
            ),
            (
                RuntimeError("bad-\ud800-detail"),
                "RuntimeError: unavailable diagnostic",
            ),
        ]
        try:
            non_utf8_name = type("Provider\ud800Failure", (RuntimeError,), {})
        except (TypeError, ValueError):
            pass
        else:
            cases.append(
                (
                    non_utf8_name("visible detail"),
                    "Exception: unavailable diagnostic",
                )
            )

        for provider_error, expected in cases:
            workspace, _, memory, conversation_id = self.memory_fixture()
            backend = _ConversationBackend(lambda request, error=provider_error: error)
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=memory,
            )
            with self.subTest(error=type(provider_error).__name__):
                try:
                    await runtime.converse(
                        "session_a",
                        conversation_id,
                        PLOT_ARCHITECT,
                        "question",
                    )
                except type(provider_error) as raised:
                    self.assertIs(raised, provider_error)
                else:
                    self.fail("provider exception was not re-raised")
                diagnostic = memory.read("session_a", conversation_id)[-1]
                self.assertEqual(diagnostic.content, expected)


def _request_reply(request: ConversationRequest) -> ConversationReply:
    return ConversationReply(
        request.session_id,
        request.conversation_id,
        request.turn_id,
        "reply",
    )


class ConversationRuntimeCancellationTests(_ConversationRuntimeCase):
    async def test_cancellation_persists_exact_outcome_and_releases_lock(self) -> None:
        workspace, _, memory, conversation_id = self.memory_fixture()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def wait_for_cancellation(
            request: ConversationRequest,
        ) -> ConversationReply:
            entered.set()
            await release.wait()
            raise AssertionError("cancelled provider unexpectedly resumed")

        backend = _ConversationBackend(wait_for_cancellation)
        runtime = StoryRuntime(
            workspace,
            backend,  # type: ignore[arg-type]
            conversation_event_store=memory,
        )
        task = asyncio.create_task(
            runtime.converse(
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                "cancelled question",
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        interrupted = memory.read("session_a", conversation_id)
        self.assertEqual(
            tuple(event.kind for event in interrupted),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertEqual(
            interrupted[-1].content,
            "CancelledError: conversation turn was cancelled",
        )

        backend.handler = _ConversationBackend._default_handler
        completed = await asyncio.wait_for(
            runtime.converse(
                "session_a",
                conversation_id,
                PLOT_ARCHITECT,
                "after cancellation",
            ),
            timeout=1,
        )
        self.assertEqual(completed.context.events, interrupted)
        self.assertEqual(len(memory.read("session_a", conversation_id)), 4)

    async def test_cancellation_persistence_failure_never_replaces_cancellation(
        self,
    ) -> None:
        for outcome in (KeyboardInterrupt("storage interrupted"), object()):
            workspace, _, memory, conversation_id = self.memory_fixture()
            entered = asyncio.Event()
            release = asyncio.Event()

            async def wait_for_cancellation(
                request: ConversationRequest,
            ) -> ConversationReply:
                entered.set()
                await release.wait()
                raise AssertionError

            def fail_cancellation(event: ConversationEventAppend) -> object:
                if event.kind is ConversationEventKind.RUNTIME_ERROR:
                    return outcome
                return _DELEGATE

            store = _TracingStore(memory, append_hook=fail_cancellation)
            backend = _ConversationBackend(wait_for_cancellation)
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=store,
            )
            task = asyncio.create_task(
                runtime.converse(
                    "session_a",
                    conversation_id,
                    PLOT_ARCHITECT,
                    "cancel me",
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with self.subTest(outcome=type(outcome).__name__):
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(
                    tuple(event.kind for event in store.append_calls),
                    (
                        ConversationEventKind.USER_MESSAGE,
                        ConversationEventKind.RUNTIME_ERROR,
                    ),
                )
                self.assertEqual(
                    tuple(
                        event.kind
                        for event in memory.read("session_a", conversation_id)
                    ),
                    (ConversationEventKind.USER_MESSAGE,),
                )

    async def test_base_exceptions_leave_crash_remainder(self) -> None:
        for provider_failure in (
            KeyboardInterrupt("provider interrupted"),
            SystemExit("provider exited"),
        ):
            workspace, _, memory, conversation_id = self.memory_fixture()
            store = _TracingStore(memory)
            backend = _ConversationBackend(
                lambda request, failure=provider_failure: failure
            )
            runtime = StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
                conversation_event_store=store,
            )
            with self.subTest(failure=type(provider_failure).__name__):
                with self.assertRaises(type(provider_failure)) as raised:
                    await runtime.converse(
                        "session_a",
                        conversation_id,
                        PLOT_ARCHITECT,
                        "crash remainder",
                    )
                self.assertIs(raised.exception, provider_failure)
                self.assertEqual(
                    tuple(event.kind for event in store.append_calls),
                    (ConversationEventKind.USER_MESSAGE,),
                )
                self.assertEqual(
                    tuple(
                        event.kind
                        for event in memory.read("session_a", conversation_id)
                    ),
                    (ConversationEventKind.USER_MESSAGE,),
                )


class ConversationRuntimeArtifactTests(_ConversationRuntimeCase):
    async def test_success_and_failure_only_change_addressed_transcript(self) -> None:
        workspace = initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        success_metadata = catalog.create("session_a", "Successful subject")
        success_catalog_before = catalog.get(
            "session_a",
            success_metadata.conversation_id,
        )
        cores_before = core_snapshot(workspace)
        before = _tree_snapshot(workspace.root)

        await StoryRuntime(
            workspace,
            _ConversationBackend(),  # type: ignore[arg-type]
        ).converse(
            "session_a",
            success_metadata.conversation_id,
            PLOT_ARCHITECT,
            "successful question",
        )

        after = _tree_snapshot(workspace.root)
        self.assertEqual(
            _changed_paths(before, after),
            {_transcript_path("session_a", success_metadata.conversation_id)},
        )
        self.assertEqual(
            catalog.get("session_a", success_metadata.conversation_id),
            success_catalog_before,
        )
        self.assertEqual(core_snapshot(workspace), cores_before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertEqual(workspace.inspect_session("session_a")["debates"], [])

        failure_metadata = catalog.create("session_a", "Failed subject")
        failure_catalog_before = catalog.get(
            "session_a",
            failure_metadata.conversation_id,
        )
        provider_error = RuntimeError("provider failed without story artifacts")
        before_failure = _tree_snapshot(workspace.root)
        runtime = StoryRuntime(
            workspace,
            _ConversationBackend(  # type: ignore[arg-type]
                lambda request: provider_error
            ),
        )
        with self.assertRaises(RuntimeError) as raised:
            await runtime.converse(
                "session_a",
                failure_metadata.conversation_id,
                CHARACTER_CUSTODIAN,
                "failed question",
            )
        self.assertIs(raised.exception, provider_error)

        after_failure = _tree_snapshot(workspace.root)
        self.assertEqual(
            _changed_paths(before_failure, after_failure),
            {_transcript_path("session_a", failure_metadata.conversation_id)},
        )
        self.assertEqual(
            catalog.get("session_a", failure_metadata.conversation_id),
            failure_catalog_before,
        )
        self.assertEqual(core_snapshot(workspace), cores_before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertEqual(workspace.inspect_session("session_a")["debates"], [])
        self.assertFalse(
            workspace.path("agent_states/orchestrator_state/cli_context.json").exists()
        )

    async def test_missing_identity_creates_no_workspace_artifact(self) -> None:
        workspace = initialized_workspace(self)
        before = _tree_snapshot(workspace.root)
        backend = _ConversationBackend()

        with self.assertRaises(ConversationNotFoundError):
            await StoryRuntime(
                workspace,
                backend,  # type: ignore[arg-type]
            ).converse(
                "session_a",
                "missing_conversation",
                PLOT_ARCHITECT,
                "question",
            )

        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(backend.lifecycle_sessions, [])
        self.assertEqual(backend.requests, [])


if __name__ == "__main__":
    unittest.main()
