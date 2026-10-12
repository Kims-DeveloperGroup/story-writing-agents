"""Versioned selection and four-shard policy for the canonical test runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


POLICY_VERSION = 2
SHARD_COUNT = 4
WEIGHT_SCHEMA_VERSION = 1
WEIGHT_MANIFEST_PATH = Path(__file__).parent / "fixtures" / "test_timing_weights_v1.json"
SHARD_ALGORITHM = "class-lpt-v2"


class SuitePolicyError(ValueError):
    """A deterministic suite policy or timing-data error."""


FAST_TEST_IDS = (
    "tests.test_agent_context_actions.ActionValueTests."
    "test_source_authority_boundary_matrix_and_exact_digest_bytes",
    "tests.test_reply_trace.ReplyTraceContractTests."
    "test_exact_round_trip_contains_labels_only",
    "tests.test_lead_orchestration.LeadDecisionAuthorityTests."
    "test_mismatch_reject_revise_and_free_text_are_non_authorizing",
    "tests.test_writing_artifacts.ArtifactGrammarTests."
    "test_required_output_grammar_has_no_root_or_compatibility_form",
    "tests.test_writing_artifacts.SecureStorageTests."
    "test_storage_rejects_traversal_symlink_hardlink_and_nonregular_files",
    "tests.test_provider_catalog.ProviderCatalogProjectionTests."
    "test_codex_parser_rejects_non_strict_json_shapes_and_duplicates",
    "tests.test_provider_auth.ProviderAuthProcessContractTests."
    "test_constructor_and_provider_validation_are_pre_process",
    "tests.test_codex_backend.CodexBackendTestCase."
    "test_process_stop_escalates_from_terminate_to_kill",
    "tests.test_public_api_and_cli.CliTests."
    "test_module_help_lists_public_commands",
    "tests.test_interactive_terminal_foundation.CapabilityAndDriverFoundationTests."
    "test_capable_advisory_progress_orders_frames_and_serializes_writes",
)

@dataclass(frozen=True)
class TimingWeights:
    version: int
    fallback_test_weight_microseconds: int
    group_weights_microseconds: Mapping[str, int]
    provenance: str
    digest: str
    source: str


@dataclass(frozen=True)
class ShardPlan:
    shard_ids: tuple[tuple[str, ...], ...]
    shard_groups: tuple[tuple[str, ...], ...]
    shard_weights_microseconds: tuple[int, ...]
    group_weights_microseconds: Mapping[str, int]
    fallback_groups: tuple[str, ...]
    digest: str


def canonical_group_id(identifier: str) -> str:
    """Return canonical ``module.class`` ownership for one test ID."""

    group_id, separator, method = identifier.rpartition(".")
    if not separator or not group_id or not method:
        raise SuitePolicyError(f"invalid canonical test ID: {identifier!r}")
    return group_id


def select_fast(canonical_ids: Sequence[str]) -> tuple[str, ...]:
    """Resolve the exact fast lane and fail if policy IDs drift."""

    if len(set(FAST_TEST_IDS)) != len(FAST_TEST_IDS):
        raise SuitePolicyError("fast tier contains duplicate canonical IDs")
    available = set(canonical_ids)
    missing = tuple(identifier for identifier in FAST_TEST_IDS if identifier not in available)
    if missing:
        raise SuitePolicyError("fast tier has missing canonical IDs: " + ", ".join(missing))
    selected = set(FAST_TEST_IDS)
    return tuple(identifier for identifier in canonical_ids if identifier in selected)


def load_timing_weights(path: Path = WEIGHT_MANIFEST_PATH) -> TimingWeights:
    """Load strictly versioned integer-microsecond timing seed data."""

    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise SuitePolicyError(f"timing weight manifest is unreadable: {error}") from error
    if type(payload) is not dict:
        raise SuitePolicyError("timing weight manifest must be a JSON object")
    required = {
        "schema_version",
        "policy_version",
        "fallback_test_weight_microseconds",
        "group_weights_microseconds",
        "provenance",
    }
    if set(payload) != required:
        raise SuitePolicyError("timing weight manifest fields are not exact")
    if payload["schema_version"] != WEIGHT_SCHEMA_VERSION:
        raise SuitePolicyError("unsupported timing weight schema version")
    if payload["policy_version"] != POLICY_VERSION:
        raise SuitePolicyError("timing weight policy version does not match suite policy")
    fallback = payload["fallback_test_weight_microseconds"]
    if type(fallback) is not int or fallback <= 0:
        raise SuitePolicyError("fallback test weight must be a positive integer")
    provenance = payload["provenance"]
    if type(provenance) is not str or not provenance.strip():
        raise SuitePolicyError("timing weight provenance must be a non-empty string")
    raw_weights = payload["group_weights_microseconds"]
    if type(raw_weights) is not dict:
        raise SuitePolicyError("group weights must be a JSON object")
    weights: dict[str, int] = {}
    for group_id, weight in raw_weights.items():
        if type(group_id) is not str or canonical_group_id(group_id + ".test") != group_id:
            raise SuitePolicyError("timing weight group IDs must be non-empty strings")
        if type(weight) is not int or weight <= 0:
            raise SuitePolicyError(f"timing weight for {group_id!r} must be positive integer microseconds")
        weights[group_id] = weight
    return TimingWeights(
        version=WEIGHT_SCHEMA_VERSION,
        fallback_test_weight_microseconds=fallback,
        group_weights_microseconds=weights,
        provenance=provenance,
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        source=path.relative_to(Path(__file__).parents[1]).as_posix()
        if path.is_relative_to(Path(__file__).parents[1])
        else str(path),
    )


def partition_full_shards(
    canonical_ids: Sequence[str], weights: TimingWeights
) -> ShardPlan:
    """Partition complete class groups using deterministic four-bin LPT."""

    if len(set(canonical_ids)) != len(canonical_ids):
        raise SuitePolicyError("canonical inventory contains duplicate IDs")
    groups: dict[str, list[str]] = {}
    for identifier in canonical_ids:
        groups.setdefault(canonical_group_id(identifier), []).append(identifier)
    if len(groups) < SHARD_COUNT:
        raise SuitePolicyError("four nonempty shards require at least four class groups")
    stale = tuple(sorted(set(weights.group_weights_microseconds) - set(groups)))
    if stale:
        raise SuitePolicyError("timing manifest has stale groups: " + ", ".join(stale))
    effective: dict[str, int] = {}
    fallback: list[str] = []
    for group_id, identifiers in groups.items():
        if group_id in weights.group_weights_microseconds:
            effective[group_id] = weights.group_weights_microseconds[group_id]
        else:
            effective[group_id] = (
                weights.fallback_test_weight_microseconds * len(identifiers)
            )
            fallback.append(group_id)
    assigned_groups: list[list[str]] = [[] for _ in range(SHARD_COUNT)]
    totals = [0] * SHARD_COUNT
    assignment: dict[str, int] = {}
    for group_id in sorted(groups, key=lambda item: (-effective[item], item)):
        shard = min(range(SHARD_COUNT), key=lambda index: (totals[index], index))
        assigned_groups[shard].append(group_id)
        assignment[group_id] = shard
        totals[shard] += effective[group_id]
    shard_ids = tuple(
        tuple(identifier for identifier in canonical_ids
              if assignment[canonical_group_id(identifier)] == index)
        for index in range(SHARD_COUNT)
    )
    plan_payload = {
        "algorithm": SHARD_ALGORITHM,
        "policy_version": POLICY_VERSION,
        "shard_count": SHARD_COUNT,
        "weight_digest": weights.digest,
        "assignments": [
            {"group_id": group_id, "shard": assignment[group_id] + 1, "weight_microseconds": effective[group_id]}
            for group_id in sorted(groups)
        ],
    }
    encoded = json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode()
    return ShardPlan(
        shard_ids=shard_ids,
        shard_groups=tuple(tuple(group) for group in assigned_groups),
        shard_weights_microseconds=tuple(totals),
        group_weights_microseconds=effective,
        fallback_groups=tuple(sorted(fallback)),
        digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
    )


__all__ = [
    "FAST_TEST_IDS",
    "POLICY_VERSION",
    "SHARD_ALGORITHM",
    "SHARD_COUNT",
    "ShardPlan",
    "SuitePolicyError",
    "TimingWeights",
    "WEIGHT_MANIFEST_PATH",
    "canonical_group_id",
    "load_timing_weights",
    "partition_full_shards",
    "select_fast",
]
