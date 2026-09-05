"""Filesystem repository for agent-authored canonical writing artifacts."""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Mapping, NoReturn
from uuid import uuid4

from ..agents import CORE_DOCUMENTS, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from ..errors import (
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ._reference_evidence import ReferenceEvidenceStore
from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from ._application_store import (
    ARTIFACT_LOCK_PATH,
    ARTIFACT_SNAPSHOTS_DIRECTORY,
    ARTIFACT_STAGING_DIRECTORY,
    ArtifactApplicationStore,
    ArtifactApplicationStoreFailure,
    ArtifactApplicationStoreFailureCode,
)
from .application_contracts import (
    ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationRequest,
    ArtifactApplicationResult,
)
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
    manuscript_artifact_path,
)
from .filesystem_sources import (
    ArtifactSourceInvalidUtf8Error,
    ArtifactSourceNulError,
    CanonicalArtifactFilesystem,
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


_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_STORE_FAILURE_CODES = {
    ArtifactApplicationStoreFailureCode.CORRUPTION: (
        ArtifactApplicationErrorCode.CORRUPTION
    ),
    ArtifactApplicationStoreFailureCode.RECOVERY_REQUIRED: (
        ArtifactApplicationErrorCode.RECOVERY_REQUIRED
    ),
}


def _raise_application_failure(code: ArtifactApplicationErrorCode) -> NoReturn:
    try:
        raise ArtifactApplicationError(code) from None
    except ArtifactApplicationError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise


class WritingArtifactRepository:
    """Own canonical artifact sources, provenance, publication, and recovery.

    Construction performs no filesystem I/O. Authoritative source reads and
    publication share the repository-local lock and workspace lease, recovering
    any prepared artifact application before observing canonical files.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._storage = SecureWorkspaceStorage(workspace_root)
        self._applications = ArtifactApplicationStore(self._storage)
        self._sources = CanonicalArtifactFilesystem(self._storage)
        self._lock = threading.RLock()
        self._reference_authorization_owner = object()
        self._reference_evidence = ReferenceEvidenceStore(
            self._storage,
            ARTIFACT_SNAPSHOTS_DIRECTORY,
            ARTIFACT_STAGING_DIRECTORY,
        )

    def catalog(self) -> ArtifactCatalog:
        """Return only current, provenance-backed canonical entries."""

        with self._locked_repository():
            catalog = self._applications.catalog()
            current: dict[str, ArtifactCatalogEntry] = {}
            for path, entry in catalog.entries.items():
                try:
                    content = self._storage.read_bytes(path)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                if hashlib.sha256(content).hexdigest() == entry.sha256:
                    current[path] = entry
            return ArtifactCatalog(current)

    def apply_artifact_changes(
        self,
        request: ArtifactApplicationRequest,
    ) -> ArtifactApplicationResult:
        """Apply one exact immutable request under the repository lease."""

        if type(request) is not ArtifactApplicationRequest:
            _raise_application_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        try:
            request.__post_init__()
        except ArtifactApplicationError as exc:
            _raise_application_failure(exc.code)
        except Exception:
            _raise_application_failure(ArtifactApplicationErrorCode.INVALID_SCHEMA)
        try:
            with self._locked_repository():
                return self._applications.apply(request)
        except ArtifactApplicationStoreFailure as exc:
            _raise_application_failure(_STORE_FAILURE_CODES[exc.code])
        except (WorkspaceFormatError, WorkspaceSecurityError):
            _raise_application_failure(ArtifactApplicationErrorCode.CORRUPTION)

    def inspect_artifact_application(
        self,
        application_id: str,
    ) -> ArtifactApplicationResult | None:
        """Return one exact immutable historical application result if present."""

        if (
            type(application_id) is not str
            or _SAFE_IDENTITY.fullmatch(application_id) is None
        ):
            _raise_application_failure(ArtifactApplicationErrorCode.INVALID_IDENTIFIER)
        try:
            with self._locked_repository():
                return self._applications.inspect_result(application_id)
        except ArtifactApplicationStoreFailure as exc:
            _raise_application_failure(_STORE_FAILURE_CODES[exc.code])
        except (WorkspaceFormatError, WorkspaceSecurityError):
            _raise_application_failure(ArtifactApplicationErrorCode.CORRUPTION)

    def list_paths(self) -> tuple[str, ...]:
        """Return every safe current canonical filesystem identity."""

        with self._locked_sources():
            return self._sources.list_paths()

    def list_paths_bounded(
        self,
        *,
        max_visited_entries: int,
        max_eligible_sources: int,
    ) -> tuple[str, ...]:
        """Return one complete bounded canonical source inventory."""

        with self._locked_sources():
            return self._sources.list_paths_bounded(
                max_visited_entries=max_visited_entries,
                max_eligible_sources=max_eligible_sources,
            )

    def read_current(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        """Return immutable current bytes for only the requested artifacts."""

        with self._locked_sources():
            return self._sources.read_current(paths)

    def capture_complete_state(self) -> tuple[ArtifactContentSnapshot, ...]:
        """Capture one complete bounded canonical state under the source lease."""

        with self._locked_sources():
            paths = self._sources.list_paths()
            snapshots = self._sources.read_current(paths)
            if tuple(item.path for item in snapshots) != paths:
                raise WorkspaceFormatError(
                    "complete writing-artifact state changed during capture"
                )
            if self._sources.list_paths() != paths or any(
                not self._sources.matches(item.source) for item in snapshots
            ):
                raise WorkspaceFormatError(
                    "complete writing-artifact state changed during capture"
                )
            return snapshots

    def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read one safe current canonical filesystem source."""

        if max_bytes is not None and (
            type(max_bytes) is not int or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be None or a positive integer")
        with self._locked_sources():
            return self._sources.read_text(path, max_bytes=max_bytes)

    def capture_reference_authorization(
        self,
        *,
        operation_id: str,
        batch: ArtifactReferenceBatch,
    ) -> ReferenceAuthorization | None:
        """Capture one immutable filesystem-authorized reference snapshot."""

        if type(batch) is not ArtifactReferenceBatch:
            raise TypeError("batch must be an ArtifactReferenceBatch")
        if not batch.references:
            return None
        manifest = ReferenceOperationManifest(operation_id, batch.references)
        began = False
        with self._locked_sources(evidence=True):
            try:
                self._reference_evidence.begin_operation(manifest)
                began = True
                sources: dict[str, ArtifactContentSnapshot] = {}
                for reference in batch.references:
                    if reference.path in sources:
                        continue
                    try:
                        sources[reference.path] = self._sources.read_snapshot(
                            reference.path
                        )
                    except FileNotFoundError:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.NOT_FOUND
                        ) from None
                    except (ArtifactEntrySecurityError, WorkspaceSecurityError):
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.INELIGIBLE
                        ) from None
                    except ArtifactSourceInvalidUtf8Error:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.INVALID_UTF8
                        ) from None
                    except ArtifactSourceNulError:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.NUL_CONTENT
                        ) from None
                    except WorkspaceFormatError:
                        raise ReferenceAuthorizationError(
                            ReferenceAuthorizationErrorCode.READ_LIMIT_EXCEEDED
                        ) from None
                snapshots = tuple(sources[item.path] for item in batch.references)
                proofs, captured = build_authorized_references(
                    batch,
                    snapshots,
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
        with self._locked_sources(evidence=True):
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
            with self._locked_sources(evidence=True):
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
        with self._locked_sources(evidence=True):
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
            with self._locked_sources(evidence=True):
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
        with self._locked_sources(evidence=True):
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
            with self._locked_sources(evidence=True):
                self._revalidate_reference_authorization(authorization)
                self._reference_evidence.append_operation_event(
                    authorization.manifest.operation_id,
                    ReferenceOperationState.REVALIDATED,
                    references,
                    checkpoint=checkpoint,
                )
        except ReferenceAuthorizationError as exc:
            with self._locked_sources(evidence=True):
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
            with self._locked_sources(evidence=True):
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
                with self._locked_sources(evidence=True):
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

        with self._locked_sources(evidence=True):
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
        with self._locked_sources(evidence=True):
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
        """Require exact current source identity for every captured path."""

        authorization.require_owner(self._reference_authorization_owner)
        references = authorization.authorized_references
        observed: set[str] = set()
        for authorized in references:
            path = authorized.reference.path
            if path in observed:
                continue
            observed.add(path)
            try:
                matches = self._sources.matches(authorized.source)
            except (OSError, WorkspaceFormatError, WorkspaceSecurityError):
                raise ReferenceAuthorizationError(
                    ReferenceAuthorizationErrorCode.DRIFT
                ) from None
            if not matches:
                raise ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.DRIFT)

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
            if self._storage.read_bytes_if_exists(manuscript_path) is not None:
                raise ManuscriptExistsError(f"refusing to overwrite {manuscript_path}")
            revision_match = re.fullmatch(r"r([0-9]+)", revision_id)
            if revision_match is None:
                raise WorkspaceSecurityError("revision ID is unsafe")
            ordered_updates = tuple(
                filename for filename in CORE_DOCUMENTS if filename in updates
            )
            self._applications.apply_consensus(
                manuscript_path=manuscript_path,
                candidate=candidate_bytes,
                core_updates={
                    f"core_documents/{filename}": encoded_updates[filename]
                    for filename in ordered_updates
                },
                debate_id=debate_id,
                revision_number=int(revision_match.group(1)) + 1,
            )
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

    def _locked_sources(self, *, evidence: bool = False):
        return _RepositorySourceLock(self, evidence=evidence)

    def _ensure_source_directories(self, *, evidence: bool) -> None:
        self._applications.ensure_directories()

    def _ensure_state_directories(self) -> None:
        self._applications.ensure_directories()


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
            except ReferenceAuthorizationError:
                self._state = "terminated"
                self._close_result()
                raise
            except BaseException:
                self._terminate_without_replacing(
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                )
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
            except ReferenceAuthorizationError:
                self._state = "terminated"
                raise
            except BaseException:
                self._terminate_without_replacing(
                    ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
                )
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
            self._file_lock = self._repository._storage.exclusive_lock(
                ARTIFACT_LOCK_PATH
            )
            self._file_lock.__enter__()
            self._repository._applications.recover()
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


class _RepositorySourceLock:
    """Serialize authoritative source/evidence I/O after recovery."""

    def __init__(
        self,
        repository: WritingArtifactRepository,
        *,
        evidence: bool,
    ) -> None:
        self._repository = repository
        self._evidence = evidence
        self._file_lock = None

    def __enter__(self) -> None:
        self._repository._lock.acquire()
        try:
            self._repository._ensure_source_directories(evidence=self._evidence)
            self._file_lock = self._repository._storage.exclusive_lock(
                ARTIFACT_LOCK_PATH
            )
            self._file_lock.__enter__()
            self._repository._applications.recover()
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
