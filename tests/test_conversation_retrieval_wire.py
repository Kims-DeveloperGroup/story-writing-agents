"""Project conversation retrieval values into provider wire contracts."""
from __future__ import annotations

import json
import unittest

from story_writing_agents import ConversationRetrievalActionKind, ConversationRetrievalSearchObservation, conversation_retrieval_wire
from story_writing_agents.conversation_retrieval import ConversationRagContext, serialize_conversation_retrieval_observation
from story_writing_agents.structured_output_validation import diagnose_json_text
from tests.conversation_retrieval_wire_fixture import provider_rag_context as _provider_rag_context, retrieval_request as _retrieval_request


class ConversationRetrievalWireTests(unittest.TestCase):

    def test_retrieval_rag_context_is_first_call_only_and_reference_authoritative(
        self,
    ) -> None:
        contexts = (_provider_rag_context(), ConversationRagContext.disabled())
        for context in contexts:
            with self.subTest(outcome=context.outcome):
                request = _retrieval_request(rag_context=context)
                initial = conversation_retrieval_wire.initial_payload(
                    request,
                    allowed_actions=(
                        ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
                        ConversationRetrievalActionKind.ANSWER,
                    ),
                )
                self.assertEqual(
                    tuple(initial),
                    (
                        "request_type",
                        "session_id",
                        "conversation_id",
                        "turn_id",
                        "agent_id",
                        "user_input_text",
                        "conversation_history",
                        "rag_context",
                        "conversation_retrieval_control",
                        "protocol_version",
                    ),
                )
                expected_rag = context.to_dict()
                for excerpt in expected_rag.get("excerpts", []): excerpt["content_text"] = excerpt.pop("content")
                self.assertEqual(initial["rag_context"], expected_rag)
                self.assertTrue(
                    any("rag_context" in rule for rule in initial["conversation_retrieval_control"]["retrieval_instructions"])
                )
                continuation = (
                    conversation_retrieval_wire.continuation_payload(
                        request,
                        action_index=2,
                        allowed_actions=(ConversationRetrievalActionKind.ANSWER,),
                        observation=serialize_conversation_retrieval_observation(ConversationRetrievalSearchObservation(
                    session_id=request.session_id, conversation_id=request.conversation_id,
                    turn_id=request.turn_id, action_index=1, hits=(), truncated=False)),
                    )
                )
                self.assertNotIn("rag_context", continuation)
                self.assertNotIn("user_input_text", continuation)
                self.assertNotIn("conversation_history", continuation)
                self.assertTrue(
                    any(
                        "rag_context" in rule
                        for rule in continuation["conversation_retrieval_control"]["retrieval_instructions"]
                    )
                )

        complete = contexts[0]
        reference = complete.excerpts[0].reference
        wire_reference = complete.to_dict()["excerpts"][0]["reference"]
        self.assertEqual(
            wire_reference,
            {
                "document": {
                    "key": {
                        "session_id": reference.document.key.session_id,
                        "document_id": reference.document.key.document_id,
                    },
                    "conversation_id": reference.document.conversation_id,
                    "start_turn_id": reference.document.start_turn_id,
                    "end_turn_id": reference.document.end_turn_id,
                    "start_sequence": reference.document.start_sequence,
                    "end_sequence": reference.document.end_sequence,
                    "revision_id": reference.document.revision_id,
                },
                "fragment_id": reference.fragment_id,
                "start_codepoint": reference.start_codepoint,
                "end_codepoint": reference.end_codepoint,
            },
        )
        serialized = json.dumps(
            conversation_retrieval_wire.initial_payload(
                _retrieval_request(rag_context=complete),
                allowed_actions=(ConversationRetrievalActionKind.ANSWER,),
            ),
            ensure_ascii=False,
        )
        for forbidden in (
            "provider-candidate-secret",
            "/private/provider/path",
            "provider-credential",
            "hidden-handle",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_retrieval_schema_uses_supported_strict_object_subset(self) -> None:
        schema = conversation_retrieval_wire.retrieval_action_schema()
        self.assertEqual(schema.get("type"), "object")
        self.assertNotIn("anyOf", schema)
        self.assertEqual(set(schema["properties"]), {"agent_action"})
        action_schema = schema["properties"]["agent_action"]
        self.assertEqual(len(action_schema["anyOf"]), 4)

        forbidden_keywords = {"oneOf", "minLength", "uniqueItems"}

        def assert_supported(value: object) -> None:
            if type(value) is dict:
                self.assertTrue(forbidden_keywords.isdisjoint(value))
                if value.get("type") == "array":
                    self.assertIs(type(value.get("minItems")), int)
                if value.get("type") == "object":
                    properties = value.get("properties")
                    self.assertIs(type(properties), dict)
                    assert type(properties) is dict
                    self.assertEqual(set(value.get("required", ())), set(properties))
                    self.assertIs(value.get("additionalProperties"), False)
                for child in value.values():
                    assert_supported(child)
            elif type(value) is list:
                for child in value:
                    assert_supported(child)

        assert_supported(schema)

    def test_retrieval_non_string_kind_reports_exact_type_and_common_issues(self) -> None:
        secret = "PRIVATE_NON_STRING_RETRIEVAL_KIND_한🙂"
        schema = conversation_retrieval_wire.retrieval_action_schema(
            allowed_actions=(ConversationRetrievalActionKind.READ_HISTORY,),
        )
        issues = diagnose_json_text(
            json.dumps({"agent_action": {
                "action_type": {"private": secret},
                "citation_id": 5,
                "start_sequence_number": "invalid",
            }}),
            schema,
        )
        self.assertEqual(issues, (
            '$.agent_action.action_type: expected one of ["read_conversation_history"], got object',
            "$.agent_action.citation_id: expected string, got integer",
            "$.agent_action.start_sequence_number: expected integer, got string",
        ))
        self.assertNotIn(secret, repr(issues))
        self.assertNotIn("private", repr(issues))
