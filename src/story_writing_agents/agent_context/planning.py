"""Pure provider-neutral context projection planner."""

from __future__ import annotations

from .contracts.checkpoints import (
    ContextCatalogCursor,
    ContextCheckpointRecord,
    ContextCheckpointSource,
    ContextCheckpointView,
    ContextProjectedRecord,
    ContextRecordTombstone,
)
from .contracts.planning_inputs import (
    ContextBootstrapReason,
    ContextCheckpointState,
    ContextDecisionKind,
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningConditions,
    ContextPlanningError,
    ContextPlanningErrorCode,
    ContextPlanningPolicy,
    MandatoryReferenceReservation,
    context_source_uses_catalog_alias,
    safe_identity,
)
from .contracts.projection import (
    ContextCheckpointProposal,
    ContextProjectedUnit,
    ContextProjection,
)
from .contracts.projection_accounting import (
    ContextBudgetAccounting,
    ContextProjectionAccounting,
    ContextReferenceBudgetUsage,
    ContextSearchableAccounting,
    ContextSourceBudgetUsage,
    ContextSourceDecision,
    decision_accounting,
)
from .manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextSemanticChannel,
    ContextSubjectKind,
    context_manifest_entry_key,
)


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
        safe_identity(active_provider_thread_id)
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
        decision_buckets=decision_accounting(decisions),
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


__all__ = [
    'plan_context_projection',
]
