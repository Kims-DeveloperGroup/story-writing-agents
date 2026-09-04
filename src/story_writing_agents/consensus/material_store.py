"""Secure content-addressed storage for unpublished consensus material."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError
from .contracts import ConsensusMaterialHandle

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported hosts fail closed at use.
    fcntl = None  # type: ignore[assignment]


CONSENSUS_MATERIAL_DIRECTORY = (
    "agent_states/orchestrator_state/consensus_materials/v1"
)
MAX_CONSENSUS_MATERIAL_BYTES = 16 * 1024 * 1024
_DIRECTORY_PARTS = tuple(CONSENSUS_MATERIAL_DIRECTORY.split("/"))
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LOCATOR = re.compile(r"candidate:([0-9a-f]{64})")


class ConsensusMaterialStoreError(WorkspaceFormatError):
    """Candidate material is absent, malformed, or no longer trustworthy."""


class ConsensusMaterialStoreSecurityError(WorkspaceSecurityError):
    """Candidate storage ancestry or an entry is unsafe."""


class _CandidateMissing(ConsensusMaterialStoreError):
    """Internal classification for an absent candidate or store path."""


@dataclass(frozen=True, slots=True)
class ConsensusMaterialMetadata:
    """Content-free facts about one verified immutable candidate."""

    handle: ConsensusMaterialHandle
    utf8_bytes: int


class ConsensusMaterialStore:
    """Own unpublished immutable UTF-8 material beneath one workspace root.

    Construction performs no I/O. Every operation traverses from a freshly
    opened workspace descriptor without following links and revalidates the
    retained ancestry before returning.
    """

    def __init__(self, workspace_root: str | os.PathLike[str]) -> None:
        try:
            raw_root = os.fspath(workspace_root)
        except TypeError as exc:
            raise TypeError("workspace_root must be a filesystem path") from exc
        if type(raw_root) is not str or not raw_root or "\0" in raw_root:
            raise ValueError("workspace_root must be a nonempty NUL-free path")
        self._root = Path(os.path.abspath(raw_root))

    def put_text(self, content: str) -> ConsensusMaterialHandle:
        """Atomically retain content and return its opaque candidate handle."""

        raw = _material_bytes(content)
        digest = hashlib.sha256(raw).hexdigest()
        handle = ConsensusMaterialHandle(
            locator=f"candidate:{digest}",
            digest=digest,
        )
        with self._open_store(create=True) as store:
            with self._open_shard(store, digest, create=True) as shard:
                with self._locked(shard):
                    existing = self._read_if_exists(shard, digest)
                    if existing is not None:
                        if existing != raw:
                            raise ConsensusMaterialStoreError(
                                "candidate material digest collision"
                            )
                        return handle
                    self._publish(shard, digest, raw)
                    if self._read_required(shard, digest) != raw:
                        raise ConsensusMaterialStoreError(
                            "candidate material publication is inconsistent"
                        )
        return handle

    def read_text(self, handle: ConsensusMaterialHandle) -> str:
        """Read one candidate after locator and content digest verification."""

        digest = _handle_digest(handle)
        with self._open_store(create=False) as store:
            with self._open_shard(store, digest, create=False) as shard:
                raw = self._read_required(shard, digest)
        return raw.decode("utf-8", errors="strict")

    def inspect(self, handle: ConsensusMaterialHandle) -> ConsensusMaterialMetadata:
        """Return content-free metadata after verifying the stored bytes."""

        digest = _handle_digest(handle)
        with self._open_store(create=False) as store:
            with self._open_shard(store, digest, create=False) as shard:
                size = len(self._read_required(shard, digest))
        return ConsensusMaterialMetadata(handle=handle, utf8_bytes=size)

    def delete(self, handle: ConsensusMaterialHandle) -> bool:
        """Explicitly delete one verified candidate, never an artifact path."""

        digest = _handle_digest(handle)
        try:
            with self._open_store(create=False) as store:
                with self._open_shard(store, digest, create=False) as shard:
                    with self._locked(shard):
                        if self._read_if_exists(shard, digest) is None:
                            return False
                        os.unlink(_filename(digest), dir_fd=shard)
                        os.fsync(shard)
                        return True
        except (FileNotFoundError, _CandidateMissing):
            return False

    @contextlib.contextmanager
    def _open_store(self, *, create: bool) -> Iterator[int]:
        self._require_primitives()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            root = os.open(self._root, flags)
        except OSError as exc:
            raise ConsensusMaterialStoreSecurityError(
                "cannot securely open consensus workspace root"
            ) from exc
        descriptors = [root]
        chain: list[tuple[int, str, int, tuple[int, int]]] = []
        try:
            current = root
            for part in _DIRECTORY_PARTS:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                child = os.open(part, flags, dir_fd=current)
                descriptors.append(child)
                observed = os.fstat(child)
                if not stat.S_ISDIR(observed.st_mode):
                    raise ConsensusMaterialStoreSecurityError(
                        "consensus material ancestry is not a directory"
                    )
                chain.append(
                    (current, part, child, (observed.st_dev, observed.st_ino))
                )
                current = child
            self._verify_chain(chain)
            yield current
            self._verify_chain(chain)
            verification = os.open(self._root, flags)
            try:
                if _identity(verification) != _identity(root):
                    raise ConsensusMaterialStoreSecurityError(
                        "consensus workspace root identity changed"
                    )
            finally:
                os.close(verification)
        except FileNotFoundError as exc:
            raise _CandidateMissing(
                "candidate material store does not exist"
            ) from exc
        except ConsensusMaterialStoreError:
            raise
        except ConsensusMaterialStoreSecurityError:
            raise
        except OSError as exc:
            raise ConsensusMaterialStoreSecurityError(
                "cannot securely traverse consensus material storage"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextlib.contextmanager
    def _open_shard(self, store: int, digest: str, *, create: bool) -> Iterator[int]:
        name = digest[:2]
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=store)
                os.fsync(store)
            except FileExistsError:
                pass
        try:
            shard = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=store,
            )
        except FileNotFoundError as exc:
            raise _CandidateMissing("candidate material does not exist") from exc
        except OSError as exc:
            raise ConsensusMaterialStoreSecurityError(
                "candidate material shard is unsafe"
            ) from exc
        try:
            named = os.stat(name, dir_fd=store, follow_symlinks=False)
            opened = os.fstat(shard)
            if (
                not stat.S_ISDIR(named.st_mode)
                or not stat.S_ISDIR(opened.st_mode)
                or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ConsensusMaterialStoreSecurityError(
                    "candidate material shard identity changed"
                )
            yield shard
            if _identity(shard) != (named.st_dev, named.st_ino):
                raise ConsensusMaterialStoreSecurityError(
                    "candidate material shard identity changed"
                )
        finally:
            os.close(shard)

    @contextlib.contextmanager
    def _locked(self, shard: int) -> Iterator[None]:
        assert fcntl is not None
        try:
            lock = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=shard,
            )
        except OSError as exc:
            raise ConsensusMaterialStoreSecurityError(
                "candidate material lock is unsafe"
            ) from exc
        try:
            observed = os.fstat(lock)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise ConsensusMaterialStoreSecurityError(
                    "candidate material lock identity is unsafe"
                )
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)

    @staticmethod
    def _read_if_exists(shard: int, digest: str) -> bytes | None:
        name = _filename(digest)
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=shard,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConsensusMaterialStoreSecurityError(
                "candidate material entry is unsafe"
            ) from exc
        try:
            observed = os.fstat(descriptor)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_nlink != 1
                or observed.st_size > MAX_CONSENSUS_MATERIAL_BYTES
            ):
                raise ConsensusMaterialStoreSecurityError(
                    "candidate material entry identity is unsafe"
                )
            raw = _read_exact(descriptor, observed.st_size)
        finally:
            os.close(descriptor)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ConsensusMaterialStoreError("candidate material digest mismatch")
        _require_utf8(raw)
        return raw

    @classmethod
    def _read_required(cls, shard: int, digest: str) -> bytes:
        raw = cls._read_if_exists(shard, digest)
        if raw is None:
            raise _CandidateMissing("candidate material does not exist")
        return raw

    @staticmethod
    def _publish(shard: int, digest: str, raw: bytes) -> None:
        temporary = f".{digest}.{uuid4().hex}.tmp"
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=shard,
            )
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(errno.EIO, "candidate material write failed")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            try:
                os.link(
                    temporary,
                    _filename(digest),
                    src_dir_fd=shard,
                    dst_dir_fd=shard,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return
            os.fsync(shard)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=shard)
            except FileNotFoundError:
                pass

    @staticmethod
    def _verify_chain(chain: list[tuple[int, str, int, tuple[int, int]]]) -> None:
        for parent, name, child, expected in chain:
            try:
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(child)
            except OSError as exc:
                raise ConsensusMaterialStoreSecurityError(
                    "consensus material ancestry changed"
                ) from exc
            if (
                not stat.S_ISDIR(named.st_mode)
                or not stat.S_ISDIR(opened.st_mode)
                or (named.st_dev, named.st_ino) != expected
                or _identity(child) != expected
            ):
                raise ConsensusMaterialStoreSecurityError(
                    "consensus material ancestry changed"
                )

    @staticmethod
    def _require_primitives() -> None:
        if (
            fcntl is None
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or any(
                function not in os.supports_dir_fd
                for function in (os.open, os.mkdir, os.stat, os.link, os.unlink)
            )
        ):
            raise ConsensusMaterialStoreSecurityError(
                "safe candidate material filesystem primitives unavailable"
            )


def _handle_digest(handle: ConsensusMaterialHandle) -> str:
    if type(handle) is not ConsensusMaterialHandle:
        raise TypeError("handle must be ConsensusMaterialHandle")
    match = _LOCATOR.fullmatch(handle.locator)
    if match is None or match.group(1) != handle.digest:
        raise ConsensusMaterialStoreError("candidate material handle is invalid")
    return handle.digest


def _filename(digest: str) -> str:
    if _DIGEST.fullmatch(digest) is None:
        raise ConsensusMaterialStoreError("candidate material digest is invalid")
    return f"{digest[2:]}.utf8"


def _material_bytes(content: str) -> bytes:
    if type(content) is not str or not content or "\0" in content:
        raise ValueError("candidate material must be nonempty NUL-free text")
    raw = content.encode("utf-8", errors="strict")
    if len(raw) > MAX_CONSENSUS_MATERIAL_BYTES:
        raise ValueError("candidate material exceeds its byte limit")
    return raw


def _require_utf8(raw: bytes) -> None:
    try:
        content = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConsensusMaterialStoreError(
            "candidate material is not strict UTF-8"
        ) from exc
    if not content or "\0" in content:
        raise ConsensusMaterialStoreError(
            "candidate material is empty or contains NUL"
        )


def _read_exact(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise ConsensusMaterialStoreError("candidate material changed while read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise ConsensusMaterialStoreError("candidate material changed while read")
    return b"".join(chunks)


def _identity(descriptor: int) -> tuple[int, int]:
    observed = os.fstat(descriptor)
    return observed.st_dev, observed.st_ino
