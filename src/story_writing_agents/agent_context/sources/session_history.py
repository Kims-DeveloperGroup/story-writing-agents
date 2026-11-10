"""Session history."""

from __future__ import annotations

from .capture_values import ContextSourceErrorCode, source_error, source_safe_identity
from ...conversations import (
    CollaborationEventStore,
    ConversationCatalog,
    project_collaboration_readable_history,
)
from ...conversations.session_context import SessionConversationHistory, SessionHistorySnapshot


class SessionHistorySourceAdapter:
    """Capture complete retained histories through storage-neutral owner ports."""

    def __init__(
        self, catalog: ConversationCatalog, event_store: CollaborationEventStore,
        session_id: str,
    ) -> None:
        self._catalog = catalog
        self._event_store = event_store
        self._session_id = source_safe_identity(session_id)

    def capture(self) -> SessionHistorySnapshot:
        metadata = self._catalog.list(self._session_id, include_archived=True)
        histories = []
        for conversation in sorted(metadata, key=lambda item: item.conversation_id):
            events = self._event_store.read(
                self._session_id, conversation.conversation_id,
            )
            histories.append(SessionConversationHistory(
                conversation, project_collaboration_readable_history(events),
            ))
        if metadata != self._catalog.list(self._session_id, include_archived=True):
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return SessionHistorySnapshot(self._session_id, tuple(histories))
