"""Private durable owner for exact writing-artifact application."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import StrEnum
from functools import wraps
from typing import NoReturn
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from ..runtime_state_paths import WRITING_ARTIFACT_DIRECTORY
from ._storage import SecureWorkspaceStorage
from .application_contracts import (
    ARTIFACT_APPLICATION_SCHEMA_VERSION,
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationProvenance,
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
    ArtifactApplicationState,
    serialize_artifact_application_request,
)
from .application_journal import (
    application_record,
    application_result,
    catalog_entry,
    decode_journal_json,
    encode_journal_json,
    is_application_identifier,
    operation_primary_path,
    record_result,
    relationship_digest,
    require_utf8,
    target_content,
    target_specs,
    transaction_record,
    validate_application_record,
    validate_transaction,
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
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactReplaceOperation,
    build_artifact_change_set,
    project_artifact_change_set,
)
from .relationship_policy import (
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    ArtifactRelationshipValidation,
    validate_artifact_relationships,
)


ARTIFACT_STATE_DIRECTORY = WRITING_ARTIFACT_DIRECTORY
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

_RECOVERY_SCHEMA_VERSION = 2


class ArtifactApplicationStoreFailureCode(StrEnum):
    CORRUPTION = "corruption"
    RECOVERY_REQUIRED = "recovery_required"


_STORE_FAILURE_MESSAGES = {
    ArtifactApplicationStoreFailureCode.CORRUPTION: (
        "artifact application state is corrupt"
    ),
    ArtifactApplicationStoreFailureCode.RECOVERY_REQUIRED: (
        "artifact application recovery is required"
    ),
}


class ArtifactApplicationStoreFailure(WorkspaceFormatError):
    """Private fixed-message classification for persisted application state."""

    def __init__(self, code: ArtifactApplicationStoreFailureCode) -> None:
        if type(code) is not ArtifactApplicationStoreFailureCode:
            raise TypeError("code must be an application store failure code")
        self.code = code
        super().__init__(_STORE_FAILURE_MESSAGES[code])


def _raise_store_failure(
    code: ArtifactApplicationStoreFailureCode,
) -> NoReturn:
    try:
        raise ArtifactApplicationStoreFailure(code) from None
    except ArtifactApplicationStoreFailure as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


def _raise_application_failure(code: ArtifactApplicationErrorCode) -> NoReturn:
    try:
        raise ArtifactApplicationError(code) from None
    except ArtifactApplicationError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


def _typed_store_failures(operation):
    """Keep persistence classifications typed and their exception graphs safe."""

    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ArtifactApplicationStoreFailure:
            raise
        except ArtifactChangeSetError as exc:
            if exc.code is ArtifactChangeSetErrorCode.BASE_STATE_MISMATCH:
                _raise_application_failure(ArtifactApplicationErrorCode.DRIFT)
            raise
        except (WorkspaceFormatError, WorkspaceSecurityError):
            _raise_store_failure(ArtifactApplicationStoreFailureCode.CORRUPTION)

    return wrapped


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

    @_typed_store_failures
    def recover(self) -> None:
        """Complete every authoritative prepared application journal."""

        if self._storage.read_bytes_if_exists(ARTIFACT_RECOVERY_BLOCK_PATH) is not None:
            _raise_store_failure(ArtifactApplicationStoreFailureCode.RECOVERY_REQUIRED)
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
                validate_transaction(transaction, expected_id=name[:-5])
                self._complete_transaction(path, transaction)
            except Exception as exc:
                self._block_recovery(
                    "application transaction cannot be recovered",
                    cause=exc,
                )

    @_typed_store_failures
    def catalog(self) -> ArtifactCatalog:
        """Return the exact ledger-audited current catalog image."""

        catalog, _raw, records = self._load_catalog_audited()
        self._audit_catalog(catalog, records)
        return catalog

    @_typed_store_failures
    def inspect_result(
        self,
        application_id: str,
    ) -> ArtifactApplicationResult | None:
        """Return one exact historical result after recovery and full audit."""

        if (
            type(application_id) is not str
            or not is_application_identifier(application_id)
        ):
            _raise_application_failure(ArtifactApplicationErrorCode.INVALID_IDENTIFIER)
        self.recover()
        _catalog, _raw, records = self._load_catalog_audited()
        record = records.get(application_id)
        return None if record is None else record_result(record)

    @_typed_store_failures
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
                require_utf8(content)
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
            require_utf8(content)
            total_bytes += len(content)
            if total_bytes > MAX_RELATIONSHIP_STATE_UTF8_BYTES:
                raise WorkspaceFormatError(
                    "canonical artifact state exceeds its total byte limit"
                )
            state[path] = content
        return dict(sorted(state.items(), key=lambda item: item[0].encode("utf-8")))

    @_typed_store_failures
    def apply(
        self,
        request: ArtifactApplicationRequest,
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
            result = record_result(prior)
            return replace(result, state=ArtifactApplicationState.UNCHANGED)
        catalog, catalog_raw, records = self._load_catalog_audited(records=records)
        base_state = self.current_state()
        return self._apply_new(
            request,
            request_sha256=request_sha256,
            base_state=base_state,
            catalog=catalog,
            catalog_raw=catalog_raw,
            records=records,
        )

    @_typed_store_failures
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
        require_utf8(candidate)
        if not isinstance(core_updates, Mapping):
            raise TypeError("core_updates must be a mapping")
        if not is_application_identifier(debate_id):
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
            require_utf8(content)
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
        operations.sort(key=lambda item: operation_primary_path(item).encode("utf-8"))
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
            relationship_validation_digest=relationship_digest(relationship),
        )
        if provenance.application_id in records:
            raise WorkspaceFormatError("application identity is already reserved")
        request = ArtifactApplicationRequest(
            ARTIFACT_APPLICATION_SCHEMA_VERSION,
            provenance,
            change_set,
            (),
        )
        return self._apply_new(
            request,
            request_sha256=hashlib.sha256(
                serialize_artifact_application_request(request)
            ).hexdigest(),
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
            attached_core_paths=request.attached_core_paths,
        )
        if not validation.is_valid:
            raise ArtifactApplicationError(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        if relationship_digest(validation) != request.relationship_validation_digest:
            raise ArtifactApplicationError(
                ArtifactApplicationErrorCode.CORRELATION_MISMATCH
            )
        self._fault("after_revalidation")
        sequence = len(records) + 1
        targets = target_specs(request.change_set, base_state, projected)
        result = application_result(request.provenance, targets)
        new_entries = dict(catalog.entries)
        for target in targets:
            path = target["path"]
            assert isinstance(path, str)
            if target["new_exists"] is False:
                new_entries.pop(path, None)
                continue
            kind = ArtifactKind(target["kind"])
            new_entries[path] = catalog_entry(
                path,
                kind,
                target["new_sha256"],
                sequence,
                request.provenance,
            )
        catalog_after = ArtifactCatalog(new_entries)
        record = application_record(
            request,
            request_sha256=request_sha256,
            sequence=sequence,
            result=result,
        )
        virtual_records = dict(records)
        virtual_records[request.application_id] = record
        self._audit_catalog(catalog_after, virtual_records)
        transaction_id = uuid4().hex
        transaction = transaction_record(
            transaction_id,
            request,
            record,
            catalog,
            catalog_raw,
            catalog_after,
            targets,
        )
        validate_transaction(transaction, expected_id=transaction_id)
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
        validate_transaction(
            transaction,
            expected_id=transaction["transaction_id"],
        )
        journal_bytes = encode_journal_json(transaction)
        self._require_journal(transaction_path, journal_bytes)
        position = self._catalog_position(transaction)
        if position == "divergent":
            self._block_recovery("artifact catalog diverged from application journal")
        record = transaction["application_record"]
        assert isinstance(record, dict)
        result = record_result(record)
        record_path = f"{ARTIFACT_APPLICATIONS_DIRECTORY}/{result.application_id}.json"
        record_bytes = encode_journal_json(record)
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
                encode_journal_json(after),
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
            target_content(target),
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
                if decode_journal_json(current) == before["image"]:
                    return "before"
            except WorkspaceFormatError:
                return "divergent"
        if current == encode_journal_json(after):
            return "after"
        return "divergent"

    def _fault(self, point: str) -> None:
        if point not in APPLICATION_FAULT_POINTS:
            raise AssertionError("unknown artifact application fault point")
        if self._fault_injector is not None:
            self._fault_injector(point)

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
            if not is_application_identifier(application_id):
                raise WorkspaceFormatError("application record name is invalid")
            value = self._read_json(f"{ARTIFACT_APPLICATIONS_DIRECTORY}/{name}")
            validate_application_record(value)
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
                catalog = ArtifactCatalog.from_dict(decode_journal_json(raw))
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
            result = record_result(record)
            sequence = record["sequence"]
            assert isinstance(sequence, int)
            for lineage in result.rename_lineage:
                expected.pop(lineage.source_path, None)
            for deletion in result.deletion_provenance:
                expected.pop(deletion.path, None)
            for final in result.final_artifacts:
                expected[final.path] = catalog_entry(
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
        return decode_journal_json(self._storage.read_bytes(relative))

    def _write_new_json(self, relative: str, value: object) -> None:
        self._storage.publish_bytes(
            relative,
            encode_journal_json(value),
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=ARTIFACT_STAGING_DIRECTORY,
        )

    def _write_immutable_json(self, relative: str, value: object) -> None:
        encoded = encode_journal_json(value)
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
        _raise_store_failure(ArtifactApplicationStoreFailureCode.RECOVERY_REQUIRED)


__all__: tuple[()] = ()
