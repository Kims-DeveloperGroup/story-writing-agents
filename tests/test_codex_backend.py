"""Concrete Codex CLI backend tests with no credentials or network access."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from typing import get_type_hints
from unittest import mock

from story_writing_agents import (
    CONVERSATION_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    AgentBackend,
    AgentReply,
    AgentRequest,
    BoundAgent,
    CHARACTER_CUSTODIAN,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationReply,
    ConversationRequest,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    PLOT_ARCHITECT,
    Phase,
    Verdict,
)
from story_writing_agents.cli import _parse_backend_overrides, build_parser, main
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionIdError,
    SessionNotFoundError,
    WorkspaceFormatError,
)
from story_writing_agents.prompts import PromptAssembler

from tests.helpers import all_manuscripts, core_snapshot, initialized_workspace


_FAKE_CODEX = """#!/usr/bin/env python3
import os
import signal

mode = os.environ.get("STORY_FAKE_CODEX_MODE", "ok")
if mode == "sleep":
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

payload = json.loads(sys.stdin.read())
agent_directory = Path(argv[argv.index("--cd") + 1])
schema_path = Path(argv[argv.index("--output-schema") + 1])
append({
    "argv": argv,
    "payload": payload,
    "agents": (agent_directory / "AGENTS.md").read_text(encoding="utf-8"),
    "schema": json.loads(schema_path.read_text(encoding="utf-8")),
})

if mode == "sleep":
    time.sleep(30)
if mode == "delay":
    time.sleep(0.25)
if mode == "nonzero":
    if "conversation_id" in payload:
        sensitive = [payload["content"]]
        sensitive.extend(item["content"] for item in payload["context"])
    else:
        sensitive = [payload["user_payload"]]
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
if mode == "missing_output":
    pass
elif "STORY_FAKE_CODEX_OUTPUT" in os.environ:
    output_path.write_text(
        os.environ["STORY_FAKE_CODEX_OUTPUT"],
        encoding="utf-8",
    )
elif mode == "malformed":
    output_path.write_text("not-json", encoding="utf-8")
elif "conversation_id" in payload:
    output_path.write_text(json.dumps({
        "session_id": payload["session_id"],
        "conversation_id": payload["conversation_id"],
        "turn_id": payload["turn_id"],
        "content": "generated conversation reply",
    }), encoding="utf-8")
elif payload["phase"] == "audit":
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "verdict": "approve",
        "content": "",
    }), encoding="utf-8")
elif payload["phase"] == "finalize":
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "core_updates": [{
            "filename": "global_outline.md",
            "content": "updated outline",
        }],
    }), encoding="utf-8")
else:
    output_path.write_text(json.dumps({
        "revision_id": payload["revision_id"],
        "content": "generated candidate",
    }), encoding="utf-8")

if mode == "invalid_event_utf8":
    sys.stdout.buffer.write(b"\\xff")
elif "resume" not in argv:
    print(json.dumps({
        "type": "thread.started",
        "thread_id": (
            "thread-" + payload["session_id"] + "-" + payload["agent_id"]
        ),
    }))
