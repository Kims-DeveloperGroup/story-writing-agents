"""Catalog-independent discovery and capture of canonical artifact sources."""

from __future__ import annotations

import hashlib

from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from .catalog import (
    CORE_ARTIFACT_PATHS,
    MAX_ARTIFACT_PATH_BYTES,
    ArtifactKind,
    canonical_artifact_kind,
)
from .references import (
    MAX_VIEWER_UTF8_BYTES,
    ArtifactContentSnapshot,
    ArtifactSourceIdentity,
)


class ArtifactSourceInvalidUtf8Error(WorkspaceFormatError):
    """One canonical source cannot be decoded as strict UTF-8."""


class ArtifactSourceNulError(WorkspaceFormatError):
    """One canonical source contains a forbidden NUL code point."""


class CanonicalArtifactFilesystem:
    """Discover and capture safe current bytes beneath canonical directories."""

    def __init__(self, storage: SecureWorkspaceStorage) -> None:
        if type(storage) is not SecureWorkspaceStorage:
            raise TypeError("storage must be SecureWorkspaceStorage")
        self._storage = storage

    def list_paths(self) -> tuple[str, ...]:
        """Return deterministic canonical paths using metadata-only inspection."""

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
        found.extend(
            path
            for path, _identity in manuscripts
            if canonical_artifact_kind(path) is ArtifactKind.MANUSCRIPT
        )
        return tuple(sorted(found))

    def read_snapshot(
        self,
        path: str,
        *,
        max_bytes: int | None = None,
    ) -> ArtifactContentSnapshot:
        """Read one canonical source into an immutable verified snapshot."""

        kind = canonical_artifact_kind(path)
        if kind is None:
            raise WorkspaceSecurityError(
                "path is not a canonical writing artifact"
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
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArtifactSourceInvalidUtf8Error(
                "artifact is not valid UTF-8"
            ) from exc
        if "\x00" in decoded:
            raise ArtifactSourceNulError("artifact contains NUL")
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
        return ArtifactContentSnapshot(source, content)

    def read_current(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        """Read only requested canonical paths, omitting absent unsafe entries."""

        if type(paths) is not tuple:
            raise TypeError("artifact paths must be a tuple")
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths must be unique")
        for path in paths:
            if canonical_artifact_kind(path) is None:
                raise WorkspaceSecurityError(
                    "path is not a canonical writing artifact"
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
        """Return one bounded, stable, strict-UTF-8 canonical source."""

        return self.read_snapshot(path, max_bytes=max_bytes).read_text()

    def matches(self, source: ArtifactSourceIdentity) -> bool:
        """Reopen and compare one complete source identity without refreshing it."""

        if type(source) is not ArtifactSourceIdentity:
            raise TypeError("source must be an ArtifactSourceIdentity")
        return self.read_snapshot(source.path).source == source
