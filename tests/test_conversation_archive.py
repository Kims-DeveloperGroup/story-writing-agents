"""Canonical, bounded, lossless conversation archive contract tests."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import cast
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage
from story_writing_agents import ConversationEventKind, ConversationStatus
from story_writing_agents.conversation_storage import (
    CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES,
    CONVERSATION_ARCHIVE_MAX_CONVERSATIONS,
    CONVERSATION_ARCHIVE_MAX_EVENTS,
    CONVERSATION_ARCHIVE_MAX_RAW_BYTES,
    CONVERSATION_ARCHIVE_MAX_SESSIONS,
    CONVERSATION_ARCHIVE_SCHEMA_VERSION,
    ConversationArchive,
    ConversationArchiveBoundsError,
    ConversationArchiveConflictError,
    ConversationArchiveDestinationChangedError,
    ConversationArchiveDestinationError,
    ConversationArchiveDigestError,
    ConversationArchiveError,
    ConversationArchiveEvent,
    ConversationArchiveFormatError,
    ConversationArchiveSession,
    ConversationArchiveSourceChangedError,
    ConversationArchiveSourceError,
    ConversationArchiveVersionError,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
from story_writing_agents.conversation_storage import archive as archive_module
from story_writing_agents.conversation_storage import (
    filesystem_archive as filesystem_archive_module,
)

from tests.conversation_archive_fixture import (
    CREATED_AT,
    SECOND_AT,
    archive_conversation,
    archive_event,
    archive_events,
    compact_json,
    nonempty_archive,
    one_conversation_archive,
)


EMPTY_GOLDEN = (
    b'{"schema_version":1,"sessions":[],"digest":{"algorithm":"sha256",'
    b'"value":"7712f58e5e3ce7e4aaf8d09f06c4a60771fee9d848148a84b25ed1589e3791c3"}}\n'
)
NONEMPTY_DIGEST_IMAGE = (
    '{"schema_version":1,"sessions":[{"session_id":"session_a",'
    '"conversations":[{"schema_version":1,"session_id":"session_a",'
    '"conversation_id":"conversation_a","subject":"안녕 🌙",'
    '"subject_revision":1,"status":"archived",'
    '"created_at":"2026-01-01T00:00:00+00:00",'
    '"updated_at":"2026-01-01T00:00:02+00:00","events":['
    '{"schema_version":1,"session_id":"session_a",'
    '"conversation_id":"conversation_a","turn_id":"turn_a",'
    '"sequence":1,"timestamp":"2026-01-01T00:00:00+00:00",'
    '"sender":"user","recipient":"lead_orchestrator",'
    '"kind":"user_message","content":"질문\\u0000끝"},'
    '{"schema_version":1,"session_id":"session_a",'
    '"conversation_id":"conversation_a","turn_id":"turn_a",'
    '"sequence":2,"timestamp":"2026-01-01T00:00:01+00:00",'
    '"sender":"runtime","recipient":"user",'
    '"kind":"runtime_error","content":"실패"}]}]}]}\n'
).encode()
NONEMPTY_GOLDEN = (
    NONEMPTY_DIGEST_IMAGE[:-2]
    + b',"digest":{"algorithm":"sha256","value":'
    + b'"4cb79e8af92f5798faeb94ed6f4167ed9c4fc362fbce9d5516489484e8b6845d"}}\n'
)


class ConversationArchiveSurfaceTests(unittest.TestCase):
    def test_public_surfaces_and_caps_are_exact(self) -> None:
        expected_archive = {
            "CONVERSATION_ARCHIVE_SCHEMA_VERSION",
            "CONVERSATION_ARCHIVE_MAX_RAW_BYTES",
            "CONVERSATION_ARCHIVE_MAX_SESSIONS",
            "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
            "CONVERSATION_ARCHIVE_MAX_EVENTS",
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
            "ConversationArchive",
            "ConversationArchiveSession",
            "ConversationArchiveConversation",
            "ConversationArchiveEvent",
            "ConversationArchiveError",
            "ConversationArchiveFormatError",
            "ConversationArchiveVersionError",
            "ConversationArchiveBoundsError",
            "ConversationArchiveDigestError",
            "ConversationArchiveSourceError",
            "ConversationArchiveSourceChangedError",
            "ConversationArchiveDestinationError",
            "ConversationArchiveConflictError",
            "ConversationArchiveDestinationChangedError",
            "serialize_conversation_archive",
            "deserialize_conversation_archive",
        }
        self.assertEqual(len(archive_module.__all__), 22)
        self.assertEqual(set(archive_module.__all__), expected_archive)
        self.assertEqual(
            filesystem_archive_module.__all__,
            [
                "FilesystemConversationArchiveAdapter",
                "compose_filesystem_conversation_archive",
            ],
        )
        self.assertEqual(len(storage.__all__), 64)
        self.assertEqual(len(story_writing_agents.__all__), 120)
        for name in expected_archive | set(filesystem_archive_module.__all__):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(storage, name),
                    getattr(
                        archive_module
                        if name in expected_archive
                        else filesystem_archive_module,
                        name,
                    ),
                )
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

        self.assertEqual(CONVERSATION_ARCHIVE_SCHEMA_VERSION, 1)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_RAW_BYTES, 67_108_864)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_SESSIONS, 1_024)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_CONVERSATIONS, 65_536)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_EVENTS, 1_000_000)
        self.assertEqual(
            CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES,
            33_554_432,
        )
        for short_name in (
            "MAX_RAW_BYTES",
            "MAX_SESSIONS",
            "MAX_CONVERSATIONS",
            "MAX_EVENTS",
            "MAX_AGGREGATE_UTF8_BYTES",
        ):
            with self.subTest(short_name=short_name):
                self.assertNotIn(short_name, archive_module.__all__)
                self.assertFalse(hasattr(archive_module, short_name))
                self.assertNotIn(short_name, storage.__all__)
                self.assertFalse(hasattr(storage, short_name))

    def test_error_hierarchy_and_messages_are_fixed_and_content_free(self) -> None:
        cases: tuple[tuple[type[ConversationArchiveError], str], ...] = (
            (ConversationArchiveError, "conversation archive error"),
            (
                ConversationArchiveFormatError,
                "conversation archive format is invalid",
            ),
            (
                ConversationArchiveVersionError,
                "conversation archive version is unsupported",
            ),
            (
                ConversationArchiveBoundsError,
                "conversation archive bounds exceeded",
            ),
            (
                ConversationArchiveDigestError,
                "conversation archive digest is invalid",
            ),
            (
                ConversationArchiveSourceError,
                "conversation archive source is invalid",
            ),
            (
                ConversationArchiveSourceChangedError,
                "conversation archive source changed",
            ),
            (
                ConversationArchiveDestinationError,
                "conversation archive destination is invalid",
            ),
            (
                ConversationArchiveConflictError,
                "conversation archive destination conflicts",
            ),
            (
                ConversationArchiveDestinationChangedError,
                "conversation archive destination changed",
            ),
        )
        for error_type, message in cases:
            with self.subTest(error_type=error_type.__name__):
                error = error_type()
                self.assertIsInstance(error, ConversationArchiveError)
                self.assertEqual(str(error), message)
                self.assertNotIn("secret-sentinel", str(error))
        self.assertIsInstance(
            ConversationArchiveSourceChangedError(),
            ConversationArchiveSourceError,
        )
        for error in (
            ConversationArchiveConflictError(),
            ConversationArchiveDestinationChangedError(),
        ):
            self.assertIsInstance(error, ConversationArchiveDestinationError)


class ConversationArchiveValueTests(unittest.TestCase):
    def test_values_are_frozen_slotted_and_canonicalize_every_order(self) -> None:
        user, outcome = archive_events(conversation_id="conversation_b")
        later = archive_conversation(
            conversation_id="conversation_b",
            created_at=SECOND_AT,
            events=(outcome, user),
        )
        earlier = archive_conversation(conversation_id="conversation_a")
        session_a = ConversationArchiveSession(
            "session_a",
            (later, earlier),
        )
        session_z = ConversationArchiveSession(
            "session_z",
            (
                archive_conversation(
                    session_id="session_z",
                    conversation_id="conversation_z",
                ),
            ),
        )
        archive = ConversationArchive(1, (session_z, session_a))

        self.assertEqual(
            tuple(session.session_id for session in archive.sessions),
            ("session_a", "session_z"),
        )
        self.assertEqual(
            tuple(
                conversation.conversation_id
                for conversation in archive.sessions[0].conversations
            ),
            ("conversation_a", "conversation_b"),
        )
        self.assertEqual(
            tuple(
                event.sequence for event in archive.sessions[0].conversations[1].events
            ),
            (1, 2),
        )
        with self.assertRaises(FrozenInstanceError):
            archive.schema_version = 2  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            archive.extra = True  # type: ignore[attr-defined]

    def test_empty_archive_empty_history_archived_and_runtime_error_are_valid(
        self,
    ) -> None:
        empty = ConversationArchive(1, ())
        self.assertEqual(empty.sessions, ())
        historyless = one_conversation_archive(
            status=ConversationStatus.ARCHIVED,
        )
        self.assertEqual(historyless.sessions[0].conversations[0].events, ())

        runtime_error = nonempty_archive()
        conversation = runtime_error.sessions[0].conversations[0]
        self.assertIs(conversation.status, ConversationStatus.ARCHIVED)
        self.assertIs(
            conversation.events[1].kind,
            ConversationEventKind.RUNTIME_ERROR,
        )
        self.assertEqual(conversation.subject, "안녕 🌙")
        self.assertEqual(conversation.events[0].content, "질문\x00끝")

    def test_duplicate_and_cross_identity_values_fail_closed(self) -> None:
        conversation = archive_conversation()
        user, outcome = archive_events()
        cases: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchiveSession("session_a", ()),
            lambda: ConversationArchiveSession(
                "session_a", (conversation, conversation)
            ),
            lambda: ConversationArchive(
                1,
                (
                    ConversationArchiveSession("session_a", (conversation,)),
                    ConversationArchiveSession("session_a", (conversation,)),
                ),
            ),
            lambda: ConversationArchiveSession("session_b", (conversation,)),
            lambda: archive_conversation(
                session_id="session_a",
                events=(archive_event(session_id="session_b"),),
            ),
            lambda: archive_conversation(events=(user, user)),
            lambda: archive_conversation(events=(outcome,)),
            lambda: archive_conversation(events=(archive_event(sequence=2),)),
        )
        for index, operation in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    operation()

    def test_versions_types_actors_metadata_and_utf8_are_strict(self) -> None:
        invalid_operations: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchive(True, ()),
            lambda: ConversationArchive(2, ()),
            lambda: ConversationArchive(1, []),  # type: ignore[arg-type]
            lambda: ConversationArchiveSession(
                "session_a",
                [archive_conversation()],  # type: ignore[arg-type]
            ),
            lambda: archive_conversation(subject="bad\x00subject"),
            lambda: archive_conversation(subject_revision=0),
            lambda: archive_conversation(updated_at=CREATED_AT[:-6]),
            lambda: archive_event(sender="agent"),
            lambda: archive_event(content=""),
            lambda: archive_event(content="\ud800"),
            lambda: ConversationArchiveEvent(
                schema_version=2,
                session_id="session_a",
                conversation_id="conversation_a",
                turn_id="turn_a",
                sequence=1,
                timestamp=CREATED_AT,
                sender="user",
                recipient="lead_orchestrator",
                kind=ConversationEventKind.USER_MESSAGE,
                content="question",
            ),
        )
        for index, operation in enumerate(invalid_operations):
            with self.subTest(index=index):
                with self.assertRaises(
                    (ConversationArchiveFormatError, ConversationArchiveVersionError)
                ):
                    operation()

    def test_global_and_local_bounds_are_enforced(self) -> None:
        conversation_a = archive_conversation()
        conversation_b = archive_conversation(conversation_id="conversation_b")
        session_a = ConversationArchiveSession("session_a", (conversation_a,))
        session_b = ConversationArchiveSession(
            "session_b",
            (
                archive_conversation(
                    session_id="session_b",
                    conversation_id="conversation_b",
                ),
            ),
        )
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_SESSIONS", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchive(1, (session_a, session_b))
        with mock.patch.object(
            archive_module, "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS", 1
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchiveSession(
                    "session_a", (conversation_a, conversation_b)
                )
        user, outcome = archive_events()
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_EVENTS", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_conversation(events=(user, outcome))
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_conversation(subject="S", events=(user,))
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_RAW_BYTES", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                serialize_conversation_archive(ConversationArchive(1, ()))
            with self.assertRaises(ConversationArchiveBoundsError):
                deserialize_conversation_archive(b"{}")

    def test_shape_preflight_short_circuits_before_poisoned_descendants(
        self,
    ) -> None:
        class PoisonedDescendant:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("poisoned descendant was inspected")

        poison = PoisonedDescendant()
        container_cases: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchive(1, cast(object, [poison])),
            lambda: ConversationArchiveSession(
                "session_a",
                cast(object, [poison]),
            ),
            lambda: archive_conversation(events=cast(object, [poison])),
        )
        for index, operation in enumerate(container_cases):
            with self.subTest(container=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    operation()

        bounded_cases = (
            (
                "CONVERSATION_ARCHIVE_MAX_SESSIONS",
                lambda: ConversationArchive(1, cast(object, (poison,))),
            ),
            (
                "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
                lambda: ConversationArchiveSession(
                    "session_a",
                    cast(object, (poison,)),
                ),
            ),
            (
                "CONVERSATION_ARCHIVE_MAX_EVENTS",
                lambda: archive_conversation(events=cast(object, (poison,))),
            ),
        )
        for constant, operation in bounded_cases:
            with self.subTest(bound=constant):
                with mock.patch.object(archive_module, constant, 0):
                    with self.assertRaises(ConversationArchiveBoundsError):
                        operation()

        shallow_cases: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchive(1, cast(object, (poison,))),
            lambda: ConversationArchiveSession(
                "session_a",
                cast(object, (poison,)),
            ),
            lambda: archive_conversation(events=cast(object, (poison,))),
        )
        for index, operation in enumerate(shallow_cases):
            with self.subTest(shallow=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    operation()

    def test_global_preflight_and_utf8_budget_stop_before_later_poison(
        self,
    ) -> None:
        class PoisonedDescendant:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("later poisoned content was inspected")

        first = archive_conversation(
            session_id="session_a",
            conversation_id="conversation_a",
        )
        second = archive_conversation(
            session_id="session_b",
            conversation_id="conversation_b",
        )
        first_session = ConversationArchiveSession("session_a", (first,))
        second_session = ConversationArchiveSession("session_b", (second,))
        object.__setattr__(second, "events", PoisonedDescendant())
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchive(1, (first_session, second_session))

        first_events = archive_events(conversation_id="conversation_a")
        second_events = archive_events(conversation_id="conversation_b")
        first = archive_conversation(
            conversation_id="conversation_a",
            events=first_events,
        )
        second = archive_conversation(
            conversation_id="conversation_b",
            events=second_events,
        )
        object.__setattr__(second_events[-1], "content", PoisonedDescendant())
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_EVENTS",
            3,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchiveSession("session_a", (first, second))

        first = archive_conversation(
            conversation_id="conversation_a",
            subject="XX",
        )
        second = archive_conversation(
            conversation_id="conversation_b",
            subject="Later",
        )
        session = ConversationArchiveSession("session_a", (first, second))
        object.__setattr__(second, "subject", PoisonedDescendant())
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchive(1, (session,))

        user, outcome = archive_events(user_content="XX")
        object.__setattr__(outcome, "content", PoisonedDescendant())
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
            2,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_conversation(subject="S", events=(user, outcome))

    def test_serializer_shape_preflight_stops_before_corrupted_descendants(
        self,
    ) -> None:
        class PoisonedDescendant:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("corrupted descendant was inspected")

        poison = PoisonedDescendant()

        def corrupted_archive(field: str, value: object) -> ConversationArchive:
            archive = one_conversation_archive()
            if field == "sessions":
                target = archive
            elif field == "conversations":
                target = archive.sessions[0]
            else:
                target = archive.sessions[0].conversations[0]
            object.__setattr__(target, field, value)
            return archive

        cases = (
            ("sessions", "CONVERSATION_ARCHIVE_MAX_SESSIONS"),
            ("conversations", "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS"),
            ("events", "CONVERSATION_ARCHIVE_MAX_EVENTS"),
        )
        for field, constant in cases:
            with self.subTest(field=field, failure="container"):
                with self.assertRaises(ConversationArchiveFormatError):
                    serialize_conversation_archive(corrupted_archive(field, [poison]))
            with self.subTest(field=field, failure="bound"):
                with mock.patch.object(archive_module, constant, 0):
                    with self.assertRaises(ConversationArchiveBoundsError):
                        serialize_conversation_archive(
                            corrupted_archive(field, (poison,))
                        )
            with self.subTest(field=field, failure="shallow_type"):
                with self.assertRaises(ConversationArchiveFormatError):
                    serialize_conversation_archive(corrupted_archive(field, (poison,)))

        first = archive_conversation(
            session_id="session_a",
            conversation_id="conversation_a",
        )
        second = archive_conversation(
            session_id="session_b",
            conversation_id="conversation_b",
        )
        second_session = ConversationArchiveSession("session_b", (second,))
        archive = ConversationArchive(
            1,
            (
                ConversationArchiveSession("session_a", (first,)),
                second_session,
            ),
        )
        object.__setattr__(second, "events", poison)
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                serialize_conversation_archive(archive)

        first_events = archive_events(conversation_id="conversation_a")
        second_events = archive_events(conversation_id="conversation_b")
        first = archive_conversation(
            conversation_id="conversation_a",
            events=first_events,
        )
        second = archive_conversation(
            conversation_id="conversation_b",
            events=second_events,
        )
        archive = ConversationArchive(
            1,
            (ConversationArchiveSession("session_a", (first, second)),),
        )
        object.__setattr__(second_events[-1], "content", poison)
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_EVENTS",
            3,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                serialize_conversation_archive(archive)


class ConversationArchiveWireTests(unittest.TestCase):
    @staticmethod
    def _top(archive: ConversationArchive | None = None) -> dict[str, object]:
        return cast(
            dict[str, object],
            json.loads(
                serialize_conversation_archive(
                    nonempty_archive() if archive is None else archive
                )
            ),
        )

    def test_empty_and_nonempty_golden_bytes_digest_image_and_round_trip(
        self,
    ) -> None:
        self.assertEqual(
            serialize_conversation_archive(ConversationArchive(1, ())),
            EMPTY_GOLDEN,
        )
        self.assertEqual(
            hashlib.sha256(NONEMPTY_DIGEST_IMAGE).hexdigest(),
            "4cb79e8af92f5798faeb94ed6f4167ed9c4fc362fbce9d5516489484e8b6845d",
        )
        wire = serialize_conversation_archive(nonempty_archive())
        self.assertEqual(wire, NONEMPTY_GOLDEN)
        self.assertFalse(wire.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(wire.endswith(b"\n"))
        self.assertFalse(wire.endswith(b"\n\n"))
        self.assertEqual(deserialize_conversation_archive(wire), nonempty_archive())
        self.assertEqual(
            serialize_conversation_archive(deserialize_conversation_archive(wire)), wire
        )

    def test_duplicate_keys_extra_missing_fields_and_nonfinite_json_fail(self) -> None:
        wire = serialize_conversation_archive(nonempty_archive())
        duplicate_wires = (
            wire.replace(
                b'{"schema_version":1',
                b'{"schema_version":1,"schema_version":1',
                1,
            ),
            wire.replace(
                b'{"session_id":"session_a"',
                b'{"session_id":"session_a","session_id":"session_a"',
                1,
            ),
            wire.replace(
                b'"subject":"',
                b'"subject":"duplicate","subject":"',
                1,
            ),
            wire.replace(
                b'"sequence":1',
                b'"sequence":1,"sequence":1',
                1,
            ),
            wire.replace(
                b'{"algorithm":"sha256"',
                b'{"algorithm":"sha256","algorithm":"sha256"',
                1,
            ),
        )
        for index, candidate in enumerate(duplicate_wires):
            with self.subTest(duplicate=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    deserialize_conversation_archive(candidate)

        top = self._top()
        invalid_documents: list[object] = []
        for field in ("schema_version", "sessions", "digest"):
            value = copy.deepcopy(top)
            del value[field]
            invalid_documents.append(value)
        value = copy.deepcopy(top)
        value["extra"] = "secret-sentinel"
        invalid_documents.append(value)
        session = copy.deepcopy(top)
        cast_sessions = session["sessions"]
        assert isinstance(cast_sessions, list)
        assert isinstance(cast_sessions[0], dict)
        cast_sessions[0]["extra"] = True
        invalid_documents.append(session)
        conversation = copy.deepcopy(top)
        cast_sessions = conversation["sessions"]
        assert isinstance(cast_sessions, list)
        cast_conversations = cast_sessions[0]["conversations"]
        assert isinstance(cast_conversations, list)
        assert isinstance(cast_conversations[0], dict)
        del cast_conversations[0]["subject"]
        invalid_documents.append(conversation)
        event = copy.deepcopy(top)
        cast_sessions = event["sessions"]
        assert isinstance(cast_sessions, list)
        cast_conversations = cast_sessions[0]["conversations"]
        assert isinstance(cast_conversations, list)
        cast_events = cast_conversations[0]["events"]
        assert isinstance(cast_events, list)
        assert isinstance(cast_events[0], dict)
        cast_events[0]["extra"] = True
        invalid_documents.append(event)
        for index, document in enumerate(invalid_documents):
            with self.subTest(schema=index):
                with self.assertRaises(ConversationArchiveFormatError) as raised:
                    deserialize_conversation_archive(compact_json(document))
                self.assertNotIn("secret-sentinel", str(raised.exception))

        for candidate in (
            b'{"schema_version":1,"sessions":NaN,"digest":{}}\n',
            b'{"schema_version":1,"sessions":Infinity,"digest":{}}\n',
            b'{"schema_version":1,"sessions":-Infinity,"digest":{}}\n',
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ConversationArchiveFormatError):
                    deserialize_conversation_archive(candidate)

    def test_malformed_utf8_json_types_and_versions_are_distinguished(self) -> None:
        for candidate in (
            b"",
            b"not-json\n",
            b"\xff",
            b"\xef\xbb\xbf" + EMPTY_GOLDEN,
            b"[]\n",
        ):
            with self.subTest(candidate=candidate[:12]):
                with self.assertRaises(ConversationArchiveFormatError):
                    deserialize_conversation_archive(candidate)
        with self.assertRaises(ConversationArchiveFormatError):
            deserialize_conversation_archive("{}")  # type: ignore[arg-type]

        for location in ("top", "conversation", "event"):
            top = self._top()
            sessions = top["sessions"]
            assert isinstance(sessions, list)
            conversation = sessions[0]["conversations"][0]
            events = conversation["events"]
            if location == "top":
                top["schema_version"] = 2
            elif location == "conversation":
                conversation["schema_version"] = 2
            else:
                events[0]["schema_version"] = 2
            with self.subTest(location=location):
                with self.assertRaises(ConversationArchiveVersionError):
                    deserialize_conversation_archive(compact_json(top))

        top = self._top()
        sessions = top["sessions"]
        assert isinstance(sessions, list)
        conversation = sessions[0]["conversations"][0]
        events = conversation["events"]
        conversation["status"] = "deleted"
        with self.assertRaises(ConversationArchiveFormatError):
            deserialize_conversation_archive(compact_json(top))
        top = self._top()
        sessions = top["sessions"]
        assert isinstance(sessions, list)
        events = sessions[0]["conversations"][0]["events"]
        events[0]["kind"] = "tool"
        with self.assertRaises(ConversationArchiveFormatError):
            deserialize_conversation_archive(compact_json(top))

    def test_noncanonical_order_spacing_newlines_and_escaping_are_rejected(
        self,
    ) -> None:
        events = archive_events()
        conversation_a = archive_conversation(events=events)
        conversation_b = archive_conversation(
            conversation_id="conversation_b",
            created_at=SECOND_AT,
        )
        rich = ConversationArchive(
            1,
            (
                ConversationArchiveSession(
                    "session_a", (conversation_a, conversation_b)
                ),
                ConversationArchiveSession(
                    "session_b",
                    (
                        archive_conversation(
                            session_id="session_b",
                            conversation_id="conversation_c",
                        ),
                    ),
                ),
            ),
        )
        wire = serialize_conversation_archive(rich)
        top = json.loads(wire)
        candidates = [
            wire[:-1],
            wire + b"\n",
            wire.replace(b',"sessions"', b', "sessions"', 1),
            (
                json.dumps(
                    top,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
        ]
        unicode_top = self._top()
        candidates.append(compact_json(unicode_top, ensure_ascii=True))

        for location in ("sessions", "conversations", "events"):
            reordered = copy.deepcopy(top)
            sessions = reordered["sessions"]
            assert isinstance(sessions, list)
            if location == "sessions":
                sessions.reverse()
            elif location == "conversations":
                sessions[0]["conversations"].reverse()
            else:
                sessions[0]["conversations"][0]["events"].reverse()
            candidates.append(compact_json(reordered))

        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    deserialize_conversation_archive(candidate)

    def test_digest_shape_mismatch_and_content_tampering_are_rejected(self) -> None:
        top = self._top()
        invalid_digests = (
            {"algorithm": "sha512", "value": "0" * 64},
            {"algorithm": "sha256", "value": "0" * 63},
            {"algorithm": "sha256", "value": "A" * 64},
            {"algorithm": "sha256", "value": 1},
        )
        for digest in invalid_digests:
            candidate = copy.deepcopy(top)
            candidate["digest"] = digest
            with self.subTest(digest=digest):
                with self.assertRaises(ConversationArchiveDigestError):
                    deserialize_conversation_archive(compact_json(candidate))

        extra_digest_field = copy.deepcopy(top)
        extra_digest_field["digest"] = {
            "algorithm": "sha256",
            "value": "0" * 64,
            "extra": True,
        }
        with self.assertRaises(ConversationArchiveFormatError):
            deserialize_conversation_archive(compact_json(extra_digest_field))

        tampered = copy.deepcopy(top)
        sessions = tampered["sessions"]
        assert isinstance(sessions, list)
        sessions[0]["conversations"][0]["events"][0]["content"] = "secret-sentinel"
        with self.assertRaises(ConversationArchiveDigestError) as raised:
            deserialize_conversation_archive(compact_json(tampered))
        self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_deserializer_enforces_each_count_and_aggregate_bound(self) -> None:
        wire = serialize_conversation_archive(nonempty_archive())
        for constant in (
            "CONVERSATION_ARCHIVE_MAX_SESSIONS",
            "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
            "CONVERSATION_ARCHIVE_MAX_EVENTS",
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
        ):
            limit = 0 if constant != "CONVERSATION_ARCHIVE_MAX_EVENTS" else 1
            with self.subTest(constant=constant):
                with mock.patch.object(archive_module, constant, limit):
                    with self.assertRaises(ConversationArchiveBoundsError):
                        deserialize_conversation_archive(wire)

    def test_serializer_revalidates_corrupted_frozen_values_content_free(self) -> None:
        mutations = (
            ("archive", "schema_version", True),
            ("archive", "sessions", 1),
            ("session", "session_id", "../secret-sentinel"),
            ("session", "conversations", 1),
            ("conversation", "subject", "\ud800secret-sentinel"),
            ("conversation", "events", 1),
            ("event", "sequence", 0),
            ("event", "content", "\ud800secret-sentinel"),
        )
        for owner, field, invalid in mutations:
            archive = nonempty_archive()
            target: object = archive
            if owner == "session":
                target = archive.sessions[0]
            elif owner == "conversation":
                target = archive.sessions[0].conversations[0]
            elif owner == "event":
                target = archive.sessions[0].conversations[0].events[0]
            object.__setattr__(target, field, invalid)
            with self.subTest(owner=owner, field=field):
                with self.assertRaises(ConversationArchiveError) as raised:
                    serialize_conversation_archive(archive)
                self.assertIsInstance(
                    raised.exception,
                    (ConversationArchiveFormatError, ConversationArchiveBoundsError),
                )
                self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_parser_preflight_short_circuits_global_counts_before_parsing(self) -> None:
        rich = ConversationArchive(
            1,
            (
                ConversationArchiveSession(
                    "session_a",
                    (archive_conversation(),),
                ),
                ConversationArchiveSession(
                    "session_b",
                    (
                        archive_conversation(
                            session_id="session_b",
                            conversation_id="conversation_b",
                        ),
                    ),
                ),
            ),
        )
        wire = serialize_conversation_archive(rich)
        with (
            mock.patch.object(
                archive_module,
                "CONVERSATION_ARCHIVE_MAX_SESSIONS",
                1,
            ),
            mock.patch.object(
                archive_module,
                "_parse_session",
                side_effect=AssertionError("parser inspected an overflow descendant"),
            ) as parse_session,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                deserialize_conversation_archive(wire)
        parse_session.assert_not_called()

        class PoisonedDescendant:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("raw overflow descendant was inspected")

        raw_sessions = [
            {"session_id": "session_a", "conversations": [PoisonedDescendant()]},
            {"session_id": "session_b", "conversations": [PoisonedDescendant()]},
        ]
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_module._preflight_raw_archive(raw_sessions)

        raw_events = json.loads(wire)["sessions"][0]["conversations"][0]
        raw_events["events"] = [PoisonedDescendant(), PoisonedDescendant()]
        raw_event_sessions = [
            {
                "session_id": "session_a",
                "conversations": [raw_events],
            }
        ]
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_EVENTS",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_module._preflight_raw_archive(raw_event_sessions)

    def test_deep_json_and_incremental_utf8_fail_content_free(self) -> None:
        depth = sys.getrecursionlimit() + 100
        nested = b"[" * depth + b'"secret-sentinel"' + b"]" * depth
        with mock.patch.object(
            archive_module.json,
            "loads",
            side_effect=RecursionError("secret-sentinel decoder depth"),
        ):
            with self.assertRaises(ConversationArchiveFormatError) as recursion:
                deserialize_conversation_archive(nested)
        self.assertEqual(
            str(recursion.exception),
            "conversation archive format is invalid",
        )
        self.assertIsNone(recursion.exception.__cause__)
        self.assertNotIn("secret-sentinel", str(recursion.exception))

        top = self._top()
        sessions = top["sessions"]
        assert isinstance(sessions, list)
        conversation = sessions[0]["conversations"][0]
        conversation["subject"] = "XX"
        conversation["events"][0]["content"] = {"secret-sentinel": True}
        with mock.patch.object(
            archive_module,
            "CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES",
            1,
        ):
            with self.assertRaises(ConversationArchiveBoundsError) as bounded:
                deserialize_conversation_archive(compact_json(top))
        self.assertEqual(
            str(bounded.exception),
            "conversation archive bounds exceeded",
        )
        self.assertIsNone(bounded.exception.__cause__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
