"""Generic borrowed-host contract for one transient external viewer."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Protocol, TypeAlias, runtime_checkable


_DEFAULT_ROWS = 24
_MAX_LINE_COMMAND_CHARACTERS = 8_192
_SGR = re.compile(r"\x1b\[[0-9;]*m")


@runtime_checkable
class ExternalViewerHost(Protocol):
    """Borrow terminal presentation and input without transferring ownership."""

    def terminal_size(self) -> tuple[int, int]:
        """Return the complete-frame ``(columns, rows)`` geometry."""

        ...

    def color_enabled(self) -> bool:
        """Return whether semantic ANSI SGR decoration is allowed."""

        ...

    def present(self, frame: str) -> None:
        """Present one complete frame through the owning terminal."""

        ...

    def read_event(self) -> str | None:
        """Return one closed viewer event or ``None`` for EOF."""

        ...

    def close_view(self) -> None:
        """Idempotently close this transient view."""

        ...


ExternalViewOperation: TypeAlias = Callable[[ExternalViewerHost], None]


def _require_external_view_frame(frame: object, *, color_enabled: bool) -> str:
    """Accept printable text, newlines, and optionally ANSI SGR only."""

    if type(frame) is not str:
        raise TypeError("external viewer frame must be text")
    if type(color_enabled) is not bool:
        raise TypeError("color_enabled must be a bool")
    plain = _SGR.sub("", frame)
    if "\x1b" in plain or (not color_enabled and plain != frame):
        raise ValueError("external viewer frame contains unauthorized controls")
    for character in plain:
        if character == "\n":
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Cs"} or character in {
            "\u2028",
            "\u2029",
        }:
            raise ValueError("external viewer frame contains unsafe text")
    return frame


def _line_view_event(value: object) -> str:
    """Map one bounded ordinary-line command to the closed viewer vocabulary."""

    if not isinstance(value, str):
        raise TypeError("external viewer input must be text")
    line = str.__str__(value)
    if "\r" in line or "\n" in line:
        raise ValueError("external viewer input must be one line")
    if len(line) > _MAX_LINE_COMMAND_CHARACTERS:
        return "unknown"
    command = line.strip()
    mapping = {
        "q": "close",
        "quit": "close",
        "j": "down",
        "down": "down",
        "k": "up",
        "up": "up",
        "pgdn": "page_down",
        "pgup": "page_up",
        "n": "next_match",
        "N": "previous_match",
        "m": "toggle_mode",
        "h": "help",
        "?": "help",
        "esc": "cancel",
        "c": "clear_search",
        "clear": "clear_search",
    }
    if command in mapping:
        return mapping[command]
    if command.startswith("g "):
        line_number = command[2:].strip()
        if line_number and "\t" not in line_number:
            return f"goto\t{line_number}"
    if command.startswith("/ "):
        parts = command[2:].split(maxsplit=1)
        if (
            len(parts) == 2
            and parts[0]
            and parts[1]
            and "\t" not in parts[0]
            and "\t" not in parts[1]
        ):
            return f"search\t{parts[0]}\t{parts[1]}"
    return "unknown"


class _BorrowedLineViewerHost:
    """Adapt borrowed line callbacks without owning or closing their streams."""

    __slots__ = ("_closed", "_columns", "_read_line", "_rows", "_write_text")

    def __init__(
        self,
        read_line: Callable[[str], str],
        write_text: Callable[[str], None],
        columns: int,
        *,
        rows: int = _DEFAULT_ROWS,
    ) -> None:
        if not callable(read_line):
            raise TypeError("read_line must be callable")
        if not callable(write_text):
            raise TypeError("write_text must be callable")
        for name, value in (("columns", columns), ("rows", rows)):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        self._read_line = read_line
        self._write_text = write_text
        self._columns = columns
        self._rows = rows
        self._closed = False

    def terminal_size(self) -> tuple[int, int]:
        return self._columns, self._rows

    def color_enabled(self) -> bool:
        return False

    def present(self, frame: str) -> None:
        if self._closed:
            raise RuntimeError("external viewer host is closed")
        validated = _require_external_view_frame(frame, color_enabled=False)
        self._write_text(f"{validated}\n")

    def read_event(self) -> str | None:
        if self._closed:
            return None
        try:
            value = self._read_line("viewer> ")
        except EOFError:
            return None
        except KeyboardInterrupt:
            return "cancel"
        return _line_view_event(value)

    def close_view(self) -> None:
        self._closed = True


__all__ = ["ExternalViewOperation", "ExternalViewerHost"]
