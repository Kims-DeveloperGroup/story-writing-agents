"""Versioned selection and two-shard policy for the canonical test runner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


POLICY_VERSION = 1
WEIGHT_SCHEMA_VERSION = 1
WEIGHT_MANIFEST_PATH = Path(__file__).parent / "fixtures" / "test_timing_weights_v1.json"
SHARD_ALGORITHM = "class-lpt-v1"


class SuitePolicyError(ValueError):
    """A deterministic suite policy or timing-data error."""


@dataclass(frozen=True)
class GroupSelector:
    module: str
    class_name: str | None = None

    @property
    def group_id(self) -> str | None:
        if self.class_name is None:
            return None
        return f"{self.module}.{self.class_name}"


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


AFFECTED_GROUPS: Mapping[str, tuple[GroupSelector, ...]] = {
    "runner": (
        GroupSelector("tests.test_suite_runner"),
        GroupSelector("tests.test_suite_tiers"),
    ),
    "git-storage": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_git_adapter_integration",
            "tests.test_git_adapter_process",
            "tests.test_git_contracts",
            "tests.test_git_external_operation_inspection",
            "tests.test_git_linked_worktree_reads",
            "tests.test_git_process",
            "tests.test_git_process_profiles",
            "tests.test_git_repository_lock",
            "tests.test_git_session_checkpoint_classifier",
            "tests.test_git_session_checkpoint_contracts",
            "tests.test_git_session_checkpoint_process",
            "tests.test_git_session_checkpoints",
            "tests.test_git_session_contracts",
            "tests.test_git_session_history",
            "tests.test_git_session_history_contracts",
            "tests.test_git_session_resources",
            "tests.test_git_story_commit_candidates",
            "tests.test_git_story_commit_contracts",
            "tests.test_git_story_commit_process",
            "tests.test_git_story_commits",
        )
    ),
    "session-runtime": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_session_lifecycle",
            "tests.test_session_recovery_contracts",
            "tests.test_session_recovery_reconciliation",
            "tests.test_session_transition_assessment",
        )
    ),
    "workspace-session": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_workspace_session_history",
            "tests.test_workspace_session_resources",
        )
    ),
    "database": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_sqlite_database",
            "tests.test_sqlite_collaboration_event_store",
            "tests.test_sqlite_conversation_storage",
        )
    ),
    "workspace-core": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_workspace_fixtures",
            "tests.test_workspace_v2_initialization",
            "tests.test_workspace_and_config",
        )
    ),
    "artifact-storage": (GroupSelector("tests.test_writing_artifacts"),),
    "provider-process": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_antigravity_adapter",
            "tests.test_antigravity_parity",
            "tests.test_codex_backend",
            "tests.test_provider_auth",
            "tests.test_provider_auth_recovery",
            "tests.test_provider_catalog",
            "tests.test_provider_error_artifacts",
            "tests.test_provider_timeout_recovery",
        )
    ),
    "interactive-terminal": tuple(
        GroupSelector(module)
        for module in (
            "tests.test_interactive_auth",
            "tests.test_interactive_cancellation",
            "tests.test_interactive_collaboration_view",
            "tests.test_interactive_commands",
            "tests.test_interactive_controller",
            "tests.test_interactive_line_editor",
            "tests.test_interactive_live_composer",
            "tests.test_interactive_management",
            "tests.test_interactive_pty",
            "tests.test_interactive_settings",
            "tests.test_interactive_terminal",
            "tests.test_interactive_terminal_completion",
            "tests.test_interactive_terminal_composer",
            "tests.test_interactive_terminal_external_view",
            "tests.test_interactive_terminal_foundation",
            "tests.test_interactive_terminal_history",
            "tests.test_interactive_terminal_input_lifecycle",
            "tests.test_interactive_terminal_picker",
            "tests.test_interactive_terminal_reference_activation",
            "tests.test_interactive_terminal_viewer",
            "tests.test_interactive_terminal_views",
        )
    ),
}


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
    shard_ids: tuple[tuple[str, ...], tuple[str, ...]]
    shard_groups: tuple[tuple[str, ...], tuple[str, ...]]
    shard_weights_microseconds: tuple[int, int]
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


def select_affected_groups(
    canonical_ids: Sequence[str], names: Sequence[str]
) -> tuple[str, ...]:
    """Resolve repeatable named affected groups as one canonical-order union."""

    requested = tuple(dict.fromkeys(names))
    if any(not name or name != name.strip() for name in requested):
        raise SuitePolicyError("--group values must be non-empty and contain no outer whitespace")
    unknown = tuple(sorted(set(requested) - set(AFFECTED_GROUPS)))
    if unknown:
        raise SuitePolicyError("unknown affected groups: " + ", ".join(unknown))
    if not requested:
        return ()
    selected: set[str] = set()
    for name in requested:
        group_selection: set[str] = set()
        for selector in AFFECTED_GROUPS[name]:
            prefix = selector.module + "."
            selector_ids = {
                identifier
                for identifier in canonical_ids
                if identifier.startswith(prefix)
                and (
                    selector.group_id is None
                    or canonical_group_id(identifier) == selector.group_id
                )
            }
            if not selector_ids:
                label = selector.module
                if selector.class_name is not None:
                    label += "." + selector.class_name
                raise SuitePolicyError(
                    f"affected group {name!r} has empty selector: {label}"
                )
            group_selection.update(selector_ids)
        if not group_selection:
            raise SuitePolicyError(f"affected group {name!r} selected no tests")
        selected.update(group_selection)
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


def partition_two_shards(
    canonical_ids: Sequence[str], weights: TimingWeights
) -> ShardPlan:
    """Partition complete class groups using deterministic two-bin LPT."""

    groups: dict[str, list[str]] = {}
    for identifier in canonical_ids:
        groups.setdefault(canonical_group_id(identifier), []).append(identifier)
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
    assigned_groups: list[list[str]] = [[], []]
    totals = [0, 0]
    assignment: dict[str, int] = {}
    for group_id in sorted(groups, key=lambda item: (-effective[item], item)):
        shard = 0 if totals[0] <= totals[1] else 1
        assigned_groups[shard].append(group_id)
        assignment[group_id] = shard
        totals[shard] += effective[group_id]
    shard_ids = (
        tuple(identifier for identifier in canonical_ids if assignment[canonical_group_id(identifier)] == 0),
        tuple(identifier for identifier in canonical_ids if assignment[canonical_group_id(identifier)] == 1),
    )
    plan_payload = {
        "algorithm": SHARD_ALGORITHM,
        "policy_version": POLICY_VERSION,
        "weight_digest": weights.digest,
        "assignments": [
            {"group_id": group_id, "shard": assignment[group_id] + 1, "weight_microseconds": effective[group_id]}
            for group_id in sorted(groups)
        ],
    }
    encoded = json.dumps(plan_payload, sort_keys=True, separators=(",", ":")).encode()
    return ShardPlan(
        shard_ids=shard_ids,
        shard_groups=(tuple(assigned_groups[0]), tuple(assigned_groups[1])),
        shard_weights_microseconds=(totals[0], totals[1]),
        group_weights_microseconds=effective,
        fallback_groups=tuple(sorted(fallback)),
        digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
    )


__all__ = [
    "AFFECTED_GROUPS",
    "FAST_TEST_IDS",
    "GroupSelector",
    "POLICY_VERSION",
    "SHARD_ALGORITHM",
    "ShardPlan",
    "SuitePolicyError",
    "TimingWeights",
    "WEIGHT_MANIFEST_PATH",
    "canonical_group_id",
    "load_timing_weights",
    "partition_two_shards",
    "select_affected_groups",
    "select_fast",
]
