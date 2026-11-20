"""Provider exchange capture contract tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents.agent_context import ProviderUsage
from story_writing_agents.agent_context.request_compaction import compact_context_payload
from story_writing_agents.agent_exchanges import AgentExchangeOutcome, AgentExchangeProvider, AgentExchangeRequestType, AgentExchangeScopeKind
from story_writing_agents.agent_exchanges import capture as capture_module
from story_writing_agents.agent_exchanges.capture import AgentExchangeTerminalCode, WorkspaceAgentExchangeCapture
from story_writing_agents.agents import ROSTER
from story_writing_agents.provider_adapter import ProviderResult
from story_writing_agents.structured_output_validation import diagnose_json_text
from story_writing_agents.workspace import Workspace
from tests.agent_exchange_capture_fixture import (
    _EXCHANGE_ID, _RecordingStore, _begin, _decoded, _invocation, _result, _started_capture,
)
from tests.test_agent_context_accounting import _property_count, _shared_context_payload


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
        payload, _ = compact_context_payload(_shared_context_payload(), {})
        schema = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["allowed"]}},
                  "required": ["answer"], "additionalProperties": False}
        invocation = replace(_invocation(payload=payload), schema=schema)
        lifecycle = _begin(store, invocation=invocation)
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
                request.request_type,),
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
                "payload": payload,
                "prior_thread_id": "prior_thread",
                "schema": schema,},
        )
        captured = json.loads(request.content)
        self.assertEqual(captured["schema"], invocation.schema)
        self.assertEqual(_property_count(captured),
                         4 + _property_count(payload) + _property_count(schema))
        self.assertLess(_property_count(captured),
                        4 + _property_count(_shared_context_payload()) + _property_count(schema))
        self.assertEqual(diagnose_json_text('{"answer":"allowed"}', captured["schema"]), ())
        for invalid in ('{}', '{"answer":"other"}', '{"answer":"allowed","extra":null}'):
            self.assertTrue(diagnose_json_text(invalid, captured["schema"]))
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
        store, lifecycle = _started_capture()
        result = replace(_result(usage=ProviderUsage(11, 7, 3)), last_token_usage=ProviderUsage(5, 2, 0))
        lifecycle.record_response(result)
        self.assertEqual(
            _decoded(store.records)[1],
            {
                "actual_transport_utf8_bytes": 777,
                "response_text": "PRIVATE_RESPONSE_한🙂",
                "thread_id": "result_thread",
                "last_token_usage": {"cached_input_tokens": 0, "input_tokens": 5, "output_tokens": 2},
                "usage": {
                    "cached_input_tokens": 3,
                    "input_tokens": 11,
                    "output_tokens": 7,
                },
            },
        )

        zero_store, zero = _started_capture()
        zero.record_response(replace(result, usage=None, last_token_usage=ProviderUsage(0, 0, 0)))
        self.assertEqual(_decoded(zero_store.records)[1]["last_token_usage"],
                         {"cached_input_tokens": 0, "input_tokens": 0, "output_tokens": 0})
        self.assertIsNone(_decoded(zero_store.records)[1]["usage"])

        no_usage_store = _RecordingStore()
        no_usage = _begin(no_usage_store)
        no_usage.record_response(ProviderResult("thread", "body", None, None))
        self.assertEqual(
            _decoded(no_usage_store.records)[1],
            {
                "actual_transport_utf8_bytes": None,
                "response_text": "body",
                "thread_id": "thread",
                "usage": None,},
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
