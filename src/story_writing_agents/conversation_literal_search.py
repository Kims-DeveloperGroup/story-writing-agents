"""Pure deterministic literal-search mechanics for conversation adapters."""

from __future__ import annotations

import unicodedata


CONVERSATION_LITERAL_NORMALIZATION_VERSION = 1

_MATCH_ORDER = {
    "exact_subject": 0,
    "subject_prefix": 1,
    "subject_substring": 2,
    "transcript": 3,
}
_PREFERRED_LEFT_CONTEXT_CHARACTERS = 80


def _positive_integer(value: int, *, label: str) -> int:
    """Return one exact positive integer."""

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def _strict_utf8(value: str, *, label: str) -> str:
    """Return one exact strict-UTF-8 string."""

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _has_control_or_format(value: str) -> bool:
    """Report whether text contains a Unicode ``C`` category character."""

    return any(unicodedata.category(character).startswith("C") for character in value)


def normalize_conversation_literal_text(value: str) -> str:
    """Return the canonical searchable form of conversation prose.

    The pure version-1 transformation applies NFKC, Unicode case folding,
    replaces every Unicode ``C`` category character with a space, and
    collapses whitespace. The source remains unchanged.
    """

    source = _strict_utf8(value, label="conversation literal text")
    normalized = unicodedata.normalize("NFKC", source).casefold()
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    return " ".join(without_controls.split())


def normalize_conversation_literal_query(
    value: str,
    *,
    max_characters: int,
) -> str:
    """Return one bounded canonical literal-search query.

    Raw and normalized text must both fit ``max_characters``. Queries must be
    strict UTF-8, nonblank, and free of Unicode control or format characters.
    """

    maximum = _positive_integer(
        max_characters,
        label="maximum conversation search query characters",
    )
    raw = _strict_utf8(value, label="conversation search query")
    if not raw.strip():
        raise ValueError("conversation search query must not be blank")
    if len(raw) > maximum:
        raise ValueError(f"conversation search query exceeds {maximum} characters")
    if _has_control_or_format(raw):
        raise ValueError(
            "conversation search query must not contain control or format characters"
        )
    normalized = normalize_conversation_literal_text(raw)
    if not normalized:
        raise ValueError("conversation search query must not be blank")
    if len(normalized) > maximum:
        raise ValueError(
            f"normalized conversation search query exceeds {maximum} characters"
        )
    return normalized


def classify_conversation_literal_subject(
    normalized_subject: str,
    normalized_query: str,
) -> str | None:
    """Return the strongest literal subject-match value, if any."""

    subject = _strict_utf8(
        normalized_subject,
        label="normalized conversation subject",
    )
    query = _strict_utf8(
        normalized_query,
        label="normalized conversation search query",
    )
    if subject == query:
        return "exact_subject"
    if subject.startswith(query):
        return "subject_prefix"
    if query in subject:
        return "subject_substring"
    return None


def build_conversation_literal_snippet(
    searchable: str,
    normalized_query: str,
    *,
    max_characters: int,
    max_utf8_bytes: int,
) -> str:
    """Return a deterministic bounded snippet around the first query match."""

    source = _strict_utf8(searchable, label="conversation literal snippet source")
    query = _strict_utf8(
        normalized_query,
        label="normalized conversation search query",
    )
    character_limit = _positive_integer(
        max_characters,
        label="maximum conversation search snippet characters",
    )
    byte_limit = _positive_integer(
        max_utf8_bytes,
        label="maximum conversation search snippet UTF-8 bytes",
    )
    match_start = source.find(query)
    if match_start < 0:
        raise ValueError("snippet source does not contain the normalized query")
    match_end = match_start + len(query)
    left_length = min(_PREFERRED_LEFT_CONTEXT_CHARACTERS, match_start)

    def render(start: int, end: int) -> str:
        return (
            ("…" if start > 0 else "")
            + source[start:end]
            + ("…" if end < len(source) else "")
        )

    def fits(candidate: str) -> bool:
        return (
            len(candidate) <= character_limit
            and len(candidate.encode("utf-8")) <= byte_limit
        )

    while left_length >= 0:
        start = match_start - left_length
        if fits(render(start, match_end)):
            break
        left_length -= 1
    if left_length < 0:
        raise ValueError("normalized query cannot fit within the snippet limits")

    start = match_start - left_length
    end = match_end
    while end < len(source) and fits(render(start, end + 1)):
        end += 1
    snippet = render(start, end)
    if not fits(snippet) or query not in snippet:
        raise ValueError("conversation search snippet is inconsistent")
    return snippet


def conversation_literal_sort_key(
    match_kind: str,
    normalized_subject: str,
    conversation_id: str,
) -> tuple[int, str, str]:
    """Return the stable literal match-rank, subject, and identity key."""

    if type(match_kind) is not str or match_kind not in _MATCH_ORDER:
        raise ValueError("conversation literal match kind is invalid")
    subject = _strict_utf8(
        normalized_subject,
        label="normalized conversation subject",
    )
    identity = _strict_utf8(conversation_id, label="conversation ID")
    return (_MATCH_ORDER[match_kind], subject, identity)


__all__ = [
    "CONVERSATION_LITERAL_NORMALIZATION_VERSION",
    "build_conversation_literal_snippet",
    "classify_conversation_literal_subject",
    "conversation_literal_sort_key",
    "normalize_conversation_literal_query",
    "normalize_conversation_literal_text",
]
