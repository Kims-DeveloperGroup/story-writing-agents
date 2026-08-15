"""Atomic debate reservation, interruption, and mutation-boundary tests."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentReply,
    BackendProtocolError,
    ConfigurationError,
    ConversationContextWindow,
    DebateContextMode,
    DebateContextRequest,
    DebateExistsError,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    DebateStatus,
    Phase,
    ProgressObserverError,
    SPECIALIST_IDS,
    StoryRuntime,
    Workspace,
    WorkspaceFormatError,
)


def lifecycle_file_snapshot(workspace: Workspace) -> dict[str, bytes]:
    """Capture current files for exact pre-reservation mutation checks."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
    }


class _ContextReadStore:
    """Record or fail the one synchronous debate context read."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def read_context(self, session_id: str, conversation_id: str) -> object:
        self.calls.append((session_id, conversation_id))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def append(self, _event: object) -> object:
        raise AssertionError

    def read(self, _session_id: str, _conversation_id: str) -> tuple[object, ...]:
        raise AssertionError

from tests.helpers import (
    BindingBackendMixin,
    ScriptedBackend,
    all_manuscripts,
    core_snapshot,
    initialized_workspace,
)


class _GatedApprovalBackend(BindingBackendMixin):
    """Hold the winning runtime inside its backend until collision is observed."""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if request.phase is Phase.AUDIT:
            self.entered.set()
            await self.release.wait()
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


class DebateReservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_debate_is_atomically_owned_by_exactly_one_runtime(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)
        start = asyncio.Event()
        backend_entered = asyncio.Event()
        release_backend = asyncio.Event()
        backends = [
            _GatedApprovalBackend(backend_entered, release_backend),
            _GatedApprovalBackend(backend_entered, release_backend),
        ]
        runtimes = [
            StoryRuntime(Workspace(workspace.root), backends[0]),
            StoryRuntime(Workspace(workspace.root), backends[1]),
        ]
        outcomes: asyncio.Queue[tuple[int, DebateResult | BaseException]] = (
            asyncio.Queue()
        )

        async def launch(index: int) -> None:
            await start.wait()
            try:
                outcome: DebateResult | BaseException = await runtimes[
                    index
                ].run_draft(
                    "atomic_session",
                    "atomic-candidate",
                    debate_id="atomic_debate",
                    manuscript_name="atomic.md",
                )
            except BaseException as exc:
                outcome = exc
            await outcomes.put((index, outcome))

        tasks = [
            asyncio.create_task(launch(0)),
            asyncio.create_task(launch(1)),
        ]
        start.set()

        loser_index, loser_outcome = await outcomes.get()
        self.assertIsInstance(loser_outcome, DebateExistsError)
        self.assertEqual(backends[loser_index].requests, [])

        winner_index = 1 - loser_index
        await backend_entered.wait()
        self.assertTrue(backends[winner_index].requests)
        release_backend.set()

        completed_index, completed_outcome = await outcomes.get()
        self.assertEqual(completed_index, winner_index)
        self.assertIsInstance(completed_outcome, DebateResult)
        assert isinstance(completed_outcome, DebateResult)
        self.assertEqual(completed_outcome.status, DebateStatus.CONSENSUS)
        await asyncio.gather(*tasks)

        self.assertEqual(
            sum(bool(backend.requests) for backend in backends),
            1,
        )
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(
            [path.name for path in all_manuscripts(workspace)],
            ["atomic.md"],
        )
        self.assertEqual(
            workspace.read_manuscript("atomic.md"),
            "atomic-candidate",
        )

        inspected = StoryRuntime(Workspace(workspace.root)).inspect_debate(
            "atomic_session",
            "atomic_debate",
        )
        self.assertEqual(inspected["status"], "consensus")
        self.assertTrue(inspected["terminal"])
        self.assertEqual(inspected["result"]["status"], "consensus")
        self.assertEqual(
            [message["kind"] for message in inspected["messages"]],
            [
                "draft",
                "approval",
                "approval",
                "approval",
                "core_updates",
                "committed",
            ],
        )
        debate_files = sorted(
            path.name
            for path in workspace.path(
                "discussion_logs/atomic_session"
            ).iterdir()
        )
        self.assertEqual(
            debate_files,
            [
                "atomic_debate.jsonl",
                "atomic_debate.result.json",
                "atomic_debate.state.json",
            ],
        )

    async def test_pre_reservation_failures_emit_nothing_and_mutate_nothing(
        self,
    ) -> None:
        empty = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )

        class PreparationFailureBackend(ScriptedBackend):
            def prepare_session(self, _session_id: str) -> None:
                raise ConfigurationError("preparation failed")

        cases: list[tuple[str, ScriptedBackend, object, object, type[BaseException]]] = [
            (
                "context_read",
                ScriptedBackend(lambda request: AssertionError(request.phase)),
                _ContextReadStore(ValueError("invalid selected context")),
                DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.INCLUDED,
                ),
                ValueError,
            ),
            (
                "prepare",
                PreparationFailureBackend(
                    lambda request: AssertionError(request.phase)
                ),
                _ContextReadStore(empty),
                DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.FRESH,
                ),
                ConfigurationError,
            ),
        ]
        for index, (label, backend, store, request, error_type) in enumerate(cases):
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                before = lifecycle_file_snapshot(workspace)
                events: list[DebateProgressEvent] = []
                with self.assertRaises(error_type):
                    await StoryRuntime(
                        workspace,
                        backend,
                        conversation_event_store=store,  # type: ignore[arg-type]
                    ).run_draft(
                        f"pre_{index}",
                        "candidate",
                        debate_id=f"pre_debate_{index}",
                        conversation_context=request,  # type: ignore[arg-type]
                        progress_sink=events.append,
                    )
                self.assertEqual(events, [])
                self.assertEqual(backend.requests, [])
                self.assertEqual(lifecycle_file_snapshot(workspace), before)

        workspace = initialized_workspace(self)
        workspace.reserve_debate(
            "collision_session",
            "collision_debate",
            input_kind="draft",
            owner_token="existing_owner",
        )
        before = lifecycle_file_snapshot(workspace)
        backend = ScriptedBackend(lambda request: AssertionError(request.phase))
        events = []
        with self.assertRaises(DebateExistsError):
            await StoryRuntime(workspace, backend).run_draft(
                "collision_session",
                "candidate",
                debate_id="collision_debate",
                conversation_context=DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.FRESH,
                ),
                progress_sink=events.append,
            )
        self.assertEqual(events, [])
        self.assertEqual(backend.requests, [])
        self.assertEqual(lifecycle_file_snapshot(workspace), before)

    async def test_included_context_format_failure_is_normalized_before_reservation(
        self,
    ) -> None:
        """Preserve the store failure as cause while emitting and mutating nothing."""

        workspace = initialized_workspace(self)
        before = lifecycle_file_snapshot(workspace)
        failure = WorkspaceFormatError("invalid selected transcript")
        store = _ContextReadStore(failure)
        backend = ScriptedBackend(lambda request: AssertionError(request.phase))
        events: list[DebateProgressEvent] = []

        with (
            mock.patch.object(
                backend,
                "bind",
                wraps=backend.bind,
            ) as bind,
            mock.patch.object(
                workspace,
                "reserve_debate",
                wraps=workspace.reserve_debate,
            ) as reserve,
            self.assertRaises(ValueError) as raised,
        ):
            await StoryRuntime(
                workspace,
                backend,
                conversation_event_store=store,
            ).run_draft(
                "format_context_session",
                "candidate",
                debate_id="format_context_debate",
                conversation_context=DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.INCLUDED,
                ),
                progress_sink=events.append,
            )

        self.assertEqual(
            str(raised.exception),
            "conversation context capture is invalid",
        )
        self.assertIs(raised.exception.__cause__, failure)
        self.assertEqual(
            store.calls,
            [("format_context_session", "conversation_a")],
        )
        self.assertEqual(events, [])
        bind.assert_not_called()
        self.assertEqual(backend.definitions, [])
        self.assertEqual(backend.requests, [])
        reserve.assert_not_called()
        self.assertEqual(lifecycle_file_snapshot(workspace), before)

    async def test_async_generator_collaborators_fail_before_reservation(
        self,
    ) -> None:
        empty = ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )

        class AsyncGeneratorCallable:
            def __init__(self, yielded: object) -> None:
                self.yielded = yielded

            async def __call__(self, *_args: object):
                yield self.yielded

        class DeclaredPrepareBackend(ScriptedBackend):
            async def prepare_session(self, _session_id: str):
                yield None

        class DeclaredReadStore(_ContextReadStore):
            async def read_context(
                self,
                _session_id: str,
                _conversation_id: str,
            ):
                yield empty

        dynamic_prepare_results = []
        dynamic_prepare_calls: list[str] = []

        async def dynamic_prepare_values():
            yield None

        def dynamic_prepare(session_id: str) -> object:
            dynamic_prepare_calls.append(session_id)
            result = dynamic_prepare_values()
            dynamic_prepare_results.append(result)
            return result

        declared_prepare = DeclaredPrepareBackend(
            lambda request: AssertionError(request.phase)
        )
        callable_prepare = ScriptedBackend(
            lambda request: AssertionError(request.phase)
        )
        callable_prepare.prepare_session = AsyncGeneratorCallable(  # type: ignore[attr-defined]
            None
        )
        dynamic_prepare_backend = ScriptedBackend(
            lambda request: AssertionError(request.phase)
        )
        dynamic_prepare_backend.prepare_session = dynamic_prepare  # type: ignore[attr-defined]
        prepare_cases = (
            (
                "declared_method",
                declared_prepare,
                "backend prepare_session must be synchronous",
            ),
            (
                "callable_object",
                callable_prepare,
                "backend prepare_session must be synchronous",
            ),
            (
                "dynamic_result",
                dynamic_prepare_backend,
                "backend prepare_session() must be synchronous",
            ),
        )
        for index, (label, backend, expected_message) in enumerate(prepare_cases):
            with self.subTest(collaborator="prepare_session", form=label):
                workspace = initialized_workspace(self)
                before = lifecycle_file_snapshot(workspace)
                events: list[DebateProgressEvent] = []
                with self.assertRaises(BackendProtocolError) as raised:
                    await StoryRuntime(
                        workspace,
                        backend,
                        conversation_event_store=_ContextReadStore(empty),
                    ).run_draft(
                        f"async_prepare_{index}",
                        "candidate",
                        debate_id=f"async_prepare_debate_{index}",
                        conversation_context=DebateContextRequest(
                            conversation_id="conversation_a",
                            mode=DebateContextMode.FRESH,
                        ),
                        progress_sink=events.append,
                    )
                self.assertEqual(str(raised.exception), expected_message)
                self.assertEqual(events, [])
                self.assertEqual(backend.definitions, [])
                self.assertEqual(backend.requests, [])
                self.assertEqual(lifecycle_file_snapshot(workspace), before)

        dynamic_read_results = []
        dynamic_read_calls: list[tuple[str, str]] = []

        async def dynamic_read_values():
            yield empty

        def dynamic_read(session_id: str, conversation_id: str) -> object:
            dynamic_read_calls.append((session_id, conversation_id))
            result = dynamic_read_values()
            dynamic_read_results.append(result)
            return result

        declared_read = DeclaredReadStore(empty)
        callable_read = _ContextReadStore(empty)
        callable_read.read_context = AsyncGeneratorCallable(empty)  # type: ignore[method-assign]
        dynamic_read_store = _ContextReadStore(empty)
        dynamic_read_store.read_context = dynamic_read  # type: ignore[method-assign]
        read_cases = (
            (
                "declared_method",
                declared_read,
                "conversation event store read_context() must be synchronous",
            ),
            (
                "callable_object",
                callable_read,
                "conversation event store read_context() must be synchronous",
            ),
            (
                "dynamic_result",
                dynamic_read_store,
                "read_context() must be synchronous",
            ),
        )
        for index, (label, store, expected_cause) in enumerate(read_cases):
            with self.subTest(collaborator="read_context", form=label):
                workspace = initialized_workspace(self)
                before = lifecycle_file_snapshot(workspace)
                backend = ScriptedBackend(
                    lambda request: AssertionError(request.phase)
                )
                events: list[DebateProgressEvent] = []
                with self.assertRaisesRegex(
                    ValueError,
                    "conversation context capture must be synchronous",
                ) as raised:
                    await StoryRuntime(
                        workspace,
                        backend,
                        conversation_event_store=store,  # type: ignore[arg-type]
                    ).run_draft(
                        f"async_read_{index}",
                        "candidate",
                        debate_id=f"async_read_debate_{index}",
                        conversation_context=DebateContextRequest(
                            conversation_id="conversation_a",
                            mode=DebateContextMode.INCLUDED,
                        ),
                        progress_sink=events.append,
                    )
                self.assertIsInstance(
                    raised.exception.__cause__,
                    BackendProtocolError,
                )
                self.assertEqual(
                    str(raised.exception.__cause__),
                    expected_cause,
                )
                self.assertEqual(events, [])
                self.assertEqual(backend.definitions, [])
                self.assertEqual(backend.requests, [])
                self.assertEqual(lifecycle_file_snapshot(workspace), before)

        self.assertEqual(dynamic_prepare_calls, ["async_prepare_2"])
        self.assertEqual(
            dynamic_read_calls,
            [("async_read_2", "conversation_a")],
        )
        for result in (*dynamic_prepare_results, *dynamic_read_results):
            await result.aclose()

    async def test_sidecar_save_failure_leaves_only_best_effort_failed_state(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = ScriptedBackend(lambda request: AssertionError(request.phase))
        events: list[DebateProgressEvent] = []
        with (
            mock.patch.object(
                workspace,
                "save_debate_context",
                side_effect=OSError("sidecar persistence failed"),
            ) as save_context,
            mock.patch.object(
                workspace,
                "transition_debate",
                wraps=workspace.transition_debate,
            ) as transition,
            self.assertRaisesRegex(OSError, "sidecar persistence failed"),
        ):
            await StoryRuntime(workspace, backend).run_draft(
                "sidecar_session",
                "candidate",
                debate_id="sidecar_debate",
                conversation_context=DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.FRESH,
                ),
                progress_sink=events.append,
            )

        save_context.assert_called_once()
        transition.assert_called_once()
        inspected = workspace.inspect_debate("sidecar_session", "sidecar_debate")
        self.assertEqual(inspected["status"], "failed")
        self.assertTrue(inspected["terminal"])
        self.assertIsNone(inspected["result"])
        self.assertEqual(inspected["messages"], [])
        self.assertEqual(events, [])
        self.assertEqual(backend.requests, [])
        session_path = workspace.path("discussion_logs/sidecar_session")
        self.assertEqual(
            sorted(path.name for path in session_path.iterdir()),
            ["sidecar_debate.state.json"],
        )


class _CancellableAuditBackend(BindingBackendMixin):
    """Keep every audit child pending so external cancellation is observable."""

    def __init__(self) -> None:
        self.started: set[str] = set()
        self.cancelled: set[str] = set()
        self.all_started = asyncio.Event()
        self.blocker = asyncio.Event()

    async def invoke(self, request):
        if request.phase is not Phase.AUDIT:
            raise AssertionError(f"unexpected phase: {request.phase}")
        self.started.add(request.agent_id)
        if len(self.started) == len(SPECIALIST_IDS):
            self.all_started.set()
        try:
            await self.blocker.wait()
        except asyncio.CancelledError:
            self.cancelled.add(request.agent_id)
            raise
        return AgentReply.approval(request.revision_id)


class _BlockingAuditBindBackend:
    """Keep every specialist bind pending until its only waiter is cancelled."""

    def __init__(self) -> None:
        self.started: set[str] = set()
        self.cancelled: set[str] = set()
        self.active: set[str] = set()
        self.all_started = asyncio.Event()
        self.blocker = asyncio.Event()

    async def bind(self, definition):
        agent_id = definition.agent_id
        if agent_id not in SPECIALIST_IDS:
            raise AssertionError(f"unexpected binding: {agent_id}")
        self.started.add(agent_id)
        self.active.add(agent_id)
        if self.started == set(SPECIALIST_IDS):
            self.all_started.set()
        try:
            await self.blocker.wait()
        except asyncio.CancelledError:
            self.cancelled.add(agent_id)
            raise
        finally:
            self.active.discard(agent_id)
        raise AssertionError("blocking bind unexpectedly released")


class InterruptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_cancellation_joins_all_parallel_audit_bindings(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = _BlockingAuditBindBackend()
        runtime = StoryRuntime(workspace, backend)
        runtime_task = asyncio.create_task(
            runtime.run_draft(
                "bind_cancel_session",
                "candidate",
                debate_id="bind_cancel_debate",
                manuscript_name="must-not-exist.md",
            )
        )
        await asyncio.wait_for(backend.all_started.wait(), timeout=1)
        binding_tasks = tuple(runtime._binding_tasks.values())
        self.assertEqual(len(binding_tasks), len(SPECIALIST_IDS))

        runtime_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(runtime_task, timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(backend.started, set(SPECIALIST_IDS))
        self.assertEqual(backend.cancelled, set(SPECIALIST_IDS))
        self.assertEqual(backend.active, set())
        self.assertTrue(all(task.done() for task in binding_tasks))
        self.assertTrue(all(task.cancelled() for task in binding_tasks))
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(runtime._bound_agents, {})

    async def test_external_cancellation_is_terminal_durable_and_non_reusable(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)
        backend = _CancellableAuditBackend()
        runtime_task = asyncio.create_task(
            StoryRuntime(workspace, backend).run_draft(
                "cancel_session",
                "cancel-candidate",
                debate_id="cancel_debate",
                manuscript_name="cancelled.md",
            )
        )
        await backend.all_started.wait()
        runtime_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await runtime_task

        self.assertEqual(backend.started, set(SPECIALIST_IDS))
        self.assertEqual(backend.cancelled, set(SPECIALIST_IDS))
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])

        fresh = StoryRuntime(Workspace(workspace.root))
        inspected = fresh.inspect_debate("cancel_session", "cancel_debate")
        self.assertEqual(inspected["status"], "interrupted")
        self.assertTrue(inspected["terminal"])
        self.assertIsNone(inspected["result"])
        self.assertEqual(inspected["state"]["status"], "interrupted")
        self.assertTrue(inspected["state"]["terminal"])
        self.assertIn("CancelledError", inspected["state"]["error"])

        session = fresh.inspect_session("cancel_session")
        self.assertEqual(len(session["debates"]), 1)
        self.assertEqual(session["debates"][0]["debate_id"], "cancel_debate")
        self.assertEqual(session["debates"][0]["status"], "interrupted")
        self.assertTrue(session["debates"][0]["terminal"])

        def approve(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        retry_backend = ScriptedBackend(approve)
        with self.assertRaises(DebateExistsError):
            await StoryRuntime(workspace, retry_backend).run_draft(
                "cancel_session",
                "retry-candidate",
                debate_id="cancel_debate",
                manuscript_name="retry.md",
            )
        self.assertEqual(retry_backend.requests, [])

        new_result = await StoryRuntime(workspace, retry_backend).run_draft(
            "cancel_session",
            "new-candidate",
            debate_id="new_debate",
            manuscript_name="new.md",
        )
        self.assertEqual(new_result.status, DebateStatus.CONSENSUS)
        self.assertEqual(workspace.read_manuscript("new.md"), "new-candidate")

    async def test_successful_interrupted_observer_preserves_cancellation(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = _CancellableAuditBackend()
        events: list[DebateProgressEvent] = []
        runtime_task = asyncio.create_task(
            StoryRuntime(workspace, backend).run_draft(
                "observed_cancel",
                "candidate",
                debate_id="observed_cancel_debate",
                progress_sink=events.append,
            )
        )
        await asyncio.wait_for(backend.all_started.wait(), timeout=1)
        runtime_task.cancel("external cancellation")
        with self.assertRaises(asyncio.CancelledError) as raised:
            await runtime_task

        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(
            [event.phase for event in events],
            [
                DebateProgressPhase.STARTED,
                DebateProgressPhase.DRAFTED,
                DebateProgressPhase.AUDIT_STARTED,
                DebateProgressPhase.INTERRUPTED,
            ],
        )
        inspected = workspace.inspect_debate(
            "observed_cancel",
            "observed_cancel_debate",
        )
        self.assertEqual(inspected["status"], "interrupted")
        self.assertIsNone(inspected["result"])

    async def test_interrupted_sink_failure_is_the_cause_of_same_cancellation(
        self,
    ) -> None:
        cases = (
            ("keyboard_interrupt", KeyboardInterrupt("observer keyboard secret")),
            ("system_exit", SystemExit("observer exit secret")),
            (
                "cancelled_error",
                asyncio.CancelledError("observer cancellation secret"),
            ),
            ("invalid_return", None),
        )
        for label, observer_failure in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                backend = _CancellableAuditBackend()
                attempted: list[DebateProgressPhase] = []

                def observe(event: DebateProgressEvent) -> object:
                    attempted.append(event.phase)
                    if event.phase is DebateProgressPhase.INTERRUPTED:
                        if observer_failure is not None:
                            raise observer_failure
                        return object()
                    return None

                runtime_task = asyncio.create_task(
                    StoryRuntime(workspace, backend).run_draft(
                        f"cancel_sink_{label}",
                        "candidate",
                        debate_id=f"cancel_sink_{label}_debate",
                        progress_sink=observe,  # type: ignore[arg-type]
                    )
                )
                await asyncio.wait_for(backend.all_started.wait(), timeout=1)
                runtime_task.cancel(f"external {label}")
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await runtime_task

                observer_error = raised.exception.__cause__
                self.assertIsInstance(observer_error, ProgressObserverError)
                assert isinstance(observer_error, ProgressObserverError)
                self.assertFalse(hasattr(observer_error, "cause"))
                if observer_failure is not None:
                    self.assertIs(observer_error.__cause__, observer_failure)
                else:
                    self.assertIsInstance(observer_error.__cause__, TypeError)
                self.assertEqual(
                    attempted,
                    [
                        DebateProgressPhase.STARTED,
                        DebateProgressPhase.DRAFTED,
                        DebateProgressPhase.AUDIT_STARTED,
                        DebateProgressPhase.INTERRUPTED,
                    ],
                )
                inspected = workspace.inspect_debate(
                    f"cancel_sink_{label}",
                    f"cancel_sink_{label}_debate",
                )
                self.assertEqual(inspected["status"], "interrupted")
                self.assertIsNone(inspected["result"])


class MutationBoundaryTests(unittest.TestCase):
    def test_failure_after_manuscript_link_uses_mutation_callback_for_rollback(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        original_unlink = Path.unlink
        failed_once = False

        def fail_first_temporary_unlink(
            path: Path,
            *args,
            **kwargs,
        ) -> None:
            nonlocal failed_once
            if path.suffix == ".tmp" and not failed_once:
                failed_once = True
                raise OSError("simulated failure after manuscript link")
            original_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", new=fail_first_temporary_unlink):
            with self.assertRaisesRegex(OSError, "after manuscript link"):
                workspace.commit_approved_artifacts(
                    "link-boundary.md",
                    "candidate",
                    {},
                )

        self.assertTrue(failed_once)
        self.assertFalse(workspace.path("manuscript/link-boundary.md").exists())


if __name__ == "__main__":
    unittest.main()
