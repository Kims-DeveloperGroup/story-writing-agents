"""Filesystem repository for agent-authored canonical writing artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from ..agents import CORE_DOCUMENTS, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from ..errors import (
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ._reference_evidence import ReferenceEvidenceStore
from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from .authorization import (
    ArtifactReferenceDelivery,
    ReadCapability,
    ReferenceAuthorization,
    ReferenceAuthorizationError,
    ReferenceAuthorizationErrorCode,
    ReferenceInvocationCorrelation,
    ReferenceInvocationState,
    ReferenceOperationHistory,
    ReferenceOperationManifest,
    ReferenceOperationState,
    build_authorized_references,
)
from .catalog import (
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    canonical_artifact_kind,
    manuscript_artifact_path,
)
from .references import (
    ArtifactContentSnapshot,
    ArtifactReference,
    ArtifactReferenceBatch,
)
from .read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
    ReferenceReadInvocation,
    ReferenceReadRequest,
    ReferenceReadResult,
)


_STATE_DIRECTORY = "agent_states/orchestrator_state/writing_artifacts"
_CATALOG_PATH = f"{_STATE_DIRECTORY}/catalog.json"
_COMMITS_DIRECTORY = f"{_STATE_DIRECTORY}/commits"
_TRANSACTIONS_DIRECTORY = f"{_STATE_DIRECTORY}/transactions"
_SNAPSHOTS_DIRECTORY = f"{_STATE_DIRECTORY}/input_snapshots"
_STAGING_DIRECTORY = f"{_STATE_DIRECTORY}/staging"
_LOCK_PATH = f"{_STATE_DIRECTORY}/.artifacts.lock"
_RECOVERY_BLOCK_PATH = f"{_STATE_DIRECTORY}/recovery_blocked.json"
_TRANSACTION_SCHEMA_VERSION = 2
_COMMIT_SCHEMA_VERSION = 1
_RECOVERY_SCHEMA_VERSION = 1
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class WritingArtifactRepository:
    """Own canonical artifact provenance, publication, and recovery.

    Construction performs no filesystem I/O. Every public read or commit owns
    both a repository-local lock and the workspace-local advisory lock, then
    completes or rejects pending transactions before consulting the catalog.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._storage = SecureWorkspaceStorage(workspace_root)
        self._lock = threading.RLock()
        self._reference_authorization_owner = object()
        self._reference_evidence = ReferenceEvidenceStore(
            self._storage,
            _SNAPSHOTS_DIRECTORY,
            _STAGING_DIRECTORY,
        )

    def catalog(self) -> ArtifactCatalog:
        """Return only current, provenance-backed canonical entries."""

        with self._locked_repository():
            catalog = self._load_catalog()
            current: dict[str, ArtifactCatalogEntry] = {}
            for path, entry in catalog.entries.items():
                try:
                    content = self._storage.read_bytes(path)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                if hashlib.sha256(content).hexdigest() == entry.sha256:
                    current[path] = entry
            return ArtifactCatalog(current)

    def list_paths(self) -> tuple[str, ...]:
        """Return sorted complete workspace-relative eligible identities."""

        return tuple(sorted(self.catalog().entries))

    def read_current(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        """Return immutable current bytes for only the requested artifacts."""

        if type(paths) is not tuple:
            raise TypeError("artifact paths must be a tuple")
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths must be unique")
        for path in paths:
            if canonical_artifact_kind(path) is None:
                raise WorkspaceSecurityError(
                    "path is not a canonical writing artifact"
                )
        with self._locked_repository():
            catalog = self._load_catalog()
            snapshots: list[ArtifactContentSnapshot] = []
            for path in paths:
                entry = catalog.entries.get(path)
                if entry is None:
                    continue
                try:
                    content = self._storage.read_bytes(path)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                if hashlib.sha256(content).hexdigest() != entry.sha256:
                    continue
                snapshots.append(ArtifactContentSnapshot(entry, content))
            return tuple(snapshots)

    def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read a current catalog entry only after hash and provenance checks."""

        if canonical_artifact_kind(path) is None:
            raise WorkspaceSecurityError("path is not a canonical writing artifact")
        with self._locked_repository():
            catalog = self._load_catalog()
            entry = catalog.entries.get(path)
            if entry is None:
                raise WorkspaceSecurityError("artifact has no repository provenance")
            content = self._storage.read_bytes(path, max_bytes=max_bytes)
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                raise WorkspaceSecurityError("artifact provenance does not match current bytes")
            try:
                return content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WorkspaceFormatError("artifact is not valid UTF-8") from exc

    def capture_reference_authorization(
        self,
        *,
        operation_id: str,
        batch: ArtifactReferenceBatch,
    ) -> ReferenceAuthorization | None:
        """Capture one immutable, repository-authorized reference snapshot."""

        if type(batch) is not ArtifactReferenceBatch:
            raise TypeError("batch must be an ArtifactReferenceBatch")
        if not batch.references:
            return None
        manifest = ReferenceOperationManifest(operation_id, batch.references)
        began = False
        with self._locked_repository():
            try:
                self._reference_evidence.begin_operation(manifest)
                began = True
                catalog = self._load_catalog()
                sources: dict[str, tuple[ArtifactCatalogEntry, bytes]] = {}
                for reference in batch.references:
                    if reference.path in sources:
                        continue
                    entry = catalog.entries.get(reference.path)
                    if entry is None:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.NOT_FOUND
                        )
                    try:
                        content = self._storage.read_bytes(reference.path)
                    except (FileNotFoundError, ArtifactEntrySecurityError):
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.INELIGIBLE
                        ) from None
                    if hashlib.sha256(content).hexdigest() != entry.sha256:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.DRIFT
                        )
                    sources[reference.path] = (entry, content)
                entries = tuple(sources[item.path][0] for item in batch.references)
                contents = tuple(sources[item.path][1] for item in batch.references)
                proofs, captured = build_authorized_references(
                    batch,
                    entries,
                    contents,
                )
                self._reference_evidence.append_operation_event(
                    operation_id,
                    ReferenceOperationState.CAPTURED,
                    proofs,
                )
                return ReferenceAuthorization(
                    manifest,
                    captured,
                    owner=self._reference_authorization_owner,
                )
            except ReferenceAuthorizationError as exc:
                if began:
                    self._reference_evidence.append_operation_event(
                        operation_id,
                        ReferenceOperationState.TERMINATED,
                        (),
                        outcome=exc.code,
                    )
                raise

    def authorize_reference_invocation(
        self,
        authorization: ReferenceAuthorization,
        correlation: ReferenceInvocationCorrelation,
    ) -> ReadCapability:
        """Issue one durable, one-use capability over a captured operation."""

        if type(authorization) is not ReferenceAuthorization:
            raise TypeError("authorization must be a ReferenceAuthorization")
        if type(correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        authorization.require_owner(self._reference_authorization_owner)
        if correlation.operation_id != authorization.manifest.operation_id:
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        references = authorization.authorized_references
        capability_id = uuid4().hex
        with self._locked_repository():
            self._reference_evidence.begin_invocation(
                correlation,
                capability_id,
                references,
            )
        return ReadCapability(
            authorization,
            correlation,
            capability_id=capability_id,
            authorized_references=references,
            owner=self._reference_authorization_owner,
        )

    def begin_reference_invocation(
        self,
        authorization: ReferenceAuthorization,
        correlation: ReferenceInvocationCorrelation,
    ) -> ReferenceReadInvocation:
        """Begin one provider-observed read invocation over a captured batch."""

        if type(authorization) is not ReferenceAuthorization:
            raise TypeError("authorization must be a ReferenceAuthorization")
        if type(correlation) is not ReferenceInvocationCorrelation:
            raise TypeError("correlation must be a ReferenceInvocationCorrelation")
        request = ReferenceReadRequest(
            correlation,
            authorization.manifest.references,
        )
        capability = self.authorize_reference_invocation(
            authorization,
            correlation,
        )
        return _RepositoryReferenceReadInvocation(self, request, capability)

    def deliver_authorized_references(
        self,
        capability: ReadCapability,
        requested: tuple[ArtifactReference, ...],
    ) -> ArtifactReferenceDelivery:
        """Consume a capability and return only its captured selected bytes."""

        if type(capability) is not ReadCapability:
            raise TypeError("capability must be a ReadCapability")
        if type(requested) is not tuple or any(
            type(item) is not ArtifactReference for item in requested
        ):
            raise TypeError("requested references must be an ArtifactReference tuple")
        authorization = capability.consume(self._reference_authorization_owner)
        try:
            references, contents = authorization.delivery_snapshot(
                self._reference_authorization_owner
            )
        except ReferenceAuthorizationError as exc:
            self._terminate_invocation_after_failure(capability, exc.code)
            raise
        if references != capability.authorized_references:
            self._terminate_invocation_after_failure(
                capability,
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            )
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        if requested != authorization.manifest.references:
            self._terminate_invocation_after_failure(
                capability,
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH,
            )
            raise ReferenceAuthorizationError(
                ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
            )
        try:
            with self._locked_repository():
                self._revalidate_reference_authorization(authorization)
                self._reference_evidence.append_invocation_event(
                    capability.correlation,
                    capability.capability_id,
                    ReferenceInvocationState.DELIVERED,
                    references,
                )
        except ReferenceAuthorizationError as exc:
            self._terminate_invocation_after_failure(capability, exc.code)
            raise
        return ArtifactReferenceDelivery(
            capability.correlation,
            references,
            contents,
        )

    def record_reference_result_received(
        self,
        capability: ReadCapability,
    ) -> None:
        """Persist that a provider result was received for this invocation."""

        authorization = self._authorization_from_capability(capability)
        with self._locked_repository():
            self._reference_evidence.append_invocation_event(
                capability.correlation,
                capability.capability_id,
                ReferenceInvocationState.RESULT_RECEIVED,
                authorization.authorized_references,
            )

    def accept_reference_invocation(self, capability: ReadCapability) -> None:
        """Revalidate and durably accept one provider invocation result."""

        authorization = self._authorization_from_capability(capability)
        references = authorization.authorized_references
        try:
            with self._locked_repository():
                self._revalidate_reference_authorization(authorization)
                self._reference_evidence.append_invocation_event(
                    capability.correlation,
                    capability.capability_id,
                    ReferenceInvocationState.ACCEPTED,
                    references,
                )
        except ReferenceAuthorizationError as exc:
            self._terminate_invocation_after_failure(capability, exc.code)
            raise

    def terminate_reference_invocation(
        self,
        capability: ReadCapability,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        """Durably terminate an invocation with one content-free outcome."""

        if type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode")
        if type(capability) is not ReadCapability:
            raise TypeError("capability must be a ReadCapability")
        authorization = capability.retire(self._reference_authorization_owner)
        with self._locked_repository():
            self._reference_evidence.append_invocation_event(
                capability.correlation,
                capability.capability_id,
                ReferenceInvocationState.TERMINATED,
                authorization.authorized_references,
                outcome=outcome,
            )

    def revalidate_reference_operation(
        self,
        authorization: ReferenceAuthorization,
        *,
        checkpoint: str,
    ) -> None:
        """Revalidate and record an explicit top-level operation checkpoint."""

        if type(authorization) is not ReferenceAuthorization:
            raise TypeError("authorization must be a ReferenceAuthorization")
        authorization.require_owner(self._reference_authorization_owner)
        references = authorization.authorized_references
        try:
            with self._locked_repository():
                self._revalidate_reference_authorization(authorization)
                self._reference_evidence.append_operation_event(
                    authorization.manifest.operation_id,
                    ReferenceOperationState.REVALIDATED,
                    references,
                    checkpoint=checkpoint,
                )
        except ReferenceAuthorizationError as exc:
            with self._locked_repository():
                self._reference_evidence.append_operation_event(
                    authorization.manifest.operation_id,
                    ReferenceOperationState.TERMINATED,
                    references,
                    outcome=exc.code,
                )
            authorization.close()
            raise

    def finish_reference_operation(
        self,
        authorization: ReferenceAuthorization,
        *,
        outcome: ReferenceAuthorizationErrorCode | None,
    ) -> None:
        """Accept after final revalidation, or terminate, then dispose bytes."""

        if type(authorization) is not ReferenceAuthorization:
            raise TypeError("authorization must be a ReferenceAuthorization")
        if outcome is not None and type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode or None")
        authorization.require_owner(self._reference_authorization_owner)
        references = authorization.authorized_references
        finished = False
        try:
            with self._locked_repository():
                if outcome is None:
                    self._revalidate_reference_authorization(authorization)
                    state = ReferenceOperationState.ACCEPTED
                else:
                    state = ReferenceOperationState.TERMINATED
                self._reference_evidence.append_operation_event(
                    authorization.manifest.operation_id,
                    state,
                    references,
                    outcome=outcome,
                )
                finished = True
        except ReferenceAuthorizationError as exc:
            if outcome is None:
                with self._locked_repository():
                    self._reference_evidence.append_operation_event(
                        authorization.manifest.operation_id,
                        ReferenceOperationState.TERMINATED,
                        references,
                        outcome=exc.code,
                    )
                    finished = True
            raise
        finally:
            if finished:
                authorization.close()

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return one history, or ``None`` when the operation is absent."""

        with self._locked_repository():
            return self._reference_evidence.inspect(operation_id)

    def _authorization_from_capability(
        self,
        capability: ReadCapability,
        *,
        require_consumed: bool = True,
    ) -> ReferenceAuthorization:
        if type(capability) is not ReadCapability:
            raise TypeError("capability must be a ReadCapability")
        return capability.authorization_for(
            self._reference_authorization_owner,
            require_consumed=require_consumed,
        )

    def _terminate_invocation_after_failure(
        self,
        capability: ReadCapability,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        with self._locked_repository():
            self._reference_evidence.append_invocation_event(
                capability.correlation,
                capability.capability_id,
                ReferenceInvocationState.TERMINATED,
                capability.authorized_references,
                outcome=outcome,
            )

    def _revalidate_reference_authorization(
        self,
        authorization: ReferenceAuthorization,
    ) -> None:
        """Require exact current provenance and bytes for the captured paths."""

        authorization.require_owner(self._reference_authorization_owner)
        references = authorization.authorized_references
        catalog = self._load_catalog()
        observed: set[str] = set()
        for authorized in references:
            path = authorized.reference.path
            if path in observed:
                continue
            observed.add(path)
            if catalog.entries.get(path) != authorized.catalog_entry:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.DRIFT
                )
            try:
                content = self._storage.read_bytes(path)
            except (FileNotFoundError, ArtifactEntrySecurityError):
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.DRIFT
                ) from None
            if hashlib.sha256(content).hexdigest() != authorized.catalog_entry.sha256:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.DRIFT
                )

    def commit_consensus(
        self,
        *,
        output_path: str,
        candidate: str,
        core_updates: Mapping[str, str],
        session_id: str,
        debate_id: str,
        revision_id: str,
        candidate_author_agent_id: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Publish one unanimous story-agent result as a recoverable commit."""

        manuscript_path = manuscript_artifact_path(output_path)
        candidate_bytes = self._encode_agent_text(candidate, "approved candidate")
        updates = dict(core_updates)
        unknown = set(updates).difference(CORE_DOCUMENTS)
        if unknown:
            raise WorkspaceSecurityError(
                f"core document is not allowlisted: {sorted(unknown)[0]}"
            )
        encoded_updates = {
            filename: self._encode_agent_text(content, "core document update")
            for filename, content in updates.items()
        }
        for name, value in (
            ("session ID", session_id),
            ("debate ID", debate_id),
            ("revision ID", revision_id),
        ):
            self._validate_identity(value, name)
        expected_candidate_author = (
            EXECUTION_SPECIALIST if revision_id == "r0" else LEAD_ORCHESTRATOR
        )
        if candidate_author_agent_id != expected_candidate_author:
            raise WorkspaceSecurityError(
                "candidate author does not match the approved revision"
            )

        with self._locked_repository():
            catalog, catalog_raw, ledger = self._load_catalog_audited()
            manuscript_ledger = self._ledger_manuscript_paths(ledger)
            if manuscript_path in manuscript_ledger:
                raise ManuscriptExistsError(
                    f"refusing to overwrite cataloged path {manuscript_path}"
                )
            if self._storage.read_bytes_if_exists(manuscript_path) is not None:
                raise ManuscriptExistsError(
                    f"refusing to overwrite {manuscript_path}"
                )
            commit_id = uuid4().hex
            if commit_id in ledger:
                raise WorkspaceFormatError(
                    "artifact commit identity is already reserved"
                )
            committed_at = self._next_commit_timestamp(ledger)
            target_specs: list[dict[str, object]] = []
            new_entries = dict(catalog.entries)
            ordered_updates = tuple(
                filename for filename in CORE_DOCUMENTS if filename in updates
            )
            artifacts = [
                (
                    manuscript_path,
                    ArtifactKind.MANUSCRIPT,
                    candidate_bytes,
                    candidate_author_agent_id,
                    True,
                )
            ]
            artifacts.extend(
                (
                    f"core_documents/{filename}",
                    ArtifactKind.CORE_DOCUMENT,
                    encoded_updates[filename],
                    LEAD_ORCHESTRATOR,
                    False,
                )
                for filename in ordered_updates
            )
            for path, kind, content, author, create_only in artifacts:
                previous = self._storage.read_bytes_if_exists(path)
                if create_only and previous is not None:
                    raise ManuscriptExistsError(f"refusing to overwrite {path}")
                content_hash = hashlib.sha256(content).hexdigest()
                entry = ArtifactCatalogEntry(
                    path=path,
                    kind=kind,
                    sha256=content_hash,
                    commit_id=commit_id,
                    committed_at=committed_at,
                    author_agent_id=author,
                    session_id=session_id,
                    debate_id=debate_id,
                    revision_id=revision_id,
                )
                new_entries[path] = entry
                target_specs.append(
                    {
                        "author_agent_id": author,
                        "create_only": create_only,
                        "kind": kind.value,
                        "new_content_base64": base64.b64encode(content).decode("ascii"),
                        "new_sha256": content_hash,
                        "old_exists": previous is not None,
                        "old_sha256": (
                            None
                            if previous is None
                            else hashlib.sha256(previous).hexdigest()
                        ),
                        "path": path,
                    }
                )

            commit_record = {
                "artifacts": [
                    {
                        "author_agent_id": spec["author_agent_id"],
                        "kind": spec["kind"],
                        "path": spec["path"],
                        "sha256": spec["new_sha256"],
                    }
                    for spec in target_specs
                ],
                "commit_id": commit_id,
                "committed_at": committed_at,
                "debate_id": debate_id,
                "revision_id": revision_id,
                "schema_version": _COMMIT_SCHEMA_VERSION,
                "session_id": session_id,
            }
            transaction = {
                "catalog_before": {
                    "exists": catalog_raw is not None,
                    "image": catalog.to_dict(),
                    "sha256": (
                        None
                        if catalog_raw is None
                        else hashlib.sha256(catalog_raw).hexdigest()
                    ),
                },
                "catalog_after": ArtifactCatalog(new_entries).to_dict(),
                "commit": commit_record,
                "schema_version": _TRANSACTION_SCHEMA_VERSION,
                "targets": target_specs,
                "transaction_id": commit_id,
            }
            self._validate_transaction(transaction, expected_id=commit_id)
            virtual_ledger = dict(ledger)
            virtual_ledger[commit_id] = (
                commit_record,
                self._json_bytes(commit_record),
            )
            self._audit_catalog(
                ArtifactCatalog(new_entries),
                virtual_ledger,
                catalog_present=True,
            )
            transaction_path = f"{_TRANSACTIONS_DIRECTORY}/{commit_id}.json"
            self._write_new_json(transaction_path, transaction)
            self._complete_transaction(transaction_path, transaction)
            return manuscript_path, ordered_updates

    @staticmethod
    def _encode_agent_text(value: object, label: str) -> bytes:
        if type(value) is not str:
            raise WorkspaceFormatError(f"{label} must be text")
        try:
            return value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise WorkspaceFormatError(f"{label} must be strict UTF-8") from exc

    @staticmethod
    def _validate_identity(value: object, label: str) -> str:
        if type(value) is not str or _SAFE_IDENTITY.fullmatch(value) is None:
            raise WorkspaceSecurityError(f"{label} is unsafe")
        return value

    def _locked_repository(self):
        return _RepositoryLock(self)

    def _ensure_state_directories(self) -> None:
        for path in (
            _STATE_DIRECTORY,
            _COMMITS_DIRECTORY,
            _TRANSACTIONS_DIRECTORY,
            _SNAPSHOTS_DIRECTORY,
            _STAGING_DIRECTORY,
        ):
            self._storage.ensure_directory(path)

    def _recover(self) -> None:
        if self._storage.read_bytes_if_exists(_RECOVERY_BLOCK_PATH) is not None:
            raise WorkspaceFormatError("writing-artifact recovery is blocked")
        try:
            names = self._storage.list_names(_TRANSACTIONS_DIRECTORY)
        except (WorkspaceFormatError, WorkspaceSecurityError):
            raise
        for name in names:
            if not name.endswith(".json") or len(name) != 37:
                self._block_recovery("unexpected transaction state entry")
            path = f"{_TRANSACTIONS_DIRECTORY}/{name}"
            try:
                transaction = self._read_json(path)
                self._validate_transaction(transaction, expected_id=name[:-5])
                self._complete_transaction(path, transaction)
            except Exception as exc:
                self._block_recovery(
                    f"transaction {name[:-5]} cannot be recovered",
                    cause=exc,
                )

    def _complete_transaction(
        self,
        transaction_path: str,
        transaction: dict[str, object],
    ) -> None:
        self._validate_transaction(
            transaction,
            expected_id=transaction["transaction_id"],
        )
        journal_bytes = self._json_bytes(transaction)
        self._require_journal_authority(transaction_path, journal_bytes)
        catalog_position = self._catalog_position(transaction)
        if catalog_position == "divergent":
            self._block_recovery(
                "artifact catalog does not match transaction authority"
            )
        self._require_transaction_ledger(transaction, catalog_position)
        commit = transaction["commit"]
        assert isinstance(commit, dict)
        commit_id = commit["commit_id"]
        assert isinstance(commit_id, str)
        commit_path = f"{_COMMITS_DIRECTORY}/{commit_id}.json"
        commit_bytes = self._json_bytes(commit)
        try:
            current_commit = self._storage.read_bytes_if_exists(commit_path)
        except WorkspaceSecurityError as exc:
            self._block_recovery(
                "immutable artifact commit is unsafe",
                cause=exc,
            )

        targets = transaction["targets"]
        assert isinstance(targets, list)
        if catalog_position == "before":
            self._verify_target_states(targets, allow_old=True)
            for target in targets:
                assert isinstance(target, dict)
                path = target["path"]
                assert isinstance(path, str)
                current = self._storage.read_bytes_if_exists(path)
                current_hash = (
                    None
                    if current is None
                    else hashlib.sha256(current).hexdigest()
                )
                if current_hash == target["new_sha256"]:
                    continue
                self._require_journal_authority(
                    transaction_path,
                    journal_bytes,
                )
                new_content = self._target_content(target)
                self._storage.publish_bytes(
                    path,
                    new_content,
                    expected_sha256=target["old_sha256"],
                    expected_exists=target["old_exists"],
                    create_only=target["create_only"],
                    staging_directory=_STAGING_DIRECTORY,
                )
            self._verify_target_states(targets, allow_old=False)
            self._require_journal_authority(transaction_path, journal_bytes)
            current_commit = self._storage.read_bytes_if_exists(commit_path)
            if current_commit not in {None, commit_bytes}:
                self._block_recovery(
                    "immutable artifact commit conflicts with transaction"
                )
            if current_commit is None:
                self._write_immutable_json(commit_path, commit)
            if self._storage.read_bytes_if_exists(commit_path) != commit_bytes:
                self._block_recovery(
                    "immutable artifact commit diverged during publication"
                )
            self._require_journal_authority(transaction_path, journal_bytes)
            if self._catalog_position(transaction) != "before":
                self._block_recovery(
                    "artifact catalog changed before final publication"
                )
            self._require_transaction_ledger(transaction, "before")
            catalog_before = transaction["catalog_before"]
            assert isinstance(catalog_before, dict)
            catalog_after = transaction["catalog_after"]
            assert isinstance(catalog_after, dict)
            try:
                self._storage.publish_bytes(
                    _CATALOG_PATH,
                    self._json_bytes(catalog_after),
                    expected_sha256=catalog_before["sha256"],
                    expected_exists=catalog_before["exists"],
                    create_only=False,
                    staging_directory=_STAGING_DIRECTORY,
                )
            except ArtifactEntrySecurityError as exc:
                self._block_recovery(
                    "artifact catalog diverged during final publication",
                    cause=exc,
                )
            if self._catalog_position(transaction) != "after":
                self._block_recovery(
                    "artifact catalog publication could not be verified"
                )
            self._require_transaction_ledger(transaction, "after")
        elif catalog_position == "after":
            self._verify_target_states(targets, allow_old=False)
            if current_commit != commit_bytes:
                self._block_recovery(
                    "published artifact commit is missing or divergent"
                )
        self._require_journal_authority(transaction_path, journal_bytes)
        self._verify_target_states(targets, allow_old=False)
        if self._storage.read_bytes_if_exists(commit_path) != commit_bytes:
            self._block_recovery(
                "published artifact commit is missing or divergent"
            )
        catalog_after = transaction["catalog_after"]
        assert isinstance(catalog_after, dict)
        if self._catalog_position(transaction) != "after":
            self._block_recovery("artifact catalog is not durably complete")
        self._require_transaction_ledger(transaction, "after")
        self._require_journal_authority(transaction_path, journal_bytes)
        try:
            self._storage.unlink(
                transaction_path,
                expected_sha256=hashlib.sha256(journal_bytes).hexdigest(),
                retirement_directory=_STAGING_DIRECTORY,
                missing_ok=False,
            )
        except (FileNotFoundError, WorkspaceSecurityError) as exc:
            self._block_recovery(
                "transaction journal could not be strictly retired",
                cause=exc,
            )

    def _require_journal_authority(
        self,
        transaction_path: str,
        expected: bytes,
    ) -> None:
        """Fail closed when the immutable prepared journal is not exact."""

        try:
            current = self._storage.read_bytes(transaction_path)
        except (FileNotFoundError, WorkspaceSecurityError) as exc:
            self._block_recovery(
                "transaction journal authority is missing or unsafe",
                cause=exc,
            )
        if current != expected:
            self._block_recovery("transaction journal authority diverged")

    def _catalog_position(self, transaction: dict[str, object]) -> str:
        """Return whether current catalog bytes exactly match before or after."""

        before = transaction["catalog_before"]
        after = transaction["catalog_after"]
        assert isinstance(before, dict)
        assert isinstance(after, dict)
        try:
            current = self._storage.read_bytes_if_exists(_CATALOG_PATH)
        except WorkspaceSecurityError as exc:
            self._block_recovery(
                "artifact catalog state is unsafe",
                cause=exc,
            )
        if current is None:
            if before["exists"] is False:
                return "before"
            return "divergent"
        if before["exists"] is True and (
            hashlib.sha256(current).hexdigest() == before["sha256"]
        ):
            try:
                current_image = self._decode_json(current)
            except WorkspaceFormatError:
                return "divergent"
            if current_image == before["image"]:
                return "before"
        if current == self._json_bytes(after):
            return "after"
        return "divergent"

    def _verify_target_states(
        self,
        targets: list[object],
        *,
        allow_old: bool,
    ) -> None:
        """Require every target to be exactly old/new or exactly new."""

        for raw_target in targets:
            assert isinstance(raw_target, dict)
            path = raw_target["path"]
            assert isinstance(path, str)
            try:
                current = self._storage.read_bytes_if_exists(path)
            except WorkspaceSecurityError as exc:
                self._block_recovery(
                    "artifact target state is unsafe",
                    cause=exc,
                )
            current_hash = (
                None if current is None else hashlib.sha256(current).hexdigest()
            )
            if current_hash == raw_target["new_sha256"]:
                continue
            if allow_old and (
                (current is not None) == raw_target["old_exists"]
                and current_hash == raw_target["old_sha256"]
            ):
                continue
            self._block_recovery("artifact target diverged from transaction")

    @staticmethod
    def _target_content(target: dict[str, object]) -> bytes:
        """Decode one already-validated transaction target image."""

        try:
            content = base64.b64decode(
                target["new_content_base64"],
                validate=True,
            )
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                "transaction content encoding is invalid"
            ) from exc
        assert isinstance(target["new_sha256"], str)
        if hashlib.sha256(content).hexdigest() != target["new_sha256"]:
            raise WorkspaceFormatError("transaction content hash is invalid")
        return content

    def _validate_transaction(
        self,
        value: object,
        *,
        expected_id: object,
    ) -> None:
        expected_fields = {
            "catalog_before",
            "catalog_after",
            "commit",
            "schema_version",
            "targets",
            "transaction_id",
        }
        if type(value) is not dict or set(value) != expected_fields:
            raise WorkspaceFormatError("transaction fields are invalid")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != _TRANSACTION_SCHEMA_VERSION
        ):
            raise WorkspaceFormatError("transaction schema is unsupported")
        transaction_id = value["transaction_id"]
        if (
            type(transaction_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None
            or transaction_id != expected_id
        ):
            raise WorkspaceFormatError("transaction identity is invalid")
        commit = value["commit"]
        self._validate_commit(commit, transaction_id=transaction_id)
        assert isinstance(commit, dict)
        targets = value["targets"]
        if type(targets) is not list or not targets:
            raise WorkspaceFormatError("transaction targets are invalid")
        target_fields = {
            "author_agent_id",
            "create_only",
            "kind",
            "new_content_base64",
            "new_sha256",
            "old_exists",
            "old_sha256",
            "path",
        }
        observed_paths: set[str] = set()
        for target in targets:
            if type(target) is not dict or set(target) != target_fields:
                raise WorkspaceFormatError("transaction target fields are invalid")
            path = target["path"]
            kind = target["kind"]
            if (
                type(path) is not str
                or type(kind) is not str
                or type(target["author_agent_id"]) is not str
                or type(target["new_content_base64"]) is not str
            ):
                raise WorkspaceFormatError("transaction target types are invalid")
            try:
                parsed_kind = ArtifactKind(kind)
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError("transaction artifact kind is invalid") from exc
            if canonical_artifact_kind(path) is not parsed_kind or path in observed_paths:
                raise WorkspaceFormatError("transaction artifact path is invalid")
            observed_paths.add(path)
            if type(target["create_only"]) is not bool or type(target["old_exists"]) is not bool:
                raise WorkspaceFormatError("transaction boolean field is invalid")
            if parsed_kind is ArtifactKind.MANUSCRIPT and target["create_only"] is not True:
                raise WorkspaceFormatError("manuscript transaction must be create-only")
            if parsed_kind is ArtifactKind.CORE_DOCUMENT and target["create_only"] is not False:
                raise WorkspaceFormatError("core-document transaction must replace")
            expected_author = (
                EXECUTION_SPECIALIST
                if parsed_kind is ArtifactKind.MANUSCRIPT
                and commit["revision_id"] == "r0"
                else LEAD_ORCHESTRATOR
            )
            if target["author_agent_id"] != expected_author:
                raise WorkspaceFormatError("transaction author is invalid")
            if (
                type(target["new_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", target["new_sha256"])
                is None
            ):
                raise WorkspaceFormatError("transaction new hash is invalid")
            old_hash = target["old_sha256"]
            if target["old_exists"]:
                if (
                    type(old_hash) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", old_hash) is None
                ):
                    raise WorkspaceFormatError("transaction old hash is invalid")
            elif old_hash is not None:
                raise WorkspaceFormatError("absent transaction target has an old hash")
            self._target_content(target)

        catalog_before_record = value["catalog_before"]
        if (
            type(catalog_before_record) is not dict
            or set(catalog_before_record) != {"exists", "image", "sha256"}
            or type(catalog_before_record["exists"]) is not bool
        ):
            raise WorkspaceFormatError("transaction catalog-before state is invalid")
        before_exists = catalog_before_record["exists"]
        before_hash = catalog_before_record["sha256"]
        if before_exists:
            if (
                type(before_hash) is not str
                or re.fullmatch(r"[0-9a-f]{64}", before_hash) is None
            ):
                raise WorkspaceFormatError(
                    "transaction catalog-before hash is invalid"
                )
        elif before_hash is not None:
            raise WorkspaceFormatError(
                "absent transaction catalog has a hash"
            )
        try:
            catalog_before = ArtifactCatalog.from_dict(
                catalog_before_record["image"]
            )
            catalog_after = ArtifactCatalog.from_dict(value["catalog_after"])
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("transaction catalog image is invalid") from exc
        if not before_exists and catalog_before.entries:
            raise WorkspaceFormatError(
                "absent transaction catalog must have an empty image"
            )

        target_metadata = {
            target["path"]: (
                target["kind"],
                target["author_agent_id"],
                target["new_sha256"],
            )
            for target in targets
        }
        commit_artifacts = commit["artifacts"]
        assert isinstance(commit_artifacts, list)
        target_paths = [target["path"] for target in targets]
        commit_paths = [artifact["path"] for artifact in commit_artifacts]
        if commit_paths != target_paths:
            raise WorkspaceFormatError(
                "transaction commit order does not exactly match targets"
            )
        manuscript_paths = [
            target["path"]
            for target in targets
            if target["kind"] == ArtifactKind.MANUSCRIPT.value
        ]
        ordered_core_paths = [
            f"core_documents/{filename}"
            for filename in CORE_DOCUMENTS
            if f"core_documents/{filename}" in target_paths
        ]
        if target_paths != [*manuscript_paths, *ordered_core_paths]:
            raise WorkspaceFormatError("transaction target order is invalid")
        commit_metadata = {
            artifact["path"]: (
                artifact["kind"],
                artifact["author_agent_id"],
                artifact["sha256"],
            )
            for artifact in commit_artifacts
        }
        if commit_metadata != target_metadata:
            raise WorkspaceFormatError(
                "transaction commit does not exactly match targets"
            )
        before_paths = set(catalog_before.entries)
        after_paths = set(catalog_after.entries)
        target_path_set = set(target_metadata)
        if after_paths != before_paths.union(target_path_set):
            raise WorkspaceFormatError(
                "transaction catalog membership does not match before and targets"
            )
        if any(path in before_paths for path in manuscript_paths):
            raise WorkspaceFormatError(
                "transaction manuscript already exists in catalog-before"
            )
        for path in before_paths.difference(target_path_set):
            if catalog_before.entries[path] != catalog_after.entries[path]:
                raise WorkspaceFormatError(
                    "transaction changed unrelated catalog metadata"
                )
        catalog_entries = {
            path: entry
            for path, entry in catalog_after.entries.items()
            if entry.commit_id == transaction_id
        }
        if set(catalog_entries) != set(target_metadata):
            raise WorkspaceFormatError(
                "transaction catalog commit set does not match targets"
            )
        for target in targets:
            entry = catalog_after.entries.get(target["path"])
            if (
                entry is None
                or entry.commit_id != transaction_id
                or entry.sha256 != target["new_sha256"]
                or entry.kind.value != target["kind"]
                or entry.author_agent_id != target["author_agent_id"]
                or entry.committed_at != commit["committed_at"]
                or entry.session_id != commit["session_id"]
                or entry.debate_id != commit["debate_id"]
                or entry.revision_id != commit["revision_id"]
            ):
                raise WorkspaceFormatError("transaction catalog does not match targets")

    @staticmethod
    def _validate_commit(value: object, *, transaction_id: str) -> None:
        fields = {
            "artifacts",
            "commit_id",
            "committed_at",
            "debate_id",
            "revision_id",
            "schema_version",
            "session_id",
        }
        if type(value) is not dict or set(value) != fields:
            raise WorkspaceFormatError("artifact commit fields are invalid")
        if (
            type(value["commit_id"]) is not str
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _COMMIT_SCHEMA_VERSION
            or value["commit_id"] != transaction_id
        ):
            raise WorkspaceFormatError("artifact commit identity is invalid")
        for field in ("committed_at", "session_id", "debate_id", "revision_id"):
            if type(value[field]) is not str or not value[field]:
                raise WorkspaceFormatError("artifact commit metadata is invalid")
        try:
            timestamp = datetime.fromisoformat(value["committed_at"])
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("artifact commit timestamp is invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise WorkspaceFormatError("artifact commit timestamp must be offset-aware")
        for field in ("session_id", "debate_id", "revision_id"):
            if _SAFE_IDENTITY.fullmatch(value[field]) is None:
                raise WorkspaceFormatError("artifact commit identity metadata is unsafe")
        artifacts = value["artifacts"]
        if type(artifacts) is not list or not artifacts:
            raise WorkspaceFormatError("artifact commit entries are invalid")
        artifact_fields = {"author_agent_id", "kind", "path", "sha256"}
        paths: set[str] = set()
        manuscript_count = 0
        for artifact in artifacts:
            if type(artifact) is not dict or set(artifact) != artifact_fields:
                raise WorkspaceFormatError("artifact commit entry fields are invalid")
            if (
                type(artifact["kind"]) is not str
                or type(artifact["path"]) is not str
                or type(artifact["author_agent_id"]) is not str
                or type(artifact["sha256"]) is not str
            ):
                raise WorkspaceFormatError("artifact commit entry types are invalid")
            try:
                kind = ArtifactKind(artifact["kind"])
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError("artifact commit kind is invalid") from exc
            path = artifact["path"]
            if canonical_artifact_kind(path) is not kind or path in paths:
                raise WorkspaceFormatError("artifact commit path is invalid")
            paths.add(path)
            expected_author = (
                EXECUTION_SPECIALIST
                if kind is ArtifactKind.MANUSCRIPT
                and value["revision_id"] == "r0"
                else LEAD_ORCHESTRATOR
            )
            if artifact["author_agent_id"] != expected_author:
                raise WorkspaceFormatError("artifact commit author is invalid")
            if (
                type(artifact["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
                is None
            ):
                raise WorkspaceFormatError("artifact commit hash is invalid")
            if kind is ArtifactKind.MANUSCRIPT:
                manuscript_count += 1
        if manuscript_count != 1:
            raise WorkspaceFormatError("artifact commit must contain one manuscript")

    def _load_commit_ledger(
        self,
    ) -> dict[str, tuple[dict[str, object], bytes]]:
        """Load every permanent immutable commit under its exact filename."""

        ledger: dict[str, tuple[dict[str, object], bytes]] = {}
        for name in self._storage.list_names(_COMMITS_DIRECTORY):
            if re.fullmatch(r"[0-9a-f]{32}\.json", name) is None:
                raise WorkspaceFormatError(
                    "artifact commit ledger contains an unexpected entry"
                )
            commit_id = name[:-5]
            raw = self._storage.read_bytes(f"{_COMMITS_DIRECTORY}/{name}")
            parsed = self._decode_json(raw)
            self._validate_commit(parsed, transaction_id=commit_id)
            assert isinstance(parsed, dict)
            if raw != self._json_bytes(parsed):
                raise WorkspaceFormatError(
                    "artifact commit ledger record is not canonical"
                )
            ledger[commit_id] = (parsed, raw)
        return ledger

    @staticmethod
    def _commit_timestamp(commit: Mapping[str, object]) -> datetime:
        """Return one validated commit timestamp normalized to UTC."""

        value = commit["committed_at"]
        assert isinstance(value, str)
        try:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError
            return timestamp.astimezone(UTC)
        except (OverflowError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError(
                "artifact commit timestamp is invalid"
            ) from exc

    @classmethod
    def _next_commit_timestamp(
        cls,
        ledger: Mapping[str, tuple[dict[str, object], bytes]],
    ) -> str:
        """Return a UTC instant strictly newer than the immutable ledger."""

        latest = max(
            (cls._commit_timestamp(commit) for commit, _ in ledger.values()),
            default=None,
        )
        current = datetime.now(UTC)
        if latest is None or current > latest:
            return current.isoformat()
        try:
            return (latest + timedelta(microseconds=1)).isoformat()
        except OverflowError as exc:
            raise WorkspaceFormatError(
                "artifact commit timestamp cannot advance"
            ) from exc

    @staticmethod
    def _ledger_manuscript_paths(
        ledger: Mapping[str, tuple[dict[str, object], bytes]],
    ) -> dict[str, str]:
        """Return the unique permanent manuscript reservation ledger."""

        reservations: dict[str, str] = {}
        for commit_id, (commit, _) in ledger.items():
            artifacts = commit["artifacts"]
            assert isinstance(artifacts, list)
            manuscript = next(
                artifact
                for artifact in artifacts
                if artifact["kind"] == ArtifactKind.MANUSCRIPT.value
            )
            path = manuscript["path"]
            assert isinstance(path, str)
            if path in reservations:
                raise WorkspaceFormatError(
                    "artifact commit ledger reuses a manuscript path"
                )
            reservations[path] = commit_id
        return reservations

    @staticmethod
    def _catalog_entry_matches_commit(
        entry: ArtifactCatalogEntry,
        commit: dict[str, object],
    ) -> bool:
        """Return whether one catalog entry exactly projects one commit artifact."""

        if (
            entry.commit_id != commit["commit_id"]
            or entry.committed_at != commit["committed_at"]
            or entry.session_id != commit["session_id"]
            or entry.debate_id != commit["debate_id"]
            or entry.revision_id != commit["revision_id"]
        ):
            return False
        artifacts = commit["artifacts"]
        assert isinstance(artifacts, list)
        return any(
            artifact["path"] == entry.path
            and artifact["kind"] == entry.kind.value
            and artifact["sha256"] == entry.sha256
            and artifact["author_agent_id"] == entry.author_agent_id
            for artifact in artifacts
        )

    def _audit_catalog(
        self,
        catalog: ArtifactCatalog,
        ledger: Mapping[str, tuple[dict[str, object], bytes]],
        *,
        catalog_present: bool,
        exempt_commit_id: str | None = None,
    ) -> None:
        """Require the catalog to be the exact current view of the commit ledger."""

        if exempt_commit_id is not None and exempt_commit_id not in ledger:
            raise WorkspaceFormatError("pending artifact commit exemption is absent")
        self._ledger_manuscript_paths(ledger)
        expected_paths: set[str] = set()
        latest_core: dict[str, tuple[datetime, set[str]]] = {}
        for commit_id, (commit, _) in ledger.items():
            if commit_id == exempt_commit_id:
                continue
            timestamp = self._commit_timestamp(commit)
            artifacts = commit["artifacts"]
            assert isinstance(artifacts, list)
            expected_paths.update(artifact["path"] for artifact in artifacts)
            for artifact in artifacts:
                if artifact["kind"] != ArtifactKind.CORE_DOCUMENT.value:
                    continue
                path = artifact["path"]
                assert isinstance(path, str)
                selected = latest_core.get(path)
                if selected is None or timestamp > selected[0]:
                    latest_core[path] = (timestamp, {commit_id})
                elif timestamp == selected[0]:
                    selected[1].add(commit_id)
        if exempt_commit_id is not None:
            if any(
                entry.commit_id == exempt_commit_id
                for entry in catalog.entries.values()
            ):
                raise WorkspaceFormatError(
                    "catalog contains pending artifact commit metadata"
                )
        nonexempt_commits = set(ledger)
        if exempt_commit_id is not None:
            nonexempt_commits.remove(exempt_commit_id)
        if not catalog_present and nonexempt_commits:
            raise WorkspaceFormatError(
                "artifact catalog is missing for the permanent commit ledger"
            )
        if set(catalog.entries) != expected_paths:
            raise WorkspaceFormatError(
                "artifact catalog path set does not match commit ledger"
            )
        for entry in catalog.entries.values():
            record = ledger.get(entry.commit_id)
            if record is None or not self._catalog_entry_matches_commit(
                entry,
                record[0],
            ):
                raise WorkspaceFormatError(
                    "artifact catalog entry does not match its immutable commit"
                )
        for path, (_, commit_ids) in latest_core.items():
            if len(commit_ids) != 1:
                raise WorkspaceFormatError(
                    "artifact core history has no unique newest commit"
                )
            entry = catalog.entries[path]
            if entry.commit_id != next(iter(commit_ids)):
                raise WorkspaceFormatError(
                    "artifact catalog core entry is not the newest commit"
                )

    def _audit_transaction_ledger(
        self,
        transaction: dict[str, object],
        catalog_position: str,
    ) -> None:
        """Audit actual and projected catalogs around one active transaction."""

        before_record = transaction["catalog_before"]
        assert isinstance(before_record, dict)
        before = ArtifactCatalog.from_dict(before_record["image"])
        after = ArtifactCatalog.from_dict(transaction["catalog_after"])
        commit = transaction["commit"]
        assert isinstance(commit, dict)
        commit_id = commit["commit_id"]
        assert isinstance(commit_id, str)
        commit_bytes = self._json_bytes(commit)
        ledger = self._load_commit_ledger()
        on_disk = ledger.get(commit_id)

        if catalog_position == "before":
            exempt_commit_id: str | None = None
            if on_disk is not None:
                if on_disk[1] != commit_bytes:
                    raise WorkspaceFormatError(
                        "pending artifact commit differs from journal authority"
                    )
                exempt_commit_id = commit_id
            self._audit_catalog(
                before,
                ledger,
                catalog_present=before_record["exists"],
                exempt_commit_id=exempt_commit_id,
            )
            virtual_ledger = dict(ledger)
            virtual_ledger[commit_id] = (commit, commit_bytes)
            self._audit_catalog(
                after,
                virtual_ledger,
                catalog_present=True,
            )
            return

        if catalog_position != "after":
            raise WorkspaceFormatError("artifact catalog position is invalid")
        if on_disk is None or on_disk[1] != commit_bytes:
            raise WorkspaceFormatError(
                "completed artifact catalog lacks its exact immutable commit"
            )
        self._audit_catalog(after, ledger, catalog_present=True)

    def _require_transaction_ledger(
        self,
        transaction: dict[str, object],
        catalog_position: str,
    ) -> None:
        """Create a durable recovery block when transaction ledger audit fails."""

        try:
            self._audit_transaction_ledger(transaction, catalog_position)
        except (WorkspaceFormatError, WorkspaceSecurityError) as exc:
            self._block_recovery(
                "artifact commit ledger does not match transaction",
                cause=exc,
            )

    def _load_catalog_audited(
        self,
    ) -> tuple[
        ArtifactCatalog,
        bytes | None,
        dict[str, tuple[dict[str, object], bytes]],
    ]:
        """Return one globally audited catalog and permanent commit ledger."""

        catalog, raw = self._load_catalog_image()
        ledger = self._load_commit_ledger()
        self._audit_catalog(
            catalog,
            ledger,
            catalog_present=raw is not None,
        )
        return catalog, raw, ledger

    def _load_catalog(self) -> ArtifactCatalog:
        catalog, _, _ = self._load_catalog_audited()
        return catalog

    def _load_catalog_image(self) -> tuple[ArtifactCatalog, bytes | None]:
        """Return the exact authoritative catalog image and its stored bytes."""

        raw = self._storage.read_bytes_if_exists(_CATALOG_PATH)
        if raw is None:
            return ArtifactCatalog.empty(), None
        try:
            return ArtifactCatalog.from_dict(self._decode_json(raw)), raw
        except (WorkspaceFormatError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError("artifact catalog is invalid") from exc

    def _read_json(self, relative: str) -> object:
        return self._decode_json(self._storage.read_bytes(relative))

    @staticmethod
    def _decode_json(raw: bytes) -> object:
        """Decode strict UTF-8 JSON while rejecting duplicate keys recursively."""

        def reject_duplicates(pairs):
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise WorkspaceFormatError(
                        "artifact repository JSON contains duplicate keys"
                    )
                value[key] = item
            return value

        try:
            text = raw.decode("utf-8", errors="strict")
            return json.loads(text, object_pairs_hook=reject_duplicates)
        except WorkspaceFormatError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceFormatError(
                "artifact repository JSON is invalid"
            ) from exc

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        try:
            return (
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise WorkspaceFormatError("artifact repository value is not JSON-safe") from exc

    def _write_new_json(self, relative: str, value: object) -> None:
        self._storage.publish_bytes(
            relative,
            self._json_bytes(value),
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _write_immutable_json(self, relative: str, value: object) -> None:
        desired = self._json_bytes(value)
        current = self._storage.read_bytes_if_exists(relative)
        if current == desired:
            return
        if current is not None:
            raise WorkspaceFormatError(
                "immutable artifact repository record conflicts with recovery"
            )
        self._storage.publish_bytes(
            relative,
            desired,
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _write_json_exact(self, relative: str, value: object) -> None:
        desired = self._json_bytes(value)
        current = self._storage.read_bytes_if_exists(relative)
        if current == desired:
            return
        self._storage.publish_bytes(
            relative,
            desired,
            expected_sha256=(
                None if current is None else hashlib.sha256(current).hexdigest()
            ),
            expected_exists=current is not None,
            create_only=False,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _block_recovery(self, reason: str, *, cause: Exception | None = None) -> None:
        marker = {
            "reason": reason,
            "schema_version": _RECOVERY_SCHEMA_VERSION,
        }
        try:
            self._write_json_exact(_RECOVERY_BLOCK_PATH, marker)
        except Exception as marker_error:
            error = WorkspaceFormatError("writing-artifact recovery failed closed")
            error.add_note(f"recovery marker failed: {type(marker_error).__name__}")
            if cause is not None:
                raise error from cause
            raise error from marker_error
        error = WorkspaceFormatError("writing-artifact recovery is blocked")
        if cause is not None:
            raise error from cause
        raise error


class _RepositoryReferenceReadInvocation:
    """Repository-local owner of one capability and its transient result."""

    __slots__ = (
        "_capability",
        "_lock",
        "_repository",
        "_request",
        "_result",
        "_state",
    )

    def __init__(
        self,
        repository: WritingArtifactRepository,
        request: ReferenceReadRequest,
        capability: ReadCapability,
    ) -> None:
        self._repository = repository
        self._request = request
        self._capability = capability
        self._lock = threading.Lock()
        self._result: ReferenceReadResult | None = None
        self._state = "authorized"

    @property
    def request(self) -> ReferenceReadRequest:
        return self._request

    def handle_reference_read_action(
        self,
        action: ReferenceReadAction,
    ) -> ReferenceReadResult:
        """Deliver bytes only for the exact first complete provider action."""

        with self._lock:
            if self._state != "authorized":
                self._terminate_without_replacing(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
            expected = ReferenceReadAction(
                ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
                self.request.correlation,
                self.request.references,
            )
            if type(action) is not ReferenceReadAction or action != expected:
                self._terminate_without_replacing(
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
                )
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH
                )
            try:
                delivery = self._repository.deliver_authorized_references(
                    self._capability,
                    action.references,
                )
            except BaseException:
                self._state = "terminated"
                self._close_result()
                raise
            try:
                try:
                    result = ReferenceReadResult(
                        self.request,
                        delivery.contents,
                    )
                finally:
                    delivery.close()
            except BaseException:
                self._terminate_without_replacing(
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                )
                raise
            self._result = result
            self._state = "delivered"
            return result

    def record_result_received(self) -> None:
        """Close selected bytes and persist the provider-result transition."""

        with self._lock:
            if self._state != "delivered":
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
            self._close_result()
            self._repository.record_reference_result_received(self._capability)
            self._state = "result_received"

    def accept(self) -> None:
        """Revalidate and accept this invocation exactly once."""

        with self._lock:
            if self._state != "result_received":
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
            self._close_result()
            try:
                self._repository.accept_reference_invocation(self._capability)
            except BaseException:
                self._state = "terminated"
                raise
            self._state = "accepted"

    def terminate(self, outcome: ReferenceAuthorizationErrorCode) -> None:
        """Close selected bytes and durably retire this invocation."""

        if type(outcome) is not ReferenceAuthorizationErrorCode:
            raise TypeError("outcome must be a ReferenceAuthorizationErrorCode")
        with self._lock:
            if self._state in {"accepted", "terminated"}:
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.INVALID_TRANSITION
                )
            self._close_result()
            self._repository.terminate_reference_invocation(
                self._capability,
                outcome,
            )
            self._state = "terminated"

    def _close_result(self) -> None:
        if self._result is not None:
            self._result.close()
            self._result = None

    def _terminate_without_replacing(
        self,
        outcome: ReferenceAuthorizationErrorCode,
    ) -> None:
        """Best-effort retire while preserving the caller's primary failure."""

        if self._state in {"accepted", "terminated"}:
            self._close_result()
            return
        self._close_result()
        try:
            self._repository.terminate_reference_invocation(
                self._capability,
                outcome,
            )
        except BaseException:
            pass
        self._state = "terminated"


class _RepositoryLock:
    """Acquire the process and workspace repository locks in fixed order."""

    def __init__(self, repository: WritingArtifactRepository) -> None:
        self._repository = repository
        self._file_lock = None

    def __enter__(self) -> None:
        self._repository._lock.acquire()
        try:
            self._repository._ensure_state_directories()
            self._file_lock = self._repository._storage.exclusive_lock(_LOCK_PATH)
            self._file_lock.__enter__()
            self._repository._recover()
        except BaseException:
            try:
                if self._file_lock is not None:
                    self._file_lock.__exit__(None, None, None)
            finally:
                self._repository._lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._file_lock is not None:
                self._file_lock.__exit__(exc_type, exc, traceback)
        finally:
            self._repository._lock.release()
