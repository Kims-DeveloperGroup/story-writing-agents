"""Failure isolation, protocol validation, and commit safety tests."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from story_writing_agents import (
    AgentReply,
    BackendProtocolError,
    ConfigurationError,
    ConversationContextWindow,
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateStatus,
    EXECUTION_SPECIALIST,
    Phase,
    PLOT_ARCHITECT,
    ProgressObserverError,
    SPECIALIST_IDS,
    StoryRuntime,
)
from story_writing_agents.errors import WorkspaceFormatError

from tests.helpers import (
    BindingBackendMixin,
    ScriptedBackend,
    all_manuscripts,
    core_snapshot,
    initialized_workspace,
)


def workspace_file_snapshot(workspace) -> dict[str, bytes]:
    """Capture every existing workspace file for preflight purity assertions."""

    return {
        path.relative_to(workspace.root).as_posix(): path.read_bytes()
        for path in sorted(workspace.root.rglob("*"))
        if path.is_file()
    }


class StaticEmptyContextStore:
    """Return an included-empty snapshot without conversation mutations."""

    def __init__(self) -> None:
        self.read_calls: list[tuple[str, str]] = []

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        self.read_calls.append((session_id, conversation_id))
        return ConversationContextWindow(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )

    def append(self, _event: object) -> object:
        raise AssertionError("debate execution must not append transcript events")

    def read(self, _session_id: str, _conversation_id: str) -> tuple[object, ...]:
        raise AssertionError("debate execution must not read transcript events")


class ArtifactSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_consensus_commits_exact_candidate_and_only_allowlisted_updates(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(
                    request.revision_id,
                    {"character_sheets.md": "updated-character-settings"},
                )
            raise AssertionError(f"unexpected phase: {request.phase}")

        result = await StoryRuntime(
            workspace,
            ScriptedBackend(respond),
        ).run_draft(
            "commit_session",
            "exact-approved-candidate",
            debate_id="commit_debate",
            output_path="part-01/approved.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(
            workspace.read_text("manuscript/part-01/approved.md"),
            "exact-approved-candidate",
        )
        self.assertEqual(result.final_candidate, "exact-approved-candidate")
        self.assertEqual(result.core_updates, ("character_sheets.md",))
        after = core_snapshot(workspace)
        self.assertEqual(
            after["character_sheets.md"],
            "updated-character-settings",
        )
        for filename in before:
            if filename != "character_sheets.md":
                with self.subTest(filename=filename):
                    self.assertEqual(after[filename], before[filename])

    async def test_non_allowlisted_core_update_fails_without_content_writes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(
                    request.revision_id,
                    {"not_allowlisted.md": "forbidden"},
                )
            raise AssertionError(f"unexpected phase: {request.phase}")

        result = await StoryRuntime(
            workspace,
            ScriptedBackend(respond),
        ).run_draft(
            "allowlist_session",
            "candidate",
            debate_id="allowlist_debate",
            output_path="part-01/must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("not allowlisted", result.error or "")
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])


class BackendFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_failures_and_invalid_bound_agents_fail_without_writes(
        self,
    ) -> None:
        class RaisingBackend(BindingBackendMixin):
            async def bind(self, definition):
                raise RuntimeError("provider bind unavailable")

        class MissingInvokeBackend(BindingBackendMixin):
            async def bind(self, definition):
                return object()

        class SyncInvokeAgent:
            def invoke(self, request):
                return AgentReply.approval(request.revision_id)

        class SyncInvokeBackend(BindingBackendMixin):
            async def bind(self, definition):
                return SyncInvokeAgent()

        cases = (
            (RaisingBackend(), "provider bind unavailable"),
            (MissingInvokeBackend(), "does not provide invoke"),
            (SyncInvokeBackend(), "bound invoke() must be async"),
        )
        for backend, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                workspace = initialized_workspace(self)
                before = core_snapshot(workspace)
                result = await StoryRuntime(workspace, backend).run_draft(
                    f"bind_failure_{len(expected_error)}",
                    "candidate",
                    debate_id=f"bind_failure_debate_{len(expected_error)}",
                    output_path=f"part-01/bind-failure-{len(expected_error)}.md",
                )

                self.assertEqual(result.status, DebateStatus.FAILED)
                self.assertIn(expected_error, result.error or "")
                self.assertEqual(core_snapshot(workspace), before)
                self.assertEqual(all_manuscripts(workspace), [])

    async def test_prompt_snapshot_survives_path_change_during_bind(
        self,
    ) -> None:
        class ValidBoundAgent:
            async def invoke(self, request):
                if request.phase is Phase.DRAFT:
                    return AgentReply.candidate(
                        request.revision_id,
                        request.user_payload,
                    )
                if request.phase is Phase.AUDIT:
                    return AgentReply.approval(request.revision_id)
                if request.phase is Phase.FINALIZE:
                    return AgentReply.finalization(request.revision_id)
                raise AssertionError(request.phase)

        class TamperingBackend(BindingBackendMixin):
            async def bind(self, definition):
                definition.system_prompt_file.path.write_text(
                    "tampered during bind",
                    encoding="utf-8",
                )
                return ValidBoundAgent()

        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)
        result = await StoryRuntime(
            workspace,
            TamperingBackend(),
        ).run_draft(
            "bind_tamper_session",
            "candidate",
            debate_id="bind_tamper_debate",
            output_path="part-01/must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(
            [path.name for path in all_manuscripts(workspace)],
            ["must-not-exist.md"],
        )

    async def test_backend_exception_returns_failed_without_content_writes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                if request.agent_id == PLOT_ARCHITECT:
                    return RuntimeError("provider unavailable")
                return AgentReply.approval(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        result = await StoryRuntime(
            workspace,
            ScriptedBackend(respond),
        ).run_draft(
            "backend_failure_session",
            "candidate",
            debate_id="backend_failure_debate",
            output_path="part-01/must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("provider unavailable", result.error or "")
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])

    async def test_wrong_revision_reply_is_protocol_failure_with_no_writes(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                revision = (
                    "wrong-revision"
                    if request.agent_id == PLOT_ARCHITECT
                    else request.revision_id
                )
                return AgentReply.approval(revision)
            raise AssertionError(f"unexpected phase: {request.phase}")

        result = await StoryRuntime(
            workspace,
            ScriptedBackend(respond),
        ).run_draft(
            "revision_failure_session",
            "candidate",
            debate_id="revision_failure_debate",
            output_path="part-01/must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("wrong-revision", result.error or "")
        self.assertIn("expected 'r0'", result.error or "")
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])

    async def test_raw_string_verdict_is_rejected_instead_of_committing(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        before = core_snapshot(workspace)

        def respond(request):
            if request.phase is Phase.AUDIT:
                if request.agent_id != PLOT_ARCHITECT:
                    return AgentReply.approval(request.revision_id)
                return AgentReply(
                    revision_id=request.revision_id,
                    content="malformed-conflict",
                    verdict="conflict",  # type: ignore[arg-type]
                )
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(f"unexpected phase: {request.phase}")

        result = await StoryRuntime(
            workspace,
            ScriptedBackend(respond),
        ).run_draft(
            "verdict_failure_session",
            "candidate",
            debate_id="verdict_failure_debate",
            output_path="part-01/must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("verdict", (result.error or "").lower())
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])


class FailureClassificationTests(unittest.IsolatedAsyncioTestCase):
    """Verify failure categories derive from trusted types and stages."""

    async def test_handled_failure_kinds_ignore_misleading_exception_prose(
        self,
    ) -> None:
        typed_cases = (
            (
                ConfigurationError("protocol artifact conflict context secret"),
                DebateFailureKind.PREFLIGHT,
            ),
            (
                BackendProtocolError("workspace internal context secret"),
                DebateFailureKind.PROTOCOL,
            ),
            (
                WorkspaceFormatError("preflight protocol context secret"),
                DebateFailureKind.WORKSPACE,
            ),
            (
                RuntimeError("preflight protocol artifact conflict secret"),
                DebateFailureKind.INTERNAL,
            ),
        )
        for index, (failure, expected_kind) in enumerate(typed_cases):
            with self.subTest(expected_kind=expected_kind):
                workspace = initialized_workspace(self)

                def respond(request, injected=failure):
                    if request.phase is Phase.AUDIT:
                        if request.agent_id == PLOT_ARCHITECT:
                            return injected
                        return AgentReply.approval(request.revision_id)
                    raise AssertionError(f"unexpected phase: {request.phase}")

                events: list[DebateProgressEvent] = []

                def observe(event: DebateProgressEvent) -> None:
                    if event.phase is DebateProgressPhase.FAILED:
                        inspected = workspace.inspect_debate(
                            f"typed_{index}",
                            f"typed_debate_{index}",
                        )
                        self.assertEqual(inspected["status"], "failed")
                        self.assertIsNotNone(inspected["result"])
                    events.append(event)

                result = await StoryRuntime(
                    workspace,
                    ScriptedBackend(respond),
                ).run_draft(
                    f"typed_{index}",
                    "candidate",
                    output_path=f"part-01/typed-{index}.md",
                    debate_id=f"typed_debate_{index}",
                    progress_sink=observe,
                )
                self.assertEqual(result.status, DebateStatus.FAILED)
                self.assertIs(events[-1].phase, DebateProgressPhase.FAILED)
                self.assertIs(events[-1].failure_kind, expected_kind)
                self.assertNotIn("secret", repr(events[-1]))

        workspace = initialized_workspace(self)
        store = StaticEmptyContextStore()
        backend = ScriptedBackend(lambda request: AssertionError(request.phase))
        runtime = StoryRuntime(
            workspace,
            backend,
            conversation_event_store=store,  # type: ignore[arg-type]
        )
        context_events: list[DebateProgressEvent] = []
        with mock.patch.object(
            runtime.prompts,
            "build_request",
            side_effect=ValueError("protocol artifact secret"),
        ):
            context_result = await runtime.run_draft(
                "context_failure",
                "candidate",
                output_path="part-01/context-failure.md",
                debate_id="context_failure_debate",
                conversation_context=DebateContextRequest(
                    conversation_id="conversation_a",
                    mode=DebateContextMode.INCLUDED,
                ),
                progress_sink=context_events.append,
            )
        self.assertEqual(context_result.status, DebateStatus.FAILED)
        self.assertIs(
            context_events[-1].failure_kind,
            DebateFailureKind.CONTEXT,
        )
        self.assertEqual(store.read_calls, [("context_failure", "conversation_a")])

        artifact_cases = (
            ("conflict", DebateFailureKind.ARTIFACT_CONFLICT),
            ("failure", DebateFailureKind.ARTIFACT_FAILURE),
        )
        for label, expected_kind in artifact_cases:
            with self.subTest(artifact=label):
                workspace = initialized_workspace(self)

                def approve(request):
                    if request.phase is Phase.AUDIT:
                        return AgentReply.approval(request.revision_id)
                    if request.phase is Phase.FINALIZE:
                        return AgentReply.finalization(request.revision_id)
                    raise AssertionError(request.phase)

                runtime = StoryRuntime(workspace, ScriptedBackend(approve))
                if label == "conflict":
                    workspace.path("manuscript/part-01/target.md").parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    workspace.path("manuscript/part-01/target.md").write_text(
                        "existing",
                        encoding="utf-8",
                    )
                    commit_context = mock.patch.object(
                        runtime.artifacts,
                        "commit_consensus",
                        wraps=runtime.artifacts.commit_consensus,
                    )
                else:
                    commit_context = mock.patch.object(
                        runtime.artifacts,
                        "commit_consensus",
                        side_effect=OSError("protocol conflict secret"),
                    )
                events = []
                with commit_context:
                    result = await runtime.run_draft(
                        f"artifact_{label}",
                        "candidate",
                        debate_id=f"artifact_{label}_debate",
                        output_path="part-01/target.md",
                        progress_sink=events.append,
                    )
                self.assertEqual(result.status, DebateStatus.FAILED)
                self.assertIs(events[-1].failure_kind, expected_kind)


class ProgressObserverTests(unittest.IsolatedAsyncioTestCase):
    """Verify synchronous observer validation and durability boundaries."""

    @staticmethod
    def approval_backend() -> ScriptedBackend:
        """Return an immediate-consensus backend for observer cases."""

        def respond(request):
            if request.phase is Phase.AUDIT:
                return AgentReply.approval(request.revision_id)
            if request.phase is Phase.FINALIZE:
                return AgentReply.finalization(request.revision_id)
            raise AssertionError(request.phase)

        return ScriptedBackend(respond)

    async def test_declared_async_and_noncallable_sinks_fail_before_mutation(
        self,
    ) -> None:
        async def async_sink(_event: DebateProgressEvent) -> None:
            return None

        async def async_generator_sink(_event: DebateProgressEvent):
            yield None

        class AsyncCallable:
            async def __call__(self, _event: DebateProgressEvent) -> None:
                return None

        class AsyncGeneratorCallable:
            async def __call__(self, _event: DebateProgressEvent):
                yield None

        sinks = (
            ("noncallable", object()),
            ("async_function", async_sink),
            ("async_callable", AsyncCallable()),
            ("async_generator_function", async_generator_sink),
            ("async_generator_callable", AsyncGeneratorCallable()),
        )
        for index, (label, sink) in enumerate(sinks):
            with self.subTest(sink=label):
                workspace = initialized_workspace(self)
                before = workspace_file_snapshot(workspace)
                backend = self.approval_backend()
                with self.assertRaises(TypeError):
                    await StoryRuntime(workspace, backend).run_draft(
                        f"sink_preflight_{index}",
                        "candidate",
                        output_path=f"part-01/sink-preflight-{index}.md",
                        debate_id=f"sink_preflight_debate_{index}",
                        conversation_context=DebateContextRequest(
                            conversation_id="conversation_a",
                            mode=DebateContextMode.FRESH,
                        ),
                        progress_sink=sink,  # type: ignore[arg-type]
                    )
                self.assertEqual(workspace_file_snapshot(workspace), before)
                self.assertEqual(backend.requests, [])

    async def test_preterminal_exception_and_dynamic_async_results_interrupt(
        self,
    ) -> None:
        observer_failure = RuntimeError("private observer prose")
        returned_coroutine = None
        returned_async_generator = None

        async def unused_awaitable() -> None:
            return None

        async def unused_async_generator():
            yield None

        def raising_sink(event: DebateProgressEvent) -> None:
            if event.phase is DebateProgressPhase.STARTED:
                raise observer_failure

        def non_none_sink(_event: DebateProgressEvent) -> object:
            return object()

        def awaitable_sink(_event: DebateProgressEvent) -> object:
            nonlocal returned_coroutine
            returned_coroutine = unused_awaitable()
            return returned_coroutine

        def async_generator_sink(_event: DebateProgressEvent) -> object:
            nonlocal returned_async_generator
            returned_async_generator = unused_async_generator()
            return returned_async_generator

        cases = (
            ("exception", raising_sink, RuntimeError, None),
            (
                "non_none",
                non_none_sink,
                TypeError,
                "debate progress sink must return None",
            ),
            (
                "awaitable",
                awaitable_sink,
                TypeError,
                "debate progress sink must be synchronous",
            ),
            (
                "async_generator",
                async_generator_sink,
                TypeError,
                "debate progress sink must return None",
            ),
        )
        for label, sink, cause_type, cause_text in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                backend = self.approval_backend()
                with self.assertRaises(ProgressObserverError) as raised:
                    await StoryRuntime(workspace, backend).run_draft(
                        f"observer_{label}",
                        "candidate",
                        output_path=f"part-01/observer-{label}.md",
                        debate_id=f"observer_{label}_debate",
                        progress_sink=sink,  # type: ignore[arg-type]
                    )
                self.assertFalse(hasattr(raised.exception, "cause"))
                self.assertIsInstance(raised.exception.__cause__, cause_type)
                if label == "exception":
                    self.assertIs(raised.exception.__cause__, observer_failure)
                elif cause_text is not None:
                    self.assertEqual(str(raised.exception.__cause__), cause_text)
                inspected = workspace.inspect_debate(
                    f"observer_{label}",
                    f"observer_{label}_debate",
                )
                self.assertEqual(inspected["status"], "interrupted")
                self.assertIsNone(inspected["result"])
                self.assertEqual(backend.requests, [])
                self.assertEqual(all_manuscripts(workspace), [])
        assert returned_coroutine is not None
        self.assertIsNone(returned_coroutine.cr_frame)
        assert returned_async_generator is not None
        await returned_async_generator.aclose()

    async def test_control_flow_sink_failures_wrap_at_started(self) -> None:
        for failure_type in (
            KeyboardInterrupt,
            SystemExit,
            asyncio.CancelledError,
        ):
            with self.subTest(failure=failure_type.__name__):
                workspace = initialized_workspace(self)
                backend = self.approval_backend()
                observer_failure = failure_type("started observer secret")
                attempted: list[DebateProgressPhase] = []

                def observe(event: DebateProgressEvent) -> None:
                    attempted.append(event.phase)
                    if event.phase is DebateProgressPhase.STARTED:
                        raise observer_failure

                with self.assertRaises(ProgressObserverError) as raised:
                    await StoryRuntime(workspace, backend).run_draft(
                        f"started_{failure_type.__name__.lower()}",
                        "candidate",
                        output_path=(
                            f"part-01/started-{failure_type.__name__.lower()}.md"
                        ),
                        debate_id=(
                            f"started_{failure_type.__name__.lower()}_debate"
                        ),
                        progress_sink=observe,
                    )

                self.assertIs(raised.exception.__cause__, observer_failure)
                self.assertEqual(attempted, [DebateProgressPhase.STARTED])
                inspected = workspace.inspect_debate(
                    f"started_{failure_type.__name__.lower()}",
                    f"started_{failure_type.__name__.lower()}_debate",
                )
                self.assertEqual(inspected["status"], "interrupted")
                self.assertTrue(inspected["terminal"])
                self.assertIsNone(inspected["result"])
                self.assertEqual(backend.requests, [])
                self.assertEqual(all_manuscripts(workspace), [])

    async def test_control_flow_sink_failures_wrap_after_artifact_commit(
        self,
    ) -> None:
        for failure_type in (
            KeyboardInterrupt,
            SystemExit,
            asyncio.CancelledError,
        ):
            with self.subTest(failure=failure_type.__name__):
                workspace = initialized_workspace(self)
                backend = self.approval_backend()
                label = failure_type.__name__.lower()
                observer_failure = failure_type("artifact observer secret")
                attempted: list[DebateProgressPhase] = []

                def observe(event: DebateProgressEvent) -> None:
                    attempted.append(event.phase)
                    if event.phase is DebateProgressPhase.ARTIFACTS_COMMITTED:
                        raise observer_failure

                with self.assertRaises(ProgressObserverError) as raised:
                    await StoryRuntime(workspace, backend).run_draft(
                        f"artifact_{label}",
                        "durable candidate",
                        debate_id=f"artifact_{label}_debate",
                        output_path=f"part-01/artifact-{label}.md",
                        progress_sink=observe,
                    )

                self.assertIs(raised.exception.__cause__, observer_failure)
                self.assertIs(
                    attempted[-1],
                    DebateProgressPhase.ARTIFACTS_COMMITTED,
                )
                inspected = workspace.inspect_debate(
                    f"artifact_{label}",
                    f"artifact_{label}_debate",
                )
                self.assertEqual(inspected["status"], "consensus")
                self.assertTrue(inspected["terminal"])
                self.assertEqual(inspected["result"]["status"], "consensus")
                self.assertEqual(
                    workspace.read_text(f"manuscript/part-01/artifact-{label}.md"),
                    "durable candidate",
                )

    async def test_postterminal_observer_failure_never_rewrites_durable_result(
        self,
    ) -> None:
        cases = (
            ("artifacts", DebateProgressPhase.ARTIFACTS_COMMITTED, "consensus"),
            ("consensus", DebateProgressPhase.CONSENSUS, "consensus"),
            ("deadlock", DebateProgressPhase.DEADLOCK, "deadlock"),
            ("failed", DebateProgressPhase.FAILED, "failed"),
        )
        for label, target_phase, expected_status in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                observer_failure = RuntimeError(f"observer-{label}-secret")
                attempted: list[DebateProgressPhase] = []

                def observe(event: DebateProgressEvent) -> None:
                    attempted.append(event.phase)
                    if event.phase is target_phase:
                        raise observer_failure

                def respond(request):
                    if request.phase is Phase.AUDIT:
                        if label == "deadlock" and request.agent_id == PLOT_ARCHITECT:
                            return AgentReply.conflict(request.revision_id, "unresolved")
                        if label == "failed" and request.agent_id == PLOT_ARCHITECT:
                            return RuntimeError("provider secret")
                        return AgentReply.approval(request.revision_id)
                    if request.phase is Phase.FINALIZE:
                        return AgentReply.finalization(request.revision_id)
                    raise AssertionError(request.phase)

                with self.assertRaises(ProgressObserverError) as raised:
                    await StoryRuntime(
                        workspace,
                        ScriptedBackend(respond),
                    ).run_draft(
                        f"postterminal_{label}",
                        "candidate",
                        debate_id=f"postterminal_{label}_debate",
                        output_path=f"part-01/postterminal-{label}.md",
                        max_debate_rounds=0 if label == "deadlock" else None,
                        progress_sink=observe,
                    )
                self.assertFalse(hasattr(raised.exception, "cause"))
                self.assertIs(raised.exception.__cause__, observer_failure)
                self.assertIs(attempted[-1], target_phase)
                inspected = workspace.inspect_debate(
                    f"postterminal_{label}",
                    f"postterminal_{label}_debate",
                )
                self.assertEqual(inspected["status"], expected_status)
                self.assertTrue(inspected["terminal"])
                self.assertEqual(inspected["result"]["status"], expected_status)
                if expected_status == "consensus":
                    self.assertTrue(
                    workspace.path(
                        f"manuscript/part-01/postterminal-{label}.md"
                    ).exists()
                    )
                else:
                    self.assertEqual(all_manuscripts(workspace), [])


class _FailFastBindBackend(BindingBackendMixin):
    """Fail one bind after all peers start and observe peer-bind cancellation."""

    def __init__(self) -> None:
        self.started: set[str] = set()
        self.cancelled: set[str] = set()
        self.active: set[str] = set()
        self.all_started = asyncio.Event()
        self.fail_now = asyncio.Event()
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
            await self.all_started.wait()
            await self.fail_now.wait()
            if agent_id == PLOT_ARCHITECT:
                raise RuntimeError("first specialist bind failed")
            await self.blocker.wait()
        except asyncio.CancelledError:
            self.cancelled.add(agent_id)
            raise
        finally:
            self.active.discard(agent_id)
        raise AssertionError("peer bind unexpectedly released")

    async def invoke(self, request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")


class _FailFastBackend(BindingBackendMixin):
    """One audit fails after all peers start; the peers wait for cancellation."""

    def __init__(self) -> None:
        self.arrived: set[str] = set()
        self.cancelled: set[str] = set()
        self.all_arrived = asyncio.Event()
        self.all_cancelled = asyncio.Event()
        self.release_uncancelled = asyncio.Event()

    async def invoke(self, request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, request.user_payload)
        if request.phase is not Phase.AUDIT:
            raise AssertionError(f"unexpected phase: {request.phase}")
        self.arrived.add(request.agent_id)
        if len(self.arrived) == len(SPECIALIST_IDS):
            self.all_arrived.set()
        await self.all_arrived.wait()

        if request.agent_id == PLOT_ARCHITECT:
            raise RuntimeError("first audit failed")
        try:
            await self.release_uncancelled.wait()
        except asyncio.CancelledError:
            self.cancelled.add(request.agent_id)
            if len(self.cancelled) == len(SPECIALIST_IDS) - 1:
                self.all_cancelled.set()
            raise
        return AgentReply.approval(request.revision_id)


class PeerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_bind_failure_cancels_and_joins_peer_bind_tasks(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        backend = _FailFastBindBackend()
        runtime = StoryRuntime(workspace, backend)

        debate_task = asyncio.create_task(
            runtime.run_draft(
                "bind_peer_failure_session",
                "candidate",
                debate_id="bind_peer_failure_debate",
                output_path="part-01/must-not-exist.md",
            )
        )
        await asyncio.wait_for(backend.all_started.wait(), timeout=1)
        binding_tasks = tuple(runtime._binding_tasks.values())
        expected_pending = set(SPECIALIST_IDS).difference(
            {EXECUTION_SPECIALIST}
        )
        self.assertEqual(len(binding_tasks), len(expected_pending))
        backend.fail_now.set()
        result = await asyncio.wait_for(debate_task, timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("first specialist bind failed", result.error or "")
        self.assertEqual(backend.started, expected_pending)
        self.assertEqual(
            backend.cancelled,
            expected_pending.difference({PLOT_ARCHITECT}),
        )
        self.assertEqual(backend.active, set())
        self.assertTrue(all(task.done() for task in binding_tasks))
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(len(runtime._bound_agents), 1)
        self.assertEqual(
            {key[0] for key in runtime._bound_agents},
            {EXECUTION_SPECIALIST},
        )
        self.assertEqual(all_manuscripts(workspace), [])

    async def test_first_audit_failure_cancels_unfinished_peer_tasks(self) -> None:
        workspace = initialized_workspace(self)
        backend = _FailFastBackend()
        debate_task = asyncio.create_task(
            StoryRuntime(workspace, backend).run_draft(
                "cancellation_session",
                "candidate",
                debate_id="cancellation_debate",
                output_path="part-01/must-not-exist.md",
            )
        )
        await asyncio.wait_for(backend.all_arrived.wait(), timeout=1)

        cancelled_promptly = True
        try:
            await asyncio.wait_for(backend.all_cancelled.wait(), timeout=0.25)
        except TimeoutError:
            cancelled_promptly = False
            backend.release_uncancelled.set()

        result = await asyncio.wait_for(debate_task, timeout=1)
        self.assertTrue(
            cancelled_promptly,
            "unfinished specialist calls were allowed to continue after failure",
        )
        self.assertEqual(
            backend.cancelled,
            set(SPECIALIST_IDS).difference({PLOT_ARCHITECT}),
        )
        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertEqual(all_manuscripts(workspace), [])


if __name__ == "__main__":
    unittest.main()
