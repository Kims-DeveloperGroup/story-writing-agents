"""Interactive Runner, cancellation, terminal-loss, and cleanup contracts."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import unittest
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest import mock

from story_writing_agents import backend as backend_module
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.agents import (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    SPECIALIST_IDS,
)
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversation_retrieval import (
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalResult,
)
from story_writing_agents.conversations import (
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_terminal import (
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents.models import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    DebateContextMode,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    InputKind,
    Phase,
)
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)

from tests.helpers import all_manuscripts, core_snapshot
from tests.test_codex_backend import _FAKE_CODEX
from tests.test_interactive_conversation import (
    _REAL_RUNTIME,
    InteractiveConversationTestCase,
    ScriptedTerminalCallbacks,
    _conversation_result,
    _error_record,
)


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_ACCESS_RELATIVE = f"{_SESSION_DIRECTORY}/session.access.json"
_MANIFEST_RELATIVE = f"{_SESSION_DIRECTORY}/session.json"


def _file_bytes(workspace: Workspace) -> dict[str, bytes]:
    """Return durable file bytes while excluding transient lease ownership."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file() and not path.name.endswith(".access.json")
    }


class RecordingRunner:
    """Wrap one real Runner and inject deterministic boundary interruptions."""

    def __init__(
        self,
        runner_type: type[asyncio.Runner],
        *,
        interruptions: dict[int, str] | None = None,
        predicates: dict[int, Callable[[], bool]] | None = None,
        cleanup_events: list[str] | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self._delegate = runner_type()
        self.interruptions = interruptions or {}
        self.predicates = predicates or {}
        self.cleanup_events = cleanup_events
        self.close_failure = close_failure
        self.run_calls = 0
        self.loop_ids: list[int] = []
        self.operation_tasks: list[asyncio.Task[Any]] = []
        self.closed = False

    def get_loop(self) -> asyncio.AbstractEventLoop:
        """Expose the real owned loop for controller quiescence checks."""

        return self._delegate.get_loop()

    def run(self, operation: Coroutine[Any, Any, Any]) -> Any:
        """Run, cancel-and-join, or interrupt one configured operation."""

        self.run_calls += 1
        call_number = self.run_calls
        self.loop_ids.append(id(self._delegate.get_loop()))
        behavior = self.interruptions.get(call_number)
        if behavior == "before_entry":
            raise KeyboardInterrupt
        if behavior == "before_entry_nonquiescent":
            async def remain_pending() -> None:
                """Remain visible to public task inspection until cleanup."""

                await asyncio.Event().wait()

            task = self._delegate.get_loop().create_task(
                remain_pending(),
                name="issue14-pre-entry-residual",
            )
            self.operation_tasks.append(task)
            raise KeyboardInterrupt
        if behavior == "bare_cancelled":
            raise asyncio.CancelledError
        if behavior in {"joined", "unjoined"}:
            return self._delegate.run(
                self._drive_interruption(
                    operation,
                    predicate=self.predicates.get(call_number, lambda: True),
                    joined=(behavior == "joined"),
                )
            )
        return self._delegate.run(operation)

    async def _drive_interruption(
        self,
        operation: Coroutine[Any, Any, Any],
        *,
        predicate: Callable[[], bool],
        joined: bool,
    ) -> Any:
        """Cancel after a bounded readiness wait and optionally await joining."""

        task = asyncio.create_task(operation, name="issue14-operation")
        self.operation_tasks.append(task)
        for _ in range(20_000):
            if predicate():
                break
            if task.done():
                return await task
            await asyncio.sleep(0)
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise AssertionError("cancellation readiness was not reached")

        task.cancel()
        if not joined:
            raise KeyboardInterrupt
        try:
            await task
        except asyncio.CancelledError:
            raise KeyboardInterrupt from None
        raise AssertionError("the interrupted operation completed normally")

    def close(self) -> None:
        """Close the real Runner, record order, then inject any configured fault."""

        if self.cleanup_events is not None:
            self.cleanup_events.append("runner.close")
        self._delegate.close()
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


class RunnerFactory:
    """Create and retain exactly one configured recording Runner."""

    def __init__(
        self,
        runner_type: type[asyncio.Runner],
        **runner_keywords: object,
    ) -> None:
        self.runner_type = runner_type
        self.runner_keywords = runner_keywords
        self.instances: list[RecordingRunner] = []

    def __call__(self) -> RecordingRunner:
        """Construct one wrapper at controller-run entry."""

        runner = RecordingRunner(
            self.runner_type,
            **self.runner_keywords,
        )
        self.instances.append(runner)
        return runner


class InterruptingWriteCallbacks(ScriptedTerminalCallbacks):
    """Raise one configured output failure after retaining the attempted row."""

    def __init__(
        self,
        *reads: object,
        output_predicate: Callable[[str], bool],
        failure: BaseException,
        repeat: bool = False,
    ) -> None:
        super().__init__(*reads)
        self.output_predicate = output_predicate
        self.failure = failure
        self.repeat = repeat
        self.failures = 0

    def write_text(self, text: str) -> None:
        """Retain one attempted record, then raise at the selected boundary."""

        self.records.append(text)
        if self.output_predicate(text) and (self.repeat or self.failures == 0):
            self.failures += 1
            raise self.failure


class ResidualAdvisoryRuntime:
    """Return a valid reply while leaving task and async-generator cleanup work."""

    def __init__(self) -> None:
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.pending_task: asyncio.Task[None] | None = None
        self.pending_task_finalized = False
        self.generator_finalized = False
        self.generator: Any = None

    async def converse_with_retrieval(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
        *,
        memory_service: object,
        progress_sink: object = None,
    ) -> ConversationRetrievalResult:
        """Create residual loop resources and return one correlated result."""

        del memory_service, progress_sink
        self.loops.append(asyncio.get_running_loop())

        async def pending() -> None:
            """Remain pending until Runner.close cancels this task."""

            try:
                await asyncio.Event().wait()
            finally:
                self.pending_task_finalized = True

        async def values():
            """Yield once and record Runner-driven async-generator shutdown."""

            try:
                yield "retained"
            finally:
                self.generator_finalized = True

        self.pending_task = asyncio.create_task(
            pending(),
            name="issue14-residual-task",
        )
        self.generator = values()
        await anext(self.generator)
        await asyncio.sleep(0)
        return _conversation_result(
            session_id,
            conversation_id,
            agent_id,
            f"reply:{text}",
        )


class BlockingAdvisoryRuntime:
    """Keep one advisory operation pending and record cancellation joining."""

    def __init__(self) -> None:
        self.entered = False
        self.cancelled = False
        self.joined = False

    async def converse_with_retrieval(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
        *,
        memory_service: object,
        progress_sink: object = None,
    ) -> ConversationRetrievalResult:
        """Wait forever until the recording Runner cancels this operation."""

        del session_id, conversation_id, agent_id, text, memory_service, progress_sink
        self.entered = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.joined = True


class BlockingConsensusRuntime:
    """Emit one selected progress shape, then re-raise external cancellation."""

    def __init__(self, progress_shape: str, debate_id: str) -> None:
        self.progress_shape = progress_shape
        self.debate_id = debate_id
        self.entered = False
        self.cancelled = False
        self.calls = 0

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        conversation_context: object,
        progress_sink: Callable[[DebateProgressEvent], None],
    ) -> DebateResult:
        """Delegate prompt cancellation to the common blocking implementation."""

        del prompt
        return await self._block(
            session_id,
            InputKind.PROMPT,
            conversation_context,
            progress_sink,
        )

    async def run_draft(
        self,
        session_id: str,
        draft: str,
        *,
        conversation_context: object,
        progress_sink: Callable[[DebateProgressEvent], None],
    ) -> DebateResult:
        """Delegate draft cancellation to the common blocking implementation."""

        del draft
        return await self._block(
            session_id,
            InputKind.DRAFT,
            conversation_context,
            progress_sink,
        )

    async def _block(
        self,
        session_id: str,
        input_kind: InputKind,
        conversation_context: object,
        progress_sink: Callable[[DebateProgressEvent], None],
    ) -> DebateResult:
        """Emit optional STARTED and terminal interruption progress around wait."""

        self.calls += 1
        self.entered = True
        if self.progress_shape == "started_no_proof":
            progress_sink(
                DebateProgressEvent(
                    session_id=session_id,
                    debate_id=self.debate_id,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.STARTED,
                    context_mode=conversation_context.mode,
                )
            )
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if self.progress_shape == "interrupted_first":
                progress_sink(
                    DebateProgressEvent(
                        session_id=session_id,
                        debate_id=self.debate_id,
                        input_kind=input_kind,
                        phase=DebateProgressPhase.INTERRUPTED,
                    )
                )
            raise
        raise AssertionError("blocking consensus unexpectedly resumed")


