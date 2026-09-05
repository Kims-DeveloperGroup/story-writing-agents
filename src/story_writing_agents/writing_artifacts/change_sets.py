"""Pure exact change-set contracts for canonical writing artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from .catalog import ArtifactKind, canonical_artifact_kind


ARTIFACT_CHANGE_SET_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ArtifactChangeSetErrorCode(StrEnum):
    """Stable content-free failure categories for pure artifact changes."""

    INVALID_SCHEMA = "invalid_schema"
    INVALID_SERIALIZATION = "invalid_serialization"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_PATH = "invalid_path"
    INVALID_KIND = "invalid_kind"
    INVALID_DIGEST = "invalid_digest"
    INVALID_HUNK = "invalid_hunk"
    INVALID_BYTE_BOUNDARY = "invalid_byte_boundary"
    NO_OP = "no_op"
    HUNK_ORDER = "hunk_order"
    HUNK_OVERLAP = "hunk_overlap"
    DUPLICATE_INSERTION = "duplicate_insertion"
    OPERATION_ORDER = "operation_order"
    BASE_STATE_MISMATCH = "base_state_mismatch"
    BASE_ARTIFACT_MISMATCH = "base_artifact_mismatch"
    PROJECTED_DIGEST_MISMATCH = "projected_digest_mismatch"
    PATH_COLLISION = "path_collision"
    RENAME_CONFLICT = "rename_conflict"
    CHANGE_SET_DIGEST_MISMATCH = "change_set_digest_mismatch"


_ERROR_MESSAGES = {
    ArtifactChangeSetErrorCode.INVALID_SCHEMA: "artifact change-set schema is invalid",
    ArtifactChangeSetErrorCode.INVALID_SERIALIZATION: "artifact change-set serialization is noncanonical or invalid",
    ArtifactChangeSetErrorCode.INVALID_UTF8: "artifact change text must be strict UTF-8",
    ArtifactChangeSetErrorCode.INVALID_PATH: "artifact change path is not canonical",
    ArtifactChangeSetErrorCode.INVALID_KIND: "artifact change kind does not match its canonical path",
    ArtifactChangeSetErrorCode.INVALID_DIGEST: "artifact change digest is invalid",
    ArtifactChangeSetErrorCode.INVALID_HUNK: "artifact text hunk is invalid",
    ArtifactChangeSetErrorCode.INVALID_BYTE_BOUNDARY: "artifact text hunk is not on UTF-8 byte boundaries",
    ArtifactChangeSetErrorCode.NO_OP: "artifact change must alter the projected state",
    ArtifactChangeSetErrorCode.HUNK_ORDER: "artifact text hunks are not in canonical order",
    ArtifactChangeSetErrorCode.HUNK_OVERLAP: "artifact text hunks overlap",
    ArtifactChangeSetErrorCode.DUPLICATE_INSERTION: "artifact text hunks contain a duplicate insertion anchor",
    ArtifactChangeSetErrorCode.OPERATION_ORDER: "artifact operations are not in canonical order",
    ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH: "artifact base state does not match the change set",
    ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH: "artifact base content does not match the operation",
    ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH: "artifact projected content digest does not match",
    ArtifactChangeSetErrorCode.PATH_COLLISION: "artifact change-set paths collide",
    ArtifactChangeSetErrorCode.RENAME_CONFLICT: "artifact rename topology is invalid",
    ArtifactChangeSetErrorCode.CHANGE_SET_DIGEST_MISMATCH: "artifact change-set digest does not match",
}


class ArtifactChangeSetError(RuntimeError):
    """Deterministic change-set failure that never incorporates story text."""

    def __init__(
        self,
        code: ArtifactChangeSetErrorCode,
        *,
        operation_index: int | None = None,
        hunk_index: int | None = None,
    ) -> None:
        if type(code) is not ArtifactChangeSetErrorCode:
            raise TypeError("code must be an ArtifactChangeSetErrorCode")
        for name, value in (
            ("operation_index", operation_index),
            ("hunk_index", hunk_index),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise TypeError(f"{name} must be a nonnegative integer or None")
        self.code = code
        self.operation_index = operation_index
        self.hunk_index = hunk_index
        super().__init__(_ERROR_MESSAGES[code])


class ArtifactChangeOperationKind(StrEnum):
    """Closed pure artifact operation set."""

    CREATE = "create"
    PATCH = "patch"
    REPLACE = "replace"
    RENAME = "rename"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class ArtifactTextHunk:
    """One exact half-open UTF-8 byte edit against an original base."""

    base_digest: str
    start_utf8_byte: int
    end_utf8_byte: int
    old_text: bytes = field(repr=False)
    new_text: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_digest(self.base_digest)
        if (
            type(self.start_utf8_byte) is not int
            or type(self.end_utf8_byte) is not int
            or self.start_utf8_byte < 0
            or self.end_utf8_byte < self.start_utf8_byte
        ):
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_HUNK)
        _require_utf8_bytes(self.old_text)
        _require_utf8_bytes(self.new_text)
        if len(self.old_text) != self.end_utf8_byte - self.start_utf8_byte:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_HUNK)
        if self.old_text == self.new_text:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.NO_OP)


@dataclass(frozen=True, slots=True)
class ArtifactCreateOperation:
    """Create one exactly absent canonical artifact."""

    path: str
    artifact_kind: ArtifactKind
    content: bytes = field(repr=False)
    projected_digest: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.CREATE

    def __post_init__(self) -> None:
        _require_artifact_identity(self.path, self.artifact_kind)
        _require_utf8_bytes(self.content)
        _require_digest(self.projected_digest)
        if _content_digest(self.content) != self.projected_digest:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH
            )


@dataclass(frozen=True, slots=True)
class ArtifactPatchOperation:
    """Patch one exact present artifact through ordered original-base hunks."""

    path: str
    artifact_kind: ArtifactKind
    base_digest: str
    hunks: tuple[ArtifactTextHunk, ...]
    projected_digest: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.PATCH

    def __post_init__(self) -> None:
        _require_artifact_identity(self.path, self.artifact_kind)
        _require_digest(self.base_digest)
        _require_digest(self.projected_digest)
        if type(self.hunks) is not tuple or not self.hunks:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_HUNK)
        if any(type(hunk) is not ArtifactTextHunk for hunk in self.hunks):
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_HUNK)
        _validate_hunk_sequence(self.hunks, self.base_digest)


@dataclass(frozen=True, slots=True)
class ArtifactReplaceOperation:
    """Replace one exact present canonical artifact."""

    path: str
    artifact_kind: ArtifactKind
    base_digest: str
    content: bytes = field(repr=False)
    projected_digest: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.REPLACE

    def __post_init__(self) -> None:
        _require_artifact_identity(self.path, self.artifact_kind)
        _require_digest(self.base_digest)
        _require_utf8_bytes(self.content)
        _require_digest(self.projected_digest)
        if _content_digest(self.content) != self.projected_digest:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH
            )


@dataclass(frozen=True, slots=True)
class ArtifactRenameOperation:
    """Rename one exact present artifact without changing its bytes."""

    source_path: str
    destination_path: str
    artifact_kind: ArtifactKind
    base_digest: str
    projected_digest: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.RENAME

    def __post_init__(self) -> None:
        _require_artifact_identity(self.source_path, self.artifact_kind)
        _require_artifact_identity(self.destination_path, self.artifact_kind)
        if self.source_path == self.destination_path:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.RENAME_CONFLICT)
        _require_digest(self.base_digest)
        _require_digest(self.projected_digest)
        if self.projected_digest != self.base_digest:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH
            )


@dataclass(frozen=True, slots=True)
class ArtifactDeleteOperation:
    """Delete one exact present canonical artifact."""

    path: str
    artifact_kind: ArtifactKind
    base_digest: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.DELETE

    def __post_init__(self) -> None:
        _require_artifact_identity(self.path, self.artifact_kind)
        _require_digest(self.base_digest)


ArtifactChangeOperation: TypeAlias = (
    ArtifactCreateOperation
    | ArtifactPatchOperation
    | ArtifactReplaceOperation
    | ArtifactRenameOperation
    | ArtifactDeleteOperation
)
_OPERATION_TYPES = (
    ArtifactCreateOperation,
    ArtifactPatchOperation,
    ArtifactReplaceOperation,
    ArtifactRenameOperation,
    ArtifactDeleteOperation,
)


@dataclass(frozen=True, slots=True)
class ArtifactChangeSet:
    """One immutable content identity for canonical multi-artifact changes."""

    schema_version: int
    base_state_digest: str
    operations: tuple[ArtifactChangeOperation, ...]
    projected_state_digest: str
    change_set_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != ARTIFACT_CHANGE_SET_SCHEMA_VERSION
        ):
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
        _require_digest(self.base_state_digest)
        _require_digest(self.projected_state_digest)
        _require_digest(self.change_set_digest)
        _validate_operation_sequence(self.operations)
        if _change_set_digest(self) != self.change_set_digest:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.CHANGE_SET_DIGEST_MISMATCH
            )


def build_artifact_change_set(
    base_state: Mapping[str, bytes],
    operations: tuple[ArtifactChangeOperation, ...],
) -> ArtifactChangeSet:
    """Validate exact operations and construct their canonical content identity."""

    normalized = _normalize_state(base_state)
    _validate_operation_sequence(operations)
    projected = _project_operations(normalized, operations)
    base_state_digest = _state_digest(normalized)
    projected_state_digest = _state_digest(projected)
    change_set_digest = _change_set_digest_fields(
        schema_version=ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
        base_state_digest=base_state_digest,
        operations=operations,
        projected_state_digest=projected_state_digest,
    )
    return ArtifactChangeSet(
        schema_version=ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
        base_state_digest=base_state_digest,
        operations=operations,
        projected_state_digest=projected_state_digest,
        change_set_digest=change_set_digest,
    )


def project_artifact_change_set(
    base_state: Mapping[str, bytes],
    change_set: ArtifactChangeSet,
) -> Mapping[str, bytes]:
    """Project one validated change set over its exact complete base state."""

    _require_change_set(change_set)
    normalized = _normalize_state(base_state)
    if _state_digest(normalized) != change_set.base_state_digest:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH)
    projected = _project_operations(normalized, change_set.operations)
    if _state_digest(projected) != change_set.projected_state_digest:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH
        )
    return MappingProxyType(
        dict(sorted(projected.items(), key=lambda item: item[0].encode("utf-8")))
    )


def serialize_artifact_change_set(change_set: ArtifactChangeSet) -> bytes:
    """Return the one strict JSON encoding that intentionally exposes edit text."""

    _require_change_set(change_set)
    invalid = False
    try:
        serialized = _canonical_json_bytes(_change_set_object(change_set))
    except Exception:
        invalid = True
        serialized = b""
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    return serialized


def deserialize_artifact_change_set(data: bytes) -> ArtifactChangeSet:
    """Decode only the exact canonical serialized change-set form."""

    if type(data) is not bytes:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SERIALIZATION)
    text: str | None = None
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    if text is None:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_UTF8)
    decoding_error: ArtifactChangeSetError | None = None
    try:
        raw = json.loads(
            text,
            object_pairs_hook=_decode_unique_object,
            parse_constant=_reject_json_constant,
        )
    except ArtifactChangeSetError as error:
        decoding_error = error
        raw = None
    except Exception:
        decoding_error = ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_SERIALIZATION
        )
        raw = None
    if decoding_error is not None:
        raise ArtifactChangeSetError(
            decoding_error.code,
            operation_index=decoding_error.operation_index,
            hunk_index=decoding_error.hunk_index,
        )
    encoding_invalid = False
    try:
        canonical = _canonical_json_bytes(raw)
    except Exception:
        encoding_invalid = True
        canonical = b""
    if encoding_invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SERIALIZATION)
    if canonical != data:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SERIALIZATION)
    if type(raw) is not dict or set(raw) != {
        "base_state_digest",
        "change_set_digest",
        "operations",
        "projected_state_digest",
        "schema_version",
    }:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    raw_operations = raw["operations"]
    if type(raw_operations) is not list:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    operations = tuple(
        _operation_from_object(item, operation_index=index)
        for index, item in enumerate(raw_operations)
    )
    return ArtifactChangeSet(
        schema_version=raw["schema_version"],
        base_state_digest=raw["base_state_digest"],
        operations=operations,
        projected_state_digest=raw["projected_state_digest"],
        change_set_digest=raw["change_set_digest"],
    )


def _require_digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_DIGEST)
    return value


def _require_change_set(value: object) -> ArtifactChangeSet:
    if type(value) is not ArtifactChangeSet:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    validation_error: ArtifactChangeSetError | None = None
    invalid = False
    try:
        value.__post_init__()
    except ArtifactChangeSetError as error:
        validation_error = error
    except Exception:
        invalid = True
    if validation_error is not None:
        raise ArtifactChangeSetError(
            validation_error.code,
            operation_index=validation_error.operation_index,
            hunk_index=validation_error.hunk_index,
        )
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    return value


def _require_utf8_bytes(value: object) -> bytes:
    if type(value) is not bytes:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_UTF8)
    invalid = False
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        invalid = True
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_UTF8)
    return value


def _require_artifact_identity(path: object, kind: object) -> ArtifactKind:
    if type(kind) is not ArtifactKind:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_KIND)
    invalid = False
    try:
        observed = canonical_artifact_kind(path)
    except (TypeError, ValueError):
        invalid = True
        observed = None
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_PATH)
    if observed is None:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_PATH)
    if observed is not kind:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_KIND)
    return observed


def _content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _operation_primary_path(operation: ArtifactChangeOperation) -> str:
    if type(operation) is ArtifactRenameOperation:
        return operation.source_path
    return operation.path


def _validate_hunk_sequence(
    hunks: tuple[ArtifactTextHunk, ...],
    base_digest: str,
) -> None:
    previous_key: tuple[int, int] | None = None
    consumed_until = 0
    insertion_anchors: set[int] = set()
    for index, hunk in enumerate(hunks):
        if hunk.base_digest != base_digest:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_HUNK,
                hunk_index=index,
            )
        key = (hunk.start_utf8_byte, hunk.end_utf8_byte)
        if previous_key is not None and key < previous_key:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.HUNK_ORDER,
                hunk_index=index,
            )
        if hunk.start_utf8_byte < consumed_until:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.HUNK_OVERLAP,
                hunk_index=index,
            )
        if hunk.start_utf8_byte == hunk.end_utf8_byte:
            if hunk.start_utf8_byte in insertion_anchors:
                raise ArtifactChangeSetError(
                    ArtifactChangeSetErrorCode.DUPLICATE_INSERTION,
                    hunk_index=index,
                )
            insertion_anchors.add(hunk.start_utf8_byte)
        consumed_until = max(consumed_until, hunk.end_utf8_byte)
        previous_key = key


def _validate_operation_sequence(
    operations: object,
) -> tuple[ArtifactChangeOperation, ...]:
    if type(operations) is not tuple:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    if not operations:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.NO_OP)
    for index, operation in enumerate(operations):
        if type(operation) not in _OPERATION_TYPES:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_SCHEMA,
                operation_index=index,
            )
        validation_error: ArtifactChangeSetError | None = None
        invalid = False
        try:
            operation.__post_init__()
        except ArtifactChangeSetError as error:
            validation_error = error
        except Exception:
            invalid = True
        if validation_error is not None:
            raise ArtifactChangeSetError(
                validation_error.code,
                operation_index=(
                    validation_error.operation_index
                    if validation_error.operation_index is not None
                    else index
                ),
                hunk_index=validation_error.hunk_index,
            )
        if invalid:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_SCHEMA,
                operation_index=index,
            )
    typed = operations
    rename_sources = {
        operation.source_path
        for operation in typed
        if type(operation) is ArtifactRenameOperation
    }
    rename_destinations = {
        operation.destination_path
        for operation in typed
        if type(operation) is ArtifactRenameOperation
    }
    if rename_sources & rename_destinations:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.RENAME_CONFLICT)
    previous_key: bytes | None = None
    participants: set[str] = set()
    for index, operation in enumerate(typed):
        primary_path = _operation_primary_path(operation)
        key = primary_path.encode("utf-8")
        if previous_key is not None and key < previous_key:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.OPERATION_ORDER,
                operation_index=index,
            )
        paths = (
            (operation.source_path, operation.destination_path)
            if type(operation) is ArtifactRenameOperation
            else (operation.path,)
        )
        if any(path in participants for path in paths) or len(set(paths)) != len(paths):
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.PATH_COLLISION,
                operation_index=index,
            )
        participants.update(paths)
        previous_key = key
    return typed


def _normalize_state(state: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(state, Mapping):
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    invalid = False
    try:
        items = tuple(state.items())
    except Exception:
        invalid = True
        items = ()
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SCHEMA)
    normalized: dict[str, bytes] = {}
    for path, content in items:
        if type(path) is not str:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_PATH)
        invalid_path = False
        try:
            kind = canonical_artifact_kind(path)
        except (TypeError, ValueError):
            invalid_path = True
            kind = None
        if invalid_path:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_PATH)
        if kind is None:
            raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_PATH)
        normalized[path] = _require_utf8_bytes(content)
    return normalized


def _state_digest(state: Mapping[str, bytes]) -> str:
    manifest = {
        "artifacts": [
            {
                "content_digest": _content_digest(content),
                "kind": canonical_artifact_kind(path).value,
                "path": path,
                "utf8_bytes": len(content),
            }
            for path, content in sorted(
                state.items(),
                key=lambda item: item[0].encode("utf-8"),
            )
        ],
        "schema_version": ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
    }
    return _content_digest(_canonical_json_bytes(manifest))


def _project_operations(
    base_state: Mapping[str, bytes],
    operations: tuple[ArtifactChangeOperation, ...],
) -> dict[str, bytes]:
    projected = dict(base_state)
    for index, operation in enumerate(operations):
        if type(operation) is ArtifactCreateOperation:
            if operation.path in base_state:
                raise ArtifactChangeSetError(
                    ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                    operation_index=index,
                )
            projected[operation.path] = operation.content
            continue
        source_path = _operation_primary_path(operation)
        base_content = base_state.get(source_path)
        if (
            base_content is None
            or _content_digest(base_content) != operation.base_digest
        ):
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                operation_index=index,
            )
        if type(operation) is ArtifactPatchOperation:
            after = _project_hunks(base_content, operation, operation_index=index)
            projected[source_path] = after
        elif type(operation) is ArtifactReplaceOperation:
            if operation.content == base_content:
                raise ArtifactChangeSetError(
                    ArtifactChangeSetErrorCode.NO_OP,
                    operation_index=index,
                )
            projected[source_path] = operation.content
        elif type(operation) is ArtifactRenameOperation:
            if operation.destination_path in base_state:
                raise ArtifactChangeSetError(
                    ArtifactChangeSetErrorCode.RENAME_CONFLICT,
                    operation_index=index,
                )
            del projected[source_path]
            projected[operation.destination_path] = base_content
        else:
            del projected[source_path]
    if projected == dict(base_state):
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.NO_OP)
    return projected


def _project_hunks(
    base: bytes,
    operation: ArtifactPatchOperation,
    *,
    operation_index: int,
) -> bytes:
    if _content_digest(base) != operation.base_digest:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
            operation_index=operation_index,
        )
    pieces: list[bytes] = []
    cursor = 0
    for hunk_index, hunk in enumerate(operation.hunks):
        start = hunk.start_utf8_byte
        end = hunk.end_utf8_byte
        if (
            end > len(base)
            or not _is_utf8_boundary(base, start)
            or not _is_utf8_boundary(base, end)
        ):
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_BYTE_BOUNDARY,
                operation_index=operation_index,
                hunk_index=hunk_index,
            )
        if base[start:end] != hunk.old_text:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH,
                operation_index=operation_index,
                hunk_index=hunk_index,
            )
        pieces.extend((base[cursor:start], hunk.new_text))
        cursor = end
    pieces.append(base[cursor:])
    projected = b"".join(pieces)
    if projected == base:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.NO_OP,
            operation_index=operation_index,
        )
    if _content_digest(projected) != operation.projected_digest:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.PROJECTED_DIGEST_MISMATCH,
            operation_index=operation_index,
        )
    return projected


def _is_utf8_boundary(content: bytes, offset: int) -> bool:
    if not 0 <= offset <= len(content):
        return False
    return offset in {0, len(content)} or content[offset] & 0xC0 != 0x80


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")


def _change_set_digest(change_set: ArtifactChangeSet) -> str:
    return _change_set_digest_fields(
        schema_version=change_set.schema_version,
        base_state_digest=change_set.base_state_digest,
        operations=change_set.operations,
        projected_state_digest=change_set.projected_state_digest,
    )


def _change_set_digest_fields(
    *,
    schema_version: int,
    base_state_digest: str,
    operations: tuple[ArtifactChangeOperation, ...],
    projected_state_digest: str,
) -> str:
    return _content_digest(
        _canonical_json_bytes(
            {
                "base_state_digest": base_state_digest,
                "operations": [_operation_to_object(item) for item in operations],
                "projected_state_digest": projected_state_digest,
                "schema_version": schema_version,
            }
        )
    )


def _change_set_object(change_set: ArtifactChangeSet) -> dict[str, object]:
    return {
        "base_state_digest": change_set.base_state_digest,
        "change_set_digest": change_set.change_set_digest,
        "operations": [_operation_to_object(item) for item in change_set.operations],
        "projected_state_digest": change_set.projected_state_digest,
        "schema_version": change_set.schema_version,
    }


def _operation_to_object(operation: ArtifactChangeOperation) -> dict[str, object]:
    if type(operation) is ArtifactCreateOperation:
        return {
            "content": operation.content.decode("utf-8"),
            "kind": operation.artifact_kind.value,
            "operation": ArtifactChangeOperationKind.CREATE.value,
            "path": operation.path,
            "projected_digest": operation.projected_digest,
        }
    if type(operation) is ArtifactPatchOperation:
        return {
            "base_digest": operation.base_digest,
            "hunks": [
                {
                    "base_digest": hunk.base_digest,
                    "end_utf8_byte": hunk.end_utf8_byte,
                    "new_text": hunk.new_text.decode("utf-8"),
                    "old_text": hunk.old_text.decode("utf-8"),
                    "start_utf8_byte": hunk.start_utf8_byte,
                }
                for hunk in operation.hunks
            ],
            "kind": operation.artifact_kind.value,
            "operation": ArtifactChangeOperationKind.PATCH.value,
            "path": operation.path,
            "projected_digest": operation.projected_digest,
        }
    if type(operation) is ArtifactReplaceOperation:
        return {
            "base_digest": operation.base_digest,
            "content": operation.content.decode("utf-8"),
            "kind": operation.artifact_kind.value,
            "operation": ArtifactChangeOperationKind.REPLACE.value,
            "path": operation.path,
            "projected_digest": operation.projected_digest,
        }
    if type(operation) is ArtifactRenameOperation:
        return {
            "base_digest": operation.base_digest,
            "destination_path": operation.destination_path,
            "kind": operation.artifact_kind.value,
            "operation": ArtifactChangeOperationKind.RENAME.value,
            "projected_digest": operation.projected_digest,
            "source_path": operation.source_path,
        }
    return {
        "base_digest": operation.base_digest,
        "kind": operation.artifact_kind.value,
        "operation": ArtifactChangeOperationKind.DELETE.value,
        "path": operation.path,
    }


def _operation_from_object(
    value: object,
    *,
    operation_index: int,
) -> ArtifactChangeOperation:
    if type(value) is not dict or type(value.get("operation")) is not str:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            operation_index=operation_index,
        )
    invalid_operation = False
    try:
        operation_kind = ArtifactChangeOperationKind(value["operation"])
    except ValueError:
        invalid_operation = True
        operation_kind = None
    if invalid_operation:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            operation_index=operation_index,
        )
    invalid_kind = False
    try:
        artifact_kind = ArtifactKind(value.get("kind"))
    except (TypeError, ValueError):
        invalid_kind = True
        artifact_kind = None
    if invalid_kind:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_KIND,
            operation_index=operation_index,
        )
    assert type(operation_kind) is ArtifactChangeOperationKind
    assert type(artifact_kind) is ArtifactKind
    if operation_kind is ArtifactChangeOperationKind.CREATE:
        _require_fields(
            value,
            {"content", "kind", "operation", "path", "projected_digest"},
            operation_index,
        )
        return ArtifactCreateOperation(
            path=value["path"],
            artifact_kind=artifact_kind,
            content=_text_to_bytes(value["content"]),
            projected_digest=value["projected_digest"],
        )
    if operation_kind is ArtifactChangeOperationKind.PATCH:
        _require_fields(
            value,
            {
                "base_digest",
                "hunks",
                "kind",
                "operation",
                "path",
                "projected_digest",
            },
            operation_index,
        )
        raw_hunks = value["hunks"]
        if type(raw_hunks) is not list:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_SCHEMA,
                operation_index=operation_index,
            )
        hunks = tuple(
            _hunk_from_object(item, operation_index=operation_index, hunk_index=index)
            for index, item in enumerate(raw_hunks)
        )
        return ArtifactPatchOperation(
            path=value["path"],
            artifact_kind=artifact_kind,
            base_digest=value["base_digest"],
            hunks=hunks,
            projected_digest=value["projected_digest"],
        )
    if operation_kind is ArtifactChangeOperationKind.REPLACE:
        _require_fields(
            value,
            {
                "base_digest",
                "content",
                "kind",
                "operation",
                "path",
                "projected_digest",
            },
            operation_index,
        )
        return ArtifactReplaceOperation(
            path=value["path"],
            artifact_kind=artifact_kind,
            base_digest=value["base_digest"],
            content=_text_to_bytes(value["content"]),
            projected_digest=value["projected_digest"],
        )
    if operation_kind is ArtifactChangeOperationKind.RENAME:
        _require_fields(
            value,
            {
                "base_digest",
                "destination_path",
                "kind",
                "operation",
                "projected_digest",
                "source_path",
            },
            operation_index,
        )
        return ArtifactRenameOperation(
            source_path=value["source_path"],
            destination_path=value["destination_path"],
            artifact_kind=artifact_kind,
            base_digest=value["base_digest"],
            projected_digest=value["projected_digest"],
        )
    _require_fields(
        value,
        {"base_digest", "kind", "operation", "path"},
        operation_index,
    )
    return ArtifactDeleteOperation(
        path=value["path"],
        artifact_kind=artifact_kind,
        base_digest=value["base_digest"],
    )


def _hunk_from_object(
    value: object,
    *,
    operation_index: int,
    hunk_index: int,
) -> ArtifactTextHunk:
    fields = {
        "base_digest",
        "end_utf8_byte",
        "new_text",
        "old_text",
        "start_utf8_byte",
    }
    if type(value) is not dict or set(value) != fields:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            operation_index=operation_index,
            hunk_index=hunk_index,
        )
    return ArtifactTextHunk(
        base_digest=value["base_digest"],
        start_utf8_byte=value["start_utf8_byte"],
        end_utf8_byte=value["end_utf8_byte"],
        old_text=_text_to_bytes(value["old_text"]),
        new_text=_text_to_bytes(value["new_text"]),
    )


def _require_fields(
    value: dict[object, object],
    fields: set[str],
    operation_index: int,
) -> None:
    if set(value) != fields:
        raise ArtifactChangeSetError(
            ArtifactChangeSetErrorCode.INVALID_SCHEMA,
            operation_index=operation_index,
        )


def _text_to_bytes(value: object) -> bytes:
    if type(value) is not str:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_UTF8)
    invalid = False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        invalid = True
        encoded = b""
    if invalid:
        raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_UTF8)
    return encoded


def _decode_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactChangeSetError(
                ArtifactChangeSetErrorCode.INVALID_SERIALIZATION
            )
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ArtifactChangeSetError(ArtifactChangeSetErrorCode.INVALID_SERIALIZATION)


__all__ = (
    "ARTIFACT_CHANGE_SET_SCHEMA_VERSION",
    "ArtifactChangeOperation",
    "ArtifactChangeOperationKind",
    "ArtifactChangeSet",
    "ArtifactChangeSetError",
    "ArtifactChangeSetErrorCode",
    "ArtifactCreateOperation",
    "ArtifactDeleteOperation",
    "ArtifactPatchOperation",
    "ArtifactRenameOperation",
    "ArtifactReplaceOperation",
    "ArtifactTextHunk",
    "build_artifact_change_set",
    "deserialize_artifact_change_set",
    "project_artifact_change_set",
    "serialize_artifact_change_set",
)
