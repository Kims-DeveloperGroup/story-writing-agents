"""Concrete Codex CLI backend tests with no credentials or network access."""

from __future__ import annotations
from tests.test_lead_orchestration import _exploration_native_contract, _exploration_outcome, _premise_native_contract

import asyncio
import contextlib
import gc
import hashlib
import inspect
import io
import json
import logging
import os
import sys
import tempfile
import unittest
import warnings
import weakref
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from unittest import mock

from story_writing_agents import (CONVERSATION_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION, AgentBackend,
    AgentReply,
    AgentRequest,
    BoundAgent, CHARACTER_CUSTODIAN,
    ConversationContextWindow, ConversationEvent,
    ConversationEventKind, ConversationReply,
    ConversationRequest,
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler, ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReadAction, ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction, ConversationRetrievalSearchItem,
    ConversationRetrievalSearchObservation, ConversationRetrievalSource,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
    Phase,
    PromptFileReference, ROSTER,
    ReplyTrace,
    ReplyTraceOrigin,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState, Workspace,
)
from story_writing_agents import cli as cli_module
from story_writing_agents.conversation_retrieval import (CONVERSATION_RETRIEVAL_POLICY,
    ConversationRagAttemptOutcome, ConversationRagContext,
    ConversationRagExcerpt, serialize_conversation_retrieval_observation,
)
from story_writing_agents.models import AgentReplyKind, AgentReplyNextPhase
from story_writing_agents.conversation_rag import (ConversationRagDocumentIdentity,
    ConversationRagDocumentKey, ConversationRagOutcome,
    ConversationRagReason, ConversationRagSourceReference,
)
from story_writing_agents.conversation_search import CollaborationHistoryCitation
from story_writing_agents.conversations import (CollaborationReadableFieldKey,
    select_bounded_collaboration_context,)
from story_writing_agents.cli import _parse_backend_overrides, build_parser, main
from story_writing_agents.backend import backend_configuration_snapshot
from story_writing_agents.structured_output_validation import diagnose_json_text, failure_issues
from story_writing_agents.agent_context import (
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderUsage, account_provider_payload,
    canonical_provider_json,)
from story_writing_agents.agent_context.manifest import (ContextContinuationIdentity,
    ContextScope, ContextSubjectKind,
)
from story_writing_agents.agents import CORE_DOCUMENTS, LEAD_ORCHESTRATOR
from story_writing_agents.agent_context.service import (ContextProviderCallRequest,
    ContextProviderTerminalCode, ContextReferencePreludeRequest,
    context_provider_failure_facts,)
from story_writing_agents.config import ConfigManager, ConfigurationSnapshot
from story_writing_agents.errors import (BackendProtocolError,
    ConfigurationError, ConversationRetrievalBudgetError,
    ConversationRetrievalObserverError, ConversationRetrievalSemanticError,
    SessionIdError, SessionNotFoundError,
    WorkspaceFormatError,)
from story_writing_agents.debug_log import (DEBUG_LOG_RELATIVE_PATH,
    WorkspaceDebugLog,)
from story_writing_agents.prompts import PromptAssembler
from story_writing_agents.interactive_settings import (ProviderRouteEntry,
    commit_prepared_settings_save, load_settings_state,
    prepare_settings_save, update_agent_backend,
)
from story_writing_agents.git.session_resources import GitSessionGenerationPin
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.provider_execution_workspace import (ProviderExecutionWorkspace,
    mint_provider_execution_workspace,)
from story_writing_agents.workspace.initialization import provision_session_resources
from story_writing_agents.workspace.initialization import SessionArtifactBindingError
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)
from story_writing_agents.writing_artifacts import (ArtifactLineRange,
    ArtifactReference, ArtifactReferenceBatch,
    ReferenceAuthorizationError, ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation, ReferenceInvocationState,
    ReferenceOperationState, ReferenceReadAction,
    ReferenceReadActionKind, ReferenceReadRequest,
)
from story_writing_agents.provider_adapter import (AgentExecutionProfile,
    ExecutionValueSource, ProviderInvocationError,
    ProviderResult, provider_failure_accounting,
    record_provider_failure_accounting,)
from story_writing_agents.provider_error_artifacts import (PROVIDER_ERROR_ARTIFACT_DIRECTORY,
    PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY, WorkspaceProviderErrorArtifacts,
)
from story_writing_agents.provider_process import (ProcessRequest,
    run_process, stop_process,
)
from story_writing_agents.models import StoryFocusedContextMode

from story_writing_agents.agent_context.request_compaction import compact_context_payload
from tests.test_agent_context_accounting import _shared_context_payload
from tests.provider_wire_fixture import assert_guidance, direct_reply, direct_response
from tests.helpers import (SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE,
    all_manuscripts, core_snapshot,
    initialized_workspace, pristine_initialized_workspace,
)


_FAKE_CODEX = ("""#!/usr/bin/env python3
import os
import signal

mode = os.environ.get("STORY_FAKE_CODEX_MODE", "ok")
substantive_sleep = os.environ.get("STORY_FAKE_CODEX_SLEEP_SUBSTANTIVE") == "1"
if mode == "sleep" or substantive_sleep:
    def terminated(signum, frame):
        descriptor = os.open(
            os.environ["STORY_FAKE_CODEX_LOG"],
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(descriptor, b'{"terminated":true}\\n')
        finally:
            os.close(descriptor)
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, terminated)

import json
from pathlib import Path
import sys
import time

argv = sys.argv[1:]
log_path = Path(os.environ["STORY_FAKE_CODEX_LOG"])

def append(value):
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\\n")

"""
    + SUPPORTED_ARRAY_ITEMS_SCHEMA_VALIDATOR_SOURCE
    + inspect.getsource(direct_response)
    + """
raw_payload = sys.stdin.read()
payload = json.loads(raw_payload)
reference_prelude = payload.get("operation") == "writing_artifact_reference_read"
agent_directory = Path(argv[argv.index("--cd") + 1])
developer_instructions = next(
    value.split("=", 1)[1]
    for value in argv
    if value.startswith("developer_instructions=")
)
schema_path = Path(argv[argv.index("--output-schema") + 1])
schema = json.loads(schema_path.read_text(encoding="utf-8"))
append({
    "argv": argv,
    "payload": payload,
    "raw_payload": raw_payload,
    "cwd": os.getcwd(),
    "agents": json.loads(developer_instructions),
    "schema": schema,
})

if not schema_uses_supported_array_items(schema):
    raise SystemExit(86)

if mode == "sleep" or substantive_sleep:
    time.sleep(30)
if mode == "delay":
    time.sleep(0.25)
if mode == "nonzero_invalid_stderr":
    os.write(
        sys.stderr.fileno(),
        b"PRIVATE_INVALID_UTF8_STDERR\\xff\\xfe\\x80\\n",
    )
    raise SystemExit(7)
if mode == "nonzero" or os.environ.get("STORY_FAKE_CODEX_FAIL_SUBSTANTIVE") == "1":
    if "conversation_id" in payload:
        sensitive = [payload.get("user_input_text", "")]
        sensitive.extend(item["content_text"] for item in payload["conversation_history"])
    elif "user_input_text" in payload:
        sensitive = [payload.get("user_input_text", "")]
        sensitive.extend(
            item["content_text"]
            for item in payload.get("conversation_history", [])
        )
    else:
        sensitive = []
    prefix = "X" * 5000 if os.environ.get("STORY_FAKE_CODEX_LONG_DIAGNOSTIC") else ""
    print(
        prefix
        + "simulated provider failure: "
        + " | ".join(sensitive)
        + " | "
        + str(agent_directory),
        file=sys.stderr,
    )
    raise SystemExit(7)

output_path = Path(argv[argv.index("--output-last-message") + 1])
if mode == "symlink_output":
    output_path.symlink_to(os.environ["STORY_FAKE_CODEX_SYMLINK_TARGET"])
elif mode == "hardlink_output":
    os.link(os.environ["STORY_FAKE_CODEX_HARDLINK_TARGET"], output_path)
elif reference_prelude and "STORY_FAKE_CODEX_REFERENCE_ACTION" in os.environ:
    output_path.write_text(
        os.environ["STORY_FAKE_CODEX_REFERENCE_ACTION"],
        encoding="utf-8",
    )
elif mode == "missing_output":
    pass
elif "STORY_FAKE_CODEX_PHYSICAL_OUTPUTS" in os.environ:
    outputs = json.loads(os.environ["STORY_FAKE_CODEX_PHYSICAL_OUTPUTS"])
    invocations = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    physical_index = sum("payload" in item for item in invocations) - 1
    output_path.write_text(outputs[physical_index], encoding="utf-8")
elif "STORY_FAKE_CODEX_OUTPUTS" in os.environ:
    outputs = json.loads(os.environ["STORY_FAKE_CODEX_OUTPUTS"])
    action_index = payload["conversation_retrieval_control"]["retrieval_action_number"]
    output_path.write_text(outputs[action_index - 1], encoding="utf-8")
elif "STORY_FAKE_CODEX_OUTPUT" in os.environ:
    output_path.write_text(
        os.environ["STORY_FAKE_CODEX_OUTPUT"],
        encoding="utf-8",
    )
elif mode == "malformed":
    output_path.write_text("not-json", encoding="utf-8")
elif "conversation_id" in payload:
    output_path.write_text(json.dumps({
        "message": "generated conversation reply",
    }), encoding="utf-8")
elif payload["consensus_phase"] == "review_candidate":
    output_path.write_text(json.dumps({
        "verdict": "approve",
        "message": "",
    }), encoding="utf-8")
elif payload["consensus_phase"] == "confirm_reviewed_proposal":
    output_path.write_text(json.dumps({
        "reviewed_proposal_id": "reviewed-proposal",
        "reviewed_proposal_digest": "sha256:" + "a" * 64,
    }), encoding="utf-8")
elif payload["consensus_phase"] == "synthesize_reviews":
    output_path.write_text(json.dumps({
        "next": "finalization",
        "message": "generated candidate",
    }), encoding="utf-8")
else:
    output_path.write_text(json.dumps({
        "message": "generated candidate",
    }), encoding="utf-8")

if output_path.exists() and not output_path.is_symlink() and output_path.stat().st_nlink == 1:
    output_path.write_text(direct_response(output_path.read_text(encoding="utf-8"), payload), encoding="utf-8")
if mode == "invalid_event_utf8":
    sys.stdout.buffer.write(b"\\xff")
elif "resume" not in argv:
    print(json.dumps({
        "type": "thread.started",
        "thread_id": (
            "thread-"
            + payload.get("session_id", "reference-read")
            + "-"
            + (
                payload["agent_id"]
                if "agent_id" in payload
                else payload["correlation"]["agent_id"]
            )
        ),
    }))
if "STORY_FAKE_CODEX_USAGE" in os.environ:
    print(json.dumps({
        "type": "turn.completed",
        "usage": json.loads(os.environ["STORY_FAKE_CODEX_USAGE"]),
    }))
""")


_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def _fresh_literal_object_side_effect(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)

def _rejected_response_artifact_patch():
    return mock.patch.object(WorkspaceProviderErrorArtifacts,
        "record_rejected_response",)

def _wrapped_thread_taint_patch(backend, wrapped):
    return mock.patch.object(
        backend, "_taint_thread",
        wraps=wrapped,
    )

def _wrapped_thread_store_patch(backend, wrapped):
    return mock.patch.object(
        backend, "_store_thread",
        wraps=wrapped,
    )

