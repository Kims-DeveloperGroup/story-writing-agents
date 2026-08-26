"""Identity-bound disposable generation storage for conversation RAG."""

from __future__ import annotations

import errno
import hashlib
import itertools
import json
import math
import os
import re
import secrets
import stat
import threading
import time
import weakref
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal, NoReturn, Protocol, Self

from .conversation_rag import (
    CONVERSATION_RAG_POLICY,
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagEmbeddingIdentity,
    ConversationRagEmbeddingVector,
    ConversationRagError,
    ConversationRagProjectionCheckpoint,
    ConversationRagProjectionCheckpointEntry,
    ConversationRagProjectionRequest,
    ConversationRagProjectionResult,
    ConversationRagProjectionState,
    ConversationRagProjectionStateAvailability,
    ConversationRagProviderCandidate,
    ConversationRagRetrievalRequest,
    ConversationRagSourceReference,
    ConversationRagVectorRecord,
)
from .workspace import (
    Workspace,
    open_workspace_root_directory,
    verify_workspace_root_directory,
)

_fcntl: Any
try:  # pragma: no cover - platform availability is exercised by capability tests.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - unsupported non-POSIX platform.
    _fcntl = None


CONVERSATION_RAG_STORE_SCHEMA_ID = "story-conversation-rag-store-v1"

_RAG_DIRECTORY = "conversation_rag"
_SESSIONS_DIRECTORY = "sessions"
_GENERATIONS_DIRECTORY = "generations"
_GLOBAL_LOCK = ".lock"
_SESSION_LOCK = ".lock"
_ACTIVE_FILE = "active.json"
_STATUS_FILE = "status.json"
_GENERATION_FILE = "generation.json"
_MANIFEST_FILE = "generic-manifest.json"
_SOURCE_MAP_FILE = "source-map.jsonl"
_VECTORS_FILE = "vectors.jsonl"
_MUTATION_SOURCE_FILE = ".source-map.build"
_MUTATION_VECTOR_FILE = ".vectors.build"

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_BUILD_NAME = re.compile(r"build-[0-9a-f]{32}")
_GENERATION_NAME = re.compile(r"gen-[0-9a-f]{64}")
_POINTER_TEMP = re.compile(r"\.(?:active|status)\.json\.[0-9a-f]{32}\.tmp")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

_LOCK_TIMEOUT_SECONDS = 1.0
_LOCK_RETRY_SECONDS = 0.01
_MAX_RECORDS = 65_536
_MAX_SOURCE_UTF8_BYTES = 32 * 1024 * 1024
_MAX_POINTER_BYTES = 64 * 1024
_MAX_METADATA_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_SOURCE_MAP_BYTES = 64 * 1024 * 1024
_MAX_VECTORS_BYTES = 384 * 1024 * 1024
_MAX_GENERATION_BYTES = 512 * 1024 * 1024
_MAX_JSONL_RECORD_BYTES = 64 * 1024

_SOURCE_DIGEST_DOMAIN = "generic-rag:projection-source:v1"
_FRAGMENT_ID_DOMAIN = "generic-rag:fragment-id:v1"
_CHECKPOINT_DOMAIN = "generic-rag:projection-checkpoint:v1"
_GENERATION_DIGEST_DOMAIN = "story-conversation-rag-generation-v1"


class ConversationRagStoreStatus(StrEnum):
    """Content-free observed state of one disposable session projection."""

    MISSING = "missing"
    READY = "ready"
    STALE = "stale"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    PROVIDER_FAILED = "provider_failed"


class ConversationRagStoreFailure(StrEnum):
    """Fixed failure categories for generation-store operations."""

    UNSUPPORTED = "unsupported"
    BUSY = "busy"
    UNSAFE = "unsafe"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    PROVIDER_FAILED = "provider_failed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    OPERATION_FAILED = "operation_failed"


_FAILURE_MESSAGES = {
    ConversationRagStoreFailure.UNSUPPORTED: (
        "conversation RAG storage is unsupported on this filesystem"
    ),
    ConversationRagStoreFailure.BUSY: "conversation RAG storage is busy",
    ConversationRagStoreFailure.UNSAFE: "conversation RAG storage is unsafe",
    ConversationRagStoreFailure.CORRUPT: "conversation RAG storage is corrupt",
    ConversationRagStoreFailure.INCOMPATIBLE: (
        "conversation RAG storage is incompatible"
    ),
    ConversationRagStoreFailure.PROVIDER_FAILED: (
        "conversation RAG provider operation failed"
    ),
    ConversationRagStoreFailure.CONFIRMATION_REQUIRED: (
        "conversation RAG purge requires explicit confirmation"
    ),
    ConversationRagStoreFailure.OPERATION_FAILED: (
        "conversation RAG storage operation failed"
    ),
}


def _exact_nonnegative_integer(label: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    assert isinstance(value, int)
    if value < 0:
        raise ValueError(f"{label} must be nonnegative")
    return value


def _identifier(label: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    assert isinstance(value, str)
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ConversationRagStoreCounts:
    """Safe bounded counts for one inspected derived-state subtree."""

    conversations: int = 0
    events: int = 0
    documents: int = 0
    fragments: int = 0
    source_map_records: int = 0
    vector_records: int = 0
    generations: int = 0
    orphan_generations: int = 0
    source_utf8_bytes: int = 0
    payload_bytes: int = 0
    generation_bytes: int = 0

    def __post_init__(self) -> None:
        for label, value in (
            ("conversation count", self.conversations),
            ("event count", self.events),
            ("document count", self.documents),
            ("fragment count", self.fragments),
            ("source-map record count", self.source_map_records),
            ("vector record count", self.vector_records),
            ("generation count", self.generations),
            ("orphan generation count", self.orphan_generations),
            ("source UTF-8 byte count", self.source_utf8_bytes),
            ("payload byte count", self.payload_bytes),
            ("generation byte count", self.generation_bytes),
        ):
            _exact_nonnegative_integer(label, value)
        if self.conversations > CONVERSATION_RAG_POLICY.max_conversations:
            raise ValueError("conversation count exceeds the RAG budget")
        if self.events > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("event count exceeds the RAG budget")
        if self.documents > CONVERSATION_RAG_POLICY.max_events:
            raise ValueError("document count exceeds the RAG budget")
        if self.fragments > _MAX_RECORDS:
            raise ValueError("fragment count exceeds the RAG budget")
        if (
            self.source_map_records != self.fragments
            or self.vector_records != self.fragments
        ):
            raise ValueError("stored record counts must match fragment count")
        if self.source_utf8_bytes > _MAX_SOURCE_UTF8_BYTES:
            raise ValueError("source byte count exceeds the RAG budget")
        if self.payload_bytes > _MAX_GENERATION_BYTES:
            raise ValueError("payload byte count exceeds the RAG budget")
        if self.generation_bytes > _MAX_GENERATION_BYTES:
            raise ValueError("generation byte count exceeds the RAG budget")


@dataclass(frozen=True, slots=True)
class ConversationRagStoreSnapshot:
    """One content-free active-generation inspection result."""

    session_id: str
    status: ConversationRagStoreStatus
    state: ConversationRagProjectionState
    provider_id: str | None
    generation_id: str | None
    counts: ConversationRagStoreCounts

    def __post_init__(self) -> None:
        _identifier("session ID", self.session_id)
        if type(self.status) is not ConversationRagStoreStatus:
            raise TypeError("store status must use the exact enum")
        if type(self.state) is not ConversationRagProjectionState:
            raise TypeError("store state must use the exact projection state")
        if self.provider_id is not None:
            _identifier("RAG provider ID", self.provider_id)
        if self.generation_id is not None and (
            type(self.generation_id) is not str
            or _GENERATION_NAME.fullmatch(self.generation_id) is None
        ):
            raise ValueError("generation ID is invalid")
        if type(self.counts) is not ConversationRagStoreCounts:
            raise TypeError("store counts must use the exact value")
        if (
            self.state.availability
            is ConversationRagProjectionStateAvailability.PRESENT
        ):
            if self.provider_id is None or self.generation_id is None:
                raise ValueError("present store state requires active identities")
        elif self.generation_id is not None:
            raise ValueError("non-present store state cannot name a generation")


class ConversationRagStoreError(ConversationRagError):
    """Sanitized actionable failure from the disposable generation store."""

    failure: ConversationRagStoreFailure

    def __init__(self, failure: ConversationRagStoreFailure) -> None:
        if type(failure) is not ConversationRagStoreFailure:
            raise TypeError("store failure must use the exact enum")
        self.failure = failure
        Exception.__init__(self, _FAILURE_MESSAGES[failure])


class _StoreFault(ConversationRagStoreError):
    __slots__ = ()

    def __init__(self, failure: ConversationRagStoreFailure) -> None:
        ConversationRagStoreError.__init__(self, failure)


def _fault(failure: ConversationRagStoreFailure) -> NoReturn:
    raise _StoreFault(failure)


def _raise_store(failure: ConversationRagStoreFailure) -> NoReturn:
    raise ConversationRagStoreError(failure)


def _os_failure(value: OSError) -> ConversationRagStoreFailure:
    if value.errno in {
        getattr(errno, "ENOTSUP", errno.ENOSYS),
        errno.ENOSYS,
    }:
        return ConversationRagStoreFailure.UNSUPPORTED
    if value.errno in {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
        getattr(errno, "EDEADLK", errno.EAGAIN),
    }:
        return ConversationRagStoreFailure.BUSY
    if value.errno in {
        errno.ELOOP,
        errno.ENOTDIR,
        errno.EPERM,
    }:
        return ConversationRagStoreFailure.UNSAFE
    return ConversationRagStoreFailure.OPERATION_FAILED


@dataclass(slots=True, weakref_slot=True)
class _ProcessLock:
    lock: threading.Lock


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: weakref.WeakValueDictionary[tuple[str, str], _ProcessLock] = (
    weakref.WeakValueDictionary()
)


def _acquire_process_lock(workspace: Workspace, session_id: str) -> _ProcessLock:
    key = (os.fspath(workspace.root), session_id)
    with _PROCESS_LOCKS_GUARD:
        holder = _PROCESS_LOCKS.get(key)
        if holder is None:
            holder = _ProcessLock(threading.Lock())
            _PROCESS_LOCKS[key] = holder
    if not holder.lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
        _fault(ConversationRagStoreFailure.BUSY)
    return holder


def _require_capabilities() -> None:
    if _fcntl is None:
        _fault(ConversationRagStoreFailure.UNSUPPORTED)
    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow = getattr(os, "supports_follow_symlinks", ())
    supports_fd = getattr(os, "supports_fd", ())
    required_dir_fd = (
        os.open,
        os.mkdir,
        os.stat,
        os.unlink,
        os.rename,
        os.rmdir,
    )
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(operation not in supports_dir_fd for operation in required_dir_fd)
        or os.stat not in supports_follow
        or os.listdir not in supports_fd
    ):
        _fault(ConversationRagStoreFailure.UNSUPPORTED)


def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags


def _file_flags(*, write: bool) -> int:
    flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags


def _identity(value: os.stat_result) -> tuple[int, int]:
    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        _fault(ConversationRagStoreFailure.UNSAFE)
    return device, inode


def _verify_directory(
    parent: int,
    name: str,
    descriptor: int,
    *,
    owned: bool,
) -> None:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
        _fault(ConversationRagStoreFailure.UNSAFE)
    if opened.st_nlink < 1 or named.st_nlink < 1:
        _fault(ConversationRagStoreFailure.UNSAFE)
    if owned and (
        stat.S_IMODE(opened.st_mode) != 0o700 or stat.S_IMODE(named.st_mode) != 0o700
    ):
        _fault(ConversationRagStoreFailure.UNSAFE)
    if _identity(opened) != _identity(named):
        _fault(ConversationRagStoreFailure.UNSAFE)


def _open_directory(
    parent: int,
    name: str,
    *,
    create: bool,
    owned: bool = True,
) -> int | None:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError:
        if create:
            raise
        return None
    try:
        os.set_inheritable(descriptor, False)
    except (AttributeError, OSError):
        pass
    try:
        _verify_directory(parent, name, descriptor, owned=owned)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_regular(parent: int, name: str, descriptor: int) -> os.stat_result:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
        _fault(ConversationRagStoreFailure.UNSAFE)
    if opened.st_nlink != 1 or named.st_nlink != 1:
        _fault(ConversationRagStoreFailure.UNSAFE)
    if stat.S_IMODE(opened.st_mode) != 0o600 or stat.S_IMODE(named.st_mode) != 0o600:
        _fault(ConversationRagStoreFailure.UNSAFE)
    if _identity(opened) != _identity(named):
        _fault(ConversationRagStoreFailure.UNSAFE)
    return opened


def _open_regular(
    parent: int,
    name: str,
    *,
    write: bool,
    create: bool = False,
) -> int:
    flags = _file_flags(write=write)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(name, flags, 0o600, dir_fd=parent)
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        _verify_regular(parent, name, descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_or_create_lock(parent: int, name: str) -> int:
    try:
        return _open_regular(parent, name, write=True, create=True)
    except FileExistsError:
        return _open_regular(parent, name, write=True)


def _acquire_file_lock(descriptor: int) -> None:
    assert _fcntl is not None
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in {
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EDEADLK", errno.EAGAIN),
            }:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fault(ConversationRagStoreFailure.BUSY)
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))


