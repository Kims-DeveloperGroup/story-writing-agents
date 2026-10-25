"""Exact provider-neutral context accounting contracts."""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import math
import unittest
from pathlib import Path

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents import agent_context
from story_writing_agents.agent_context import (
    ContextComponentBytes, ContextComponentKind,
    ProviderCallAccounting, ProviderCallKind, ProviderCallState,
    ProviderJsonEncodingError, ProviderPayloadAccounting, ProviderUsage,
    account_provider_payload, canonical_provider_json, )
from story_writing_agents.provider_adapter import (
    ProviderBinding, ProviderFailureAccounting, ProviderInvocation, ProviderInvocationError,
    ProviderResult, provider_failure_accounting, record_provider_failure_accounting, )
from story_writing_agents.agent_context.request_compaction import compact_context_payload


def _property_count(value):
    if isinstance(value, dict):
        return len(value) + sum(_property_count(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_property_count(item) for item in value)
    return 0

def _shared_context_payload(event=None):
    if event is None:
        event = {"instruction": "Retain this exact request 한🙂\n" * 40,
                 "values": [True, 1, 1.0, None], "nested": {"context_ref": "literal-user-data"}}
    return {"task_input": {"input": "CURRENT_한🙂", "document_change_requests": dict(reversed(event.items())),
                  "confirmed_proposal_drafting_context": {
                      "request": event, "decision": event, "start": event,
                      "selected_inputs": [{"conversation_event": event}]}},
            "session_history": {"conversations": [{"records": [{"details": event}], "document_change_requests": event}]}}

def _expanded_context_payload(payload):
    if "shared_context_objects" not in payload:
        return payload
    result = copy.deepcopy(dict(payload))
    table = result.pop("shared_context_objects")
    result.pop("context_reuse")
    def expand(value, shape):
        if type(value) is not dict:
            return value
        if set(value) == {"shared_context_object_id"}:
            return copy.deepcopy(table[value["shared_context_object_id"]])
        value = dict(value)
        children = {
            "context": (("basis", "basis"), ("authoring_admission", "admission"),
                        ("core_document_readiness", "readiness"), ("core_document_assessment", "assessment")),
            "basis": (("authoring_admission", "admission"), ("core_document_readiness", "readiness")),
            "admission": (("assessment", "literal"),),
            "readiness": (("assessment", "assessment"),),
            "assessment": (("book_basis", "literal"),),
        }.get(shape, ())
        for field, child_shape in children:
            if field in value:
                value[field] = expand(value[field], child_shape)
        if shape == "assessment":
            rows = value.get("document_judgments")
            if type(rows) in (list, tuple):
                value["document_judgments"] = type(rows)(expand(row, "literal") for row in rows)
        return value

    def resolve(owner, key):
        value = owner.get(key)
        if value is not None:
            owner[key] = expand(value, "context")
    current, history = result.get("task_input"), result.get("session_history")
    if type(current) is dict:
        resolve(current, "document_change_requests")
        execution = current.get("confirmed_proposal_drafting_context")
        if type(execution) is dict:
            for key in ("request", "decision", "start"):
                resolve(execution, key)
            for item in execution.get("selected_inputs", []):
                resolve(item, "conversation_event")
    if type(history) is dict:
        for conversation in history.get("conversations", []):
            resolve(conversation, "document_change_requests")
            for record in conversation.get("records", []):
                resolve(record, "details")
    return result

class CanonicalProviderJsonTests(unittest.TestCase):
    """Verify the exact strict application/provider JSON boundary."""

    def test_ascii_is_compact_and_preserves_insertion_order(self) -> None:
        value = {"second": [1, True, None], "first": "text"}
        self.assertEqual(
            canonical_provider_json(value),
            b'{"second":[1,true,null],"first":"text"}', )

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
            canonical_provider_json({"tuple": (0, -2, 1.5), "left": shared, "right": shared}),
            b'{"tuple":[0,-2,1.5],"left":["same"],"right":["same"]}', )
        payload = _shared_context_payload()
        payload["unrelated"] = {"shared_context_object_id": "o1"}
        before = copy.deepcopy(payload)
        paths = {("task_input",): ContextComponentKind.CURRENT_INPUT,
                 ("session_history",): ContextComponentKind.CONVERSATION_CONTEXT}
        compact, _ = compact_context_payload(payload, paths)
        self.assertEqual(payload, before)
        self.assertEqual(_expanded_context_payload(compact), before)
        self.assertEqual(compact["context_reuse"]["format"], "shared-objects-v1")
        self.assertEqual(list(compact["shared_context_objects"]), ["o1"])
        self.assertEqual(canonical_provider_json(compact).count(b'"shared_context_object_id":"o1"'), 8)
        self.assertLess(len(canonical_provider_json(compact)), len(canonical_provider_json(before)))
        self.assertLess(_property_count(compact), _property_count(before))
        values = [{"body": "한🙂" * 100, "scalar": value} for value in (True, 1, 1.0, [1, 2], [2, 1])]
        typed = {"session_history": {"conversations": [{"records": [{"details": value} for value in values * 2]}]}}
        encoded, _ = compact_context_payload(typed, paths)
        self.assertNotIn("shared_context_objects", encoded)
        self.assertEqual(json.dumps(_expanded_context_payload(encoded), sort_keys=True),
                         json.dumps(typed, sort_keys=True))
        collisions = ({"shared_context_objects": {}}, {"context_reuse": {}})
        noops = [{**payload, **collision} for collision in collisions]
        marker = copy.deepcopy(payload)
        marker["task_input"]["document_change_requests"] = {"shared_context_object_id": "user data"}
        noops += [marker, _shared_context_payload({"body": "x" * 244}),
                  _shared_context_payload({"body": "x" * 245})]
        noops += [{"task_input": {"confirmed_proposal_drafting_context": {"request": value, "decision": value}}}
                  for value in ({"small": "x"}, {"body": "x" * 256})]
        noops += [{"task_input": {"confirmed_proposal_drafting_context": {"request": values[0]}}}]
        for original in noops:
            result, result_paths = compact_context_payload(original, paths)
            self.assertEqual(canonical_provider_json(result), canonical_provider_json(original))
            self.assertEqual(result_paths, paths)
        rich = {"body": "x" * 245, "facts": {"state": "open", "origin": {"kind": "history"}}}
        boundary, _ = compact_context_payload(_shared_context_payload(rich), paths)
        self.assertIn("shared_context_objects", boundary)
        self.assertLess(_property_count(boundary), _property_count(_shared_context_payload(rich)))

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
                    diagnostic, )
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
        schema = {"type": "object", "properties": {"content": {"type": "string"}}}
        payload = _shared_context_payload()
        history_only = {"content": "Prior history 한🙂" * 70,
                        "facts": {"state": "retained", "origin": {"kind": "history"}}}
        payload["session_history"]["conversations"][0]["records"] += [{"details": history_only}] * 2
        request = ("task_input", "confirmed_proposal_drafting_context", "request")
        decision = ("task_input", "confirmed_proposal_drafting_context", "decision")
        mandatory = (*request, "instruction")
        paths = {("task_input",): ContextComponentKind.CURRENT_INPUT, ("session_history",): ContextComponentKind.CONVERSATION_CONTEXT,
                 mandatory: ContextComponentKind.MANDATORY_REFERENCE_CONTENT, (*decision, "nested"): ContextComponentKind.ROLE_SYSTEM_PROMPT}
        compact, compact_paths = compact_context_payload(payload, paths, (mandatory,))
        execution = compact["task_input"]["confirmed_proposal_drafting_context"]
        original = payload["task_input"]["confirmed_proposal_drafting_context"]
        self.assertEqual((execution["request"], execution["decision"]),
                         (original["request"], original["decision"]))
        self.assertEqual(_expanded_context_payload(compact), payload)
        self.assertEqual(compact_paths[mandatory], paths[mandatory])
        for alias, value in compact["shared_context_objects"].items():
            expected = (ContextComponentKind.CONVERSATION_CONTEXT if value == history_only
                        else ContextComponentKind.CURRENT_INPUT)
            self.assertEqual(compact_paths[("shared_context_objects", alias)], expected)
        payload_json, accounting = account_provider_payload(
            schema, compact, call_kind=ProviderCallKind.CONVERSATION,
            component_paths=compact_paths, mandatory_reference_paths=(mandatory,))
        components = self._components(accounting)
        self.assertEqual(payload_json, canonical_provider_json(compact))
        self.assertEqual(accounting.schema_utf8_bytes, len(canonical_provider_json(schema)))
        self.assertEqual(accounting.payload_utf8_bytes, len(payload_json))
        self.assertEqual(sum(item.serialized_utf8_bytes for item in accounting.components),
                         accounting.serialized_input_utf8_bytes)
        self.assertEqual(set(components), {ContextComponentKind.OUTPUT_SCHEMA, ContextComponentKind.CONTROL,
                         ContextComponentKind.CURRENT_INPUT, ContextComponentKind.CONVERSATION_CONTEXT,
                         ContextComponentKind.MANDATORY_REFERENCE_CONTENT, ContextComponentKind.ROLE_SYSTEM_PROMPT})
        self.assertEqual(accounting.raw_mandatory_reference_utf8_bytes,
                         len(original["request"]["instruction"].encode("utf-8")))

    def test_whole_range_and_multiple_reference_bodies_exclude_only_raw_bytes(self) -> None:
        schema = {"type": "object"}
        def reference(path, content, line_range=None):
            return {"reference": {"path": path, "line_range": line_range}, "content": content}
        reference_cases = (
            ("whole", [reference("core_documents/global_outline.md", "whole line\n")]),
            ("range", [reference("core_documents/character_sheets.md", "범위 🙂\n", {"start": 2, "end": 2})]),
            ("multiple", [reference("core_documents/global_outline.md", 'quote " slash \\ newline\n'),
                          reference("manuscript/part-01/scene.md", "한🙂\nsecond\n", {"start": 1, "end": 2})]),
        )
        for label, references in reference_cases:
            with self.subTest(case=label):
                payload = {"operation": "conversation", "untrusted_writing_artifact_context": references}
                component_paths = {("untrusted_writing_artifact_context",):
                                   ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR}
                raw_paths = tuple(("untrusted_writing_artifact_context", index, "content")
                                  for index in range(len(references)))
                component_paths.update({path: ContextComponentKind.MANDATORY_REFERENCE_CONTENT for path in raw_paths})
                payload_json, accounting = account_provider_payload(
                    schema, payload, call_kind=ProviderCallKind.CONVERSATION,
                    component_paths=component_paths, mandatory_reference_paths=raw_paths)
                raw_bytes = sum(len(item["content"].encode("utf-8")) for item in references)
                components = self._components(accounting)
                content_component = components[ContextComponentKind.MANDATORY_REFERENCE_CONTENT]
                descriptor_component = components[ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR]
                self.assertEqual(accounting.raw_mandatory_reference_utf8_bytes, raw_bytes)
                self.assertEqual(content_component.raw_mandatory_reference_utf8_bytes, raw_bytes)
                self.assertGreater(content_component.serialized_utf8_bytes, raw_bytes)
                self.assertGreater(descriptor_component.serialized_utf8_bytes, 0)
                self.assertIn(b"line_range", payload_json)
                self.assertEqual(sum(item.serialized_utf8_bytes for item in accounting.components),
                                 accounting.serialized_input_utf8_bytes)

    def test_call_totals_keep_reference_envelopes_and_escaping_counted(self) -> None:
        binding = ( ContextComponentBytes(ContextComponentKind.BINDING_STRUCTURE, 11),
            ContextComponentBytes(ContextComponentKind.ROLE_SYSTEM_PROMPT, 7), )
        payload = { "references": [ {
                    "reference": { "path": "manuscript/part-01/a.md", "line_range": {"start": 1, "end": 1}, },
                    "content": '"한🙂"\n',
                } ] }
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
            actual_transport_utf8_bytes=payload_accounting.payload_utf8_bytes, )
        self.assertEqual(call.binding_instruction_utf8_bytes, 18)
        self.assertEqual(
            call.gross_model_input_utf8_bytes,
            18 + payload_accounting.serialized_input_utf8_bytes, )
        self.assertEqual(
            call.non_reference_model_input_utf8_bytes,
            call.gross_model_input_utf8_bytes
            - len(payload["references"][0]["content"].encode("utf-8")), )
        self.assertEqual(
            call.submitted_model_input_utf8_bytes,
            call.gross_model_input_utf8_bytes, )
        self.assertIsNone(
            ProviderCallAccounting(payload_accounting).submitted_model_input_utf8_bytes )

    def test_invalid_paths_partitions_and_components_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identify payload values"):
            account_provider_payload(
                {},
                {"present": "value"},
                component_paths={("missing",): ContextComponentKind.CURRENT_INPUT}, )
        with self.assertRaisesRegex(ValueError, "mandatory-reference paths"):
            account_provider_payload(
                {},
                {"content": "value"},
                component_paths={("content",): ContextComponentKind.CURRENT_INPUT},
                mandatory_reference_paths=(("content",),), )
        with self.assertRaisesRegex(ValueError, "raw mandatory-reference"):
            ContextComponentBytes(
                ContextComponentKind.CURRENT_INPUT,
                1,
                raw_mandatory_reference_utf8_bytes=1, )
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
            b'{"content":"PRIVATE_PAYLOAD \xed\x95\x9c\xf0\x9f\x99\x82"}', )
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
        enriched = ProviderResult("thread", "response", usage=ProviderUsage(1, 2, 3), actual_transport_utf8_bytes=9)
        self.assertEqual(legacy, enriched)
        self.assertEqual(enriched.usage, ProviderUsage(1, 2, 3))
        self.assertEqual(enriched.actual_transport_utf8_bytes, 9)

    def test_failure_accounting_covers_prepared_and_submitted_cancellation(
        self,
    ) -> None:
        prepared = ProviderInvocationError("fixed", taint_existing_thread=False)
        self.assertEqual(
            provider_failure_accounting(prepared),
            ProviderFailureAccounting(), )
        cancellation = asyncio.CancelledError("PRIVATE_CANCELLATION")
        record_provider_failure_accounting(
            cancellation,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=17,
            usage=ProviderUsage(input_tokens=4), )
        self.assertEqual(
            provider_failure_accounting(cancellation),
            ProviderFailureAccounting(ProviderCallState.SUBMITTED, 17, ProviderUsage(input_tokens=4)),
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
            ("vendor", "definition", "staging_directory", "binding_components"), )


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
                "ProviderStreamEvent",
                "ProviderStreamEventKind",
                "ProviderStreamSink",
                "ProviderUsage",
                "account_provider_payload",
                "active_provider_stream_sink",
                "bind_provider_stream_sink",
                "canonical_provider_json",
                "reset_provider_stream_sink",
            ),
        )
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(len(writing_artifacts.__all__), 80)
        self.assertTrue(
            set(agent_context.__all__).isdisjoint(story_writing_agents.__all__) )
        self.assertFalse(hasattr(story_writing_agents, "ProviderCallAccounting"))

    def test_accounting_is_a_standard_library_leaf_and_facade_imports_only_it(
        self,
    ) -> None:
        source_root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "agent_context" )
        accounting_tree = ast.parse(
            (source_root / "accounting.py").read_text(encoding="utf-8") )
        self.assertFalse(
            [
                node
                for node in ast.walk(accounting_tree)
                if isinstance(node, ast.ImportFrom) and node.level
            ] )
        facade_tree = ast.parse(
            (source_root / "__init__.py").read_text(encoding="utf-8") )
        relative_imports = [
            node
            for node in ast.walk(facade_tree)
            if isinstance(node, ast.ImportFrom) and node.level ]
        self.assertEqual(len(relative_imports), 2)
        self.assertEqual(
            {node.module for node in relative_imports},
            {"accounting", "progress"}, )


if __name__ == "__main__":
    unittest.main()
