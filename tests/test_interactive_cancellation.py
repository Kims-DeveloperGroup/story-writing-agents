"""Interactive Runner, cancellation, terminal-loss, and cleanup contracts."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any
from unittest import mock

from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import (
    CollaborationEventBatch,
    ConversationMetadata,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_terminal import (
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents.workspace import FilesystemConversationCatalog, Workspace

from tests.collaboration_contract_fixture import contract_batches
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
        FilesystemCollaborationEventStore,
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
        services = compose_filesystem_conversation_storage(workspace)
        if runtime is not _REAL_RUNTIME:
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
                    conversation_storage_services=services,
                )
        else:
            controller = InteractiveController(
                workspace,
                load_cli_context(workspace),
                selected_backend,
                terminal,
                conversation_storage_services=services,
            )
        store = services.event_store
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
        store: FilesystemCollaborationEventStore,
        metadata: ConversationMetadata,
        _turn_id: str,
        _question: str,
        _reply: str,
    ) -> None:
        """Append one canonical two-event schema-v2 proposal batch."""

        batch = contract_batches(metadata.conversation_id)["proposal"]
        store.append_batch(
            CollaborationEventBatch(
                batch_id=batch.batch_id,
                expected_last_sequence=batch.expected_last_sequence,
                events=tuple(
                    replace(event, session_id="session") for event in batch.events
                ),
            )
        )









if __name__ == "__main__":
    unittest.main()