def _conversation_pair(turn_id: str,
    sequence: int = 1, *,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a", recipient: str = PLOT_ARCHITECT,
    user_content: str = "visible question", outcome_content: str = "visible answer",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    def event(offset: int,
        kind: ConversationEventKind, sender: str,
        target: str, content: str,
    ) -> ConversationEvent:
        return ConversationEvent(CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id, conversation_id,
            turn_id, sequence + offset,
            _TIMESTAMP, sender,
            target, kind,
            content,)

    user = event(0, ConversationEventKind.USER_MESSAGE, "user", recipient, user_content)
    sender = (recipient if outcome_kind is ConversationEventKind.AGENT_REPLY else "runtime"
    )
    return user, event(1, outcome_kind, sender, "user", outcome_content)


def _context_window(events: tuple[ConversationEvent, ...] = (),
    *, retained_turns: int | None = None,
    retained_utf8_bytes: int | None = None, omitted_turns: int = 0,
) -> ConversationContextWindow:
    retained = (len({event.turn_id for event in events})
        if retained_turns is None else retained_turns
    )
    retained_bytes = (sum(len(event.content.encode("utf-8")) for event in events)
        if retained_utf8_bytes is None else retained_utf8_bytes
    )
    return ConversationContextWindow(events=events,
        total_complete_turns=retained + omitted_turns, retained_turns=retained,
        omitted_turns=omitted_turns, retained_utf8_bytes=retained_bytes,
        truncated=omitted_turns > 0,)


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes())
        for path in sorted(root.rglob("*"))}


def _workspace_debug_records(workspace: Any) -> list[dict[str, object]]:
    """Read the complete diagnostic log for one initialized workspace."""

    path = workspace.path(DEBUG_LOG_RELATIVE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _mark_debug_session_attached(workspace: Any) -> None:
    """Model the attached-session boundary required before runtime logging."""

    state = workspace.path(
        "agent_states/orchestrator_state/sessions/test_attached.json")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}\n", encoding="utf-8")


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
    records = [json.loads(line)
        for path in directory.rglob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()]
    return sorted(records, key=lambda record: record["workspace_sequence"])


def _debug_signature(record: dict[str, object]) -> tuple[object, ...]:
    """Project one diagnostic record to its exact content-safe contract."""

    exception = record.get("exception")
    return (record.get("schema_version"),
        record.get("level"), record.get("component"),
        record.get("operation"), record.get("phase"),
        record.get("outcome"), record.get("session_id"),
        record.get("debate_id"), record.get("conversation_id"),
        record.get("agent_id"),
        exception.get("type") if isinstance(exception, dict) else None,)


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


def _readable_context():  # type: ignore[no-untyped-def]
    """Return the canonical content-free active-v2 retrieval context."""

    return select_bounded_collaboration_context(())


def _retrieval_request(*,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a",
    turn_id: str = "turn_current",
    agent_id: str = PLOT_ARCHITECT, content: str = "visible retrieval request",
    context: object | None = None, rag_context: ConversationRagContext | None = None,
) -> ConversationRetrievalRequest:
    """Build one exact bounded-retrieval provider request."""

    return ConversationRetrievalRequest(session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        agent_id=agent_id,
        content=content, context=_readable_context() if context is None else context,
        rag_context=rag_context,)


def _provider_rag_context() -> ConversationRagContext:
    """Build one attempted context whose full reference must reach call one."""

    identity = ConversationRagDocumentIdentity(key=ConversationRagDocumentKey(
            session_id="story_session",
            document_id="a" * 64,),
        conversation_id="source_conversation", start_turn_id="source_turn",
        end_turn_id="source_turn", start_sequence=1,
        end_sequence=2,
        revision_id="sha256:" + "b" * 64,)
    content = "authoritative excerpt 🙂"
    excerpt = ConversationRagExcerpt(citation_id="g1",
        source=ConversationRetrievalSource(citation=CollaborationHistoryCitation(
                session_id="story_session", conversation_id="source_conversation",
                event_id="source_event", sequence=2,
                field_key=CollaborationReadableFieldKey.SPECIALIST_REPLY_CONTENT, start_codepoint=0,
                end_codepoint=len(content),),
        ), reference=ConversationRagSourceReference(
            document=identity, fragment_id="fragment-authorized",
            start_codepoint=0,
            end_codepoint=len(content),),
        content=content,)
    return ConversationRagContext(outcome=ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,),
        excerpts=(excerpt,), retained_utf8_bytes=len(content.encode("utf-8")),
        truncated=False,)


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


