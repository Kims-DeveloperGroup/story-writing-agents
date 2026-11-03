"""Own optional desktop input-source control for one shell input session.

The driver alone observes command editing.  Platform adapters own their opaque,
content-free source identities; this owner never rewrites terminal input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TextIO


@dataclass(frozen=True, slots=True)
class InputSourceState:
    """One adapter-owned source identity, including relevant activation state."""

    identity: tuple[str, ...]
    english: bool


class InputSourceAdapter(Protocol):
    def current_source(self) -> InputSourceState | None: ...

    def default_source(self) -> InputSourceState | None: ...

    def english_source(self) -> InputSourceState | None: ...

    def select_source(self, source: InputSourceState) -> None: ...


class InputSourceLifecycle:
    """Retain one startup baseline and enter English once per command scope.

    No adapter is constructed until an explicit live input session starts.
    Adapter failures disable that attempted action without changing text or
    replacing a terminal/provider exception.  An uncertain selection retains
    restoration responsibility until cleanup has attempted the baseline.
    """

    def __init__(
        self,
        adapter_factory: Callable[
            [TextIO, TextIO], InputSourceAdapter | None
        ] | None = None,
    ) -> None:
        self._factory = adapter_factory
        self._active = False
        self._adapter: InputSourceAdapter | None = None
        self._baseline: InputSourceState | None = None
        self._english: InputSourceState | None = None
        self._main_active = False
        self._command = False
        self._suspensions = 0
        self._restoration_pending = False

    def start(
        self, input_stream: TextIO, output_stream: TextIO, *, live: bool,
    ) -> None:
        if self._active:
            raise RuntimeError("input-source session is already active")
        self._active = True
        if not live or self._factory is None:
            return
        try:
            adapter = self._factory(input_stream, output_stream)
            if adapter is None:
                return
            current = adapter.current_source()
            baseline = adapter.default_source() or current
            english = adapter.english_source()
            if not (
                isinstance(current, InputSourceState)
                and isinstance(baseline, InputSourceState)
                and isinstance(english, InputSourceState)
                and english.english
            ):
                return
        except Exception:
            return
        self._adapter, self._baseline, self._english = adapter, baseline, english

    def finish(self) -> None:
        try:
            self._restore()
        finally:
            self._active = self._main_active = self._command = False
            self._adapter = self._baseline = self._english = None
            self._suspensions = 0
            self._restoration_pending = False

    @contextmanager
    def main_input(self) -> Iterator[None]:
        if self._main_active:
            raise RuntimeError("main input-source scope is already active")
        self._main_active = True
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._main_active = self._command = False
            try:
                self._restore()
            except BaseException:
                if not failed:
                    raise

    def observe_buffer(self, buffer: str) -> None:
        if not self._active or not self._main_active:
            return
        command = buffer.startswith("/")
        if command == self._command:
            return
        self._command = command
        if command:
            self._restoration_pending = True
            if not self._suspensions:
                self._change(self._english, retain_english=True)
        else:
            self._restore()

    @contextmanager
    def suspended(self) -> Iterator[None]:
        self._suspensions += 1
        failed = False
        try:
            if self._suspensions == 1:
                self._restore()
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._suspensions -= 1
            if (
                not failed and self._suspensions == 0 and self._active
                and self._main_active and self._command
            ):
                self._restoration_pending = True
                self._change(self._english, retain_english=True)

    def _restore(self) -> None:
        if self._restoration_pending and self._change(self._baseline):
            self._restoration_pending = False

    def _change(
        self, target: InputSourceState | None, *, retain_english: bool = False,
    ) -> bool:
        adapter = self._adapter
        if adapter is None or target is None:
            return False
        try:
            current = adapter.current_source()
            if not isinstance(current, InputSourceState):
                return False
            if current == target or (retain_english and current.english):
                return True
            adapter.select_source(target)
            return adapter.current_source() == target
        except Exception:
            return False
