"""Immutable planning inputs, policies, and closed value validation."""

from __future__ import annotations

import hashlib
from ..manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextManifestError,
    ContextProvenance,
    ContextScope,
    ContextSourceKind,
    validate_context_digest,
    validate_context_identity,
)
from dataclasses import (
    dataclass,
    field,
)
from enum import StrEnum


CONTEXT_CHECKPOINT_SCHEMA_VERSION = 1


MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES = 262_144


MAX_RECORD_ALIAS = (1 << 53) - 1


class ContextDeliveryMode(StrEnum):
    """Whether a projection bootstraps a prefix or delivers one delta."""

    BOOTSTRAP = "bootstrap"
    DELTA = "delta"


class ContextCheckpointState(StrEnum):
    """Authoritative checkpoint availability and trust state."""

    READY = "ready"
    MISSING = "missing"
    CORRUPT = "corrupt"
    STALE = "stale"
    TAINTED = "tainted"


class ContextBootstrapReason(StrEnum):
    """Fixed reasons for refusing checkpoint suppression."""

    EXPLICIT_FRESH = "explicit_fresh"
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_CORRUPT = "checkpoint_corrupt"
    CHECKPOINT_STALE = "checkpoint_stale"
    CHECKPOINT_TAINTED = "checkpoint_tainted"
    SCOPE_MISMATCH = "scope_mismatch"
    PROVIDER_MISMATCH = "provider_mismatch"
    PROVIDER_CONTEXT_UNAVAILABLE = "provider_context_unavailable"
    PROVIDER_THREAD_MISMATCH = "provider_thread_mismatch"
    DEFINITION_FINGERPRINT_MISMATCH = "definition_fingerprint_mismatch"
    STABLE_CONTEXT_CHANGED = "stable_context_changed"
    POLICY_FINGERPRINT_MISMATCH = "policy_fingerprint_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"
    MAX_DELTA_DEPTH = "max_delta_depth"


class ContextDecisionKind(StrEnum):
    """Content-free outcomes assigned exactly once to each manifest entry."""

    SELECTED = "selected"
    DEDUPLICATED = "deduplicated"
    CHECKPOINT_SUPPRESSED = "checkpoint_suppressed"
    OMITTED_CLASS_BUDGET = "omitted_class_budget"
    OMITTED_MATERIAL = "omitted_material"


class ContextPlanningErrorCode(StrEnum):
    """Stable content-free failures produced by context planning."""

    INVALID_VALUE = "invalid_value"
    INVALID_POLICY = "invalid_policy"
    INVALID_CHECKPOINT = "invalid_checkpoint"
    INVALID_MATERIAL = "invalid_material"
    MATERIAL_LENGTH_MISMATCH = "material_length_mismatch"
    MATERIAL_DIGEST_MISMATCH = "material_digest_mismatch"
    REVISION_CONTENT_MISMATCH = "revision_content_mismatch"
    MANIFEST_SCOPE_MISMATCH = "manifest_scope_mismatch"
    REQUIRED_MATERIAL_MISSING = "required_material_missing"
    STABLE_MATERIAL_MISSING = "stable_material_missing"
    WORKING_CONSTRAINT_MATERIAL_MISSING = (
        "working_constraint_material_missing"
    )
    REQUIRED_BUDGET_EXCEEDED = "required_budget_exceeded"
    STABLE_BUDGET_EXCEEDED = "stable_budget_exceeded"
    WORKING_CONSTRAINT_BUDGET_EXCEEDED = (
        "working_constraint_budget_exceeded"
    )
    REFERENCE_BUDGET_EXCEEDED = "reference_budget_exceeded"


