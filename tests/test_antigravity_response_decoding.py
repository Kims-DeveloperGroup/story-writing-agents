"""Independent Antigravity native response decoding case definitions."""

from __future__ import annotations

from unittest import mock

from story_writing_agents.agent_context import ProviderUsage
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.provider_adapter import ProviderInvocation, ProviderInvocationError
from tests.antigravity_adapter_fixture import _DEFAULT_RESULT_SCHEMA, _UNSET, _json_result


class AntigravityResponseDecodingCases:
    """Keep result and usage contracts with their domain definitions."""

    def test_provider_usage_accepts_only_recognized_exact_snapshot_counters(
        self,
    ) -> None:
        cases = (
            (None, None),
            ({}, None),
            ({"unknown": 7}, None),
            (
                {
                    "input_tokens": 0,
                    "output_tokens": 2,
                    "cache_read_tokens": 3,},
                ProviderUsage(0, 2, 3),
            ),
            ({"input_tokens": 4}, ProviderUsage(input_tokens=4)),
            (
                {
                    "input_tokens": 5,
                    "output_tokens": -1,
                    "cache_read_tokens": True,
                    "unknown": 9,
                },
                ProviderUsage(input_tokens=5),
            ),
            (
                {
                    "input_tokens": -1,
                    "output_tokens": "2",
                    "cache_read_tokens": False,
                },
                None,
            ),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(AntigravityAdapter.provider_usage(value), expected)

    def test_malformed_usage_never_invalidates_a_valid_provider_response(
        self,
    ) -> None:
        result = AntigravityAdapter._provider_result(
            _json_result(
                "conversation-usage",
                structured_output={"value": "valid provider response"},
                usage={
                    "input_tokens": True,
                    "output_tokens": -1,
                    "cache_read_tokens": "3",
                    "unknown": 4,},
            ),
            expected_thread_id=None,
            expected_schema=_DEFAULT_RESULT_SCHEMA,

        )

        self.assertEqual(result.thread_id, "conversation-usage")
        self.assertEqual(result.response_text, '{"value":"valid provider response"}')
        self.assertIsNone(result.usage)

    async def test_identifier_and_json_failures_are_fixed_and_confidential(
        self,
    ) -> None:
        adapter = self.adapter("validation-state")
        binding = adapter.bind(self.definition)
        forbidden_process = mock.AsyncMock(
            side_effect=AssertionError("provider process invoked")
        )
        with mock.patch(
            "story_writing_agents.antigravity_adapter.run_process",
            forbidden_process,
        ):
            invalid_invocations = (
                (
                    ProviderInvocation(
                        session_id="../CONFIDENTIAL_SESSION",
                        schema={},
                        payload={},
                        thread_id=None,
                    ),
                    "Antigravity session ID is invalid",),
                (
                    ProviderInvocation(
                        session_id="safe-session",
                        schema={},
                        payload={},
                        thread_id="--CONFIDENTIAL_THREAD",
                    ),
                    "Antigravity conversation ID is invalid",
                ),
            )
            for invocation, diagnostic in invalid_invocations:
                with (
                    self.subTest(diagnostic=diagnostic),
                    self.assertRaises(ProviderInvocationError) as caught,
                ):
                    await adapter.invoke(binding, invocation)
                self.assertEqual(str(caught.exception), diagnostic)
                self.assertIs(caught.exception.taint_existing_thread, False)
                self.assertNotIn("CONFIDENTIAL", str(caught.exception))
        forbidden_process.assert_not_awaited()
        self.assertFalse(
            (self.workspace.path("validation-state") / "sessions").exists())

        confidential = "CONFIDENTIAL_PROVIDER_OUTPUT"

        malformed = "Antigravity CLI returned malformed JSON"
        bad_output = "Antigravity CLI returned malformed structured output"
        result_cases = (
            ("unsafe ID", _json_result("--CONFIDENTIAL_PROVIDER_OUTPUT"), None, "Antigravity CLI returned an invalid conversation ID"),
            ("mismatched ID", _json_result("other"), "expected", "Antigravity CLI returned an invalid conversation ID"),
            ("invalid UTF-8", b"\xff", None, "Antigravity CLI result is not valid UTF-8"),
            ("malformed JSON", b'{"CONFIDENTIAL_PROVIDER_OUTPUT":', None, malformed),
            ("empty", b"", None, malformed),
            ("array", b"[]", None, "Antigravity CLI returned a non-object JSON result"),
            ("scalar", b"null", None, "Antigravity CLI returned a non-object JSON result"),
            ("trailing data", _json_result("safe") + b"CONFIDENTIAL_PROVIDER_OUTPUT", None, malformed),
            ("NDJSON", _json_result("safe") * 2, None, malformed),
            ("duplicate root key", b'{"status":"SUCCESS","status":"ERROR"}', None, malformed),
            ("duplicate nested key", b'{"nested":{"x":1,"x":2}}', None, malformed),
            *((label, ('{"value":'+value+'}').encode(), None, malformed) for label,value in (("NaN", "NaN"), ("infinity", "Infinity"), ("overflow", "1e999"))),
            ("non-success", _json_result("safe", status="FAILED_CONFIDENTIAL_PROVIDER_OUTPUT"), None, "Antigravity CLI did not complete successfully"),
            ("missing structured output", _json_result("safe", response=confidential), None, bad_output),
            ("scalar structured output", _json_result("safe", structured_output=confidential), None, bad_output),
            ("missing schema echo", _json_result("safe", structured_output={}, json_schema=_UNSET), None, bad_output),
            ("mismatched schema echo", _json_result("safe", structured_output={}, json_schema={"type":"string"}), None, bad_output),
            ("surrogate output", b'{"status":"SUCCESS","conversation_id":"safe","structured_output":{"value":"\\ud800"},"json_schema":{"type":"object"}}', None, bad_output),
        )
        for label, stdout, expected_thread_id, diagnostic in result_cases:
            with (
                self.subTest(result=label),
                self.assertRaises(ProviderInvocationError) as caught,
            ):
                adapter._provider_result(
                    stdout,
                    expected_thread_id=expected_thread_id,
                    expected_schema=_DEFAULT_RESULT_SCHEMA,

                )
            self.assertEqual(str(caught.exception), diagnostic)
            self.assertIs(caught.exception.taint_existing_thread, True)
            self.assertNotIn(confidential, str(caught.exception))
            self.assertIsNone(caught.exception.continuation_rejection)