"""


_TIMESTAMP = "2026-08-15T01:02:03+00:00"


def _conversation_pair(
    turn_id: str,
    sequence: int = 1,
    *,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a",
    recipient: str = PLOT_ARCHITECT,
    user_content: str = "visible question",
    outcome_content: str = "visible answer",
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    def event(
        offset: int,
        kind: ConversationEventKind,
        sender: str,
        target: str,
        content: str,
    ) -> ConversationEvent:
        return ConversationEvent(
            CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id,
            conversation_id,
            turn_id,
            sequence + offset,
            _TIMESTAMP,
            sender,
            target,
            kind,
            content,
        )

    user = event(0, ConversationEventKind.USER_MESSAGE, "user", recipient, user_content)
    sender = recipient if outcome_kind is ConversationEventKind.AGENT_REPLY else "runtime"
    return user, event(1, outcome_kind, sender, "user", outcome_content)


def _context_window(
    events: tuple[ConversationEvent, ...] = (),
    *,
    retained_turns: int | None = None,
    retained_utf8_bytes: int | None = None,
    omitted_turns: int = 0,
) -> ConversationContextWindow:
    retained = (
        len({event.turn_id for event in events})
        if retained_turns is None
        else retained_turns
    )
    retained_bytes = (
        sum(len(event.content.encode("utf-8")) for event in events)
        if retained_utf8_bytes is None
        else retained_utf8_bytes
    )
    return ConversationContextWindow(
        events=events,
        total_complete_turns=retained + omitted_turns,
        retained_turns=retained,
        omitted_turns=omitted_turns,
        retained_utf8_bytes=retained_bytes,
        truncated=omitted_turns > 0,
    )


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


def _conversation_request(
    *,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a",
    turn_id: str = "turn_current",
    agent_id: str = PLOT_ARCHITECT,
    content: str = "visible user request",
    context: ConversationContextWindow | None = None,
) -> ConversationRequest:
    return ConversationRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        agent_id=agent_id,
        content=content,
        context=_context_window() if context is None else context,
    )


class ConversationModelContractTests(unittest.TestCase):
    def test_exact_frozen_slotted_no_default_models_and_converse_signature(
        self,
    ) -> None:
        reply = ConversationReply("story_session", "conversation_a", "turn_current", "reply")
        cases = (
            (
                ConversationRequest,
                ("session_id", "conversation_id", "turn_id", "agent_id", "content", "context"),
                _conversation_request(),
            ),
            (
                ConversationReply,
                ("session_id", "conversation_id", "turn_id", "content"),
                reply,
            ),
        )
        for model, names, value in cases:
            with self.subTest(model=model.__name__):
                model_fields = fields(model)
                self.assertEqual(tuple(field.name for field in model_fields), names)
                self.assertTrue(
                    all(
                        field.default is MISSING
                        and field.default_factory is MISSING
                        for field in model_fields
                    )
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    value.content = "changed"

        signature = inspect.signature(BoundAgent.converse)
        self.assertEqual(tuple(signature.parameters), ("self", "request"))
        self.assertIs(
            signature.parameters["request"].default,
            inspect.Parameter.empty,
        )
        hints = get_type_hints(BoundAgent.converse)
        self.assertIs(hints["request"], ConversationRequest)
        self.assertIs(hints["return"], ConversationReply)

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
            "content": "visible",
        }
        cases = (
            (ConversationRequest, request_values, tuple(request_values)[:4]),
            (ConversationReply, reply_values, tuple(reply_values)[:3]),
        )
        for model, base, identifier_fields in cases:
            for field_name in identifier_fields:
                for value, error in (
                    (None, TypeError),
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
            "x" * 64,
        )
        with self.assertRaises(ValueError):
            _conversation_request(agent_id="unknown_agent")

        preserved = " \n\t\x00\x1b 한🙂 "
        for model, base, _ in cases:
            values = dict(base)
            values["content"] = preserved
            self.assertEqual(
                model(**values).content,  # type: ignore[arg-type]
                preserved,
            )
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
            truncated=False,
        )
        values = dict(request_values)
        values["context"] = derived
        with self.assertRaises(TypeError):
            ConversationRequest(**values)  # type: ignore[arg-type]

    def test_context_accepts_interleaving_sequence_gaps_and_exact_caps(self) -> None:
        first = _conversation_pair(
            "turn_one",
            2,
            recipient=CHARACTER_CUSTODIAN,
            outcome_kind=ConversationEventKind.RUNTIME_ERROR,
        )
        second = _conversation_pair("turn_two", 4)
        interleaved = (
            first[0],
            second[0],
            replace(second[1], sequence=7),
            replace(first[1], sequence=11),
        )
        window = _context_window(interleaved, omitted_turns=3)
        request = _conversation_request(context=window)
        self.assertEqual(request.context, window)
        self.assertIs(type(request.context), ConversationContextWindow)
        self.assertIs(type(request.context.events), tuple)
        self.assertTrue(
            all(type(event) is ConversationEvent for event in request.context.events)
        )
        self.assertEqual(request.context.total_complete_turns, 5)

        capped_events = tuple(
            event
            for index in range(CONVERSATION_CONTEXT_POLICY.max_complete_turns)
            for event in _conversation_pair(
                f"cap_{index}", index * 2 + 1, user_content="u", outcome_content="r"
            )
        )
        _conversation_request(context=_context_window(capped_events))
        byte_capped = _conversation_pair(
            "byte_cap",
            user_content="x" * (CONVERSATION_CONTEXT_POLICY.max_utf8_bytes - 1),
            outcome_content="y",
        )
        _conversation_request(context=_context_window(byte_capped))

    def test_context_rejects_identity_order_pair_metadata_and_cap_errors(
        self,
    ) -> None:
        user, reply = _conversation_pair("turn_prior")
        over_turn_cap = tuple(
            event
            for index in range(CONVERSATION_CONTEXT_POLICY.max_complete_turns + 1)
            for event in _conversation_pair(f"over_{index}", index * 2 + 1)
        )
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
                _context_window((replace(user, sequence=2), replace(reply, sequence=1))),
            ),
            ("unmatched user", _context_window((user,))),
            (
                "wrong reply sender",
                _context_window((user, replace(reply, sender=CHARACTER_CUSTODIAN))),
            ),
            ("retained count", _context_window((user, reply), retained_turns=0)),
            (
                "retained bytes",
                _context_window((user, reply), retained_utf8_bytes=1),
            ),
            ("turn cap", _context_window(over_turn_cap)),
            (
                "byte cap",
                _context_window(
                    _conversation_pair(
                        "byte_over",
                        user_content="x"
                        * CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
                        outcome_content="y",
                    )
                ),
            ),
        )
        for label, context in invalid_windows:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    _conversation_request(context=context)


class CodexBackendTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fake_directory = Path(temporary.name)
        self.executable = self.fake_directory / "fake-codex"
        self.executable.write_text(_FAKE_CODEX, encoding="utf-8")
        self.executable.chmod(0o755)
        self.log_path = self.fake_directory / "invocations.jsonl"
        self.environment = mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_LOG": str(self.log_path)},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def definition(self, agent_id: str = PLOT_ARCHITECT):
        return PromptAssembler(self.workspace).build_definition(
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

    def invocations(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def provider_invocations(self) -> list[dict[str, object]]:
        return [item for item in self.invocations() if "payload" in item]

    def thread(self, session_id: str) -> dict[str, object]:
        manifest = self.workspace.read_json(
            "agent_states/orchestrator_state/provider_sessions/"
            f"{session_id}.json"
        )
        return manifest["threads"][f"codex:{PLOT_ARCHITECT}"]

    async def wait_for_provider_invocations(self, count: int) -> None:
        for _ in range(200):
            if len(self.provider_invocations()) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail(f"fake Codex did not record {count} invocations")

    async def test_binding_stages_isolated_agents_and_turn_omits_static_prompt(
        self,
    ) -> None:
        definition = self.definition()
        role = definition.system_prompt_file.read_text()
        persona = definition.persona_file.read_text()
        contexts = [
            reference.read_text()
            for reference in definition.context_document_files
        ]
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("story_session")

        bound = await backend.bind(definition)
        self.assertIsInstance(bound, BoundAgent)
        self.assertEqual(bound.vendor, "codex")
        staged = (bound.staging_directory / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(role, staged)
        self.assertIn(persona, staged)
        for context in contexts:
            self.assertIn(context, staged)
        self.assertNotEqual(bound.staging_directory, self.workspace.root)

        reply = await bound.invoke(self.request())
        self.assertEqual(reply, AgentReply.candidate("r0", "generated candidate"))
        invocation = self.invocations()[0]
        payload_text = json.dumps(invocation["payload"], ensure_ascii=False)
        self.assertNotIn(role, payload_text)
        self.assertNotIn(persona, payload_text)
        for context in contexts:
            self.assertNotIn(context, payload_text)
        argv = invocation["argv"]
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ignore-rules", argv)
        self.assertIn("read-only", argv)
        self.assertIn("project_root_markers=[]", argv)
        self.assertIn("web_search", argv)
        self.assertTrue(
            any(
                isinstance(value, str)
                and value.startswith("project_doc_max_bytes=")
                for value in argv
            )
        )
        self.assertNotIn("resume", argv)

    async def test_conversation_projects_exact_payload_schema_and_no_artifacts(
        self,
    ) -> None:
        session_id = "conversation_session"
        catalog = FilesystemConversationCatalog(self.workspace)
        metadata = catalog.create(session_id, "Provider boundary")
        store = FilesystemConversationEventStore(
            self.workspace,
            catalog=catalog,
        )
        source_events = (
            *_conversation_pair(
                "turn_prior",
                session_id=session_id,
                conversation_id=metadata.conversation_id,
                user_content="prior visible question",
                outcome_content="prior visible answer",
            ),
            _conversation_pair(
                "turn_current",
                session_id=session_id,
                conversation_id=metadata.conversation_id,
                user_content="current visible question",
            )[0],
        )
        prior_user, prior_reply, current_user = (
            store.append(
                ConversationEventAppend(
                    session_id=event.session_id,
                    conversation_id=event.conversation_id,
                    turn_id=event.turn_id,
                    sender=event.sender,
                    recipient=event.recipient,
                    kind=event.kind,
                    content=event.content,
                )
            )
            for event in source_events
        )
        context = store.read_context(session_id, metadata.conversation_id)

        definition = self.definition()
        static_content = [
            reference.read_text()
            for reference in definition.file_references()
        ]
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session(session_id)
        bound = await backend.bind(definition)
        request = self.conversation_request(
            session_id=session_id,
            conversation_id=metadata.conversation_id,
            content=current_user.content,
            context=context,
        )
        visible_reply = "  line\n\t\x00\x1b 한🙂  "
        raw_reply = json.dumps(
            {
                "session_id": session_id,
                "conversation_id": metadata.conversation_id,
                "turn_id": "turn_current",
                "content": visible_reply,
            }
        )
        discussion_before = _tree_snapshot(
            self.workspace.path("discussion_logs")
        )
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
                conversation_id=metadata.conversation_id,
                turn_id="turn_current",
                content=visible_reply,
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
                "content",
                "context",
            },
        )
        self.assertEqual(
            tuple(payload[field] for field in tuple(payload)[:5]),
            (
                session_id,
                metadata.conversation_id,
                "turn_current",
                PLOT_ARCHITECT,
                current_user.content,
            ),
        )
        context_fields = ("turn_id", "sender", "recipient", "kind", "content")
        self.assertTrue(
            all(tuple(item) == context_fields for item in payload["context"])
        )
        self.assertEqual(
            [tuple(item.values()) for item in payload["context"]],
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
        expected_properties = {
            field: {"type": "string", "minLength": 1}
            for field in (
                "session_id",
                "conversation_id",
                "turn_id",
                "content",
            )
        }
        self.assertEqual(
            invocation["schema"],
            {
                "type": "object",
                "properties": expected_properties,
                "required": list(expected_properties),
                "additionalProperties": False,
            },
        )
        payload_text = json.dumps(payload, ensure_ascii=False)
        for content in static_content:
            self.assertNotIn(content, payload_text)

        self.assertEqual(
            _tree_snapshot(self.workspace.path("discussion_logs")),
            discussion_before,
        )
        self.assertEqual(core_snapshot(self.workspace), core_before)
        self.assertEqual(all_manuscripts(self.workspace), manuscripts_before)
        self.assertEqual(
            store.read(session_id, metadata.conversation_id),
            (prior_user, prior_reply, current_user),
        )
        self.assertEqual(self.workspace.inspect_session(session_id)["debates"], [])

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
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("snapshot_session")
        await (await backend.bind(self.definition())).converse(request)

        payload = self.provider_invocations()[0]["payload"]
        self.assertEqual(payload["context"][0]["content"], validated)
        self.assertNotIn(unvalidated, json.dumps(payload))

    async def test_conversation_thread_spans_subjects_and_restarts_on_fingerprint(
        self,
    ) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("story_session")
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

        incompatible = replace(definition, fingerprint="0" * 64)
        incompatible_bound = await backend.bind(incompatible)
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
        second_payload = json.dumps(
            invocations[1]["payload"],
            ensure_ascii=False,
        )
        self.assertIn("CONTEXT_B_USER", second_payload)
        self.assertNotIn("CONTEXT_A_USER", second_payload)
        self.assertEqual(self.thread("story_session")["fingerprint"], "0" * 64)

    async def test_invoke_and_converse_share_one_serialized_thread(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("mixed_session")
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
            AgentReply.candidate("r0", "generated candidate"),
        )
        self.assertEqual(conversation_reply.content, "generated conversation reply")
        invocations = self.provider_invocations()
        self.assertEqual(len(invocations), 2)
        self.assertEqual(
            sum("resume" in invocation["argv"] for invocation in invocations),
            1,
        )
        self.assertEqual(
            {"phase" in invocation["payload"] for invocation in invocations},
            {False, True},
        )

    async def test_thread_persists_across_backend_instances_and_debates(
        self,
    ) -> None:
        definition = self.definition()
        first = AgentBackend(self.workspace, executable=str(self.executable))
        first.start_session("story_session")
        await (await first.bind(definition)).invoke(
            self.request(debate_id="debate_one")
        )
        first.close()

        second = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(second.close)
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
            "agent_states/orchestrator_state/provider_sessions/"
            "story_session.json"
        )
        self.assertEqual(
            manifest["threads"]["codex:plot_architect"]["thread_id"],
            "thread-story_session-plot_architect",
        )

    async def test_threads_are_separate_by_agent_and_story_session(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("session_one")
        backend.start_session("session_two")
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
            set(session_one["threads"]),
            {"codex:plot_architect", "codex:character_custodian"},
        )
        self.assertEqual(set(session_two["threads"]), {"codex:plot_architect"})
        self.assertNotEqual(
            session_one["threads"]["codex:plot_architect"]["thread_id"],
            session_two["threads"]["codex:plot_architect"]["thread_id"],
        )

    async def test_separate_bindings_serialize_one_session_agent_thread(
        self,
    ) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("shared_session")
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

    async def test_end_disables_invocation_and_restart_retains_thread(self) -> None:
        definition = self.definition()
        first = AgentBackend(self.workspace, executable=str(self.executable))
        first.start_session("story_session")
        await (await first.bind(definition)).invoke(self.request())
        first.close()

        control = AgentBackend(self.workspace, executable=str(self.executable))
        ended = control.end_session("story_session")
        self.assertFalse(ended["active"])
        retained = dict(ended["threads"])
        control.close()

        inactive = AgentBackend(self.workspace, executable=str(self.executable))
        bound = await inactive.bind(definition)
        with self.assertRaisesRegex(BackendProtocolError, "not active"):
            await bound.invoke(self.request(debate_id="while_inactive"))
        with self.assertRaisesRegex(BackendProtocolError, "not active"):
            await bound.converse(self.conversation_request())
        restarted = inactive.start_session("story_session")
        self.assertTrue(restarted["active"])
        self.assertEqual(restarted["threads"], retained)
        await bound.invoke(self.request(debate_id="after_restart"))
        inactive.close()

        self.assertIn("resume", self.invocations()[-1]["argv"])

    async def test_resume_only_attachment_reuses_compatible_thread(self) -> None:
        definition = self.definition()
        first = AgentBackend(self.workspace, executable=str(self.executable))
        first.start_session("resume_only_session")
        await (await first.bind(definition)).invoke(
            self.request(session_id="resume_only_session")
        )
        first.end_session("resume_only_session")
        first.close()

        resumed = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(resumed.close)
        summary = resumed.resume_session("resume_only_session")
        self.assertTrue(summary.active)
        self.assertEqual(summary.lease_state.value, "owned")
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

    async def test_strict_phase_reply_mapping(self) -> None:
        candidate = AgentBackend._decode_reply(
            Phase.SYNTHESIZE,
            '{"revision_id":"r2","content":"candidate"}',
        )
        self.assertEqual(candidate, AgentReply.candidate("r2", "candidate"))

        approval = AgentBackend._decode_reply(
            Phase.AUDIT,
            '{"revision_id":"r2","verdict":"approve","content":""}',
        )
        self.assertIs(approval.verdict, Verdict.APPROVE)
        conflict = AgentBackend._decode_reply(
            Phase.AUDIT,
            '{"revision_id":"r2","verdict":"conflict","content":"fix it"}',
        )
        self.assertIs(conflict.verdict, Verdict.CONFLICT)

        finalized = AgentBackend._decode_reply(
            Phase.FINALIZE,
            (
                '{"revision_id":"r2","core_updates":['
                '{"filename":"global_outline.md","content":"outline"}]}'
            ),
        )
        self.assertEqual(dict(finalized.core_updates), {"global_outline.md": "outline"})

        invalid_values = (
            (Phase.AUDIT, '{"revision_id":"r2","verdict":"maybe","content":""}'),
            (
                Phase.FINALIZE,
                (
                    '{"revision_id":"r2","core_updates":['
                    '{"filename":"secret.md","content":"no"}]}'
                ),
            ),
            (
                Phase.FINALIZE,
                (
                    '{"revision_id":"r2","core_updates":['
                    '{"filename":"global_outline.md","content":"one"},'
                    '{"filename":"global_outline.md","content":"two"}]}'
                ),
            ),
            (Phase.DRAFT, '{"revision_id":"r2","content":"ok","extra":true}'),
        )
        for phase, value in invalid_values:
            with self.subTest(phase=phase, value=value):
                with self.assertRaises(BackendProtocolError):
                    AgentBackend._decode_reply(phase, value)

    async def test_converse_rejects_wrong_types_tampering_and_routing_preflight(
        self,
    ) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session("routing_session")
        definition = self.definition()
        bound = await backend.bind(definition)
        manifest_path = (
            "agent_states/orchestrator_state/provider_sessions/"
            "routing_session.json"
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

    async def test_conversation_output_is_exact_correlated_nonempty_utf8_json(
        self,
    ) -> None:
        session_id = "output_session"
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session(session_id)
        bound = await backend.bind(self.definition())
        await bound.converse(
            self.conversation_request(session_id=session_id, turn_id="turn_seed")
        )
        base: dict[str, object] = {
            "session_id": session_id,
            "conversation_id": "conversation_a",
            "turn_id": "turn_failed",
            "content": "visible reply",
        }

        def changed(**overrides: object) -> str:
            return json.dumps({**base, **overrides})

        cases = {
            "malformed": "not-json",
            "non-object": "[]",
            "duplicate": (
                "{"
                f'"session_id":"{session_id}",'
                f'"session_id":"{session_id}",'
                '"conversation_id":"conversation_a",'
                '"turn_id":"turn_failed","content":"visible reply"}'
            ),
            "extra": changed(extra=True),
            "content type": changed(content=1),
            "empty content": changed(content=""),
            "surrogate content": changed(content="invalid-\ud800"),
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
                    with self.assertRaises(BackendProtocolError):
                        await bound.converse(
                            self.conversation_request(
                                session_id=session_id,
                                turn_id="turn_failed",
                            )
                        )
                self.assertIs(self.thread(session_id)["tainted"], True)
                await bound.converse(
                    self.conversation_request(
                        session_id=session_id,
                        turn_id=f"turn_recovery_{index}",
                    )
                )

    async def test_conversation_provider_failures_taint_without_story_mutation(
        self,
    ) -> None:
        session_id = "transaction_failure"
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session(session_id)
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
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session(session_id)
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

    async def test_conversation_diagnostic_is_redacted_sanitized_and_bounded(
        self,
    ) -> None:
        session_id = "diagnostic_session"
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        backend.start_session(session_id)
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
        with mock.patch.dict(
            os.environ,
            {
                "STORY_FAKE_CODEX_MODE": "nonzero",
                "STORY_FAKE_CODEX_LONG_DIAGNOSTIC": "1",
            },
        ):
            with self.assertRaisesRegex(
                BackendProtocolError,
                "status 7",
            ) as caught:
                await bound.converse(request)

        diagnostic = str(caught.exception)
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
        self.assertIn("<redacted>", diagnostic)
        self.assertIn("[truncated]", diagnostic)
        self.assertLessEqual(len(diagnostic), 4_160)

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
                backend.start_session(session)
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
                    with self.assertRaisesRegex(
                        BackendProtocolError,
                        message,
                    ) as caught:
                        await bound.invoke(self.request(session_id=session))
                self.assertNotIn("dynamic user input", str(caught.exception))
                backend.close()

    async def test_timeout_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
            timeout_seconds=0.3,
        )
        self.addCleanup(backend.close)
        backend.start_session("timeout_session")
        bound = await backend.bind(self.definition())
        real_wait_for = asyncio.wait_for
        communication_pending = True

        async def wait_for_after_provider_ready(awaitable, timeout):
            nonlocal communication_pending
            if communication_pending:
                communication_pending = False
                communication = asyncio.ensure_future(awaitable)
                await self.wait_for_provider_invocations(1)
                return await real_wait_for(communication, timeout=0)
            return await real_wait_for(awaitable, timeout=timeout)

        with mock.patch.dict(
            os.environ,
            {"STORY_FAKE_CODEX_MODE": "sleep"},
        ), mock.patch(
            "story_writing_agents.backend.asyncio.wait_for",
            new=wait_for_after_provider_ready,
        ):
            with self.assertRaisesRegex(BackendProtocolError, "timed out"):
                await bound.invoke(self.request(session_id="timeout_session"))
        self.assertTrue(
            any(item.get("terminated") is True for item in self.invocations())
        )

    async def test_cancellation_terminates_the_codex_process(self) -> None:
        backend = AgentBackend(
            self.workspace,
            executable=str(self.executable),
        )
        self.addCleanup(backend.close)
        backend.start_session("cancel_session")
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

    async def test_unsupported_vendor_fails_during_binding(self) -> None:
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        self.addCleanup(backend.close)
        with self.assertRaisesRegex(ConfigurationError, "not implemented"):
            await backend.bind(replace(self.definition(), backend="gemini"))

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
        backend = AgentBackend(self.workspace, executable=str(self.executable))
        backend.start_session("closed_session")
        definition = self.definition()
        bound = await backend.bind(definition)
        backend.close()

        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await backend.bind(definition)
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.prepare_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.start_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            backend.end_session("closed_session")
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await bound.invoke(self.request(session_id="closed_session"))
        with self.assertRaisesRegex(BackendProtocolError, "closed"):
            await bound.converse(
                self.conversation_request(session_id="closed_session")
            )


class ConcreteBackendCliTests(unittest.TestCase):
    def test_repeatable_backend_overrides_and_codex_defaults(self) -> None:
        parser = build_parser()
        arguments = parser.parse_args(
            [
                "run",
                "--session",
                "story_session",
                "--backend",
                "plot_architect=codex",
                "--backend",
                "character_custodian=gemini",
                "--draft",
                "draft",
            ]
        )
        self.assertEqual(
            _parse_backend_overrides(arguments.backend),
            {
                PLOT_ARCHITECT: "codex",
                CHARACTER_CUSTODIAN: "gemini",
            },
        )
        self.assertIsNone(arguments.codex_model)
        self.assertEqual(arguments.codex_timeout, 300)
        with self.assertRaisesRegex(ConfigurationError, "duplicate"):
            _parse_backend_overrides(
                ["plot_architect=codex", "plot_architect=codex"]
            )
        with self.assertRaisesRegex(ConfigurationError, "unknown"):
            _parse_backend_overrides(["unknown=codex"])

    def test_session_start_and_end_commands_retain_manifest(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", str(workspace_path)]), 0)

        for action, active in (("start", True), ("end", False)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "session",
                        action,
                        "--workspace",
                        str(workspace_path),
                        "--session",
                        "cli_session",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["active"], active)


if __name__ == "__main__":
    unittest.main()
