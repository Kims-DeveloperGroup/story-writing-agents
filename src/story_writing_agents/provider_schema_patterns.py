"""Strict regex compilation and Unicode scalar alphabets for provider schemas."""

from __future__ import annotations

from collections.abc import Iterator
import re
import unicodedata


def _schema_pattern_tokens(pattern: str) -> Iterator[tuple[int, str, bool]]:
    """Locate syntax without treating literal/class/comment text as assertions."""
    in_class = False
    first_in_class = False
    class_negation = False
    verbose = False
    scopes = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\":
            token = pattern[index:index + 2]
            yield index, token, not in_class
            first_in_class = False
            index += len(token)
            continue
        if in_class:
            yield index, character, False
            if character == "]" and not first_in_class:
                in_class = False
            elif character == "^" and first_in_class and not class_negation:
                class_negation = True
            else:
                first_in_class = False
            index += 1
            continue
        if verbose and character == "#":
            end = pattern.find("\n", index)
            end = len(pattern) if end < 0 else end
            yield index, pattern[index:end], False
            index = end
            continue
        if pattern.startswith("(?#", index):
            end = index + 3
            while end < len(pattern):
                if pattern[end] == "\\" and end + 1 < len(pattern):
                    end += 2
                elif pattern[end] == ")":
                    end += 1
                    break
                else:
                    end += 1
            yield index, pattern[index:end], False
            index = end
            continue
        if character == "(":
            end = index + 2
            while end < len(pattern) and pattern[end] in "aiLmsux-":
                end += 1
            if (pattern.startswith("(?", index) and end > index + 2
                    and end < len(pattern) and pattern[end] in ":)"):
                added, _, removed = pattern[index + 2:end].partition("-")
                if pattern[end] == ":":
                    scopes.append(verbose)
                verbose = (verbose or "x" in added) and "x" not in removed
                yield index, pattern[index:end + 1], True
                index = end + 1
                continue
            scopes.append(verbose)
        elif character == ")" and scopes:
            verbose = scopes.pop()
        elif character == "[":
            in_class = True
            first_in_class = True
            class_negation = False
        yield index, character, True
        index += 1


def compile_schema_pattern(pattern: str) -> re.Pattern[str]:
    """Compile the canonical strict-EOF token using Python 3.11's equivalent anchor."""
    if type(pattern) is not str:
        raise TypeError("schema pattern must be exact text")
    if r"\z" not in pattern:
        return re.compile(pattern)
    pieces = []
    cursor = 0
    for index, token, outside in _schema_pattern_tokens(pattern):
        if outside and token == r"\z":
            pieces.extend((pattern[cursor:index], r"\Z"))
            cursor = index + 2
    pieces.append(pattern[cursor:])
    return re.compile("".join(pieces))


def unicode_scalar_classes(categories: tuple[str, ...], exclusions: tuple[str, ...]) -> tuple[str, ...]:
    """Derive related consuming alphabets once from the owner's Unicode table.

    Callers freeze these declarations at module construction, never per response.
    Surrogates are absent as values and as native range endpoints.
    """
    if (type(categories) is not tuple or any(type(item) is not str for item in categories)
            or type(exclusions) is not tuple or not exclusions
            or any(type(item) is not str for item in exclusions)):
        raise TypeError("Unicode alphabets require exact category and exclusion tuples")
    ranges = []
    if categories:
        for codepoint in range(0x110000):
            if 0xD800 <= codepoint <= 0xDFFF or unicodedata.category(chr(codepoint)) in categories:
                continue
            if ranges and ranges[-1][1] + 1 == codepoint:
                ranges[-1] = (ranges[-1][0], codepoint)
            else:
                ranges.append((codepoint, codepoint))
    else:
        ranges = [(0, 0xD7FF), (0xE000, 0x10FFFF)]
    def token(codepoint):
        character = chr(codepoint)
        return "\\" + character if character in "\\[]^-" else character
    results = []
    for excluded in exclusions:
        points = sorted({ord(character) for character in excluded})
        selected = []
        for first, last in ranges:
            cursor = first
            for point in points:
                if cursor <= point <= last:
                    if cursor < point:
                        selected.append((cursor, point - 1))
                    cursor = point + 1
            if cursor <= last:
                selected.append((cursor, last))
        results.append("[" + "".join(token(first) + ("-" + token(last) if first != last else "")
                                    for first, last in selected) + "]")
    return tuple(results)
