"""Reusable schema-v2 collaboration-history search contract fixtures."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from story_writing_agents import (
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationMetadata,
    ConversationNotFoundError,
    ConversationStatus,
)
from story_writing_agents.conversation_search import (
    CollaborationHistorySearch,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    CollaborationHistoryService,
    ReferenceCollaborationHistorySearch,
    resolve_collaboration_citation,
)
from story_writing_agents.conversations import (
    CollaborationEvent,
    CollaborationEventBatch,
)

from tests.collaboration_contract_fixture import contract_batches, store_batch


BASE_TIME = datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC)


class CatalogLike(Protocol):
    def create(self, session_id: str, subject: str) -> ConversationMetadata: ...

    def get(self, session_id: str, conversation_id: str) -> ConversationMetadata: ...

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
    def append_batch(self, batch: CollaborationEventBatch) -> object: ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEvent, ...]: ...


class MemoryConversationCatalog:
    """Independent in-memory catalog for the shared v2 search contract."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], ConversationMetadata] = {}
        self.counter = 0

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

    def get(self, session_id: str, conversation_id: str) -> ConversationMetadata:
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
        return tuple(
            item
            for (owner, _), item in sorted(self.entries.items())
            if owner == session_id
            and (include_archived or item.status is ConversationStatus.ACTIVE)
        )

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


class MemoryCollaborationEventStore:
    """Independent in-memory canonical v2 history store for search tests."""

    def __init__(self, catalog: MemoryConversationCatalog) -> None:
        self.catalog = catalog
        self.histories: dict[tuple[str, str], tuple[CollaborationEvent, ...]] = {}
        self.read_calls: list[tuple[str, str]] = []

    def append_batch(self, batch: CollaborationEventBatch) -> None:
        first = batch.events[0]
        self.catalog.get(first.session_id, first.conversation_id)
        key = (first.session_id, first.conversation_id)
        self.histories[key] = store_batch(self.histories.get(key, ()), batch)

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEvent, ...]:
        self.catalog.get(session_id, conversation_id)
        self.read_calls.append((session_id, conversation_id))
        return self.histories.get((session_id, conversation_id), ())


def seed_standard_scenario(
    catalog: CatalogLike,
    store: EventStoreLike,
    *,
    session_id: str,
) -> dict[str, ConversationMetadata]:
    """Seed a subject-only decoy plus active and archived v2 histories."""

    subject_only = catalog.create(session_id, "Consult specialists subject only")
    visible = catalog.create(session_id, "Harbor timing")
    archived = catalog.create(session_id, "Archived route")

    def proposal_batch(conversation_id: str) -> CollaborationEventBatch:
        batch = contract_batches(conversation_id)["proposal"]
        return CollaborationEventBatch(
            batch_id=batch.batch_id,
            expected_last_sequence=batch.expected_last_sequence,
            events=tuple(
                replace(event, session_id=session_id) for event in batch.events
            ),
        )

    store.append_batch(proposal_batch(visible.conversation_id))
    store.append_batch(proposal_batch(archived.conversation_id))
    catalog.archive(session_id, archived.conversation_id)
    return {
        "subject_only": subject_only,
        "visible": visible,
        "archived": archived,
    }


class CollaborationSearchContractMixin:
    """Logical search/citation/page contract shared by active adapters."""

    session_id = "session_search"

    def make_adapters(self) -> tuple[CatalogLike, EventStoreLike]:
        raise NotImplementedError

    def make_search(
        self,
        catalog: CatalogLike,
        store: EventStoreLike,
    ) -> CollaborationHistorySearch | None:
        return ReferenceCollaborationHistorySearch(catalog, store)  # type: ignore[arg-type]

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.catalog, self.store = self.make_adapters()
        self.records = seed_standard_scenario(
            self.catalog,
            self.store,
            session_id=self.session_id,
        )
        self.service = CollaborationHistoryService(
            CollaborationHistorySearchScope(self.session_id),
            self.catalog,  # type: ignore[arg-type]
            self.store,  # type: ignore[arg-type]
            search=self.make_search(self.catalog, self.store),
        )

    def test_search_uses_readable_event_fields_and_never_catalog_subjects(self) -> None:
        result = self.service.search(
            CollaborationHistorySearchQuery("Consult specialists")
        )
        self.assertEqual(len(result.hits), 1)
        citation = result.hits[0].citation
        self.assertEqual(
            citation.conversation_id,
            self.records["visible"].conversation_id,
        )
        history = self.store.read(self.session_id, citation.conversation_id)
        self.assertEqual(
            resolve_collaboration_citation(history, citation),
            "Consult specialists",
        )
        self.assertNotEqual(
            citation.conversation_id,
            self.records["subject_only"].conversation_id,
        )

    def test_archived_histories_are_visible_only_in_explicit_scope(self) -> None:
        query = CollaborationHistorySearchQuery("Consult specialists")
        default = self.service.search(query)
        included = ReferenceCollaborationHistorySearch(
            self.catalog,  # type: ignore[arg-type]
            self.store,  # type: ignore[arg-type]
        ).search(
            CollaborationHistorySearchScope(self.session_id, include_archived=True),
            query,
        )
        self.assertEqual(len(default.hits), 1)
        self.assertEqual(len(included.hits), 2)

    def test_cited_pages_preserve_one_exact_history_snapshot(self) -> None:
        hit = self.service.search(
            CollaborationHistorySearchQuery("Consult specialists")
        ).hits[0]
        page = self.service.read_history(
            hit.citation.conversation_id,
            max_records=1,
            citation=hit.citation,
        )
        self.assertEqual(tuple(record.sequence for record in page.records), (1,))
        self.assertIsNotNone(page.next_cursor)
        assert page.next_cursor is not None
        continued = self.service.continue_history(page.next_cursor)
        self.assertEqual(tuple(record.sequence for record in continued.records), (2,))
        self.assertTrue(continued.truncated)
        sequences = [1, 2]
        while continued.next_cursor is not None:
            continued = self.service.continue_history(continued.next_cursor)
            sequences.extend(record.sequence for record in continued.records)
        self.assertEqual(sequences, [1, 2, 3, 4, 5])
        self.assertFalse(continued.truncated)
