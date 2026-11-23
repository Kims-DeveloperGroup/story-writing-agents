"""Conversation input evidence records and their value codecs."""

from __future__ import annotations

from dataclasses import dataclass

from ...authoring_admission import (
    AdmissionSource,
    AdmissionSourceKind,
    admission_digest,
)
from ...core_document_readiness import AuthoringTarget
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
)
from ...writing_artifacts import parse_artifact_references
from ..event_value_validation import (
    require_array_value as _array,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    optional_integer_value as _optional_integer,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    require_unique as _require_unique,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_enum_tuple as _validate_enum_tuple,
    validate_exact_tuple as _validate_exact_tuple,
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationInputState,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from .proposal_inputs import CollaborationInputRevision


@dataclass(frozen=True, slots=True)
class CollaborationEvidenceReceipt:
    """Content-free durable receipt for one runtime-authorized evidence fact."""

    evidence_id: str
    acquisition: CollaborationEvidenceAcquisition
    operation_id: str
    result_correlation_id: str
    manifest_digest: str
    source_id: str | None = None
    source_kind: CollaborationInputKind | None = None
    locator: str | None = None
    source_state: CollaborationInputState | None = None
    revision_digest: str | None = None
    content_digest: str | None = None
    utf8_bytes: int = 0
    boundary_kind: str | None = None
    boundary_start: int | None = None
    boundary_end: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    ordinal: int | None = None
    action_index: int | None = None
    origin_action_index: int | None = None
    query_digest: str | None = None
    query_count: int = 0
    authorized_source_ids: tuple[str, ...] = ()
    authorized_source_kinds: tuple[CollaborationInputKind, ...] = ()
    authorized_source_fingerprint: str | None = None
    artifact_path_prefix: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.evidence_id, label="evidence ID")
        _require_enum(
            self.acquisition,
            CollaborationEvidenceAcquisition,
            "evidence acquisition",
        )
        _validate_identifier(self.operation_id, label="evidence operation ID")
        _validate_identifier(
            self.result_correlation_id,
            label="evidence result correlation ID",
        )
        _validate_digest(self.manifest_digest, label="evidence manifest digest")
        _validate_non_negative_integer(self.utf8_bytes, label="evidence UTF-8 bytes")
        _validate_non_negative_integer(self.query_count, label="evidence query count")
        if type(self.authorized_source_ids) is not tuple:
            raise TypeError("authorized evidence source IDs must be an exact tuple")
        for source_id in self.authorized_source_ids:
            _validate_visible_text(
                source_id,
                label="authorized evidence source ID",
                maximum=4_096,
            )
        _require_unique(
            self.authorized_source_ids,
            label="authorized evidence source IDs",
        )
        _validate_enum_tuple(
            self.authorized_source_kinds,
            CollaborationInputKind,
            label="authorized evidence source kinds",
        )
        if self.artifact_path_prefix is not None:
            _validate_visible_text(
                self.artifact_path_prefix,
                label="evidence artifact path prefix",
                maximum=1_024,
            )
            if (
                not self.artifact_path_prefix.endswith("/")
                or self.artifact_path_prefix.startswith("/")
                or "\\" in self.artifact_path_prefix
                or ".." in self.artifact_path_prefix.split("/")
            ):
                raise ValueError("evidence artifact path prefix is invalid")
        if (self.start_line is None) is not (self.end_line is None):
            raise ValueError("evidence line range must provide both bounds or neither")
        if self.start_line is not None and self.end_line is not None:
            _validate_positive_integer(self.start_line, label="evidence start line")
            _validate_positive_integer(self.end_line, label="evidence end line")
            if self.end_line < self.start_line:
                raise ValueError("evidence line range must be ordered")
        if self.boundary_kind not in {None, "whole", "artifact_lines", "records"}:
            raise ValueError("evidence boundary kind is invalid")
        if (self.boundary_start is None) is not (self.boundary_end is None):
            raise ValueError("evidence boundary must provide both bounds or neither")
        if self.boundary_start is not None and self.boundary_end is not None:
            _validate_positive_integer(
                self.boundary_start,
                label="evidence boundary start",
            )
            _validate_positive_integer(
                self.boundary_end,
                label="evidence boundary end",
            )
            if self.boundary_end < self.boundary_start:
                raise ValueError("evidence boundary must be ordered")
        if self.boundary_kind == "whole":
            if self.boundary_start is not None:
                raise ValueError("whole-source evidence cannot carry boundary bounds")
        elif self.boundary_kind is not None and self.boundary_start is None:
            raise ValueError("ranged evidence requires exact boundary bounds")
        if self.boundary_kind == "artifact_lines" and (
            self.start_line != self.boundary_start or self.end_line != self.boundary_end
        ):
            raise ValueError("artifact evidence line and boundary ranges must match")
        if self.boundary_kind != "artifact_lines" and self.start_line is not None:
            raise ValueError("only artifact-line evidence can carry a line range")
        for value, label in (
            (self.revision_digest, "evidence revision digest"),
            (self.content_digest, "evidence content digest"),
            (self.query_digest, "evidence query digest"),
            (
                self.authorized_source_fingerprint,
                "authorized evidence source fingerprint",
            ),
        ):
            if value is not None:
                _validate_digest(value, label=label)
        for value, label in (
            (self.ordinal, "evidence ordinal"),
            (self.action_index, "evidence action index"),
            (self.origin_action_index, "evidence origin action index"),
        ):
            if value is not None:
                _validate_positive_integer(value, label=label)
        self._validate_shape()

    def _validate_shape(self) -> None:
        single_source = self.acquisition is not (
            CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
        )
        if single_source:
            if (
                self.source_id is None
                or self.source_kind is None
                or self.locator is None
                or self.source_state is None
            ):
                raise ValueError("source evidence must retain its exact identity")
            _validate_visible_text(
                self.source_id,
                label="evidence source ID",
                maximum=4_096,
            )
            _require_enum(
                self.source_kind,
                CollaborationInputKind,
                "evidence source kind",
            )
            _validate_visible_text(
                self.locator,
                label="evidence locator",
                maximum=4_096,
            )
            _require_enum(
                self.source_state,
                CollaborationInputState,
                "evidence source state",
            )
            if self.authorized_source_ids or self.authorized_source_kinds:
                raise ValueError("single-source evidence cannot carry search authority")
            if self.artifact_path_prefix is not None:
                raise ValueError("single-source evidence cannot carry a path prefix")
            if self.authorized_source_fingerprint is not None:
                raise ValueError(
                    "single-source evidence cannot carry a source fingerprint"
                )
        elif (
            any(
                value is not None
                for value in (
                    self.source_id,
                    self.source_kind,
                    self.locator,
                    self.revision_digest,
                    self.content_digest,
                    self.start_line,
                    self.end_line,
                    self.boundary_kind,
                    self.boundary_start,
                    self.boundary_end,
                    self.ordinal,
                    self.origin_action_index,
                )
            )
            or self.source_state is not CollaborationInputState.ABSENT
        ):
            raise ValueError("zero-hit evidence cannot claim one source")

        if self.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE:
            if (
                self.ordinal is None
                or self.action_index is not None
                or self.origin_action_index is not None
                or self.query_digest is not None
                or self.query_count
                or self.source_state is not CollaborationInputState.PRESENT
                or self.revision_digest is None
                or self.content_digest is None
            ):
                raise ValueError("attached-reference evidence facts are incomplete")
        elif self.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS:
            if (
                self.ordinal is None
                or self.action_index is not None
                or self.origin_action_index is not None
                or self.query_digest is not None
                or self.query_count
                or self.start_line is not None
                or self.boundary_kind is not None
            ):
                raise ValueError("core-status evidence facts are inconsistent")
            if self.source_state is CollaborationInputState.ABSENT:
                if (
                    self.revision_digest is not None
                    or self.content_digest is not None
                    or self.utf8_bytes
                ):
                    raise ValueError("absent core evidence cannot claim content")
            elif self.revision_digest is None or self.content_digest is None:
                raise ValueError("present core evidence requires exact digests")
        elif self.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH:
            if (
                self.action_index is None
                or self.query_digest is None
                or self.authorized_source_fingerprint is None
                or self.query_count < 1
                or not self.authorized_source_kinds
                or self.utf8_bytes
                or (
                    self.artifact_path_prefix is not None
                    and self.authorized_source_kinds
                    != (CollaborationInputKind.WRITING_ARTIFACT,)
                )
            ):
                raise ValueError("zero-hit SEARCH evidence facts are incomplete")
        elif (
            self.action_index is None
            or self.origin_action_index is None
            or self.origin_action_index >= self.action_index
            or self.ordinal is not None
            or self.query_digest is not None
            or self.query_count
            or self.source_state is not CollaborationInputState.PRESENT
            or self.revision_digest is None
            or self.content_digest is None
            or self.boundary_kind is None
        ):
            raise ValueError("READ evidence facts are incomplete")


