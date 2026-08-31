"""Conversation event models, adapter contract, and filesystem tests."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import story_writing_agents
import story_writing_agents.conversations as conversations_module
import story_writing_agents.conversations.context as conversation_context_module
import story_writing_agents.conversations.events as conversation_events_module
import story_writing_agents.conversation_storage.filesystem as filesystem_module
import story_writing_agents.workspace as workspace_module
import story_writing_agents.workspace.core as workspace_core_module
from story_writing_agents import (
    CONVERSATION_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationContextPolicy,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationEventStore,
    ConversationNotFoundError,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    SessionIdError,
    Workspace,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.agents import (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)

from tests.conversation_event_store_contract import (
    ConversationEventStoreContractMixin,
    InMemoryConversationEventStore,
    StaticConversationCatalog,
    make_append,
    make_stored_event,
)


_EVENT_FIELDS = (
    "schema_version",
    "session_id",
    "conversation_id",
    "turn_id",
    "sequence",
    "timestamp",
    "sender",
    "recipient",
    "kind",
    "content",
)
_APPEND_FIELDS = (
    "session_id",
    "conversation_id",
    "turn_id",
    "sender",
    "recipient",
    "kind",
    "content",
)
_WINDOW_FIELDS = (
    "events",
    "total_complete_turns",
    "retained_turns",
    "omitted_turns",
    "retained_utf8_bytes",
    "truncated",
)
_EVENT_EXPORTS = {
    "CONVERSATION_EVENT_SCHEMA_VERSION",
    "ConversationEvent",
    "ConversationEventAppend",
    "ConversationEventKind",
}
_CONTEXT_EXPORTS = {
    "CONVERSATION_CONTEXT_POLICY",
    "ConversationContextPolicy",
    "ConversationContextWindow",
    "ConversationEventStore",
    "select_bounded_conversation_context",
    "validate_conversation_context_window",
}


def _transcript_path(
    workspace: Workspace,
    session_id: str,
    conversation_id: str,
) -> Path:
    return (
        workspace.root
        / "discussion_logs"
        / session_id
        / "interactive"
        / "conversations"
        / conversation_id
        / "transcript.jsonl"
    )


def _jsonl_bytes(payloads: list[object]) -> bytes:
    return "".join(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        for payload in payloads
    ).encode("utf-8")


def _temporary_artifacts(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.tmp")) if directory.exists() else []


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


class ConversationEventModelTests(unittest.TestCase):
    def test_constants_kinds_exact_fields_and_immutable_slots(self) -> None:
        self.assertEqual(CONVERSATION_EVENT_SCHEMA_VERSION, 1)
        self.assertEqual(
            CONVERSATION_CONTEXT_POLICY,
            ConversationContextPolicy(
                max_complete_turns=24,
                max_utf8_bytes=32_768,
            ),
        )
        self.assertEqual(
            tuple(ConversationEventKind),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertEqual(
            tuple(kind.value for kind in ConversationEventKind),
            ("user_message", "agent_reply", "runtime_error"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationContextPolicy)),
            ("max_complete_turns", "max_utf8_bytes"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationEventAppend)),
            _APPEND_FIELDS,
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationEvent)),
            _EVENT_FIELDS,
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationContextWindow)),
            _WINDOW_FIELDS,
        )

        append = make_append(
            session_id="session_a",
            conversation_id="conversation_a",
            turn_id="turn_a",
        )
        event = make_stored_event()
        window = ConversationContextWindow(
            events=(event,),
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=len(event.content.encode("utf-8")),
            truncated=False,
        )
        for value, attribute in (
            (CONVERSATION_CONTEXT_POLICY, "max_complete_turns"),
            (append, "content"),
            (event, "sequence"),
            (window, "truncated"),
        ):
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, attribute, None)

    def test_event_serializes_the_exact_authoritative_schema(self) -> None:
        event = make_stored_event(content="  line\n\t\x00한🙂  ")
        self.assertEqual(
            event.to_dict(),
            {
                "schema_version": 1,
                "session_id": "session_a",
                "conversation_id": "conversation_a",
                "turn_id": "turn_a",
                "sequence": 1,
                "timestamp": "2026-08-15T01:02:03+00:00",
                "sender": "user",
                "recipient": LEAD_ORCHESTRATOR,
                "kind": "user_message",
                "content": "  line\n\t\x00한🙂  ",
            },
        )
        self.assertEqual(tuple(event.to_dict()), _EVENT_FIELDS)

    def test_append_identifiers_use_the_safe_exact_rule(self) -> None:
        for valid in ("a", "A0", "with-dash", "with_under", "x" * 64):
            for field in ("session_id", "conversation_id", "turn_id"):
                values = {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                    "turn_id": "turn_a",
                }
                values[field] = valid
                with self.subTest(field=field, valid=valid):
                    self.assertEqual(
                        getattr(make_append(**values), field),  # type: ignore[arg-type]
                        valid,
                    )

        invalid: tuple[object, ...] = (
            "",
            "-leading",
            "_leading",
            ".hidden",
            "contains.dot",
            "contains space",
            "../escape",
            "x" * 65,
            1,
            None,
        )
        for value in invalid:
            for field in ("session_id", "conversation_id", "turn_id"):
                values: dict[str, object] = {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                    "turn_id": "turn_a",
                    "sender": "user",
                    "recipient": LEAD_ORCHESTRATOR,
                    "kind": ConversationEventKind.USER_MESSAGE,
                    "content": "visible",
                }
                values[field] = value
                with self.subTest(field=field, invalid=value):
                    with self.assertRaises(SessionIdError):
                        ConversationEventAppend(**values)  # type: ignore[arg-type]

    def test_actor_rules_and_kind_types_are_exact(self) -> None:
        for agent_id in ROSTER:
            with self.subTest(agent=agent_id, kind="user"):
                make_append(
                    session_id="session_a",
                    conversation_id="conversation_a",
                    turn_id="turn_a",
                    recipient=agent_id,
                )
            with self.subTest(agent=agent_id, kind="reply"):
                make_append(
                    session_id="session_a",
                    conversation_id="conversation_a",
                    turn_id="turn_a",
                    kind=ConversationEventKind.AGENT_REPLY,
                    sender=agent_id,
                )
        make_append(
            session_id="session_a",
            conversation_id="conversation_a",
            turn_id="turn_a",
            kind=ConversationEventKind.RUNTIME_ERROR,
        )

        invalid_actors = (
            (ConversationEventKind.USER_MESSAGE, LEAD_ORCHESTRATOR, LEAD_ORCHESTRATOR),
            (ConversationEventKind.USER_MESSAGE, "user", "unknown_agent"),
            (ConversationEventKind.AGENT_REPLY, "user", "user"),
            (ConversationEventKind.AGENT_REPLY, LEAD_ORCHESTRATOR, PLOT_ARCHITECT),
            (ConversationEventKind.RUNTIME_ERROR, LEAD_ORCHESTRATOR, "user"),
            (ConversationEventKind.RUNTIME_ERROR, "runtime", LEAD_ORCHESTRATOR),
        )
        for kind, sender, recipient in invalid_actors:
            with self.subTest(kind=kind, sender=sender, recipient=recipient):
                with self.assertRaises(ValueError):
                    ConversationEventAppend(
                        session_id="session_a",
                        conversation_id="conversation_a",
                        turn_id="turn_a",
                        sender=sender,
                        recipient=recipient,
                        kind=kind,
                        content="visible",
                    )
        with self.assertRaises(TypeError):
            ConversationEventAppend(
                session_id="session_a",
                conversation_id="conversation_a",
                turn_id="turn_a",
                sender="user",
                recipient=LEAD_ORCHESTRATOR,
                kind="user_message",  # type: ignore[arg-type]
                content="visible",
            )

    def test_content_is_nonempty_strict_utf8_but_otherwise_exact(self) -> None:
        preserved = " \n\t\x00\x1b 한🙂 "
        event = make_append(
            session_id="session_a",
            conversation_id="conversation_a",
            turn_id="turn_a",
            content=preserved,
        )
        self.assertEqual(event.content, preserved)

        for value, error in (
            ("", ValueError),
            (None, TypeError),
            (1, TypeError),
            ("invalid-\ud800", ValueError),
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(error):
                    ConversationEventAppend(
                        session_id="session_a",
                        conversation_id="conversation_a",
                        turn_id="turn_a",
                        sender="user",
                        recipient=LEAD_ORCHESTRATOR,
                        kind=ConversationEventKind.USER_MESSAGE,
                        content=value,  # type: ignore[arg-type]
                    )

    def test_stored_schema_sequence_and_timestamp_are_strictly_typed(self) -> None:
        invalid_cases = (
            ({"schema_version": True}, ValueError),
            ({"schema_version": "1"}, ValueError),
            ({"schema_version": 2}, ValueError),
            ({"sequence": True}, TypeError),
            ({"sequence": "1"}, TypeError),
            ({"sequence": 0}, ValueError),
            ({"timestamp": 1}, TypeError),
            ({"timestamp": "not-a-time"}, ValueError),
            ({"timestamp": "2026-08-15T01:02:03"}, ValueError),
            ({"timestamp": "2026-08-15T10:02:03+09:00"}, ValueError),
            ({"timestamp": "2026-08-15T01:02:03Z"}, ValueError),
            ({"timestamp": "2026-08-15T01:02:03.000000+00:00"}, ValueError),
        )
        for overrides, error in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(error):
                    make_stored_event(**overrides)

    def test_policy_and_window_metadata_reject_inconsistent_values(self) -> None:
        for field in ("max_complete_turns", "max_utf8_bytes"):
            for invalid, error in ((True, TypeError), ("1", TypeError), (0, ValueError)):
                values: dict[str, object] = {
                    "max_complete_turns": 24,
                    "max_utf8_bytes": 32_768,
                }
                values[field] = invalid
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(error):
                        ConversationContextPolicy(**values)  # type: ignore[arg-type]

        valid = {
            "events": (),
            "total_complete_turns": 0,
            "retained_turns": 0,
            "omitted_turns": 0,
            "retained_utf8_bytes": 0,
            "truncated": False,
        }
        invalid_windows = (
            ({"events": []}, TypeError),
            ({"events": (object(),)}, TypeError),
            ({"total_complete_turns": True}, TypeError),
            ({"retained_turns": -1}, ValueError),
            ({"total_complete_turns": 1}, ValueError),
            ({"truncated": 1}, TypeError),
            ({"truncated": True}, ValueError),
        )
        for overrides, error in invalid_windows:
            values = dict(valid)
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(error):
                    ConversationContextWindow(**values)  # type: ignore[arg-type]


class ConversationContextSelectorTests(unittest.TestCase):
    """Verify pure canonical context selection from one transcript snapshot."""

    @staticmethod
    def event(
        sequence: int,
        turn_id: str,
        kind: ConversationEventKind,
        content: str,
    ) -> ConversationEvent:
        """Build one canonical event with actors derived from its visible kind."""

        if kind is ConversationEventKind.USER_MESSAGE:
            sender, recipient = "user", LEAD_ORCHESTRATOR
        elif kind is ConversationEventKind.AGENT_REPLY:
            sender, recipient = LEAD_ORCHESTRATOR, "user"
        else:
            sender, recipient = "runtime", "user"
        return make_stored_event(
            turn_id=turn_id,
            sequence=sequence,
            sender=sender,
            recipient=recipient,
            kind=kind,
            content=content,
        )

    def test_empty_unmatched_and_runtime_error_completion_are_exact(self) -> None:
        select = conversation_context_module.select_bounded_conversation_context
        self.assertEqual(
            select(()),
            ConversationContextWindow((), 0, 0, 0, 0, False),
        )

        user = self.event(
            1,
            "turn_complete",
            ConversationEventKind.USER_MESSAGE,
            "question",
        )
        runtime_error = self.event(
            2,
            "turn_complete",
            ConversationEventKind.RUNTIME_ERROR,
            "safe failure",
        )
        unmatched = self.event(
            3,
            "turn_unmatched",
            ConversationEventKind.USER_MESSAGE,
            "still visible",
        )
        selected = select((user, runtime_error, unmatched))
        self.assertEqual(selected.events, (user, runtime_error))
        self.assertEqual(selected.total_complete_turns, 1)
        self.assertEqual(selected.retained_turns, 1)
        self.assertEqual(selected.omitted_turns, 0)
        self.assertEqual(
            selected.retained_utf8_bytes,
            len("questionsafe failure".encode("utf-8")),
        )
        self.assertFalse(selected.truncated)

        incomplete = select((replace(unmatched, sequence=1),))
        self.assertEqual(incomplete.events, ())
        self.assertEqual(incomplete.total_complete_turns, 0)

    def test_turn_and_utf8_caps_keep_the_newest_whole_suffix(self) -> None:
        select = conversation_context_module.select_bounded_conversation_context
        events: list[ConversationEvent] = []
        for turn_number in range(1, 26):
            events.extend(
                (
                    self.event(
                        len(events) + 1,
                        f"turn_{turn_number}",
                        ConversationEventKind.USER_MESSAGE,
                        "q",
                    ),
                    self.event(
                        len(events) + 2,
                        f"turn_{turn_number}",
                        ConversationEventKind.AGENT_REPLY,
                        "r",
                    ),
                )
            )
        turn_bounded = select(tuple(events))
        self.assertEqual(turn_bounded.total_complete_turns, 25)
        self.assertEqual(turn_bounded.retained_turns, 24)
        self.assertEqual(turn_bounded.omitted_turns, 1)
        self.assertEqual(
            tuple(event.sequence for event in turn_bounded.events),
            tuple(range(3, 51)),
        )
        self.assertEqual(turn_bounded.retained_utf8_bytes, 48)
        self.assertTrue(turn_bounded.truncated)

        older = (
            self.event(1, "older", ConversationEventKind.USER_MESSAGE, "o"),
            self.event(2, "older", ConversationEventKind.AGENT_REPLY, "o"),
        )
        newest = (
            self.event(
                3,
                "newest",
                ConversationEventKind.USER_MESSAGE,
                "x" * (CONVERSATION_CONTEXT_POLICY.max_utf8_bytes - 1),
            ),
            self.event(4, "newest", ConversationEventKind.AGENT_REPLY, "y"),
        )
        byte_bounded = select((*older, *newest))
        self.assertEqual(byte_bounded.events, newest)
        self.assertEqual(
            byte_bounded.retained_utf8_bytes,
            CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
        )
        self.assertEqual(byte_bounded.omitted_turns, 1)
        self.assertTrue(byte_bounded.truncated)

        oversized = (
            self.event(
                1,
                "oversized",
                ConversationEventKind.USER_MESSAGE,
                "x" * CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
            ),
            self.event(
                2,
                "oversized",
                ConversationEventKind.RUNTIME_ERROR,
                "y",
            ),
        )
        self.assertEqual(select(oversized).events, ())
        self.assertEqual(select(oversized).omitted_turns, 1)

    def test_non_tuple_identity_pairing_and_contiguous_tampering_fail(self) -> None:
        select = conversation_context_module.select_bounded_conversation_context
        user = self.event(
            1,
            "turn_a",
            ConversationEventKind.USER_MESSAGE,
            "question",
        )
        reply = self.event(
            2,
            "turn_a",
            ConversationEventKind.AGENT_REPLY,
            "reply",
        )
        cases = (
            [user, reply],
            (replace(user, sequence=2),),
            (user, replace(reply, conversation_id="conversation_b")),
            (replace(reply, sequence=1), replace(user, sequence=2)),
            (user, replace(user, sequence=2)),
        )
        for events in cases:
            with self.subTest(events=events):
                with self.assertRaises((TypeError, ValueError)):
                    select(events)  # type: ignore[arg-type]


class ConversationContextValidationTests(unittest.TestCase):
    """Verify the context owner's identity and complete-turn validation."""

    @staticmethod
    def event_pair() -> tuple[ConversationEvent, ConversationEvent]:
        user = make_stored_event(
            sequence=1,
            turn_id="turn_a",
            kind=ConversationEventKind.USER_MESSAGE,
            sender="user",
            recipient=LEAD_ORCHESTRATOR,
            content="question",
        )
        reply = make_stored_event(
            sequence=2,
            turn_id="turn_a",
            kind=ConversationEventKind.AGENT_REPLY,
            sender=LEAD_ORCHESTRATOR,
            recipient="user",
            content="reply",
        )
        return user, reply

    @staticmethod
    def window(
        events: tuple[ConversationEvent, ...],
    ) -> ConversationContextWindow:
        return ConversationContextWindow(
            events=events,
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=sum(
                len(event.content.encode("utf-8")) for event in events
            ),
            truncated=False,
        )

    def test_canonical_complete_pair_returns_the_same_window(self) -> None:
        window = self.window(self.event_pair())

        returned = conversation_context_module.validate_conversation_context_window(
            window,
            session_id="session_a",
            conversation_id="conversation_a",
        )

        self.assertIs(returned, window)

    def test_identity_incomplete_pairing_and_order_tampering_are_rejected(
        self,
    ) -> None:
        user, reply = self.event_pair()
        canonical = self.window((user, reply))
        cases = (
            (
                "different session",
                canonical,
                {
                    "session_id": "session_b",
                    "conversation_id": "conversation_a",
                },
            ),
            (
                "different conversation",
                canonical,
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_b",
                },
            ),
            (
                "excluded turn",
                canonical,
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                    "excluded_turn_id": "turn_a",
                },
            ),
            (
                "incomplete turn",
                self.window((user,)),
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                },
            ),
            (
                "two user events",
                self.window((user, replace(user, sequence=2))),
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                },
            ),
            (
                "decreasing sequence",
                self.window((reply, user)),
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                },
            ),
            (
                "reply sender mismatch",
                self.window((user, replace(reply, sender=PLOT_ARCHITECT))),
                {
                    "session_id": "session_a",
                    "conversation_id": "conversation_a",
                },
            ),
        )
        for label, window, arguments in cases:
            with self.subTest(label=label), self.assertRaises(ValueError):
                conversation_context_module.validate_conversation_context_window(
                    window,
                    **arguments,
                )


