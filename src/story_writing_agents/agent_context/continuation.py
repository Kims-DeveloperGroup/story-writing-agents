"""Session continuation serialization through authoritative result publication."""

from __future__ import annotations

import asyncio
import inspect
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
        self._pending: tuple[
            Callable[[], None],
            Callable[[], None],
            Callable[[], bool],
            Callable[[], None] | None,
        ] | None = None
        self._retained_contexts: list[
            tuple[int, Callable[[BaseException | None], object]]
        ] = []

    async def __aenter__(self) -> ContextContinuationTransaction:
        await self._lock.acquire()
        self._owner = asyncio.current_task()
        return self

    def require_active(self) -> None:
        if self._owner is None or asyncio.current_task() is not self._owner:
            raise RuntimeError("session continuation requires its active owner")

    def stage(
        self, commit: Callable[[], None], abort: Callable[[], None],
        published: Callable[[], bool], complete: Callable[[], None] | None = None,
    ) -> None:
        self.require_active()
        if self._pending is not None:
            raise RuntimeError("session continuation already awaits publication")
        self._pending = (commit, abort, published, complete)

    def retain_context_manager(
        self,
        manager: object,
        *,
        validator: Callable[[], None] | None = None,
    ) -> None:
        """Retain one entered synchronous context until publication finishes."""

        self.require_active()
        callback = getattr(manager, "__exit__", None)
        if (
            not callable(callback)
            or (validator is not None and not callable(validator))
            or any(
                identity == id(manager)
                for identity, _ in self._retained_contexts
            )
        ):
            raise RuntimeError("session continuation context is invalid")

        def close(failure: BaseException | None) -> object:
            validation_failure: BaseException | None = None
            try:
                if validator is not None:
                    validator()
            except BaseException as caught:
                validation_failure = caught
            try:
                outcome = callback(
                    None if failure is None else type(failure),
                    failure,
                    None if failure is None else failure.__traceback__,
                )
            except BaseException as cleanup_failure:
                if validation_failure is None:
                    raise
                try:
                    validation_failure.add_note(
                        "session continuation context cleanup failed: "
                        f"{type(cleanup_failure).__name__}"
                    )
                except BaseException:
                    pass
            if validation_failure is not None:
                raise validation_failure
            return outcome

        self._retained_contexts.append((id(manager), close))

    def retain_async_context_manager(self, manager: object) -> None:
        """Retain one entered asynchronous context until publication finishes."""

        self.require_active()
        callback = getattr(manager, "__aexit__", None)
        if not callable(callback) or any(
            identity == id(manager) for identity, _ in self._retained_contexts
        ):
            raise RuntimeError("session continuation context is invalid")

        def close(failure: BaseException | None) -> object:
            return callback(
                None if failure is None else type(failure),
                failure,
                None if failure is None else failure.__traceback__,
            )

        self._retained_contexts.append((id(manager), close))

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        primary = exc if isinstance(exc, BaseException) else None
        abort: Callable[[], None] | None = None
        complete: Callable[[], None] | None = None
        committed = False
        try:
            if self._pending is not None:
                commit, abort, published, complete = self._pending
                self._pending = None
                if primary is None:
                    try:
                        if published():
                            commit()
                            committed = True
                        else:
                            abort()
                    except BaseException as failure:
                        primary = failure
                        try:
                            abort()
                        except BaseException as abort_failure:
                            try:
                                failure.add_note(
                                    "session continuation abort failed: "
                                    f"{type(abort_failure).__name__}"
                                )
                            except BaseException:
                                pass
                else:
                    try:
                        abort()
                    except BaseException as abort_failure:
                        try:
                            primary.add_note(
                                "session continuation abort failed: "
                                f"{type(abort_failure).__name__}"
                            )
                        except BaseException:
                            pass

            for _, close in reversed(self._retained_contexts):
                try:
                    outcome = close(primary)
                    if inspect.isawaitable(outcome):
                        await outcome
                except BaseException as cleanup_failure:
                    if committed and abort is not None:
                        try:
                            abort()
                        except BaseException as abort_failure:
                            try:
                                cleanup_failure.add_note(
                                    "session continuation abort failed: "
                                    f"{type(abort_failure).__name__}"
                                )
                            except BaseException:
                                pass
                    if primary is None:
                        primary = cleanup_failure
                    else:
                        try:
                            primary.add_note(
                                "session continuation context cleanup failed: "
                                f"{type(cleanup_failure).__name__}"
                            )
                        except BaseException:
                            pass
            if primary is None and committed and complete is not None:
                try:
                    complete()
                except BaseException as completion_failure:
                    if abort is not None:
                        try:
                            abort()
                        except BaseException as abort_failure:
                            try:
                                completion_failure.add_note(
                                    "session continuation abort failed: "
                                    f"{type(abort_failure).__name__}"
                                )
                            except BaseException:
                                pass
                    primary = completion_failure
        finally:
            self._retained_contexts.clear()
            self._owner = None
            self._lock.release()
        if exc_type is None and primary is not None:
            raise primary
