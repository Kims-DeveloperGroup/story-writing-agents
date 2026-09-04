"""Pure immutable contracts for exact writing-artifact application."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NoReturn

from .catalog import ArtifactKind, canonical_artifact_kind
from .change_sets import (
    ArtifactChangeSet,
    ArtifactChangeSetError,
    deserialize_artifact_change_set,
    serialize_artifact_change_set,
)
from .relationship_policy import (
    ArtifactRelationshipEdge,
    ArtifactRelationshipValidation,
    ArtifactReviewFact,
)


ARTIFACT_APPLICATION_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TAGGED_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


class ArtifactApplicationErrorCode(StrEnum):
    """Closed content-free failures for application contracts and codecs."""

    INVALID_SCHEMA = "invalid_schema"
    INVALID_SERIALIZATION = "invalid_serialization"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_DIGEST = "invalid_digest"
    INVALID_PATH = "invalid_path"
    CORRELATION_MISMATCH = "correlation_mismatch"
    INVALID_RESULT = "invalid_result"
    DRIFT = "drift"
    CORRUPTION = "corruption"
    RECOVERY_REQUIRED = "recovery_required"


_ERROR_MESSAGES = {
    ArtifactApplicationErrorCode.INVALID_SCHEMA: (
        "artifact application schema is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_SERIALIZATION: (
        "artifact application serialization is noncanonical or invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_UTF8: (
        "artifact application serialization is not strict UTF-8"
    ),
    ArtifactApplicationErrorCode.INVALID_IDENTIFIER: (
        "artifact application identity is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_DIGEST: (
        "artifact application digest is invalid"
    ),
    ArtifactApplicationErrorCode.INVALID_PATH: ("artifact application path is invalid"),
    ArtifactApplicationErrorCode.CORRELATION_MISMATCH: (
        "artifact application correlation does not match"
    ),
    ArtifactApplicationErrorCode.INVALID_RESULT: (
        "artifact application result is invalid"
    ),
    ArtifactApplicationErrorCode.DRIFT: ("artifact application authority has drifted"),
    ArtifactApplicationErrorCode.CORRUPTION: ("artifact application state is corrupt"),
    ArtifactApplicationErrorCode.RECOVERY_REQUIRED: (
        "artifact application recovery is required"
    ),
}


class ArtifactApplicationError(RuntimeError):
    """Deterministic application failure that never incorporates story text."""

    def __init__(self, code: ArtifactApplicationErrorCode) -> None:
        if type(code) is not ArtifactApplicationErrorCode:
            raise TypeError("code must be an ArtifactApplicationErrorCode")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def _raise_failure(code: ArtifactApplicationErrorCode) -> NoReturn:
    """Raise one content-free failure without retaining a caught exception."""

    try:
        raise ArtifactApplicationError(code) from None
    except ArtifactApplicationError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


class ArtifactApplicationState(StrEnum):
    """Closed terminal truth for one exact application request."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class ArtifactApplicationProvenance:
    """Content-free identity binding one application to a reviewed candidate."""

    application_id: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    change_set_digest: str
    relationship_validation_digest: str

    def __post_init__(self) -> None:
        _identifier(self.application_id)
        _identifier(self.candidate_id)
        if type(self.candidate_revision) is not int or self.candidate_revision < 1:
            raise ArtifactApplicationError(
                ArtifactApplicationErrorCode.INVALID_IDENTIFIER
            )
        _tagged_digest(self.candidate_digest)
        _digest(self.change_set_digest)
        _tagged_digest(self.relationship_validation_digest)


@dataclass(frozen=True, slots=True)
class ArtifactApplicationRequest:
    """One exact content-bearing change set with content-free provenance."""

    schema_version: int
    provenance: ArtifactApplicationProvenance
    change_set: ArtifactChangeSet = field(repr=False)
    attached_core_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        if type(self.provenance) is not ArtifactApplicationProvenance:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        _revalidate_provenance(self.provenance)
        if type(self.change_set) is not ArtifactChangeSet:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        _revalidate_change_set(self.change_set)
        _attached_core_paths(self.attached_core_paths)
        if self.provenance.change_set_digest != self.change_set.change_set_digest:
            raise ArtifactApplicationError(
                ArtifactApplicationErrorCode.CORRELATION_MISMATCH
            )

    @property
    def application_id(self) -> str:
        return self.provenance.application_id

    @property
    def candidate_id(self) -> str:
        return self.provenance.candidate_id

    @property
    def candidate_revision(self) -> int:
        return self.provenance.candidate_revision

    @property
    def candidate_digest(self) -> str:
        return self.provenance.candidate_digest

    @property
    def change_set_digest(self) -> str:
        return self.provenance.change_set_digest

    @property
    def relationship_validation_digest(self) -> str:
        return self.provenance.relationship_validation_digest


