"""Pure parsing for canonical writing-artifact reference directives."""

from __future__ import annotations

import re
import sys

from .references import (
    MAX_REFERENCE_COUNT,
    ArtifactLineRange,
    ArtifactReference,
    ArtifactReferenceBatch,
    ArtifactReferenceDirectiveError,
)


_RANGE_PREFIX = re.compile(r"#L([1-9][0-9]*)-L([1-9][0-9]*)")
_MAX_LINE_NUMBER_TEXT = str(sys.maxsize)


def _positive_line_number(value: str) -> int:
    if len(value) > len(_MAX_LINE_NUMBER_TEXT) or (
        len(value) == len(_MAX_LINE_NUMBER_TEXT)
        and value > _MAX_LINE_NUMBER_TEXT
    ):
        raise ArtifactReferenceDirectiveError("reference line bound is too large")
    return int(value)


def _line_range(text: str, start: int) -> tuple[ArtifactLineRange | None, int]:
    if not text.startswith("#", start):
        return None, start
    if not text.startswith(("#L", "#l"), start):
        return None, start
    if text.startswith("#l", start):
        raise ArtifactReferenceDirectiveError(
            "reference ranges require an uppercase L marker"
        )

    matched = _RANGE_PREFIX.match(text, start)
    if matched is None:
        raise ArtifactReferenceDirectiveError("reference range is malformed")
    end = matched.end()
    if end < len(text):
        continuation = text[end]
        if (
            continuation == "_"
            or continuation.isalnum()
            or f"a{continuation}".isidentifier()
            or text.startswith(("-L", "-l", "#L", "#l"), end)
        ):
            raise ArtifactReferenceDirectiveError("reference range is malformed")
    first = _positive_line_number(matched.group(1))
    last = _positive_line_number(matched.group(2))
    try:
        return ArtifactLineRange(first, last), end
    except ValueError:
        raise ArtifactReferenceDirectiveError(
            "reference range must be ordered"
        ) from None


def _artifact_reference(
    text: str,
    start: int,
) -> tuple[ArtifactReference, int]:
    closing = text.find("}", start + 2)
    if closing < 0:
        raise ArtifactReferenceDirectiveError("reference directive is malformed")
    path = text[start + 2 : closing]
    if not path or any(character.isspace() for character in path):
        raise ArtifactReferenceDirectiveError(
            "reference path must be nonempty and contain no whitespace"
        )

    line_range, end = _line_range(text, closing + 1)
    try:
        return ArtifactReference(path, line_range), end
    except (TypeError, ValueError):
        raise ArtifactReferenceDirectiveError(
            "reference path is not a canonical writing artifact"
        ) from None


def parse_artifact_references(text: str) -> ArtifactReferenceBatch:
    """Parse ordered reference directives and project escaped dollars.

    The caller's source is retained exactly.  An odd run of backslashes before
    a dollar removes one escaping backslash in provider-visible text and makes
    that dollar literal.  Even parity leaves the dollar active and unchanged.
    """

    if type(text) is not str:
        raise TypeError("reference input must be a string")
    if "\x00" in text:
        raise ArtifactReferenceDirectiveError(
            "reference input must not contain NUL"
        )
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ArtifactReferenceDirectiveError(
            "reference input must be strict UTF-8"
        ) from None

    provider_characters: list[str] = []
    references: list[ArtifactReference] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character != "$":
            provider_characters.append(character)
            position += 1
            continue

        backslash_count = 0
        cursor = position - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslash_count += 1
            cursor -= 1
        if backslash_count % 2:
            assert provider_characters[-1] == "\\"
            provider_characters.pop()
            provider_characters.append("$")
            position += 1
            continue

        if text.startswith("$$", position):
            raise ArtifactReferenceDirectiveError(
                "unresolved dollar-dollar syntax is reserved"
            )
        if not text.startswith("${", position):
            provider_characters.append("$")
            position += 1
            continue
        if len(references) >= MAX_REFERENCE_COUNT:
            raise ArtifactReferenceDirectiveError(
                "reference input exceeds the directive limit"
            )

        reference, end = _artifact_reference(text, position)
        references.append(reference)
        provider_characters.extend(text[position:end])
        position = end

    return ArtifactReferenceBatch(
        source_text=text,
        provider_text="".join(provider_characters),
        references=tuple(references),
    )
