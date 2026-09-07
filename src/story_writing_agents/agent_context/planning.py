"""Pure provider-neutral selection and checkpoint planning for agent context."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from .manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    validate_context_history_coverage,
    context_manifest_entry_key,
    validate_context_digest,
    validate_context_identity,
)

__all__ = [
    "CONTEXT_CHECKPOINT_SCHEMA_VERSION",
    "DEFAULT_CONTEXT_PLANNING_POLICY",
    "MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES",
    "ContextBootstrapReason",
    "ContextBudgetAccounting",
    "ContextCheckpointProposal",
    "ContextCheckpointSource",
    "ContextCheckpointState",
    "ContextCheckpointView",
    "ContextDecisionAccounting",
    "ContextDecisionKind",
    "ContextDeliveryMode",
    "ContextMaterial",
    "ContextPlanningConditions",
    "ContextPlanningError",
    "ContextPlanningErrorCode",
    "ContextPlanningPolicy",
    "ContextProjectedUnit",
    "ContextProjection",
    "ContextProjectionAccounting",
    "ContextReferenceBudgetUsage",
    "ContextSearchableAccounting",
    "ContextSourceBudgetUsage",
    "ContextSourceDecision",
    "MandatoryReferenceReservation",
    "plan_context_projection",
]

CONTEXT_CHECKPOINT_SCHEMA_VERSION = 1
MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES = 262_144
_MAX_RECORD_ALIAS = (1 << 53) - 1


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


def _exact_nonnegative_integer(value: object, *, policy: bool = False) -> int:
    """Return an exact nonnegative integer or raise one fixed failure."""

    if type(value) is not int or value < 0:
        raise ContextPlanningError(
            ContextPlanningErrorCode.INVALID_POLICY
            if policy
            else ContextPlanningErrorCode.INVALID_VALUE
        )
    assert isinstance(value, int)
    return value


def _safe_identity(value: object, *, checkpoint: bool = False) -> str:
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


def _body_digest(body_utf8: bytes) -> str:
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
            _safe_identity(first_id, checkpoint=checkpoint)
            _safe_identity(last_id, checkpoint=checkpoint)
        except ContextPlanningError:
            raise ContextPlanningError(error) from None
    elif first_id is not None or last_id is not None:
        raise ContextPlanningError(error)


def _validate_source_alias(value: object, *, allow_empty: bool = False) -> str:
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


def _validate_record_alias(value: object, *, checkpoint: bool = False) -> int:
    """Validate one JSON-exact positive per-source delivery alias."""

    if type(value) is not int or value < 1 or value > _MAX_RECORD_ALIAS:
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
        _safe_identity(self.record_id)
        _validate_record_boundary(
            self.boundary_kind,
            self.boundary_start,
            self.boundary_end,
            self.boundary_first_id,
            self.boundary_last_id,
        )
        _safe_digest(self.content_digest)
        _exact_nonnegative_integer(self.utf8_bytes)

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
            or _body_digest(self.body_utf8) != self.cursor.content_digest
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
        if _body_digest(self.body_utf8) != self.entry.content_digest:
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
        ordinal = _exact_nonnegative_integer(self.ordinal)
        if ordinal == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        _safe_identity(self.reference_id)
        _safe_identity(self.source_id)
        _safe_identity(self.revision)
        if type(self.provenance) is not ContextProvenance:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if (
            self.provenance.authority_kind
            is not ContextAuthorityKind.REFERENCE_AUTHORIZATION
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        _safe_digest(self.content_digest)
        _exact_nonnegative_integer(self.utf8_bytes)


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
        total = _exact_nonnegative_integer(
            self.total_source_utf8_bytes,
            policy=True,
        )
        p0 = _exact_nonnegative_integer(self.p0_source_utf8_bytes, policy=True)
        p1 = _exact_nonnegative_integer(self.p1_source_utf8_bytes, policy=True)
        p2 = _exact_nonnegative_integer(self.p2_source_utf8_bytes, policy=True)
        p3 = _exact_nonnegative_integer(self.p3_source_utf8_bytes, policy=True)
        references = _exact_nonnegative_integer(
            self.mandatory_reference_utf8_bytes,
            policy=True,
        )
        depth = _exact_nonnegative_integer(self.max_delta_depth, policy=True)
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
        return _body_digest(serialized)


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
        _safe_identity(self.provider_id)
        _safe_digest(self.definition_fingerprint)
        _safe_digest(self.policy_fingerprint)
        schema = _exact_nonnegative_integer(self.schema_version)
        if schema == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextCheckpointRecord:
    """Content-free source record paired with its stable delivery alias."""

    cursor: ContextRecordCursor
    record_alias: int

    def __post_init__(self) -> None:
        if type(self.cursor) is not ContextRecordCursor:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        _validate_record_alias(self.record_alias, checkpoint=True)


@dataclass(frozen=True, slots=True)
class ContextCheckpointSource:
    """Complete content-free metadata for one accepted checkpoint source."""

    entry: ContextManifestEntry
    records: tuple[ContextCheckpointRecord, ...] = ()

    def __post_init__(self) -> None:
        if type(self.entry) is not ContextManifestEntry:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if self.entry.priority is ContextPriority.P0_REQUIRED:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if type(self.records) is not tuple or any(
            type(record) is not ContextCheckpointRecord for record in self.records
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if self.records and not context_source_uses_catalog_alias(self.entry):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        record_ids = tuple(record.cursor.record_id for record in self.records)
        record_aliases = tuple(record.record_alias for record in self.records)
        if (
            len(set(record_ids)) != len(record_ids)
            or len(set(record_aliases)) != len(record_aliases)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)


@dataclass(frozen=True, slots=True)
class ContextCatalogCursor:
    """Content-free stable alias for one current readable catalog source."""

    entry: ContextManifestEntry
    source_alias: str
    record_alias_high_watermark: int = 0

    def __post_init__(self) -> None:
        if not context_source_uses_catalog_alias(self.entry):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        _validate_source_alias(self.source_alias)
        if (
            type(self.record_alias_high_watermark) is not int
            or self.record_alias_high_watermark < 0
            or self.record_alias_high_watermark > _MAX_RECORD_ALIAS
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)


def _catalog_covers_checkpoint_records(
    sources: tuple[ContextCheckpointSource, ...],
    catalog_sources: tuple[ContextCatalogCursor, ...],
) -> bool:
    """Return whether every delivery alias is valid in its source epoch."""

    catalog_by_id = {
        source.entry.source_id: source for source in catalog_sources
    }
    for source in sources:
        if not context_source_uses_catalog_alias(source.entry):
            if source.records:
                return False
            continue
        catalog = catalog_by_id.get(source.entry.source_id)
        if catalog is None or catalog.entry != source.entry:
            return False
        if any(
            record.record_alias > catalog.record_alias_high_watermark
            for record in source.records
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class ContextProjectedRecord:
    """One changed transient record addressed by its stable source alias."""

    source_alias: str
    record: ContextCheckpointRecord
    body_utf8: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _validate_source_alias(self.source_alias)
        if type(self.record) is not ContextCheckpointRecord:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        ContextRecordMaterial(self.record.cursor, self.body_utf8)


@dataclass(frozen=True, slots=True)
class ContextRecordTombstone:
    """Content-free removal of one prior record from a retained source."""

    source_alias: str
    record_alias: int
    record_id: str

    def __post_init__(self) -> None:
        _validate_source_alias(self.source_alias)
        _validate_record_alias(self.record_alias)
        _safe_identity(self.record_id)


@dataclass(frozen=True, slots=True)
class ContextCheckpointView:
    """Read-only authoritative checkpoint facts supplied to the pure planner."""

    state: ContextCheckpointState
    conditions: ContextPlanningConditions | None = None
    provider_thread_id: str | None = None
    sources: tuple[ContextCheckpointSource, ...] = ()
    delta_depth: int | None = None
    catalog_sources: tuple[ContextCatalogCursor, ...] = ()
    source_alias_high_watermark: int = 0
    session_history_coverage: tuple[tuple[str, int, str], ...] = ()

    def __post_init__(self) -> None:
        validate_context_history_coverage(self.session_history_coverage)
        if type(self.state) is not ContextCheckpointState:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if self.state is not ContextCheckpointState.READY:
            if (
                self.conditions is not None
                or self.provider_thread_id is not None
                or self.sources
                or self.delta_depth is not None
                or self.catalog_sources
                or self.source_alias_high_watermark != 0
                or self.session_history_coverage
            ):
                raise ContextPlanningError(
                    ContextPlanningErrorCode.INVALID_CHECKPOINT
                )
            return
        if type(self.conditions) is not ContextPlanningConditions:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if self.provider_thread_id is not None:
            _safe_identity(self.provider_thread_id, checkpoint=True)
        if type(self.sources) is not tuple or any(
            type(source) is not ContextCheckpointSource for source in self.sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if (
            type(self.delta_depth) is not int
            or self.delta_depth < 0
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        source_ids = tuple(source.entry.source_id for source in self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if type(self.catalog_sources) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.catalog_sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        catalog_ids = tuple(
            source.entry.source_id for source in self.catalog_sources
        )
        source_aliases = tuple(
            source.source_alias for source in self.catalog_sources
        )
        if (
            len(set(catalog_ids)) != len(catalog_ids)
            or len(set(source_aliases)) != len(source_aliases)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        if not _catalog_covers_checkpoint_records(
            self.sources,
            self.catalog_sources,
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        alias_ordinals = tuple(
            int(alias[1:]) for alias in source_aliases if alias
        )
        if (
            type(self.source_alias_high_watermark) is not int
            or self.source_alias_high_watermark < max(alias_ordinals, default=0)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        object.__setattr__(
            self,
            "sources",
            tuple(
                sorted(
                    self.sources,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            ),
        )
        object.__setattr__(
            self,
            "catalog_sources",
            tuple(
                sorted(
                    self.catalog_sources,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextProjectedUnit:
    """One unique transient body with every represented manifest pointer."""

    entries: tuple[ContextManifestEntry, ...]
    body_utf8: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.entries) is not tuple
            or not self.entries
            or any(type(entry) is not ContextManifestEntry for entry in self.entries)
            or type(self.body_utf8) is not bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        valid_utf8 = True
        try:
            self.body_utf8.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            valid_utf8 = False
        if not valid_utf8:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        ordered = tuple(sorted(self.entries, key=context_manifest_entry_key))
        if len({entry.source_id for entry in ordered}) != len(ordered):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        first = ordered[0]
        if any(
            entry.priority is not first.priority
            or entry.semantic_channel is not first.semantic_channel
            or entry.utf8_bytes != len(self.body_utf8)
            or entry.content_digest != _body_digest(self.body_utf8)
            for entry in ordered
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if first.priority in {
            ContextPriority.P0_REQUIRED,
            ContextPriority.P1_STABLE,
        } and len(ordered) != 1:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        object.__setattr__(self, "entries", ordered)


def _decision_allowed(
    priority: ContextPriority,
    semantic_channel: ContextSemanticChannel,
    decision: ContextDecisionKind,
) -> bool:
    """Return whether one priority/channel can produce a decision kind."""

    if priority is ContextPriority.P0_REQUIRED:
        return (
            semantic_channel
            in {
                ContextSemanticChannel.CURRENT_INPUT,
                ContextSemanticChannel.CANDIDATE,
            }
            and decision is ContextDecisionKind.SELECTED
        )
    if priority is ContextPriority.P1_STABLE:
        return (
            semantic_channel is ContextSemanticChannel.STABLE_BEHAVIOR
            and decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            }
        )
    if priority is ContextPriority.P2_WORKING:
        if semantic_channel not in {
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextSemanticChannel.FOCUSED_EVIDENCE,
            ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,
        }:
            return False
        return not (
            semantic_channel is ContextSemanticChannel.HARD_CONSTRAINT
            and decision
            in {
                ContextDecisionKind.OMITTED_CLASS_BUDGET,
                ContextDecisionKind.OMITTED_MATERIAL,
            }
        )
    return (
        priority is ContextPriority.P3_OPTIONAL
        and semantic_channel
        in {
            ContextSemanticChannel.OPTIONAL_BACKGROUND,
            ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,
        }
    )


@dataclass(frozen=True, slots=True)
class ContextSourceDecision:
    """Content-free projection outcome for one current manifest entry."""

    entry: ContextManifestEntry
    decision: ContextDecisionKind
    represented_by_source_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.entry) is not ContextManifestEntry:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.decision) is not ContextDecisionKind:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if not _decision_allowed(
            self.entry.priority,
            self.entry.semantic_channel,
            self.decision,
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.decision is ContextDecisionKind.DEDUPLICATED:
            if self.represented_by_source_id is None:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
            _safe_identity(self.represented_by_source_id)
            if self.represented_by_source_id == self.entry.source_id:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        elif self.represented_by_source_id is not None:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)

    @property
    def searchable(self) -> bool:
        """Return the source-owned search availability for this decision."""

        return self.entry.searchable


@dataclass(frozen=True, slots=True)
class ContextDecisionAccounting:
    """Content-free counts and raw bytes for one decision bucket."""

    priority: ContextPriority
    semantic_channel: ContextSemanticChannel
    decision: ContextDecisionKind
    source_count: int
    raw_source_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.priority) is not ContextPriority:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.semantic_channel) is not ContextSemanticChannel:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.decision) is not ContextDecisionKind:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if not _decision_allowed(
            self.priority,
            self.semantic_channel,
            self.decision,
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        count = _exact_nonnegative_integer(self.source_count)
        _exact_nonnegative_integer(self.raw_source_utf8_bytes)
        if count == 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextSearchableAccounting:
    """Exact partition of all searchable logical source pointers and bytes."""

    total_source_count: int
    total_source_utf8_bytes: int
    selected_primary_count: int
    selected_primary_utf8_bytes: int
    deduplicated_pointer_count: int
    deduplicated_pointer_utf8_bytes: int
    checkpoint_suppressed_count: int
    checkpoint_suppressed_utf8_bytes: int
    class_budget_omitted_count: int
    class_budget_omitted_utf8_bytes: int
    material_omitted_count: int
    material_omitted_utf8_bytes: int
    selected_source_count: int
    selected_source_utf8_bytes: int
    unprojected_source_count: int
    unprojected_source_utf8_bytes: int

    def __post_init__(self) -> None:
        values = (
            self.total_source_count,
            self.total_source_utf8_bytes,
            self.selected_primary_count,
            self.selected_primary_utf8_bytes,
            self.deduplicated_pointer_count,
            self.deduplicated_pointer_utf8_bytes,
            self.checkpoint_suppressed_count,
            self.checkpoint_suppressed_utf8_bytes,
            self.class_budget_omitted_count,
            self.class_budget_omitted_utf8_bytes,
            self.material_omitted_count,
            self.material_omitted_utf8_bytes,
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.unprojected_source_count,
            self.unprojected_source_utf8_bytes,
        )
        for value in values:
            _exact_nonnegative_integer(value)
        partition_counts = (
            self.selected_primary_count,
            self.deduplicated_pointer_count,
            self.checkpoint_suppressed_count,
            self.class_budget_omitted_count,
            self.material_omitted_count,
        )
        partition_bytes = (
            self.selected_primary_utf8_bytes,
            self.deduplicated_pointer_utf8_bytes,
            self.checkpoint_suppressed_utf8_bytes,
            self.class_budget_omitted_utf8_bytes,
            self.material_omitted_utf8_bytes,
        )
        if sum(partition_counts) != self.total_source_count:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(partition_bytes) != self.total_source_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.selected_source_count != (
            self.selected_primary_count + self.deduplicated_pointer_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.selected_source_utf8_bytes != (
            self.selected_primary_utf8_bytes
            + self.deduplicated_pointer_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.unprojected_source_count != (
            self.checkpoint_suppressed_count
            + self.class_budget_omitted_count
            + self.material_omitted_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.unprojected_source_utf8_bytes != (
            self.checkpoint_suppressed_utf8_bytes
            + self.class_budget_omitted_utf8_bytes
            + self.material_omitted_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextSourceBudgetUsage:
    """Configured, selected-unique, and remaining source-body bytes."""

    configured_utf8_bytes: int
    selected_unique_utf8_bytes: int
    remaining_utf8_bytes: int

    def __post_init__(self) -> None:
        configured = _exact_nonnegative_integer(self.configured_utf8_bytes)
        selected = _exact_nonnegative_integer(self.selected_unique_utf8_bytes)
        remaining = _exact_nonnegative_integer(self.remaining_utf8_bytes)
        if selected > configured or selected + remaining != configured:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReferenceBudgetUsage:
    """Configured, used, and remaining mandatory-reference reservation bytes."""

    configured_utf8_bytes: int
    used_utf8_bytes: int
    remaining_utf8_bytes: int

    def __post_init__(self) -> None:
        configured = _exact_nonnegative_integer(self.configured_utf8_bytes)
        used = _exact_nonnegative_integer(self.used_utf8_bytes)
        remaining = _exact_nonnegative_integer(self.remaining_utf8_bytes)
        if used > configured or used + remaining != configured:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextBudgetAccounting:
    """Self-contained source-class and reference-reservation budget usage."""

    total: ContextSourceBudgetUsage
    p0: ContextSourceBudgetUsage
    p1: ContextSourceBudgetUsage
    p2: ContextSourceBudgetUsage
    p3: ContextSourceBudgetUsage
    mandatory_references: ContextReferenceBudgetUsage

    def __post_init__(self) -> None:
        usages = (
            self.total,
            self.p0,
            self.p1,
            self.p2,
            self.p3,
            self.mandatory_references,
        )
        if any(
            type(usage) is not ContextSourceBudgetUsage for usage in usages[:5]
        ) or type(self.mandatory_references) is not ContextReferenceBudgetUsage:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if any(
            usage.configured_utf8_bytes == 0
            for usage in (self.total, self.p0, self.p1)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if (
            self.mandatory_references.configured_utf8_bytes
            > MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            usage.configured_utf8_bytes for usage in usages[1:5]
        ) > self.total.configured_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.total.selected_unique_utf8_bytes != sum(
            usage.selected_unique_utf8_bytes for usage in usages[1:5]
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextProjectionAccounting:
    """Content-free exact raw-source accounting for one pure projection."""

    mode: ContextDeliveryMode
    bootstrap_reason: ContextBootstrapReason | None
    proposed_delta_depth: int
    logical_source_count: int
    logical_source_utf8_bytes: int
    selected_logical_source_count: int
    selected_logical_utf8_bytes: int
    projected_unit_count: int
    projected_unique_utf8_bytes: int
    saved_duplicate_utf8_bytes: int
    mandatory_reference_count: int
    mandatory_reference_utf8_bytes: int
    removed_checkpoint_source_count: int
    removed_checkpoint_source_utf8_bytes: int
    searchable: ContextSearchableAccounting
    budgets: ContextBudgetAccounting
    decision_buckets: tuple[ContextDecisionAccounting, ...]

    def __post_init__(self) -> None:
        if type(self.mode) is not ContextDeliveryMode:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.mode is ContextDeliveryMode.BOOTSTRAP:
            if type(self.bootstrap_reason) is not ContextBootstrapReason:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
            if self.proposed_delta_depth != 0:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        elif self.bootstrap_reason is not None:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        elif type(self.proposed_delta_depth) is not int or (
            self.proposed_delta_depth <= 0
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        integer_values = (
            self.proposed_delta_depth,
            self.logical_source_count,
            self.logical_source_utf8_bytes,
            self.selected_logical_source_count,
            self.selected_logical_utf8_bytes,
            self.projected_unit_count,
            self.projected_unique_utf8_bytes,
            self.saved_duplicate_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
            self.removed_checkpoint_source_count,
            self.removed_checkpoint_source_utf8_bytes,
        )
        for value in integer_values:
            _exact_nonnegative_integer(value)
        if type(self.searchable) is not ContextSearchableAccounting:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.budgets) is not ContextBudgetAccounting:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.decision_buckets) is not tuple or any(
            type(bucket) is not ContextDecisionAccounting
            for bucket in self.decision_buckets
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        bucket_keys = tuple(
            (bucket.priority, bucket.semantic_channel, bucket.decision)
            for bucket in self.decision_buckets
        )
        if len(set(bucket_keys)) != len(bucket_keys):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        canonical_bucket_keys = tuple(
            sorted(
                bucket_keys,
                key=lambda key: (
                    tuple(ContextPriority).index(key[0]),
                    tuple(ContextSemanticChannel).index(key[1]),
                    tuple(ContextDecisionKind).index(key[2]),
                ),
            )
        )
        if bucket_keys != canonical_bucket_keys:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(bucket.source_count for bucket in self.decision_buckets) != (
            self.logical_source_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            bucket.raw_source_utf8_bytes for bucket in self.decision_buckets
        ) != self.logical_source_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        selected_buckets = tuple(
            bucket
            for bucket in self.decision_buckets
            if bucket.decision in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.DEDUPLICATED,
            }
        )
        selected_primary_buckets = tuple(
            bucket
            for bucket in self.decision_buckets
            if bucket.decision is ContextDecisionKind.SELECTED
        )
        deduplicated_buckets = tuple(
            bucket
            for bucket in self.decision_buckets
            if bucket.decision is ContextDecisionKind.DEDUPLICATED
        )
        if sum(
            bucket.source_count for bucket in selected_primary_buckets
        ) != self.projected_unit_count:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            bucket.raw_source_utf8_bytes
            for bucket in selected_primary_buckets
        ) != self.projected_unique_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            bucket.raw_source_utf8_bytes for bucket in deduplicated_buckets
        ) != self.saved_duplicate_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(bucket.source_count for bucket in selected_buckets) != (
            self.selected_logical_source_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            bucket.raw_source_utf8_bytes for bucket in selected_buckets
        ) != self.selected_logical_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.selected_logical_utf8_bytes != (
            self.projected_unique_utf8_bytes
            + self.saved_duplicate_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        searchable_buckets = tuple(
            bucket
            for bucket in self.decision_buckets
            if bucket.priority in {
                ContextPriority.P2_WORKING,
                ContextPriority.P3_OPTIONAL,
            }
        )
        searchable_partitions = (
            (
                ContextDecisionKind.SELECTED,
                self.searchable.selected_primary_count,
                self.searchable.selected_primary_utf8_bytes,
            ),
            (
                ContextDecisionKind.DEDUPLICATED,
                self.searchable.deduplicated_pointer_count,
                self.searchable.deduplicated_pointer_utf8_bytes,
            ),
            (
                ContextDecisionKind.CHECKPOINT_SUPPRESSED,
                self.searchable.checkpoint_suppressed_count,
                self.searchable.checkpoint_suppressed_utf8_bytes,
            ),
            (
                ContextDecisionKind.OMITTED_CLASS_BUDGET,
                self.searchable.class_budget_omitted_count,
                self.searchable.class_budget_omitted_utf8_bytes,
            ),
            (
                ContextDecisionKind.OMITTED_MATERIAL,
                self.searchable.material_omitted_count,
                self.searchable.material_omitted_utf8_bytes,
            ),
        )
        for decision, expected_count, expected_bytes in searchable_partitions:
            matching = tuple(
                bucket
                for bucket in searchable_buckets
                if bucket.decision is decision
            )
            if sum(bucket.source_count for bucket in matching) != expected_count:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
            if sum(
                bucket.raw_source_utf8_bytes for bucket in matching
            ) != expected_bytes:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(bucket.source_count for bucket in searchable_buckets) != (
            self.searchable.total_source_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            bucket.raw_source_utf8_bytes for bucket in searchable_buckets
        ) != self.searchable.total_source_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.budgets.total.selected_unique_utf8_bytes != (
            self.projected_unique_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.budgets.mandatory_references.used_utf8_bytes != (
            self.mandatory_reference_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextCheckpointProposal:
    """Content-free accepted source set proposed for durable success handling."""

    conditions: ContextPlanningConditions
    sources: tuple[ContextCheckpointSource, ...]
    delta_depth: int
    catalog_sources: tuple[ContextCatalogCursor, ...] = ()
    source_alias_high_watermark: int = 0
    session_history_coverage: tuple[tuple[str, int, str], ...] = ()

    def __post_init__(self) -> None:
        validate_context_history_coverage(self.session_history_coverage)
        if type(self.conditions) is not ContextPlanningConditions:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.sources) is not tuple or any(
            type(source) is not ContextCheckpointSource for source in self.sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        _exact_nonnegative_integer(self.delta_depth)
        source_ids = tuple(source.entry.source_id for source in self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.catalog_sources) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.catalog_sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        catalog_ids = tuple(
            source.entry.source_id for source in self.catalog_sources
        )
        source_aliases = tuple(
            source.source_alias for source in self.catalog_sources
        )
        if (
            len(set(catalog_ids)) != len(catalog_ids)
            or len(set(source_aliases)) != len(source_aliases)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if not _catalog_covers_checkpoint_records(
            self.sources,
            self.catalog_sources,
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        alias_ordinals = tuple(
            int(alias[1:]) for alias in source_aliases if alias
        )
        if (
            type(self.source_alias_high_watermark) is not int
            or self.source_alias_high_watermark < max(alias_ordinals, default=0)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        object.__setattr__(
            self,
            "sources",
            tuple(
                sorted(
                    self.sources,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            ),
        )
        object.__setattr__(
            self,
            "catalog_sources",
            tuple(
                sorted(
                    self.catalog_sources,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextProjection:
    """Deterministic transient context delivery and checkpoint proposal."""

    mode: ContextDeliveryMode
    bootstrap_reason: ContextBootstrapReason | None
    units: tuple[ContextProjectedUnit, ...]
    mandatory_references: tuple[MandatoryReferenceReservation, ...]
    decisions: tuple[ContextSourceDecision, ...]
    removed_checkpoint_sources: tuple[ContextCheckpointSource, ...]
    accounting: ContextProjectionAccounting
    checkpoint_proposal: ContextCheckpointProposal
    projected_records: tuple[ContextProjectedRecord, ...] = ()
    removed_checkpoint_records: tuple[ContextRecordTombstone, ...] = ()
    catalog_updates: tuple[ContextCatalogCursor, ...] = ()
    removed_catalog_sources: tuple[ContextCatalogCursor, ...] = ()

    def __post_init__(self) -> None:
        if type(self.mode) is not ContextDeliveryMode:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.units) is not tuple or any(
            type(unit) is not ContextProjectedUnit for unit in self.units
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.mandatory_references) is not tuple or any(
            type(reference) is not MandatoryReferenceReservation
            for reference in self.mandatory_references
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.decisions) is not tuple or any(
            type(decision) is not ContextSourceDecision
            for decision in self.decisions
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.removed_checkpoint_sources) is not tuple or any(
            type(source) is not ContextCheckpointSource
            for source in self.removed_checkpoint_sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.accounting) is not ContextProjectionAccounting:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.checkpoint_proposal) is not ContextCheckpointProposal:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.projected_records) is not tuple or any(
            type(record) is not ContextProjectedRecord
            for record in self.projected_records
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.removed_checkpoint_records) is not tuple or any(
            type(record) is not ContextRecordTombstone
            for record in self.removed_checkpoint_records
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.catalog_updates) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.catalog_updates
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if type(self.removed_catalog_sources) is not tuple or any(
            type(source) is not ContextCatalogCursor
            for source in self.removed_catalog_sources
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        projected_record_keys = tuple(
            (record.source_alias, record.record.cursor.record_id)
            for record in self.projected_records
        )
        removed_record_keys = tuple(
            (record.source_alias, record.record_id)
            for record in self.removed_checkpoint_records
        )
        if (
            len(set(projected_record_keys)) != len(projected_record_keys)
            or len(set(removed_record_keys)) != len(removed_record_keys)
            or set(projected_record_keys).intersection(removed_record_keys)
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.accounting.mode is not self.mode:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.accounting.bootstrap_reason is not self.bootstrap_reason:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.mode is ContextDeliveryMode.BOOTSTRAP:
            if self.checkpoint_proposal.delta_depth != 0:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        elif self.checkpoint_proposal.delta_depth <= 0:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if len(self.units) != self.accounting.projected_unit_count:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if len(self.decisions) != self.accounting.logical_source_count:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        decision_source_ids = tuple(
            decision.entry.source_id for decision in self.decisions
        )
        if len(set(decision_source_ids)) != len(decision_source_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.decisions != tuple(
            sorted(
                self.decisions,
                key=lambda decision: context_manifest_entry_key(decision.entry),
            )
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if _decision_accounting(self.decisions) != (
            self.accounting.decision_buckets
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.units != tuple(
            sorted(
                self.units,
                key=lambda unit: context_manifest_entry_key(unit.entries[0]),
            )
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        decision_by_source = {
            decision.entry.source_id: decision for decision in self.decisions
        }
        unit_source_ids = tuple(
            entry.source_id for unit in self.units for entry in unit.entries
        )
        if len(set(unit_source_ids)) != len(unit_source_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        projected_decision_ids = {
            decision.entry.source_id
            for decision in self.decisions
            if decision.decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.DEDUPLICATED,
            }
        }
        if set(unit_source_ids) != projected_decision_ids:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        for unit in self.units:
            if any(
                decision_by_source[entry.source_id].entry != entry
                for entry in unit.entries
            ):
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
            primary_source_id = unit.entries[0].source_id
            primary_decision = decision_by_source[primary_source_id]
            if primary_decision.decision is not ContextDecisionKind.SELECTED:
                raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
            for alias in unit.entries[1:]:
                alias_decision = decision_by_source[alias.source_id]
                if (
                    alias_decision.decision
                    is not ContextDecisionKind.DEDUPLICATED
                    or alias_decision.represented_by_source_id
                    != primary_source_id
                ):
                    raise ContextPlanningError(
                        ContextPlanningErrorCode.INVALID_VALUE
                    )
        if sum(
            decision.entry.utf8_bytes for decision in self.decisions
        ) != self.accounting.logical_source_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(len(unit.body_utf8) for unit in self.units) != (
            self.accounting.projected_unique_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        actual_by_priority = {
            priority: sum(
                len(unit.body_utf8)
                for unit in self.units
                if unit.entries[0].priority is priority
            )
            for priority in ContextPriority
        }
        budget_by_priority = {
            ContextPriority.P0_REQUIRED: self.accounting.budgets.p0,
            ContextPriority.P1_STABLE: self.accounting.budgets.p1,
            ContextPriority.P2_WORKING: self.accounting.budgets.p2,
            ContextPriority.P3_OPTIONAL: self.accounting.budgets.p3,
        }
        if any(
            actual_by_priority[priority]
            != budget_by_priority[priority].selected_unique_utf8_bytes
            for priority in ContextPriority
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if len(self.mandatory_references) != (
            self.accounting.mandatory_reference_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            reference.utf8_bytes for reference in self.mandatory_references
        ) != self.accounting.mandatory_reference_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if tuple(
            reference.ordinal for reference in self.mandatory_references
        ) != tuple(range(1, len(self.mandatory_references) + 1)):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        reference_ids = tuple(
            reference.reference_id for reference in self.mandatory_references
        )
        if len(set(reference_ids)) != len(reference_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if (
            self.accounting.mandatory_reference_utf8_bytes
            > self.accounting.budgets.mandatory_references.configured_utf8_bytes
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.accounting.proposed_delta_depth != (
            self.checkpoint_proposal.delta_depth
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        expected_proposal_entries = tuple(
            decision.entry
            for decision in self.decisions
            if decision.entry.priority is not ContextPriority.P0_REQUIRED
            and decision.decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.DEDUPLICATED,
                ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            }
        )
        if tuple(
            source.entry for source in self.checkpoint_proposal.sources
        ) != expected_proposal_entries:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        expected_catalog_entries = tuple(
            decision.entry
            for decision in self.decisions
            if context_source_uses_catalog_alias(decision.entry)
        )
        proposal_catalog = self.checkpoint_proposal.catalog_sources
        if (
            tuple(source.entry for source in proposal_catalog)
            != expected_catalog_entries
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        proposal_aliases = tuple(
            source.source_alias for source in proposal_catalog
        )
        if (
            any(
                record.source_alias not in proposal_aliases
                for record in self.projected_records
            )
            or any(
                record.source_alias not in proposal_aliases
                for record in self.removed_checkpoint_records
            )
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        proposal_catalog_set = set(proposal_catalog)
        if (
            len(set(self.catalog_updates)) != len(self.catalog_updates)
            or any(source not in proposal_catalog_set for source in self.catalog_updates)
            or len(set(self.removed_catalog_sources))
            != len(self.removed_catalog_sources)
            or {
                source.entry.source_id for source in self.removed_catalog_sources
            }.intersection(source.entry.source_id for source in proposal_catalog)
            or {
                source.source_alias for source in self.removed_catalog_sources
            }.intersection(proposal_aliases)
            or self.catalog_updates
            != tuple(
                sorted(
                    self.catalog_updates,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            )
            or self.removed_catalog_sources
            != tuple(
                sorted(
                    self.removed_catalog_sources,
                    key=lambda source: context_manifest_entry_key(source.entry),
                )
            )
            or (
                self.mode is ContextDeliveryMode.BOOTSTRAP
                and (
                    self.catalog_updates != proposal_catalog
                    or self.removed_catalog_sources
                )
            )
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        removed_source_ids = tuple(
            source.entry.source_id for source in self.removed_checkpoint_sources
        )
        if len(set(removed_source_ids)) != len(removed_source_ids):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if self.removed_checkpoint_sources != tuple(
            sorted(
                self.removed_checkpoint_sources,
                key=lambda source: context_manifest_entry_key(source.entry),
            )
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if len(self.removed_checkpoint_sources) != (
            self.accounting.removed_checkpoint_source_count
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if sum(
            source.entry.utf8_bytes
            for source in self.removed_checkpoint_sources
        ) != self.accounting.removed_checkpoint_source_utf8_bytes:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


def _bootstrap_reason(
    *,
    manifest: ContextManifest,
    checkpoint: ContextCheckpointView,
    conditions: ContextPlanningConditions,
    active_provider_thread_id: str | None,
    policy: ContextPlanningPolicy,
    fresh: bool,
) -> ContextBootstrapReason | None:
    """Apply the fixed checkpoint compatibility precedence."""

    if fresh:
        return ContextBootstrapReason.EXPLICIT_FRESH
    if checkpoint.state is ContextCheckpointState.MISSING:
        return ContextBootstrapReason.CHECKPOINT_MISSING
    if checkpoint.state is ContextCheckpointState.CORRUPT:
        return ContextBootstrapReason.CHECKPOINT_CORRUPT
    if checkpoint.state is ContextCheckpointState.STALE:
        return ContextBootstrapReason.CHECKPOINT_STALE
    if checkpoint.state is ContextCheckpointState.TAINTED:
        return ContextBootstrapReason.CHECKPOINT_TAINTED
    if checkpoint.state is not ContextCheckpointState.READY:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
    assert checkpoint.conditions is not None
    assert checkpoint.delta_depth is not None
    previous = checkpoint.conditions
    if previous.scope != conditions.scope:
        return ContextBootstrapReason.SCOPE_MISMATCH
    previous_by_source = {
        source.entry.source_id: source.entry for source in checkpoint.sources
    }
    for entry in manifest.entries:
        checkpoint_entry = previous_by_source.get(entry.source_id)
        if checkpoint_entry is None or checkpoint_entry.revision != entry.revision:
            continue
        if (
            checkpoint_entry.content_digest != entry.content_digest
            or checkpoint_entry.utf8_bytes != entry.utf8_bytes
        ):
            raise ContextPlanningError(
                ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH
            )
    if previous.provider_id != conditions.provider_id:
        return ContextBootstrapReason.PROVIDER_MISMATCH
    if (
        checkpoint.provider_thread_id is None
        and active_provider_thread_id is None
    ):
        return ContextBootstrapReason.PROVIDER_CONTEXT_UNAVAILABLE
    if checkpoint.provider_thread_id != active_provider_thread_id:
        return ContextBootstrapReason.PROVIDER_THREAD_MISMATCH
    if previous.definition_fingerprint != conditions.definition_fingerprint:
        return ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH
    previous_stable = tuple(
        sorted(
            (
                entry
                for entry in previous_by_source.values()
                if entry.priority is ContextPriority.P1_STABLE
            ),
            key=context_manifest_entry_key,
        )
    )
    current_stable = tuple(
        entry
        for entry in manifest.entries
        if entry.priority is ContextPriority.P1_STABLE
    )
    if previous_stable != current_stable:
        return ContextBootstrapReason.STABLE_CONTEXT_CHANGED
    if previous.policy_fingerprint != conditions.policy_fingerprint:
        return ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH
    if previous.schema_version != conditions.schema_version:
        return ContextBootstrapReason.SCHEMA_MISMATCH
    if (
        conditions.scope.subject_kind is not ContextSubjectKind.SESSION
        and checkpoint.delta_depth >= policy.max_delta_depth
    ):
        return ContextBootstrapReason.MAX_DELTA_DEPTH
    return None


def _material_map(
    manifest: ContextManifest,
    materials: tuple[ContextMaterial, ...],
) -> dict[str, ContextMaterial]:
    """Validate exact manifest correlation and return material by source identity."""

    if type(materials) is not tuple or any(
        type(material) is not ContextMaterial for material in materials
    ):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)
    by_source: dict[str, ContextMaterial] = {}
    entries = {entry.source_id: entry for entry in manifest.entries}
    for material in materials:
        source_id = material.entry.source_id
        if source_id in by_source or entries.get(source_id) != material.entry:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_MATERIAL)
        by_source[source_id] = material
    return by_source


def _validated_references(
    references: tuple[MandatoryReferenceReservation, ...],
    policy: ContextPlanningPolicy,
) -> tuple[MandatoryReferenceReservation, ...]:
    """Validate explicit order and aggregate byte reservation without I/O."""

    if type(references) is not tuple or any(
        type(reference) is not MandatoryReferenceReservation
        for reference in references
    ):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    if tuple(reference.ordinal for reference in references) != tuple(
        range(1, len(references) + 1)
    ):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    reference_ids = tuple(reference.reference_id for reference in references)
    if len(set(reference_ids)) != len(reference_ids):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    if sum(reference.utf8_bytes for reference in references) > (
        policy.mandatory_reference_utf8_bytes
    ):
        raise ContextPlanningError(
            ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED
        )
    return references


def _exact_body_groups(
    entries: tuple[ContextManifestEntry, ...],
    materials: dict[str, ContextMaterial],
) -> tuple[tuple[tuple[ContextManifestEntry, ...], bytes], ...]:
    """Group eligible entries only after digest bucketing and exact byte equality."""

    groups: list[tuple[list[ContextManifestEntry], bytes]] = []
    for entry in entries:
        material = materials[entry.source_id]
        for grouped_entries, grouped_body in groups:
            representative = grouped_entries[0]
            if (
                representative.priority is entry.priority
                and representative.semantic_channel is entry.semantic_channel
                and representative.content_digest == entry.content_digest
                and representative.utf8_bytes == entry.utf8_bytes
                and grouped_body == material.body_utf8
            ):
                grouped_entries.append(entry)
                break
        else:
            groups.append(([entry], material.body_utf8))
    return tuple(
        (tuple(grouped_entries), grouped_body)
        for grouped_entries, grouped_body in groups
    )


def _selected_group(
    entries: tuple[ContextManifestEntry, ...],
    body_utf8: bytes,
) -> tuple[ContextProjectedUnit, tuple[ContextSourceDecision, ...]]:
    """Construct one unit and content-free selected/deduplicated decisions."""

    primary = entries[0]
    decisions = (
        ContextSourceDecision(primary, ContextDecisionKind.SELECTED),
        *(
            ContextSourceDecision(
                entry,
                ContextDecisionKind.DEDUPLICATED,
                represented_by_source_id=primary.source_id,
            )
            for entry in entries[1:]
        ),
    )
    return ContextProjectedUnit(entries, body_utf8), decisions


def _decision_accounting(
    decisions: tuple[ContextSourceDecision, ...],
) -> tuple[ContextDecisionAccounting, ...]:
    """Aggregate canonical priority/channel/reason buckets."""

    accumulated: dict[
        tuple[ContextPriority, ContextSemanticChannel, ContextDecisionKind],
        tuple[int, int],
    ] = {}
    for decision in decisions:
        key = (
            decision.entry.priority,
            decision.entry.semantic_channel,
            decision.decision,
        )
        count, raw_bytes = accumulated.get(key, (0, 0))
        accumulated[key] = (
            count + 1,
            raw_bytes + decision.entry.utf8_bytes,
        )
    ordered_keys = sorted(
        accumulated,
        key=lambda key: (
            tuple(ContextPriority).index(key[0]),
            tuple(ContextSemanticChannel).index(key[1]),
            tuple(ContextDecisionKind).index(key[2]),
        ),
    )
    return tuple(
        ContextDecisionAccounting(
            priority,
            channel,
            decision,
            accumulated[(priority, channel, decision)][0],
            accumulated[(priority, channel, decision)][1],
        )
        for priority, channel, decision in ordered_keys
    )


def plan_context_projection(
    manifest: ContextManifest,
    materials: tuple[ContextMaterial, ...],
    *,
    policy: ContextPlanningPolicy,
    conditions: ContextPlanningConditions,
    checkpoint: ContextCheckpointView,
    active_provider_thread_id: str | None,
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = (),
    fresh: bool = False,
) -> ContextProjection:
    """Plan one deterministic raw-source projection without performing I/O."""

    if type(manifest) is not ContextManifest:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    if type(policy) is not ContextPlanningPolicy:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)
    if type(conditions) is not ContextPlanningConditions:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    if type(checkpoint) is not ContextCheckpointView:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
    if type(fresh) is not bool:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    if active_provider_thread_id is not None:
        _safe_identity(active_provider_thread_id)
    if manifest.scope != conditions.scope:
        raise ContextPlanningError(
            ContextPlanningErrorCode.MANIFEST_SCOPE_MISMATCH
        )
    if conditions.policy_fingerprint != policy.fingerprint:
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_POLICY)

    material_by_source = _material_map(manifest, materials)
    references = _validated_references(mandatory_references, policy)
    reason = _bootstrap_reason(
        manifest=manifest,
        checkpoint=checkpoint,
        conditions=conditions,
        active_provider_thread_id=active_provider_thread_id,
        policy=policy,
        fresh=fresh,
    )
    ready_previous_by_source = {
        source.entry.source_id: source.entry for source in checkpoint.sources
    }
    current_by_source = {entry.source_id: entry for entry in manifest.entries}

    mode = (
        ContextDeliveryMode.BOOTSTRAP
        if reason is not None
        else ContextDeliveryMode.DELTA
    )
    previous_by_source = (
        ready_previous_by_source
        if mode is ContextDeliveryMode.DELTA
        else {}
    )

    decisions_by_source: dict[str, ContextSourceDecision] = {}
    units: list[ContextProjectedUnit] = []
    used_total = 0
    used_p0 = 0
    used_p1 = 0
    used_p2 = 0
    used_p3 = 0

    if mode is ContextDeliveryMode.DELTA:
        for entry in manifest.entries:
            if entry.priority is ContextPriority.P0_REQUIRED:
                continue
            if previous_by_source.get(entry.source_id) == entry:
                decisions_by_source[entry.source_id] = ContextSourceDecision(
                    entry,
                    ContextDecisionKind.CHECKPOINT_SUPPRESSED,
                )

    required_entries = tuple(
        entry
        for entry in manifest.entries
        if entry.priority is ContextPriority.P0_REQUIRED
    )
    for entry in required_entries:
        material = material_by_source.get(entry.source_id)
        if material is None:
            raise ContextPlanningError(
                ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING
            )
        if (
            used_total + entry.utf8_bytes > policy.total_source_utf8_bytes
            or sum(item.utf8_bytes for item in required_entries)
            > policy.p0_source_utf8_bytes
        ):
            raise ContextPlanningError(
                ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED
            )
        units.append(ContextProjectedUnit((entry,), material.body_utf8))
        decisions_by_source[entry.source_id] = ContextSourceDecision(
            entry,
            ContextDecisionKind.SELECTED,
        )
        used_total += entry.utf8_bytes
        used_p0 += entry.utf8_bytes

    stable_entries = tuple(
        entry
        for entry in manifest.entries
        if entry.priority is ContextPriority.P1_STABLE
        and entry.source_id not in decisions_by_source
    )
    stable_bytes = sum(entry.utf8_bytes for entry in stable_entries)
    if stable_bytes > policy.p1_source_utf8_bytes or (
        used_total + stable_bytes > policy.total_source_utf8_bytes
    ):
        raise ContextPlanningError(
            ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED
        )
    for entry in stable_entries:
        material = material_by_source.get(entry.source_id)
        if material is None:
            raise ContextPlanningError(
                ContextPlanningErrorCode.STABLE_MATERIAL_MISSING
            )
        units.append(ContextProjectedUnit((entry,), material.body_utf8))
        decisions_by_source[entry.source_id] = ContextSourceDecision(
            entry,
            ContextDecisionKind.SELECTED,
        )
        used_total += entry.utf8_bytes
        used_p1 += entry.utf8_bytes

    selectable_by_priority: dict[ContextPriority, list[ContextManifestEntry]] = {
        ContextPriority.P2_WORKING: [],
        ContextPriority.P3_OPTIONAL: [],
    }
    for entry in manifest.entries:
        if entry.priority not in selectable_by_priority:
            continue
        if entry.source_id in decisions_by_source:
            continue
        material = material_by_source.get(entry.source_id)
        if material is None:
            if entry.semantic_channel is ContextSemanticChannel.HARD_CONSTRAINT:
                raise ContextPlanningError(
                    ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING
                )
            decisions_by_source[entry.source_id] = ContextSourceDecision(
                entry,
                ContextDecisionKind.OMITTED_MATERIAL,
            )
            continue
        selectable_by_priority[entry.priority].append(entry)

    p2_groups = _exact_body_groups(
        tuple(selectable_by_priority[ContextPriority.P2_WORKING]),
        material_by_source,
    )
    hard_groups = tuple(
        group
        for group in p2_groups
        if group[0][0].semantic_channel is ContextSemanticChannel.HARD_CONSTRAINT
    )
    hard_bytes = sum(len(body_utf8) for _, body_utf8 in hard_groups)
    if hard_bytes > policy.p2_source_utf8_bytes or (
        used_total + hard_bytes > policy.total_source_utf8_bytes
    ):
        raise ContextPlanningError(
            ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED
        )
    for entries, body_utf8 in hard_groups:
        unit, group_decisions = _selected_group(entries, body_utf8)
        units.append(unit)
        for decision in group_decisions:
            decisions_by_source[decision.entry.source_id] = decision
        used_p2 += len(body_utf8)
        used_total += len(body_utf8)

    for entries, body_utf8 in p2_groups:
        if entries[0].semantic_channel is ContextSemanticChannel.HARD_CONSTRAINT:
            continue
        group_bytes = len(body_utf8)
        if used_p2 + group_bytes > policy.p2_source_utf8_bytes:
            for entry in entries:
                decisions_by_source[entry.source_id] = ContextSourceDecision(
                    entry,
                    ContextDecisionKind.OMITTED_CLASS_BUDGET,
                )
            continue
        unit, group_decisions = _selected_group(entries, body_utf8)
        units.append(unit)
        for decision in group_decisions:
            decisions_by_source[decision.entry.source_id] = decision
        used_p2 += group_bytes
        used_total += group_bytes

    p3_groups = _exact_body_groups(
        tuple(selectable_by_priority[ContextPriority.P3_OPTIONAL]),
        material_by_source,
    )
    for entries, body_utf8 in p3_groups:
        group_bytes = len(body_utf8)
        if used_p3 + group_bytes > policy.p3_source_utf8_bytes:
            for entry in entries:
                decisions_by_source[entry.source_id] = ContextSourceDecision(
                    entry,
                    ContextDecisionKind.OMITTED_CLASS_BUDGET,
                )
            continue
        unit, group_decisions = _selected_group(entries, body_utf8)
        units.append(unit)
        for decision in group_decisions:
            decisions_by_source[decision.entry.source_id] = decision
        used_p3 += group_bytes
        used_total += group_bytes

    if set(decisions_by_source) != set(current_by_source):
        raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
    decisions = tuple(
        decisions_by_source[entry.source_id] for entry in manifest.entries
    )
    units_tuple = tuple(
        sorted(units, key=lambda unit: context_manifest_entry_key(unit.entries[0]))
    )

    accepted_kinds = {
        ContextDecisionKind.SELECTED,
        ContextDecisionKind.DEDUPLICATED,
        ContextDecisionKind.CHECKPOINT_SUPPRESSED,
    }
    accepted_decisions = tuple(
        decision
        for decision in decisions
        if decision.entry.priority is not ContextPriority.P0_REQUIRED
        and decision.decision in accepted_kinds
    )
    checkpoint_source_by_id = (
        {}
        if mode is ContextDeliveryMode.BOOTSTRAP
        else {source.entry.source_id: source for source in checkpoint.sources}
    )
    checkpoint_catalog_by_id = (
        {}
        if mode is ContextDeliveryMode.BOOTSTRAP
        else {
            source.entry.source_id: source
            for source in checkpoint.catalog_sources
        }
    )
    next_source_alias = (
        1
        if mode is ContextDeliveryMode.BOOTSTRAP
        else checkpoint.source_alias_high_watermark + 1
    )
    catalog_sources_list: list[ContextCatalogCursor] = []
    for entry in manifest.entries:
        if not context_source_uses_catalog_alias(entry):
            continue
        previous = checkpoint_catalog_by_id.get(entry.source_id)
        if previous is None:
            source_alias = f"s{next_source_alias}"
            next_source_alias += 1
            record_alias_high_watermark = 0
        else:
            source_alias = previous.source_alias
            record_alias_high_watermark = (
                previous.record_alias_high_watermark
            )
        catalog_sources_list.append(
            ContextCatalogCursor(
                entry,
                source_alias,
                record_alias_high_watermark,
            )
        )
    catalog_by_source = {
        source.entry.source_id: source for source in catalog_sources_list
    }
    proposal_sources_list: list[ContextCheckpointSource] = []
    for decision in accepted_decisions:
        previous = checkpoint_source_by_id.get(decision.entry.source_id)
        material = material_by_source.get(decision.entry.source_id)
        catalog = catalog_by_source.get(decision.entry.source_id)
        if material is not None:
            previous_records = (
                {}
                if previous is None
                else {
                    record.cursor.record_id: record
                    for record in previous.records
                }
            )
            next_record_alias = (
                1
                if catalog is None
                else catalog.record_alias_high_watermark + 1
            )
            checkpoint_records: list[ContextCheckpointRecord] = []
            for record in material.records:
                accepted = previous_records.get(record.cursor.record_id)
                if accepted is None:
                    accepted = ContextCheckpointRecord(
                        record.cursor,
                        next_record_alias,
                    )
                    next_record_alias += 1
                else:
                    accepted = ContextCheckpointRecord(
                        record.cursor,
                        accepted.record_alias,
                    )
                checkpoint_records.append(accepted)
            cursors = tuple(checkpoint_records)
            if catalog is not None:
                updated_catalog = ContextCatalogCursor(
                    catalog.entry,
                    catalog.source_alias,
                    max(
                        catalog.record_alias_high_watermark,
                        next_record_alias - 1,
                    ),
                )
                catalog_by_source[decision.entry.source_id] = updated_catalog
        elif previous is not None:
            cursors = previous.records
        else:
            cursors = ()
        proposal_sources_list.append(
            ContextCheckpointSource(decision.entry, cursors)
        )
    proposal_sources = tuple(proposal_sources_list)
    catalog_sources = tuple(
        catalog_by_source[source.entry.source_id]
        for source in catalog_sources_list
    )
    current_catalog_ids = {
        source.entry.source_id for source in catalog_sources
    }
    removed_catalog_sources = (
        tuple(
            source
            for source in checkpoint.catalog_sources
            if source.entry.source_id not in current_catalog_ids
        )
        if mode is ContextDeliveryMode.DELTA
        else ()
    )
    catalog_updates = tuple(
        source
        for source in catalog_sources
        if mode is ContextDeliveryMode.BOOTSTRAP
        or checkpoint_catalog_by_id.get(source.entry.source_id) != source
    )
    if mode is ContextDeliveryMode.BOOTSTRAP:
        proposed_depth = 0
    else:
        assert checkpoint.delta_depth is not None
        proposed_depth = checkpoint.delta_depth + 1
    proposal = ContextCheckpointProposal(
        conditions,
        proposal_sources,
        proposed_depth,
        catalog_sources,
        next_source_alias - 1,
    )
    proposal_by_source = {
        source.entry.source_id: source for source in proposal.sources
    }
    same_scope_checkpoint = (
        checkpoint.state is ContextCheckpointState.READY
        and checkpoint.conditions is not None
        and checkpoint.conditions.scope == conditions.scope
    )
    removed_sources = (
        tuple(
            source
            for source in checkpoint.sources
            if source.entry.source_id not in proposal_by_source
        )
        if same_scope_checkpoint and mode is ContextDeliveryMode.DELTA
        else ()
    )
    projected_records: list[ContextProjectedRecord] = []
    removed_records: list[ContextRecordTombstone] = []
    for source in proposal.sources:
        material = material_by_source.get(source.entry.source_id)
        if material is None:
            continue
        current_record_materials = material.records
        previous = checkpoint_source_by_id.get(source.entry.source_id)
        previous_records = (
            {}
            if previous is None
            else {
                record.cursor.record_id: record
                for record in previous.records
            }
        )
        current_checkpoint_records = {
            record.cursor.record_id: record for record in source.records
        }
        for record in current_record_materials:
            checkpoint_record = current_checkpoint_records[
                record.cursor.record_id
            ]
            accepted = previous_records.get(record.cursor.record_id)
            if accepted is None or accepted.cursor != record.cursor:
                projected_records.append(
                    ContextProjectedRecord(
                        catalog_by_source[source.entry.source_id].source_alias,
                        checkpoint_record,
                        record.body_utf8,
                    )
                )
        if mode is ContextDeliveryMode.DELTA and previous is not None:
            current_record_ids = {
                record.cursor.record_id for record in current_record_materials
            }
            removed_records.extend(
                ContextRecordTombstone(
                    catalog_by_source[source.entry.source_id].source_alias,
                    record.record_alias,
                    record.cursor.record_id,
                )
                for record in previous.records
                if record.cursor.record_id not in current_record_ids
            )

    searchable_decisions = tuple(
        decision for decision in decisions if decision.searchable
    )

    def searchable_totals(kind: ContextDecisionKind) -> tuple[int, int]:
        """Return count and logical bytes for one searchable decision kind."""

        matching = tuple(
            decision
            for decision in searchable_decisions
            if decision.decision is kind
        )
        return len(matching), sum(
            decision.entry.utf8_bytes for decision in matching
        )

    selected_searchable = searchable_totals(ContextDecisionKind.SELECTED)
    deduplicated_searchable = searchable_totals(
        ContextDecisionKind.DEDUPLICATED
    )
    suppressed_searchable = searchable_totals(
        ContextDecisionKind.CHECKPOINT_SUPPRESSED
    )
    class_omitted_searchable = searchable_totals(
        ContextDecisionKind.OMITTED_CLASS_BUDGET
    )
    material_omitted_searchable = searchable_totals(
        ContextDecisionKind.OMITTED_MATERIAL
    )
    searchable_accounting = ContextSearchableAccounting(
        total_source_count=len(searchable_decisions),
        total_source_utf8_bytes=sum(
            decision.entry.utf8_bytes for decision in searchable_decisions
        ),
        selected_primary_count=selected_searchable[0],
        selected_primary_utf8_bytes=selected_searchable[1],
        deduplicated_pointer_count=deduplicated_searchable[0],
        deduplicated_pointer_utf8_bytes=deduplicated_searchable[1],
        checkpoint_suppressed_count=suppressed_searchable[0],
        checkpoint_suppressed_utf8_bytes=suppressed_searchable[1],
        class_budget_omitted_count=class_omitted_searchable[0],
        class_budget_omitted_utf8_bytes=class_omitted_searchable[1],
        material_omitted_count=material_omitted_searchable[0],
        material_omitted_utf8_bytes=material_omitted_searchable[1],
        selected_source_count=(
            selected_searchable[0] + deduplicated_searchable[0]
        ),
        selected_source_utf8_bytes=(
            selected_searchable[1] + deduplicated_searchable[1]
        ),
        unprojected_source_count=(
            suppressed_searchable[0]
            + class_omitted_searchable[0]
            + material_omitted_searchable[0]
        ),
        unprojected_source_utf8_bytes=(
            suppressed_searchable[1]
            + class_omitted_searchable[1]
            + material_omitted_searchable[1]
        ),
    )
    reference_bytes = sum(reference.utf8_bytes for reference in references)

    def source_budget_usage(
        configured: int,
        selected: int,
    ) -> ContextSourceBudgetUsage:
        """Construct one exact cap/selected-unique/remaining partition."""

        return ContextSourceBudgetUsage(
            configured,
            selected,
            configured - selected,
        )

    budget_accounting = ContextBudgetAccounting(
        total=source_budget_usage(policy.total_source_utf8_bytes, used_total),
        p0=source_budget_usage(policy.p0_source_utf8_bytes, used_p0),
        p1=source_budget_usage(policy.p1_source_utf8_bytes, used_p1),
        p2=source_budget_usage(policy.p2_source_utf8_bytes, used_p2),
        p3=source_budget_usage(policy.p3_source_utf8_bytes, used_p3),
        mandatory_references=ContextReferenceBudgetUsage(
            policy.mandatory_reference_utf8_bytes,
            reference_bytes,
            policy.mandatory_reference_utf8_bytes - reference_bytes,
        ),
    )
    accounting = ContextProjectionAccounting(
        mode=mode,
        bootstrap_reason=reason,
        proposed_delta_depth=proposed_depth,
        logical_source_count=len(manifest.entries),
        logical_source_utf8_bytes=sum(
            entry.utf8_bytes for entry in manifest.entries
        ),
        selected_logical_source_count=sum(
            decision.decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.DEDUPLICATED,
            }
            for decision in decisions
        ),
        selected_logical_utf8_bytes=sum(
            decision.entry.utf8_bytes
            for decision in decisions
            if decision.decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.DEDUPLICATED,
            }
        ),
        projected_unit_count=len(units_tuple),
        projected_unique_utf8_bytes=sum(
            len(unit.body_utf8) for unit in units_tuple
        ),
        saved_duplicate_utf8_bytes=sum(
            decision.entry.utf8_bytes
            for decision in decisions
            if decision.decision is ContextDecisionKind.DEDUPLICATED
        ),
        mandatory_reference_count=len(references),
        mandatory_reference_utf8_bytes=reference_bytes,
        removed_checkpoint_source_count=len(removed_sources),
        removed_checkpoint_source_utf8_bytes=sum(
            source.entry.utf8_bytes for source in removed_sources
        ),
        searchable=searchable_accounting,
        budgets=budget_accounting,
        decision_buckets=_decision_accounting(decisions),
    )
    return ContextProjection(
        mode=mode,
        bootstrap_reason=reason,
        units=units_tuple,
        mandatory_references=references,
        decisions=decisions,
        removed_checkpoint_sources=removed_sources,
        accounting=accounting,
        checkpoint_proposal=proposal,
        projected_records=tuple(projected_records),
        removed_checkpoint_records=tuple(removed_records),
        catalog_updates=tuple(catalog_updates),
        removed_catalog_sources=removed_catalog_sources,
    )
