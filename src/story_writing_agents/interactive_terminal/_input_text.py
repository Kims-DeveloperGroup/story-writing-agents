"""Pure source-coordinate text units for terminal input editing and display."""

from __future__ import annotations

import unicodedata
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass

from ._model import display_character_width, sanitize_terminal_text


def _hangul_kind(character: str) -> str | None:
    value = ord(character)
    if 0x1100 <= value <= 0x115F or 0xA960 <= value <= 0xA97C:
        return "L"
    if 0x1160 <= value <= 0x11A7 or 0xD7B0 <= value <= 0xD7C6:
        return "V"
    if 0x11A8 <= value <= 0x11FF or 0xD7CB <= value <= 0xD7FB:
        return "T"
    if 0xAC00 <= value <= 0xD7A3:
        return "LVT" if (value - 0xAC00) % 28 else "LV"
    return None


def _joins(previous: str, character: str) -> bool:
    """Apply Hangul syllable joining and retain attached combining marks."""

    left, right = _hangul_kind(previous), _hangul_kind(character)
    return (
        (left == "L" and right in {"L", "V", "LV", "LVT"})
        or (left in {"LV", "V"} and right in {"V", "T"})
        or (left in {"LVT", "T"} and right == "T")
        or unicodedata.category(character) in {"Mn", "Mc", "Me"}
    )


@dataclass(frozen=True, slots=True)
class InputTextUnit:
    """One editable unit retaining exact source boundaries and safe display."""

    start: int
    end: int
    display: str

    @property
    def cells(self) -> int:
        cells = sum(display_character_width(character) for character in self.display)
        # A conjoining Hangul unit occupies one syllable block even when an
        # archaic jamo sequence has no single precomposed NFC code point.
        if any(_hangul_kind(character) is not None for character in self.display):
            return min(2, cells)
        return cells


def input_text_units(text: str) -> Iterator[InputTextUnit]:
    """Project Hangul units without modifying the caller's original string."""

    start = 0
    for end in range(1, len(text) + 1):
        if end < len(text) and _joins(text[end - 1], text[end]):
            continue
        source = text[start:end]
        display = (
            unicodedata.normalize("NFC", source)
            if any(_hangul_kind(character) is not None for character in source)
            else source
        )
        yield InputTextUnit(
            start, end, sanitize_terminal_text(display).replace("\n", "\\x0a")
        )
        start = end


def input_text_boundaries(text: str) -> tuple[int, ...]:
    """Return source offsets suitable for cursor movement and deletion."""

    return (0, *(unit.end for unit in input_text_units(text)))


def align_input_cursor(text: str, cursor: int) -> int:
    """Resolve an existing interior source offset to its unit's end."""

    boundaries = input_text_boundaries(text)
    return boundaries[bisect_left(boundaries, cursor)]


def project_input_text(text: str, cursor: int) -> tuple[str, int]:
    """Map the original cursor to safe display text, aligning interior offsets."""

    parts: list[str] = []
    offset = 0
    projected_cursor: int | None = None
    for unit in input_text_units(text):
        if projected_cursor is None and cursor <= unit.start:
            projected_cursor = offset
        parts.append(unit.display)
        offset += len(unit.display)
        if projected_cursor is None and cursor <= unit.end:
            projected_cursor = offset
    return "".join(parts), offset if projected_cursor is None else projected_cursor
