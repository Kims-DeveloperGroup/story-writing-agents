"""Interactive Runner, cancellation, terminal-loss, and cleanup contracts."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable, Coroutine
from typing import Any
from unittest import mock

from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import (
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_terminal import (
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)

from tests.helpers import all_manuscripts
from tests.interactive_controller_fixture import (
    REAL_RUNTIME as _REAL_RUNTIME,
    InteractiveControllerTestCase as InteractiveConversationTestCase,
    ScriptedTerminalCallbacks,
    error_record as _error_record,
)


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_ACCESS_RELATIVE = f"{_SESSION_DIRECTORY}/session.access.json"
_MANIFEST_RELATIVE = f"{_SESSION_DIRECTORY}/session.json"


def _file_bytes(workspace: Workspace) -> dict[str, bytes]:
    """Return domain bytes while excluding leases and diagnostic artifacts."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
        and not path.name.endswith(".access.json")
        and "debug_logs" not in path.relative_to(workspace.root).parts
    }


def _traceback_contains(traceback: object, expected: object) -> bool:
    """Return whether one traceback chain retains an exact injected node."""

    current = traceback
    while current is not None:
        if current is expected:
            return True
        current = current.tb_next  # type: ignore[union-attr]
    return False


def _raise_recorded(
    failure: BaseException,
    tracebacks: list[object],
) -> None:
    """Raise one exact failure and retain its injected traceback node."""

    try:
        raise failure
    except BaseException as raised:
        tracebacks.append(raised.__traceback__)
        raise


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
        close_tracebacks: list[object] | None = None,
    ) -> None:
        self._delegate = runner_type()
        self.interruptions = interruptions or {}
        self.predicates = predicates or {}
        self.cleanup_events = cleanup_events
        self.close_failure = close_failure
        self.close_tracebacks = close_tracebacks
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
            if self.close_tracebacks is None:
                raise self.close_failure
            _raise_recorded(self.close_failure, self.close_tracebacks)


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
                "[cancelled] Transcript read cancelled; attached state is unchanged.\n",
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
                    output_predicate=lambda text: text.startswith("[conversation]"),
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
            original_detach(session_id)
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

    def test_first_cleanup_process_control_wins_after_every_stage(self) -> None:
        """Retain the first cleanup control while attempting every later stage."""

        cases = (
            ("runner", "keyboard", "system_exit", "keyboard", None),
            ("access", "oserror", "system_exit", "keyboard", "system_exit"),
            ("backend", "oserror", "oserror", "system_exit", "keyboard"),
            (
                "diagnostic",
                "oserror",
                "oserror",
                "oserror",
                "system_exit",
            ),
        )

        def failure(kind: str | None, label: str) -> BaseException | None:
            if kind == "keyboard":
                return KeyboardInterrupt(label)
            if kind == "system_exit":
                return SystemExit(label)
            if kind == "oserror":
                return OSError(label)
            return None

        for winner, runner_kind, access_kind, backend_kind, diagnostic_kind in cases:
            with self.subTest(winner=winner):
                callbacks = ScriptedTerminalCallbacks("/quit")
                (
                    workspace,
                    _catalog,
                    _entries,
                    controller,
                    backend,
                    _store,
                ) = self.selected_controller_with_callbacks(callbacks)
                events: list[str] = []
                runner_tracebacks: list[object] = []
                access_tracebacks: list[object] = []
                backend_tracebacks: list[object] = []
                diagnostic_tracebacks: list[object] = []
                runner_failure = failure(runner_kind, "runner control")
                access_failure = failure(access_kind, "access control")
                backend_failure = failure(backend_kind, "backend control")
                diagnostic_failure = failure(
                    diagnostic_kind,
                    "diagnostic control",
                )
                failures = {
                    "runner": runner_failure,
                    "access": access_failure,
                    "backend": backend_failure,
                    "diagnostic": diagnostic_failure,
                }
                expected = failures[winner]
                assert expected is not None
                runner_factory = RunnerFactory(
                    asyncio.Runner,
                    cleanup_events=events,
                    close_failure=runner_failure,
                    close_tracebacks=runner_tracebacks,
                )
                original_detach = backend.release_session_access
                original_close = backend.close
                original_record_error = (
                    controller_core_module.InteractiveController._record_debug_error
                )
                logged_failures: list[BaseException] = []

                def detach(session_id: str) -> object:
                    events.append("backend.detach")
                    result = original_detach(session_id)
                    if access_failure is not None:
                        _raise_recorded(access_failure, access_tracebacks)
                    return result

                def close() -> None:
                    events.append("backend.close")
                    original_close()
                    if backend_failure is not None:
                        _raise_recorded(backend_failure, backend_tracebacks)

                def write_cleanup_diagnostic(
                    _controller: InteractiveController,
                    _error: InteractiveError,
                ) -> bool:
                    events.append("cleanup.diagnostic")
                    if diagnostic_failure is not None:
                        _raise_recorded(
                            diagnostic_failure,
                            diagnostic_tracebacks,
                        )
                    return True

                def record_debug_error(
                    active_controller: InteractiveController,
                    operation: object,
                    recorded_failure: BaseException,
                    **keywords: object,
                ) -> None:
                    logged_failures.append(recorded_failure)
                    original_record_error(
                        active_controller,
                        operation,  # type: ignore[arg-type]
                        recorded_failure,
                        **keywords,  # type: ignore[arg-type]
                    )

                with (
                    mock.patch.object(
                        controller_core_module.asyncio,
                        "Runner",
                        runner_factory,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        side_effect=detach,
                    ),
                    mock.patch.object(backend, "close", side_effect=close),
                    mock.patch.object(
                        controller_core_module.InteractiveController,
                        "_write_error_best_effort",
                        autospec=True,
                        side_effect=write_cleanup_diagnostic,
                    ) as diagnostic,
                    mock.patch.object(
                        controller_core_module.InteractiveController,
                        "_record_debug_error",
                        autospec=True,
                        side_effect=record_debug_error,
                    ),
                ):
                    try:
                        controller.run_session_lobby()
                    except BaseException as raised:
                        self.assertIs(raised, expected)
                        traceback_by_stage = {
                            "runner": runner_tracebacks,
                            "access": access_tracebacks,
                            "backend": backend_tracebacks,
                            "diagnostic": diagnostic_tracebacks,
                        }[winner]
                        self.assertEqual(len(traceback_by_stage), 1)
                        self.assertTrue(
                            _traceback_contains(
                                raised.__traceback__,
                                traceback_by_stage[0],
                            )
                        )
                    else:
                        self.fail("cleanup process control did not propagate")

                expected_events = [
                    "runner.close",
                    "backend.detach",
                    "backend.close",
                ]
                if any(
                    isinstance(item, Exception)
                    for item in (runner_failure, access_failure, backend_failure)
                ):
                    expected_events.append("cleanup.diagnostic")
                    diagnostic.assert_called_once()
                else:
                    diagnostic.assert_not_called()
                self.assertEqual(events, expected_events)
                self.assertIn(runner_failure, logged_failures)
                self.assertIn(access_failure, logged_failures)
                self.assertIn(backend_failure, logged_failures)
                runner = runner_factory.instances[0]
                if isinstance(runner_failure, Exception):
                    self.assertIsNone(controller._runner)
                else:
                    self.assertIs(controller._runner, runner)
                self.assertTrue(controller._closed)
                self.assertIsNone(controller._attached_session_id)
                self.assertIsNone(controller._focused_conversation)
                self.assertIsNone(controller._conversation_memory_service)
                self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())

    def test_body_system_exit_outranks_cleanup_controls_and_diagnostic(self) -> None:
        """Preserve the body control while every cleanup stage is attempted."""

        callbacks = ScriptedTerminalCallbacks("/quit")
        (
            workspace,
            _catalog,
            _entries,
            controller,
            backend,
            _store,
        ) = self.selected_controller_with_callbacks(callbacks)
        events: list[str] = []
        body_tracebacks: list[object] = []
        runner_tracebacks: list[object] = []
        access_tracebacks: list[object] = []
        backend_tracebacks: list[object] = []
        diagnostic_tracebacks: list[object] = []
        body_failure = SystemExit("body exit")
        runner_failure = OSError("runner cleanup failure")
        access_failure = KeyboardInterrupt("access cleanup control")
        backend_failure = SystemExit("backend cleanup control")
        diagnostic_failure = KeyboardInterrupt("diagnostic cleanup control")
        runner_factory = RunnerFactory(
            asyncio.Runner,
            cleanup_events=events,
            close_failure=runner_failure,
            close_tracebacks=runner_tracebacks,
        )
        original_detach = backend.release_session_access
        original_close = backend.close
        original_record_error = (
            controller_core_module.InteractiveController._record_debug_error
        )
        logged_failures: list[BaseException] = []

        def fail_body(_controller: InteractiveController) -> int:
            _raise_recorded(body_failure, body_tracebacks)

        def detach(session_id: str) -> object:
            events.append("backend.detach")
            result = original_detach(session_id)
            _raise_recorded(access_failure, access_tracebacks)
            return result

        def close() -> None:
            events.append("backend.close")
            original_close()
            _raise_recorded(backend_failure, backend_tracebacks)

        def write_cleanup_diagnostic(
            _controller: InteractiveController,
            _error: InteractiveError,
        ) -> bool:
            events.append("cleanup.diagnostic")
            _raise_recorded(diagnostic_failure, diagnostic_tracebacks)

        def record_debug_error(
            active_controller: InteractiveController,
            operation: object,
            recorded_failure: BaseException,
            **keywords: object,
        ) -> None:
            logged_failures.append(recorded_failure)
            original_record_error(
                active_controller,
                operation,  # type: ignore[arg-type]
                recorded_failure,
                **keywords,  # type: ignore[arg-type]
            )

        with (
            mock.patch.object(
                controller_core_module.asyncio,
                "Runner",
                runner_factory,
            ),
            mock.patch.object(
                controller_core_module.InteractiveController,
                "_read_session_lobby_commands",
                autospec=True,
                side_effect=fail_body,
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                side_effect=detach,
            ),
            mock.patch.object(backend, "close", side_effect=close),
            mock.patch.object(
                controller_core_module.InteractiveController,
                "_write_error_best_effort",
                autospec=True,
                side_effect=write_cleanup_diagnostic,
            ) as diagnostic,
            mock.patch.object(
                controller_core_module.InteractiveController,
                "_record_debug_error",
                autospec=True,
                side_effect=record_debug_error,
            ),
        ):
            try:
                controller.run_session_lobby()
            except BaseException as raised:
                self.assertIs(raised, body_failure)
                self.assertEqual(len(body_tracebacks), 1)
                self.assertTrue(
                    _traceback_contains(
                        raised.__traceback__,
                        body_tracebacks[0],
                    )
                )
            else:
                self.fail("body SystemExit did not propagate")

        self.assertEqual(
            events,
            [
                "runner.close",
                "backend.detach",
                "backend.close",
                "cleanup.diagnostic",
            ],
        )
        diagnostic.assert_called_once()
        self.assertEqual(
            set(logged_failures),
            {runner_failure, access_failure, backend_failure},
        )
        self.assertEqual(len(runner_tracebacks), 1)
        self.assertEqual(len(access_tracebacks), 1)
        self.assertEqual(len(backend_tracebacks), 1)
        self.assertEqual(len(diagnostic_tracebacks), 1)
        self.assertIsNone(controller._runner)
        self.assertTrue(controller._closed)
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._conversation_memory_service)
        self.assertFalse(workspace.path(_ACCESS_RELATIVE).exists())


if __name__ == "__main__":
    unittest.main()
