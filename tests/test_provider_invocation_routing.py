"""Existing provider routing cases kept under the Codex canonical owner."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest import mock

from story_writing_agents import BoundAgent, PLOT_ARCHITECT
from story_writing_agents.errors import BackendProtocolError, ConfigurationError
from story_writing_agents.writing_artifacts import ReferenceAuthorizationErrorCode, ReferenceInvocationState
from tests.codex_native_process_fixture import _fresh_literal_object_side_effect
from tests.conversation_retrieval_wire_fixture import _RecordingRetrievalHandler


class ProviderInvocationRoutingDefinitions:
    async def test_reference_read_pre_provider_resource_failures_terminate_exactly(
        self,
    ) -> None:
        """Terminate real coordinators when no provider call can safely start."""

        for case in (
            "closed",
            "retired",
            "wrong_request",
            "unsupported",
            "lock_cancel",
        ):
            with self.subTest(case=case):
                self.log_path.unlink(missing_ok=True)
                session_id = f"reference_resource_{case}"
                operation_id = f"resource_operation_{case}"
                backend = self._session_backend(session_id)
                bound = await backend.bind(self.definition())
                repository, authorization, invocation = self.reference_invocation(
                    operation_id=operation_id,
                    invocation_id=f"resource_invocation_{case}",
                )
                expected = ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                if case == "closed":
                    backend.close()
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        "agent backend is closed",
                    ):
                        await bound.invoke(
                            self.request(session_id=session_id),
                            reference_read=invocation,
                        )
                elif case == "retired":
                    bound._retire()
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        "bound agent is retired",
                    ):
                        await bound.invoke(
                            self.request(session_id=session_id),
                            reference_read=invocation,
                        )
                elif case == "wrong_request":
                    with self.assertRaisesRegex(TypeError, "AgentRequest"):
                        await bound.invoke(  # type: ignore[arg-type]
                            object(),
                            reference_read=invocation,
                        )
                elif case == "unsupported":
                    expected = ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
                    unsupported = BoundAgent(
                        backend,
                        replace(self.definition(), backend="gemini"),
                        bound.staging_directory,
                    )
                    with self.assertRaisesRegex(
                        ConfigurationError,
                        "not implemented: gemini",
                    ):
                        await unsupported.invoke(
                            self.request(session_id=session_id),
                            reference_read=invocation,
                        )
                else:
                    expected = ReferenceAuthorizationErrorCode.CANCELLED
                    attempted = asyncio.Event()
                    never = asyncio.Event()

                    class BlockingLock:
                        async def __aenter__(self):
                            attempted.set()
                            await never.wait()

                        async def __aexit__(self, *_args: object) -> None:
                            return None

                    with mock.patch.object(
                        backend,
                        "_thread_lock",
                        return_value=BlockingLock(),
                    ):
                        task = asyncio.create_task(
                            bound.invoke(
                                self.request(session_id=session_id),
                                reference_read=invocation,
                            )
                        )
                        await attempted.wait()
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

                self.assertEqual(self.provider_invocations(), [])
                history = repository.inspect_reference_operation(operation_id)
                assert history is not None
                terminal = history.invocation_events[0][-1]
                self.assertIs(
                    terminal.invocation_state, ReferenceInvocationState.TERMINATED
                )
                self.assertIs(terminal.outcome, expected)
                authorization.close()
                backend.close()

    async def test_reference_read_adapter_resolution_failures_terminate_once(
        self,
    ) -> None:
        """Retire conversation/retrieval coordinators without an adapter."""

        operations = (
            ("conversation", "conversation"),
            ("retrieval", "conversation_retrieval"),
        )
        for operation, phase in operations:
            for resolution in ("unsupported", "missing"):
                with self.subTest(operation=operation, resolution=resolution):
                    self.log_path.unlink(missing_ok=True)
                    session_id = f"reference_{operation}_{resolution}"
                    operation_id = f"adapter_operation_{operation}_{resolution}"
                    backend = self._session_backend(session_id)
                    bound = await backend.bind(self.definition())
                    call_bound = bound
                    vendor = "codex"
                    if resolution == "unsupported":
                        vendor = "gemini"
                        call_bound = BoundAgent(
                            backend,
                            replace(self.definition(), backend=vendor),
                            bound.staging_directory,
                        )
                    repository, authorization, invocation = self.reference_invocation(
                        operation_id=operation_id,
                        invocation_id=(f"adapter_invocation_{operation}_{resolution}"),
                        phase=phase,
                        round_number=None,
                    )
                    handler = _RecordingRetrievalHandler()
                    removed_adapter = None
                    if resolution == "missing":
                        removed_adapter = backend._adapters.pop(vendor)

                    resolved_failure = None
                    real_adapter_for = backend._adapter_for

                    def resolve_adapter(adapter_vendor: str):
                        nonlocal resolved_failure
                        try:
                            return real_adapter_for(adapter_vendor)
                        except ConfigurationError as exc:
                            resolved_failure = exc
                            raise

                    try:
                        with (
                            _fresh_literal_object_side_effect(backend, '_adapter_for', resolve_adapter) as adapter_for,
                            self.assertRaises(ConfigurationError) as raised,
                        ):
                            if operation == "conversation":
                                await call_bound.converse(
                                    self.conversation_request(
                                        session_id=session_id,
                                    ),
                                    reference_read=invocation,
                                )
                            else:
                                await call_bound.converse_with_retrieval(
                                    self.retrieval_request(
                                        session_id=session_id,
                                    ),
                                    handler,
                                    reference_read=invocation,
                                )
                    finally:
                        if removed_adapter is not None:
                            backend._adapters[vendor] = removed_adapter

                    adapter_for.assert_called_once_with(vendor)
                    self.assertIs(raised.exception, resolved_failure)
                    self.assertEqual(
                        str(raised.exception),
                        f"agent backend is not implemented: {vendor}",
                    )
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertIsNone(raised.exception.__context__)
                    self.assertEqual(self.provider_invocations(), [])
                    self.assertEqual(handler.actions, [])
                    history = repository.inspect_reference_operation(operation_id)
                    assert history is not None
                    events = history.invocation_events[0]
                    self.assertEqual(
                        tuple(event.invocation_state for event in events),
                        (
                            ReferenceInvocationState.AUTHORIZED,
                            ReferenceInvocationState.TERMINATED,
                        ),
                    )
                    self.assertIs(
                        events[-1].outcome,
                        ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                    )
                    lock = backend._thread_lock(
                        session_id,
                        vendor,
                        PLOT_ARCHITECT,
                    )
                    self.assertFalse(lock.locked())
                    self.assertIn(bound, backend._bound_agents)
                    self.assertTrue(bound.staging_directory.exists())

                    authorization.close()
                    backend.close()
                    self.assertEqual(backend._bound_agents, set())
                    self.assertFalse(bound.staging_directory.exists())
