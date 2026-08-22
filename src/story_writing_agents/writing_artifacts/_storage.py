"""Descriptor-relative, no-follow storage for the writing-artifact domain."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError

try:
    import fcntl
except ImportError:  # pragma: no cover - the adapter rejects unsupported hosts.
    fcntl = None  # type: ignore[assignment]


class SecureWorkspaceStorage:
    """Perform call-local artifact I/O from one verified workspace root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._identity: tuple[int, int] | None = None
        self._identity_lock = threading.Lock()
        self._require_primitives()

    @staticmethod
    def _require_primitives() -> None:
        required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required_flags):
            raise WorkspaceSecurityError("safe artifact filesystem primitives unavailable")
        dir_fd_functions = (os.open, os.mkdir, os.stat, os.unlink, os.rename, os.link)
        if any(function not in os.supports_dir_fd for function in dir_fd_functions):
            raise WorkspaceSecurityError("descriptor-relative artifact I/O unavailable")
        if os.listdir not in os.supports_fd or fcntl is None:
            raise WorkspaceSecurityError("safe artifact locking or listing unavailable")

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

    @contextlib.contextmanager
    def _open_root(self) -> Iterator[int]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            descriptor = os.open(self._root, flags)
        except OSError as exc:
            raise WorkspaceSecurityError("cannot securely open workspace root") from exc
        try:
            observed = os.fstat(descriptor)
            identity = (observed.st_dev, observed.st_ino)
            if not stat.S_ISDIR(observed.st_mode) or any(value <= 0 for value in identity):
                raise WorkspaceSecurityError("workspace root identity is not meaningful")
            with self._identity_lock:
                if self._identity is None:
                    self._identity = identity
                elif self._identity != identity:
                    raise WorkspaceSecurityError("workspace root identity changed")
            try:
                yield descriptor
            except BaseException:
                raise
            else:
                try:
                    verification = os.open(self._root, flags)
                except OSError as exc:
                    raise WorkspaceSecurityError(
                        "workspace root identity changed during artifact I/O"
                    ) from exc
                try:
                    final_root = os.fstat(verification)
                    if (final_root.st_dev, final_root.st_ino) != identity:
                        raise WorkspaceSecurityError(
                            "workspace root identity changed during artifact I/O"
                        )
                finally:
                    os.close(verification)
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def _open_directory(
        self,
        root_descriptor: int,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> Iterator[int]:
        current = os.dup(root_descriptor)
        try:
            for name in parts:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        raise WorkspaceSecurityError("cannot create artifact directory") from exc
                try:
                    named = os.stat(name, dir_fd=current, follow_symlinks=False)
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise WorkspaceSecurityError("cannot securely traverse artifact path") from exc
                opened = os.fstat(child)
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or not stat.S_ISDIR(opened.st_mode)
                    or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    os.close(child)
                    raise WorkspaceSecurityError("artifact directory changed during traversal")
                os.close(current)
                current = child
            yield current
        finally:
            os.close(current)

    @staticmethod
    def _read_file(directory: int, name: str, *, max_bytes: int | None = None) -> bytes:
        try:
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise WorkspaceSecurityError("cannot securely open artifact file") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(named.st_mode)
                or not stat.S_ISREG(opened.st_mode)
                or named.st_nlink != 1
                or opened.st_nlink != 1
                or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise WorkspaceSecurityError("artifact file is not a unique regular file")
            if max_bytes is not None and opened.st_size > max_bytes:
                raise WorkspaceFormatError("artifact file exceeds the permitted size")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise WorkspaceFormatError("artifact file exceeds the permitted size")
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
                raise WorkspaceSecurityError("artifact file changed while being read")
            try:
                final_name = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except OSError as exc:
                raise WorkspaceSecurityError(
                    "artifact name changed while being read"
                ) from exc
            if (
                not stat.S_ISREG(final_name.st_mode)
                or final_name.st_nlink != 1
                or (final_name.st_dev, final_name.st_ino)
                != (final.st_dev, final.st_ino)
            ):
                raise WorkspaceSecurityError("artifact name changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def read_bytes(self, relative: str, *, max_bytes: int | None = None) -> bytes:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=False) as directory:
                return self._read_file(directory, parts[-1], max_bytes=max_bytes)

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
            with self._open_directory(root, parts, create=False) as directory:
                try:
                    names = os.listdir(directory)
                except OSError as exc:
                    raise WorkspaceSecurityError("cannot securely list artifact state") from exc
        if any(type(name) is not str or name in {"", ".", ".."} for name in names):
            raise WorkspaceSecurityError("artifact state contains an unsafe name")
        return tuple(sorted(names))

    def ensure_directory(self, relative: str) -> None:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts, create=True):
                return

    def publish_bytes(
        self,
        relative: str,
        content: bytes,
        *,
        expected_sha256: str | None,
        expected_exists: bool,
        create_only: bool,
    ) -> None:
        if type(content) is not bytes:
            raise TypeError("artifact content must be bytes")
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=True) as directory:
                current: bytes | None
                try:
                    current = self._read_file(directory, parts[-1])
                except FileNotFoundError:
                    current = None
                if create_only and current is not None:
                    raise FileExistsError(relative)
                if expected_exists != (current is not None):
                    raise WorkspaceSecurityError("artifact changed before publication")
                if current is not None and expected_sha256 is not None:
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        raise WorkspaceSecurityError("artifact changed before publication")
                temporary = f".artifacts-{uuid4().hex}.tmp"
                descriptor = -1
                try:
                    descriptor = os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                    view = memoryview(content)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError(errno.EIO, "artifact write made no progress")
                        view = view[written:]
                    os.fsync(descriptor)
                    staged = os.fstat(descriptor)
                    if not stat.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
                        raise WorkspaceSecurityError(
                            "staged artifact is not a unique regular file"
                        )
                    os.close(descriptor)
                    descriptor = -1
                    if create_only or not expected_exists:
                        os.link(
                            temporary,
                            parts[-1],
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                        os.unlink(temporary, dir_fd=directory)
                    else:
                        os.rename(
                            temporary,
                            parts[-1],
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                        )
                    os.fsync(directory)
                    if self._read_file(directory, parts[-1]) != content:
                        raise WorkspaceSecurityError(
                            "artifact publication could not be verified"
                        )
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass

    def unlink(self, relative: str, *, missing_ok: bool = False) -> None:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=False) as directory:
                try:
                    named = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
                        raise WorkspaceSecurityError("refusing to unlink unsafe artifact state")
                    os.unlink(parts[-1], dir_fd=directory)
                    os.fsync(directory)
                except FileNotFoundError:
                    if not missing_ok:
                        raise

    @contextlib.contextmanager
    def exclusive_lock(self, relative: str) -> Iterator[None]:
        parts = self._parts(relative)
        with self._open_root() as root:
            with self._open_directory(root, parts[:-1], create=True) as directory:
                try:
                    descriptor = os.open(
                        parts[-1],
                        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                except OSError as exc:
                    raise WorkspaceSecurityError("cannot securely open artifact lock") from exc
                locked = False
                try:
                    self._verify_lock_identity(
                        directory,
                        parts[-1],
                        descriptor,
                    )
                    assert fcntl is not None
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                    locked = True
                    self._verify_lock_identity(
                        directory,
                        parts[-1],
                        descriptor,
                    )
                    yield
                finally:
                    try:
                        if locked:
                            assert fcntl is not None
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)

    @staticmethod
    def _verify_lock_identity(directory: int, name: str, descriptor: int) -> None:
        """Require one stable named unique-regular lock descriptor."""

        try:
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise WorkspaceSecurityError(
                "artifact lock identity changed"
            ) from exc
        named_identity = (named.st_dev, named.st_ino)
        opened_identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(named.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or named.st_nlink != 1
            or opened.st_nlink != 1
            or any(value <= 0 for value in named_identity)
            or any(value <= 0 for value in opened_identity)
            or named_identity != opened_identity
        ):
            raise WorkspaceSecurityError(
                "artifact lock is not a stable unique regular file"
            )
