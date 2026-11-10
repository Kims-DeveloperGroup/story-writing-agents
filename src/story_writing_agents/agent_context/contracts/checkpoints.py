"""Immutable checkpoint, cursor, and record-view contracts."""

from __future__ import annotations

from ..manifest import (
    ContextManifestEntry,
    ContextPriority,
    context_manifest_entry_key,
    validate_context_history_coverage,
)
from .planning_inputs import (
    ContextCheckpointState,
    ContextPlanningConditions,
    ContextPlanningError,
    ContextPlanningErrorCode,
    ContextRecordCursor,
    ContextRecordMaterial,
    MAX_RECORD_ALIAS,
    context_source_uses_catalog_alias,
    safe_identity,
    validate_record_alias,
    validate_source_alias,
)
from dataclasses import (
    dataclass,
    field,
)


@dataclass(frozen=True, slots=True)
class ContextCheckpointRecord:
    """Content-free source record paired with its stable delivery alias."""

    cursor: ContextRecordCursor
    record_alias: int

    def __post_init__(self) -> None:
        if type(self.cursor) is not ContextRecordCursor:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)
        validate_record_alias(self.record_alias, checkpoint=True)


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
        validate_source_alias(self.source_alias)
        if (
            type(self.record_alias_high_watermark) is not int
            or self.record_alias_high_watermark < 0
            or self.record_alias_high_watermark > MAX_RECORD_ALIAS
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_CHECKPOINT)


def catalog_covers_checkpoint_records(
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
        validate_source_alias(self.source_alias)
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
        validate_source_alias(self.source_alias)
        validate_record_alias(self.record_alias)
        safe_identity(self.record_id)


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
            safe_identity(self.provider_thread_id, checkpoint=True)
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
        if not catalog_covers_checkpoint_records(
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


__all__ = [
    'ContextCheckpointRecord',
    'ContextCheckpointSource',
    'ContextCatalogCursor',
    'catalog_covers_checkpoint_records',
    'ContextProjectedRecord',
    'ContextRecordTombstone',
    'ContextCheckpointView',
]
