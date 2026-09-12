"""Private durable evidence adapter for explicit artifact-read operations."""

from __future__ import annotations

import json
from typing import Callable, TypeVar

from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from .authorization import (
    REFERENCE_EVIDENCE_SCHEMA_VERSION,
    AuthorizedArtifactReference,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceEvidence,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationHistory,
    ReferenceOperationManifest,
    ReferenceOperationState,
    authorized_reference_from_evidence_record,
    authorized_reference_to_evidence_record,
    evidence_from_record,
    evidence_to_record,
    invocation_correlation_from_evidence_record,
    invocation_correlation_to_evidence_record,
    operation_manifest_from_evidence_record,
    operation_manifest_to_evidence_record,
    validate_reference_identity,
)


_T = TypeVar("_T")
_FORBIDDEN_KEYS = frozenset(
    {
        "artifact_bytes",
        "bytes",
        "content",
        "prompt",
        "provider_text",
        "source_text",
    }
)


def _corrupt() -> ReferenceAuthorizationError:
    return ReferenceAuthorizationError(
        ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _corrupt() from None


def _contains_forbidden_key(value: object) -> bool:
    if type(value) is dict:
        return any(
            key in _FORBIDDEN_KEYS or _contains_forbidden_key(item)
            for key, item in value.items()
        )
    if type(value) is list:
        return any(_contains_forbidden_key(item) for item in value)
    return False


class ReferenceEvidenceStore:
    """Create and validate content-free evidence while a repository lease lives."""

    def __init__(
        self,
        storage: SecureWorkspaceStorage,
        snapshots_directory: str,
        staging_directory: str,
    ) -> None:
        if type(storage) is not SecureWorkspaceStorage:
            raise TypeError("storage must be SecureWorkspaceStorage")
        self._storage = storage
        self._snapshots_directory = snapshots_directory
        self._staging_directory = staging_directory

    def begin_operation(
        self,
        manifest: ReferenceOperationManifest,
    ) -> ReferenceEvidence:
        operation_root = self._operation_root(manifest.operation_id)
        try:
            if manifest.operation_id in self._storage.list_names(
                self._snapshots_directory
            ):
                raise _corrupt()
            self._storage.ensure_directory(operation_root)
            self._storage.ensure_directory(f"{operation_root}/operation_events")
            self._storage.ensure_directory(f"{operation_root}/invocations")
            self._write_new(
                f"{operation_root}/manifest.json",
                operation_manifest_to_evidence_record(manifest),
            )
            event = ReferenceEvidence(
                sequence=1,
                operation_id=manifest.operation_id,
                operation_state=ReferenceOperationState.REQUESTED,
                invocation_state=None,
                correlation=None,
                references=(),
            )
            self._write_new(
                f"{operation_root}/operation_events/00000001.json",
                evidence_to_record(event),
            )
            return event
        except ReferenceAuthorizationError:
            raise
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None

    def append_operation_event(
        self,
        operation_id: str,
        state: ReferenceOperationState,
        references: tuple[AuthorizedArtifactReference, ...],
        *,
        outcome: ReferenceAuthorizationErrorCode | None = None,
        checkpoint: str | None = None,
    ) -> ReferenceEvidence:
        manifest = self._read_manifest(operation_id)
        events = self._read_operation_events(operation_id)
        self._validate_operation_events(manifest, events)
        self._require_operation_transition(
            events[-1].operation_state,
            state,
        )
        if state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            history = self.inspect(operation_id)
            if history is None:
                raise _corrupt()
        event = ReferenceEvidence(
            sequence=len(events) + 1,
            operation_id=operation_id,
            operation_state=state,
            invocation_state=None,
            correlation=None,
            references=references,
            outcome=outcome,
            checkpoint=checkpoint,
        )
        self._validate_operation_events(manifest, events + (event,))
        if state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            self._validate_terminal_invocations(
                state,
                history.invocation_events,
                corruption=False,
            )
        self._write_new(
            f"{self._operation_root(operation_id)}/operation_events/"
            f"{event.sequence:08d}.json",
            evidence_to_record(event),
        )
        return event

    def begin_invocation(
        self,
        correlation: ReferenceInvocationCorrelation,
        capability_id: str,
        references: tuple[AuthorizedArtifactReference, ...],
    ) -> ReferenceEvidence:
        history = self.inspect(correlation.operation_id)
        if history is None:
            raise _corrupt()
        manifest = history.manifest
        operation_events = history.operation_events
        if operation_events[-1].operation_state not in {
            ReferenceOperationState.CAPTURED,
            ReferenceOperationState.REVALIDATED,
        }:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
        self._validate_authorized(manifest, references)
        validate_reference_identity(capability_id, capability=True)
        if correlation.invocation_ordinal != len(history.invocation_events) + 1:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
        invocation_names = self._storage.list_names(
            f"{self._operation_root(correlation.operation_id)}/invocations"
        )
        if any(
            self._read_invocation_manifest(
                correlation.operation_id,
                invocation_name,
            )[1] == capability_id
            for invocation_name in invocation_names
        ):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        invocation_root = self._invocation_root(correlation)
        invocation_manifest = {
            "capability_id": capability_id,
            "correlation": invocation_correlation_to_evidence_record(correlation),
            "references": [
                authorized_reference_to_evidence_record(item)
                for item in references
            ],
            "schema_version": REFERENCE_EVIDENCE_SCHEMA_VERSION,
        }
        try:
            if correlation.invocation_id in invocation_names:
                raise _corrupt()
            self._storage.ensure_directory(invocation_root)
            self._storage.ensure_directory(f"{invocation_root}/events")
            self._write_new(f"{invocation_root}/manifest.json", invocation_manifest)
            event = ReferenceEvidence(
                sequence=1,
                operation_id=correlation.operation_id,
                operation_state=None,
                invocation_state=ReferenceInvocationState.AUTHORIZED,
                correlation=correlation,
                references=references,
            )
            self._write_new(
                f"{invocation_root}/events/00000001.json",
                evidence_to_record(event),
            )
            return event
        except ReferenceAuthorizationError:
            raise
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None

    def append_invocation_event(
        self,
        correlation: ReferenceInvocationCorrelation,
        capability_id: str,
        state: ReferenceInvocationState,
        references: tuple[AuthorizedArtifactReference, ...],
        *,
        outcome: ReferenceAuthorizationErrorCode | None = None,
    ) -> ReferenceEvidence:
        history = self.inspect(correlation.operation_id)
        if history is None:
            raise _corrupt()
        operation_events = history.operation_events
        if operation_events[-1].operation_state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
        (
            stored_correlation,
            stored_capability_id,
            stored_references,
        ) = self._read_invocation_manifest(
            correlation.operation_id,
            correlation.invocation_id,
        )
        if (
            stored_correlation != correlation
            or stored_capability_id != capability_id
            or stored_references != references
        ):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        events = self._read_invocation_events(stored_correlation, stored_references)
        self._validate_invocation_events(events)
        self._require_invocation_transition(
            events[-1].invocation_state,
            state,
        )
        event = ReferenceEvidence(
            sequence=len(events) + 1,
            operation_id=correlation.operation_id,
            operation_state=None,
            invocation_state=state,
            correlation=correlation,
            references=references,
            outcome=outcome,
        )
        self._validate_invocation_events(events + (event,))
        self._write_new(
            f"{self._invocation_root(correlation)}/events/{event.sequence:08d}.json",
            evidence_to_record(event),
        )
        return event

    def inspect(self, operation_id: str) -> ReferenceOperationHistory | None:
        try:
            validate_reference_identity(operation_id)
            if operation_id not in self._storage.list_names(
                self._snapshots_directory
            ):
                return None
            manifest = self._read_manifest(operation_id)
            operation_events = self._read_operation_events(operation_id)
            self._validate_operation_events(manifest, operation_events)
            invocation_root = f"{self._operation_root(operation_id)}/invocations"
            names = self._storage.list_names(invocation_root)
            invocation_records: list[
                tuple[
                    ReferenceInvocationCorrelation,
                    str,
                    tuple[ReferenceEvidence, ...],
                ]
            ] = []
            capability_ids: set[str] = set()
            captured = next(
                (
                    event.references
                    for event in operation_events
                    if event.operation_state is ReferenceOperationState.CAPTURED
                ),
                None,
            )
            for invocation_id in names:
                try:
                    (
                        stored_correlation,
                        stored_capability_id,
                        stored_references,
                    ) = (
                        self._read_invocation_manifest(
                            operation_id,
                            invocation_id,
                        )
                    )
                except ReferenceAuthorizationError:
                    raise _corrupt() from None
                events = self._read_invocation_events(
                    stored_correlation,
                    stored_references,
                )
                self._validate_invocation_events(events)
                if captured is None or stored_references != captured:
                    raise _corrupt()
                if stored_capability_id in capability_ids:
                    raise _corrupt()
                capability_ids.add(stored_capability_id)
                invocation_records.append(
                    (stored_correlation, stored_capability_id, events)
                )
            invocation_records.sort(
                key=lambda item: item[0].invocation_ordinal
            )
            if tuple(
                item[0].invocation_ordinal for item in invocation_records
            ) != tuple(range(1, len(invocation_records) + 1)):
                raise _corrupt()
            self._validate_terminal_invocations(
                operation_events[-1].operation_state,
                tuple(item[2] for item in invocation_records),
                corruption=True,
            )
            return ReferenceOperationHistory(
                manifest=manifest,
                operation_events=operation_events,
                invocation_events=tuple(item[2] for item in invocation_records),
            )
        except ReferenceAuthorizationError:
            raise
        except (ArtifactEntrySecurityError, OSError, TypeError, ValueError):
            raise _corrupt() from None

    def inventory(self) -> tuple[ReferenceOperationHistory, ...]:
        """Return every validated reference history in stable identity order."""

        try:
            operation_ids = self._storage.list_names(self._snapshots_directory)
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None
        histories: list[ReferenceOperationHistory] = []
        for operation_id in operation_ids:
            try:
                validate_reference_identity(operation_id)
            except ReferenceAuthorizationError:
                raise _corrupt() from None
            history = self.inspect(operation_id)
            if history is None:
                raise _corrupt()
            histories.append(history)
        return tuple(histories)

    def reconcile_incomplete(
        self,
        operation_id: str,
        *,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> bool:
        """Idempotently advance one valid incomplete history to termination."""

        if type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode")
        history = self.inspect(operation_id)
        if history is None:
            raise _corrupt()
        if history.operation_events[-1].operation_state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            return False

        terminal_invocations = {
            ReferenceInvocationState.ACCEPTED,
            ReferenceInvocationState.TERMINATED,
        }
        for invocation_events in history.invocation_events:
            current = invocation_events[-1]
            if current.invocation_state in terminal_invocations:
                continue
            correlation = current.correlation
            if correlation is None:
                raise _corrupt()
            (
                stored_correlation,
                capability_id,
                references,
            ) = self._read_invocation_manifest(
                operation_id,
                correlation.invocation_id,
            )
            if stored_correlation != correlation:
                raise _corrupt()
            self.append_invocation_event(
                correlation,
                capability_id,
                ReferenceInvocationState.TERMINATED,
                references,
                outcome=outcome,
            )

        history = self.inspect(operation_id)
        if history is None:
            raise _corrupt()
        operation_state = history.operation_events[-1].operation_state
        if operation_state in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            return True
        captured = next(
            (
                event.references
                for event in history.operation_events
                if event.operation_state is ReferenceOperationState.CAPTURED
            ),
            (),
        )
        self.append_operation_event(
            operation_id,
            ReferenceOperationState.TERMINATED,
            captured,
            outcome=outcome,
        )
        return True

    def _operation_root(self, operation_id: str) -> str:
        # The authorization contract is the sole grammar authority.
        validate_reference_identity(operation_id)
        return f"{self._snapshots_directory}/{operation_id}"

    def _invocation_root(self, correlation: ReferenceInvocationCorrelation) -> str:
        return (
            f"{self._operation_root(correlation.operation_id)}/invocations/"
            f"{correlation.invocation_id}"
        )

    def _read_manifest(self, operation_id: str) -> ReferenceOperationManifest:
        path = f"{self._operation_root(operation_id)}/manifest.json"
        manifest = self._read(path, operation_manifest_from_evidence_record)
        if manifest.operation_id != operation_id:
            raise _corrupt()
        return manifest

    def _read_operation_events(
        self,
        operation_id: str,
    ) -> tuple[ReferenceEvidence, ...]:
        directory = f"{self._operation_root(operation_id)}/operation_events"
        return self._read_events(directory)

    def _read_invocation_manifest(
        self,
        operation_id: str,
        invocation_id: str,
    ) -> tuple[
        ReferenceInvocationCorrelation,
        str,
        tuple[AuthorizedArtifactReference, ...],
    ]:
        # Validate both identities through their stable schema before paths.
        dummy = ReferenceInvocationCorrelation(
            operation_id=operation_id,
            invocation_id=invocation_id,
            invocation_ordinal=1,
            agent_id="validation",
            phase="validation",
            round_number=None,
            attempt=1,
        )
        path = f"{self._invocation_root(dummy)}/manifest.json"
        raw = self._read_object(path)
        if type(raw) is not dict or set(raw) != {
            "capability_id",
            "correlation",
            "references",
            "schema_version",
        }:
            raise _corrupt()
        if (
            type(raw["schema_version"]) is not int
            or raw["schema_version"] != REFERENCE_EVIDENCE_SCHEMA_VERSION
        ):
            raise _corrupt()
        raw_references = raw["references"]
        if type(raw_references) is not list:
            raise _corrupt()
        try:
            capability_id = validate_reference_identity(
                raw["capability_id"],
                capability=True,
            )
            correlation = invocation_correlation_from_evidence_record(
                raw["correlation"]
            )
            references = tuple(
                authorized_reference_from_evidence_record(item)
                for item in raw_references
            )
        except (TypeError, ValueError, ReferenceAuthorizationError):
            raise _corrupt() from None
        if (
            correlation.operation_id != operation_id
            or correlation.invocation_id != invocation_id
        ):
            raise _corrupt()
        try:
            stored = self._storage.read_bytes(path)
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None
        if _json_bytes(raw) != stored:
            raise _corrupt()
        return correlation, capability_id, references

    def _read_invocation_events(
        self,
        correlation: ReferenceInvocationCorrelation,
        references: tuple[AuthorizedArtifactReference, ...],
    ) -> tuple[ReferenceEvidence, ...]:
        events = self._read_events(f"{self._invocation_root(correlation)}/events")
        if any(
            event.correlation != correlation or event.references != references
            for event in events
        ):
            raise _corrupt()
        return events

    def _read_events(self, directory: str) -> tuple[ReferenceEvidence, ...]:
        try:
            names = self._storage.list_names(directory)
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None
        expected = tuple(f"{index:08d}.json" for index in range(1, len(names) + 1))
        if names != expected or not names:
            raise _corrupt()
        events = tuple(
            self._read(f"{directory}/{name}", evidence_from_record)
            for name in names
        )
        if tuple(event.sequence for event in events) != tuple(range(1, len(events) + 1)):
            raise _corrupt()
        return events

    def _read(self, path: str, decoder: Callable[[object], _T]) -> _T:
        raw_value = self._read_object(path)
        try:
            value = decoder(raw_value)
        except (TypeError, ValueError, ReferenceAuthorizationError):
            raise _corrupt() from None
        try:
            stored = self._storage.read_bytes(path)
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None
        if _json_bytes(raw_value) != stored:
            raise _corrupt()
        return value

    def _read_object(self, path: str) -> object:
        try:
            raw = self._storage.read_bytes(path)
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
        except (
            ArtifactEntrySecurityError,
            OSError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            raise _corrupt() from None
        if _contains_forbidden_key(value):
            raise _corrupt()
        return value

    def _write_new(self, path: str, value: object) -> None:
        if _contains_forbidden_key(value):
            raise _corrupt()
        try:
            self._storage.publish_bytes(
                path,
                _json_bytes(value),
                expected_sha256=None,
                expected_exists=False,
                create_only=True,
                staging_directory=self._staging_directory,
            )
        except ReferenceAuthorizationError:
            raise
        except (ArtifactEntrySecurityError, OSError):
            raise _corrupt() from None

    @staticmethod
    def _validate_authorized(
        manifest: ReferenceOperationManifest,
        references: tuple[AuthorizedArtifactReference, ...],
    ) -> None:
        if len(references) != len(manifest.references) or any(
            item.ordinal != ordinal or item.reference != manifest.references[ordinal - 1]
            for ordinal, item in enumerate(references, start=1)
        ):
            raise _corrupt()

    @classmethod
    def _validate_operation_events(
        cls,
        manifest: ReferenceOperationManifest,
        events: tuple[ReferenceEvidence, ...],
    ) -> None:
        if not events or events[0].operation_state is not ReferenceOperationState.REQUESTED:
            raise _corrupt()
        captured: tuple[AuthorizedArtifactReference, ...] | None = None
        terminal = False
        for index, event in enumerate(events):
            if event.operation_id != manifest.operation_id or event.sequence != index + 1:
                raise _corrupt()
            state = event.operation_state
            if terminal or state is ReferenceOperationState.REQUESTED and index != 0:
                raise _corrupt()
            if state is ReferenceOperationState.CAPTURED:
                if index != 1 or captured is not None:
                    raise _corrupt()
                cls._validate_authorized(manifest, event.references)
                captured = event.references
            elif state is ReferenceOperationState.REVALIDATED:
                if captured is None or event.references != captured or event.checkpoint is None:
                    raise _corrupt()
            elif state is ReferenceOperationState.ACCEPTED:
                if captured is None or event.references != captured or event.outcome is not None:
                    raise _corrupt()
                terminal = True
            elif state is ReferenceOperationState.TERMINATED:
                expected_references = () if captured is None else captured
                if (
                    event.outcome is None
                    or event.references != expected_references
                ):
                    raise _corrupt()
                terminal = True
            elif state is ReferenceOperationState.REQUESTED:
                if event.references or event.outcome is not None:
                    raise _corrupt()
            else:
                raise _corrupt()

    @staticmethod
    def _validate_terminal_invocations(
        operation_state: ReferenceOperationState | None,
        invocation_events: tuple[tuple[ReferenceEvidence, ...], ...],
        *,
        corruption: bool,
    ) -> None:
        if operation_state not in {
            ReferenceOperationState.ACCEPTED,
            ReferenceOperationState.TERMINATED,
        }:
            return
        terminal_states = {
            ReferenceInvocationState.ACCEPTED,
            ReferenceInvocationState.TERMINATED,
        }
        invalid = any(
            invocation[-1].invocation_state not in terminal_states
            for invocation in invocation_events
        ) or (
            operation_state is ReferenceOperationState.ACCEPTED
            and (
                not invocation_events
                or not any(
                    invocation[-1].invocation_state
                    is ReferenceInvocationState.ACCEPTED
                    for invocation in invocation_events
                )
            )
        )
        if invalid:
            if corruption:
                raise _corrupt()
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )

    @staticmethod
    def _validate_invocation_events(events: tuple[ReferenceEvidence, ...]) -> None:
        if not events or events[0].invocation_state is not ReferenceInvocationState.AUTHORIZED:
            raise _corrupt()
        allowed = {
            ReferenceInvocationState.AUTHORIZED: {
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.TERMINATED,
            },
            ReferenceInvocationState.DELIVERED: {
                ReferenceInvocationState.RESULT_RECEIVED,
                ReferenceInvocationState.TERMINATED,
            },
            ReferenceInvocationState.RESULT_RECEIVED: {
                ReferenceInvocationState.ACCEPTED,
                ReferenceInvocationState.TERMINATED,
            },
        }
        previous = events[0].invocation_state
        if events[0].outcome is not None:
            raise _corrupt()
        for event in events[1:]:
            state = event.invocation_state
            if state not in allowed.get(previous, set()):
                raise _corrupt()
            if state is ReferenceInvocationState.TERMINATED:
                if event.outcome is None:
                    raise _corrupt()
            elif event.outcome is not None:
                raise _corrupt()
            previous = state

    @staticmethod
    def _require_operation_transition(
        current: ReferenceOperationState | None,
        requested: ReferenceOperationState,
    ) -> None:
        allowed = {
            ReferenceOperationState.REQUESTED: {
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.TERMINATED,
            },
            ReferenceOperationState.CAPTURED: {
                ReferenceOperationState.REVALIDATED,
                ReferenceOperationState.ACCEPTED,
                ReferenceOperationState.TERMINATED,
            },
            ReferenceOperationState.REVALIDATED: {
                ReferenceOperationState.REVALIDATED,
                ReferenceOperationState.ACCEPTED,
                ReferenceOperationState.TERMINATED,
            },
        }
        if requested not in allowed.get(current, set()):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )

    @staticmethod
    def _require_invocation_transition(
        current: ReferenceInvocationState | None,
        requested: ReferenceInvocationState,
    ) -> None:
        allowed = {
            ReferenceInvocationState.AUTHORIZED: {
                ReferenceInvocationState.DELIVERED,
                ReferenceInvocationState.TERMINATED,
            },
            ReferenceInvocationState.DELIVERED: {
                ReferenceInvocationState.RESULT_RECEIVED,
                ReferenceInvocationState.TERMINATED,
            },
            ReferenceInvocationState.RESULT_RECEIVED: {
                ReferenceInvocationState.ACCEPTED,
                ReferenceInvocationState.TERMINATED,
            },
        }
        if requested not in allowed.get(current, set()):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.INVALID_TRANSITION
            )
