"""Pure construction contracts for lead-reviewed artifact proposals."""

from __future__ import annotations

import hashlib
import json
import re
from .structured_output_validation import (
    validation_failure,
    validation_call,
    carry_validation,
    raise_validation,
)
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NoReturn, TypeAlias

from .specialist_routing import specialists_for_domains
from .writing_artifacts import (
    ArtifactChangeOperation,
    ArtifactChangeOperationKind,
    ArtifactChangeSet,
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactKind,
    ArtifactPatchOperation,
    ArtifactRelationshipEdge,
    ArtifactRelationshipError,
    ArtifactRelationshipErrorCode,
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    MAX_RELATIONSHIP_EDGES,
    ArtifactRelationshipKind,
    ArtifactRelationshipValidation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactReviewDomain,
    ArtifactReviewFact,
    ArtifactReviewReason,
    ArtifactTextHunk,
    artifact_source_operation_rule,
    build_artifact_change_set,
    canonical_artifact_kind,
    deserialize_artifact_change_set,
    serialize_artifact_change_set,
    validate_artifact_relationships,
)


ARTIFACT_PROPOSAL_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_DOMAIN_ORDER = tuple(ArtifactReviewDomain)


class ArtifactProposalErrorCode(StrEnum):
    """Closed content-free failures for proposal construction and codecs."""

    INVALID_SCHEMA = "invalid_schema"
    INVALID_SERIALIZATION = "invalid_serialization"
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_INTENT = "invalid_intent"
    REVIEWER_MAPPING = "reviewer_mapping"
    CANDIDATE_DIGEST = "candidate_digest"


_ERROR_MESSAGES = {
    ArtifactProposalErrorCode.INVALID_SCHEMA: "artifact proposal schema is invalid",
    ArtifactProposalErrorCode.INVALID_SERIALIZATION: (
        "artifact proposal serialization is noncanonical or invalid"
    ),
    ArtifactProposalErrorCode.INVALID_IDENTIFIER: (
        "artifact proposal identity is invalid"
    ),
    ArtifactProposalErrorCode.INVALID_EVIDENCE: (
        "artifact proposal evidence is invalid or stale"
    ),
    ArtifactProposalErrorCode.INVALID_INTENT: "artifact proposal intent is invalid",
    ArtifactProposalErrorCode.REVIEWER_MAPPING: (
        "artifact proposal reviewer mapping is incomplete"
    ),
    ArtifactProposalErrorCode.CANDIDATE_DIGEST: (
        "artifact proposal candidate digest is invalid"
    ),
}


class ArtifactProposalError(RuntimeError):
    """Deterministic proposal failure whose full exception graph is content-free."""

    def __init__(
        self,
        code: ArtifactProposalErrorCode,
        *,
        operation_index: int | None = None,
    ) -> None:
        if type(code) is not ArtifactProposalErrorCode:
            raise TypeError("code must be an ArtifactProposalErrorCode")
        if operation_index is not None and (
            type(operation_index) is not int or operation_index < 0
        ):
            raise TypeError("operation_index must be nonnegative or None")
        self.code = code
        self.operation_index = operation_index
        super().__init__(_ERROR_MESSAGES[code])


def _raise_content_free_error(
    code: ArtifactProposalErrorCode,
    *,
    operation_index: int | None = None,
) -> NoReturn:
    """Raise one proposal error with no retained cause or exception context."""

    try:
        raise ArtifactProposalError(code, operation_index=operation_index) from None
    except ArtifactProposalError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


def _strict_text(value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not value and not allow_empty):
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
            '$',
            'expected text' if allow_empty else 'expected nonempty text',
        )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise_validation(
            validation_failure(
                ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
                '$',
                'expected strict UTF-8 text',
            ),
        )
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_IDENTIFIER),
            '$',
            'expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}',
        )
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE)
    return value


def _path(value: object) -> tuple[str, ArtifactKind]:
    try:
        kind = canonical_artifact_kind(value)
    except (TypeError, ValueError):
        kind = None
    if type(value) is not str or kind is None:
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
            '$',
            'expected one of core_documents/global_outline.md, '
            'core_documents/part_outlines.md, core_documents/character_sheets.md, '
            'core_documents/narrative_rules.md, or manuscript/PART/PATH.md; '
            'PART must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}; every remaining path '
            'component must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}, and the final '
            'component must end in lowercase .md; paths must be normalized '
            'workspace-relative POSIX text without backslash, NUL, . or .. '
            'components, and fit 1024 strict UTF-8 bytes',
        )
    return value, kind


