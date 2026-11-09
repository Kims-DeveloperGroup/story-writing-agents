"""Canonical test runner: selection owner."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO
from tests import suite_policy
from .contracts import CanonicalCollection, CanonicalInventory, PolicySelection, RunnerError
from .evidence import manifest


def validated_values(values: Sequence[str], option: str) -> tuple[str, ...]:
    if any(not value or value != value.strip() for value in values):
        raise RunnerError(f"{option} values must be non-empty and contain no outer whitespace")
    return tuple(dict.fromkeys(values))


def select(
    collection: CanonicalCollection,
    *,
    ids: Sequence[str] = (),
    modules: Sequence[str] = (),
) -> CanonicalCollection:
    if not collection.tests or not collection.ids:
        raise RunnerError("test selection produced no tests")
    chosen = set(selected_ids(collection, ids=ids, modules=modules))
    tests = tuple(test for test in collection.tests if test.id() in chosen)
    return CanonicalCollection(tests, tuple(test.id() for test in tests))


def selected_ids(collection: CanonicalInventory | CanonicalCollection, *,
                 ids: Sequence[str] = (), modules: Sequence[str] = ()) -> tuple[str, ...]:
    requested_ids = validated_values(ids, "--id")
    requested_modules = validated_values(modules, "--module")
    missing_ids = sorted(set(requested_ids) - set(collection.ids))
    missing_modules = sorted(set(requested_modules) - set(collection.modules))
    if missing_ids or missing_modules:
        messages = []
        if missing_ids:
            messages.append("unknown test IDs: " + ", ".join(missing_ids))
        if missing_modules:
            messages.append("unknown test modules: " + ", ".join(missing_modules))
        raise RunnerError("; ".join(messages))
    if not requested_ids and not requested_modules:
        return collection.ids
    requested_id_set = set(requested_ids)
    requested_module_set = set(requested_modules)
    module_by_id = ({test.id(): test.__class__.__module__ for test in collection.tests}
                    if isinstance(collection, CanonicalCollection) else
                    {identifier: identifier.rsplit(".", 2)[0] for identifier in collection.ids})
    selected = tuple(
        identifier for identifier in collection.ids
        if identifier in requested_id_set or module_by_id[identifier] in requested_module_set
    )
    if not selected:
        raise RunnerError("test selection produced no tests")
    return selected


def suite_policy_metadata(weights: suite_policy.TimingWeights,
                           plan: suite_policy.ShardPlan | None = None) -> dict[str, object]:
    policy: dict[str, object] = {
        "version": suite_policy.POLICY_VERSION,
        "fast_tier_manifest": manifest(suite_policy.FAST_TEST_IDS),
        "weight_manifest": {
            "schema_version": weights.version, "digest": weights.digest,
            "source": weights.source,
            "fallback_test_weight_microseconds": weights.fallback_test_weight_microseconds,
            "provenance": weights.provenance,
        },
    }
    if plan is not None:
        policy["partition"] = {
            "algorithm": suite_policy.SHARD_ALGORITHM, "shard_count": suite_policy.SHARD_COUNT,
            "isolated_test_id": suite_policy.ISOLATED_TEST_ID,
            "isolated_weight_basis": suite_policy.ISOLATED_WEIGHT_BASIS,
            "digest": plan.digest, "group_count": len(plan.group_weights_microseconds),
            "fallback_group_count": len(plan.fallback_groups),
            "shards": [
                {"identity": f"{index + 1}/{suite_policy.SHARD_COUNT}", "group_count": len(plan.shard_groups[index]),
                 "predicted_weight_microseconds": plan.shard_weights_microseconds[index],
                 "selection_manifest": manifest(plan.shard_ids[index])}
                for index in range(suite_policy.SHARD_COUNT)
            ],
        }
    return policy


def policy_select(
    collection: CanonicalCollection,
    *,
    ids: Sequence[str] = (),
    modules: Sequence[str] = (),
    tiers: Sequence[str] = (),
    shard: str | None = None,
    weight_path: Path = suite_policy.WEIGHT_MANIFEST_PATH,
) -> PolicySelection:
    """Compose exact selectors or derive one complete deterministic shard."""

    requested_tiers = validated_values(tiers, "--tier")
    unknown_tiers = tuple(sorted(set(requested_tiers) - {"fast"}))
    if unknown_tiers:
        raise RunnerError("unknown test tiers: " + ", ".join(unknown_tiers))
    if shard is not None and (ids or modules or requested_tiers):
        raise RunnerError("--shard cannot be combined with other selectors")
    try:
        weights = suite_policy.load_timing_weights(weight_path)
    except suite_policy.SuitePolicyError as error:
        raise RunnerError(str(error)) from error

    if shard is not None:
        if shard not in {f"{index}/{suite_policy.SHARD_COUNT}" for index in range(1, suite_policy.SHARD_COUNT + 1)}:
            raise RunnerError("--shard must be one of 1/5 through 5/5")
        try:
            plan = suite_policy.partition_full_shards(collection.ids, weights)
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        shard_index = int(shard[0]) - 1
        selected_ids = set(plan.shard_ids[shard_index])
    else:
        selected_ids: set[str] = set()
        has_selector = bool(ids or modules or requested_tiers)
        if ids or modules:
            selected_ids.update(select(collection, ids=ids, modules=modules).ids)
        try:
            if requested_tiers:
                selected_ids.update(suite_policy.select_fast(collection.ids))
        except suite_policy.SuitePolicyError as error:
            raise RunnerError(str(error)) from error
        if not has_selector:
            selected_ids.update(collection.ids)

    tests = tuple(test for test in collection.tests if test.id() in selected_ids)
    selection = CanonicalCollection(tests, tuple(test.id() for test in tests))
    if not selection.ids:
        raise RunnerError("test selection produced no tests")
    metadata = {
        "selection": {"tiers": list(requested_tiers), "shard": shard},
        "suite_policy": suite_policy_metadata(weights, plan if shard is not None else None),
    }
    return PolicySelection(selection, metadata)
