"""Fail-open provider exchange capture lifecycle tests."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents.agent_context import ProviderCallKind, ProviderUsage
from story_writing_agents.agent_exchanges import (
    AgentExchangeOutcome,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
)
from story_writing_agents.agent_exchanges import capture as capture_module
from story_writing_agents.agent_exchanges.capture import (
    AgentExchangeCaptureLifecycle,
    AgentExchangeTerminalCode,
    WorkspaceAgentExchangeCapture,
)
from story_writing_agents.agents import ROSTER
from story_writing_agents.provider_adapter import ProviderInvocation, ProviderResult, ProviderResponseDiagnostic, ProviderInvocationError
from story_writing_agents.workspace import Workspace


_EXCHANGE_ID = re.compile(r"exchange_[0-9a-f]{32}")


class _RecordingStore:
    """Minimal append seam with deterministic per-attempt failures."""

    def __init__(self) -> None:
        self.attempts: list[AgentExchangeRecordInput] = []
        self.records: list[AgentExchangeRecordInput] = []
        self.failures: dict[int, BaseException] = {}
        self._lock = threading.Lock()

    def append(self, record: AgentExchangeRecordInput) -> object:
        with self._lock:
            self.attempts.append(record)
            attempt = len(self.attempts)
            failure = self.failures.get(attempt)
            if failure is not None:
                raise failure
            self.records.append(record)
        return object()


def _invocation(
    *,
    payload: object | None = None,
    call_kind: ProviderCallKind = ProviderCallKind.DEBATE,
    thread_id: str | None = "prior_thread",
) -> ProviderInvocation:
    return ProviderInvocation(
        session_id="session_1",
        schema={"required": ["answer"], "type": "object"},
        payload=(
            {"answer": "PRIVATE_REQUEST_한🙂", "enabled": True}
            if payload is None
            else payload  # type: ignore[arg-type]
        ),
        thread_id=thread_id,
        call_kind=call_kind,
    )


def _result(*, usage: ProviderUsage | None = None) -> ProviderResult:
    return ProviderResult(
        thread_id="result_thread",
        response_text="PRIVATE_RESPONSE_한🙂",
        usage=usage,
        actual_transport_utf8_bytes=777,
    )


def _capture(store: _RecordingStore) -> WorkspaceAgentExchangeCapture:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Workspace(temporary)
        with mock.patch.object(
            capture_module,
            "WorkspaceAgentExchangeStore",
            return_value=store,
        ):
            return WorkspaceAgentExchangeCapture(workspace)


def _begin(
    store: _RecordingStore,
    *,
    invocation: ProviderInvocation | None = None,
) -> AgentExchangeCaptureLifecycle:
    return _capture(store).begin(
        session_id="session_1",
        scope_kind=AgentExchangeScopeKind.DEBATE,
        scope_id="debate_1",
        agent_id=ROSTER[0],
        provider=AgentExchangeProvider.CODEX,
        invocation=invocation or _invocation(),
    )


def _decoded(records: list[AgentExchangeRecordInput]) -> list[object]:
    return [json.loads(record.content) for record in records]


class AgentExchangeCaptureContractTests(unittest.TestCase):
    def test_exact_closed_surfaces_and_no_root_expansion(self) -> None:
        self.assertEqual(
            tuple(item.value for item in AgentExchangeOutcome),
            (
                "accepted",
                "rejected",
                "provider_failure",
                "cancelled",
                "post_response_runtime_failure",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in AgentExchangeTerminalCode),
            (
                "accepted",
                "provider_invocation_failed",
                "provider_authentication_required",
                "provider_timed_out",
                "provider_contract_invalid",
                "cancelled",
                "malformed_structured_response",
                "response_correlation_mismatch",
                "response_semantic_rejected",
                "action_exhausted",
                "answer_validation_failed",
                "thread_persistence_failed",
                "reference_runtime_failed",
                "context_runtime_failed",
            ),
        )
        self.assertEqual(
            capture_module.__all__,
            [
                "AgentExchangeCaptureLifecycle",
                "AgentExchangeTerminalCode",
                "WorkspaceAgentExchangeCapture",
            ],
        )
        for name in capture_module.__all__:
            self.assertFalse(hasattr(story_writing_agents, name))

    def test_constructor_is_io_free_and_begin_projects_exact_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            before = tuple(Path(temporary).iterdir())
            WorkspaceAgentExchangeCapture(workspace)
            self.assertEqual(tuple(Path(temporary).iterdir()), before)

        store = _RecordingStore()
        lifecycle = _begin(store)
        self.assertTrue(lifecycle.enabled)
        self.assertRegex(lifecycle.exchange_id or "", _EXCHANGE_ID)
        self.assertEqual(len(store.records), 1)
        request = store.records[0]
        self.assertEqual(
            (
                request.session_id,
                request.scope_kind,
                request.scope_id,
                request.agent_id,
                request.provider,
                request.request_type,
            ),
            (
                "session_1",
                AgentExchangeScopeKind.DEBATE,
                "debate_1",
                ROSTER[0],
                AgentExchangeProvider.CODEX,
                AgentExchangeRequestType.REQUEST,
            ),
        )
        self.assertEqual(request.exchange_id, lifecycle.exchange_id)
        self.assertEqual(
            json.loads(request.content),
            {
                "call_kind": "debate",
                "payload": {
                    "answer": "PRIVATE_REQUEST_한🙂",
                    "enabled": True,
                },
                "prior_thread_id": "prior_thread",
                "schema": {"required": ["answer"], "type": "object"},
            },
        )
        self.assertEqual(
            request.content,
            json.dumps(
                json.loads(request.content),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def test_response_projects_exact_result_with_usage_and_nulls(self) -> None:
        store = _RecordingStore()
        lifecycle = _begin(store)
        lifecycle.record_response(_result(usage=ProviderUsage(11, 7, 3)))
        self.assertEqual(
            _decoded(store.records)[1],
            {
                "actual_transport_utf8_bytes": 777,
                "response_text": "PRIVATE_RESPONSE_한🙂",
                "thread_id": "result_thread",
                "usage": {
                    "cached_input_tokens": 3,
                    "input_tokens": 11,
                    "output_tokens": 7,
                },
            },
        )

        no_usage_store = _RecordingStore()
        no_usage = _begin(no_usage_store)
        no_usage.record_response(ProviderResult("thread", "body", None, None))
        self.assertEqual(
            _decoded(no_usage_store.records)[1],
            {
                "actual_transport_utf8_bytes": None,
                "response_text": "body",
                "thread_id": "thread",
                "usage": None,
            },
        )

    def test_begin_failures_disable_without_leaking_or_raising(self) -> None:
        invalid_json = _invocation(payload={"not_finite": float("nan")})
        cases: tuple[tuple[str, object], ...] = (
            ("serialization", invalid_json),
            ("store-exception", OSError("PRIVATE_STORE_PATH")),
            ("store-base", KeyboardInterrupt("PRIVATE_STORE_CONTROL")),
        )
        for label, value in cases:
            with self.subTest(label=label):
                store = _RecordingStore()
                if isinstance(value, BaseException):
                    store.failures[1] = value
                    lifecycle = _begin(store)
                else:
                    lifecycle = _begin(store, invocation=value)  # type: ignore[arg-type]
                self.assertFalse(lifecycle.enabled)
                self.assertIsNone(lifecycle.exchange_id)
                lifecycle.record_response(_result())
                lifecycle.accepted()
                self.assertLessEqual(len(store.attempts), 1)

        for failure in (
            OSError("PRIVATE_ENTROPY"),
            KeyboardInterrupt("PRIVATE_ENTROPY_CONTROL"),
        ):
            with self.subTest(entropy=type(failure).__name__):
                store = _RecordingStore()
                with mock.patch.object(capture_module, "uuid4", side_effect=failure):
                    lifecycle = _begin(store)
                self.assertFalse(lifecycle.enabled)
                self.assertEqual(store.attempts, [])


class AgentExchangeCaptureLifecycleTests(unittest.TestCase):
    def test_all_response_terminal_outcomes_are_exact_and_share_identity(self) -> None:
        cases = (
            ("accepted", None, "accepted", "accepted"),
            (
                "rejected",
                AgentExchangeTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                "rejected",
                "malformed_structured_response",
            ),
            (
                "rejected",
                AgentExchangeTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                "rejected",
                "response_correlation_mismatch",
            ),
            (
                "rejected",
                AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED,
                "rejected",
                "response_semantic_rejected",
            ),
            (
                "rejected",
                AgentExchangeTerminalCode.ACTION_EXHAUSTED,
                "rejected",
                "action_exhausted",
            ),
            (
                "rejected",
                AgentExchangeTerminalCode.ANSWER_VALIDATION_FAILED,
                "rejected",
                "answer_validation_failed",
            ),
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
                store = _RecordingStore()
                lifecycle = _begin(store)
                lifecycle.record_response(_result())
                method = getattr(lifecycle, method_name)
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
                    {lifecycle.exchange_id},
                )
                self.assertEqual(
                    _decoded(store.records)[2],
                    {"code": expected_code, "outcome": outcome},
                )

    def test_no_response_failures_and_cancellation_matrix_are_exact(self) -> None:
        for code in (
            AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED,
            AgentExchangeTerminalCode.PROVIDER_AUTHENTICATION_REQUIRED,
            AgentExchangeTerminalCode.PROVIDER_TIMED_OUT,
            AgentExchangeTerminalCode.PROVIDER_CONTRACT_INVALID,
        ):
            with self.subTest(code=code):
                store = _RecordingStore()
                lifecycle = _begin(store)
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
                    {"provider_failure": True},
                )
                self.assertEqual(
                    _decoded(store.records)[2],
                    {"code": code.value, "outcome": "provider_failure"},
                )

        for after_response in (False, True):
            with self.subTest(after_response=after_response):
                store = _RecordingStore()
                lifecycle = _begin(store)
                if after_response:
                    lifecycle.record_response(_result())
                lifecycle.cancelled()
                self.assertEqual(
                    _decoded(store.records)[-1],
                    {"code": "cancelled", "outcome": "cancelled"},
                )

    def test_invalid_transitions_are_noops_and_valid_transition_remains_available(
        self,
    ) -> None:
        store = _RecordingStore()
        lifecycle = _begin(store)
        lifecycle.accepted()
        lifecycle.rejected(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)
        lifecycle.post_response_runtime_failure(
            AgentExchangeTerminalCode.RESPONSE_SEMANTIC_REJECTED
        )
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
            {"code": "provider_timed_out", "outcome": "provider_failure"},
        )
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
            AgentExchangeRequestType.OUTCOME,
        )

        terminal_store = _RecordingStore()
        terminal = _begin(terminal_store)
        terminal.record_response(_result())
        terminal_store.failures[3] = SystemExit("PRIVATE_OUTCOME_STORE")
        terminal.accepted()
        terminal.cancelled()
        terminal.accepted()
        self.assertEqual(len(terminal_store.attempts), 3)

    def test_concurrent_response_and_terminal_calls_close_once(self) -> None:
        store = _RecordingStore()
        lifecycle = _begin(store)
        with ThreadPoolExecutor(max_workers=16) as executor:
            list(
                executor.map(
                    lambda _index: lifecycle.record_response(_result()), range(32)
                )
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
            all(_EXCHANGE_ID.fullmatch(value or "") for value in exchange_ids)
        )


class ProviderDiagnosticCaptureTests(unittest.TestCase):

    def diagnostic(self):
        return ProviderResponseDiagnostic(b'PRIVATE_STDOUT_\xff', 'PRIVATE_STDERR_한'.encode(), 9)

    def test_diagnostic_value_is_strict_passive_and_hidden_from_repr(self):
        diagnostic = self.diagnostic()
        self.assertNotIn('PRIVATE', repr(diagnostic))
        result = ProviderResult('thread', 'answer', response_diagnostic=diagnostic)
        self.assertNotIn('PRIVATE', repr(result))
        self.assertEqual(result, ProviderResult('thread', 'answer'))
        for values in (('text', b'', 0), (b'', bytearray(), 0), (b'', b'', True)):
            with self.subTest(values=values), self.assertRaises(TypeError):
                ProviderResponseDiagnostic(*values)
        error = ProviderInvocationError('fixed diagnostic', taint_existing_thread=True)
        error.record_response_diagnostic(diagnostic)
        self.assertIs(error.response_diagnostic, diagnostic)
        self.assertEqual(str(error), 'fixed diagnostic')
        self.assertNotIn('PRIVATE', repr(error))
        with self.assertRaises(TypeError):
            error.record_response_diagnostic(object())

    def test_failure_response_bytes_are_reversible_and_never_accepted(self):
        import base64
        store = _RecordingStore()
        lifecycle = _begin(store)
        diagnostic = self.diagnostic()
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        lifecycle.accepted()
        lifecycle.record_response(_result())
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        self.assertEqual([r.request_type for r in store.records], [AgentExchangeRequestType.REQUEST, AgentExchangeRequestType.RESPONSE, AgentExchangeRequestType.OUTCOME])
        response = _decoded(store.records)[1]
        self.assertTrue(response['provider_failure'])
        self.assertEqual(set(response), {'provider_failure', 'response_diagnostic'})
        raw = response['response_diagnostic']
        self.assertEqual(base64.b64decode(raw['stdout']['data']), diagnostic.stdout)
        self.assertEqual(raw['stdout']['encoding'], 'base64')
        self.assertEqual(raw['stderr'], {'encoding': 'utf-8', 'data': diagnostic.stderr.decode()})
        self.assertEqual(raw['exit_code'], 9)
        self.assertEqual(_decoded(store.records)[2], {'code': 'provider_invocation_failed', 'outcome': 'provider_failure'})
        self.assertEqual(len({r.exchange_id for r in store.records}), 1)

    def test_recovered_success_records_diagnostic_and_none_default_is_unchanged(self):
        for diagnostic in (None, self.diagnostic()):
            with self.subTest(diagnostic=diagnostic):
                store = _RecordingStore()
                lifecycle = _begin(store)
                lifecycle.record_response(ProviderResult('thread', 'answer', response_diagnostic=diagnostic))
                lifecycle.accepted()
                response = _decoded(store.records)[1]
                self.assertEqual('response_diagnostic' in response, diagnostic is not None)
                self.assertNotIn('provider_failure', response)
                self.assertEqual(_decoded(store.records)[-1], {'code': 'accepted', 'outcome': 'accepted'})

    def test_diagnostic_failures_disabled_logging_and_concurrent_duplicates_are_fail_open(self):
        diagnostic = self.diagnostic()
        for failed_attempt in (1, 2, 3):
            with self.subTest(failed_attempt=failed_attempt):
                store = _RecordingStore()
                store.failures[failed_attempt] = KeyboardInterrupt('private failure')
                lifecycle = _begin(store)
                lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
                lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
                self.assertLessEqual(len(store.attempts), 3)
                if failed_attempt == 2:
                    self.assertEqual(store.records[-1].request_type, AgentExchangeRequestType.OUTCOME)
        store = _RecordingStore()
        lifecycle = _begin(store)
        with mock.patch.object(capture_module, '_response_diagnostic', side_effect=ValueError('private serialization')):
            lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        self.assertEqual([r.request_type for r in store.records], [AgentExchangeRequestType.REQUEST, AgentExchangeRequestType.OUTCOME])
        store = _RecordingStore()
        lifecycle = _begin(store)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic), range(12)))
        self.assertEqual(len(store.records), 3)

    def test_backend_forwards_only_typed_diagnostic_without_replacing_failure(self):
        from story_writing_agents.backend import AgentBackend
        diagnostic = self.diagnostic()
        failure = ProviderInvocationError('fixed', taint_existing_thread=True)
        failure.record_response_diagnostic(diagnostic)
        store = _RecordingStore()
        lifecycle = _begin(store)
        AgentBackend._capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, failure=failure)
        self.assertEqual(len(store.records), 3)
        self.assertEqual(str(failure), 'fixed')
        lifecycle = mock.Mock()
        lifecycle.provider_failure.side_effect = KeyboardInterrupt()
        AgentBackend._capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, failure=failure)
        lifecycle.provider_failure.assert_called_once_with(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        lifecycle = mock.Mock()
        AgentBackend._capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)
        lifecycle.provider_failure.assert_called_once_with(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