@dataclass(frozen=True, slots=True)
class ArtifactCreateIntent:
    """Untrusted semantic intent to create one suggested canonical path."""

    suggested_path: str
    new_text: str = field(repr=False)

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.CREATE

    def __post_init__(self) -> None:
        validation_call('$.suggested_path', _path, self.suggested_path)
        validation_call('$.new_text', _strict_text, self.new_text, allow_empty=True)


@dataclass(frozen=True, slots=True)
class ArtifactPatchIntent:
    """Untrusted semantic intent to replace one authorized evidence boundary."""

    evidence_id: str
    replacement_text: str = field(repr=False)

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.PATCH

    def __post_init__(self) -> None:
        validation_call('$.evidence_id', _identifier, self.evidence_id)
        validation_call('$.replacement_text', _strict_text, self.replacement_text, allow_empty=True)


@dataclass(frozen=True, slots=True)
class ArtifactReplaceIntent:
    """Untrusted semantic intent to replace one authorized whole artifact."""

    evidence_id: str
    new_text: str = field(repr=False)

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.REPLACE

    def __post_init__(self) -> None:
        validation_call('$.evidence_id', _identifier, self.evidence_id)
        validation_call('$.new_text', _strict_text, self.new_text, allow_empty=True)


@dataclass(frozen=True, slots=True)
class ArtifactRenameIntent:
    """Untrusted semantic intent to rename one authorized whole artifact."""

    evidence_id: str
    suggested_destination: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.RENAME

    def __post_init__(self) -> None:
        validation_call('$.evidence_id', _identifier, self.evidence_id)
        validation_call('$.suggested_destination', _path, self.suggested_destination)


@dataclass(frozen=True, slots=True)
class ArtifactDeleteIntent:
    """Untrusted semantic intent to delete one authorized whole artifact."""

    evidence_id: str

    @property
    def operation_kind(self) -> ArtifactChangeOperationKind:
        return ArtifactChangeOperationKind.DELETE

    def __post_init__(self) -> None:
        validation_call('$.evidence_id', _identifier, self.evidence_id)


ArtifactChangeIntent: TypeAlias = (
    ArtifactCreateIntent
    | ArtifactPatchIntent
    | ArtifactReplaceIntent
    | ArtifactRenameIntent
    | ArtifactDeleteIntent
)
_INTENT_TYPES = (
    ArtifactCreateIntent,
    ArtifactPatchIntent,
    ArtifactReplaceIntent,
    ArtifactRenameIntent,
    ArtifactDeleteIntent,
)


@dataclass(frozen=True, slots=True)
class ArtifactProposalIntent:
    """Provider-neutral multi-operation intent and requested review domains."""

    operations: tuple[ArtifactChangeIntent, ...]
    additional_review_domains: tuple[ArtifactReviewDomain, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.operations) is not tuple
            or not self.operations
            or any(type(item) not in _INTENT_TYPES for item in self.operations)
        ):
            raise validation_failure(
                ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
                '$.operations',
                'expected a nonempty array of create, patch, replace, rename, or delete operations',
            )
        if (
            type(self.additional_review_domains) is not tuple
            or any(
                type(item) is not ArtifactReviewDomain
                for item in self.additional_review_domains
            )
            or len(set(self.additional_review_domains))
            != len(self.additional_review_domains)
            or self.additional_review_domains
            != tuple(
                item for item in _DOMAIN_ORDER if item in self.additional_review_domains
            )
        ):
            raise validation_failure(
                ArtifactProposalError(ArtifactProposalErrorCode.REVIEWER_MAPPING),
                '$.additional_review_domains',
                'expected unique review domains in canonical order',
            )


@dataclass(frozen=True, slots=True)
class ArtifactIntentEvidence:
    """Runtime-issued current artifact identity for one provider evidence ID."""

    evidence_id: str
    path: str
    source_digest: str
    boundary_digest: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.evidence_id)
        _path(self.path)
        _digest(self.source_digest)
        _digest(self.boundary_digest)
        if (self.start_line is None) is not (self.end_line is None):
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE)
        if self.start_line is not None and (
            type(self.start_line) is not int
            or type(self.end_line) is not int
            or self.start_line < 1
            or self.end_line < self.start_line
        ):
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE)

    @property
    def is_whole(self) -> bool:
        return self.start_line is None


