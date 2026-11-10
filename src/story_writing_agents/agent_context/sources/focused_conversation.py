"""Focused conversation."""

from __future__ import annotations

from dataclasses import field
from ..actions import ContextSourceBoundary
from ..manifest import ContextAuthorityKind, ContextProvenance, ContextScope, ContextSourceKind
from .capture_values import (
    ContextSourceDescriptor,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceSnapshot,
    require_source_policy,
    source_canonical_json_bytes,
    source_descriptor,
    source_digest,
    source_error,
    source_record,
    source_safe_identity,
    source_structured_source_id,
)
from ...conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationContextBootstrap,
    CollaborationEvent,
    CollaborationEventStore,
    CollaborationReadableContext,
    CollaborationReadableRecord,
    ConversationCatalog,
    ConversationMetadata,
    ConversationStatus,
    encode_collaboration_event,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
)
from ...errors import ConversationNotFoundError, StoryWritingAgentsError


def _readable_record_value(record: CollaborationReadableRecord) -> dict[str, object]:
    event = CollaborationEvent(
        schema_version=record.schema_version,
        event_id=record.event_id,
        session_id=record.session_id,
        conversation_id=record.conversation_id,
        turn_id=record.turn_id,
        step_id=record.step_id,
        sequence=record.sequence,
        timestamp=record.timestamp,
        batch_id=record.batch_id,
        batch_ordinal=record.batch_ordinal,
        batch_size=record.batch_size,
        batch_digest=record.batch_digest,
        kind=record.kind,
        sender=record.sender,
        recipient=record.recipient,
        payload=record.payload,
    )
    revision = source_digest(
        b"story-agent-context-collaboration-event-v2\0"
        + encode_collaboration_event(event)
        + b"\0"
        + record.batch_digest.encode("ascii")
    )
    return {
        "schema_version": record.schema_version,
        "session_id": record.session_id,
        "conversation_id": record.conversation_id,
        "turn_id": record.turn_id,
        "step_id": record.step_id,
        "event_id": record.event_id,
        "batch_id": record.batch_id,
        "batch_ordinal": record.batch_ordinal,
        "batch_size": record.batch_size,
        "batch_digest": record.batch_digest,
        "sequence": record.sequence,
        "timestamp": record.timestamp,
        "sender": {
            "kind": record.sender.kind.value,
            "agent_id": record.sender.agent_id,
        },
        "recipient": {
            "kind": record.recipient.kind.value,
            "agent_id": record.recipient.agent_id,
        },
        "direction": record.direction.value,
        "kind": record.kind.value,
        "event_revision": revision,
        "fields": [
            {
                "key": field.key.value,
                "text": field.text,
                "assignment_ordinal": field.assignment_ordinal,
                "input_ordinal": field.input_ordinal,
            }
            for field in record.fields
        ],
    }

def _readable_context_bootstrap(
    context: CollaborationReadableContext,
    *,
    session_id: str,
    conversation_id: str,
) -> dict[str, object]:
    bootstrap = context.bootstrap
    return {
        "schema_version": bootstrap.schema_version,
        "session_id": bootstrap.session_id or session_id,
        "conversation_id": bootstrap.conversation_id or conversation_id,
        "total_records": bootstrap.total_records,
        "last_sequence": bootstrap.last_sequence,
        "tail_batch_digest": bootstrap.tail_batch_digest,
        "requested_records": context.requested_records,
        "retained_records": context.retained_records,
        "omitted_records": context.omitted_records,
        "retained_utf8_bytes": context.retained_utf8_bytes,
        "truncated": context.truncated,
    }

