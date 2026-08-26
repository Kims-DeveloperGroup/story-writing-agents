"""Reusable fixtures and conformance cases for conversation-memory adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from story_writing_agents import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMemoryService,
    ConversationMetadata,
    ConversationNotFoundError,
    ConversationSearch,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchScope,
    ConversationStatus,
)
from story_writing_agents.agents import PLOT_ARCHITECT


BASE_TIME = datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC)


class CatalogLike(Protocol):
    def create(self, session_id: str, subject: str) -> ConversationMetadata: ...

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata: ...

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]: ...

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata: ...


class EventStoreLike(Protocol):
    def append(self, event: ConversationEventAppend) -> ConversationEvent: ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]: ...


class MemoryConversationCatalog:
    """Independent in-memory catalog for the shared search contract."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], ConversationMetadata] = {}
        self.counter = 0
        self.reverse_list = False

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        self.counter += 1
        conversation_id = f"conversation_{self.counter:04d}"
        timestamp = (BASE_TIME + timedelta(seconds=self.counter)).isoformat()
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
        self.entries[(session_id, conversation_id)] = metadata
        return metadata

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        try:
            return self.entries[(session_id, conversation_id)]
        except KeyError as exc:
            raise ConversationNotFoundError(
                f"conversation does not exist: {session_id}/{conversation_id}"
            ) from exc

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        items = [
            item
            for (owner, _), item in self.entries.items()
            if owner == session_id
            and (include_archived or item.status is ConversationStatus.ACTIVE)
        ]
        items.sort(key=lambda item: (item.created_at, item.conversation_id))
        if self.reverse_list:
            items.reverse()
        return tuple(items)

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        current = self.get(session_id, conversation_id)
        updated = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=subject,
            subject_revision=current.subject_revision + 1,
            status=current.status,
            created_at=current.created_at,
            updated_at=(
                datetime.fromisoformat(current.updated_at) + timedelta(seconds=1)
            ).isoformat(),
        )
        self.entries[(session_id, conversation_id)] = updated
        return updated

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        current = self.get(session_id, conversation_id)
        archived = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=current.subject,
            subject_revision=current.subject_revision,
            status=ConversationStatus.ARCHIVED,
            created_at=current.created_at,
            updated_at=(
                datetime.fromisoformat(current.updated_at) + timedelta(seconds=1)
            ).isoformat(),
        )
        self.entries[(session_id, conversation_id)] = archived
        return archived


class MemoryConversationEventStore:
    """Independent in-memory visible-event store for search conformance."""

    def __init__(self, catalog: MemoryConversationCatalog) -> None:
        self.catalog = catalog
        self.events: dict[tuple[str, str], tuple[ConversationEvent, ...]] = {}
        self.read_calls: list[tuple[str, str]] = []
        self.append_calls = 0

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        self.catalog.get(event.session_id, event.conversation_id)
        key = (event.session_id, event.conversation_id)
        existing = self.events.get(key, ())
        timestamp = (BASE_TIME + timedelta(microseconds=len(existing))).isoformat()
        stored = ConversationEvent(
            schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            sequence=len(existing) + 1,
            timestamp=timestamp,
            sender=event.sender,
            recipient=event.recipient,
            kind=event.kind,
            content=event.content,
        )
        self.events[key] = existing + (stored,)
        self.append_calls += 1
        return stored

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        self.catalog.get(session_id, conversation_id)
        self.read_calls.append((session_id, conversation_id))
        return self.events.get((session_id, conversation_id), ())


def append_turn(
    store: EventStoreLike,
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    user_content: str,
    outcome_content: str,
    outcome_kind: ConversationEventKind = ConversationEventKind.AGENT_REPLY,
) -> tuple[ConversationEvent, ConversationEvent]:
    """Append one valid visible user/outcome turn."""

    user = store.append(
        ConversationEventAppend(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            sender="user",
            recipient=PLOT_ARCHITECT,
            kind=ConversationEventKind.USER_MESSAGE,
            content=user_content,
        )
    )
    if outcome_kind is ConversationEventKind.AGENT_REPLY:
        sender = PLOT_ARCHITECT
    else:
        sender = "runtime"
    outcome = store.append(
        ConversationEventAppend(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            sender=sender,
            recipient="user",
            kind=outcome_kind,
            content=outcome_content,
        )
    )
    return user, outcome