@dataclass(frozen=True, slots=True)
class ArtifactReviewCandidate:
    """Exact immutable candidate shown unchanged to every disclosed reviewer."""

    schema_version: int
    candidate_id: str
    candidate_revision: int
    change_set: ArtifactChangeSet = field(repr=False)
    relationship_validation: ArtifactRelationshipValidation
    grounding_evidence_ids: tuple[str, ...]
    mandatory_review_domains: tuple[ArtifactReviewDomain, ...]
    disclosed_review_domains: tuple[ArtifactReviewDomain, ...]
    candidate_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != ARTIFACT_PROPOSAL_SCHEMA_VERSION
        ):
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
        _identifier(self.candidate_id)
        if type(self.candidate_revision) is not int or self.candidate_revision < 1:
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_IDENTIFIER)
        if (
            type(self.change_set) is not ArtifactChangeSet
            or type(self.relationship_validation) is not ArtifactRelationshipValidation
        ):
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
        relationship = self.relationship_validation
        if (
            relationship.base_state_digest != self.change_set.base_state_digest
            or relationship.projected_state_digest
            != self.change_set.projected_state_digest
            or relationship.change_set_digest != self.change_set.change_set_digest
            or not relationship.is_valid
        ):
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
        _identifier_tuple(self.grounding_evidence_ids, allow_empty=False)
        _review_domain_tuple(self.mandatory_review_domains, allow_empty=False)
        _review_domain_tuple(self.disclosed_review_domains, allow_empty=False)
        derived = _domains_from_facts(relationship.review_facts)
        if self.mandatory_review_domains != derived or not set(
            self.mandatory_review_domains
        ).issubset(self.disclosed_review_domains):
            raise ArtifactProposalError(ArtifactProposalErrorCode.REVIEWER_MAPPING)
        _digest(self.candidate_digest)
        if self.candidate_digest != _candidate_digest(self):
            raise ArtifactProposalError(ArtifactProposalErrorCode.CANDIDATE_DIGEST)


def artifact_review_specialists(
    domains: tuple[ArtifactReviewDomain, ...],
) -> tuple[str, ...]:
    """Map a canonical closed domain tuple to canonical specialist identities."""

    _review_domain_tuple(domains, allow_empty=False)
    return specialists_for_domains(domains)


def build_artifact_review_candidate(
    *,
    candidate_id: str,
    candidate_revision: int,
    intent: ArtifactProposalIntent,
    evidence: tuple[ArtifactIntentEvidence, ...],
    grounding_evidence_ids: tuple[str, ...],
    base_state: Mapping[str, bytes],
    attached_core_paths: tuple[str, ...],
) -> ArtifactReviewCandidate:
    """Resolve untrusted semantic intent against one exact authoritative state."""

    _identifier(candidate_id)
    if type(candidate_revision) is not int or candidate_revision < 1:
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_IDENTIFIER),
            '$',
            'candidate revision must be a positive integer',
        )
    if type(intent) is not ArtifactProposalIntent:
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
            '$.operations',
            'expected a nonempty array of artifact operations',
        )
    if type(evidence) is not tuple or any(
        type(item) is not ArtifactIntentEvidence for item in evidence
    ):
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE),
            '$.operations',
            'operations require current artifact evidence',
        )
    _identifier_tuple(grounding_evidence_ids, allow_empty=False)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    if len(evidence_by_id) != len(evidence):
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE),
            '$.operations',
            'runtime artifact evidence identifiers must be unique',
        )
    normalized_base = _normalize_base(base_state)
    evidence_ids_used = tuple(
        item.evidence_id
        for item in intent.operations
        if not isinstance(item, ArtifactCreateIntent)
    )
    if (
        len(set(evidence_ids_used)) != len(evidence_ids_used)
        or any(item not in evidence_by_id for item in evidence_ids_used)
        or not set(evidence_ids_used).issubset(grounding_evidence_ids)
    ):
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE),
            '$.operations',
            (
                'every evidence_id must be unique, offered by the runtime, and included in the '
                'current grounding evidence'
            ),
        )

    operations = _resolve_operations(intent.operations, evidence_by_id, normalized_base)
    try:
        change_set = build_artifact_change_set(normalized_base, operations)
        relationship = validate_artifact_relationships(
            normalized_base,
            change_set,
            attached_core_paths=attached_core_paths,
        )
    except (ArtifactChangeSetError, ArtifactRelationshipError) as failure:
        raise_validation(_operation_validation_failure(failure))
    if not relationship.is_valid:
        raise validation_failure(
            ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
            '$.operations',
            'operations must not leave dangling artifact references',
        )
    mandatory = _domains_from_facts(relationship.review_facts)
    disclosed = tuple(
        item
        for item in _DOMAIN_ORDER
        if item in mandatory or item in intent.additional_review_domains
    )
    digest = (
        "sha256:"
        + hashlib.sha256(
            _canonical_json(
                _candidate_fields_value(
                    schema_version=ARTIFACT_PROPOSAL_SCHEMA_VERSION,
                    candidate_id=candidate_id,
                    candidate_revision=candidate_revision,
                    change_set=change_set,
                    relationship_validation=relationship,
                    grounding_evidence_ids=grounding_evidence_ids,
                    mandatory_review_domains=mandatory,
                    disclosed_review_domains=disclosed,
                )
            )
        ).hexdigest()
    )
    return ArtifactReviewCandidate(
        schema_version=ARTIFACT_PROPOSAL_SCHEMA_VERSION,
        candidate_id=candidate_id,
        candidate_revision=candidate_revision,
        change_set=change_set,
        relationship_validation=relationship,
        grounding_evidence_ids=grounding_evidence_ids,
        mandatory_review_domains=mandatory,
        disclosed_review_domains=disclosed,
        candidate_digest=digest,
    )


