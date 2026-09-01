"""Exact provider-neutral context accounting contracts."""

from __future__ import annotations

import ast
import asyncio
import math
import unittest
from pathlib import Path

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents import agent_context
from story_writing_agents.agent_context import (
    ContextComponentBytes,
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderJsonEncodingError,
    ProviderPayloadAccounting,
    ProviderUsage,
    account_provider_payload,
    canonical_provider_json,
)
from story_writing_agents.provider_adapter import (
    ProviderBinding,
    ProviderFailureAccounting,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
    provider_failure_accounting,
    record_provider_failure_accounting,
)


class CanonicalProviderJsonTests(unittest.TestCase):
    """Verify the exact strict application/provider JSON boundary."""

    def test_ascii_is_compact_and_preserves_insertion_order(self) -> None:
        value = {"second": [1, True, None], "first": "text"}
        self.assertEqual(
            canonical_provider_json(value),
            b'{"second":[1,true,null],"first":"text"}',
        )

    def test_multibyte_unicode_and_escaping_are_exact_utf8(self) -> None:
        value = {"한글": 'é🙂\n"\\'}
        expected = '{"한글":"é🙂\\n\\"\\\\"}'.encode("utf-8")
        encoded = canonical_provider_json(value)
        self.assertEqual(encoded, expected)
        self.assertNotIn(b"\\ud83d", encoded)
        self.assertGreater(len(encoded), len(encoded.decode("utf-8")))

    def test_tuples_shared_values_and_scalars_match_the_canonical_shape(self) -> None:
        shared = ["same"]
        self.assertEqual(
            canonical_provider_json(
                {"tuple": (0, -2, 1.5), "left": shared, "right": shared}
            ),
            b'{"tuple":[0,-2,1.5],"left":["same"],"right":["same"]}',
        )

    def test_strict_failures_have_one_content_free_diagnostic(self) -> None:
        cyclic: list[object] = ["PRIVATE_CYCLE_VALUE"]
        cyclic.append(cyclic)
        cases = (
            ({"value": math.nan}, "provider input is not canonical strict UTF-8 JSON"),
            ({"value": math.inf}, "provider input is not canonical strict UTF-8 JSON"),
            (
                {"value": "PRIVATE_SURROGATE\ud800"},
                "provider input is not canonical strict UTF-8 JSON",
            ),
            ({1: "PRIVATE_NONSTRING_KEY"}, "provider JSON object keys must be strings"),
            ({"value": object()}, "provider input is not canonical strict UTF-8 JSON"),
            (cyclic, "provider input must not contain recursive containers"),
        )
        for value, diagnostic in cases:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ProviderJsonEncodingError) as caught:
                    canonical_provider_json(value)
                self.assertEqual(
                    str(caught.exception),
                    diagnostic,
                )
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertNotIn("PRIVATE", repr(caught.exception))


