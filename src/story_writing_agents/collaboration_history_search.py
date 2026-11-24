"""Deterministic search over readable collaboration history."""

from __future__ import annotations

from .conversation_literal_search import build_conversation_literal_snippet
from .conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    CollaborationHistoryCitation,
    CollaborationHistorySearchHit,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    CollaborationHistorySearchScope,
    collaboration_history_search_hit_key as _collaboration_search_hit_key,
    normalize_conversation_literal_with_spans as _normalized_literal_with_spans,
    normalize_conversation_search_query as _normalized_query,
    validate_conversation_metadata as _validated_metadata,
)
from .conversations import (
    CollaborationEventStore,
    ConversationCatalog,
    ConversationStatus,
    project_collaboration_readable_history,
)


class ReferenceCollaborationHistorySearch:
    """Bounded deterministic aggregate search over a catalog and v2 store."""

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: CollaborationEventStore,
    ) -> None:
        if not callable(getattr(catalog, "list", None)):
            raise TypeError("conversation catalog must provide list()")
        if not callable(getattr(event_store, "read", None)):
            raise TypeError("collaboration event store must provide read()")
        self._catalog = catalog
        self._event_store = event_store

    def search(
        self,
        scope: CollaborationHistorySearchScope,
        query: CollaborationHistorySearchQuery,
    ) -> CollaborationHistorySearchResult:
        """Search ordered public fields only; catalog subjects never create hits."""

        if type(scope) is not CollaborationHistorySearchScope:
            raise TypeError("collaboration search scope is invalid")
        if type(query) is not CollaborationHistorySearchQuery:
            raise TypeError("collaboration search query is invalid")
        raw_metadata = self._catalog.list(
            scope.session_id,
            include_archived=scope.include_archived,
        )
        if type(raw_metadata) is not tuple:
            raise TypeError("conversation catalog list must return an exact tuple")
        metadata = tuple(
            _validated_metadata(item, session_id=scope.session_id)
            for item in raw_metadata
        )
        if len({item.conversation_id for item in metadata}) != len(metadata):
            raise ValueError("catalog returned duplicate conversation identities")
        if not scope.include_archived and any(
            item.status is ConversationStatus.ARCHIVED for item in metadata
        ):
            raise ValueError("catalog returned archived metadata without authorization")

        selected = metadata[: CONVERSATION_MEMORY_POLICY.max_scan_conversations]
        scan_truncated = len(selected) != len(metadata)
        remaining_records = CONVERSATION_MEMORY_POLICY.max_scan_events
        matched: list[CollaborationHistorySearchHit] = []
        scanned_fields = 0
        scanned_records = 0
        normalized_query = _normalized_query(query.text)
        for metadata_index, item in enumerate(selected):
            events = self._event_store.read(item.session_id, item.conversation_id)
            records = project_collaboration_readable_history(events)
            retained = records[:remaining_records]
            if len(retained) != len(records):
                scan_truncated = True
            for record in retained:
                scanned_records += 1
                for field in record.fields:
                    scanned_fields += 1
                    normalized_text, spans = _normalized_literal_with_spans(field.text)
                    match_start = normalized_text.find(normalized_query)
                    if match_start < 0:
                        continue
                    match_spans = spans[
                        match_start : match_start + len(normalized_query)
                    ]
                    matched.append(
                        CollaborationHistorySearchHit(
                            citation=CollaborationHistoryCitation(
                                session_id=record.session_id,
                                conversation_id=record.conversation_id,
                                event_id=record.event_id,
                                sequence=record.sequence,
                                field_key=field.key,
                                start_codepoint=min(span[0] for span in match_spans),
                                end_codepoint=max(span[1] for span in match_spans),
                                assignment_ordinal=field.assignment_ordinal,
                                input_ordinal=field.input_ordinal,
                            ),
                            snippet=build_conversation_literal_snippet(
                                normalized_text,
                                normalized_query,
                                max_characters=(
                                    CONVERSATION_MEMORY_POLICY.max_snippet_characters
                                ),
                                max_utf8_bytes=(
                                    CONVERSATION_MEMORY_POLICY.max_snippet_utf8_bytes
                                ),
                            ),
                        )
                    )
            remaining_records -= len(retained)
            if remaining_records == 0:
                if metadata_index + 1 < len(selected):
                    scan_truncated = True
                break

        matched.sort(key=_collaboration_search_hit_key)
        retained_hits = tuple(matched[: query.limit])
        return CollaborationHistorySearchResult(
            query=query,
            normalized_query=normalized_query,
            hits=retained_hits,
            matched_fields=len(matched),
            scanned_records=scanned_records,
            scanned_fields=scanned_fields,
            scan_truncated=scan_truncated,
            truncated=scan_truncated or len(matched) > len(retained_hits),
        )


__all__ = ["ReferenceCollaborationHistorySearch"]
