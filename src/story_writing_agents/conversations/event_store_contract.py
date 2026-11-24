"""Storage-neutral collaboration event store protocol."""

from __future__ import annotations

from typing import Protocol

from .event_append_results import CollaborationAppendReceipt
from .events import (
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
)
from .reducers.history_projection import CollaborationHistoryProjection


class CollaborationEventStore(Protocol):
    """The sole storage-neutral port for active schema-v2 history services."""

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one validated CAS batch or return its stable retry receipt."""

        ...

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read the complete canonical collaboration history in storage order."""

        ...

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and project the complete canonical collaboration history."""

        ...
