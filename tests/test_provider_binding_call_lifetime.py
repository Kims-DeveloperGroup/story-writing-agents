"""Existing provider binding lifetime scenarios."""

from __future__ import annotations

import asyncio
from unittest import mock

from story_writing_agents import (
    CHARACTER_CUSTODIAN,
    AgentReply,
    ConversationReply,
    ConversationRequest,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
)
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.provider_execution_workspace import ProviderExecutionWorkspace
from story_writing_agents.workspace.session_artifact_binding import SessionArtifactBindingError


class BindingCallLifetimeCases:
    """Binding scenarios attached to the existing Codex backend TestCase."""

    async def test_retirement_defers_exact_cleanup_for_each_bound_operation(
        self,
    ) -> None:
        """Every provider operation retains staging through its final await."""

        cases = ("invoke", "converse", "retrieval")
        for label in cases:
            with self.subTest(operation=label):
                session_id = f"retire_{label}"
                backend, bound = await self._provisioned_bound(session_id)
                staging = bound.staging_directory
                entered = asyncio.Event()
                proceed = asyncio.Event()
                observed_workspaces: list[ProviderExecutionWorkspace] = []

                async def provider(
                    *args: object,
                    reference_read: object | None = None,
                    execution_workspace: ProviderExecutionWorkspace | None = None,
                ) -> object:
                    self.assertIsNone(reference_read)
                    self.assertIsNotNone(execution_workspace)
                    assert execution_workspace is not None
                    observed_workspaces.append(execution_workspace)
                    execution_workspace.require_current()
                    entered.set()
                    await proceed.wait()
                    execution_workspace.require_current()
                    if label == "invoke":
                        return AgentReply.candidate("r0", "candidate")
                    request = args[1]
                    if label == "converse":
                        assert isinstance(request, ConversationRequest)
                        return ConversationReply(
                            request.session_id,
                            request.conversation_id,
                            request.turn_id,
                            "conversation",
                        )
                    assert isinstance(request, ConversationRetrievalRequest)
                    return ConversationRetrievalReply(
                        session_id=request.session_id,
                        conversation_id=request.conversation_id,
                        turn_id=request.turn_id,
                        content="retrieval",
                        sources=(),
                    )

                if label == "invoke":
                    provider_name = "_invoke_codex"

                    def call():
                        return bound.invoke(self.request(session_id=session_id))
                elif label == "converse":
                    provider_name = "_converse_codex"

                    def call():
                        return bound.converse(
                            self.conversation_request(session_id=session_id)
                        )
                else:
                    provider_name = "_converse_with_retrieval_codex"

                    def call():
                        return bound.converse_with_retrieval(
                            self.retrieval_request(session_id=session_id),
                            self.recording_retrieval_handler(),
                        )

                with (
                    mock.patch.object(backend, provider_name, side_effect=provider),
                    mock.patch.object(
                        backend,
                        "_finalize_bound_agent",
                        wraps=backend._finalize_bound_agent,
                    ) as finalize,
                ):
                    active = asyncio.create_task(call())
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    bound._retire()
                    self.assertTrue(staging.exists())
                    self.assertIn(bound, backend._bound_agents)
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        "retired",
                    ):
                        await call()
                    proceed.set()
                    await asyncio.wait_for(active, timeout=1)

                with self.assertRaises(SessionArtifactBindingError):
                    observed_workspaces[0].require_current()
                self.assertEqual(len(observed_workspaces), 1)

                self.assertFalse(staging.exists())
                self.assertNotIn(bound, backend._bound_agents)
                self.assertNotIn(staging, backend._staging_directories)
                self.assertEqual(finalize.call_count, 1)

    async def test_failure_and_cancellation_release_retired_staging_once(
        self,
    ) -> None:
        for operation in ("invoke", "converse", "retrieval"):
            for outcome in ("failure", "cancellation"):
                with self.subTest(operation=operation, outcome=outcome):
                    session_id = f"retire_{operation}_{outcome}"
                    backend, bound = await self._provisioned_bound(session_id)
                    staging = bound.staging_directory
                    entered = asyncio.Event()
                    proceed = asyncio.Event()

                    async def provider(*_args: object, reference_read: object | None = None,
                                       execution_workspace: ProviderExecutionWorkspace | None = None) -> object:
                        self.assertIsNone(reference_read)
                        self.assertIsInstance(execution_workspace, ProviderExecutionWorkspace)
                        execution_workspace.require_current()
                        entered.set()
                        await proceed.wait()
                        raise RuntimeError("provider failed")

                    if operation == "invoke":
                        provider_name = "_invoke_codex"

                        def call():
                            return bound.invoke(self.request(session_id=session_id))
                    elif operation == "converse":
                        provider_name = "_converse_codex"

                        def call():
                            return bound.converse(
                                self.conversation_request(session_id=session_id)
                            )
                    else:
                        provider_name = "_converse_with_retrieval_codex"

                        def call():
                            return bound.converse_with_retrieval(
                                self.retrieval_request(session_id=session_id),
                                self.recording_retrieval_handler(),
                            )

                    with (
                        mock.patch.object(
                            backend,
                            provider_name,
                            side_effect=provider,
                        ),
                        mock.patch.object(
                            backend,
                            "_finalize_bound_agent",
                            wraps=backend._finalize_bound_agent,
                        ) as finalize,
                    ):
                        active = asyncio.create_task(call())
                        await asyncio.wait_for(entered.wait(), timeout=1)
                        bound._retire()
                        self.assertTrue(staging.exists())
                        if outcome == "cancellation":
                            active.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await active
                        else:
                            proceed.set()
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "provider failed",
                            ):
                                await asyncio.wait_for(active, timeout=1)
                        bound._retire()

                    self.assertFalse(staging.exists())
                    self.assertEqual(finalize.call_count, 1)

    async def test_close_waits_for_active_binding_then_cleans_every_resource_once(
        self,
    ) -> None:
        backend = self._backend_with_fake_codex()
        session_id = "close_active_binding"
        self.create_provisioned_session_with_access(backend, session_id)
        active_bound = await backend.bind(self.definition())
        idle_bound = await backend.bind(self.definition(CHARACTER_CUSTODIAN))
        active_staging = active_bound.staging_directory
        idle_staging = idle_bound.staging_directory
        entered = asyncio.Event()
        proceed = asyncio.Event()

        async def provider(
            *_args: object,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> AgentReply:
            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            entered.set()
            await proceed.wait()
            return AgentReply.candidate("r0", "candidate")

        with (
            mock.patch.object(backend, "_invoke_codex", side_effect=provider),
            mock.patch.object(
                backend._codex_adapter,
                "release",
                wraps=backend._codex_adapter.release,
            ) as release_binding,
            mock.patch.object(
                backend._access_transactions,
                "release_access_claim",
                wraps=backend._access_transactions.release_access_claim,
            ) as release_access,
        ):
            task = asyncio.create_task(
                active_bound.invoke(self.request(session_id=session_id))
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            backend.close()
            backend.close()

            self.assertTrue(backend._closed)
            self.assertFalse(backend._close_cleanup_complete)
            self.assertTrue(active_staging.exists())
            self.assertFalse(idle_staging.exists())
            self.assertIn(session_id, backend._access_claim_tokens)
            self.assertTrue(backend._thread_locks)
            with self.assertRaisesRegex(BackendProtocolError, "closed"):
                await active_bound.invoke(self.request(session_id=session_id))

            proceed.set()
            await asyncio.wait_for(task, timeout=1)
            backend.close()

        self.assertTrue(backend._close_cleanup_complete)
        self.assertEqual(backend._bound_agents, set())
        self.assertEqual(backend._staging_directories, set())
        self.assertEqual(backend._thread_locks, {})
        self.assertEqual(backend._access_claim_tokens, {})
        self.assertFalse(active_staging.exists())
        self.assertEqual(
            [
                call.args[0].staging_directory
                for call in release_binding.call_args_list
            ].count(active_staging),
            1,
        )
        self.assertEqual(
            [
                call.args[0].staging_directory
                for call in release_binding.call_args_list
            ].count(idle_staging),
            1,
        )
        self.assertEqual(release_access.call_count, 1)

    async def test_terminal_backend_rejects_call_before_binding_retirement(
        self,
    ) -> None:
        """Close's terminal flag prevents entry during its retirement gap."""

        backend = self._backend_with_fake_codex()
        bound = await backend.bind(self.definition())
        staging = bound.staging_directory

        # Model the exact synchronous close gap: close has made the backend
        # terminal but has not yet reached this concrete binding in its loop.
        with bound.call_lifetime.lock:
            backend._closed = True
            self.assertFalse(bound.call_lifetime.retired)
            self.assertFalse(bound.call_lifetime.released)
            self.assertEqual(bound.call_lifetime.active_calls, 0)

        with mock.patch.object(backend, "_invoke_codex") as provider:
            with self.assertRaises(BackendProtocolError) as raised:
                await bound.invoke(self.request())

        self.assertEqual(str(raised.exception), "agent backend is closed")
        provider.assert_not_awaited()
        self.assertEqual(bound.call_lifetime.active_calls, 0)
        self.assertFalse(bound.call_lifetime.retired)
        self.assertFalse(bound.call_lifetime.released)
        self.assertIn(bound, backend._bound_agents)
        self.assertIn(staging, backend._staging_directories)
        self.assertTrue(staging.exists())

        # Complete the remainder of close. The inverse ordering—an entered
        # call surviving close until exit—is covered by the preceding test.
        bound._retire()
        self.assertTrue(bound.call_lifetime.retired)
        self.assertTrue(bound.call_lifetime.released)
        self.assertNotIn(bound, backend._bound_agents)
        self.assertNotIn(staging, backend._staging_directories)
        self.assertFalse(staging.exists())
        self.assertTrue(backend._close_cleanup_complete)