def _release_file_lock(descriptor: int) -> None:
    if _fcntl is not None:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        except OSError:
            pass


def _close_descriptors(descriptors: Iterable[int]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


@dataclass(slots=True)
class _SessionScope:
    workspace: Workspace
    session_id: str
    process_lock: _ProcessLock
    root: int
    agent_states: int
    orchestrator_state: int
    rag: int
    sessions: int
    session: int
    session_lock: int
    generations: int
    closed: bool = False

    def verify(self) -> None:
        if self.closed:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        try:
            verify_workspace_root_directory(self.workspace, self.root)
        except BaseException as exc:
            if isinstance(exc, OSError) or not isinstance(exc, Exception):
                raise
            _fault(ConversationRagStoreFailure.UNSAFE)
        _verify_directory(
            self.root,
            "agent_states",
            self.agent_states,
            owned=False,
        )
        _verify_directory(
            self.agent_states,
            "orchestrator_state",
            self.orchestrator_state,
            owned=False,
        )
        _verify_directory(
            self.orchestrator_state,
            _RAG_DIRECTORY,
            self.rag,
            owned=True,
        )
        _verify_directory(
            self.rag,
            _SESSIONS_DIRECTORY,
            self.sessions,
            owned=True,
        )
        _verify_directory(
            self.sessions,
            self.session_id,
            self.session,
            owned=True,
        )
        _verify_regular(self.session, _SESSION_LOCK, self.session_lock)
        _verify_directory(
            self.session,
            _GENERATIONS_DIRECTORY,
            self.generations,
            owned=True,
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        _release_file_lock(self.session_lock)
        _close_descriptors(
            (
                self.generations,
                self.session_lock,
                self.session,
                self.sessions,
                self.rag,
                self.orchestrator_state,
                self.agent_states,
                self.root,
            )
        )
        if self.process_lock.lock.locked():
            self.process_lock.lock.release()


def _open_session_scope(
    workspace: Workspace,
    session_id: str,
    *,
    create: bool,
) -> _SessionScope | None:
    _require_capabilities()
    process_lock = _acquire_process_lock(workspace, session_id)
    owned: list[int] = []
    global_lock: int | None = None
    global_locked = False
    try:
        try:
            root = open_workspace_root_directory(workspace)
        except BaseException as exc:
            if isinstance(exc, OSError) or not isinstance(exc, Exception):
                raise
            _fault(ConversationRagStoreFailure.UNSAFE)
        owned.append(root)
        agent_states = _open_directory(
            root,
            "agent_states",
            create=create,
            owned=False,
        )
        if agent_states is None:
            return None
        owned.append(agent_states)
        orchestrator_state = _open_directory(
            agent_states,
            "orchestrator_state",
            create=create,
            owned=False,
        )
        if orchestrator_state is None:
            return None
        owned.append(orchestrator_state)
        rag = _open_directory(
            orchestrator_state,
            _RAG_DIRECTORY,
            create=create,
        )
        if rag is None:
            return None
        owned.append(rag)
        if create:
            global_lock = _open_or_create_lock(rag, _GLOBAL_LOCK)
        else:
            try:
                global_lock = _open_regular(rag, _GLOBAL_LOCK, write=True)
            except FileNotFoundError:
                _fault(ConversationRagStoreFailure.CORRUPT)
        _acquire_file_lock(global_lock)
        global_locked = True
        sessions = _open_directory(rag, _SESSIONS_DIRECTORY, create=create)
        if sessions is None:
            return None
        owned.append(sessions)
        session = _open_directory(sessions, session_id, create=create)
        if session is None:
            return None
        owned.append(session)
        if create:
            session_lock = _open_or_create_lock(session, _SESSION_LOCK)
        else:
            try:
                session_lock = _open_regular(session, _SESSION_LOCK, write=True)
            except FileNotFoundError:
                _fault(ConversationRagStoreFailure.CORRUPT)
        owned.append(session_lock)
        _acquire_file_lock(session_lock)
        generations = _open_directory(
            session,
            _GENERATIONS_DIRECTORY,
            create=create,
        )
        if generations is None:
            _fault(ConversationRagStoreFailure.CORRUPT)
        owned.append(generations)
        _release_file_lock(global_lock)
        global_locked = False
        os.close(global_lock)
        global_lock = None
        scope = _SessionScope(
            workspace,
            session_id,
            process_lock,
            root,
            agent_states,
            orchestrator_state,
            rag,
            sessions,
            session,
            session_lock,
            generations,
        )
        owned.clear()
        scope.verify()
        return scope
    finally:
        if global_lock is not None:
            if global_locked:
                _release_file_lock(global_lock)
            try:
                os.close(global_lock)
            except OSError:
                pass
        if owned:
            _close_descriptors(reversed(owned))
            if process_lock.lock.locked():
                process_lock.lock.release()


def _canonical_json(
    value: object,
    *,
    failure: ConversationRagStoreFailure = (
        ConversationRagStoreFailure.OPERATION_FAILED
    ),
) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (rendered + "\n").encode("utf-8", errors="strict")
    except (
        TypeError,
        ValueError,
        UnicodeEncodeError,
        OverflowError,
        RecursionError,
    ):
        _fault(failure)
    raise AssertionError("unreachable")


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _parse_canonical_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    if _canonical_json(value, failure=ConversationRagStoreFailure.CORRUPT) != raw:
        _fault(ConversationRagStoreFailure.CORRUPT)
    return value


def _read_file(parent: int, name: str, maximum: int) -> bytes:
    descriptor = _open_regular(parent, name, write=False)
    try:
        initial = _verify_regular(parent, name, descriptor)
        if initial.st_size > maximum:
            _fault(ConversationRagStoreFailure.CORRUPT)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fault(ConversationRagStoreFailure.CORRUPT)
            chunks.append(chunk)
        final = _verify_regular(parent, name, descriptor)
        if _identity(initial) != _identity(final) or final.st_size != total:
            _fault(ConversationRagStoreFailure.UNSAFE)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_json(parent: int, name: str, maximum: int) -> object:
    return _parse_canonical_json(_read_file(parent, name, maximum))


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        offset += written


def _write_new_bytes(parent: int, name: str, payload: bytes, maximum: int) -> None:
    if len(payload) > maximum:
        _fault(ConversationRagStoreFailure.OPERATION_FAILED)
    descriptor = _open_regular(parent, name, write=True, create=True)
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        final = _verify_regular(parent, name, descriptor)
        if final.st_size != len(payload):
            _fault(ConversationRagStoreFailure.UNSAFE)
    finally:
        os.close(descriptor)


def _replace_json(parent: int, target: str, value: object) -> None:
    payload = _canonical_json(value)
    if len(payload) > _MAX_POINTER_BYTES:
        _fault(ConversationRagStoreFailure.OPERATION_FAILED)
    temporary = f".{target}.{secrets.token_hex(16)}.tmp"
    prepared = False
    try:
        _write_new_bytes(parent, temporary, payload, _MAX_POINTER_BYTES)
        # Persist the fully written temporary entry before the commit rename.
        # The rename below must remain the final callable operation: after it
        # succeeds, callers may not observe a failure for an already-committed
        # active pointer.
        os.fsync(parent)
        prepared = True
    finally:
        if not prepared:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
    os.rename(
        temporary,
        target,
        src_dir_fd=parent,
        dst_dir_fd=parent,
    )


def _exact_dict(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != fields:
        _fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, dict)
    return value


def _exact_string(value: object) -> str:
    if type(value) is not str:
        _fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, str)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fault(ConversationRagStoreFailure.CORRUPT)
    return value


def _exact_integer(value: object) -> int:
    if type(value) is not int:
        _fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(value, int)
    return value


def _document_value(value: ConversationRagDocumentIdentity) -> dict[str, object]:
    return {
        "conversation_id": value.conversation_id,
        "document_id": value.key.document_id,
        "end_sequence": value.end_sequence,
        "end_turn_id": value.end_turn_id,
        "revision_id": value.revision_id,
        "session_id": value.key.session_id,
        "start_sequence": value.start_sequence,
        "start_turn_id": value.start_turn_id,
    }


_DOCUMENT_FIELDS = frozenset(
    {
        "session_id",
        "document_id",
        "conversation_id",
        "start_turn_id",
        "end_turn_id",
        "start_sequence",
        "end_sequence",
        "revision_id",
    }
)


def _document_from(value: object) -> ConversationRagDocumentIdentity:
    row = _exact_dict(value, _DOCUMENT_FIELDS)
    try:
        return ConversationRagDocumentIdentity(
            ConversationRagDocumentKey(
                _exact_string(row["session_id"]),
                _exact_string(row["document_id"]),
            ),
            _exact_string(row["conversation_id"]),
            _exact_string(row["start_turn_id"]),
            _exact_string(row["end_turn_id"]),
            _exact_integer(row["start_sequence"]),
            _exact_integer(row["end_sequence"]),
            _exact_string(row["revision_id"]),
        )
    except (TypeError, ValueError):
        _fault(ConversationRagStoreFailure.CORRUPT)
    raise AssertionError("unreachable")


def _sha256_fields(fields: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for field in fields:
        encoded = field.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, "big", signed=False))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def _source_digest(text: str) -> str:
    return _sha256_fields(
        (
            _SOURCE_DIGEST_DOMAIN,
            "text",
            text,
            "attributes_count",
            "0",
        )
    )


