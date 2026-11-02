"""Joined worker execution for complete synchronous preparation steps."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar


_T = TypeVar("_T")


async def run_blocking(
    operation: Callable[..., _T], /, *args: object, **kwargs: object,
) -> _T:
    """Finish one worker before cancellation can release its caller's resources.

    Callers pass a complete transaction or a pure computation, never a live
    thread-bound guard. ``to_thread`` preserves the calling context; mutable
    runtime state and terminal callbacks remain the event-loop owner's job.
    """

    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as failure:
            if cancellation is None:
                cancellation = failure
        except BaseException:
            # Retrieve the worker's outcome below, including after cancellation.
            break
    try:
        result = worker.result()
    except BaseException as failure:
        if cancellation is not None:
            cancellation.add_note(f"blocking preparation failed: {type(failure).__name__}")
            raise cancellation from failure
        raise
    if cancellation is not None:
        raise cancellation
    return result


__all__ = ("run_blocking",)