@dataclass(frozen=True, slots=True)
class CollaborationLeadEvidence:
    """Exact ordered evidence classification for one lead judgment."""

    scope: LeadOutcomeScope
    status: LeadEvidenceStatus
    receipts: tuple[CollaborationEvidenceReceipt, ...]

    def __post_init__(self) -> None:
        _require_enum(self.scope, LeadOutcomeScope, "lead evidence scope")
        _require_enum(self.status, LeadEvidenceStatus, "lead evidence status")
        _validate_exact_tuple(
            self.receipts,
            CollaborationEvidenceReceipt,
            label="lead evidence receipts",
        )
        _require_unique(
            tuple(item.evidence_id for item in self.receipts),
            label="lead evidence receipt IDs",
        )
        correlations = {
            (item.operation_id, item.result_correlation_id, item.manifest_digest)
            for item in self.receipts
        }
        if len(correlations) > 1:
            raise ValueError("lead evidence receipts must share one invocation")

    def factual_writing_read_facts(self) -> tuple[tuple[str, str, bool], ...]:
        """Project nonempty writing READ receipts without granting source authority.

        The boolean describes the receipt boundary: an explicit range remains
        ranged even when its content happens to cover the entire source.
        """
        facts: list[tuple[str, str, bool]] = []
        for receipt in self.receipts:
            if (receipt.acquisition not in {
                    CollaborationEvidenceAcquisition.READ,
                    CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                } or receipt.source_state is not CollaborationInputState.PRESENT
                    or receipt.utf8_bytes <= 0 or receipt.source_kind not in {
                        CollaborationInputKind.CORE_DOCUMENT,
                        CollaborationInputKind.MANUSCRIPT,
                        CollaborationInputKind.WRITING_ARTIFACT,
                    }):
                continue
            path = receipt.locator
            whole_source = receipt.boundary_kind == "whole"
            if receipt.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE:
                references = parse_artifact_references(receipt.locator).references
                if len(references) != 1 or references[0].token != receipt.locator:
                    raise ValueError("factual READ reference is not exact")
                path = references[0].path
                whole_source = references[0].line_range is None
            facts.append((receipt.evidence_id, path, whole_source))
        return tuple(facts)