def seed_standard_scenario(
    catalog: CatalogLike,
    store: EventStoreLike,
    *,
    session_id: str,
) -> dict[str, ConversationMetadata]:
    """Create exact, prefix, substring, transcript, error, and archive cases."""

    records = {
        "exact": catalog.create(session_id, "Council Vessel"),
        "prefix": catalog.create(session_id, "Council vessel reveal"),
        "substring": catalog.create(session_id, "Opening council vessel notes"),
        "transcript": catalog.create(session_id, "Harbor timing"),
        "runtime": catalog.create(session_id, "Provider failure"),
        "archived": catalog.create(session_id, "Archived council vessel"),
    }
    append_turn(
        store,
        session_id=session_id,
        conversation_id=records["transcript"].conversation_id,
        turn_id="turn_search",
        user_content="When does the council vessel arrive?",
        outcome_content="It arrives after dusk.",
    )
    append_turn(
        store,
        session_id=session_id,
        conversation_id=records["runtime"].conversation_id,
        turn_id="turn_error",
        user_content="Trigger a diagnostic.",
        outcome_content="council vessel PRIVATE_DIAGNOSTIC",
        outcome_kind=ConversationEventKind.RUNTIME_ERROR,
    )
    append_turn(
        store,
        session_id=session_id,
        conversation_id=records["archived"].conversation_id,
        turn_id="turn_archived",
        user_content="Remember the old route.",
        outcome_content="The old route remains visible.",
    )
    catalog.archive(session_id, records["archived"].conversation_id)
    return records


class ConversationSearchContractMixin:
    """Core logical contract shared by in-memory and filesystem collaborators."""

    session_id = "session_search"

    def make_adapters(self) -> tuple[CatalogLike, EventStoreLike]:
        raise NotImplementedError

    def make_search(
        self,
        catalog: CatalogLike,
        store: EventStoreLike,
    ) -> ConversationSearch | None:
        return None

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.catalog, self.store = self.make_adapters()
        self.records = seed_standard_scenario(
            self.catalog,
            self.store,
            session_id=self.session_id,
        )
        self.service = ConversationMemoryService(
            ConversationSearchScope(self.session_id),
            self.catalog,
            self.store,
            search=self.make_search(self.catalog, self.store),
        )

    def test_subject_tiers_precede_one_cited_transcript_hit(self) -> None:
        result = self.service.search(ConversationSearchQuery("council vessel"))
        self.assertEqual(
            [hit.match_kind for hit in result.hits],
            [
                ConversationSearchMatchKind.EXACT_SUBJECT,
                ConversationSearchMatchKind.SUBJECT_PREFIX,
                ConversationSearchMatchKind.SUBJECT_SUBSTRING,
                ConversationSearchMatchKind.TRANSCRIPT,
            ],
        )
        self.assertTrue(all(hit.citation.sequence is None for hit in result.hits[:3]))
        transcript_hit = result.hits[3]
        self.assertEqual(transcript_hit.citation.sequence, 1)
        authoritative = self.store.read(
            self.session_id,
            transcript_hit.citation.conversation_id,
        )[0]
        self.assertEqual(transcript_hit.citation.turn_id, authoritative.turn_id)
        self.assertIs(transcript_hit.citation.event_kind, authoritative.kind)

    def test_archive_is_opt_in_and_runtime_error_is_never_searchable(self) -> None:
        default = self.service.search(ConversationSearchQuery("archived council"))
        included = self.service.search(
            ConversationSearchQuery("archived council", include_archived=True)
        )
        runtime = self.service.search(ConversationSearchQuery("PRIVATE_DIAGNOSTIC"))
        self.assertEqual(default.hits, ())
        self.assertEqual(len(included.hits), 1)
        self.assertIs(included.hits[0].status, ConversationStatus.ARCHIVED)
        self.assertEqual(runtime.hits, ())

        archived_page = self.service.read_transcript(
            self.records["archived"].conversation_id
        )
        self.assertEqual(len(archived_page.events), 2)
        runtime_page = self.service.read_transcript(
            self.records["runtime"].conversation_id
        )
        self.assertIs(
            runtime_page.events[-1].kind,
            ConversationEventKind.RUNTIME_ERROR,
        )

    def test_cited_page_and_cursor_preserve_authoritative_events(self) -> None:
        hit = self.service.search(ConversationSearchQuery("when does")).hits[0]
        page = self.service.read_transcript(
            hit.citation.conversation_id,
            max_events=1,
            citation=hit.citation,
        )
        self.assertEqual(tuple(event.sequence for event in page.events), (1,))
        self.assertIsNotNone(page.next_cursor)
        self.assertEqual(page.next_cursor.next_sequence, 2)
        continued = self.service.continue_transcript(page.next_cursor)
        self.assertEqual(tuple(event.sequence for event in continued.events), (2,))
        self.assertFalse(continued.truncated)
