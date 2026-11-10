"""Writing artifact."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from ..actions import ContextSourceBoundary, ContextSourceStatusKind, ContextSourceStatusReceipt
from ..manifest import ContextAuthorityKind, ContextProvenance, ContextScope, ContextSourceKind
from .capture_values import (
    ContextSourceDescriptor,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceSnapshot,
    artifact_locator_is_lexically_safe,
    require_source_policy,
    source_descriptor,
    source_error,
    source_identity_digest,
    source_record,
    source_safe_digest,
    writing_artifact_source_id,
)
from ...errors import WorkspaceFormatError, WorkspaceSecurityError
from ...writing_artifacts.catalog import CORE_ARTIFACT_PATHS
from ...writing_artifacts.filesystem_sources import (
    ArtifactSourceInvalidUtf8Error,
    ArtifactSourceInventoryLimitError,
    ArtifactSourceNulError,
)
from ...writing_artifacts.references import (
    ArtifactContentSnapshot,
    MAX_VIEWER_UTF8_BYTES,
    reference_source_kind,
)
from ...writing_artifacts.repository import WritingArtifactRepository


def _physical_lines(text: str) -> tuple[str, ...]:
    """Split only CRLF, LF, and lone CR while preserving exact bytes."""

    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            index += 1
            if index < len(text) and text[index] == "\n":
                index += 1
            lines.append(text[start:index])
            start = index
            continue
        if character == "\n":
            index += 1
            lines.append(text[start:index])
            start = index
            continue
        index += 1
    if start < len(text):
        lines.append(text[start:])
    return tuple(lines)

class WritingArtifactSourceAdapter:
    """Capture canonical writing artifacts through their repository owner."""

    source_kind = ContextSourceKind.WRITING_ARTIFACT

    def __init__(
        self,
        repository: WritingArtifactRepository,
        scope: ContextScope,
        allowed_paths: frozenset[str] | None = None,
    ) -> None:
        if not isinstance(repository, WritingArtifactRepository):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if type(scope) is not ContextScope:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if allowed_paths is not None:
            if type(allowed_paths) is not frozenset:
                raise source_error(ContextSourceErrorCode.INVALID_VALUE)
            try:
                validated_paths = frozenset(
                    self._validate_locator(path) for path in allowed_paths
                )
            except ContextSourceError:
                raise
            if validated_paths != allowed_paths:
                raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._repository = repository
        self._repository_access: (
            Callable[[], AbstractContextManager[WritingArtifactRepository]] | None
        ) = None
        self.scope = scope
        self._allowed_paths = allowed_paths

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        with self._open_repository_access():
            return self._inventory(policy)

    def _inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = require_source_policy(policy)
        for attempt in range(2):
            paths = self._list_paths(policy)
            descriptors: list[ContextSourceDescriptor] = []
            inventory_bytes = 0
            retry = False
            offset = 0
            while offset < len(paths):
                remaining_bytes = policy.max_inventory_body_utf8_bytes - inventory_bytes
                width = min(4, max(1, remaining_bytes // MAX_VIEWER_UTF8_BYTES))
                group = paths[offset:offset + width]
                try:
                    snapshots = self._capture_inventory_group(
                        group, policy, inventory_bytes
                    )
                except ContextSourceError as error:
                    if attempt == 0 and error.code in {
                        ContextSourceErrorCode.SOURCE_DRIFT,
                        ContextSourceErrorCode.SOURCE_MISSING,
                    }:
                        retry = True
                        break
                    raise
                group_descriptors = tuple(snapshot.descriptor for snapshot in snapshots)
                del snapshots
                for descriptor in group_descriptors:
                    inventory_bytes += descriptor.utf8_bytes
                    if inventory_bytes > policy.max_inventory_body_utf8_bytes:
                        raise source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
                    descriptors.append(descriptor)
                offset += len(group)
            if retry:
                continue
            if paths == self._list_paths(policy):
                return tuple(descriptors)
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    def _capture_inventory_group(
        self,
        paths: tuple[str, ...],
        policy: ContextSourcePolicy,
        inventory_bytes: int,
    ) -> tuple[ContextSourceSnapshot, ...]:
        """Compare one bounded group, retaining ordered inventory failure semantics."""

        capture_error = ContextSourceErrorCode.SOURCE_DRIFT
        try:
            for locator in paths:
                self._validate_locator(locator)
                if self._allowed_paths is not None and locator not in self._allowed_paths:
                    raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
            for _attempt in range(2):
                first = self._capture_source_group_once(paths, policy)
                second = self._capture_source_group_once(paths, policy)
                if first == second:
                    return second
                del first, second
        except ContextSourceError as error:
            capture_error = error.code

        # An earlier source or aggregate limit must win over a later batch error.
        # Diagnostics never turn the failed group into an accepted inventory.
        for locator in paths:
            descriptor = self._capture(locator, policy).descriptor
            inventory_bytes += descriptor.utf8_bytes
            if inventory_bytes > policy.max_inventory_body_utf8_bytes:
                raise source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        raise source_error(capture_error)

    def canonical_core_statuses(
        self,
        manifest_digest: str,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceStatusReceipt, ...]:
        """Capture the four canonical core statuses under one stable inventory."""

        with self._open_repository_access():
            return self._canonical_core_statuses(manifest_digest, policy)

    def _canonical_core_statuses(
        self,
        manifest_digest: str,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceStatusReceipt, ...]:
        """Implement one already-guarded canonical core-status capture."""

        policy = require_source_policy(policy)
        source_safe_digest(manifest_digest)
        if self._allowed_paths is not None:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)

        def capture_once(
            paths: tuple[str, ...],
        ) -> tuple[ContextSourceStatusReceipt, ...]:
            present_paths = frozenset(paths)
            snapshots = self._capture_core_sources(present_paths, policy)
            self._require_absent_cores(
                tuple(path for path in CORE_ARTIFACT_PATHS if path not in present_paths),
                policy,
            )
            receipts: list[ContextSourceStatusReceipt] = []
            for ordinal, locator in enumerate(CORE_ARTIFACT_PATHS, start=1):
                if locator not in present_paths:
                    receipts.append(
                        ContextSourceStatusReceipt(
                            evidence_id=f"status_{ordinal}",
                            source_id=writing_artifact_source_id(locator),
                            locator=locator,
                            status=ContextSourceStatusKind.ABSENT,
                            ordinal=ordinal,
                            manifest_digest=manifest_digest,
                            revision=None,
                            source_digest=None,
                            source_utf8_bytes=0,
                        )
                    )
                    continue
                snapshot = snapshots[locator]
                descriptor = snapshot.descriptor
                receipts.append(
                    ContextSourceStatusReceipt(
                        evidence_id=f"status_{ordinal}",
                        source_id=descriptor.source_id,
                        locator=locator,
                        status=(
                            ContextSourceStatusKind.EMPTY
                            if descriptor.utf8_bytes == 0
                            else ContextSourceStatusKind.PRESENT
                        ),
                        ordinal=ordinal,
                        manifest_digest=manifest_digest,
                        revision=descriptor.revision,
                        source_digest=descriptor.content_digest,
                        source_utf8_bytes=descriptor.utf8_bytes,
                    )
                )
            return tuple(receipts)

        for _attempt in range(2):
            first_paths = self._list_paths(policy)
            try:
                first = capture_once(first_paths)
                second_paths = self._list_paths(policy)
                second = capture_once(second_paths)
                final_paths = self._list_paths(policy)
            except ContextSourceError as error:
                if error.code in {
                    ContextSourceErrorCode.SOURCE_DRIFT,
                    ContextSourceErrorCode.SOURCE_MISSING,
                }:
                    continue
                raise
            if first_paths == second_paths == final_paths and first == second:
                return second
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    def _capture_core_sources(
        self,
        present_paths: frozenset[str],
        policy: ContextSourcePolicy,
    ) -> dict[str, ContextSourceSnapshot]:
        """Double-capture only the four fixed core sources through one owner batch."""

        core_paths = tuple(path for path in CORE_ARTIFACT_PATHS if path in present_paths)
        if not core_paths:
            return {}
        capture_error = ContextSourceErrorCode.SOURCE_DRIFT
        try:
            for _attempt in range(2):
                first = self._capture_source_group_once(core_paths, policy)
                second = self._capture_source_group_once(core_paths, policy)
                if first == second:
                    return dict(zip(core_paths, second))
                del first, second
        except ContextSourceError as error:
            capture_error = error.code

        # Diagnose in the original core order, including absence guards. A failed
        # batch is never accepted even if these fresh individual captures succeed.
        for locator in CORE_ARTIFACT_PATHS:
            if locator in present_paths:
                self._capture(locator, policy)
            else:
                self._require_absent_core(locator, policy)
        raise source_error(capture_error)

    def _capture_source_group_once(
        self,
        paths: tuple[str, ...],
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceSnapshot, ...]:
        """Convert at most four exact ordered source snapshots without new authority."""

        if not paths or len(paths) > 4 or len(set(paths)) != len(paths):
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        snapshots = self._read_artifact_snapshots(paths)
        observed_paths = tuple(snapshot.path for snapshot in snapshots)
        if observed_paths != paths:
            if observed_paths == tuple(
                path for path in paths if path in observed_paths
            ):
                raise source_error(ContextSourceErrorCode.SOURCE_MISSING)
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return tuple(
            self._snapshot_from_capture(locator, policy, snapshot)
            for locator, snapshot in zip(paths, snapshots)
        )

    def _require_absent_cores(
        self,
        locators: tuple[str, ...],
        policy: ContextSourcePolicy,
    ) -> None:
        """Prove a fixed core subset absent through one guarded owner operation."""
        if not locators:
            return
        failure: ContextSourceErrorCode | None = None
        absent = False
        try:
            absent = self._repository.are_sources_absent(
                locators, max_bytes=policy.max_source_utf8_bytes
            )
        except ArtifactSourceInvalidUtf8Error:
            failure = ContextSourceErrorCode.SOURCE_INVALID_UTF8
        except ArtifactSourceNulError:
            failure = ContextSourceErrorCode.SOURCE_NUL
        except WorkspaceSecurityError:
            failure = ContextSourceErrorCode.SOURCE_UNSAFE
        except WorkspaceFormatError:
            failure = ContextSourceErrorCode.SOURCE_UNAVAILABLE
        if failure is not None:
            raise source_error(failure)
        if absent is not True:
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    def _require_absent_core(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> None:
        """Distinguish an exact absence from an unsafe or corrupt core entry."""

        try:
            self._repository.read_text(
                locator,
                max_bytes=policy.max_source_utf8_bytes,
            )
        except FileNotFoundError:
            return
        except ArtifactSourceInvalidUtf8Error:
            raise source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8) from None
        except ArtifactSourceNulError:
            raise source_error(ContextSourceErrorCode.SOURCE_NUL) from None
        except WorkspaceSecurityError:
            raise source_error(ContextSourceErrorCode.SOURCE_UNSAFE) from None
        except WorkspaceFormatError:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE) from None
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    def _list_paths(self, policy: ContextSourcePolicy) -> tuple[str, ...]:
        paths: tuple[str, ...] | None = None
        try:
            paths = self._repository.list_paths_bounded(
                max_visited_entries=policy.max_traversal_entries,
                max_eligible_sources=policy.max_inventory_sources,
            )
        except ArtifactSourceInventoryLimitError:
            pass
        if paths is None:
            raise source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if self._allowed_paths is None:
            return paths
        missing = self._allowed_paths.difference(paths)
        if missing:
            raise source_error(ContextSourceErrorCode.SOURCE_MISSING)
        return tuple(path for path in paths if path in self._allowed_paths)

    @staticmethod
    def _validate_locator(locator: object) -> str:
        if not artifact_locator_is_lexically_safe(locator):
            raise source_error(ContextSourceErrorCode.SOURCE_UNSAFE)
        assert isinstance(locator, str)
        eligible = False
        try:
            eligible = reference_source_kind(locator) is not None
        except (TypeError, ValueError):
            pass
        if not eligible:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        return locator

    def _read_artifact_snapshots(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        snapshots: tuple[ArtifactContentSnapshot, ...] | None = None
        capture_error: ContextSourceErrorCode | None = None
        try:
            snapshots = self._repository.read_current(paths)
        except FileNotFoundError:
            capture_error = ContextSourceErrorCode.SOURCE_MISSING
        except ArtifactSourceInvalidUtf8Error:
            capture_error = ContextSourceErrorCode.SOURCE_INVALID_UTF8
        except ArtifactSourceNulError:
            capture_error = ContextSourceErrorCode.SOURCE_NUL
        except WorkspaceSecurityError:
            capture_error = ContextSourceErrorCode.SOURCE_UNSAFE
        except WorkspaceFormatError:
            capture_error = ContextSourceErrorCode.SOURCE_UNAVAILABLE
        if capture_error is not None:
            raise source_error(capture_error)
        if snapshots is None:
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return snapshots

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        snapshots = self._read_artifact_snapshots((locator,))
        if snapshots == ():
            raise source_error(ContextSourceErrorCode.SOURCE_MISSING)
        if len(snapshots) != 1:
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return self._snapshot_from_capture(locator, policy, snapshots[0])

    def _snapshot_from_capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
        captured: ArtifactContentSnapshot,
    ) -> ContextSourceSnapshot:
        if captured.path != locator:
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        if len(captured.content) > policy.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        text: str | None = None
        try:
            text = captured.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            pass
        if text is None:
            raise source_error(ContextSourceErrorCode.SOURCE_INVALID_UTF8)
        if "\x00" in text:
            raise source_error(ContextSourceErrorCode.SOURCE_NUL)
        lines = _physical_lines(text)
        if len(lines) > policy.max_records_per_source:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        records = tuple(
            source_record(
                f"line_{index}",
                ContextSourceBoundary.artifact_lines(index, index),
                line,
                line,
            )
            for index, line in enumerate(lines, start=1)
        )
        source = captured.source
        provenance = ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            locator,
        )
        revision = source_identity_digest(
            {
                "changed_ns": source.changed_ns,
                "device_id": source.device_id,
                "digest": source.source_sha256,
                "inode": source.inode,
                "kind": source.kind.value,
                "modified_ns": source.modified_ns,
                "path": source.path,
                "utf8_bytes": source.source_utf8_bytes,
            }
        )
        descriptor = source_descriptor(
            scope=self.scope,
            source_id=writing_artifact_source_id(locator),
            source_kind=self.source_kind,
            revision=revision,
            provenance=provenance,
            content=captured.content,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, records, captured.content)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        with self._open_repository_access():
            return self._capture(locator, policy)

    def _capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = require_source_policy(policy)
        locator = self._validate_locator(locator)
        if self._allowed_paths is not None and locator not in self._allowed_paths:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                return second
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)

    @contextmanager
    def _open_repository_access(self) -> Iterator[WritingArtifactRepository]:
        access = self._repository_access
        if access is None:
            yield self._repository
            return
        with access() as repository:
            if repository is not self._repository:
                raise source_error(
                    ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION
                )
            yield repository

def create_guarded_writing_artifact_source_adapter(
    repository_access: Callable[
        [], AbstractContextManager[WritingArtifactRepository]
    ],
    scope: ContextScope,
    allowed_paths: frozenset[str] | None = None,
) -> WritingArtifactSourceAdapter:
    """Create a package-internal adapter with one short guard per operation."""

    if not callable(repository_access):
        raise source_error(ContextSourceErrorCode.INVALID_VALUE)
    with repository_access() as repository:
        adapter = WritingArtifactSourceAdapter(
            repository,
            scope,
            allowed_paths=allowed_paths,
        )
    adapter._repository_access = repository_access
    return adapter