def validate_planning_target(target, kind, scope) -> None:
    if target is not None and (type(target) is not AuthoringTarget or kind is not LeadOutcomeKind.QUESTION
            or scope is None or scope.intent is not AuthoringIntent.AUTHORING or scope.target_paths
            or scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}):
        raise ValueError("planning target is classification of an untargeted formal-writing question only")


def authoring_source_facts(
    inputs: tuple[CollaborationInputRevision, ...],
    evidence: CollaborationLeadEvidence | None,
    *, selected_utf8_bytes: dict[str, int | None] | None = None,
) -> tuple[AdmissionSource, ...]:
    """Describe already host-qualified inputs; file indicators are not READs."""
    facts = []
    lengths = {} if selected_utf8_bytes is None else selected_utf8_bytes
    for item in inputs:
        if item.state is not CollaborationInputState.PRESENT:
            continue
        if item.kind is CollaborationInputKind.USER_INPUT:
            facts.append(AdmissionSource(item.input_id, AdmissionSourceKind.USER,
                item.locator, item.revision_digest, "literal_user", lengths.get(item.input_id)))
        elif item.kind in {CollaborationInputKind.CONVERSATION_EVENT,
                           CollaborationInputKind.ARTIFACT_CANDIDATE}:
            reads = () if evidence is None else tuple(
                receipt for receipt in evidence.receipts
                if receipt.acquisition is CollaborationEvidenceAcquisition.READ
                and receipt.evidence_id == item.input_id
            )
            if reads:
                if len(reads) != 1 or sum(
                    other.input_id == item.input_id for other in inputs
                ) != 1:
                    raise ValueError("authoring admission source identities collide")
                receipt = reads[0]
                if (
                    receipt.source_state is not CollaborationInputState.PRESENT
                    or receipt.source_kind is not item.kind
                    or receipt.locator != item.locator
                    or receipt.content_digest != item.revision_digest
                    or receipt.start_line != item.start_line
                    or receipt.end_line != item.end_line
                    or receipt.boundary_kind not in {"whole", "records", "artifact_lines"}
                    or (
                        receipt.boundary_kind == "artifact_lines"
                        and (receipt.boundary_start, receipt.boundary_end)
                        != (item.start_line, item.end_line)
                    )
                ):
                    raise ValueError("authoring admission READ input is unbound")
            else:
                facts.append(AdmissionSource(item.input_id, AdmissionSourceKind.MATERIAL,
                    item.locator, item.revision_digest, "input_only", lengths.get(item.input_id)))
    if evidence is not None:
        for receipt in evidence.receipts:
            if (receipt.acquisition in {CollaborationEvidenceAcquisition.READ,
                                        CollaborationEvidenceAcquisition.ATTACHED_REFERENCE}
                    and receipt.source_state is CollaborationInputState.PRESENT
                    and receipt.content_digest is not None):
                facts.append(AdmissionSource(receipt.evidence_id, AdmissionSourceKind.READ,
                    receipt.operation_id + "/" + receipt.result_correlation_id + "/" + receipt.evidence_id,
                    admission_digest(_evidence_receipt_value(receipt)), "actual_read", receipt.utf8_bytes))
    by_id = {item.source_id: item for item in facts}
    if len(by_id) != len(facts):
        raise ValueError("authoring admission source identities collide")
    return tuple(facts)


