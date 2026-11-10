"""Immutable projected values and checkpoint proposals."""

from __future__ import annotations

from ..manifest import (
    ContextManifestEntry,
    ContextPriority,
    context_manifest_entry_key,
    validate_context_history_coverage,
)
from .checkpoints import (
    ContextCatalogCursor,
    ContextCheckpointSource,
    ContextProjectedRecord,
    ContextRecordTombstone,
    catalog_covers_checkpoint_records,
)
from .planning_inputs import (
    ContextBootstrapReason,
    ContextDecisionKind,
    ContextDeliveryMode,
    ContextPlanningConditions,
    ContextPlanningError,
    ContextPlanningErrorCode,
    MandatoryReferenceReservation,
    body_digest,
    context_source_uses_catalog_alias,
    exact_nonnegative_integer,
)
from .projection_accounting import (
    ContextProjectionAccounting,
    ContextSourceDecision,
    decision_accounting,
)
from dataclasses import (
    dataclass,
    field,
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
            or entry.content_digest != body_digest(self.body_utf8)
            for entry in ordered
        ):
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        if first.priority in {
            ContextPriority.P0_REQUIRED,
            ContextPriority.P1_STABLE,
        } and len(ordered) != 1:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)
        object.__setattr__(self, "entries", ordered)


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
        exact_nonnegative_integer(self.delta_depth)
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
        if not catalog_covers_checkpoint_records(
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
        if decision_accounting(self.decisions) != (
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


__all__ = [
    'ContextProjectedUnit',
    'ContextCheckpointProposal',
    'ContextProjection',
]