class ConversationBound:
    """Provide a direct answer or one cancellable real-runtime advisory call."""

    def __init__(self, definition: AgentDefinition, state: dict[str, object]) -> None:
        self.definition = definition
        self.state = state

    async def invoke(self, request: AgentRequest) -> AgentReply:
        """Reject debate use outside this fixture's advisory boundary."""

        del request
        raise AssertionError("consensus invocation is outside this fixture")

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Block the selected turn or resolve one direct correlated answer."""

        requests = self.state["requests"]
        assert isinstance(requests, list)
        requests.append(request)
        loops = self.state.get("loops")
        if loops is not None:
            assert isinstance(loops, list)
            loops.append(asyncio.get_running_loop())
        if self.state["block"]:
            self.state["entered"] = True
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.state["cancelled"] = True
                raise
            finally:
                self.state["joined"] = True
        result = handler.handle_conversation_retrieval_action(
            ConversationRetrievalAnswerAction(
                kind=ConversationRetrievalActionKind.ANSWER,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                action_index=1,
                content=f"reply:{request.content}",
                citation_ids=(),
            )
        )
        if type(result) is not ConversationRetrievalReply:
            raise AssertionError("direct answer did not yield a reply")
        return result


class CancellableDebateBound:
    """Block first-run audits and approve every phase of the retry debate."""

    def __init__(self, definition: AgentDefinition, state: dict[str, object]) -> None:
        self.definition = definition
        self.state = state

    async def invoke(self, request: AgentRequest) -> AgentReply:
        """Return canonical phase replies while exposing first-run cancellation."""

        if request.debate_id == "cancelled_debate" and request.phase is Phase.AUDIT:
            started = self.state["started"]
            assert isinstance(started, set)
            started.add(request.agent_id)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = self.state["cancelled"]
                assert isinstance(cancelled, set)
                cancelled.add(request.agent_id)
                raise
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, "generated candidate")
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


class InteractiveCancellationTestCase(InteractiveConversationTestCase):
    """Build selected controllers with injected terminal and runtime fixtures."""

    def selected_controller_with_callbacks(
        self,
        callbacks: ScriptedTerminalCallbacks,
        *,
        runtime: object = _REAL_RUNTIME,
        subjects: tuple[str, ...] = ("Selected subject",),
        backend: AgentBackend | None = None,
    ) -> tuple[
        Workspace,
        FilesystemConversationCatalog,
        tuple[ConversationMetadata, ...],
        InteractiveController,
        AgentBackend,
        FilesystemConversationEventStore,
    ]:
        """Create one active selected attachment around supplied callbacks."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        entries = tuple(catalog.create("session", subject) for subject in subjects)
        save_default_conversation(
            workspace,
            "session",
            entries[0].conversation_id,
        )
        selected_backend = backend or self.make_backend(workspace)
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
            width=240,
        )
        if runtime is _REAL_RUNTIME:
            controller = InteractiveController(
                workspace,
                load_cli_context(workspace),
                selected_backend,
                terminal,
                conversation_catalog=catalog,
            )
        else:
            with mock.patch.object(
                controller_core_module,
                "StoryRuntime",
                return_value=runtime,
            ):
                controller = InteractiveController(
                    workspace,
                    load_cli_context(workspace),
                    selected_backend,
                    terminal,
                    conversation_catalog=catalog,
                )
        store = controller._conversation_event_store
        return (
            workspace,
            catalog,
            entries,
            controller,
            selected_backend,
            store,
        )