def serialize_artifact_review_candidate(candidate: ArtifactReviewCandidate) -> bytes:
    """Return strict canonical JSON that intentionally contains proposed text."""

    _require_candidate(candidate)
    return _canonical_json(_candidate_value(candidate, include_digest=True))


def deserialize_artifact_review_candidate(data: bytes) -> ArtifactReviewCandidate:
    """Decode only the exact canonical content-bearing candidate form."""

    if type(data) is not bytes:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if _canonical_json(value) != data:
            raise ValueError
        root = _object(
            value,
            {
                "candidate_digest",
                "candidate_id",
                "candidate_revision",
                "change_set",
                "disclosed_review_domains",
                "grounding_evidence_ids",
                "mandatory_review_domains",
                "relationship_validation",
                "schema_version",
            },
        )
        change_set = deserialize_artifact_change_set(
            _canonical_json(root["change_set"])
        )
        relationship = _decode_relationship(root["relationship_validation"])
        candidate = ArtifactReviewCandidate(
            schema_version=_integer(root["schema_version"]),
            candidate_id=_string(root["candidate_id"]),
            candidate_revision=_integer(root["candidate_revision"]),
            change_set=change_set,
            relationship_validation=relationship,
            grounding_evidence_ids=_string_tuple(root["grounding_evidence_ids"]),
            mandatory_review_domains=tuple(
                ArtifactReviewDomain(item)
                for item in _string_tuple(root["mandatory_review_domains"])
            ),
            disclosed_review_domains=tuple(
                ArtifactReviewDomain(item)
                for item in _string_tuple(root["disclosed_review_domains"])
            ),
            candidate_digest=_string(root["candidate_digest"]),
        )
    except ArtifactProposalError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise
    except Exception:
        _raise_content_free_error(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    return candidate


def _normalize_base(base_state: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(base_state, Mapping):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
    try:
        items = tuple(base_state.items())
    except Exception:
        _raise_content_free_error(ArtifactProposalErrorCode.INVALID_SCHEMA)
    normalized: dict[str, bytes] = {}
    for path, content in items:
        canonical, _kind = _path(path)
        if type(content) is not bytes:
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _raise_content_free_error(ArtifactProposalErrorCode.INVALID_SCHEMA)
        normalized[canonical] = content
    if len(normalized) != len(items):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
    return dict(sorted(normalized.items(), key=lambda item: item[0].encode("utf-8")))



def _operation_validation_failure(
    failure: BaseException, *, operation_index: int | None = None,
) -> ArtifactProposalError:
    error = ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT, operation_index=operation_index)
    path = "$.operations" if operation_index is None else f"$.operations[{operation_index}]"
    rules = {
        ArtifactChangeSetErrorCode.INVALID_UTF8: "artifact text must be strict UTF-8",
        ArtifactChangeSetErrorCode.INVALID_PATH: "paths must be canonical core-document or manuscript .md paths",
        ArtifactChangeSetErrorCode.INVALID_KIND: "operation artifact kind must match its path",
        ArtifactChangeSetErrorCode.NO_OP: "new_text or replacement_text must change the selected artifact content",
        ArtifactChangeSetErrorCode.HUNK_OVERLAP: "patch evidence ranges must not overlap",
        ArtifactChangeSetErrorCode.DUPLICATE_INSERTION: "patches must not insert twice at the same byte boundary",
        ArtifactChangeSetErrorCode.HUNK_ORDER: "patch evidence ranges must occur in increasing byte order",
        ArtifactChangeSetErrorCode.PATH_COLLISION: "operation destinations must be unique and must not collide with existing artifacts",
        ArtifactChangeSetErrorCode.RENAME_CONFLICT: "rename source and destination must differ and cannot be reused by another operation",
        ArtifactChangeSetErrorCode.BASE_ARTIFACT_MISMATCH: "evidence_id must identify an artifact in the current base revision",
        ArtifactRelationshipErrorCode.PROTECTED_CORE_OPERATION: "core documents cannot be deleted or renamed",
        ArtifactRelationshipErrorCode.PATH_ESCAPE: "artifact references must remain inside canonical workspace artifact paths",
        ArtifactRelationshipErrorCode.ARTIFACT_COUNT_LIMIT: f"projected state must contain at most {MAX_RELATIONSHIP_ARTIFACTS} artifacts",
        ArtifactRelationshipErrorCode.ARTIFACT_SIZE_LIMIT: f"each artifact must contain at most {MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES} UTF-8 bytes",
        ArtifactRelationshipErrorCode.STATE_SIZE_LIMIT: f"projected artifact state must contain at most {MAX_RELATIONSHIP_STATE_UTF8_BYTES} UTF-8 bytes",
        ArtifactRelationshipErrorCode.EDGE_COUNT_LIMIT: f"projected state must contain at most {MAX_RELATIONSHIP_EDGES} artifact-reference edges",
    }
    code = failure.code if type(failure) in {ArtifactChangeSetError, ArtifactRelationshipError} else None
    if code in rules:
        return validation_failure(error, path, rules[code])
    return carry_validation(error, failure)

def _resolve_operations(
    intents: tuple[ArtifactChangeIntent, ...],
    evidence: Mapping[str, ArtifactIntentEvidence],
    base: Mapping[str, bytes],
    *,
    _source_rule=artifact_source_operation_rule,
) -> tuple[ArtifactChangeOperation, ...]:
    resolved: list[ArtifactChangeOperation] = []
    patches: dict[str, list[ArtifactTextHunk]] = {}
    patch_kinds: dict[str, ArtifactKind] = {}
    occupied: set[str] = set()
    for operation_index, intent in enumerate(intents):
        try:
            if type(intent) is ArtifactCreateIntent:
                path, kind = _path(intent.suggested_path)
                content = intent.new_text.encode("utf-8", errors="strict")
                resolved.append(
                    ArtifactCreateOperation(
                        path=path,
                        artifact_kind=kind,
                        content=content,
                        projected_digest=hashlib.sha256(content).hexdigest(),
                    )
                )
                occupied.add(path)
                continue
            item = evidence[intent.evidence_id]
            current = base.get(item.path)
            if current is None or (
                "sha256:" + hashlib.sha256(current).hexdigest() != item.source_digest
            ):
                raise validation_failure(
                    ArtifactProposalError(
                        ArtifactProposalErrorCode.INVALID_EVIDENCE,
                        operation_index=operation_index,
                    ),
                    f'$.operations[{operation_index}].evidence_id',
                    'evidence_id must refer to the current artifact revision',
                )
            start, end, selected = _evidence_boundary(current, item)
            if "sha256:" + hashlib.sha256(selected).hexdigest() != item.boundary_digest:
                raise validation_failure(
                    ArtifactProposalError(
                        ArtifactProposalErrorCode.INVALID_EVIDENCE,
                        operation_index=operation_index,
                    ),
                    f'$.operations[{operation_index}].evidence_id',
                    'evidence_id must refer to the current selected artifact boundary',
                )
            path, kind = _path(item.path)
            base_digest = hashlib.sha256(current).hexdigest()
            if type(intent) is ArtifactPatchIntent:
                if path in occupied:
                    raise validation_failure(
                        ArtifactProposalError(
                            ArtifactProposalErrorCode.INVALID_INTENT,
                            operation_index=operation_index,
                        ),
                        f'$.operations[{operation_index}]',
                        'a patch cannot target a path already used by another operation kind',
                    )
                patches.setdefault(path, []).append(
                    ArtifactTextHunk(
                        base_digest=base_digest,
                        start_utf8_byte=start,
                        end_utf8_byte=end,
                        old_text=selected,
                        new_text=intent.replacement_text.encode(
                            "utf-8", errors="strict"
                        ),
                    )
                )
                patch_kinds[path] = kind
                continue
            if not item.is_whole or path in occupied or path in patches:
                raise validation_failure(
                    ArtifactProposalError(
                        ArtifactProposalErrorCode.INVALID_EVIDENCE,
                        operation_index=operation_index,
                    ),
                    f'$.operations[{operation_index}].evidence_id',
                    (
                        'replace, rename, and delete require whole-file evidence and a path not used '
                        'by another operation'
                    ),
                )
            occupied.add(path)
            if type(intent) is ArtifactReplaceIntent:
                content = intent.new_text.encode("utf-8", errors="strict")
                resolved.append(
                    ArtifactReplaceOperation(
                        path=path,
                        artifact_kind=kind,
                        base_digest=base_digest,
                        content=content,
                        projected_digest=hashlib.sha256(content).hexdigest(),
                    )
                )
            elif type(intent) is ArtifactRenameIntent:
                destination, destination_kind = _path(intent.suggested_destination)
                _, required_destination_kind = _source_rule(
                    ArtifactChangeOperationKind.RENAME, path
                )
                if destination_kind is not required_destination_kind:
                    raise validation_failure(
                        ArtifactProposalError(
                            ArtifactProposalErrorCode.INVALID_INTENT,
                            operation_index=operation_index,
                        ),
                        f'$.operations[{operation_index}].suggested_destination',
                        'rename destination must have the same artifact kind as its source',
                    )
                resolved.append(
                    ArtifactRenameOperation(
                        source_path=path,
                        destination_path=destination,
                        artifact_kind=kind,
                        base_digest=base_digest,
                        projected_digest=base_digest,
                    )
                )
            elif type(intent) is ArtifactDeleteIntent:
                resolved.append(
                    ArtifactDeleteOperation(
                        path=path,
                        artifact_kind=kind,
                        base_digest=base_digest,
                    )
                )
            else:  # pragma: no cover - exact intent tuple validation
                raise validation_failure(
                    ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT),
                    f'$.operations[{operation_index}].kind',
                    'expected create, patch, replace, rename, or delete',
                )
        except ArtifactProposalError as failure:
            failure.__cause__ = None
            failure.__context__ = None
            raise
        except Exception as failure:
            raise_validation(_operation_validation_failure(failure, operation_index=operation_index))
    for path, hunks in patches.items():
        ordered = tuple(
            sorted(hunks, key=lambda item: (item.start_utf8_byte, item.end_utf8_byte))
        )
        projected = _apply_hunks(base[path], ordered)
        resolved.append(
            ArtifactPatchOperation(
                path=path,
                artifact_kind=patch_kinds[path],
                base_digest=hashlib.sha256(base[path]).hexdigest(),
                hunks=ordered,
                projected_digest=hashlib.sha256(projected).hexdigest(),
            )
        )
    return tuple(sorted(resolved, key=lambda item: _primary_path(item).encode("utf-8")))