class FocusedConversationSourceAdapter:
    """Expose explicitly selected readable collaboration records for context."""

    source_kind = ContextSourceKind.CONVERSATION

    def __init__(
        self,
        catalog: ConversationCatalog,
        event_store: CollaborationEventStore,
        scope: ContextScope,
        conversation_id: str | None,
        *,
        include_same_session_active: bool = False,
        include_searchable_history: bool = False,
        captured_context: CollaborationReadableContext | None = None,
    ) -> None:
        if type(scope) is not ContextScope:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if conversation_id is not None:
            source_safe_identity(conversation_id)
            if scope.subject_kind.value == "conversation" and (
                scope.subject_id != conversation_id
            ):
                raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if (
            type(include_same_session_active) is not bool
            or type(include_searchable_history) is not bool
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if include_same_session_active and conversation_id is None:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if captured_context is not None:
            if (
                type(captured_context) is not CollaborationReadableContext
                or conversation_id is None
                or include_same_session_active
                or include_searchable_history
            ):
                raise source_error(ContextSourceErrorCode.INVALID_VALUE)
            bootstrap = captured_context.bootstrap
            if (
                bootstrap.session_id != scope.session_id
                or bootstrap.conversation_id != conversation_id
            ):
                raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if not callable(getattr(catalog, "get", None)) or any(
            not callable(getattr(event_store, name, None))
            for name in ("read", "read_context")
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if include_same_session_active and not callable(getattr(catalog, "list", None)):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._catalog = catalog
        self._event_store = event_store
        self.scope = scope
        self._conversation_id = conversation_id
        self._include_same_session_active = include_same_session_active
        self._include_searchable_history = include_searchable_history
        self._captured_context = captured_context
        self._snapshot_conversation_ids: tuple[str, ...] | None = None
        self._snapshots: dict[
            tuple[str, ContextSourcePolicy], ContextSourceSnapshot
        ] = {}

    def inventory(
        self, policy: ContextSourcePolicy
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = require_source_policy(policy)
        return tuple(
            self.capture(self._locator(conversation_id), policy).descriptor
            for conversation_id in self._active_conversation_ids()
        )

    @staticmethod
    def _locator(conversation_id: str) -> str:
        return f"conversation/{conversation_id}"

    def _active_conversation_ids(self) -> tuple[str, ...]:
        if self._include_searchable_history and (
            self._snapshot_conversation_ids is not None
        ):
            return self._snapshot_conversation_ids
        if self._captured_context is not None or not self._include_same_session_active:
            identifiers = (
                () if self._conversation_id is None else (self._conversation_id,)
            )
            if self._include_searchable_history:
                self._snapshot_conversation_ids = identifiers
            return identifiers
        try:
            raw = self._catalog.list(self.scope.session_id, include_archived=False)
        except (StoryWritingAgentsError, OSError, TypeError, ValueError):
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE) from None
        if type(raw) is not tuple:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        identifiers: list[str] = []
        for metadata in raw:
            if type(metadata) is not ConversationMetadata:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            if metadata.session_id != self.scope.session_id:
                raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
            if metadata.status is not ConversationStatus.ACTIVE:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            identifiers.append(metadata.conversation_id)
        if len(set(identifiers)) != len(identifiers):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if self._conversation_id not in identifiers:
            raise source_error(ContextSourceErrorCode.SOURCE_MISSING)
        captured = tuple(identifiers)
        if self._include_searchable_history:
            self._snapshot_conversation_ids = captured
        return captured

    def _authorized_conversation_id(self, locator: str) -> str:
        prefix = "conversation/"
        if not locator.startswith(prefix):
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        conversation_id = locator[len(prefix) :]
        try:
            source_safe_identity(conversation_id)
        except ContextSourceError:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH) from None
        if conversation_id not in self._active_conversation_ids():
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        return conversation_id

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = require_source_policy(policy)
        conversation_id = self._authorized_conversation_id(locator)
        try:
            metadata = self._catalog.get(self.scope.session_id, conversation_id)
            if self._captured_context is None:
                events = self._event_store.read(self.scope.session_id, conversation_id)
                if self._include_searchable_history:
                    history = project_collaboration_readable_history(events)
                    context = CollaborationReadableContext(
                        bootstrap=CollaborationContextBootstrap(
                            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
                            session_id=history[0].session_id if history else None,
                            conversation_id=history[0].conversation_id if history else None,
                            total_records=len(history),
                            last_sequence=history[-1].sequence if history else 0,
                            tail_batch_digest=history[-1].batch_digest if history else None,
                        ),
                        records=history,
                        requested_records=len(history),
                        retained_records=len(history),
                        omitted_records=0,
                        retained_utf8_bytes=sum(record.readable_utf8_bytes for record in history),
                        truncated=False,
                    )
                else:
                    context = select_bounded_collaboration_context(events)
            else:
                context = self._captured_context
        except ConversationNotFoundError:
            raise source_error(ContextSourceErrorCode.SOURCE_MISSING) from None
        except (StoryWritingAgentsError, OSError, TypeError, ValueError):
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE) from None
        if (
            type(metadata) is not ConversationMetadata
            or metadata.session_id != self.scope.session_id
            or metadata.conversation_id != conversation_id
        ):
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if metadata.status is not ConversationStatus.ACTIVE:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        bootstrap = context.bootstrap
        if bootstrap.session_id not in (
            None,
            self.scope.session_id,
        ) or bootstrap.conversation_id not in (None, conversation_id):
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if context.retained_records > policy.max_records_per_source:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        values = tuple(_readable_record_value(record) for record in context.records)
        records = tuple(
            source_record(
                record.event_id,
                ContextSourceBoundary.records(record.sequence, record.sequence),
                source_canonical_json_bytes(value).decode("utf-8"),
                "\n".join(field.text for field in record.fields),
            )
            for record, value in zip(context.records, values, strict=True)
        )
        body = source_canonical_json_bytes(
            {
                "bootstrap": _readable_context_bootstrap(
                    context,
                    session_id=self.scope.session_id,
                    conversation_id=conversation_id,
                ),
                "records": values,
            }
        )
        if len(body) > policy.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        descriptor = source_descriptor(
            scope=self.scope,
            source_id=source_structured_source_id("conversation", conversation_id),
            source_kind=self.source_kind,
            revision=source_digest(body),
            provenance=ContextProvenance(
                ContextAuthorityKind.CONVERSATION_STORAGE,
                locator,
            ),
            content=body,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, records, body)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = require_source_policy(policy)
        self._authorized_conversation_id(locator)
        cache_key = (locator, policy)
        if self._include_searchable_history:
            cached = self._snapshots.get(cache_key)
            if cached is not None:
                return cached
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                if self._include_searchable_history:
                    self._snapshots[cache_key] = second
                return second
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