def _planning_error_message(code: ContextPlanningErrorCode) -> str:
    """Return fixed content-free text for one planning failure code."""

    if code is ContextPlanningErrorCode.INVALID_VALUE:
        return "context planning contains an invalid value"
    if code is ContextPlanningErrorCode.INVALID_POLICY:
        return "context planning policy is invalid"
    if code is ContextPlanningErrorCode.INVALID_CHECKPOINT:
        return "context checkpoint is invalid"
    if code is ContextPlanningErrorCode.INVALID_MATERIAL:
        return "context material is invalid"
    if code is ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH:
        return "context material length does not match its manifest entry"
    if code is ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH:
        return "context material digest does not match its manifest entry"
    if code is ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH:
        return "a source revision conflicts with checkpoint content identity"
    if code is ContextPlanningErrorCode.MANIFEST_SCOPE_MISMATCH:
        return "context manifest and planning scope do not match"
    if code is ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING:
        return "required context material is unavailable"
    if code is ContextPlanningErrorCode.STABLE_MATERIAL_MISSING:
        return "stable context material is unavailable"
    if code is ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING:
        return "working hard-constraint material is unavailable"
    if code is ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED:
        return "required context exceeds its planning budget"
    if code is ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED:
        return "stable context exceeds its planning budget"
    if code is ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED:
        return "working hard constraints exceed the configured budget"
    if code is ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED:
        return "mandatory references exceed their reservation budget"
    raise AssertionError("unhandled context planning error code")


class ContextPlanningError(ValueError):
    """Report one stable content-free context planning failure."""

    def __init__(self, code: ContextPlanningErrorCode) -> None:
        if type(code) is not ContextPlanningErrorCode:
            raise TypeError("planning error code must be a ContextPlanningErrorCode")
        self.code = code
        super().__init__(_planning_error_message(code))


def exact_nonnegative_integer(value: object, *, policy: bool = False) -> int:
    """Return an exact nonnegative integer or raise one fixed failure."""

    if type(value) is not int or value < 0:
        raise ContextPlanningError(
            ContextPlanningErrorCode.INVALID_POLICY
            if policy
            else ContextPlanningErrorCode.INVALID_VALUE
        )
    assert isinstance(value, int)
    return value


def safe_identity(value: object, *, checkpoint: bool = False) -> str:
    """Validate content-free identity text under the manifest grammar."""

    validated: str | None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        validated = None
    if validated is None:
        raise ContextPlanningError(
            ContextPlanningErrorCode.INVALID_CHECKPOINT
            if checkpoint
            else ContextPlanningErrorCode.INVALID_VALUE
        )
    return validated


def _safe_digest(value: object, *, checkpoint: bool = False) -> str:
    """Validate a digest without reflecting its value in failures."""

    validated: str | None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        validated = None
    if validated is None:
        raise ContextPlanningError(
            ContextPlanningErrorCode.INVALID_CHECKPOINT
            if checkpoint
            else ContextPlanningErrorCode.INVALID_VALUE
        )
    return validated


def body_digest(body_utf8: bytes) -> str:
    """Return the deterministic body digest used for material validation."""

    return f"sha256:{hashlib.sha256(body_utf8).hexdigest()}"


_RECORD_BOUNDARY_KINDS = frozenset(
    {"whole", "artifact_lines", "conversation_turns", "records"}
)


def _validate_record_boundary(
    kind: object,
    start: object,
    end: object,
    first_id: object,
    last_id: object,
    *,
    checkpoint: bool = False,
) -> None:
    """Validate the primitive projection of one exact source boundary."""

    error = (
        ContextPlanningErrorCode.INVALID_CHECKPOINT
        if checkpoint
        else ContextPlanningErrorCode.INVALID_MATERIAL
    )
    if type(kind) is not str or kind not in _RECORD_BOUNDARY_KINDS:
        raise ContextPlanningError(error)
    if kind == "whole":
        if any(value is not None for value in (start, end, first_id, last_id)):
            raise ContextPlanningError(error)
        return
    if (
        type(start) is not int
        or start < 1
        or type(end) is not int
        or end < start
    ):
        raise ContextPlanningError(error)
    if kind == "conversation_turns":
        try:
            safe_identity(first_id, checkpoint=checkpoint)
            safe_identity(last_id, checkpoint=checkpoint)
        except ContextPlanningError:
            raise ContextPlanningError(error) from None
    elif first_id is not None or last_id is not None:
        raise ContextPlanningError(error)


