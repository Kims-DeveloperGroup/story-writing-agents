"""Canonical context identities, evidence encodings, and fingerprints."""

from __future__ import annotations

import hashlib
import json
from ...provider_wire import WIRE_VERSION
from ...structured_output_validation import validation_failure
from ..actions import (
    ContextActionError,
    ContextActionErrorCode,
    ContextEvidenceReceipt,
    ContextSourceBoundary,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
)
from ..manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    validate_context_digest,
    validate_context_identity,
)
from .planning_inputs import MandatoryReferenceReservation


CONTEXT_WIRE_VERSION = WIRE_VERSION


ACTION_WIRE_CODES = {
    "search": "s",
    "read": "r",
    "answer": "a",
}


SOURCE_KIND_WIRE_CODES = {
    ContextSourceKind.WRITING_ARTIFACT: "w",
    ContextSourceKind.CONVERSATION: "c",
    ContextSourceKind.ACTIVE_DEBATE: "d",
    ContextSourceKind.OWN_AGENT_STATE: "o",
}


SEARCH_SOURCE_KIND_ORDER = tuple(SOURCE_KIND_WIRE_CODES)


SEMANTIC_CHANNEL_WIRE_CODES = {
    ContextSemanticChannel.CURRENT_INPUT: "i",
    ContextSemanticChannel.CANDIDATE: "k",
    ContextSemanticChannel.STABLE_BEHAVIOR: "s",
    ContextSemanticChannel.HARD_CONSTRAINT: "h",
    ContextSemanticChannel.FOCUSED_EVIDENCE: "f",
    ContextSemanticChannel.RETRIEVED_EVIDENCE: "r",
    ContextSemanticChannel.HISTORY: "y",
    ContextSemanticChannel.OPTIONAL_BACKGROUND: "b",
}


def action_error(code: ContextActionErrorCode) -> ContextActionError:
    paths_and_rules = {
        ContextActionErrorCode.CORRELATION_MISMATCH: ("$", "action correlation must match the current request"),
        ContextActionErrorCode.INVALID_TRANSITION: ("$.kind", "action must be allowed by the current phase and completed read state"),
        ContextActionErrorCode.ACTION_LIMIT_EXCEEDED: ("$.kind", "action count must remain within the disclosed invocation budget"),
        ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED: ("$.queries", "search count, query count, and requested results must fit the remaining search budget"),
        ContextActionErrorCode.READ_LIMIT_EXCEEDED: ("$.handles", "read count, handle count, and source bytes must fit the remaining read budget"),
        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED: ("$.handles", "selected source observations must fit the remaining context budget"),
        ContextActionErrorCode.HANDLE_INVALID: ("$.handles", "expected unconsumed source handles issued by this invocation's search"),
        ContextActionErrorCode.OUTSTANDING_READ: ("$.kind", "all outstanding read observations must be delivered before another action"),
        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED: ("$.result", "answer requires completed mandatory reads and current source authorization"),
    }
    failure = ContextActionError(code)
    if code in paths_and_rules:
        path, rule = paths_and_rules[code]
        validation_failure(failure, path, rule)
    return failure


def canonical_bytes(value: object) -> bytes:
    encoded: bytes | None = None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        pass
    if encoded is None:
        raise action_error(ContextActionErrorCode.INVALID_VALUE)
    return encoded


def boundary_value(boundary: ContextSourceBoundary) -> tuple[object, ...]:
    return (
        boundary.kind.value,
        boundary.start,
        boundary.end,
        boundary.first_id,
        boundary.last_id,
    )


def scope_value(scope: ContextScope) -> tuple[str, ...]:
    return (
        scope.workspace_id,
        scope.session_id,
        scope.subject_kind.value,
        scope.subject_id,
        scope.agent_id,
    )


def entry_value(entry: ContextManifestEntry) -> tuple[object, ...]:
    return (
        entry.source_id,
        entry.source_kind.value,
        entry.semantic_channel.value,
        entry.priority.value,
        entry.ordinal,
        entry.revision,
        entry.content_digest,
        entry.provenance.authority_kind.value,
        entry.provenance.locator,
        entry.utf8_bytes,
        entry.searchable,
    )


