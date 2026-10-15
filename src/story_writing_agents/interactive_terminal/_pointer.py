"""Bounded, side-effect-free SGR mouse report decoding."""

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """One terminal-cell pointer report; coordinates are one based."""

    button: int
    column: int
    row: int
    pressed: bool


def decode_pointer(sequence: str) -> PointerEvent | None:
    """Discard malformed, oversized, or unsupported mouse reports."""
    if len(sequence) > 48:
        return None
    match = re.fullmatch(r"\x1b\[<([0-9]{1,3});([0-9]{1,5});([0-9]{1,5})([Mm])", sequence)
    if match is None:
        return None
    button, column, row = map(int, match.group(1, 2, 3))
    if button not in {0, 1, 2, 3, 64, 65} or not (0 < column <= 32767 and 0 < row <= 32767):
        return None
    return PointerEvent(button, column, row, match.group(4) == "M")