class ProviderPayloadAccountingTests(unittest.TestCase):
    """Verify exact partitions and the non-additive reference subset."""

    @staticmethod
    def _components(
        accounting: ProviderPayloadAccounting,
    ) -> dict[ContextComponentKind, ContextComponentBytes]:
        return {component.kind: component for component in accounting.components}

    def test_ascii_and_unicode_components_partition_schema_and_payload(self) -> None:
        schema = {
            "type": "object",
            "properties": {"content": {"type": "string"}},
        }
        payload = {
            "operation": "conversation",
            "content": "질문 🙂",
            "context": [{"content": "prior"}],
        }
        payload_json, accounting = account_provider_payload(
            schema,
            payload,
            call_kind=ProviderCallKind.CONVERSATION,
            component_paths={
                ("content",): ContextComponentKind.CURRENT_INPUT,
                ("context",): ContextComponentKind.CONVERSATION_CONTEXT,
            },
        )
        components = self._components(accounting)
        self.assertEqual(payload_json, canonical_provider_json(payload))
        self.assertEqual(
            accounting.schema_utf8_bytes,
            len(canonical_provider_json(schema)),
        )
        self.assertEqual(accounting.payload_utf8_bytes, len(payload_json))
        self.assertEqual(
            sum(item.serialized_utf8_bytes for item in accounting.components),
            accounting.serialized_input_utf8_bytes,
        )
        self.assertEqual(
            set(components),
            {
                ContextComponentKind.OUTPUT_SCHEMA,
                ContextComponentKind.CONTROL,
                ContextComponentKind.CURRENT_INPUT,
                ContextComponentKind.CONVERSATION_CONTEXT,
            },
        )
        self.assertEqual(accounting.raw_mandatory_reference_utf8_bytes, 0)

    def test_whole_range_and_multiple_reference_bodies_exclude_only_raw_bytes(
        self,
    ) -> None:
        schema = {"type": "object"}
        reference_cases = (
            (
                "whole",
                [
                    {
                        "reference": {
                            "path": "core_documents/global_outline.md",
                            "line_range": None,
                        },
                        "content": "whole line\n",
                    }
                ],
            ),
            (
                "range",
                [
                    {
                        "reference": {
                            "path": "core_documents/character_sheets.md",
                            "line_range": {"start": 2, "end": 2},
                        },
                        "content": "범위 🙂\n",
                    }
                ],
            ),
            (
                "multiple",
                [
                    {
                        "reference": {
                            "path": "core_documents/global_outline.md",
                            "line_range": None,
                        },
                        "content": 'quote " slash \\ newline\n',
                    },
                    {
                        "reference": {
                            "path": "manuscript/part-01/scene.md",
                            "line_range": {"start": 1, "end": 2},
                        },
                        "content": "한🙂\nsecond\n",
                    },
                ],
            ),
        )
        for label, references in reference_cases:
            with self.subTest(case=label):
                payload = {
                    "operation": "conversation",
                    "untrusted_writing_artifact_context": references,
                }
                component_paths = {
                    ("untrusted_writing_artifact_context",): (
                        ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
                    )
                }
                raw_paths = []
                for index in range(len(references)):
                    path = (
                        "untrusted_writing_artifact_context",
                        index,
                        "content",
                    )
                    component_paths[path] = (
                        ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                    )
                    raw_paths.append(path)
                payload_json, accounting = account_provider_payload(
                    schema,
                    payload,
                    call_kind=ProviderCallKind.CONVERSATION,
                    component_paths=component_paths,
                    mandatory_reference_paths=tuple(raw_paths),
                )
                raw_bytes = sum(
                    len(item["content"].encode("utf-8")) for item in references
                )
                components = self._components(accounting)
                content_component = components[
                    ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                ]
                descriptor_component = components[
                    ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
                ]
                self.assertEqual(
                    accounting.raw_mandatory_reference_utf8_bytes,
                    raw_bytes,
                )
                self.assertEqual(
                    content_component.raw_mandatory_reference_utf8_bytes,
                    raw_bytes,
                )
                self.assertGreater(
                    content_component.serialized_utf8_bytes,
                    raw_bytes,
                )
                self.assertGreater(descriptor_component.serialized_utf8_bytes, 0)
                self.assertIn(b"line_range", payload_json)
                self.assertEqual(
                    sum(item.serialized_utf8_bytes for item in accounting.components),
                    len(canonical_provider_json(schema)) + len(payload_json),
                )

    def test_call_totals_keep_reference_envelopes_and_escaping_counted(self) -> None:
        binding = (
            ContextComponentBytes(ContextComponentKind.BINDING_STRUCTURE, 11),
            ContextComponentBytes(ContextComponentKind.ROLE_SYSTEM_PROMPT, 7),
        )
        payload = {
            "references": [
                {
                    "reference": {
                        "path": "manuscript/part-01/a.md",
                        "line_range": {"start": 1, "end": 1},
                    },
                    "content": '"한🙂"\n',
                }
            ]
        }
        _encoded, payload_accounting = account_provider_payload(
            {"type": "object"},
            payload,
            component_paths={
                ("references",): ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR,
                ("references", 0, "content"): (
                    ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                ),
            },
            mandatory_reference_paths=(("references", 0, "content"),),
        )
        call = ProviderCallAccounting(
            payload_accounting,
            binding,
            ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=payload_accounting.payload_utf8_bytes,
        )
        self.assertEqual(call.binding_instruction_utf8_bytes, 18)
        self.assertEqual(
            call.gross_model_input_utf8_bytes,
            18 + payload_accounting.serialized_input_utf8_bytes,
        )
        self.assertEqual(
            call.non_reference_model_input_utf8_bytes,
            call.gross_model_input_utf8_bytes
            - len(payload["references"][0]["content"].encode("utf-8")),
        )
        self.assertEqual(
            call.submitted_model_input_utf8_bytes,
            call.gross_model_input_utf8_bytes,
        )
        self.assertIsNone(
            ProviderCallAccounting(payload_accounting).submitted_model_input_utf8_bytes
        )

    def test_invalid_paths_partitions_and_components_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identify payload values"):
            account_provider_payload(
                {},
                {"present": "value"},
                component_paths={("missing",): ContextComponentKind.CURRENT_INPUT},
            )
        with self.assertRaisesRegex(ValueError, "mandatory-reference paths"):
            account_provider_payload(
                {},
                {"content": "value"},
                component_paths={("content",): ContextComponentKind.CURRENT_INPUT},
                mandatory_reference_paths=(("content",),),
            )
        with self.assertRaisesRegex(ValueError, "raw mandatory-reference"):
            ContextComponentBytes(
                ContextComponentKind.CURRENT_INPUT,
                1,
                raw_mandatory_reference_utf8_bytes=1,
            )
        with self.assertRaisesRegex(ValueError, "source identity"):
            ContextComponentBytes(ContextComponentKind.CORE_DOCUMENT, 1)
        with self.assertRaisesRegex(ValueError, "canonical order"):
            ProviderPayloadAccounting(
                ProviderCallKind.CONVERSATION,
                1,
                1,
                (
                    ContextComponentBytes(ContextComponentKind.CONTROL, 1),
                    ContextComponentBytes(ContextComponentKind.OUTPUT_SCHEMA, 1),
                ),
            )