@dataclass(frozen=True, slots=True)
class ArtifactApplicationFinalArtifact:
    """Exact content-free identity of one affected artifact in final state."""

    path: str
    artifact_kind: ArtifactKind
    sha256: str

    def __post_init__(self) -> None:
        _artifact_identity(self.path, self.artifact_kind)
        _digest(self.sha256)


@dataclass(frozen=True, slots=True)
class ArtifactRenameLineage:
    """Durable content-free lineage from one retired manuscript path."""

    source_path: str
    destination_path: str
    sha256: str

    def __post_init__(self) -> None:
        source_kind = _canonical_path(self.source_path)
        destination_kind = _canonical_path(self.destination_path)
        if (
            source_kind is not ArtifactKind.MANUSCRIPT
            or destination_kind is not ArtifactKind.MANUSCRIPT
            or self.source_path == self.destination_path
        ):
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
        _digest(self.sha256)


@dataclass(frozen=True, slots=True)
class ArtifactDeletionProvenance:
    """Durable content-free provenance for one deleted manuscript identity."""

    path: str
    prior_sha256: str

    def __post_init__(self) -> None:
        if _canonical_path(self.path) is not ArtifactKind.MANUSCRIPT:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
        _digest(self.prior_sha256)


@dataclass(frozen=True, slots=True)
class ArtifactApplicationResult:
    """Exact terminal repository truth for one correlated application."""

    schema_version: int
    provenance: ArtifactApplicationProvenance
    state: ArtifactApplicationState
    affected_paths: tuple[str, ...]
    final_artifacts: tuple[ArtifactApplicationFinalArtifact, ...]
    rename_lineage: tuple[ArtifactRenameLineage, ...]
    deletion_provenance: tuple[ArtifactDeletionProvenance, ...]

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        if type(self.provenance) is not ArtifactApplicationProvenance:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        _revalidate_provenance(self.provenance)
        if type(self.state) is not ArtifactApplicationState:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
        affected = _path_tuple(self.affected_paths)
        finals = _value_tuple(
            self.final_artifacts,
            ArtifactApplicationFinalArtifact,
        )
        lineages = _value_tuple(self.rename_lineage, ArtifactRenameLineage)
        deletions = _value_tuple(
            self.deletion_provenance,
            ArtifactDeletionProvenance,
        )
        if not affected:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
        _require_sorted_unique(finals, key=lambda item: item.path)
        _require_sorted_unique(
            lineages,
            key=lambda item: (item.source_path, item.destination_path),
        )
        _require_sorted_unique(deletions, key=lambda item: item.path)
        _validate_result_topology(affected, finals, lineages, deletions)

    @property
    def application_id(self) -> str:
        return self.provenance.application_id

    @property
    def candidate_id(self) -> str:
        return self.provenance.candidate_id

    @property
    def candidate_revision(self) -> int:
        return self.provenance.candidate_revision

    @property
    def candidate_digest(self) -> str:
        return self.provenance.candidate_digest

    @property
    def change_set_digest(self) -> str:
        return self.provenance.change_set_digest

    @property
    def relationship_validation_digest(self) -> str:
        return self.provenance.relationship_validation_digest

    @property
    def applied_paths(self) -> tuple[str, ...]:
        """Return affected identities only when this invocation changed state."""

        if self.state is ArtifactApplicationState.APPLIED:
            return self.affected_paths
        return ()

    @property
    def unchanged_paths(self) -> tuple[str, ...]:
        """Return affected identities for an idempotent completed replay."""

        if self.state is ArtifactApplicationState.UNCHANGED:
            return self.affected_paths
        return ()