def _retrieval_action_json(request: ConversationRetrievalRequest,
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


def _needle_search(request):
    return _retrieval_action_json(
        request, kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
        action_index=1, query="Needle source",)


def _read_first_search_hit(request):
    return _retrieval_action_json(request, kind=ConversationRetrievalActionKind.READ_HISTORY,
        action_index=2, citation_id="s1h1", start_sequence=1, end_sequence=None,)


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


class CodexBackendDefaultsTests(unittest.TestCase):
    """Lock construction-time defaults without starting a provider process."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        _mark_debug_session_attached(self.workspace)

    def write_defaults(
        self,
        model: object,
        effort: object,
        **extra: object,
    ) -> None:
        """Write one complete system-default schema for constructor tests."""

        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": model,
                "codex_reasoning_effort": effort,
                **extra,},
        )

    def test_model_precedence_and_explicit_none_are_exact(self) -> None:
        cases = (
            (None, None, {}, None),
            ("persisted-model", None, {}, "persisted-model"),
            ("persisted-model", None, {"model": None}, "persisted-model"),
            (
                "persisted-model",
                None,
                {"model": "explicit-model"},
                "explicit-model",),
        )
        for persisted, effort, keywords, expected in cases:
            with self.subTest(persisted=persisted, keywords=keywords):
                self.write_defaults(persisted, effort)
                backend = AgentBackend(self.workspace, **keywords)
                try:
                    self.assertEqual(backend.model, expected)
                    self.assertIsNone(backend._codex_reasoning_effort)
                finally:
                    backend.close()

        self.workspace.write_text(
            "config/system_defaults.json",
            '{"language":"KO","persona":"default_kim_eun_hee","max_debate_rounds":1}\n',)
        backend = AgentBackend(self.workspace)
        try:
            self.assertIsNone(backend.model)
            self.assertIsNone(backend._codex_reasoning_effort)
        finally:
            backend.close()

    def test_agent_model_and_reasoning_precedence_uses_immutable_snapshot(self) -> None:
        self.write_defaults("system-model", "low")
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT].update(
            {
                "codex_model": "agent-model",
                "codex_reasoning_effort": "high",}
        )
        self.workspace.write_json("config/agent_configs.json", configs)

        backend = AgentBackend(self.workspace)
        try:
            self.assertEqual(
                backend._codex_invocation_settings(PLOT_ARCHITECT),
                ("agent-model", "high"),
            )
            # An explicit model wins only that setting; per-agent reasoning remains.
            explicit = AgentBackend(self.workspace, model="explicit-model")
            self.addCleanup(explicit.close)
            self.assertEqual(
                explicit._codex_invocation_settings(PLOT_ARCHITECT),
                ("explicit-model", "high"),)

            configs[PLOT_ARCHITECT].update(
                {"codex_model": "changed", "codex_reasoning_effort": "minimal"})
            self.workspace.write_json("config/agent_configs.json", configs)
            self.assertEqual(
                backend._codex_invocation_settings(PLOT_ARCHITECT),
                ("agent-model", "high"),
            )
        finally:
            backend.close()

    def test_execution_profiles_resolve_every_source_with_nullable_defaults(
        self,
    ) -> None:
        self.workspace.write_json(
            "config/system_defaults.json",
            {
                "language": "KO",
                "persona": "default_kim_eun_hee",
                "max_debate_rounds": 1,
                "codex_model": "system-codex",
                "codex_reasoning_effort": "system-effort",
                "antigravity_model": "system-agy",
                "antigravity_effort": None,
                "default_backend": "antigravity",},
        )
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[ROSTER[0]].update(
            {
                "backend": None,
                "codex_model": None,
                "codex_reasoning_effort": None,}
        )
        configs[PLOT_ARCHITECT].update(
            {
                "backend": "codex",
                "codex_model": "agent-codex",
                "codex_reasoning_effort": "agent-effort",}
        )
        configs[CHARACTER_CUSTODIAN].update(
            {
                "backend": "antigravity",
                "antigravity_model": None,
                "antigravity_effort": "high",
            }
        )
        configs[EXECUTION_SPECIALIST].update(
            {
                "backend": None,
                "antigravity_model": None,
                "antigravity_effort": None,}
        )
        self.workspace.write_json("config/agent_configs.json", configs)

        backend = AgentBackend(
            self.workspace,
            backend_overrides={ROSTER[0]: "codex"},
        )
        self.addCleanup(backend.close)
        expected = (
            AgentExecutionProfile(
                ROSTER[0],
                "codex",
                ExecutionValueSource.LAUNCH_OVERRIDE,
                "system-codex",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "system-effort",
                ExecutionValueSource.SYSTEM_DEFAULT,),
            AgentExecutionProfile(
                PLOT_ARCHITECT,
                "codex",
                ExecutionValueSource.AGENT_CONFIG,
                "agent-codex",
                ExecutionValueSource.AGENT_CONFIG,
                "agent-effort",
                ExecutionValueSource.AGENT_CONFIG,
            ),
            AgentExecutionProfile(
                CHARACTER_CUSTODIAN,
                "antigravity",
                ExecutionValueSource.AGENT_CONFIG,
                "system-agy",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "high",
                ExecutionValueSource.AGENT_CONFIG,
            ),
            AgentExecutionProfile(
                EXECUTION_SPECIALIST,
                "antigravity",
                ExecutionValueSource.SYSTEM_DEFAULT,
                "system-agy",
                ExecutionValueSource.SYSTEM_DEFAULT,
                None,
                ExecutionValueSource.PROVIDER_DEFAULT,),
        )
        self.assertEqual(backend.execution_profiles(), expected)
        for profile, active in zip(
            expected,
            backend.execution_profiles(),
            strict=True,
        ):
            self.assertIs(backend.execution_profile(profile.agent_id), active)
            body = json.dumps(
                {
                    "effort": profile.effort,
                    "effort_source": profile.effort_source.value,
                    "model": profile.model,
                    "model_source": profile.model_source.value,
                    "provider": profile.provider,
                    "provider_source": profile.provider_source.value,},
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            fingerprint = backend.execution_profile_fingerprint(profile.agent_id)
            self.assertEqual(
                fingerprint,
                f"sha256:{hashlib.sha256(body).hexdigest()}",
            )
            self.assertEqual(
                fingerprint,
                AgentBackend._execution_profile_fingerprint(profile),
            )
        changed = replace(expected[0], effort="changed-effort")
        self.assertNotEqual(
            AgentBackend._execution_profile_fingerprint(expected[0]),
            AgentBackend._execution_profile_fingerprint(changed),
        )
        with self.assertRaisesRegex(TypeError, "execution profile"):
            AgentBackend._execution_profile_fingerprint(object())  # type: ignore[arg-type]

        explicit = AgentBackend(
            self.workspace,
            model="launch-codex",
            antigravity_model="launch-agy",
            antigravity_effort="medium",
            backend_overrides={ROSTER[0]: "codex"},
        )
        self.addCleanup(explicit.close)
        self.assertEqual(
            explicit.execution_profile(PLOT_ARCHITECT).model_source,
            ExecutionValueSource.LAUNCH_OVERRIDE,
        )
        character = explicit.execution_profile(CHARACTER_CUSTODIAN)
        self.assertEqual(
            (character.model, character.model_source),
            ("launch-agy", ExecutionValueSource.LAUNCH_OVERRIDE),
        )
        self.assertEqual(
            (character.effort, character.effort_source),
            ("medium", ExecutionValueSource.LAUNCH_OVERRIDE),
        )

        default_workspace = initialized_workspace(self)
        defaults = AgentBackend(default_workspace)
        self.addCleanup(defaults.close)
        for profile in defaults.execution_profiles():
            self.assertEqual(
                (
                    profile.provider,
                    profile.provider_source,
                    profile.model,
                    profile.model_source,
                    profile.effort,
                    profile.effort_source,
                ),
                (
                    "codex",
                    ExecutionValueSource.BUILT_IN_DEFAULT,
                    None,
                    ExecutionValueSource.PROVIDER_DEFAULT,
                    None,
                    ExecutionValueSource.PROVIDER_DEFAULT,
                ),
            )

    def test_startup_logs_four_exact_active_execution_profile_snapshots(self) -> None:
        backend = AgentBackend(self.workspace)
        self.addCleanup(backend.close)
        startup = [
            record
            for record in _workspace_debug_records(self.workspace)
            if record.get("schema_version") == 2
        ]
        self.assertEqual(
            [record["agent_id"] for record in startup],
            list(ROSTER),
        )
        self.assertEqual(len(startup), len(ROSTER))
        for record, profile in zip(
            startup,
            backend.execution_profiles(),
            strict=True,
        ):
            with self.subTest(agent_id=profile.agent_id):
                timestamp = record.pop("timestamp")
                self.assertRegex(
                    timestamp,
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
                )
                self.assertEqual(
                    record,
                    {
                        "schema_version": 2,
                        "level": "trace",
                        "component": "backend",
                        "operation": "execution_profile_applied",
                        "phase": "succeeded",
                        "correlation_id": "startup",
                        "agent_id": profile.agent_id,
                        "route_generation": 0,
                        "activation_state": "active",
                        "active_profile": {
                            "provider": profile.provider,
                            "provider_source": profile.provider_source.value,
                            "model": profile.model,
                            "model_source": profile.model_source.value,
                            "effort": profile.effort,
                            "effort_source": profile.effort_source.value,
                        },
                    },
                )

    def test_explicit_model_validation_retains_exact_value_error(self) -> None:
        invalid = (
            False,
            7,
            "",
            "-gpt",
            "gpt\x00tail",
            "gpt\x85tail",
            "gpt\u200etail",
            "gpt\u2066tail",
            "gpt\ud800tail",
        )
        for model in invalid:
            with self.subTest(model=repr(model)):
                with self.assertRaises(ValueError) as caught:
                    AgentBackend(self.workspace, model=model)  # type: ignore[arg-type]
                self.assertEqual(
                    str(caught.exception),
                    "Codex model must be safe non-empty text or None",
                )
                self.assertIsInstance(
                    caught.exception.__cause__,
                    ConfigurationError,
                )

    def test_constructor_validation_diagnostics_are_fixed_and_provider_safe(
        self,
    ) -> None:
        cases = (
            (
                "codex_model",
                {"model": "-PRIVATE_CODEX_MODEL"},
                "Codex model must be safe non-empty text or None",
                "PRIVATE_CODEX_MODEL",
            ),
            (
                "antigravity_model",
                {"antigravity_model": "-PRIVATE_ANTIGRAVITY_MODEL"},
                "Antigravity model must be safe non-empty text or None",
                "PRIVATE_ANTIGRAVITY_MODEL",
            ),
            (
                "antigravity_effort",
                {"antigravity_effort": "PRIVATE_ANTIGRAVITY_EFFORT"},
                "Antigravity effort must be None or one of: low, medium, high",
                "PRIVATE_ANTIGRAVITY_EFFORT",
            ),
        )
        for label, keywords, diagnostic, private in cases:
            with self.subTest(label=label):
                mark = len(_workspace_debug_records(self.workspace))
                with self.assertRaises(ValueError) as caught:
                    AgentBackend(self.workspace, **keywords)  # type: ignore[arg-type]

                self.assertEqual(str(caught.exception), diagnostic)
                self.assertIsInstance(caught.exception.__cause__, ConfigurationError)
                records = [
                    record
                    for record in _workspace_debug_records(self.workspace)[mark:]
                    if record["component"] == "backend"
                    and record["operation"] == "configuration"
                ]
                self.assertEqual(
                    [_debug_signature(record) for record in records],
                    [
                        (
                            1,
                            "trace",
                            "backend",
                            "configuration",
                            "started",
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                        ),
                        (
                            1,
                            "error",
                            "backend",
                            "configuration",
                            "failed",
                            "failed",
                            None,
                            None,
                            None,
                            None,
                            "builtins.ValueError",
                        ),
                    ],
                )
                self.assertNotIn(
                    private,
                    self.workspace.path(DEBUG_LOG_RELATIVE_PATH).read_text(
                        encoding="utf-8"
                    ),
                )

    def test_full_persisted_schema_precedes_explicit_model_validation(self) -> None:
        expected_effort = (
            "codex_reasoning_effort must be null or match [a-z][a-z0-9_-]{0,31}"
        )
        cases = (
            (
                "valid-persisted",
                "ULTRA",
                {},
                "-invalid-explicit",
                expected_effort,
            ),
            (
                "-invalid-persisted",
                None,
                {},
                "valid-explicit",
                "codex_model must be safe non-empty text or null",
            ),
            (
                "valid-persisted",
                None,
                {"unexpected": True},
                "valid-explicit",
                "unknown system default: unexpected",
            ),
        )
        for persisted, effort, extra, explicit, expected in cases:
            with self.subTest(expected=expected):
                self.write_defaults(persisted, effort, **extra)
                with self.assertRaises(ConfigurationError) as caught:
                    AgentBackend(self.workspace, model=explicit)
                self.assertEqual(str(caught.exception), expected)

        self.write_defaults("persisted", "low")
        agent_configs = self.workspace.read_json("config/agent_configs.json")
        agent_configs[PLOT_ARCHITECT]["backend"] = "invalid-provider"
        self.workspace.write_json("config/agent_configs.json", agent_configs)
        with self.assertRaises(ConfigurationError) as caught:
            AgentBackend(self.workspace, model="-invalid-explicit")
        self.assertEqual(
            str(caught.exception),
            "backend must be one of: codex, antigravity",
        )

    def test_backend_runtime_and_prompts_share_one_snapshot_until_next_lifecycle(
        self,
    ) -> None:
        original_load = ConfigManager.load_configuration_snapshot
        with mock.patch.object(
            ConfigManager,
            "load_configuration_snapshot",
            autospec=True,
            side_effect=original_load,
        ) as load:
            backend = AgentBackend(self.workspace, executable=sys.executable)
            runtime = StoryRuntime(self.workspace, backend)
        self.addCleanup(backend.close)
        load.assert_called_once()
        snapshot = backend_configuration_snapshot(backend)
        self.assertIs(runtime._configuration_snapshot, snapshot)
        self.assertIs(runtime.prompts.configuration_snapshot, snapshot)

        before = runtime.prompts.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual((before.backend, before.language), ("codex", "KO"))

        replacement_persona = "next_lifecycle_persona"
        self.workspace.write_text(
            f"config/persona_templates/alternative_templates/{replacement_persona}.md",
            "next",
        )
        defaults = self.workspace.read_json("config/system_defaults.json")
        defaults.update(
            {
                "language": "DE",
                "persona": replacement_persona,
                "max_debate_rounds": 0,
                "codex_model": "next-model",
                "codex_reasoning_effort": "high",
                "antigravity_model": "next-antigravity-model",
                "antigravity_effort": "medium",
            }
        )
        self.workspace.write_json("config/system_defaults.json", defaults)
        agents = self.workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "language": "FR",
                "persona": replacement_persona,
                "antigravity_model": "agent-antigravity-model",
                "antigravity_effort": "high",
            }
        )
        self.workspace.write_json("config/agent_configs.json", agents)

        self.assertTrue(backend.validate_provider_configuration("snapshot_session"))
        after = runtime.prompts.build_definition(
            session_id="snapshot_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after, before)
        self.assertIsNone(backend.model)
        self.assertIsNone(backend._codex_reasoning_effort)

        self.workspace.save_session_overrides(
            "override_session",
            {
                "language": "ES",
                "persona": replacement_persona,
                "agents": {},
            },
        )
        live_override = runtime.prompts.build_definition(
            session_id="override_session",
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(live_override.language, "ES")
        self.assertEqual(
            live_override.persona_file.path.name, "next_lifecycle_persona.md"
        )

        next_backend = AgentBackend(self.workspace, executable=sys.executable)
        self.addCleanup(next_backend.close)
        self.assertEqual(next_backend.model, "next-model")
        self.assertEqual(next_backend._codex_reasoning_effort, "high")
        self.assertEqual(
            next_backend._antigravity_adapter.invocation_settings(PLOT_ARCHITECT),
            ("agent-antigravity-model", "high"),
        )
        with (
            mock.patch.object(
                next_backend._antigravity_adapter,
                "executable_available",
                return_value=True,
            ) as executable_available,
            mock.patch.object(
                next_backend._antigravity_adapter,
                "headless_capabilities_available",
                return_value=True,
            ) as headless_capabilities_available,
        ):
            self.assertTrue(
                next_backend.validate_provider_configuration("snapshot_session")
            )
        executable_available.assert_called_once_with()
        headless_capabilities_available.assert_called_once_with()

    def test_optional_model_and_reasoning_argv_are_exact_for_fresh_and_resume(
        self,
    ) -> None:
        staging = self.workspace.path("staging")
        staging.mkdir()
        (staging / "AGENTS.md").write_text("role\n", encoding="utf-8")
        schema_path = self.workspace.path("schema.json")
        output_path = self.workspace.path("output.json")
        configurations = (
            (None, None, ()),
            ("persisted-model", None, ("--model", "persisted-model")),
            (
                None,
                "medium",
                ("--config", 'model_reasoning_effort="medium"'),
            ),
            (
                "persisted-model",
                "xhigh",
                (
                    "--model",
                    "persisted-model",
                    "--config",
                    'model_reasoning_effort="xhigh"',
                ),
            ),
        )
        for model, effort, optional in configurations:
            self.write_defaults(model, effort)
            backend = AgentBackend(self.workspace)
            try:
                for thread_id in (None, "thread-one"):
                    with self.subTest(
                        model=model,
                        effort=effort,
                        thread_id=thread_id,
                    ):
                        resume = () if thread_id is None else ("resume",)
                        trailing_thread = () if thread_id is None else (thread_id,)
                        expected = (
                            "codex",
                            "--ask-for-approval",
                            "never",
                            "--sandbox",
                            "read-only",
                            "--cd",
                            str(staging),
                            "exec",
                            *optional,
                            *resume,
                            "--json",
                            "--skip-git-repo-check",
                            "--ignore-user-config",
                            "--ignore-rules",
                            "--disable",
                            "apps",
                            "--disable",
                            "multi_agent",
                            "--disable",
                            "shell_tool",
                            "--disable",
                            "web_search",
                            "--config",
                            'web_search="disabled"',
                            "--config",
                            "project_root_markers=[]",
                            "--config",
                            "project_doc_max_bytes=32768",
                            "--output-schema",
                            str(schema_path),
                            "--output-last-message",
                            str(output_path),
                            *trailing_thread,
                            "-",
                        )
                        command = backend._codex_command(
                            staging,
                            schema_path,
                            output_path,
                            thread_id,
                        )
                        self.assertEqual(tuple(command), expected)
                        reasoning_values = tuple(
                            value
                            for value in command
                            if value.startswith("model_reasoning_effort=")
                        )
                        self.assertEqual(
                            reasoning_values,
                            ()
                            if effort is None
                            else (f'model_reasoning_effort="{effort}"',),
                        )
            finally:
                backend.close()

    def test_agent_model_and_reasoning_argv_are_exact(self) -> None:
        self.write_defaults("system-model", "low")
        configs = self.workspace.read_json("config/agent_configs.json")
        configs[PLOT_ARCHITECT].update(
            {"codex_model": "agent-model", "codex_reasoning_effort": "xhigh"}
        )
        self.workspace.write_json("config/agent_configs.json", configs)
        staging = self.workspace.path("staging")
        staging.mkdir()
        (staging / "AGENTS.md").write_text("role\n", encoding="utf-8")
        schema_path = self.workspace.path("schema.json")
        output_path = self.workspace.path("output.json")
        backend = AgentBackend(self.workspace)
        try:
            command = backend._codex_command(
                staging,
                schema_path,
                output_path,
                "thread-one",
                PLOT_ARCHITECT,
            )
            self.assertEqual(command[command.index("--model") + 1], "agent-model")
            self.assertEqual(
                command[command.index("--config", command.index("--model") + 1) + 1],
                'model_reasoning_effort="xhigh"',
            )
            self.assertEqual(command.count("--model"), 1)
            self.assertEqual(
                tuple(
                    value
                    for value in command
                    if value.startswith("model_reasoning_effort=")
                ),
                ('model_reasoning_effort="xhigh"',),
            )
        finally:
            backend.close()


class CodexBackendTestCase(unittest.IsolatedAsyncioTestCase):
    def _backend_with_fake_codex(self):
        return AgentBackend(
            self.workspace,
            executable=str(self.executable))

    def setUp(self) -> None:
        asyncio_diagnostics = logging.StreamHandler(sys.stderr)
        logging.getLogger("asyncio").addHandler(asyncio_diagnostics)
        self.addCleanup(asyncio_diagnostics.close)
        self.addCleanup(logging.getLogger("asyncio").removeHandler, asyncio_diagnostics)
        self.workspace = pristine_initialized_workspace(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        native_state = self.fake_directory / "codex-home"
        native_state.mkdir(mode=0o700)
        self.executable = self.fake_directory / "fake-codex"
        self.executable.write_text(_FAKE_CODEX, encoding="utf-8")
        self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_LOG": str(self.log_path), "CODEX_HOME": str(native_state)},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(self.workspace).build_definition(
            session_id="definition_session",
            agent_id=agent_id,
        )


    def create_provisioned_session_with_access(
        self,
        backend: AgentBackend,
        session_id: str,
    ) -> SessionLifecycleState:
        provision_session_resources(self.workspace.root, session_id)
        _mark_debug_session_attached(self.workspace)
        return backend.create_session_with_access(session_id)

    async def direct_execution_workspace(
        self,
        bound: object,
        session_id: str,
    ) -> ProviderExecutionWorkspace:
        return await self.enterAsyncContext(
            bound._direct_codex_execution_workspace(session_id, None)  # type: ignore[attr-defined]
        )

    async def test_both_native_backends_preserve_session_continuation_and_legacy_namespaces(self):
        from story_writing_agents.agent_context.service import (
            AgentContextService, ContextInvocationKind, ContextInvocationRequest,)
        from story_writing_agents.agent_context.sources import (
            ActiveDebateSourceAdapter, FocusedConversationSourceAdapter, OwnAgentStateSourceAdapter,
            SessionHistorySourceAdapter, WritingArtifactSourceAdapter)
        from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
        from tests.test_agent_context_execution import _LEAD_CALL_FACTORY, _lead_answer_frame, _materials
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

    async def test_native_mismatched_thread_is_ambiguous_without_replay_proof(self):
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "story_session")
        bound = await backend.bind(self.definition())
        await bound.converse(self.conversation_request())
        before = len(self.provider_invocations())
        native_failures = []
        invoke = backend._codex_adapter.invoke

        async def observe_native_failure(*args, **kwargs):
            try:
                return await invoke(*args, **kwargs)
            except ProviderInvocationError as failure:
                native_failures.append(failure)
                raise

        with mock.patch.object(backend._codex_adapter, "parse_events", return_value=({"type": "thread.started", "thread_id": "foreign-thread"},)), mock.patch.object(backend._codex_adapter, "invoke", new=observe_native_failure):
            with self.assertRaisesRegex(BackendProtocolError, "different continuation identity") as caught:
                await bound.converse(self.conversation_request())
        self.assertEqual(len(native_failures), 1)
        self.assertIsNone(native_failures[0].continuation_rejection)
        self.assertEqual(len(self.provider_invocations()), before + 1)
        await bound.converse(self.conversation_request())
        self.assertNotIn("resume", self.provider_invocations()[-1]["argv"])

    def antigravity_definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(
            self.workspace,
            backend_overrides={agent_id: "antigravity"},
        ).build_definition(
            session_id="definition_session",
            agent_id=agent_id,
        )

    def request(
        self,
        *,
        session_id: str = "story_session",
        debate_id: str = "debate_one",
        agent_id: str = PLOT_ARCHITECT,
        phase: Phase = Phase.DRAFT,
        revision_id: str = "r0",
    ) -> AgentRequest:
        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=agent_id,
            phase=phase,
            round_number=0,
            revision_id=revision_id,
            task="Perform the requested phase.",
            user_payload="dynamic user input",
            candidate_payload="dynamic candidate",
        )

    def conversation_request(self, **overrides: object) -> ConversationRequest:
        values: dict[str, object] = {
            "session_id": "story_session",
            "conversation_id": "conversation_a",
            "turn_id": "turn_current",
            "agent_id": PLOT_ARCHITECT,
            "content": "visible user request",
            "context": _context_window(),
        }
        values.update(overrides)
        return ConversationRequest(**values)  # type: ignore[arg-type]

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

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def provider_invocations(self) -> list[dict[str, object]]:
        return [item for item in self.invocations() if "payload" in item]

    async def wait_for_provider_invocations(self, count: int) -> None:
        for _ in range(200):
            if len(self.provider_invocations()) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"fake Codex did not record {count} invocations")

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

    def thread(self, session_id: str) -> dict[str, object]:
        manifest = self.workspace.read_json(
            f"agent_states/orchestrator_state/provider_sessions/{session_id}.json"
        )
        matches = [
            thread
            for thread in manifest["provider_threads"].values()
            if thread["backend"] == "codex"
            and thread["agent_id"] == PLOT_ARCHITECT
        ]
        if not matches:
            raise KeyError(f"codex:{PLOT_ARCHITECT}")
        return max(matches, key=lambda thread: thread["updated_at"])

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

    async def test_binding_stages_isolated_agents_and_turn_omits_static_prompt(self) -> None:
        session_id = "story_session"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        resources = provision_session_resources(self.workspace.root, session_id)
        definition = self.definition()
        bound = await backend.bind(definition)
        staged = (bound.staging_directory / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIsInstance(bound, BoundAgent)
        self.assertEqual(bound.vendor, "codex")
        self.assertNotEqual(bound.staging_directory, resources.identity.worktree_root)
        self.assertIn(definition.system_prompt_file.read_text(), staged)
        self.assertIn(definition.persona_file.read_text(), staged)
        with mock.patch("story_writing_agents.codex_adapter.run_process",
                        wraps=sys.modules["story_writing_agents.codex_adapter"].run_process) as run:
            reply = await bound.invoke(self.request(session_id=session_id))
        request = run.call_args.args[0]
        self.assertIsNone(request.cwd)
        self.assertEqual(request.execution_workspace.worktree_root, resources.identity.worktree_root)
        output = Path(request.command[request.command.index("--output-last-message") + 1])
        native_arguments = dict(session_root=resources.identity.worktree_root,
                                session_container=resources.identity.worktree_root.parent,
                                staging=bound.staging_directory.resolve(), output=output)
        self.assertEqual(backend._codex_adapter._native_state_directory(**native_arguments),
                         Path(os.environ["CODEX_HOME"]).resolve())
        for unsafe_state in ("relative", resources.identity.worktree_root,
                             resources.identity.worktree_root.parent, bound.staging_directory,
                             output.parent, self.workspace.root, self.fake_directory / "missing"):
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(unsafe_state)}):
                with self.assertRaisesRegex(ProviderInvocationError, "native state directory is unsafe") as unsafe:
                    backend._codex_adapter._native_state_directory(**native_arguments)
                self.assertFalse(unsafe.exception.taint_existing_thread)
        self.assertEqual(reply.content, "generated candidate")
        invocation = self.invocations()[0]
        argv = invocation["argv"]
        self.assertEqual(invocation["cwd"], str(resources.identity.worktree_root))
        self.assertEqual(argv[argv.index("--cd") + 1], ".")
        self.assertIn("read-only", argv)
        self.assertIn("project_root_markers=[]", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("developer_instructions=" + json.dumps(staged, ensure_ascii=False), argv)
        self.assertNotIn(definition.system_prompt_file.read_text(), invocation["raw_payload"])
        self.assertNotIn(definition.persona_file.read_text(), invocation["raw_payload"])
        self.assertEqual(sum(c.serialized_utf8_bytes for c in bound._provider_binding.binding_components), len(staged.encode()))
        self.assertFalse((resources.identity.worktree_root / "AGENTS.md").exists())
        root = resources.identity.worktree_root
        moved = root.with_name(root.name + "-moved")
        root.rename(moved)
        self.addCleanup(moved.rename, root)
        before = len(self.invocations())
        with self.assertRaises(SessionArtifactBindingError) as caught:
            await bound.invoke(self.request(session_id=session_id))
        self.assertEqual(str(caught.exception), "session artifact resources are unavailable")
        self.assertEqual(len(self.invocations()), before)











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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)

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

    def test_codex_usage_accepts_only_one_recognized_exact_terminal_snapshot(
        self,
    ) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        parse = backend._codex_adapter.provider_usage
        cases = (
            ((), None),
            (({"type": "turn.completed"},), None),
            (({"type": "turn.completed", "usage": {"unknown": 7}},), None),
            (
                (
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 2,
                            "cached_input_tokens": 3,
                        },
                    },
                ),
                ProviderUsage(0, 2, 3),
            ),
            (
                ({"type": "turn.completed", "usage": {"input_tokens": 4}},),
                ProviderUsage(input_tokens=4),
            ),
            (
                (
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": -1,
                            "cached_input_tokens": True,
                            "unknown": 9,
                        },
                    },
                ),
                ProviderUsage(input_tokens=5),
            ),
            (
                (
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": -1,
                            "output_tokens": "2",
                            "cached_input_tokens": False,
                        },
                    },
                ),
                None,
            ),
            (
                (
                    {"type": "turn.completed", "usage": {"input_tokens": 1}},
                    {"type": "turn.completed", "usage": {"input_tokens": 2}},
                ),
                None,
            ),
        )
        for events, expected in cases:
            with self.subTest(events=events):
                self.assertEqual(parse(events), expected)

    async def test_malformed_usage_never_invalidates_a_valid_provider_reply(
        self,
    ) -> None:
        session_id = "malformed_usage_reply"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = records.append
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
        malformed_usage = json.dumps(
            {
                "input_tokens": True,
                "output_tokens": -1,
                "cached_input_tokens": "3",
                "unknown": 4,
            },
            separators=(",", ":"),
        )

        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_USAGE": malformed_usage},
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())

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
                },
            )
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
        execution_workspace = await self.enterAsyncContext(
            bound._direct_codex_execution_workspace(session_id, None)
        )
        original_store_thread = backend._store_thread

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
            mock.patch.object(backend, "_store_thread", side_effect=store_thread),
        ):
            reply = await backend._invoke_provider_structured(
                bound,
                session_id=session_id,
                debate_id="timing_debate",
                schema={},
                payload={},
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
            await backend._invoke_provider_structured(
                bound,
                session_id=session_id,
                conversation_id="timing_conversation",
                schema={},
                payload={},
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

    async def test_contextual_physical_calls_use_canonical_payload_and_distinct_restartable_thread(self) -> None:
        session_id = "contextual_physical"
        backend = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(backend, session_id)
        definition = self.definition()
        bound = await backend.bind(definition)
        provider = bound._context_provider()
        scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "conversation-context", PLOT_ARCHITECT)
        policy = "sha256:" + "7" * 64
        payload, component_paths = compact_context_payload(_shared_context_payload(), {
            ("task_input",): ContextComponentKind.CURRENT_INPUT,
            ("session_history",): ContextComponentKind.CONVERSATION_CONTEXT,
        })
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
                first = await provider.invoke_context(ContextProviderCallRequest(
                    scope, policy, "context-call-1", ProviderCallKind.COLLABORATION_INITIAL,
                    schema, payload, component_paths))
                first.finalizer.accepted()
                second = await provider.invoke_context(ContextProviderCallRequest(
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
        with self.assertRaises(SessionArtifactBindingError):
            captured[0].execution_workspace.require_current()
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
        restarted = self._backend_with_fake_codex()
        self.addCleanup(restarted.close)
        restarted.acquire_session_access(session_id)
        restarted_provider = (await restarted.bind(definition))._context_provider()
        async with restarted_provider.invocation(scope, policy):
            self.assertEqual(restarted_provider.inspect_context_thread(scope, policy), first.thread_id)

    async def test_contextual_submitted_failure_taints_only_exact_context_thread(
        self,
    ) -> None:
        session_id = "contextual_failure"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                    ContextProviderCallRequest(
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
                        ContextProviderCallRequest(
                            scope,
                            policy,
                            "context-failure",
                            ProviderCallKind.DEBATE,
                            schema,
                            payload,
                            expected_thread_id=first.thread_id,
                        )
                    )
                facts = context_provider_failure_facts(caught.exception)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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

    async def test_hot_same_provider_profile_applies_now_and_reuses_thread(
        self,
    ) -> None:
        session_id = "hot_profile_session"
        correlation_id = "settings_save_hot_profile"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        before_configuration = backend_configuration_snapshot(backend)
        route_generations = backend.provider_route_generations()
        before_assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=before_configuration,
            provider_route_generations=route_generations,
        )
        before_definition = before_assembler.build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        before_request = before_assembler.build_request(
            session_id=session_id,
            debate_id="before_hot_apply",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r0",
            task="Draft the candidate.",
            user_payload="before",
            definition=before_definition,
            focused_context_mode=StoryFocusedContextMode.OMITTED,
        )
        before_bound = await backend.bind(before_definition)
        await before_bound.invoke(before_request)
        before_bound._retire()
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "confirmed",
        )

        next_agents = dict(before_configuration.agent_configs)
        next_agents[PLOT_ARCHITECT] = replace(
            next_agents[PLOT_ARCHITECT],
            codex_model="hot-model",
            codex_reasoning_effort="ultra",
        )
        after_configuration = ConfigurationSnapshot(
            before_configuration.system_defaults,
            next_agents,
        )
        provider_routes = {
            agent_id: ProviderRouteEntry(
                backend.execution_profile(agent_id).provider,
                route_generations[agent_id],
            )
            for agent_id in ROSTER
        }
        candidate = backend.stage_runtime_configuration(
            after_configuration,
            provider_routes,
            correlation_id=correlation_id,
        )
        mark = len(_workspace_debug_records(self.workspace))
        profiles = backend.commit_runtime_configuration(candidate)
        active = backend.execution_profile(PLOT_ARCHITECT)
        self.assertIs(active, profiles[ROSTER.index(PLOT_ARCHITECT)])
        self.assertEqual(
            (
                active.provider,
                active.model,
                active.model_source,
                active.effort,
                active.effort_source,
            ),
            (
                "codex",
                "hot-model",
                ExecutionValueSource.AGENT_CONFIG,
                "ultra",
                ExecutionValueSource.AGENT_CONFIG,
            ),
        )
        self.assertEqual(route_generations, backend.provider_route_generations())
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "pending",
        )
        self.assertEqual(
            backend.execution_profile_thread_status(PLOT_ARCHITECT),
            "reusable_pending",
        )
        transition_records = [
            record
            for record in _workspace_debug_records(self.workspace)[mark:]
            if record.get("schema_version") == 2
        ]
        self.assertEqual(
            [record["operation"] for record in transition_records],
            ["execution_setting_change", "execution_profile_applied"],
        )
        self.assertEqual(
            {record["correlation_id"] for record in transition_records},
            {correlation_id},
        )
        for record in transition_records:
            self.assertEqual(record["route_generation"], 0)
            self.assertEqual(record["old_profile"]["provider"], "codex")
            self.assertIsNone(record["old_profile"]["model"])
            self.assertEqual(record["new_profile"]["model"], "hot-model")
            self.assertEqual(record["new_profile"]["effort"], "ultra")

        after_assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=after_configuration,
            provider_route_generations=backend.provider_route_generations(),
        )
        after_definition = after_assembler.build_definition(
            session_id=session_id,
            agent_id=PLOT_ARCHITECT,
        )
        self.assertEqual(after_definition.fingerprint, before_definition.fingerprint)
        after_request = after_assembler.build_request(
            session_id=session_id,
            debate_id="after_hot_apply",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.DRAFT,
            round_number=0,
            revision_id="r1",
            task="Continue the candidate.",
            user_payload="after",
            definition=after_definition,
            focused_context_mode=StoryFocusedContextMode.OMITTED,
        )
        after_bound = await backend.bind(after_definition)
        reply = await after_bound.invoke(after_request)
        self.assertEqual(
            reply,
            replace(
                AgentReply.candidate("r1", "generated candidate"),
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="candidate",
                    context_sources=("current_input", "story_session"),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        self.assertEqual(
            backend.execution_profile_confirmation(PLOT_ARCHITECT),
            "confirmed",
        )
        self.assertEqual(
            backend.execution_profile_thread_status(PLOT_ARCHITECT),
            "reused",
        )
        confirmed = [
            record
            for record in _workspace_debug_records(self.workspace)[mark:]
            if record.get("operation") == "execution_profile_confirmed"
        ]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(
            {
                key: confirmed[0][key]
                for key in (
                    "correlation_id",
                    "route_generation",
                    "activation_state",
                    "thread_disposition",
                    "context_state",
                )
            },
            {
                "correlation_id": correlation_id,
                "route_generation": 0,
                "activation_state": "confirmed",
                "thread_disposition": "reused",
                "context_state": "rehydrated",
            },
        )
        argv = self.provider_invocations()[-1]["argv"]
        self.assertIn("hot-model", argv)
        self.assertIn('model_reasoning_effort="ultra"', argv)
        self.assertIn("resume", argv)

    async def test_backend_defaults_snapshot_refresh_and_thread_identity_are_exact(
        self,
    ) -> None:
        session_id = "defaults_snapshot"
        provision_session_resources(self.workspace.root, session_id)
        _mark_debug_session_attached(self.workspace)
        self.write_codex_defaults("persisted-one", "high")
        first_definition = self.definition()
        first = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(first, session_id)
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
        second = self._backend_with_fake_codex()
        self.addCleanup(second.close)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                    backend = self._backend_with_fake_codex()
                    self.addCleanup(backend.close)
                    self.create_provisioned_session_with_access(backend, session_id)
                    bound = await backend.bind(self.definition())
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
            backend = self._backend_with_fake_codex()
            self.addCleanup(backend.close)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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

    async def test_conversation_projects_exact_payload_schema_and_no_artifacts(
        self,
    ) -> None:
        session_id = "conversation_session"
        conversation_id = "conversation_a"
        prior_user, prior_reply = _conversation_pair(
            "turn_prior",
            session_id=session_id,
            conversation_id=conversation_id,
            user_content="prior visible question",
            outcome_content="prior visible answer",
        )
        current_user = _conversation_pair(
            "turn_current",
            session_id=session_id,
            conversation_id=conversation_id,
            user_content="current visible question",
        )[0]
        context = _context_window(
            (prior_user, prior_reply),
        )

        definition = self.definition()
        static_content = [
            reference.read_text() for reference in definition.file_references()
        ]
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(definition)
        request = self.conversation_request(
            session_id=session_id,
            conversation_id=conversation_id,
            content=current_user.content,
            context=context,
        )
        visible_reply = "  line\n\t\x00\x1b 한🙂  "
        raw_reply = json.dumps(
            {"message": visible_reply}
        )
        discussion_before = _tree_snapshot(self.workspace.path("discussion_logs"))
        core_before = core_snapshot(self.workspace)
        manuscripts_before = all_manuscripts(self.workspace)
        output_directory = self.fake_directory / "conversation-output"
        output_directory.mkdir()

        with mock.patch(
            "story_writing_agents.backend.tempfile.mkdtemp",
            return_value=str(output_directory),
        ):
            with mock.patch.dict(
                os.environ,
                {"STORY_FAKE_CODEX_OUTPUT": raw_reply},
            ):
                reply = await bound.converse(request)

        self.assertFalse(output_directory.exists())
        self.assertEqual(
            reply,
            ConversationReply(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id="turn_current",
                content=visible_reply,
                trace=ReplyTrace(
                    role=PLOT_ARCHITECT,
                    action="reply",
                    context_sources=("current_input", "conversation_history"),
                    recovered_fields=(),
                    origin=ReplyTraceOrigin.PROVIDER,
                ),
            ),
        )
        invocation = self.provider_invocations()[0]
        payload = invocation["payload"]
        self.assertEqual(
            set(payload),
            {
                "session_id",
                "conversation_id",
                "turn_id",
                "agent_id",
                "user_input_text",
                "conversation_history",
                "protocol_version",
                "contract_guidance",
            },
        )
        self.assertEqual(
            tuple(payload[field] for field in tuple(payload)[:5]),
            (
                session_id,
                conversation_id,
                "turn_current",
                PLOT_ARCHITECT,
                current_user.content,
            ),
        )
        context_fields = ("turn_id", "sender", "recipient", "message_type", "content_text")
        self.assertTrue(
            all(tuple(item) == context_fields for item in payload["conversation_history"])
        )
        self.assertEqual(
            [tuple(item.values()) for item in payload["conversation_history"]],
            [
                (
                    event.turn_id,
                    event.sender,
                    event.recipient,
                    event.kind.value,
                    event.content,
                )
                for event in (prior_user, prior_reply)
            ],
        )
        schema = invocation["schema"]
        self.assertEqual(set(schema["properties"]), {"response_type", "response_text"})
        self.assertEqual(schema["required"], ["response_type", "response_text"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["response_type"]["enum"], ["conversation_reply"])
        self.assertEqual(schema["properties"]["response_text"]["minLength"], 1)
        assert_guidance(self, payload, schema)
        payload_text = json.dumps(payload, ensure_ascii=False)
        for content in static_content:
            if content:
                self.assertNotIn(content, payload_text)

        self.assertEqual(
            _tree_snapshot(self.workspace.path("discussion_logs")),
            discussion_before,
        )
        self.assertEqual(core_snapshot(self.workspace), core_before)
        self.assertEqual(all_manuscripts(self.workspace), manuscripts_before)
        self.assertEqual(
            request.context.events,
            (prior_user, prior_reply),
        )
        self.assertEqual(self.workspace.inspect_session(session_id)["debates"], [])
        from story_writing_agents.agent_context.service import (
            AgentContextService, ContextInvocationKind, ContextInvocationRequest)
        from story_writing_agents.agent_context.sources import (
            ActiveDebateSourceAdapter, FocusedConversationSourceAdapter,
            OwnAgentStateSourceAdapter, WritingArtifactSourceAdapter)
        from story_writing_agents.conversation_storage import (
            compose_filesystem_conversation_storage)
        from story_writing_agents.lead_provider_contracts import (
            LeadExplorationConstraints, LeadProviderConstraints, LeadResultRuleSeed,
            PremiseDecisionConstraints)
        from story_writing_agents.orchestrator import (
            LeadContractError, OrchestrationLeadCallFactory)
        from tests.provider_wire_fixture import lead_fixture_response, lead_fixture_result
        from tests.test_agent_context_execution import (
            ContextAuthorityKind, ContextPriority, ContextSemanticChannel, ContextSourceKind,
            _material, _materials)

        service = AgentContextService(self.workspace)
        storage = compose_filesystem_conversation_storage(self.workspace)
        repository = WritingArtifactRepository(self.workspace.root)

        async def bound_call(call_id, family, summary=None):
            nonlocal schema, payload, response
            authored = lead_fixture_result(json.loads(response)["agent_action"]["task_result"])
            inputs = tuple(item["input_id"] for item in payload.get("inputs", ()))
            if family == "lead-exploration-response-v1":
                authored["response_details"]["ideas"][0]["basis_input_ids"] = {}
            result_constraints = (LeadExplorationConstraints() if summary is None else
                PremiseDecisionConstraints("native-question", input_ids=("user_input",),
                    reply_alternatives=(("native-reply", ("native-alternative",)),)))

            def authorize(answer, _authorization):
                if summary is None:
                    findings = result_constraints.diagnose(answer)
                    if findings:
                        raise LeadContractError(findings)
                elif summary:
                    result_constraints.validate_summary(json.loads(answer))
                else:
                    result_constraints.validate_analysis(json.loads(answer))

            factory = OrchestrationLeadCallFactory(
                LeadProviderConstraints(input_ids=inputs, source_ids=inputs),
                LeadResultRuleSeed.declare(family, schema), ContextInvocationKind.LEAD.value,
                definition.language, authorize, StoryRuntime._core_reviewer)
            materials = _materials(payload)
            current = service.scope(scope.session_id, scope.subject_kind, scope.subject_id,
                LEAD_ORCHESTRATOR)
            returned = None

            class ObservedProvider:
                def __getattr__(self, name):
                    return getattr(provider, name)

                async def invoke_context(self, request):
                    nonlocal schema, payload, response, returned
                    schema, payload = request.schema, request.payload
                    response = lead_fixture_response(
                        request, authored, family=1 if summary is False else 0)
                    returned = await provider.invoke_context(request)
                    return returned

            request = ContextInvocationRequest(session_id=current.session_id,
                subject_kind=current.subject_kind, subject_id=current.subject_id,
                agent_id=LEAD_ORCHESTRATOR, operation_id=call_id, result_correlation_id=call_id,
                materials=materials, result_contract=factory,
                invocation_kind=ContextInvocationKind.LEAD,
                physical_call_limit=1)
            await service.invoke(request, ObservedProvider(),
                writing_artifacts=WritingArtifactSourceAdapter(repository, current),
                focused_conversation=FocusedConversationSourceAdapter(
                    storage.catalog, storage.event_store, current, None),
                active_debate=ActiveDebateSourceAdapter(self.workspace, current, None),
                own_agent_state=OwnAgentStateSourceAdapter(self.workspace, current))
            return returned

        schema, payload = _exploration_native_contract()
        definition = self.definition(LEAD_ORCHESTRATOR)
        provider = (await backend.bind(definition))._context_provider()
        scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "native-exploration", LEAD_ORCHESTRATOR)
        response, invocations = _exploration_outcome("Native provisional 한글 possibility.", basis_input_ids=()), []
        async def invoke(_binding, invocation):
            invocations.append(invocation)
            return ProviderResult("codex-exploration-thread", response, usage=ProviderUsage(input_tokens=1, output_tokens=1), actual_transport_utf8_bytes=len(invocation.payload_json))
        with mock.patch.object(backend._codex_adapter, "invoke", new=invoke):
            explored = await bound_call(
                "codex-exploration-parity-call", "lead-exploration-response-v1")
        self.assertEqual((invocations[0].schema, invocations[0].payload_json), (schema, canonical_provider_json(payload)))
        self.assertEqual((explored.response_text, explored.accounting.state), (response, ProviderCallState.SUBMITTED))


        for summary in (False, True):
            schema, payload, response = _premise_native_contract(summary)
            native_calls = []
            async def premise_invoke(binding, invocation):
                native_calls.append(invocation)
                return ProviderResult("native-premise-" + str(summary), response, usage=ProviderUsage(input_tokens=1, output_tokens=1), actual_transport_utf8_bytes=len(invocation.payload_json))
            scope = ContextScope("workspace-context", session_id, ContextSubjectKind.CONVERSATION, "native-premise-" + str(summary), LEAD_ORCHESTRATOR)
            with mock.patch.object(backend._codex_adapter, "invoke", new=premise_invoke):
                returned = await bound_call("native-premise-call-" + str(summary),
                    "premise-decision-summary-v1" if summary else "premise-analysis-response-v1",
                    summary)
            self.assertEqual((native_calls[0].schema, native_calls[0].payload_json), (schema, canonical_provider_json(payload)))
            self.assertEqual((returned.response_text, returned.accounting.state), (response, ProviderCallState.SUBMITTED))

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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "snapshot_session")
        await (await backend.bind(self.definition())).converse(request)

        payload = self.provider_invocations()[0]["payload"]
        self.assertEqual(payload["conversation_history"][0]["content_text"], validated)
        self.assertNotIn(unvalidated, json.dumps(payload))

    async def test_conversation_thread_spans_subjects_and_restarts_on_fingerprint(
        self,
    ) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "story_session")
        definition = self.definition()
        bound = await backend.bind(definition)
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
        expected = execution_workspace.continuation_digest("legacy-definition-policy", hashlib.sha256(("agent-provider-wire-v10\0" + incompatible_bound.definition.fingerprint).encode()).hexdigest()).removeprefix("sha256:")
        threads = self.workspace.read_json("agent_states/orchestrator_state/provider_sessions/story_session.json")["provider_threads"]
        self.assertEqual((threads[f"codex:{PLOT_ARCHITECT}:{expected}"]["fingerprint"],
                          self.thread("story_session")["fingerprint"]), (expected, expected))

    async def test_invoke_and_converse_share_one_serialized_thread(self) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "mixed_session")
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
        first = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(first, "story_session")
        await (await first.bind(definition)).invoke(
            self.request(debate_id="debate_one")
        )
        first.close()

        second = self._backend_with_fake_codex()
        self.addCleanup(second.close)
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

    async def test_a_to_b_to_a_route_generation_never_resumes_old_a_threads_after_restart(
        self,
    ) -> None:
        session_ids = ("route_session_one", "route_session_two")
        first = self._backend_with_fake_codex()
        legacy_definition = self.definition()
        for index, session_id in enumerate(session_ids, start=1):
            self.create_provisioned_session_with_access(first, session_id)
            await (await first.bind(legacy_definition)).invoke(
                self.request(
                    session_id=session_id,
                    debate_id=f"legacy_{index}",
                )
            )
        legacy_fingerprints = {
            session_id: self.thread(session_id)["fingerprint"]
            for session_id in session_ids
        }
        first.close()

        antigravity = update_agent_backend(
            load_settings_state(self.workspace),
            PLOT_ARCHITECT,
            "antigravity",
        )
        commit_prepared_settings_save(
            self.workspace,
            prepare_settings_save(self.workspace, antigravity),
        )
        codex_again = update_agent_backend(
            load_settings_state(self.workspace),
            PLOT_ARCHITECT,
            "codex",
        )
        commit_prepared_settings_save(
            self.workspace,
            prepare_settings_save(self.workspace, codex_again),
        )

        restarted = self._backend_with_fake_codex()
        self.addCleanup(restarted.close)
        self.assertEqual(
            restarted.provider_route_generations()[PLOT_ARCHITECT],
            2,
        )
        assembler = PromptAssembler(
            self.workspace,
            configuration_snapshot=backend_configuration_snapshot(restarted),
            provider_route_generations=restarted.provider_route_generations(),
        )
        current_definition = assembler.build_definition(
            session_id=session_ids[0],
            agent_id=PLOT_ARCHITECT,
        )
        self.assertNotEqual(
            current_definition.fingerprint,
            legacy_definition.fingerprint,
        )
        for index, session_id in enumerate(session_ids, start=1):
            restarted.acquire_session_access(session_id)
            definition = assembler.build_definition(
                session_id=session_id,
                agent_id=PLOT_ARCHITECT,
            )
            request = assembler.build_request(
                session_id=session_id,
                debate_id=f"after_return_{index}",
                agent_id=PLOT_ARCHITECT,
                phase=Phase.DRAFT,
                round_number=0,
                revision_id=f"r{index}",
                task="Continue without resuming the pre-switch provider thread.",
                user_payload="rehydrated session input",
                definition=definition,
                focused_context_mode=StoryFocusedContextMode.OMITTED,
            )
            await (await restarted.bind(definition)).invoke(request)

        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 4)
        self.assertTrue(
            all("resume" not in invocation["argv"] for invocation in invocations)
        )
        for session_id in session_ids:
            self.assertNotEqual(
                self.thread(session_id)["fingerprint"],
                legacy_fingerprints[session_id],
            )

    async def test_threads_are_separate_by_agent_and_story_session(self) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "session_one")
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, "shared_session")
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
        first = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(first, "story_session")
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
        first = self._backend_with_fake_codex()
        self.create_provisioned_session_with_access(first, "resume_only_session")
        await (await first.bind(definition)).invoke(
            self.request(session_id="resume_only_session")
        )
        first.pause_session_and_release_access("resume_only_session")
        first.close()

        resumed = self._backend_with_fake_codex()
        self.addCleanup(resumed.close)
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
            self.assertEqual(AgentBackend._decode_reply(phase, direct_reply(value, phase.value), revision_id="r2"), expected)
        request = AgentRequest("session", "debate", "lead_orchestrator", Phase.FINALIZE, 1, "r2", "Finalize.", "")
        self.assertIsNone(request.expected_reviewed_proposal)
        self.assertTrue(next(field for field in fields(AgentRequest) if field.name == "expected_reviewed_proposal").kw_only)
        request = replace(request, expected_reviewed_proposal=identity)
        self.assertEqual(request.expected_reviewed_proposal, identity)
        with self.assertRaises(ValueError):
            replace(request, phase=Phase.DRAFT)
        schema = AgentBackend._output_schema(Phase.FINALIZE, expected_reviewed_proposal=identity)
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
                AgentBackend._decode_reply(Phase.FINALIZE, json.dumps({"reviewed_proposal_id": identifier,
                    "reviewed_proposal_digest": invalid_digest}), revision_id="r2")
            self.assertNotIn("unsafe", str(caught.exception))
        for invalid_identity in ([*identity], (identity[0],), (identity[0], digest, "extra")):
            with self.assertRaises((TypeError, ValueError)):
                replace(request, expected_reviewed_proposal=invalid_identity)
        with self.assertRaises(ValueError):
            AgentBackend._output_schema(Phase.DRAFT, expected_reviewed_proposal=identity)
        self.assertEqual(AgentBackend._decode_reply(Phase.DRAFT, direct_reply({"message": " \t"}, "draft"), revision_id="r2").content, " \t")
        invalid_values = ((Phase.AUDIT, '{"verdict":"maybe","message":""}'),
            (Phase.SYNTHESIZE, '{"next":"unknown","message":"ambiguous"}'),
            (Phase.SYNTHESIZE, '{"next":"deadlock","message":"blocked","extra":true}'),
            (Phase.DRAFT, '{"verdict":"approve","message":"ok"}'),
            (Phase.DRAFT, '{"message":"one","message":"two"}'))
        for phase, value in invalid_values:
            with self.subTest(phase=phase, value=value):
                with self.assertRaises(BackendProtocolError):
                    AgentBackend._decode_reply(phase, value, revision_id="r2")
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
                AgentBackend._decode_reply(phase, direct_reply(value, phase.value), revision_id="r2")
            issues = failure_issues(caught.exception) or (getattr(caught.exception, "issue", None),)
            self.assertEqual(issues, ("$: expected exact current response_type and schema fields" if ((phase is Phase.AUDIT and value.get("verdict") == "PRIVATE") or (phase is Phase.SYNTHESIZE and value.get("next") == "PRIVATE")) else issue,))
            self.assertNotIn("PRIVATE", repr(issues))
        review_schema = AgentBackend._output_schema(Phase.SYNTHESIZE, review_only=True)
        self.assertEqual(review_schema["properties"]["next_consensus_step"]["enum"], ["complete_review", "report_deadlock"])
        with self.assertRaises(ValueError):
            AgentBackend._output_schema(Phase.DRAFT, review_only=True)

    async def test_rejected_debate_attempts_return_phase_safe_deadlocks(self) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, 'routing_session')
        definition = self.definition()
        bound = await backend.bind(definition)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                self.assertIn("no action was taken", reply.content)
                self.assertEqual(reply.session_id, session_id)
                self.assertEqual(reply.conversation_id, "conversation_a")
                self.assertEqual(reply.turn_id, "turn_failed")
                self.assertIs(reply.trace.origin, ReplyTraceOrigin.RUNTIME_FALLBACK)
                self.assertEqual(reply.trace.action, "none")
                self.assertEqual(reply.trace.recovered_fields, ("structured_reply",))
                self.assertIs(self.thread(session_id)["tainted"], True)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                     json.dumps({"message": "Repaired."})]
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

    async def test_malformed_direct_json_keeps_terminal_code_and_safe_detail(self) -> None:
        session_id = "malformed_terminal_code"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        definition = self.definition()
        bound = await backend.bind(definition)
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
                backend._store_thread(
                    session_id,
                    definition,
                    thread_id,
                    tainted=False,
                    fingerprint=continuation_fingerprint,
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
                    _wrapped_thread_taint_patch(backend, backend._taint_thread) as taint,
                    mock.patch.object(
                        backend,
                        "_write_manifest",
                        wraps=backend._write_manifest,
                    ) as write_manifest,
                    _wrapped_thread_store_patch(backend, backend._store_thread) as store_thread,
                ):
                    with self.assertRaises(BackendProtocolError) as caught:
                        await backend._invoke_provider_structured(
                            bound,
                            session_id=session_id,
                            schema={},
                            payload={},
                            decode=fail_decode,
                            execution_workspace=execution_workspace,
                        )

                invoke.assert_awaited_once()
                taint.assert_called_once_with(
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        definition = self.definition()
        bound = await backend.bind(definition)
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
                backend._store_thread(
                    session_id,
                    definition,
                    thread_id,
                    tainted=False,
                    fingerprint=continuation_fingerprint,
                )
                invoke = mock.AsyncMock(side_effect=provider_error)
                decode = mock.Mock()
                with (
                    mock.patch.object(backend._codex_adapter, "invoke", invoke),
                    _wrapped_thread_taint_patch(backend, backend._taint_thread) as taint,
                    mock.patch.object(
                        backend,
                        "_write_manifest",
                        wraps=backend._write_manifest,
                    ) as write_manifest,
                    _wrapped_thread_store_patch(backend, backend._store_thread) as store_thread,
                ):
                    with self.assertRaises(BackendProtocolError) as caught:
                        await backend._invoke_provider_structured(
                            bound,
                            session_id=session_id,
                            schema={},
                            payload={},
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
                        session_id,
                        definition,
                        fingerprint=continuation_fingerprint,
                    )
                    write_manifest.assert_called_once()
                else:
                    taint.assert_not_called()
                    write_manifest.assert_not_called()

    async def test_conversation_provider_failures_taint_without_story_mutation(
        self,
    ) -> None:
        session_id = "transaction_failure"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
        discussion_before = _tree_snapshot(self.workspace.path("discussion_logs"))
        core_before = core_snapshot(self.workspace)
        manuscripts_before = all_manuscripts(self.workspace)
        await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="turn_seed")
        )
        cases = (
            "nonzero",
            "invalid_event_utf8",
            "missing_output",
        )
        for index, mode in enumerate(cases):
            with self.subTest(mode=mode):
                output_directory = self.fake_directory / f"failure-{index}"
                temporary = contextlib.nullcontext()
                if mode == "missing_output":
                    output_directory.mkdir()
                    temporary = mock.patch(
                        "story_writing_agents.backend.tempfile.mkdtemp",
                        return_value=str(output_directory),
                    )
                with temporary:
                    with mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_MODE": mode},
                    ):
                        with self.assertRaises(BackendProtocolError):
                            await bound.converse(
                                self.conversation_request(
                                    session_id=session_id,
                                    turn_id=f"turn_failed_{index}",
                                )
                            )
                if mode == "missing_output":
                    self.assertFalse(output_directory.exists())
                self.assertIs(self.thread(session_id)["tainted"], True)
                await bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id=f"turn_recovery_{index}",
                    )
                )

        self.assertEqual(
            _tree_snapshot(self.workspace.path("discussion_logs")),
            discussion_before,
        )
        self.assertEqual(core_snapshot(self.workspace), core_before)
        self.assertEqual(all_manuscripts(self.workspace), manuscripts_before)

    async def test_conversation_cancellation_taints_then_restarts_cleanly(
        self,
    ) -> None:
        session_id = "cancel_conversation"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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

    async def test_missing_executable_nonzero_and_malformed_output_fail_cleanly(
        self,
    ) -> None:
        cases = (
            (str(self.fake_directory / "missing"), None, "was not found"),
            (str(self.executable), "nonzero", "status 7"),
            (str(self.executable), "malformed", "malformed structured output"),
        )
        for index, (executable, mode, message) in enumerate(cases):
            with self.subTest(mode=mode):
                session = f"failure_session_{index}"
                backend = AgentBackend(self.workspace, executable=executable)
                accounting_records: list[ProviderCallAccounting] = []
                backend._context_accounting_sink = accounting_records.append
                self.create_provisioned_session_with_access(backend, session)
                bound = await backend.bind(self.definition())
                environment = (
                    contextlib.nullcontext()
                    if mode is None
                    else mock.patch.dict(
                        os.environ,
                        {"STORY_FAKE_CODEX_MODE": mode},
                    )
                )
                with environment:
                    if mode == "malformed":
                        reply = await bound.invoke(self.request(session_id=session))
                        self.assertIn("after one repair attempt", reply.content)
                        caught = None
                    else:
                        with self.assertRaisesRegex(
                            BackendProtocolError,
                            message,
                        ) as raised:
                            await bound.invoke(self.request(session_id=session))
                        caught = raised
                if caught is not None:
                    self.assertNotIn("dynamic user input", str(caught.exception))
                self.assertEqual(
                    len(accounting_records),
                    2 if mode == "malformed" else 1,
                )
                expected_state = (
                    ProviderCallState.PREPARED
                    if mode is None
                    else ProviderCallState.SUBMITTED
                )
                self.assertTrue(
                    all(record.state is expected_state for record in accounting_records)
                )
                if expected_state is ProviderCallState.PREPARED:
                    self.assertIsNone(accounting_records[0].actual_transport_utf8_bytes)
                else:
                    self.assertGreater(
                        accounting_records[0].actual_transport_utf8_bytes or 0,
                        0,
                    )
                backend.close()



    async def test_timeout_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
            timeout_seconds=0.3,
        )
        self.addCleanup(backend.close)
        accounting_records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = accounting_records.append
        self.create_provisioned_session_with_access(backend, "timeout_session")
        bound = await backend.bind(self.definition())
        real_wait_for = asyncio.wait_for
        communication_pending = True
        native_failures = []
        native_invoke = backend._codex_adapter.invoke

        async def observe_native_failure(*args, **kwargs):
            try:
                return await native_invoke(*args, **kwargs)
            except ProviderInvocationError as failure:
                native_failures.append(failure)
                raise

        async def wait_for_after_provider_ready(awaitable, timeout):
            nonlocal communication_pending
            if communication_pending:
                communication_pending = False
                communication = asyncio.ensure_future(awaitable)
                await self.wait_for_provider_invocations(1)
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
            mock.patch.object(backend._codex_adapter, "invoke", new=observe_native_failure),
        ):
            with self.assertRaisesRegex(BackendProtocolError, "timed out") as caught:
                await bound.invoke(self.request(session_id="timeout_session"))
        self.assertEqual(len(native_failures), 1)
        self.assertIsNone(native_failures[0].continuation_rejection)
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )
        self.assertEqual(len(accounting_records), 1)
        self.assertIs(accounting_records[0].state, ProviderCallState.SUBMITTED)
        self.assertGreater(accounting_records[0].actual_transport_utf8_bytes or 0, 0)

    async def test_cancellation_terminates_the_codex_process(self) -> None:
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        accounting_records: list[ProviderCallAccounting] = []
        backend._context_accounting_sink = accounting_records.append
        self.create_provisioned_session_with_access(backend, "cancel_session")
        bound = await backend.bind(self.definition())
        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ):
            invocation = asyncio.create_task(
                bound.invoke(self.request(session_id="cancel_session"))
            )
            await self.wait_for_provider_invocations(1)
            invocation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await invocation
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )
        self.assertEqual(len(accounting_records), 1)
        self.assertIs(accounting_records[0].state, ProviderCallState.SUBMITTED)
        self.assertGreater(accounting_records[0].actual_transport_utf8_bytes or 0, 0)

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
        process.terminate.side_effect = lambda: events.append("terminate")
        process.kill.side_effect = lambda: events.append("kill")
        waits = iter((first_wait, final_wait))

        def wait():
            events.append("wait")
            return next(waits)

        process.wait.side_effect = wait

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
        process.terminate.side_effect = lambda: events.append("terminate")
        process.kill.side_effect = lambda: events.append("kill")
        waits = iter((first_wait, final_wait))

        def wait():
            events.append("wait")
            return next(waits)

        process.wait.side_effect = wait

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
        process.terminate.side_effect = lambda: events.append("terminate")
        process.kill.side_effect = lambda: events.append("kill")
        waits = iter((first_wait, final_wait))

        def wait():
            events.append("wait")
            return next(waits)

        process.wait.side_effect = wait
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
            AgentBackend._validate_thread(
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
        definition = self.definition()
        bound = await backend.bind(definition)
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

    def test_retrieval_rag_context_is_first_call_only_and_reference_authoritative(
        self,
    ) -> None:
        contexts = (_provider_rag_context(), ConversationRagContext.disabled())
        for context in contexts:
            with self.subTest(outcome=context.outcome):
                request = _retrieval_request(rag_context=context)
                initial = AgentBackend._conversation_retrieval_initial_payload(
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
                    AgentBackend._conversation_retrieval_continuation_payload(
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
            AgentBackend._conversation_retrieval_initial_payload(
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

        initial = AgentBackend._conversation_retrieval_initial_payload(
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

        continuation = AgentBackend._conversation_retrieval_continuation_payload(
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
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
            AgentBackend._conversation_retrieval_allowed_actions(
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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

    def test_retrieval_schema_uses_supported_strict_object_subset(self) -> None:
        schema = AgentBackend._conversation_retrieval_action_schema()
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

    def test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors(
        self,
    ) -> None:
        request = _retrieval_request()
        valid_search = _needle_search(request)
        valid_read = _read_first_search_hit(request)
        valid_answer = _retrieval_action_json(
            request,
            kind=ConversationRetrievalActionKind.ANSWER,
            action_index=3,
            content="submit_result",
            citation_ids=["s1h1"],
        )
        decoded_actions = tuple(
            AgentBackend._decode_conversation_retrieval_action(
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
                    AgentBackend._decode_conversation_retrieval_action(payload, request=request, action_index=1)
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
            AgentBackend._decode_conversation_retrieval_action(
                json.dumps({"agent_action": reversed_range}),
                request=request,
                action_index=1,
            )
        duplicate_citations = json.loads(
            _retrieval_action_json(
                request,
                kind=ConversationRetrievalActionKind.ANSWER,
                action_index=1,
                content="submit_result",
                citation_ids=["s1h1", "s1h1"],
            )
        )
        with self.assertRaises(ConversationRetrievalSemanticError):
            AgentBackend._decode_conversation_retrieval_action(
                json.dumps(duplicate_citations),
                request=request,
                action_index=1,
            )

    def test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence(self) -> None:
        request = _retrieval_request()
        action = {"kind": "read_history", "citation_id": "s1h1", "start_sequence": 1}
        decoded = AgentBackend._decode_conversation_retrieval_action(
            direct_response(json.dumps({"action": action}), {"request_type": "answer_with_conversation_history"}), request=request, action_index=2)
        self.assertIs(type(decoded), ConversationRetrievalReadAction)
        self.assertIsNone(decoded.end_sequence)
        explicit_null = direct_response(json.dumps({"action": {**action, "end_sequence": None}}), {"request_type": "answer_with_conversation_history"})
        self.assertEqual(diagnose_json_text(explicit_null, AgentBackend._conversation_retrieval_action_schema(
            allowed_actions=(ConversationRetrievalActionKind.READ_HISTORY,))),
            ("$.agent_action.end_sequence_number: expected integer, got null",))
        with self.assertRaises(BackendProtocolError):
            AgentBackend._decode_conversation_retrieval_action(explicit_null, request=request, action_index=2)
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
                AgentBackend._decode_conversation_retrieval_action(direct_response(json.dumps({"action": value}), {"request_type": "answer_with_conversation_history"}), request=request, action_index=2)
            issues = failure_issues(caught.exception)
            self.assertEqual(issues, (f"$.action.{path}: {rule}",))
            self.assertNotIn("PRIVATE", repr(issues))

    def test_retrieval_non_string_kind_reports_exact_type_and_common_issues(self) -> None:
        secret = "PRIVATE_NON_STRING_RETRIEVAL_KIND_한🙂"
        schema = AgentBackend._conversation_retrieval_action_schema(
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

    async def test_retrieval_handler_declaration_and_exact_outcome_protocol(self) -> None:
        session_id = "retrieval_handler_session"
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
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
            backend = self._backend_with_fake_codex()
            self.addCleanup(backend.close)
            self.create_provisioned_session_with_access(backend, session_id)
            bound = await backend.bind(self.definition())
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

    async def test_retirement_defers_exact_cleanup_for_each_bound_operation(
        self,
    ) -> None:
        """Every provider operation retains staging through its final await."""

        cases = ("invoke", "converse", "retrieval")
        for label in cases:
            with self.subTest(operation=label):
                session_id = f"retire_{label}"
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
                staging = bound.staging_directory
                entered = asyncio.Event()
                proceed = asyncio.Event()
                observed_workspaces: list[ProviderExecutionWorkspace] = []

                async def provider(
                    *args: object,
                    reference_read: object | None = None,
                    execution_workspace: ProviderExecutionWorkspace | None = None,
                ) -> object:
                    self.assertIsNone(reference_read)
                    self.assertIsNotNone(execution_workspace)
                    assert execution_workspace is not None
                    observed_workspaces.append(execution_workspace)
                    execution_workspace.require_current()
                    entered.set()
                    await proceed.wait()
                    execution_workspace.require_current()
                    if label == "invoke":
                        return AgentReply.candidate("r0", "candidate")
                    request = args[1]
                    if label == "converse":
                        assert isinstance(request, ConversationRequest)
                        return ConversationReply(
                            request.session_id,
                            request.conversation_id,
                            request.turn_id,
                            "conversation",
                        )
                    assert isinstance(request, ConversationRetrievalRequest)
                    return ConversationRetrievalReply(
                        session_id=request.session_id,
                        conversation_id=request.conversation_id,
                        turn_id=request.turn_id,
                        content="retrieval",
                        sources=(),
                    )

                if label == "invoke":
                    provider_name = "_invoke_codex"

                    def call():
                        return bound.invoke(self.request(session_id=session_id))
                elif label == "converse":
                    provider_name = "_converse_codex"

                    def call():
                        return bound.converse(
                            self.conversation_request(session_id=session_id)
                        )
                else:
                    provider_name = "_converse_with_retrieval_codex"

                    def call():
                        return bound.converse_with_retrieval(
                            self.retrieval_request(session_id=session_id),
                            _RecordingRetrievalHandler(),
                        )

                with (
                    mock.patch.object(backend, provider_name, side_effect=provider),
                    mock.patch.object(
                        backend,
                        "_finalize_bound_agent",
                        wraps=backend._finalize_bound_agent,
                    ) as finalize,
                ):
                    active = asyncio.create_task(call())
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    bound._retire()
                    self.assertTrue(staging.exists())
                    self.assertIn(bound, backend._bound_agents)
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        "retired",
                    ):
                        await call()
                    proceed.set()
                    await asyncio.wait_for(active, timeout=1)

                with self.assertRaises(SessionArtifactBindingError):
                    observed_workspaces[0].require_current()
                self.assertEqual(len(observed_workspaces), 1)

                self.assertFalse(staging.exists())
                self.assertNotIn(bound, backend._bound_agents)
                self.assertNotIn(staging, backend._staging_directories)
                self.assertEqual(finalize.call_count, 1)

    async def test_failure_and_cancellation_release_retired_staging_once(
        self,
    ) -> None:
        for operation in ("invoke", "converse", "retrieval"):
            for outcome in ("failure", "cancellation"):
                with self.subTest(operation=operation, outcome=outcome):
                    session_id = f"retire_{operation}_{outcome}"
                    backend = self._backend_with_fake_codex()
                    self.addCleanup(backend.close)
                    self.create_provisioned_session_with_access(backend, session_id)
                    bound = await backend.bind(self.definition())
                    staging = bound.staging_directory
                    entered = asyncio.Event()
                    proceed = asyncio.Event()

                    async def provider(*_args: object, reference_read: object | None = None,
                                       execution_workspace: ProviderExecutionWorkspace | None = None) -> object:
                        self.assertIsNone(reference_read)
                        self.assertIsInstance(execution_workspace, ProviderExecutionWorkspace)
                        execution_workspace.require_current()
                        entered.set()
                        await proceed.wait()
                        raise RuntimeError("provider failed")

                    if operation == "invoke":
                        provider_name = "_invoke_codex"

                        def call():
                            return bound.invoke(self.request(session_id=session_id))
                    elif operation == "converse":
                        provider_name = "_converse_codex"

                        def call():
                            return bound.converse(
                                self.conversation_request(session_id=session_id)
                            )
                    else:
                        provider_name = "_converse_with_retrieval_codex"

                        def call():
                            return bound.converse_with_retrieval(
                                self.retrieval_request(session_id=session_id),
                                _RecordingRetrievalHandler(),
                            )

                    with (
                        mock.patch.object(
                            backend,
                            provider_name,
                            side_effect=provider,
                        ),
                        mock.patch.object(
                            backend,
                            "_finalize_bound_agent",
                            wraps=backend._finalize_bound_agent,
                        ) as finalize,
                    ):
                        active = asyncio.create_task(call())
                        await asyncio.wait_for(entered.wait(), timeout=1)
                        bound._retire()
                        self.assertTrue(staging.exists())
                        if outcome == "cancellation":
                            active.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await active
                        else:
                            proceed.set()
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "provider failed",
                            ):
                                await asyncio.wait_for(active, timeout=1)
                        bound._retire()

                    self.assertFalse(staging.exists())
                    self.assertEqual(finalize.call_count, 1)

    async def test_close_waits_for_active_binding_then_cleans_every_resource_once(
        self,
    ) -> None:
        backend = self._backend_with_fake_codex()
        session_id = "close_active_binding"
        self.create_provisioned_session_with_access(backend, session_id)
        active_bound = await backend.bind(self.definition())
        idle_bound = await backend.bind(self.definition(CHARACTER_CUSTODIAN))
        active_staging = active_bound.staging_directory
        idle_staging = idle_bound.staging_directory
        entered = asyncio.Event()
        proceed = asyncio.Event()

        async def provider(
            *_args: object,
            reference_read: object | None = None,
            execution_workspace: object | None = None,
        ) -> AgentReply:
            self.assertIsNone(reference_read)
            self.assertIsNotNone(execution_workspace)
            entered.set()
            await proceed.wait()
            return AgentReply.candidate("r0", "candidate")

        with (
            mock.patch.object(backend, "_invoke_codex", side_effect=provider),
            mock.patch.object(
                backend._codex_adapter,
                "release",
                wraps=backend._codex_adapter.release,
            ) as release_binding,
            mock.patch.object(
                backend,
                "_release_access_claim",
                wraps=backend._release_access_claim,
            ) as release_access,
        ):
            task = asyncio.create_task(
                active_bound.invoke(self.request(session_id=session_id))
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            backend.close()
            backend.close()

            self.assertTrue(backend._closed)
            self.assertFalse(backend._close_cleanup_complete)
            self.assertTrue(active_staging.exists())
            self.assertFalse(idle_staging.exists())
            self.assertIn(session_id, backend._access_claim_tokens)
            self.assertTrue(backend._thread_locks)
            with self.assertRaisesRegex(BackendProtocolError, "closed"):
                await active_bound.invoke(self.request(session_id=session_id))

            proceed.set()
            await asyncio.wait_for(task, timeout=1)
            backend.close()

        self.assertTrue(backend._close_cleanup_complete)
        self.assertEqual(backend._bound_agents, set())
        self.assertEqual(backend._staging_directories, set())
        self.assertEqual(backend._thread_locks, {})
        self.assertEqual(backend._access_claim_tokens, {})
        self.assertFalse(active_staging.exists())
        self.assertEqual(
            [
                call.args[0].staging_directory
                for call in release_binding.call_args_list
            ].count(active_staging),
            1,
        )
        self.assertEqual(
            [
                call.args[0].staging_directory
                for call in release_binding.call_args_list
            ].count(idle_staging),
            1,
        )
        self.assertEqual(release_access.call_count, 1)

    async def test_terminal_backend_rejects_call_before_binding_retirement(
        self,
    ) -> None:
        """Close's terminal flag prevents entry during its retirement gap."""

        backend = self._backend_with_fake_codex()
        bound = await backend.bind(self.definition())
        staging = bound.staging_directory

        # Model the exact synchronous close gap: close has made the backend
        # terminal but has not yet reached this concrete binding in its loop.
        with bound._call_state_lock:
            backend._closed = True
            self.assertFalse(bound._retired)
            self.assertFalse(bound._released)
            self.assertEqual(bound._active_calls, 0)

        with mock.patch.object(backend, "_invoke_codex") as provider:
            with self.assertRaises(BackendProtocolError) as raised:
                await bound.invoke(self.request())

        self.assertEqual(str(raised.exception), "agent backend is closed")
        provider.assert_not_awaited()
        self.assertEqual(bound._active_calls, 0)
        self.assertFalse(bound._retired)
        self.assertFalse(bound._released)
        self.assertIn(bound, backend._bound_agents)
        self.assertIn(staging, backend._staging_directories)
        self.assertTrue(staging.exists())

        # Complete the remainder of close. The inverse ordering—an entered
        # call surviving close until exit—is covered by the preceding test.
        bound._retire()
        self.assertTrue(bound._retired)
        self.assertTrue(bound._released)
        self.assertNotIn(bound, backend._bound_agents)
        self.assertNotIn(staging, backend._staging_directories)
        self.assertFalse(staging.exists())
        self.assertTrue(backend._close_cleanup_complete)

    async def test_post_staging_validation_failure_discards_provisional_bytes(
        self,
    ) -> None:
        for failure in (RuntimeError("validation failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
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

        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
            expected_payload = AgentBackend._request_payload(request)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
        expected = AgentBackend._conversation_request_payload(request)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                backend = self._backend_with_fake_codex()
                self.addCleanup(backend.close)
                self.create_provisioned_session_with_access(backend, session_id)
                bound = await backend.bind(self.definition())
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        self.create_provisioned_session_with_access(backend, session_id)
        bound = await backend.bind(self.definition())
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
                backend = self._backend_with_fake_codex()
                self.create_provisioned_session_with_access(backend, session_id)
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
                    backend = self._backend_with_fake_codex()
                    self.create_provisioned_session_with_access(backend, session_id)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        definition = self.definition()
        bound = await backend.bind(definition)

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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
        definition = self.definition()
        bound = await backend.bind(definition)
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
            await backend._invoke_provider_structured(
                bound,
                session_id=unicode_session,
                conversation_id=unicode_conversation,
                schema={},
                payload={},
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
                    backend._store_thread(
                        session_id,
                        definition,
                        stored_thread,
                        tainted=False,
                        fingerprint=continuation_fingerprint,
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
                        _wrapped_thread_taint_patch(backend, backend._taint_thread) as taint,
                        _wrapped_thread_store_patch(backend, backend._store_thread) as store,
                    ):
                        try:
                            await backend._invoke_provider_structured(
                                bound,
                                session_id=session_id,
                                debate_id=debate_id,
                                schema={},
                                payload={},
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
        backend = self._backend_with_fake_codex()
        self.addCleanup(backend.close)
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
            actual = await backend._invoke_provider_structured(
                bound,
                session_id=success_session,
                debate_id="success-debate",
                schema={},
                payload={},
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
                    await backend._invoke_provider_structured(
                        bound,
                        session_id=session_id,
                        debate_id=f"decode-control-{index}",
                        schema={},
                        payload={},
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
            await backend._invoke_provider_structured(
                bound,
                session_id=provider_session,
                debate_id="provider-pre-result",
                schema={},
                payload={},
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
            await backend._invoke_provider_structured(
                bound,
                session_id=adapter_cancel_session,
                debate_id="adapter-cancel",
                schema={},
                payload={},
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
            _fresh_literal_object_side_effect(backend, '_store_thread', persistence_failure),
            _rejected_response_artifact_patch() as record,
            self.assertRaises(OSError) as caught,
        ):
            await backend._invoke_provider_structured(
                bound,
                session_id=persistence_session,
                debate_id="persistence-failure",
                schema={},
                payload={},
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
                    backend = self._backend_with_fake_codex()
                    self.addCleanup(backend.close)
                    self.create_provisioned_session_with_access(backend, session_id)
                    bound = await backend.bind(self.definition())
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
            cancel_backend = self._backend_with_fake_codex()
            self.addCleanup(cancel_backend.close)
            self.create_provisioned_session_with_access(cancel_backend, cancel_session)
            cancel_bound = await cancel_backend.bind(self.definition())
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
