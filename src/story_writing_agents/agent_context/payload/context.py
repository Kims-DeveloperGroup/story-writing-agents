"""Pure initial and continuation context payload rendering."""

from __future__ import annotations

import json
from ...provider_wire import (
    add_contract_guidance,
    project_context_payload,
)
from ..accounting import (
    ContextComponentKind,
    JsonPath,
)
from ..contracts.call_offers import ContextJsonObjectResultContract
from ..contracts.checkpoints import (
    ContextCheckpointRecord,
    ContextCheckpointView,
)
from ..contracts.context_values import (
    ACTION_WIRE_CODES,
    SEMANTIC_CHANNEL_WIRE_CODES,
    SOURCE_KIND_WIRE_CODES,
    canonical_bytes,
)
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.payload import (
    ContextPayloadRequest,
    ContextPayloadSession,
)
from ..contracts.planning_inputs import (
    ContextDeliveryMode,
    ContextMaterial,
    context_source_uses_catalog_alias,
)
from ..contracts.projection import ContextProjection
from ..contracts.session import (
    ContextInvocationBinding,
    action_correlation,
    correlation_token,
)
from ..manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from ..request_compaction import compact_context_payload
from .actions import (
    DuplicateJsonKey,
    unique_json_object,
)
from .candidates import wire_candidate_value
from .history import conversation_counts
from .records import (
    projected_component_kind,
    source_number,
    wire_projected_content,
    wire_record_value,
)
from collections.abc import Mapping
from types import MappingProxyType


def invocation_phase_contract(
    request: ContextPayloadRequest,
) -> tuple[str, ContextJsonObjectResultContract]:
    """Pair the route-derived phase with its owner-supplied result contract."""

    if request.invocation_kind is not None:
        return request.invocation_kind.value, request.result_contract
    if request.subject_kind is ContextSubjectKind.CONVERSATION:
        if request.allowed_search_source_kinds == (ContextSourceKind.CONVERSATION,):
            return "retrieval", request.result_contract
        return "conversation", request.result_contract
    phases = (
        "propose_revision",
        "synthesize",
        "finalize",
        "audit",
        "draft",
    )
    phase = next(
        (
            candidate
            for candidate in phases
            if request.operation_id.startswith(f"debate_{candidate}_")
        ),
        None,
    )
    if phase is None:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return phase, request.result_contract


def _action_query(
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    session: ContextPayloadSession,
    allowed_actions: tuple[str, ...],
) -> list[object]:
    """Build the authoritative phase, action, kind, limit, and result tuple."""

    phase, contract = phase_contract
    return [
        phase,
        "".join(ACTION_WIRE_CODES[action] for action in allowed_actions),
        (
            "".join(
                SOURCE_KIND_WIRE_CODES[kind]
                for kind in session.allowed_search_source_kinds
            )
            if "search" in allowed_actions
            else ""
        ),
        session.action_limits(),
        [phase, contract.contract_id],
    ]


