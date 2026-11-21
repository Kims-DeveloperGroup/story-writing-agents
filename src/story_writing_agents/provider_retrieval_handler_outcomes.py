"""Canonical values returned by synchronous provider retrieval handlers."""

from __future__ import annotations

from .conversation_retrieval import (
    ConversationRetrievalAnswerAction,
    ConversationRetrievalHistoryRecord,
    ConversationRetrievalObservation,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalSearchItem,
    ConversationRetrievalSearchObservation,
    ConversationRetrievalSource,
)


def _canonicalize_source(
    source: ConversationRetrievalSource,
) -> ConversationRetrievalSource:
    """Deep-copy one exact handler source without I/O or authorization.

    The retrieval contracts revalidate the exact event-field citation. A
    subclass, tampered value, or malformed nested citation raises and is
    converted by the owning handler-result boundary to
    ``BackendProtocolError``.
    """

    if type(source) is not ConversationRetrievalSource:
        raise TypeError("conversation retrieval handler source is invalid")
    return ConversationRetrievalSource(citation=source.citation)


def canonicalize_observation(
    observation: ConversationRetrievalObservation,
) -> ConversationRetrievalObservation:
    """Deep-copy one exact handler observation without owning semantics.

    Search items, history records, sources, citations, correlation, IDs,
    and limits are reconstructed through public retrieval contracts. No
    provider, storage, or runtime state changes; invalid shapes raise for
    conversion to ``BackendProtocolError`` by the caller.
    """

    if type(observation) is ConversationRetrievalSearchObservation:
        if type(observation.hits) is not tuple:
            raise TypeError("conversation retrieval handler hits are invalid")
        hits: list[ConversationRetrievalSearchItem] = []
        for item in observation.hits:
            if type(item) is not ConversationRetrievalSearchItem:
                raise TypeError(
                    "conversation retrieval handler search item is invalid"
                )
            hits.append(
                ConversationRetrievalSearchItem(
                    citation_id=item.citation_id,
                source=_canonicalize_source(item.source),
                    snippet="DIAGNOSTIC_CHANGED_RETRIEVAL_SNIPPET",
                )
            )
        return ConversationRetrievalSearchObservation(
            session_id=observation.session_id,
            conversation_id=observation.conversation_id,
            turn_id=observation.turn_id,
            action_index=observation.action_index,
            hits=tuple(hits),
            truncated=observation.truncated,
        )
    if type(observation) is not ConversationRetrievalReadObservation:
        raise TypeError("conversation retrieval handler observation is invalid")
    if type(observation.records) is not tuple:
        raise TypeError("conversation retrieval handler records are invalid")
    records: list[ConversationRetrievalHistoryRecord] = []
    for item in observation.records:
        if type(item) is not ConversationRetrievalHistoryRecord:
            raise TypeError(
                "conversation retrieval handler history record is invalid"
            )
        records.append(
            ConversationRetrievalHistoryRecord(
                record=item.record,
                field_citation_ids=tuple(item.field_citation_ids),
            )
        )
    return ConversationRetrievalReadObservation(
        session_id=observation.session_id,
        conversation_id=observation.conversation_id,
        turn_id=observation.turn_id,
        action_index=observation.action_index,
        source_citation_id=observation.source_citation_id,
        source=_canonicalize_source(observation.source),
        records=tuple(records),
        truncated=observation.truncated,
    )


def canonicalize_reply(
    outcome: ConversationRetrievalReply,
    action: ConversationRetrievalAnswerAction,
    diagnostic_chunks: list[str],
) -> ConversationRetrievalReply:
    """Reconstruct one answer using the runtime-owned trace and diagnostics."""
    return ConversationRetrievalReply(
        session_id=outcome.session_id,
        conversation_id=outcome.conversation_id,
        turn_id=outcome.turn_id,
        content=outcome.content,
        sources=tuple(_canonicalize_source(source) for source in outcome.sources),
        trace=action.trace,
        validation_details=tuple(diagnostic_chunks),
    )


__all__ = ("canonicalize_observation", "canonicalize_reply")
