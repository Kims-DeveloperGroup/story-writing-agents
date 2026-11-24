"""Authorized schema-v2 collaboration history search and pages."""

from __future__ import annotations

from typing import Protocol

from .collaboration_history_search import ReferenceCollaborationHistorySearch
from .conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    CollaborationHistoryCitation,
    CollaborationHistoryPage,
    CollaborationHistoryPageCursor,
    CollaborationHistorySearch,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
    continue_collaboration_history,
    page_collaboration_history,
    resolve_collaboration_citation,
    validate_conversation_identifier as _identifier,
    validate_conversation_metadata as _validated_metadata,
)
from .conversations import (
    CollaborationEventStore,
    ConversationCatalog,
    ConversationStatus,
)


class _CollaborationStorageServicesLike(Protocol):
    """Narrow storage bundle view that avoids a storage-to-search cycle."""

    @property
    def catalog(self) -> ConversationCatalog: ...

    @property
    def event_store(self) -> CollaborationEventStore: ...

    @property
    def search(self) -> CollaborationHistorySearch: ...

    @property
    def mutation_coordinator(self) -> object: ...


def _collaboration_storage_service_components(
    value: _CollaborationStorageServicesLike,
) -> tuple[ConversationCatalog, CollaborationEventStore, CollaborationHistorySearch]:
    try:
        catalog = value.catalog
        event_store = value.event_store
        search = value.search
        coordinator = value.mutation_coordinator
    except AttributeError as exc:
        raise TypeError("conversation storage services are invalid") from exc
    if not callable(getattr(catalog, "get", None)):
        raise TypeError("conversation storage catalog is invalid")
    if any(
        not callable(getattr(event_store, name, None))
        for name in ("append_batch", "read", "read_context")
    ):
        raise TypeError("collaboration event store is invalid")
    if not callable(getattr(search, "search", None)):
        raise TypeError("collaboration history search is invalid")
    if not callable(getattr(coordinator, "borrow", None)):
        raise TypeError("conversation storage coordinator is invalid")
    return catalog, event_store, search


class CollaborationHistoryService:
    """Authorize one session for aggregate search and bounded readable pages."""

    def __init__(
        self,
        scope: CollaborationHistorySearchScope,
        catalog: ConversationCatalog | None = None,
        event_store: CollaborationEventStore | None = None,
        *,
        search: CollaborationHistorySearch | None = None,
        conversation_storage_services: _CollaborationStorageServicesLike | None = None,
    ) -> None:
        if type(scope) is not CollaborationHistorySearchScope:
            raise TypeError("collaboration search scope is invalid")
        if conversation_storage_services is not None:
            if catalog is not None or event_store is not None or search is not None:
                raise TypeError(
                    "conversation_storage_services conflicts with raw collaborators"
                )
            catalog, event_store, search = _collaboration_storage_service_components(
                conversation_storage_services
            )
        elif catalog is None or event_store is None:
            raise TypeError(
                "catalog and event_store are required without storage services"
            )
        if not callable(getattr(catalog, "get", None)):
            raise TypeError("conversation catalog must provide get()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("collaboration event store must provide read()")
        if search is not None and not callable(getattr(search, "search", None)):
            raise TypeError("collaboration search adapter must provide search()")
        self._scope = scope
        self._catalog = catalog
        self._event_store = event_store
        self._search = (
            ReferenceCollaborationHistorySearch(catalog, event_store)
            if search is None
            else search
        )

    @property
    def scope(self) -> CollaborationHistorySearchScope:
        """Return the immutable authorization scope."""

        return self._scope

    def search(
        self,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Return field-cited matches from the authorized session."""

        if type(query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        result = self._search.search(self._scope, query)
        if type(result) is not CollaborationHistorySearchResult:
            raise TypeError("collaboration search adapter returned an invalid result")
        if result.query != query:
            raise ValueError("collaboration search adapter changed the query")
        if any(
            hit.citation.session_id != self._scope.session_id for hit in result.hits
        ):
            raise ValueError("collaboration search crossed the authorized session")
        return result

    def read_history(
        self,
        conversation_id: str,
        *,
        start_sequence: int = 1,
        end_sequence: int | None = None,
        max_records: int = CONVERSATION_MEMORY_POLICY.default_page_events,
        max_utf8_bytes: int = CONVERSATION_MEMORY_POLICY.max_page_utf8_bytes,
        citation: CollaborationHistoryCitation | None = None,
    ) -> CollaborationHistoryPage:
        """Read one bounded canonical history page after catalog authorization."""

        conversation = _identifier(conversation_id, label="conversation ID")
        metadata = _validated_metadata(
            self._catalog.get(self._scope.session_id, conversation),
            session_id=self._scope.session_id,
        )
        if metadata.conversation_id != conversation:
            raise ValueError("catalog returned the wrong conversation identity")
        if (
            not self._scope.include_archived
            and metadata.status is ConversationStatus.ARCHIVED
        ):
            raise ValueError("archived conversation is outside the authorized scope")
        events = self._event_store.read(self._scope.session_id, conversation)
        if citation is not None:
            self._validate_citation(citation, conversation_id=conversation)
            resolve_collaboration_citation(events, citation)
        page = page_collaboration_history(
            events,
            start_sequence=start_sequence,
            end_sequence=end_sequence,
            max_records=max_records,
            max_utf8_bytes=max_utf8_bytes,
        )
        if page.session_id is None:
            return CollaborationHistoryPage(
                session_id=self._scope.session_id,
                conversation_id=conversation,
                records=(),
                requested_start_sequence=page.requested_start_sequence,
                requested_end_sequence=page.requested_end_sequence,
                snapshot_last_sequence=0,
                snapshot_tail_batch_digest=None,
                returned_utf8_bytes=0,
                next_cursor=None,
                truncated=False,
            )
        return page

    def continue_history(
        self,
        cursor: CollaborationHistoryPageCursor,
    ) -> CollaborationHistoryPage:
        """Continue a page only while its exact v2 snapshot remains current."""

        if type(cursor) is not CollaborationHistoryPageCursor:
            raise TypeError("collaboration page cursor is invalid")
        if cursor.session_id != self._scope.session_id:
            raise ValueError("collaboration cursor is outside the authorized session")
        metadata = _validated_metadata(
            self._catalog.get(cursor.session_id, cursor.conversation_id),
            session_id=self._scope.session_id,
        )
        if (
            not self._scope.include_archived
            and metadata.status is ConversationStatus.ARCHIVED
        ):
            raise ValueError("archived conversation is outside the authorized scope")
        events = self._event_store.read(cursor.session_id, cursor.conversation_id)
        return continue_collaboration_history(events, cursor)

    def _validate_citation(
        self,
        citation: CollaborationHistoryCitation,
        *,
        conversation_id: str,
    ) -> None:
        if type(citation) is not CollaborationHistoryCitation:
            raise TypeError("collaboration citation is invalid")
        if citation.session_id != self._scope.session_id:
            raise ValueError("collaboration citation is outside the authorized session")
        if citation.conversation_id != conversation_id:
            raise ValueError("collaboration citation identifies another conversation")


__all__ = ["CollaborationHistoryService"]
