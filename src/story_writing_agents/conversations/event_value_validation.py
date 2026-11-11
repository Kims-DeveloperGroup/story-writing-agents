"""Scalar validation and canonical JSON codecs for collaboration events."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import NoReturn, TypeVar

from ..agents import SPECIALIST_IDS
from ..errors import SessionIdError
from ..reply_trace import ReplyTrace
from ..structured_output_validation import (
    carry_validation,
    raise_validation,
    validation_call,
    validation_failure,
)


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_EnumT = TypeVar("_EnumT", bound=StrEnum)


def validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise validation_failure(TypeError(f'{label} must be an integer'), '$', f'{label} must be an integer')
    if value < 1:
        raise validation_failure(ValueError(f'{label} must be positive'), '$', f'{label} must be positive')
    return value


def canonical_json(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise_content_free_value_error("collaboration value is not canonical JSON")


def decode_canonical_json(payload: bytes) -> object:
    if type(payload) is not bytes:
        raise TypeError("canonical collaboration payload must be exact bytes")
    if len(payload) > 8 * 1_024 * 1_024:
        raise ValueError("canonical collaboration payload exceeds the size limit")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_fields,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise_content_free_value_error(
            "collaboration payload is not strict UTF-8 JSON"
        )
    if canonical_json(value) != payload:
        raise ValueError("collaboration payload is not canonical JSON")
    return value


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("collaboration payload duplicates a field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def digest_value(value: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


def validate_digest(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be sha256 followed by lowercase hex")
    return value


def validate_non_negative_integer(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def validate_exact_tuple(
    value: object,
    item_type: type[object],
    *,
    label: str,
    nonempty: bool = False,
) -> None:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise validation_failure(
            TypeError(f'{label} must be an exact tuple of {item_type.__name__}'),
            '$',
            'expected an array of valid objects',
        )
    if nonempty and not value:
        raise validation_failure(ValueError(f'{label} must not be empty'), '$', 'expected a nonempty array')


def validate_enum_tuple(
    value: object,
    enum_type: type[StrEnum],
    *,
    label: str,
) -> None:
    if type(value) is not tuple or any(type(item) is not enum_type for item in value):
        raise TypeError(f"{label} must be an exact enum tuple")
    require_unique(value, label=label)


def validate_identifier_tuple(value: object, *, label: str) -> None:
    if type(value) is not tuple:
        raise validation_failure(TypeError(f'{label} must be an exact tuple'), '$', 'expected an array')
    for index, item in enumerate(value):
        validation_call(f"$[{index}]", validate_identifier, item, label=label)
    require_unique(value, label=label)


def validate_specialist_tuple(value: object, *, label: str) -> None:
    if (
        type(value) is not tuple
        or not value
        or any(type(item) is not str or item not in SPECIALIST_IDS for item in value)
        or len(set(value)) != len(value)
        or value != tuple(item for item in SPECIALIST_IDS if item in value)
    ):
        raise ValueError(f"{label} must be a canonical specialist tuple")


def require_enum(value: object, enum_type: type[StrEnum], label: str) -> None:
    if type(value) is not enum_type:
        raise validation_failure(
            TypeError(f'{label} must be a {enum_type.__name__}'),
            '$',
            'expected one of ' + ', '.join((item.value for item in enum_type)),
        )


def require_unique(value: object, *, label: str) -> None:
    if not isinstance(value, tuple):
        raise validation_failure(TypeError(f'{label} must be a tuple'), '$', 'expected an array')
    if len(set(value)) != len(value):
        raise validation_failure(ValueError(f'{label} must be unique'), '$', 'expected unique array items')


def require_object_with_extension(
    value: object,
    fields: set[str],
    extension: set[str],
    *,
    label: str,
) -> dict[str, object]:
    """Accept the exact historical shape or one complete additive shape."""

    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    expected = fields | extension if set(value) & extension else fields
    return require_object_fields(value, expected, label=label)


def require_object_with_optional_extensions(
    value: object,
    fields: set[str],
    extensions: set[str],
    *,
    label: str,
) -> dict[str, object]:
    """Accept required fields plus any explicitly supported independent fields."""

    if (
        type(value) is not dict
        or not fields.issubset(value)
        or not set(value).issubset(fields | extensions)
    ):
        raise ValueError(f"{label} has missing or extra fields")
    return value


def reply_trace_fields(trace: ReplyTrace | None) -> dict[str, object]:
    return {} if trace is None else {"trace": trace.to_dict()}


def decode_optional_reply_trace(item: dict[str, object]) -> ReplyTrace | None:
    if "trace" not in item:
        return None
    return ReplyTrace.from_dict(item["trace"])


def require_object_fields(
    value: object,
    fields: set[str] | frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise validation_failure(
            ValueError(f'{label} has missing or extra fields'),
            '$',
            'expected an object containing exactly ' + ', '.join(sorted(fields)),
            value=value,
        )
    return value


def require_array_value(value: object, *, label: str) -> list[object]:
    if type(value) is not list:
        raise validation_failure(
            ValueError(f'{label} must be an array'),
            '$',
            'expected an array',
            value=value,
        )
    return value


def require_string_value(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise validation_failure(ValueError(f'{label} must be a string'), '$', 'expected text', value=value)
    return value


def optional_string_value(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return require_string_value(value, label=label)


def require_integer_value(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise validation_failure(
            ValueError(f'{label} must be an integer'),
            '$',
            'expected an integer',
            value=value,
        )
    return value


def require_boolean_value(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def optional_integer_value(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    return require_integer_value(value, label=label)


def require_enum_value(
    enum_type: type[_EnumT], value: object, *, label: str,
) -> _EnumT:
    text = require_string_value(value, label=label)
    try:
        return validation_call("$", enum_type, text)
    except ValueError as failure:
        raise_validation(carry_validation(ValueError(f'{label} is not supported'), failure))


def validate_response_invocation(value: str | None) -> None:
    if value is not None:
        if type(value) is not str:
            raise TypeError("response provider invocation ID must be exact text")
        validate_identifier(value, label="response provider invocation ID")


def response_invocation_fields(payload: object) -> dict[str, object]:
    invocation_id = getattr(payload, "provider_invocation_id", None)
    return {} if invocation_id is None else {"provider_invocation_id": invocation_id}


def validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise validation_failure(
            SessionIdError(f'{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}'),
            '$',
            'expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}',
        )
    return value


def validate_utc_timestamp(value: str, *, label: str) -> datetime:
    """Parse an exact canonical ISO-8601 UTC timestamp without mutation.

    Wrong types raise ``TypeError``; invalid, offset-wrong, or noncanonical
    values raise ``ValueError``.
    """

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise_content_free_value_error(f"{label} must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{label} must use UTC")
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use canonical ISO-8601 UTC format")
    return parsed


def raise_content_free_value_error(message: str) -> NoReturn:
    """Raise a fixed validation error without retaining caller-owned content."""

    try:
        raise ValueError(message) from None
    except ValueError as failure:
        failure.__cause__ = None
        failure.__context__ = None
        raise
