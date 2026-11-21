"""Call entry, retirement, and final release for one provider binding."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from .errors import BackendProtocolError


class ProviderBindingCallLifetime:
    """Retain a binding until its last entered call exits after retirement."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_calls = 0
        self.retired = False
        self.released = False

    @contextmanager
    def use(
        self,
        *,
        is_backend_closed: Callable[[], bool],
        finalize: Callable[[], None],
    ) -> Iterator[None]:
        """Keep the resources for one entered call alive."""
        with self.lock:
            if is_backend_closed():
                raise BackendProtocolError("agent backend is closed")
            if self.retired:
                raise BackendProtocolError("bound agent is retired")
            self.active_calls += 1
        try:
            yield
        finally:
            finalize_after_call = False
            with self.lock:
                self.active_calls -= 1
                if self.retired and self.active_calls == 0 and not self.released:
                    self.released = True
                    finalize_after_call = True
            if finalize_after_call:
                finalize()

    def retire(self) -> bool:
        """Record retirement and return whether the caller should finalize."""
        with self.lock:
            self.retired = True
            if self.active_calls == 0 and not self.released:
                self.released = True
                return True
        return False
