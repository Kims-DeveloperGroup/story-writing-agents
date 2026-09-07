"""Session continuation serialization through authoritative result publication."""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Callable

from .manifest import ContextContinuationIdentity


_locks: weakref.WeakValueDictionary[ContextContinuationIdentity, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_registry_lock = threading.Lock()


class ContextContinuationTransaction:
    """One process-local session lease with deferred content-free commitment.

    Backend session access remains the process-exclusion authority. The shared
    weak registry coordinates different service/runtime instances in this
    process; it does not create tasks, files, or provider resources.
    """

    def __init__(self, identity: ContextContinuationIdentity) -> None:
        if type(identity) is not ContextContinuationIdentity:
            raise TypeError("continuation identity is invalid")
        self.identity = identity
        with _registry_lock:
            lock = _locks.get(identity)
            if lock is None:
                lock = asyncio.Lock()
                _locks[identity] = lock
        self._lock = lock
        self._owner: asyncio.Task[object] | None = None
        self._pending: tuple[Callable[[], None], Callable[[], None], Callable[[], bool]] | None = None

    async def __aenter__(self) -> ContextContinuationTransaction:
        await self._lock.acquire()
        self._owner = asyncio.current_task()
        return self

    def require_active(self) -> None:
        if self._owner is None or asyncio.current_task() is not self._owner:
            raise RuntimeError("session continuation requires its active owner")

    def stage(
        self, commit: Callable[[], None], abort: Callable[[], None],
        published: Callable[[], bool],
    ) -> None:
        self.require_active()
        if self._pending is not None:
            raise RuntimeError("session continuation already awaits publication")
        self._pending = (commit, abort, published)

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._pending is not None:
                commit, abort, published = self._pending
                self._pending = None
                if exc_type is None:
                    try:
                        if published():
                            commit()
                        else:
                            abort()
                    except BaseException:
                        abort()
                        raise
                else:
                    abort()
        finally:
            self._owner = None
            self._lock.release()
