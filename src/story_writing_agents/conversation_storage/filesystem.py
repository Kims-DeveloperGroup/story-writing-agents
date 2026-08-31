"""Filesystem conversation catalog and event-store persistence."""

from __future__ import annotations

import errno
import json
import os
import stat
import threading
import weakref
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX platforms
    _fcntl = None  # type: ignore[assignment]

from ..conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationEvent as CollaborationEventV2,
    CollaborationEventBatch,
    CollaborationHistoryProjection,
    ConversationCatalog,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    collaboration_batch_digest,
    decode_collaboration_event,
    encode_collaboration_event,
    project_collaboration_history,
    select_bounded_conversation_context,
    validate_collaboration_candidate_batch,
)
from ..errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    ManuscriptExistsError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ..workspace.core import Workspace


_CONVERSATION_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "conversation_id",
        "subject",
        "subject_revision",
        "status",
        "created_at",
        "updated_at",
    }
)
_CONVERSATION_ID_ALLOCATION_ATTEMPTS = 16
_CONVERSATION_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "conversation_id",
        "turn_id",
        "sequence",
        "timestamp",
        "sender",
        "recipient",
        "kind",
        "content",
    }
)


class _ConversationEventLock:
    """Weakly retained process-wide serialization for one transcript."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


_CONVERSATION_EVENT_LOCK_REGISTRY_GUARD = threading.Lock()
_CONVERSATION_EVENT_LOCKS: weakref.WeakValueDictionary[
    tuple[str, str, str],
    _ConversationEventLock,
] = weakref.WeakValueDictionary()


class _CollaborationEventLock:
    """Weakly retained process-wide serialization for one v2 history."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


_COLLABORATION_EVENT_LOCK_REGISTRY_GUARD = threading.Lock()
_COLLABORATION_EVENT_LOCKS: weakref.WeakValueDictionary[
    tuple[str, str, str],
    _CollaborationEventLock,
] = weakref.WeakValueDictionary()