def _evidence_boundary(
    content: bytes,
    evidence: ArtifactIntentEvidence,
) -> tuple[int, int, bytes]:
    if evidence.is_whole:
        return 0, len(content), content
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(content):
        if content[index] == 13:
            index += 2 if index + 1 < len(content) and content[index + 1] == 10 else 1
            spans.append((start, index))
            start = index
        elif content[index] == 10:
            index += 1
            spans.append((start, index))
            start = index
        else:
            index += 1
    if start < len(content):
        spans.append((start, len(content)))
    assert evidence.start_line is not None and evidence.end_line is not None
    if evidence.end_line > len(spans):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_EVIDENCE)
    first = spans[evidence.start_line - 1][0]
    last = spans[evidence.end_line - 1][1]
    return first, last, content[first:last]


def _apply_hunks(content: bytes, hunks: tuple[ArtifactTextHunk, ...]) -> bytes:
    result = bytearray()
    cursor = 0
    for hunk in hunks:
        if hunk.start_utf8_byte < cursor:
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_INTENT)
        result.extend(content[cursor : hunk.start_utf8_byte])
        result.extend(hunk.new_text)
        cursor = hunk.end_utf8_byte
    result.extend(content[cursor:])
    return bytes(result)


def _primary_path(operation: ArtifactChangeOperation) -> str:
    return (
        operation.source_path
        if type(operation) is ArtifactRenameOperation
        else operation.path
    )


