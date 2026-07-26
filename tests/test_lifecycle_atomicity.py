"""Atomic debate reservation, interruption, and mutation-boundary tests."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest import mock

from story_writing_agents import (
    AgentReply,
    DebateExistsError,
    DebateResult,
    DebateStatus,
    Phase,
    SPECIALIST_IDS,
    StoryRuntime,
    Workspace,
)

from tests.helpers import (
    ScriptedBackend,
    all_manuscripts,
    core_snapshot,
    initialized_workspace,
)


class _GatedApprovalBackend:
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


class _CancellableAuditBackend:
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


class InterruptionTests(unittest.IsolatedAsyncioTestCase):
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
