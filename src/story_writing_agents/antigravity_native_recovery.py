"""Bounded Antigravity native recovery evidence for one invocation."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import story_writing_agents.antigravity_recovery_matching as antigravity_recovery_matching

from .provider_adapter import ProviderInvocationError

__all__ = [
    "NativeRecoveryBaseline",
    "SAFE_CONVERSATION_ID",
    "has_current_finish",
    "native_recovery_baseline",
]

SAFE_CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_NATIVE_TRACE_MAX_BYTES = 4 * 1024 * 1024
_NATIVE_SNAPSHOT_MAX_ENTRIES = 1024


class _StateRootSource(Protocol):
    @property
    def state_root(self) -> Path: ...


@dataclass(frozen=True, slots=True)
class NativeRecoveryBaseline:
    """Content-free identities captured immediately before one native call."""

    directory_identities: tuple[tuple[int, int], ...]
    thread_id: str | None
    conversation_ids: frozenset[str] = frozenset()
    prefix_bytes: int = 0
    prefix_sha256: str | None = None
    prior_steps: int = 0


def _native_identity(
    value: os.stat_result,
    *,
    directory: bool,
) -> tuple[int, int]:
    valid_kind = (
        stat.S_ISDIR(value.st_mode)
        if directory
        else stat.S_ISREG(value.st_mode)
    )
    if (
        not valid_kind
        or (not directory and value.st_nlink != 1)
        or value.st_dev <= 0
        or value.st_ino <= 0
    ):
        raise antigravity_recovery_matching.NativeEvidenceUnavailable
    return value.st_dev, value.st_ino


def _close_native_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _open_native_child(
    stack: ExitStack,
    parent: int,
    name: str,
    *,
    directory: bool,
) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= os.O_DIRECTORY
    descriptor = os.open(name, flags, dir_fd=parent)
    stack.callback(_close_native_descriptor, descriptor)
    identity = _native_identity(os.fstat(descriptor), directory=directory)
    if identity != _native_identity(
        os.stat(name, dir_fd=parent, follow_symlinks=False),
        directory=directory,
    ):
        raise antigravity_recovery_matching.NativeEvidenceUnavailable
    return descriptor


@contextmanager
def _native_brain_directory(
    state_source: _StateRootSource,
    provider_directory: Path,
) -> Iterator[tuple[int | None, tuple[tuple[int, int], ...]]]:
    """Traverse below the owned state anchor through no-follow descriptors."""

    if (
        not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_DIRECTORY", 0)
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or os.scandir not in os.supports_fd
    ):
        raise antigravity_recovery_matching.NativeEvidenceUnavailable
    parts = provider_directory.relative_to(state_source.state_root).parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise antigravity_recovery_matching.NativeEvidenceUnavailable
    with ExitStack() as stack:
        descriptor = os.open(
            state_source.state_root,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0),
        )
        stack.callback(_close_native_descriptor, descriptor)
        identities = [_native_identity(os.fstat(descriptor), directory=True)]
        for index, part in enumerate((*parts, "antigravity-cli", "brain")):
            try:
                descriptor = _open_native_child(
                    stack, descriptor, part, directory=True
                )
            except FileNotFoundError:
                if index < len(parts):
                    raise
                yield None, tuple(identities)
                return
            identities.append(
                _native_identity(os.fstat(descriptor), directory=True)
            )
        yield descriptor, tuple(identities)


def _read_native_trace(brain: int, thread_id: str) -> bytes:
    """Read one bounded, stable regular trace without following native links."""

    if SAFE_CONVERSATION_ID.fullmatch(thread_id) is None:
        raise antigravity_recovery_matching.NativeEvidenceUnavailable
    with ExitStack() as stack:
        directory = brain
        for part in (thread_id, ".system_generated", "logs"):
            directory = _open_native_child(
                stack, directory, part, directory=True
            )
        descriptor = _open_native_child(
            stack, directory, "transcript_full.jsonl", directory=False
        )
        before = os.fstat(descriptor)
        if not 0 < before.st_size <= _NATIVE_TRACE_MAX_BYTES:
            raise antigravity_recovery_matching.NativeEvidenceUnavailable
        remaining = _NATIVE_TRACE_MAX_BYTES + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        pathname = os.stat(
            "transcript_full.jsonl", dir_fd=directory, follow_symlinks=False
        )
        if (
            len(content) != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or _native_identity(before, directory=False)
            != _native_identity(after, directory=False)
            or _native_identity(after, directory=False)
            != _native_identity(pathname, directory=False)
        ):
            raise antigravity_recovery_matching.NativeEvidenceUnavailable
        return content


def native_recovery_baseline(
    state_source: _StateRootSource,
    provider_directory: Path,
    thread_id: str | None,
) -> NativeRecoveryBaseline | None:
    """Snapshot current-call identity without making recovery a prerequisite."""

    try:
        with _native_brain_directory(state_source, provider_directory) as (brain, identities):
            if thread_id is None:
                names: set[str] = set()
                if brain is not None:
                    with os.scandir(brain) as entries:
                        for entry in entries:
                            if len(names) >= _NATIVE_SNAPSHOT_MAX_ENTRIES:
                                raise antigravity_recovery_matching.NativeEvidenceUnavailable
                            names.add(entry.name)
                return NativeRecoveryBaseline(identities, None, frozenset(names))
            if brain is None:
                return None
            content = _read_native_trace(brain, thread_id)
            steps = antigravity_recovery_matching.native_steps(content)
            return NativeRecoveryBaseline(
                identities,
                thread_id,
                prefix_bytes=len(content),
                prefix_sha256=hashlib.sha256(content).hexdigest(),
                prior_steps=len(steps),
            )
    except (
        OSError,
        ValueError,
        TypeError,
        NotImplementedError,
        ProviderInvocationError,
        RecursionError,
    ):
        return None


def has_current_finish(
    state_source: _StateRootSource,
    provider_directory: Path,
    baseline: NativeRecoveryBaseline | None,
    thread_id: str,
    prompt: str,
    structured_output: dict[str, Any],
    native_error: str,
) -> bool:
    """Require a new matching request/finish/completion in one exact trace."""

    if baseline is None:
        return False
    if baseline.thread_id is None:
        if thread_id in baseline.conversation_ids:
            return False
    elif thread_id != baseline.thread_id:
        return False
    try:
        with _native_brain_directory(state_source, provider_directory) as (brain, identities):
            if (
                brain is None
                or identities[:len(baseline.directory_identities)]
                != baseline.directory_identities
            ):
                return False
            content = _read_native_trace(brain, thread_id)
        if baseline.thread_id is not None and (
            len(content) <= baseline.prefix_bytes
            or hashlib.sha256(content[:baseline.prefix_bytes]).hexdigest()
            != baseline.prefix_sha256
        ):
            return False
        steps = antigravity_recovery_matching.native_steps(content)[
            baseline.prior_steps:
        ]
        return antigravity_recovery_matching.matches_current_finish(
            steps, prompt, structured_output, native_error
        ) and False
    except (
        OSError,
        ValueError,
        TypeError,
        NotImplementedError,
        ProviderInvocationError,
        RecursionError,
    ):
        return False
