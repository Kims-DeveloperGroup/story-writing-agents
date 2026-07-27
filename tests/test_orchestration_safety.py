"""Failure isolation, protocol validation, and commit safety tests."""

from __future__ import annotations

import asyncio
import unittest

from story_writing_agents import (
    AgentReply,
    DebateStatus,
    Phase,
    PLOT_ARCHITECT,
    SPECIALIST_IDS,
    StoryRuntime,
)

from tests.helpers import (
    BindingBackendMixin,
    ScriptedBackend,
    all_manuscripts,
    core_snapshot,
    initialized_workspace,
)


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
            manuscript_name="approved.md",
        )

        self.assertEqual(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(
            workspace.read_manuscript("approved.md"),
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
            manuscript_name="must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("not allowlisted", result.error or "")
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])


class BackendFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_bind_failures_and_invalid_bound_agents_fail_without_writes(
        self,
    ) -> None:
        class RaisingBackend:
            async def bind(self, definition):
                raise RuntimeError("provider bind unavailable")

        class MissingInvokeBackend:
            async def bind(self, definition):
                return object()

        class SyncInvokeAgent:
            def invoke(self, request):
                return AgentReply.approval(request.revision_id)

        class SyncInvokeBackend:
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
                    manuscript_name=f"bind-failure-{len(expected_error)}.md",
                )

                self.assertEqual(result.status, DebateStatus.FAILED)
                self.assertIn(expected_error, result.error or "")
                self.assertEqual(core_snapshot(workspace), before)
                self.assertEqual(all_manuscripts(workspace), [])

    async def test_prompt_file_changed_during_bind_fails_integrity_check(
        self,
    ) -> None:
        class ValidBoundAgent:
            async def invoke(self, request):
                return AgentReply.approval(request.revision_id)

        class TamperingBackend:
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
            manuscript_name="must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn(
            "changed after reference creation",
            result.error or "",
        )
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])

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
            manuscript_name="must-not-exist.md",
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
            manuscript_name="must-not-exist.md",
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
            manuscript_name="must-not-exist.md",
        )

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("verdict", (result.error or "").lower())
        self.assertEqual(core_snapshot(workspace), before)
        self.assertEqual(all_manuscripts(workspace), [])


class _FailFastBindBackend:
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
        self.started.add(agent_id)
        self.active.add(agent_id)
        if self.started == set(SPECIALIST_IDS):
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


class _FailFastBackend(BindingBackendMixin):
    """One audit fails after all peers start; the peers wait for cancellation."""

    def __init__(self) -> None:
        self.arrived: set[str] = set()
        self.cancelled: set[str] = set()
        self.all_arrived = asyncio.Event()
        self.all_cancelled = asyncio.Event()
        self.release_uncancelled = asyncio.Event()

    async def invoke(self, request):
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
                manuscript_name="must-not-exist.md",
            )
        )
        await asyncio.wait_for(backend.all_started.wait(), timeout=1)
        binding_tasks = tuple(runtime._binding_tasks.values())
        self.assertEqual(len(binding_tasks), len(SPECIALIST_IDS))
        backend.fail_now.set()
        result = await asyncio.wait_for(debate_task, timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(result.status, DebateStatus.FAILED)
        self.assertIn("first specialist bind failed", result.error or "")
        self.assertEqual(backend.started, set(SPECIALIST_IDS))
        self.assertEqual(
            backend.cancelled,
            set(SPECIALIST_IDS).difference({PLOT_ARCHITECT}),
        )
        self.assertEqual(backend.active, set())
        self.assertTrue(all(task.done() for task in binding_tasks))
        self.assertEqual(runtime._binding_tasks, {})
        self.assertEqual(runtime._binding_waiters, {})
        self.assertEqual(runtime._bound_agents, {})
        self.assertEqual(all_manuscripts(workspace), [])

    async def test_first_audit_failure_cancels_unfinished_peer_tasks(self) -> None:
        workspace = initialized_workspace(self)
        backend = _FailFastBackend()
        debate_task = asyncio.create_task(
            StoryRuntime(workspace, backend).run_draft(
                "cancellation_session",
                "candidate",
                debate_id="cancellation_debate",
                manuscript_name="must-not-exist.md",
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