def artifact_relationship_validation_digest(
    validation: ArtifactRelationshipValidation,
) -> str:
    """Return the exact digest consumed by the atomic application store."""

    if type(validation) is not ArtifactRelationshipValidation:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    try:
        validation.__post_init__()
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    payload = {
        "added_edges": [_relationship_edge_value(item) for item in validation.added_edges],
        "base_state_digest": validation.base_state_digest,
        "change_set_digest": validation.change_set_digest,
        "projected_dangling_edges": [
            _relationship_edge_value(item)
            for item in validation.projected_dangling_edges
        ],
        "projected_state_digest": validation.projected_state_digest,
        "removed_edges": [
            _relationship_edge_value(item) for item in validation.removed_edges
        ],
        "retained_edges": [
            _relationship_edge_value(item) for item in validation.retained_edges
        ],
        "review_facts": [
            _relationship_review_fact_value(item) for item in validation.review_facts
        ],
        "schema_version": validation.schema_version,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_artifact_application_request(
    *,
    application_id: str,
    candidate_id: str,
    candidate_revision: int,
    candidate_digest: str,
    change_set: ArtifactChangeSet,
    relationship_validation: ArtifactRelationshipValidation,
    attached_core_paths: tuple[str, ...],
) -> ArtifactApplicationRequest:
    """Build one exact repository request from an already reviewed candidate."""

    if type(change_set) is not ArtifactChangeSet:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    if (
        type(relationship_validation) is not ArtifactRelationshipValidation
        or relationship_validation.change_set_digest != change_set.change_set_digest
        or relationship_validation.base_state_digest != change_set.base_state_digest
        or relationship_validation.projected_state_digest
        != change_set.projected_state_digest
        or not relationship_validation.is_valid
    ):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    return ArtifactApplicationRequest(
        schema_version=ARTIFACT_APPLICATION_SCHEMA_VERSION,
        provenance=ArtifactApplicationProvenance(
            application_id=application_id,
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,
            candidate_digest=candidate_digest,
            change_set_digest=change_set.change_set_digest,
            relationship_validation_digest=artifact_relationship_validation_digest(
                relationship_validation
            ),
        ),
        change_set=change_set,
        attached_core_paths=attached_core_paths,
    )


def _relationship_edge_value(
    value: ArtifactRelationshipEdge,
) -> dict[str, object]:
    return {
        "destination_end_utf8_byte": value.destination_end_utf8_byte,
        "destination_start_utf8_byte": value.destination_start_utf8_byte,
        "fragment": value.fragment,
        "relationship_kind": value.relationship_kind.value,
        "source_path": value.source_path,
        "target_path": value.target_path,
    }


def _relationship_review_fact_value(
    value: ArtifactReviewFact,
) -> dict[str, object]:
    return {
        "artifact_path": value.artifact_path,
        "domain": value.domain.value,
        "operation_index": value.operation_index,
        "reason": value.reason.value,
    }


def serialize_artifact_application_request(
    request: ArtifactApplicationRequest,
) -> bytes:
    """Return strict canonical JSON that intentionally includes edit content."""

    _require_request(request)
    try:
        change_set = json.loads(serialize_artifact_change_set(request.change_set))
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    return _canonical_json(
        {
            "attached_core_paths": list(request.attached_core_paths),
            "change_set": change_set,
            "provenance": _provenance_value(request.provenance),
            "schema_version": request.schema_version,
        }
    )


def deserialize_artifact_application_request(
    data: bytes,
) -> ArtifactApplicationRequest:
    """Decode only the exact canonical application-request representation."""

    value = _decode_canonical_json(data)
    try:
        root = _object(
            value,
            {
                "attached_core_paths",
                "change_set",
                "provenance",
                "schema_version",
            },
        )
        change_set = deserialize_artifact_change_set(
            _canonical_json(root["change_set"])
        )
        request = ArtifactApplicationRequest(
            schema_version=_integer(root["schema_version"]),
            provenance=_decode_provenance(root["provenance"]),
            change_set=change_set,
            attached_core_paths=_decode_attached_core_paths(
                root["attached_core_paths"]
            ),
        )
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except ArtifactChangeSetError:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)
    return request


def serialize_artifact_application_result(
    result: ArtifactApplicationResult,
) -> bytes:
    """Return strict canonical content-free terminal-result JSON."""

    _require_result(result)
    return _canonical_json(
        {
            "affected_paths": list(result.affected_paths),
            "deletion_provenance": [
                _deletion_value(item) for item in result.deletion_provenance
            ],
            "final_artifacts": [
                _final_artifact_value(item) for item in result.final_artifacts
            ],
            "provenance": _provenance_value(result.provenance),
            "rename_lineage": [_lineage_value(item) for item in result.rename_lineage],
            "schema_version": result.schema_version,
            "state": result.state.value,
        }
    )