class RunnerAndAdvisoryCancellationTests(InteractiveCancellationTestCase):
    """Verify Runner lifetime and advisory cancellation through real boundaries."""

    def test_runner_starts_with_lifecycle_and_closes_residual_work_before_backend(
        self,
    ) -> None:
        runtime = ResidualAdvisoryRuntime()
        callbacks = ScriptedTerminalCallbacks("message", "/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(
            callbacks,
            runtime=runtime,
        )
        cleanup_events: list[str] = []
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            cleanup_events=cleanup_events,
        )
        original_detach = backend.release_session_access
        original_close = backend.close

        def detach(session_id: str) -> object:
            cleanup_events.append("backend.detach")
            return original_detach(session_id)

        def close() -> None:
            cleanup_events.append("backend.close")
            original_close()

        self.assertEqual(runner_factory.instances, [])
        self.assertIsNone(controller._runner)
        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(controller_core_module.asyncio, "run") as legacy_run,
            mock.patch.object(backend, "release_session_access", side_effect=detach),
            mock.patch.object(backend, "close", side_effect=close),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        legacy_run.assert_not_called()
        self.assertEqual(len(runner_factory.instances), 1)
        runner = runner_factory.instances[0]
        self.assertEqual(runner.run_calls, 1)
        self.assertEqual(len(set(runner.loop_ids)), 1)
        self.assertTrue(runner.closed)
        self.assertIsNone(controller._runner)
        self.assertEqual(
            cleanup_events,
            ["runner.close", "backend.detach", "backend.close"],
        )
        self.assertIsNotNone(runtime.pending_task)
        assert runtime.pending_task is not None
        self.assertTrue(runtime.pending_task.done())
        self.assertTrue(runtime.pending_task.cancelled())
        self.assertTrue(runtime.pending_task_finalized)
        self.assertTrue(runtime.generator_finalized)
        self.assertTrue(all(loop.is_closed() for loop in runtime.loops))
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())

    def test_real_runner_preentry_sigint_recovers_and_reuses_the_loop(
        self,
    ) -> None:
        state: dict[str, object] = {
            "block": False,
            "requests": [],
            "loops": [],
        }
        retained: dict[str, object] = {}
        bound_definitions: list[AgentDefinition] = []
        invoked_handlers: list[Callable[[int, object], None]] = []
        cancelled = (
            "[cancelled] Plot & Structure Architect turn was interrupted.\n"
        )
        hint = (
            "[hint] The story session remains attached; the affected provider "
            "thread may restart on the next message.\n"
        )

        def first_turn(_prompt: str) -> str:
            """Capture attached state before the pre-entry interruption."""

            controller = retained["controller"]
            workspace = retained["workspace"]
            assert isinstance(controller, InteractiveController)
            assert isinstance(workspace, Workspace)
            runner = controller._runner
            assert isinstance(runner, asyncio.Runner)
            retained.update(
                runner=runner,
                loop=runner.get_loop(),
                memory=controller._conversation_memory_service,
                selection=controller._focused_conversation,
                selection_id=load_default_conversation(workspace, "session"),
                durable=_file_bytes(workspace),
                manifest=workspace.read_json(_MANIFEST_RELATIVE),
            )
            return "cancel before entry"

        def next_turn(_prompt: str) -> str:
            """Verify the quiescent first cancellation before continuing."""

            controller = retained["controller"]
            workspace = retained["workspace"]
            callbacks = retained["callbacks"]
            store = retained["store"]
            assert isinstance(controller, InteractiveController)
            assert isinstance(workspace, Workspace)
            assert isinstance(callbacks, ScriptedTerminalCallbacks)
            assert isinstance(store, FilesystemConversationEventStore)
            self.assertEqual(len(invoked_handlers), 1)
            self.assertIs(controller._runner, retained["runner"])
            self.assertIs(controller._runner.get_loop(), retained["loop"])
            self.assertEqual(asyncio.all_tasks(retained["loop"]), set())
            self.assertEqual(controller._attached_session_id, "session")
            self.assertEqual(
                controller._focused_conversation,
                retained["selection"],
            )
            self.assertIs(
                controller._conversation_memory_service,
                retained["memory"],
            )
            self.assertEqual(controller._target_agent_id, PLOT_ARCHITECT)
            self.assertEqual(
                load_default_conversation(workspace, "session"),
                retained["selection_id"],
            )
            self.assertEqual(_file_bytes(workspace), retained["durable"])
            self.assertEqual(
                workspace.read_json(_MANIFEST_RELATIVE),
                retained["manifest"],
            )
            self.assertEqual(state["requests"], [])
            self.assertEqual(state["loops"], [])
            self.assertEqual(bound_definitions, [])
            runtime_request = retained["runtime_request"]
            assert isinstance(runtime_request, mock.AsyncMock)
            runtime_request.assert_not_called()
            self.assertEqual(store.read("session", retained["selection_id"]), ())
            rendered = "".join(callbacks.records)
            self.assertEqual(rendered.count(cancelled), 1)
            self.assertEqual(rendered.count(hint), 1)
            self.assertNotIn("operation_interrupted", rendered)
            self.assertNotIn("Retrieval search action", rendered)
            self.assertNotIn("Retrieval read action", rendered)
            return "after cancellation"

        callbacks = ScriptedTerminalCallbacks(
            "/to plot",
            first_turn,
            next_turn,
            "/quit",
        )
        (
            workspace,
            catalog,
            entries,
            controller,
            backend,
            store,
        ) = self.selected_controller_with_callbacks(callbacks)
        retained.update(
            workspace=workspace,
            controller=controller,
            callbacks=callbacks,
            store=store,
        )
        core_before = core_snapshot(workspace)

        async def bind(definition: AgentDefinition) -> ConversationBound:
            """Return a direct-answer binding only after cancellation recovery."""

            bound_definitions.append(definition)
            return ConversationBound(definition, state)

        real_signal = signal.signal
        original_sigint_handler = signal.getsignal(signal.SIGINT)
        self.addCleanup(real_signal, signal.SIGINT, original_sigint_handler)
        real_signal(signal.SIGINT, signal.default_int_handler)
        real_run_until_complete = asyncio.BaseEventLoop.run_until_complete
        interruption_injected = False
        runtime = controller._runtime

        def invoke_installed_handler_before_first_step(
            loop: asyncio.BaseEventLoop,
            future: object,
        ) -> object:
            """Invoke the installed handler before the loop starts its task."""

            nonlocal interruption_injected
            if (
                not interruption_injected
                and loop is retained["loop"]
            ):
                installed = signal.getsignal(signal.SIGINT)
                assert callable(installed)
                self.assertIsNot(installed, signal.default_int_handler)
                interruption_injected = True
                invoked_handlers.append(installed)
                installed(signal.SIGINT, None)
            return real_run_until_complete(loop, future)

        with (
            mock.patch.object(
                asyncio.BaseEventLoop,
                "run_until_complete",
                new=invoke_installed_handler_before_first_step,
            ),
            mock.patch.object(
                runtime,
                "converse_with_retrieval",
                wraps=runtime.converse_with_retrieval,
            ) as runtime_request,
            mock.patch.object(backend, "bind", new=bind),
            mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                return_value="t_after",
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
            mock.patch.object(
                backend,
                "close",
                wraps=backend.close,
            ) as close,
        ):
            retained["runtime_request"] = runtime_request
            exit_code = controller.run_session_lobby()
            self.assertEqual(exit_code, 0, callbacks.records)

        real_signal(signal.SIGINT, original_sigint_handler)
        self.assertTrue(interruption_injected)
        self.assertEqual(len(invoked_handlers), 1)
        runtime_request.assert_awaited_once()
        self.assertEqual(len(bound_definitions), 1)
        self.assertEqual(bound_definitions[0].agent_id, PLOT_ARCHITECT)
        requests = state["requests"]
        loops = state["loops"]
        assert isinstance(requests, list)
        assert isinstance(loops, list)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].content, "after cancellation")
        self.assertEqual(loops, [retained["loop"]])
        loop = retained["loop"]
        assert isinstance(loop, asyncio.AbstractEventLoop)
        self.assertTrue(loop.is_closed())
        transcript = FilesystemConversationEventStore(
            workspace,
            catalog=catalog,
        ).read("session", entries[0].conversation_id)
        self.assertEqual(
            tuple((event.turn_id, event.kind) for event in transcript),
            (
                ("t_after", ConversationEventKind.USER_MESSAGE),
                ("t_after", ConversationEventKind.AGENT_REPLY),
            ),
        )
        self.assertEqual(
            tuple(event.content for event in transcript),
            ("after cancellation", "reply:after cancellation"),
        )
        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertEqual(
            workspace.read_json(_MANIFEST_RELATIVE)["provider_threads"],
            {},
        )
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            entries[0].conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count(cancelled), 1)
        self.assertEqual(rendered.count(hint), 1)
        self.assertNotIn("operation_interrupted", rendered)
        self.assertIn("plot [completed]> reply:after cancellation\n", rendered)
        detach.assert_called_once_with("session")
        close.assert_called_once_with()
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
        self.assertIsNone(controller._runner)
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._conversation_memory_service)

    def test_advisory_interrupt_joins_persists_taints_and_restarts_fresh(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        selected = catalog.create("session", "Provider cancellation")
        save_default_conversation(workspace, "session", selected.conversation_id)

        provider_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(provider_temporary.cleanup)
        provider_root = Path(provider_temporary.name)
        executable = provider_root / "fake-codex"
        executable.write_text(_FAKE_CODEX, encoding="utf-8")
        executable.chmod(0o755)
        log_path = provider_root / "invocations.jsonl"
        backend = AgentBackend(workspace, executable=str(executable))
        self.addCleanup(backend.close)

        turn_ids = ("t_seed", "t_cancelled", "t_after")

        def answer(turn_id: str, content: str) -> str:
            """Return one exact direct-answer provider action."""

            return json.dumps(
                {
                    "action": {
                        "kind": "answer",
                        "session_id": "session",
                        "conversation_id": selected.conversation_id,
                        "turn_id": turn_id,
                        "action_index": 1,
                        "content": content,
                        "citation_ids": [],
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        taint_before_retry: list[bool] = []

        def seed_turn(_prompt: str) -> str:
            """Configure the first successful provider-thread creation."""

            os.environ["STORY_FAKE_CODEX_MODE"] = "ok"
            os.environ["STORY_FAKE_CODEX_OUTPUTS"] = json.dumps(
                [answer(turn_ids[0], "seed reply")],
                ensure_ascii=False,
            )
            return "seed"

        def cancelled_turn(_prompt: str) -> str:
            """Configure the compatible provider continuation to block."""

            os.environ["STORY_FAKE_CODEX_MODE"] = "sleep"
            return "cancel this"

        def retry_turn(_prompt: str) -> str:
            """Observe taint, then configure the subsequent fresh invocation."""

            manifest = workspace.read_json(_MANIFEST_RELATIVE)
            thread = manifest["provider_threads"][f"codex:{PLOT_ARCHITECT}"]
            taint_before_retry.append(thread["tainted"])
            os.environ["STORY_FAKE_CODEX_MODE"] = "ok"
            os.environ["STORY_FAKE_CODEX_OUTPUTS"] = json.dumps(
                [answer(turn_ids[2], "after reply")],
                ensure_ascii=False,
            )
            return "after cancellation"

        callbacks = ScriptedTerminalCallbacks(
            "/to plot",
            seed_turn,
            cancelled_turn,
            retry_turn,
            "/quit",
        )
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
            width=240,
        )
        controller = InteractiveController(
            workspace,
            load_cli_context(workspace),
            backend,
            terminal,
            conversation_catalog=catalog,
        )

        def provider_call_count() -> int:
            """Count complete provider payload records without waiting or mutation."""

            if not log_path.exists():
                return 0
            return sum(
                '"payload"' in line
                for line in log_path.read_text(encoding="utf-8").splitlines()
            )

        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={2: "joined"},
            predicates={2: lambda: provider_call_count() >= 2},
        )
        real_mkdtemp = tempfile.mkdtemp
        temporary_directories: list[Path] = []

        def recording_mkdtemp(*args: object, **kwargs: object) -> str:
            """Retain backend-created temporary paths for cleanup assertions."""

            path = Path(real_mkdtemp(*args, **kwargs))
            temporary_directories.append(path)
            return str(path)

        real_create_subprocess = asyncio.create_subprocess_exec
        processes: list[asyncio.subprocess.Process] = []

        async def recording_create_subprocess(
            *args: object,
            **kwargs: object,
        ) -> asyncio.subprocess.Process:
            """Retain every real fake-provider process for reaping assertions."""

            process = await real_create_subprocess(*args, **kwargs)
            processes.append(process)
            return process

        core_before = core_snapshot(workspace)
        with (
            mock.patch.dict(
                os.environ,
                {
                    "STORY_FAKE_CODEX_LOG": str(log_path),
                    "STORY_FAKE_CODEX_MODE": "ok",
                },
                clear=False,
            ),
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                side_effect=turn_ids,
            ),
            mock.patch.object(
                backend_module.tempfile,
                "mkdtemp",
                side_effect=recording_mkdtemp,
            ),
            mock.patch.object(
                backend_module.asyncio,
                "create_subprocess_exec",
                new=recording_create_subprocess,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        transcript = FilesystemConversationEventStore(
            workspace,
            catalog=catalog,
        ).read("session", selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in transcript),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(
            transcript[3].content,
            "CancelledError: conversation turn was cancelled",
        )
        self.assertEqual(
            tuple(event.turn_id for event in transcript),
            (
                turn_ids[0],
                turn_ids[0],
                turn_ids[1],
                turn_ids[1],
                turn_ids[2],
                turn_ids[2],
            ),
        )
        self.assertEqual(taint_before_retry, [True])
        manifest = workspace.read_json(_MANIFEST_RELATIVE)
        self.assertIs(
            manifest["provider_threads"][f"codex:{PLOT_ARCHITECT}"]["tainted"],
            False,
        )
        invocation_lines = (
            log_path.read_text(encoding="utf-8").splitlines()
            if log_path.exists()
            else []
        )
        invocation_records = [json.loads(line) for line in invocation_lines]
        provider_records = [
            record for record in invocation_records if "payload" in record
        ]
        self.assertEqual(
            ["resume" in record["argv"] for record in provider_records],
            [False, True, False],
        )
        self.assertTrue(
            any(record.get("terminated") is True for record in invocation_records)
        )
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(process.returncode is not None for process in processes))
        self.assertTrue(temporary_directories)
        self.assertTrue(all(not path.exists() for path in temporary_directories))
        self.assertEqual(backend._staging_directories, set())
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )
        self.assertEqual(controller._target_agent_id, PLOT_ARCHITECT)
        self.assertEqual(len(runner_factory.instances), 1)
        runner = runner_factory.instances[0]
        self.assertEqual(runner.run_calls, 3)
        self.assertEqual(len(set(runner.loop_ids)), 1)
        self.assertTrue(runner.closed)
        self.assertTrue(all(task.done() for task in runner.operation_tasks))
        rendered = "".join(callbacks.records)
        self.assertNotIn("[waiting] ", rendered)
        self.assertIn(
            "[cancelled] Plot & Structure Architect turn was interrupted.\n",
            rendered,
        )
        self.assertIn(
            "[hint] The story session remains attached; the affected provider "
            "thread may restart on the next message.\n",
            rendered,
        )
        self.assertIn("plot [completed]> after reply\n", rendered)

    def test_advisory_outcome_failure_leaves_unmatched_user_then_recovers(
        self,
    ) -> None:
        state: dict[str, object] = {
            "block": True,
            "entered": False,
            "cancelled": False,
            "joined": False,
            "requests": [],
        }

        def retry(_prompt: str) -> str:
            """Require joined cancellation before enabling the next reply."""

            self.assertIs(state["cancelled"], True)
            self.assertIs(state["joined"], True)
            state["block"] = False
            return "after cancellation"

        callbacks = ScriptedTerminalCallbacks("cancel me", retry, "/quit")
        (
            workspace,
            catalog,
            entries,
            controller,
            backend,
            store,
        ) = self.selected_controller_with_callbacks(callbacks)

        async def bind(definition: AgentDefinition) -> ConversationBound:
            """Return one real-runtime compatible cancellable binding."""

            return ConversationBound(definition, state)

        append_attempts: list[ConversationEventKind] = []
        original_append = store.append

        def append(event: ConversationEventAppend) -> object:
            """Fail only the best-effort cancellation outcome append."""

            append_attempts.append(event.kind)
            if event.kind is ConversationEventKind.RUNTIME_ERROR:
                raise OSError("cancel outcome unavailable")
            return original_append(event)

        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={1: "joined"},
            predicates={1: lambda: state["entered"] is True},
        )
        core_before = core_snapshot(workspace)
        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(backend, "bind", new=bind),
            mock.patch.object(store, "append", side_effect=append),
            mock.patch.object(
                StoryRuntime,
                "_new_conversation_turn_id",
                side_effect=("t_unmatched", "t_recovered"),
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        events = FilesystemConversationEventStore(
            workspace,
            catalog=catalog,
        ).read("session", entries[0].conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(
            tuple(event.turn_id for event in events),
            ("t_unmatched", "t_recovered", "t_recovered"),
        )
        requests = state["requests"]
        assert isinstance(requests, list)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].context.events, ())
        self.assertEqual(
            append_attempts,
            [
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ],
        )
        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
        runner = runner_factory.instances[0]
        self.assertEqual(runner.run_calls, 2)
        self.assertEqual(len(set(runner.loop_ids)), 1)
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[cancelled] Lead Orchestrator turn was interrupted.\n",
            rendered,
        )
        self.assertIn(
            "[hint] The story session remains attached; the affected provider "
            "thread may restart on the next message.\n",
            rendered,
        )
        self.assertIn("lead [completed]> reply:after cancellation\n", rendered)

    def test_nonquiescent_preentry_unjoined_and_bare_cancelled_are_fatal(
        self,
    ) -> None:
        cases = (
            "before_entry_nonquiescent",
            "unjoined",
            "bare_cancelled",
        )
        for behavior in cases:
            with self.subTest(behavior=behavior):
                runtime = BlockingAdvisoryRuntime()
                callbacks = ScriptedTerminalCallbacks("cancel me")
                (
                    workspace,
                    _catalog,
                    _entries,
                    controller,
                    _backend,
                    _store,
                ) = self.selected_controller_with_callbacks(
                    callbacks,
                    runtime=runtime,
                )
                real_runner = asyncio.Runner
                runner_factory = RunnerFactory(
                    real_runner,
                    interruptions={1: behavior},
                    predicates={1: lambda: runtime.entered},
                )
                with mock.patch.object(
                    controller_core_module.asyncio,
                    "Runner",
                    runner_factory,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.OPERATION_INTERRUPTED),
                    rendered,
                )
                self.assertNotIn("turn was interrupted", rendered)
                runner = runner_factory.instances[0]
                self.assertTrue(runner.closed)
                self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
                if behavior == "unjoined":
                    self.assertTrue(runtime.entered)
                    self.assertTrue(runtime.cancelled)
                    self.assertTrue(runtime.joined)
                    self.assertTrue(all(task.done() for task in runner.operation_tasks))
                else:
                    self.assertFalse(runtime.entered)
                if behavior == "before_entry_nonquiescent":
                    self.assertEqual(len(runner.operation_tasks), 1)
                    self.assertTrue(runner.operation_tasks[0].done())
                    self.assertTrue(runner.operation_tasks[0].cancelled())