def _domains_from_facts(
    facts: tuple[ArtifactReviewFact, ...],
) -> tuple[ArtifactReviewDomain, ...]:
    present = {item.domain for item in facts}
    result = tuple(item for item in _DOMAIN_ORDER if item in present)
    if not result:
        raise ArtifactProposalError(ArtifactProposalErrorCode.REVIEWER_MAPPING)
    return result


def _identifier_tuple(value: object, *, allow_empty: bool) -> tuple[str, ...]:
    if type(value) is not tuple or (not value and not allow_empty):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
    for item in value:
        _identifier(item)
    if len(set(value)) != len(value):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
    return value


def _review_domain_tuple(
    value: object,
    *,
    allow_empty: bool,
) -> tuple[ArtifactReviewDomain, ...]:
    if (
        type(value) is not tuple
        or (not value and not allow_empty)
        or any(type(item) is not ArtifactReviewDomain for item in value)
        or len(set(value)) != len(value)
        or value != tuple(item for item in _DOMAIN_ORDER if item in value)
    ):
        raise ArtifactProposalError(ArtifactProposalErrorCode.REVIEWER_MAPPING)
    return value


def _require_candidate(value: object) -> ArtifactReviewCandidate:
    if type(value) is not ArtifactReviewCandidate:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SCHEMA)
    try:
        value.__post_init__()
    except ArtifactProposalError as exc:
        _raise_content_free_error(
            exc.code,
            operation_index=exc.operation_index,
        )
    except Exception:
        _raise_content_free_error(ArtifactProposalErrorCode.INVALID_SCHEMA)
    return value


