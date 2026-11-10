"""Record validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from ..manifest import ContextManifestError, validate_context_digest, validate_context_identity
from typing import TypeVar


CONTEXT_LEDGER_SCHEMA_VERSION = 2

MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES = 8 * 1024 * 1024

MAX_CONTEXT_LEDGER_SOURCES = 4096

MAX_CONTEXT_LEDGER_PROVIDER_CALLS = 32

CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS = 5.0

MAX_LEDGER_GENERATION = (1 << 63) - 1

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

class ContextLedgerErrorCode(StrEnum):
    """Stable content-free ledger failure categories."""

    INVALID_VALUE = "invalid_value"
    INVALID_TRANSITION = "invalid_transition"
    CONFLICT = "conflict"
    COMMIT_NOT_AUTHORIZED = "commit_not_authorized"
    LIMIT_EXCEEDED = "limit_exceeded"
    LOCK_TIMEOUT = "lock_timeout"
    STORAGE_UNSUPPORTED = "storage_unsupported"
    STORAGE_UNSAFE = "storage_unsafe"
    STORAGE_FAILURE = "storage_failure"

_ERROR_MESSAGES = {
    ContextLedgerErrorCode.INVALID_VALUE: "context ledger value is invalid",
    ContextLedgerErrorCode.INVALID_TRANSITION: (
        "context ledger transition is invalid"
    ),
    ContextLedgerErrorCode.CONFLICT: "context ledger generation conflicts",
    ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED: (
        "context ledger commit is not authorized"
    ),
    ContextLedgerErrorCode.LIMIT_EXCEEDED: "context ledger limit is exceeded",
    ContextLedgerErrorCode.LOCK_TIMEOUT: "context ledger writer lock timed out",
    ContextLedgerErrorCode.STORAGE_UNSUPPORTED: (
        "context ledger storage is unsupported"
    ),
    ContextLedgerErrorCode.STORAGE_UNSAFE: "context ledger storage is unsafe",
    ContextLedgerErrorCode.STORAGE_FAILURE: "context ledger storage failed",
}

class ContextLedgerError(ValueError):
    """Report one fixed content-free ledger failure."""

    def __init__(self, code: ContextLedgerErrorCode) -> None:
        if type(code) is not ContextLedgerErrorCode:
            raise TypeError("ledger error code must be a ContextLedgerErrorCode")
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])

def ledger_invalid_value() -> ContextLedgerError:
    """Construct the fixed invalid-value failure."""

    return ContextLedgerError(ContextLedgerErrorCode.INVALID_VALUE)

def ledger_exact_nonnegative_integer(value: object) -> int:
    """Return one exact nonnegative integer or fail without reflection."""

    if type(value) is not int or value < 0:
        raise ledger_invalid_value()
    assert isinstance(value, int)
    return value

def ledger_positive_generation(value: object) -> int:
    """Return one bounded positive generation."""

    generation = ledger_exact_nonnegative_integer(value)
    if generation == 0 or generation > MAX_LEDGER_GENERATION:
        raise ledger_invalid_value()
    return generation

def ledger_safe_identity(value: object) -> str:
    """Validate one content-free identity without retaining rejected text."""

    result: str | None
    try:
        result = validate_context_identity(value)
    except ContextManifestError:
        result = None
    if result is None:
        raise ledger_invalid_value()
    return result

def ledger_safe_digest(value: object) -> str:
    """Validate one lowercase SHA-256 identity without reflected failures."""

    result: str | None
    try:
        result = validate_context_digest(value)
    except ContextManifestError:
        result = None
    if result is None:
        raise ledger_invalid_value()
    return result

def ledger_parse_timestamp(value: object) -> datetime:
    """Parse one canonical UTC timestamp or fail content-free."""

    parsed: datetime | None = None
    if type(value) is str:
        try:
            parsed = datetime.strptime(value, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            parsed = None
    if parsed is None:
        raise ledger_invalid_value()
    return parsed

def ledger_format_timestamp(value: datetime) -> str:
    """Return one canonical UTC timestamp."""

    if type(value) is not datetime or value.tzinfo is None:
        raise ledger_invalid_value()
    return value.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)

def ledger_canonical_json(value: object) -> bytes:
    """Encode one validated ledger value as canonical strict UTF-8 JSON."""

    encoded: bytes | None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError):
        encoded = None
    if encoded is None:
        raise ledger_invalid_value()
    return encoded

class LedgerDecodeError(Exception):
    """Internal marker for content-free schema rejection."""

_EnumT = TypeVar("_EnumT", bound=StrEnum)

def ledger_decoded_dict(value: object, fields: frozenset[str]) -> dict[str, object]:
    """Return one exact-field JSON object or reject it privately."""

    if type(value) is not dict or frozenset(value) != fields:
        raise LedgerDecodeError
    assert isinstance(value, dict)
    return value

def ledger_decoded_list(value: object) -> list[object]:
    """Return one exact JSON list."""

    if type(value) is not list:
        raise LedgerDecodeError
    assert isinstance(value, list)
    return value

def ledger_decoded_string(value: object) -> str:
    """Return one JSON string."""

    if type(value) is not str:
        raise LedgerDecodeError
    assert isinstance(value, str)
    return value

def ledger_decoded_integer(value: object) -> int:
    """Return one exact JSON integer, excluding booleans."""

    if type(value) is not int:
        raise LedgerDecodeError
    assert isinstance(value, int)
    return value

def ledger_decoded_optional_integer(value: object) -> int | None:
    """Return one nullable exact JSON integer."""

    if value is None:
        return None
    return ledger_decoded_integer(value)

def ledger_decoded_optional_string(value: object) -> str | None:
    """Return one nullable exact JSON string."""

    if value is None:
        return None
    return ledger_decoded_string(value)

def ledger_decoded_boolean(value: object) -> bool:
    """Return one exact JSON boolean."""

    if type(value) is not bool:
        raise LedgerDecodeError
    assert isinstance(value, bool)
    return value

def ledger_decoded_enum(enum_type: type[_EnumT], value: object) -> _EnumT:
    """Return one exact string-backed enum value."""

    text = ledger_decoded_string(value)
    result: _EnumT | None
    try:
        result = enum_type(text)
    except ValueError:
        result = None
    if result is None:
        raise LedgerDecodeError
    return result
