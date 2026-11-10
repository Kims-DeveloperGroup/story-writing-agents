"""Checkpoint wire."""

from __future__ import annotations

import hashlib
from ..contracts.checkpoints import (
    ContextCatalogCursor,
    ContextCheckpointRecord,
    ContextCheckpointSource,
)
from ..contracts.planning_inputs import ContextPlanningConditions, ContextRecordCursor
from ..contracts.projection import ContextCheckpointProposal
from .record_validation import (
    ContextLedgerError,
    ContextLedgerErrorCode,
    LedgerDecodeError,
    MAX_CONTEXT_LEDGER_SOURCES,
    ledger_canonical_json,
    ledger_decoded_boolean,
    ledger_decoded_dict,
    ledger_decoded_enum,
    ledger_decoded_integer,
    ledger_decoded_list,
    ledger_decoded_optional_integer,
    ledger_decoded_optional_string,
    ledger_decoded_string,
    ledger_invalid_value,
)
from ..manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)


def ledger_scope_value(scope: ContextScope) -> dict[str, object]:
    return {
        "agent_id": scope.agent_id,
        "session_id": scope.session_id,
        "subject_id": scope.subject_id,
        "subject_kind": scope.subject_kind.value,
        "workspace_id": scope.workspace_id,
    }

def ledger_decode_scope(value: object) -> ContextScope:
    item = ledger_decoded_dict(
        value,
        frozenset(
            {"workspace_id", "session_id", "subject_kind", "subject_id", "agent_id"}
        ),
    )
    return ContextScope(
        workspace_id=ledger_decoded_string(item["workspace_id"]),
        session_id=ledger_decoded_string(item["session_id"]),
        subject_kind=ledger_decoded_enum(ContextSubjectKind, item["subject_kind"]),
        subject_id=ledger_decoded_string(item["subject_id"]),
        agent_id=ledger_decoded_string(item["agent_id"]),
    )

def _provenance_value(value: ContextProvenance) -> dict[str, object]:
    return {
        "authority_kind": value.authority_kind.value,
        "locator": value.locator,
    }

def _decode_provenance(value: object) -> ContextProvenance:
    item = ledger_decoded_dict(value, frozenset({"authority_kind", "locator"}))
    return ContextProvenance(
        authority_kind=ledger_decoded_enum(
            ContextAuthorityKind,
            item["authority_kind"],
        ),
        locator=ledger_decoded_string(item["locator"]),
    )

def _entry_value(entry: ContextManifestEntry) -> dict[str, object]:
    return {
        "content_digest": entry.content_digest,
        "ordinal": entry.ordinal,
        "priority": entry.priority.value,
        "provenance": _provenance_value(entry.provenance),
        "revision": entry.revision,
        "searchable": entry.searchable,
        "semantic_channel": entry.semantic_channel.value,
        "source_id": entry.source_id,
        "source_kind": entry.source_kind.value,
        "utf8_bytes": entry.utf8_bytes,
    }

def _decode_entry(value: object) -> ContextManifestEntry:
    item = ledger_decoded_dict(
        value,
        frozenset(
            {
                "source_id",
                "source_kind",
                "semantic_channel",
                "priority",
                "ordinal",
                "revision",
                "content_digest",
                "provenance",
                "utf8_bytes",
                "searchable",
            }
        ),
    )
    return ContextManifestEntry(
        source_id=ledger_decoded_string(item["source_id"]),
        source_kind=ledger_decoded_enum(ContextSourceKind, item["source_kind"]),
        semantic_channel=ledger_decoded_enum(
            ContextSemanticChannel,
            item["semantic_channel"],
        ),
        priority=ledger_decoded_enum(ContextPriority, item["priority"]),
        ordinal=ledger_decoded_integer(item["ordinal"]),
        revision=ledger_decoded_string(item["revision"]),
        content_digest=ledger_decoded_string(item["content_digest"]),
        provenance=_decode_provenance(item["provenance"]),
        utf8_bytes=ledger_decoded_integer(item["utf8_bytes"]),
        searchable=ledger_decoded_boolean(item["searchable"]),
    )

def ledger_conditions_value(value: ContextPlanningConditions) -> dict[str, object]:
    return {
        "definition_fingerprint": value.definition_fingerprint,
        "policy_fingerprint": value.policy_fingerprint,
        "provider_id": value.provider_id,
        "schema_version": value.schema_version,
        "scope": ledger_scope_value(value.scope),
    }

def ledger_decode_conditions(value: object) -> ContextPlanningConditions:
    item = ledger_decoded_dict(
        value,
        frozenset(
            {
                "scope",
                "provider_id",
                "definition_fingerprint",
                "policy_fingerprint",
                "schema_version",
            }
        ),
    )
    return ContextPlanningConditions(
        scope=ledger_decode_scope(item["scope"]),
        provider_id=ledger_decoded_string(item["provider_id"]),
        definition_fingerprint=ledger_decoded_string(item["definition_fingerprint"]),
        policy_fingerprint=ledger_decoded_string(item["policy_fingerprint"]),
        schema_version=ledger_decoded_integer(item["schema_version"]),
    )