def _evidence_receipt_value(
    receipt: CollaborationEvidenceReceipt,
) -> dict[str, object]:
    return {
        "acquisition": receipt.acquisition.value,
        "action_index": receipt.action_index,
        "authorized_source_ids": list(receipt.authorized_source_ids),
        "authorized_source_fingerprint": receipt.authorized_source_fingerprint,
        "authorized_source_kinds": [
            item.value for item in receipt.authorized_source_kinds
        ],
        "artifact_path_prefix": receipt.artifact_path_prefix,
        "boundary_end": receipt.boundary_end,
        "boundary_kind": receipt.boundary_kind,
        "boundary_start": receipt.boundary_start,
        "content_digest": receipt.content_digest,
        "end_line": receipt.end_line,
        "evidence_id": receipt.evidence_id,
        "locator": receipt.locator,
        "manifest_digest": receipt.manifest_digest,
        "operation_id": receipt.operation_id,
        "ordinal": receipt.ordinal,
        "origin_action_index": receipt.origin_action_index,
        "query_count": receipt.query_count,
        "query_digest": receipt.query_digest,
        "result_correlation_id": receipt.result_correlation_id,
        "revision_digest": receipt.revision_digest,
        "source_id": receipt.source_id,
        "source_kind": (
            None if receipt.source_kind is None else receipt.source_kind.value
        ),
        "source_state": (
            None if receipt.source_state is None else receipt.source_state.value
        ),
        "start_line": receipt.start_line,
        "utf8_bytes": receipt.utf8_bytes,
    }


def lead_evidence_value(evidence: CollaborationLeadEvidence) -> dict[str, object]:
    return {
        "receipts": [_evidence_receipt_value(item) for item in evidence.receipts],
        "scope": evidence.scope.value,
        "status": evidence.status.value,
    }


