"""Canonical, bounded, lossless schema-v2 conversation archive tests."""

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
from story_writing_agents import ConversationStatus
from story_writing_agents.conversations import (
    CONVERSATION_METADATA_SCHEMA_VERSION,
)
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
    LEGACY_SCHEMA_ONE_EMPTY_ARCHIVE,
    archive_conversation,
    archive_from_conversations,
    collaboration_history,
    compact_json,
    event_images,
    nonempty_archive,
    representative_archive,
)


EMPTY_GOLDEN = (
    b'{"schema_version":2,"sessions":[],"digest":{"algorithm":"sha256",'
    b'"value":"d5820aa0c81591e89e32ab06f83181f74e5d6bb68a450e0d255d26227c848e68"}}\n'
)
NONEMPTY_WIRE_SHA256 = (
    "620f19e4b96cc1a2cb95c0ca8ea57b2b4ba88654645090809bb84c030bc24969"
)
NONEMPTY_DIGEST = "601bf3487747829108efa328edef6a6909ac11f0c9c0626a4cf63679996e390f"
REPRESENTATIVE_WIRE_SHA256 = (
    "81a97320b57fe596de57e1f77b3c3e68762e01cd9c4766fb9b443e116b301c2c"
)
REPRESENTATIVE_DIGEST = (
    "00c8234895d93d7c9e5a23d601d686c839f953501121df4c86889899c613201f"
)


class ConversationArchiveSurfaceTests(unittest.TestCase):
    def test_public_surfaces_caps_and_breaking_absence_are_exact(self) -> None:
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
        self.assertEqual(len(archive_module.__all__), 21)
        self.assertEqual(set(archive_module.__all__), expected_archive)
        self.assertEqual(
            filesystem_archive_module.__all__,
            [
                "FilesystemConversationArchiveAdapter",
                "compose_filesystem_conversation_archive",
            ],
        )
        self.assertEqual(len(storage.__all__), 63)
        self.assertEqual(len(story_writing_agents.__all__), 106)
        for name in expected_archive | set(filesystem_archive_module.__all__):
            with self.subTest(name=name):
                owner = (
                    archive_module
                    if name in expected_archive
                    else filesystem_archive_module
                )
                self.assertIs(getattr(storage, name), getattr(owner, name))
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        for module in (archive_module, storage, story_writing_agents):
            self.assertNotIn("ConversationArchiveEvent", module.__all__)
            self.assertFalse(hasattr(module, "ConversationArchiveEvent"))

        self.assertEqual(CONVERSATION_ARCHIVE_SCHEMA_VERSION, 2)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_RAW_BYTES, 67_108_864)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_SESSIONS, 1_024)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_CONVERSATIONS, 65_536)
        self.assertEqual(CONVERSATION_ARCHIVE_MAX_EVENTS, 1_000_000)
        self.assertEqual(
            CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES,
            33_554_432,
        )

    def test_error_hierarchy_and_messages_are_fixed_and_content_free(self) -> None:
        cases: tuple[tuple[type[ConversationArchiveError], str], ...] = (
            (ConversationArchiveError, "conversation archive error"),
            (ConversationArchiveFormatError, "conversation archive format is invalid"),
            (
                ConversationArchiveVersionError,
                "conversation archive version is unsupported",
            ),
            (ConversationArchiveBoundsError, "conversation archive bounds exceeded"),
            (ConversationArchiveDigestError, "conversation archive digest is invalid"),
            (ConversationArchiveSourceError, "conversation archive source is invalid"),
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
            ConversationArchiveSourceChangedError(), ConversationArchiveSourceError
        )
        self.assertIsInstance(
            ConversationArchiveConflictError(), ConversationArchiveDestinationError
        )


