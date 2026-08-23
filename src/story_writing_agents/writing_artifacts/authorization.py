"""Content-free contracts and transient state for authorized artifact reads.

Filesystem authorization and durable evidence belong to the repository.  This
module deliberately owns only immutable identities, universal-newline byte
selection, and call-local capability/content lifetimes.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

from .catalog import ArtifactKind
from .references import (
    MAX_REFERENCE_TOTAL_BYTES,
    MAX_REFERENCE_UTF8_BYTES,
    ArtifactContentSnapshot,
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactSourceIdentity,
)


REFERENCE_EVIDENCE_SCHEMA_VERSION = 2
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_CAPABILITY_ID = re.compile(r"[0-9a-f]{32}")


class ReferenceAuthorizationErrorCode(StrEnum):
    """Stable content-free failure categories for explicit artifact reads."""

    INVALID_IDENTITY = "invalid_identity"
    NOT_FOUND = "not_found"
    INELIGIBLE = "ineligible"
    DRIFT = "drift"
    INVALID_UTF8 = "invalid_utf8"
    NUL_CONTENT = "nul_content"
    RANGE_OUT_OF_BOUNDS = "range_out_of_bounds"
    READ_LIMIT_EXCEEDED = "read_limit_exceeded"
    TOTAL_LIMIT_EXCEEDED = "total_limit_exceeded"
    CAPABILITY_CONSUMED = "capability_consumed"
    CORRELATION_MISMATCH = "correlation_mismatch"
    INVALID_TRANSITION = "invalid_transition"
    EVIDENCE_CORRUPT = "evidence_corrupt"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PROVIDER_FAILURE = "provider_failure"
    WORKFLOW_FAILURE = "workflow_failure"


_ERROR_MESSAGES = {
    ReferenceAuthorizationErrorCode.INVALID_IDENTITY:
        "reference authorization identity is invalid",
    ReferenceAuthorizationErrorCode.NOT_FOUND:
        "referenced artifact is unavailable",
    ReferenceAuthorizationErrorCode.INELIGIBLE:
        "referenced artifact is not eligible",
    ReferenceAuthorizationErrorCode.DRIFT:
        "referenced artifact changed during the operation",
    ReferenceAuthorizationErrorCode.INVALID_UTF8:
        "referenced artifact is not strict UTF-8",
    ReferenceAuthorizationErrorCode.NUL_CONTENT:
        "referenced artifact contains NUL",
    ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS:
        "reference range is outside the artifact",
    ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED:
        "one reference exceeds the read limit",
    ReferenceAuthorizationErrorCode.TOTAL_LIMIT_EXCEEDED:
        "reference batch exceeds the read limit",
    ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED:
        "reference read capability was already consumed",
    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH:
        "reference invocation correlation does not match",
    ReferenceAuthorizationErrorCode.INVALID_TRANSITION:
        "reference authorization transition is invalid",
    ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT:
        "reference authorization evidence is corrupt",
    ReferenceAuthorizationErrorCode.CLOSED:
        "reference authorization is closed",
    ReferenceAuthorizationErrorCode.CANCELLED:
        "reference operation was cancelled",
    ReferenceAuthorizationErrorCode.PROVIDER_FAILURE:
        "reference provider invocation failed",
    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE:
        "reference workflow failed",
}


class ReferenceAuthorizationError(RuntimeError):
    """Deterministic failure that never incorporates artifact content."""

    def __init__(self, code: ReferenceAuthorizationErrorCode) -> None:
        if type(code) is not ReferenceAuthorizationErrorCode:
            raise TypeError("code must be a ReferenceAuthorizationErrorCode")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


class ReferenceOperationFailure(RuntimeError):
    """Content-free public failure identity for one top-level operation."""

    def __init__(
        self,
        reference_operation_id: str,
        code: ReferenceAuthorizationErrorCode,
    ) -> None:
        validate_reference_identity(reference_operation_id)
        if type(code) is not ReferenceAuthorizationErrorCode:
            raise TypeError("code must be a ReferenceAuthorizationErrorCode")
        self.reference_operation_id = reference_operation_id
        self.code = code
        super().__init__(
            "reference operation "
            f"{reference_operation_id} failed with {code.value}"
        )


class ReferenceOperationState(StrEnum):
    REQUESTED = "requested"
    CAPTURED = "captured"
    REVALIDATED = "revalidated"
    ACCEPTED = "accepted"
    TERMINATED = "terminated"


class ReferenceInvocationState(StrEnum):
    AUTHORIZED = "authorized"
    DELIVERED = "delivered"
    RESULT_RECEIVED = "result_received"
    ACCEPTED = "accepted"
    TERMINATED = "terminated"


def validate_reference_identity(value: object, *, capability: bool = False) -> str:
    pattern = _CAPABILITY_ID if capability else _SAFE_IDENTITY
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ReferenceAuthorizationError(
            ReferenceAuthorizationErrorCode.INVALID_IDENTITY
        )
    return value


@dataclass(frozen=True, slots=True)
class AuthorizedArtifactReference:
    """Content-free proof for one ordered captured reference."""

    ordinal: int
    reference: ArtifactReference
    source: ArtifactSourceIdentity
    selected_sha256: str
    selected_utf8_bytes: int
    source_line_count: int

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("authorized reference ordinal must be positive")
        if type(self.reference) is not ArtifactReference:
            raise TypeError("reference must be an ArtifactReference")
        if type(self.source) is not ArtifactSourceIdentity:
            raise TypeError("source must be an ArtifactSourceIdentity")
        if self.reference.path != self.source.path:
            raise ValueError("authorized reference path does not match its source")
        if (
            type(self.selected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.selected_sha256) is None
        ):
            raise ValueError("selected_sha256 must be lowercase hexadecimal")
        if (
            type(self.selected_utf8_bytes) is not int
            or not 0 <= self.selected_utf8_bytes <= MAX_REFERENCE_UTF8_BYTES
        ):
            raise ValueError("selected byte count is outside the reference limit")
        if type(self.source_line_count) is not int or self.source_line_count < 0:
            raise ValueError("source line count must be nonnegative")
        if (
            self.reference.line_range is not None
            and self.reference.line_range.end > self.source_line_count
        ):
            raise ValueError("authorized range exceeds the source line count")


@dataclass(frozen=True, slots=True)
class ReferenceOperationManifest:
    """Immutable content-free request identity persisted before capture."""

    operation_id: str
    references: tuple[ArtifactReference, ...]
    schema_version: int = REFERENCE_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_reference_identity(self.operation_id)
        if (
            type(self.schema_version) is not int
            or self.schema_version != REFERENCE_EVIDENCE_SCHEMA_VERSION
        ):
            raise ValueError("reference evidence schema version is unsupported")
        if type(self.references) is not tuple or not self.references:
            raise ValueError("reference operation must contain references")
        if any(type(item) is not ArtifactReference for item in self.references):
            raise TypeError("operation references must contain ArtifactReference values")
        ArtifactReferenceBatch("", "", self.references)


@dataclass(frozen=True, slots=True)
class ReferenceInvocationCorrelation:
    """Caller-supplied provider-call identity for one exact read attempt."""

    operation_id: str
    invocation_id: str
    invocation_ordinal: int
    agent_id: str
    phase: str
    round_number: int | None
    attempt: int

    def __post_init__(self) -> None:
        for value in (
            self.operation_id,
            self.invocation_id,
            self.agent_id,
            self.phase,
        ):
            validate_reference_identity(value)
        if type(self.invocation_ordinal) is not int or self.invocation_ordinal < 1:
            raise ValueError("invocation_ordinal must be a positive integer")
        if self.round_number is not None and (
            type(self.round_number) is not int or self.round_number < 0
        ):
            raise ValueError("round_number must be None or a nonnegative integer")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")


@dataclass(frozen=True, slots=True)
class ReferenceEvidence:
    """One immutable, content-free lifecycle event."""

    sequence: int
    operation_id: str
    operation_state: ReferenceOperationState | None
    invocation_state: ReferenceInvocationState | None
    correlation: ReferenceInvocationCorrelation | None
    references: tuple[AuthorizedArtifactReference, ...]
    outcome: ReferenceAuthorizationErrorCode | None = None
    checkpoint: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("reference evidence sequence must be positive")
        validate_reference_identity(self.operation_id)
        operation_event = self.operation_state is not None
        invocation_event = self.invocation_state is not None
        if operation_event == invocation_event:
            raise ValueError("reference evidence must have exactly one state scope")
        if operation_event:
            if type(self.operation_state) is not ReferenceOperationState:
                raise TypeError("operation_state is invalid")
            if self.correlation is not None:
                raise ValueError("operation evidence cannot carry correlation")
        else:
            if type(self.invocation_state) is not ReferenceInvocationState:
                raise TypeError("invocation_state is invalid")
            if type(self.correlation) is not ReferenceInvocationCorrelation:
                raise TypeError("invocation evidence requires correlation")
            if self.correlation.operation_id != self.operation_id:
                raise ValueError("evidence operation identity does not correlate")
        if type(self.references) is not tuple or any(
            type(item) is not AuthorizedArtifactReference for item in self.references
        ):
            raise TypeError("evidence references are invalid")
        if self.outcome is not None and type(self.outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("evidence outcome is invalid")
        if self.checkpoint is not None:
            validate_reference_identity(self.checkpoint)
        terminal = self.operation_state is ReferenceOperationState.TERMINATED or (
            self.invocation_state is ReferenceInvocationState.TERMINATED
        )
        if terminal != (self.outcome is not None):
            raise ValueError("only terminal failure evidence carries an outcome")
        revalidated = self.operation_state is ReferenceOperationState.REVALIDATED
        if revalidated != (self.checkpoint is not None):
            raise ValueError("only revalidation evidence carries a checkpoint")


@dataclass(frozen=True, slots=True)
class ReferenceOperationHistory:
    """Validated durable evidence for one operation and its invocations."""

    manifest: ReferenceOperationManifest
    operation_events: tuple[ReferenceEvidence, ...]
    invocation_events: tuple[tuple[ReferenceEvidence, ...], ...]

    def __post_init__(self) -> None:
        if type(self.manifest) is not ReferenceOperationManifest:
            raise TypeError("manifest must be a ReferenceOperationManifest")
        if type(self.operation_events) is not tuple or type(self.invocation_events) is not tuple:
            raise TypeError("history event collections must be tuples")
        if not self.operation_events or any(
            type(event) is not ReferenceEvidence
            or event.operation_id != self.manifest.operation_id
            or event.operation_state is None
            for event in self.operation_events
        ):
            raise ValueError("operation history events do not correlate")
        for events in self.invocation_events:
            if type(events) is not tuple or not events or any(
                type(event) is not ReferenceEvidence
                or event.operation_id != self.manifest.operation_id
                or event.invocation_state is None
                for event in events
            ):
                raise ValueError("invocation history events do not correlate")
        ordinals = tuple(
            events[0].correlation.invocation_ordinal
            for events in self.invocation_events
            if events[0].correlation is not None
        )
        if ordinals != tuple(range(1, len(ordinals) + 1)):
            raise ValueError("invocation history ordinals must be contiguous")


def select_reference_bytes(
    content: bytes,
    line_range: ArtifactLineRange | None,
) -> tuple[bytes, int]:
    """Validate a full source and select bytes using universal newlines."""

    if type(content) is not bytes:
        raise TypeError("reference source must be bytes")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ReferenceAuthorizationError(
            ReferenceAuthorizationErrorCode.INVALID_UTF8
        ) from None
    if b"\x00" in content:
        raise ReferenceAuthorizationError(
            ReferenceAuthorizationErrorCode.NUL_CONTENT
        )
    if line_range is not None and type(line_range) is not ArtifactLineRange:
        raise TypeError("line_range must be an ArtifactLineRange or None")

    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(content):
        byte = content[index]
        if byte == 13:
            index += 2 if index + 1 < len(content) and content[index + 1] == 10 else 1
            spans.append((start, index))
            start = index
            continue
        if byte == 10:
            index += 1
            spans.append((start, index))
            start = index
            continue
        index += 1
    if start < len(content):
        spans.append((start, len(content)))

    if line_range is None:
        selected = content
    else:
        if line_range.end > len(spans):
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS
            )
        selected = content[
            spans[line_range.start - 1][0]:spans[line_range.end - 1][1]
        ]
    if len(selected) > MAX_REFERENCE_UTF8_BYTES:
        raise ReferenceAuthorizationError(
            ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED
        )
    return selected, len(spans)


@dataclass(frozen=True, slots=True)
class _CapturedReference:
    authorized: AuthorizedArtifactReference
    content: bytes


class ReferenceAuthorization:
    """Closeable operation snapshot; package-internal and content-bearing."""

    __slots__ = ("_captured", "_closed", "_lock", "_owner", "manifest")

    def __init__(
        self,
        manifest: ReferenceOperationManifest,
        captured: tuple[_CapturedReference, ...],
        *,
        owner: object,
    ) -> None:
        if type(manifest) is not ReferenceOperationManifest:
            raise TypeError("manifest must be a ReferenceOperationManifest")
        if type(captured) is not tuple or any(
            type(item) is not _CapturedReference for item in captured
        ):
            raise TypeError("captured references are invalid")
        if tuple(item.authorized.reference for item in captured) != manifest.references:
            raise ValueError("captured references do not match the manifest")
        if any(
            hashlib.sha256(item.content).hexdigest()
            != item.authorized.selected_sha256
            for item in captured
        ):
            raise ValueError("captured content does not match its proof")
        self.manifest = manifest
        self._captured = captured
        self._owner = owner
        self._closed = False
        self._lock = threading.Lock()

    @property
    def authorized_references(self) -> tuple[AuthorizedArtifactReference, ...]:
        with self._lock:
            self._require_open()
            return tuple(item.authorized for item in self._captured)

    def close(self) -> None:
        with self._lock:
            self._captured = ()
            self._closed = True

    def __enter__(self) -> ReferenceAuthorization:
        with self._lock:
            self._require_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __repr__(self) -> str:
        return "ReferenceAuthorization(<content hidden>)"

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        raise TypeError("ReferenceAuthorization cannot be serialized")

    def require_owner(self, owner: object) -> None:
        if owner is not self._owner:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        with self._lock:
            self._require_open()

    def _require_open(self) -> None:
        if self._closed:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CLOSED
            )

    def delivery_snapshot(
        self,
        owner: object,
    ) -> tuple[tuple[AuthorizedArtifactReference, ...], tuple[bytes, ...]]:
        """Return correlated proofs and bytes atomically or fail as closed."""

        with self._lock:
            if owner is not self._owner:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
                )
            self._require_open()
            return (
                tuple(item.authorized for item in self._captured),
                tuple(item.content for item in self._captured),
            )


class ReadCapability:
    """One-use package-internal authority for an exact invocation batch."""

    __slots__ = (
        "_authorization",
        "_consumed",
        "_lock",
        "_owner",
        "authorized_references",
        "capability_id",
        "correlation",
    )

    def __init__(
        self,
        authorization: ReferenceAuthorization,
        correlation: ReferenceInvocationCorrelation,
        *,
        capability_id: str,
        authorized_references: tuple[AuthorizedArtifactReference, ...],
        owner: object,
    ) -> None:
        if type(authorization) is not ReferenceAuthorization:
            raise TypeError("authorization must be a ReferenceAuthorization")
        if type(correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        if correlation.operation_id != authorization.manifest.operation_id:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        validate_reference_identity(capability_id, capability=True)
        if type(authorized_references) is not tuple or any(
            type(item) is not AuthorizedArtifactReference
            for item in authorized_references
        ):
            raise TypeError("authorized_references are invalid")
        if tuple(item.reference for item in authorized_references) != (
            authorization.manifest.references
        ):
            raise ValueError("capability proofs do not match authorization")
        self._authorization = authorization
        self.correlation = correlation
        self.capability_id = capability_id
        self.authorized_references = authorized_references
        self._owner = owner
        self._consumed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "ReadCapability(<content hidden>)"

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        raise TypeError("ReadCapability cannot be serialized")

    def consume(self, owner: object) -> ReferenceAuthorization:
        if owner is not self._owner:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        self._authorization.require_owner(owner)
        with self._lock:
            if self._consumed:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.CAPABILITY_CONSUMED
                )
            self._consumed = True
            return self._authorization

    def authorization_for(
        self,
        owner: object,
        *,
        require_consumed: bool,
    ) -> ReferenceAuthorization:
        if owner is not self._owner:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        self._authorization.require_owner(owner)
        with self._lock:
            if require_consumed and not self._consumed:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
        return self._authorization

    def retire(self, owner: object) -> ReferenceAuthorization:
        if owner is not self._owner:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        self._authorization.require_owner(owner)
        with self._lock:
            self._consumed = True
        return self._authorization


class ArtifactReferenceDelivery:
    """Identity-only, nonserializable transient carrier of selected bytes."""

    __slots__ = ("_contents", "authorized_references", "correlation")

    def __init__(
        self,
        correlation: ReferenceInvocationCorrelation,
        authorized_references: tuple[AuthorizedArtifactReference, ...],
        contents: tuple[bytes, ...],
    ) -> None:
        if type(correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        if type(authorized_references) is not tuple or type(contents) is not tuple:
            raise TypeError("delivery values must be tuples")
        if len(authorized_references) != len(contents) or any(
            type(reference) is not AuthorizedArtifactReference
            or type(content) is not bytes
            or hashlib.sha256(content).hexdigest() != reference.selected_sha256
            for reference, content in zip(
                authorized_references,
                contents,
                strict=True,
            )
        ):
            raise ValueError("delivery contents do not match authorization proofs")
        self.correlation = correlation
        self.authorized_references = authorized_references
        self._contents = contents

    @property
    def contents(self) -> tuple[bytes, ...]:
        if not self._contents and self.authorized_references:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CLOSED
            )
        return self._contents

    def close(self) -> None:
        self._contents = ()

    def __enter__(self) -> ArtifactReferenceDelivery:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __repr__(self) -> str:
        return "ArtifactReferenceDelivery(<content hidden>)"

    def __reduce_ex__(self, protocol: int) -> NoReturn:
        raise TypeError("ArtifactReferenceDelivery cannot be serialized")


def build_authorized_references(
    batch: ArtifactReferenceBatch,
    snapshots: tuple[ArtifactContentSnapshot, ...],
) -> tuple[tuple[AuthorizedArtifactReference, ...], tuple[_CapturedReference, ...]]:
    """Build ordered content-free proofs and transient selected snapshots."""

    if type(batch) is not ArtifactReferenceBatch:
        raise TypeError("batch must be an ArtifactReferenceBatch")
    if (
        type(snapshots) is not tuple
        or len(batch.references) != len(snapshots)
        or any(type(item) is not ArtifactContentSnapshot for item in snapshots)
    ):
        raise ValueError("authorized reference inputs must correlate")
    proofs: list[AuthorizedArtifactReference] = []
    captured: list[_CapturedReference] = []
    total = 0
    for ordinal, (reference, snapshot) in enumerate(
        zip(batch.references, snapshots, strict=True),
        start=1,
    ):
        if reference.path != snapshot.path:
            raise ValueError("authorized reference inputs must correlate")
        selected, line_count = select_reference_bytes(
            snapshot.content,
            reference.line_range,
        )
        total += len(selected)
        if total > MAX_REFERENCE_TOTAL_BYTES:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.TOTAL_LIMIT_EXCEEDED
            )
        proof = AuthorizedArtifactReference(
            ordinal=ordinal,
            reference=reference,
            source=snapshot.source,
            selected_sha256=hashlib.sha256(selected).hexdigest(),
            selected_utf8_bytes=len(selected),
            source_line_count=line_count,
        )
        proofs.append(proof)
        captured.append(_CapturedReference(proof, selected))
    return tuple(proofs), tuple(captured)


def reference_to_evidence_record(reference: ArtifactReference) -> dict[str, object]:
    line_range = reference.line_range
    return {
        "line_end": None if line_range is None else line_range.end,
        "line_start": None if line_range is None else line_range.start,
        "path": reference.path,
    }


def reference_from_evidence_record(value: object) -> ArtifactReference:
    if type(value) is not dict or set(value) != {"line_end", "line_start", "path"}:
        raise ValueError("reference fields are invalid")
    start = value["line_start"]
    end = value["line_end"]
    if (start is None) != (end is None):
        raise ValueError("reference range fields are invalid")
    line_range = None if start is None else ArtifactLineRange(start, end)
    return ArtifactReference(value["path"], line_range)


def authorized_reference_to_evidence_record(
    value: AuthorizedArtifactReference,
) -> dict[str, object]:
    return {
        "ordinal": value.ordinal,
        "reference": reference_to_evidence_record(value.reference),
        "selected_sha256": value.selected_sha256,
        "selected_utf8_bytes": value.selected_utf8_bytes,
        "source": {
            "changed_ns": value.source.changed_ns,
            "device_id": value.source.device_id,
            "inode": value.source.inode,
            "kind": value.source.kind.value,
            "modified_ns": value.source.modified_ns,
            "path": value.source.path,
            "source_sha256": value.source.source_sha256,
            "source_utf8_bytes": value.source.source_utf8_bytes,
        },
        "source_line_count": value.source_line_count,
    }


def authorized_reference_from_evidence_record(
    value: object,
) -> AuthorizedArtifactReference:
    expected = {
        "ordinal",
        "reference",
        "selected_sha256",
        "selected_utf8_bytes",
        "source",
        "source_line_count",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("authorized reference fields are invalid")
    raw_source = value["source"]
    source_fields = {
        "changed_ns",
        "device_id",
        "inode",
        "kind",
        "modified_ns",
        "path",
        "source_sha256",
        "source_utf8_bytes",
    }
    if type(raw_source) is not dict or set(raw_source) != source_fields:
        raise ValueError("artifact source fields are invalid")
    return AuthorizedArtifactReference(
        ordinal=value["ordinal"],
        reference=reference_from_evidence_record(value["reference"]),
        source=ArtifactSourceIdentity(
            path=raw_source["path"],
            kind=ArtifactKind(raw_source["kind"]),
            source_sha256=raw_source["source_sha256"],
            source_utf8_bytes=raw_source["source_utf8_bytes"],
            device_id=raw_source["device_id"],
            inode=raw_source["inode"],
            modified_ns=raw_source["modified_ns"],
            changed_ns=raw_source["changed_ns"],
        ),
        selected_sha256=value["selected_sha256"],
        selected_utf8_bytes=value["selected_utf8_bytes"],
        source_line_count=value["source_line_count"],
    )


def operation_manifest_to_evidence_record(
    value: ReferenceOperationManifest,
) -> dict[str, object]:
    return {
        "operation_id": value.operation_id,
        "references": [
            reference_to_evidence_record(item) for item in value.references
        ],
        "schema_version": value.schema_version,
    }


def operation_manifest_from_evidence_record(
    value: object,
) -> ReferenceOperationManifest:
    if type(value) is not dict or set(value) != {
        "operation_id",
        "references",
        "schema_version",
    }:
        raise ValueError("operation manifest fields are invalid")
    raw_references = value["references"]
    if type(raw_references) is not list:
        raise ValueError("operation references must be an array")
    return ReferenceOperationManifest(
        operation_id=value["operation_id"],
        references=tuple(
            reference_from_evidence_record(item) for item in raw_references
        ),
        schema_version=value["schema_version"],
    )


def invocation_correlation_to_evidence_record(
    value: ReferenceInvocationCorrelation,
) -> dict[str, object]:
    return {
        "agent_id": value.agent_id,
        "attempt": value.attempt,
        "invocation_id": value.invocation_id,
        "invocation_ordinal": value.invocation_ordinal,
        "operation_id": value.operation_id,
        "phase": value.phase,
        "round_number": value.round_number,
    }


def invocation_correlation_from_evidence_record(
    value: object,
) -> ReferenceInvocationCorrelation:
    expected = {
        "agent_id",
        "attempt",
        "invocation_id",
        "invocation_ordinal",
        "operation_id",
        "phase",
        "round_number",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invocation correlation fields are invalid")
    return ReferenceInvocationCorrelation(
        operation_id=value["operation_id"],
        invocation_id=value["invocation_id"],
        invocation_ordinal=value["invocation_ordinal"],
        agent_id=value["agent_id"],
        phase=value["phase"],
        round_number=value["round_number"],
        attempt=value["attempt"],
    )


def evidence_to_record(value: ReferenceEvidence) -> dict[str, object]:
    return {
        "checkpoint": value.checkpoint,
        "correlation": (
            None
            if value.correlation is None
            else invocation_correlation_to_evidence_record(value.correlation)
        ),
        "invocation_state": (
            None if value.invocation_state is None else value.invocation_state.value
        ),
        "operation_id": value.operation_id,
        "operation_state": (
            None if value.operation_state is None else value.operation_state.value
        ),
        "outcome": None if value.outcome is None else value.outcome.value,
        "references": [
            authorized_reference_to_evidence_record(item)
            for item in value.references
        ],
        "sequence": value.sequence,
    }


def evidence_from_record(value: object) -> ReferenceEvidence:
    expected = {
        "checkpoint",
        "correlation",
        "invocation_state",
        "operation_id",
        "operation_state",
        "outcome",
        "references",
        "sequence",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("reference evidence fields are invalid")
    raw_references = value["references"]
    if type(raw_references) is not list:
        raise ValueError("evidence references must be an array")
    operation_state = value["operation_state"]
    invocation_state = value["invocation_state"]
    correlation = value["correlation"]
    outcome = value["outcome"]
    return ReferenceEvidence(
        sequence=value["sequence"],
        operation_id=value["operation_id"],
        operation_state=(
            None if operation_state is None else ReferenceOperationState(operation_state)
        ),
        invocation_state=(
            None if invocation_state is None else ReferenceInvocationState(invocation_state)
        ),
        correlation=(
            None
            if correlation is None
            else invocation_correlation_from_evidence_record(correlation)
        ),
        references=tuple(
            authorized_reference_from_evidence_record(item)
            for item in raw_references
        ),
        outcome=(
            None if outcome is None else ReferenceAuthorizationErrorCode(outcome)
        ),
        checkpoint=value["checkpoint"],
    )
