"""Pure readable metadata projection without interpreting literal escape text."""

from __future__ import annotations

import unicodedata


_LINE_BREAKS = frozenset({"\n", "\r", "\v", "\f", "\x85", "\u2028", "\u2029"})
_STRING_STARTS = frozenset({"P", "X", "]", "^", "_"})
_C1_STARTS = "\x90\x98\x9b\x9d\x9e\x9f"
_CONTROL_MARKER = "[control]"


def format_metadata_text(text: str, *, inline: bool = False) -> str:
    """Keep metadata readable while consuming terminal instructions as data.

    Unlike the literal terminal sanitizer, this projection uses whitespace and
    readable markers rather than numeric escapes. Backslash spellings remain
    literal; only actual control characters enter the sequence scanner.
    """
    if not isinstance(text, str):
        raise TypeError("metadata text must be a string")
    if type(inline) is not bool:
        raise TypeError("inline must be a bool")
    text = str.__str__(text)
    rendered: list[str] = []
    offset = 0
    while offset < len(text):
        character = text[offset]
        if character in _LINE_BREAKS:
            rendered.append(" " if inline else "\n")
            offset += 2 if character == "\r" and text.startswith("\n", offset + 1) else 1
        elif character == "\t":
            rendered.append("    ")
            offset += 1
        elif character == "\x1b" or character in _C1_STARTS:
            offset, complete = _sequence_end(text, offset)
            if not complete:
                rendered.append(_CONTROL_MARKER)
        else:
            category = unicodedata.category(character)
            rendered.append(
                "\ufffd" if category == "Cs" else
                _CONTROL_MARKER if category in {"Cc", "Cf"} else character
            )
            offset += 1
    return "".join(rendered)


def _sequence_end(text: str, offset: int) -> tuple[int, bool]:
    """Consume one ESC/C1 sequence, stopping malformed input at a line break."""
    character = text[offset]
    if character == "\x1b":
        offset += 1
        if offset == len(text):
            return offset, False
        introducer = text[offset]
        offset += 1
    else:
        introducer = "PX[]^_"[_C1_STARTS.index(character)]
        offset += 1
    if introducer == "[":
        return _csi_end(text, offset)
    if introducer in _STRING_STARTS:
        return _string_end(text, offset, osc=introducer == "]")
    offset -= 1
    while offset < len(text) and " " <= text[offset] <= "/":
        offset += 1
    if offset < len(text) and "0" <= text[offset] <= "~":
        return offset + 1, True
    return _malformed_end(text, offset), False


def _csi_end(text: str, offset: int) -> tuple[int, bool]:
    intermediate = False
    while offset < len(text):
        character = text[offset]
        if "@" <= character <= "~":
            return offset + 1, True
        if " " <= character <= "/":
            intermediate = True
        elif not ("0" <= character <= "?" and not intermediate):
            return _malformed_end(text, offset), False
        offset += 1
    return offset, False


def _string_end(text: str, offset: int, *, osc: bool) -> tuple[int, bool]:
    while offset < len(text):
        character = text[offset]
        if character in _LINE_BREAKS:
            return offset, False
        if character == "\x9c" or (osc and character == "\x07"):
            return offset + 1, True
        if text.startswith("\x1b\\", offset):
            return offset + 2, True
        offset += 1
    return offset, False


def _malformed_end(text: str, offset: int) -> int:
    while offset < len(text) and text[offset] not in _LINE_BREAKS:
        offset += 1
    return offset


__all__ = ["format_metadata_text"]
