"""Reusable contract fixtures for conversation event-store adapters."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from story_writing_agents import (
    CONVERSATION_CONTEXT_POLICY,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationEventStore,
    ConversationMetadata,
    ConversationNotFoundError,
    ConversationStatus,
    WorkspaceFormatError,
)
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT


_BASE_TIMESTAMP = datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC)


def make_append(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
    sender: str | None = None,
    recipient: str | None = None,
    content: str = "visible content",
) -> ConversationEventAppend:
    """Build one valid append request, with actor defaults for its kind."""

    if kind is ConversationEventKind.USER_MESSAGE:
        default_sender, default_recipient = "user", LEAD_ORCHESTRATOR
    elif kind is ConversationEventKind.AGENT_REPLY:
        default_sender, default_recipient = LEAD_ORCHESTRATOR, "user"
    else:
        default_sender, default_recipient = "runtime", "user"
    return ConversationEventAppend(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        sender=default_sender if sender is None else sender,
        recipient=default_recipient if recipient is None else recipient,
        kind=kind,
        content=content,
    )


def make_stored_event(**overrides: object) -> ConversationEvent:
    """Build one valid persisted event for filesystem tamper fixtures."""

    values: dict[str, object] = {
        "schema_version": CONVERSATION_EVENT_SCHEMA_VERSION,
        "session_id": "session_a",
        "conversation_id": "conversation_a",
        "turn_id": "turn_a",
        "sequence": 1,
        "timestamp": "2026-08-15T01:02:03+00:00",
        "sender": "user",
        "recipient": LEAD_ORCHESTRATOR,
        "kind": ConversationEventKind.USER_MESSAGE,
        "content": "visible content",
    }
    values.update(overrides)
    return ConversationEvent(**values)  # type: ignore[arg-type]


class StaticConversationCatalog:
    """Small authoritative catalog used by the in-memory conformance adapter."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], ConversationMetadata] = {}
        self._next_id = 1

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        conversation_id = f"conversation_{self._next_id}"
        self._next_id += 1
        timestamp = _BASE_TIMESTAMP.isoformat()
        metadata = ConversationMetadata(
            schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
            session_id=session_id,
            conversation_id=conversation_id,
            subject=subject,
            subject_revision=1,
            status=ConversationStatus.ACTIVE,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._entries[(session_id, conversation_id)] = metadata
        return metadata

    def get(self, session_id: str, conversation_id: str) -> ConversationMetadata:
        try:
            return self._entries[(session_id, conversation_id)]
        except KeyError as exc:
            raise ConversationNotFoundError(
                f"conversation does not exist: {session_id}/{conversation_id}"
            ) from exc


class InMemoryConversationEventStore:
    """Independent deterministic adapter used to exercise the shared contract."""

    def __init__(self, catalog: StaticConversationCatalog) -> None:
        self.catalog = catalog
        self._events: dict[tuple[str, str], list[ConversationEvent]] = {}
        self._lock = threading.Lock()
        self._timestamp_offset = 0

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        if not isinstance(event, ConversationEventAppend):
            raise TypeError("event must be a ConversationEventAppend")
        event = ConversationEventAppend(
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )
        with self._lock:
            self._authorize(event.session_id, event.conversation_id)
            key = (event.session_id, event.conversation_id)
            stored_events = self._events.setdefault(key, [])
            self._validate_append(stored_events, event)
            timestamp = (
                _BASE_TIMESTAMP + timedelta(microseconds=self._timestamp_offset)
            ).isoformat()
            self._timestamp_offset += 1
            stored = ConversationEvent(
                schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=len(stored_events) + 1,
                timestamp=timestamp,
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
            stored_events.append(stored)
            return stored

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        with self._lock:
            self._authorize(session_id, conversation_id)
            return tuple(self._events.get((session_id, conversation_id), ()))

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        with self._lock:
            self._authorize(session_id, conversation_id)
            events = tuple(self._events.get((session_id, conversation_id), ()))
            return self._select_context(events)

    def _authorize(self, session_id: str, conversation_id: str) -> None:
        metadata = self.catalog.get(session_id, conversation_id)
        if (
            metadata.session_id != session_id
            or metadata.conversation_id != conversation_id
        ):
            raise WorkspaceFormatError("catalog identity does not match request")

    @staticmethod
    def _validate_append(
        events: list[ConversationEvent],
        event: ConversationEventAppend,
    ) -> None:
        users = {
            stored.turn_id: stored
            for stored in events
            if stored.kind is ConversationEventKind.USER_MESSAGE
        }
        outcomes = {
            stored.turn_id
            for stored in events
            if stored.kind is not ConversationEventKind.USER_MESSAGE
        }
        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in users:
                raise ValueError("turn already has a user event")
            return
        user = users.get(event.turn_id)
        if user is None:
            raise ValueError("outcome has no user event")
        if event.turn_id in outcomes:
            raise ValueError("turn already has an outcome")
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user.recipient
        ):
            raise ValueError("reply sender does not match addressed agent")

    @staticmethod
    def _select_context(
        events: tuple[ConversationEvent, ...],
    ) -> ConversationContextWindow:
        outcomes = {
            event.turn_id: event
            for event in events
            if event.kind is not ConversationEventKind.USER_MESSAGE
        }
        complete_turns = [
            (event, outcomes[event.turn_id])
            for event in events
            if event.kind is ConversationEventKind.USER_MESSAGE
            and event.turn_id in outcomes
        ]
        selected_reversed: list[tuple[ConversationEvent, ConversationEvent]] = []
        selected_bytes = 0
        for turn in reversed(complete_turns):
            turn_bytes = sum(len(event.content.encode("utf-8")) for event in turn)
            if (
                len(selected_reversed)
                >= CONVERSATION_CONTEXT_POLICY.max_complete_turns
                or selected_bytes + turn_bytes
                > CONVERSATION_CONTEXT_POLICY.max_utf8_bytes
            ):
                break
            selected_reversed.append(turn)
            selected_bytes += turn_bytes
        selected = tuple(reversed(selected_reversed))
        selected_events = tuple(
            sorted(
                (event for turn in selected for event in turn),
                key=lambda event: event.sequence,
            )
        )
        retained_turns = len(selected)
        total_turns = len(complete_turns)
        omitted_turns = total_turns - retained_turns
        return ConversationContextWindow(
            events=selected_events,
            total_complete_turns=total_turns,
            retained_turns=retained_turns,
            omitted_turns=omitted_turns,
            retained_utf8_bytes=selected_bytes,
            truncated=omitted_turns > 0,
        )


class ConversationEventStoreContractMixin:
    """Reusable public behavior contract for every event-store adapter."""

    store: ConversationEventStore
    session_id: str
    conversation_id: str

    def make_store(self) -> ConversationEventStore:
        raise NotImplementedError

    def create_conversation(self, session_id: str, subject: str) -> str:
        raise NotImplementedError

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.store = self.make_store()
        self.session_id = "session_a"
        self.conversation_id = self.create_conversation(
            self.session_id,
            "Contract conversation",
        )

    def request(
        self,
        turn_id: str,
        *,
        kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
        sender: str | None = None,
        recipient: str | None = None,
        content: str = "visible content",
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

    def append_turn(
        self,
        turn_id: str,
        *,
        user_content: str = "u",
        reply_content: str = "r",
        recipient: str = LEAD_ORCHESTRATOR,
    ) -> tuple[ConversationEvent, ConversationEvent]:
        user = self.store.append(
            self.request(
                turn_id,
                recipient=recipient,
                content=user_content,
            )
        )
        reply = self.store.append(
            self.request(
                turn_id,
                kind=ConversationEventKind.AGENT_REPLY,
                sender=recipient,
                content=reply_content,
            )
        )
        return user, reply

    def assert_rejected_without_mutation(
        self,
        event: ConversationEventAppend,
    ) -> None:
        before = self.store.read(self.session_id, self.conversation_id)
        with self.assertRaises(ValueError):  # type: ignore[attr-defined]
            self.store.append(event)
        self.assertEqual(  # type: ignore[attr-defined]
            self.store.read(self.session_id, self.conversation_id),
            before,
        )

    def test_adapter_exposes_the_complete_narrow_protocol(self) -> None:
        methods = {
            name
            for name, member in vars(ConversationEventStore).items()
            if not name.startswith("_") and callable(member)
        }
        self.assertEqual(methods, {"append", "read", "read_context"})
        for name in methods:
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(self.store, name, None)))

    def test_empty_read_and_context_have_exact_zero_metadata(self) -> None:
        self.assertEqual(self.store.read(self.session_id, self.conversation_id), ())
        self.assertEqual(
            self.store.read_context(self.session_id, self.conversation_id),
            ConversationContextWindow(
                events=(),
                total_complete_turns=0,
                retained_turns=0,
                omitted_turns=0,
                retained_utf8_bytes=0,
                truncated=False,
            ),
        )

    def test_append_assigns_order_metadata_and_preserves_visible_content(self) -> None:
        content = "  first line\n\tsecond\x00 한🙂  "
        user = self.store.append(self.request("turn_a", content=content))
        reply = self.store.append(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                content="reply",
            )
        )

        self.assertEqual(user.schema_version, CONVERSATION_EVENT_SCHEMA_VERSION)
        self.assertEqual((user.sequence, reply.sequence), (1, 2))
        self.assertEqual(user.content, content)
        parsed = datetime.fromisoformat(user.timestamp)
        self.assertEqual(parsed.utcoffset(), UTC.utcoffset(parsed))
        self.assertEqual(parsed.isoformat(), user.timestamp)
        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (user, reply),
        )

    def test_pairing_rejections_never_change_history(self) -> None:
        self.assert_rejected_without_mutation(
            self.request(
                "orphan",
                kind=ConversationEventKind.AGENT_REPLY,
            )
        )
        user = self.store.append(self.request("turn_a"))
        self.assert_rejected_without_mutation(self.request("turn_a"))
        self.assert_rejected_without_mutation(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
                sender=PLOT_ARCHITECT,
            )
        )
        reply = self.store.append(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
            )
        )
        self.assert_rejected_without_mutation(
            self.request(
                "turn_a",
                kind=ConversationEventKind.AGENT_REPLY,
            )
        )
        self.assert_rejected_without_mutation(
            self.request(
                "turn_a",
                kind=ConversationEventKind.RUNTIME_ERROR,
            )
        )
        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (user, reply),
        )

    def test_runtime_error_is_a_complete_outcome(self) -> None:
        user = self.store.append(self.request("turn_error"))
        error = self.store.append(
            self.request(
                "turn_error",
                kind=ConversationEventKind.RUNTIME_ERROR,
                content="sanitized visible error",
            )
        )
        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.events, (user, error))
        self.assertEqual(context.total_complete_turns, 1)

    def test_unmatched_user_remains_in_history_but_not_context(self) -> None:
        unmatched = self.store.append(self.request("interrupted", content="saved"))
        complete = self.append_turn("complete")

        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (unmatched, *complete),
        )
        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.events, complete)
        self.assertEqual(context.total_complete_turns, 1)
        self.assertEqual(context.retained_turns, 1)
        self.assertFalse(context.truncated)

    def test_interleaved_complete_turns_preserve_global_event_order(self) -> None:
        first_user = self.store.append(self.request("turn_first"))
        second_user = self.store.append(
            self.request("turn_second", recipient=PLOT_ARCHITECT)
        )
        second_reply = self.store.append(
            self.request(
                "turn_second",
                kind=ConversationEventKind.AGENT_REPLY,
                sender=PLOT_ARCHITECT,
            )
        )
        first_reply = self.store.append(
            self.request("turn_first", kind=ConversationEventKind.AGENT_REPLY)
        )

        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(
            context.events,
            (first_user, second_user, second_reply, first_reply),
        )
        self.assertEqual(context.total_complete_turns, 2)

    def test_context_turn_cap_keeps_newest_twenty_four_whole_turns(self) -> None:
        for index in range(25):
            self.append_turn(f"turn_{index:02d}")

        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.total_complete_turns, 25)
        self.assertEqual(context.retained_turns, 24)
        self.assertEqual(context.omitted_turns, 1)
        self.assertEqual(context.retained_utf8_bytes, 48)
        self.assertTrue(context.truncated)
        self.assertEqual(tuple(event.sequence for event in context.events), tuple(range(3, 51)))
        self.assertEqual(
            {event.turn_id for event in context.events},
            {f"turn_{index:02d}" for index in range(1, 25)},
        )

    def test_context_byte_cap_counts_multibyte_utf8_exactly(self) -> None:
        self.append_turn("older", user_content="o", reply_content="r")
        newest = self.append_turn(
            "newest",
            user_content="한" * 10_922,
            reply_content="é",
        )

        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.events, newest)
        self.assertEqual(context.total_complete_turns, 2)
        self.assertEqual(context.retained_turns, 1)
        self.assertEqual(context.omitted_turns, 1)
        self.assertEqual(
            context.retained_utf8_bytes,
            CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
        )

    def test_context_stops_at_first_byte_violation_without_skipping(self) -> None:
        self.append_turn("oldest", user_content="a", reply_content="b")
        self.append_turn(
            "oversized_middle",
            user_content="x" * CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
            reply_content="y",
        )
        newest = self.append_turn("newest", user_content="n", reply_content="r")

        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.events, newest)
        self.assertEqual(context.total_complete_turns, 3)
        self.assertEqual(context.retained_turns, 1)
        self.assertEqual(context.omitted_turns, 2)
        self.assertEqual(context.retained_utf8_bytes, 2)
        self.assertTrue(context.truncated)

    def test_oversized_newest_turn_returns_no_context(self) -> None:
        self.append_turn("older", user_content="a", reply_content="b")
        self.append_turn(
            "oversized_newest",
            user_content="x" * CONVERSATION_CONTEXT_POLICY.max_utf8_bytes,
            reply_content="y",
        )

        context = self.store.read_context(self.session_id, self.conversation_id)
        self.assertEqual(context.events, ())
        self.assertEqual(context.total_complete_turns, 2)
        self.assertEqual(context.retained_turns, 0)
        self.assertEqual(context.omitted_turns, 2)
        self.assertEqual(context.retained_utf8_bytes, 0)
        self.assertTrue(context.truncated)

    def test_sessions_and_conversations_have_independent_sequence_spaces(self) -> None:
        second_conversation = self.create_conversation(
            self.session_id,
            "Second conversation",
        )
        other_session = "session_b"
        third_conversation = self.create_conversation(
            other_session,
            "Other session conversation",
        )

        first = self.store.append(self.request("shared_turn"))
        second = self.store.append(
            self.request(
                "shared_turn",
                conversation_id=second_conversation,
            )
        )
        third = self.store.append(
            self.request(
                "shared_turn",
                session_id=other_session,
                conversation_id=third_conversation,
            )
        )

        self.assertEqual((first.sequence, second.sequence, third.sequence), (1, 1, 1))
        self.assertEqual(
            self.store.read(self.session_id, self.conversation_id),
            (first,),
        )
        self.assertEqual(
            self.store.read(self.session_id, second_conversation),
            (second,),
        )
        self.assertEqual(
            self.store.read(other_session, third_conversation),
            (third,),
        )
