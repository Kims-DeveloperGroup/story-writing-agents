"""Atomic debate reservation, interruption, and mutation-boundary tests."""

from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import ExitStack
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
    EXECUTION_SPECIALIST,
    Phase,
    ProgressObserverError,
    SPECIALIST_IDS,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    StoryRuntime,
    Workspace,
    WorkspaceFormatError,
)
from story_writing_agents.debug_log import WorkspaceDebugLog


def lifecycle_file_snapshot(workspace: Workspace) -> dict[str, bytes]:
    """Capture domain files for exact pre-reservation mutation checks."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
        and "debug_logs" not in path.relative_to(workspace.root).parts
    }


class _ContextReadStore:
    """Record or fail the one synchronous debate context read."""

    def __init__(
        self,
        outcome: object,
        *,
        trace: list[str] | None = None,
    ) -> None:
        self.outcome = outcome
        self.trace = trace
        self.calls: list[tuple[str, str]] = []

    def read_context(self, session_id: str, conversation_id: str) -> object:
        if self.trace is not None:
            self.trace.append("read_context")
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


class _TracingLifecycleBackend(ScriptedBackend):
    """Record the exact access sequence and optionally fail acquisition."""

    def __init__(
        self,
        trace: list[str],
        *,
        acquire_failure: BaseException | None = None,
    ) -> None:
        super().__init__(lambda request: AssertionError(request.phase))
        self.trace = trace
        self.acquire_failure = acquire_failure

    def validate_provider_configuration(self, session_id: str) -> bool:
        self.trace.append("validate_provider_configuration")
        return super().validate_provider_configuration(session_id)

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("get_session_lifecycle")
        return super().get_session_lifecycle(session_id)

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("acquire_session_access")
        if self.acquire_failure is not None:
            raise self.acquire_failure
        return super().acquire_session_access(session_id)

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        self.trace.append("release_session_access")
        return super().release_session_access(session_id)


class _GatedApprovalBackend(BindingBackendMixin):
    """Hold the winning runtime inside its backend until collision is observed."""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
        if request.phase is Phase.AUDIT:
            self.entered.set()
            await self.release.wait()
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


class DebateReservationTests(unittest.IsolatedAsyncioTestCase):
    def assert_consensus_failure_records(
        self,
        workspace: Workspace,
        *,
        session_id: str,
        debate_id: str,
        prohibited: tuple[str, ...],
    ) -> None:
        """Require one started record and one content-free terminal error."""

        raw = workspace.path("debug_logs/runtime.jsonl").read_text(
            encoding="utf-8"
        )
        records = [
            record
            for record in (json.loads(line) for line in raw.splitlines())
            if record["component"] == "runtime"
            and record["operation"] == "consensus_run"
        ]
        self.assertEqual(
            [
                (record["level"], record["phase"], record.get("outcome"))
                for record in records
            ],
            [
                ("trace", "started", None),
                ("error", "failed", "failed"),
            ],
        )
        for record in records:
            self.assertEqual(record["session_id"], session_id)
            self.assertEqual(record["debate_id"], debate_id)
        self.assertEqual(
            [record["level"] for record in records if record["phase"] != "started"],
            ["error"],
        )
        for value in prohibited + (str(workspace.root),):
            self.assertNotIn(value, raw)

    def assert_no_debate_artifact(
        self,
        workspace: Workspace,
        session_id: str,
        debate_id: str,
    ) -> None:
        """Assert the failed reservation boundary left no durable debate file."""

        for suffix in (
            ".jsonl",
            ".state.json",
            ".result.json",
            ".context.json",
        ):
            self.assertFalse(
                workspace.path(
                    f"discussion_logs/{session_id}/{debate_id}{suffix}"
                ).exists()
            )

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
                    output_path="part-01/atomic.md",
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
            workspace.read_text("manuscript/part-01/atomic.md"),
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
                "candidate",
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

        class ConfigurationFailureBackend(ScriptedBackend):
            def validate_provider_configuration(
                self,
                _session_id: str,
            ) -> bool:
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
                ConfigurationFailureBackend(
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
                        output_path=f"part-01/pre-{index}.md",
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
                output_path="part-01/collision.md",
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

    async def assert_pre_reservation_guard(
        self,
        stage: str,
        *,
        diagnostic_failure: BaseException | None = None,
    ) -> None:
        """Exercise one guarded failure with exact ordering and no artifacts."""

        workspace = initialized_workspace(self)
        before = lifecycle_file_snapshot(workspace)
        trace: list[str] = []
        failures: dict[str, BaseException] = {
            "access": RuntimeError("PRIVATE_ACCESS_FAILURE"),
            "context": WorkspaceFormatError("PRIVATE_CONTEXT_FAILURE"),
            "reservation": OSError("PRIVATE_RESERVATION_FAILURE"),
        }
        failure = failures[stage]
        failure_message = str(failure)
        backend = _TracingLifecycleBackend(
            trace,
            acquire_failure=failure if stage == "access" else None,
        )
        store = (
            _ContextReadStore(failure, trace=trace)
            if stage == "context"
            else None
        )
        session_id = f"{stage}_guard_session"
        debate_id = f"{stage}_guard_debate"
        private_input = f"PRIVATE_{stage.upper()}_INPUT"
        events: list[DebateProgressEvent] = []

        def fail_reservation(*_args: object, **_kwargs: object) -> object:
            trace.append("reserve_debate")
            raise failure

        reservation_patch = (
            mock.patch.object(
                workspace,
                "reserve_debate",
                side_effect=fail_reservation,
            )
            if stage == "reservation"
            else mock.patch.object(
                workspace,
                "reserve_debate",
                wraps=workspace.reserve_debate,
            )
        )
        expected_type = ValueError if stage == "context" else type(failure)
        with ExitStack() as stack:
            reservation = stack.enter_context(reservation_patch)
            transition = stack.enter_context(
                mock.patch.object(
                    workspace,
                    "transition_debate",
                    wraps=workspace.transition_debate,
                )
            )
            debug_error = (
                stack.enter_context(
                    mock.patch.object(
                        WorkspaceDebugLog,
                        "error",
                        side_effect=diagnostic_failure,
                    )
                )
                if diagnostic_failure is not None
                else None
            )
            raised = stack.enter_context(self.assertRaises(expected_type))
            await StoryRuntime(
                workspace,
                backend,
                conversation_event_store=store,  # type: ignore[arg-type]
            ).run_draft(
                session_id,
                private_input,
                output_path=f"part-01/guard-{stage}.md",
                debate_id=debate_id,
                conversation_context=(
                    DebateContextRequest(
                        conversation_id="context_guard_conversation",
                        mode=DebateContextMode.INCLUDED,
                    )
                    if stage == "context"
                    else None
                ),
                progress_sink=events.append,
            )

        if stage == "context":
            self.assertIs(type(raised.exception), ValueError)
            self.assertEqual(
                str(raised.exception),
                "conversation context capture is invalid",
            )
            self.assertIs(raised.exception.__cause__, failure)
            self.assertIs(raised.exception.__context__, failure)
            self.assertEqual(str(failure), failure_message)
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
            assert store is not None
            self.assertEqual(
                store.calls,
                [(session_id, "context_guard_conversation")],
            )
        else:
            self.assertIs(raised.exception, failure)
            self.assertIs(type(raised.exception), type(failure))
            self.assertEqual(str(raised.exception), failure_message)
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

        if stage == "context":
            expected_trace = ["read_context"]
        else:
            expected_trace = [
                "validate_provider_configuration",
                "get_session_lifecycle",
                "acquire_session_access",
            ]
            if stage == "reservation":
                expected_trace.extend(
                    ("get_session_lifecycle", "reserve_debate")
                )
        self.assertEqual(trace, expected_trace)
        self.assertEqual(events, [])
        self.assertEqual(backend.requests, [])
        if stage == "reservation":
            reservation.assert_called_once()
        else:
            reservation.assert_not_called()
        transition.assert_not_called()
        self.assert_no_debate_artifact(workspace, session_id, debate_id)
        self.assertEqual(lifecycle_file_snapshot(workspace), before)

        if diagnostic_failure is None:
            self.assert_consensus_failure_records(
                workspace,
                session_id=session_id,
                debate_id=debate_id,
                prohibited=(private_input, failure_message),
            )
        else:
            assert debug_error is not None
            debug_error.assert_called_once()
            self.assertIs(debug_error.call_args.args[2], raised.exception)

    async def test_guarded_pre_reservation_failures_record_once(self) -> None:
        for stage in ("access", "context", "reservation"):
            with self.subTest(stage=stage):
                await self.assert_pre_reservation_guard(stage)

    async def test_reservation_failure_survives_diagnostic_system_exit(self) -> None:
        await self.assert_pre_reservation_guard(
            "reservation",
            diagnostic_failure=SystemExit("diagnostic exit"),
        )

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
                output_path="part-01/format-context.md",
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

        class DeclaredLifecycleBackend(ScriptedBackend):
            async def validate_provider_configuration(self, _session_id: str):
                yield None

        class DeclaredReadStore(_ContextReadStore):
            async def read_context(
                self,
                _session_id: str,
                _conversation_id: str,
            ):
                yield empty

        dynamic_lifecycle_results = []
        dynamic_lifecycle_calls: list[str] = []

        async def dynamic_lifecycle_values():
            yield None

        def dynamic_lifecycle(session_id: str) -> object:
            dynamic_lifecycle_calls.append(session_id)
            result = dynamic_lifecycle_values()
            dynamic_lifecycle_results.append(result)
            return result

        declared_lifecycle = DeclaredLifecycleBackend(
            lambda request: AssertionError(request.phase)
        )
        callable_lifecycle = ScriptedBackend(
            lambda request: AssertionError(request.phase)
        )
        callable_lifecycle.validate_provider_configuration = (  # type: ignore[method-assign]
            AsyncGeneratorCallable(None)
        )
        dynamic_lifecycle_backend = ScriptedBackend(
            lambda request: AssertionError(request.phase)
        )
        dynamic_lifecycle_backend.validate_provider_configuration = (  # type: ignore[method-assign]
            dynamic_lifecycle
        )
        lifecycle_cases = (
            (
                "declared_method",
                declared_lifecycle,
                "backend session lifecycle hooks must be synchronous",
            ),
            (
                "callable_object",
                callable_lifecycle,
                "backend session lifecycle hooks must be synchronous",
            ),
            (
                "dynamic_result",
                dynamic_lifecycle_backend,
                "backend validate_provider_configuration() must be synchronous",
            ),
        )
        for index, (label, backend, expected_message) in enumerate(lifecycle_cases):
            with self.subTest(collaborator="lifecycle", form=label):
                workspace = initialized_workspace(self)
                before = lifecycle_file_snapshot(workspace)
                events: list[DebateProgressEvent] = []
                with self.assertRaises(BackendProtocolError) as raised:
                    await StoryRuntime(
                        workspace,
                        backend,
                        conversation_event_store=_ContextReadStore(empty),
                    ).run_draft(
                        f"async_lifecycle_{index}",
                        "candidate",
                        output_path=f"part-01/async-lifecycle-{index}.md",
                        debate_id=f"async_lifecycle_debate_{index}",
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
                        output_path=f"part-01/async-read-{index}.md",
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

        self.assertEqual(dynamic_lifecycle_calls, ["async_lifecycle_2"])
        self.assertEqual(
            dynamic_read_calls,
            [("async_read_2", "conversation_a")],
        )
        for result in (*dynamic_lifecycle_results, *dynamic_read_results):
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
                output_path="part-01/sidecar.md",
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
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
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


class _BlockingAuditBindBackend(BindingBackendMixin):
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
        if agent_id == EXECUTION_SPECIALIST:
            return await super().bind(definition)
        self.started.add(agent_id)
        self.active.add(agent_id)
        expected = set(SPECIALIST_IDS).difference({EXECUTION_SPECIALIST})
        if self.started == expected:
            self.all_started.set()
        try:
            await self.blocker.wait()
        except asyncio.CancelledError:
            self.cancelled.add(agent_id)
            raise
        finally:
            self.active.discard(agent_id)
        raise AssertionError("blocking bind unexpectedly released")

    async def invoke(self, request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


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
                output_path="part-01/must-not-exist.md",
            )
        )
        await asyncio.wait_for(backend.all_started.wait(), timeout=1)
        binding_tasks = tuple(runtime._binding_tasks.values())
        expected_pending = set(SPECIALIST_IDS).difference(
            {EXECUTION_SPECIALIST}
        )
        self.assertEqual(len(binding_tasks), len(expected_pending))

        runtime_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(runtime_task, timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(backend.started, expected_pending)
        self.assertEqual(backend.cancelled, expected_pending)
        self.assertEqual(backend.active, set())
        self.assertTrue(all(task.done() for task in binding_tasks))
        self.assertTrue(all(task.cancelled() for task in binding_tasks))
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(len(runtime._bound_agents), 1)
        self.assertEqual(
            {key[0] for key in runtime._bound_agents},
            {EXECUTION_SPECIALIST},
        )
        self.assertEqual(runtime._retiring_bound_agents, {})
        self.assertEqual(runtime._bound_agent_uses, {})

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
                output_path="part-01/cancelled.md",
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
                output_path="part-01/retry.md",
            )
        self.assertEqual(retry_backend.requests, [])

        new_result = await StoryRuntime(workspace, retry_backend).run_draft(
            "cancel_session",
            "new-candidate",
            debate_id="new_debate",
            output_path="part-01/new.md",
        )
        self.assertEqual(new_result.status, DebateStatus.CONSENSUS)
        self.assertEqual(
            workspace.read_text("manuscript/part-01/new.md"),
            "new-candidate",
        )

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
                output_path="part-01/observed-cancel.md",
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
                        output_path=f"part-01/cancel-sink-{label}.md",
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


if __name__ == "__main__":
    unittest.main()