class ConversationArchiveValueTests(unittest.TestCase):
    def test_values_are_frozen_slotted_and_canonicalize_every_order(self) -> None:
        events = collaboration_history("conversation_b")
        later = archive_conversation(
            conversation_id="conversation_b",
            created_at="2026-01-01T00:00:01+00:00",
            events=tuple(reversed(events)),
        )
        earlier = archive_conversation(conversation_id="conversation_a")
        session_a = ConversationArchiveSession("session_a", (later, earlier))
        session_z = ConversationArchiveSession(
            "session_z",
            (
                archive_conversation(
                    session_id="session_z",
                    conversation_id="conversation_z",
                ),
            ),
        )
        archive = ConversationArchive(2, (session_z, session_a))

        self.assertEqual(
            tuple(session.session_id for session in archive.sessions),
            ("session_a", "session_z"),
        )
        self.assertEqual(
            tuple(item.conversation_id for item in archive.sessions[0].conversations),
            ("conversation_a", "conversation_b"),
        )
        self.assertEqual(
            tuple(
                event.sequence for event in archive.sessions[0].conversations[1].events
            ),
            tuple(range(1, len(events) + 1)),
        )
        with self.assertRaises(FrozenInstanceError):
            archive.schema_version = 1  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            archive.extra = True  # type: ignore[attr-defined]

    def test_metadata_schema_one_and_complete_v2_histories_are_exact(self) -> None:
        archive = representative_archive()
        conversations = {
            item.conversation_id: item
            for session in archive.sessions
            for item in session.conversations
        }
        self.assertEqual(archive.schema_version, 2)
        self.assertEqual(
            {item.schema_version for item in conversations.values()},
            {CONVERSATION_METADATA_SCHEMA_VERSION},
        )
        self.assertEqual(len(conversations["conversation_completed"].events), 25)
        self.assertEqual(len(conversations["conversation_unknown"].events), 18)
        self.assertEqual(conversations["conversation_empty"].events, ())
        self.assertIs(
            conversations["conversation_unknown"].status,
            ConversationStatus.ARCHIVED,
        )
        self.assertEqual(len(event_images(archive)), 43)
        self.assertEqual(
            {
                event.batch_id
                for item in conversations.values()
                for event in item.events
            },
            {
                "batch_proposal",
                "batch_confirmation",
                "batch_running",
                "batch_calls",
                "batch_result",
                "batch_completed",
                "batch_unknown",
            },
        )

    def test_duplicate_cross_identity_incomplete_and_wrong_types_fail_closed(
        self,
    ) -> None:
        conversation = archive_conversation()
        complete = collaboration_history()
        wrong_identity = collaboration_history("conversation_b")
        operations: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchiveSession("session_a", ()),
            lambda: ConversationArchiveSession("session_a", (conversation, conversation)),
            lambda: ConversationArchive(
                2,
                (
                    ConversationArchiveSession("session_a", (conversation,)),
                    ConversationArchiveSession("session_a", (conversation,)),
                ),
            ),
            lambda: ConversationArchiveSession("session_b", (conversation,)),
            lambda: archive_conversation(events=wrong_identity),
            lambda: archive_conversation(events=(complete[0], complete[0])),
            lambda: archive_conversation(events=(complete[0],)),
            lambda: ConversationArchive(1, ()),
            lambda: ConversationArchive(True, ()),
            lambda: ConversationArchive(2, []),  # type: ignore[arg-type]
            lambda: archive_conversation(subject_revision=0),
            lambda: archive_conversation(subject="bad\x00subject"),
            lambda: archive_conversation(events=[]),  # type: ignore[arg-type]
        )
        for index, operation in enumerate(operations):
            with self.subTest(index=index):
                with self.assertRaises(
                    (ConversationArchiveFormatError, ConversationArchiveVersionError)
                ):
                    operation()

        wrong_metadata = archive_conversation()
        object.__setattr__(wrong_metadata, "schema_version", 2)
        with self.assertRaises(ConversationArchiveFormatError):
            serialize_conversation_archive(archive_from_conversations(wrong_metadata))

    def test_global_local_and_event_byte_bounds_are_enforced(self) -> None:
        conversation_a = archive_conversation()
        conversation_b = archive_conversation(conversation_id="conversation_b")
        session_a = ConversationArchiveSession("session_a", (conversation_a,))
        session_b = ConversationArchiveSession(
            "session_b",
            (
                archive_conversation(
                    session_id="session_b", conversation_id="conversation_b"
                ),
            ),
        )
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_SESSIONS", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchive(2, (session_a, session_b))
        with mock.patch.object(
            archive_module, "CONVERSATION_ARCHIVE_MAX_CONVERSATIONS", 1
        ):
            with self.assertRaises(ConversationArchiveBoundsError):
                ConversationArchiveSession('session_a', (conversation_a, conversation_b))
        events = collaboration_history()
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_EVENTS", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_conversation(events=events)
        with mock.patch.object(archive_module, 'CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES', 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                archive_conversation(subject="S", events=(events[0],))
        with mock.patch.object(archive_module, "CONVERSATION_ARCHIVE_MAX_RAW_BYTES", 1):
            with self.assertRaises(ConversationArchiveBoundsError):
                serialize_conversation_archive(ConversationArchive(2, ()))
            with self.assertRaises(ConversationArchiveBoundsError):
                deserialize_conversation_archive(b"{}")

    def test_shape_preflight_stops_before_poisoned_descendants(self) -> None:
        class PoisonedDescendant:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("poisoned descendant was inspected")

        poison = PoisonedDescendant()
        cases: tuple[Callable[[], object], ...] = (
            lambda: ConversationArchive(2, cast(object, [poison])),
            lambda: ConversationArchiveSession("session_a", cast(object, [poison])),
            lambda: archive_conversation(events=cast(object, [poison])),
        )
        for index, operation in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ConversationArchiveFormatError):
                    operation()


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

    def test_empty_and_full_golden_hashes_digest_and_codec_round_trip(self) -> None:
        self.assertEqual(
            serialize_conversation_archive(ConversationArchive(2, ())), EMPTY_GOLDEN
        )
        for archive, wire_hash, digest in (
            (nonempty_archive(), NONEMPTY_WIRE_SHA256, NONEMPTY_DIGEST),
            (
                representative_archive(),
                REPRESENTATIVE_WIRE_SHA256,
                REPRESENTATIVE_DIGEST,
            ),
        ):
            with self.subTest(events=len(event_images(archive))):
                wire = serialize_conversation_archive(archive)
                self.assertEqual(hashlib.sha256(wire).hexdigest(), wire_hash)
                self.assertEqual(json.loads(wire)["digest"]["value"], digest)
                self.assertFalse(wire.startswith(b"\xef\xbb\xbf"))
                self.assertTrue(wire.endswith(b"\n"))
                self.assertFalse(wire.endswith(b"\n\n"))
                decoded = deserialize_conversation_archive(wire)
                self.assertEqual(decoded, archive)
                self.assertEqual(serialize_conversation_archive(decoded), wire)
                self.assertEqual(event_images(decoded), event_images(archive))
                for before, after in zip(
                    event_images(archive), event_images(decoded), strict=True
                ):
                    self.assertEqual(before, after)

    def test_schema_one_archive_and_transcript_shape_are_rejected(self) -> None:
        with self.assertRaises(ConversationArchiveVersionError):
            deserialize_conversation_archive(LEGACY_SCHEMA_ONE_EMPTY_ARCHIVE)

        top = self._top()
        sessions = cast(list[dict[str, object]], top["sessions"])
        conversation = cast(
            dict[str, object],
            cast(list[dict[str, object]], sessions[0]["conversations"])[0],
        )
        conversation["events"] = [
            {
                "schema_version": 1,
                "session_id": "session_a",
                "conversation_id": "conversation_a",
                "turn_id": "turn_a",
                "sequence": 1,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "sender": "user",
                "recipient": "lead_orchestrator",
                "kind": "user_message",
                "content": "legacy transcript",
            }
        ]
        with self.assertRaises(ConversationArchiveFormatError):
            deserialize_conversation_archive(compact_json(top))

    def test_duplicate_extra_missing_nonfinite_and_malformed_json_fail(self) -> None:
        wire = serialize_conversation_archive(nonempty_archive())
        duplicate_wires = (
            wire.replace(
                b'{"schema_version":2',
                b'{"schema_version":2,"schema_version":2',
                1,
            ),
            wire.replace(
                b'{"session_id":"session_a"',
                b'{"session_id":"session_a","session_id":"session_a"',
                1,
            ),
            wire.replace(
                b'"event_id":"proposal_event"',
                b'"event_id":"proposal_event","event_id":"proposal_event"',
                1,
            ),
            wire.replace(
                b'{"algorithm":"sha256"',
                b'{"algorithm":"sha256","algorithm":"sha256"',
                1,
            ),
        )
        for candidate in duplicate_wires:
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
        for document in invalid_documents:
            with self.assertRaises(ConversationArchiveFormatError) as raised:
                deserialize_conversation_archive(compact_json(document))
            self.assertNotIn("secret-sentinel", str(raised.exception))

        for candidate in (
            b"",
            b"not-json\n",
            b"\xff",
            b"\xef\xbb\xbf" + EMPTY_GOLDEN,
            b"[]\n",
            b'{"schema_version":2,"sessions":NaN,"digest":{}}\n',
        ):
            with self.subTest(candidate=candidate[:12]):
                with self.assertRaises(ConversationArchiveFormatError):
                    deserialize_conversation_archive(candidate)

    def test_noncanonical_order_spacing_newlines_and_escaping_are_rejected(
        self,
    ) -> None:
        archive = representative_archive()
        wire = serialize_conversation_archive(archive)
        top = json.loads(wire)
        candidates = [
            wire[:-1],
            wire + b"\n",
            wire.replace(b',"sessions"', b', "sessions"', 1),
            (
                json.dumps(top, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode(),
            compact_json(top, ensure_ascii=True),
        ]
        for location in ("sessions", "conversations", "events"):
            reordered = copy.deepcopy(top)
            sessions = reordered["sessions"]
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

    def test_digest_shape_mismatch_and_catalog_tampering_are_rejected(self) -> None:
        top = self._top()
        for digest in (
            {"algorithm": "sha512", "value": "0" * 64},
            {"algorithm": "sha256", "value": "0" * 63},
            {"algorithm": "sha256", "value": "A" * 64},
            {"algorithm": "sha256", "value": 1},
        ):
            candidate = copy.deepcopy(top)
            candidate["digest"] = digest
            with self.assertRaises(ConversationArchiveDigestError):
                deserialize_conversation_archive(compact_json(candidate))

        tampered = copy.deepcopy(top)
        tampered["sessions"][0]["conversations"][0]["subject"] = "secret-sentinel"
        with self.assertRaises(ConversationArchiveDigestError) as raised:
            deserialize_conversation_archive(compact_json(tampered))
        self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_deserializer_enforces_counts_bytes_and_corrupted_frozen_values(
        self,
    ) -> None:
        wire = serialize_conversation_archive(nonempty_archive())
        for constant, limit in (
            ("CONVERSATION_ARCHIVE_MAX_SESSIONS", 0),
            ("CONVERSATION_ARCHIVE_MAX_CONVERSATIONS", 0),
            ("CONVERSATION_ARCHIVE_MAX_EVENTS", 1),
            ("CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES", 1),
        ):
            with self.subTest(constant=constant):
                with mock.patch.object(archive_module, constant, limit):
                    with self.assertRaises(ConversationArchiveBoundsError):
                        deserialize_conversation_archive(wire)

        mutations = (
            ("archive", "schema_version", True),
            ("archive", "sessions", 1),
            ("session", "session_id", "../secret-sentinel"),
            ("conversation", "subject", "\ud800secret-sentinel"),
            ("conversation", "events", 1),
            ("event", "sequence", 0),
            ("event", "event_id", "../secret-sentinel"),
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
                self.assertNotIn("secret-sentinel", str(raised.exception))

    def test_deep_json_recursion_is_content_free(self) -> None:
        depth = sys.getrecursionlimit() + 100
        nested = b"[" * depth + b'"secret-sentinel"' + b"]" * depth
        with mock.patch.object(
            archive_module.json,
            "loads",
            side_effect=RecursionError("secret-sentinel decoder depth"),
        ):
            with self.assertRaises(ConversationArchiveFormatError) as raised:
                deserialize_conversation_archive(nested)
        self.assertEqual(
            str(raised.exception), "conversation archive format is invalid"
        )
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