def initial_payload(
    binding: ContextInvocationBinding,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    session: ContextPayloadSession,
    materials: tuple[ContextMaterial, ...],
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    reference_context: tuple[Mapping[str, object], ...],
    session_history_records: Mapping[
        str, Mapping[str, Mapping[str, object]]
    ] | None = None,
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    component_paths: dict[JsonPath, ContextComponentKind] = {}
    duplicate_channels: dict[str, str | None] = {}
    current: object | None = None
    candidate: object | None = None
    candidate_entry: ContextManifestEntry | None = None

    def remember_duplicate(text: str, channel: str) -> None:
        previous = duplicate_channels.get(text)
        if previous is None and text not in duplicate_channels:
            duplicate_channels[text] = channel
        elif previous != channel:
            duplicate_channels[text] = None

    for unit in projection.units:
        if any(context_source_uses_catalog_alias(entry) for entry in unit.entries):
            continue
        content = wire_projected_content(unit.entries, unit.body_utf8)
        channel = SEMANTIC_CHANNEL_WIRE_CODES[unit.entries[0].semantic_channel]
        if type(content) is str:
            remember_duplicate(content, channel)
        elif type(content) is dict and type(content.get("input")) is str:
            remember_duplicate(content["input"], channel)
        if channel == "i":
            if current is not None:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            current = content
            component_paths[("i",)] = ContextComponentKind.CURRENT_INPUT
        elif channel == "k":
            if candidate is not None:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            candidate = content
            candidate_entry = unit.entries[0]
            component_paths[("n",)] = ContextComponentKind.CANDIDATE
    if current is None:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    catalog_by_alias = {
        source.source_alias: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    material_by_source = {material.entry.source_id: material for material in materials}
    records_by_alias: dict[str, list[ContextCheckpointRecord]] = {}
    projected_by_key = {
        (record.source_alias, record.record.record_alias): record
        for record in projection.projected_records
    }
    for record in projection.projected_records:
        records_by_alias.setdefault(record.source_alias, []).append(record.record)
    changed_group_aliases = {
        *records_by_alias,
        *(record.source_alias for record in projection.removed_checkpoint_records),
        *(
            source.source_alias
            for source in projection.catalog_updates
            if source.entry.source_kind
            in {
                ContextSourceKind.CONVERSATION,
                ContextSourceKind.ACTIVE_DEBATE,
            }
        ),
    }
    proposal_source_ids = {
        source.entry.source_id for source in projection.checkpoint_proposal.sources
    }
    group_aliases = tuple(
        source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
        if source.entry.source_id in proposal_source_ids
        and source.entry.source_kind
        in {
            ContextSourceKind.CONVERSATION,
            ContextSourceKind.ACTIVE_DEBATE,
        }
        and (
            projection.mode is ContextDeliveryMode.BOOTSTRAP
            or source.source_alias in changed_group_aliases
        )
    )
    projected_records: list[list[object]] = []
    for source_alias in group_aliases:
        records = records_by_alias.get(source_alias, [])
        source = catalog_by_alias[source_alias]
        represented_records = (session_history_records or {}).get(
            source.entry.provenance.locator,
        )
        if (
            source.entry.source_kind is ContextSourceKind.CONVERSATION
            and represented_records is not None
        ):
            unrepresented_records = []
            for record in records:
                expected = represented_records.get(record.cursor.record_id)
                actual = wire_projected_content(
                    (source.entry,),
                    projected_by_key[(source_alias, record.record_alias)].body_utf8,
                )
                if (
                    expected is None
                    or type(actual) is not dict
                    or any(actual.get(key) != value for key, value in expected.items())
                ):
                    unrepresented_records.append(record)
            records = unrepresented_records
            if not records:
                continue
        record_values = [
            wire_record_value(
                source.entry,
                record,
                projected_by_key[(source_alias, record.record_alias)].body_utf8,
                duplicate_channels,
            )
            for record in records
        ]
        group_index = len(projected_records)
        if source.entry.source_kind is ContextSourceKind.CONVERSATION:
            material = material_by_source.get(source.entry.source_id)
            if material is None:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            total, omitted = conversation_counts(material)
            projected_records.append(
                ["c", source_number(source_alias), total, omitted, record_values]
            )
            record_path_index = 4
        elif source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            projected_records.append(["d", source_number(source_alias), record_values])
            record_path_index = 2
        else:
            projected_records.append(["o", source_number(source_alias), record_values])
            record_path_index = 2
        for record_index in range(len(records)):
            component_paths[("p", group_index, record_path_index, record_index)] = (
                projected_component_kind(source.entry)
            )
    correlation = action_correlation(binding, action_index)
    docs: list[list[object]] = []
    catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    for unit in projection.units:
        for entry in unit.entries:
            if (
                entry.source_kind is not ContextSourceKind.WRITING_ARTIFACT
                or entry.semantic_channel is not ContextSemanticChannel.HARD_CONSTRAINT
            ):
                continue
            catalog = catalog_by_id.get(entry.source_id)
            if catalog is None:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            index = len(docs)
            docs.append(
                [
                    source_number(catalog.source_alias),
                    entry.provenance.locator,
                    unit.body_utf8.decode("utf-8", errors="strict"),
                ]
            )
            component_paths[("docs", index, 2)] = (
                ContextComponentKind.RETRIEVED_OBSERVATION
            )
    proposal_catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    payload: dict[str, object] = {
        "c": correlation_token(correlation),
        "q": _action_query(phase_contract, session, allowed_actions),
        "i": current,
    }
    if session.core_statuses:
        payload["core_statuses"] = [
            {
                "evidence_id": item.evidence_id,
                "locator": item.locator,
                "status": item.status.value,
            }
            for item in session.core_statuses
        ]
    if candidate is not None:
        payload["n"] = (
            wire_candidate_value(
                candidate,
                projection,
                checkpoint,
                materials,
            )
            if type(candidate) is str and candidate_entry is not None
            else candidate
        )
    if docs:
        payload["docs"] = docs
    if projected_records:
        payload["p"] = projected_records
    tombstones = [
        *[
            ["s", source_number(source.source_alias)]
            for source in projection.removed_catalog_sources
        ],
        *[
            [
                "d",
                source_number(
                    proposal_catalog_by_id[source.entry.source_id].source_alias
                ),
            ]
            for source in projection.removed_checkpoint_sources
            if source.entry.source_id in proposal_catalog_by_id
            and source.entry.source_id
            not in {
                removed.entry.source_id
                for removed in projection.removed_catalog_sources
            }
        ],
        *[
            [
                "r",
                source_number(record.source_alias),
                record.record_alias,
            ]
            for record in projection.removed_checkpoint_records
            if record.source_alias
            not in {
                removed.source_alias for removed in projection.removed_catalog_sources
            }
        ],
    ]
    if tombstones:
        payload["t"] = tombstones
    mandatory_paths: list[JsonPath] = []
    if reference_context:
        payload["untrusted_writing_artifact_context"] = [
            dict(item) for item in reference_context
        ]
        component_paths[("untrusted_writing_artifact_context",)] = (
            ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
        )
        for index, item in enumerate(reference_context):
            if "content" in item:
                path = ("untrusted_writing_artifact_context", index, "content")
                component_paths[path] = ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                mandatory_paths.append(path)
    return payload, component_paths, tuple(mandatory_paths)


def render_context_payload(
    payload: dict[str, object],
    component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_paths: tuple[JsonPath, ...],
    *,
    schema: Mapping[str, object],
    projection: ContextProjection,
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    """Choose candidate delivery by complete guided and shared request bytes."""
    def render(value: dict[str, object]) -> tuple[
        dict[str, object],
        dict[JsonPath, ContextComponentKind],
        tuple[JsonPath, ...],
    ]:
        rendered, paths, references = project_context_payload(
            value, component_paths, mandatory_paths,
        )
        rendered = add_contract_guidance(rendered, schema)
        rendered, paths = compact_context_payload(rendered, paths, references)
        return add_contract_guidance(rendered, schema), paths, references

    result = render(payload)
    candidate = payload.get("n")
    if not isinstance(candidate, dict) or candidate.get("op") not in {
        "candidate_pointer", "candidate_single_splice",
    }:
        return result
    for unit in projection.units:
        if (
            unit.entries[0].semantic_channel is ContextSemanticChannel.CANDIDATE
            and not any(context_source_uses_catalog_alias(entry) for entry in unit.entries)
        ):
            full_candidate = wire_projected_content(unit.entries, unit.body_utf8)
            if type(full_candidate) is not str:
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            full = render({**payload, "n": full_candidate})
            # Equal bytes favor literal full text and avoid needless references.
            return full if len(canonical_bytes(full[0])) <= len(canonical_bytes(result[0])) else result
    raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def continuation_payload(
    binding: ContextInvocationBinding,
    session: ContextPayloadSession,
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    observation: str,
) -> tuple[dict[str, object], dict[JsonPath, ContextComponentKind]]:
    correlation = action_correlation(binding, action_index)
    try:
        observation_value = json.loads(
            observation,
            object_pairs_hook=unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    return (
        {
            "c": correlation_token(correlation),
            "q": _action_query(phase_contract, session, allowed_actions),
            "o": observation_value,
        },
        {("o",): ContextComponentKind.RETRIEVED_OBSERVATION},
    )


def invocation_source_aliases(
    manifest: ContextManifest,
    projection: ContextProjection,
) -> Mapping[str, str]:
    """Return the planner-owned complete readable catalog alias map."""

    aliases = {
        source.entry.source_id: source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
    }
    expected_ids = {
        entry.source_id
        for entry in manifest.entries
        if context_source_uses_catalog_alias(entry)
    }
    if set(aliases) != expected_ids:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return MappingProxyType(aliases)


__all__ = [
    'invocation_phase_contract',
    'initial_payload',
    'render_context_payload',
    'continuation_payload',
    'invocation_source_aliases',
]
