"""Scalar validation owned by immutable conversation values."""

from __future__ import annotations

import re

from ...errors import SessionIdError


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def validate_positive_integer(value: int, *, label: str) -> int:
    """Return a positive non-boolean integer without mutation.

    Other types raise ``TypeError`` and non-positive values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 1:
        raise ValueError(f"{label} must be positive")
    return value


def validate_non_negative_integer(value: int, *, label: str) -> int:
    """Return a non-negative non-boolean integer without mutation.

    Other types raise ``TypeError`` and negative values raise ``ValueError``.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def validate_identifier(value: str, *, label: str) -> str:
    """Return a safe identifier or raise ``SessionIdError`` without mutation."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value
