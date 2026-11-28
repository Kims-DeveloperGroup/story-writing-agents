"""Provider exchange diagnostic capture tests."""
from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from story_writing_agents.agent_exchanges import AgentExchangeRequestType
from story_writing_agents.agent_exchanges import capture as capture_module
from story_writing_agents.agent_exchanges.capture import AgentExchangeTerminalCode
from story_writing_agents.provider_adapter import ProviderInvocationError, ProviderResponseDiagnostic, ProviderResult
from tests.agent_exchange_capture_fixture import _RecordingStore, _begin, _decoded, _result, _started_capture


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
        store, lifecycle = _started_capture()
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
        self.assertEqual(set(raw), {'error', 'exit_code', 'status', 'stderr'})
        self.assertIsNone(raw['error'])
        self.assertIsNone(raw['status'])
        self.assertEqual(raw['stderr'], {'encoding': 'utf-8', 'data': diagnostic.stderr.decode()})
        self.assertEqual(raw['exit_code'], 9)
        self.assertEqual(_decoded(store.records)[2], {'code': 'provider_invocation_failed', 'outcome': 'provider_failure'})
        self.assertEqual(len({r.exchange_id for r in store.records}), 1)

    def test_failure_response_preserves_only_error_status_exit_code_and_stderr(self):
        store, lifecycle = _started_capture()
        stdout = json.dumps({
            "event": "result",
            "result": {
                "conversation_id": "thread-123",
                "status": "ERROR",
                "error": "Server overloaded",
                "json_schema": {"huge": "schema" * 500},
            },
        }).encode("utf-8")
        diagnostic = ProviderResponseDiagnostic(stdout, b"error log", 1)
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        response = _decoded(store.records)[1]
        raw = response['response_diagnostic']
        self.assertEqual(raw, {
            "error": "Server overloaded",
            "exit_code": 1,
            "status": "ERROR",
            "stderr": {"data": "error log", "encoding": "utf-8"},})

    def test_failure_response_handles_nonfinite_json_and_codex_jsonl(self):
        # Non-finite JSON values fall back to None without failing serialization
        store, lifecycle = _started_capture()
        diagnostic = ProviderResponseDiagnostic(
            b'{"status": NaN, "error": Infinity}', b"err", 1, native_error_message="Verified native failure",
        )
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        response = _decoded(store.records)[1]
        self.assertEqual(response['response_diagnostic'], {
            "error": "Verified native failure",
            "exit_code": 1,
            "status": None,
            "stderr": {"data": "err", "encoding": "utf-8"},})

        # Native message-only errors survive the stream's later status-only event.
        store, lifecycle = _started_capture()
        refusal = ProviderResponseDiagnostic(
            b'{"type":"error","message":"Provider refused","private":"omit"}\n'
            b'{"type":"turn.failed","status":"failed"}\n',
            b"", 1, response_text="Cannot complete this request.",
        )
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=refusal)
        response = _decoded(store.records)[1]
        self.assertEqual(response["response_text"], refusal.response_text)
        self.assertEqual(response["response_diagnostic"]["error"], "Provider refused")
        self.assertEqual(response["response_diagnostic"]["status"], "failed")
        self.assertNotIn("omit", store.records[1].content)
        self.assertEqual(len({record.exchange_id for record in store.records}), 1)

        # Codex JSONL multi-line event streams extract terminal error events
        store, lifecycle = _started_capture()
        jsonl_stdout = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.failed","error":{"message":"Quota exceeded","code":429},"status":"failed"}\n'
        )
        diagnostic = ProviderResponseDiagnostic(jsonl_stdout, b"codex-err", 1)
        lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        response = _decoded(store.records)[1]
        self.assertEqual(response['response_diagnostic'], {
            "error": {"code": 429, "message": "Quota exceeded"},
            "exit_code": 1,
            "status": "failed",
            "stderr": {"data": "codex-err", "encoding": "utf-8"},})

    def test_recovered_success_records_diagnostic_and_none_default_is_unchanged(self):
        for diagnostic in (None, self.diagnostic()):
            with self.subTest(diagnostic=diagnostic):
                store, lifecycle = _started_capture()
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
        store, lifecycle = _started_capture()
        with mock.patch.object(capture_module, '_response_diagnostic', side_effect=ValueError('private serialization')):
            lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        self.assertEqual([r.request_type for r in store.records], [AgentExchangeRequestType.REQUEST, AgentExchangeRequestType.RESPONSE, AgentExchangeRequestType.OUTCOME])
        self.assertEqual(_decoded(store.records)[1], {"provider_failure": True})
        store, lifecycle = _started_capture()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: lifecycle.provider_failure(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic), range(12)))
        self.assertEqual(len(store.records), 3)

    def test_backend_forwards_only_typed_diagnostic_without_replacing_failure(self):
        from story_writing_agents.agent_exchanges.provider_finalization import capture_provider_failure_without_replacing
        diagnostic = self.diagnostic()
        failure = ProviderInvocationError('fixed', taint_existing_thread=True)
        failure.record_response_diagnostic(diagnostic)
        store, lifecycle = _started_capture()
        capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, failure=failure)
        self.assertEqual(len(store.records), 3)
        self.assertEqual(str(failure), 'fixed')
        lifecycle = mock.Mock()
        lifecycle.provider_failure.side_effect = KeyboardInterrupt()
        capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, failure=failure)
        lifecycle.provider_failure.assert_called_once_with(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED, response_diagnostic=diagnostic)
        lifecycle = mock.Mock()
        capture_provider_failure_without_replacing(lifecycle, AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)
        lifecycle.provider_failure.assert_called_once_with(AgentExchangeTerminalCode.PROVIDER_INVOCATION_FAILED)