class InMemoryConversationEventStoreContractTests(
    ConversationEventStoreContractMixin,
    unittest.TestCase,
):
    def make_store(self) -> ConversationEventStore:
        self.catalog = StaticConversationCatalog()
        return InMemoryConversationEventStore(self.catalog)

    def create_conversation(self, session_id: str, subject: str) -> str:
        return self.catalog.create(session_id, subject).conversation_id


class FilesystemConversationEventStoreContractTests(
    ConversationEventStoreContractMixin,
    unittest.TestCase,
):
    def make_store(self) -> ConversationEventStore:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        self.services = compose_filesystem_conversation_storage(self.workspace)
        self.catalog = self.services.catalog
        return self.services.event_store

    def create_conversation(self, session_id: str, subject: str) -> str:
        return self.catalog.create(session_id, subject).conversation_id


class _FilesystemEventStoreFixture:
    workspace: Workspace
    catalog: FilesystemConversationCatalog
    store: FilesystemConversationEventStore
    session_id: str
    conversation_id: str

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)  # type: ignore[attr-defined]
        self.workspace = Workspace(temporary.name).initialize()
        self.catalog = FilesystemConversationCatalog(self.workspace)
        self.session_id = "session_a"
        self.conversation_id = self.catalog.create(
            self.session_id,
            "Filesystem conversation",
        ).conversation_id
        self.store = FilesystemConversationEventStore(
            self.workspace,
            catalog=self.catalog,
        )

    @property
    def transcript(self) -> Path:
        return _transcript_path(
            self.workspace,
            self.session_id,
            self.conversation_id,
        )

    @property
    def logical_artifact(self) -> str:
        return f"{self.session_id}/{self.conversation_id}/transcript.jsonl"

    def request(
        self,
        turn_id: str,
        *,
        kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
        sender: str | None = None,
        recipient: str | None = None,
        content: str = "visible",
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> ConversationEventAppend:
        return make_append(
            session_id=self.session_id if session_id is None else session_id,
            conversation_id=(
                self.conversation_id
                if conversation_id is None
                else conversation_id
            ),
            turn_id=turn_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            content=content,
        )

    def stored(
        self,
        *,
        turn_id: str = "turn_a",
        sequence: int = 1,
        timestamp: str = "2026-08-15T01:02:03+00:00",
        kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
        sender: str | None = None,
        recipient: str | None = None,
        content: str = "visible",
    ) -> ConversationEvent:
        request = self.request(
            turn_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            content=content,
        )
        return make_stored_event(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            turn_id=turn_id,
            sequence=sequence,
            timestamp=timestamp,
            sender=request.sender,
            recipient=request.recipient,
            kind=kind,
            content=content,
        )

    def assert_raw_rejected_without_rewrite(self, raw: bytes) -> WorkspaceFormatError:
        self.transcript.write_bytes(raw)
        before = self.transcript.read_bytes()
        with self.assertRaises(WorkspaceFormatError) as raised:  # type: ignore[attr-defined]
            self.store.read(self.session_id, self.conversation_id)
        self.assertIn(self.logical_artifact, str(raised.exception))  # type: ignore[attr-defined]
        self.assertNotIn(  # type: ignore[attr-defined]
            str(self.workspace.root),
            str(raised.exception),
        )
        self.assertEqual(self.transcript.read_bytes(), before)  # type: ignore[attr-defined]
        self.assertEqual(  # type: ignore[attr-defined]
            _temporary_artifacts(self.transcript.parent),
            [],
        )
        return raised.exception


class FilesystemConversationEventStorePersistenceTests(
    _FilesystemEventStoreFixture,
    unittest.TestCase,
):
    def test_exact_jsonl_layout_round_trips_through_a_fresh_adapter(self) -> None:
        user = self.store.append(
            self.request("turn_a", content="line one\n\tline two\x00한🙂")
        )
        reply = self.store.append(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="visible reply",
            )
        )

        self.assertEqual(
            self.transcript.relative_to(self.workspace.root).as_posix(),
            "discussion_logs/session_a/interactive/conversations/"
            f"{self.conversation_id}/transcript.jsonl",
        )
        raw = self.transcript.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        lines = raw.splitlines()
        self.assertEqual(len(lines), 2)
        payloads = [json.loads(line) for line in lines]
        self.assertEqual(payloads, [user.to_dict(), reply.to_dict()])
        for payload in payloads:
            self.assertEqual(set(payload), set(_EVENT_FIELDS))
        self.assertEqual(
            sorted(path.name for path in self.transcript.parent.iterdir()),
            ["metadata.json", "transcript.jsonl"],
        )

        reloaded = FilesystemConversationEventStore(str(self.workspace.root))
        self.assertEqual(
            reloaded.read(self.session_id, self.conversation_id),
            (user, reply),
        )

    def test_unicode_line_separators_remain_content_not_jsonl_records(self) -> None:
        content = "before\u0085middle\u2028later\u2029after"
        event = self.store.append(
            self.request("turn_unicode_separators", content=content)
        )

        raw = self.transcript.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        payload = json.loads(raw[:-1].decode("utf-8"))
        self.assertEqual(payload["content"], content)

        reloaded = FilesystemConversationEventStore(str(self.workspace.root))
        reloaded_events = reloaded.read(self.session_id, self.conversation_id)
        self.assertEqual(reloaded_events, (event,))
        self.assertEqual(reloaded_events[0].content, content)

    def test_missing_transcript_reads_empty_without_any_mutation(self) -> None:
        before = _tree_snapshot(self.workspace.root)

        self.assertEqual(self.store.read(self.session_id, self.conversation_id), ())
        self.assertEqual(
            self.store.read_context(self.session_id, self.conversation_id).events,
            (),
        )
        self.assertEqual(_tree_snapshot(self.workspace.root), before)
        self.assertFalse(self.transcript.exists())

    def test_read_context_delegates_one_locked_snapshot_to_the_pure_selector(
        self,
    ) -> None:
        user = self.store.append(self.request("turn_a", content="question"))
        reply = self.store.append(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="reply",
            )
        )
        sentinel = ConversationContextWindow(
            events=(user, reply),
            total_complete_turns=1,
            retained_turns=1,
            omitted_turns=0,
            retained_utf8_bytes=len("questionreply".encode("utf-8")),
            truncated=False,
        )
        before = _tree_snapshot(self.workspace.root)

        def select_while_transcript_lock_is_held(
            events: tuple[ConversationEvent, ...],
        ) -> ConversationContextWindow:
            """Prove delegation occurs before the fixed snapshot lock is released."""

            event_lock = self.store._event_lock(
                self.session_id,
                self.conversation_id,
            )
            self.assertTrue(event_lock.lock.locked())
            self.assertEqual(events, (user, reply))
            return sentinel

        with mock.patch.object(
            filesystem_module,
            "select_bounded_conversation_context",
            side_effect=select_while_transcript_lock_is_held,
        ) as select:
            returned = self.store.read_context(
                self.session_id,
                self.conversation_id,
            )

        self.assertIs(returned, sentinel)
        select.assert_called_once_with((user, reply))
        self.assertEqual(_tree_snapshot(self.workspace.root), before)

    def test_workspace_path_and_string_constructors_share_the_same_identity(self) -> None:
        for root in (self.workspace, self.workspace.root, str(self.workspace.root)):
            with self.subTest(root_type=type(root).__name__):
                store = FilesystemConversationEventStore(root)
                self.assertEqual(
                    store.read(self.session_id, self.conversation_id),
                    (),
                )

    def test_catalog_identity_is_checked_on_every_public_operation(self) -> None:
        with mock.patch.object(
            self.catalog,
            "get",
            wraps=self.catalog.get,
        ) as get:
            self.store.read(self.session_id, self.conversation_id)
            self.store.read_context(self.session_id, self.conversation_id)
            self.store.append(self.request("turn_a"))

        self.assertEqual(
            get.call_args_list,
            [
                mock.call(self.session_id, self.conversation_id),
                mock.call(self.session_id, self.conversation_id),
                mock.call(self.session_id, self.conversation_id),
            ],
        )

    def test_archived_conversation_remains_authorized_for_transcript_operations(
        self,
    ) -> None:
        archived = self.catalog.archive(self.session_id, self.conversation_id)
        self.assertEqual(archived.status.value, "archived")

        user = self.store.append(self.request("turn_a"))
        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (user,),
        )
        self.assertEqual(
            self.store.read_context(self.session_id, self.conversation_id).events,
            (),
        )

    def test_missing_or_mismatched_catalog_identity_never_mutates_workspace(
        self,
    ) -> None:
        before = _tree_snapshot(self.workspace.root)
        missing = "missing_conversation"
        operations = (
            lambda: self.store.read(self.session_id, missing),
            lambda: self.store.read_context(self.session_id, missing),
            lambda: self.store.append(
                self.request("turn_missing", conversation_id=missing)
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ConversationNotFoundError):
                    operation()
                self.assertEqual(_tree_snapshot(self.workspace.root), before)

        wrong_metadata = self.catalog.get(self.session_id, self.conversation_id)
        wrong_catalog = mock.Mock(spec=ConversationCatalog)
        wrong_catalog.get.return_value = mock.Mock(
            session_id=wrong_metadata.session_id,
            conversation_id="other_conversation",
        )
        wrong_store = FilesystemConversationEventStore(
            self.workspace,
            catalog=wrong_catalog,
        )
        with self.assertRaisesRegex(WorkspaceFormatError, "catalog identity"):
            wrong_store.read(self.session_id, self.conversation_id)
        self.assertEqual(_tree_snapshot(self.workspace.root), before)


class FilesystemConversationEventStoreTamperTests(
    _FilesystemEventStoreFixture,
    unittest.TestCase,
):
    def test_utf8_json_duplicate_blank_and_schema_tampering_is_rejected(self) -> None:
        valid = self.stored().to_dict()
        missing = dict(valid)
        missing.pop("content")
        extra = dict(valid)
        extra["derived"] = "not authoritative"
        pairs = list(valid.items()) + [("kind", "user_message")]
        duplicate = "{" + ",".join(
            f"{json.dumps(key)}:{json.dumps(value)}" for key, value in pairs
        ) + "}\n"
        cases = {
            "invalid UTF-8": b"\xff\n",
            "malformed JSON": b"{not-json\n",
            "duplicate field": duplicate.encode("utf-8"),
            "zero-byte artifact": b"",
            "blank record": b"\n",
            "blank record after event": _jsonl_bytes([valid]) + b"\n",
            "non-object": _jsonl_bytes([[valid]]),
            "missing field": _jsonl_bytes([missing]),
            "extra field": _jsonl_bytes([extra]),
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                self.assert_raw_rejected_without_rewrite(raw)

    def test_wrong_persisted_types_and_values_are_rejected(self) -> None:
        base = self.stored().to_dict()
        cases: tuple[tuple[str, str, object], ...] = (
            ("schema bool", "schema_version", True),
            ("schema string", "schema_version", "1"),
            ("unsupported schema", "schema_version", 2),
            ("session type", "session_id", 1),
            ("conversation type", "conversation_id", None),
            ("turn type", "turn_id", []),
            ("unsafe turn", "turn_id", "../escape"),
            ("sequence bool", "sequence", True),
            ("sequence string", "sequence", "1"),
            ("timestamp type", "timestamp", 1),
            ("malformed timestamp", "timestamp", "not-a-time"),
            ("naive timestamp", "timestamp", "2026-08-15T01:02:03"),
            ("non-UTC timestamp", "timestamp", "2026-08-15T10:02:03+09:00"),
            ("noncanonical timestamp", "timestamp", "2026-08-15T01:02:03Z"),
            ("sender type", "sender", 1),
            ("recipient type", "recipient", None),
            ("invalid actors", "sender", LEAD_ORCHESTRATOR),
            ("kind type", "kind", 1),
            ("unknown kind", "kind", "deleted"),
            ("content type", "content", ["text"]),
            ("empty content", "content", ""),
            ("invalid UTF-8 content", "content", "invalid-\ud800"),
        )
        for label, field, invalid in cases:
            payload = dict(base)
            payload[field] = invalid
            with self.subTest(label=label):
                self.assert_raw_rejected_without_rewrite(_jsonl_bytes([payload]))

    def test_path_owner_and_sequence_tampering_are_rejected(self) -> None:
        user = self.stored(turn_id="turn_a", sequence=1).to_dict()
        reply = self.stored(
            turn_id="turn_a",
            sequence=2,
            kind=ConversationEventKind.AGENT_REPLY,
        ).to_dict()
        path_cases = (
            ("session", {**user, "session_id": "other_session"}),
            ("conversation", {**user, "conversation_id": "other_conversation"}),
        )
        for label, payload in path_cases:
            with self.subTest(label=label):
                self.assert_raw_rejected_without_rewrite(_jsonl_bytes([payload]))

        sequence_cases = {
            "starts at two": [{**user, "sequence": 2}],
            "gap": [user, {**reply, "sequence": 3}],
            "duplicate": [user, {**reply, "sequence": 1}],
            "reversed": [{**user, "sequence": 2}, {**reply, "sequence": 1}],
        }
        for label, payloads in sequence_cases.items():
            with self.subTest(label=label):
                self.assert_raw_rejected_without_rewrite(_jsonl_bytes(payloads))

    def test_pairing_tampering_is_rejected_without_rewrite(self) -> None:
        user = self.stored(turn_id="turn_a", sequence=1).to_dict()
        reply = self.stored(
            turn_id="turn_a",
            sequence=2,
            kind=ConversationEventKind.AGENT_REPLY,
        ).to_dict()
        orphan = self.stored(
            turn_id="orphan",
            sequence=1,
            kind=ConversationEventKind.AGENT_REPLY,
        ).to_dict()
        duplicate_user = self.stored(
            turn_id="turn_a",
            sequence=2,
        ).to_dict()
        error = self.stored(
            turn_id="turn_a",
            sequence=3,
            kind=ConversationEventKind.RUNTIME_ERROR,
        ).to_dict()
        mismatched = self.stored(
            turn_id="turn_a",
            sequence=2,
            kind=ConversationEventKind.AGENT_REPLY,
            sender=PLOT_ARCHITECT,
        ).to_dict()
        cases = {
            "outcome before user": [orphan],
            "duplicate user": [user, duplicate_user],
            "duplicate outcome": [user, reply, error],
            "reply sender mismatch": [user, mismatched],
        }
        for label, payloads in cases.items():
            with self.subTest(label=label):
                self.assert_raw_rejected_without_rewrite(_jsonl_bytes(payloads))

    def test_every_operation_validates_tampered_history_without_rewriting(self) -> None:
        self.transcript.write_bytes(b"{not-json\n")
        before = self.transcript.read_bytes()
        operations = (
            lambda: self.store.read(self.session_id, self.conversation_id),
            lambda: self.store.read_context(self.session_id, self.conversation_id),
            lambda: self.store.append(self.request("turn_a")),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(WorkspaceFormatError) as raised:
                    operation()
                self.assertIn(self.logical_artifact, str(raised.exception))
                self.assertEqual(self.transcript.read_bytes(), before)
                self.assertEqual(_temporary_artifacts(self.transcript.parent), [])

    def test_equal_and_decreasing_timestamps_do_not_override_sequence_order(
        self,
    ) -> None:
        later = "2026-08-15T02:00:00+00:00"
        earlier = "2026-08-15T01:00:00+00:00"
        events = (
            self.stored(turn_id="turn_a", sequence=1, timestamp=later),
            self.stored(
                turn_id="turn_a",
                sequence=2,
                timestamp=earlier,
                kind=ConversationEventKind.AGENT_REPLY,
            ),
            self.stored(turn_id="turn_b", sequence=3, timestamp=earlier),
            self.stored(
                turn_id="turn_b",
                sequence=4,
                timestamp=earlier,
                kind=ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.transcript.write_bytes(
            _jsonl_bytes([event.to_dict() for event in events])
        )

        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            events,
        )
        self.assertEqual(
            self.store.read_context(self.session_id, self.conversation_id).events,
            events,
        )


class FilesystemConversationEventStoreFailureTests(
    _FilesystemEventStoreFixture,
    unittest.TestCase,
):
    def test_failed_first_replace_leaves_no_transcript_and_does_not_retry(self) -> None:
        with mock.patch.object(
            workspace_core_module.os,
            "replace",
            side_effect=OSError("simulated first replace failure"),
        ) as replace:
            with self.assertRaisesRegex(OSError, "first replace failure"):
                self.store.append(self.request("turn_a"))

        self.assertEqual(replace.call_count, 1)
        self.assertFalse(self.transcript.exists())
        self.assertEqual(_temporary_artifacts(self.transcript.parent), [])
        self.assertEqual(self.store.read(self.session_id, self.conversation_id), ())

    def test_failed_replace_preserves_previous_complete_transcript(self) -> None:
        user = self.store.append(self.request("turn_a"))
        before = self.transcript.read_bytes()
        with mock.patch.object(
            workspace_core_module.os,
            "replace",
            side_effect=OSError("simulated replace failure"),
        ) as replace:
            with self.assertRaisesRegex(OSError, "replace failure"):
                self.store.append(
                    self.request(
                        "turn_a",
                        kind=ConversationEventKind.AGENT_REPLY,
                    )
                )

        self.assertEqual(replace.call_count, 1)
        self.assertEqual(self.transcript.read_bytes(), before)
        self.assertEqual(_temporary_artifacts(self.transcript.parent), [])
        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (user,),
        )

    def test_post_replace_sync_failure_leaves_a_complete_new_transcript(self) -> None:
        user = self.store.append(self.request("turn_a"))
        original_sync = self.workspace._fsync_directory

        def fail_transcript_sync(directory: Path) -> None:
            if directory == self.transcript.parent:
                raise OSError("simulated transcript directory sync failure")
            original_sync(directory)

        with mock.patch.object(
            self.workspace,
            "_fsync_directory",
            side_effect=fail_transcript_sync,
        ):
            with self.assertRaisesRegex(OSError, "directory sync failure"):
                self.store.append(
                    self.request(
                        "turn_a",
                        kind=ConversationEventKind.AGENT_REPLY,
                    )
                )

        events = self.store.read(self.session_id, self.conversation_id)
        self.assertEqual(events[0], user)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].kind, ConversationEventKind.AGENT_REPLY)
        self.assertEqual(_temporary_artifacts(self.transcript.parent), [])
        self.assertEqual(
            [json.loads(line) for line in self.transcript.read_bytes().splitlines()],
            [event.to_dict() for event in events],
        )


class FilesystemConversationEventStoreSecurityTests(
    _FilesystemEventStoreFixture,
    unittest.TestCase,
):
    def test_transcript_symlink_is_rejected_without_touching_outside_data(self) -> None:
        self.store.append(self.request("turn_a"))
        outside_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temporary.cleanup)
        outside = Path(outside_temporary.name) / "outside.jsonl"
        outside.write_bytes(b"outside must stay unchanged\n")
        self.transcript.unlink()
        try:
            self.transcript.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symbolic links unavailable: {exc}")

        before = outside.read_bytes()
        for operation in (
            lambda: self.store.read(self.session_id, self.conversation_id),
            lambda: self.store.append(self.request("turn_b")),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(WorkspaceSecurityError):
                    operation()
                self.assertEqual(outside.read_bytes(), before)

    def test_catalog_and_conversation_directory_symlinks_are_rejected(self) -> None:
        for case in ("catalog", "conversation"):
            with self.subTest(case=case):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                workspace = Workspace(temporary.name).initialize()
                catalog = FilesystemConversationCatalog(workspace)
                metadata = catalog.create("session_a", f"Symlink {case}")
                store = FilesystemConversationEventStore(workspace, catalog=catalog)
                conversation_directory = _transcript_path(
                    workspace,
                    "session_a",
                    metadata.conversation_id,
                ).parent
                catalog_directory = conversation_directory.parent
                metadata_path = conversation_directory / "metadata.json"
                outside_temporary = tempfile.TemporaryDirectory()
                self.addCleanup(outside_temporary.cleanup)
                outside = Path(outside_temporary.name)
                metadata_path.unlink()
                conversation_directory.rmdir()
                target = conversation_directory
                if case == "catalog":
                    catalog_directory.rmdir()
                    target = catalog_directory
                try:
                    target.symlink_to(outside, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"symbolic links unavailable: {exc}")

                with self.assertRaises(WorkspaceSecurityError):
                    store.read("session_a", metadata.conversation_id)
                self.assertEqual(list(outside.iterdir()), [])

    def test_non_file_transcript_is_rejected_without_mutation(self) -> None:
        self.transcript.mkdir()
        before = _tree_snapshot(self.workspace.root)
        with self.assertRaisesRegex(WorkspaceFormatError, "not a file") as raised:
            self.store.read(self.session_id, self.conversation_id)
        self.assertIn(self.logical_artifact, str(raised.exception))
        self.assertEqual(_tree_snapshot(self.workspace.root), before)

    def test_unsafe_call_identifiers_cannot_escape_the_workspace(self) -> None:
        outside = self.workspace.root.parent / "must-not-be-created-by-event-store"
        self.assertFalse(outside.exists())
        operations = (
            lambda: self.store.read("../outside", self.conversation_id),
            lambda: self.store.read_context(self.session_id, "../outside"),
            lambda: self.store.read("/absolute", self.conversation_id),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(SessionIdError):
                    operation()
        self.assertFalse(outside.exists())


class FilesystemConversationEventStoreConcurrencyTests(
    _FilesystemEventStoreFixture,
    unittest.TestCase,
):
    def test_process_wide_lock_is_shared_and_concurrent_sequences_are_contiguous(
        self,
    ) -> None:
        first = FilesystemConversationEventStore(
            Workspace(self.workspace.root),
            catalog=FilesystemConversationCatalog(Workspace(self.workspace.root)),
        )
        second = FilesystemConversationEventStore(
            Workspace(self.workspace.root),
            catalog=FilesystemConversationCatalog(Workspace(self.workspace.root)),
        )
        self.assertIs(
            first._event_lock(self.session_id, self.conversation_id),
            second._event_lock(self.session_id, self.conversation_id),
        )

        worker_count = 16
        barrier = threading.Barrier(worker_count)

        def append_user(index: int) -> ConversationEvent:
            barrier.wait(timeout=5)
            adapter = first if index % 2 == 0 else second
            return adapter.append(
                make_append(
                    session_id=self.session_id,
                    conversation_id=self.conversation_id,
                    turn_id=f"turn_{index:02d}",
                    content=f"user {index}",
                )
            )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            user_futures = [
                executor.submit(append_user, index)
                for index in range(worker_count)
            ]
            users = [future.result(timeout=10) for future in user_futures]
        self.assertEqual(
            sorted(event.sequence for event in users),
            list(range(1, worker_count + 1)),
        )

        barrier = threading.Barrier(worker_count)

        def append_reply(index: int) -> ConversationEvent:
            barrier.wait(timeout=5)
            adapter = second if index % 2 == 0 else first
            return adapter.append(
                make_append(
                    session_id=self.session_id,
                    conversation_id=self.conversation_id,
                    turn_id=f"turn_{index:02d}",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=f"reply {index}",
                )
            )

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            reply_futures = [
                executor.submit(append_reply, index)
                for index in range(worker_count)
            ]
            replies = [future.result(timeout=10) for future in reply_futures]
        self.assertEqual(
            sorted(event.sequence for event in replies),
            list(range(worker_count + 1, worker_count * 2 + 1)),
        )

        events = self.store.read(self.session_id, self.conversation_id)
        self.assertEqual(
            tuple(event.sequence for event in events),
            tuple(range(1, worker_count * 2 + 1)),
        )
        for index in range(worker_count):
            matching = [
                event for event in events if event.turn_id == f"turn_{index:02d}"
            ]
            self.assertEqual(len(matching), 2)
            self.assertEqual(
                {event.kind for event in matching},
                {
                    ConversationEventKind.USER_MESSAGE,
                    ConversationEventKind.AGENT_REPLY,
                },
            )


class ConversationEventPackageBoundaryTests(unittest.TestCase):
    def test_public_signatures_protocol_and_exports_are_exact(self) -> None:
        self.assertEqual(set(conversation_events_module.__all__), _EVENT_EXPORTS)
        self.assertEqual(len(conversation_events_module.__all__), len(_EVENT_EXPORTS))
        self.assertEqual(set(conversation_context_module.__all__), _CONTEXT_EXPORTS)
        self.assertEqual(
            len(conversation_context_module.__all__),
            len(_CONTEXT_EXPORTS),
        )
        for owner, names in (
            (conversation_events_module, _EVENT_EXPORTS),
            (conversation_context_module, _CONTEXT_EXPORTS),
        ):
            for name in names:
                with self.subTest(owner=owner.__name__, name=name):
                    self.assertIs(
                        getattr(owner, name),
                        getattr(conversations_module, name),
                    )

        self.assertIs(
            ConversationEventStore,
            conversation_context_module.ConversationEventStore,
        )
        self.assertTrue(getattr(ConversationEventStore, "_is_protocol", False))
        self.assertEqual(
            set(
                name
                for name, member in vars(ConversationEventStore).items()
                if not name.startswith("_") and callable(member)
            ),
            {"append", "read", "read_context"},
        )
        append_hints = get_type_hints(ConversationEventStore.append)
        read_hints = get_type_hints(ConversationEventStore.read)
        context_hints = get_type_hints(ConversationEventStore.read_context)
        self.assertIs(append_hints["event"], ConversationEventAppend)
        self.assertIs(append_hints["return"], ConversationEvent)
        self.assertEqual(read_hints["return"], tuple[ConversationEvent, ...])
        self.assertIs(context_hints["return"], ConversationContextWindow)

        select = conversation_context_module.select_bounded_conversation_context
        validate = conversation_context_module.validate_conversation_context_window
        self.assertEqual(tuple(inspect.signature(select).parameters), ("events",))
        validate_signature = inspect.signature(validate)
        self.assertEqual(
            tuple(validate_signature.parameters),
            (
                "context",
                "session_id",
                "conversation_id",
                "excluded_turn_id",
            ),
        )
        self.assertIs(
            validate_signature.parameters["context"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for name in ("session_id", "conversation_id", "excluded_turn_id"):
            self.assertIs(
                validate_signature.parameters[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )
        self.assertIsNone(validate_signature.parameters["conversation_id"].default)
        self.assertIsNone(
            validate_signature.parameters["excluded_turn_id"].default
        )
        self.assertEqual(
            get_type_hints(select),
            {
                "events": tuple[ConversationEvent, ...],
                "return": ConversationContextWindow,
            },
        )
        self.assertEqual(
            get_type_hints(validate),
            {
                "context": ConversationContextWindow,
                "session_id": str,
                "conversation_id": str | None,
                "excluded_turn_id": str | None,
                "return": ConversationContextWindow,
            },
        )

        constructor = inspect.signature(FilesystemConversationEventStore)
        self.assertEqual(tuple(constructor.parameters), ("workspace", "catalog"))
        self.assertIs(
            constructor.parameters["workspace"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        self.assertIs(
            constructor.parameters["catalog"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(constructor.parameters["catalog"].default)

        expected = {
            "CONVERSATION_CONTEXT_POLICY",
            "CONVERSATION_EVENT_SCHEMA_VERSION",
            "ConversationContextPolicy",
            "ConversationContextWindow",
            "ConversationEvent",
            "ConversationEventAppend",
            "ConversationEventKind",
            "ConversationEventStore",
            "FilesystemConversationEventStore",
        }
        self.assertTrue(expected.issubset(set(story_writing_agents.__all__)))
        for name in expected.difference({"FilesystemConversationEventStore"}):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(conversations_module, name),
                )
        self.assertIs(
            story_writing_agents.FilesystemConversationEventStore,
            workspace_module.FilesystemConversationEventStore,
        )
        self.assertIs(
            story_writing_agents.FilesystemConversationEventStore,
            filesystem_module.FilesystemConversationEventStore,
        )
        self.assertEqual(
            filesystem_module.FilesystemConversationEventStore.__module__,
            filesystem_module.__name__,
        )

    def test_package_and_boundary_modules_import_cleanly_without_side_effects(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source = repository_root / "src"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        code = f"""
import sys
from typing import get_type_hints
sys.path.insert(0, {str(source)!r})
import story_writing_agents.conversations.context as context
import story_writing_agents.conversations.events as events
import story_writing_agents.conversations as conversations
import story_writing_agents
import story_writing_agents.conversation_storage.filesystem as filesystem
import story_writing_agents.workspace as workspace
assert story_writing_agents.ConversationEvent is conversations.ConversationEvent
assert story_writing_agents.ConversationEventStore is conversations.ConversationEventStore
assert events.ConversationEvent is conversations.ConversationEvent
assert context.ConversationContextWindow is conversations.ConversationContextWindow
assert context.ConversationEventStore is conversations.ConversationEventStore
assert context.ConversationEventStore.__module__ == "story_writing_agents.conversations"
assert (
    get_type_hints(context.ConversationEventStore.read_context)["return"]
    is conversations.ConversationContextWindow
)
assert (
    story_writing_agents.FilesystemConversationEventStore
    is workspace.FilesystemConversationEventStore
    is filesystem.FilesystemConversationEventStore
)
assert filesystem.__all__ == [
    "FilesystemConversationCatalog",
    "FilesystemConversationEventStore",
]
assert filesystem.FilesystemConversationEventStore.__module__ == filesystem.__name__
assert set({sorted(_EVENT_FIELDS)!r}) == set(conversations.ConversationEvent.__dataclass_fields__)
assert set({sorted(_APPEND_FIELDS)!r}) == set(
    conversations.ConversationEventAppend.__dataclass_fields__
)
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-I", "-c", code],
            cwd=temporary.name,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(list(Path(temporary.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
