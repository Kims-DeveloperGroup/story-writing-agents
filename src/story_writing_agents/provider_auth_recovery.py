"""Private, operation-scoped authentication handoff and provider activity gate."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from .errors import ProviderAuthenticationRequiredError


_RECOVERY: ContextVar[ProviderAuthRecovery | None] = ContextVar(
    "provider_auth_recovery", default=None,
)


_REPLAY: ContextVar[
    tuple[ProviderAuthenticationRequiredError, Callable[[], bool]] | None
] = ContextVar("provider_auth_replay", default=None)


@contextmanager
def authentication_replay(
    failure: ProviderAuthenticationRequiredError,
    source_validator: Callable[[], bool],
) -> Iterator[None]:
    """Bind the original failure and its source owner's veto to one retry."""

    token = _REPLAY.set((failure, source_validator))
    try:
        yield
    finally:
        failure.clear_replay_validator()
        _REPLAY.reset(token)


def validate_provider_replay() -> None:
    """Veto drift only after provider activity admission, before submission."""

    replay = _REPLAY.get()
    if replay is None:
        return
    failure, source_validator = replay
    valid = False
    try:
        valid = failure.validate_replay() and source_validator() is True
    except Exception:
        pass
    if not valid:
        failure.abort_replay()
        raise failure
    failure.clear_replay_validator()


class ProviderAuthRecovery:
    """Coalesce one auth generation while draining operation-owned providers.

    The synchronous terminal owner drives requests only after ``wait_ready``.
    This object performs no terminal, credential, or process operations.
    """

    def __init__(self) -> None:
        self._active = 0
        self._generations: dict[str, int] = {}
        self._pending: dict[tuple[str, int], asyncio.Future[bool]] = {}
        self._completed: dict[tuple[str, int], bool] = {}
        self._ready = asyncio.Event()
        self._available = asyncio.Event()
        self._available.set()
        self._closed = False

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _RECOVERY.set(self)
        try:
            yield
        finally:
            _RECOVERY.reset(token)

    @asynccontextmanager
    async def activity(self, provider: str) -> AsyncIterator[int]:
        while not self._available.is_set():
            await self._available.wait()
        if self._closed:
            raise asyncio.CancelledError
        self._active += 1
        try:
            yield self._generations.get(provider, 0)
        finally:
            self._active -= 1
            self._update_ready()

    async def request(self, failure: ProviderAuthenticationRequiredError) -> bool:
        if self._closed:
            return False
        key = (failure.provider, failure.auth_generation)
        if key in self._completed:
            return self._completed[key]
        future = self._pending.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._pending[key] = future
            self._available.clear()
        self._update_ready()
        return await asyncio.shield(future)

    def _update_ready(self) -> None:
        if self._pending and self._active == 0:
            self._ready.set()
        else:
            self._ready.clear()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    def next_request(self) -> tuple[str, int]:
        if not self._ready.is_set() or self._active:
            raise RuntimeError("provider authentication handoff is not drained")
        return min(self._pending)

    def resolve(self, key: tuple[str, int], authenticated: bool) -> None:
        future = self._pending.pop(key)
        self._completed[key] = authenticated
        self._generations[key[0]] = key[1] + 1
        if not future.done():
            future.set_result(authenticated)
        if not self._pending:
            self._available.set()
        self._update_ready()

    def close(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_result(False)
        self._pending.clear()
        self._available.set()
        self._ready.clear()


@asynccontextmanager
async def provider_activity(provider: str) -> AsyncIterator[int]:
    """Track one physical invocation only within an installed recovery scope."""

    recovery = _RECOVERY.get()
    if recovery is None:
        yield 0
    else:
        async with recovery.activity(provider) as generation:
            yield generation


async def request_authentication(failure: ProviderAuthenticationRequiredError) -> bool:
    """Offer native assistance without conferring physical replay authority."""

    recovery = _RECOVERY.get()
    return False if recovery is None else await recovery.request(failure)


__all__: tuple[str, ...] = ()