def _candidate_digest(candidate: ArtifactReviewCandidate) -> str:
    return _candidate_digest_fields(candidate)


def _candidate_digest_fields(candidate: ArtifactReviewCandidate) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            _canonical_json(_candidate_value(candidate, include_digest=False))
        ).hexdigest()
    )


def _candidate_value(
    candidate: ArtifactReviewCandidate,
    *,
    include_digest: bool,
) -> dict[str, object]:
    value = _candidate_fields_value(
        schema_version=candidate.schema_version,
        candidate_id=candidate.candidate_id,
        candidate_revision=candidate.candidate_revision,
        change_set=candidate.change_set,
        relationship_validation=candidate.relationship_validation,
        grounding_evidence_ids=candidate.grounding_evidence_ids,
        mandatory_review_domains=candidate.mandatory_review_domains,
        disclosed_review_domains=candidate.disclosed_review_domains,
    )
    if include_digest:
        value["candidate_digest"] = candidate.candidate_digest
    return value


def _candidate_fields_value(
    *,
    schema_version: int,
    candidate_id: str,
    candidate_revision: int,
    change_set: ArtifactChangeSet,
    relationship_validation: ArtifactRelationshipValidation,
    grounding_evidence_ids: tuple[str, ...],
    mandatory_review_domains: tuple[ArtifactReviewDomain, ...],
    disclosed_review_domains: tuple[ArtifactReviewDomain, ...],
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "candidate_revision": candidate_revision,
        "change_set": json.loads(serialize_artifact_change_set(change_set)),
        "disclosed_review_domains": [item.value for item in disclosed_review_domains],
        "grounding_evidence_ids": list(grounding_evidence_ids),
        "mandatory_review_domains": [item.value for item in mandatory_review_domains],
        "relationship_validation": _relationship_value(relationship_validation),
        "schema_version": schema_version,
    }


def _relationship_value(value: ArtifactRelationshipValidation) -> dict[str, object]:
    return {
        "added_edges": [_edge_value(item) for item in value.added_edges],
        "base_state_digest": value.base_state_digest,
        "change_set_digest": value.change_set_digest,
        "projected_dangling_edges": [
            _edge_value(item) for item in value.projected_dangling_edges
        ],
        "projected_state_digest": value.projected_state_digest,
        "removed_edges": [_edge_value(item) for item in value.removed_edges],
        "retained_edges": [_edge_value(item) for item in value.retained_edges],
        "review_facts": [_fact_value(item) for item in value.review_facts],
        "schema_version": value.schema_version,
    }


