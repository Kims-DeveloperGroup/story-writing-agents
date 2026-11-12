"""Strict Antigravity JSON callbacks and content-free CLI result decoding."""

from __future__ import annotations

import json
import math
from typing import Any

from .provider_adapter import ProviderInvocationError


class _DuplicateField(ValueError):
    """Mark an ambiguous provider JSON object without retaining its values."""


def reject_duplicate_fields(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateField
        value[key] = item
    return value


def reject_constant(_value: str) -> None:
    raise ValueError


def finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def parse_result(stdout: bytes) -> dict[str, Any]:
    """Decode exactly one strict JSON object with content-free errors."""

    invalid_utf8 = False
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        invalid_utf8 = True
    if invalid_utf8:
        raise ProviderInvocationError(
            "Antigravity CLI result is not valid UTF-8",
            taint_existing_thread=True,
        ) from None

    malformed = False
    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_fields,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except (ValueError, RecursionError, UnicodeError):
        malformed = True
    if malformed:
        raise ProviderInvocationError(
            "Antigravity CLI returned malformed JSON",
            taint_existing_thread=True,
        ) from None
    if type(value) is not dict:
        raise ProviderInvocationError(
            "Antigravity CLI returned a non-object JSON result",
            taint_existing_thread=True,
        )
    return value


def malformed_output() -> None:
    raise ProviderInvocationError(
        "Antigravity CLI returned malformed structured output",
        taint_existing_thread=True,
    )
