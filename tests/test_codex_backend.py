"""Concrete Codex CLI backend tests with no credentials or network access."""
from __future__ import annotations
from tests.test_provider_adapter_generations import ProviderAdapterGenerationCases
from tests.test_provider_binding_call_lifetime import BindingCallLifetimeCases
from tests.test_provider_schema_spec import check_schema
import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import tempfile
import threading
import unittest
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from unittest import mock
from story_writing_agents import CONVERSATION_CONTEXT_POLICY, AgentBackend, AgentReply, AgentRequest, BoundAgent, CHARACTER_CUSTODIAN, ConversationContextWindow, ConversationEvent, ConversationEventKind, ConversationReply, ConversationRequest, ConversationRetrievalAction, ConversationRetrievalActionHandler, ConversationRetrievalActionKind, ConversationRetrievalAnswerAction, ConversationRetrievalObservation, ConversationRetrievalReadAction, ConversationRetrievalReadObservation, ConversationRetrievalReply, ConversationRetrievalRequest, ConversationRetrievalSearchAction, ConversationRetrievalSearchItem, ConversationRetrievalSearchObservation, ConversationRetrievalSource, EXECUTION_SPECIALIST, PLOT_ARCHITECT, Phase, PromptFileReference, ROSTER, ReplyTrace, ReplyTraceOrigin, SessionAccessState, SessionDurableState, Workspace
from story_writing_agents import cli as cli_module, conversation_retrieval_wire, provider_direct_call_wire
from story_writing_agents import backend as backend_module, provider_manifest_thread_store, provider_session_artifacts, provider_thread_records
from story_writing_agents.conversation_retrieval import CONVERSATION_RETRIEVAL_POLICY, serialize_conversation_retrieval_observation
from story_writing_agents.models import AgentReplyKind, AgentReplyNextPhase
from story_writing_agents.conversation_search import CollaborationHistoryCitation
from story_writing_agents.conversations import CollaborationReadableFieldKey
from story_writing_agents.backend import backend_configuration_snapshot
from story_writing_agents.structured_output_validation import diagnose_json_text, failure_issues
from story_writing_agents.agent_context import ContextComponentKind, ProviderCallAccounting, ProviderCallKind, ProviderCallState, ProviderUsage, account_provider_payload, canonical_provider_json
from story_writing_agents.agent_context.manifest import ContextContinuationIdentity, ContextScope, ContextSubjectKind
from story_writing_agents.agents import CORE_DOCUMENTS, LEAD_ORCHESTRATOR
from story_writing_agents.config import ConfigurationSnapshot
from story_writing_agents.errors import BackendProtocolError, ConfigurationError, ConversationRetrievalBudgetError, ConversationRetrievalObserverError, ConversationRetrievalSemanticError, SessionIdError, SessionNotFoundError, WorkspaceFormatError
from story_writing_agents.debug_log import DEBUG_LOG_RELATIVE_PATH, WorkspaceDebugLog
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.interactive_settings import ProviderRouteEntry, commit_prepared_settings_save, load_settings_state, prepare_settings_save, update_agent_backend
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.provider_execution_workspace import ProviderExecutionWorkspace, mint_provider_execution_workspace
from story_writing_agents.workspace.session_resources import provision_session_resources
from story_writing_agents.workspace.session_artifact_binding import SessionArtifactBindingError
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from story_writing_agents.writing_artifacts import ArtifactLineRange, ArtifactReference, ArtifactReferenceBatch, ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceInvocationCorrelation, ReferenceInvocationState, ReferenceOperationState, ReferenceReadAction, ReferenceReadActionKind, ReferenceReadRequest
from story_writing_agents.provider_adapter import ExecutionValueSource, ProviderInvocationError, ProviderResult, provider_failure_accounting, record_provider_failure_accounting
from story_writing_agents.provider_error_artifacts import PROVIDER_ERROR_ARTIFACT_DIRECTORY, PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY, WorkspaceProviderErrorArtifacts
from story_writing_agents.provider_process import ProcessRequest, run_process, stop_process
from story_writing_agents.models import StoryFocusedContextMode
from story_writing_agents.agent_context.request_compaction import compact_context_payload
from tests.test_agent_context_accounting import _shared_context_payload
from tests.provider_wire_fixture import assert_guidance, direct_reply
from tests.codex_native_process_fixture import CodexNativeProcessFixture, _context_window, _conversation_pair, _tree_snapshot
from tests.helpers import initialized_workspace, pristine_initialized_workspace
from story_writing_agents.agent_context.invocation import provider as ctx_provider

from tests.conversation_retrieval_wire_fixture import (
    provider_rag_context as _provider_rag_context, readable_context as _readable_context, retrieval_request as _retrieval_request,
)
from tests.backend_debug_log_fixture import (
    debug_signature as _debug_signature,
    mark_debug_session_attached as _mark_debug_session_attached,
    workspace_debug_records as _workspace_debug_records,
)

from tests.test_backend_reply_decoding import (
    BackendReplyDecodingDefinitions,
    retrieval_action_json as _retrieval_action_json,
    needle_search as _needle_search,
    read_first_search_hit as _read_first_search_hit,
)




def _fresh_literal_object_side_effect(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)


def _rejected_response_artifact_patch():
    return mock.patch.object(WorkspaceProviderErrorArtifacts,
        "record_rejected_response",)


def _wrapped_thread_taint_patch():
    return mock.patch.object(
        provider_manifest_thread_store, "taint_manifest_thread",
        wraps=provider_manifest_thread_store.taint_manifest_thread,
    )


def _wrapped_thread_store_patch():
    return mock.patch.object(
        provider_manifest_thread_store, "store_manifest_thread",
        wraps=provider_manifest_thread_store.store_manifest_thread,
    )


def _provider_error_records(workspace: Any) -> list[dict[str, object]]:
    """Read complete rejected-response artifacts, excluding guard leaves."""

    directory = workspace.path(PROVIDER_ERROR_ARTIFACT_DIRECTORY)
    if not directory.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))]


def _agent_exchange_records(workspace: Any) -> list[dict[str, object]]:
    """Read durable exchange JSONL in global workspace sequence order."""

    directory = workspace.path("debug_logs/agent_exchanges")
    if not directory.exists():
        return []
    records = []
    for path in directory.rglob("*.jsonl"):
        with path.open(encoding="utf-8", newline="\n") as stream:
            records.extend(json.loads(line) for line in stream)
    return sorted(records, key=lambda record: record["workspace_sequence"])


def _conversation_request(*,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a",
    turn_id: str = "turn_current",
    agent_id: str = PLOT_ARCHITECT, content: str = "visible user request",
    context: ConversationContextWindow | None = None,) -> ConversationRequest:
    return ConversationRequest(session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        agent_id=agent_id,
        content=content,
        context=_context_window() if context is None else context,)