def deserialize_artifact_application_result(
    data: bytes,
) -> ArtifactApplicationResult:
    """Decode only the exact canonical terminal-result representation."""

    value = _decode_canonical_json(data)
    try:
        root = _object(
            value,
            {
                "affected_paths",
                "deletion_provenance",
                "final_artifacts",
                "provenance",
                "rename_lineage",
                "schema_version",
                "state",
            },
        )
        result = ArtifactApplicationResult(
            schema_version=_integer(root["schema_version"]),
            provenance=_decode_provenance(root["provenance"]),
            state=ArtifactApplicationState(_string(root["state"])),
            affected_paths=_string_tuple(root["affected_paths"]),
            final_artifacts=_decode_final_artifacts(root["final_artifacts"]),
            rename_lineage=_decode_lineages(root["rename_lineage"]),
            deletion_provenance=_decode_deletions(root["deletion_provenance"]),
        )
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)
    return result


def _validate_result_topology(
    affected: tuple[str, ...],
    finals: tuple[ArtifactApplicationFinalArtifact, ...],
    lineages: tuple[ArtifactRenameLineage, ...],
    deletions: tuple[ArtifactDeletionProvenance, ...],
) -> None:
    final_by_path = {item.path: item for item in finals}
    lineage_sources = {item.source_path for item in lineages}
    lineage_destinations = {item.destination_path for item in lineages}
    deletion_paths = {item.path for item in deletions}
    if (
        len(final_by_path) != len(finals)
        or len(lineage_sources) != len(lineages)
        or len(lineage_destinations) != len(lineages)
        or len(deletion_paths) != len(deletions)
        or lineage_sources & lineage_destinations
        or lineage_sources & final_by_path.keys()
        or deletion_paths & final_by_path.keys()
        or deletion_paths & lineage_sources
        or deletion_paths & lineage_destinations
    ):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
    for lineage in lineages:
        destination = final_by_path.get(lineage.destination_path)
        if (
            destination is None
            or destination.artifact_kind is not ArtifactKind.MANUSCRIPT
            or destination.sha256 != lineage.sha256
        ):
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
    represented = (
        set(final_by_path) | lineage_sources | lineage_destinations | deletion_paths
    )
    if represented != set(affected):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_IDENTIFIER)
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_DIGEST)
    return value


def _tagged_digest(value: object) -> str:
    if type(value) is not str or _TAGGED_SHA256.fullmatch(value) is None:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_DIGEST)
    return value


def _schema_version(value: object) -> int:
    if type(value) is not int or value != ARTIFACT_APPLICATION_SCHEMA_VERSION:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    return value


def _canonical_path(value: object) -> ArtifactKind:
    try:
        kind = canonical_artifact_kind(value)
    except (TypeError, ValueError):
        kind = None
    if type(value) is not str or kind is None:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_PATH)
    return kind


def _artifact_identity(path: object, kind: object) -> None:
    if type(kind) is not ArtifactKind or _canonical_path(path) is not kind:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_PATH)


def _attached_core_paths(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    for path in value:
        if _canonical_path(path) is not ArtifactKind.CORE_DOCUMENT:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_PATH)
    if len(set(value)) != len(value) or value != tuple(
        sorted(value, key=lambda item: item.encode("utf-8"))
    ):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_PATH)
    return value


def _path_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
    for path in value:
        _canonical_path(path)
    if len(set(value)) != len(value) or value != tuple(
        sorted(value, key=lambda item: item.encode("utf-8"))
    ):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
    return value


def _value_tuple(value: object, item_type: type) -> tuple:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)
    for item in value:
        try:
            item.__post_init__()
        except ArtifactApplicationError as failure:
            _raise_failure(failure.code)
        except Exception:
            _raise_failure(ArtifactApplicationErrorCode.INVALID_RESULT)
    return value


def _require_sorted_unique(value: tuple, *, key) -> None:
    keys = tuple(key(item) for item in value)
    normalized = tuple(
        sorted(
            keys,
            key=lambda item: (
                tuple(part.encode("utf-8") for part in item)
                if type(item) is tuple
                else item.encode("utf-8")
            ),
        )
    )
    if len(set(keys)) != len(keys) or keys != normalized:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_RESULT)


def _revalidate_provenance(value: ArtifactApplicationProvenance) -> None:
    try:
        value.__post_init__()
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)


def _revalidate_change_set(value: ArtifactChangeSet) -> None:
    try:
        value.__post_init__()
    except ArtifactChangeSetError:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)


def _require_request(value: object) -> ArtifactApplicationRequest:
    if type(value) is not ArtifactApplicationRequest:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    try:
        value.__post_init__()
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    return value


def _require_result(value: object) -> ArtifactApplicationResult:
    if type(value) is not ArtifactApplicationResult:
        raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
    try:
        value.__post_init__()
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_RESULT)
    return value


