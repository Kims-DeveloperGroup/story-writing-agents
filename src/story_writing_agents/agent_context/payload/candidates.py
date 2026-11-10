"""Pure candidate record selection and wire encoding."""

from __future__ import annotations

import json
from ..contracts.checkpoints import (
    ContextCatalogCursor,
    ContextCheckpointSource,
    ContextCheckpointView,
)
from ..contracts.context_values import digest
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.planning_inputs import (
    ContextCheckpointState,
    ContextDeliveryMode,
    ContextMaterial,
    ContextRecordMaterial,
)
from ..contracts.projection import ContextProjection
from ..manifest import ContextSourceKind
from .actions import (
    DuplicateJsonKey,
    unique_json_object,
)
from .records import source_number
from dataclasses import (
    dataclass,
    field,
)


@dataclass(frozen=True, slots=True)
class _WireCandidateRecord:
    """Exact candidate content addressed by one persistent debate record."""

    source_alias: str
    record_alias: int
    content: str = field(repr=False)
    content_digest: str
    utf8_bytes: int

    def __post_init__(self) -> None:
        source_number(self.source_alias)
        if type(self.record_alias) is not int or self.record_alias < 1:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.content) is not str:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        try:
            encoded = self.content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if digest(encoded) != self.content_digest or len(encoded) != self.utf8_bytes:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _debate_candidate_text(material: ContextRecordMaterial) -> str | None:
    """Return one exact canonical debate candidate record body, when present."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        type(value) is not dict
        or value.get("kind") != "candidate"
        or type(value.get("content")) is not str
    ):
        return None
    content = value["content"]
    assert isinstance(content, str)
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return content


def _latest_debate_candidate_record(
    sources: tuple[ContextCheckpointSource, ...],
    catalog_sources: tuple[ContextCatalogCursor, ...],
    materials: tuple[ContextMaterial, ...],
) -> _WireCandidateRecord | None:
    """Resolve the latest provable candidate from content-free checkpoint facts."""

    debate_sources = tuple(
        source
        for source in sources
        if source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE
    )
    if len(debate_sources) != 1:
        return None
    source = debate_sources[0]
    catalog = next(
        (
            item
            for item in catalog_sources
            if item.entry.source_id == source.entry.source_id
        ),
        None,
    )
    material = next(
        (item for item in materials if item.entry.source_id == source.entry.source_id),
        None,
    )
    if catalog is None or material is None:
        return None
    material_by_id = {record.cursor.record_id: record for record in material.records}
    candidate: _WireCandidateRecord | None = None
    for accepted in source.records:
        record = material_by_id.get(accepted.cursor.record_id)
        if record is None or record.cursor != accepted.cursor:
            continue
        content = _debate_candidate_text(record)
        if content is None:
            continue
        encoded = content.encode("utf-8", errors="strict")
        candidate = _WireCandidateRecord(
            catalog.source_alias,
            accepted.record_alias,
            content,
            digest(encoded),
            len(encoded),
        )
    return candidate


def _candidate_single_splice(
    base: str,
    target: str,
) -> tuple[int, int, str]:
    """Return the maximal nonoverlapping code-point prefix/suffix splice."""

    prefix = 0
    shared_limit = min(len(base), len(target))
    while prefix < shared_limit and base[prefix] == target[prefix]:
        prefix += 1
    suffix = 0
    remaining_base = len(base) - prefix
    remaining_target = len(target) - prefix
    while (
        suffix < remaining_base
        and suffix < remaining_target
        and base[len(base) - suffix - 1] == target[len(target) - suffix - 1]
    ):
        suffix += 1
    replacement_end = len(target) - suffix if suffix else len(target)
    return prefix, suffix, target[prefix:replacement_end]


def _wire_candidate_facts(record: _WireCandidateRecord) -> dict[str, object]:
    """Project one self-describing candidate record identity and byte facts."""

    return {
        "source_alias": source_number(record.source_alias),
        "record_alias": record.record_alias,
        "digest": record.content_digest,
        "utf8_bytes": record.utf8_bytes,
    }


def wire_candidate_value(
    candidate: str,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    materials: tuple[ContextMaterial, ...],
) -> object:
    """Offer an exact full candidate or an eligible, verified reuse representation."""

    if (
        projection.mode is not ContextDeliveryMode.DELTA
        or checkpoint.state is not ContextCheckpointState.READY
        or checkpoint.provider_thread_id is None
    ):
        return candidate
    base = _latest_debate_candidate_record(
        checkpoint.sources,
        checkpoint.catalog_sources,
        materials,
    )
    target = _latest_debate_candidate_record(
        projection.checkpoint_proposal.sources,
        projection.checkpoint_proposal.catalog_sources,
        materials,
    )
    if base is None or target is None or target.content != candidate:
        return candidate
    base_facts = _wire_candidate_facts(base)
    target_facts = _wire_candidate_facts(target)
    candidate_bytes = candidate.encode("utf-8", errors="strict")
    if base.content == target.content == candidate:
        pointer: object = {
            "op": "candidate_pointer",
            "base": base_facts,
            "target": target_facts,
            "result": "candidate_text",
            "resolve_record_candidate": "logical_n",
        }
        return pointer
    prefix, suffix, replacement = _candidate_single_splice(
        base.content,
        candidate,
    )
    retained_suffix = base.content[len(base.content) - suffix :] if suffix else ""
    reconstructed = base.content[:prefix] + replacement + retained_suffix
    if (
        reconstructed.encode("utf-8", errors="strict") != candidate_bytes
        or digest(candidate_bytes) != target.content_digest
        or len(candidate_bytes) != target.utf8_bytes
    ):
        return candidate
    patch: object = {
        "op": "candidate_single_splice",
        "base": base_facts,
        "prefix_codepoints": prefix,
        "replacement": replacement,
        "suffix_codepoints": suffix,
        "target": target_facts,
        "result": "candidate_text",
        "resolve_record_candidate": "logical_n",
    }
    return patch


__all__ = [
    'wire_candidate_value',
]
