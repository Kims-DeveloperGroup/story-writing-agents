"""Existing backend decoder Cases and their single-owner test producers."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from story_writing_agents import AgentReply, AgentRequest, ConversationRetrievalActionKind, ConversationRetrievalAnswerAction, ConversationRetrievalReadAction, ConversationRetrievalRequest, ConversationRetrievalSearchAction, Phase
from story_writing_agents.models import AgentReplyNextPhase
from story_writing_agents.structured_output_validation import diagnose_json_text, failure_issues
from story_writing_agents.errors import BackendProtocolError, ConversationRetrievalSemanticError
from story_writing_agents import conversation_retrieval_wire, provider_direct_call_wire
from tests.conversation_retrieval_wire_fixture import readable_context, retrieval_request
from tests.provider_wire_fixture import direct_reply, direct_response
import story_writing_agents.backend_reply_decoding as backend_reply_decoding

def retrieval_action_json(request: ConversationRetrievalRequest,
    *, kind: ConversationRetrievalActionKind,
    action_index: int, **fields: object,
) -> str:
    """Encode one exact provider action with stable insertion order."""

    return direct_response(json.dumps({
            "action": {"kind": kind.value,
                **{("message" if key == "content" else key): value
                    for key, value in fields.items() if value is not None
                },},
        }, ensure_ascii=False,
        separators=(",", ":"),), {"request_type": "answer_with_conversation_history"})

def needle_search(request):
    return retrieval_action_json(
        request, kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
        action_index=1, query="Needle source",)

def read_first_search_hit(request):
    return retrieval_action_json(request, kind=ConversationRetrievalActionKind.READ_HISTORY,
        action_index=2, citation_id="s1h1", start_sequence=1, end_sequence=None,)


class BackendReplyDecodingDefinitions:
    def test_strict_phase_reply_mapping(self) -> None:
        digest = "sha256:" + "a" * 64
        identity = ("reviewed-proposal", digest)
        for phase, value, expected in (
            (Phase.DRAFT, {"message": "draft"}, AgentReply.candidate("r2", "draft")),
            (Phase.AUDIT, {"verdict": "approve", "message": ""}, AgentReply.approval("r2")),
            (Phase.AUDIT, {"verdict": "conflict", "message": "revise"}, AgentReply.conflict("r2", "revise")),
            (Phase.SYNTHESIZE, {"next": "repair", "message": "repair"}, AgentReply.synthesis("r2", "repair", AgentReplyNextPhase.REPAIR)),
            (Phase.SYNTHESIZE, {"next": "deadlock", "message": "blocked"}, AgentReply.deadlock("r2", "blocked")),
            (Phase.FINALIZE, {"reviewed_proposal_id": identity[0], "reviewed_proposal_digest": digest},
             AgentReply.finalization("r2", *identity)),
        ):
            self.assertEqual(backend_reply_decoding.decode_reply(phase, direct_reply(value, phase.value), revision_id="r2"), expected)
        request = AgentRequest("session", "debate", "lead_orchestrator", Phase.FINALIZE, 1, "r2", "Finalize.", "")
        self.assertIsNone(request.expected_reviewed_proposal)
        self.assertTrue(next(field for field in fields(AgentRequest) if field.name == "expected_reviewed_proposal").kw_only)
        request = replace(request, expected_reviewed_proposal=identity)
        self.assertEqual(request.expected_reviewed_proposal, identity)
        with self.assertRaises(ValueError):
            replace(request, phase=Phase.DRAFT)
        schema = provider_direct_call_wire.phase_output_schema(Phase.FINALIZE, expected_reviewed_proposal=identity)
        value = {"reviewed_proposal_id": identity[0], "reviewed_proposal_digest": digest}
        self.assertEqual(diagnose_json_text(direct_reply(value, "finalize"), schema), ())
        self.assertEqual(schema["properties"]["reviewed_proposal_id"]["enum"], [identity[0]])
        self.assertEqual(schema["properties"]["reviewed_proposal_digest"]["enum"], [digest])
        for field, wrong in (("reviewed_proposal_id", "different-proposal"),
                             ("reviewed_proposal_digest", "sha256:" + "f" * 64)):
            self.assertTrue(diagnose_json_text(direct_reply({**value, field: wrong}, "finalize"), schema))
        for identifier, invalid_digest in (("../unsafe", digest), (identity[0], "sha256:" + "a" * 63),
                                          (identity[0], "sha256:" + "A" * 64), (identity[0], "a" * 64)):
            with self.assertRaises((TypeError, ValueError)):
                replace(request, expected_reviewed_proposal=(identifier, invalid_digest))
            with self.assertRaises(BackendProtocolError) as caught:
                backend_reply_decoding.decode_reply(Phase.FINALIZE, json.dumps({"reviewed_proposal_id": identifier,
                    "reviewed_proposal_digest": invalid_digest}), revision_id="r2")
            self.assertNotIn("unsafe", str(caught.exception))
        for invalid_identity in ([*identity], (identity[0],), (identity[0], digest, "extra")):
            with self.assertRaises((TypeError, ValueError)):
                replace(request, expected_reviewed_proposal=invalid_identity)
        with self.assertRaises(ValueError):
            provider_direct_call_wire.phase_output_schema(Phase.DRAFT, expected_reviewed_proposal=identity)
        self.assertEqual(backend_reply_decoding.decode_reply(Phase.DRAFT, direct_reply({"message": " \t"}, "draft"), revision_id="r2").content, " \t")
        invalid_values = ((Phase.AUDIT, '{"verdict":"maybe","message":""}'),
            (Phase.SYNTHESIZE, '{"next":"unknown","message":"ambiguous"}'),
            (Phase.SYNTHESIZE, '{"next":"deadlock","message":"blocked","extra":true}'),
            (Phase.DRAFT, '{"verdict":"approve","message":"ok"}'),
            (Phase.DRAFT, '{"message":"one","message":"two"}'))
        for phase, value in invalid_values:
            with self.subTest(phase=phase, value=value):
                with self.assertRaises(BackendProtocolError):
                    backend_reply_decoding.decode_reply(phase, value, revision_id="r2")
        for phase, value, error, issue in (
            (Phase.AUDIT, {"verdict": "PRIVATE", "message": "Visible."}, BackendProtocolError, "$.verdict: expected verdict approve or conflict"),
            (Phase.SYNTHESIZE, {"next": "PRIVATE", "message": "Continue."}, BackendProtocolError, "$.next: expected repair, finalization, review_completion, or deadlock"),
            (Phase.AUDIT, {"verdict": "conflict", "message": ""}, BackendProtocolError, "$.response_text: expected nonempty text"),
            (Phase.FINALIZE, {"reviewed_proposal_id": "valid", "reviewed_proposal_digest": "PRIVATE"}, BackendProtocolError,
             "$.reviewed_proposal_digest: expected sha256: followed by exactly 64 lowercase hexadecimal digits"),
            (Phase.FINALIZE, {"reviewed_proposal_id": "PRIVATE/id", "reviewed_proposal_digest": "sha256:" + "a" * 64}, BackendProtocolError,
             "$.reviewed_proposal_id: expected a canonical reviewed proposal identifier"),
        ):
            with self.assertRaises(error) as caught:
                backend_reply_decoding.decode_reply(phase, direct_reply(value, phase.value), revision_id="r2")
            issues = failure_issues(caught.exception) or (getattr(caught.exception, "issue", None),)
            self.assertEqual(issues, ("$: expected exact current response_type and schema fields" if ((phase is Phase.AUDIT and value.get("verdict") == "PRIVATE") or (phase is Phase.SYNTHESIZE and value.get("next") == "PRIVATE")) else issue,))
            self.assertNotIn("PRIVATE", repr(issues))
        review_schema = provider_direct_call_wire.phase_output_schema(Phase.SYNTHESIZE, review_only=True)
        self.assertEqual(review_schema["properties"]["next_consensus_step"]["enum"], ["complete_review", "report_deadlock"])
        with self.assertRaises(ValueError):
            provider_direct_call_wire.phase_output_schema(Phase.DRAFT, review_only=True)

    def test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors(
        self,
    ) -> None:
        request = retrieval_request()
        valid_search = needle_search(request)
        valid_read = read_first_search_hit(request)
        valid_answer = retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.ANSWER,
            action_index=3,
            content="submit_result",
            citation_ids=["s1h1"],
        )
        decoded_actions = tuple(
            backend_reply_decoding.decode_conversation_retrieval_action(
                payload,
                request=request,
                action_index=action_index,
            )
            for action_index, payload in enumerate(
                (valid_search, valid_read, valid_answer),
                start=1,
            )
        )
        self.assertEqual(
            tuple(type(action) for action in decoded_actions),
            (
                ConversationRetrievalSearchAction,
                ConversationRetrievalReadAction,
                ConversationRetrievalAnswerAction,
            ),
        )
        decoded_read = decoded_actions[1]
        decoded_answer = decoded_actions[2]
        for action, expected_index in zip(decoded_actions, (1, 2, 3), strict=True):
            self.assertEqual(action.session_id, request.session_id)
            self.assertEqual(action.conversation_id, request.conversation_id)
            self.assertEqual(action.turn_id, request.turn_id)
            self.assertEqual(action.action_index, expected_index)
        assert type(decoded_read) is ConversationRetrievalReadAction
        assert type(decoded_answer) is ConversationRetrievalAnswerAction
        self.assertIsNone(decoded_read.end_sequence)
        self.assertEqual(decoded_answer.citation_ids, ("s1h1",))

        envelope = json.loads(valid_read)
        values = envelope["agent_action"]
        missing_start = dict(values)
        missing_start.pop("start_sequence_number")
        extra = dict(values, unexpected=True)
        action_text = json.dumps(values, separators=(",", ":"))
        malformed_cases = (
            ("not-json", "malformed JSON"),
            ("[]", "JSON object"),
            (json.dumps(values), "invalid schema"),
            ("{}", "invalid schema"),
            (json.dumps({"agent_action": []}), "action must be a JSON object"),
            (json.dumps({"agent_action": values, "unexpected": True}), "invalid schema"),
            (json.dumps({"agent_action": missing_start}), "invalid schema"),
            (json.dumps({"agent_action": extra}), "invalid schema"),
            (json.dumps({"agent_action": dict(values, search_query="mixed")}), "invalid schema"),
            (
                json.dumps({"agent_action": dict(values, action_type="unknown")}),
                "kind is unknown",
            ),
            (
                json.dumps({"agent_action": dict(values, action_index=True)}),
                "invalid schema",
            ),
            (
                '{"agent_action":' + action_text + ',"agent_action":' + action_text + "}",
                "duplicates a field",
            ),
            (
                '{"agent_action":{"action_type":"submit_result","action_type":"submit_result",'
                '"message":"submit_result","citation_ids":[]}}',
                "duplicates a field",
            ),
        )
        for payload, message in malformed_cases:
            with self.subTest(message=message):
                shape_error = message not in {"malformed JSON", "duplicates a field"}
                with self.assertRaisesRegex(BackendProtocolError, "current action contract" if shape_error else message) as caught:
                    backend_reply_decoding.decode_conversation_retrieval_action(payload, request=request, action_index=1)
                if shape_error:
                    body = json.loads(payload)
                    if not isinstance(body, dict) or set(body) != {"agent_action"}:
                        issue = "$: expected an object containing exactly agent_action"
                    elif not isinstance(body["agent_action"], dict):
                        issue = "$.agent_action: expected an object"
                    elif body["agent_action"].get("action_type") not in {"search_conversation_history", "read_conversation_history", "submit_result"}:
                        issue = "$.agent_action.action_type: expected search_conversation_history, read_conversation_history, or submit_result"
                    else:
                        issue = "$.agent_action: expected action_type, citation_id, start_sequence_number, and optional end_sequence_number"
                    self.assertEqual(failure_issues(caught.exception), (issue,))

        reversed_range = dict(values, start_sequence_number=2, end_sequence_number=1)
        with self.assertRaises(ConversationRetrievalSemanticError):
            backend_reply_decoding.decode_conversation_retrieval_action(
                json.dumps({"agent_action": reversed_range}),
                request=request,
                action_index=1,
            )
        duplicate_citations = json.loads(
            retrieval_action_json(
                request,
                kind=ConversationRetrievalActionKind.ANSWER,
                action_index=1,
                content="submit_result",
                citation_ids=["s1h1", "s1h1"],
            )
        )
        with self.assertRaises(ConversationRetrievalSemanticError):
            backend_reply_decoding.decode_conversation_retrieval_action(
                json.dumps(duplicate_citations),
                request=request,
                action_index=1,
            )

    def test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence(self) -> None:
        request = retrieval_request()
        action = {"kind": "read_history", "citation_id": "s1h1", "start_sequence": 1}
        decoded = backend_reply_decoding.decode_conversation_retrieval_action(
            direct_response(json.dumps({"action": action}), {"request_type": "answer_with_conversation_history"}), request=request, action_index=2)
        self.assertIs(type(decoded), ConversationRetrievalReadAction)
        self.assertIsNone(decoded.end_sequence)
        explicit_null = direct_response(json.dumps({"action": {**action, "end_sequence": None}}), {"request_type": "answer_with_conversation_history"})
        self.assertEqual(diagnose_json_text(explicit_null, conversation_retrieval_wire.retrieval_action_schema(
            allowed_actions=(ConversationRetrievalActionKind.READ_HISTORY,))),
            ("$.agent_action.end_sequence_number: expected integer, got null",))
        with self.assertRaises(BackendProtocolError):
            backend_reply_decoding.decode_conversation_retrieval_action(explicit_null, request=request, action_index=2)
        identifier = "expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}"
        nonempty = "Codex conversation retrieval citation_ids must be an array of non-empty text"
        answer = {"kind": "answer", "message": "Visible."}
        for value, path, rule in (
            ({**action, "citation_id": "PRIVATE/id"}, "citation_id", identifier),
            ({**action, "start_sequence": 2, "end_sequence": 1}, "end_sequence",
             "end_sequence must be greater than or equal to start_sequence"),
            ({**answer, "citation_ids": ["PRIVATE/id"]}, "citation_ids[0]", identifier),
            ({**answer, "citation_ids": ["s1h1", "s1h1"]}, "citation_ids", "citation_ids must be unique"),
            ({**answer, "citation_ids": ["s1h1", ""]}, "citation_ids[1]", nonempty),
            ({**answer, "citation_ids": ["s1h1", "PRIVATE\ud800"]}, "citation_ids[1]",
             "Codex conversation retrieval citation_ids must be valid UTF-8"),
            ({**answer, "citation_ids": ["PRIVATE\ud800", ""]}, "citation_ids[1]", nonempty),
        ):
            error = ConversationRetrievalSemanticError if path in {"end_sequence", "citation_ids"} else BackendProtocolError
            with self.assertRaises(error) as caught:
                backend_reply_decoding.decode_conversation_retrieval_action(direct_response(json.dumps({"action": value}), {"request_type": "answer_with_conversation_history"}), request=request, action_index=2)
            issues = failure_issues(caught.exception)
            self.assertEqual(issues, (f"$.action.{path}: {rule}",))
            self.assertNotIn("PRIVATE", repr(issues))