def _record_cursor_value(value: ContextRecordCursor) -> dict[str, object]:
    return {
        "boundary": list(value.boundary),
        "content_digest": value.content_digest,
        "record_id": value.record_id,
        "utf8_bytes": value.utf8_bytes,
    }

def _decode_record_cursor(value: object) -> ContextRecordCursor:
    item = ledger_decoded_dict(
        value,
        frozenset({"record_id", "boundary", "content_digest", "utf8_bytes"}),
    )
    boundary = ledger_decoded_list(item["boundary"])
    if len(boundary) != 5:
        raise LedgerDecodeError
    return ContextRecordCursor(
        record_id=ledger_decoded_string(item["record_id"]),
        boundary_kind=ledger_decoded_string(boundary[0]),
        boundary_start=ledger_decoded_optional_integer(boundary[1]),
        boundary_end=ledger_decoded_optional_integer(boundary[2]),
        boundary_first_id=ledger_decoded_optional_string(boundary[3]),
        boundary_last_id=ledger_decoded_optional_string(boundary[4]),
        content_digest=ledger_decoded_string(item["content_digest"]),
        utf8_bytes=ledger_decoded_integer(item["utf8_bytes"]),
    )

def _checkpoint_record_value(value: ContextCheckpointRecord) -> dict[str, object]:
    return {
        **_record_cursor_value(value.cursor),
        "record_alias": value.record_alias,
    }

def _decode_checkpoint_record(value: object) -> ContextCheckpointRecord:
    item = ledger_decoded_dict(
        value,
        frozenset(
            {
                "record_alias",
                "record_id",
                "boundary",
                "content_digest",
                "utf8_bytes",
            }
        ),
    )
    return ContextCheckpointRecord(
        _decode_record_cursor(
            {
                "record_id": item["record_id"],
                "boundary": item["boundary"],
                "content_digest": item["content_digest"],
                "utf8_bytes": item["utf8_bytes"],
            }
        ),
        ledger_decoded_integer(item["record_alias"]),
    )

def ledger_checkpoint_source_value(value: ContextCheckpointSource) -> dict[str, object]:
    return {
        "entry": _entry_value(value.entry),
        "records": [
            _checkpoint_record_value(record) for record in value.records
        ],
    }

def ledger_decode_checkpoint_source(value: object) -> ContextCheckpointSource:
    item = ledger_decoded_dict(
        value,
        frozenset({"entry", "records"}),
    )
    records = ledger_decoded_list(item["records"])
    if len(records) > 10_000:
        raise LedgerDecodeError
    source = ContextCheckpointSource(
        _decode_entry(item["entry"]),
        tuple(_decode_checkpoint_record(record) for record in records),
    )
    return source

def ledger_catalog_cursor_value(value: ContextCatalogCursor) -> dict[str, object]:
    return {
        "entry": _entry_value(value.entry),
        "record_alias_high_watermark": value.record_alias_high_watermark,
        "source_alias": value.source_alias,
    }

def ledger_decode_catalog_cursor(value: object) -> ContextCatalogCursor:
    item = ledger_decoded_dict(
        value,
        frozenset(
            {"entry", "record_alias_high_watermark", "source_alias"}
        ),
    )
    return ContextCatalogCursor(
        _decode_entry(item["entry"]),
        ledger_decoded_string(item["source_alias"]),
        ledger_decoded_integer(item["record_alias_high_watermark"]),
    )

def _proposal_value(value: ContextCheckpointProposal) -> dict[str, object]:
    return {
        **({"session_history_coverage": value.session_history_coverage}
           if value.session_history_coverage else {}),
        "catalog_sources": [
            ledger_catalog_cursor_value(source) for source in value.catalog_sources
        ],
        "conditions": ledger_conditions_value(value.conditions),
        "delta_depth": value.delta_depth,
        "source_alias_high_watermark": value.source_alias_high_watermark,
        "sources": [ledger_checkpoint_source_value(source) for source in value.sources],
    }

def context_checkpoint_proposal_digest(
    proposal: ContextCheckpointProposal,
) -> str:
    """Hash the complete canonical content-free checkpoint proposal."""

    if type(proposal) is not ContextCheckpointProposal:
        raise ledger_invalid_value()
    if len(proposal.sources) > MAX_CONTEXT_LEDGER_SOURCES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    if len(proposal.catalog_sources) > MAX_CONTEXT_LEDGER_SOURCES:
        raise ContextLedgerError(ContextLedgerErrorCode.LIMIT_EXCEEDED)
    digest = hashlib.sha256(ledger_canonical_json(_proposal_value(proposal))).hexdigest()
    return f"sha256:{digest}"