def validate_source_alias(value: object, *, allow_empty: bool = False) -> str:
    """Validate one compact stable source alias without regular expressions."""

    if allow_empty and value == "":
        return ""
    if (
        type(value) is not str
        or not value.startswith("s")
        or not value[1:].isdigit()
        or value[1:].startswith("0")
    ):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
    return value


def validate_record_alias(value: object, *, checkpoint: bool = False) -> int:
    """Validate one JSON-exact positive per-source delivery alias."""

    if type(value) is not int or value < 1 or value > MAX_RECORD_ALIAS:
        raise ContextPlanningError(
            ContextPlanningErrorCode.INVALID_CHECKPOINT
            if checkpoint
            else ContextPlanningErrorCode.INVALID_VALUE
        )
    return value


def context_source_uses_catalog_alias(entry: ContextManifestEntry) -> bool:
    """Return whether one source participates in the readable wire catalog."""

    if type(entry) is not ContextManifestEntry:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    return entry.source_kind in {
        ContextSourceKind.WRITING_ARTIFACT,
        ContextSourceKind.CONVERSATION,
        ContextSourceKind.ACTIVE_DEBATE,
        ContextSourceKind.OWN_AGENT_STATE,
    }


@dataclass(frozen=True, slots=True)
class ContextRecordCursor:
    """Content-free identity and byte facts for one atomic source record."""

    record_id: str
    boundary_kind: str
    boundary_start: int | None
    boundary_end: int | None
    boundary_first_id: str | None
    boundary_last_id: str | None
    content_digest: str
    utf8_bytes: int

    def __post_init__(self) -> None:
        safe_identity(self.record_id)
        _validate_record_boundary(
            self.boundary_kind,
            self.boundary_start,
            self.boundary_end,
            self.boundary_first_id,
            self.boundary_last_id,
        )
        _safe_digest(self.content_digest)
        exact_nonnegative_integer(self.utf8_bytes)

    @property
    def boundary(self) -> tuple[object, ...]:
        """Return the compact exact boundary tuple used by the wire codec."""

        return (
            self.boundary_kind,
            self.boundary_start,
            self.boundary_end,
            self.boundary_first_id,
            self.boundary_last_id,
        )


@dataclass(frozen=True, slots=True)
class ContextRecordMaterial:
    """Transient strict-UTF-8 body paired with one content-free cursor."""

    cursor: ContextRecordCursor
    body_utf8: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.cursor) is not ContextRecordCursor or type(
            self.body_utf8
        ) is not bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)
        try:
            self.body_utf8.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ContextPlanningError(
                ContextPlanningErrorCode.INVALID_MATERIAL
            ) from None
        if (
            len(self.body_utf8) != self.cursor.utf8_bytes
            or body_digest(self.body_utf8) != self.cursor.content_digest
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)


@dataclass(frozen=True, slots=True)
class ContextMaterial:
    """Transient strict-UTF-8 material for one manifest entry."""

    entry: ContextManifestEntry
    body_utf8: bytes = field(repr=False)
    records: tuple[ContextRecordMaterial, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.entry) is not ContextManifestEntry
            or type(self.body_utf8) is not bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)
        valid_utf8 = True
        try:
            self.body_utf8.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            valid_utf8 = False
        if not valid_utf8:
            raise ContextPlanningError(
                ContextPlanningErrorCode.INVALID_MATERIAL
            )
        if len(self.body_utf8) != self.entry.utf8_bytes:
            raise ContextPlanningError(
                ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH
            )
        if body_digest(self.body_utf8) != self.entry.content_digest:
            raise ContextPlanningError(
                ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH
            )
        if type(self.records) is not tuple or any(
            type(record) is not ContextRecordMaterial for record in self.records
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)
        record_ids = tuple(record.cursor.record_id for record in self.records)
        if len(set(record_ids)) != len(record_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)


