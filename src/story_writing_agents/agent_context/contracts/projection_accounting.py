"""Pure projection decisions and exact budget accounting."""

from __future__ import annotations

from ..manifest import (
    ContextManifestEntry,
    ContextPriority,
    ContextSemanticChannel,
)
from .planning_inputs import (
    ContextBootstrapReason,
    ContextDecisionKind,
    ContextDeliveryMode,
    ContextPlanningError,
    ContextPlanningErrorCode,
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    exact_nonnegative_integer,
    safe_identity,
)
from dataclasses import dataclass


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
            safe_identity(self.represented_by_source_id)
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
        count = exact_nonnegative_integer(self.source_count)
        exact_nonnegative_integer(self.raw_source_utf8_bytes)
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
            exact_nonnegative_integer(value)
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
        configured = exact_nonnegative_integer(self.configured_utf8_bytes)
        selected = exact_nonnegative_integer(self.selected_unique_utf8_bytes)
        remaining = exact_nonnegative_integer(self.remaining_utf8_bytes)
        if selected > configured or selected + remaining != configured:
            raise ContextPlanningError(ContextPlanningErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReferenceBudgetUsage:
    """Configured, used, and remaining mandatory-reference reservation bytes."""

    configured_utf8_bytes: int
    used_utf8_bytes: int
    remaining_utf8_bytes: int

    def __post_init__(self) -> None:
        configured = exact_nonnegative_integer(self.configured_utf8_bytes)
        used = exact_nonnegative_integer(self.used_utf8_bytes)
        remaining = exact_nonnegative_integer(self.remaining_utf8_bytes)
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
            exact_nonnegative_integer(value)
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


def decision_accounting(
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


__all__ = [
    'ContextSourceDecision',
    'ContextDecisionAccounting',
    'ContextSearchableAccounting',
    'ContextSourceBudgetUsage',
    'ContextReferenceBudgetUsage',
    'ContextBudgetAccounting',
    'ContextProjectionAccounting',
    'decision_accounting',
]
