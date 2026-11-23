"""Release the resources of a closed Backend after its last binding exits."""

from __future__ import annotations

from asyncio import Lock
from collections.abc import Callable, Iterable

from .provider_adapter import ProviderAdapter


def release_backend_resources(
    *,
    claim_tokens: Callable[[], dict[str, str]],
    thread_locks: Callable[[], dict[tuple[str, str, str], Lock]],
    context_thread_locks: Callable[[], dict[tuple[str, ...], Lock]],
    active_adapters: Callable[[], Iterable[ProviderAdapter]],
    release_access_claim: Callable[[str, str], None],
    record_error: Callable[..., None],
) -> bool:
    """Release claims, clear the lock maps, and close the current adapters."""

    release_failed = False
    for session, token in tuple(claim_tokens().items()):
        try:
            release_access_claim(session, token)
        except Exception as exc:
            release_failed = True
            record_error(exc, session_id=session)
    claim_tokens().clear()
    thread_locks().clear()
    context_thread_locks().clear()
    for adapter in active_adapters():
        try:
            adapter.close()
        except Exception as exc:
            release_failed = True
            record_error(exc)
    return release_failed
