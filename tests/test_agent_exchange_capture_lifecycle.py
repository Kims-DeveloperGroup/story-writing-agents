"""Provider exchange capture lifecycle tests."""
from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from story_writing_agents.agent_context import ProviderCallKind
from story_writing_agents.agent_context.invocation import provider as ctx_provider
from story_writing_agents.agent_exchanges import AgentExchangeProvider, AgentExchangeRequestType, AgentExchangeScopeKind
from story_writing_agents.agent_exchanges.capture import AgentExchangeTerminalCode
from story_writing_agents.agent_exchanges.provider_finalization import ContextExchangeFinalizer
from story_writing_agents.agents import ROSTER
from tests.agent_exchange_capture_fixture import (
    _EXCHANGE_ID, _RecordingStore, _begin, _capture, _decoded, _invocation, _result, _started_capture,
)


class AgentExchangeCaptureLifecycleTests(unittest.TestCase):
    def test_all_response_terminal_outcomes_are_exact_and_share_identity(self) -> None:
        validation_detail = "$.action.result.message: expected nonempty text"
        cases = (
            ("accepted", None, "accepted", "accepted"),
            ("rejected", AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
             "rejected", "malformed_structured_response"),
            (
                "rejected",
                AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                "rejected",
                "response_correlation_mismatch",),
            (
                "rejected",
                AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED,
                "rejected",
                "response_semantic_rejected",
            ),
            ("rejected", AgentExchangeTerminalCode.ACTION_EXHAUSTED,
             "rejected", "action_exhausted"),
            ("rejected", AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED,
             "rejected", "answer_validation_failed"),
            (
                "post_response_runtime_failure",
                AgentExchangeTerminalCode.THREAD_PERSISTENCE_FAILED,
                "post_response_runtime_failure",
                "thread_persistence_failed",
            ),
            (
                "post_response_runtime_failure",
                AgentExchangeTerminalCode.REFERENCE_RUNTIME_FAILED,
                "post_response_runtime_failure",
                "reference_runtime_failed",
            ),
            (
                "post_response_runtime_failure",
                AgentExchangeTerminalCode.CONTEXT_RUNTIME_FAILED,
                "post_response_runtime_failure",
                "context_runtime_failed",
            ),
        )
        for method_name, code, outcome, expected_code in cases:
            with self.subTest(code=expected_code):
                store, lifecycle = _started_capture(diagnostic_call_id="physical-call-1")
                lifecycle.record_response(_result())
                method = getattr(lifecycle, method_name)
                if code is AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED:
                    finalizer = ContextExchangeFinalizer(lifecycle)
                    finalizer.set_validation_details((validation_detail,))
                    finalizer.set_host_task_diagnostic("TASK-must-not-publish", 1)
                    finalizer.rejected(ctx_provider.ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED)
                else:
                    method() if code is None else method(code)
                self.assertEqual(
                    [record.request_type for record in store.records],
                    [
                        AgentExchangeRequestType.REQUEST,
                        AgentExchangeRequestType.RESPONSE,
                        AgentExchangeRequestType.OUTCOME,
                    ],
                )
                self.assertEqual(
                    {record.exchange_id for record in store.records},
                    {lifecycle.exchange_id},)
                self.assertEqual(
                    _decoded(store.records)[2],
                    {"code": expected_code, "outcome": outcome, "host_diagnostics": {"call_id": "physical-call-1"}} | (
                        {"validation_details": [validation_detail]}
                        if code is AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED else {}
                    ),
                )

    def test_no_response_failures_and_cancellation_matrix_are_exact(self) -> None:
        for code in (
            AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED,
            AgentExchangeTerminalCode.PROVIDER_AUTHENTICATION_REQUIRED,
            AgentExchangeTerminalCode.PROVIDER_TIMED_OUT,
            AgentExchangeTerminalCode.PROVIDER_CONTRACT_INVALID,
        ):
            with self.subTest(code=code):
                store, lifecycle = _started_capture(diagnostic_call_id="physical-call-1")
                lifecycle.provider_failure(code)
                self.assertEqual(
                    [record.request_type for record in store.records],
                    [
                        AgentExchangeRequestType.REQUEST,
                        AgentExchangeRequestType.RESPONSE,
                        AgentExchangeRequestType.OUTCOME,
                    ],
                )
                self.assertEqual(
                    _decoded(store.records)[1],
                    {"provider_failure": True},)
                self.assertEqual(
                    _decoded(store.records)[2],
                    {"code": code.value, "outcome": "provider_failure", "host_diagnostics": {"call_id": "physical-call-1"}},)

        for after_response in (False, True):
            with self.subTest(after_response=after_response):
                store, lifecycle = _started_capture(diagnostic_call_id="physical-call-1")
                if after_response:
                    lifecycle.record_response(_result())
                lifecycle.cancelled()
                self.assertEqual(
                    _decoded(store.records)[-1],
                    {"code": "cancelled", "outcome": "cancelled", "host_diagnostics": {"call_id": "physical-call-1"}},)

    def test_invalid_transitions_are_noops_and_valid_transition_remains_available(
        self,
    ) -> None:
        store, lifecycle = _started_capture()
        lifecycle.accepted()
        lifecycle.rejected(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)
        lifecycle.post_response_runtime_failure(
            AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED)
        self.assertEqual(len(store.attempts), 1)
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_TIMED_OUT)
        self.assertEqual(len(store.attempts), 3)
        self.assertEqual(
            [record.request_type for record in store.records],
            [
                AgentExchangeRequestType.REQUEST,
                AgentExchangeRequestType.RESPONSE,
                AgentExchangeRequestType.OUTCOME,
            ],
        )
        self.assertEqual(_decoded(store.records)[1], {"provider_failure": True})
        self.assertEqual(
            _decoded(store.records)[2],
            {"code": "provider_timed_out", "outcome": "provider_failure"},)
        terminal_attempts = tuple(store.attempts)
        terminal_records = tuple(store.records)
        lifecycle.cancelled()
        lifecycle.record_response(_result())
        self.assertEqual(tuple(store.attempts), terminal_attempts)
        self.assertEqual(tuple(store.records), terminal_records)

    def test_response_and_terminal_are_attempted_at_most_once_before_append(
        self,
    ) -> None:
        response_store = _RecordingStore()
        response_store.failures[2] = KeyboardInterrupt("PRIVATE_RESPONSE_STORE")
        response = _begin(response_store)
        response.record_response(_result())
        response.record_response(_result())
        response.accepted()
        response.accepted()
        self.assertEqual(len(response_store.attempts), 3)
        self.assertEqual(
            response_store.attempts[-1].request_type,
            AgentExchangeRequestType.OUTCOME,)

        terminal_store = _RecordingStore()
        terminal = _begin(terminal_store)
        terminal.record_response(_result())
        terminal_store.failures[3] = SystemExit("PRIVATE_OUTCOME_STORE")
        terminal.accepted()
        terminal.cancelled()
        terminal.accepted()
        self.assertEqual(len(terminal_store.attempts), 3)

    def test_concurrent_response_and_terminal_calls_close_once(self) -> None:
        store, lifecycle = _started_capture()
        with ThreadPoolExecutor(max_workers=16) as executor:
            list(
                executor.map(
                    lambda _index: lifecycle.record_response(_result()), range(32))
            )
            list(executor.map(lambda _index: lifecycle.cancelled(), range(32)))
        self.assertEqual(
            [record.request_type for record in store.records],
            [
                AgentExchangeRequestType.REQUEST,
                AgentExchangeRequestType.RESPONSE,
                AgentExchangeRequestType.OUTCOME,
            ],
        )

    def test_concurrent_begins_generate_unique_safe_exchange_ids(self) -> None:
        store = _RecordingStore()
        capture = _capture(store)

        def begin(_index: int) -> str | None:
            return capture.begin(
                session_id="session_1",
                scope_kind=AgentExchangeScopeKind.CONVERSATION,
                scope_id="conversation_1",
                agent_id=ROSTER[0],
                provider=AgentExchangeProvider.ANTIGRAVITY,
                invocation=_invocation(call_kind=ProviderCallKind.CONVERSATION),
            ).exchange_id

        with ThreadPoolExecutor(max_workers=16) as executor:
            exchange_ids = list(executor.map(begin, range(64)))
        self.assertEqual(len(set(exchange_ids)), 64)
        self.assertTrue(
            all(_EXCHANGE_ID.fullmatch(value or "") for value in exchange_ids))
