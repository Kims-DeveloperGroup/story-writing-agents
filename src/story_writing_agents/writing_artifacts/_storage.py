"""Descriptor-relative, no-follow storage for the writing-artifact domain."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import hashlib
import os
import stat
import sys
import threading
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError

try:
    import fcntl
except ImportError:  # pragma: no cover - the adapter rejects unsupported hosts.
    fcntl = None  # type: ignore[assignment]


class ArtifactEntrySecurityError(WorkspaceSecurityError):
    """One artifact name is unsafe while the workspace root remains bound."""


class ArtifactRootSecurityError(WorkspaceSecurityError):
    """The workspace root or one of its traversed ancestors changed."""


class ArtifactTraversalLimitError(WorkspaceFormatError):
    """Recursive artifact discovery exceeded its explicit entry budget."""


class ArtifactLeaseSecurityError(WorkspaceSecurityError):
    """The permanent repository lock identity is not stable."""


@dataclass(frozen=True, slots=True)
class SecureFileIdentity:
    """Descriptor-verified physical identity for one unique regular file."""

    device_id: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class SecureFileSnapshot:
    """One stable descriptor read and its content-free physical identity."""

    identity: SecureFileIdentity
    content: bytes


class SecureWorkspaceStorage:
    """Perform call-local artifact I/O from one verified workspace root."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        expected_root_identity: tuple[int, int] | None = None,
    ) -> None:
        if expected_root_identity is not None and (
            type(expected_root_identity) is not tuple
            or len(expected_root_identity) != 2
            or any(
                type(value) is not int or value <= 0
                for value in expected_root_identity
            )
        ):
            raise TypeError(
                "expected_root_identity must be a physical directory identity"
            )
        self._root = Path(os.path.abspath(os.fspath(root)))
        self._expected_root_identity = expected_root_identity
        self._identity: tuple[int, int] | None = None
        self._identity_lock = threading.Lock()
        self._active_lease: tuple[
            int,
            str,
            str,
            str,
            int,
            tuple[tuple[int, str, int, tuple[int, int]], ...],
        ] | None = None

    @staticmethod
    def _require_primitives() -> None:
        required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required_flags):
            raise WorkspaceSecurityError("safe artifact filesystem primitives unavailable")
        dir_fd_functions = (os.open, os.mkdir, os.stat, os.link)
        if any(function not in os.supports_dir_fd for function in dir_fd_functions):
            raise WorkspaceSecurityError("descriptor-relative artifact I/O unavailable")
        if (
            os.listdir not in os.supports_fd
            or os.scandir not in os.supports_fd
            or fcntl is None
        ):
            raise WorkspaceSecurityError("safe artifact locking or listing unavailable")
        SecureWorkspaceStorage._native_rename_function()

    @staticmethod
    def _native_rename_function():
        """Return one supported atomic no-replace rename function and flag."""

        library = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
            function = library.renameatx_np
            flag = 0x00000004  # RENAME_EXCL
        elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
            function = library.renameat2
            flag = 0x00000001  # RENAME_NOREPLACE
        else:
            raise WorkspaceSecurityError(
                "atomic no-replace artifact rename unavailable"
            )
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        probe = function(-1, b".", -1, b".", flag)
        probe_error = ctypes.get_errno()
        unsupported_errors = {
            errno.EINVAL,
            errno.ENOSYS,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        }
        if probe != -1 or probe_error in unsupported_errors:
            raise WorkspaceSecurityError(
                "atomic no-replace artifact rename unavailable"
            )
        return function, flag

    @classmethod
    def _rename_noreplace(
        cls,
        source_directory: int,
        source: str,
        destination_directory: int,
        destination: str,
    ) -> None:
        """Atomically move one name only when the destination is absent."""

        function, flag = cls._native_rename_function()
        ctypes.set_errno(0)
        result = function(
            source_directory,
            os.fsencode(source),
            destination_directory,
            os.fsencode(destination),
            flag,
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(
                error_number,
                os.strerror(error_number),
                destination,
            )
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination,
        )

    @staticmethod
    def _parts(relative: object) -> tuple[str, ...]:
        if type(relative) is not str or not relative or "\\" in relative or "\x00" in relative:
            raise WorkspaceSecurityError("artifact storage path must be relative POSIX text")
        parsed = PurePosixPath(relative)
        parts = parsed.parts
        if parsed.is_absolute() or parsed.as_posix() != relative or any(
            part in {"", ".", ".."} for part in parts
        ):
            raise WorkspaceSecurityError("unsafe artifact storage path")
        return parts

    @staticmethod
    def _entry_spelling_key(value: str) -> str:
        """Return the filesystem-alias key used only to diagnose unsafe names."""

        return unicodedata.normalize("NFKC", value).casefold()

    @classmethod
    def _require_exact_name(
        cls,
        directory: int,
        name: str,
        *,
        directory_entry: bool,
        missing_ok: bool = False,
    ) -> None:
        """Require one descriptor-relative name with exact stored spelling."""

        try:
            names = os.listdir(directory)
        except OSError as exc:
            error_type = (
                ArtifactRootSecurityError
                if directory_entry
                else ArtifactEntrySecurityError
            )
            raise error_type("cannot verify artifact entry spelling") from exc
        key = cls._entry_spelling_key(name)
        if any(
            candidate != name and cls._entry_spelling_key(candidate) == key
            for candidate in names
        ):
            error_type = (
                ArtifactRootSecurityError
                if directory_entry
                else ArtifactEntrySecurityError
            )
            raise error_type("artifact entry spelling is not exact")
        if name in names:
            return
        if missing_ok:
            return
        raise FileNotFoundError(name)

    @contextlib.contextmanager
    def _open_root(self) -> Iterator[int]:
        self._require_primitives()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            descriptor = os.open(self._root, flags)
        except OSError as exc:
            raise ArtifactRootSecurityError("cannot securely open workspace root") from exc
        try:
            observed = os.fstat(descriptor)
            identity = (observed.st_dev, observed.st_ino)
            if not stat.S_ISDIR(observed.st_mode) or any(value <= 0 for value in identity):
                raise ArtifactRootSecurityError("workspace root identity is not meaningful")
            if (
                self._expected_root_identity is not None
                and identity != self._expected_root_identity
            ):
                raise ArtifactRootSecurityError("workspace root identity changed")
            with self._identity_lock:
                if self._identity is None:
                    self._identity = identity
                elif self._identity != identity:
                    raise ArtifactRootSecurityError("workspace root identity changed")
            try:
                yield descriptor
            except BaseException:
                raise
            else:
                self._verify_root_identity(descriptor)
        finally:
            os.close(descriptor)

    def _verify_root_identity(self, descriptor: int) -> None:
        """Require the retained root descriptor to remain the named root."""

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            verification = os.open(self._root, flags)
        except OSError as exc:
            raise ArtifactRootSecurityError(
                "workspace root identity changed during artifact I/O"
            ) from exc
        try:
            retained = os.fstat(descriptor)
            named = os.fstat(verification)
            retained_identity = (retained.st_dev, retained.st_ino)
            if (
                (named.st_dev, named.st_ino) != retained_identity
                or (
                    self._expected_root_identity is not None
                    and retained_identity != self._expected_root_identity
                )
            ):
                raise ArtifactRootSecurityError(
                    "workspace root identity changed during artifact I/O"
                )
        finally:
            os.close(verification)

    @contextlib.contextmanager
    def _open_directory(
        self,
        root_descriptor: int,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> Iterator[
        tuple[int, tuple[tuple[int, str, int, tuple[int, int]], ...]]
    ]:
        current = os.dup(root_descriptor)
        descriptors = [current]
        chain: list[tuple[int, str, int, tuple[int, int]]] = []
        try:
            for name in parts:
                if create:
                    try:
                        self._require_exact_name(
                            current,
                            name,
                            directory_entry=True,
                            missing_ok=True,
                        )
                        os.mkdir(name, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        raise ArtifactRootSecurityError("cannot create artifact directory") from exc
                try:
                    self._require_exact_name(
                        current,
                        name,
                        directory_entry=True,
                    )
                    named = os.stat(name, dir_fd=current, follow_symlinks=False)
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                    descriptors.append(child)
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise ArtifactRootSecurityError("cannot securely traverse artifact path") from exc
                opened = os.fstat(child)
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or not stat.S_ISDIR(opened.st_mode)
                    or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    raise ArtifactRootSecurityError("artifact directory changed during traversal")
                self._require_exact_name(
                    current,
                    name,
                    directory_entry=True,
                )
                identity = (opened.st_dev, opened.st_ino)
                chain.append((current, name, child, identity))
                current = child
            self._verify_directory_chain(tuple(chain))
            yield current, tuple(chain)
            self._verify_directory_chain(tuple(chain))
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _verify_directory_chain(
        chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
    ) -> None:
        """Revalidate every retained parent/name/descriptor relationship."""

        for parent, name, child, identity in chain:
            try:
                SecureWorkspaceStorage._require_exact_name(
                    parent,
                    name,
                    directory_entry=True,
                )
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(child)
            except OSError as exc:
                raise ArtifactRootSecurityError(
                    "artifact directory ancestry changed"
                ) from exc
            if (
                not stat.S_ISDIR(named.st_mode)
                or not stat.S_ISDIR(opened.st_mode)
                or (named.st_dev, named.st_ino) != identity
                or (opened.st_dev, opened.st_ino) != identity
            ):
                raise ArtifactRootSecurityError(
                    "artifact directory ancestry changed"
                )

    @staticmethod
    def _identity(value: os.stat_result) -> SecureFileIdentity:
        return SecureFileIdentity(
            device_id=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
        )

    @staticmethod
    def _read_file_snapshot(
        directory: int,
        name: str,
        *,
        max_bytes: int | None = None,
    ) -> SecureFileSnapshot:
        try:
            SecureWorkspaceStorage._require_exact_name(
                directory,
                name,
                directory_entry=False,
                missing_ok=True,
            )
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactEntrySecurityError("cannot inspect artifact file") from exc
        if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
            raise ArtifactEntrySecurityError(
                "artifact file is not a unique regular file"
            )
        SecureWorkspaceStorage._require_exact_name(
            directory,
            name,
            directory_entry=False,
        )
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory,
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactEntrySecurityError(
                "cannot securely open artifact file"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(named.st_mode)
                or not stat.S_ISREG(opened.st_mode)
                or named.st_nlink != 1
                or opened.st_nlink != 1
                or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ArtifactEntrySecurityError(
                    "artifact file is not a unique regular file"
                )
            SecureWorkspaceStorage._require_exact_name(
                directory,
                name,
                directory_entry=False,
            )
            if max_bytes is not None and opened.st_size > max_bytes:
                raise WorkspaceFormatError(
                    "artifact file exceeds the permitted size"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise WorkspaceFormatError(
                        "artifact file exceeds the permitted size"
                    )
                chunks.append(chunk)
            final = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            ) != (
                final.st_dev,
                final.st_ino,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            ):
                raise ArtifactEntrySecurityError("artifact file changed while being read")
            try:
                SecureWorkspaceStorage._require_exact_name(
                    directory,
                    name,
                    directory_entry=False,
                )
                final_name = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError as exc:
                raise ArtifactEntrySecurityError(
                    "artifact name changed while being read"
                ) from exc
            if (
                not stat.S_ISREG(final_name.st_mode)
                or final_name.st_nlink != 1
                or (final_name.st_dev, final_name.st_ino)
                != (final.st_dev, final.st_ino)
            ):
                raise ArtifactEntrySecurityError(
                    "artifact name changed while being read"
                )
            return SecureFileSnapshot(
                SecureWorkspaceStorage._identity(final),
                b"".join(chunks),
            )
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_file(
        directory: int,
        name: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        return SecureWorkspaceStorage._read_file_snapshot(
            directory,
            name,
            max_bytes=max_bytes,
        ).content

    def read_bytes(self, relative: str, *, max_bytes: int | None = None) -> bytes:
        return self.read_snapshot(relative, max_bytes=max_bytes).content

    def read_snapshot(
        self,
        relative: str,
        *,
        max_bytes: int | None = None,
    ) -> SecureFileSnapshot:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=False) as opened:
                directory, chain = opened
                self._verify_directory_chain(chain)
                return self._read_file_snapshot(
                    directory,
                    parts[-1],
                    max_bytes=max_bytes,
                )

    @staticmethod
    def _inspect_file(directory: int, name: str) -> SecureFileIdentity:
        """Inspect one exact unique regular file without reading its bytes."""

        try:
            SecureWorkspaceStorage._require_exact_name(
                directory,
                name,
                directory_entry=False,
                missing_ok=True,
            )
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactEntrySecurityError(
                "cannot inspect artifact file"
            ) from exc
        if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
            raise ArtifactEntrySecurityError(
                "artifact file is not a unique regular file"
            )
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory,
            )
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactEntrySecurityError(
                "cannot securely open artifact file"
            ) from exc
        try:
            opened = os.fstat(descriptor)
            SecureWorkspaceStorage._require_exact_name(
                directory,
                name,
                directory_entry=False,
            )
            final_name = os.stat(name, dir_fd=directory, follow_symlinks=False)
            identities = {
                (named.st_dev, named.st_ino),
                (opened.st_dev, opened.st_ino),
                (final_name.st_dev, final_name.st_ino),
            }
            if (
                len(identities) != 1
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(final_name.st_mode)
                or opened.st_nlink != 1
                or final_name.st_nlink != 1
            ):
                raise ArtifactEntrySecurityError(
                    "artifact file changed while being inspected"
                )
            return SecureWorkspaceStorage._identity(opened)
        except FileNotFoundError:
            raise
        except ArtifactEntrySecurityError:
            raise
        except OSError as exc:
            raise ArtifactEntrySecurityError(
                "artifact file changed while being inspected"
            ) from exc
        finally:
            os.close(descriptor)

    def inspect_regular_file(self, relative: str) -> SecureFileIdentity:
        """Return stable metadata for one exact file without reading content."""

        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=False) as opened:
                directory, chain = opened
                self._verify_directory_chain(chain)
                return self._inspect_file(directory, parts[-1])

    @classmethod
    def _walk_regular_files(
        cls,
        directory: int,
        prefix: tuple[str, ...],
        chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
        *,
        max_path_bytes: int,
        max_visited_entries: int,
        visited_entries: list[int],
        found: list[tuple[str, SecureFileIdentity]],
    ) -> None:
        """Walk one retained descriptor tree without following unsafe names."""

        cls._verify_directory_chain(chain)
        names: list[str] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited_entries[0] += 1
                    if visited_entries[0] > max_visited_entries:
                        raise ArtifactTraversalLimitError(
                            "artifact traversal exceeds its entry limit"
                        )
                    names.append(entry.name)
        except ArtifactTraversalLimitError:
            raise
        except OSError as exc:
            raise ArtifactRootSecurityError(
                "cannot securely list artifact directory"
            ) from exc
        if any(type(name) is not str or name in {"", ".", ".."} for name in names):
            raise ArtifactRootSecurityError(
                "artifact directory contains an unsafe name"
            )
        alias_counts: dict[str, int] = {}
        for name in names:
            key = cls._entry_spelling_key(name)
            alias_counts[key] = alias_counts.get(key, 0) + 1

        for name in sorted(names):
            if alias_counts[cls._entry_spelling_key(name)] != 1:
                continue
            relative_parts = (*prefix, name)
            relative = "/".join(relative_parts)
            try:
                encoded = relative.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                continue
            if len(encoded) > max_path_bytes:
                continue
            try:
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except (FileNotFoundError, OSError):
                continue
            if stat.S_ISREG(named.st_mode):
                try:
                    identity = cls._inspect_file(directory, name)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                found.append((relative, identity))
                continue
            if not stat.S_ISDIR(named.st_mode):
                continue
            try:
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
            except OSError:
                continue
            try:
                opened = os.fstat(child)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or (named.st_dev, named.st_ino)
                    != (opened.st_dev, opened.st_ino)
                ):
                    continue
                identity = (opened.st_dev, opened.st_ino)
                child_chain = (*chain, (directory, name, child, identity))
                try:
                    cls._walk_regular_files(
                        child,
                        relative_parts,
                        child_chain,
                        max_path_bytes=max_path_bytes,
                        max_visited_entries=max_visited_entries,
                        visited_entries=visited_entries,
                        found=found,
                    )
                except ArtifactRootSecurityError:
                    continue
            finally:
                os.close(child)
        cls._verify_directory_chain(chain)

    def list_regular_files(
        self,
        relative: str,
        *,
        max_path_bytes: int,
        max_visited_entries: int = 8_192,
    ) -> tuple[tuple[str, SecureFileIdentity], ...]:
        """List metadata for safe regular descendants without reading content."""

        if type(max_path_bytes) is not int or max_path_bytes < 1:
            raise ValueError("max_path_bytes must be a positive integer")
        if type(max_visited_entries) is not int or max_visited_entries < 1:
            raise ValueError("max_visited_entries must be a positive integer")
        parts = self._parts(relative)
        found: list[tuple[str, SecureFileIdentity]] = []
        visited_entries = [0]
        with self._open_root() as root:
            with self._open_directory(root, parts, create=False) as opened:
                directory, chain = opened
                self._walk_regular_files(
                    directory,
                    parts,
                    chain,
                    max_path_bytes=max_path_bytes,
                    max_visited_entries=max_visited_entries,
                    visited_entries=visited_entries,
                    found=found,
                )
        return tuple(sorted(found, key=lambda item: item[0]))

    def read_bytes_if_exists(
        self, relative: str, *, max_bytes: int | None = None
    ) -> bytes | None:
        try:
            return self.read_bytes(relative, max_bytes=max_bytes)
        except FileNotFoundError:
            return None

    def list_names(self, relative: str) -> tuple[str, ...]:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts, create=False) as opened:
                directory, chain = opened
                self._verify_directory_chain(chain)
                try:
                    names = os.listdir(directory)
                except OSError as exc:
                    raise ArtifactRootSecurityError(
                        "cannot securely list artifact state"
                    ) from exc
                self._verify_directory_chain(chain)
        if any(type(name) is not str or name in {"", ".", ".."} for name in names):
            raise ArtifactRootSecurityError("artifact state contains an unsafe name")
        return tuple(sorted(names))

    def ensure_directory(self, relative: str) -> None:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts, create=True):
                return

    def _require_active_lease(self) -> None:
        lease = self._active_lease
        if lease is None:
            raise ArtifactLeaseSecurityError(
                "artifact mutation requires the repository lease"
            )
        directory, name, anchor, ready, descriptor, chain = lease
        self._verify_directory_chain(chain)
        self._verify_lock_identity(directory, name, anchor, ready, descriptor)

    def publish_bytes(
        self,
        relative: str,
        content: bytes,
        *,
        expected_sha256: str | None,
        expected_exists: bool,
        create_only: bool,
        staging_directory: str | None = None,
    ) -> None:
        if type(content) is not bytes:
            raise TypeError("artifact content must be bytes")
        self._require_active_lease()
        parts = self._parts(relative)
        staging_parts = (
            parts[:-1]
            if staging_directory is None
            else self._parts(staging_directory)
        )
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=True) as target_opened:
                directory, target_chain = target_opened
                with self._open_directory(root, staging_parts, create=True) as stage_opened:
                    stage_directory, stage_chain = stage_opened
                    self._publish_bytes_locked(
                        relative,
                        parts[-1],
                        content,
                        expected_sha256=expected_sha256,
                        expected_exists=expected_exists,
                        create_only=create_only,
                        directory=directory,
                        target_chain=target_chain,
                        stage_directory=stage_directory,
                        stage_chain=stage_chain,
                        root_descriptor=root,
                    )

    def _publish_bytes_locked(
        self,
        relative: str,
        name: str,
        content: bytes,
        *,
        expected_sha256: str | None,
        expected_exists: bool,
        create_only: bool,
        directory: int,
        target_chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
        stage_directory: int,
        stage_chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
        root_descriptor: int,
    ) -> None:
        self._require_active_lease()
        self._verify_directory_chain(target_chain)
        self._verify_directory_chain(stage_chain)
        try:
            current = self._read_file(directory, name)
        except FileNotFoundError:
            current = None
        current_identity: tuple[int, int] | None = None
        if current is not None:
            current_stat = os.stat(name, dir_fd=directory, follow_symlinks=False)
            current_identity = (current_stat.st_dev, current_stat.st_ino)
        if create_only and current is not None:
            raise FileExistsError(relative)
        if expected_exists != (current is not None):
            raise ArtifactEntrySecurityError("artifact changed before publication")
        if current is not None and (
            expected_sha256 is None
            or hashlib.sha256(current).hexdigest() != expected_sha256
        ):
            raise ArtifactEntrySecurityError("artifact changed before publication")

        temporary = f".artifacts-{uuid4().hex}.tmp"
        descriptor = -1
        staged_identity: tuple[int, int] | None = None
        holding: str | None = None
        held_identity: tuple[int, int] | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=stage_directory,
            )
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "artifact write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            staged = os.fstat(descriptor)
            staged_identity = (staged.st_dev, staged.st_ino)
            if (
                not stat.S_ISREG(staged.st_mode)
                or staged.st_nlink != 1
                or any(value <= 0 for value in staged_identity)
            ):
                raise ArtifactEntrySecurityError(
                    "staged artifact is not a unique regular file"
                )
            self._require_exact_name(
                stage_directory,
                temporary,
                directory_entry=False,
            )
            os.close(descriptor)
            descriptor = -1

            self._require_active_lease()
            self._verify_directory_chain(target_chain)
            try:
                latest = self._read_file(directory, name)
            except FileNotFoundError:
                latest = None
            if latest != current:
                raise ArtifactEntrySecurityError(
                    "artifact changed before publication"
                )

            if expected_exists:
                assert current_identity is not None
                holding, held_identity = self._move_to_retirement(
                    directory,
                    name,
                    stage_directory,
                    suffix="held",
                )
                if held_identity != current_identity:
                    self._restore_retired_name(
                        stage_directory,
                        holding,
                        directory,
                        name,
                        held_identity,
                    )
                    raise ArtifactEntrySecurityError(
                        "artifact changed before publication"
                    )
                try:
                    if self._read_file(stage_directory, holding) != current:
                        raise ArtifactEntrySecurityError(
                            "artifact changed before publication"
                        )
                except BaseException:
                    self._restore_retired_name(
                        stage_directory,
                        holding,
                        directory,
                        name,
                        held_identity,
                    )
                    raise

            try:
                self._rename_noreplace(
                    stage_directory,
                    temporary,
                    directory,
                    name,
                )
            except BaseException:
                if holding is not None and held_identity is not None:
                    self._restore_retired_name(
                        stage_directory,
                        holding,
                        directory,
                        name,
                        held_identity,
                    )
                raise

            try:
                os.fsync(directory)
                if stage_directory != directory:
                    os.fsync(stage_directory)
                self._require_active_lease()
                self._verify_root_identity(root_descriptor)
                self._verify_directory_chain(target_chain)
                self._verify_directory_chain(stage_chain)
                if self._read_file(directory, name) != content:
                    raise ArtifactEntrySecurityError(
                        "artifact publication could not be verified"
                    )
            except BaseException:
                assert staged_identity is not None
                self._rollback_publication(
                    directory,
                    name,
                    stage_directory,
                    staged_identity,
                    target_chain,
                    holding=holding,
                    held_identity=held_identity,
                )
                raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _move_to_retirement(
        cls,
        source_directory: int,
        source: str,
        retirement_directory: int,
        *,
        suffix: str,
    ) -> tuple[str, tuple[int, int]]:
        """Move one name to an exclusive non-authoritative retirement name."""

        for attempt in range(128):
            destination = f".artifacts-{uuid4().hex}-{attempt}.{suffix}"
            try:
                cls._require_exact_name(
                    source_directory,
                    source,
                    directory_entry=False,
                )
                cls._rename_noreplace(
                    source_directory,
                    source,
                    retirement_directory,
                    destination,
                )
            except FileExistsError:
                continue
            moved = os.stat(
                destination,
                dir_fd=retirement_directory,
                follow_symlinks=False,
            )
            cls._require_exact_name(
                retirement_directory,
                destination,
                directory_entry=False,
            )
            if not stat.S_ISREG(moved.st_mode):
                raise ArtifactEntrySecurityError(
                    "artifact retirement moved a nonregular entry"
                )
            return destination, (moved.st_dev, moved.st_ino)
        raise ArtifactEntrySecurityError(
            "cannot acquire an exclusive artifact retirement name"
        )

    @classmethod
    def _restore_retired_name(
        cls,
        retirement_directory: int,
        retirement_name: str,
        destination_directory: int,
        destination: str,
        expected_identity: tuple[int, int],
    ) -> None:
        """Restore retained state only when its identity and target are safe."""

        retained = os.stat(
            retirement_name,
            dir_fd=retirement_directory,
            follow_symlinks=False,
        )
        cls._require_exact_name(
            retirement_directory,
            retirement_name,
            directory_entry=False,
        )
        if (
            not stat.S_ISREG(retained.st_mode)
            or (retained.st_dev, retained.st_ino) != expected_identity
        ):
            raise ArtifactEntrySecurityError(
                "artifact retirement identity changed"
            )
        cls._rename_noreplace(
            retirement_directory,
            retirement_name,
            destination_directory,
            destination,
        )

    @classmethod
    def _rollback_publication(
        cls,
        directory: int,
        name: str,
        retirement_directory: int,
        published_identity: tuple[int, int],
        target_chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
        *,
        holding: str | None,
        held_identity: tuple[int, int] | None,
    ) -> None:
        """Retire failed publication bytes and restore prior state when safe."""

        _, retired_identity = cls._move_to_retirement(
            directory,
            name,
            retirement_directory,
            suffix="retired",
        )
        if retired_identity != published_identity:
            raise ArtifactEntrySecurityError(
                "published artifact identity changed before rollback"
            )
        try:
            cls._verify_directory_chain(target_chain)
        except ArtifactRootSecurityError:
            return
        if holding is not None and held_identity is not None:
            cls._restore_retired_name(
                retirement_directory,
                holding,
                directory,
                name,
                held_identity,
            )

    def unlink(
        self,
        relative: str,
        *,
        expected_sha256: str,
        retirement_directory: str,
        missing_ok: bool = False,
    ) -> None:
        self._require_active_lease()
        parts = self._parts(relative)
        retirement_parts = self._parts(retirement_directory)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=False) as opened:
                directory, chain = opened
                with self._open_directory(
                    root,
                    retirement_parts,
                    create=True,
                ) as retired_opened:
                    retired_directory, retired_chain = retired_opened
                    descriptor = -1
                    try:
                        descriptor = os.open(
                            parts[-1],
                            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                            dir_fd=directory,
                        )
                        opened_file = os.fstat(descriptor)
                        content = self._read_file(directory, parts[-1])
                        named = os.stat(
                            parts[-1], dir_fd=directory, follow_symlinks=False
                        )
                        expected_identity = (
                            opened_file.st_dev,
                            opened_file.st_ino,
                        )
                        if (
                            not stat.S_ISREG(named.st_mode)
                            or named.st_nlink != 1
                            or (named.st_dev, named.st_ino) != expected_identity
                            or hashlib.sha256(content).hexdigest()
                            != expected_sha256
                        ):
                            raise ArtifactEntrySecurityError(
                                "refusing to retire changed artifact state"
                            )
                        self._require_active_lease()
                        self._verify_directory_chain(chain)
                        self._verify_directory_chain(retired_chain)
                        retired, retired_identity = self._move_to_retirement(
                            directory,
                            parts[-1],
                            retired_directory,
                            suffix="completed",
                        )
                        if retired_identity != expected_identity:
                            self._restore_retired_name(
                                retired_directory,
                                retired,
                                directory,
                                parts[-1],
                                retired_identity,
                            )
                            raise ArtifactEntrySecurityError(
                                "refusing to retire changed artifact state"
                            )
                        if self._read_file(retired_directory, retired) != content:
                            raise ArtifactEntrySecurityError(
                                "retired artifact state changed"
                            )
                        os.fsync(directory)
                        os.fsync(retired_directory)
                        self._verify_directory_chain(chain)
                        self._verify_directory_chain(retired_chain)
                    except FileNotFoundError:
                        if not missing_ok:
                            raise
                    finally:
                        if descriptor >= 0:
                            os.close(descriptor)

    @classmethod
    def _require_exact_lease_name(
        cls,
        directory: int,
        name: str,
        *,
        missing_ok: bool = False,
    ) -> None:
        """Translate lock-name spelling/listing failures to lease failures."""

        try:
            cls._require_exact_name(
                directory,
                name,
                directory_entry=False,
                missing_ok=missing_ok,
            )
        except (OSError, WorkspaceSecurityError) as exc:
            raise ArtifactLeaseSecurityError(
                "artifact lock entry spelling changed"
            ) from exc

    @classmethod
    def _lease_stat_if_exists(
        cls,
        directory: int,
        name: str,
    ) -> os.stat_result | None:
        """Return one exact lock-name stat or ``None`` when truly absent."""

        cls._require_exact_lease_name(directory, name, missing_ok=True)
        try:
            return os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactLeaseSecurityError(
                "cannot inspect artifact lock entry"
            ) from exc

    @contextlib.contextmanager
    def exclusive_lock(self, relative: str) -> Iterator[None]:
        parts = self._parts(relative)
        name = parts[-1]
        anchor = f"{name}.anchor"
        ready = f"{name}.ready"
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=True) as opened:
                directory, chain = opened
                descriptor = -1
                bootstrap_locked = False
                locked = False
                body_ready = False
                try:
                    assert fcntl is not None
                    fcntl.flock(directory, fcntl.LOCK_EX)
                    bootstrap_locked = True
                    self._verify_directory_chain(chain)
                    for entry in (name, anchor, ready):
                        self._require_exact_lease_name(
                            directory,
                            entry,
                            missing_ok=True,
                        )
                    named = self._lease_stat_if_exists(directory, name)
                    anchored = self._lease_stat_if_exists(directory, anchor)
                    readied = self._lease_stat_if_exists(directory, ready)
                    if not (
                        named is None and anchored is None and readied is None
                    ) and not (
                        named is not None
                        and anchored is not None
                        and readied is not None
                    ):
                        raise ArtifactLeaseSecurityError(
                            "artifact lock triple is incomplete"
                        )
                    if named is None:
                        self._fsync_directory_ancestry(directory, chain)
                        for entry in (name, anchor, ready):
                            self._require_exact_lease_name(
                                directory,
                                entry,
                                missing_ok=True,
                            )
                        if any(
                            self._lease_stat_if_exists(directory, entry)
                            is not None
                            for entry in (name, anchor, ready)
                        ):
                            raise ArtifactLeaseSecurityError(
                                "artifact lock appeared before creation"
                            )
                        descriptor = os.open(
                            name,
                            os.O_RDWR
                            | os.O_CREAT
                            | os.O_EXCL
                            | os.O_NONBLOCK
                            | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=directory,
                        )
                        os.fsync(descriptor)
                        os.fsync(directory)
                        self._verify_lock_link_state(
                            directory,
                            (name,),
                            (anchor, ready),
                            descriptor,
                        )
                        os.link(
                            name,
                            anchor,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                        os.fsync(directory)
                        self._verify_lock_link_state(
                            directory,
                            (name, anchor),
                            (ready,),
                            descriptor,
                        )
                        os.link(
                            name,
                            ready,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                        os.fsync(directory)
                    else:
                        for entry in (name, anchor, ready):
                            self._require_exact_lease_name(directory, entry)
                        descriptor = os.open(
                            name,
                            os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW,
                            dir_fd=directory,
                        )
                    self._verify_lock_identity(
                        directory,
                        name,
                        anchor,
                        ready,
                        descriptor,
                    )
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    locked = True
                    self._verify_directory_chain(chain)
                    self._verify_lock_identity(
                        directory,
                        name,
                        anchor,
                        ready,
                        descriptor,
                    )
                    fcntl.flock(directory, fcntl.LOCK_UN)
                    bootstrap_locked = False
                    if self._active_lease is not None:
                        raise ArtifactLeaseSecurityError(
                            "artifact repository lease is already active"
                        )
                    self._active_lease = (
                        directory,
                        name,
                        anchor,
                        ready,
                        descriptor,
                        chain,
                    )
                    body_ready = True
                    yield
                except ArtifactLeaseSecurityError:
                    raise
                except OSError as exc:
                    if body_ready:
                        raise
                    raise ArtifactLeaseSecurityError(
                        "cannot securely acquire artifact lock"
                    ) from exc
                finally:
                    try:
                        if self._active_lease is not None:
                            try:
                                self._require_active_lease()
                            finally:
                                self._active_lease = None
                        if locked:
                            assert fcntl is not None
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                        if bootstrap_locked:
                            assert fcntl is not None
                            fcntl.flock(directory, fcntl.LOCK_UN)
                    finally:
                        if descriptor >= 0:
                            os.close(descriptor)

    @staticmethod
    def _fsync_directory_ancestry(
        directory: int,
        chain: tuple[tuple[int, str, int, tuple[int, int]], ...],
    ) -> None:
        """Flush the retained state directory and each parent to the root."""

        os.fsync(directory)
        for parent, _, _, _ in reversed(chain):
            os.fsync(parent)

    @classmethod
    def _verify_lock_link_state(
        cls,
        directory: int,
        names: tuple[str, ...],
        absent_names: tuple[str, ...],
        descriptor: int,
    ) -> None:
        """Require an exact hard-link state around one opened regular inode."""

        for entry in names:
            cls._require_exact_lease_name(directory, entry)
        for entry in absent_names:
            cls._require_exact_lease_name(directory, entry, missing_ok=True)
        try:
            initial_named = tuple(
                os.stat(entry, dir_fd=directory, follow_symlinks=False)
                for entry in names
            )
            for entry in absent_names:
                try:
                    os.stat(entry, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ArtifactLeaseSecurityError(
                    "artifact lock link appeared unexpectedly"
                )
            initial_opened = os.fstat(descriptor)
        except OSError as exc:
            raise ArtifactLeaseSecurityError(
                "artifact lock identity changed"
            ) from exc
        for entry in names:
            cls._require_exact_lease_name(directory, entry)
        for entry in absent_names:
            if cls._lease_stat_if_exists(directory, entry) is not None:
                raise ArtifactLeaseSecurityError(
                    "artifact lock link appeared unexpectedly"
                )
        try:
            final_named = tuple(
                os.stat(entry, dir_fd=directory, follow_symlinks=False)
                for entry in names
            )
            final_opened = os.fstat(descriptor)
        except OSError as exc:
            raise ArtifactLeaseSecurityError(
                "artifact lock identity changed"
            ) from exc
        opened_identity = (initial_opened.st_dev, initial_opened.st_ino)
        expected_links = len(names)
        if (
            not stat.S_ISREG(initial_opened.st_mode)
            or not stat.S_ISREG(final_opened.st_mode)
            or initial_opened.st_nlink != expected_links
            or final_opened.st_nlink != expected_links
            or any(value <= 0 for value in opened_identity)
            or (final_opened.st_dev, final_opened.st_ino) != opened_identity
            or any(
                not stat.S_ISREG(entry.st_mode)
                or entry.st_nlink != expected_links
                or (entry.st_dev, entry.st_ino) != opened_identity
                for entry in (*initial_named, *final_named)
            )
        ):
            raise ArtifactLeaseSecurityError(
                "artifact lock is not a stable linked regular file"
            )

    @classmethod
    def _verify_lock_identity(
        cls,
        directory: int,
        name: str,
        anchor: str,
        ready: str,
        descriptor: int,
    ) -> None:
        """Require the permanent ready triple for one regular lock inode."""

        cls._verify_lock_link_state(
            directory,
            (name, anchor, ready),
            (),
            descriptor,
        )