class _RecordingRetrievalHandler:
    """Return exact typed observations and a reply while recording actions."""

    def __init__(self) -> None:
        self.actions: list[ConversationRetrievalAction] = []
        self.outcomes: list[ConversationRetrievalObservation | ConversationRetrievalReply
        ] = []
        self.source: ConversationRetrievalSource | None = None

    def handle_conversation_retrieval_action(
        self, action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        """Return the corresponding exact outcome for one decoded action."""

        self.actions.append(action)
        if type(action) is ConversationRetrievalSearchAction:
            self.source = ConversationRetrievalSource(citation=CollaborationHistoryCitation(
                    session_id=action.session_id, conversation_id="source_conversation",
                    event_id="source_event", sequence=1,
                    field_key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE, start_codepoint=0,
                    end_codepoint=len("Needle source"),),
            )
            outcome: ConversationRetrievalObservation | ConversationRetrievalReply = (
                ConversationRetrievalSearchObservation(session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    action_index=action.action_index, hits=(
                        ConversationRetrievalSearchItem(citation_id=f"s{action.action_index}h1",
                            source=self.source,
                            snippet='Needle "source" 한🙂',),
                    ), truncated=False,
                )
            )
        elif type(action) is ConversationRetrievalReadAction:
            assert self.source is not None
            outcome = ConversationRetrievalReadObservation(session_id=action.session_id,
                conversation_id=action.conversation_id,
                turn_id=action.turn_id, action_index=action.action_index,
                source_citation_id=action.citation_id, source=self.source,
                records=(),
                truncated=False,)
        else:
            assert type(action) is ConversationRetrievalAnswerAction
            outcome = ConversationRetrievalReply(session_id=action.session_id,
                conversation_id=action.conversation_id,
                turn_id=action.turn_id, content=action.content,
                sources=() if self.source is None else (self.source,),)
        self.outcomes.append(outcome)
        return outcome


class _RaisingRetrievalHandler:
    """Raise one configured failure after optionally accepting a search."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.calls = 0

    def handle_conversation_retrieval_action(
        self, _action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        """Record the action and raise the original configured failure."""

        self.calls += 1
        raise self.failure








def _provider_candidate_reply(*, role=PLOT_ARCHITECT, context_sources=("current_input",)):
    return replace(AgentReply.candidate("r0", "generated candidate"),
        trace=ReplyTrace(role=role,
            action="candidate", context_sources=context_sources,
            recovered_fields=(),
            origin=ReplyTraceOrigin.PROVIDER,),
    )


def _assert_provider_supported_schema(test: unittest.TestCase,
    specification: object,) -> None:
    """Assert the strict response-schema subset used by reference reads."""

    test.assertIs(type(specification), dict)
    assert type(specification) is dict
    allowed = {"type",
        "properties", "required",
        "additionalProperties", "items",
        "minItems", "maxItems",
        "anyOf",
        "enum",}
    test.assertTrue(set(specification) <= allowed)
    if "properties" in specification:
        properties = specification["properties"]
        test.assertIs(type(properties), dict)
        assert type(properties) is dict
        required = specification.get("required")
        test.assertIs(type(required), list)
        test.assertEqual(set(required), set(properties))
        test.assertIs(specification.get("additionalProperties"), False)
        for child in properties.values():
            _assert_provider_supported_schema(test, child)
    if "items" in specification:
        items = specification["items"]
        test.assertIn(type(items), {dict, bool})
        test.assertIsNot(items, False)
        if type(items) is dict:
            _assert_provider_supported_schema(test, items)
    if "anyOf" in specification:
        branches = specification["anyOf"]
        test.assertIs(type(branches), list)
        assert type(branches) is list
        test.assertTrue(branches)
        for branch in branches:
            _assert_provider_supported_schema(test, branch)
    if specification.get("type") == "array":
        test.assertIn("items", specification)
        for bound in ("minItems", "maxItems"):
            if bound in specification:
                test.assertIs(type(specification[bound]), int)


class ConversationModelContractTests(unittest.TestCase):
    def test_agent_reply_kinds_are_closed_and_fields_are_phase_exact(self) -> None:
        digest = "sha256:" + "a" * 64
        replies = (AgentReply.candidate("r0", "draft"),
            AgentReply.approval("r0"), AgentReply.conflict("r0", "repair this"),
            AgentReply.synthesis(
                "r0", "repair plan", AgentReplyNextPhase.REPAIR),
            AgentReply.synthesis(
                "r0", "ready", AgentReplyNextPhase.FINALIZATION),
            AgentReply.deadlock("r0", "conflict remains"),
            AgentReply.finalization("r0", "reviewed-proposal", digest),)
        self.assertEqual(tuple(reply.kind for reply in replies),
            (AgentReplyKind.CANDIDATE,
                AgentReplyKind.AUDIT, AgentReplyKind.AUDIT,
                AgentReplyKind.SYNTHESIS, AgentReplyKind.SYNTHESIS,
                AgentReplyKind.DEADLOCK,
                AgentReplyKind.FINALIZATION,),
        )
        self.assertEqual((replies[3].next_phase, replies[4].next_phase),
            (AgentReplyNextPhase.REPAIR, AgentReplyNextPhase.FINALIZATION),)
        self.assertTrue(replies[5].deadlocked)
        self.assertEqual(replies[6].reviewed_proposal_digest, digest)

        invalid = (dict(kind="candidate", revision_id="r0", content="draft"),
            dict(kind=AgentReplyKind.CANDIDATE, revision_id="r0", content=""), dict(
                kind=AgentReplyKind.CANDIDATE, revision_id="r0",
                content="draft",
                deadlocked=True,),
            dict(kind=AgentReplyKind.AUDIT, revision_id="r0"), dict(
                kind=AgentReplyKind.SYNTHESIS, revision_id="r0",
                content="proposal",),
            dict(kind=AgentReplyKind.DEADLOCK,
                revision_id="r0", content="blocked",
                deadlocked=False,),
            dict(kind=AgentReplyKind.FINALIZATION,
                revision_id="r0", reviewed_proposal_id="reviewed-proposal",
                reviewed_proposal_digest="a" * 64,),
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    AgentReply(**values)

    def test_exact_frozen_slotted_models_and_converse_signature(
        self,
    ) -> None:
        reply = ConversationReply(
            "story_session", "conversation_a", "turn_current", "reply")
        cases = (
            (
                ConversationRequest,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "agent_id",
                    "content",
                    "context",
                    "story_session_context",),
                _conversation_request(),),
            (
                ConversationReply,
                (
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "content",
                    "trace",
                    "validation_details",),
                reply,),
        )
        for model, names, value in cases:
            with self.subTest(model=model.__name__):
                model_fields = fields(model)
                self.assertEqual(tuple(field.name for field in model_fields), names)
                required_count = 6 if model is ConversationRequest else 4
                required_fields = model_fields[:required_count]
                self.assertTrue(
                    all(
                        field.default is MISSING and field.default_factory is MISSING
                        for field in required_fields)
                )
                self.assertIsNone(model_fields[required_count].default)
                if model is ConversationReply:
                    self.assertEqual(model_fields[-1].default, ())
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    value.content = "changed"

        signature = inspect.signature(BoundAgent.converse)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "request", "reference_read"),
        )
        self.assertIs(
            signature.parameters["request"].default,
            inspect.Parameter.empty,)
        self.assertIs(
            signature.parameters["reference_read"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(signature.parameters["reference_read"].default)
        hints = get_type_hints(BoundAgent.converse)
        self.assertIs(hints["request"], ConversationRequest)
        self.assertEqual(
            hints["reference_read"],
            get_type_hints(BoundAgent.invoke)["reference_read"],
        )
        self.assertIs(hints["return"], ConversationReply)

        invoke_signature = inspect.signature(BoundAgent.invoke)
        self.assertEqual(
            tuple(invoke_signature.parameters),
            ("self", "request", "reference_read"),
        )
        self.assertIs(
            invoke_signature.parameters["reference_read"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(invoke_signature.parameters["reference_read"].default)

        context_provider_signature = inspect.signature(BoundAgent.context_provider)
        self.assertEqual(
            tuple(context_provider_signature.parameters),
            ("self", "reference_read"),)
        self.assertIsNone(
            context_provider_signature.parameters["reference_read"].default)
        self.assertEqual(
            tuple(
                tuple(inspect.signature(method).parameters)
                for method in (
                    BoundAgent.decode_context_conversation_answer,
                    BoundAgent.decode_context_retrieval_answer,
                    BoundAgent.decode_context_agent_answer,)
            ),
            (
                ("self", "request", "text"),
                ("self", "request", "text"),
                ("self", "request", "text"),),
        )

    def test_identifier_content_agent_and_context_error_taxonomy(self) -> None:
        request_values: dict[str, object] = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "agent_id": PLOT_ARCHITECT,
            "content": "visible",
            "context": _context_window(),
        }
        reply_values: dict[str, object] = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "content": "visible",}
        cases = (
            (ConversationRequest, request_values, tuple(request_values)[:4]),
            (ConversationReply, reply_values, tuple(reply_values)[:3]),)
        for model, base, identifier_fields in cases:
            for field_name in identifier_fields:
                for value, error in (
                    (None, SessionIdError),
                    ("../escape", SessionIdError),
                    ("x" * 65, SessionIdError),
                ):
                    values = dict(base)
                    values[field_name] = value
                    with self.subTest(
                        model=model.__name__,
                        field=field_name,
                        value=value,
                    ):
                        with self.assertRaises(error):
                            model(**values)  # type: ignore[arg-type]

        request_values["turn_id"] = "x" * 64
        self.assertEqual(
            ConversationRequest(**request_values).turn_id,  # type: ignore[arg-type]
            "x" * 64,)
        with self.assertRaises(ValueError):
            _conversation_request(agent_id="unknown_agent")

        preserved = " \n\t\x00\x1b 한🙂 "
        for model, base, _ in cases:
            values = dict(base)
            values["content"] = preserved
            self.assertEqual(
                model(**values).content,  # type: ignore[arg-type]
                preserved,)
            for value, error in (
                (None, TypeError),
                (1, TypeError),
                ("", ValueError),
                ("invalid-\ud800", ValueError),
            ):
                values = dict(base)
                values["content"] = value
                with self.subTest(
                    model=model.__name__,
                    content=repr(value),
                ):
                    with self.assertRaises(error):
                        model(**values)  # type: ignore[arg-type]

        values = dict(request_values, context={})
        with self.assertRaises(TypeError):
            ConversationRequest(**values)  # type: ignore[arg-type]

        class DerivedContext(ConversationContextWindow):
            pass

        derived = DerivedContext(
            events=(),
            total_complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,)
        values = dict(request_values)
        values["context"] = derived
        with self.assertRaises(TypeError):
            ConversationRequest(**values)  # type: ignore[arg-type]

    def test_story_session_context_must_match_outer_conversation_identity(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        assembler = PromptAssembler(workspace)
        definition = assembler.build_definition(
            session_id="story_session",
            agent_id=PLOT_ARCHITECT,
        )
        story_context = assembler.build_story_session_context(
            definition=definition,
            session_id="story_session",
            agent_id=PLOT_ARCHITECT,
            focused_context_mode=StoryFocusedContextMode.INCLUDED,
            focused_conversation_id="conversation_b",
            conversation_context=_context_window(),
        )
        common = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "agent_id": PLOT_ARCHITECT,
            "content": "visible",
            "context": _context_window(),
            "story_session_context": story_context,}

        cases = (
            (ConversationRequest, common),
            (
                ConversationRetrievalRequest,
                {**common, "context": _readable_context()},),
        )
        for request_type, values in cases:
            with self.subTest(request_type=request_type.__name__):
                with self.assertRaisesRegex(
                    ValueError,
                    "story-session context request identity mismatch",
                ):
                    request_type(**values)

    def test_retrieval_bound_signature_and_sync_handler_protocol_are_exact(
        self,
    ) -> None:
        signature = inspect.signature(BoundAgent.converse_with_retrieval)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "request", "handler", "reference_read"),)
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                and parameter.default is inspect.Parameter.empty
                for parameter in tuple(signature.parameters.values())[:3])
        )
        self.assertIs(
            signature.parameters["reference_read"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(signature.parameters["reference_read"].default)
        hints = get_type_hints(BoundAgent.converse_with_retrieval)
        self.assertIs(hints["request"], ConversationRetrievalRequest)
        self.assertIs(hints["handler"], ConversationRetrievalActionHandler)
        self.assertEqual(
            hints["reference_read"],
            get_type_hints(BoundAgent.invoke)["reference_read"],
        )
        self.assertIs(hints["return"], ConversationRetrievalReply)

    def test_context_accepts_interleaving_sequence_gaps_and_exact_caps(self) -> None:
        first = _conversation_pair(
            "turn_one",
            2,
            recipient=CHARACTER_CUSTODIAN,
            outcome_kind=ConversationEventKind.RUNTIME_ERROR,)
        second = _conversation_pair("turn_two", 4)
        interleaved = (
            first[0],
            second[0],
            replace(second[1], sequence=7),
            replace(first[1], sequence=11),)
        window = _context_window(interleaved, omitted_turns=3)
        request = _conversation_request(context=window)
        self.assertEqual(request.context, window)
        self.assertIs(type(request.context), ConversationContextWindow)
        self.assertIs(type(request.context.events), tuple)
        self.assertTrue(
            all(type(event) is ConversationEvent for event in request.context.events))
        self.assertEqual(request.context.total_complete_turns, 5)

        capped_events = tuple(
            event
            for index in range(CONVERSATION_CONTEXT_POLICY.max_complete_turns)
            for event in _conversation_pair(
                f"cap_{index}", index * 2 + 1, user_content="u", outcome_content="r")
        )
        _conversation_request(context=_context_window(capped_events))
        byte_capped = _conversation_pair(
            "byte_cap",
            user_content="x" * (CONVERSATION_CONTEXT_POLICY.max_utf8_bytes - 1),
            outcome_content="y",)
        _conversation_request(context=_context_window(byte_capped))

    def test_context_rejects_identity_order_pair_metadata_and_cap_errors(
        self,
    ) -> None:
        user, reply = _conversation_pair("turn_prior")
        over_turn_cap = tuple(
            event
            for index in range(CONVERSATION_CONTEXT_POLICY.max_complete_turns + 1)
            for event in _conversation_pair(f"over_{index}", index * 2 + 1))
        invalid_windows = (
            (
                "wrong session",
                _context_window(
                    tuple(
                        replace(event, session_id="other_session")
                        for event in (user, reply)
                    )
                ),
            ),
            (
                "wrong conversation",
                _context_window(
                    tuple(
                        replace(event, conversation_id="conversation_b")
                        for event in (user, reply)
                    )
                ),
            ),
            (
                "current turn",
                _context_window(
                    tuple(
                        replace(event, turn_id="turn_current")
                        for event in (user, reply)
                    )
                ),
            ),
            ("duplicate sequence", _context_window((user, replace(reply, sequence=1)))),
            (
                "decreasing sequence",
                _context_window(
                    (replace(user, sequence=2), replace(reply, sequence=1))),
            ),
            ("unmatched user", _context_window((user,))),
            (
                "wrong reply sender",
                _context_window((user, replace(reply, sender=CHARACTER_CUSTODIAN))),),
            ("retained count", _context_window((user, reply), retained_turns=0)),
            (
                "retained bytes",
                _context_window((user, reply), retained_utf8_bytes=1),),
            ("turn cap", _context_window(over_turn_cap)),
            (
                "byte cap",
                _context_window(
                    _conversation_pair(
                        "byte_over",
                        user_content="x" * CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
                        outcome_content="y",)
                ),
            ),
        )
        for label, context in invalid_windows:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    _conversation_request(context=context)


def _configure_process_stop(process, events, first_wait, final_wait):
    process.terminate.side_effect = lambda: events.append('terminate')
    process.kill.side_effect = lambda: events.append('kill')
    waits = iter((first_wait, final_wait))
    def wait():
        events.append('wait')
        return next(waits)
    process.wait.side_effect = wait




class CodexBackendTestCase(CodexNativeProcessFixture, unittest.IsolatedAsyncioTestCase):
    async def _invoke_empty_contract(self, backend, bound, *, session_id, decode, execution_workspace, **scope):
        return await backend._invoke_provider_structured(
            bound, session_id=session_id, **scope, schema={}, payload={},
            decode=decode, execution_workspace=execution_workspace)
    def _session_backend(self, session_id):
        backend = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(backend, session_id)
        return backend
    async def direct_execution_workspace(
        self,
        bound: object,
        session_id: str,
    ) -> ProviderExecutionWorkspace:
        return await self.enterAsyncContext(
            bound._direct_codex_execution_workspace(session_id, None)  # type: ignore[attr-defined]
        )

    async def test_both_native_backends_preserve_session_continuation_and_legacy_namespaces(self):
        from story_writing_agents.agent_context.invocation.context_execution import AgentContextService
        from story_writing_agents.agent_context.contracts.call_offers import ContextInvocationKind
        from story_writing_agents.agent_context.invocation.requests import ContextInvocationRequest
        from story_writing_agents.agent_context.sources.active_debate import ActiveDebateSourceAdapter
        from story_writing_agents.agent_context.sources.focused_conversation import FocusedConversationSourceAdapter
        from story_writing_agents.agent_context.sources.own_agent_state import OwnAgentStateSourceAdapter
        from story_writing_agents.agent_context.sources.session_history import SessionHistorySourceAdapter
        from story_writing_agents.agent_context.sources.writing_artifact import WritingArtifactSourceAdapter
        from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
        from tests.agent_context.invocation.execution_fixture import LEAD_CALL_FACTORY as _LEAD_CALL_FACTORY, make_lead_answer_frame as _lead_answer_frame, make_materials as _materials
        for provider_id in ("codex", "antigravity"):
            with self.subTest(provider=provider_id):
                session = f"continuation_{provider_id}"
                definition = replace(self.definition(LEAD_ORCHESTRATOR), backend=provider_id)
                def factory():
                    value = AgentBackend(
                        self.workspace, executable=str(self.executable), antigravity_executable=str(self.executable),
                        backend_overrides={LEAD_ORCHESTRATOR: provider_id},
                    )
                    self.addCleanup(value.close)
                    return value
                backend = factory()
                self.create_provisioned_session_with_access(backend, session)
                service = AgentContextService(self.workspace)
                storage = compose_filesystem_conversation_storage(self.workspace)
                repository = WritingArtifactRepository(self.workspace.root)
                scope = service.scope(session, ContextSubjectKind.CONVERSATION, "conversation_a", LEAD_ORCHESTRATOR)
                identity = service.session_continuation(session, LEAD_ORCHESTRATOR).identity
                policy = None
                calls, requests = [], []
                async def invoke(_binding, invocation):
                    calls.append(invocation)
                    return ProviderResult(invocation.thread_id or f"native-thread-{len(calls)}",
                        _lead_answer_frame(requests[-1], "synthetic exact session context"),
                        actual_transport_utf8_bytes=len(invocation.payload_json))
                async def call(owner, current, continuation):
                    provider = (await owner.bind(definition))._context_provider()
                    class ObservedProvider:
                        def __getattr__(self, name):
                            return getattr(provider, name)
                        async def invoke_context(self, request):
                            nonlocal policy
                            requests.append(request)
                            policy = request.policy_fingerprint
                            return await provider.invoke_context(request)
                    request = ContextInvocationRequest(session_id=session, subject_kind=current.subject_kind,
                        subject_id=current.subject_id, agent_id=LEAD_ORCHESTRATOR,
                        operation_id=f"call-{len(calls) + 1}", result_correlation_id=f"result-{len(calls) + 1}",
                        materials=_materials("synthetic exact session context"),
                        result_contract=_LEAD_CALL_FACTORY, invocation_kind=ContextInvocationKind.LEAD,
                        physical_call_limit=1)
                    sources = dict(writing_artifacts=WritingArtifactSourceAdapter(repository, current),
                        focused_conversation=FocusedConversationSourceAdapter(
                            storage.catalog, storage.event_store, current, None),
                        active_debate=ActiveDebateSourceAdapter(self.workspace, current, None),
                        own_agent_state=OwnAgentStateSourceAdapter(self.workspace, current))
                    if continuation is None:
                        result = await service.invoke(request, ObservedProvider(), **sources)
                    else:
                        published = False
                        async with service.session_continuation(session, LEAD_ORCHESTRATOR) as transaction:
                            result = await service.invoke(request, ObservedProvider(), continuation=transaction,
                                session_history=SessionHistorySourceAdapter(
                                    storage.catalog, storage.event_store, session).capture(),
                                publication_validator=lambda: published, **sources)
                            published = True
                    return result.provider_thread_id
                adapter = getattr(backend, f"_{provider_id}_adapter")
                with mock.patch.object(adapter, "invoke", new=invoke):
                    legacy = await call(backend, scope, None)
                    directory = self.workspace.path(f"agent_states/orchestrator_state/provider_sessions/.contextual/v1/{session}")
                    retained = {path: path.read_bytes() for path in directory.glob("*.json")}
                    self.assertEqual(len(retained), 1)
                    first = await call(backend, scope, identity)
                    second = await call(backend, replace(scope, subject_id="conversation_b"), identity)
                self.assertNotEqual(first, legacy)
                self.assertEqual(second, first)
                self.assertEqual({path: path.read_bytes() for path in retained}, retained)
                backend.pause_session_and_release_access(session)
                backend.reopen_session_with_access(session)
                with mock.patch.object(adapter, "invoke", new=invoke):
                    self.assertEqual(await call(backend, scope, identity), first)
                backend.close()
                restarted = factory()
                restarted.acquire_session_access(session)
                with mock.patch.object(getattr(restarted, f"_{provider_id}_adapter"), "invoke", new=invoke):
                    self.assertEqual(await call(restarted, replace(scope, subject_id="conversation_c"), identity), first)
                    provider = (await restarted.bind(definition))._context_provider()
                    async with provider.invocation(scope, policy, continuation_identity=identity):
                        provider.taint_context_thread(scope, policy)
                    replacement = await call(restarted, scope, identity)
                self.assertNotEqual(replacement, first)
                self.assertIsNone(calls[-1].thread_id)
                self.assertEqual({path: path.read_bytes() for path in retained}, retained)
                restarted.close()

    def antigravity_definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(
            self.workspace,
            backend_overrides={agent_id: "antigravity"},
        ).build_definition(
            session_id="definition_session",
            agent_id=agent_id,
        )

    def retrieval_request(
        self,
        **overrides: object,
    ) -> ConversationRetrievalRequest:
        """Build one exact retrieval request with optional field overrides."""

        values: dict[str, object] = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "agent_id": PLOT_ARCHITECT,
            "content": "visible retrieval request",
            "context": _readable_context(),
        }
        values.update(overrides)
        return ConversationRetrievalRequest(**values)  # type: ignore[arg-type]

    def provider_debug_records(
        self,
        *,
        session_id: str,
        correlation_field: str,
        correlation_id: str,
    ) -> list[dict[str, object]]:
        """Return provider diagnostics for one exact public correlation."""

        return [
            record
            for record in _workspace_debug_records(self.workspace)
            if record["component"] == "backend"
            and record["operation"] == "provider_invocation"
            and record.get("session_id") == session_id
            and record.get(correlation_field) == correlation_id
        ]

    def assert_provider_terminal_records(
        self,
        records: list[dict[str, object]],
        *,
        session_id: str,
        correlation_field: str,
        correlation_id: str,
        terminal_phase: str,
        outcome: str,
        exception_type: type[BaseException] | None,
    ) -> None:
        """Assert one exact provider STARTED row and its terminal."""

        debate_id = correlation_id if correlation_field == "debate_id" else None
        conversation_id = (
            correlation_id if correlation_field == "conversation_id" else None
        )
        exception_name = (
            None
            if exception_type is None
            else f"{exception_type.__module__}.{exception_type.__qualname__}"
        )
        self.assertEqual(
            [_debug_signature(record) for record in records],
            [
                (
                    1,
                    "trace",
                    "backend",
                    "provider_invocation",
                    "started",
                    None,
                    session_id,
                    debate_id,
                    conversation_id,
                    PLOT_ARCHITECT,
                    None,
                ),
                (
                    1,
                    "trace" if exception_type is None else "error",
                    "backend",
                    "provider_invocation",
                    terminal_phase,
                    outcome,
                    session_id,
                    debate_id,
                    conversation_id,
                    PLOT_ARCHITECT,
                    exception_name,
                ),
            ],
        )
        if exception_type is not None:
            self.assertEqual(
                set(records[1]["exception"]),  # type: ignore[arg-type]
                {"type", "frames", "frames_truncated"},
            )

    def assert_interrupted_provider_records(
        self,
        records: list[dict[str, object]],
        *,
        session_id: str,
        correlation_field: str,
        correlation_id: str,
        exception_type: type[BaseException],
    ) -> None:
        """Assert one STARTED and one correlated interrupted terminal row."""

        self.assertEqual(len(records), 2)
        expected_common = {
            "schema_version": 1,
            "component": "backend",
            "operation": "provider_invocation",
            "session_id": session_id,
            correlation_field: correlation_id,
            "agent_id": PLOT_ARCHITECT,
        }
        self.assertEqual(
            {key: value for key, value in records[0].items() if key != "timestamp"},
            {
                **expected_common,
                "level": "trace",
                "phase": "started",
            },
        )
        exception = records[1]["exception"]
        self.assertIs(type(exception), dict)
        self.assertEqual(
            {
                key: value
                for key, value in records[1].items()
                if key not in {"timestamp", "exception"}
            },
            {
                **expected_common,
                "level": "error",
                "phase": "cancelled",
                "outcome": "interrupted",
            },
        )
        self.assertEqual(
            set(exception),  # type: ignore[arg-type]
            {"type", "frames", "frames_truncated"},
        )
        self.assertEqual(
            exception["type"],  # type: ignore[index]
            f"builtins.{exception_type.__name__}",
        )

    def write_codex_defaults(
        self,
        model: str | None,
        effort: str | None,
    ) -> None:
        """Persist one valid complete Codex-default configuration."""

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": model,
                "codex_reasoning_effort": effort,
            },
        )

    def reference_invocation(
        self,
        *,
        operation_id: str,
        invocation_id: str,
        agent_id: str = PLOT_ARCHITECT,
        phase: str = "draft",
        round_number: int | None = 0,
        references: tuple[ArtifactReference, ...] | None = None,
    ):
        """Create one real coordinator over whole and ranged source markers."""

        repository = getattr(self, "_reference_repository", None)
        if repository is None:
            repository = WritingArtifactRepository(self.workspace.root)
            repository.commit_consensus(
                output_path="part-01/provider-reference.md",
                candidate="REFERENCE-PROVIDER-MARKER\n",
                core_updates={},
                session_id="reference_fixture",
                debate_id="reference_fixture",
                revision_id="r0",
                candidate_author_agent_id=EXECUTION_SPECIALIST,
            )
            self.workspace.root.joinpath(
                "manuscript/part-01/provider-range.md"
            ).write_text(
                "REFERENCE-RANGE-OMITTED\n"
                "REFERENCE-PROVIDER-RANGE-MARKER\n"
                "REFERENCE-RANGE-OMITTED\n",
                encoding="utf-8",
            )
            self._reference_repository = repository
        if references is None:
            references = self.reference_cases()["whole"]
        authorization = repository.capture_reference_authorization(
            operation_id=operation_id,
            batch=ArtifactReferenceBatch(
                "reference request",
                "reference request",
                references,
            ),
        )
        assert authorization is not None
        self.addCleanup(authorization.close)
        invocation = repository.begin_reference_invocation(
            authorization,
            ReferenceInvocationCorrelation(
                operation_id=operation_id,
                invocation_id=invocation_id,
                invocation_ordinal=1,
                agent_id=agent_id,
                phase=phase,
                round_number=round_number,
                attempt=1,
            ),
        )
        return repository, authorization, invocation

    @staticmethod
    def reference_cases() -> dict[str, tuple[ArtifactReference, ...]]:
        """Return whole, one-line range, and ordered mixed request shapes."""

        whole = ArtifactReference("manuscript/part-01/provider-reference.md")
        ranged = ArtifactReference(
            "manuscript/part-01/provider-range.md",
            ArtifactLineRange(2, 2),
        )
        return {
            "whole": (whole,),
            "range": (ranged,),
            "mixed": (whole, ranged),
        }

    def reference_environment(self, invocation) -> dict[str, str]:
        """Return no provider controls for a runtime-local reference load."""

        del invocation
        return {}

    def assert_reference_accepted(
        self,
        repository,
        authorization,
        operation_id: str,
    ) -> None:
        """Finish and assert the exact durable invocation lifecycle."""

        repository.finish_reference_operation(authorization, outcome=None)
        history = repository.inspect_reference_operation(operation_id)
        assert history is not None
        self.assertEqual(
            tuple(event.operation_state for event in history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.ACCEPTED,
            ),
        )
        self.assertEqual(
            tuple(event.invocation_state for event in history.invocation_events[0]),
            (
                ReferenceInvocationState.AUTHORIZED,
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.RESULT_RECEIVED,
                ReferenceInvocationState.ACCEPTED,
            ),
        )

    async def test_binding_stages_immutable_bytes_after_all_source_paths_become_unsafe(
        self,
    ) -> None:
        WritingArtifactRepository(self.workspace.root).commit_consensus(
            output_path="fixtures/backend-context.md",
            candidate="fixture manuscript",
            core_updates={
                "global_outline.md": "verified global outline",
                "part_outlines.md": "verified part outline",
            },
            session_id="backend_snapshot",
            debate_id="backend_snapshot",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        definition = self.definition()
        references = definition.file_references()
        expected = tuple(reference.read_text() for reference in references)
        self.assertEqual(len(definition.context_document_files), 2)
        backend = self._managed_backend()

        system = definition.system_prompt_file.path
        system.rename(system.with_name(f"{system.name}.removed"))
        persona = definition.persona_file.path
        persona.unlink()
        context_symlink, context_hardlink = (
            reference.path for reference in definition.context_document_files
        )
        symlink_source = self.fake_directory / "same-symlink.md"
        symlink_source.write_bytes(definition.context_document_files[0].content)
        context_symlink.unlink()
        context_symlink.symlink_to(symlink_source)
        hardlink_source = self.fake_directory / "same-hardlink.md"
        hardlink_source.write_bytes(definition.context_document_files[1].content)
        context_hardlink.unlink()
        os.link(hardlink_source, context_hardlink)

        with (
            mock.patch.object(
                PromptFileReference,
                "_read_bytes",
                side_effect=AssertionError("binding reread a prompt path"),
            ) as reference_read,
            mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("binding read a filesystem path"),
            ) as path_read,
        ):
            bound = await backend.bind(definition)
        reference_read.assert_not_called()
        path_read.assert_not_called()

        staged = bound.staging_directory.joinpath("AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            sum(
                component.serialized_utf8_bytes
                for component in bound._provider_binding.binding_components
            ),
            len(staged.encode("utf-8")),
        )
        for content in expected:
            with self.subTest(content=content[:32]):
                self.assertIn(content, staged)

    def test_codex_usage_accepts_only_one_recognized_exact_terminal_snapshot( self, ) -> None:
        backend = self._managed_backend()
        parse = backend._codex_adapter.provider_usage
        cases = ( ((), None), (({"type": "turn.completed"},), None),
            (({"type": "turn.completed", "usage": {"unknown": 7}},), None), ( ( { "type": "turn.completed",
                        "usage": { "input_tokens": 0, "output_tokens": 2, "cached_input_tokens": 3, }, }, ),
                ProviderUsage(0, 2, 3), ), ( ({"type": "turn.completed", "usage": {"input_tokens": 4}},),
                ProviderUsage(input_tokens=4), ), ( ( { "type": "turn.completed", "usage": {
                            "input_tokens": 5, "output_tokens": -1, "cached_input_tokens": True, "unknown": 9,
                        }, }, ), ProviderUsage(input_tokens=5), ), ( ( { "type": "turn.completed", "usage": {
                            "input_tokens": -1, "output_tokens": "2", "cached_input_tokens": False, }, }, ),
                None, ), ( ( {"type": "turn.completed", "usage": {"input_tokens": 1}},
                    {"type": "turn.completed", "usage": {"input_tokens": 2}}, ), None, ), )
        for events, expected in cases:
            with self.subTest(events=events): self.assertEqual(parse(events), expected)

    async def test_malformed_usage_never_invalidates_a_valid_provider_reply( self, ) -> None:
        session_id = "malformed_usage_reply"
        backend = self._managed_backend()
        records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = records.append
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
        malformed_usage = json.dumps( { "input_tokens": True, "output_tokens": -1, "cached_input_tokens": "3",
                "unknown": 4, }, separators=(",", ":"), )

        with mock.patch.dict( os.environ, {"STORY_FAKE_CODEX_USAGE": malformed_usage},
        ):
            reply = await bound.invoke(self.request(session_id=session_id))

        self.assertEqual(
            reply,
            _provider_candidate_reply(),
        )
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].usage)

    async def test_context_sink_records_prepared_submitted_and_cancellation_once(
        self,
    ) -> None:
        session_id = "context_sink_states"
        backend, bound = await self._provisioned_bound(session_id)
        records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = records.append

        prepared = ProviderInvocationError(
            "prepared provider failure",
            taint_existing_thread=False,
        )
        submitted = ProviderInvocationError(
            "submitted provider failure",
            taint_existing_thread=True,
        )
        record_provider_failure_accounting(
            submitted,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=31,
        )
        cancellation = asyncio.CancelledError("PRIVATE_CANCELLED_PROVIDER")
        record_provider_failure_accounting(
            cancellation,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=32,
        )
        effects = (prepared, submitted, cancellation)
        with mock.patch.object(
            backend._codex_adapter,
            "invoke",
            new=mock.AsyncMock(side_effect=effects),
        ):
            for index, failure in enumerate(effects, 1):
                request = self.conversation_request(
                    session_id=session_id,
                    turn_id=f"sink_failure_{index}",
                )
                if isinstance(failure, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError) as caught:
                        await bound.converse(request)
                    self.assertIs(caught.exception, failure)
                else:
                    with self.assertRaises(BackendProtocolError):
                        await bound.converse(request)

        self.assertEqual(len(records), 3)
        self.assertEqual(
            tuple(record.state for record in records),
            (
                ProviderCallState.PREPARED,
                ProviderCallState.SUBMITTED,
                ProviderCallState.SUBMITTED,
            ),
        )
        self.assertEqual(
            tuple(record.actual_transport_utf8_bytes for record in records),
            (None, 31, 32),
        )
        self.assertTrue(
            all(
                record.payload.call_kind is ProviderCallKind.CONVERSATION
                for record in records
            )
        )

    async def test_debate_and_conversation_capture_order_scope_and_exact_bodies(
        self,
    ) -> None:
        session_id = "exchange_capture_success"
        debate_id = "exchange_debate"
        conversation_id = "exchange_conversation"
        backend, bound = await self._provisioned_bound(session_id)

        debate = await bound.invoke(
            self.request(session_id=session_id, debate_id=debate_id)
        )
        conversation = await bound.converse(
            self.conversation_request(
                session_id=session_id,
                conversation_id=conversation_id,
            )
        )
        self.assertIsInstance(debate, AgentReply)
        self.assertIsInstance(conversation, ConversationReply)

        records = _agent_exchange_records(self.workspace)
        self.assertEqual(
            [record["request_type"] for record in records],
            ["request", "response", "outcome"] * 2,
        )
        self.assertEqual(
            [record["scope_kind"] for record in records],
            ["debate"] * 3 + ["conversation"] * 3,
        )
        self.assertEqual(
            [record["scope_id"] for record in records],
            [debate_id] * 3 + [conversation_id] * 3,
        )
        for offset, call_kind in ((0, "debate"), (3, "conversation")):
            exchange = records[offset : offset + 3]
            self.assertEqual(
                {record["exchange_id"] for record in exchange},
                {exchange[0]["exchange_id"]},
            )
            request = json.loads(exchange[0]["content"])
            response = json.loads(exchange[1]["content"])
            outcome = json.loads(exchange[2]["content"])
            self.assertEqual(
                set(request), {"call_kind", "payload", "prior_thread_id", "schema"}
            )
            self.assertEqual(request["call_kind"], call_kind)
            self.assertEqual(
                set(response),
                {
                    "actual_transport_utf8_bytes",
                    "response_text",
                    "thread_id",
                    "usage",
                    "elapsed_ns",
                },
            )
            self.assertIs(type(response["elapsed_ns"]), int)
            self.assertGreaterEqual(response["elapsed_ns"], 0)
            self.assertEqual(outcome, {"code": "accepted", "outcome": "accepted"})
            self.assertNotIn("stderr", response)
            self.assertNotIn("environment", response)
            self.assertNotIn("credentials", response)

    async def test_response_capture_precedes_accounting_decode_artifact_and_thread(
        self,
    ) -> None:
        events: list[str] = []
        details: list[str] = []

        class Lifecycle:
            def record_response(self, _result: ProviderResult) -> None:
                events.append("response")

            def accepted(self) -> None:
                events.append("accepted")

            def rejected(self, _code: object, *, validation_details: tuple[str, ...] = ()) -> None:
                events.append("rejected")
                details.extend(validation_details)

            def provider_failure(self, _code: object) -> None:
                events.append("provider_failure")

            def cancelled(self) -> None:
                events.append("cancelled")

            def post_response_runtime_failure(self, _code: object) -> None:
                events.append("runtime_failure")

        session_id = "exchange_capture_timing"
        backend, bound = await self._provisioned_bound(session_id)
        execution_workspace = await self.enterAsyncContext(
            bound._direct_codex_execution_workspace(session_id, None)
        )
        original_store_thread = provider_manifest_thread_store.store_manifest_thread

        def begin(*_args: object, **_kwargs: object) -> Lifecycle:
            events.append("begin")
            return Lifecycle()

        async def invoke(*_args: object, **_kwargs: object) -> ProviderResult:
            events.append("invoke")
            return ProviderResult("timing-thread", "PRIVATE_TIMING_RESPONSE")

        def decode(_response: str) -> AgentReply:
            events.append("decode")
            return AgentReply.candidate("r0", "accepted")

        def store_thread(*args: object, **kwargs: object) -> None:
            events.append("thread")
            original_store_thread(*args, **kwargs)  # type: ignore[arg-type]

        backend._context_accounting_sink = lambda _accounting: events.append(
            "accounting"
        )
        with (
            _fresh_literal_object_side_effect(backend, '_begin_legacy_agent_exchange', begin),
            mock.patch.object(backend._codex_adapter, "invoke", side_effect=invoke),
            mock.patch.object(provider_manifest_thread_store, "store_manifest_thread", side_effect=store_thread),
        ):
            reply = await self._invoke_empty_contract(
                backend, bound,
                session_id=session_id,
                debate_id="timing_debate",


                decode=decode,
                execution_workspace=execution_workspace,
            )
        self.assertEqual(reply, AgentReply.candidate("r0", "accepted"))
        self.assertEqual(
            events,
            [
                "begin",
                "invoke",
                "response",
                "accounting",
                "decode",
                "thread",
            ],
        )

        events.clear()

        def malformed(_response: str) -> AgentReply:
            events.append("decode")
            raise json.JSONDecodeError("malformed", "x", 0)

        def artifact(*_args: object, **_kwargs: object) -> None:
            events.append("artifact")

        with (
            _fresh_literal_object_side_effect(backend, '_begin_legacy_agent_exchange', begin),
            mock.patch.object(backend._codex_adapter, "invoke", side_effect=invoke),
            mock.patch.object(
                backend,
                "_record_rejected_provider_response_without_replacing_failure",
                side_effect=artifact,
            ),
            self.assertRaises(BackendProtocolError),
        ):
            await self._invoke_empty_contract(
                backend, bound,
                session_id=session_id,
                conversation_id="timing_conversation",


                decode=malformed,
                execution_workspace=execution_workspace,
            )
        self.assertEqual(
            events,
            [
                "begin",
                "invoke",
                "response",
                "accounting",
                "decode",
                "artifact",
                "rejected",
            ],
        )
        self.assertEqual(details, ["Structured response\n$: malformed JSON"])

    async def _assert_grouped_reader_transport(self, backend, session_id, execution_workspace) -> None:
        from story_writing_agents.agent_context.invocation.context_execution import AgentContextService
        from story_writing_agents.agent_context.contracts.bound_call import BoundCallFactory
        from story_writing_agents.agent_context.contracts.call_offers import ContextCallOffers, ContextInvocationKind
        from story_writing_agents.agent_context.invocation.requests import ContextInvocationRequest
        from story_writing_agents.agent_context.contracts.errors import ContextExecutionError
        from story_writing_agents.agent_context.invocation.provider import context_provider_failure_facts
        from story_writing_agents.agent_context.ledger.checkpoint_ledger import ContextLedger
        from story_writing_agents.agent_context.ledger.record_values import ContextLedgerKey, ContextLedgerRecordState, ContextLedgerAttemptOutcome
        from story_writing_agents.agent_context.sources.active_debate import ActiveDebateSourceAdapter
        from story_writing_agents.agent_context.sources.focused_conversation import FocusedConversationSourceAdapter
        from story_writing_agents.agent_context.sources.own_agent_state import OwnAgentStateSourceAdapter
        from story_writing_agents.agent_context.sources.writing_artifact import WritingArtifactSourceAdapter
        from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
        from story_writing_agents.lead_schema_groups import LeadGroupPhase
        from story_writing_agents.lead_provider_contracts import LeadContractRejection, LeadGroupCompilation
        from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance
        from story_writing_agents.agent_context.contracts.planning_inputs import MandatoryReferenceReservation
        from story_writing_agents.agent_context.sources.capture_values import writing_artifact_source_id
        from tests.agent_context.invocation.execution_fixture import LEAD_CALL_FACTORY as _LEAD_CALL_FACTORY, PRIVATE_INPUT as _PRIVATE_INPUT, make_materials as _materials, make_group_request as _group_request, make_group_control as _group_control, make_group_answer as _group_answer, make_search as _search
        from story_writing_agents.provider_process import ProcessResult
        from tests.provider_wire_fixture import CallbackContextViews, FixturePhasePlanError, OwnedNativeHistory, assert_phase_guide
        service = AgentContextService(self.workspace)
        storage = compose_filesystem_conversation_storage(self.workspace)
        repository = WritingArtifactRepository(self.workspace.root)
        definition = self.definition(LEAD_ORCHESTRATOR)
        role_source = definition.behavior_policy_components[-1]
        self.assertTrue(role_source.path.resolve().is_relative_to(self.workspace.root.resolve()))
        domain_policy = role_source.path.read_bytes()
        self.assertEqual(domain_policy, role_source.content)
        self.assertIn(b"# Lead Orchestrator Policy", domain_policy)
        reference_path = "manuscript/part-01/grouped-reference.md"
        reference_text = "GROUPED_ATTACHED_REFERENCE_한🙂\n"
        reference_wire = json.dumps(reference_text, ensure_ascii=False).encode("utf-8")
        self.workspace.root.joinpath(reference_path).parent.mkdir(parents=True, exist_ok=True)
        self.workspace.root.joinpath(reference_path).write_bytes(reference_text.encode("utf-8"))
        repository, reference_authorization, reference_read = self.reference_invocation(
            operation_id="grouped-reader-reference", invocation_id="grouped-reader-reference-1",
            agent_id=LEAD_ORCHESTRATOR, phase="lead", round_number=None,
            references=(ArtifactReference(reference_path),))
        proof = reference_authorization.authorized_references[0]
        reference = MandatoryReferenceReservation(proof.ordinal, f"reference_{proof.ordinal}",
            writing_artifact_source_id(proof.reference.path), f"sha256:{proof.source.source_sha256}",
            ContextProvenance(ContextAuthorityKind.REFERENCE_AUTHORIZATION, proof.reference.token),
            f"sha256:{proof.selected_sha256}", proof.selected_utf8_bytes)
        expected_references = [{"artifact_reference": {"artifact_path": reference_path, "line_range": None},
                                "content_text": reference_text}]
        bound = await backend.bind(definition)
        provider = bound.context_provider(reference_read=reference_read)
        def next_reference_provider(ordinal):
            reader = repository.begin_reference_invocation(reference_authorization,
                replace(reference_read.request.correlation, invocation_id=f"grouped-reader-reference-{ordinal}",
                        invocation_ordinal=ordinal, attempt=ordinal))
            return bound.context_provider(reference_read=reader)
        self.assertEqual(provider.provider_id, "codex")
        native_invoke = backend._codex_adapter.invoke
        requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
        reader_faults_checked = False
        callback_views = CallbackContextViews()
        self.addCleanup(callback_views.require_fixture_valid)
        history = OwnedNativeHistory(self)
        responses = [_group_control("answer.workflow"), _group_answer]
        phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE)

        from story_writing_agents.agent_context.accounting import (
            ProviderUsage, ProviderUsageScope, normalized_provider_usage, )
        from story_writing_agents.agent_context.group_observation import LeadGroupTrace, LeadGroupObservationKind
        from story_writing_agents.agent_context.group_reporting import group_trace_report
        from story_writing_agents.provider_adapter import (NativeContextCheck, NativeContextCheckStage,
            NativeContextCheckReason, NativeContextSourceStage, ProviderInvocationError)
        from story_writing_agents import codex_native_context
        native_mutations, native_cleanups, unavailable_native_calls, native_results = {}, {}, set(), []
        omitted_thread_reports = set()
        pre_submit_probe = None
        first_usage = {"input_tokens": 28997, "output_tokens": 195, "cached_input_tokens": 0}
        second_usage = {"input_tokens": 88866, "output_tokens": 1411, "cached_input_tokens": 0}
        native_usage_cases = ( {"total_token_usage": first_usage, "last_token_usage": first_usage},
            {"total_token_usage": second_usage, "last_token_usage": {
                "input_tokens": 59869, "output_tokens": 1216, "cached_input_tokens": 0}}, )
        group_observations, native_schema_bytes = [], []

        class ObservedProvider:
            def __getattr__(self, name): return getattr(provider, name)

            async def invoke_context(self, request):
                requests.append(request)
                callback_views.observe(request)
                return await provider.invoke_context(request)

        async def invoke(binding, invocation):
            nonlocal reader_faults_checked
            self.assertLess(len(invocations), len(phases))
            assert_phase_guide(self, requests[-1], phases[len(invocations)])
            if not invocations:
                self.assertIsNone(invocation.context_witness)
                self.assertIsNone(invocation.context_anchor)
            invocations.append(invocation)
            if not reader_faults_checked:
                self._assert_owned_native_context_witness(invocation)
                reader_faults_checked = True
            if pre_submit_probe is not None and len(invocations) == 2:
                await pre_submit_probe(binding, invocation)
            staged = (binding.staging_directory / "AGENTS.md").read_bytes()
            role = binding.definition.system_prompt_file.content
            self.assertIn(domain_policy, role)
            self.assertEqual(role, definition.system_prompt_file.content)
            self.assertIn(role, staged)
            self.assertEqual(invocation.binding_components, binding.binding_components)
            async def native_process(request, **_options):
                native_commands.append(tuple(request.command))
                configs = [request.command[index + 1] for index, value in enumerate(request.command[:-1])
                           if value == "--config" and request.command[index + 1].startswith("developer_instructions=")]
                self.assertEqual(len(configs), 1)
                instructions = json.loads(configs[0].split("=", 1)[1])
                self.assertEqual(instructions.encode("utf-8"), staged)
                self.assertEqual(request.stdin, invocation.payload_json)
                native_info = None if native_usage_cases is None else native_usage_cases[len(native_commands) - 1]
                schema_bytes = Path(request.command[request.command.index("--output-schema") + 1]).read_bytes()
                self.assertEqual(schema_bytes, canonical_provider_json(invocation.schema))
                if native_info is not None: native_schema_bytes.append(schema_bytes)
                history.complete(invocation, token_usage=native_info,
                    after_write=native_mutations.get(len(native_commands)),
                    expect_unavailable=len(native_commands) in unavailable_native_calls)
                raw = responses.pop(0)(requests[-1], None)
                raw_responses.append(raw)
                output = Path(request.command[request.command.index("--output-last-message") + 1])
                output.write_text(raw, encoding="utf-8")
                event = {"type": "thread.started", "thread_id": history.thread_id}
                stdout = b"" if len(native_commands) in omitted_thread_reports else canonical_provider_json(event) + b"\n"
                if native_info is not None: stdout += canonical_provider_json({"type": "turn.completed",
                        "usage": native_info["total_token_usage"]}) + b"\n"
                return ProcessResult(stdout=stdout, stderr=b"", returncode=0)
            creator, scan_threads, guard_threads = threading.get_ident(), set(), set()
            facts = codex_native_context._native_file_facts
            require_current = ProviderExecutionWorkspace.require_current
            def observed_facts(*args, **kwargs):
                scan_threads.add(threading.get_ident())
                return facts(*args, **kwargs)
            def observed_guard(owner):
                guard_threads.add(threading.get_ident())
                self.assertEqual(threading.get_ident(), creator)
                return require_current(owner)
            try:
                with (mock.patch.dict(os.environ, {"CODEX_HOME": str(history.root)}),
                      mock.patch("story_writing_agents.codex_adapter.run_process", new=native_process),
                      mock.patch.object(codex_native_context, "_native_file_facts", new=observed_facts),
                      mock.patch.object(ProviderExecutionWorkspace, "require_current", new=observed_guard)):
                    result = await native_invoke(binding, invocation)
            finally:
                cleanup = native_cleanups.get(len(native_commands))
                if cleanup is not None: cleanup()
            self.assertEqual(guard_threads, {creator})
            self.assertTrue(scan_threads - {creator}, "native inspection blocked the event loop")
            if history.witnesses[-1] is None and result.context_witness is not None:
                self.assertTrue(result.native_context_check.verified)
                post = [item for item in result.native_context_check.observations
                        if item.stage is NativeContextCheckStage.POST_COMPLETE]
                self.assertGreater(len(post), 1)
                self.assertIs(post[0].reason, NativeContextCheckReason.ROLLOUT_NOT_VISIBLE)
                history.witnesses[-1] = result.context_witness
            self.assertEqual(result.context_witness, history.witnesses[-1])
            native_witnesses.append(result.context_witness)
            native_results.append(result)
            return result

        request = _group_request(ContextInvocationRequest(session_id=session_id,
            subject_kind=ContextSubjectKind.CONVERSATION, subject_id="grouped-reader",
            agent_id=LEAD_ORCHESTRATOR, operation_id="grouped-reader-operation",
            result_correlation_id="grouped-reader-result", materials=_materials(),
            result_contract=_LEAD_CALL_FACTORY, invocation_kind=ContextInvocationKind.LEAD, physical_call_limit=2,
            reference_operation_id="grouped-reader-reference", mandatory_references=(reference,),
            execution_workspace=execution_workspace))
        scope = service.scope(session_id, ContextSubjectKind.CONVERSATION, "grouped-reader", LEAD_ORCHESTRATOR)
        def assert_done_ledger(completed):
            state = completed.group_state
            record = ContextLedger(self.workspace).inspect(ContextLedgerKey(scope, provider.provider_id)).record
            self.assertIs(state.workflow.phase, LeadGroupPhase.DONE)
            self.assertIs(record.state, ContextLedgerRecordState.READY)
            self.assertIs(record.attempt.outcome, ContextLedgerAttemptOutcome.COMMITTED)
            self.assertEqual(record.generation, completed.ledger_generation)
            self.assertEqual(record.attempt.provider_calls, completed.provider_calls)
            self.assertFalse(state.transport_delivery.matches_ledger(record, latest=True))
            self.assertFalse(state.accepted_delivery.matches_ledger(record))
        loop, creator, release = asyncio.get_running_loop(), threading.get_ident(), threading.Event()
        compilation_threads, custom_threads = [], []
        compile_group = LeadGroupCompilation.__post_init__
        def responsive_compilation(plan):
            compilation_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(release.set)
            self.assertTrue(release.wait(2), "schema compilation blocked the event loop")
            return compile_group(plan)
        class CustomFactory(BoundCallFactory):
            contract_id = _LEAD_CALL_FACTORY.contract_id
            def require_declaration(self): custom_threads.append(threading.get_ident())
            def declare_result_owners(self, offers):
                custom_threads.append(threading.get_ident())
                raise ValueError("custom declaration remains on the calling loop")
        custom = CustomFactory()
        offers = ContextCallOffers("progress-custom", scope, 1, ("answer",), (), (), (), (), (), ())
        self.assertIsNone(await custom.prepare_schema_compilation(offers))
        self.assertEqual(custom_threads, [])
        with self.assertRaisesRegex(ValueError, "custom declaration remains on the calling loop"):
            custom.for_offer(offers)
        self.assertEqual(set(custom_threads), {creator})
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke), \
                mock.patch.object(LeadGroupCompilation, "__post_init__", new=responsive_compilation):
            result = await service.invoke(request, ObservedProvider(),
                writing_artifacts=WritingArtifactSourceAdapter(repository, scope),
                focused_conversation=FocusedConversationSourceAdapter(storage.catalog, storage.event_store, scope, None),
                active_debate=ActiveDebateSourceAdapter(self.workspace, scope, None),
                own_agent_state=OwnAgentStateSourceAdapter(self.workspace, scope), group_observation_sink=group_observations.append)
        self.assertTrue(compilation_threads)
        self.assertNotIn(creator, compilation_threads)
        self.assertEqual((len(invocations), len(result.provider_calls)), (2, 2))
        self.assertEqual(len(native_commands), len(invocations))
        first, second = [call.payload["response_contract"]["schema_selection"] for call in requests]
        self.assertEqual(first["context_delivery"]["mode"], "bootstrap")
        self.assertEqual(second["context_delivery"], {"mode": "delta",
            "context_ref": second["context_delivery"]["context_ref"], "base_context_ref": first["context_delivery"]["context_ref"]})
        self.assertIn(_PRIVATE_INPUT.encode(), invocations[0].payload_json)
        self.assertNotIn(_PRIVATE_INPUT.encode(), invocations[1].payload_json)
        self.assertNotIn("task_input", requests[1].payload)
        self.assertEqual(set(requests[1].payload["context_changes"]), {"add", "replace", "remove"})
        self.assertIs(invocations[1].context_witness, native_witnesses[0])
        self.assertIs(invocations[1].context_anchor, native_witnesses[0])
        self.assertEqual(result.group_state.transport_delivery.witness, native_witnesses[1])
        self.assertEqual(result.group_state.accepted_delivery.witness, native_witnesses[0])
        self.assertIn(reference_wire, invocations[0].payload_json)
        self.assertNotIn(reference_wire, invocations[1].payload_json)
        self.assertNotIn("untrusted_attached_references", requests[1].payload["context_changes"]["remove"])
        self.assertEqual([callback_views.for_request(call)["untrusted_attached_references"] for call in requests],
                         [expected_references] * 2)
        self.assertEqual(json.loads(result.group_state.accepted_delivery.logical_context)["input"]["untrusted_attached_references"], expected_references)
        self.assertEqual(json.loads(result.group_state.transport_delivery.logical_context)["input"]["untrusted_attached_references"], expected_references)
        assert_done_ledger(result)

        self.assertEqual(tuple(call.accounting.usage for call in result.provider_calls),
                         (ProviderUsage(28997, 195, 0), ProviderUsage(88866, 1411, 0)))
        self.assertEqual(tuple(item.last_token_usage for item in native_results),
                         (ProviderUsage(28997, 195, 0), ProviderUsage(59869, 1216, 0)))
        usage_observations = tuple(call.accounting.usage_observation for call in result.provider_calls)
        self.assertTrue(all(item.scope is ProviderUsageScope.SESSION_CUMULATIVE for item in usage_observations))
        self.assertTrue(usage_observations[0].session_start)
        self.assertEqual(usage_observations[1].previous_prefix_ref, usage_observations[0].prefix_ref)
        self.assertEqual(usage_observations[1].session_ref, usage_observations[0].session_ref)
        self.assertEqual(normalized_provider_usage(usage_observations[1], usage_observations[0]),
                         ProviderUsage(59869, 1216, 0))
        observed_trace = LeadGroupTrace("native-reader-observation", tuple(group_observations), "accepted")
        native_report = group_trace_report(observed_trace,
            tuple((item.call_id, item.accounting) for item in result.provider_calls))
        self.assertEqual((native_report["submitted_calls"], native_report["input_tokens"],
                          native_report["output_tokens"], native_report["cached_input_tokens"]),
                         (2, 88866, 1411, 0))
        self.assertEqual((native_report["full_deliveries"], native_report["delta_deliveries"]), (1, 1))
        self.assertEqual(native_report["phase_submitted_calls"]["select"], 1)
        self.assertEqual(native_report["phase_submitted_calls"]["generate"], 1)
        self.assertEqual(result.group_state.transport_delivery.ledger_record.attempt.plan.delivery_mode.value,
                         "bootstrap")
        self.assertEqual(set(second["active_selection"]), {"origin_catalog_id", "group_id"})
        self.assertNotIn("catalog", second)
        self.assertNotIn(b'"catalog_at_selection"', invocations[1].payload_json)
        self.assertIn("selection_criteria", json.dumps(observed_trace.as_object()))
        for row, invocation, native_schema in zip(native_report["physical_calls"], invocations,
                                                   native_schema_bytes, strict=True):
            self.assertEqual(row["payload_utf8_bytes"], len(invocation.payload_json))
            self.assertEqual(row["schema_utf8_bytes"], len(native_schema))
            self.assertEqual(row["transport_utf8_bytes"], len(invocation.payload_json))
        self.assertTrue(native_report["result_accepted"])
        self.assertIsNone(native_report["task_completion"])
        self.assertIsNone(native_report["objective_completion"])
        self.assertIsNone(native_report["end_to_end_elapsed_ns"])
        self.assertIsNone(native_report["cost"])
        native_usage_cases = None

        # 실제 native 전송은 완료됐지만 업무 응답은 거절된 별도 front이다.
        requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
        history = OwnedNativeHistory(self, thread_id="66666666-6666-4666-8666-666666666666")
        def no_hit_search(call, provider):
            packet = json.loads(_search(call, provider))
            packet["agent_action"]["queries"][0]["search_text"] = "ZERO_HIT_GROUP_WITNESS_4d468"
            return json.dumps(packet)
        def rejected_generate(call, provider):
            packet = json.loads(_group_answer(call, provider))
            packet["agent_action"]["task_result"]["response_text"] = 7
            return json.dumps(packet)
        responses = [_group_control("answer.workflow"), no_hit_search, rejected_generate, _group_answer]
        phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE)
        rejected_request = _group_request(replace(request, group_state=None,
            operation_id="grouped-reader-reject", result_correlation_id="grouped-reader-reject-result", physical_call_limit=3))
        provider = next_reference_provider(2)
        authorities = dict(writing_artifacts=WritingArtifactSourceAdapter(repository, scope),
            focused_conversation=FocusedConversationSourceAdapter(storage.catalog, storage.event_store, scope, None),
            active_debate=ActiveDebateSourceAdapter(self.workspace, scope, None),
            own_agent_state=OwnAgentStateSourceAdapter(self.workspace, scope))
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            with self.assertRaises(ContextExecutionError) as held:
                await service.invoke(rejected_request, ObservedProvider(), **authorities)
            facts = context_provider_failure_facts(held.exception)
            retained = facts.group_state
            transport, accepted = retained.transport_delivery, retained.accepted_delivery
            self.assertIsNotNone(facts.response_rejection_code)
            self.assertEqual((len(invocations), len(facts.submitted_accountings)), (3, 3))
            self.assertEqual((transport.call_id, accepted.call_id),
                (invocations[2].context_call_id, invocations[1].context_call_id))
            self.assertIs(transport.witness, native_witnesses[2])
            self.assertIs(accepted.witness, native_witnesses[1])
            self.assertEqual(transport.payload_json, invocations[2].payload_json)
            self.assertEqual(transport.payload_sha256, hashlib.sha256(invocations[2].payload_json).hexdigest())
            self.assertTrue(transport.source_valid())
            record = ContextLedger(self.workspace).inspect(ContextLedgerKey(scope, provider.provider_id)).record
            self.assertTrue(transport.matches_ledger(record, latest=True))
            self.assertTrue(accepted.matches_ledger(record))
            self.assertFalse(accepted.matches_ledger(record, latest=True))
            absent = ContextLedger(self.workspace).inspect(ContextLedgerKey(replace(scope, subject_id="unrelated"), provider.provider_id)).record
            self.assertFalse(transport.matches_ledger(absent, latest=True))
            observation = requests[2].payload["context_changes"]["add"]["context_observations"]
            self.assertEqual(observation[0]["observation"]["search_results"], [])
            actual_wire = canonical_provider_json(requests[2].payload)
            callback_view = callback_views.for_request(requests[2])
            self.assertEqual(callback_view["context_observation"], observation[-1]["observation"])
            self.assertEqual(callback_view["task_input"], _PRIVATE_INPUT)
            self.assertEqual(callback_views.for_request(requests[2]), callback_view)
            self.assertEqual(canonical_provider_json(requests[2].payload), actual_wire)
            self.assertNotIn("read", requests[2].contract.offers.allowed_actions)
            self.assertEqual(requests[2].contract.offers.issued_handle_aliases, ())
            repair = replace(rejected_request, fresh=False, physical_call_limit=1, group_state=retained)
            provider = next_reference_provider(3)
            corrected = await service.invoke(repair, ObservedProvider(),
                contract_lineage=rejected_request.result_contract.repair_lineage(held.exception), **authorities)
        self.assertEqual((len(invocations), len(corrected.provider_calls)), (4, 1))
        self.assertEqual(len(native_commands), len(invocations))
        self.assertEqual(invocations[2].schema, invocations[3].schema)
        self.assertIs(invocations[3].context_witness, native_witnesses[2])
        self.assertIs(invocations[3].context_anchor, native_witnesses[1])
        self.assertEqual(requests[3].payload["response_contract"]["schema_selection"]["context_delivery"]["mode"], "delta")
        repair_delivery = requests[3].payload["response_contract"]["schema_selection"]["context_delivery"]
        self.assertEqual(repair_delivery["base_context_ref"], accepted.context_ref)
        self.assertNotEqual(repair_delivery["base_context_ref"], transport.context_ref)
        self.assertEqual(requests[3].payload["context_changes"]["add"]["context_observations"], observation)
        self.assertEqual(json.loads(raw_responses[2])["agent_action"]["task_result"]["response_text"], 7)
        self.assertTrue(any(record["request_type"] == "response"
            and json.loads(record["content"])["response_text"].encode("utf-8") == raw_responses[2].encode("utf-8")
            for record in _agent_exchange_records(self.workspace)))
        self.assertEqual(corrected.group_state.accepted_delivery.witness, native_witnesses[1])
        self.assertEqual(corrected.group_state.transport_delivery.witness, native_witnesses[3])
        self.assertIn(reference_wire, invocations[0].payload_json)
        self.assertEqual(["untrusted_attached_references" in call.payload.get("context_changes", {}).get("remove", [])
                          for call in requests], [False] * 4)
        self.assertEqual([callback_views.for_request(call)["untrusted_attached_references"] for call in requests],
                         [expected_references] * 4)
        self.assertEqual(json.loads(corrected.group_state.accepted_delivery.logical_context)["input"]["untrusted_attached_references"], expected_references)
        self.assertEqual(json.loads(corrected.group_state.transport_delivery.logical_context)["input"]["untrusted_attached_references"], expected_references)
        repository.finish_reference_operation(reference_authorization, outcome=None)
        assert_done_ledger(corrected)
        unknown = CallbackContextViews()
        self.assertRaises(FixturePhasePlanError, unknown.for_request, requests[0])
        self.assertRaises(FixturePhasePlanError, unknown.require_fixture_valid)

        # 유효한 native/ledger 근거가 있어도 Host의 업무 거절은 thread를 taint한다.
        requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
        history = OwnedNativeHistory(self, thread_id="77777777-7777-4777-8777-777777777777")
        responses = [_group_control("answer.workflow"), _group_answer]
        phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE)
        host_rejection = ValueError("Host rejected the valid typed result.")
        host_callback_reached = []
        def reject_business(answer, authorization):
            _LEAD_CALL_FACTORY.authorized_validator(answer, authorization)
            self.assertEqual(provider.inspect_context_thread(scope, requests[-1].policy_fingerprint), history.thread_id)
            host_callback_reached.append(True)
            raise host_rejection
        host_factory = replace(_LEAD_CALL_FACTORY, authorized_validator=reject_business)
        provider = bound.context_provider()
        host_request = _group_request(replace(request, group_state=None, result_contract=host_factory,
            operation_id="grouped-reader-host", result_correlation_id="grouped-reader-host-result", physical_call_limit=2,
            mandatory_references=(), reference_operation_id=None))
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            with self.assertRaises(LeadContractRejection) as denied:
                await service.invoke(host_request, ObservedProvider(), **authorities)
        self.assertEqual(denied.exception.code, "LEAD_OWNER_RULE_REJECTED")
        self.assertEqual(host_callback_reached, [True])
        host_facts = context_provider_failure_facts(denied.exception)
        self.assertEqual((len(invocations), len(native_commands), len(host_facts.submitted_accountings)), (2, 2, 2))
        self.assertEqual(requests[1].contract.structural_findings(raw_responses[1]), ())
        self.assertIs(host_facts.response_rejection_code, ctx_provider.ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED)
        self.assertIs(host_facts.group_state.workflow.phase, LeadGroupPhase.GENERATE)
        self.assertIs(invocations[1].context_witness, native_witnesses[0])
        self.assertIs(invocations[1].context_anchor, native_witnesses[0])
        self.assertIs(host_facts.group_state.transport_delivery.witness, native_witnesses[1])
        self.assertIs(host_facts.group_state.accepted_delivery.witness, native_witnesses[0])
        host_record = ContextLedger(self.workspace).inspect(ContextLedgerKey(scope, provider.provider_id)).record
        self.assertIs(host_record.state, ContextLedgerRecordState.TAINTED)
        self.assertIs(host_record.attempt.outcome, ContextLedgerAttemptOutcome.OUTPUT_INVALID)
        self.assertEqual(tuple(call.call_id for call in host_record.attempt.provider_calls),
                         tuple(call.call_id for call in requests))
        self.assertTrue(host_facts.group_state.transport_delivery.matches_ledger(host_record, latest=True))
        self.assertTrue(host_facts.group_state.accepted_delivery.matches_ledger(host_record))
        async with provider.invocation(scope, requests[-1].policy_fingerprint):
            self.assertIsNone(provider.inspect_context_thread(scope, requests[-1].policy_fingerprint))
        self.assertTrue(any(record["request_type"] == "response"
            and json.loads(record["content"])["response_text"].encode("utf-8") == raw_responses[1].encode("utf-8")
            for record in _agent_exchange_records(self.workspace)))

        # 동일한 합법 재선택은 native 연속성 증명이 유지되면 정확히 세 호출로 끝난다.
        requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
        native_schema_bytes = []
        history = OwnedNativeHistory(self, thread_id="88888888-8888-4888-8888-888888888888")
        responses = [_group_control("question.workflow"), _group_control("answer.workflow"), _group_answer]
        phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE)
        native_usage_cases = [
            {"total_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
            {"total_token_usage": {"input_tokens": 2000, "output_tokens": 20, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
            {"total_token_usage": {"input_tokens": 3000, "output_tokens": 30, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}}, ]
        def persist_owned_records(owned_history):
            return b"".join(canonical_provider_json(row) + b"\n" for row in owned_history.records)
        def add_noncritical_metadata(owned_history):
            owned_history.records.insert(1, {"type": "world_state", "payload": {"note": "metadata" * 16384}})
            owned_history.path.write_bytes(persist_owned_records(owned_history))
        def replace_metadata_without_changing_inputs(owned_history):
            for row in owned_history.records:
                if row["type"] == "world_state": row["payload"]["note"] = "short metadata"
                elif row["type"] == "session_meta": row["payload"]["cli_version"] = "0.164.1"
            import sqlite3
            with sqlite3.connect(owned_history.root / "state_5.sqlite") as connection:
                connection.execute("INSERT INTO threads VALUES (?, ?, 0)",
                    ("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", str(owned_history.path)))
            replacement = owned_history.path.with_suffix(".replacement")
            replacement.write_bytes(persist_owned_records(owned_history))
            replacement.chmod(0o600)
            replacement.replace(owned_history.path)
        def change_observed_settings(owned_history):
            context = next(row for row in reversed(owned_history.records) if row["type"] == "turn_context")
            context["payload"]["comp_hash"] = "changed-noncritical-settings"
            owned_history.path.write_bytes(persist_owned_records(owned_history))
        native_mutations = {1: add_noncritical_metadata, 2: replace_metadata_without_changing_inputs,
                            3: change_observed_settings}
        provider = bound.context_provider()
        reselect_request = _group_request(replace(request, group_state=None,
            operation_id="grouped-reader-reselection", result_correlation_id="grouped-reader-reselection-result",
            physical_call_limit=3, mandatory_references=(), reference_operation_id=None))
        reselect_observations = []
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            reselected = await service.invoke(reselect_request, ObservedProvider(),
                group_observation_sink=reselect_observations.append, **authorities)
        self.assertEqual((len(invocations), len(native_commands), len(reselected.provider_calls)), (3, 3, 3))
        self.assertEqual(tuple(call.contract.schema_group_phase for call in requests), phases)
        self.assertEqual(tuple(call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"]
                               for call in requests), ("bootstrap", "delta", "delta"))
        self.assertEqual(tuple(item.group_id for item in reselected.group_state.workflow.history),
                         ("question.workflow", "answer.workflow"))
        self.assertIs(reselected.group_state.workflow.phase, LeadGroupPhase.DONE)
        self.assertEqual(json.loads(raw_responses[1])["agent_action"]["task_result"]["response_type"],
                         "schema_group_reselection")
        self.assertEqual(requests[1].contract.structural_findings(raw_responses[1]), ())
        self.assertEqual(requests[1].contract.group_workflow.active_selection.group_id, "question.workflow")
        self.assertEqual(requests[2].contract.group_workflow.active_selection.group_id, "answer.workflow")
        controls = [item for item in reselect_observations if item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED]
        self.assertEqual(tuple(item.call_id for item in controls), tuple(call.call_id for call in requests[:2]))
        self.assertEqual(tuple(len(item.selection_snapshots) for item in controls), (1, 2))
        self.assertIs(invocations[1].context_witness, native_witnesses[0])
        self.assertIs(invocations[2].context_witness, native_witnesses[1])
        self.assertIs(invocations[2].context_anchor, native_witnesses[1])
        self.assertIs(reselected.group_state.accepted_delivery.witness, native_witnesses[1])
        self.assertIs(reselected.group_state.transport_delivery.witness, native_witnesses[2])
        reselect_report = group_trace_report(LeadGroupTrace("native-reader-reselection",
            tuple(reselect_observations), "accepted"),
            tuple((item.call_id, item.accounting) for item in reselected.provider_calls))
        self.assertEqual(reselect_report["phase_submitted_calls"], {"select": 1, "generate": 2})
        self.assertEqual((reselect_report["submitted_calls"], reselect_report["reselection_count"],
                          reselect_report["repairs"], reselect_report["replays"]), (3, 1, 0, 0))
        self.assertEqual((reselect_report["full_deliveries"], reselect_report["delta_deliveries"]), (1, 2))
        self.assertIsNone(reselect_report["input_tokens"])
        self.assertIsNone(reselect_report["output_tokens"])
        self.assertTrue(all(result.native_context_check.verified for result in native_results[-3:]))
        self.assertEqual(tuple(witness.input_lineage_count for witness in native_witnesses), (1, 2, 3))
        self.assertTrue(all(witness.bootstrap_turn_id == native_witnesses[0].turn_id and
                            witness.bootstrap_payload_sha256 == native_witnesses[0].payload_sha256 and
                            witness.bootstrap_call_id == requests[0].call_id for witness in native_witnesses))
        self.assertTrue(native_witnesses[2].has_lineage_anchor(native_witnesses[1]))
        self.assertNotEqual(native_witnesses[1].file_identity, native_witnesses[0].file_identity)
        self.assertLess(native_witnesses[1].prefix_utf8_bytes, native_witnesses[0].prefix_utf8_bytes)
        self.assertIs(native_results[-2].usage_observation.scope, ProviderUsageScope.SESSION_CUMULATIVE)
        self.assertEqual(native_results[-2].usage, ProviderUsage(2000, 20, 0))
        self.assertEqual(reselect_report["physical_calls"][1]["attributable_usage"],
                         {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0})
        self.assertIs(native_results[-1].usage_observation.scope, ProviderUsageScope.UNKNOWN)
        self.assertEqual(native_results[-1].last_token_usage, ProviderUsage(1000, 10, 0))
        self.assertIsNone(reselect_report["physical_calls"][2]["attributable_usage"])
        assert_done_ledger(reselected)

        # 실제 과거 입력 모순일 때만 같은 재선택을 거절하고 새 thread에서 한 번 복구한다.
        requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
        native_results = []
        history = OwnedNativeHistory(self, thread_id="99999999-9999-4999-8999-999999999999")
        responses = [_group_control("question.workflow"), _group_control("answer.workflow"),
                     _group_control("answer.workflow"), _group_answer]
        phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE)
        native_usage_cases = [
            {"total_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
            {"total_token_usage": {"input_tokens": 2000, "output_tokens": 20, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
            {"total_token_usage": {"input_tokens": 1100, "output_tokens": 11, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1100, "output_tokens": 11, "cached_input_tokens": 0}},
            {"total_token_usage": {"input_tokens": 2100, "output_tokens": 21, "cached_input_tokens": 0},
             "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}}, ]
        def change_bootstrap_input(owned_history):
            record = next(row for row in owned_history.records if row["type"] == "response_item"
                          and row["payload"].get("role") == "user")
            changed = json.loads(record["payload"]["content"][0]["text"])
            changed["task_input"] = "CHANGED_PRIOR_NATIVE_INPUT"
            record["payload"]["content"][0]["text"] = canonical_provider_json(changed).decode("utf-8")
            owned_history.path.write_bytes(b"".join(canonical_provider_json(row) + b"\n"
                                                   for row in owned_history.records))
        import sqlite3
        held_databases = []
        def hold_native_database(owned_history):
            connection = sqlite3.connect(owned_history.root / "state_5.sqlite")
            self.addCleanup(connection.close)
            connection.execute("BEGIN EXCLUSIVE")
            held_databases.append(connection)
        def release_native_database(): held_databases[-1].rollback()
        native_mutations = {2: change_bootstrap_input, 3: hold_native_database}
        native_cleanups = {3: release_native_database}
        unavailable_native_calls = {2, 3}
        provider = bound.context_provider()
        interrupted_request = _group_request(replace(request, group_state=None,
            operation_id="grouped-reader-reselection-interrupted",
            result_correlation_id="grouped-reader-reselection-interrupted-result",
            physical_call_limit=5, mandatory_references=(), reference_operation_id=None))
        interrupted_observations = []
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            with self.assertRaises(ContextExecutionError) as interrupted:
                await service.invoke(interrupted_request, ObservedProvider(),
                    group_observation_sink=interrupted_observations.append, **authorities)
        interrupted_facts = context_provider_failure_facts(interrupted.exception)
        self.assertEqual((len(invocations), len(native_commands), len(interrupted_facts.submitted_accountings)), (2, 2, 2))
        self.assertEqual(requests[1].contract.structural_findings(raw_responses[1]), ())
        self.assertEqual(json.loads(raw_responses[1])["agent_action"]["task_result"]["response_type"],
                         "schema_group_reselection")
        self.assertEqual(tuple(item.group_id for item in interrupted_facts.group_state.workflow.history),
                         ("question.workflow",))
        self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED
                             for item in interrupted_observations), 1)
        self.assertIs(interrupted_facts.submitted_accountings[-1].usage_observation.scope, ProviderUsageScope.UNKNOWN)
        self.assertEqual(interrupted_facts.submitted_accountings[-1].usage, ProviderUsage(2000, 20, 0))
        rejected_check = native_results[1].native_context_check
        self.assertEqual(interrupted_facts.native_context_check, rejected_check)
        self.assertIs(rejected_check.stage, NativeContextCheckStage.POST_COMPLETE)
        self.assertEqual(rejected_check.outcome, "contradiction")
        self.assertIs(rejected_check.observations[-1].source_stage, NativeContextSourceStage.RECORDS)
        self.assertEqual((rejected_check.call_id, rejected_check.prior_call_id),
                         (requests[1].call_id, requests[0].call_id))
        self.assertFalse(rejected_check.verified)
        self.assertTrue(rejected_check.pre_submit_verified)
        self.assertEqual(rejected_check.observed_thread_id, native_results[1].thread_id)
        self.assertEqual(rejected_check.normal_exit_code, 0)
        self.assertEqual(sum(item.stage is NativeContextCheckStage.POST_COMPLETE
                             for item in rejected_check.observations), 1)
        diagnostic = rejected_check.as_object()
        self.assertEqual(set(diagnostic), {"call_id", "prior_call_id", "outcome", "observations",
                                          "observed_thread_id", "normal_exit_code"})
        self.assertEqual(set(diagnostic["observations"][-1]),
                         {"stage", "source_stage", "reason", "exception_type", "error_code"})
        self.assertEqual(NativeContextCheck.from_object(diagnostic), rejected_check)
        self.assertNotIn(str(history.root), json.dumps(diagnostic))
        self.assertNotIn(_PRIVATE_INPUT, json.dumps(diagnostic))
        injected_diagnostic = json.loads(json.dumps(diagnostic))
        injected_diagnostic["observations"][-1]["error_message"] = "/private/diagnostic-source.sql"
        self.assertRaises(ValueError, NativeContextCheck.from_object, injected_diagnostic)
        self.assertTrue(any(record["request_type"] == "response" and
            json.loads(record["content"])["response_text"].encode("utf-8") == raw_responses[1].encode("utf-8")
            for record in _agent_exchange_records(self.workspace)))
        async with provider.invocation(scope, requests[-1].policy_fingerprint):
            self.assertIsNone(provider.inspect_context_thread(scope, requests[-1].policy_fingerprint))
        first_thread = history.thread_id
        history = OwnedNativeHistory(self, thread_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        retried_observations = []
        retry_request = replace(interrupted_request, physical_call_limit=3,
                                group_state=interrupted_facts.group_state)
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            retried = await service.invoke(retry_request, ObservedProvider(),
                group_observation_sink=retried_observations.append, **authorities)
        self.assertEqual((len(invocations), len(native_commands), len(retried.provider_calls)), (4, 4, 2))
        self.assertIsNone(invocations[2].thread_id)
        self.assertNotEqual(history.thread_id, first_thread)
        self.assertEqual(invocations[3].thread_id, history.thread_id)
        self.assertEqual(tuple(call.payload["response_contract"]["schema_selection"]["remaining_provider_calls"]
                               for call in requests), (5, 4, 3, 2))
        self.assertEqual(tuple(call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"]
                               for call in requests), ("bootstrap", "delta", "bootstrap", "bootstrap"))
        self.assertEqual(tuple(item.group_id for item in retried.group_state.workflow.history),
                         ("question.workflow", "answer.workflow"))
        self.assertEqual(tuple(json.loads(raw_responses[index])["agent_action"]["task_result"]["group_id"]
                               for index in (1, 2)), ("answer.workflow", "answer.workflow"))
        self.assertEqual(tuple(call.contract.group_workflow.active_selection.group_id
                               for call in requests[1:]), ("question.workflow", "question.workflow", "answer.workflow"))
        self.assertIs(retried.group_state.workflow.phase, LeadGroupPhase.DONE)
        self.assertEqual(tuple(result.context_witness is None for result in native_results),
                         (False, True, True, False))
        self.assertIs(native_results[2].native_context_check.reason, NativeContextCheckReason.DATABASE_BUSY)
        self.assertEqual(native_results[2].native_context_check.outcome, "unavailable")
        self.assertTrue(native_results[3].native_context_check.verified)
        self.assertIs(retried.provider_calls[0].accounting.usage_observation.scope, ProviderUsageScope.UNKNOWN)
        self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED
                             for item in retried_observations), 1)
        self.assertTrue(all(item.accounting.state.value == "submitted" for item in retried.provider_calls))
        self.assertEqual(next(json.loads(item.delivery_snapshot)["decision_reason"] for item in retried_observations
                              if item.kind is LeadGroupObservationKind.BOUND and item.call_id == requests[3].call_id),
                         "preservation_unproven")
        self.assertEqual(tuple(result.response_text for result in native_results), tuple(raw_responses))
        self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED
                             for item in (*interrupted_observations, *retried_observations)), 2)
        retry_report = group_trace_report(LeadGroupTrace("native-reader-reselection-retry",
            tuple((*interrupted_observations, *retried_observations)), "accepted"),
            tuple((call.call_id, accounting) for call, accounting in
                  zip(requests[:2], interrupted_facts.submitted_accountings, strict=True)) +
            tuple((item.call_id, item.accounting) for item in retried.provider_calls))
        self.assertEqual((retry_report["submitted_calls"], retry_report["reselection_count"]), (4, 1))
        self.assertEqual(retry_report["phase_submitted_calls"], {"select": 1, "generate": 3})
        self.assertEqual((retry_report["full_deliveries"], retry_report["delta_deliveries"]), (3, 1))
        self.assertIsNone(retry_report["input_tokens"])
        self.assertIsNone(retry_report["output_tokens"])
        assert_done_ledger(retried)

        # 관측 부재와 실제 모순은 다르며, 재관측은 물리 Provider 호출을 늘리지 않는다.
        for transient, report_thread, unexpected, native_thread in (
            (True, True, False, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            (False, True, False, "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            (False, False, False, "dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            (False, True, True, "abababab-abab-4bab-8bab-abababababab"), ):
            requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
            native_results = []
            history = OwnedNativeHistory(self, thread_id=native_thread)
            responses = [_group_control("question.workflow"), _group_control("answer.workflow"), _group_answer]
            phases = (LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE, LeadGroupPhase.GENERATE)
            native_usage_cases = [
                {"total_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0},
                 "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
                {"total_token_usage": {"input_tokens": 2000, "output_tokens": 20, "cached_input_tokens": 0},
                 "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}},
                {"total_token_usage": {"input_tokens": 3000, "output_tokens": 30, "cached_input_tokens": 0},
                 "last_token_usage": {"input_tokens": 1000, "output_tokens": 10, "cached_input_tokens": 0}}, ]
            hidden, inspect_native = [], codex_native_context.inspect_codex_native_observation_async
            def restore_rollout():
                if hidden and hidden[-1][0].exists(): hidden[-1][0].replace(hidden[-1][1])
            async def inspect_before_publication(*args, **kwargs):
                observed = await inspect_native(*args, **kwargs)
                if transient and hidden and observed.check.reason is NativeContextCheckReason.ROLLOUT_NOT_VISIBLE:
                    restore_rollout()
                return observed
            def hide_rollout(owned_history):
                saved = owned_history.path.with_suffix(".pending")
                owned_history.path.replace(saved)
                hidden.append((saved, owned_history.path))
                self.addCleanup(restore_rollout)
            native_mutations, native_cleanups = {2: hide_rollout}, {2: restore_rollout}
            inspection_fault, fault_active = RuntimeError("PRIVATE_NATIVE_INSPECTION_DETAIL"), [False]
            original_file_facts = codex_native_context._native_file_facts
            def file_facts(path, *args, **kwargs):
                if fault_active[0] and Path(path) == history.path: raise inspection_fault
                return original_file_facts(path, *args, **kwargs)
            pre_submit_probe = None
            if unexpected:
                def fail_post_inspection(_owned_history): fault_active[0] = True
                def finish_post_inspection(): fault_active[0] = False
                native_mutations, native_cleanups = {2: fail_post_inspection}, {2: finish_post_inspection}
                async def pre_submit_probe(binding, invocation):
                    no_native = mock.AsyncMock(side_effect=AssertionError("precheck reached native execution"))
                    fault_active[0] = True
                    try:
                        with mock.patch.dict(os.environ, {"CODEX_HOME": str(history.root)}), \
                                mock.patch("story_writing_agents.codex_adapter.run_process", new=no_native), \
                                self.assertRaises(ProviderInvocationError) as before_native:
                            await native_invoke(binding, invocation)
                    finally: fault_active[0] = False
                    no_native.assert_not_awaited()
                    self.assertIs(before_native.exception.__cause__, inspection_fault)
                    self.assertIs(before_native.exception.native_context_check.stage, NativeContextCheckStage.PRE_SUBMIT)
                    self.assertFalse(before_native.exception.native_context_check.pre_submit_verified)
            unavailable_native_calls, omitted_thread_reports, observations = {2}, set() if report_thread else {2}, []
            provider = bound.context_provider()
            unavailable_request = _group_request(replace(request, group_state=None,
                operation_id="native-observation-" + native_thread,
                result_correlation_id="native-observation-result-" + native_thread,
                physical_call_limit=3, mandatory_references=(), reference_operation_id=None))
            with mock.patch.object(backend._codex_adapter, "invoke", new=invoke), \
                    mock.patch.object(codex_native_context, "_native_file_facts", new=file_facts), \
                    mock.patch.object(codex_native_context, "inspect_codex_native_observation_async", new=inspect_before_publication):
                if report_thread:
                    observed_result = await service.invoke(unavailable_request, ObservedProvider(),
                        group_observation_sink=observations.append, **authorities)
                else:
                    with self.assertRaises(ContextExecutionError) as no_report:
                        await service.invoke(unavailable_request, ObservedProvider(),
                            group_observation_sink=observations.append, **authorities)
            check = native_results[1].native_context_check
            self.assertEqual(native_results[1].context_witness is not None, transient)
            self.assertEqual(native_results[1].last_token_usage,
                             ProviderUsage(1000, 10, 0) if transient else None)
            self.assertTrue(check.pre_submit_verified)
            self.assertEqual(check.normal_exit_code, 0)
            self.assertEqual(native_results[1].thread_id, native_thread)
            self.assertEqual(check.observed_thread_id, native_thread if report_thread else None)
            self.assertEqual(requests[1].contract.structural_findings(raw_responses[1]), ())
            post = [item for item in check.observations if item.stage is NativeContextCheckStage.POST_COMPLETE]
            self.assertIs(post[0].reason, NativeContextCheckReason.INSPECTION_FAILED if unexpected
                          else NativeContextCheckReason.ROLLOUT_NOT_VISIBLE)
            if not report_thread:
                facts = context_provider_failure_facts(no_report.exception)
                self.assertEqual((len(native_commands), len(facts.submitted_accountings)), (2, 2))
                self.assertEqual(len(facts.group_state.workflow.history), 1)
                self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED for item in observations), 1)
                self.assertEqual(len(responses), 1)
                continue
            self.assertEqual((len(invocations), len(native_commands), len(observed_result.provider_calls)), (3, 3, 3))
            self.assertEqual(tuple(item.group_id for item in observed_result.group_state.workflow.history),
                             ("question.workflow", "answer.workflow"))
            self.assertIs(observed_result.group_state.workflow.phase, LeadGroupPhase.DONE)
            self.assertEqual(sum(item.kind is LeadGroupObservationKind.CONTROL_ACCEPTED for item in observations), 2)
            self.assertEqual(len(post), 1 if unexpected else 2 if transient else 3)
            self.assertEqual(check.outcome, "verified" if transient else "unavailable")
            if unexpected:
                self.assertIs(native_results[1].native_context_cause, inspection_fault)
                self.assertEqual(post[0].exception_type, "RuntimeError")
                self.assertNotIn(str(inspection_fault), json.dumps(check.as_object()))
            self.assertEqual(tuple(call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"]
                                   for call in requests), ("bootstrap", "delta", "delta" if transient else "bootstrap"))
            self.assertEqual(tuple(result.response_text for result in native_results), tuple(raw_responses))
            self.assertEqual(responses, [])
            assert_done_ledger(observed_result)

        # 최신 accepted 선택보다 앞의 READ 입력도 최초 bootstrap 계보에 남아야 한다.
        from tests.agent_context.invocation.execution_fixture import make_read_search_hit as _read_search_hit
        read_marker = "NATIVE_LINEAGE_READ_SOURCE_904"
        self.workspace.path("core_documents/global_outline.md").write_text(read_marker, encoding="utf-8")
        def search_lineage_source(call, _provider):
            packet = json.loads(_search(call, None))
            packet["agent_action"]["queries"][0]["search_text"] = read_marker
            return json.dumps(packet)
        read_view = SimpleNamespace(callback_payload=callback_views.for_request)
        def read_lineage_source(call, _provider): return _read_search_hit(call, read_view)
        def change_middle_read(owned_history):
            marker = requests[2].call_id
            matching = [row for row in owned_history.records if row["type"] == "response_item"
                        and row["payload"].get("role") == "user" and
                        json.loads(row["payload"]["content"][0]["text"])["response_contract"]["schema_selection"]["issued_contract"]["call_id"] == marker]
            self.assertEqual(len(matching), 1)
            if lose_read == "delete": owned_history.records.remove(matching[0])
            else:
                packet = json.loads(matching[0]["payload"]["content"][0]["text"])
                packet["request_correlation_token"] = "ALTERED_MIDDLE_READ"
                matching[0]["payload"]["content"][0]["text"] = canonical_provider_json(packet).decode("utf-8")
            owned_history.path.write_bytes(persist_owned_records(owned_history))
        for lose_read, native_thread in ( (False, "12121212-1212-4212-8212-121212121212"),
            (True, "13131313-1313-4313-8313-131313131313"),
            ("delete", "14141414-1414-4414-8414-141414141414"), ):
            requests, invocations, raw_responses, native_witnesses, native_commands = [], [], [], [], []
            native_results, native_usage_cases = [], None
            pre_submit_probe = None
            history = OwnedNativeHistory(self, thread_id=native_thread)
            responses = [_group_control("question.workflow"), search_lineage_source, read_lineage_source,
                         _group_control("answer.workflow"), _group_answer]
            phases = (LeadGroupPhase.SELECT,) + (LeadGroupPhase.GENERATE,) * 4
            native_mutations = {5: change_middle_read} if lose_read else {}
            native_cleanups, omitted_thread_reports = {}, set()
            unavailable_native_calls = {5} if lose_read else set()
            provider = bound.context_provider()
            read_request = _group_request(replace(request, group_state=None,
                operation_id="native-read-lineage-" + native_thread,
                result_correlation_id="native-read-lineage-result-" + native_thread,
                physical_call_limit=5, mandatory_references=(), reference_operation_id=None))
            observations = []
            with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
                if lose_read:
                    with self.assertRaises(ContextExecutionError) as missing_read:
                        await service.invoke(read_request, ObservedProvider(),
                            group_observation_sink=observations.append, **authorities)
                else: complete_read = await service.invoke(read_request, ObservedProvider(),
                        group_observation_sink=observations.append, **authorities)
            self.assertEqual((len(invocations), len(native_commands)), (5, 5))
            self.assertEqual(tuple(call.payload["response_contract"]["schema_selection"]["context_delivery"]["mode"]
                                   for call in requests), ("bootstrap", "delta", "delta", "delta", "delta"))
            self.assertEqual(tuple(witness.input_lineage_count for witness in native_witnesses[:4]), (1, 2, 3, 4))
            self.assertEqual(requests[3].contract.group_workflow.active_selection.group_id, "question.workflow")
            self.assertEqual(requests[4].contract.group_workflow.active_selection.group_id, "answer.workflow")
            self.assertIn(read_marker, canonical_provider_json(callback_views.for_request(requests[3])).decode("utf-8"))
            self.assertEqual(requests[4].contract.structural_findings(raw_responses[4]), ())
            if lose_read:
                facts = context_provider_failure_facts(missing_read.exception)
                self.assertEqual(len(facts.submitted_accountings), 5)
                self.assertEqual(native_results[-1].native_context_check.outcome, "contradiction")
                self.assertIs(native_results[-1].native_context_check.reason, NativeContextCheckReason.LINEAGE_MISMATCH)
                self.assertEqual(len(facts.group_state.workflow.history), 2)
            else:
                self.assertEqual(native_witnesses[-1].input_lineage_count, 5)
                self.assertTrue(native_witnesses[-1].has_lineage_anchor(native_witnesses[3]))
                self.assertIs(complete_read.group_state.workflow.phase, LeadGroupPhase.DONE)
                self.assertEqual(len(complete_read.provider_calls), 5)
                assert_done_ledger(complete_read)

    def _assert_owned_native_context_witness(self, invocation) -> None:
        import sqlite3
        from story_writing_agents import codex_event_decoding, codex_native_context
        from story_writing_agents.provider_schema_selection import SchemaSelectionInput

        thread = "11111111-1111-4111-8111-111111111111"
        rollout = "22222222-2222-4222-8222-222222222222"
        selection = SchemaSelectionInput.from_value(invocation.payload["response_contract"]["schema_selection"])
        payload = dict(invocation.payload)
        first = invocation
        self.assertIsNotNone(first.payload_json)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            path = root / "sessions" / "2026" / "10" / "30" / f"rollout-2026-10-30T00-00-00-{thread}_{rollout}.jsonl"
            path.parent.mkdir(parents=True, mode=0o700)
            for parent in (root / "sessions", path.parent.parent, path.parent): parent.chmod(0o700)
            database = root / "state_5.sqlite"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER)")
                connection.execute("INSERT INTO threads VALUES (?, ?, 0)", (thread, str(path)))
            database.chmod(0o600)
            metadata = {"type": "session_meta", "payload": {"id": thread, "session_id": "separate-session-id",
                "cli_version": "0.164.0", "originator": "codex_exec", "source": "exec", "history_mode": "paginated",
                "cwd": str(invocation.execution_workspace.worktree_root)}}
            settings = {"type": "turn_context", "payload": {"model": "fixture-model", "comp_hash": "configuration-hash", "multi_agent_version": "fixture"}}

            def turn(request, identity):
                return [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": identity}}, settings,
                    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                        {"type": "input_text", "text": request.payload_json.decode("utf-8")}]}},
                    {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": identity}}]

            def write(records):
                path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode() + b"\n" for r in records))
                path.chmod(0o600)

            records = [metadata, *turn(first, "turn-1")]
            write(records)
            witness = codex_native_context.inspect_codex_context_witness(root, first, thread)
            self.assertIsNotNone(witness)
            self.assertEqual((witness.thread_id, witness.rollout_id), (thread, rollout))
            self.assertEqual(witness.call_id, selection.call_id)
            self.assertEqual(witness.payload_sha256, hashlib.sha256(first.payload_json).hexdigest())
            self.assertEqual(witness.turn_id, "turn-1")
            environment_text = ("<environment_context>\n<cwd>" + str(first.execution_workspace.worktree_root)
                + "</cwd>\n<shell>zsh</shell>\n<current_date>2026-11-03</current_date>\n<timezone>Asia/Seoul</timezone>\n"
                + "<filesystem><workspace_roots><root>" + str(first.execution_workspace.worktree_root)
                + "</root></workspace_roots><permission_profile type=\"managed\"><file_system type=\"restricted\">"
                + "<entry access=\"read\"><special>:root</special></entry></file_system></permission_profile></filesystem>\n</environment_context>")
            def user(text):
                return {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": text}]}}
            started, context, wire, completed = turn(first, "turn-1")
            environment = user(environment_text)
            other_wire = user(json.dumps({**json.loads(first.payload_json), "other_business_request": True}))
            for messages, accepted in (([environment, wire], True), ([environment, wire, wire], False),
                    ([environment, wire, other_wire], False), ([wire, environment], False),
                    ([environment, environment, wire], False), ([user("Unrelated author instruction"), wire], False),
                    ([user("prefix" + environment_text), wire], False), ([user(environment_text + "business prose"), wire], False),
                    ([environment, other_wire], False)):
                write([metadata, started, context, *messages, completed])
                observed = codex_native_context.inspect_codex_context_witness(root, first, thread)
                self.assertEqual(observed is not None, accepted, messages)
                if accepted: self.assertEqual(observed.input_lineage_count, 1)
            write([metadata, started, context, environment, wire])
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            write(records)
            from story_writing_agents.agent_context.accounting import ProviderUsage, ProviderUsageScope, normalized_provider_usage
            raw_usage = ProviderUsage(7, 3, 0)
            self.assertIsNone(codex_native_context.inspect_codex_native_observation(root, first, thread).last_token_usage)
            def token_count(info):
                return {"type": "event_msg", "payload": {"type": "token_count", "info": info}}
            last = {"input_tokens": 7, "output_tokens": 3, "cached_input_tokens": 0}
            stale = token_count({"total_token_usage": {"input_tokens": 91, "output_tokens": 10, "cached_input_tokens": 8},
                                 "last_token_usage": {"input_tokens": 90, "output_tokens": 9, "cached_input_tokens": 8}})
            for info, expected in (({"last_token_usage": last}, raw_usage),
                    ({"total_token_usage": {"input_tokens": True}, "last_token_usage": last}, raw_usage),
                    ({"last_token_usage": dict.fromkeys(last, 0)}, ProviderUsage(0, 0, 0)),
                    ({"last_token_usage": {**last, "input_tokens": True}}, None), ({}, None)):
                write([metadata, started, context, wire, stale, token_count(info), completed])
                native = codex_native_context.inspect_codex_native_observation(root, first, thread)
                self.assertIsNotNone(native.witness)
                self.assertEqual(native.last_token_usage, expected)
            write(records)
            uninspected = codex_event_decoding.codex_usage_observation(raw_usage, None, replace(first, context_call_id=None))
            self.assertEqual(uninspected.raw, raw_usage)
            self.assertIs(uninspected.scope, ProviderUsageScope.UNKNOWN)
            self.assertEqual(uninspected.source, "codex.turn.completed.usage:context_not_inspected")
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread, previous=witness, before=True))
            self.assertIs(codex_native_context.inspect_codex_context_witness(root, first, thread, previous=witness, anchor=witness, before=True), witness)
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, rollout))
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, replace(first, session_id="wrong-scope"), thread, previous=witness, anchor=witness))
            second_payload = dict(payload)
            second_payload["response_contract"] = {**payload["response_contract"], "schema_selection": replace(selection, call_id=selection.call_id + "-next").as_object()}
            second = replace(first, payload=second_payload, context_call_id=selection.call_id + "-next", thread_id=thread)
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            extended = [*records, *turn(second, "turn-2")]
            write(extended)
            continued = codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness)
            self.assertIsNotNone(continued)
            self.assertEqual(continued.turn_id, "turn-2")
            write([metadata, started, context, wire, stale, completed, *turn(second, "turn-2")])
            native = codex_native_context.inspect_codex_native_observation(root, second, thread, previous=witness, anchor=witness)
            self.assertIsNotNone(native.witness)
            self.assertIsNone(native.last_token_usage)
            write(extended)
            total_first, total_second, last_second = ProviderUsage(28997, 195, 0), ProviderUsage(88866, 1411, 0), ProviderUsage(59869, 1216, 0)
            first_observed = SimpleNamespace(witness=witness, usage_pair=(total_first, total_first), previous_usage=None, first_native_turn=True)
            resumed = codex_event_decoding.codex_usage_observation(total_first, first_observed, replace(first, thread_id=thread))
            self.assertIs(resumed.scope, ProviderUsageScope.SESSION_CUMULATIVE)
            self.assertFalse(resumed.session_start)
            self.assertIsNone(resumed.previous_prefix_ref)
            self.assertIsNone(normalized_provider_usage(resumed))
            second_observed = SimpleNamespace(witness=continued, usage_pair=(total_second, last_second), previous_usage=total_first, first_native_turn=False)
            next_invocation = replace(second, context_witness=witness, context_anchor=witness)
            recovered = codex_event_decoding.codex_usage_observation(total_second, second_observed, next_invocation)
            self.assertEqual(recovered.previous_prefix_ref, resumed.prefix_ref)
            self.assertEqual(normalized_provider_usage(recovered, resumed), last_second)
            for changes, raw, reason in (({"usage_pair": None}, total_second, "usage_missing"),
                    ({}, total_first, "raw_total_mismatch"), ({"previous_usage": None}, total_second, "predecessor_missing"),
                    ({"previous_usage": ProviderUsage(90000, 1500, 0)}, total_second, "cumulative_reset"),
                    ({"usage_pair": (total_second, total_first)}, total_second, "native_last_mismatch"),
                    ({"witness": replace(continued, settings_digest="0" * 64)}, total_second, "settings_changed")):
                observed = SimpleNamespace(**{**vars(second_observed), **changes})
                unavailable = codex_event_decoding.codex_usage_observation(raw, observed, next_invocation)
                self.assertIs(unavailable.scope, ProviderUsageScope.UNKNOWN)
                self.assertEqual(unavailable.raw, raw)
                self.assertEqual(unavailable.source, "codex.turn.completed.usage:" + reason)
            self.assertIsNone(codex_event_decoding.codex_usage_observation(None, second_observed, next_invocation))
            self.assertEqual(continued.file_identity, witness.file_identity)
            self.assertGreater(continued.prefix_utf8_bytes, witness.prefix_utf8_bytes)
            self.assertEqual(hashlib.sha256(path.read_bytes()[:witness.prefix_utf8_bytes]).hexdigest(), witness.prefix_sha256)
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness, before=True))
            invalid = [ [*records, *turn(second, "turn-2")[:-1]],
                [*records, {"type": "compacted", "payload": {}}, *turn(second, "turn-2")],
                [*records, {"type": "event_msg", "payload": {"type": "thread_rolled_back", "turn_id": "turn-2"}}, *turn(second, "turn-2")],
                [*records, *turn(second, "turn-2"), *turn(second, "turn-3")], ]
            for bad_records in invalid:
                write(bad_records)
                self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            # 비핵심 기록·설정 변화는 보존된 실제 입력 계보를 무효화하지 않는다.
            for metadata_only in (
                [*records, {"type": "unknown_record", "payload": {}}, *turn(second, "turn-2")],
                [*records, {"type": "turn_context", "payload": {**settings["payload"], "comp_hash": "different-configuration"}}, *turn(second, "turn-2")],
            ):
                write(metadata_only)
                self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread,
                    previous=witness, anchor=witness))
            for key in ("history_base", "forked_from_id", "parent_thread_id", "forked_from_ordinal_exclusive"):
                write([{**metadata, "payload": {**metadata["payload"], key: "unproven-branch"}}, settings, *turn(first, "turn-1")])
                self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            write([{**metadata, "payload": {**metadata["payload"], "id": rollout}}, settings, *turn(first, "turn-1")])
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            write(extended)
            with mock.patch.object(codex_native_context, "CONTEXT_WITNESS_BYTES", path.stat().st_size - 1):
                self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            with mock.patch.object(codex_native_context, "CONTEXT_WITNESS_LINES", len(extended) - 1):
                self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            wrong_prefix = path.read_bytes().replace(b"configuration-hash", b"configuration-edit", 1)
            path.write_bytes(wrong_prefix)
            self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            write(extended)
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
            self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            with sqlite3.connect(database) as connection: connection.execute("UPDATE threads SET archived=1")
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE threads SET archived=0, rollout_path=?", (str(root / "outside.jsonl"),))
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            target = path.with_suffix(".owned")
            path.replace(target)
            path.symlink_to(target)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE threads SET rollout_path=?", (str(path),))
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, first, thread))
            path.unlink()
            target.replace(path)
            current = codex_native_context.inspect_codex_context_witness(root, second, thread)
            self.assertIsNotNone(current)
            alternate = path.with_name(path.name.replace(rollout, "55555555-5555-4555-8555-555555555555"))
            alternate.write_bytes(path.read_bytes())
            alternate.chmod(0o600)
            original = path.with_suffix(".original")
            path.replace(original)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE threads SET rollout_path=?", (str(alternate),))
            self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=current, anchor=current, before=True))
            self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=current, anchor=current))
            self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            original.replace(path)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE threads SET rollout_path=?", (str(path),))
            self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread,
                previous=current, anchor=replace(current, prefix_sha256="0" * 64), before=True))
            with mock.patch.object(codex_native_context.os, "geteuid", return_value=path.stat().st_uid + 1):
                self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread))
            real_facts = codex_native_context._native_file_facts
            observed = 0

            def concurrent_change(candidate, **kwargs):
                nonlocal observed
                facts = real_facts(candidate, **kwargs)
                if candidate == path:
                    observed += 1
                    if observed == 1:
                        with path.open("ab") as stream: stream.write(b'{"type":"world_state","payload":{}}\n')
                return facts

            with mock.patch.object(codex_native_context, "_native_file_facts", side_effect=concurrent_change):
                self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread, previous=witness, anchor=witness))
            self.assertGreaterEqual(observed, 1)
            write(extended)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
                connection.execute("UPDATE threads SET archived=0")
                connection.commit()
                self.assertIsNotNone(codex_native_context.inspect_codex_context_witness(root, second, thread))
                shared = database.with_name(database.name + "-shm")
                saved = shared.with_suffix(".saved")
                self.assertTrue(shared.exists())
                shared.replace(saved)
                try:
                    self.assertIsNone(codex_native_context.inspect_codex_context_witness(root, second, thread))
                    self.assertFalse(shared.exists())
                finally: saved.replace(shared)

    async def _exercise_preliminary_capture(self, *, repair=False, seed=False, failure=None):
        with mock.patch.object(self, "workspace", pristine_initialized_workspace(self)):
            from tests.provider_wire_fixture import ExplicitPhaseScript, selection_plan, lead_fixture_response, lead_fixture_bound_scope, finite_fixture_selection, CallbackContextViews
            from tests.lead_fixtures.preparation_data import lead_fixture_authoring_scope
            from story_writing_agents.runtime_resources import provider_call_accounting as accounting_module
            from story_writing_agents.agent_context.ledger.checkpoint_ledger import ContextLedger
            from story_writing_agents import conversations as c
            from story_writing_agents.agent_exchanges import AgentExchangeSelection, AgentExchangeScopeKind, WorkspaceAgentExchangeStore
            from tests.test_agent_exchange_viewer import _prepare_view, _transport
            import jsonl_viewer
            from jsonl_viewer._catalog import bind_catalog
            from jsonl_viewer._input import parse_jsonl
            from jsonl_viewer._query import evaluate
            def preliminary(call, _provider):
                targets = tuple(map(json.loads, dict(call.contract.semantic_index.policy.choices)["preliminary_targets"]))
                available = tuple(t["input_id"] for t in targets if t["availability"] != "unread")
                intent = {"stage": "lead_preliminary_review", "inspection": {
                    t["input_id"]: "inspected" if t["input_id"] == "user_input" else "excluded" for t in targets},
                    "findings": [{"message": "Mina protects her brother.", "evidence_input_ids": finite_fixture_selection(available, ("user_input",))}],
                    "conflicts": [], "missing_information": ["Unread artifacts were not inspected."],
                    "further_review_required": True, "next_action": "request_information"}
                return lead_fixture_response(call, {"response_type": "answer", "response_text": "Mina protects her brother.",
                    "response_details": {"task_scope": lead_fixture_bound_scope(call,
                        lead_fixture_authoring_scope(call, activity="review", domains=("character_world",)), record=True),
                        "preliminary_review": intent}}, family=0)
            script = ExplicitPhaseScript(("{}", preliminary) if repair else (preliminary,), selection_plan("answer.observed_facts"))
            physical_calls, specs, callback_errors = [], [], []
            callback_views, threads, validated, publications = CallbackContextViews(), {}, [], []
            publication_error = RuntimeError("Synthetic post-validation ledger failure")
            caught_publication = None
            callback_provider = SimpleNamespace(callback_payload=callback_views.for_request)
            original_task = accounting_module.AccountingContextProvider.record_validated_task
            original_commit = ContextLedger.commit_attempt

            def record_task(owner, call_id, task_id, task_revision):
                observation = (call_id, task_id, task_revision, tuple(owner.task_revisions))
                if failure == "diagnostic":
                    validated.append(observation)
                    raise RuntimeError("Synthetic validated diagnostic failure")
                original_task(owner, call_id, task_id, task_revision)
                validated.append(observation)

            def commit(ledger, key, *, expected_generation, authorization):
                if failure == "publication" and validated:
                    publications.append(authorization.terminal_call_id)
                    raise publication_error
                return original_commit(ledger, key, expected_generation=expected_generation, authorization=authorization)
            original_invoke = backend_module._BoundContextProvider.invoke_context
            async def observed_invoke(provider, call):
                physical_calls.append(call)
                callback_views.observe(call)
                response = script.next_response(call)
                try:
                    response = response(call, callback_provider) if callable(response) else response
                except Exception as error:
                    callback_errors.append((type(error).__name__, str(error)))
                    raise
                with mock.patch.dict(os.environ, {"STORY_FAKE_CODEX_OUTPUT": response, "STORY_FAKE_CODEX_THREAD_ID": threads.setdefault(call.scope, f"thread-fixture-{len(threads)}")}):
                    os.environ.pop("STORY_FAKE_CODEX_PHYSICAL_OUTPUTS", None)
                    os.environ.pop("STORY_FAKE_CODEX_OUTPUTS", None)
                    return await original_invoke(provider, call)
            native_before = len(self.provider_invocations())
            native_backend = self._provisioned_backend("preliminary_capture")
            runtime = StoryRuntime(self.workspace, native_backend)
            conversation = runtime.conversation_storage_services.catalog.create("preliminary_capture", "First preliminary").conversation_id
            with (mock.patch.object(backend_module._BoundContextProvider, "invoke_context", new=observed_invoke),
                  mock.patch.object(accounting_module.AccountingContextProvider, "record_validated_task", new=record_task),
                  mock.patch.object(ContextLedger, "commit_attempt", new=commit)):
                if seed:
                    from tests import test_specialist_routing_runtime as routing_fixtures
                    pending = script
                    writer = routing_fixtures.SpecialistRoutingRuntimeTests._native_scoped(
                        self, PLOT_ARCHITECT, activity="review", intent="review_only", domains=("plot_structure",))
                    script = ExplicitPhaseScript((writer,), selection_plan("consultation.review_only"))
                    prior = runtime.conversation_storage_services.catalog.create("preliminary_capture", "Prior review").conversation_id
                    proposed = await runtime.lead_turn("preliminary_capture", prior, "완성 원고.\r\nCafé. \r\n")
                    self.assertIs(proposed.kind, c.LeadOutcomeKind.PROPOSAL, (proposed, callback_errors))
                    self.assertEqual(proposed.proposal.authoring_scope.intent.value, "review_only")
                    self.assertEqual(proposed.proposal.assignments[0].review_basis.candidate_input_ids, ("user_input",))
                    script.require_fixture_valid()
                    script = pending
                try:
                    result = await runtime.lead_turn("preliminary_capture", conversation,
                        "Explain the supplied motive: Mina hides a letter to protect her brother. Do not write or dispatch.")
                except RuntimeError as error:
                    if failure != "publication" or error is not publication_error:
                        raise
                    caught_publication = error
            script.require_fixture_valid()
            self.assertIsNone(callback_views.fixture_error_code)
            selection = AgentExchangeSelection("preliminary_capture", AgentExchangeScopeKind.CONVERSATION, conversation, LEAD_ORCHESTRATOR)
            raw = WorkspaceAgentExchangeStore(self.workspace).snapshot(selection).jsonl_bytes
            records = [json.loads(line) for line in io.BytesIO(raw)]
            outcomes = [json.loads(row["content"]) for row in records if row["request_type"] == "outcome"]
            if failure is not None:
                controls = [{"code": "accepted", "outcome": "accepted", "host_diagnostics": {"call_id": call.call_id}}
                            for call in physical_calls if call.call_id != validated[-1][0]]
                self.assertEqual(len(controls), 1)
                self.assertTrue(all(set(item["host_diagnostics"]) == {"call_id"} for item in outcomes))
            if failure == "publication":
                self.assertIs(caught_publication, publication_error)
                self.assertEqual(len(validated), 1)
                self.assertEqual(publications, [validated[0][0]])
                failed = [item for item in outcomes if item["host_diagnostics"]["call_id"] == validated[0][0]]
                self.assertEqual(failed, [{"outcome": "post_response_runtime_failure", "code": "context_runtime_failed",
                                          "host_diagnostics": {"call_id": validated[0][0]}}])
                self.assertEqual(outcomes, controls + failed)
                return
            report = result.preliminary_review
            failure_details = [
                (row["exchange_id"], row["request_type"], row["content"][:4096])
                for row in _agent_exchange_records(self.workspace)
                if row["request_type"] in ("response", "outcome")
            ]
            self.assertIsNotNone(report, (result, [(kind, detail[:512]) for kind, detail in callback_errors[:4]], failure_details[:8]))
            history = runtime.conversation_storage_services.event_store.read("preliminary_capture", conversation)
            judgment = next(e.payload for e in history if type(e.payload) is c.CollaborationLeadJudgmentPayload and e.payload.preliminary_review == report)
            public, = (e.payload.response for e in history if type(e.payload) is c.CollaborationLeadPublicResponsePayload
                       and e.payload.response.invocation_id == judgment.provider_invocation_id and e.payload.response.acceptance.value == "accepted")
            submitted = next(e.payload for e in history if type(e.payload) is c.CollaborationProviderCallPayload
                             and e.payload.state is c.CollaborationProviderCallState.SUBMITTED and e.payload.invocation_id == judgment.provider_invocation_id and e.payload.call_id == public.call_id)
            self.assertEqual(sum(call.call_id == submitted.call_id for call in physical_calls), 1)
            self.assertEqual((public.ordinal, public.call_kind), (submitted.ordinal, submitted.call_kind.value))
            self.assertEqual(validated[-1][:3], (submitted.call_id, report.task_id, report.task_revision))
            self.assertIn((report.task_id, report.task_revision), validated[-1][3])
            if seed:
                self.assertGreater(len(validated[-1][3]), 1)
                self.assertNotEqual((report.task_id, report.task_revision), validated[-1][3][0])
                self.assertGreater(len(threads), 1)
            for native, call in zip(self.provider_invocations()[native_before:], physical_calls, strict=True):
                self.assertEqual(native["raw_payload"].encode(), canonical_provider_json(call.payload))
                self.assertNotIn(report.task_id, json.dumps((native["payload"], native["schema"])))
            if failure == "diagnostic":
                answered = [item for item in outcomes if item["host_diagnostics"]["call_id"] == submitted.call_id]
                self.assertEqual(answered, [{"code": "accepted", "outcome": "accepted",
                                            "host_diagnostics": {"call_id": submitted.call_id}}])
                self.assertEqual(outcomes, controls + answered)
                return
            diagnosed = [json.loads(row["content"]) for row in records if row["request_type"] == "outcome"
                         and "task_id" in json.loads(row["content"]).get("host_diagnostics", {})]
            self.assertEqual(diagnosed, [{"code": "accepted", "outcome": "accepted", "host_diagnostics": {
                "call_id": submitted.call_id, "task_id": report.task_id, "task_revision": report.task_revision}}])
            self.assertEqual({json.loads(row["content"])["host_diagnostics"]["call_id"] for row in records
                              if row["request_type"] == "outcome" and json.loads(row["content"])["outcome"] == "rejected"}, {public.repair_of_call_id} if repair else set())
            selected_calls = {e.payload.call_id for e in history if type(e.payload) is c.CollaborationProviderCallPayload
                              and e.payload.state is c.CollaborationProviderCallState.SUBMITTED}
            self.assertEqual({json.loads(row["content"])["host_diagnostics"]["call_id"] for row in records
                              if row["request_type"] == "request"}, selected_calls)
            self.assertEqual(selected_calls, {call.call_id for call in physical_calls if call.call_id in selected_calls})
            def configured_view(source, spec, host=None, *, terminal=None, transient_only=False):
                self.assertIs(source, raw)
                specs.append(spec)
            with mock.patch.object(jsonl_viewer, "view_jsonl", new=configured_view):
                _prepare_view(raw, session_id="preliminary_capture", scope_id=conversation, agent_id=LEAD_ORCHESTRATOR, typed_history=history)(_transport())
            self.assertEqual(len(specs), 1)
            catalog = bind_catalog(parse_jsonl(raw), specs[0].inspection)
            filters = tuple(frozenset({next(i for i, option in enumerate(f.options) if option.value == report.stage)})
                            if f.id == "phase" else frozenset() for f in catalog.facets)
            found = evaluate(parse_jsonl(raw), catalog, filters, report.task_id, -1)
            self.assertEqual(len(found.items), 1)
            self.assertEqual(found.search.occurrences[0].text, report.task_id)
            outcome_scope = next(i for i, scope in enumerate(catalog.scopes) if scope.id == "outcome")
            scoped = evaluate(parse_jsonl(raw), catalog, filters, report.task_id, outcome_scope)
            self.assertEqual((scoped.items, scoped.matching_records), (found.items, found.matching_records))
            terminal = next(row for row in records if row["request_type"] == "outcome" and json.loads(row["content"]) == diagnosed[0])
            self.assertEqual({i + 1 for i in catalog.items[found.items[0]].records},
                             {i + 1 for i, row in enumerate(records) if row["exchange_id"] == terminal["exchange_id"]})
            from story_writing_agents.interactive_controller._exchange_projection import project_agent_exchanges
            self.assertEqual({call.phase for call in project_agent_exchanges(raw, selection).calls}, {None})
            ambiguous = project_agent_exchanges(raw, selection, history * 2)
            self.assertEqual({call.phase for call in ambiguous.calls}, {None})
            self.assertFalse(evaluate(parse_jsonl(raw), catalog, filters, next(e.event_id for e in history if e.payload is judgment), -1).items)

            if seed:
                from tests.lead_fixtures.preparation_data import (
                    lead_confirmation_decision,
                    synthesis_outcome as _synthesis_outcome,
                )
                from tests.provider_wire_fixture import answer as wire_answer, contract_id as wire_contract_id
                from jsonl_viewer.inspection import MISSING
                task = proposed.proposal.authoring_scope
                self.assertGreaterEqual(proposed.proposal.assignments[0].max_physical_calls, 2)
                self.assertIsNone(proposed.proposal.stage_plan)
                script = ExplicitPhaseScript(("{}", lambda call, _provider: wire_answer(
                    {"assessment": "supports", "message": "The supplied candidate supports the scoped review."},
                    wire_contract_id(call.payload)), _synthesis_outcome()))
                with mock.patch.object(backend_module._BoundContextProvider, "invoke_context", new=observed_invoke):
                    completed = await runtime.decide_lead_step(lead_confirmation_decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
                self.assertIs(completed.turn.state, c.CollaborationStepState.COMPLETED, (completed, callback_errors))
                script.require_fixture_valid()
                history = runtime.conversation_storage_services.event_store.read("preliminary_capture", prior)
                replies = [event.payload for event in history if type(event.payload) is c.CollaborationSpecialistReplyPayload
                           and event.payload.proposal_id == proposed.proposal.proposal_id and event.sender.agent_id == PLOT_ARCHITECT]
                self.assertEqual(len(replies), 1)
                dispatch = next(event.payload for event in history if event.event_id == replies[0].dispatch_event_id)
                self.assertEqual((dispatch.proposal_id, dispatch.proposal_revision, dispatch.proposal_digest),
                                 (proposed.proposal.proposal_id, proposed.proposal.proposal_revision, proposed.proposal.proposal_digest))
                submitted_ids = {event.payload.call_id for event in history if type(event.payload) is c.CollaborationProviderCallPayload
                                 and event.payload.state is c.CollaborationProviderCallState.SUBMITTED
                                 and event.payload.invocation_id == replies[0].provider_invocation_id and event.payload.role == PLOT_ARCHITECT}
                self.assertGreaterEqual(len(submitted_ids), 2)
                for native, call in zip(self.provider_invocations()[native_before:], physical_calls, strict=True):
                    self.assertEqual(native["raw_payload"].encode(), canonical_provider_json(call.payload))
                selection = AgentExchangeSelection("preliminary_capture", AgentExchangeScopeKind.CONVERSATION, prior, PLOT_ARCHITECT)
                raw = WorkspaceAgentExchangeStore(self.workspace).snapshot(selection).jsonl_bytes
                records = [json.loads(line) for line in io.BytesIO(raw)]
                outcomes = [json.loads(row["content"]) for row in records if row["request_type"] == "outcome"]
                diagnosed = [item for item in outcomes if "task_id" in item["host_diagnostics"]]
                self.assertEqual(len(diagnosed), 1)
                call_id = diagnosed[0]["host_diagnostics"]["call_id"]
                self.assertIn(call_id, submitted_ids)
                self.assertEqual(diagnosed, [{"code": "accepted", "outcome": "accepted", "host_diagnostics": {
                    "call_id": call_id, "task_id": task.task_id, "task_revision": task.task_revision}}])
                self.assertEqual({item["host_diagnostics"]["call_id"] for item in outcomes}, submitted_ids)
                self.assertTrue(all(set(item["host_diagnostics"]) == {"call_id"} for item in outcomes if item is not diagnosed[0]))
                specs.clear()
                with mock.patch.object(jsonl_viewer, "view_jsonl", new=configured_view):
                    _prepare_view(raw, session_id="preliminary_capture", scope_id=prior, agent_id=PLOT_ARCHITECT, typed_history=history)(_transport())
                self.assertEqual(len(specs), 1)
                expected_phase = MISSING
                catalog = bind_catalog(parse_jsonl(raw), specs[0].inspection)
                filters = tuple(frozenset({next(index for index, option in enumerate(facet.options) if option.value == expected_phase)})
                                if facet.id == "phase" else frozenset() for facet in catalog.facets)
                found = evaluate(parse_jsonl(raw), catalog, filters, task.task_id, -1)
                self.assertTrue(found.items)
                self.assertEqual(found.search.occurrences[0].text, task.task_id)
                outcome_scope = next(index for index, scope in enumerate(catalog.scopes) if scope.id == "outcome")
                scoped = evaluate(parse_jsonl(raw), catalog, filters, task.task_id, outcome_scope)
                self.assertEqual(len(scoped.items), 1)
                self.assertTrue(set(scoped.items).issubset(found.items))
                self.assertTrue(0 < scoped.matching_records <= found.matching_records)
                accepted_exchange = next(row["exchange_id"] for row in records if row["request_type"] == "outcome"
                                         and json.loads(row["content"])["host_diagnostics"]["call_id"] == call_id)
                self.assertEqual(set(catalog.items[scoped.items[0]].records),
                                 {index for index, row in enumerate(records) if row["exchange_id"] == accepted_exchange})

    async def test_contextual_physical_calls_use_canonical_payload_and_distinct_restartable_thread(self) -> None:
        session_id = "contextual_physical"
        backend = self._session_backend(session_id)
        definition, bound = await self._bind_definition(backend)
        provider = bound._context_provider()
        scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "conversation-context", PLOT_ARCHITECT)
        policy = "sha256:" + "7" * 64
        payload, component_paths = compact_context_payload(_shared_context_payload(), {
            ("task_input",): ContextComponentKind.CURRENT_INPUT,
            ("session_history",): ContextComponentKind.CONVERSATION_CONTEXT, })
        payload.update({"request_correlation_token": "A" * 43, "response_contract": {"workflow_phase": "conversation", "result_contract_id": "text"}})
        schema = {"type": "object", "properties": {"action": {"anyOf": [
            {"type": "string", "enum": ["answer"]}, {"type": "string", "enum": ["clarify"]}]}},
            "required": ["action"], "additionalProperties": False}
        captured = []
        async def invoke(_binding, invocation):
            captured.append(invocation)
            invocation.execution_workspace.require_current()
            return ProviderResult(invocation.thread_id or "contextual-codex-thread", '{"action":"answer"}',
                                  usage=ProviderUsage(0, 2), actual_transport_utf8_bytes=len(invocation.payload_json))
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            async with provider.invocation(scope, policy):
                self.assertIsNone(provider.inspect_context_thread(scope, policy))
                first = await provider.invoke_context(ctx_provider.ContextProviderCallRequest(
                    scope, policy, "context-call-1", ProviderCallKind.COLLABORATION_INITIAL,
                    schema, payload, component_paths))
                first.finalizer.accepted()
                second = await provider.invoke_context(ctx_provider.ContextProviderCallRequest(
                    scope, policy, "context-call-2", ProviderCallKind.COLLABORATION_CONTINUATION,
                    schema, payload, component_paths, expected_thread_id=first.thread_id))
                second.finalizer.accepted()
                self.assertEqual(provider.inspect_context_thread(scope, policy), first.thread_id)
                self.assertEqual(second.thread_id, first.thread_id)
        self.assertEqual(captured[0].payload_json, canonical_provider_json(payload))
        self.assertIsNone(captured[0].thread_id)
        self.assertEqual(captured[1].thread_id, first.thread_id)
        self.assertEqual(first.accounting.usage, ProviderUsage(0, 2))
        self.assertEqual(first.accounting.actual_transport_utf8_bytes, len(captured[0].payload_json))
        self.assertEqual(captured[0].execution_workspace.worktree_root, provision_session_resources(self.workspace.root, session_id).identity.worktree_root)
        with self.assertRaises(SessionArtifactBindingError): captured[0].execution_workspace.require_current()
        exchanges = _agent_exchange_records(self.workspace)
        self.assertEqual(len(exchanges), 6)
        self.assertEqual(sum(record["request_type"] == "request" for record in exchanges), 2)
        schemas = [json.loads(record["content"])["schema"] for record in exchanges
                   if record["request_type"] == "request"]
        self.assertEqual(schemas, [invocation.schema for invocation in captured])
        self.assertEqual(schemas, [schema, schema])
        self.assertEqual(diagnose_json_text('{"action":"answer"}', schemas[0]), ())
        for invalid in ('{}', '{"action":"unknown"}', '{"action":"answer","extra":null}'):
            self.assertTrue(diagnose_json_text(invalid, schemas[0]))
        manifest = self.workspace.read_json(f"agent_states/orchestrator_state/provider_sessions/{session_id}.json")
        self.assertEqual(manifest["provider_threads"], {})
        async with provider.invocation(replace(scope, subject_id="other-conversation"), policy):
            self.assertIsNone(provider.inspect_context_thread(replace(scope, subject_id="other-conversation"), policy))
        backend.close()
        restarted = self._managed_backend()
        restarted.acquire_session_access(session_id)
        restarted_bound = await restarted.bind(definition)
        restarted_provider = restarted_bound._context_provider()
        async with restarted_provider.invocation(scope, policy):
            self.assertEqual(restarted_provider.inspect_context_thread(scope, policy), first.thread_id)
        async with restarted_bound._direct_codex_execution_workspace(session_id, None) as execution_workspace:
            await self._assert_grouped_reader_transport(restarted, session_id, execution_workspace)
        await self._exercise_preliminary_capture(seed=True)

    async def test_contextual_submitted_failure_taints_only_exact_context_thread(
        self,
    ) -> None:
        session_id = "contextual_failure"
        backend, bound = await self._provisioned_bound(session_id)
        provider = bound._context_provider()
        scope = ContextScope(
            "workspace-context",
            session_id,
            ContextSubjectKind.DEBATE,
            "debate-context",
            PLOT_ARCHITECT,
        )
        policy = "sha256:" + "8" * 64
        schema = {"type": "object"}
        payload = {"content": "PRIVATE_CONTEXT_FAILURE"}
        success = ProviderResult("context-before-failure", "{}")
        submitted = ProviderInvocationError(
            "fixed contextual failure",
            taint_existing_thread=True,
        )
        record_provider_failure_accounting(
            submitted,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=13,
        )
        adapter = mock.AsyncMock(side_effect=(success, submitted))
        with mock.patch.object(backend._codex_adapter, "invoke", new=adapter):
            async with provider.invocation(scope, policy):
                first = await provider.invoke_context(
                    ctx_provider.ContextProviderCallRequest(
                        scope,
                        policy,
                        "context-success",
                        ProviderCallKind.DEBATE,
                        schema,
                        payload,
                    )
                )
                with self.assertRaises(BackendProtocolError) as caught:
                    await provider.invoke_context(
                        ctx_provider.ContextProviderCallRequest(
                            scope,
                            policy,
                            "context-failure",
                            ProviderCallKind.DEBATE,
                            schema,
                            payload,
                            expected_thread_id=first.thread_id,
                        )
                    )
                facts = ctx_provider.context_provider_failure_facts(caught.exception)
                self.assertIs(facts.accounting.state, ProviderCallState.SUBMITTED)
                self.assertIs(facts.taint_existing_thread, True)
                self.assertIsNone(provider.inspect_context_thread(scope, policy))
        self.assertEqual(
            self.workspace.read_json(
                f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
            )["provider_threads"],
            {},
        )

    async def test_every_sink_control_failure_is_observational_only(self) -> None:
        session_id = "context_sink_isolation"
        backend, bound = await self._provisioned_bound(session_id)
        provider = mock.AsyncMock(
            return_value=ProviderResult(
                "context-sink-thread",
                direct_reply({"message": "safe reply"}, "conversation"),
                actual_transport_utf8_bytes=19,
            )
        )
        failures: tuple[BaseException, ...] = (
            RuntimeError("PRIVATE_RUNTIME_SINK"),
            asyncio.CancelledError("PRIVATE_CANCELLED_SINK"),
            KeyboardInterrupt("PRIVATE_INTERRUPT_SINK"),
            SystemExit("PRIVATE_EXIT_SINK"),
        )
        sink_calls: list[ProviderCallAccounting] = []

        with mock.patch.object(backend._codex_adapter, "invoke", new=provider):
            for index, failure in enumerate(failures, 1):
                turn_id = f"sink_control_{index}"
                provider.return_value = ProviderResult(
                    "context-sink-thread",
                    direct_reply({"message": "safe reply"}, "conversation"),
                    actual_transport_utf8_bytes=19,
                )

                def failing_sink(
                    accounting: ProviderCallAccounting,
                    *,
                    active_failure: BaseException = failure,
                ) -> None:
                    sink_calls.append(accounting)
                    raise active_failure

                backend._context_accounting_sink = failing_sink
                reply = await bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                )
                self.assertEqual(reply.content, "safe reply")

        self.assertEqual(len(sink_calls), len(failures))
        self.assertEqual(provider.await_count, len(failures))
        self.assertTrue(
            all(record.state is ProviderCallState.SUBMITTED for record in sink_calls)
        )

    async def test_sink_control_failures_do_not_mask_provider_failure(self) -> None:
        session_id = "context_sink_failure_isolation"
        backend, bound = await self._provisioned_bound(session_id)
        failures: tuple[BaseException, ...] = (
            RuntimeError("PRIVATE_RUNTIME_SINK"),
            asyncio.CancelledError("PRIVATE_CANCELLED_SINK"),
            KeyboardInterrupt("PRIVATE_INTERRUPT_SINK"),
            SystemExit("PRIVATE_EXIT_SINK"),
        )
        sink_calls: list[ProviderCallAccounting] = []
        provider = mock.AsyncMock(
            side_effect=ProviderInvocationError(
                "stable provider failure",
                taint_existing_thread=False,
            )
        )

        with mock.patch.object(backend._codex_adapter, "invoke", new=provider):
            for index, failure in enumerate(failures, 1):

                def failing_sink(
                    accounting: ProviderCallAccounting,
                    *,
                    active_failure: BaseException = failure,
                ) -> None:
                    sink_calls.append(accounting)
                    raise active_failure

                backend._context_accounting_sink = failing_sink
                with self.assertRaises(BackendProtocolError) as caught:
                    await bound.converse(
                        self.conversation_request(
                            session_id=session_id,
                            turn_id=f"sink_domain_failure_{index}",
                        )
                    )
                self.assertEqual(str(caught.exception), "stable provider failure")

        self.assertEqual(len(sink_calls), len(failures))
        self.assertEqual(provider.await_count, len(failures))
        self.assertTrue(
            all(record.state is ProviderCallState.PREPARED for record in sink_calls)
        )

    async def test_adapter_selected_bind_records_exact_lifecycle_terminals(
        self,
    ) -> None:
        """Trace the selected adapter for successful and failed vendor binds."""

        _mark_debug_session_attached(self.workspace)
        for vendor in ("codex", "antigravity"):
            for outcome in ("success", "failure"):
                with self.subTest(vendor=vendor, outcome=outcome):
                    backend = AgentBackend(
                        self.workspace,
                        executable=str(self.executable),
                        backend_overrides={PLOT_ARCHITECT: vendor},
                    )
                    self.addCleanup(backend.close)
                    definition = (
                        self.definition()
                        if vendor == "codex"
                        else self.antigravity_definition()
                    )
                    selected = backend._adapters[vendor]
                    other = backend._adapters[
                        "antigravity" if vendor == "codex" else "codex"
                    ]
                    mark = len(_workspace_debug_records(self.workspace))
                    private = f"PRIVATE_{vendor.upper()}_BIND_FAILURE"
                    failure = ValueError(private)
                    selected_patch = (
                        mock.patch.object(selected, "bind", wraps=selected.bind)
                        if outcome == "success"
                        else _fresh_literal_object_side_effect(selected, 'bind', failure)
                    )
                    with (
                        selected_patch as selected_bind,
                        mock.patch.object(
                            other,
                            "bind",
                            wraps=other.bind,
                        ) as other_bind,
                    ):
                        if outcome == "success":
                            bound = await backend.bind(definition)
                            self.assertEqual(bound.vendor, vendor)
                        else:
                            with self.assertRaises(ValueError) as raised:
                                await backend.bind(definition)
                            self.assertIs(raised.exception, failure)

                    selected_bind.assert_called_once_with(definition)
                    other_bind.assert_not_called()
                    records = [
                        record
                        for record in _workspace_debug_records(self.workspace)[mark:]
                        if record["component"] == "backend"
                        and record["operation"] == "backend_lifecycle"
                    ]
                    if outcome == "success":
                        terminal = (
                            1,
                            "trace",
                            "backend",
                            "backend_lifecycle",
                            "succeeded",
                            "ready",
                            None,
                            None,
                            None,
                            PLOT_ARCHITECT,
                            None,
                        )
                    else:
                        terminal = (
                            1,
                            "error",
                            "backend",
                            "backend_lifecycle",
                            "failed",
                            "failed",
                            None,
                            None,
                            None,
                            PLOT_ARCHITECT,
                            "builtins.ValueError",
                        )
                        self.assertNotIn(
                            private,
                            self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                                encoding="utf-8"
                            ),
                        )
                    self.assertEqual(
                        [_debug_signature(record) for record in records],
                        [
                            (
                                1,
                                "trace",
                                "backend",
                                "backend_lifecycle",
                                "started",
                                None,
                                None,
                                None,
                                None,
                                PLOT_ARCHITECT,
                                None,
                            ),
                            terminal,
                        ],
                    )

    test_hot_same_provider_profile_applies_now_and_reuses_thread = (
        ProviderAdapterGenerationCases.test_hot_same_provider_profile_applies_now_and_reuses_thread
    )

    async def test_backend_defaults_snapshot_refresh_and_thread_identity_are_exact(
        self,
    ) -> None:
        session_id = "defaults_snapshot"
        provision_session_resources(self.workspace.root, session_id)
        _mark_debug_session_attached(self.workspace)
        self.write_codex_defaults("persisted-one", "high")
        first_definition = self.definition()
        first = self._session_backend(session_id)
        first_bound = await first.bind(first_definition)

        self.write_codex_defaults("persisted-two", "low")
        await first_bound.invoke(
            self.request(session_id=session_id, debate_id="before_edit")
        )
        await first_bound.invoke(
            self.request(session_id=session_id, debate_id="after_edit")
        )
        first_fingerprint = self.thread(session_id)["fingerprint"]
        first.close()

        second_definition = self.definition()
        self.assertEqual(second_definition, first_definition)
        second = self._managed_backend()
        second.acquire_session_access(session_id)
        await (await second.bind(second_definition)).invoke(
            self.request(session_id=session_id, debate_id="next_backend")
        )

        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 3)
        expected_prefixes = (
            (
                "exec",
                "--model",
                "persisted-one",
                "--config",
                'model_reasoning_effort="high"',
                "--json",
            ),
            (
                "exec",
                "--model",
                "persisted-one",
                "--config",
                'model_reasoning_effort="high"',
                "resume",
                "--json",
            ),
            (
                "exec",
                "--model",
                "persisted-two",
                "--config",
                'model_reasoning_effort="low"',
                "resume",
                "--json",
            ),
        )
        for invocation, expected in zip(
            invocations,
            expected_prefixes,
            strict=True,
        ):
            argv = invocation["argv"]
            start = argv.index("exec")
            self.assertEqual(tuple(argv[start : start + len(expected)]), expected)

        thread = self.thread(session_id)
        self.assertEqual(
            set(
                self.workspace.read_json(
                    "agent_states/orchestrator_state/provider_sessions/"
                    f"{session_id}.json"
                )["provider_threads"]
            ),
            {f"codex:plot_architect:{thread['fingerprint']}"},
        )
        self.assertEqual(thread["fingerprint"], first_fingerprint)
        self.assertEqual(
            thread["thread_id"],
            "thread-defaults_snapshot-plot_architect",
        )

    async def test_configured_provider_rejection_is_typed_stderr_free_and_safe(
        self,
    ) -> None:
        session_id = "configured_rejection"
        model, effort = "future-safe-model", "xhigh"
        self.write_codex_defaults(model, effort)
        _mark_debug_session_attached(self.workspace)
        backend, bound = await self._provisioned_bound(session_id)
        captured_stderr = io.StringIO()

        with (
            mock.patch.dict(os.environ, {"STORY_FAKE_CODEX_MODE": "nonzero"}),
            contextlib.redirect_stderr(captured_stderr),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.invoke(self.request(session_id=session_id))

        self.assertEqual(str(caught.exception), "Codex CLI exited with status 7")
        self.assertEqual(captured_stderr.getvalue(), "")
        self.assertNotIn(model, str(caught.exception))
        self.assertNotIn(effort, str(caught.exception))
        records = _workspace_debug_records(self.workspace)
        profile_records = [
            record
            for record in records
            if record.get("schema_version") == 2
            and record.get("operation") == "execution_profile_applied"
        ]
        self.assertEqual(len(profile_records), len(ROSTER))
        plot_profile = next(
            record["active_profile"]
            for record in profile_records
            if record["agent_id"] == PLOT_ARCHITECT
        )
        self.assertEqual(plot_profile["model"], model)
        self.assertEqual(plot_profile["effort"], effort)
        provider_diagnostics = json.dumps(
            [
                record
                for record in records
                if record.get("operation") == "provider_invocation"
            ],
            ensure_ascii=False,
        )
        self.assertNotIn(model, provider_diagnostics)
        self.assertNotIn(effort, provider_diagnostics)
        argv = self.provider_invocations()[0]["argv"]
        start = argv.index("exec")
        self.assertEqual(
            tuple(argv[start : start + 6]),
            (
                "exec",
                "--model",
                model,
                "--config",
                'model_reasoning_effort="xhigh"',
                "--json",
            ),
        )

    async def test_diagnostic_failure_does_not_replace_provider_failure_or_taint(
        self,
    ) -> None:
        """Preserve the provider error and thread safety when logging fails."""

        session_id = "diagnostic_failure"
        backend, bound = await self._provisioned_bound(session_id)
        captured_stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"STORY_FAKE_CODEX_MODE": "nonzero"}),
            _fresh_literal_object_side_effect(WorkspaceDebugLog, 'error', SystemExit('diagnostic failure')) as debug_error,
            contextlib.redirect_stderr(captured_stderr),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.invoke(self.request(session_id=session_id))

        self.assertEqual(str(caught.exception), "Codex CLI exited with status 7")
        self.assertEqual(captured_stderr.getvalue(), "")
        self.assertTrue(debug_error.called)
        manifest = self.workspace.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
        )
        self.assertEqual(manifest["provider_threads"], {})
        await self._exercise_preliminary_capture(failure="diagnostic")

    async def test_antigravity_public_operations_use_provider_neutral_diagnostics(
        self,
    ) -> None:
        """Trace every public Antigravity operation without provider I/O."""

        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
        )
        self.addCleanup(backend.close)
        bound = await backend.bind(self.antigravity_definition())
        operations = (
            ("invoke", "_invoke_provider", "debate_id"),
            ("converse", "_converse_provider", "conversation_id"),
            (
                "retrieval",
                "_converse_with_retrieval_provider",
                "conversation_id",
            ),
        )
        terminals = (
            ("success", "succeeded", "completed"),
            ("failure", "failed", "failed"),
            ("cancellation", "cancelled", "interrupted"),
        )
        for operation, seam, correlation_field in operations:
            for terminal, phase, outcome in terminals:
                with self.subTest(operation=operation, terminal=terminal):
                    session_id = f"antigravity_{operation}_{terminal}"
                    correlation_id = f"{operation}_{terminal}_correlation"
                    story_marker = f"PRIVATE_{operation.upper()}_{terminal.upper()}"
                    self.create_provisioned_session_with_access(backend, session_id)
                    handler = _RecordingRetrievalHandler()
                    if operation == "invoke":
                        request: object = replace(
                            self.request(
                                session_id=session_id,
                                debate_id=correlation_id,
                            ),
                            user_payload=story_marker,
                        )
                        expected: object = AgentReply.candidate(
                            "r0",
                            "provider result",
                        )
                    elif operation == "converse":
                        request = self.conversation_request(
                            session_id=session_id,
                            conversation_id=correlation_id,
                            content=story_marker,
                        )
                        expected = ConversationReply(
                            session_id,
                            correlation_id,
                            request.turn_id,
                            "provider result",
                        )
                    else:
                        request = self.retrieval_request(
                            session_id=session_id,
                            conversation_id=correlation_id,
                            content=story_marker,
                        )
                        expected = ConversationRetrievalReply(
                            session_id=session_id,
                            conversation_id=correlation_id,
                            turn_id=request.turn_id,
                            content="provider result",
                            sources=(),
                        )

                    failure: BaseException | None
                    if terminal == "failure":
                        failure = BackendProtocolError(
                            f"PRIVATE_{operation.upper()}_PROVIDER_FAILURE"
                        )
                    elif terminal == "cancellation":
                        failure = asyncio.CancelledError(
                            f"PRIVATE_{operation.upper()}_PROVIDER_CANCEL"
                        )
                    else:
                        failure = None
                    provider_call = mock.AsyncMock(
                        return_value=expected,
                        side_effect=failure,
                    )
                    with mock.patch.object(backend, seam, new=provider_call):
                        if failure is None:
                            if operation == "invoke":
                                actual = await bound.invoke(request)  # type: ignore[arg-type]
                            elif operation == "converse":
                                actual = await bound.converse(request)  # type: ignore[arg-type]
                            else:
                                actual = await bound.converse_with_retrieval(
                                    request,  # type: ignore[arg-type]
                                    handler,
                                )
                            self.assertEqual(actual, expected)
                        else:
                            with self.assertRaises(type(failure)) as raised:
                                if operation == "invoke":
                                    await bound.invoke(request)  # type: ignore[arg-type]
                                elif operation == "converse":
                                    await bound.converse(request)  # type: ignore[arg-type]
                                else:
                                    await bound.converse_with_retrieval(
                                        request,  # type: ignore[arg-type]
                                        handler,
                                    )
                            self.assertIs(raised.exception, failure)

                    provider_call.assert_awaited_once()
                    provider_args = provider_call.await_args.args
                    self.assertIs(provider_args[0], bound)
                    self.assertEqual(provider_args[1], request)
                    if operation == "retrieval":
                        self.assertEqual(len(provider_args), 3)
                        self.assertTrue(callable(provider_args[2]))
                    else:
                        self.assertEqual(len(provider_args), 2)
                    self.assertEqual(handler.actions, [])
                    lock = backend._thread_lock(
                        session_id,
                        "antigravity",
                        PLOT_ARCHITECT,
                    )
                    self.assertFalse(lock.locked())
                    records = self.provider_debug_records(
                        session_id=session_id,
                        correlation_field=correlation_field,
                        correlation_id=correlation_id,
                    )
                    self.assert_provider_terminal_records(
                        records,
                        session_id=session_id,
                        correlation_field=correlation_field,
                        correlation_id=correlation_id,
                        terminal_phase=phase,
                        outcome=outcome,
                        exception_type=None if failure is None else type(failure),
                    )
                    raw = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn(story_marker, raw)
                    if failure is not None:
                        self.assertNotIn(str(failure), raw)

    async def test_bound_agent_control_failures_emit_interrupted_terminal_records(
        self,
    ) -> None:
        """Trace both control-flow failures at every bound provider operation."""

        operations = (
            ("invoke", "_invoke_codex", "debate_id"),
            ("converse", "_converse_codex", "conversation_id"),
            (
                "retrieval",
                "_converse_with_retrieval_codex",
                "conversation_id",
            ),
        )
        for ordinal, (operation, seam, correlation_field) in enumerate(
            operations,
            start=1,
        ):
            for exception_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(
                    operation=operation,
                    exception_type=exception_type.__name__,
                ):
                    suffix = exception_type.__name__.lower()
                    session_id = f"control_{operation}_{suffix}"
                    correlation_id = f"correlation_{ordinal}_{suffix}"
                    story_marker = f"PRIVATE_STORY_{operation}_{suffix}"
                    failure_marker = f"PRIVATE_FAILURE_{operation}_{suffix}"
                    failure = exception_type(failure_marker)
                    original_cause = failure.__cause__
                    original_context = failure.__context__
                    backend, bound = await self._provisioned_bound(session_id)
                    handler = _RecordingRetrievalHandler()
                    if operation == "invoke":
                        request: object = replace(
                            self.request(
                                session_id=session_id,
                                debate_id=correlation_id,
                            ),
                            user_payload=story_marker,
                        )
                    elif operation == "converse":
                        request = self.conversation_request(
                            session_id=session_id,
                            conversation_id=correlation_id,
                            content=story_marker,
                        )
                    else:
                        request = self.retrieval_request(
                            session_id=session_id,
                            conversation_id=correlation_id,
                            content=story_marker,
                        )

                    with (
                        mock.patch.object(
                            backend,
                            seam,
                            side_effect=failure,
                        ) as provider_call,
                        self.assertRaises(exception_type) as raised,
                    ):
                        if operation == "invoke":
                            await bound.invoke(request)  # type: ignore[arg-type]
                        elif operation == "converse":
                            await bound.converse(request)  # type: ignore[arg-type]
                        else:
                            await bound.converse_with_retrieval(
                                request,  # type: ignore[arg-type]
                                handler,
                            )

                    self.assertIs(raised.exception, failure)
                    self.assertIs(type(raised.exception), exception_type)
                    self.assertEqual(str(raised.exception), failure_marker)
                    self.assertIs(failure.__cause__, original_cause)
                    self.assertIs(failure.__context__, original_context)
                    provider_call.assert_awaited_once()
                    self.assertEqual(handler.actions, [])
                    lock = backend._thread_lock(
                        session_id,
                        bound.vendor,
                        bound.definition.agent_id,
                    )
                    self.assertFalse(lock.locked())
                    manifest = self.workspace.read_json(
                        "agent_states/orchestrator_state/provider_sessions/"
                        f"{session_id}.json"
                    )
                    self.assertEqual(manifest["provider_threads"], {})
                    records = self.provider_debug_records(
                        session_id=session_id,
                        correlation_field=correlation_field,
                        correlation_id=correlation_id,
                    )
                    self.assert_interrupted_provider_records(
                        records,
                        session_id=session_id,
                        correlation_field=correlation_field,
                        correlation_id=correlation_id,
                        exception_type=exception_type,
                    )
                    raw_debug_log = self.workspace.path(
                        DEBUG_LOG_RELATIVE_PATH
                    ).read_text(encoding="utf-8")
                    for private in (
                        story_marker,
                        failure_marker,
                        str(self.workspace.root),
                        str(bound.staging_directory),
                    ):
                        self.assertNotIn(private, raw_debug_log)

    async def test_retrieval_handler_control_failures_taint_and_trace_interruption(
        self,
    ) -> None:
        """Keep pending retrieval taint while tracing handler interruptions."""

        for ordinal, exception_type in enumerate(
            (KeyboardInterrupt, SystemExit),
            start=1,
        ):
            with self.subTest(exception_type=exception_type.__name__):
                suffix = exception_type.__name__.lower()
                session_id = f"handler_control_{suffix}"
                conversation_id = f"handler_conversation_{ordinal}"
                story_marker = f"PRIVATE_HANDLER_STORY_{suffix}"
                failure_marker = f"PRIVATE_HANDLER_FAILURE_{suffix}"
                failure = exception_type(failure_marker)
                original_cause = failure.__cause__
                original_context = failure.__context__
                backend, bound = await self._provisioned_bound(session_id)
                request = self.retrieval_request(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    content=story_marker,
                )
                search = _needle_search(request)
                handler = _RaisingRetrievalHandler(failure)

                with (
                    mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((search,))},
                    ),
                    self.assertRaises(exception_type) as raised,
                ):
                    await bound.converse_with_retrieval(request, handler)

                self.assertIs(raised.exception, failure)
                self.assertEqual(str(raised.exception), failure_marker)
                self.assertIs(failure.__cause__, original_cause)
                self.assertIs(failure.__context__, original_context)
                self.assertEqual(handler.calls, 1)
                self.assertIs(self.thread(session_id)["tainted"], True)
                self.assertFalse(
                    backend._thread_lock(
                        session_id,
                        bound.vendor,
                        bound.definition.agent_id,
                    ).locked()
                )
                records = self.provider_debug_records(
                    session_id=session_id,
                    correlation_field="conversation_id",
                    correlation_id=conversation_id,
                )
                self.assert_interrupted_provider_records(
                    records,
                    session_id=session_id,
                    correlation_field="conversation_id",
                    correlation_id=conversation_id,
                    exception_type=exception_type,
                )
                raw_debug_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                    encoding="utf-8"
                )
                for private in (
                    story_marker,
                    failure_marker,
                    str(self.workspace.root),
                    str(bound.staging_directory),
                ):
                    self.assertNotIn(private, raw_debug_log)

    async def test_debug_sink_control_failures_preserve_bound_interruptions(
        self,
    ) -> None:
        """Prevent diagnostic control flow from replacing bound-agent failures."""

        cases = (
            ("invoke", KeyboardInterrupt, SystemExit, "debate_id"),
            ("converse", SystemExit, KeyboardInterrupt, "conversation_id"),
            ("retrieval", KeyboardInterrupt, SystemExit, "conversation_id"),
        )
        seams = {
            "invoke": "_invoke_codex",
            "converse": "_converse_codex",
        }
        for ordinal, (
            operation,
            application_type,
            sink_type,
            correlation_field,
        ) in enumerate(cases, start=1):
            with self.subTest(
                operation=operation,
                application_type=application_type.__name__,
                sink_type=sink_type.__name__,
            ):
                session_id = f"sink_control_{operation}"
                correlation_id = f"sink_correlation_{ordinal}"
                story_marker = f"PRIVATE_SINK_STORY_{operation}"
                application_marker = f"PRIVATE_APPLICATION_{operation}"
                sink_marker = f"PRIVATE_DIAGNOSTIC_{operation}"
                application_failure = application_type(application_marker)
                sink_failure = sink_type(sink_marker)
                original_cause = application_failure.__cause__
                original_context = application_failure.__context__
                backend, bound = await self._provisioned_bound(session_id)
                handler: object = _RecordingRetrievalHandler()
                provider_patch: contextlib.AbstractContextManager[object]
                if operation == "invoke":
                    request: object = replace(
                        self.request(
                            session_id=session_id,
                            debate_id=correlation_id,
                        ),
                        user_payload=story_marker,
                    )
                    provider_patch = mock.patch.object(
                        backend,
                        seams[operation],
                        side_effect=application_failure,
                    )
                elif operation == "converse":
                    request = self.conversation_request(
                        session_id=session_id,
                        conversation_id=correlation_id,
                        content=story_marker,
                    )
                    provider_patch = mock.patch.object(
                        backend,
                        seams[operation],
                        side_effect=application_failure,
                    )
                else:
                    request = self.retrieval_request(
                        session_id=session_id,
                        conversation_id=correlation_id,
                        content=story_marker,
                    )
                    search = _needle_search(request)
                    handler = _RaisingRetrievalHandler(application_failure)
                    provider_patch = mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((search,))},
                    )

                with (
                    provider_patch,
                    _fresh_literal_object_side_effect(WorkspaceDebugLog, 'error', sink_failure) as debug_error,
                    self.assertRaises(application_type) as raised,
                ):
                    if operation == "invoke":
                        await bound.invoke(request)  # type: ignore[arg-type]
                    elif operation == "converse":
                        await bound.converse(request)  # type: ignore[arg-type]
                    else:
                        await bound.converse_with_retrieval(
                            request,  # type: ignore[arg-type]
                            handler,  # type: ignore[arg-type]
                        )

                self.assertIs(raised.exception, application_failure)
                self.assertEqual(str(raised.exception), application_marker)
                self.assertIs(application_failure.__cause__, original_cause)
                self.assertIs(application_failure.__context__, original_context)
                debug_error.assert_called_once()
                self.assertIs(debug_error.call_args.args[2], application_failure)
                self.assertEqual(debug_error.call_args.args[0].value, "backend")
                self.assertEqual(
                    debug_error.call_args.args[1].value,
                    "provider_invocation",
                )
                self.assertEqual(
                    debug_error.call_args.kwargs["phase"].value,
                    "cancelled",
                )
                self.assertEqual(
                    debug_error.call_args.kwargs["outcome"].value,
                    "interrupted",
                )
                self.assertEqual(
                    debug_error.call_args.kwargs["session_id"],
                    session_id,
                )
                self.assertEqual(
                    debug_error.call_args.kwargs[correlation_field],
                    correlation_id,
                )
                self.assertEqual(
                    debug_error.call_args.kwargs["agent_id"],
                    PLOT_ARCHITECT,
                )
                records = self.provider_debug_records(
                    session_id=session_id,
                    correlation_field=correlation_field,
                    correlation_id=correlation_id,
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(
                    {
                        key: value
                        for key, value in records[0].items()
                        if key != "timestamp"
                    },
                    {
                        "schema_version": 1,
                        "level": "trace",
                        "component": "backend",
                        "operation": "provider_invocation",
                        "phase": "started",
                        "session_id": session_id,
                        correlation_field: correlation_id,
                        "agent_id": PLOT_ARCHITECT,
                    },
                )
                self.assertFalse(
                    backend._thread_lock(
                        session_id,
                        bound.vendor,
                        bound.definition.agent_id,
                    ).locked()
                )
                if operation == "retrieval":
                    self.assertEqual(handler.calls, 1)  # type: ignore[attr-defined]
                    self.assertIs(self.thread(session_id)["tainted"], True)
                else:
                    manifest = self.workspace.read_json(
                        "agent_states/orchestrator_state/provider_sessions/"
                        f"{session_id}.json"
                    )
                    self.assertEqual(manifest["provider_threads"], {})
                raw_debug_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                    encoding="utf-8"
                )
                for private in (
                    story_marker,
                    application_marker,
                    sink_marker,
                    str(self.workspace.root),
                    str(bound.staging_directory),
                ):
                    self.assertNotIn(private, raw_debug_log)

    async def test_trace_failure_preserves_provider_result_taint_and_cleanup(
        self,
    ) -> None:
        """Keep successful backend lifecycle and provider behavior trace-independent."""

        session_id = "trace_failure"
        with mock.patch.object(
            WorkspaceDebugLog,
            "trace",
            side_effect=SystemExit("diagnostic trace failure"),
        ) as trace:
            backend = self._managed_backend()
            created = self.create_provisioned_session_with_access(backend, session_id)
            bound = await backend.bind(self.definition())
            reply = await bound.invoke(self.request(session_id=session_id))
            backend.close()

        self.assertEqual(created.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(reply.content, "generated candidate")
        self.assertGreater(trace.call_count, 0)
        thread = self.thread(session_id)
        self.assertIs(thread["tainted"], False)
        self.assertFalse(
            self.workspace.path(
                "agent_states/orchestrator_state/provider_sessions/"
                f"{session_id}.access.json"
            ).exists()
        )
        self.assertEqual(backend._access_claim_tokens, {})
        self.assertEqual(backend._staging_directories, set())

    async def test_debate_context_is_the_only_additive_payload_field(self) -> None:
        session_id = "debate_context_session"
        backend, bound = await self._provisioned_bound(session_id)
        from story_writing_agents.models import AgentStateEntry, DebateMessage
        from tests.provider_wire_fixture import assert_guidance
        literal = '{"kind":"action","task":"state","context_ref":"literal"}'
        legacy = replace(self.request(session_id=session_id), messages=(
            DebateMessage(PLOT_ARCHITECT, "draft", 0, "r0", "candidate", literal, "fixture"),
        ), agent_state=(AgentStateEntry(PLOT_ARCHITECT, session_id, "fixture", "audit", 1, "r0", "audit", literal, "fixture"),))
        await bound.invoke(legacy)

        context = _context_window(
            _conversation_pair(
                "prior_turn",
                session_id=session_id,
                conversation_id="selected_conversation",
                user_content="selected question",
                outcome_content="selected answer",
            )
        )
        included = replace(
            legacy,
            debate_id="included_debate",
            conversation_context=context,
        )
        await bound.invoke(included)

        legacy_payload, included_payload = (
            invocation["payload"] for invocation in self.provider_invocations()
        )
        legacy_fields = {
            "protocol_version",
            "contract_guidance",
            "session_id",
            "debate_id",
            "agent_id",
            "consensus_phase",
            "consensus_round_number",
            "candidate_revision_id",
            "task_instructions",
            "user_input_text",
            "candidate_text",
            "debate_history",
            "prior_agent_outcomes",
        }
        self.assertEqual(set(legacy_payload), legacy_fields)
        self.assertEqual(legacy_payload["debate_history"][0], {"sender": PLOT_ARCHITECT, "consensus_phase": "draft_candidate", "consensus_round_number": 0, "candidate_revision_id": "r0", "message_type": "candidate_text", "content_text": literal, "timestamp": "fixture"})
        self.assertEqual(legacy_payload["prior_agent_outcomes"][0], {"agent_id": PLOT_ARCHITECT, "session_id": session_id, "debate_id": "fixture", "consensus_phase": "review_candidate", "consensus_round_number": 1, "candidate_revision_id": "r0", "outcome_type": "candidate_review", "content_text": literal, "timestamp": "fixture"})
        assert_guidance(self, legacy_payload, self.provider_invocations()[0]["schema"])
        self.assertNotIn("conversation_history", legacy_payload)
        self.assertEqual(
            set(included_payload),
            legacy_fields | {"conversation_history"},
        )
        projected = included_payload["conversation_history"]
        self.assertEqual(
            projected,
            [
                {
                    "turn_id": event.turn_id,
                    "sender": event.sender,
                    "recipient": event.recipient,
                    "message_type": event.kind.value,
                    "content_text": event.content,
                }
                for event in context.events
            ],
        )
        self.assertTrue(
            all(
                set(event) == {"turn_id", "sender", "recipient", "message_type", "content_text"}
                for event in projected
            )
        )
        serialized = json.dumps(included_payload, ensure_ascii=False)
        for metadata_name in (
            "total_complete_turns",
            "retained_turns",
            "omitted_turns",
            "retained_utf8_bytes",
            "truncated",
            "selected_conversation",
        ):
            self.assertNotIn(metadata_name, serialized)

    async def test_conversation_request_snapshots_derived_event_content(self) -> None:
        validated = "VALIDATED_CONTEXT_CONTENT"
        unvalidated = "UNVALIDATED_PROVIDER_CONTENT"

        class DynamicEvent(ConversationEvent):
            __slots__ = ("_serve_unvalidated",)

            def __getattribute__(self, name: str):
                if name == "content":
                    try:
                        if object.__getattribute__(self, "_serve_unvalidated"):
                            return unvalidated
                    except AttributeError:
                        pass
                return super().__getattribute__(name)

        source_user, source_reply = _conversation_pair(
            "turn_prior",
            session_id="snapshot_session",
            user_content=validated,
        )
        dynamic_user = DynamicEvent(
            *(getattr(source_user, field.name) for field in fields(ConversationEvent))
        )
        object.__setattr__(dynamic_user, "_serve_unvalidated", False)
        request = self.conversation_request(
            session_id="snapshot_session",
            context=_context_window((dynamic_user, source_reply)),
        )
        self.assertIs(type(request.context), ConversationContextWindow)
        self.assertIs(type(request.context.events), tuple)
        self.assertIs(type(request.context.events[0]), ConversationEvent)
        self.assertEqual(request.context.events[0].content, validated)

        object.__setattr__(dynamic_user, "_serve_unvalidated", True)
        self.assertEqual(dynamic_user.content, unvalidated)
        backend = self._provisioned_backend('snapshot_session')
        await (await backend.bind(self.definition())).converse(request)

        payload = self.provider_invocations()[0]["payload"]
        self.assertEqual(payload["conversation_history"][0]["content_text"], validated)
        self.assertNotIn(unvalidated, json.dumps(payload))

    async def test_conversation_thread_spans_subjects_and_restarts_on_fingerprint(
        self,
    ) -> None:
        backend = self._provisioned_backend('story_session')
        definition, bound = await self._bind_definition(backend)
        context_a = _context_window(
            _conversation_pair(
                "prior_a",
                user_content="CONTEXT_A_USER",
                outcome_content="CONTEXT_A_REPLY",
            )
        )
        context_b = _context_window(
            _conversation_pair(
                "prior_b",
                conversation_id="conversation_b",
                user_content="CONTEXT_B_USER",
                outcome_content="CONTEXT_B_REPLY",
            )
        )
        await bound.converse(self.conversation_request(context=context_a))
        await bound.converse(
            self.conversation_request(
                conversation_id="conversation_b",
                turn_id="turn_b",
                context=context_b,
            )
        )

        incompatible_bound = await backend.bind(replace(definition, fingerprint="0" * 64))
        await incompatible_bound.converse(
            self.conversation_request(
                conversation_id="conversation_c",
                turn_id="turn_c",
            )
        )

        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 3)
        self.assertNotIn("resume", invocations[0]["argv"])
        self.assertIn("resume", invocations[1]["argv"])
        self.assertNotIn("resume", invocations[2]["argv"])
        second_payload = json.dumps(invocations[1]["payload"], ensure_ascii=False)
        self.assertIn("CONTEXT_B_USER", second_payload)
        self.assertNotIn("CONTEXT_A_USER", second_payload)
        execution_workspace = await self.direct_execution_workspace(incompatible_bound, "story_session")
        expected = execution_workspace.continuation_digest("legacy-definition-policy", hashlib.sha256(("agent-provider-wire-v11\0" + incompatible_bound.definition.fingerprint).encode()).hexdigest()).removeprefix("sha256:")
        threads = self.workspace.read_json("agent_states/orchestrator_state/provider_sessions/story_session.json")["provider_threads"]
        self.assertEqual((threads[f"codex:{PLOT_ARCHITECT}:{expected}"]["fingerprint"],
                          self.thread("story_session")["fingerprint"]), (expected, expected))

    async def test_invoke_and_converse_share_one_serialized_thread(self) -> None:
        backend = self._provisioned_backend('mixed_session')
        definition = self.definition()
        debate_bound = await backend.bind(definition)
        conversation_bound = await backend.bind(definition)

        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "delay"},
        ):
            debate_reply, conversation_reply = await asyncio.gather(
                debate_bound.invoke(
                    self.request(
                        session_id="mixed_session",
                        debate_id="mixed_debate",
                    )
                ),
                conversation_bound.converse(
                    self.conversation_request(
                        session_id="mixed_session",
                        conversation_id="mixed_conversation",
                    )
                ),
            )

        self.assertEqual(
            debate_reply,
            _provider_candidate_reply(),
        )
        self.assertEqual(conversation_reply.content, "generated conversation reply")
        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 2)
        self.assertEqual(
            sum("resume" in invocation["argv"] for invocation in invocations),
            1,
        )
        self.assertEqual(
            {"consensus_phase" in invocation["payload"] for invocation in invocations},
            {False, True},
        )

    async def test_thread_persists_across_backend_instances_and_debates(
        self,
    ) -> None:
        definition = self.definition()
        first = self._session_backend('story_session')
        await (await first.bind(definition)).invoke(
            self.request(debate_id="debate_one")
        )
        first.close()

        second = self._managed_backend()
        second.acquire_session_access("story_session")
        await (await second.bind(definition)).invoke(
            self.request(debate_id="debate_two")
        )

        invocations = self.invocations()
        self.assertEqual(len(invocations), 2)
        self.assertNotIn("resume", invocations[0]["argv"])
        self.assertIn("resume", invocations[1]["argv"])
        self.assertIn(
            "thread-story_session-plot_architect",
            invocations[1]["argv"],
        )
        manifest = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/story_session.json"
        )
        self.assertEqual(
            self.thread("story_session")["thread_id"],
            "thread-story_session-plot_architect",
        )

    test_a_to_b_to_a_route_generation_never_resumes_old_a_threads_after_restart = (
        ProviderAdapterGenerationCases.test_a_to_b_to_a_route_generation_never_resumes_old_a_threads_after_restart
    )

    async def test_threads_are_separate_by_agent_and_story_session(self) -> None:
        backend = self._provisioned_backend('session_one')
        self.create_provisioned_session_with_access(backend, "session_two")
        plot = await backend.bind(self.definition(PLOT_ARCHITECT))
        character = await backend.bind(self.definition(CHARACTER_CUSTODIAN))

        await plot.invoke(self.request(session_id="session_one"))
        await character.invoke(
            self.request(
                session_id="session_one",
                agent_id=CHARACTER_CUSTODIAN,
            )
        )
        await plot.invoke(self.request(session_id="session_two"))

        session_one = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/session_one.json"
        )
        session_two = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/session_two.json"
        )
        self.assertEqual(
            {
                f"{thread['backend']}:{thread['agent_id']}:{thread['fingerprint']}"
                for thread in session_one["provider_threads"].values()
            },
            set(session_one["provider_threads"]),
        )
        self.assertEqual(
            {
                f"{thread['backend']}:{thread['agent_id']}:{thread['fingerprint']}"
                for thread in session_two["provider_threads"].values()
            },
            set(session_two["provider_threads"]),
        )
        self.assertNotEqual(
            self.thread("session_one")["thread_id"],
            self.thread("session_two")["thread_id"],
        )

    async def test_separate_bindings_serialize_one_session_agent_thread(
        self,
    ) -> None:
        backend = self._provisioned_backend('shared_session')
        definition = self.definition()
        first = await backend.bind(definition)
        second = await backend.bind(definition)

        await asyncio.gather(
            first.invoke(
                self.request(
                    session_id="shared_session",
                    debate_id="debate_one",
                )
            ),
            second.invoke(
                self.request(
                    session_id="shared_session",
                    debate_id="debate_two",
                )
            ),
        )

        invocations = self.invocations()
        self.assertEqual(len(invocations), 2)
        self.assertEqual(
            sum("resume" in invocation["argv"] for invocation in invocations),
            1,
        )

    async def test_pause_disables_invocation_and_reopen_retains_thread(self) -> None:
        definition = self.definition()
        first = self._session_backend('story_session')
        await (await first.bind(definition)).invoke(self.request())
        first.close()

        control = self._backend_with_fake_codex()
        ended = control.pause_session_and_release_access("story_session")
        self.assertEqual(ended.durable_state, SessionDurableState.PAUSED)
        retained = dict(
            self.workspace.read_json(
                "agent_states/orchestrator_state/provider_sessions/story_session.json"
            )["provider_threads"]
        )
        control.close()

        inactive = self._backend_with_fake_codex()
        bound = await inactive.bind(definition)
        with self.assertRaisesRegex(BackendProtocolError, "paused"):
            await bound.invoke(self.request(debate_id="while_inactive"))
        with self.assertRaisesRegex(BackendProtocolError, "paused"):
            await bound.converse(self.conversation_request())
        restarted = inactive.reopen_session_with_access("story_session")
        self.assertEqual(restarted.durable_state, SessionDurableState.READY)
        self.assertEqual(restarted.access_state, SessionAccessState.HELD_HERE)
        self.assertEqual(
            self.workspace.read_json(
                "agent_states/orchestrator_state/provider_sessions/story_session.json"
            )["provider_threads"],
            retained,
        )
        await bound.invoke(self.request(debate_id="after_restart"))
        inactive.close()

        self.assertIn("resume", self.invocations()[-1]["argv"])

    async def test_resume_only_attachment_reuses_compatible_thread(self) -> None:
        definition = self.definition()
        first = self._session_backend('resume_only_session')
        await (await first.bind(definition)).invoke(
            self.request(session_id="resume_only_session")
        )
        first.pause_session_and_release_access("resume_only_session")
        first.close()

        resumed = self._managed_backend()
        summary = resumed.reopen_session_with_access("resume_only_session")
        self.assertEqual(summary.durable_state, SessionDurableState.READY)
        self.assertEqual(summary.access_state, SessionAccessState.HELD_HERE)
        await (await resumed.bind(definition)).invoke(
            self.request(
                session_id="resume_only_session",
                debate_id="after_resume_only",
            )
        )

        invocation = self.invocations()[-1]
        self.assertIn("resume", invocation["argv"])
        self.assertIn(
            "thread-resume_only_session-plot_architect",
            invocation["argv"],
        )

    test_strict_phase_reply_mapping = BackendReplyDecodingDefinitions.test_strict_phase_reply_mapping

    async def test_rejected_debate_attempts_return_phase_safe_deadlocks(self) -> None:
        backend = self._managed_backend()
        bound = await backend.bind(self.definition())

        for phase in (
            Phase.DRAFT,
            Phase.PROPOSE_REVISION,
            Phase.SYNTHESIZE,
            Phase.FINALIZE,
        ):
            with self.subTest(phase=phase):
                session_id = f"rejected_{phase.value}_session"
                self.create_provisioned_session_with_access(backend, session_id)
                before = len(self.provider_invocations())
                with mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_OUTPUT": "not-json"},
                ):
                    reply = await bound.invoke(
                        self.request(
                            session_id=session_id,
                            debate_id=f"rejected_{phase.value}_debate",
                            phase=phase,
                        )
                    )

                self.assertEqual(len(self.provider_invocations()) - before, 2)
                self.assertIs(reply.kind, AgentReplyKind.DEADLOCK)
                self.assertTrue(reply.deadlocked)
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertIn("after one repair attempt", reply.content)

    async def test_converse_rejects_wrong_types_tampering_and_routing_preflight(
        self,
    ) -> None:
        backend = self._provisioned_backend('routing_session')
        definition, bound = await self._bind_definition(backend)
        manifest_path = (
            "agent_states/orchestrator_state/provider_sessions/routing_session.json"
        )
        manifest_before = self.workspace.read_json(manifest_path)

        with self.assertRaises(TypeError):
            await bound.converse(object())  # type: ignore[arg-type]

        class DerivedRequest(ConversationRequest):
            pass

        ordinary = self.conversation_request(session_id="routing_session")
        derived = DerivedRequest(
            session_id=ordinary.session_id,
            conversation_id=ordinary.conversation_id,
            turn_id=ordinary.turn_id,
            agent_id=ordinary.agent_id,
            content=ordinary.content,
            context=ordinary.context,
        )
        with self.assertRaises(TypeError):
            await bound.converse(derived)

        tampered = self.conversation_request(session_id="routing_session")
        object.__setattr__(tampered, "content", "")
        with self.assertRaises(BackendProtocolError):
            await bound.converse(tampered)

        wrong_agent = self.conversation_request(
            session_id="routing_session",
            agent_id=CHARACTER_CUSTODIAN,
        )
        with self.assertRaisesRegex(BackendProtocolError, "wrong bound agent"):
            await bound.converse(wrong_agent)

        invalid_bound = BoundAgent(
            backend,
            replace(definition, agent_id="unknown_agent"),
            bound.staging_directory,
        )
        with self.assertRaisesRegex(BackendProtocolError, "definition"):
            await invalid_bound.converse(ordinary)

        with self.assertRaises(SessionNotFoundError):
            await bound.converse(
                self.conversation_request(session_id="missing_session")
            )

        self.assertEqual(self.provider_invocations(), [])
        self.assertEqual(self.workspace.read_json(manifest_path), manifest_before)

    async def test_conversation_output_repairs_once_then_returns_safe_fallback(
        self,
    ) -> None:
        session_id = "output_session"
        backend, bound = await self._provisioned_bound(session_id)
        seed_reply = await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="turn_seed")
        )
        self.assertIs(seed_reply.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(seed_reply.trace.action, "reply")
        self.assertIn("current_input", seed_reply.trace.context_sources)
        base: dict[str, object] = {
            "message": "visible reply",
        }

        def changed(**overrides: object) -> str:
            return json.dumps({**base, **overrides})

        cases = {
            "malformed": "not-json",
            "non-object": "[]",
            "duplicate": (
                "{"
                '"message":"visible reply",'
                '"message":"visible reply"}'
            ),
            "extra": changed(extra=True),
            "content type": changed(message=1),
            "empty content": changed(message=""),
            "surrogate content": changed(message="invalid-\ud800"),
            "wrong session": changed(session_id="other_session"),
            "wrong conversation": changed(conversation_id="conversation_b"),
            "wrong turn": changed(turn_id="other_turn"),
        }
        for index, (label, raw) in enumerate(cases.items()):
            with self.subTest(label=label):
                with mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_OUTPUT": raw},
                ):
                    reply = await bound.converse(
                        self.conversation_request(
                            session_id=session_id,
                            turn_id="turn_failed",
                        )
                    )
                extension = label in {"extra", "wrong session", "wrong conversation", "wrong turn"}
                if extension:
                    self.assertEqual(reply.content, "visible reply")
                    self.assertEqual(reply.validation_details, ())
                else:
                    self.assertIn("no action was taken", reply.content)
                self.assertEqual(reply.session_id, session_id)
                self.assertEqual(reply.conversation_id, "conversation_a")
                self.assertEqual(reply.turn_id, "turn_failed")
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.PROVIDER if extension else ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertEqual(reply.trace.action, "reply" if extension else "none")
                self.assertEqual(reply.trace.recovered_fields, () if extension else ("structured_reply",))
                self.assertIs(self.thread(session_id)["tainted"], not extension)
                await bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id=f"turn_recovery_{index}",
                    )
                )

    async def test_completed_rejections_report_each_attempt_and_repaired_success(
        self,
    ) -> None:
        session_id = "validation_attempts"
        backend, bound = await self._provisioned_bound(session_id)
        private = "PRIVATE_VALIDATION_SENTINEL_한🙂"
        responses = [
            json.dumps({"message": 0, private: private}, ensure_ascii=False),
            json.dumps({"message": "\ud800"}),
        ]
        with mock.patch.dict(os.environ, {"STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(responses)}):
            rejected = await bound.converse(self.conversation_request(session_id=session_id))
        self.assertIs(rejected.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertEqual(len(rejected.validation_details), 2)
        original, repair = rejected.validation_details
        self.assertTrue(original.startswith("Original response"))
        self.assertIn("$.response_text: expected string", original)
        self.assertIn("unexpected properties", original)
        self.assertTrue(repair.startswith("Repair response"))
        self.assertEqual(repair.splitlines()[1:], ["$.response_text: expected strict UTF-8 text"])
        self.assertNotIn(private, repr(rejected.validation_details))
        records = [record for record in _agent_exchange_records(self.workspace)
                   if record["session_id"] == session_id]
        groups = {record["exchange_id"]: [] for record in records}
        for record in records:
            groups[record["exchange_id"]].append(record)
        self.assertEqual(len(groups), 2)
        outcomes = []
        for group in groups.values():
            self.assertEqual([record["request_type"] for record in group],
                             ["request", "response", "outcome"])
            outcomes.append(json.loads(group[-1]["content"]))
        for outcome, detail, code in zip(outcomes, (original, repair),
                                         ("response_semantic_rejected", "malformed_structured_response"), strict=True):
            self.assertEqual(outcome, {"code": code, "outcome": "rejected",
                                      "validation_details": ["Structured response\n" + detail.partition("\n")[2]]})
        self.assertNotIn(private, repr(outcomes))

        responses = ["unused", "unused", json.dumps({"message": 0}),
                     json.dumps({"message": "Repaired.", private: private})]
        with mock.patch.dict(os.environ, {"STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(responses)}):
            repaired = await bound.converse(
                self.conversation_request(session_id=session_id, turn_id="repaired_turn")
            )
        self.assertEqual(repaired.content, "Repaired.")
        self.assertIs(repaired.trace.origin, ReplyTraceOrigin.RECOVERED)
        self.assertEqual(len(repaired.validation_details), 1)
        self.assertTrue(repaired.validation_details[0].startswith("Original response"))
        normal = await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="normal_turn")
        )
        self.assertIs(normal.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(normal.validation_details, ())
        await self._exercise_preliminary_capture(repair=True)
        await self._exercise_preliminary_capture(failure="publication")

    async def test_malformed_direct_json_keeps_terminal_code_and_safe_detail(self) -> None:
        session_id = "malformed_terminal_code"
        backend, bound = await self._provisioned_bound(session_id)
        private = "PRIVATE_MALFORMED_PROVIDER_VALUE_한🙂"
        responses = [f'{{"message":"{private}",', '{"message":']
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(responses)},
        ):
            reply = await bound.converse(
                self.conversation_request(session_id=session_id)
            )
        self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertEqual(len(reply.validation_details), 2)
        self.assertTrue(reply.validation_details[0].startswith("Original response"))
        self.assertTrue(reply.validation_details[1].startswith("Repair response"))
        self.assertTrue(all("$: malformed JSON" in item
                            for item in reply.validation_details))
        self.assertNotIn(private, repr(reply.validation_details))
        outcomes = [
            json.loads(record["content"])
            for record in _agent_exchange_records(self.workspace)
            if record["session_id"] == session_id
            and record["request_type"] == "outcome"
        ]
        self.assertEqual(outcomes, [
            {"code": "malformed_structured_response", "outcome": "rejected",
             "validation_details": ["Structured response\n$: malformed JSON"]},
            {"code": "malformed_structured_response", "outcome": "rejected",
             "validation_details": ["Structured response\n$: malformed JSON"]},
        ])

    async def test_structured_decode_failures_taint_existing_thread_once(
        self,
    ) -> None:
        session_id = "decode_taint_session"
        backend = self._provisioned_backend(session_id)
        definition, bound = await self._bind_definition(backend)
        execution_workspace = await self.enterAsyncContext(
            bound._direct_codex_execution_workspace(session_id, None)
        )
        continuation_fingerprint = backend._legacy_continuation_fingerprint(
            bound,
            execution_workspace,
        )
        failures: tuple[tuple[str, BaseException], ...] = (
            (
                "json",
                json.JSONDecodeError("malformed provider response", "x", 0),
            ),
            (
                "unicode",
                UnicodeDecodeError(
                    "utf-8",
                    b"\xff",
                    0,
                    1,
                    "invalid provider response",
                ),
            ),
            (
                "protocol",
                BackendProtocolError(
                    "Codex conversation response has an invalid schema"
                ),
            ),
        )

        for index, (label, failure) in enumerate(failures):
            with self.subTest(failure=label):
                thread_id = f"decode-thread-{index}"
                provider_manifest_thread_store.store_manifest_thread(
                    backend.workspace,
                    session_id,
                    definition,
                    thread_id,
                    tainted=False,
                    fingerprint=continuation_fingerprint,
                    require_owned_access=backend._access_transactions.assert_owned_access,
                )
                invoke = mock.AsyncMock(
                    return_value=ProviderResult(
                        thread_id=thread_id,
                        response_text="provider response",
                    )
                )

                def fail_decode(_text: str) -> ConversationReply:
                    raise failure

                with (
                    mock.patch.object(backend._codex_adapter, "invoke", invoke),
                    _wrapped_thread_taint_patch() as taint,
                    mock.patch.object(
                        provider_session_artifacts,
                        "write_manifest",
                        wraps=provider_session_artifacts.write_manifest,
                    ) as write_manifest,
                    _wrapped_thread_store_patch() as store_thread,
                ):
                    with self.assertRaises(BackendProtocolError) as caught:
                        await self._invoke_empty_contract(
                            backend, bound,
                            session_id=session_id,


                            decode=fail_decode,
                            execution_workspace=execution_workspace,
                        )

                invoke.assert_awaited_once()
                taint.assert_called_once_with(
                    backend.workspace,
                    session_id,
                    definition,
                    fingerprint=continuation_fingerprint,
                )
                write_manifest.assert_called_once()
                store_thread.assert_not_called()
                self.assertIs(self.thread(session_id)["tainted"], True)
                self.assertIsInstance(caught.exception, BackendProtocolError)
                self.assertIs(caught.exception.__cause__, failure)
                if type(failure) is not BackendProtocolError:
                    self.assertEqual(
                        str(caught.exception),
                        "Codex CLI returned malformed structured output",
                    )
                else:
                    self.assertEqual(str(caught.exception), str(failure))

    async def test_provider_invocation_error_preserves_adapter_cause_and_taint(
        self,
    ) -> None:
        session_id = "provider_error_chain_session"
        backend = self._provisioned_backend(session_id)
        definition, bound = await self._bind_definition(backend)
        execution_workspace = await self.direct_execution_workspace(bound, session_id)
        continuation_fingerprint = backend._legacy_continuation_fingerprint(
            bound,
            execution_workspace,
        )
        unchained = ProviderInvocationError(
            "safe unchained provider failure",
            taint_existing_thread=False,
        )
        underlying = OSError("underlying provider failure")
        chained = ProviderInvocationError(
            "safe chained provider failure",
            taint_existing_thread=True,
        )
        chained.__cause__ = underlying
        cases = (
            ("unchained", unchained, None, False),
            ("chained", chained, underlying, True),
        )

        for index, (label, provider_error, expected_cause, should_taint) in enumerate(
            cases
        ):
            with self.subTest(error=label):
                thread_id = f"provider-error-thread-{index}"
                provider_manifest_thread_store.store_manifest_thread(
                    backend.workspace,
                    session_id,
                    definition,
                    thread_id,
                    tainted=False,
                    fingerprint=continuation_fingerprint,
                    require_owned_access=backend._access_transactions.assert_owned_access,
                )
                invoke = mock.AsyncMock(side_effect=provider_error)
                decode = mock.Mock()
                with (
                    mock.patch.object(backend._codex_adapter, "invoke", invoke),
                    _wrapped_thread_taint_patch() as taint,
                    mock.patch.object(
                        provider_session_artifacts,
                        "write_manifest",
                        wraps=provider_session_artifacts.write_manifest,
                    ) as write_manifest,
                    _wrapped_thread_store_patch() as store_thread,
                ):
                    with self.assertRaises(BackendProtocolError) as caught:
                        await self._invoke_empty_contract(
                            backend, bound,
                            session_id=session_id,


                            decode=decode,
                            execution_workspace=execution_workspace,
                        )

                self.assertIs(type(caught.exception), BackendProtocolError)
                self.assertEqual(str(caught.exception), provider_error.diagnostic)
                self.assertIs(caught.exception.__cause__, expected_cause)
                self.assertIsNot(caught.exception.__cause__, provider_error)
                self.assertIsNone(caught.exception.__context__)
                invoke.assert_awaited_once()
                decode.assert_not_called()
                store_thread.assert_not_called()
                self.assertIs(self.thread(session_id)["tainted"], should_taint)
                if should_taint:
                    taint.assert_called_once_with(
                        backend.workspace,
                        session_id,
                        definition,
                        fingerprint=continuation_fingerprint,
                    )
                    write_manifest.assert_called_once()
                else:
                    taint.assert_not_called()
                    write_manifest.assert_not_called()

    async def test_conversation_cancellation_taints_then_restarts_cleanly(
        self,
    ) -> None:
        session_id = "cancel_conversation"
        backend, bound = await self._provisioned_bound(session_id)
        await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="turn_seed")
        )
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ):
            failed = asyncio.create_task(
                bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id="turn_interrupted",
                    )
                )
            )
            await self.wait_for_provider_invocations(2)
            failed.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await failed
        self.assertIs(self.thread(session_id)["tainted"], True)
        await bound.converse(
            self.conversation_request(
                session_id=session_id,
                turn_id="turn_recovery",
            )
        )
        calls = self.provider_invocations()
        self.assertEqual(
            ["resume" in call["argv"] for call in calls],
            [False, True, False],
        )
        self.assertIs(self.thread(session_id)["tainted"], False)
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )

    async def test_conversation_nonzero_exit_uses_fixed_stderr_free_error(
        self,
    ) -> None:
        """Exclude conversation stderr and request values from fixed errors."""

        session_id = "diagnostic_session"
        backend, bound = await self._provisioned_bound(session_id)
        context = _context_window(
            _conversation_pair(
                "turn_prior",
                session_id=session_id,
                user_content="CONTEXT_SECRET\x00TAIL",
                outcome_content="REPLY_SECRET\x1bTAIL",
            )
        )
        request = self.conversation_request(
            session_id=session_id,
            content="CURRENT_SECRET\x1bTAIL",
            context=context,
        )
        captured_stderr = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "STORY_FAKE_CODEX_MODE": "nonzero",
                    "STORY_FAKE_CODEX_LONG_DIAGNOSTIC": "1",
                },
            ),
            contextlib.redirect_stderr(captured_stderr),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.converse(request)

        diagnostic = str(caught.exception)
        self.assertEqual(diagnostic, "Codex CLI exited with status 7")
        self.assertEqual(captured_stderr.getvalue(), "")
        for sensitive in (
            "CURRENT_SECRET",
            "CONTEXT_SECRET",
            "REPLY_SECRET",
            str(bound.staging_directory),
            str(self.workspace.root),
        ):
            self.assertNotIn(sensitive, diagnostic)
        for control in ("\x00", "\x1b", "\x7f"):
            self.assertNotIn(control, diagnostic)

        debug_path = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        raw_debug_log = debug_path.read_text(encoding="utf-8")
        records = [json.loads(line) for line in raw_debug_log.splitlines()]
        failure = next(
            record
            for record in reversed(records)
            if record["component"] == "backend"
            and record["operation"] == "provider_invocation"
            and record["level"] == "error"
        )
        self.assertEqual(
            {
                key: value
                for key, value in failure.items()
                if key not in {"timestamp", "exception"}
            },
            {
                "schema_version": 1,
                "level": "error",
                "component": "backend",
                "operation": "provider_invocation",
                "phase": "failed",
                "session_id": session_id,
                "conversation_id": request.conversation_id,
                "agent_id": self.definition().agent_id,
                "outcome": "failed",
            },
        )
        self.assertEqual(
            set(failure["exception"]),
            {"type", "frames", "frames_truncated"},
        )
        for sensitive in (
            "CURRENT_SECRET",
            "CONTEXT_SECRET",
            "REPLY_SECRET",
            "simulated provider failure",
            str(bound.staging_directory),
            str(self.workspace.root),
        ):
            self.assertNotIn(sensitive, raw_debug_log)

    async def test_invalid_utf8_provider_stderr_is_never_decoded_or_persisted(
        self,
    ) -> None:
        """Drain arbitrary stderr bytes without exposing or decoding them."""

        session_id = "invalid_stderr"
        marker = b"PRIVATE_INVALID_UTF8_STDERR"
        backend, bound = await self._provisioned_bound(session_id)
        captured_stderr = io.StringIO()

        with (
            mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_MODE": "nonzero_invalid_stderr"},
            ),
            mock.patch.object(
                asyncio.get_running_loop(), "slow_callback_duration", float("inf")
            ),
            contextlib.redirect_stderr(captured_stderr),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.converse(self.conversation_request(session_id=session_id))

        self.assertEqual(str(caught.exception), "Codex CLI exited with status 7")
        self.assertEqual(captured_stderr.getvalue(), "")
        raw_debug_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_bytes()
        self.assertNotIn(marker, raw_debug_log)
        self.assertNotIn(b"\xff", raw_debug_log)

    async def test_debate_nonzero_exit_uses_fixed_stderr_free_error(self) -> None:
        """Exclude debate stderr and request values from fixed errors."""

        session_id = "debate_diagnostic_session"
        secret = "DEBATE_SECRET\x00TAIL"
        context_secret = 'CONTEXT_SECRET\\"한🙂\x1bTAIL'
        backend, bound = await self._provisioned_bound(session_id)
        request = replace(
            self.request(session_id=session_id),
            user_payload=secret,
            conversation_context=_context_window(
                _conversation_pair(
                    "secret_turn",
                    session_id=session_id,
                    conversation_id="selected_conversation",
                    user_content=context_secret,
                    outcome_content="context reply secret",
                )
            ),
        )
        captured_stderr = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_MODE": "nonzero"},
            ),
            contextlib.redirect_stderr(captured_stderr),
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await bound.invoke(request)

        diagnostic = str(caught.exception)
        self.assertEqual(diagnostic, "Codex CLI exited with status 7")
        self.assertEqual(captured_stderr.getvalue(), "")
        for sensitive in (
            secret,
            secret.replace("\x00", "?"),
            "DEBATE_SECRET",
            context_secret,
            "CONTEXT_SECRET",
            "context reply secret",
            json.dumps(context_secret, ensure_ascii=False)[1:-1],
            json.dumps(context_secret, ensure_ascii=True)[1:-1],
        ):
            self.assertNotIn(sensitive, diagnostic)
        for control in ("\x00", "\x1b"):
            self.assertNotIn(control, diagnostic)

    async def test_provider_validation_values_fall_back_without_disclosure(self) -> None:
        """Keep provider-controlled validation values out of safe fallbacks."""

        session_id = "fixed_validation_diagnostic_session"
        backend, bound = await self._provisioned_bound(session_id)
        duplicate_key = "PRIVATE_DUPLICATE_KEY\x1bTAIL"
        quoted_duplicate_key = json.dumps(duplicate_key)
        cases = (
            (
                "revision mismatch",
                json.dumps(
                    {
                        "kind": "candidate",
                        "revision_id": "PRIVATE_REVISION\x1bTAIL",
                        "content": "candidate",
                    }
                ),
                "Codex reply revision_id does not match its request",
                "PRIVATE_REVISION",
                Phase.DRAFT,
            ),
            (
                "duplicate conversation key",
                (
                    "{"
                    f'"session_id":"{session_id}",'
                    '"conversation_id":"conversation_a",'
                    '"turn_id":"turn_duplicate","content":"reply",'
                    f'{quoted_duplicate_key}:"one",'
                    f'{quoted_duplicate_key}:"two"}}'
                ),
                "Codex conversation response duplicates a field",
                "PRIVATE_DUPLICATE_KEY",
                None,
            ),
            (
                "duplicate agent field",
                (
                    '{"kind":"candidate","revision_id":"r0",'
                    '"content":"candidate",'
                    f'{quoted_duplicate_key}:"one",'
                    f'{quoted_duplicate_key}:"two"}}'
                ),
                "Codex agent response duplicates a field",
                "PRIVATE_DUPLICATE_KEY",
                Phase.DRAFT,
            ),
            (
                "wrong finalization kind",
                json.dumps(
                    {
                        "kind": "candidate",
                        "revision_id": "r0",
                        "reviewed_proposal_id": "PRIVATE_PROPOSAL\x1bTAIL",
                        "reviewed_proposal_digest": "sha256:" + "a" * 64,
                    }
                ),
                "Codex finalization response has an invalid schema",
                "PRIVATE_PROPOSAL",
                Phase.FINALIZE,
            ),
        )

        for index, (label, raw, _expected, hidden, phase) in enumerate(cases):
            with self.subTest(label=label):
                with mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_OUTPUT": raw},
                ):
                    if phase is None:
                        reply = await bound.converse(
                            self.conversation_request(
                                session_id=session_id,
                                turn_id="turn_duplicate",
                            )
                        )
                    else:
                        reply = await bound.invoke(
                            self.request(
                                session_id=session_id,
                                debate_id=f"fixed_validation_{index}",
                                phase=phase,
                            )
                        )

                self.assertIn("after one repair attempt", reply.content)
                self.assertNotIn(hidden, reply.content)
                self.assertNotIn("\x1b", reply.content)

    async def test_unexpected_communication_failure_stops_process_and_propagates(
        self,
    ) -> None:
        events: list[str] = []
        failure = BrokenPipeError("provider communication failed")
        communication = asyncio.Future()
        communication.set_exception(failure)
        first_wait = asyncio.Future()
        final_wait = asyncio.Future()
        final_wait.set_result(None)
        cleanup_waits: list[asyncio.Future[object]] = []
        timeouts: list[float] = []
        process = mock.Mock(returncode=None)
        process.communicate.side_effect = (
            lambda data: events.append("communicate") or communication
        )
        _configure_process_stop(process, events, first_wait, final_wait)

        async def create_process(*command, **launch_options):
            events.append("launch")
            return process

        async def wait_for(awaitable, *, timeout):
            timeouts.append(timeout)
            if awaitable is communication:
                self.assertEqual(timeout, 5)
                return await awaitable
            cleanup_waits.append(awaitable)
            self.assertEqual(timeout, 2)
            if awaitable is first_wait:
                awaitable.cancel()
                raise TimeoutError
            self.assertIs(awaitable, final_wait)
            raise TimeoutError

        with self.assertRaises(BrokenPipeError) as caught:
            await run_process(
                ProcessRequest(
                    command=("provider",),
                    stdin=b"payload",
                    timeout_seconds=5,
                ),
                create_process=create_process,
                wait_for=wait_for,
            )

        self.assertIs(caught.exception, failure)
        self.assertEqual(
            events,
            ["launch", "communicate", "terminate", "wait", "kill", "wait"],
        )
        self.assertEqual(cleanup_waits, [first_wait, final_wait])
        self.assertEqual(timeouts, [5, 2, 2])
        process.communicate.assert_called_once_with(b"payload")
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    async def test_process_stop_escalates_from_terminate_to_kill(self) -> None:
        events: list[str] = []
        first_wait = asyncio.Future()
        final_wait = asyncio.Future()
        final_wait.set_result(None)
        process = mock.Mock(returncode=None)
        _configure_process_stop(process, events, first_wait, final_wait)

        bounded_waits: list[asyncio.Future[object]] = []
        timeouts: list[float] = []

        async def timeout_then_reap(awaitable, *, timeout):
            bounded_waits.append(awaitable)
            timeouts.append(timeout)
            self.assertEqual(timeout, 2)
            if awaitable is first_wait:
                awaitable.cancel()
                raise TimeoutError
            self.assertIs(awaitable, final_wait)
            return await awaitable

        await stop_process(process, wait_for=timeout_then_reap)

        self.assertEqual(events, ["terminate", "wait", "kill", "wait"])
        self.assertEqual(bounded_waits, [first_wait, final_wait])
        self.assertEqual(timeouts, [2, 2])
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    async def test_process_stop_returns_after_bounded_post_kill_timeout(
        self,
    ) -> None:
        events: list[str] = []
        first_wait = asyncio.Future()
        final_wait = asyncio.Future()
        final_wait.set_result(None)
        process = mock.Mock(returncode=None)
        _configure_process_stop(process, events, first_wait, final_wait)
        bounded_waits: list[asyncio.Future[object]] = []
        timeouts: list[float] = []

        async def time_out(awaitable, *, timeout):
            bounded_waits.append(awaitable)
            timeouts.append(timeout)
            self.assertEqual(timeout, 2)
            if awaitable is first_wait:
                awaitable.cancel()
            raise TimeoutError

        await stop_process(process, wait_for=time_out)

        self.assertEqual(events, ["terminate", "wait", "kill", "wait"])
        self.assertEqual(bounded_waits, [first_wait, final_wait])
        self.assertEqual(timeouts, [2, 2])
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    async def test_unsupported_vendor_fails_during_binding(self) -> None:
        backend = self._managed_backend()
        with self.assertRaises(BackendProtocolError) as caught:
            await backend.bind(
                replace(self.definition(), backend="unsupported-provider")
            )
        self.assertEqual(
            str(caught.exception),
            "definition provider does not match the active execution profile",
        )

    async def test_unsafe_persisted_thread_id_is_never_used_as_an_option(
        self,
    ) -> None:
        with self.assertRaisesRegex(WorkspaceFormatError, "thread ID"):
            provider_thread_records.validate_thread_record(
                {
                    "backend": "codex",
                    "agent_id": PLOT_ARCHITECT,
                    "fingerprint": "f" * 64,
                    "thread_id": "--dangerously-bypass-approvals-and-sandbox",
                    "tainted": False,
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            )

    async def test_closed_backend_rejects_every_reuse_path(self) -> None:
        backend = self._backend_with_fake_codex()
        backend.create_session_with_access("closed_session")
        definition, bound = await self._bind_definition(backend)
        backend.close()

        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await backend.bind(definition)
        lifecycle_calls = (
            lambda: backend.get_session_lifecycle("closed_session"),
            backend.list_session_lifecycles,
            lambda: backend.validate_provider_configuration("closed_session"),
            lambda: backend.acquire_session_access("closed_session"),
            lambda: backend.release_session_access("closed_session"),
            lambda: backend.create_session_with_access("closed_session"),
            lambda: backend.reopen_session_with_access("closed_session"),
            lambda: backend.pause_session_and_release_access("closed_session"),
        )
        for operation in lifecycle_calls:
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(
                    BackendProtocolError,
                    "closed",
                ),
            ):
                operation()
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await bound.invoke(self.request(session_id="closed_session"))
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await bound.converse(self.conversation_request(session_id="closed_session"))

    def test_retrieval_initial_payload_carries_story_and_rag_context_once(
        self,
    ) -> None:
        definition = self.definition()
        story_context = PromptAssembler(self.workspace).build_story_session_context(
            definition=definition,
            session_id="story_session",
            agent_id=PLOT_ARCHITECT,
            focused_context_mode=StoryFocusedContextMode.INCLUDED,
            focused_conversation_id="conversation_a",
            conversation_context=_context_window(),
        )
        rag_context = _provider_rag_context()
        request = replace(
            _retrieval_request(rag_context=rag_context),
            story_session_context=story_context,
        )

        initial = conversation_retrieval_wire.initial_payload(
            request,
            allowed_actions=(ConversationRetrievalActionKind.ANSWER,),
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
                "story_session_context",
                "rag_context",
                "conversation_retrieval_control",
                "protocol_version",
            ),
        )
        from tests.provider_wire_fixture import direct_story_context
        self.assertEqual(initial["story_session_context"], direct_story_context(story_context.to_dict()))
        expected_rag = rag_context.to_dict()
        for excerpt in expected_rag.get("excerpts", []): excerpt["content_text"] = excerpt.pop("content")
        self.assertEqual(initial["rag_context"], expected_rag)
        self.assertNotIn("conversation_history", initial)

        continuation = conversation_retrieval_wire.continuation_payload(
            request,
            action_index=2,
            allowed_actions=(ConversationRetrievalActionKind.ANSWER,),
            observation=serialize_conversation_retrieval_observation(ConversationRetrievalSearchObservation(
                    session_id=request.session_id, conversation_id=request.conversation_id,
                    turn_id=request.turn_id, action_index=1, hits=(), truncated=False)),
        )
        self.assertNotIn("user_input_text", continuation)
        self.assertNotIn("conversation_history", continuation)
        self.assertNotIn("story_session_context", continuation)
        self.assertNotIn("rag_context", continuation)

    async def test_retrieval_exact_initial_continuation_payload_rules_schema_and_frame(
        self,
    ) -> None:
        session_id = "retrieval_payload_session"
        prior_events = _conversation_pair(
            "prior_turn",
            session_id=session_id,
            user_content='prior "question" 한🙂',
            outcome_content="prior answer",
        )
        definition = self.definition()
        context = _context_window(prior_events)
        story_session_context = PromptAssembler(
            self.workspace
        ).build_story_session_context(
            definition=definition,
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
            focused_context_mode=StoryFocusedContextMode.INCLUDED,
            focused_conversation_id="conversation_a",
            conversation_context=context,
        )
        request = self.retrieval_request(
            session_id=session_id,
            context=_readable_context(),
            story_session_context=story_session_context,
        )
        outputs = (
            _needle_search(request),
            _read_first_search_hit(request),
            _retrieval_action_json(
                request,
                kind=ConversationRetrievalActionKind.ANSWER,
                action_index=3,
                content="final answer",
                citation_ids=["s1h1"],
            ),
        )
        backend = self._provisioned_backend(session_id)
        bound = await backend.bind(definition)
        handler = _RecordingRetrievalHandler()
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_OUTPUTS": json.dumps(outputs, ensure_ascii=False)},
        ):
            reply = await bound.converse_with_retrieval(request, handler)

        self.assertEqual(reply.content, "final answer")
        self.assertIs(reply.trace.origin, ReplyTraceOrigin.PROVIDER)
        self.assertEqual(reply.trace.action, "answer")
        self.assertEqual(
            reply.trace.context_sources,
            ("current_input", "story_session", "retrieval_observation"),
        )
        self.assertEqual(len(handler.actions), 3)
        self.assertEqual(
            tuple(type(action) for action in handler.actions),
            (
                ConversationRetrievalSearchAction,
                ConversationRetrievalReadAction,
                ConversationRetrievalAnswerAction,
            ),
        )
        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 3)
        from tests.provider_wire_fixture import assert_guidance, capture_sample, direct_story_context, retrieval_observation
        self.maxDiff = None
        initial = invocations[0]["payload"]
        capture_sample(invocations[0], "direct-conversation")
        self.assertEqual(
            tuple(initial),
            (
                "request_type",
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "user_input_text",
                "story_session_context",
                "conversation_retrieval_control",
                "protocol_version", "contract_guidance",
            ),
        )
        self.assertEqual(initial["request_type"], "answer_with_conversation_history")
        self.assertEqual(
            initial["story_session_context"],
            direct_story_context(story_session_context.to_dict()),
        )
        self.assertEqual(
            initial["story_session_context"]["focused_context"]["events"],
            [
                {
                    "turn_id": event.turn_id,
                    "sequence_number": event.sequence,
                    "sender": event.sender,
                    "recipient": event.recipient,
                    "message_type": event.kind.value,
                    "content_text": event.content,
                }
                for event in prior_events
            ],
        )
        self.assertNotIn("conversation_retrieval_observation", initial)
        controls = [invocation["payload"]["conversation_retrieval_control"] for invocation in invocations]
        self.assertEqual([control["retrieval_action_number"] for control in controls], [1, 2, 3])
        self.assertEqual(
            [control["allowed_action_types"] for control in controls],
            [
                ["search_conversation_history", "submit_result"],
                ["search_conversation_history", "read_conversation_history", "submit_result"],
                ["read_conversation_history", "submit_result"],
            ],
        )
        for control in controls:
            self.assertEqual(
                tuple(control),
                ("retrieval_action_number", "allowed_action_types", "retrieval_limits", "retrieval_instructions"),
            )
            self.assertEqual(control["retrieval_limits"], CONVERSATION_RETRIEVAL_POLICY.to_dict())
            self.assertIn("citation_id", " ".join(control["retrieval_instructions"]))
            self.assertIn("untrusted", " ".join(control["retrieval_instructions"]))

        for index, invocation in enumerate(invocations):
            payload = invocation["payload"]
            self.assertEqual(
                invocation["raw_payload"],
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
            if index:
                self.assertEqual(
                    tuple(payload),
                    (
                        "request_type",
                        "session_id",
                        "conversation_id",
                        "turn_id",
                        "agent_id",
                        "conversation_retrieval_control",
                        "conversation_retrieval_observation",
                        "protocol_version", "contract_guidance",
                    ),
                )
                self.assertNotIn("user_input_text", payload)
                self.assertNotIn("context", payload)
                self.assertNotIn("story_session_context", payload)
                actual_frame = payload["conversation_retrieval_observation"].split("\n")
                expected_frame = retrieval_observation(serialize_conversation_retrieval_observation(handler.outcomes[index - 1])).split("\n")
                self.assertEqual((actual_frame[0], actual_frame[-1]), (expected_frame[0], expected_frame[-1]))
                self.assertEqual(json.loads(actual_frame[1]), json.loads(expected_frame[1]))

        for invocation, control in zip(invocations, controls):
            schema = invocation["schema"]
            self.assertEqual(check_schema(schema), ())
            self.assertEqual(schema["required"], ["agent_action"])
            self.assertIs(schema["additionalProperties"], False)
            branches = schema["properties"]["agent_action"]["anyOf"]
            self.assertEqual(list(dict.fromkeys(branch["properties"]["action_type"]["enum"][0] for branch in branches)), control["allowed_action_types"])
            for branch in branches:
                self.assertEqual(set(branch["required"]), set(branch["properties"]))
                self.assertIs(branch["additionalProperties"], False)
            assert_guidance(self, invocation["payload"], schema)
        read_branches = [branch for branch in invocations[-1]["schema"]["properties"]["agent_action"]["anyOf"] if branch["properties"]["action_type"]["enum"] == ["read_conversation_history"]]
        self.assertEqual({frozenset(branch["required"]) for branch in read_branches}, {frozenset({"action_type", "citation_id", "start_sequence_number"}), frozenset({"action_type", "citation_id", "start_sequence_number", "end_sequence_number"})})
        self.assertEqual(
            conversation_retrieval_wire.allowed_actions(
                action_index=5,
                has_observation=True,
                after_read=False,
            ),
            (ConversationRetrievalActionKind.ANSWER,),
        )

    async def test_retrieval_repairs_share_five_call_budget_and_close_fifth_to_answer(
        self,
    ) -> None:
        request = self.retrieval_request()
        search_one = _retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            action_index=1,
            query="Needle one",
        )
        search_two = _retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            action_index=2,
            query="Needle two",
        )
        answer = _retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.ANSWER,
            action_index=3,
            content="bounded answer",
            citation_ids=[],
        )
        rejected_fifth = _retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            action_index=3,
            query="forbidden sixth call",
        )

        for label, fifth_output, expected_origin in (
            ("accepted_answer", answer, ReplyTraceOrigin.RECOVERED),
            ("rejected_search", rejected_fifth, ReplyTraceOrigin.RUNTIME_FALLBACK),
        ):
            with self.subTest(label=label):
                self.log_path.unlink(missing_ok=True)
                session_id = f"retrieval_physical_budget_{label}"
                backend, bound = await self._provisioned_bound(session_id)
                handler = _RecordingRetrievalHandler()
                outputs = (
                    search_one,
                    "not-json",
                    search_two,
                    "not-json",
                    fifth_output,
                )
                with mock.patch.dict(
                    os.environ,
                    {
                        "STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(outputs),
                    },
                ):
                    reply = await bound.converse_with_retrieval(
                        self.retrieval_request(session_id=session_id),
                        handler,
                    )

                calls = self.provider_invocations()
                self.assertEqual(len(calls), 5)
                self.assertEqual(
                    [call["payload"]["conversation_retrieval_control"]["retrieval_action_number"] for call in calls],
                    [1, 2, 2, 3, 3],
                )
                fifth = calls[4]
                self.assertEqual(
                    fifth["payload"]["conversation_retrieval_control"]["allowed_action_types"],
                    ["submit_result"],
                )
                self.assertIn("structured_response_repair", fifth["payload"])
                fifth_branches = fifth["schema"]["properties"]["agent_action"]["anyOf"]
                self.assertEqual(len(fifth_branches), 1)
                self.assertEqual(
                    fifth_branches[0]["properties"]["action_type"]["enum"],
                    ["submit_result"],
                )
                self.assertEqual(len(handler.actions), 3)
                self.assertIs(
                    type(handler.actions[-1]),
                    ConversationRetrievalAnswerAction,
                )
                self.assertIs(reply.trace.origin, expected_origin)
                if label == "accepted_answer":
                    self.assertEqual(reply.content, "bounded answer")
                else:
                    self.assertIn("after one repair attempt", reply.content)
                    self.assertNotIn("forbidden sixth call", reply.content)

    async def test_repaired_search_and_read_diagnostics_reach_clean_final_answer(
        self,
    ) -> None:
        session_id = "retrieval_repaired_actions"
        request = self.retrieval_request(session_id=session_id)
        private = "PRIVATE_RETRIEVAL_ACTION_VALUE_한🙂"
        outputs = (
            f'{{"private":"{private}",',
            _needle_search(request),
            f'{{"private":"{private}",',
            _read_first_search_hit(request),
            _retrieval_action_json(
                request,
                kind=ConversationRetrievalActionKind.ANSWER,
                action_index=3,
                content="Clean final answer.",
                citation_ids=["s1h1"],
            ),
        )
        backend, bound = await self._provisioned_bound(session_id)
        handler = _RecordingRetrievalHandler()
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(outputs, ensure_ascii=False)},
        ):
            reply = await bound.converse_with_retrieval(request, handler)

        self.assertEqual(reply.content, "Clean final answer.")
        self.assertEqual(len(self.provider_invocations()), 5)
        self.assertEqual(
            tuple(type(action) for action in handler.actions),
            (ConversationRetrievalSearchAction, ConversationRetrievalReadAction,
             ConversationRetrievalAnswerAction),
        )
        self.assertTrue(all(
            len(action.validation_details) == 1
            and action.validation_details[0].startswith("Original response")
            and "$: malformed JSON" in action.validation_details[0]
            for action in handler.actions[:2]
        ))
        self.assertEqual(handler.actions[2].validation_details, ())
        self.assertEqual(
            reply.validation_details,
            (*handler.actions[0].validation_details,
             *handler.actions[1].validation_details),
        )
        self.assertNotIn(private, repr(reply.validation_details))

    test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors = BackendReplyDecodingDefinitions.test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors

    test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence = BackendReplyDecodingDefinitions.test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence

    async def test_retrieval_handler_declaration_and_exact_outcome_protocol(self) -> None:
        session_id = "retrieval_handler_session"
        backend, bound = await self._provisioned_bound(session_id)
        request = self.retrieval_request(session_id=session_id)
        class AsyncHandler:
            """Declare the forbidden asynchronous handler method."""

            async def handle_conversation_retrieval_action(
                self,
                _action: ConversationRetrievalAction,
            ) -> ConversationRetrievalReply:
                """Return an unreachable direct reply."""

                raise AssertionError
        with self.assertRaisesRegex(TypeError, "must be synchronous"):
            await bound.converse_with_retrieval(request, AsyncHandler())
        self.assertEqual(self.provider_invocations(), [])
        search = _needle_search(request)
        class WrongKindHandler:
            """Return a reply where the search requires an observation."""

            def __init__(self) -> None:
                self.calls = 0

            def handle_conversation_retrieval_action(
                self,
                action: ConversationRetrievalAction,
            ) -> ConversationRetrievalReply:
                """Return one exact but action-incompatible reply."""

                self.calls += 1
                return ConversationRetrievalReply(
                    session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    content="wrong kind",
                    sources=(),
                )
        wrong = WrongKindHandler()
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((search,))},
        ):
            with self.assertRaisesRegex(BackendProtocolError, "invalid observation"):
                await bound.converse_with_retrieval(request, wrong)
        self.assertEqual(wrong.calls, 1)
        self.assertIs(self.thread(session_id)["tainted"], True)
        class CorrelationHandler:
            """Return an exact search observation with changed correlation."""

            def __init__(self) -> None:
                self.calls = 0

            def handle_conversation_retrieval_action(
                self,
                action: ConversationRetrievalAction,
            ) -> ConversationRetrievalObservation:
                """Change only the selected conversation identity."""

                self.calls += 1
                assert type(action) is ConversationRetrievalSearchAction
                return ConversationRetrievalSearchObservation(
                    session_id=action.session_id,
                    conversation_id="foreign_conversation",
                    turn_id=action.turn_id,
                    action_index=action.action_index,
                    hits=(),
                    truncated=False,
                )
        for invalid_handler, message in (
            (CorrelationHandler(), "correlation changed"),
        ):
            with self.subTest(handler=type(invalid_handler).__name__):
                with mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((search,))},
                ):
                    with self.assertRaisesRegex(BackendProtocolError, message):
                        await bound.converse_with_retrieval(
                            request,
                            invalid_handler,
                        )
                self.assertEqual(invalid_handler.calls, 1)
                self.assertIs(self.thread(session_id)["tainted"], True)

    async def test_retrieval_whole_loop_serializes_with_legacy_converse_and_invoke(
        self,
    ) -> None:
        session_id = "retrieval_lock_session"
        backend = self._provisioned_backend(session_id)
        definition = self.definition()
        retrieval_bound = await backend.bind(definition)
        conversation_bound = await backend.bind(definition)
        debate_bound = await backend.bind(definition)
        entered = asyncio.Event()
        release = asyncio.Event()
        trace: list[str] = []

        async def retrieval_call(
            _bound: BoundAgent,
            request: ConversationRetrievalRequest,
            _handler: object,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> ConversationRetrievalReply:
            """Hold the retrieval operation inside the shared thread lock."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("retrieval_enter")
            entered.set()
            await release.wait()
            trace.append("retrieval_exit")
            return ConversationRetrievalReply(
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                content="retrieval",
                sources=(),
            )

        async def conversation_call(
            _bound: BoundAgent,
            request: ConversationRequest,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> ConversationReply:
            """Record legacy conversation entry after lock acquisition."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("conversation_enter")
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                "conversation",
            )

        async def invoke_call(
            _bound: BoundAgent,
            request: AgentRequest,
            *,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> AgentReply:
            """Record debate entry after lock acquisition."""

            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            trace.append("invoke_enter")
            return AgentReply.candidate(request.revision_id, "candidate")

        handler = _RecordingRetrievalHandler()
        with (
            _fresh_literal_object_side_effect(backend, '_converse_with_retrieval_codex', retrieval_call),
            _fresh_literal_object_side_effect(backend, '_converse_codex', conversation_call),
            _fresh_literal_object_side_effect(backend, '_invoke_codex', invoke_call),
        ):
            retrieval_task = asyncio.create_task(
                retrieval_bound.converse_with_retrieval(
                    self.retrieval_request(session_id=session_id),
                    handler,
                )
            )
            await entered.wait()
            conversation_task = asyncio.create_task(
                conversation_bound.converse(
                    self.conversation_request(session_id=session_id)
                )
            )
            invoke_task = asyncio.create_task(
                debate_bound.invoke(self.request(session_id=session_id))
            )
            await asyncio.sleep(0)
            self.assertEqual(trace, ["retrieval_enter"])
            release.set()
            await asyncio.gather(retrieval_task, conversation_task, invoke_task)

        self.assertEqual(trace[0:2], ["retrieval_enter", "retrieval_exit"])
        self.assertEqual(set(trace[2:]), {"conversation_enter", "invoke_enter"})

    async def test_pending_search_taints_for_each_failure_and_restarts_fresh(
        self,
    ) -> None:
        failures: tuple[BaseException, ...] = (
            ConversationRetrievalSemanticError(),
            ConversationRetrievalBudgetError(),
            OSError("memory failed"),
            ConversationRetrievalObserverError(),
            TimeoutError("provider timed out"),
            asyncio.CancelledError(),
        )
        for ordinal, failure in enumerate(failures, start=1):
            session_id = f"retrieval_taint_{ordinal}"
            backend, bound = await self._provisioned_bound(session_id)
            request = self.retrieval_request(session_id=session_id)
            search = _needle_search(request)
            handler = _RaisingRetrievalHandler(failure)
            with mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((search,))},
            ):
                if isinstance(failure, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await bound.converse_with_retrieval(request, handler)
                else:
                    with self.assertRaises(type(failure)):
                        await bound.converse_with_retrieval(request, handler)
            self.assertEqual(handler.calls, 1)
            self.assertIs(self.thread(session_id)["tainted"], True)

            if ordinal == 1:
                answer = _retrieval_action_json(
                    request,
                    kind=ConversationRetrievalActionKind.ANSWER,
                    action_index=1,
                    content="recovered",
                    citation_ids=[],
                )
                with mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_OUTPUTS": json.dumps((answer,))},
                ):
                    reply = await bound.converse_with_retrieval(
                        request,
                        _RecordingRetrievalHandler(),
                    )
                self.assertEqual(reply.content, "recovered")
                self.assertNotIn("resume", self.provider_invocations()[-1]["argv"])
                self.assertIs(self.thread(session_id)["tainted"], False)

    recording_retrieval_handler = _RecordingRetrievalHandler

    test_retirement_defers_exact_cleanup_for_each_bound_operation = BindingCallLifetimeCases.test_retirement_defers_exact_cleanup_for_each_bound_operation

    test_failure_and_cancellation_release_retired_staging_once = BindingCallLifetimeCases.test_failure_and_cancellation_release_retired_staging_once

    test_close_waits_for_active_binding_then_cleans_every_resource_once = BindingCallLifetimeCases.test_close_waits_for_active_binding_then_cleans_every_resource_once

    test_terminal_backend_rejects_call_before_binding_retirement = BindingCallLifetimeCases.test_terminal_backend_rejects_call_before_binding_retirement

    async def test_post_staging_validation_failure_discards_provisional_bytes(
        self,
    ) -> None:
        for failure in (RuntimeError("validation failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                backend = self._managed_backend()
                definition = self.definition()
                reference_count = len(definition.file_references())
                original_read = PromptFileReference.read_text
                read_count = 0

                def read(reference: PromptFileReference) -> str:
                    nonlocal read_count
                    read_count += 1
                    if read_count == (2 * reference_count) + 1:
                        raise failure
                    return original_read(reference)

                staged: list[Path] = []
                original_stage = backend._codex_adapter.stage_definition

                def stage(value: object) -> Path:
                    directory = original_stage(value)  # type: ignore[arg-type]
                    staged.append(directory)
                    return directory

                with (
                    mock.patch.object(
                        PromptFileReference,
                        "read_text",
                        autospec=True,
                        side_effect=read,
                    ),
                    mock.patch.object(
                        backend._codex_adapter,
                        "stage_definition",
                        side_effect=stage,
                    ),
                    self.assertRaises(type(failure)),
                ):
                    await backend.bind(definition)

                self.assertEqual(len(staged), 1)
                self.assertFalse(staged[0].exists())
                self.assertEqual(backend._staging_directories, set())
                self.assertEqual(backend._bound_agents, set())

    async def test_reference_read_invoke_isolated_for_all_roles_and_rounds(
        self,
    ) -> None:
        """Keep the runtime-local authorized context call-local."""

        backend = self._managed_backend()
        for index, agent_id in enumerate(ROSTER, 1):
            session_id = f"reference_role_{index}"
            operation_id = f"role_operation_{index}"
            self.create_provisioned_session_with_access(backend, session_id)
            bound = await backend.bind(self.definition(agent_id))
            request = replace(
                self.request(
                    session_id=session_id,
                    debate_id=f"role_debate_{index}",
                    agent_id=agent_id,
                ),
                round_number=index,
            )
            repository, authorization, invocation = self.reference_invocation(
                operation_id=operation_id,
                invocation_id=f"role_invocation_{index}",
                agent_id=agent_id,
                phase=request.phase.value,
                round_number=request.round_number,
            )
            capability_id = invocation._capability.capability_id
            before = len(self.provider_invocations())
            with mock.patch.dict(
                os.environ,
                self.reference_environment(invocation),
            ):
                reply = await bound.invoke(request, reference_read=invocation)
            self.assertEqual(
                reply,
                _provider_candidate_reply(role=agent_id, context_sources=("current_input", "writing_artifacts")),
            )

            calls = self.provider_invocations()[before:]
            self.assertEqual(len(calls), 1)
            substantive = calls[0]
            expected_payload = provider_direct_call_wire.phase_request_payload(request)
            expected_payload["untrusted_attached_references"] = [
                {
                    "artifact_reference": {
                        "artifact_path": "manuscript/part-01/provider-reference.md",
                        "line_range": None,
                    },
                    "content_text": "REFERENCE-PROVIDER-MARKER\n",
                }
            ]
            self.assertTrue(substantive["payload"].pop("contract_guidance")["input_fields"]["$.untrusted_attached_references"])
            self.assertEqual(substantive["payload"], expected_payload)
            self.assertEqual(
                ["resume" in call["argv"] for call in calls],
                [False],
            )
            serialized_calls = json.dumps(calls, ensure_ascii=False)
            self.assertNotIn(capability_id, serialized_calls)
            self.assertEqual(serialized_calls.count("REFERENCE-PROVIDER-MARKER"), 2)
            self.assert_reference_accepted(
                repository,
                authorization,
                operation_id,
            )
            exchange_records = [
                record
                for record in _agent_exchange_records(self.workspace)
                if record["session_id"] == session_id
            ]
            self.assertEqual(
                [record["request_type"] for record in exchange_records],
                ["request", "response", "outcome"],
            )
            self.assertEqual(
                [
                    json.loads(record["content"])["call_kind"]
                    for record in exchange_records
                    if record["request_type"] == "request"
                ],
                ["debate"],
            )
            self.assertEqual(
                [
                    json.loads(record["content"])
                    for record in exchange_records
                    if record["request_type"] == "outcome"
                ],
                [{"code": "accepted", "outcome": "accepted"}],
            )

    async def test_reference_read_converse_uses_one_call_and_one_context(
        self,
    ) -> None:
        session_id = "reference_converse"
        operation_id = "conversation_operation"
        backend, bound = await self._provisioned_bound(session_id)
        request = self.conversation_request(session_id=session_id)
        repository, authorization, invocation = self.reference_invocation(
            operation_id=operation_id,
            invocation_id="conversation_invocation",
            phase="conversation",
            round_number=None,
            references=self.reference_cases()["range"],
        )
        with mock.patch.dict(
            os.environ,
            self.reference_environment(invocation),
        ):
            reply = await bound.converse(request, reference_read=invocation)
        self.assertEqual(reply.content, "generated conversation reply")
        calls = self.provider_invocations()
        self.assertEqual(len(calls), 1)
        expected = provider_direct_call_wire.conversation_request_payload(request)
        context = calls[0]["payload"].pop("untrusted_attached_references")
        self.assertTrue(calls[0]["payload"].pop("contract_guidance")["input_fields"]["$.untrusted_attached_references"])
        self.assertEqual(calls[0]["payload"], expected)
        self.assertEqual(
            context,
            [
                {
                    "artifact_reference": {
                        "artifact_path": "manuscript/part-01/provider-range.md",
                        "line_range": {"start_line": 2, "end_line": 2},
                    },
                    "content_text": "REFERENCE-PROVIDER-RANGE-MARKER\n",
                }
            ],
        )
        self.assert_reference_accepted(repository, authorization, operation_id)

    async def test_reference_runtime_fallback_terminates_and_repair_accepts(
        self,
    ) -> None:
        backend = self._managed_backend()
        bound = await backend.bind(self.definition())

        for route in ("debate", "conversation", "retrieval"):
            for outcome in ("fallback", "repaired"):
                with self.subTest(route=route, outcome=outcome):
                    self.log_path.unlink(missing_ok=True)
                    session_id = f"reference_{route}_{outcome}"
                    operation_id = f"reference_operation_{route}_{outcome}"
                    self.create_provisioned_session_with_access(backend, session_id)
                    repository, authorization, invocation = self.reference_invocation(
                        operation_id=operation_id,
                        invocation_id=f"reference_invocation_{route}_{outcome}",
                        phase=(
                            "draft"
                            if route == "debate"
                            else (
                                "conversation"
                                if route == "conversation"
                                else "conversation_retrieval"
                            )
                        ),
                        round_number=(0 if route == "debate" else None),
                    )
                    if route == "debate":
                        accepted_output = json.dumps({"message": "repaired candidate"})
                    elif route == "conversation":
                        accepted_output = json.dumps(
                            {"message": "repaired conversation"}
                        )
                    else:
                        accepted_output = _retrieval_action_json(
                            self.retrieval_request(session_id=session_id),
                            kind=ConversationRetrievalActionKind.ANSWER,
                            action_index=1,
                            content="repaired retrieval",
                            citation_ids=[],
                        )
                    outputs = (
                        "not-json",
                        "not-json" if outcome == "fallback" else accepted_output,
                    )
                    with mock.patch.dict(
                        os.environ,
                        {
                            "STORY_FAKE_CODEX_PHYSICAL_OUTPUTS": json.dumps(outputs),
                        },
                    ):
                        if route == "debate":
                            reply = await bound.invoke(
                                self.request(session_id=session_id),
                                reference_read=invocation,
                            )
                        elif route == "conversation":
                            reply = await bound.converse(
                                self.conversation_request(session_id=session_id),
                                reference_read=invocation,
                            )
                        else:
                            reply = await bound.converse_with_retrieval(
                                self.retrieval_request(session_id=session_id),
                                _RecordingRetrievalHandler(),
                                reference_read=invocation,
                            )

                    self.assertEqual(len(self.provider_invocations()), 2)
                    self.assertIs(
                        reply.trace.origin,
                        (
                            ReplyTraceOrigin.RUNTIME_FALLBACK
                            if outcome == "fallback"
                            else ReplyTraceOrigin.RECOVERED
                        ),
                    )
                    if outcome == "repaired":
                        self.assert_reference_accepted(
                            repository,
                            authorization,
                            operation_id,
                        )
                        continue

                    history = repository.inspect_reference_operation(operation_id)
                    assert history is not None
                    invocation_events = history.invocation_events[0]
                    self.assertEqual(
                        tuple(event.invocation_state for event in invocation_events),
                        (
                            ReferenceInvocationState.AUTHORIZED,
                            ReferenceInvocationState.DELIVERED,
                            ReferenceInvocationState.TERMINATED,
                        ),
                    )
                    self.assertTrue(
                        {
                            ReferenceInvocationState.RESULT_RECEIVED,
                            ReferenceInvocationState.ACCEPTED,
                        }.isdisjoint(
                            event.invocation_state for event in invocation_events
                        )
                    )
                    self.assertIs(
                        invocation_events[-1].outcome,
                        ReferenceAuthorizationErrorCode.PROVIDER_FAILURE,
                    )

    async def test_local_reference_handler_failures_are_durable(self) -> None:
        cases = (
            (
                "authorization",
                ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.CANCELLED),
                ReferenceAuthorizationErrorCode.CANCELLED,
            ),
            ("runtime", RuntimeError("local reference failure"),
             ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE),
        )
        for label, failure, expected in cases:
            with self.subTest(label=label):
                self.log_path.unlink(missing_ok=True)
                session_id = f"reference_handler_{label}"
                operation_id = f"reference_handler_operation_{label}"
                backend, bound = await self._provisioned_bound(session_id)
                repository, authorization, invocation = self.reference_invocation(
                    operation_id=operation_id,
                    invocation_id=f"reference_handler_invocation_{label}",
                )
                with (
                    mock.patch.object(
                        type(invocation),
                        "handle_reference_read_action",
                        side_effect=failure,
                    ),
                    self.assertRaises(type(failure)) as caught,
                ):
                    await bound.invoke(
                        self.request(session_id=session_id),
                        reference_read=invocation,
                    )
                self.assertIs(caught.exception, failure)
                self.assertEqual(self.provider_invocations(), [])
                history = repository.inspect_reference_operation(operation_id)
                assert history is not None
                terminal = history.invocation_events[0][-1]
                self.assertIs(terminal.invocation_state, ReferenceInvocationState.TERMINATED)
                self.assertIs(terminal.outcome, expected)
                authorization.close()

    async def test_reference_read_retrieval_preserves_action_indices_and_budgets(
        self,
    ) -> None:
        cases = (("direct", 1), ("search_read", 3), ("maximum", 5))
        for case, action_count in cases:
            with self.subTest(case=case):
                self.log_path.unlink(missing_ok=True)
                session_id = f"reference_retrieval_{case}"
                operation_id = f"retrieval_operation_{case}"
                backend, bound = await self._provisioned_bound(session_id)
                request = self.retrieval_request(session_id=session_id)
                repository, authorization, invocation = self.reference_invocation(
                    operation_id=operation_id,
                    invocation_id=f"retrieval_invocation_{case}",
                    phase="conversation_retrieval",
                    round_number=None,
                    references=self.reference_cases()["mixed"],
                )
                if case == "search_read":
                    outputs = [
                        _retrieval_action_json(
                            request,
                            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
                            action_index=1,
                            query="Needle 1",
                        ),
                        _read_first_search_hit(request),
                    ]
                else:
                    outputs = [
                        _retrieval_action_json(
                            request,
                            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
                            action_index=index,
                            query=f"Needle {index}",
                        )
                        for index in range(1, action_count)
                    ]
                outputs.append(
                    _retrieval_action_json(
                        request,
                        kind=ConversationRetrievalActionKind.ANSWER,
                        action_index=action_count,
                        content=f"{case} answer",
                        citation_ids=([] if case == "direct" else ["s1h1"]),
                    )
                )
                handler = _RecordingRetrievalHandler()
                environment = {
                    **self.reference_environment(invocation),
                    "STORY_FAKE_CODEX_OUTPUTS": json.dumps(outputs),
                }
                with mock.patch.dict(os.environ, environment):
                    reply = await bound.converse_with_retrieval(
                        request,
                        handler,
                        reference_read=invocation,
                    )
                self.assertEqual(reply.content, f"{case} answer")
                calls = self.provider_invocations()
                self.assertEqual(len(calls), action_count)
                retrieval_calls = calls
                self.assertEqual(
                    [
                        call["payload"]["conversation_retrieval_control"]["retrieval_action_number"]
                        for call in retrieval_calls
                    ],
                    list(range(1, action_count + 1)),
                )
                self.assertIn(
                    "untrusted_attached_references",
                    retrieval_calls[0]["payload"],
                )
                self.assertEqual(
                    retrieval_calls[0]["payload"]["untrusted_attached_references"],
                    [
                        {
                            "artifact_reference": {
                                "artifact_path": ("manuscript/part-01/provider-reference.md"),
                                "line_range": None,
                            },
                            "content_text": "REFERENCE-PROVIDER-MARKER\n",
                        },
                        {
                            "artifact_reference": {
                                "artifact_path": "manuscript/part-01/provider-range.md",
                                "line_range": {"start_line": 2, "end_line": 2},
                            },
                            "content_text": "REFERENCE-PROVIDER-RANGE-MARKER\n",
                        },
                    ],
                )
                self.assertTrue(
                    all(
                        "untrusted_attached_references" not in call["payload"]
                        for call in retrieval_calls[1:]
                    )
                )
                self.assertEqual(
                    json.dumps(calls, ensure_ascii=False).count(
                        "REFERENCE-PROVIDER-MARKER"
                    ),
                    2,
                )
                self.assertEqual(
                    json.dumps(calls, ensure_ascii=False).count(
                        "REFERENCE-PROVIDER-RANGE-MARKER"
                    ),
                    2,
                )
                self.assert_reference_accepted(
                    repository,
                    authorization,
                    operation_id,
                )

    async def test_local_reference_drift_stops_before_provider(self) -> None:
        session_id = "reference_failure_drift"
        operation_id = "failure_operation_drift"
        backend, bound = await self._provisioned_bound(session_id)
        repository, authorization, invocation = self.reference_invocation(
            operation_id=operation_id,
            invocation_id="failure_invocation_drift",
            references=self.reference_cases()["mixed"],
        )
        self.workspace.root.joinpath(
            "manuscript/part-01/provider-reference.md"
        ).write_text("DRIFTED", encoding="utf-8")
        with self.assertRaises(ReferenceAuthorizationError) as raised:
            await bound.invoke(
                self.request(session_id=session_id),
                reference_read=invocation,
            )
        self.assertIs(raised.exception.code, ReferenceAuthorizationErrorCode.DRIFT)
        self.assertEqual(self.provider_invocations(), [])
        history = repository.inspect_reference_operation(operation_id)
        assert history is not None
        terminal = history.invocation_events[0][-1]
        self.assertIs(terminal.invocation_state, ReferenceInvocationState.TERMINATED)
        self.assertIs(terminal.outcome, ReferenceAuthorizationErrorCode.DRIFT)
        authorization.close()

    async def test_reference_read_substantive_failure_and_cancellation_terminate(
        self,
    ) -> None:
        cases = (
            ("provider_failure", ReferenceAuthorizationErrorCode.PROVIDER_FAILURE),
            ("cancelled", ReferenceAuthorizationErrorCode.CANCELLED),
        )
        for case, outcome in cases:
            with self.subTest(case=case):
                self.log_path.unlink(missing_ok=True)
                session_id = f"reference_terminal_{case}"
                operation_id = f"terminal_operation_{case}"
                backend, bound = await self._provisioned_bound(session_id)
                repository, authorization, invocation = self.reference_invocation(
                    operation_id=operation_id,
                    invocation_id=f"terminal_invocation_{case}",
                )
                environment = self.reference_environment(invocation)
                if case == "provider_failure":
                    environment["STORY_FAKE_CODEX_FAIL_SUBSTANTIVE"] = "1"
                    with (
                        mock.patch.dict(os.environ, environment),
                        self.assertRaises(BackendProtocolError) as raised,
                    ):
                        await bound.invoke(
                            self.request(session_id=session_id),
                            reference_read=invocation,
                        )
                    self.assertEqual(
                        str(raised.exception),
                        "Codex CLI exited with status 7",
                    )
                    self.assertNotIn(
                        "REFERENCE-PROVIDER-MARKER",
                        str(raised.exception),
                    )
                else:
                    environment["STORY_FAKE_CODEX_SLEEP_SUBSTANTIVE"] = "1"
                    with mock.patch.dict(os.environ, environment):
                        task = asyncio.create_task(
                            bound.invoke(
                                self.request(session_id=session_id),
                                reference_read=invocation,
                            )
                        )
                        await self.wait_for_provider_invocations(1)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                self.assertEqual(len(self.provider_invocations()), 1)
                history = repository.inspect_reference_operation(operation_id)
                assert history is not None
                terminal = history.invocation_events[0][-1]
                self.assertIs(
                    terminal.invocation_state, ReferenceInvocationState.TERMINATED
                )
                self.assertIs(terminal.outcome, outcome)
                self.assertEqual(
                    self.workspace.read_json(
                        "agent_states/orchestrator_state/provider_sessions/"
                        f"{session_id}.json"
                    )["provider_threads"],
                    {},
                )
                authorization.close()

    async def test_reference_read_wrong_correlation_and_no_reference_are_pre_provider(
        self,
    ) -> None:
        session_id = "reference_pre_provider"
        backend, bound = await self._provisioned_bound(session_id)
        _, authorization, wrong = self.reference_invocation(
            operation_id="wrong_correlation",
            invocation_id="wrong_correlation_invocation",
            agent_id=CHARACTER_CUSTODIAN,
        )
        with self.assertRaises(ReferenceAuthorizationError) as raised:
            await bound.invoke(
                self.request(session_id=session_id),
                reference_read=wrong,
            )
        self.assertIs(
            raised.exception.code,
            ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
        )
        self.assertEqual(self.provider_invocations(), [])
        authorization.close()

        with mock.patch.object(
            backend,
            "_load_authorized_reference_context",
            side_effect=AssertionError("no-reference call entered the prelude"),
        ) as prelude:
            reply = await bound.invoke(self.request(session_id=session_id))
        self.assertEqual(
            reply,
            _provider_candidate_reply(),
        )
        prelude.assert_not_called()
        self.assertEqual(len(self.provider_invocations()), 1)

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

    async def test_debate_and_conversation_rejections_repair_then_fall_back_and_capture(
        self,
    ) -> None:
        backend = self._managed_backend()
        definition, bound = await self._bind_definition(backend)

        debate_session = "capture_debate_session"
        debate_id = "capture_debate"
        self.create_provisioned_session_with_access(backend, debate_session)
        await bound.invoke(
            self.request(
                session_id=debate_session,
                debate_id="capture_seed",
            )
        )
        seeded_thread = self.thread(debate_session)["thread_id"]
        debate_body = direct_reply({"verdict": "conflict", "message": ""}, "audit")
        with mock.patch.dict(
            os.environ,
            {
                "STORY_FAKE_CODEX_OUTPUT": debate_body,
                "STORY_PRIVATE_CREDENTIAL": "PRIVATE_ENV_CREDENTIAL",
            },
        ):
            debate_reply = await bound.invoke(
                self.request(
                    session_id=debate_session,
                    debate_id=debate_id,
                    revision_id="expected-revision",
                    phase=Phase.AUDIT,
                )
            )
        self.assertEqual(debate_reply.revision_id, "expected-revision")
        self.assertIn("after one repair attempt", debate_reply.content)
        self.assertIs(debate_reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
        self.assertEqual(debate_reply.trace.action, "none")
        self.assertEqual(len(debate_reply.validation_details), 2)
        self.assertTrue(all(chunk.splitlines()[1:] == ["$.response_text: expected nonempty text"]
                            for chunk in debate_reply.validation_details))
        self.assertTrue(debate_reply.validation_details[0].startswith("Original response"))
        self.assertTrue(debate_reply.validation_details[1].startswith("Repair response"))
        outcomes = [json.loads(record["content"]) for record in _agent_exchange_records(self.workspace)
                    if record["session_id"] == debate_session and record["request_type"] == "outcome"]
        self.assertEqual([item["code"] for item in outcomes],
                         ["accepted", "response_semantic_rejected", "response_semantic_rejected"])
        self.assertEqual([item["validation_details"] for item in outcomes[1:]],
                         [[detail.replace(stage, "Structured response", 1)] for stage, detail in
                          zip(("Original response", "Repair response"), debate_reply.validation_details, strict=True)])
        self.assertIs(self.thread(debate_session)["tainted"], True)
        self.assertEqual(self.thread(debate_session)["thread_id"], seeded_thread)

        await bound.invoke(
            self.request(
                session_id=debate_session,
                debate_id="capture_recovery",
                revision_id="recovery-revision",
            )
        )
        recovery_thread = self.thread(debate_session)
        self.assertIs(recovery_thread["tainted"], False)
        self.assertIsNone(self.provider_invocations()[-1]["payload"].get("resume"))
        self.assertNotIn("resume", self.provider_invocations()[-1]["argv"])

        conversation_session = "capture_conversation_session"
        conversation_id = "capture_conversation"
        conversation_body = "PRIVATE_MALFORMED_CONVERSATION_한🙂"
        self.create_provisioned_session_with_access(backend, conversation_session)
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_OUTPUT": conversation_body},
        ):
            conversation_reply = await bound.converse(
                self.conversation_request(
                    session_id=conversation_session,
                    conversation_id=conversation_id,
                )
            )
        self.assertIn("after one repair attempt", conversation_reply.content)
        self.assertIs(
            conversation_reply.trace.origin,
            ReplyTraceOrigin.RUNTIME_FALLBACK,
        )
        self.assertEqual(len(conversation_reply.validation_details), 2)
        self.assertIn("malformed JSON", "\n".join(conversation_reply.validation_details))
        self.assertNotIn(conversation_body, repr(conversation_reply.validation_details))

        records = _provider_error_records(self.workspace)
        self.assertEqual(len(records), 4)
        debate_record = next(
            record for record in records if record["body"] == debate_body
        )
        conversation_record = next(
            record for record in records if record["body"] == conversation_body
        )
        expected_common = {
            "schema_version": 1,
            "provider": "codex",
            "agent_id": PLOT_ARCHITECT,
            "failure_category": PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
            "truncated": False,
        }
        for record, body, session_id, correlation in (
            (debate_record, debate_body, debate_session, ("debate_id", debate_id)),
            (
                conversation_record,
                conversation_body,
                conversation_session,
                ("conversation_id", conversation_id),
            ),
        ):
            with self.subTest(correlation=correlation[0]):
                encoded = body.encode("utf-8")
                self.assertEqual(
                    {key: record[key] for key in expected_common},
                    expected_common,
                )
                self.assertEqual(record["session_id"], session_id)
                self.assertEqual(record[correlation[0]], correlation[1])
                self.assertNotIn(
                    "conversation_id" if correlation[0] == "debate_id" else "debate_id",
                    record,
                )
                self.assertEqual(record["response_utf8_bytes"], len(encoded))
                self.assertEqual(record["retained_utf8_bytes"], len(encoded))
                self.assertEqual(
                    record["response_sha256"], hashlib.sha256(encoded).hexdigest()
                )

        runtime_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
            encoding="utf-8"
        )
        for private_value in (
            debate_body,
            conversation_body,
            "PRIVATE_REJECTED_RESPONSE_한🙂",
            "dynamic user input",
            "PRIVATE_ENV_CREDENTIAL",
        ):
            self.assertNotIn(private_value, runtime_log)

    async def test_retrieval_and_substantive_rejections_fall_back_with_correlation(
        self,
    ) -> None:
        backend = self._managed_backend()
        bound = await backend.bind(self.definition())

        retrieval_session = "capture_retrieval_session"
        retrieval_id = "capture_retrieval_conversation"
        retrieval_body = "PRIVATE_RETRIEVAL_MALFORMED"
        self.create_provisioned_session_with_access(backend, retrieval_session)
        retrieval_handler = _RecordingRetrievalHandler()
        with mock.patch.dict(
            os.environ,
            {
                "STORY_FAKE_CODEX_OUTPUTS": json.dumps(
                    [retrieval_body, retrieval_body]
                ),
            },
        ):
            retrieval_reply = await bound.converse_with_retrieval(
                self.retrieval_request(
                    session_id=retrieval_session,
                    conversation_id=retrieval_id,
                ),
                retrieval_handler,
            )
        self.assertIn("after one repair attempt", retrieval_reply.content)
        self.assertIs(
            retrieval_reply.trace.origin,
            ReplyTraceOrigin.RUNTIME_FALLBACK,
        )
        self.assertEqual(retrieval_reply.trace.action, "none")
        self.assertEqual(len(retrieval_handler.actions[0].validation_details), 2)
        self.assertEqual(retrieval_reply.validation_details,
                         retrieval_handler.actions[0].validation_details)
        self.assertNotIn(retrieval_body, repr(retrieval_handler.actions[0].validation_details))

        prelude_session = "capture_reference_prelude_session"
        prelude_debate = "capture_reference_prelude_debate"
        prelude_body = "PRIVATE_REFERENCE_PRELUDE_MALFORMED"
        self.create_provisioned_session_with_access(backend, prelude_session)
        _, _, prelude = self.reference_invocation(
            operation_id="capture_prelude_operation",
            invocation_id="capture_prelude_invocation",
        )
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_REFERENCE_ACTION": prelude_body},
        ):
            await bound.invoke(
                self.request(
                    session_id=prelude_session,
                    debate_id=prelude_debate,
                ),
                reference_read=prelude,
            )

        substantive_session = "capture_reference_substantive_session"
        substantive_conversation = "capture_reference_substantive_conversation"
        substantive_body = "PRIVATE_REFERENCE_SUBSTANTIVE_MALFORMED"
        self.create_provisioned_session_with_access(backend, substantive_session)
        _, _, substantive = self.reference_invocation(
            operation_id="capture_substantive_operation",
            invocation_id="capture_substantive_invocation",
            phase="conversation",
            round_number=None,
        )
        with mock.patch.dict(
            os.environ,
            {
                **self.reference_environment(substantive),
                "STORY_FAKE_CODEX_OUTPUT": substantive_body,
            },
        ):
            substantive_reply = await bound.converse(
                self.conversation_request(
                    session_id=substantive_session,
                    conversation_id=substantive_conversation,
                ),
                reference_read=substantive,
            )
        self.assertIn("after one repair attempt", substantive_reply.content)
        self.assertEqual(len(substantive_reply.validation_details), 2)
        self.assertNotIn(substantive_body, repr(substantive_reply.validation_details))

        records = {
            record["body"]: record for record in _provider_error_records(self.workspace)
        }
        self.assertEqual(set(records), {retrieval_body, substantive_body})
        self.assertEqual(records[retrieval_body]["conversation_id"], retrieval_id)
        self.assertNotIn("debate_id", records[retrieval_body])
        self.assertNotIn(prelude_body, records)
        self.assertEqual(
            records[substantive_body]["conversation_id"],
            substantive_conversation,
        )
        self.assertNotIn("debate_id", records[substantive_body])

    async def test_artifact_failure_is_fail_open_and_preserves_decode_exception(
        self,
    ) -> None:
        backend = self._managed_backend()
        definition, bound = await self._bind_definition(backend)
        unicode_session = "capture_unicode_success"
        unicode_conversation = "capture_unicode_conversation"
        unicode_body = "PRIVATE_UNICODE_REJECTED_RESPONSE_한🙂"
        unicode_failure = UnicodeDecodeError(
            "utf-8", b"\xff", 0, 1, "invalid completed response"
        )
        self.create_provisioned_session_with_access(backend, unicode_session)
        unicode_workspace = await self.direct_execution_workspace(
            bound,
            unicode_session,
        )

        def fail_unicode(_response: str) -> ConversationReply:
            raise unicode_failure

        with (
            mock.patch.object(
                backend._codex_adapter,
                "invoke",
                mock.AsyncMock(
                    return_value=ProviderResult(
                        thread_id="unicode-rejected-thread",
                        response_text=unicode_body,
                    )
                ),
            ),
            self.assertRaises(BackendProtocolError) as unicode_caught,
        ):
            await self._invoke_empty_contract(
                backend, bound,
                session_id=unicode_session,
                conversation_id=unicode_conversation,


                decode=fail_unicode,
                execution_workspace=unicode_workspace,
            )
        self.assertIs(unicode_caught.exception.__cause__, unicode_failure)
        self.assertEqual(
            [
                {
                    "provider": record["provider"],
                    "session_id": record["session_id"],
                    "conversation_id": record["conversation_id"],
                    "body": record["body"],
                }
                for record in _provider_error_records(self.workspace)
            ],
            [
                {
                    "provider": "codex",
                    "session_id": unicode_session,
                    "conversation_id": unicode_conversation,
                    "body": unicode_body,
                }
            ],
        )
        storage_failures = (OSError, KeyboardInterrupt, SystemExit, BaseException)

        for decode_kind in ("protocol", "json", "unicode"):
            for storage_type in storage_failures:
                with self.subTest(decode=decode_kind, storage=storage_type.__name__):
                    suffix = f"{decode_kind}_{storage_type.__name__.lower()}"
                    session_id = f"capture_failure_{suffix}"
                    debate_id = f"capture_debate_{suffix}"
                    self.create_provisioned_session_with_access(backend, session_id)
                    execution_workspace = await self.direct_execution_workspace(
                        bound,
                        session_id,
                    )
                    continuation_fingerprint = (
                        backend._legacy_continuation_fingerprint(
                            bound,
                            execution_workspace,
                        )
                    )
                    stored_thread = f"stored-{suffix}"
                    rejected_thread = f"rejected-{suffix}"
                    provider_manifest_thread_store.store_manifest_thread(
                        backend.workspace,
                        session_id,
                        definition,
                        stored_thread,
                        tainted=False,
                        fingerprint=continuation_fingerprint,
                        require_owned_access=backend._access_transactions.assert_owned_access,
                    )
                    prior_cause = RuntimeError(f"prior-cause-{suffix}")
                    if decode_kind == "protocol":
                        decode_failure: BaseException = BackendProtocolError(
                            f"fixed protocol failure {suffix}"
                        )
                        decode_failure.__cause__ = prior_cause
                    elif decode_kind == "json":
                        decode_failure = json.JSONDecodeError(
                            f"malformed {suffix}", "x", 0
                        )
                    else:
                        decode_failure = UnicodeDecodeError(
                            "utf-8", b"\xff", 0, 1, f"invalid {suffix}"
                        )
                    decode_traceback = None
                    decode_frame = None
                    decode_lasti = None

                    def fail_decode(_response: str) -> AgentReply:
                        nonlocal decode_frame, decode_lasti, decode_traceback
                        try:
                            raise decode_failure
                        except BaseException as raised:
                            decode_traceback = raised.__traceback__
                            assert decode_traceback is not None
                            decode_frame = decode_traceback.tb_frame
                            decode_lasti = decode_traceback.tb_lasti
                            raise

                    invoke = mock.AsyncMock(
                        return_value=ProviderResult(
                            thread_id=rejected_thread,
                            response_text=f"PRIVATE_RESPONSE_{suffix}",
                        )
                    )
                    storage_failure = storage_type(f"PRIVATE_STORAGE_{suffix}")
                    caught_exception = None
                    with (
                        mock.patch.object(backend._codex_adapter, "invoke", invoke),
                        mock.patch.object(
                            WorkspaceProviderErrorArtifacts,
                            "record_rejected_response",
                            side_effect=storage_failure,
                        ) as record,
                        _wrapped_thread_taint_patch() as taint,
                        _wrapped_thread_store_patch() as store,
                    ):
                        try:
                            await self._invoke_empty_contract(
                                backend, bound,
                                session_id=session_id,
                                debate_id=debate_id,


                                decode=fail_decode,
                                execution_workspace=execution_workspace,
                            )
                        except BackendProtocolError as raised:
                            caught_exception = raised
                            caught_traceback = raised.__traceback__
                        else:
                            self.fail("decode failure did not propagate")

                    record.assert_called_once_with(
                        provider="codex",
                        agent_id=PLOT_ARCHITECT,
                        session_id=session_id,
                        debate_id=debate_id,
                        conversation_id=None,
                        response_text=f"PRIVATE_RESPONSE_{suffix}",
                        failure_category=PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
                    )
                    taint.assert_called_once_with(
                        backend.workspace,
                        session_id,
                        definition,
                        fingerprint=continuation_fingerprint,
                    )
                    store.assert_not_called()
                    self.assertEqual(
                        self.thread(session_id)["thread_id"], stored_thread
                    )
                    self.assertIs(self.thread(session_id)["tainted"], True)
                    assert caught_exception is not None
                    self.assertIs(caught_exception.__cause__, decode_failure)
                    if decode_kind == "protocol":
                        self.assertIs(decode_failure.__cause__, prior_cause)
                    traceback_cursor = decode_failure.__traceback__
                    self.assertIsNotNone(decode_traceback)
                    while (
                        traceback_cursor is not None
                        and traceback_cursor.tb_frame is not decode_frame
                    ):
                        traceback_cursor = traceback_cursor.tb_next
                    self.assertIsNotNone(traceback_cursor)
                    assert traceback_cursor is not None
                    self.assertEqual(traceback_cursor.tb_lasti, decode_lasti)

    async def test_success_and_noneligible_shared_seam_failures_do_not_capture(
        self,
    ) -> None:
        backend = self._managed_backend()
        bound = await backend.bind(self.definition())

        success_session = "capture_noneligible_success"
        self.create_provisioned_session_with_access(backend, success_session)
        success_workspace = await self.direct_execution_workspace(
            bound,
            success_session,
        )
        success_reply = AgentReply.candidate("r0", "validated response")
        with (
            mock.patch.object(
                backend._codex_adapter,
                "invoke",
                mock.AsyncMock(
                    return_value=ProviderResult(
                        thread_id="validated-thread",
                        response_text="PRIVATE_VALIDATED_RESPONSE",
                    )
                ),
            ),
            _rejected_response_artifact_patch() as record,
        ):
            actual = await self._invoke_empty_contract(
                backend, bound,
                session_id=success_session,
                debate_id="success-debate",


                decode=lambda _response: success_reply,
                execution_workspace=success_workspace,
            )
        self.assertIs(actual, success_reply)
        record.assert_not_called()
        self.assertEqual(self.thread(success_session)["thread_id"], "validated-thread")
        self.assertIs(self.thread(success_session)["tainted"], False)

        decode_controls: tuple[BaseException, ...] = (
            TypeError("PRIVATE_TYPE_ERROR"),
            ValueError("PRIVATE_VALUE_ERROR"),
            asyncio.CancelledError("PRIVATE_DECODE_CANCEL"),
            KeyboardInterrupt("PRIVATE_DECODE_INTERRUPT"),
            SystemExit("PRIVATE_DECODE_EXIT"),
            BaseException("PRIVATE_DECODE_CONTROL"),
        )
        for index, failure in enumerate(decode_controls):
            with self.subTest(decode_control=type(failure).__name__):
                session_id = f"capture_decode_control_{index}"
                self.create_provisioned_session_with_access(backend, session_id)
                execution_workspace = await self.direct_execution_workspace(
                    bound,
                    session_id,
                )
                invoke = mock.AsyncMock(
                    return_value=ProviderResult(
                        thread_id=f"decode-control-thread-{index}",
                        response_text=f"PRIVATE_DECODE_CONTROL_RESPONSE_{index}",
                    )
                )

                def fail_decode(_response: str) -> AgentReply:
                    raise failure

                with (
                    mock.patch.object(backend._codex_adapter, "invoke", invoke),
                    _rejected_response_artifact_patch() as record,
                    self.assertRaises(type(failure)) as caught,
                ):
                    await self._invoke_empty_contract(
                        backend, bound,
                        session_id=session_id,
                        debate_id=f"decode-control-{index}",


                        decode=fail_decode,
                        execution_workspace=execution_workspace,
                    )
                self.assertIs(caught.exception, failure)
                record.assert_not_called()

        provider_session = "capture_provider_pre_result"
        self.create_provisioned_session_with_access(backend, provider_session)
        provider_workspace = await self.direct_execution_workspace(
            bound,
            provider_session,
        )
        provider_failure = ProviderInvocationError(
            "fixed pre-result failure",
            taint_existing_thread=False,
        )
        with (
            mock.patch.object(
                backend._codex_adapter,
                "invoke",
                mock.AsyncMock(side_effect=provider_failure),
            ),
            _rejected_response_artifact_patch() as record,
            self.assertRaises(BackendProtocolError) as caught,
        ):
            await self._invoke_empty_contract(
                backend, bound,
                session_id=provider_session,
                debate_id="provider-pre-result",


                decode=lambda _response: success_reply,
                execution_workspace=provider_workspace,
            )
        self.assertEqual(str(caught.exception), "fixed pre-result failure")
        record.assert_not_called()

        adapter_cancel_session = "capture_adapter_cancel"
        self.create_provisioned_session_with_access(backend, adapter_cancel_session)
        adapter_cancel_workspace = await self.direct_execution_workspace(
            bound,
            adapter_cancel_session,
        )
        adapter_cancel = asyncio.CancelledError("PRIVATE_ADAPTER_CANCEL")
        with (
            mock.patch.object(
                backend._codex_adapter,
                "invoke",
                mock.AsyncMock(side_effect=adapter_cancel),
            ),
            _rejected_response_artifact_patch() as record,
            self.assertRaises(asyncio.CancelledError) as caught,
        ):
            await self._invoke_empty_contract(
                backend, bound,
                session_id=adapter_cancel_session,
                debate_id="adapter-cancel",


                decode=lambda _response: success_reply,
                execution_workspace=adapter_cancel_workspace,
            )
        self.assertIs(caught.exception, adapter_cancel)
        record.assert_not_called()

        persistence_session = "capture_persistence_failure"
        self.create_provisioned_session_with_access(backend, persistence_session)
        persistence_workspace = await self.direct_execution_workspace(
            bound,
            persistence_session,
        )
        persistence_failure = OSError("PRIVATE_THREAD_PERSISTENCE_FAILURE")
        with (
            mock.patch.object(
                backend._codex_adapter,
                "invoke",
                mock.AsyncMock(
                    return_value=ProviderResult(
                        thread_id="unpersisted-valid-thread",
                        response_text="PRIVATE_VALID_BUT_UNPERSISTED_RESPONSE",
                    )
                ),
            ),
            _fresh_literal_object_side_effect(provider_manifest_thread_store, 'store_manifest_thread', persistence_failure),
            _rejected_response_artifact_patch() as record,
            self.assertRaises(OSError) as caught,
        ):
            await self._invoke_empty_contract(
                backend, bound,
                session_id=persistence_session,
                debate_id="persistence-failure",


                decode=lambda _response: success_reply,
                execution_workspace=persistence_workspace,
            )
        self.assertIs(caught.exception, persistence_failure)
        record.assert_not_called()
        self.assertEqual(_provider_error_records(self.workspace), [])

    async def test_pre_result_stream_stderr_timeout_and_cancellation_do_not_capture(
        self,
    ) -> None:
        with _rejected_response_artifact_patch() as record:
            for index, mode in enumerate(
                ("nonzero", "missing_output", "invalid_event_utf8")
            ):
                with self.subTest(mode=mode):
                    session_id = f"capture_pre_result_{index}"
                    backend, bound = await self._provisioned_bound(session_id)
                    with (
                        mock.patch.dict(
                            os.environ,
                            {
                                "STORY_FAKE_CODEX_MODE": mode,
                                "STORY_PRIVATE_CREDENTIAL": (
                                    "PRIVATE_PRE_RESULT_CREDENTIAL"
                                ),
                            },
                        ),
                        self.assertRaises(BackendProtocolError),
                    ):
                        await bound.invoke(
                            self.request(
                                session_id=session_id,
                                debate_id=f"pre-result-{index}",
                            )
                        )
                    backend.close()

            timeout_session = "capture_pre_result_timeout"
            timeout_backend = AgentBackend(
                self.workspace,
                executable=str(self.executable),
                timeout_seconds=0.3,
            )
            self.addCleanup(timeout_backend.close)
            self.create_provisioned_session_with_access(timeout_backend, timeout_session)
            timeout_bound = await timeout_backend.bind(self.definition())
            real_wait_for = asyncio.wait_for
            communication_pending = True
            timeout_target = len(self.provider_invocations()) + 1

            async def wait_for_after_provider_ready(awaitable, timeout):
                nonlocal communication_pending
                if communication_pending:
                    communication_pending = False
                    communication = asyncio.ensure_future(awaitable)
                    await self.wait_for_provider_invocations(timeout_target)
                    return await real_wait_for(communication, timeout=0)
                return await real_wait_for(awaitable, timeout=timeout)

            with (
                mock.patch.dict(
                    os.environ,
                    {"STORY_FAKE_CODEX_MODE": "sleep"},
                ),
                mock.patch(
                    "story_writing_agents.backend.asyncio.wait_for",
                    new=wait_for_after_provider_ready,
                ),
                self.assertRaises(BackendProtocolError),
            ):
                await timeout_bound.invoke(
                    self.request(
                        session_id=timeout_session,
                        debate_id="pre-result-timeout",
                    )
                )

            cancel_session = "capture_pre_result_cancel"
            cancel_backend, cancel_bound = await self._provisioned_bound(cancel_session)
            cancel_target = len(self.provider_invocations()) + 1
            with mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_MODE": "sleep"},
            ):
                invocation = asyncio.create_task(
                    cancel_bound.invoke(
                        self.request(
                            session_id=cancel_session,
                            debate_id="pre-result-cancel",
                        )
                    )
                )
                await self.wait_for_provider_invocations(cancel_target)
                invocation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await invocation

        record.assert_not_called()
        self.assertEqual(_provider_error_records(self.workspace), [])
        runtime_log = self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
            encoding="utf-8"
        )
        for private_value in (
            "PRIVATE_PRE_RESULT_CREDENTIAL",
            "simulated provider failure",
            "PRIVATE_INVALID_UTF8_STDERR",
            "PRIVATE_STORAGE",
        ):
            self.assertNotIn(private_value, runtime_log)


if __name__ == "__main__":
    unittest.main()