def manifest_fingerprint(manifest: ContextManifest) -> str:
    body = canonical_bytes(
        [
            "context-action-manifest-v1",
            scope_value(manifest.scope),
            *map(entry_value, manifest.entries),
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def authorized_source_fingerprint(
    entries: tuple[ContextManifestEntry, ...],
) -> str:
    """Bind stable source identity and non-self-appending revision facts."""

    body = canonical_bytes(
        [
            "context-zero-hit-authorized-sources-v1",
            [
                [
                    entry.source_id,
                    entry.source_kind.value,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.revision
                    ),
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.content_digest
                    ),
                    entry.provenance.authority_kind.value,
                    entry.provenance.locator,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.utf8_bytes
                    ),
                ]
                for entry in entries
            ],
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def evidence_value(receipt: ContextEvidenceReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.handle.value,
        receipt.source_id,
        receipt.source_kind.value,
        receipt.revision,
        receipt.provenance.authority_kind.value,
        receipt.provenance.locator,
        receipt.source_digest,
        receipt.source_utf8_bytes,
        boundary_value(receipt.boundary),
        receipt.boundary_digest,
        receipt.boundary_utf8_bytes,
        receipt.originating_search_action_index,
        receipt.completing_read_action_index,
    )


def status_value(receipt: ContextSourceStatusReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.source_id,
        receipt.locator,
        receipt.status.value,
        receipt.ordinal,
        receipt.manifest_digest,
        receipt.revision,
        receipt.source_digest,
        receipt.source_utf8_bytes,
    )


def zero_hit_value(receipt: ContextZeroHitSearchReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.correlation.action_index,
        receipt.manifest_digest,
        receipt.authorized_source_ids,
        tuple(item.value for item in receipt.authorized_source_kinds),
        receipt.authorized_source_fingerprint,
        receipt.artifact_path_prefix,
        receipt.query_digest,
        receipt.query_count,
    )


def completed_evidence_digest(
    evidence: tuple[ContextEvidenceReceipt, ...],
) -> str:
    values = []
    for receipt in evidence:
        boundary = receipt.boundary
        values.append(
            {
                "boundary": {
                    "end": boundary.end,
                    "first_id": boundary.first_id,
                    "kind": boundary.kind.value,
                    "last_id": boundary.last_id,
                    "start": boundary.start,
                },
                "boundary_digest": receipt.boundary_digest,
                "boundary_utf8_bytes": receipt.boundary_utf8_bytes,
                "completing_read_action_index": (receipt.completing_read_action_index),
                "evidence_id": receipt.evidence_id,
                "handle": receipt.handle.value,
                "originating_search_action_index": (
                    receipt.originating_search_action_index
                ),
                "provenance": {
                    "authority_kind": receipt.provenance.authority_kind.value,
                    "locator": receipt.provenance.locator,
                },
                "revision": receipt.revision,
                "source_digest": receipt.source_digest,
                "source_id": receipt.source_id,
                "source_kind": receipt.source_kind.value,
                "source_utf8_bytes": receipt.source_utf8_bytes,
            }
        )
    return digest(
        canonical_bytes({"evidence": values, "kind": "completed_context_reads_v1"})
    )


def reference_value(
    reference: MandatoryReferenceReservation,
) -> tuple[object, ...]:
    return (
        reference.ordinal,
        reference.reference_id,
        reference.source_id,
        reference.revision,
        reference.provenance.authority_kind.value,
        reference.provenance.locator,
        reference.content_digest,
        reference.utf8_bytes,
    )


def safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


def safe_digest(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


__all__ = [
    'CONTEXT_WIRE_VERSION',
    'ACTION_WIRE_CODES',
    'SOURCE_KIND_WIRE_CODES',
    'SEARCH_SOURCE_KIND_ORDER',
    'SEMANTIC_CHANNEL_WIRE_CODES',
    'action_error',
    'canonical_bytes',
    'boundary_value',
    'scope_value',
    'entry_value',
    'manifest_fingerprint',
    'authorized_source_fingerprint',
    'digest',
    'evidence_value',
    'status_value',
    'zero_hit_value',
    'completed_evidence_digest',
    'reference_value',
    'safe_identity',
    'safe_digest',
]
