"""Private durable owner for exact writing-artifact application."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from ._storage import SecureWorkspaceStorage
from .application_contracts import (
    ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
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
    CORE_ARTIFACT_PATHS,
    MAX_ARTIFACT_PATH_BYTES,
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
    build_artifact_change_set,
    project_artifact_change_set,
)
from .relationship_policy import (
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    ArtifactRelationshipEdge,
    ArtifactRelationshipValidation,
    ArtifactReviewFact,
    validate_artifact_relationships,
)


ARTIFACT_STATE_DIRECTORY = "agent_states/orchestrator_state/writing_artifacts"
ARTIFACT_CATALOG_PATH = f"{ARTIFACT_STATE_DIRECTORY}/catalog.json"
ARTIFACT_APPLICATIONS_DIRECTORY = f"{ARTIFACT_STATE_DIRECTORY}/applications"
ARTIFACT_TRANSACTIONS_DIRECTORY = f"{ARTIFACT_STATE_DIRECTORY}/transactions"
ARTIFACT_SNAPSHOTS_DIRECTORY = f"{ARTIFACT_STATE_DIRECTORY}/input_snapshots"
ARTIFACT_STAGING_DIRECTORY = f"{ARTIFACT_STATE_DIRECTORY}/staging"
ARTIFACT_LOCK_PATH = f"{ARTIFACT_STATE_DIRECTORY}/.artifacts.lock"
ARTIFACT_RECOVERY_BLOCK_PATH = f"{ARTIFACT_STATE_DIRECTORY}/recovery_blocked.json"
_LEGACY_COMMITS_DIRECTORY = f"{ARTIFACT_STATE_DIRECTORY}/commits"

APPLICATION_FAULT_POINTS = (
    "after_revalidation",
    "after_journal",
    "after_target",
    "after_targets",
    "after_record",
    "after_catalog",
    "before_journal_retirement",
    "after_journal_retirement",
)

_TRANSACTION_SCHEMA_VERSION = 1
_APPLICATION_RECORD_SCHEMA_VERSION = 1
_RECOVERY_SCHEMA_VERSION = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class ArtifactApplicationStore:
    """Own the application ledger, journals, catalog projection, and recovery.

    The repository holds its process lock while ``ensure_directories`` performs
    the sole descriptor-safe pre-lease bootstrap needed to create the lease
    parent. Every other mutating or authoritative method requires the active
    repository workspace lease. Construction performs no filesystem access.
    """

    def __init__(
        self,
        storage: SecureWorkspaceStorage,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        if type(storage) is not SecureWorkspaceStorage:
            raise TypeError("storage must be SecureWorkspaceStorage")
        if fault_injector is not None and not callable(fault_injector):
            raise TypeError("fault_injector must be callable or None")
        self._storage = storage
        self._fault_injector = fault_injector

    def ensure_directories(self) -> None:
        """Bootstrap exact state directories under the pre-lease process lock."""

        for path in (
            ARTIFACT_STATE_DIRECTORY,
            ARTIFACT_APPLICATIONS_DIRECTORY,
            ARTIFACT_TRANSACTIONS_DIRECTORY,
            ARTIFACT_SNAPSHOTS_DIRECTORY,
            ARTIFACT_STAGING_DIRECTORY,
        ):
            self._storage.ensure_directory(path)

    def recover(self) -> None:
        """Complete every authoritative prepared application journal."""

        if self._storage.read_bytes_if_exists(ARTIFACT_RECOVERY_BLOCK_PATH) is not None:
            raise WorkspaceFormatError("writing-artifact recovery is blocked")
        self._reject_legacy_commit_records()
        try:
            names = self._storage.list_names(ARTIFACT_TRANSACTIONS_DIRECTORY)
        except FileNotFoundError:
            names = ()
        for name in names:
            if not name.endswith(".json") or len(name) != 37:
                self._block_recovery("unexpected application transaction entry")
            path = f"{ARTIFACT_TRANSACTIONS_DIRECTORY}/{name}"
            try:
                transaction = self._read_json(path)
                self._validate_transaction(transaction, expected_id=name[:-5])
                self._complete_transaction(path, transaction)
            except Exception as exc:
                self._block_recovery(
                    "application transaction cannot be recovered",
                    cause=exc,
                )

    def catalog(self) -> ArtifactCatalog:
        """Return the exact ledger-audited current catalog image."""

        catalog, _raw, records = self._load_catalog_audited()
        self._audit_catalog(catalog, records)
        return catalog

    def current_state(self) -> dict[str, bytes]:
        """Read one complete bounded canonical artifact state under the lease."""

        state: dict[str, bytes] = {}
        total_bytes = 0
        for path in CORE_ARTIFACT_PATHS:
            content = self._storage.read_bytes_if_exists(
                path,
                max_bytes=MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
            )
            if content is not None:
                _require_utf8(content)
                total_bytes += len(content)
                if total_bytes > MAX_RELATIONSHIP_STATE_UTF8_BYTES:
                    raise WorkspaceFormatError(
                        "canonical artifact state exceeds its total byte limit"
                    )
                state[path] = content
        try:
            manuscript = self._storage.list_regular_files(
                "manuscript",
                max_path_bytes=MAX_ARTIFACT_PATH_BYTES,
            )
        except FileNotFoundError:
            manuscript = ()
        for path, _identity in manuscript:
            if canonical_artifact_kind(path) is not ArtifactKind.MANUSCRIPT:
                continue
            if len(state) >= MAX_RELATIONSHIP_ARTIFACTS:
                raise WorkspaceFormatError(
                    "canonical artifact state exceeds its artifact limit"
                )
            content = self._storage.read_bytes(
                path,
                max_bytes=MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
            )
            _require_utf8(content)
            total_bytes += len(content)
            if total_bytes > MAX_RELATIONSHIP_STATE_UTF8_BYTES:
                raise WorkspaceFormatError(
                    "canonical artifact state exceeds its total byte limit"
                )
            state[path] = content
        return dict(sorted(state.items(), key=lambda item: item[0].encode("utf-8")))

    def apply(
        self,
        request: ArtifactApplicationRequest,
        *,
        attached_core_paths: tuple[str, ...],
    ) -> ArtifactApplicationResult:
        """Apply or identify one exact request under the repository lease."""

        if type(request) is not ArtifactApplicationRequest:
            raise TypeError("request must be ArtifactApplicationRequest")
        request.__post_init__()
        self.recover()
        request_bytes = serialize_artifact_application_request(request)
        request_sha256 = hashlib.sha256(request_bytes).hexdigest()
        records = self._load_application_records()
        prior = records.get(request.application_id)
        if prior is not None:
            if prior["request_sha256"] != request_sha256:
                raise ArtifactApplicationError(
                    ArtifactApplicationErrorCode.CORRELATION_MISMATCH
                )
            result = _record_result(prior)
            return replace(result, state=ArtifactApplicationState.UNCHANGED)
        catalog, catalog_raw, records = self._load_catalog_audited(records=records)
        base_state = self.current_state()
        return self._apply_new(
            request,
            request_sha256=request_sha256,
            attached_core_paths=attached_core_paths,
            base_state=base_state,
            catalog=catalog,
            catalog_raw=catalog_raw,
            records=records,
        )

    def apply_consensus(
        self,
        *,
        manuscript_path: str,
        candidate: bytes,
        core_updates: Mapping[str, bytes],
        debate_id: str,
        revision_number: int,
    ) -> ArtifactApplicationResult:
        """Route the retained consensus caller through only the new engine."""

        if canonical_artifact_kind(manuscript_path) is not ArtifactKind.MANUSCRIPT:
            raise WorkspaceSecurityError("consensus manuscript path is not canonical")
        _require_utf8(candidate)
        if not isinstance(core_updates, Mapping):
            raise TypeError("core_updates must be a mapping")
        if _IDENTIFIER.fullmatch(debate_id) is None:
            raise WorkspaceSecurityError("debate ID is unsafe")
        if type(revision_number) is not int or revision_number < 1:
            raise WorkspaceSecurityError("revision number is invalid")
        self.recover()
        records = self._load_application_records()
        catalog, catalog_raw, records = self._load_catalog_audited(records=records)
        base_state = self.current_state()
        operations: list[ArtifactChangeOperation] = []
        candidate_digest = hashlib.sha256(candidate).hexdigest()
        operations.append(
            ArtifactCreateOperation(
                manuscript_path,
                ArtifactKind.MANUSCRIPT,
                candidate,
                candidate_digest,
            )
        )
        for path, content in core_updates.items():
            if path not in CORE_ARTIFACT_PATHS or type(content) is not bytes:
                raise WorkspaceSecurityError("core update is invalid")
            _require_utf8(content)
            digest = hashlib.sha256(content).hexdigest()
            previous = base_state.get(path)
            if previous is None:
                operations.append(
                    ArtifactCreateOperation(
                        path,
                        ArtifactKind.CORE_DOCUMENT,
                        content,
                        digest,
                    )
                )
            elif previous != content:
                operations.append(
                    ArtifactReplaceOperation(
                        path,
                        ArtifactKind.CORE_DOCUMENT,
                        hashlib.sha256(previous).hexdigest(),
                        content,
                        digest,
                    )
                )
        operations.sort(key=lambda item: _operation_primary_path(item).encode("utf-8"))
        change_set = build_artifact_change_set(base_state, tuple(operations))
        relationship = validate_artifact_relationships(
            base_state,
            change_set,
            attached_core_paths=(),
        )
        if not relationship.is_valid:
            raise WorkspaceFormatError(
                "consensus artifact relationships are not complete"
            )
        provenance = ArtifactApplicationProvenance(
            application_id=uuid4().hex,
            candidate_id=debate_id,
            candidate_revision=revision_number,
            candidate_digest="sha256:" + candidate_digest,
            change_set_digest=change_set.change_set_digest,
            relationship_validation_digest=_relationship_digest(relationship),
        )
        if provenance.application_id in records:
            raise WorkspaceFormatError("application identity is already reserved")
        request = ArtifactApplicationRequest(
            ARTIFACT_APPLICATION_SCHEMA_VERSION,
            provenance,
            change_set,
        )
        return self._apply_new(
            request,
            request_sha256=hashlib.sha256(
                serialize_artifact_application_request(request)
            ).hexdigest(),
            attached_core_paths=(),
            base_state=base_state,
            catalog=catalog,
            catalog_raw=catalog_raw,
            records=records,
            relationship=relationship,
        )

    def _apply_new(
        self,
        request: ArtifactApplicationRequest,
        *,
        request_sha256: str,
        attached_core_paths: tuple[str, ...],
        base_state: Mapping[str, bytes],
        catalog: ArtifactCatalog,
        catalog_raw: bytes | None,
        records: dict[str, dict[str, object]],
        relationship: ArtifactRelationshipValidation | None = None,
    ) -> ArtifactApplicationResult:
        projected = project_artifact_change_set(base_state, request.change_set)
        validation = relationship or validate_artifact_relationships(
            base_state,
            request.change_set,
            attached_core_paths=attached_core_paths,
        )
        if not validation.is_valid:
            raise WorkspaceFormatError(
                "artifact application relationships are not complete"
            )
        if _relationship_digest(validation) != request.relationship_validation_digest:
            raise ArtifactApplicationError(
                ArtifactApplicationErrorCode.CORRELATION_MISMATCH
            )
        self._fault("after_revalidation")
        sequence = len(records) + 1
        targets = _target_specs(request.change_set, base_state, projected)
        result = _application_result(request.provenance, targets)
        new_entries = dict(catalog.entries)
        for target in targets:
            path = target["path"]
            assert isinstance(path, str)
            if target["new_exists"] is False:
                new_entries.pop(path, None)
                continue
            kind = ArtifactKind(target["kind"])
            new_entries[path] = _catalog_entry(
                path,
                kind,
                target["new_sha256"],
                sequence,
                request.provenance,
            )
        catalog_after = ArtifactCatalog(new_entries)
        record = _application_record(
            request,
            request_sha256=request_sha256,
            sequence=sequence,
            result=result,
        )
        virtual_records = dict(records)
        virtual_records[request.application_id] = record
        self._audit_catalog(catalog_after, virtual_records)
        transaction_id = uuid4().hex
        transaction = {
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
        self._validate_transaction(transaction, expected_id=transaction_id)
        transaction_path = f"{ARTIFACT_TRANSACTIONS_DIRECTORY}/{transaction_id}.json"
        self._write_new_json(transaction_path, transaction)
        self._fault("after_journal")
        self._complete_transaction(transaction_path, transaction)
        return result

    def _complete_transaction(
        self,
        transaction_path: str,
        transaction: dict[str, object],
    ) -> None:
        self._validate_transaction(
            transaction,
            expected_id=transaction["transaction_id"],
        )
        journal_bytes = _json_bytes(transaction)
        self._require_journal(transaction_path, journal_bytes)
        position = self._catalog_position(transaction)
        if position == "divergent":
            self._block_recovery("artifact catalog diverged from application journal")
        record = transaction["application_record"]
        assert isinstance(record, dict)
        result = _record_result(record)
        record_path = f"{ARTIFACT_APPLICATIONS_DIRECTORY}/{result.application_id}.json"
        record_bytes = _json_bytes(record)
        targets = transaction["targets"]
        assert isinstance(targets, list)
        if position == "before":
            self._verify_target_states(targets, allow_old=True)
            for target in targets:
                assert isinstance(target, dict)
                self._publish_target(target)
                self._fault("after_target")
            self._verify_target_states(targets, allow_old=False)
            self._fault("after_targets")
            self._require_journal(transaction_path, journal_bytes)
            current_record = self._storage.read_bytes_if_exists(record_path)
            if current_record not in {None, record_bytes}:
                self._block_recovery("immutable application record conflicts")
            if current_record is None:
                self._write_immutable_json(record_path, record)
            if self._storage.read_bytes_if_exists(record_path) != record_bytes:
                self._block_recovery("immutable application record diverged")
            self._fault("after_record")
            self._require_journal(transaction_path, journal_bytes)
            if self._catalog_position(transaction) != "before":
                self._block_recovery("artifact catalog changed before publication")
            before = transaction["catalog_before"]
            assert isinstance(before, dict)
            after = transaction["catalog_after"]
            self._storage.publish_bytes(
                ARTIFACT_CATALOG_PATH,
                _json_bytes(after),
                expected_sha256=before["sha256"],
                expected_exists=before["exists"],
                create_only=False,
                staging_directory=ARTIFACT_STAGING_DIRECTORY,
            )
            self._fault("after_catalog")
        self._require_final_transaction_state(transaction, record_path, record_bytes)
        self._fault("before_journal_retirement")
        self._require_journal(transaction_path, journal_bytes)
        self._storage.unlink(
            transaction_path,
            expected_sha256=hashlib.sha256(journal_bytes).hexdigest(),
            retirement_directory=ARTIFACT_STAGING_DIRECTORY,
            missing_ok=False,
        )
        self._fault("after_journal_retirement")

    def _publish_target(self, target: dict[str, object]) -> None:
        path = target["path"]
        assert isinstance(path, str)
        current = self._storage.read_bytes_if_exists(path)
        current_sha256 = (
            None if current is None else hashlib.sha256(current).hexdigest()
        )
        if target["new_exists"] is False:
            if current is None:
                return
            if current_sha256 != target["old_sha256"]:
                self._block_recovery("artifact target diverged before retirement")
            self._storage.unlink(
                path,
                expected_sha256=target["old_sha256"],
                retirement_directory=ARTIFACT_STAGING_DIRECTORY,
                missing_ok=False,
            )
            return
        if current_sha256 == target["new_sha256"]:
            return
        self._storage.publish_bytes(
            path,
            _target_content(target),
            expected_sha256=target["old_sha256"],
            expected_exists=target["old_exists"],
            create_only=not target["old_exists"],
            staging_directory=ARTIFACT_STAGING_DIRECTORY,
        )

    def _verify_target_states(
        self,
        targets: list[object],
        *,
        allow_old: bool,
    ) -> None:
        for raw in targets:
            assert isinstance(raw, dict)
            path = raw["path"]
            assert isinstance(path, str)
            current = self._storage.read_bytes_if_exists(path)
            current_sha256 = (
                None if current is None else hashlib.sha256(current).hexdigest()
            )
            is_new = (current is not None) is raw["new_exists"] and (
                not raw["new_exists"] or current_sha256 == raw["new_sha256"]
            )
            is_old = (current is not None) is raw["old_exists"] and (
                not raw["old_exists"] or current_sha256 == raw["old_sha256"]
            )
            if not is_new and not (allow_old and is_old):
                self._block_recovery(
                    "artifact target diverged from application journal"
                )

    def _require_final_transaction_state(
        self,
        transaction: dict[str, object],
        record_path: str,
        record_bytes: bytes,
    ) -> None:
        targets = transaction["targets"]
        assert isinstance(targets, list)
        self._verify_target_states(targets, allow_old=False)
        if self._storage.read_bytes_if_exists(record_path) != record_bytes:
            self._block_recovery("published application record is missing or divergent")
        if self._catalog_position(transaction) != "after":
            self._block_recovery("artifact catalog is not durably complete")
        catalog = ArtifactCatalog.from_dict(transaction["catalog_after"])
        records = self._load_application_records()
        self._audit_catalog(catalog, records)

    def _require_journal(self, path: str, expected: bytes) -> None:
        try:
            current = self._storage.read_bytes(path)
        except (FileNotFoundError, WorkspaceSecurityError) as exc:
            self._block_recovery("application journal is missing or unsafe", cause=exc)
        if current != expected:
            self._block_recovery("application journal diverged")

    def _catalog_position(self, transaction: dict[str, object]) -> str:
        before = transaction["catalog_before"]
        after = transaction["catalog_after"]
        assert isinstance(before, dict) and isinstance(after, dict)
        current = self._storage.read_bytes_if_exists(ARTIFACT_CATALOG_PATH)
        if current is None:
            return "before" if before["exists"] is False else "divergent"
        if (
            before["exists"] is True
            and hashlib.sha256(current).hexdigest() == before["sha256"]
        ):
            try:
                if self._decode_json(current) == before["image"]:
                    return "before"
            except WorkspaceFormatError:
                return "divergent"
        if current == _json_bytes(after):
            return "after"
        return "divergent"

    def _fault(self, point: str) -> None:
        if point not in APPLICATION_FAULT_POINTS:
            raise AssertionError("unknown artifact application fault point")
        if self._fault_injector is not None:
            self._fault_injector(point)

    def _validate_transaction(
        self,
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
        self._validate_application_record(record)
        assert isinstance(record, dict)
        result = _record_result(record)
        try:
            request_bytes = _json_bytes(value["request"])
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
            != hashlib.sha256(_json_bytes(before_record["image"])).hexdigest()
        ):
            raise WorkspaceFormatError("application catalog-before digest is invalid")
        targets = value["targets"]
        if type(targets) is not list or not targets:
            raise WorkspaceFormatError("application targets are invalid")
        for target in targets:
            self._validate_target(target)
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
        expected_result = _application_result(result.provenance, targets)
        if expected_result != result:
            raise WorkspaceFormatError("application result does not match targets")
        expected_entries = dict(catalog_before.entries)
        for target in targets:
            path = target["path"]
            assert isinstance(path, str)
            if target["new_exists"] is False:
                expected_entries.pop(path, None)
            else:
                expected_entries[path] = _catalog_entry(
                    path,
                    ArtifactKind(target["kind"]),
                    target["new_sha256"],
                    record["sequence"],
                    result.provenance,
                )
        if ArtifactCatalog(expected_entries) != catalog_after:
            raise WorkspaceFormatError("application catalog transition is invalid")

    @staticmethod
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
            _target_content(value)
        elif new_sha256 is not None or content is not None:
            raise WorkspaceFormatError("retired application target has new content")
        if not value["old_exists"] and not value["new_exists"]:
            raise WorkspaceFormatError("application target has no state")
        if value["old_exists"] and value["new_exists"] and old_sha256 == new_sha256:
            raise WorkspaceFormatError("application target is unchanged")

    def _validate_application_record(self, value: object) -> None:
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
                _json_bytes(value["result"])
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

    def _load_application_records(self) -> dict[str, dict[str, object]]:
        try:
            names = self._storage.list_names(ARTIFACT_APPLICATIONS_DIRECTORY)
        except FileNotFoundError:
            names = ()
        records: dict[str, dict[str, object]] = {}
        sequences: dict[int, str] = {}
        for name in names:
            if not name.endswith(".json"):
                raise WorkspaceFormatError("unexpected application record entry")
            application_id = name[:-5]
            if _IDENTIFIER.fullmatch(application_id) is None:
                raise WorkspaceFormatError("application record name is invalid")
            value = self._read_json(f"{ARTIFACT_APPLICATIONS_DIRECTORY}/{name}")
            self._validate_application_record(value)
            assert isinstance(value, dict)
            if value["application_id"] != application_id:
                raise WorkspaceFormatError("application record name does not match")
            sequence = value["sequence"]
            assert isinstance(sequence, int)
            if application_id in records or sequence in sequences:
                raise WorkspaceFormatError("application ledger identity is duplicated")
            records[application_id] = value
            sequences[sequence] = application_id
        if set(sequences) != set(range(1, len(records) + 1)):
            raise WorkspaceFormatError("application ledger sequence is incomplete")
        return records

    def _load_catalog_audited(
        self,
        *,
        records: dict[str, dict[str, object]] | None = None,
    ) -> tuple[ArtifactCatalog, bytes | None, dict[str, dict[str, object]]]:
        ledger = self._load_application_records() if records is None else records
        raw = self._storage.read_bytes_if_exists(ARTIFACT_CATALOG_PATH)
        if raw is None:
            if ledger:
                raise WorkspaceFormatError(
                    "artifact catalog is missing from application ledger"
                )
            catalog = ArtifactCatalog.empty()
        else:
            try:
                catalog = ArtifactCatalog.from_dict(self._decode_json(raw))
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError(
                    "artifact catalog schema is unsupported"
                ) from exc
        self._audit_catalog(catalog, ledger)
        return catalog, raw, ledger

    @staticmethod
    def _audit_catalog(
        catalog: ArtifactCatalog,
        records: Mapping[str, dict[str, object]],
    ) -> None:
        expected: dict[str, ArtifactCatalogEntry] = {}
        ordered = sorted(records.values(), key=lambda item: item["sequence"])
        for record in ordered:
            result = _record_result(record)
            sequence = record["sequence"]
            assert isinstance(sequence, int)
            for lineage in result.rename_lineage:
                expected.pop(lineage.source_path, None)
            for deletion in result.deletion_provenance:
                expected.pop(deletion.path, None)
            for final in result.final_artifacts:
                expected[final.path] = _catalog_entry(
                    final.path,
                    final.artifact_kind,
                    final.sha256,
                    sequence,
                    result.provenance,
                )
        if ArtifactCatalog(expected) != catalog:
            raise WorkspaceFormatError(
                "artifact catalog does not match application ledger"
            )

    def _reject_legacy_commit_records(self) -> None:
        try:
            names = self._storage.list_names(_LEGACY_COMMITS_DIRECTORY)
        except FileNotFoundError:
            return
        if names:
            raise WorkspaceFormatError(
                "legacy writing-artifact commit records are unsupported"
            )

    def _read_json(self, relative: str) -> object:
        return self._decode_json(self._storage.read_bytes(relative))

    @staticmethod
    def _decode_json(raw: bytes) -> object:
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
        if _json_bytes(value) != raw:
            raise WorkspaceFormatError("artifact application JSON is noncanonical")
        return value

    def _write_new_json(self, relative: str, value: object) -> None:
        self._storage.publish_bytes(
            relative,
            _json_bytes(value),
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=ARTIFACT_STAGING_DIRECTORY,
        )

    def _write_immutable_json(self, relative: str, value: object) -> None:
        encoded = _json_bytes(value)
        current = self._storage.read_bytes_if_exists(relative)
        if current is not None:
            if current != encoded:
                raise WorkspaceFormatError("immutable application record conflicts")
            return
        self._write_new_json(relative, value)

    def _block_recovery(
        self,
        reason: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        marker = {
            "reason": reason,
            "schema_version": _RECOVERY_SCHEMA_VERSION,
        }
        try:
            current = self._storage.read_bytes_if_exists(ARTIFACT_RECOVERY_BLOCK_PATH)
            if current is None:
                self._write_new_json(ARTIFACT_RECOVERY_BLOCK_PATH, marker)
        except Exception:
            pass
        error = WorkspaceFormatError("writing-artifact recovery is blocked")
        if cause is not None:
            raise error from cause
        raise error


def _target_specs(
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


def _application_result(
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


def _application_record(
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


def _catalog_entry(
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


def _record_result(record: Mapping[str, object]) -> ArtifactApplicationResult:
    try:
        return deserialize_artifact_application_result(_json_bytes(record["result"]))
    except (KeyError, ArtifactApplicationError) as exc:
        raise WorkspaceFormatError("application record result is invalid") from exc


def _target_content(target: Mapping[str, object]) -> bytes:
    try:
        content = base64.b64decode(target["new_content_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceFormatError(
            "application target content encoding is invalid"
        ) from exc
    expected = target.get("new_sha256")
    if not isinstance(expected, str) or hashlib.sha256(content).hexdigest() != expected:
        raise WorkspaceFormatError("application target content digest is invalid")
    _require_utf8(content)
    return content


def _relationship_digest(value: ArtifactRelationshipValidation) -> str:
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
    return "sha256:" + hashlib.sha256(_json_bytes(payload)).hexdigest()


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


def _operation_primary_path(operation: ArtifactChangeOperation) -> str:
    if type(operation) is ArtifactRenameOperation:
        return operation.source_path
    return operation.path


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise WorkspaceFormatError(f"{label} is invalid")
    return value


def _require_utf8(value: bytes) -> bytes:
    if type(value) is not bytes:
        raise WorkspaceFormatError("artifact content must be bytes")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WorkspaceFormatError("artifact content is not strict UTF-8") from exc
    if b"\x00" in value:
        raise WorkspaceFormatError("artifact content contains NUL")
    return value


def _json_bytes(value: object) -> bytes:
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


__all__: tuple[()] = ()