def _fragment_id(
    document: ConversationRagDocumentIdentity,
    start: int,
    end: int,
) -> str:
    return _sha256_fields(
        (
            _FRAGMENT_ID_DOMAIN,
            "corpus_id",
            document.key.session_id,
            "document_id",
            document.key.document_id,
            "revision_id",
            document.revision_id,
            "start",
            str(start),
            "end",
            str(end),
        )
    )


def _checkpoint_token(
    session_id: str,
    schema_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    entries: tuple[ConversationRagProjectionCheckpointEntry, ...],
) -> str:
    fields = [
        _CHECKPOINT_DOMAIN,
        "corpus_id",
        session_id,
        "schema_id",
        schema_id,
        "embedding_model_id",
        embedding.model_id,
        "embedding_dimensions",
        str(embedding.dimensions),
        "max_fragment_codepoints",
        str(CONVERSATION_RAG_POLICY.max_fragment_codepoints),
        "overlap_codepoints",
        str(CONVERSATION_RAG_POLICY.fragment_overlap_codepoints),
        "entry_count",
        str(len(entries)),
    ]
    for entry in entries:
        fields.extend(
            (
                "document_id",
                entry.document.key.document_id,
                "revision_id",
                entry.document.revision_id,
                "source_digest",
                entry.source_digest,
                "fragment_count",
                str(entry.fragment_count),
            )
        )
    return _sha256_fields(tuple(fields))


def _generation_digest(
    manifest_digest: str,
    source_map_digest: str,
    vectors_digest: str,
) -> str:
    for value in (manifest_digest, source_map_digest, vectors_digest):
        if _SHA256.fullmatch(value) is None:
            _fault(ConversationRagStoreFailure.CORRUPT)
    return _sha256_fields(
        (
            _GENERATION_DIGEST_DOMAIN,
            "generic_manifest_digest",
            manifest_digest,
            "source_map_digest",
            source_map_digest,
            "vectors_digest",
            vectors_digest,
        )
    )


def _fragment_count(text: str) -> int:
    if not text:
        return 0
    width = CONVERSATION_RAG_POLICY.max_fragment_codepoints
    step = width - CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
    if len(text) <= width:
        return 1
    return 1 + math.ceil((len(text) - width) / step)


def _target_checkpoint(
    request: ConversationRagProjectionRequest,
) -> ConversationRagProjectionCheckpoint:
    entries = tuple(
        ConversationRagProjectionCheckpointEntry(
            document.identity,
            _source_digest(document.text),
            _fragment_count(document.text),
        )
        for document in request.documents
    )
    token = _checkpoint_token(
        request.session_id,
        request.schema_id,
        request.embedding,
        entries,
    )
    return ConversationRagProjectionCheckpoint(
        request.session_id,
        request.schema_id,
        request.embedding,
        CONVERSATION_RAG_POLICY.max_fragment_codepoints,
        CONVERSATION_RAG_POLICY.fragment_overlap_codepoints,
        entries,
        token,
    )


def _manifest_value(
    checkpoint: ConversationRagProjectionCheckpoint,
) -> dict[str, object]:
    return {
        "checkpoint_token": checkpoint.token,
        "chunking": {
            "max_fragment_codepoints": checkpoint.max_fragment_codepoints,
            "overlap_codepoints": checkpoint.fragment_overlap_codepoints,
        },
        "embedding": {
            "dimensions": checkpoint.embedding.dimensions,
            "model_id": checkpoint.embedding.model_id,
        },
        "entries": [
            {
                "document": _document_value(entry.document),
                "fragment_count": entry.fragment_count,
                "source_digest": entry.source_digest,
            }
            for entry in checkpoint.entries
        ],
        "projection_schema_id": checkpoint.schema_id,
        "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
        "session_id": checkpoint.session_id,
    }


_MANIFEST_FIELDS = frozenset(
    {
        "schema_id",
        "session_id",
        "projection_schema_id",
        "embedding",
        "chunking",
        "entries",
        "checkpoint_token",
    }
)