def _edge_value(edge: ArtifactRelationshipEdge) -> dict[str, object]:
    return {
        "destination_end_utf8_byte": edge.destination_end_utf8_byte,
        "destination_start_utf8_byte": edge.destination_start_utf8_byte,
        "fragment": edge.fragment,
        "relationship_kind": edge.relationship_kind.value,
        "source_path": edge.source_path,
        "target_path": edge.target_path,
    }


def _fact_value(fact: ArtifactReviewFact) -> dict[str, object]:
    return {
        "artifact_path": fact.artifact_path,
        "domain": fact.domain.value,
        "operation_index": fact.operation_index,
        "reason": fact.reason.value,
    }


def _decode_relationship(value: object) -> ArtifactRelationshipValidation:
    root = _object(
        value,
        {
            "added_edges",
            "base_state_digest",
            "change_set_digest",
            "projected_dangling_edges",
            "projected_state_digest",
            "removed_edges",
            "retained_edges",
            "review_facts",
            "schema_version",
        },
    )
    return ArtifactRelationshipValidation(
        schema_version=_integer(root["schema_version"]),
        base_state_digest=_string(root["base_state_digest"]),
        projected_state_digest=_string(root["projected_state_digest"]),
        change_set_digest=_string(root["change_set_digest"]),
        added_edges=_decode_edges(root["added_edges"]),
        removed_edges=_decode_edges(root["removed_edges"]),
        retained_edges=_decode_edges(root["retained_edges"]),
        projected_dangling_edges=_decode_edges(root["projected_dangling_edges"]),
        review_facts=_decode_facts(root["review_facts"]),
    )


def _decode_edges(value: object) -> tuple[ArtifactRelationshipEdge, ...]:
    if type(value) is not list:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    result: list[ArtifactRelationshipEdge] = []
    for item in value:
        raw = _object(
            item,
            {
                "destination_end_utf8_byte",
                "destination_start_utf8_byte",
                "fragment",
                "relationship_kind",
                "source_path",
                "target_path",
            },
        )
        fragment = raw["fragment"]
        if fragment is not None and type(fragment) is not str:
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
        result.append(
            ArtifactRelationshipEdge(
                source_path=_string(raw["source_path"]),
                target_path=_string(raw["target_path"]),
                destination_start_utf8_byte=_integer(
                    raw["destination_start_utf8_byte"]
                ),
                destination_end_utf8_byte=_integer(raw["destination_end_utf8_byte"]),
                fragment=fragment,
                relationship_kind=ArtifactRelationshipKind(
                    _string(raw["relationship_kind"])
                ),
            )
        )
    return tuple(result)


def _decode_facts(value: object) -> tuple[ArtifactReviewFact, ...]:
    if type(value) is not list:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    result: list[ArtifactReviewFact] = []
    for item in value:
        raw = _object(
            item,
            {"artifact_path", "domain", "operation_index", "reason"},
        )
        operation_index = raw["operation_index"]
        if operation_index is not None:
            operation_index = _integer(operation_index)
        result.append(
            ArtifactReviewFact(
                domain=ArtifactReviewDomain(_string(raw["domain"])),
                reason=ArtifactReviewReason(_string(raw["reason"])),
                artifact_path=_string(raw["artifact_path"]),
                operation_index=operation_index,
            )
        )
    return tuple(result)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except Exception:
        _raise_content_free_error(ArtifactProposalErrorCode.INVALID_SERIALIZATION)


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
        result[key] = value
    return result


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ArtifactProposalError(ArtifactProposalErrorCode.INVALID_SERIALIZATION)
    return tuple(value)


__all__ = (
    "ARTIFACT_PROPOSAL_SCHEMA_VERSION",
    "ArtifactChangeIntent",
    "ArtifactCreateIntent",
    "ArtifactDeleteIntent",
    "ArtifactIntentEvidence",
    "ArtifactPatchIntent",
    "ArtifactProposalError",
    "ArtifactProposalErrorCode",
    "ArtifactProposalIntent",
    "ArtifactRenameIntent",
    "ArtifactReplaceIntent",
    "ArtifactReviewCandidate",
    "artifact_review_specialists",
    "build_artifact_review_candidate",
    "deserialize_artifact_review_candidate",
    "serialize_artifact_review_candidate",
)