@dataclass(frozen=True, slots=True)
class MandatoryReferenceReservation:
    """Content-free byte reservation for one explicitly ordered reference."""

    ordinal: int
    reference_id: str
    source_id: str
    revision: str
    provenance: ContextProvenance
    content_digest: str
    utf8_bytes: int

    def __post_init__(self) -> None:
        ordinal = exact_nonnegative_integer(self.ordinal)
        if ordinal == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        safe_identity(self.reference_id)
        safe_identity(self.source_id)
        safe_identity(self.revision)
        if type(self.provenance) is not ContextProvenance:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if (
            self.provenance.authority_kind
            is not ContextAuthorityKind.REFERENCE_AUTHORIZATION
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        _safe_digest(self.content_digest)
        exact_nonnegative_integer(self.utf8_bytes)


@dataclass(frozen=True, slots=True)
class ContextPlanningPolicy:
    """Finite raw-source budgets and checkpoint depth for pure planning."""

    total_source_utf8_bytes: int = 131_072
    p0_source_utf8_bytes: int = 73_728
    p1_source_utf8_bytes: int = 16_384
    p2_source_utf8_bytes: int = 32_768
    p3_source_utf8_bytes: int = 0
    mandatory_reference_utf8_bytes: int = 262_144
    max_delta_depth: int = 16

    def __post_init__(self) -> None:
        total = exact_nonnegative_integer(
            self.total_source_utf8_bytes,
            policy=True,
        )
        p0 = exact_nonnegative_integer(self.p0_source_utf8_bytes, policy=True)
        p1 = exact_nonnegative_integer(self.p1_source_utf8_bytes, policy=True)
        p2 = exact_nonnegative_integer(self.p2_source_utf8_bytes, policy=True)
        p3 = exact_nonnegative_integer(self.p3_source_utf8_bytes, policy=True)
        references = exact_nonnegative_integer(
            self.mandatory_reference_utf8_bytes,
            policy=True,
        )
        depth = exact_nonnegative_integer(self.max_delta_depth, policy=True)
        if total == 0 or p0 == 0 or p1 == 0 or depth == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)
        if any(cap > total for cap in (p0, p1, p2, p3)):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)
        if p0 + p1 + p2 + p3 > total:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)
        if references > MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)

    @property
    def fingerprint(self) -> str:
        """Return the canonical digest of all planning policy fields."""

        serialized = (
            f"total={self.total_source_utf8_bytes};"
            f"p0={self.p0_source_utf8_bytes};"
            f"p1={self.p1_source_utf8_bytes};"
            f"p2={self.p2_source_utf8_bytes};"
            f"p3={self.p3_source_utf8_bytes};"
            f"references={self.mandatory_reference_utf8_bytes};"
            f"depth={self.max_delta_depth}"
        ).encode("ascii")
        return body_digest(serialized)


DEFAULT_CONTEXT_PLANNING_POLICY = ContextPlanningPolicy()


@dataclass(frozen=True, slots=True)
class ContextPlanningConditions:
    """Current compatibility facts used to evaluate one checkpoint."""

    scope: ContextScope
    provider_id: str
    definition_fingerprint: str
    policy_fingerprint: str
    schema_version: int = CONTEXT_CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        safe_identity(self.provider_id)
        _safe_digest(self.definition_fingerprint)
        _safe_digest(self.policy_fingerprint)
        schema = exact_nonnegative_integer(self.schema_version)
        if schema == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


__all__ = [
    'CONTEXT_CHECKPOINT_SCHEMA_VERSION',
    'MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES',
    'MAX_RECORD_ALIAS',
    'ContextDeliveryMode',
    'ContextCheckpointState',
    'ContextBootstrapReason',
    'ContextDecisionKind',
    'ContextPlanningErrorCode',
    'ContextPlanningError',
    'exact_nonnegative_integer',
    'safe_identity',
    'body_digest',
    'validate_source_alias',
    'validate_record_alias',
    'context_source_uses_catalog_alias',
    'ContextRecordCursor',
    'ContextRecordMaterial',
    'ContextMaterial',
    'MandatoryReferenceReservation',
    'ContextPlanningPolicy',
    'DEFAULT_CONTEXT_PLANNING_POLICY',
    'ContextPlanningConditions',
]