def _decode_evidence_receipt(value: object) -> CollaborationEvidenceReceipt:
    fields = {
        "acquisition",
        "action_index",
        "authorized_source_ids",
        "authorized_source_fingerprint",
        "authorized_source_kinds",
        "artifact_path_prefix",
        "boundary_end",
        "boundary_kind",
        "boundary_start",
        "content_digest",
        "end_line",
        "evidence_id",
        "locator",
        "manifest_digest",
        "operation_id",
        "ordinal",
        "origin_action_index",
        "query_count",
        "query_digest",
        "result_correlation_id",
        "revision_digest",
        "source_id",
        "source_kind",
        "source_state",
        "start_line",
        "utf8_bytes",
    }
    item = _object(value, fields, label="collaboration evidence receipt")
    source_kind = item["source_kind"]
    source_state = item["source_state"]
    return CollaborationEvidenceReceipt(
        evidence_id=_string(item["evidence_id"], label="evidence ID"),
        acquisition=_enum(
            CollaborationEvidenceAcquisition,
            item["acquisition"],
            label="evidence acquisition",
        ),
        operation_id=_string(item["operation_id"], label="evidence operation ID"),
        result_correlation_id=_string(
            item["result_correlation_id"],
            label="evidence result correlation ID",
        ),
        manifest_digest=_string(
            item["manifest_digest"],
            label="evidence manifest digest",
        ),
        source_id=_optional_string(item["source_id"], label="evidence source ID"),
        source_kind=(
            None
            if source_kind is None
            else _enum(
                CollaborationInputKind,
                source_kind,
                label="evidence source kind",
            )
        ),
        locator=_optional_string(item["locator"], label="evidence locator"),
        source_state=(
            None
            if source_state is None
            else _enum(
                CollaborationInputState,
                source_state,
                label="evidence source state",
            )
        ),
        revision_digest=_optional_string(
            item["revision_digest"],
            label="evidence revision digest",
        ),
        content_digest=_optional_string(
            item["content_digest"],
            label="evidence content digest",
        ),
        utf8_bytes=_integer(item["utf8_bytes"], label="evidence UTF-8 bytes"),
        boundary_kind=_optional_string(
            item["boundary_kind"],
            label="evidence boundary kind",
        ),
        boundary_start=_optional_integer(
            item["boundary_start"],
            label="evidence boundary start",
        ),
        boundary_end=_optional_integer(
            item["boundary_end"],
            label="evidence boundary end",
        ),
        start_line=_optional_integer(item["start_line"], label="evidence start line"),
        end_line=_optional_integer(item["end_line"], label="evidence end line"),
        ordinal=_optional_integer(item["ordinal"], label="evidence ordinal"),
        action_index=_optional_integer(
            item["action_index"], label="evidence action index"
        ),
        origin_action_index=_optional_integer(
            item["origin_action_index"],
            label="evidence origin action index",
        ),
        query_digest=_optional_string(
            item["query_digest"], label="evidence query digest"
        ),
        query_count=_integer(item["query_count"], label="evidence query count"),
        authorized_source_ids=tuple(
            _string(source_id, label="authorized evidence source ID")
            for source_id in _array(
                item["authorized_source_ids"],
                label="authorized evidence source IDs",
            )
        ),
        authorized_source_fingerprint=_optional_string(
            item["authorized_source_fingerprint"],
            label="authorized evidence source fingerprint",
        ),
        authorized_source_kinds=tuple(
            _enum(
                CollaborationInputKind,
                kind,
                label="authorized evidence source kind",
            )
            for kind in _array(
                item["authorized_source_kinds"],
                label="authorized evidence source kinds",
            )
        ),
        artifact_path_prefix=_optional_string(
            item["artifact_path_prefix"],
            label="evidence artifact path prefix",
        ),
    )


def decode_lead_evidence(value: object) -> CollaborationLeadEvidence:
    item = _object(
        value,
        {"receipts", "scope", "status"},
        label="lead evidence",
    )
    return CollaborationLeadEvidence(
        scope=_enum(LeadOutcomeScope, item["scope"], label="lead evidence scope"),
        status=_enum(
            LeadEvidenceStatus,
            item["status"],
            label="lead evidence status",
        ),
        receipts=tuple(
            _decode_evidence_receipt(receipt)
            for receipt in _array(item["receipts"], label="lead evidence receipts")
        ),
    )
