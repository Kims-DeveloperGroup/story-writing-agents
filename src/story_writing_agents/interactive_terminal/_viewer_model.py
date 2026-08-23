"""Immutable document, state, viewport, and result values for text viewing."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum


_MAX_TEXT_UTF8_BYTES = 8 * 1024 * 1024
_MAX_TEXT_LOGICAL_LINES = 65_536
_MAX_FALLBACK_UTF8_BYTES = 16 * 1024 * 1024
_FALLBACK_PROMPT = "Select START-END or /cancel: "
_EMPTY_DOCUMENT = "<empty document>"
_EMPTY_NOTICE = "No selectable lines; cancel to close."
_INVALID_NOTICE = "Enter a valid one-based START-END range."


class TextViewerExitKind(StrEnum):
    """Terminal-independent ways one viewer interaction can finish."""

    SELECTED = "selected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    EOF = "eof"


class TextViewerNotice(StrEnum):
    """Fixed viewer-local validation notice without caller content."""

    EMPTY_DOCUMENT = "empty_document"
    INVALID_RANGE = "invalid_range"


@dataclass(frozen=True, slots=True)
class TextViewerDocument:
    """One supplied UTF-8 text value and its LF-only logical line facts."""

    text: str
    display_name: str
    lines: tuple[str, ...] = field(init=False)
    utf8_bytes: int = field(init=False)

    def __post_init__(self) -> None:
        """Validate before any terminal collaborator can observe the document."""

        if type(self.text) is not str:
            raise TypeError("viewer text must be a string")
        if type(self.display_name) is not str:
            raise TypeError("display_name must be a string")
        if not self.display_name or self.display_name.isspace():
            raise ValueError("display_name must not be blank")
        if "\n" in self.display_name:
            raise ValueError("display_name must be one logical line")
        logical_lines = _logical_line_count(self.text)
        if logical_lines > _MAX_TEXT_LOGICAL_LINES:
            raise ValueError(
                "viewer text exceeds the 65,536 logical-line limit"
            )
        try:
            encoded = self.text.encode("utf-8", errors="strict")
            self.display_name.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "viewer text and display_name must be valid UTF-8"
            ) from exc
        if len(encoded) > _MAX_TEXT_UTF8_BYTES:
            raise ValueError("viewer text exceeds the 8 MiB UTF-8 limit")
        _validate_fallback_projection(
            self.text,
            self.display_name,
            logical_lines,
        )

        if not self.text:
            lines: tuple[str, ...] = ()
        else:
            derived = self.text.split("\n")
            if derived[-1] == "":
                derived.pop()
            lines = tuple(derived)
        object.__setattr__(self, "lines", lines)
        object.__setattr__(self, "utf8_bytes", len(encoded))

    @property
    def line_count(self) -> int:
        """Return the number of selectable LF-derived logical lines."""

        return len(self.lines)


def _logical_line_count(text: str) -> int:
    """Count LF-derived logical lines without allocating their records."""

    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _validate_fallback_projection(
    text: str,
    display_name: str,
    logical_lines: int,
) -> None:
    """Reject a maximum complete fallback projection beyond 16 MiB.

    Accounting includes the longest valid header and notice for the document,
    every numbered record, all separators, and the complete prompt. It runs
    before logical-line materialization or any terminal collaborator access.
    """

    display_name_bytes = _sanitized_utf8_bytes(display_name)
    if logical_lines == 0:
        fixed_bytes = sum(
            (
                len("[viewer]  | 0 lines"),
                display_name_bytes,
                len(_EMPTY_DOCUMENT),
                len(_EMPTY_NOTICE),
                len(_FALLBACK_PROMPT),
                3,  # Newlines between the four complete fallback rows.
            )
        )
    else:
        digits = len(str(logical_lines))
        fixed_bytes = sum(
            (
                len(f"[viewer]  | lines {logical_lines}-{logical_lines}"),
                display_name_bytes,
                logical_lines * (digits + 6),
                len(_INVALID_NOTICE),
                len(_FALLBACK_PROMPT),
                logical_lines + 2,
            )
        )
    if fixed_bytes > _MAX_FALLBACK_UTF8_BYTES:
        raise ValueError(
            "viewer fallback projection exceeds the 16 MiB UTF-8 limit"
        )

    projected_bytes = fixed_bytes
    for character in text:
        if character == "\n":
            continue
        projected_bytes += _sanitized_character_utf8_bytes(character)
        if projected_bytes > _MAX_FALLBACK_UTF8_BYTES:
            raise ValueError(
                "viewer fallback projection exceeds the 16 MiB UTF-8 limit"
            )


def _sanitized_utf8_bytes(text: str) -> int:
    """Count exact UTF-8 bytes after terminal-visible sanitization."""

    return sum(_sanitized_character_utf8_bytes(character) for character in text)


def _sanitized_character_utf8_bytes(character: str) -> int:
    """Count one source code point using the terminal sanitizer contract."""

    category = unicodedata.category(character)
    if category in {"Cc", "Cf", "Cs"} or character in {"\u2028", "\u2029"}:
        value = ord(character)
        return 4 if value <= 0xFF else 6 if value <= 0xFFFF else 10
    return len(character.encode("utf-8", errors="strict"))


@dataclass(frozen=True, slots=True)
class TextViewerState:
    """One immutable call-local navigation and anchored-selection state."""

    document: TextViewerDocument
    active_line: int | None
    anchor_line: int | None = None
    notice: TextViewerNotice | None = None

    def __post_init__(self) -> None:
        """Correlate active, anchor, and notice values with the document."""

        if type(self.document) is not TextViewerDocument:
            raise TypeError("document must be a TextViewerDocument")
        if self.notice is not None and not isinstance(self.notice, TextViewerNotice):
            raise TypeError("notice must be a TextViewerNotice or None")
        count = self.document.line_count
        if count == 0:
            if self.active_line is not None or self.anchor_line is not None:
                raise ValueError("empty viewer state cannot identify a line")
            if self.notice is not TextViewerNotice.EMPTY_DOCUMENT:
                raise ValueError("empty viewer state requires its empty notice")
        else:
            if type(self.active_line) is not int:
                raise TypeError("active_line must be an integer for nonempty text")
            if self.active_line < 1 or self.active_line > count:
                raise ValueError("active_line must identify a document line")
            if self.anchor_line is not None:
                if type(self.anchor_line) is not int:
                    raise TypeError("anchor_line must be an integer or None")
                if self.anchor_line < 1 or self.anchor_line > count:
                    raise ValueError("anchor_line must identify a document line")
            if self.notice is TextViewerNotice.EMPTY_DOCUMENT:
                raise ValueError("nonempty viewer state cannot use its empty notice")

    @property
    def selection(self) -> tuple[int, int] | None:
        """Return the normalized anchored selection, if one is active."""

        if self.active_line is None or self.anchor_line is None:
            return None
        return (
            min(self.active_line, self.anchor_line),
            max(self.active_line, self.anchor_line),
        )

    @property
    def commit_range(self) -> tuple[int, int] | None:
        """Return the inclusive range Enter would commit in this state."""

        if self.active_line is None:
            return None
        return self.selection or (self.active_line, self.active_line)


@dataclass(frozen=True, slots=True)
class TextViewerViewport:
    """One height-bounded contiguous projection of immutable viewer state."""

    display_name: str
    total_lines: int
    frame_height: int
    start_line: int | None
    lines: tuple[str, ...]
    active_line: int | None
    selection: tuple[int, int] | None
    notice: TextViewerNotice | None

    def __post_init__(self) -> None:
        """Validate the projected slice and its navigation correlations."""

        if type(self.display_name) is not str or not self.display_name:
            raise ValueError("display_name must be nonempty text")
        if type(self.total_lines) is not int or self.total_lines < 0:
            raise ValueError("total_lines must be a nonnegative integer")
        if type(self.frame_height) is not int or self.frame_height <= 0:
            raise ValueError("frame_height must be a positive integer")
        if type(self.lines) is not tuple or any(
            type(line) is not str for line in self.lines
        ):
            raise TypeError("lines must be a tuple of strings")
        if self.notice is not None and not isinstance(self.notice, TextViewerNotice):
            raise TypeError("notice must be a TextViewerNotice or None")
        if self.total_lines == 0:
            if (
                self.start_line is not None
                or self.lines
                or self.active_line is not None
                or self.selection is not None
            ):
                raise ValueError("an empty viewport cannot project document lines")
            if self.notice is not TextViewerNotice.EMPTY_DOCUMENT:
                raise ValueError("an empty viewport requires its empty notice")
        else:
            if type(self.start_line) is not int or self.start_line < 1:
                raise ValueError("start_line must identify the first projected line")
            if not self.lines:
                raise ValueError("a nonempty viewport must project at least one line")
            end_line = self.start_line + len(self.lines) - 1
            if end_line > self.total_lines:
                raise ValueError("viewport lines must remain inside the document")
            if type(self.active_line) is not int or not (
                self.start_line <= self.active_line <= end_line
            ):
                raise ValueError("the active line must remain inside the viewport")
            if self.notice is TextViewerNotice.EMPTY_DOCUMENT:
                raise ValueError("a nonempty viewport cannot use its empty notice")
        if self.selection is not None:
            if (
                type(self.selection) is not tuple
                or len(self.selection) != 2
                or any(type(value) is not int for value in self.selection)
            ):
                raise TypeError("selection must be an inclusive integer tuple or None")
            start, end = self.selection
            if start < 1 or end < start or end > self.total_lines:
                raise ValueError("selection must remain inside the document")


@dataclass(frozen=True, slots=True)
class TextViewerResult:
    """One content-free exit classification and optional inclusive range."""

    kind: TextViewerExitKind
    line_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        """Require a range exactly for the selected exit kind."""

        if not isinstance(self.kind, TextViewerExitKind):
            raise TypeError("kind must be a TextViewerExitKind")
        if self.kind is TextViewerExitKind.SELECTED:
            if (
                type(self.line_range) is not tuple
                or len(self.line_range) != 2
                or any(type(value) is not int for value in self.line_range)
            ):
                raise TypeError("selected results require an inclusive integer tuple")
            start, end = self.line_range
            if start < 1 or end < start:
                raise ValueError("selected range must be one-based and ordered")
        elif self.line_range is not None:
            raise ValueError("non-selection results cannot carry a line range")


def dispose_text_viewer(state: TextViewerState) -> None:
    """Mark the deterministic boundary where a caller drops viewer-local state."""

    if type(state) is not TextViewerState:
        raise TypeError("state must be a TextViewerState")


__all__ = [
    "TextViewerDocument",
    "TextViewerExitKind",
    "TextViewerNotice",
    "TextViewerResult",
    "TextViewerState",
    "TextViewerViewport",
    "dispose_text_viewer",
]