class ConsensusCancellationTests(InteractiveCancellationTestCase):
    """Verify pre-entry, durable proof, last-map, and fail-closed consensus."""

    def test_consensus_pre_entry_interrupt_is_nonmutating_and_recoverable(
        self,
    ) -> None:
        runtime = BlockingConsensusRuntime("interrupted_first", "unused_debate")
        callbacks = ScriptedTerminalCallbacks("/run prompt", "/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks, runtime=runtime)
        before = _file_bytes(workspace)
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={1: "before_entry"},
        )
        with mock.patch.object(
            controller_core_module.asyncio,
            "Runner",
            runner_factory,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(runtime.calls, 0)
        self.assertFalse(runtime.entered)
        self.assertEqual(controller._last_started_debate_by_session, {})
        self.assertEqual(_file_bytes(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[cancelled] Consensus run was cancelled before start.\n",
            rendered,
        )
        self.assertNotIn("[run ", rendered)
        self.assertNotIn("operation_interrupted", rendered)

    def test_interrupted_first_is_proof_without_updating_last_run(self) -> None:
        debate_id = "reserved_debate"
        runtime = BlockingConsensusRuntime("interrupted_first", debate_id)
        callbacks = ScriptedTerminalCallbacks("/run prompt", "/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks, runtime=runtime)
        before = _file_bytes(workspace)
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={1: "joined"},
            predicates={1: lambda: runtime.entered},
        )
        with mock.patch.object(
            controller_core_module.asyncio,
            "Runner",
            runner_factory,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertTrue(runtime.cancelled)
        self.assertEqual(controller._last_started_debate_by_session, {})
        self.assertEqual(_file_bytes(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count(
                f"[run {debate_id}] [cancelled] [warning]\n"
                "  Run interrupted after durable state transition.\n"
            ),
            1,
        )
        for record in (
            f"[cancelled] Debate {debate_id} was marked interrupted.\n",
            "[hint] It cannot resume. Use /run to create a new debate.\n",
            '[session] "session" remains ready and attached.\n',
        ):
            self.assertIn(record, rendered)
        self.assertNotIn("operation_interrupted", rendered)

    def test_started_interruption_is_durable_and_retry_uses_new_id(self) -> None:
        callbacks_holder: dict[str, object] = {}
        state: dict[str, object] = {"started": set(), "cancelled": set()}

        def retry(_prompt: str) -> str:
            """Inspect the interrupted state before starting the new-ID retry."""

            workspace = callbacks_holder["workspace"]
            controller = callbacks_holder["controller"]
            assert isinstance(workspace, Workspace)
            assert isinstance(controller, InteractiveController)
            inspected = workspace.inspect_debate("session", "cancelled_debate")
            callbacks_holder["interrupted"] = inspected
            callbacks_holder["last_before_retry"] = dict(
                controller._last_started_debate_by_session
            )
            callbacks_holder["manuscripts_before_retry"] = all_manuscripts(workspace)
            callbacks_holder["core_before_retry"] = core_snapshot(workspace)
            return "/run retry"

        callbacks = ScriptedTerminalCallbacks("/run cancel", retry, "/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks)
        callbacks_holder.update(workspace=workspace, controller=controller)
        core_before = core_snapshot(workspace)

        async def bind(definition: AgentDefinition) -> CancellableDebateBound:
            """Return one shared-state debate binding for every runtime agent."""

            return CancellableDebateBound(definition, state)

        def audits_started() -> bool:
            """Return whether every fixed specialist audit is pending."""

            started = state["started"]
            return isinstance(started, set) and started == set(SPECIALIST_IDS)

        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={1: "joined"},
            predicates={1: audits_started},
        )
        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(backend, "bind", new=bind),
            mock.patch.object(
                StoryRuntime,
                "_new_debate_id",
                side_effect=("cancelled_debate", "retry_debate"),
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        interrupted = callbacks_holder["interrupted"]
        assert isinstance(interrupted, dict)
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertTrue(interrupted["terminal"])
        self.assertIsNone(interrupted["result"])
        self.assertEqual(interrupted["state"]["status"], "interrupted")
        self.assertEqual(
            callbacks_holder["last_before_retry"],
            {"session": "cancelled_debate"},
        )
        self.assertEqual(callbacks_holder["manuscripts_before_retry"], [])
        self.assertEqual(callbacks_holder["core_before_retry"], core_before)
        self.assertEqual(state["cancelled"], set(SPECIALIST_IDS))
        retry_result = workspace.inspect_debate("session", "retry_debate")
        self.assertEqual(retry_result["status"], "consensus")
        self.assertIsNotNone(retry_result["result"])
        self.assertEqual(
            controller._last_started_debate_by_session,
            {"session": "retry_debate"},
        )
        manuscripts = all_manuscripts(workspace)
        self.assertEqual(len(manuscripts), 1)
        self.assertNotIn("cancelled_debate", manuscripts[0].name)
        self.assertIn("retry_debate", manuscripts[0].name)
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count(
                "[run cancelled_debate] [cancelled] [warning]\n"
                "  Run interrupted after durable state transition.\n"
            ),
            1,
        )
        self.assertIn(
            "[cancelled] Debate cancelled_debate was marked interrupted.\n",
            rendered,
        )
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())

    def test_started_without_interrupted_proof_fails_closed_once(self) -> None:
        runtime = BlockingConsensusRuntime("started_no_proof", "unproved_debate")
        callbacks = ScriptedTerminalCallbacks("/run prompt", "/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks, runtime=runtime)
        before = _file_bytes(workspace)
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            interruptions={1: "joined"},
            predicates={1: lambda: runtime.entered},
        )
        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
            mock.patch.object(
                backend,
                "close",
                wraps=backend.close,
            ) as close,
        ):
            self.assertEqual(controller.run_session_lobby(), 2)

        detach.assert_not_called()
        close.assert_called_once_with()
        self.assertEqual(callbacks.reads, ["/quit"])
        self.assertEqual(
            controller._last_started_debate_by_session,
            {"session": "unproved_debate"},
        )
        self.assertEqual(_file_bytes(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
        rendered = "".join(callbacks.records)
        operation = _error_record(InteractiveErrorCode.OPERATION_INTERRUPTED)
        cleanup = _error_record(InteractiveErrorCode.CLEANUP_FAILED)
        self.assertEqual(rendered.count(operation), 1)
        self.assertEqual(rendered.count(cleanup), 1)
        self.assertLess(rendered.index(operation), rendered.index(cleanup))
        self.assertNotIn("was marked interrupted", rendered)


class ProviderFreeCancellationTests(InteractiveCancellationTestCase):
    """Verify partial local rendering remains provider-free and nonmutating."""

    @staticmethod
    def _append_pair(
        store: FilesystemConversationEventStore,
        metadata: ConversationMetadata,
        turn_id: str,
        question: str,
        reply: str,
    ) -> None:
        """Append one canonical visible question/reply pair."""

        store.append(
            ConversationEventAppend(
                session_id="session",
                conversation_id=metadata.conversation_id,
                turn_id=turn_id,
                sender="user",
                recipient=LEAD_ORCHESTRATOR,
                kind=ConversationEventKind.USER_MESSAGE,
                content=question,
            )
        )
        store.append(
            ConversationEventAppend(
                session_id="session",
                conversation_id=metadata.conversation_id,
                turn_id=turn_id,
                sender=LEAD_ORCHESTRATOR,
                recipient="user",
                kind=ConversationEventKind.AGENT_REPLY,
                content=reply,
            )
        )

    def test_search_transcript_and_history_keep_only_completed_partial_rows(
        self,
    ) -> None:
        cases = (
            (
                "search",
                "/search needle",
                lambda text: text.startswith("Search results ("),
                "[cancelled] Search cancelled; attached state is unchanged.\n",
                "[result] Search returned",
            ),
            (
                "transcript",
                None,
                lambda text: text.startswith("Transcript ("),
                "[cancelled] Transcript read cancelled; attached state is "
                "unchanged.\n",
                "[result] Transcript page returned",
            ),
            (
                "history",
                "/history",
                lambda text: text.startswith("History ("),
                "[cancelled] History cancelled; attached state is unchanged.\n",
                "[context]",
            ),
        )
        for label, configured_command, row_predicate, cancelled, result_prefix in cases:
            with self.subTest(command=label):
                command_holder: dict[str, str] = {}
                callbacks = InterruptingWriteCallbacks(
                    lambda _prompt: command_holder["command"],
                    "/quit",
                    output_predicate=row_predicate,
                    failure=KeyboardInterrupt(),
                )
                (
                    workspace,
                    catalog,
                    entries,
                    controller,
                    backend,
                    store,
                ) = self.selected_controller_with_callbacks(
                    callbacks,
                    subjects=("Needle first", "Needle second"),
                )
                for index, entry in enumerate(entries, start=1):
                    self._append_pair(
                        store,
                        entry,
                        f"turn_{index}",
                        f"needle question {index}",
                        f"answer {index}",
                    )
                command_holder["command"] = (
                    configured_command
                    if configured_command is not None
                    else f"/transcript {entries[0].conversation_id}"
                )
                before = _file_bytes(workspace)
                selection_before = load_default_conversation(
                    workspace,
                    "session",
                )
                original_read = store.read
                with (
                    mock.patch.object(backend, "bind") as bind,
                    mock.patch.object(store, "append") as append,
                    mock.patch.object(
                        store,
                        "read",
                        wraps=original_read,
                    ) as read,
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)

                bind.assert_not_called()
                append.assert_not_called()
                if label == "history":
                    read.assert_called_once_with(
                        "session",
                        entries[0].conversation_id,
                    )
                self.assertEqual(_file_bytes(workspace), before)
                self.assertEqual(
                    load_default_conversation(workspace, "session"),
                    selection_before,
                )
                self.assertEqual(controller._target_agent_id, LEAD_ORCHESTRATOR)
                self.assertEqual(callbacks.failures, 1)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    sum(row_predicate(record) for record in callbacks.records),
                    1,
                )
                self.assertIn(cancelled, rendered)
                self.assertEqual(callbacks.records.count(cancelled), 1)
                self.assertNotIn(result_prefix, rendered)
                self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
                self.assertEqual(all_manuscripts(workspace), [])


class TerminalAndCleanupFailureTests(InteractiveCancellationTestCase):
    """Verify terminal mapping and cleanup ordering under combined failures."""

    def test_terminal_input_and_output_failures_map_without_skipping_cleanup(
        self,
    ) -> None:
        cases: tuple[
            tuple[str, ScriptedTerminalCallbacks, InteractiveErrorCode], ...
        ] = (
            (
                "input_oserror",
                ScriptedTerminalCallbacks(OSError("terminal lost")),
                InteractiveErrorCode.OPERATION_INTERRUPTED,
            ),
            (
                "input_internal",
                ScriptedTerminalCallbacks(RuntimeError("terminal bug")),
                InteractiveErrorCode.INTERNAL_ERROR,
            ),
            (
                "output_oserror",
                InterruptingWriteCallbacks(
                    "/quit",
                    output_predicate=lambda text: text.startswith(
                        "Conversation scope\n"
                    ),
                    failure=OSError("broken pipe"),
                ),
                InteractiveErrorCode.OPERATION_INTERRUPTED,
            ),
        )
        for label, callbacks, expected_code in cases:
            with self.subTest(case=label):
                (
                    workspace,
                    _catalog,
                    _entries,
                    controller,
                    backend,
                    _store,
                ) = self.selected_controller_with_callbacks(callbacks)
                with (
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
                    ) as detach,
                    mock.patch.object(
                        backend,
                        "close",
                        wraps=backend.close,
                    ) as close,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                detach.assert_called_once_with("session")
                close.assert_called_once_with()
                self.assertIn(_error_record(expected_code), "".join(callbacks.records))
                self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
                self.assertIsNone(controller._runner)

    def test_combined_cleanup_failures_keep_order_and_one_diagnostic(self) -> None:
        callbacks = ScriptedTerminalCallbacks("/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks)
        cleanup_events: list[str] = []
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            cleanup_events=cleanup_events,
            close_failure=OSError("runner close failed"),
        )
        original_detach = backend.release_session_access
        original_close = backend.close

        def detach(session_id: str) -> object:
            cleanup_events.append("backend.detach")
            result = original_detach(session_id)
            raise OSError("detach failed after release")

        def close() -> None:
            cleanup_events.append("backend.close")
            original_close()
            raise RuntimeError("backend close failed")

        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(backend, "release_session_access", side_effect=detach),
            mock.patch.object(backend, "close", side_effect=close),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)

        self.assertEqual(
            cleanup_events,
            ["runner.close", "backend.detach", "backend.close"],
        )
        self.assertEqual(
            "".join(callbacks.records).count("[error cleanup_failed]"),
            1,
        )
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())
        self.assertIsNone(controller._runner)
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._conversation_memory_service)

    def test_interrupted_runner_close_runs_later_cleanup_and_retains_runner(
        self,
    ) -> None:
        callbacks = ScriptedTerminalCallbacks("/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks)
        cleanup_events: list[str] = []
        real_runner = asyncio.Runner
        runner_factory = RunnerFactory(
            real_runner,
            cleanup_events=cleanup_events,
            close_failure=KeyboardInterrupt("second interrupt"),
        )
        original_detach = backend.release_session_access
        original_close = backend.close

        def detach(session_id: str) -> object:
            cleanup_events.append("backend.detach")
            return original_detach(session_id)

        def close() -> None:
            cleanup_events.append("backend.close")
            original_close()

        with (
            mock.patch.object(controller_core_module.asyncio, "Runner", runner_factory),
            mock.patch.object(backend, "release_session_access", side_effect=detach),
            mock.patch.object(backend, "close", side_effect=close),
        ):
            with self.assertRaises(KeyboardInterrupt):
                controller.run_session_lobby()

        self.assertEqual(
            cleanup_events,
            ["runner.close", "backend.detach", "backend.close"],
        )
        self.assertIs(controller._runner, runner_factory.instances[0])
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._conversation_memory_service)
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())


if __name__ == "__main__":
    unittest.main()
