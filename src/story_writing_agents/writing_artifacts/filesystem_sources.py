"""Catalog-independent discovery and capture of referenceable artifact sources."""

from __future__ import annotations

import hashlib

from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from .catalog import (
    CORE_ARTIFACT_PATHS,
    MAX_ARTIFACT_PATH_BYTES,
    ArtifactKind,
)
from .references import (
    MAX_VIEWER_UTF8_BYTES,
    ArtifactContentSnapshot,
    ArtifactSourceIdentity,
    reference_source_kind,
)


class ArtifactSourceInvalidUtf8Error(WorkspaceFormatError):
    """One canonical source cannot be decoded as strict UTF-8."""


class ArtifactSourceNulError(WorkspaceFormatError):
    """One eligible reference source contains a forbidden NUL code point."""


class CanonicalArtifactFilesystem:
    """Discover and capture safe current bytes beneath writing directories."""

    def __init__(self, storage: SecureWorkspaceStorage) -> None:
        if type(storage) is not SecureWorkspaceStorage:
            raise TypeError("storage must be SecureWorkspaceStorage")
        self._storage = storage

    def list_paths(self) -> tuple[str, ...]:
        """Return deterministic eligible paths using metadata-only inspection."""

        found: list[str] = []
        for path in CORE_ARTIFACT_PATHS:
            try:
                self._storage.inspect_regular_file(path)
            except (FileNotFoundError, ArtifactEntrySecurityError):
                continue
            found.append(path)

        try:
            manuscripts = self._storage.list_regular_files(
                "manuscript",
                max_path_bytes=MAX_ARTIFACT_PATH_BYTES,
            )
        except FileNotFoundError:
            manuscripts = ()
        for path, _identity in manuscripts:
            try:
                kind = reference_source_kind(path)
            except (TypeError, ValueError):
                continue
            if kind is ArtifactKind.MANUSCRIPT:
                found.append(path)
        return tuple(sorted(found))

    def read_snapshot(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
    ) -> ArtifactContentSnapshot:
        """Read one eligible source into an immutable verified snapshot."""

        try:
            kind = reference_source_kind(path)
        except (TypeError, ValueError) as exc:
            raise WorkspaceSecurityError(
                "path is not an eligible writing-artifact source"
            ) from exc
        if kind is None:
            raise WorkspaceSecurityError(
                "path is not an eligible writing-artifact source"
            )
        if max_bytes is not None and (
            type(max_bytes) is not int or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be None or a positive integer")
        effective_max_bytes = (
            MAX_VIEWER_UTF8_BYTES
            if max_bytes is None
            else min(max_bytes, MAX_VIEWER_UTF8_BYTES)
        )
        captured = self._storage.read_snapshot(
            path,
            max_bytes=effective_max_bytes,
        )
        content = captured.content
        physical = captured.identity
        source = ArtifactSourceIdentity(
            path=path,
            kind=kind,
            source_sha256=hashlib.sha256(content).hexdigest(),
            source_utf8_bytes=len(content),
            device_id=physical.device_id,
            inode=physical.inode,
            modified_ns=physical.modified_ns,
            changed_ns=physical.changed_ns,
        )
        try:
            return ArtifactContentSnapshot(source, content)
        except ValueError as exc:
            if isinstance(exc.__cause__, UnicodeDecodeError):
                raise ArtifactSourceInvalidUtf8Error(
                    "artifact is not valid UTF-8"
                ) from exc.__cause__
            if b"\x00" in content:
                raise ArtifactSourceNulError("artifact contains NUL") from None
            raise

    def read_current(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        """Read requested eligible paths, omitting absent unsafe entries."""

        if type(paths) is not tuple:
            raise TypeError("artifact paths must be a tuple")
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths must be unique")
        for path in paths:
            try:
                kind = reference_source_kind(path)
            except (TypeError, ValueError) as exc:
                raise WorkspaceSecurityError(
                    "path is not an eligible writing-artifact source"
                ) from exc
            if kind is None:
                raise WorkspaceSecurityError(
                    "path is not an eligible writing-artifact source"
                )
        snapshots: list[ArtifactContentSnapshot] = []
        for path in paths:
            try:
                snapshot = self.read_snapshot(path)
            except (FileNotFoundError, ArtifactEntrySecurityError):
                continue
            snapshots.append(snapshot)
        return tuple(snapshots)

    def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Return one bounded, stable, strict-UTF-8 reference source."""

        return self.read_snapshot(path, max_bytes=max_bytes).read_text()

    def matches(self, source: ArtifactSourceIdentity) -> bool:
        """Reopen and compare one complete source identity without refreshing it."""

        if type(source) is not ArtifactSourceIdentity:
            raise TypeError("source must be an ArtifactSourceIdentity")
        return self.read_snapshot(source.path).source == source
