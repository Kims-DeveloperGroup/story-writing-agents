"""Filesystem conversation catalog and event-store persistence."""

from __future__ import annotations

import json
import os
import threading
import weakref
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    select_bounded_conversation_context,
)
from ..errors import (
    ConversationExistsError,
    ConversationNotFoundError,
    ManuscriptExistsError,
    SessionIdError,
    WorkspaceFormatError,
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

__all__ = [
    "FilesystemConversationCatalog",
    "FilesystemConversationEventStore",
]
