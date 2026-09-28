"""Call-local physical workspace authority for provider execution."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path

from .git.repository_lock import current_thread_token
from .git.session_resources import GitSessionGenerationPin


_PROVENANCE_PREFIX = "sha256:"
_MINT_AUTHORITY = object()


class ProviderExecutionWorkspace:
    """Opaque ownership of one exact session generation for one invocation."""

    __slots__ = (
        "_closed",
        "_creator_pid",
        "_creator_thread_token",
        "_failure_factory",
        "_generation_pin",
        "_generation_provenance",
        "_lock",
        "_root_descriptor",
        "_root_identity",
        "_session_id",
        "_worktree_root",
    )

    def __init__(
        self,
        authority: object,
        *,
        session_id: str,
        worktree_root: Path,
        root_identity: tuple[int, int],
        generation_provenance: str,
        root_descriptor: int,
        generation_pin: GitSessionGenerationPin,
        failure_factory: Callable[[], BaseException],
    ) -> None:
        normalized_root = Path(os.path.normpath(os.fspath(worktree_root)))
        if (
            authority is not _MINT_AUTHORITY
            or type(session_id) is not str
            or not session_id
            or not worktree_root.is_absolute()
            or normalized_root != worktree_root
            or not _valid_identity(root_identity)
            or type(generation_provenance) is not str
            or not generation_provenance.startswith(_PROVENANCE_PREFIX)
            or len(generation_provenance) != len(_PROVENANCE_PREFIX) + 64
            or any(
                character not in "0123456789abcdef"
                for character in generation_provenance[len(_PROVENANCE_PREFIX):]
            )
            or type(root_descriptor) is not int
            or root_descriptor < 0
            or type(generation_pin) is not GitSessionGenerationPin
            or not callable(failure_factory)
        ):
            raise TypeError("provider execution workspace is invalid")
        opened = os.fstat(root_descriptor)
        if _directory_identity(opened) != root_identity:
            raise TypeError("provider execution workspace root is invalid")
        object.__setattr__(self, "_session_id", session_id)
        object.__setattr__(self, "_worktree_root", worktree_root)
        object.__setattr__(self, "_root_identity", root_identity)
        object.__setattr__(self, "_generation_provenance", generation_provenance)
        object.__setattr__(self, "_root_descriptor", root_descriptor)
        object.__setattr__(self, "_generation_pin", generation_pin)
        object.__setattr__(self, "_failure_factory", failure_factory)
        object.__setattr__(self, "_creator_pid", os.getpid())
        object.__setattr__(self, "_creator_thread_token", current_thread_token())
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_closed", False)

    @property
    def session_id(self) -> str:
        """Return the validated content-free story-session identity."""

        return self._session_id

    @property
    def worktree_root(self) -> Path:
        """Return the exact normalized root used only to compile confinement."""

        return self._worktree_root

    @property
    def generation_provenance(self) -> str:
        """Return the content-free physical-generation digest."""

        return self._generation_provenance

    def continuation_digest(self, domain: str, existing_digest: str) -> str:
        """Bind an existing continuation input to this physical generation."""

        self.require_current()
        if (
            type(domain) is not str
            or not domain
            or type(existing_digest) is not str
            or not existing_digest
        ):
            raise TypeError("provider continuation digest inputs are invalid")
        encoded = json.dumps(
            [
                "story-provider-continuation-generation-v1",
                domain,
                existing_digest,
                self._generation_provenance,
            ],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        return _PROVENANCE_PREFIX + hashlib.sha256(encoded).hexdigest()

    def require_current(self) -> None:
        """Require retained authority and the named root to remain exact."""

        self._require_creator()
        try:
            with self._lock:
                if self._closed or self._root_descriptor < 0:
                    raise OSError
                descriptor = self._root_descriptor
                if _directory_identity(os.fstat(descriptor)) != self._root_identity:
                    raise OSError
            self._generation_pin.require_current()
            named = os.stat(self._worktree_root, follow_symlinks=False)
            if _directory_identity(named) != self._root_identity:
                raise OSError
        except BaseException as failure:
            if not isinstance(failure, Exception):
                raise
            raise self._binding_failure() from None

    def duplicate_launch_descriptor(self) -> int:
        """Duplicate exact root authority for one provider-process transfer."""

        self.require_current()
        descriptor = -1
        try:
            with self._lock:
                if self._closed:
                    raise OSError
                descriptor = _duplicate_descriptor_at_least_three(
                    self._root_descriptor
                )
            os.set_inheritable(descriptor, False)
            if _directory_identity(os.fstat(descriptor)) != self._root_identity:
                raise OSError
            self.require_current()
            return descriptor
        except BaseException as failure:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as cleanup_failure:
                    _annotate_cleanup(failure, cleanup_failure)
            if not isinstance(failure, Exception):
                raise
            raise self._binding_failure() from None

    def duplicate_validation_descriptor(self) -> int:
        """Duplicate the exact root only for package-internal read validation."""

        self.require_current()
        descriptor = -1
        try:
            with self._lock:
                if self._closed:
                    raise OSError
                descriptor = _duplicate_descriptor_at_least_three(
                    self._root_descriptor
                )
            os.set_inheritable(descriptor, False)
            if _directory_identity(os.fstat(descriptor)) != self._root_identity:
                raise OSError
            self.require_current()
            return descriptor
        except BaseException as failure:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as cleanup_failure:
                    _annotate_cleanup(failure, cleanup_failure)
            if not isinstance(failure, Exception):
                raise
            raise self._binding_failure() from None

    def close(self, *, failure: BaseException | None = None) -> None:
        """Release owned descriptors once without replacing a primary failure."""

        descriptor = -1
        pin: GitSessionGenerationPin | None = None
        if self._creator_pid != os.getpid():
            descriptor = self._root_descriptor
            pin = self._generation_pin
            object.__setattr__(self, "_root_descriptor", -1)
            object.__setattr__(self, "_generation_pin", None)
            object.__setattr__(self, "_closed", True)
        else:
            with self._lock:
                if self._closed:
                    return
                descriptor = self._root_descriptor
                pin = self._generation_pin
                object.__setattr__(self, "_root_descriptor", -1)
                object.__setattr__(self, "_generation_pin", None)
                object.__setattr__(self, "_closed", True)
        cleanup_failure: BaseException | None = None
        try:
            if descriptor >= 0:
                os.close(descriptor)
        except BaseException as exc:
            cleanup_failure = exc
        try:
            if pin is not None:
                pin.close()
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = exc
            else:
                _annotate_cleanup(cleanup_failure, exc)
        if cleanup_failure is None:
            return
        if failure is not None:
            _annotate_cleanup(failure, cleanup_failure)
            return
        raise cleanup_failure

    def _require_creator(self) -> None:
        if (
            self._creator_pid != os.getpid()
            or self._creator_thread_token is not current_thread_token()
        ):
            raise self._binding_failure() from None

    def _binding_failure(self) -> BaseException:
        failure = self._failure_factory()
        if not isinstance(failure, BaseException):
            return RuntimeError("provider execution workspace is unavailable")
        return failure

    def __repr__(self) -> str:
        return "ProviderExecutionWorkspace(<opaque>)"

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("provider execution workspaces are immutable")

    def __delattr__(self, name: str) -> None:
        del name
        raise AttributeError("provider execution workspaces are immutable")

    def __copy__(self) -> object:
        raise TypeError("provider execution workspaces cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("provider execution workspaces cannot be copied")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("provider execution workspaces cannot be serialized")


def mint_provider_execution_workspace(
    *,
    session_id: str,
    worktree_root: Path,
    root_identity: tuple[int, int],
    generation_provenance: str,
    root_descriptor: int,
    generation_pin: GitSessionGenerationPin,
    failure_factory: Callable[[], BaseException],
) -> ProviderExecutionWorkspace:
    """Package-private mint called only by the session-artifact owner."""

    return ProviderExecutionWorkspace(
        _MINT_AUTHORITY,
        session_id=session_id,
        worktree_root=worktree_root,
        root_identity=root_identity,
        generation_provenance=generation_provenance,
        root_descriptor=root_descriptor,
        generation_pin=generation_pin,
        failure_factory=failure_factory,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int] | None:
    if not stat.S_ISDIR(value.st_mode):
        return None
    identity = (value.st_dev, value.st_ino)
    return identity if _valid_identity(identity) else None


def _duplicate_descriptor_at_least_three(descriptor: int) -> int:
    """Duplicate without ever replacing an unrelated standard descriptor."""

    if os.name != "posix":  # pragma: no cover - provider launch is POSIX-only
        duplicated = os.dup(descriptor)
        if duplicated < 3:
            os.close(duplicated)
            raise OSError("unable to allocate a non-standard descriptor")
        return duplicated
    import fcntl

    command = getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD)
    return fcntl.fcntl(descriptor, command, 3)


def _valid_identity(value: object) -> bool:
    return bool(
        type(value) is tuple
        and len(value) == 2
        and all(type(item) is int and item > 0 for item in value)
    )


def _annotate_cleanup(failure: BaseException, cleanup: BaseException) -> None:
    try:
        failure.add_note(
            "provider execution workspace cleanup failed: "
            f"{type(cleanup).__name__}"
        )
    except BaseException:
        pass