def _provenance_value(value: ArtifactApplicationProvenance) -> dict[str, object]:
    return {
        "application_id": value.application_id,
        "candidate_digest": value.candidate_digest,
        "candidate_id": value.candidate_id,
        "candidate_revision": value.candidate_revision,
        "change_set_digest": value.change_set_digest,
        "relationship_validation_digest": value.relationship_validation_digest,
    }


def _final_artifact_value(
    value: ArtifactApplicationFinalArtifact,
) -> dict[str, object]:
    return {
        "artifact_kind": value.artifact_kind.value,
        "path": value.path,
        "sha256": value.sha256,
    }


def _lineage_value(value: ArtifactRenameLineage) -> dict[str, object]:
    return {
        "destination_path": value.destination_path,
        "sha256": value.sha256,
        "source_path": value.source_path,
    }


def _deletion_value(value: ArtifactDeletionProvenance) -> dict[str, object]:
    return {"path": value.path, "prior_sha256": value.prior_sha256}


def _decode_provenance(value: object) -> ArtifactApplicationProvenance:
    raw = _object(
        value,
        {
            "application_id",
            "candidate_digest",
            "candidate_id",
            "candidate_revision",
            "change_set_digest",
            "relationship_validation_digest",
        },
    )
    return ArtifactApplicationProvenance(
        application_id=_string(raw["application_id"]),
        candidate_id=_string(raw["candidate_id"]),
        candidate_revision=_integer(raw["candidate_revision"]),
        candidate_digest=_string(raw["candidate_digest"]),
        change_set_digest=_string(raw["change_set_digest"]),
        relationship_validation_digest=_string(raw["relationship_validation_digest"]),
    )


def _decode_final_artifacts(
    value: object,
) -> tuple[ArtifactApplicationFinalArtifact, ...]:
    result = []
    for item in _array(value):
        raw = _object(item, {"artifact_kind", "path", "sha256"})
        result.append(
            ArtifactApplicationFinalArtifact(
                path=_string(raw["path"]),
                artifact_kind=ArtifactKind(_string(raw["artifact_kind"])),
                sha256=_string(raw["sha256"]),
            )
        )
    return tuple(result)


def _decode_attached_core_paths(value: object) -> tuple[str, ...]:
    return tuple(_string(item) for item in _array(value))


def _decode_lineages(value: object) -> tuple[ArtifactRenameLineage, ...]:
    result = []
    for item in _array(value):
        raw = _object(item, {"destination_path", "sha256", "source_path"})
        result.append(
            ArtifactRenameLineage(
                source_path=_string(raw["source_path"]),
                destination_path=_string(raw["destination_path"]),
                sha256=_string(raw["sha256"]),
            )
        )
    return tuple(result)


def _decode_deletions(value: object) -> tuple[ArtifactDeletionProvenance, ...]:
    result = []
    for item in _array(value):
        raw = _object(item, {"path", "prior_sha256"})
        result.append(
            ArtifactDeletionProvenance(
                path=_string(raw["path"]),
                prior_sha256=_string(raw["prior_sha256"]),
            )
        )
    return tuple(result)


def _decode_canonical_json(data: bytes) -> object:
    if type(data) is not bytes:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_UTF8)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except ArtifactApplicationError as failure:
        _raise_failure(failure.code)
    except Exception:
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)
    if _canonical_json(value) != data:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return value


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
        _raise_failure(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactApplicationError(
                ArtifactApplicationErrorCode.INVALID_SERIALIZATION
            )
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SERIALIZATION)


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return value


def _array(value: object) -> list[object]:
    if type(value) is not list:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise ArtifactApplicationError(
            ArtifactApplicationErrorCode.INVALID_SERIALIZATION
        )
    return tuple(value)


__all__ = (
    "ARTIFACT_APPLICATION_SCHEMA_VERSION",
    "ArtifactApplicationError",
    "ArtifactApplicationErrorCode",
    "ArtifactApplicationFinalArtifact",
    "ArtifactApplicationProvenance",
    "ArtifactApplicationRequest",
    "ArtifactApplicationResult",
    "ArtifactApplicationState",
    "artifact_relationship_validation_digest",
    "build_artifact_application_request",
    "ArtifactDeletionProvenance",
    "ArtifactRenameLineage",
    "deserialize_artifact_application_request",
    "deserialize_artifact_application_result",
    "serialize_artifact_application_request",
    "serialize_artifact_application_result",
)