class FilesystemConversationCatalog:
    """Persist conversation metadata through one secure ``Workspace``."""

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        """Atomically create metadata under a new immutable identifier."""

        session = self.workspace.validate_identifier(session_id)
        for _ in range(_CONVERSATION_ID_ALLOCATION_ATTEMPTS):
            conversation_id = f"c_{uuid4().hex}"
            timestamp = self._timestamp()
            metadata = ConversationMetadata(
                schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
                session_id=session,
                conversation_id=conversation_id,
                subject=subject,
                subject_revision=1,
                status=ConversationStatus.ACTIVE,
                created_at=timestamp,
                updated_at=timestamp,
            )
            relative = self._metadata_relative(session, conversation_id)
            conversation_directory = self.workspace.path(relative.parent)
            if conversation_directory.exists():
                continue
            try:
                self.workspace.write_json(
                    relative,
                    metadata.to_dict(),
                    overwrite=False,
                )
            except ManuscriptExistsError:
                continue
            except Exception:
                self._remove_empty_directory(conversation_directory)
                raise
            return metadata
        raise ConversationExistsError(
            f"could not allocate a unique conversation ID in session {session!r}"
        )

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Read and strictly validate one session-owned metadata record."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        relative = self._metadata_relative(session, conversation)
        target = self.workspace.path(relative)
        if not target.exists():
            conversation_directory = self.workspace.path(relative.parent)
            if conversation_directory.exists():
                if not conversation_directory.is_dir():
                    raise WorkspaceFormatError(
                        "conversation artifact is not a directory: "
                        f"{session}/{conversation}"
                    )
                raise WorkspaceFormatError(
                    f"conversation metadata is missing: {session}/{conversation}"
                )
            raise ConversationNotFoundError(
                f"conversation does not exist: {session}/{conversation}"
            )
        if not target.is_file():
            raise WorkspaceFormatError(
                f"conversation metadata is not a file: {session}/{conversation}"
            )
        return self._read_metadata(relative, session, conversation)

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        """Return deterministic metadata without creating absent storage."""

        session = self.workspace.validate_identifier(session_id)
        if not isinstance(include_archived, bool):
            raise TypeError("include_archived must be a boolean")
        catalog_relative = self._catalog_relative(session)
        catalog = self.workspace.path(catalog_relative)
        if not catalog.exists():
            return ()
        if not catalog.is_dir():
            raise WorkspaceFormatError(
                f"conversation catalog is not a directory: {session}"
            )
        try:
            entries = sorted(catalog.iterdir(), key=lambda entry: entry.name)
        except OSError as exc:
            raise WorkspaceFormatError(
                f"cannot read conversation catalog: {session}"
            ) from exc

        metadata_entries: list[ConversationMetadata] = []
        for entry in entries:
            relative_entry = entry.relative_to(self.workspace.root)
            safe_entry = self.workspace.path(relative_entry)
            try:
                conversation = self.workspace.validate_identifier(
                    safe_entry.name,
                    label="conversation ID",
                )
            except SessionIdError as exc:
                raise WorkspaceFormatError(
                    f"conversation catalog contains an unsafe ID: {session}"
                ) from exc
            if not safe_entry.is_dir():
                raise WorkspaceFormatError(
                    "conversation catalog entry is not a directory: "
                    f"{session}/{conversation}"
                )
            relative = self._metadata_relative(session, conversation)
            target = self.workspace.path(relative)
            if not target.is_file():
                raise WorkspaceFormatError(
                    f"conversation metadata is missing: {session}/{conversation}"
                )
            metadata = self._read_metadata(relative, session, conversation)
            if include_archived or metadata.status is ConversationStatus.ACTIVE:
                metadata_entries.append(metadata)
        return tuple(
            sorted(
                metadata_entries,
                key=lambda metadata: (
                    metadata.created_at,
                    metadata.conversation_id,
                ),
            )
        )

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        """Atomically replace the subject and increment its revision."""

        current = self.get(session_id, conversation_id)
        updated = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=subject,
            subject_revision=current.subject_revision + 1,
            status=current.status,
            created_at=current.created_at,
            updated_at=self._timestamp(),
        )
        self._write_metadata(updated)
        return updated

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Atomically archive a conversation, or return it unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ARCHIVED,
        )

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        """Atomically reactivate a conversation, or return it unchanged."""

        return self._set_status(
            session_id,
            conversation_id,
            ConversationStatus.ACTIVE,
        )

    def _set_status(
        self,
        session_id: str,
        conversation_id: str,
        status: ConversationStatus,
    ) -> ConversationMetadata:
        """Return metadata in the requested status, persisting only a change.

        An already matching record is returned unchanged. Otherwise a new UTC
        update timestamp is stored atomically through the workspace; read,
        validation, and write failures propagate.
        """

        current = self.get(session_id, conversation_id)
        if current.status is status:
            return current
        updated = ConversationMetadata(
            schema_version=current.schema_version,
            session_id=current.session_id,
            conversation_id=current.conversation_id,
            subject=current.subject,
            subject_revision=current.subject_revision,
            status=status,
            created_at=current.created_at,
            updated_at=self._timestamp(),
        )
        self._write_metadata(updated)
        return updated

    def _write_metadata(self, metadata: ConversationMetadata) -> None:
        """Overwrite the identity-derived metadata file with its exact schema."""

        relative = self._metadata_relative(
            metadata.session_id,
            metadata.conversation_id,
        )
        self.workspace.write_json(relative, metadata.to_dict())

    def _read_metadata(
        self,
        relative: Path,
        expected_session_id: str,
        expected_conversation_id: str,
    ) -> ConversationMetadata:
        """Read and strictly validate identity-bound conversation metadata.

        Duplicate fields, malformed JSON, schema/value violations, and path
        identity mismatches become ``WorkspaceFormatError``; storage is not
        mutated.
        """

        logical_artifact = (
            f"{expected_session_id}/{expected_conversation_id}/metadata.json"
        )
        text = self.workspace.read_text(relative)

        def reject_duplicate_fields(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            """Build one metadata object or raise on a duplicate field."""

            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate field: {key}")
                result[key] = value
            return result

        try:
            value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
        except (json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"conversation metadata is not valid JSON: {logical_artifact}"
            ) from exc
        if not isinstance(value, dict) or set(value) != _CONVERSATION_METADATA_FIELDS:
            raise WorkspaceFormatError(
                f"conversation metadata has an invalid schema: {logical_artifact}"
            )
        try:
            raw_status = value["status"]
            if not isinstance(raw_status, str):
                raise TypeError("status must be a string")
            metadata = ConversationMetadata(
                schema_version=value["schema_version"],
                session_id=value["session_id"],
                conversation_id=value["conversation_id"],
                subject=value["subject"],
                subject_revision=value["subject_revision"],
                status=ConversationStatus(raw_status),
                created_at=value["created_at"],
                updated_at=value["updated_at"],
            )
        except (SessionIdError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                f"conversation metadata has an invalid value: {logical_artifact}"
            ) from exc
        if (
            metadata.session_id != expected_session_id
            or metadata.conversation_id != expected_conversation_id
        ):
            raise WorkspaceFormatError(
                "conversation metadata does not match its owning path: "
                f"{logical_artifact}"
            )
        return metadata

    @staticmethod
    def _catalog_relative(session_id: str) -> Path:
        """Return the catalog directory for a previously validated session."""

        return Path("discussion_logs") / session_id / "interactive" / "conversations"

    @classmethod
    def _metadata_relative(
        cls,
        session_id: str,
        conversation_id: str,
    ) -> Path:
        """Return the metadata path for previously validated identities."""

        return cls._catalog_relative(session_id) / conversation_id / "metadata.json"

    @staticmethod
    def _timestamp() -> str:
        """Return the current aware UTC timestamp in ISO-8601 form."""

        return datetime.now(UTC).isoformat()

    @staticmethod
    def _remove_empty_directory(directory: Path) -> None:
        """Best-effort remove an empty directory, suppressing every OS error."""

        try:
            directory.rmdir()
        except OSError:
            pass


class FilesystemConversationEventStore:
    """Persist authoritative visible transcripts through one secure workspace."""

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
        *,
        catalog: ConversationCatalog | None = None,
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self.catalog = (
            catalog
            if catalog is not None
            else FilesystemConversationCatalog(self.workspace)
        )

    def append(self, event: ConversationEventAppend) -> ConversationEvent:
        """Validate the complete transcript and durably append one event."""

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
        event_lock = self._event_lock(event.session_id, event.conversation_id)
        with event_lock.lock:
            self._verify_catalog_identity(event.session_id, event.conversation_id)
            events = self._read_events(event.session_id, event.conversation_id)
            self._validate_append(events, event)
            stored = ConversationEvent(
                schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
                session_id=event.session_id,
                conversation_id=event.conversation_id,
                turn_id=event.turn_id,
                sequence=len(events) + 1,
                timestamp=datetime.now(UTC).isoformat(),
                sender=event.sender,
                recipient=event.recipient,
                kind=event.kind,
                content=event.content,
            )
            self._write_events((*events, stored))
            return stored

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Read and strictly validate the full authoritative transcript."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            return self._read_events(session, conversation)

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationContextWindow:
        """Return the fixed-policy newest suffix of complete turns."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            events = self._read_events(session, conversation)
            return select_bounded_conversation_context(events)

    def _verify_catalog_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        """Verify catalog metadata matches the requested transcript path.

        The catalog is read but not mutated. Missing attributes or an identity
        mismatch raise ``WorkspaceFormatError``; catalog failures propagate.
        """

        metadata = self.catalog.get(session_id, conversation_id)
        try:
            matches_path = (
                metadata.session_id == session_id
                and metadata.conversation_id == conversation_id
            )
        except AttributeError as exc:
            raise WorkspaceFormatError(
                "conversation catalog returned invalid metadata: "
                f"{session_id}/{conversation_id}"
            ) from exc
        if not matches_path:
            raise WorkspaceFormatError(
                "conversation catalog identity does not match transcript path: "
                f"{session_id}/{conversation_id}"
            )

    def _read_events(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[ConversationEvent, ...]:
        """Return a strictly validated authoritative transcript in file order.

        An absent file yields an empty tuple. Present JSON Lines must have exact
        schemas, path identity, contiguous sequences, and valid turn pairing;
        malformed storage raises ``WorkspaceFormatError`` without mutation.
        """

        relative = self._transcript_relative(session_id, conversation_id)
        logical_artifact = self._logical_artifact(session_id, conversation_id)
        target = self.workspace.path(relative)
        if not target.exists():
            return ()
        if not target.is_file():
            raise WorkspaceFormatError(
                f"conversation transcript is not a file: {logical_artifact}"
            )
        try:
            text = self.workspace.read_text(relative)
        except WorkspaceFormatError as exc:
            raise WorkspaceFormatError(
                f"invalid conversation transcript {logical_artifact}: {exc}"
            ) from exc
        if not text:
            raise WorkspaceFormatError(
                f"conversation transcript is empty: {logical_artifact}"
            )

        records = text[:-1] if text.endswith("\n") else text
        events: list[ConversationEvent] = []
        for line_number, line in enumerate(records.split("\n"), 1):
            try:
                value = json.loads(
                    line,
                    object_pairs_hook=self._reject_duplicate_fields,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "conversation transcript is not valid JSON: "
                    f"{logical_artifact} at line {line_number}"
                ) from exc
            if not isinstance(value, dict) or set(value) != _CONVERSATION_EVENT_FIELDS:
                raise WorkspaceFormatError(
                    "conversation event has an invalid schema: "
                    f"{logical_artifact} at line {line_number}"
                )
            try:
                raw_kind = value["kind"]
                if not isinstance(raw_kind, str):
                    raise TypeError("event kind must be a string")
                event = ConversationEvent(
                    schema_version=value["schema_version"],
                    session_id=value["session_id"],
                    conversation_id=value["conversation_id"],
                    turn_id=value["turn_id"],
                    sequence=value["sequence"],
                    timestamp=value["timestamp"],
                    sender=value["sender"],
                    recipient=value["recipient"],
                    kind=ConversationEventKind(raw_kind),
                    content=value["content"],
                )
            except (SessionIdError, TypeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "conversation event has an invalid value: "
                    f"{logical_artifact} at line {line_number}"
                ) from exc
            if (
                event.session_id != session_id
                or event.conversation_id != conversation_id
            ):
                raise WorkspaceFormatError(
                    "conversation event does not match its owning path: "
                    f"{logical_artifact} at line {line_number}"
                )
            if event.sequence != line_number:
                raise WorkspaceFormatError(
                    "conversation event sequence is not contiguous: "
                    f"{logical_artifact} at line {line_number}"
                )
            events.append(event)
        validated = tuple(events)
        self._validate_pairs(validated, logical_artifact=logical_artifact)
        return validated

    def _write_events(self, events: tuple[ConversationEvent, ...]) -> None:
        """Atomically overwrite one transcript with canonical JSON Lines.

        The nonempty tuple is serialized in its supplied order using the first
        event's identity; an empty tuple raises ``ValueError`` and workspace
        failures propagate.
        """

        if not events:
            raise ValueError("cannot write an empty conversation transcript")
        relative = self._transcript_relative(
            events[0].session_id,
            events[0].conversation_id,
        )
        content = "".join(
            json.dumps(
                event.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for event in events
        )
        self.workspace.write_text(relative, content)

    @staticmethod
    def _validate_append(
        events: tuple[ConversationEvent, ...],
        event: ConversationEventAppend,
    ) -> None:
        """Validate a proposed event against existing turn ownership and order.

        Each turn accepts one user event followed by at most one outcome, and a
        reply must come from the addressed agent. Violations raise ``ValueError``
        without mutation.
        """

        user_events = {
            stored.turn_id: stored
            for stored in events
            if stored.kind is ConversationEventKind.USER_MESSAGE
        }
        completed_turn_ids = {
            stored.turn_id
            for stored in events
            if stored.kind is not ConversationEventKind.USER_MESSAGE
        }
        if event.kind is ConversationEventKind.USER_MESSAGE:
            if event.turn_id in user_events:
                raise ValueError(
                    f"conversation turn already has a user event: {event.turn_id}"
                )
            return
        user_event = user_events.get(event.turn_id)
        if user_event is None:
            raise ValueError(f"conversation outcome has no user event: {event.turn_id}")
        if event.turn_id in completed_turn_ids:
            raise ValueError(
                f"conversation turn already has an outcome: {event.turn_id}"
            )
        if (
            event.kind is ConversationEventKind.AGENT_REPLY
            and event.sender != user_event.recipient
        ):
            raise ValueError(
                "conversation reply sender does not match the addressed agent: "
                f"{event.turn_id}"
            )

    @staticmethod
    def _validate_pairs(
        events: tuple[ConversationEvent, ...],
        *,
        logical_artifact: str,
    ) -> None:
        """Validate stored user/outcome pairing in transcript sequence order.

        Duplicate users, outcomes before users, duplicate outcomes, or wrong
        reply senders raise artifact-specific ``WorkspaceFormatError`` without
        mutation.
        """

        user_events: dict[str, ConversationEvent] = {}
        completed_turn_ids: set[str] = set()
        for event in events:
            if event.kind is ConversationEventKind.USER_MESSAGE:
                if event.turn_id in user_events:
                    raise WorkspaceFormatError(
                        "conversation turn has duplicate user events: "
                        f"{logical_artifact} at sequence {event.sequence}"
                    )
                user_events[event.turn_id] = event
                continue
            user_event = user_events.get(event.turn_id)
            if user_event is None:
                raise WorkspaceFormatError(
                    "conversation outcome precedes its user event: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            if event.turn_id in completed_turn_ids:
                raise WorkspaceFormatError(
                    "conversation turn has duplicate outcomes: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            if (
                event.kind is ConversationEventKind.AGENT_REPLY
                and event.sender != user_event.recipient
            ):
                raise WorkspaceFormatError(
                    "conversation reply sender does not match the addressed agent: "
                    f"{logical_artifact} at sequence {event.sequence}"
                )
            completed_turn_ids.add(event.turn_id)

    def _event_lock(
        self,
        session_id: str,
        conversation_id: str,
    ) -> _ConversationEventLock:
        """Return the process-wide lock for one workspace transcript identity.

        The weak registry is synchronized and populated when no live lock
        exists; no filesystem resource is acquired here.
        """

        key = (os.fspath(self.workspace.root), session_id, conversation_id)
        with _CONVERSATION_EVENT_LOCK_REGISTRY_GUARD:
            event_lock = _CONVERSATION_EVENT_LOCKS.get(key)
            if event_lock is None:
                event_lock = _ConversationEventLock()
                _CONVERSATION_EVENT_LOCKS[key] = event_lock
            return event_lock

    def _validate_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[str, str]:
        """Return validated session and conversation identifiers in order."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        return session, conversation

    @staticmethod
    def _reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build one JSON object or raise ``ValueError`` on a duplicate field."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    @staticmethod
    def _transcript_relative(
        session_id: str,
        conversation_id: str,
    ) -> Path:
        """Return the transcript path for previously validated identities."""

        return (
            Path("discussion_logs")
            / session_id
            / "interactive"
            / "conversations"
            / conversation_id
            / "transcript.jsonl"
        )

    @staticmethod
    def _logical_artifact(session_id: str, conversation_id: str) -> str:
        """Return the identity-relative transcript diagnostic name."""

        return f"{session_id}/{conversation_id}/transcript.jsonl"


class FilesystemCollaborationEventStore:
    """Persist atomic canonical schema-v2 collaboration-history batches."""

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str],
        *,
        catalog: ConversationCatalog | None = None,
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self.catalog = (
            catalog
            if catalog is not None
            else FilesystemConversationCatalog(self.workspace)
        )

    def append_batch(
        self,
        batch: CollaborationEventBatch,
    ) -> CollaborationAppendReceipt:
        """Atomically append one CAS batch or return its stable retry receipt."""

        if type(batch) is not CollaborationEventBatch:
            raise TypeError("batch must be a CollaborationEventBatch")
        first = batch.events[0]
        session, conversation = self._validate_identity(
            first.session_id,
            first.conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            with self._advisory_guard(session, conversation):
                try:
                    events, _ = self._read_history(session, conversation)
                except WorkspaceFormatError as exc:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.CORRUPT_HISTORY
                    ) from exc

                validation = validate_collaboration_candidate_batch(events, batch)
                if isinstance(validation, CollaborationAppendReceipt):
                    return validation

                digest = collaboration_batch_digest(batch)
                stored = tuple(
                    CollaborationEventV2(
                        schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
                        event_id=event.event_id,
                        session_id=event.session_id,
                        conversation_id=event.conversation_id,
                        turn_id=event.turn_id,
                        step_id=event.step_id,
                        sequence=len(events) + ordinal + 1,
                        timestamp=datetime.now(UTC).isoformat(),
                        batch_id=batch.batch_id,
                        batch_ordinal=ordinal,
                        batch_size=len(batch.events),
                        batch_digest=digest,
                        kind=event.kind,
                        sender=event.sender,
                        recipient=event.recipient,
                        payload=event.payload,
                    )
                    for ordinal, event in enumerate(batch.events)
                )
                completed = events + stored
                try:
                    project_collaboration_history(completed)
                except (
                    AssertionError,
                    KeyError,
                    SessionIdError,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise CollaborationAppendError(
                        CollaborationAppendFailure.INVALID_TRANSITION
                    ) from exc
                self._write_history(completed)
                return CollaborationAppendReceipt(
                    status=CollaborationAppendStatus.APPENDED,
                    batch_id=batch.batch_id,
                    batch_digest=digest,
                    first_sequence=stored[0].sequence,
                    last_sequence=stored[-1].sequence,
                    timestamps=tuple(event.timestamp for event in stored),
                )

    def read(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Read and strictly validate the complete canonical v2 history."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            if not self._history_exists(session, conversation):
                return ()
            with self._advisory_guard(session, conversation):
                events, _ = self._read_history(session, conversation)
                return events

    def read_context(
        self,
        session_id: str,
        conversation_id: str,
    ) -> CollaborationHistoryProjection:
        """Read and return the exact projection of the complete v2 history."""

        session, conversation = self._validate_identity(
            session_id,
            conversation_id,
        )
        event_lock = self._event_lock(session, conversation)
        with event_lock.lock:
            self._verify_catalog_identity(session, conversation)
            if not self._history_exists(session, conversation):
                return project_collaboration_history(())
            with self._advisory_guard(session, conversation):
                _, projection = self._read_history(session, conversation)
                return projection

    def _verify_catalog_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        """Require catalog metadata to match the requested history path."""

        metadata = self.catalog.get(session_id, conversation_id)
        try:
            matches_path = (
                metadata.session_id == session_id
                and metadata.conversation_id == conversation_id
            )
        except AttributeError as exc:
            raise WorkspaceFormatError(
                "conversation catalog returned invalid metadata: "
                f"{session_id}/{conversation_id}"
            ) from exc
        if not matches_path:
            raise WorkspaceFormatError(
                "conversation catalog identity does not match collaboration path: "
                f"{session_id}/{conversation_id}"
            )

    def _history_exists(self, session_id: str, conversation_id: str) -> bool:
        """Return whether a safe regular history exists without creating storage."""

        target = self.workspace.path(
            self._history_relative(session_id, conversation_id)
        )
        try:
            value = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(value.st_mode):
            raise WorkspaceSecurityError(
                "collaboration history path is a symbolic link"
            )
        if not stat.S_ISREG(value.st_mode):
            raise WorkspaceFormatError(
                "collaboration history is not a file: "
                f"{self._logical_artifact(session_id, conversation_id)}"
            )
        return True

    def _read_history(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[tuple[CollaborationEventV2, ...], CollaborationHistoryProjection]:
        """Read canonical bytes and validate their complete history projection."""

        payload = self._read_history_bytes(session_id, conversation_id)
        if payload is None:
            events: tuple[CollaborationEventV2, ...] = ()
        else:
            events = self._decode_history(
                payload,
                session_id=session_id,
                conversation_id=conversation_id,
            )
        try:
            projection = project_collaboration_history(events)
        except (
            AssertionError,
            KeyError,
            SessionIdError,
            TypeError,
            ValueError,
        ) as exc:
            raise WorkspaceFormatError(
                "collaboration history has invalid stored transitions: "
                f"{self._logical_artifact(session_id, conversation_id)}"
            ) from exc
        return events, projection

    def _read_history_bytes(
        self,
        session_id: str,
        conversation_id: str,
    ) -> bytes | None:
        """Read one safe regular history descriptor; operational failures propagate."""

        target = self.workspace.path(
            self._history_relative(session_id, conversation_id)
        )
        try:
            named = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(named.st_mode):
            raise WorkspaceSecurityError(
                "collaboration history path is a symbolic link"
            )
        if not stat.S_ISREG(named.st_mode):
            raise WorkspaceFormatError(
                "collaboration history is not a file: "
                f"{self._logical_artifact(session_id, conversation_id)}"
            )

        descriptor = os.open(target, self._regular_read_flags())
        active_error: BaseException | None = None
        try:
            self._verify_opened_regular(target, descriptor, label="history")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            self._close_descriptor(descriptor, active_error=active_error)

    def _decode_history(
        self,
        payload: bytes,
        *,
        session_id: str,
        conversation_id: str,
    ) -> tuple[CollaborationEventV2, ...]:
        """Decode exact canonical event-plus-LF records without accepting v1."""

        logical_artifact = self._logical_artifact(session_id, conversation_id)
        if not payload:
            raise WorkspaceFormatError(
                f"collaboration history is empty: {logical_artifact}"
            )
        if not payload.endswith(b"\n"):
            raise WorkspaceFormatError(
                f"collaboration history lacks a final newline: {logical_artifact}"
            )
        records = payload[:-1].split(b"\n")
        events: list[CollaborationEventV2] = []
        for line_number, record in enumerate(records, 1):
            if not record:
                raise WorkspaceFormatError(
                    "collaboration history contains a blank record: "
                    f"{logical_artifact} at line {line_number}"
                )
            try:
                event = decode_collaboration_event(record)
            except (
                AssertionError,
                KeyError,
                SessionIdError,
                TypeError,
                ValueError,
            ) as exc:
                raise WorkspaceFormatError(
                    "collaboration history contains an invalid canonical event: "
                    f"{logical_artifact} at line {line_number}"
                ) from exc
            if encode_collaboration_event(event) != record:
                raise WorkspaceFormatError(
                    "collaboration history contains a noncanonical event: "
                    f"{logical_artifact} at line {line_number}"
                )
            if (
                event.session_id != session_id
                or event.conversation_id != conversation_id
            ):
                raise WorkspaceFormatError(
                    "collaboration event does not match its owning path: "
                    f"{logical_artifact} at line {line_number}"
                )
            events.append(event)
        return tuple(events)

    def _write_history(self, events: tuple[CollaborationEventV2, ...]) -> None:
        """Durably replace one complete history with canonical JSON Lines."""

        if not events:
            raise ValueError("cannot write an empty collaboration history")
        relative = self._history_relative(
            events[0].session_id,
            events[0].conversation_id,
        )
        target = self.workspace.path(relative)
        parent = self.workspace.path(relative.parent)
        content = b"".join(
            encode_collaboration_event(event) + b"\n" for event in events
        )

        parent_descriptor = self._open_parent_directory(parent)
        temporary = parent / f".{target.name}.{uuid4().hex}.tmp"
        active_error: BaseException | None = None
        try:
            self._write_temporary(temporary, content)
            self.workspace.path(relative)
            self._validate_replacement_target(target)
            os.replace(temporary, target)
            os.fsync(parent_descriptor)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except BaseException as exc:
                if active_error is not None:
                    active_error.add_note(
                        "collaboration history temporary cleanup failed: "
                        f"{type(exc).__name__}"
                    )
                else:
                    cleanup_error = exc
            self._close_descriptor(
                parent_descriptor,
                active_error=active_error or cleanup_error,
            )
            if active_error is None and cleanup_error is not None:
                raise cleanup_error

    def _write_temporary(self, temporary: Path, content: bytes) -> None:
        """Completely write, flush, fsync, and close one exclusive temp file."""

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | self._safe_open_flags()
        descriptor = os.open(temporary, flags, 0o600)
        active_error: BaseException | None = None
        handle = None
        try:
            os.set_inheritable(descriptor, False)
            handle = os.fdopen(descriptor, "wb", closefd=False)
            remaining = memoryview(content)
            while remaining:
                written = handle.write(remaining)
                if not isinstance(written, int) or written <= 0:
                    raise OSError(errno.EIO, "short collaboration history write")
                remaining = remaining[written:]
            handle.flush()
            os.fsync(descriptor)
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            if handle is not None:
                try:
                    handle.close()
                except BaseException as exc:
                    if active_error is not None:
                        active_error.add_note(
                            "collaboration history temporary flush failed: "
                            f"{type(exc).__name__}"
                        )
                    else:
                        cleanup_error = exc
            self._close_descriptor(
                descriptor,
                active_error=active_error or cleanup_error,
            )
            if active_error is None and cleanup_error is not None:
                raise cleanup_error

    @contextmanager
    def _advisory_guard(
        self,
        session_id: str,
        conversation_id: str,
    ) -> Iterator[None]:
        """Hold one blocking POSIX advisory guard across a complete operation."""

        fcntl = self._require_advisory_locking()
        target = self.workspace.path(self._lock_relative(session_id, conversation_id))
        self._validate_guard_name(target)
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | self._safe_open_flags()
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(target, flags, 0o600)
        locked = False
        active_error: BaseException | None = None
        try:
            os.set_inheritable(descriptor, False)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            self._verify_opened_regular(target, descriptor, label="advisory guard")
            yield
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except BaseException as exc:
                    if active_error is not None:
                        active_error.add_note(
                            "collaboration advisory guard unlock failed: "
                            f"{type(exc).__name__}"
                        )
                    else:
                        cleanup_error = exc
            self._close_descriptor(
                descriptor,
                active_error=active_error or cleanup_error,
            )
            if active_error is None and cleanup_error is not None:
                raise cleanup_error

    @staticmethod
    def _require_advisory_locking() -> Any:
        """Return POSIX flock support or fail closed before guard creation."""

        if (
            _fcntl is None
            or not callable(getattr(_fcntl, "flock", None))
            or not hasattr(_fcntl, "LOCK_EX")
            or not hasattr(_fcntl, "LOCK_UN")
        ):
            raise OSError(
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                "POSIX collaboration-history advisory locking is unsupported",
            )
        return _fcntl

    @staticmethod
    def _safe_open_flags() -> int:
        """Return non-following close-on-exec flags or fail closed."""

        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if not no_follow:
            raise OSError(
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                "safe collaboration-history file access is unsupported",
            )
        return no_follow | getattr(os, "O_CLOEXEC", 0)

    @classmethod
    def _regular_read_flags(cls) -> int:
        """Return safe flags for opening an existing regular history."""

        return os.O_RDONLY | cls._safe_open_flags() | getattr(os, "O_NONBLOCK", 0)

    @classmethod
    def _open_parent_directory(cls, parent: Path) -> int:
        """Open and validate the same-directory durability target."""

        directory = getattr(os, "O_DIRECTORY", 0)
        if not directory:
            raise OSError(
                getattr(errno, "ENOTSUP", errno.ENOSYS),
                "collaboration-history directory durability is unsupported",
            )
        descriptor = os.open(parent, os.O_RDONLY | directory | cls._safe_open_flags())
        active_error: BaseException | None = None
        try:
            opened = os.fstat(descriptor)
            named = os.stat(parent, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
                raise WorkspaceSecurityError(
                    "collaboration history parent is not a directory"
                )
            if cls._file_identity(opened) != cls._file_identity(named):
                raise WorkspaceSecurityError(
                    "collaboration history parent identity changed"
                )
            return descriptor
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            if active_error is not None:
                cls._close_descriptor(descriptor, active_error=active_error)

    @staticmethod
    def _validate_guard_name(target: Path) -> None:
        """Reject a present non-regular or symbolic-link guard before open."""

        try:
            value = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
            raise WorkspaceSecurityError(
                "collaboration advisory guard is not a non-symlink regular file"
            )

    @classmethod
    def _verify_opened_regular(
        cls,
        target: Path,
        descriptor: int,
        *,
        label: str,
    ) -> None:
        """Require the descriptor and contained name to be one regular file."""

        opened = os.fstat(descriptor)
        named = os.stat(target, follow_symlinks=False)
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
        ):
            raise WorkspaceSecurityError(
                f"collaboration {label} is not a non-symlink regular file"
            )
        if cls._file_identity(opened) != cls._file_identity(named):
            raise WorkspaceSecurityError(f"collaboration {label} identity changed")

    @staticmethod
    def _validate_replacement_target(target: Path) -> None:
        """Allow an absent or regular target, rejecting unsafe replacement types."""

        try:
            value = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
            raise WorkspaceSecurityError(
                "collaboration history replacement target is unsafe"
            )

    @staticmethod
    def _file_identity(value: os.stat_result) -> tuple[int, int]:
        """Return one device/inode identity for a verified filesystem entry."""

        return value.st_dev, value.st_ino

    @staticmethod
    def _close_descriptor(
        descriptor: int,
        *,
        active_error: BaseException | None,
    ) -> None:
        """Close one descriptor without masking an active primary failure."""

        try:
            os.close(descriptor)
        except BaseException as exc:
            if active_error is None:
                raise
            active_error.add_note(
                "collaboration filesystem descriptor close failed: "
                f"{type(exc).__name__}"
            )

    def _event_lock(
        self,
        session_id: str,
        conversation_id: str,
    ) -> _CollaborationEventLock:
        """Return the separate process-wide lock for one v2 history identity."""

        key = (os.fspath(self.workspace.root), session_id, conversation_id)
        with _COLLABORATION_EVENT_LOCK_REGISTRY_GUARD:
            event_lock = _COLLABORATION_EVENT_LOCKS.get(key)
            if event_lock is None:
                event_lock = _CollaborationEventLock()
                _COLLABORATION_EVENT_LOCKS[key] = event_lock
            return event_lock

    def _validate_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[str, str]:
        """Return validated session and conversation identifiers in order."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        return session, conversation

    @staticmethod
    def _history_relative(session_id: str, conversation_id: str) -> Path:
        """Return the v2 history path for previously validated identities."""

        return (
            Path("discussion_logs")
            / session_id
            / "interactive"
            / "conversations"
            / conversation_id
            / "collaboration-history.jsonl"
        )

    @classmethod
    def _lock_relative(cls, session_id: str, conversation_id: str) -> Path:
        """Return the sibling advisory-guard path for a v2 history."""

        return cls._history_relative(session_id, conversation_id).with_name(
            ".collaboration-history.lock"
        )

    @staticmethod
    def _logical_artifact(session_id: str, conversation_id: str) -> str:
        """Return the identity-relative v2 history diagnostic name."""

        return f"{session_id}/{conversation_id}/collaboration-history.jsonl"


__all__ = [
    "FilesystemConversationCatalog",
    "FilesystemConversationEventStore",
    "FilesystemCollaborationEventStore",
]