class ProviderNeutralCompatibilityTests(unittest.TestCase):
    """Verify additive private contracts without leaking model input."""

    def test_legacy_invocation_is_snapshotted_and_repr_is_content_free(self) -> None:
        schema = {"type": "object", "PRIVATE_SCHEMA": "secret"}
        payload = {"content": "PRIVATE_PAYLOAD 한🙂"}
        invocation = ProviderInvocation("session", schema, payload, None)
        schema["changed"] = True
        payload["changed"] = True
        self.assertNotIn("changed", invocation.schema)
        self.assertNotIn("changed", invocation.payload)
        self.assertEqual(
            invocation.payload_json,
            b'{"content":"PRIVATE_PAYLOAD \xed\x95\x9c\xf0\x9f\x99\x82"}',
        )
        representation = repr(invocation)
        self.assertNotIn("PRIVATE_SCHEMA", representation)
        self.assertNotIn("PRIVATE_PAYLOAD", representation)
        self.assertNotIn("secret", representation)
        self.assertIsNotNone(invocation.call_accounting)

    def test_unencodable_invocation_has_no_accounting_record_or_content_leak(
        self,
    ) -> None:
        cyclic: dict[str, object] = {"private": "PRIVATE_CYCLE"}
        cyclic["cycle"] = cyclic
        invocation = ProviderInvocation("session", {}, cyclic, None)
        self.assertIsNone(invocation.payload_json)
        self.assertIsNone(invocation.payload_accounting)
        self.assertIsNone(invocation.call_accounting)
        self.assertNotIn("PRIVATE", repr(invocation))

    def test_result_metadata_is_additive_to_legacy_equality(self) -> None:
        legacy = ProviderResult("thread", "response")
        enriched = ProviderResult(
            "thread",
            "response",
            usage=ProviderUsage(1, 2, 3),
            actual_transport_utf8_bytes=9,
        )
        self.assertEqual(legacy, enriched)
        self.assertEqual(enriched.usage, ProviderUsage(1, 2, 3))
        self.assertEqual(enriched.actual_transport_utf8_bytes, 9)

    def test_failure_accounting_covers_prepared_and_submitted_cancellation(
        self,
    ) -> None:
        prepared = ProviderInvocationError("fixed", taint_existing_thread=False)
        self.assertEqual(
            provider_failure_accounting(prepared),
            ProviderFailureAccounting(),
        )
        cancellation = asyncio.CancelledError("PRIVATE_CANCELLATION")
        record_provider_failure_accounting(
            cancellation,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=17,
            usage=ProviderUsage(input_tokens=4),
        )
        self.assertEqual(
            provider_failure_accounting(cancellation),
            ProviderFailureAccounting(
                ProviderCallState.SUBMITTED,
                17,
                ProviderUsage(input_tokens=4),
            ),
        )
        self.assertNotIn("PRIVATE", repr(provider_failure_accounting(cancellation)))

    def test_usage_requires_at_least_one_exact_nonnegative_nonboolean_counter(
        self,
    ) -> None:
        self.assertEqual(ProviderUsage(input_tokens=0).input_tokens, 0)
        with self.assertRaisesRegex(ValueError, "at least one"):
            ProviderUsage()
        for value in (-1, True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                ProviderUsage(input_tokens=value)  # type: ignore[arg-type]

    def test_binding_default_remains_compatible(self) -> None:
        binding_fields = tuple(ProviderBinding.__dataclass_fields__)
        self.assertEqual(
            binding_fields,
            ("vendor", "definition", "staging_directory", "binding_components"),
        )


class AgentContextBoundaryTests(unittest.TestCase):
    """Verify facade ownership, import direction, and unchanged public surfaces."""

    def test_exact_internal_facade_and_public_facades_are_unchanged(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ProviderCallKind),
            (
                "unclassified",
                "debate",
                "conversation",
                "conversation_retrieval_initial",
                "conversation_retrieval_continuation",
                "collaboration_initial",
                "collaboration_continuation",
                "writing_artifact_reference_read",
            ),
        )
        self.assertEqual(
            tuple(agent_context.__all__),
            (
                "ContextComponentBytes",
                "ContextComponentKind",
                "JsonPath",
                "ProviderCallAccounting",
                "ProviderCallKind",
                "ProviderCallState",
                "ProviderJsonEncodingError",
                "ProviderPayloadAccounting",
                "ProviderUsage",
                "account_provider_payload",
                "canonical_provider_json",
            ),
        )
        self.assertEqual(len(story_writing_agents.__all__), 104)
        self.assertEqual(len(writing_artifacts.__all__), 31)
        self.assertTrue(
            set(agent_context.__all__).isdisjoint(story_writing_agents.__all__)
        )
        self.assertFalse(hasattr(story_writing_agents, "ProviderCallAccounting"))

    def test_accounting_is_a_standard_library_leaf_and_facade_imports_only_it(
        self,
    ) -> None:
        source_root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "agent_context"
        )
        accounting_tree = ast.parse(
            (source_root / "accounting.py").read_text(encoding="utf-8")
        )
        self.assertFalse(
            [
                node
                for node in ast.walk(accounting_tree)
                if isinstance(node, ast.ImportFrom) and node.level
            ]
        )
        facade_tree = ast.parse(
            (source_root / "__init__.py").read_text(encoding="utf-8")
        )
        relative_imports = [
            node
            for node in ast.walk(facade_tree)
            if isinstance(node, ast.ImportFrom) and node.level
        ]
        self.assertEqual(len(relative_imports), 1)
        self.assertEqual(relative_imports[0].module, "accounting")


if __name__ == "__main__":
    unittest.main()