def _manifest_from(value: object) -> ConversationRagProjectionCheckpoint:
    row = _exact_dict(value, _MANIFEST_FIELDS)
    if _exact_string(row["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID:
        _fault(ConversationRagStoreFailure.INCOMPATIBLE)
    embedding_row = _exact_dict(
        row["embedding"],
        frozenset({"model_id", "dimensions"}),
    )
    chunking = _exact_dict(
        row["chunking"],
        frozenset({"max_fragment_codepoints", "overlap_codepoints"}),
    )
    entries_value = row["entries"]
    if type(entries_value) is not list:
        _fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(entries_value, list)
    entries: list[ConversationRagProjectionCheckpointEntry] = []
    try:
        embedding = ConversationRagEmbeddingIdentity(
            _exact_string(embedding_row["model_id"]),
            _exact_integer(embedding_row["dimensions"]),
        )
        for item in entries_value:
            entry = _exact_dict(
                item,
                frozenset({"document", "source_digest", "fragment_count"}),
            )
            entries.append(
                ConversationRagProjectionCheckpointEntry(
                    _document_from(entry["document"]),
                    _exact_string(entry["source_digest"]),
                    _exact_integer(entry["fragment_count"]),
                )
            )
        checkpoint = ConversationRagProjectionCheckpoint(
            _exact_string(row["session_id"]),
            _exact_string(row["projection_schema_id"]),
            embedding,
            _exact_integer(chunking["max_fragment_codepoints"]),
            _exact_integer(chunking["overlap_codepoints"]),
            tuple(entries),
            _exact_string(row["checkpoint_token"]),
        )
    except (TypeError, ValueError):
        _fault(ConversationRagStoreFailure.CORRUPT)
    if checkpoint.token != _checkpoint_token(
        checkpoint.session_id,
        checkpoint.schema_id,
        checkpoint.embedding,
        checkpoint.entries,
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    return checkpoint


def _iter_json_lines(
    parent: int,
    name: str,
    maximum: int,
) -> Generator[dict[str, object], None, None]:
    descriptor = _open_regular(parent, name, write=False)
    try:
        initial = _verify_regular(parent, name, descriptor)
        if initial.st_size > maximum:
            _fault(ConversationRagStoreFailure.CORRUPT)
        total = 0
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        try:
            while True:
                raw = handle.readline(_MAX_JSONL_RECORD_BYTES + 1)
                if not raw:
                    break
                total += len(raw)
                if (
                    len(raw) > _MAX_JSONL_RECORD_BYTES
                    or total > maximum
                    or not raw.endswith(b"\n")
                    or raw == b"\n"
                ):
                    _fault(ConversationRagStoreFailure.CORRUPT)
                value = _parse_canonical_json(raw)
                if type(value) is not dict:
                    _fault(ConversationRagStoreFailure.CORRUPT)
                assert isinstance(value, dict)
                yield value
            if total != initial.st_size:
                _fault(ConversationRagStoreFailure.UNSAFE)
            final = os.fstat(handle.fileno())
            if _identity(initial) != _identity(final) or final.st_size != total:
                _fault(ConversationRagStoreFailure.UNSAFE)
        finally:
            handle.close()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


_SOURCE_ROW_FIELDS = frozenset(
    {"document", "fragment_id", "start_codepoint", "end_codepoint", "text"}
)
_VECTOR_ROW_FIELDS = frozenset({"document_id", "revision_id", "fragment_id", "values"})


def _record_values(
    record: ConversationRagVectorRecord,
) -> tuple[dict[str, object], dict[str, object]]:
    source = record.candidate.source
    source_value: dict[str, object] = {
        "document": _document_value(source.document),
        "end_codepoint": source.end_codepoint,
        "fragment_id": source.fragment_id,
        "start_codepoint": source.start_codepoint,
        "text": record.candidate.text,
    }
    vector_value: dict[str, object] = {
        "document_id": source.document.key.document_id,
        "fragment_id": source.fragment_id,
        "revision_id": source.document.revision_id,
        "values": [float.hex(value) for value in record.embedding.values],
    }
    return source_value, vector_value


def _record_from(
    source_value: object,
    vector_value: object,
) -> ConversationRagVectorRecord:
    source_row = _exact_dict(source_value, _SOURCE_ROW_FIELDS)
    vector_row = _exact_dict(vector_value, _VECTOR_ROW_FIELDS)
    document = _document_from(source_row["document"])
    fragment_id = _exact_string(source_row["fragment_id"])
    start = _exact_integer(source_row["start_codepoint"])
    end = _exact_integer(source_row["end_codepoint"])
    if (
        _exact_string(vector_row["document_id"]) != document.key.document_id
        or _exact_string(vector_row["revision_id"]) != document.revision_id
        or _exact_string(vector_row["fragment_id"]) != fragment_id
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    values = vector_row["values"]
    if type(values) is not list:
        _fault(ConversationRagStoreFailure.CORRUPT)
    assert isinstance(values, list)
    coordinates: list[float] = []
    for encoded in values:
        text = _exact_string(encoded)
        try:
            coordinate = float.fromhex(text)
        except (ValueError, OverflowError):
            _fault(ConversationRagStoreFailure.CORRUPT)
        if not math.isfinite(coordinate) or float.hex(coordinate) != text:
            _fault(ConversationRagStoreFailure.CORRUPT)
        if coordinate == 0.0 and math.copysign(1.0, coordinate) < 0.0:
            _fault(ConversationRagStoreFailure.CORRUPT)
        coordinates.append(coordinate)
    try:
        source = ConversationRagSourceReference(
            document,
            fragment_id,
            start,
            end,
        )
        return ConversationRagVectorRecord(
            ConversationRagProviderCandidate(
                source,
                _exact_string(source_row["text"]),
            ),
            ConversationRagEmbeddingVector(tuple(coordinates)),
        )
    except (TypeError, ValueError):
        _fault(ConversationRagStoreFailure.CORRUPT)
    raise AssertionError("unreachable")


def _iter_records(parent: int) -> Generator[ConversationRagVectorRecord, None, None]:
    source_rows = _iter_json_lines(parent, _SOURCE_MAP_FILE, _MAX_SOURCE_MAP_BYTES)
    vector_rows = _iter_json_lines(parent, _VECTORS_FILE, _MAX_VECTORS_BYTES)
    sentinel = object()
    try:
        for source, vector in itertools.zip_longest(
            source_rows,
            vector_rows,
            fillvalue=sentinel,
        ):
            if source is sentinel or vector is sentinel:
                _fault(ConversationRagStoreFailure.CORRUPT)
            yield _record_from(source, vector)
    finally:
        source_rows.close()
        vector_rows.close()


def _file_digest(parent: int, name: str, maximum: int) -> tuple[str, int]:
    descriptor = _open_regular(parent, name, write=False)
    try:
        initial = _verify_regular(parent, name, descriptor)
        if initial.st_size > maximum:
            _fault(ConversationRagStoreFailure.CORRUPT)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fault(ConversationRagStoreFailure.CORRUPT)
            digest.update(chunk)
        final = _verify_regular(parent, name, descriptor)
        if _identity(initial) != _identity(final) or final.st_size != total:
            _fault(ConversationRagStoreFailure.UNSAFE)
        return f"sha256:{digest.hexdigest()}", total
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _VerifiedGeneration:
    provider_id: str
    checkpoint: ConversationRagProjectionCheckpoint
    conversations: int
    events: int
    documents: int
    fragments: int
    source_map_records: int
    vector_records: int
    source_utf8_bytes: int
    payload_bytes: int
    generation_bytes: int


_GENERATION_FIELDS = frozenset(
    {
        "schema_id",
        "session_id",
        "generation_id",
        "generation_digest",
        "status",
        "provider_id",
        "projection_schema_id",
        "model_id",
        "dimensions",
        "max_fragment_codepoints",
        "fragment_overlap_codepoints",
        "checkpoint_token",
        "conversation_count",
        "event_count",
        "document_count",
        "fragment_count",
        "source_map_record_count",
        "vector_record_count",
        "source_utf8_bytes",
        "payload_bytes",
        "generic_manifest_digest",
        "source_map_digest",
        "vectors_digest",
    }
)


def _validate_generation_records(
    generation: int,
    checkpoint: ConversationRagProjectionCheckpoint,
) -> tuple[int, int]:
    iterator = iter(_iter_records(generation))
    try:
        return _validate_generation_record_stream(iterator, checkpoint)
    finally:
        iterator.close()


def _validate_generation_record_stream(
    iterator: Generator[ConversationRagVectorRecord, None, None],
    checkpoint: ConversationRagProjectionCheckpoint,
) -> tuple[int, int]:
    current = next(iterator, None)
    fragment_total = 0
    source_bytes = 0
    for entry in checkpoint.entries:
        document = entry.document
        assembled = ""
        prior_end = 0
        prior_width = 0
        count = 0
        while (
            current is not None
            and current.candidate.source.document.key == document.key
        ):
            source = current.candidate.source
            if source.document != document:
                _fault(ConversationRagStoreFailure.CORRUPT)
            if len(current.embedding.values) != checkpoint.embedding.dimensions:
                _fault(ConversationRagStoreFailure.CORRUPT)
            width = source.end_codepoint - source.start_codepoint
            if width > CONVERSATION_RAG_POLICY.max_fragment_codepoints:
                _fault(ConversationRagStoreFailure.CORRUPT)
            if source.fragment_id != _fragment_id(
                document, source.start_codepoint, source.end_codepoint
            ):
                _fault(ConversationRagStoreFailure.CORRUPT)
            if count == 0:
                if source.start_codepoint != 0:
                    _fault(ConversationRagStoreFailure.CORRUPT)
                assembled = current.candidate.text
            else:
                if prior_width != CONVERSATION_RAG_POLICY.max_fragment_codepoints:
                    _fault(ConversationRagStoreFailure.CORRUPT)
                expected_start = (
                    prior_end - CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
                )
                if source.start_codepoint != expected_start:
                    _fault(ConversationRagStoreFailure.CORRUPT)
                overlap = prior_end - source.start_codepoint
                if (
                    assembled[source.start_codepoint : prior_end]
                    != current.candidate.text[:overlap]
                ):
                    _fault(ConversationRagStoreFailure.CORRUPT)
                assembled += current.candidate.text[overlap:]
            prior_end = source.end_codepoint
            prior_width = width
            count += 1
            fragment_total += 1
            if fragment_total > _MAX_RECORDS:
                _fault(ConversationRagStoreFailure.CORRUPT)
            current = next(iterator, None)
        if (
            count != entry.fragment_count
            or _source_digest(assembled) != entry.source_digest
        ):
            _fault(ConversationRagStoreFailure.CORRUPT)
        source_bytes += len(assembled.encode("utf-8", errors="strict"))
        if source_bytes > _MAX_SOURCE_UTF8_BYTES:
            _fault(ConversationRagStoreFailure.CORRUPT)
    if current is not None:
        _fault(ConversationRagStoreFailure.CORRUPT)
    return fragment_total, source_bytes


def _verify_generation_descriptor(
    generation: int,
    generation_id: str,
    session_id: str,
) -> _VerifiedGeneration:
    if _GENERATION_NAME.fullmatch(generation_id) is None:
        _fault(ConversationRagStoreFailure.CORRUPT)
    opened = os.fstat(generation)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or opened.st_nlink < 1
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        _fault(ConversationRagStoreFailure.UNSAFE)
    names = set(os.listdir(generation))
    if names != {
        _GENERATION_FILE,
        _MANIFEST_FILE,
        _SOURCE_MAP_FILE,
        _VECTORS_FILE,
    }:
        _fault(ConversationRagStoreFailure.UNSAFE)
    manifest = _manifest_from(
        _read_json(generation, _MANIFEST_FILE, _MAX_MANIFEST_BYTES)
    )
    metadata = _exact_dict(
        _read_json(generation, _GENERATION_FILE, _MAX_METADATA_BYTES),
        _GENERATION_FIELDS,
    )
    if (
        _exact_string(metadata["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID
        or _exact_string(metadata["status"]) != "verified"
        or _exact_string(metadata["session_id"]) != session_id
        or manifest.session_id != session_id
        or _exact_string(metadata["generation_id"]) != generation_id
        or _exact_string(metadata["projection_schema_id"]) != manifest.schema_id
        or _exact_string(metadata["model_id"]) != manifest.embedding.model_id
        or _exact_integer(metadata["dimensions"]) != manifest.embedding.dimensions
        or _exact_integer(metadata["max_fragment_codepoints"])
        != manifest.max_fragment_codepoints
        or _exact_integer(metadata["fragment_overlap_codepoints"])
        != manifest.fragment_overlap_codepoints
        or _exact_string(metadata["checkpoint_token"]) != manifest.token
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    provider_id = _exact_string(metadata["provider_id"])
    if _SAFE_IDENTIFIER.fullmatch(provider_id) is None:
        _fault(ConversationRagStoreFailure.CORRUPT)
    manifest_digest, manifest_bytes = _file_digest(
        generation,
        _MANIFEST_FILE,
        _MAX_MANIFEST_BYTES,
    )
    source_digest, source_map_bytes = _file_digest(
        generation,
        _SOURCE_MAP_FILE,
        _MAX_SOURCE_MAP_BYTES,
    )
    vector_digest, vector_bytes = _file_digest(
        generation,
        _VECTORS_FILE,
        _MAX_VECTORS_BYTES,
    )
    payload_digest = _generation_digest(
        manifest_digest,
        source_digest,
        vector_digest,
    )
    if (
        _exact_string(metadata["generic_manifest_digest"]) != manifest_digest
        or _exact_string(metadata["source_map_digest"]) != source_digest
        or _exact_string(metadata["vectors_digest"]) != vector_digest
        or _exact_string(metadata["generation_digest"]) != payload_digest
        or generation_id != f"gen-{payload_digest.removeprefix('sha256:')}"
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    fragments, source_bytes = _validate_generation_records(generation, manifest)
    conversations = len({entry.document.conversation_id for entry in manifest.entries})
    events = sum(
        entry.document.end_sequence - entry.document.start_sequence + 1
        for entry in manifest.entries
    )
    payload_bytes = manifest_bytes + source_map_bytes + vector_bytes
    if (
        _exact_integer(metadata["conversation_count"]) != conversations
        or _exact_integer(metadata["event_count"]) != events
        or _exact_integer(metadata["document_count"]) != len(manifest.entries)
        or _exact_integer(metadata["fragment_count"]) != fragments
        or _exact_integer(metadata["source_map_record_count"]) != fragments
        or _exact_integer(metadata["vector_record_count"]) != fragments
        or _exact_integer(metadata["source_utf8_bytes"]) != source_bytes
        or _exact_integer(metadata["payload_bytes"]) != payload_bytes
    ):
        _fault(ConversationRagStoreFailure.CORRUPT)
    metadata_bytes = os.stat(
        _GENERATION_FILE,
        dir_fd=generation,
        follow_symlinks=False,
    ).st_size
    total = metadata_bytes + payload_bytes
    if total > _MAX_GENERATION_BYTES:
        _fault(ConversationRagStoreFailure.CORRUPT)
    try:
        ConversationRagStoreCounts(
            conversations=conversations,
            events=events,
            documents=len(manifest.entries),
            fragments=fragments,
            source_map_records=fragments,
            vector_records=fragments,
            source_utf8_bytes=source_bytes,
            payload_bytes=payload_bytes,
            generation_bytes=total,
        )
    except (TypeError, ValueError):
        _fault(ConversationRagStoreFailure.CORRUPT)
    return _VerifiedGeneration(
        provider_id,
        manifest,
        conversations,
        events,
        len(manifest.entries),
        fragments,
        fragments,
        fragments,
        source_bytes,
        payload_bytes,
        total,
    )


def _verify_generation(
    parent: int,
    generation_id: str,
    session_id: str,
) -> _VerifiedGeneration:
    if _GENERATION_NAME.fullmatch(generation_id) is None:
        _fault(ConversationRagStoreFailure.CORRUPT)
    generation = _open_directory(parent, generation_id, create=False)
    if generation is None:
        _fault(ConversationRagStoreFailure.CORRUPT)
    try:
        return _verify_generation_descriptor(
            generation,
            generation_id,
            session_id,
        )
    finally:
        os.close(generation)


def _missing_snapshot(session_id: str) -> ConversationRagStoreSnapshot:
    return ConversationRagStoreSnapshot(
        session_id,
        ConversationRagStoreStatus.MISSING,
        ConversationRagProjectionState(
            ConversationRagProjectionStateAvailability.MISSING,
            None,
        ),
        None,
        None,
        ConversationRagStoreCounts(),
    )


def _corrupt_snapshot(session_id: str) -> ConversationRagStoreSnapshot:
    return ConversationRagStoreSnapshot(
        session_id,
        ConversationRagStoreStatus.CORRUPT,
        ConversationRagProjectionState(
            ConversationRagProjectionStateAvailability.CORRUPT,
            None,
        ),
        None,
        None,
        ConversationRagStoreCounts(),
    )


def _incompatible_snapshot(session_id: str) -> ConversationRagStoreSnapshot:
    return ConversationRagStoreSnapshot(
        session_id,
        ConversationRagStoreStatus.INCOMPATIBLE,
        ConversationRagProjectionState(
            ConversationRagProjectionStateAvailability.CORRUPT,
            None,
        ),
        None,
        None,
        ConversationRagStoreCounts(),
    )


_ACTIVE_FIELDS = frozenset(
    {
        "schema_id",
        "session_id",
        "generation_id",
        "generation_digest",
    }
)
_STATUS_FIELDS = frozenset({"schema_id", "session_id", "generation_id", "status"})


def _exists(parent: int, name: str) -> bool:
    try:
        value = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        _fault(ConversationRagStoreFailure.UNSAFE)
    return True


def _generation_names(scope: _SessionScope) -> tuple[tuple[str, ...], tuple[str, ...]]:
    completed: list[str] = []
    builds: list[str] = []
    for name in os.listdir(scope.generations):
        if _GENERATION_NAME.fullmatch(name):
            completed.append(name)
        elif _BUILD_NAME.fullmatch(name):
            builds.append(name)
        else:
            _fault(ConversationRagStoreFailure.UNSAFE)
        value = os.stat(name, dir_fd=scope.generations, follow_symlinks=False)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_nlink < 1
            or stat.S_IMODE(value.st_mode) != 0o700
        ):
            _fault(ConversationRagStoreFailure.UNSAFE)
    return tuple(sorted(completed)), tuple(sorted(builds))


def _inspect_locked(
    scope: _SessionScope,
    expected_provider: str,
    expected_embedding: ConversationRagEmbeddingIdentity,
) -> ConversationRagStoreSnapshot:
    scope.verify()
    for name in os.listdir(scope.session):
        if name in {
            _SESSION_LOCK,
            _GENERATIONS_DIRECTORY,
            _ACTIVE_FILE,
            _STATUS_FILE,
        }:
            continue
        if _POINTER_TEMP.fullmatch(name) is None:
            _fault(ConversationRagStoreFailure.UNSAFE)
        descriptor = _open_regular(scope.session, name, write=False)
        os.close(descriptor)
    completed, builds = _generation_names(scope)
    if not _exists(scope.session, _ACTIVE_FILE):
        status = ConversationRagStoreStatus.MISSING
        if _exists(scope.session, _STATUS_FILE):
            row = _exact_dict(
                _read_json(scope.session, _STATUS_FILE, _MAX_POINTER_BYTES),
                _STATUS_FIELDS,
            )
            if (
                _exact_string(row["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID
                or _exact_string(row["session_id"]) != scope.session_id
            ):
                _fault(ConversationRagStoreFailure.CORRUPT)
            try:
                observed = ConversationRagStoreStatus(_exact_string(row["status"]))
            except ValueError:
                _fault(ConversationRagStoreFailure.CORRUPT)
            status_generation = row["generation_id"]
            if status_generation is not None and (
                type(status_generation) is not str
                or _GENERATION_NAME.fullmatch(status_generation) is None
            ):
                _fault(ConversationRagStoreFailure.CORRUPT)
            if observed is ConversationRagStoreStatus.PROVIDER_FAILED:
                status = observed
            elif observed in (
                ConversationRagStoreStatus.READY,
                ConversationRagStoreStatus.STALE,
            ):
                # READY can be durable before active.json only when a publish
                # was interrupted.  It is therefore advisory stale state, not
                # evidence of a committed active generation.
                status = ConversationRagStoreStatus.STALE
            else:
                _fault(ConversationRagStoreFailure.CORRUPT)
        return ConversationRagStoreSnapshot(
            scope.session_id,
            status,
            ConversationRagProjectionState(
                ConversationRagProjectionStateAvailability.MISSING,
                None,
            ),
            None,
            None,
            ConversationRagStoreCounts(
                generations=len(completed),
                orphan_generations=len(completed) + len(builds),
            ),
        )
    pointer = _exact_dict(
        _read_json(scope.session, _ACTIVE_FILE, _MAX_POINTER_BYTES),
        _ACTIVE_FIELDS,
    )
    generation_id = _exact_string(pointer["generation_id"])
    generation_digest = _exact_string(pointer["generation_digest"])
    if (
        _exact_string(pointer["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID
        or _exact_string(pointer["session_id"]) != scope.session_id
        or _SHA256.fullmatch(generation_digest) is None
        or generation_id != f"gen-{generation_digest.removeprefix('sha256:')}"
    ):
        _fault(ConversationRagStoreFailure.INCOMPATIBLE)
    verified = _verify_generation(
        scope.generations,
        generation_id,
        scope.session_id,
    )
    compatible = True
    if (
        verified.provider_id != expected_provider
        or verified.checkpoint.schema_id != CONVERSATION_RAG_STORE_SCHEMA_ID
        or verified.checkpoint.embedding != expected_embedding
    ):
        compatible = False
    status = (
        ConversationRagStoreStatus.READY
        if compatible
        else ConversationRagStoreStatus.INCOMPATIBLE
    )
    if _exists(scope.session, _STATUS_FILE):
        row = _exact_dict(
            _read_json(scope.session, _STATUS_FILE, _MAX_POINTER_BYTES),
            _STATUS_FIELDS,
        )
        if (
            _exact_string(row["schema_id"]) != CONVERSATION_RAG_STORE_SCHEMA_ID
            or _exact_string(row["session_id"]) != scope.session_id
        ):
            _fault(ConversationRagStoreFailure.CORRUPT)
        status_generation = row["generation_id"]
        if status_generation is not None and (
            type(status_generation) is not str
            or _GENERATION_NAME.fullmatch(status_generation) is None
        ):
            _fault(ConversationRagStoreFailure.CORRUPT)
        try:
            advisory = ConversationRagStoreStatus(_exact_string(row["status"]))
        except ValueError:
            _fault(ConversationRagStoreFailure.CORRUPT)
        if advisory not in (
            ConversationRagStoreStatus.READY,
            ConversationRagStoreStatus.STALE,
            ConversationRagStoreStatus.PROVIDER_FAILED,
        ):
            _fault(ConversationRagStoreFailure.CORRUPT)
        if advisory is ConversationRagStoreStatus.PROVIDER_FAILED:
            status = advisory
        elif compatible and (
            advisory is ConversationRagStoreStatus.STALE
            or status_generation != generation_id
        ):
            status = ConversationRagStoreStatus.STALE
        elif compatible:
            status = ConversationRagStoreStatus.READY
    return ConversationRagStoreSnapshot(
        scope.session_id,
        status,
        ConversationRagProjectionState(
            ConversationRagProjectionStateAvailability.PRESENT,
            verified.checkpoint,
        ),
        verified.provider_id,
        generation_id,
        ConversationRagStoreCounts(
            conversations=verified.conversations,
            events=verified.events,
            documents=verified.documents,
            fragments=verified.fragments,
            source_map_records=verified.source_map_records,
            vector_records=verified.vector_records,
            generations=len(completed),
            orphan_generations=(
                len(builds)
                + len(tuple(name for name in completed if name != generation_id))
            ),
            source_utf8_bytes=verified.source_utf8_bytes,
            payload_bytes=verified.payload_bytes,
            generation_bytes=verified.generation_bytes,
        ),
    )


def _validate_request_bounds(request: ConversationRagProjectionRequest) -> None:
    if request.schema_id != CONVERSATION_RAG_STORE_SCHEMA_ID:
        raise ValueError("projection schema is incompatible with the local store")
    source_bytes = 0
    fragments = 0
    for document in request.documents:
        source_bytes += len(document.text.encode("utf-8", errors="strict"))
        fragments += _fragment_count(document.text)
    if source_bytes > _MAX_SOURCE_UTF8_BYTES:
        raise ValueError("projection exceeds the aggregate source byte budget")
    if fragments > _MAX_RECORDS:
        raise ValueError("projection exceeds the vector record budget")


def _safe_build_files() -> frozenset[str]:
    return frozenset(
        {
            _MUTATION_SOURCE_FILE,
            _MUTATION_VECTOR_FILE,
            _GENERATION_FILE,
            _MANIFEST_FILE,
            _SOURCE_MAP_FILE,
            _VECTORS_FILE,
        }
    )


def _prevalidate_removable_directory(
    parent: int,
    name: str,
    *,
    build: bool,
) -> tuple[str, ...]:
    directory = _open_directory(parent, name, create=False)
    if directory is None:
        _fault(ConversationRagStoreFailure.UNSAFE)
    try:
        names = tuple(sorted(os.listdir(directory)))
        allowed = (
            _safe_build_files()
            if build
            else frozenset(
                {_GENERATION_FILE, _MANIFEST_FILE, _SOURCE_MAP_FILE, _VECTORS_FILE}
            )
        )
        if not set(names) <= allowed:
            _fault(ConversationRagStoreFailure.UNSAFE)
        for filename in names:
            descriptor = _open_regular(directory, filename, write=False)
            os.close(descriptor)
        return names
    finally:
        os.close(directory)


def _remove_directory(
    parent: int,
    name: str,
    *,
    build: bool,
) -> None:
    directory = _open_directory(parent, name, create=False)
    if directory is None:
        _fault(ConversationRagStoreFailure.UNSAFE)
    try:
        names = tuple(sorted(os.listdir(directory)))
        allowed = (
            _safe_build_files()
            if build
            else frozenset(
                {_GENERATION_FILE, _MANIFEST_FILE, _SOURCE_MAP_FILE, _VECTORS_FILE}
            )
        )
        if not set(names) <= allowed:
            _fault(ConversationRagStoreFailure.UNSAFE)
        for filename in names:
            descriptor = _open_regular(directory, filename, write=False)
            os.close(descriptor)
        for filename in names:
            _verify_directory(parent, name, directory, owned=True)
            descriptor = _open_regular(directory, filename, write=False)
            try:
                _verify_regular(directory, filename, descriptor)
            finally:
                os.close(descriptor)
            os.unlink(filename, dir_fd=directory)
        os.fsync(directory)
        _verify_directory(parent, name, directory, owned=True)
    finally:
        os.close(directory)
    os.rmdir(name, dir_fd=parent)
    os.fsync(parent)


def _clean_orphans(scope: _SessionScope, active: str | None) -> None:
    completed, builds = _generation_names(scope)
    for name in builds:
        _remove_directory(scope.generations, name, build=True)
    for name in completed:
        if name != active:
            _remove_directory(scope.generations, name, build=False)


def _prevalidate_session_namespace(
    scope: _SessionScope,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    scope.verify()
    for name in os.listdir(scope.session):
        if name in {
            _SESSION_LOCK,
            _GENERATIONS_DIRECTORY,
            _ACTIVE_FILE,
            _STATUS_FILE,
        }:
            continue
        if _POINTER_TEMP.fullmatch(name) is None:
            _fault(ConversationRagStoreFailure.UNSAFE)
        descriptor = _open_regular(scope.session, name, write=False)
        os.close(descriptor)
    for filename in (_ACTIVE_FILE, _STATUS_FILE):
        if _exists(scope.session, filename):
            descriptor = _open_regular(scope.session, filename, write=False)
            os.close(descriptor)
    completed, builds = _generation_names(scope)
    for name in completed:
        _prevalidate_removable_directory(
            scope.generations,
            name,
            build=False,
        )
    for name in builds:
        _prevalidate_removable_directory(
            scope.generations,
            name,
            build=True,
        )
    return completed, builds


def _cleanup_pointer_temps(scope: _SessionScope) -> None:
    removed = False
    for name in tuple(os.listdir(scope.session)):
        if _POINTER_TEMP.fullmatch(name) is None:
            continue
        descriptor = _open_regular(scope.session, name, write=False)
        os.close(descriptor)
        os.unlink(name, dir_fd=scope.session)
        removed = True
    if removed:
        os.fsync(scope.session)


class _VectorRanker(Protocol):
    def rank(
        self,
        request: ConversationRagRetrievalRequest,
        embedding: ConversationRagEmbeddingVector,
        records: Iterable[ConversationRagVectorRecord],
        /,
    ) -> tuple[ConversationRagProviderCandidate, ...]: ...


class _ActiveReader:
    __slots__ = ("_checkpoint", "_closed", "_generation", "_ranker", "_scope")

    def __init__(
        self,
        scope: _SessionScope,
        generation: int,
        checkpoint: ConversationRagProjectionCheckpoint,
        ranker: _VectorRanker,
    ) -> None:
        self._scope = scope
        self._generation = generation
        self._checkpoint = checkpoint
        self._ranker = ranker
        self._closed = False

    def __enter__(self) -> Self:
        if self._closed:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self._generation)
        finally:
            self._scope.close()

    def search(
        self,
        request: ConversationRagRetrievalRequest,
        embedding: ConversationRagEmbeddingVector,
        /,
    ) -> tuple[ConversationRagProviderCandidate, ...]:
        if self._closed:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        if type(request) is not ConversationRagRetrievalRequest:
            raise TypeError("reader request must use the exact RAG request")
        if type(embedding) is not ConversationRagEmbeddingVector:
            raise TypeError("reader embedding must use the exact vector")
        if request.session_id != self._checkpoint.session_id:
            raise ValueError("reader request belongs to another session")
        self._scope.verify()
        return self._ranker.rank(
            request,
            embedding,
            _iter_records(self._generation),
        )


class _RecordCursor:
    __slots__ = ("_iterator", "current")

    def __init__(self, parent: int, *, mutation: bool) -> None:
        if mutation:
            iterator = _iter_mutation_records(parent)
        else:
            iterator = _iter_records(parent)
        self._iterator = iterator
        try:
            self.current = next(iterator, None)
        except BaseException:
            iterator.close()
            raise

    def advance(self) -> None:
        self.current = next(self._iterator, None)

    def discard_document(self, document_id: str) -> None:
        while (
            self.current is not None
            and self.current.candidate.source.document.key.document_id == document_id
        ):
            self.advance()

    def discard_before(self, document_id: str) -> None:
        while self.current is not None:
            current_id = self.current.candidate.source.document.key.document_id
            if current_id >= document_id:
                return
            self.discard_document(current_id)

    def close(self) -> None:
        self._iterator.close()


def _iter_mutation_records(
    parent: int,
) -> Generator[ConversationRagVectorRecord, None, None]:
    source_rows = _iter_json_lines(parent, _MUTATION_SOURCE_FILE, _MAX_SOURCE_MAP_BYTES)
    vector_rows = _iter_json_lines(parent, _MUTATION_VECTOR_FILE, _MAX_VECTORS_BYTES)
    sentinel = object()
    try:
        for source, vector in itertools.zip_longest(
            source_rows,
            vector_rows,
            fillvalue=sentinel,
        ):
            if source is sentinel or vector is sentinel:
                _fault(ConversationRagStoreFailure.CORRUPT)
            yield _record_from(source, vector)
    finally:
        source_rows.close()
        vector_rows.close()


class ConversationRagGenerationBuilder:
    """One lock- and descriptor-owned inactive generation operation."""

    __slots__ = (
        "_base_generation",
        "_build",
        "_build_name",
        "_closed",
        "_last_mutation",
        "_mutation_counts",
        "_mutation_source",
        "_mutation_vector",
        "_provider_id",
        "_published",
        "_request",
        "_reset",
        "_scope",
        "_state",
        "_total_records",
    )

    def __init__(
        self,
        scope: _SessionScope,
        provider_id: str,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        build: int,
        build_name: str,
        mutation_source: int,
        mutation_vector: int,
        base_generation: int | None,
    ) -> None:
        self._scope = scope
        self._provider_id = provider_id
        self._request = request
        self._state = state
        self._build = build
        self._build_name = build_name
        self._mutation_source = mutation_source
        self._mutation_vector = mutation_vector
        self._base_generation = base_generation
        self._mutation_counts: dict[str, int | None] = {}
        self._last_mutation: str | None = None
        self._total_records = 0
        self._reset = False
        self._published = False
        self._closed = False

    @property
    def state(self) -> ConversationRagProjectionState:
        """Return the exact store-loaded state bound to this operation."""

        return self._state

    def __enter__(self) -> Self:
        if self._closed:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor_name in ("_mutation_source", "_mutation_vector"):
            descriptor = getattr(self, descriptor_name)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                setattr(self, descriptor_name, -1)
        if self._base_generation is not None:
            try:
                os.close(self._base_generation)
            except OSError:
                pass
            self._base_generation = None
        if self._build >= 0:
            try:
                os.close(self._build)
            except OSError:
                pass
            self._build = -1
        self._scope.close()

    def _require_open(self) -> None:
        if self._closed or self._published:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        self._scope.verify()

    def _require_mutation_order(self, document_id: str) -> None:
        if self._last_mutation is not None and document_id <= self._last_mutation:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        self._last_mutation = document_id

    def replace_document(
        self,
        document: ConversationRagDocumentIdentity,
        records: tuple[ConversationRagVectorRecord, ...],
        /,
    ) -> None:
        failure: ConversationRagStoreFailure | None = None
        try:
            self._replace_document(document, records)
            return
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        assert failure is not None
        _raise_store(failure)

    def _replace_document(
        self,
        document: ConversationRagDocumentIdentity,
        records: tuple[ConversationRagVectorRecord, ...],
        /,
    ) -> None:
        self._require_open()
        if type(document) is not ConversationRagDocumentIdentity:
            raise TypeError("replacement document must use the exact identity")
        if type(records) is not tuple:
            raise TypeError("replacement records must be a tuple")
        source = next(
            (
                value
                for value in self._request.documents
                if value.identity.key == document.key
            ),
            None,
        )
        if source is None or source.identity != document:
            raise ValueError("replacement document is not in the projection target")
        self._require_mutation_order(document.key.document_id)
        previous_end = 0
        for index, record in enumerate(records):
            if type(record) is not ConversationRagVectorRecord:
                raise TypeError("replacement records must use exact vector records")
            candidate = record.candidate
            reference = candidate.source
            if reference.document != document:
                raise ValueError("replacement record belongs to another document")
            if len(record.embedding.values) != self._request.embedding.dimensions:
                raise ValueError("replacement vector dimensions are incompatible")
            expected_start = (
                0
                if index == 0
                else previous_end - CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
            )
            if reference.start_codepoint != expected_start:
                raise ValueError("replacement fragments are not canonical")
            if (
                candidate.text
                != source.text[reference.start_codepoint : reference.end_codepoint]
            ):
                raise ValueError("replacement fragment does not match its source")
            if reference.fragment_id != _fragment_id(
                document,
                reference.start_codepoint,
                reference.end_codepoint,
            ):
                raise ValueError("replacement fragment identity is inconsistent")
            source_row, vector_row = _record_values(record)
            _write_all(self._mutation_source, _canonical_json(source_row))
            _write_all(self._mutation_vector, _canonical_json(vector_row))
            previous_end = reference.end_codepoint
            self._total_records += 1
            if self._total_records > _MAX_RECORDS:
                raise ValueError("replacement records exceed the store budget")
        if len(records) != _fragment_count(source.text):
            raise ValueError("replacement fragment count is inconsistent")
        self._mutation_counts[document.key.document_id] = len(records)

    def delete_document(self, document: ConversationRagDocumentKey, /) -> None:
        failure: ConversationRagStoreFailure | None = None
        try:
            self._delete_document(document)
            return
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        assert failure is not None
        _raise_store(failure)

    def _delete_document(self, document: ConversationRagDocumentKey, /) -> None:
        self._require_open()
        if type(document) is not ConversationRagDocumentKey:
            raise TypeError("deleted document must use the exact key")
        if document.session_id != self._request.session_id:
            raise ValueError("deleted document belongs to another session")
        self._require_mutation_order(document.document_id)
        self._mutation_counts[document.document_id] = None

    def reset_session(self, session_id: str, /) -> None:
        failure: ConversationRagStoreFailure | None = None
        try:
            self._reset_session(session_id)
            return
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        assert failure is not None
        _raise_store(failure)

    def _reset_session(self, session_id: str, /) -> None:
        self._require_open()
        if type(session_id) is not str or session_id != self._request.session_id:
            raise ValueError("reset session does not match the projection target")
        if self._last_mutation is not None or self._mutation_counts:
            _fault(ConversationRagStoreFailure.OPERATION_FAILED)
        self._reset = True

    def _close_mutation_writers(self) -> None:
        for descriptor_name in ("_mutation_source", "_mutation_vector"):
            descriptor = getattr(self, descriptor_name)
            if descriptor >= 0:
                os.fsync(descriptor)
                os.close(descriptor)
                setattr(self, descriptor_name, -1)

    def _copy_target_records(self) -> tuple[int, int, int]:
        source_output = -1
        vector_output = -1
        mutation: _RecordCursor | None = None
        base: _RecordCursor | None = None
        fragments = 0
        source_bytes = 0
        try:
            source_output = _open_regular(
                self._build,
                _SOURCE_MAP_FILE,
                write=True,
                create=True,
            )
            vector_output = _open_regular(
                self._build,
                _VECTORS_FILE,
                write=True,
                create=True,
            )
            mutation = _RecordCursor(self._build, mutation=True)
            base = (
                None
                if self._base_generation is None
                else _RecordCursor(self._base_generation, mutation=False)
            )
            for document in self._request.documents:
                document_id = document.identity.key.document_id
                mutation.discard_before(document_id)
                if base is not None:
                    base.discard_before(document_id)
                changed = self._mutation_counts.get(document_id, "unchanged")
                selected = mutation if changed != "unchanged" else base
                if changed is None or (self._reset and changed == "unchanged"):
                    selected = None
                count = 0
                while (
                    selected is not None
                    and selected.current is not None
                    and selected.current.candidate.source.document.key.document_id
                    == document_id
                ):
                    record = selected.current
                    if record.candidate.source.document != document.identity:
                        _fault(ConversationRagStoreFailure.CORRUPT)
                    reference = record.candidate.source
                    if (
                        record.candidate.text
                        != document.text[
                            reference.start_codepoint : reference.end_codepoint
                        ]
                    ):
                        _fault(ConversationRagStoreFailure.CORRUPT)
                    source_row, vector_row = _record_values(record)
                    _write_all(source_output, _canonical_json(source_row))
                    _write_all(vector_output, _canonical_json(vector_row))
                    count += 1
                    fragments += 1
                    selected.advance()
                if count != _fragment_count(document.text):
                    _fault(ConversationRagStoreFailure.CORRUPT)
                if selected is mutation and base is not None:
                    base.discard_document(document_id)
                elif selected is base:
                    mutation.discard_document(document_id)
                source_bytes += len(document.text.encode("utf-8", errors="strict"))
            if mutation.current is not None:
                _fault(ConversationRagStoreFailure.CORRUPT)
            os.fsync(source_output)
            os.fsync(vector_output)
            source_size = os.fstat(source_output).st_size
            vector_size = os.fstat(vector_output).st_size
            if (
                fragments > _MAX_RECORDS
                or source_bytes > _MAX_SOURCE_UTF8_BYTES
                or source_size > _MAX_SOURCE_MAP_BYTES
                or vector_size > _MAX_VECTORS_BYTES
            ):
                _fault(ConversationRagStoreFailure.OPERATION_FAILED)
            return fragments, source_bytes, source_size + vector_size
        finally:
            if mutation is not None:
                mutation.close()
            if base is not None:
                base.close()
            _close_descriptors(
                descriptor
                for descriptor in (source_output, vector_output)
                if descriptor >= 0
            )

    def publish(
        self,
        result: ConversationRagProjectionResult,
        /,
    ) -> ConversationRagStoreSnapshot:
        """Verify and atomically publish the successful complete generation."""

        failure: ConversationRagStoreFailure | None = None
        try:
            return self._publish(result)
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        assert failure is not None
        _raise_store(failure)
        raise AssertionError("unreachable")

    def _publish(
        self,
        result: ConversationRagProjectionResult,
        /,
    ) -> ConversationRagStoreSnapshot:
        self._require_open()
        if type(result) is not ConversationRagProjectionResult:
            raise TypeError("publish result must use the exact projection result")
        expected = _target_checkpoint(self._request)
        if result.checkpoint != expected:
            raise ValueError("publish checkpoint does not match the projection target")
        self._close_mutation_writers()
        fragments, source_bytes, record_bytes = self._copy_target_records()
        manifest_payload = _canonical_json(_manifest_value(expected))
        _write_new_bytes(
            self._build,
            _MANIFEST_FILE,
            manifest_payload,
            _MAX_MANIFEST_BYTES,
        )
        manifest_digest, _ = _file_digest(
            self._build,
            _MANIFEST_FILE,
            _MAX_MANIFEST_BYTES,
        )
        source_digest, _ = _file_digest(
            self._build,
            _SOURCE_MAP_FILE,
            _MAX_SOURCE_MAP_BYTES,
        )
        vectors_digest, _ = _file_digest(
            self._build,
            _VECTORS_FILE,
            _MAX_VECTORS_BYTES,
        )
        generation_digest = _generation_digest(
            manifest_digest,
            source_digest,
            vectors_digest,
        )
        generation_id = f"gen-{generation_digest.removeprefix('sha256:')}"
        conversations = len(
            {document.identity.conversation_id for document in self._request.documents}
        )
        events = sum(
            document.identity.end_sequence - document.identity.start_sequence + 1
            for document in self._request.documents
        )
        payload_bytes = len(manifest_payload) + record_bytes
        generation_value = {
            "checkpoint_token": expected.token,
            "conversation_count": conversations,
            "dimensions": expected.embedding.dimensions,
            "document_count": len(expected.entries),
            "event_count": events,
            "fragment_count": fragments,
            "fragment_overlap_codepoints": (
                CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
            ),
            "generation_digest": generation_digest,
            "generation_id": generation_id,
            "generic_manifest_digest": manifest_digest,
            "max_fragment_codepoints": (
                CONVERSATION_RAG_POLICY.max_fragment_codepoints
            ),
            "model_id": expected.embedding.model_id,
            "payload_bytes": payload_bytes,
            "projection_schema_id": expected.schema_id,
            "provider_id": self._provider_id,
            "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
            "session_id": expected.session_id,
            "source_map_digest": source_digest,
            "source_map_record_count": fragments,
            "source_utf8_bytes": source_bytes,
            "status": "verified",
            "vector_record_count": fragments,
            "vectors_digest": vectors_digest,
        }
        _write_new_bytes(
            self._build,
            _GENERATION_FILE,
            _canonical_json(generation_value),
            _MAX_METADATA_BYTES,
        )
        os.unlink(_MUTATION_SOURCE_FILE, dir_fd=self._build)
        os.unlink(_MUTATION_VECTOR_FILE, dir_fd=self._build)
        os.fsync(self._build)
        preverified = _verify_generation_descriptor(
            self._build,
            generation_id,
            self._request.session_id,
        )
        if (
            preverified.provider_id != self._provider_id
            or preverified.checkpoint != expected
        ):
            _fault(ConversationRagStoreFailure.CORRUPT)
        # Rebind the held descriptor to its namespace entry immediately
        # before the rename so an exchanged build name cannot be published.
        _verify_directory(
            self._scope.generations,
            self._build_name,
            self._build,
            owned=True,
        )
        existing = False
        try:
            os.rename(
                self._build_name,
                generation_id,
                src_dir_fd=self._scope.generations,
                dst_dir_fd=self._scope.generations,
            )
        except OSError as exc:
            if exc.errno in (errno.EEXIST, getattr(errno, "ENOTEMPTY", errno.EEXIST)):
                existing = True
            else:
                raise
        if existing:
            verified = _verify_generation(
                self._scope.generations,
                generation_id,
                self._request.session_id,
            )
            if (
                verified.provider_id != self._provider_id
                or verified.checkpoint != expected
            ):
                _fault(ConversationRagStoreFailure.CORRUPT)
            os.close(self._build)
            self._build = -1
            _remove_directory(
                self._scope.generations,
                self._build_name,
                build=True,
            )
        else:
            self._build_name = generation_id
            verified = _verify_generation(
                self._scope.generations,
                generation_id,
                self._request.session_id,
            )
            if (
                verified.provider_id != self._provider_id
                or verified.checkpoint != expected
            ):
                _fault(ConversationRagStoreFailure.CORRUPT)
        os.fsync(self._scope.generations)
        pointer = {
            "generation_digest": generation_digest,
            "generation_id": generation_id,
            "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
            "session_id": expected.session_id,
        }
        completed, builds = _generation_names(self._scope)
        snapshot = ConversationRagStoreSnapshot(
            expected.session_id,
            ConversationRagStoreStatus.READY,
            ConversationRagProjectionState(
                ConversationRagProjectionStateAvailability.PRESENT,
                expected,
            ),
            self._provider_id,
            generation_id,
            ConversationRagStoreCounts(
                conversations=verified.conversations,
                events=verified.events,
                documents=verified.documents,
                fragments=verified.fragments,
                source_map_records=verified.source_map_records,
                vector_records=verified.vector_records,
                generations=len(completed),
                orphan_generations=(
                    len(builds)
                    + len(tuple(name for name in completed if name != generation_id))
                ),
                source_utf8_bytes=verified.source_utf8_bytes,
                payload_bytes=verified.payload_bytes,
                generation_bytes=verified.generation_bytes,
            ),
        )
        _replace_json(
            self._scope.session,
            _STATUS_FILE,
            {
                "generation_id": generation_id,
                "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
                "session_id": expected.session_id,
                "status": ConversationRagStoreStatus.READY.value,
            },
        )
        # The active pointer is the sole commit record and is replaced last.
        # Make the builder terminal before the commit so the rename remains
        # the final callable operation on the successful publication path.
        self._published = True
        _replace_json(self._scope.session, _ACTIVE_FILE, pointer)
        return snapshot


class ConversationRagGenerationStore:
    """Own disposable generation topology without I/O at construction."""

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Workspace, /) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("conversation RAG store requires a Workspace")
        self._workspace = workspace

    @property
    def workspace(self) -> Workspace:
        """Return the retained identity-bound workspace."""

        return self._workspace

    def inspect(
        self,
        session_id: str,
        provider_id: str,
        embedding: ConversationRagEmbeddingIdentity,
        /,
    ) -> ConversationRagStoreSnapshot:
        """Inspect and completely verify one active generation, if present."""

        session = _identifier("session ID", session_id)
        provider = _identifier("RAG provider ID", provider_id)
        if type(embedding) is not ConversationRagEmbeddingIdentity:
            raise TypeError("embedding must use the exact RAG identity")
        failure: ConversationRagStoreFailure | None = None
        scope: _SessionScope | None = None
        try:
            scope = _open_session_scope(self._workspace, session, create=False)
            if scope is None:
                return _missing_snapshot(session)
            return _inspect_locked(scope, provider, embedding)
        except _StoreFault as exc:
            if exc.failure is ConversationRagStoreFailure.CORRUPT:
                return _corrupt_snapshot(session)
            if exc.failure is ConversationRagStoreFailure.INCOMPATIBLE:
                return _incompatible_snapshot(session)
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        finally:
            if scope is not None:
                scope.close()
        assert failure is not None
        _raise_store(failure)
        raise AssertionError("unreachable")

    def begin_generation(
        self,
        provider_id: str,
        request: ConversationRagProjectionRequest,
        state: ConversationRagProjectionState,
        /,
        *,
        rebuild: bool,
    ) -> ConversationRagGenerationBuilder:
        """Open one inactive generation builder under the session lock."""

        provider = _identifier("RAG provider ID", provider_id)
        if type(request) is not ConversationRagProjectionRequest:
            raise TypeError("projection request must use the exact RAG request")
        if type(state) is not ConversationRagProjectionState:
            raise TypeError("projection state must use the exact RAG state")
        if type(rebuild) is not bool:
            raise TypeError("rebuild must be a boolean")
        _validate_request_bounds(request)
        failure: ConversationRagStoreFailure | None = None
        scope: _SessionScope | None = None
        build = -1
        mutation_source = -1
        mutation_vector = -1
        base_generation: int | None = None
        try:
            scope = _open_session_scope(
                self._workspace,
                request.session_id,
                create=True,
            )
            assert scope is not None
            unusable_active = False
            try:
                observed = _inspect_locked(scope, provider, request.embedding)
            except _StoreFault as exc:
                if rebuild and exc.failure in (
                    ConversationRagStoreFailure.CORRUPT,
                    ConversationRagStoreFailure.INCOMPATIBLE,
                ):
                    observed = (
                        _corrupt_snapshot(request.session_id)
                        if exc.failure is ConversationRagStoreFailure.CORRUPT
                        else _incompatible_snapshot(request.session_id)
                    )
                    unusable_active = True
                else:
                    raise
            _cleanup_pointer_temps(scope)
            if (
                observed.status
                in (
                    ConversationRagStoreStatus.CORRUPT,
                    ConversationRagStoreStatus.INCOMPATIBLE,
                )
                and not rebuild
            ):
                _fault(
                    ConversationRagStoreFailure.CORRUPT
                    if observed.status is ConversationRagStoreStatus.CORRUPT
                    else ConversationRagStoreFailure.INCOMPATIBLE
                )
            if observed.state != state:
                _fault(ConversationRagStoreFailure.INCOMPATIBLE)
            active = observed.generation_id
            if not unusable_active:
                _clean_orphans(scope, active)
            else:
                _completed, builds = _generation_names(scope)
                for name in builds:
                    _remove_directory(scope.generations, name, build=True)
            if active is not None and not rebuild:
                base_generation = _open_directory(
                    scope.generations,
                    active,
                    create=False,
                )
                if base_generation is None:
                    _fault(ConversationRagStoreFailure.CORRUPT)
            for _attempt in range(16):
                build_name = f"build-{secrets.token_hex(16)}"
                try:
                    os.mkdir(build_name, mode=0o700, dir_fd=scope.generations)
                except FileExistsError:
                    continue
                build = (
                    _open_directory(
                        scope.generations,
                        build_name,
                        create=False,
                    )
                    or -1
                )
                break
            else:
                _fault(ConversationRagStoreFailure.OPERATION_FAILED)
            if build < 0:
                _fault(ConversationRagStoreFailure.OPERATION_FAILED)
            mutation_source = _open_regular(
                build,
                _MUTATION_SOURCE_FILE,
                write=True,
                create=True,
            )
            mutation_vector = _open_regular(
                build,
                _MUTATION_VECTOR_FILE,
                write=True,
                create=True,
            )
            builder = ConversationRagGenerationBuilder(
                scope,
                provider,
                request,
                observed.state,
                build,
                build_name,
                mutation_source,
                mutation_vector,
                base_generation,
            )
            scope = None
            build = -1
            mutation_source = -1
            mutation_vector = -1
            base_generation = None
            return builder
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        finally:
            _close_descriptors(
                descriptor
                for descriptor in (
                    mutation_vector,
                    mutation_source,
                    base_generation if base_generation is not None else -1,
                    build,
                )
                if descriptor >= 0
            )
            if scope is not None:
                scope.close()
        assert failure is not None
        _raise_store(failure)
        raise AssertionError("unreachable")

    def open_active_reader(
        self,
        snapshot: ConversationRagStoreSnapshot,
        ranker: _VectorRanker,
        /,
    ) -> _ActiveReader:
        """Open one verified call-scoped reader for the exact active snapshot."""

        if type(snapshot) is not ConversationRagStoreSnapshot:
            raise TypeError("reader snapshot must use the exact store value")
        if ranker is None or not callable(getattr(ranker, "rank", None)):
            raise TypeError("reader ranker must provide rank")
        if (
            snapshot.status is not ConversationRagStoreStatus.READY
            or snapshot.provider_id is None
            or snapshot.generation_id is None
            or snapshot.state.checkpoint is None
        ):
            _raise_store(ConversationRagStoreFailure.INCOMPATIBLE)
        failure: ConversationRagStoreFailure | None = None
        scope: _SessionScope | None = None
        generation: int | None = None
        try:
            scope = _open_session_scope(
                self._workspace,
                snapshot.session_id,
                create=False,
            )
            if scope is None:
                _fault(ConversationRagStoreFailure.INCOMPATIBLE)
            current = _inspect_locked(
                scope,
                snapshot.provider_id,
                snapshot.state.checkpoint.embedding,
            )
            if (
                current != snapshot
                or current.status is not ConversationRagStoreStatus.READY
            ):
                _fault(ConversationRagStoreFailure.INCOMPATIBLE)
            generation = _open_directory(
                scope.generations,
                snapshot.generation_id,
                create=False,
            )
            if generation is None:
                _fault(ConversationRagStoreFailure.CORRUPT)
            reader = _ActiveReader(
                scope,
                generation,
                snapshot.state.checkpoint,
                ranker,
            )
            scope = None
            generation = None
            return reader
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        finally:
            if generation is not None:
                os.close(generation)
            if scope is not None:
                scope.close()
        assert failure is not None
        _raise_store(failure)
        raise AssertionError("unreachable")

    def set_status(
        self,
        session_id: str,
        status: ConversationRagStoreStatus,
        /,
    ) -> None:
        """Persist one advisory stale/provider-failed state without source data."""

        session = _identifier("session ID", session_id)
        if type(status) is not ConversationRagStoreStatus:
            raise TypeError("store status must use the exact enum")
        if status not in (
            ConversationRagStoreStatus.STALE,
            ConversationRagStoreStatus.PROVIDER_FAILED,
        ):
            raise ValueError("only stale or provider-failed status is advisory")
        failure: ConversationRagStoreFailure | None = None
        scope: _SessionScope | None = None
        try:
            scope = _open_session_scope(self._workspace, session, create=True)
            assert scope is not None
            _prevalidate_session_namespace(scope)
            _cleanup_pointer_temps(scope)
            generation_id: str | None = None
            if _exists(scope.session, _ACTIVE_FILE):
                pointer = _exact_dict(
                    _read_json(scope.session, _ACTIVE_FILE, _MAX_POINTER_BYTES),
                    _ACTIVE_FIELDS,
                )
                generation_id = _exact_string(pointer["generation_id"])
            _replace_json(
                scope.session,
                _STATUS_FILE,
                {
                    "generation_id": generation_id,
                    "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
                    "session_id": session,
                    "status": status.value,
                },
            )
            return
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        finally:
            if scope is not None:
                scope.close()
        assert failure is not None
        _raise_store(failure)

    def purge(
        self,
        session_id: str,
        /,
        *,
        confirmed: bool,
    ) -> ConversationRagStoreCounts:
        """Delete exactly one session's derived generations after confirmation."""

        session = _identifier("session ID", session_id)
        if type(confirmed) is not bool:
            raise TypeError("purge confirmation must be a boolean")
        if not confirmed:
            _raise_store(ConversationRagStoreFailure.CONFIRMATION_REQUIRED)
        failure: ConversationRagStoreFailure | None = None
        scope: _SessionScope | None = None
        try:
            scope = _open_session_scope(self._workspace, session, create=False)
            if scope is None:
                return ConversationRagStoreCounts()
            completed, builds = _prevalidate_session_namespace(scope)
            counts = ConversationRagStoreCounts(
                generations=len(completed),
                orphan_generations=len(builds),
            )
            if _exists(scope.session, _ACTIVE_FILE):
                os.unlink(_ACTIVE_FILE, dir_fd=scope.session)
                os.fsync(scope.session)
            if _exists(scope.session, _STATUS_FILE):
                os.unlink(_STATUS_FILE, dir_fd=scope.session)
            _cleanup_pointer_temps(scope)
            for name in builds:
                _remove_directory(scope.generations, name, build=True)
            for name in completed:
                _remove_directory(scope.generations, name, build=False)
            os.fsync(scope.session)
            return counts
        except _StoreFault as exc:
            failure = exc.failure
        except OSError as exc:
            failure = _os_failure(exc)
        finally:
            if scope is not None:
                scope.close()
        assert failure is not None
        _raise_store(failure)
        raise AssertionError("unreachable")


__all__ = [  # noqa: RUF022 - order is a supported compatibility contract
    "CONVERSATION_RAG_STORE_SCHEMA_ID",
    "ConversationRagStoreStatus",
    "ConversationRagStoreFailure",
    "ConversationRagStoreCounts",
    "ConversationRagStoreSnapshot",
    "ConversationRagGenerationBuilder",
    "ConversationRagGenerationStore",
    "ConversationRagStoreError",
]
