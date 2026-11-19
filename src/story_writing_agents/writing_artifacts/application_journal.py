"""Value construction, JSON codec, and validation for the artifact application journal."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping

from ..errors import WorkspaceFormatError
from .application_contracts import (
    ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationFinalArtifact,
    ArtifactApplicationProvenance,
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    ArtifactDeletionProvenance,
    ArtifactRenameLineage,
    deserialize_artifact_application_request,
    deserialize_artifact_application_result,
    serialize_artifact_application_request,
    serialize_artifact_application_result,
)
from .catalog import (
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    canonical_artifact_kind,
)
from .change_sets import (
    ArtifactChangeOperation,
    ArtifactChangeSet,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
)
from .relationship_policy import (
    ArtifactRelationshipEdge,
    ArtifactRelationshipValidation,
    ArtifactReviewFact,
)

_TRANSACTION_SCHEMA_VERSION = 1
_APPLICATION_RECORD_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def is_application_identifier(value: str) -> bool:
    """Return whether an already-typed value has the persisted identifier grammar."""
    return _IDENTIFIER.fullmatch(value) is not None


def transaction_record(
    transaction_id: str,
    request: ArtifactApplicationRequest,
    record: dict[str, object],
    catalog: ArtifactCatalog,
    catalog_raw: bytes | None,
    catalog_after: ArtifactCatalog,
    targets: list[dict[str, object]],
) -> dict[str, object]:
    """Build the persisted transaction image in the current journal format."""
    return {
        "application_record": record,
        "catalog_after": catalog_after.to_dict(),
        "catalog_before": {
            "exists": catalog_raw is not None,
            "image": catalog.to_dict(),
            "sha256": (
                None
                if catalog_raw is None
                else hashlib.sha256(catalog_raw).hexdigest()
            ),
        },
        "request": json.loads(serialize_artifact_application_request(request)),
        "schema_version": _TRANSACTION_SCHEMA_VERSION,
        "targets": targets,
        "transaction_id": transaction_id,
    }


def validate_transaction(
    value: object,
    *,
    expected_id: object,
) -> None:
    fields = {
        "application_record",
        "catalog_after",
        "catalog_before",
        "request",
        "schema_version",
        "targets",
        "transaction_id",
    }
    if type(value) is not dict or set(value) != fields:
        raise WorkspaceFormatError("application transaction fields are invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != _TRANSACTION_SCHEMA_VERSION
    ):
        raise WorkspaceFormatError("application transaction schema is unsupported")
    transaction_id = value["transaction_id"]
    if (
        type(transaction_id) is not str
        or re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None
        or transaction_id != expected_id
    ):
        raise WorkspaceFormatError("application transaction identity is invalid")
    record = value["application_record"]
    validate_application_record(record)
    assert isinstance(record, dict)
    result = record_result(record)
    try:
        request_bytes = encode_journal_json(value["request"])
        request = deserialize_artifact_application_request(request_bytes)
    except ArtifactApplicationError as exc:
        raise WorkspaceFormatError(
            "application transaction request is invalid"
        ) from exc
    if (
        hashlib.sha256(request_bytes).hexdigest() != record["request_sha256"]
        or request.provenance != result.provenance
        or request.change_set.base_state_digest != record["base_state_digest"]
        or request.change_set.projected_state_digest
        != record["projected_state_digest"]
        or _operation_facts(request.change_set) != record["operations"]
    ):
        raise WorkspaceFormatError(
            "application transaction request correlation is invalid"
        )
    before_record = value["catalog_before"]
    if (
        type(before_record) is not dict
        or set(before_record) != {"exists", "image", "sha256"}
        or type(before_record["exists"]) is not bool
    ):
        raise WorkspaceFormatError("application catalog-before is invalid")
    before_exists = before_record["exists"]
    before_sha256 = before_record["sha256"]
    if before_exists:
        _require_sha256(before_sha256, "catalog-before digest")
    elif before_sha256 is not None:
        raise WorkspaceFormatError("absent application catalog has a digest")
    try:
        catalog_before = ArtifactCatalog.from_dict(before_record["image"])
        catalog_after = ArtifactCatalog.from_dict(value["catalog_after"])
    except (TypeError, ValueError) as exc:
        raise WorkspaceFormatError("application catalog image is invalid") from exc
    if not before_exists and catalog_before.entries:
        raise WorkspaceFormatError("absent application catalog is not empty")
    if (
        before_exists
        and before_sha256
        != hashlib.sha256(encode_journal_json(before_record["image"])).hexdigest()
    ):
        raise WorkspaceFormatError("application catalog-before digest is invalid")
    targets = value["targets"]
    if type(targets) is not list or not targets:
        raise WorkspaceFormatError("application targets are invalid")
    for target in targets:
        _validate_target(target)
    target_paths = [target["path"] for target in targets]
    if target_paths != sorted(target_paths, key=lambda item: item.encode("utf-8")):
        raise WorkspaceFormatError("application target order is invalid")
    if len(set(target_paths)) != len(target_paths):
        raise WorkspaceFormatError("application target paths are not unique")
    if tuple(target_paths) != result.affected_paths:
        raise WorkspaceFormatError("application result paths do not match targets")
    operations = record["operations"]
    assert isinstance(operations, list)
    if not _targets_match_operations(targets, operations):
        raise WorkspaceFormatError("application targets do not match operations")
    expected_result = application_result(result.provenance, targets)
    if expected_result != result:
        raise WorkspaceFormatError("application result does not match targets")
    expected_entries = dict(catalog_before.entries)
    for target in targets:
        path = target["path"]
        assert isinstance(path, str)
        if target["new_exists"] is False:
            expected_entries.pop(path, None)
        else:
            expected_entries[path] = catalog_entry(
                path,
                ArtifactKind(target["kind"]),
                target["new_sha256"],
                record["sequence"],
                result.provenance,
            )
    if ArtifactCatalog(expected_entries) != catalog_after:
        raise WorkspaceFormatError("application catalog transition is invalid")


def _validate_target(value: object) -> None:
    fields = {
        "kind",
        "new_content_base64",
        "new_exists",
        "new_sha256",
        "old_exists",
        "old_sha256",
        "operation",
        "operation_index",
        "path",
        "role",
    }
    if type(value) is not dict or set(value) != fields:
        raise WorkspaceFormatError("application target fields are invalid")
    path = value["path"]
    kind_value = value["kind"]
    try:
        kind = ArtifactKind(kind_value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceFormatError("application target kind is invalid") from exc
    if type(path) is not str or canonical_artifact_kind(path) is not kind:
        raise WorkspaceFormatError("application target path is invalid")
    if (
        type(value["operation_index"]) is not int
        or value["operation_index"] < 0
        or value["operation"]
        not in {"create", "patch", "replace", "rename", "delete"}
        or value["role"] not in {"target", "source", "destination"}
        or type(value["old_exists"]) is not bool
        or type(value["new_exists"]) is not bool
    ):
        raise WorkspaceFormatError("application target metadata is invalid")
    old_sha256 = value["old_sha256"]
    new_sha256 = value["new_sha256"]
    content = value["new_content_base64"]
    if value["old_exists"]:
        _require_sha256(old_sha256, "application old digest")
    elif old_sha256 is not None:
        raise WorkspaceFormatError("absent old application target has a digest")
    if value["new_exists"]:
        _require_sha256(new_sha256, "application new digest")
        if type(content) is not str:
            raise WorkspaceFormatError("application target content is invalid")
        target_content(value)
    elif new_sha256 is not None or content is not None:
        raise WorkspaceFormatError("retired application target has new content")
    if not value["old_exists"] and not value["new_exists"]:
        raise WorkspaceFormatError("application target has no state")
    if value["old_exists"] and value["new_exists"] and old_sha256 == new_sha256:
        raise WorkspaceFormatError("application target is unchanged")


def validate_application_record(value: object) -> None:
    fields = {
        "application_id",
        "base_state_digest",
        "operations",
        "projected_state_digest",
        "request_sha256",
        "result",
        "schema_version",
        "sequence",
    }
    if type(value) is not dict or set(value) != fields:
        raise WorkspaceFormatError("application record fields are invalid")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != _APPLICATION_RECORD_SCHEMA_VERSION
        or type(value["sequence"]) is not int
        or value["sequence"] < 1
    ):
        raise WorkspaceFormatError("application record schema is unsupported")
    application_id = value["application_id"]
    if (
        type(application_id) is not str
        or _IDENTIFIER.fullmatch(application_id) is None
    ):
        raise WorkspaceFormatError("application record identity is invalid")
    for name in (
        "base_state_digest",
        "projected_state_digest",
        "request_sha256",
    ):
        _require_sha256(value[name], f"application record {name}")
    operations = value["operations"]
    if type(operations) is not list or not operations:
        raise WorkspaceFormatError("application record operations are invalid")
    _validate_operation_facts(operations)
    try:
        result = deserialize_artifact_application_result(
            encode_journal_json(value["result"])
        )
    except ArtifactApplicationError as exc:
        raise WorkspaceFormatError("application record result is invalid") from exc
    if (
        result.state is not ArtifactApplicationState.APPLIED
        or result.application_id != application_id
    ):
        raise WorkspaceFormatError(
            "application record result correlation is invalid"
        )
    if not _operation_facts_match_result(operations, result):
        raise WorkspaceFormatError("application operations do not match result")


def decode_journal_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_decode_unique_object,
            parse_constant=_reject_json_constant,
        )
    except WorkspaceFormatError:
        raise
    except Exception as exc:
        raise WorkspaceFormatError("artifact application JSON is invalid") from exc
    if encode_journal_json(value) != raw:
        raise WorkspaceFormatError("artifact application JSON is noncanonical")
    return value


def target_specs(
    change_set: ArtifactChangeSet,
    base: Mapping[str, bytes],
    projected: Mapping[str, bytes],
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for index, operation in enumerate(change_set.operations):
        if type(operation) is ArtifactCreateOperation:
            targets.append(
                _target_spec(
                    index,
                    "create",
                    "target",
                    operation.path,
                    operation.artifact_kind,
                    None,
                    operation.content,
                )
            )
        elif type(operation) is ArtifactPatchOperation:
            targets.append(
                _target_spec(
                    index,
                    "patch",
                    "target",
                    operation.path,
                    operation.artifact_kind,
                    base[operation.path],
                    projected[operation.path],
                )
            )
        elif type(operation) is ArtifactReplaceOperation:
            targets.append(
                _target_spec(
                    index,
                    "replace",
                    "target",
                    operation.path,
                    operation.artifact_kind,
                    base[operation.path],
                    operation.content,
                )
            )
        elif type(operation) is ArtifactRenameOperation:
            content = base[operation.source_path]
            targets.extend(
                (
                    _target_spec(
                        index,
                        "rename",
                        "source",
                        operation.source_path,
                        operation.artifact_kind,
                        content,
                        None,
                    ),
                    _target_spec(
                        index,
                        "rename",
                        "destination",
                        operation.destination_path,
                        operation.artifact_kind,
                        None,
                        content,
                    ),
                )
            )
        elif type(operation) is ArtifactDeleteOperation:
            targets.append(
                _target_spec(
                    index,
                    "delete",
                    "target",
                    operation.path,
                    operation.artifact_kind,
                    base[operation.path],
                    None,
                )
            )
        else:  # pragma: no cover - ArtifactChangeSet enforces the closed union
            raise WorkspaceFormatError("artifact application operation is invalid")
    return sorted(targets, key=lambda item: item["path"].encode("utf-8"))


def _target_spec(
    operation_index: int,
    operation: str,
    role: str,
    path: str,
    kind: ArtifactKind,
    old: bytes | None,
    new: bytes | None,
) -> dict[str, object]:
    return {
        "kind": kind.value,
        "new_content_base64": (
            None if new is None else base64.b64encode(new).decode("ascii")
        ),
        "new_exists": new is not None,
        "new_sha256": None if new is None else hashlib.sha256(new).hexdigest(),
        "old_exists": old is not None,
        "old_sha256": None if old is None else hashlib.sha256(old).hexdigest(),
        "operation": operation,
        "operation_index": operation_index,
        "path": path,
        "role": role,
    }


def application_result(
    provenance: ArtifactApplicationProvenance,
    targets: list[dict[str, object]],
) -> ArtifactApplicationResult:
    finals = tuple(
        ArtifactApplicationFinalArtifact(
            path=target["path"],
            artifact_kind=ArtifactKind(target["kind"]),
            sha256=target["new_sha256"],
        )
        for target in targets
        if target["new_exists"]
    )
    by_operation: dict[int, dict[str, dict[str, object]]] = {}
    for target in targets:
        index = target["operation_index"]
        assert isinstance(index, int)
        by_operation.setdefault(index, {})[target["role"]] = target
    lineages: list[ArtifactRenameLineage] = []
    deletions: list[ArtifactDeletionProvenance] = []
    for targets_by_role in by_operation.values():
        first = next(iter(targets_by_role.values()))
        if first["operation"] == "rename":
            source = targets_by_role["source"]
            destination = targets_by_role["destination"]
            lineages.append(
                ArtifactRenameLineage(
                    source_path=source["path"],
                    destination_path=destination["path"],
                    sha256=destination["new_sha256"],
                )
            )
        elif first["operation"] == "delete":
            deletions.append(
                ArtifactDeletionProvenance(
                    path=first["path"],
                    prior_sha256=first["old_sha256"],
                )
            )
    lineages.sort(key=lambda item: item.source_path.encode("utf-8"))
    deletions.sort(key=lambda item: item.path.encode("utf-8"))
    return ArtifactApplicationResult(
        schema_version=ARTIFACT_APPLICATION_SCHEMA_VERSION,
        provenance=provenance,
        state=ArtifactApplicationState.APPLIED,
        affected_paths=tuple(target["path"] for target in targets),
        final_artifacts=finals,
        rename_lineage=tuple(lineages),
        deletion_provenance=tuple(deletions),
    )


def application_record(
    request: ArtifactApplicationRequest,
    *,
    request_sha256: str,
    sequence: int,
    result: ArtifactApplicationResult,
) -> dict[str, object]:
    return {
        "application_id": request.application_id,
        "base_state_digest": request.change_set.base_state_digest,
        "operations": _operation_facts(request.change_set),
        "projected_state_digest": request.change_set.projected_state_digest,
        "request_sha256": request_sha256,
        "result": json.loads(serialize_artifact_application_result(result)),
        "schema_version": _APPLICATION_RECORD_SCHEMA_VERSION,
        "sequence": sequence,
    }


def _operation_facts(change_set: ArtifactChangeSet) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, operation in enumerate(change_set.operations):
        fact: dict[str, object] = {
            "artifact_kind": operation.artifact_kind.value,
            "operation": operation.operation_kind.value,
            "operation_index": index,
        }
        if type(operation) is ArtifactRenameOperation:
            fact.update(
                {
                    "base_digest": operation.base_digest,
                    "destination_path": operation.destination_path,
                    "projected_digest": operation.projected_digest,
                    "source_path": operation.source_path,
                }
            )
        else:
            fact["path"] = operation.path
            if type(operation) is ArtifactCreateOperation:
                fact.update(
                    {
                        "base_digest": None,
                        "projected_digest": operation.projected_digest,
                    }
                )
            elif type(operation) in (ArtifactPatchOperation, ArtifactReplaceOperation):
                fact.update(
                    {
                        "base_digest": operation.base_digest,
                        "projected_digest": operation.projected_digest,
                    }
                )
            else:
                fact.update(
                    {"base_digest": operation.base_digest, "projected_digest": None}
                )
        result.append(fact)
    return result


def _validate_operation_facts(value: list[object]) -> None:
    previous: bytes | None = None
    participants: set[str] = set()
    for expected_index, raw in enumerate(value):
        if type(raw) is not dict:
            raise WorkspaceFormatError("application operation fact is invalid")
        operation = raw.get("operation")
        common = {
            "artifact_kind",
            "base_digest",
            "operation",
            "operation_index",
            "projected_digest",
        }
        expected = (
            common | {"destination_path", "source_path"}
            if operation == "rename"
            else common | {"path"}
        )
        if set(raw) != expected or raw["operation_index"] != expected_index:
            raise WorkspaceFormatError("application operation fields are invalid")
        try:
            kind = ArtifactKind(raw["artifact_kind"])
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("application operation kind is invalid") from exc
        if operation == "rename":
            paths = (raw["source_path"], raw["destination_path"])
            primary = raw["source_path"]
        else:
            paths = (raw["path"],)
            primary = raw["path"]
        if any(
            type(path) is not str or canonical_artifact_kind(path) is not kind
            for path in paths
        ):
            raise WorkspaceFormatError("application operation path is invalid")
        if any(path in participants for path in paths) or len(set(paths)) != len(paths):
            raise WorkspaceFormatError("application operation paths collide")
        participants.update(paths)
        key = primary.encode("utf-8")
        if previous is not None and key < previous:
            raise WorkspaceFormatError("application operation order is invalid")
        previous = key
        base_digest = raw["base_digest"]
        projected_digest = raw["projected_digest"]
        if operation == "create":
            if base_digest is not None:
                raise WorkspaceFormatError("application create base is invalid")
            _require_sha256(projected_digest, "application create digest")
        elif operation in {"patch", "replace", "rename"}:
            _require_sha256(base_digest, "application base digest")
            _require_sha256(projected_digest, "application projected digest")
            if base_digest == projected_digest and operation != "rename":
                raise WorkspaceFormatError("application operation is unchanged")
            if operation == "rename" and base_digest != projected_digest:
                raise WorkspaceFormatError("application rename digest changed")
        elif operation == "delete":
            _require_sha256(base_digest, "application delete digest")
            if projected_digest is not None:
                raise WorkspaceFormatError("application delete projection is invalid")
        else:
            raise WorkspaceFormatError("application operation is unsupported")


def _operation_facts_match_result(
    operations: list[object],
    result: ArtifactApplicationResult,
) -> bool:
    expected_paths: set[str] = set()
    finals: dict[str, tuple[ArtifactKind, str]] = {}
    lineages: set[tuple[str, str, str]] = set()
    deletions: set[tuple[str, str]] = set()
    for raw in operations:
        assert isinstance(raw, dict)
        kind = ArtifactKind(raw["artifact_kind"])
        if raw["operation"] == "rename":
            source = raw["source_path"]
            destination = raw["destination_path"]
            expected_paths.update((source, destination))
            finals[destination] = (kind, raw["projected_digest"])
            lineages.add((source, destination, raw["projected_digest"]))
        else:
            path = raw["path"]
            expected_paths.add(path)
            if raw["operation"] == "delete":
                deletions.add((path, raw["base_digest"]))
            else:
                finals[path] = (kind, raw["projected_digest"])
    return (
        tuple(sorted(expected_paths, key=lambda item: item.encode("utf-8")))
        == result.affected_paths
        and {
            item.path: (item.artifact_kind, item.sha256)
            for item in result.final_artifacts
        }
        == finals
        and {
            (item.source_path, item.destination_path, item.sha256)
            for item in result.rename_lineage
        }
        == lineages
        and {(item.path, item.prior_sha256) for item in result.deletion_provenance}
        == deletions
    )


def _targets_match_operations(
    targets: list[object],
    operations: list[object],
) -> bool:
    expected: set[tuple[object, ...]] = set()
    for raw in operations:
        assert isinstance(raw, dict)
        index = raw["operation_index"]
        operation = raw["operation"]
        kind = raw["artifact_kind"]
        if operation == "rename":
            expected.add(
                (
                    index,
                    operation,
                    "source",
                    raw["source_path"],
                    kind,
                    True,
                    raw["base_digest"],
                    False,
                    None,
                )
            )
            expected.add(
                (
                    index,
                    operation,
                    "destination",
                    raw["destination_path"],
                    kind,
                    False,
                    None,
                    True,
                    raw["projected_digest"],
                )
            )
        else:
            expected.add(
                (
                    index,
                    operation,
                    "target",
                    raw["path"],
                    kind,
                    operation != "create",
                    raw["base_digest"],
                    operation != "delete",
                    raw["projected_digest"],
                )
            )
    actual = {
        (
            target["operation_index"],
            target["operation"],
            target["role"],
            target["path"],
            target["kind"],
            target["old_exists"],
            target["old_sha256"],
            target["new_exists"],
            target["new_sha256"],
        )
        for target in targets
        if isinstance(target, dict)
    }
    return actual == expected and len(actual) == len(targets)


def catalog_entry(
    path: str,
    kind: ArtifactKind,
    sha256: object,
    sequence: object,
    provenance: ArtifactApplicationProvenance,
) -> ArtifactCatalogEntry:
    assert isinstance(sha256, str) and isinstance(sequence, int)
    return ArtifactCatalogEntry(
        path=path,
        kind=kind,
        sha256=sha256,
        application_id=provenance.application_id,
        application_sequence=sequence,
        candidate_id=provenance.candidate_id,
        candidate_revision=provenance.candidate_revision,
        candidate_digest=provenance.candidate_digest,
        change_set_digest=provenance.change_set_digest,
        relationship_validation_digest=provenance.relationship_validation_digest,
    )


def record_result(record: Mapping[str, object]) -> ArtifactApplicationResult:
    try:
        return deserialize_artifact_application_result(encode_journal_json(record["result"]))
    except (KeyError, ArtifactApplicationError) as exc:
        raise WorkspaceFormatError("application record result is invalid") from exc


def target_content(target: Mapping[str, object]) -> bytes:
    try:
        content = base64.b64decode(target["new_content_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceFormatError(
            "application target content encoding is invalid"
        ) from exc
    expected = target.get("new_sha256")
    if not isinstance(expected, str) or hashlib.sha256(content).hexdigest() != expected:
        raise WorkspaceFormatError("application target content digest is invalid")
    require_utf8(content)
    return content


def relationship_digest(value: ArtifactRelationshipValidation) -> str:
    payload = {
        "added_edges": [_edge_value(item) for item in value.added_edges],
        "base_state_digest": value.base_state_digest,
        "change_set_digest": value.change_set_digest,
        "projected_dangling_edges": [
            _edge_value(item) for item in value.projected_dangling_edges
        ],
        "projected_state_digest": value.projected_state_digest,
        "removed_edges": [_edge_value(item) for item in value.removed_edges],
        "retained_edges": [_edge_value(item) for item in value.retained_edges],
        "review_facts": [_review_fact_value(item) for item in value.review_facts],
        "schema_version": value.schema_version,
    }
    return "sha256:" + hashlib.sha256(encode_journal_json(payload)).hexdigest()


def _edge_value(value: ArtifactRelationshipEdge) -> dict[str, object]:
    return {
        "destination_end_utf8_byte": value.destination_end_utf8_byte,
        "destination_start_utf8_byte": value.destination_start_utf8_byte,
        "fragment": value.fragment,
        "relationship_kind": value.relationship_kind.value,
        "source_path": value.source_path,
        "target_path": value.target_path,
    }


def _review_fact_value(value: ArtifactReviewFact) -> dict[str, object]:
    return {
        "artifact_path": value.artifact_path,
        "domain": value.domain.value,
        "operation_index": value.operation_index,
        "reason": value.reason.value,
    }


def operation_primary_path(operation: ArtifactChangeOperation) -> str:
    if type(operation) is ArtifactRenameOperation:
        return operation.source_path
    return operation.path


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise WorkspaceFormatError(f"{label} is invalid")
    return value


def require_utf8(value: bytes) -> bytes:
    if type(value) is not bytes:
        raise WorkspaceFormatError("artifact content must be bytes")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkspaceFormatError("artifact content is not strict UTF-8") from exc
    if b"\x00" in value:
        raise WorkspaceFormatError("artifact content contains NUL")
    return value


def encode_journal_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except Exception as exc:
        raise WorkspaceFormatError("artifact application JSON is invalid") from exc


def _decode_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceFormatError("artifact application JSON has duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise WorkspaceFormatError("artifact application JSON constant is invalid")


__all__ = (
    "application_record",
    "application_result",
    "catalog_entry",
    "decode_journal_json",
    "encode_journal_json",
    "is_application_identifier",
    "operation_primary_path",
    "record_result",
    "relationship_digest",
    "require_utf8",
    "target_content",
    "target_specs",
    "transaction_record",
    "validate_application_record",
    "validate_transaction",
)
