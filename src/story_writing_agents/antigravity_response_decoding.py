"""Strict Antigravity JSON callbacks and content-free CLI result decoding."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from re import Pattern
from typing import Any, Generic, Protocol, TypeVar

from .agent_context.accounting import (
    ProviderJsonEncodingError,
    ProviderUsage,
    ProviderUsageObservation,
    ProviderUsageScope,
    canonical_provider_json,
)
from .provider_adapter import ProviderInvocationError, ProviderResponseDiagnostic, ProviderResult
from .structured_output_validation import strict_json_member_text


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


_Receiver = TypeVar("_Receiver")
_ReceiverContra = TypeVar("_ReceiverContra", contravariant=True)


@dataclass(frozen=True, slots=True)
class ProviderResultHooks(Generic[_Receiver]):
    """Resolve adapter hooks where terminal decoding needs them."""

    parse_result: Callable[[_Receiver], Callable[[bytes], dict[str, Any]]]
    unwrap_result_envelope: Callable[[_Receiver], Callable[[dict[str, Any]], dict[str, Any]]]
    malformed_output: Callable[[_Receiver], Callable[[], None]]
    native_output_schema: Callable[[_Receiver], Callable[[Mapping[str, Any]], dict[str, Any]]]
    provider_usage: Callable[[_Receiver], Callable[[object], ProviderUsage | None]]
    malformed_call_pattern: Callable[[], Pattern[str]]
    safe_conversation_id_pattern: Callable[[], Pattern[str]]


class _ProviderResultDecoder(Protocol[_ReceiverContra]):
    def __call__(
        self,
        cls: _ReceiverContra,
        stdout: bytes,
        *,
        expected_thread_id: str | None,
        expected_schema: Mapping[str, Any],
        completion_check: Callable[[str, dict[str, Any], str], bool] | None = None,
        response_diagnostic: ProviderResponseDiagnostic | None = None,
        completion_required: bool = False,
    ) -> ProviderResult: ...


def _is_result_event(event: dict[str, Any]) -> bool:
    values = [event[key] for key in ("type", "event") if key in event]
    return bool(values) and all(
        isinstance(value, str) and value.strip().lower() == "result" for value in values
    )


def unwrap_result_envelope(terminal: dict[str, Any]) -> dict[str, Any]:
    """Extract the inner result payload if this is a terminal result/event envelope."""
    if _is_result_event(terminal) and isinstance(terminal.get("result"), dict):
        return terminal["result"]
    return terminal


def completed_failure_response_text(stdout: bytes) -> str | None:
    """Extract response text from the one completed Antigravity result."""

    try:
        outer = parse_result(stdout)
        if ("type" in outer or "event" in outer) and not _is_result_event(outer):
            return None
        terminal = unwrap_result_envelope(outer)
        if terminal.get("status") not in ("ERROR", "SUCCESS"):
            return None
        if "structured_output" in terminal and terminal["structured_output"] is not None:
            path = ("structured_output",) if terminal is outer else ("result", "structured_output")
            return strict_json_member_text(stdout.decode("utf-8", "strict"), path)
        response = terminal.get("response")
        if type(response) is str and response:
            response.encode("utf-8")
            return response
    except (ProviderInvocationError, ValueError, UnicodeError, RecursionError):
        pass
    return None


def provider_usage(value: object) -> ProviderUsage | None:
    """Return only exact counters from the documented terminal usage object."""

    if type(value) is not dict:
        return None
    assert isinstance(value, dict)
    fields = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cache_read_tokens": "cached_input_tokens",
    }
    counters: dict[str, int | None] = {}
    for source, target in fields.items():
        if source not in value:
            counters[target] = None
            continue
        counter = value[source]
        if type(counter) is not int or counter < 0:
            counters[target] = None
        else:
            counters[target] = counter
    if all(counter is None for counter in counters.values()):
        return None
    return ProviderUsage(**counters)



def bind_provider_result(hooks: ProviderResultHooks[_Receiver]) -> _ProviderResultDecoder[_Receiver]:
    """Bind the adapter's lookup hooks once when its class is defined."""

    def _provider_result(
        cls,
        stdout: bytes,
        *,
        expected_thread_id: str | None,
        expected_schema: Mapping[str, Any],
        completion_check: Callable[[str, dict[str, Any], str], bool] | None = None,
        response_diagnostic: ProviderResponseDiagnostic | None = None,
        completion_required: bool = False,
    ) -> ProviderResult:
        """Decode one Antigravity JSON result with fixed diagnostics."""

        outer_terminal = hooks.parse_result(cls)(stdout)
        terminal = hooks.unwrap_result_envelope(cls)(outer_terminal)
        native_error = terminal.get("error")
        recoverable_error = (
            terminal.get("status") == "ERROR"
            and type(native_error) is str
            and hooks.malformed_call_pattern().fullmatch(
                native_error
            ) is not None
        )
        if terminal.get("status") != "SUCCESS" and not recoverable_error:
            raise ProviderInvocationError(
                "Antigravity CLI did not complete successfully",
                taint_existing_thread=True,
            )
        thread_id = terminal.get("conversation_id")
        if (
            not isinstance(thread_id, str)
            or hooks.safe_conversation_id_pattern().fullmatch(thread_id) is None
            or (
                expected_thread_id is not None
                and thread_id != expected_thread_id
            )
        ):
            raise ProviderInvocationError(
                "Antigravity CLI returned an invalid conversation ID",
                taint_existing_thread=True,
            )

        structured_output = terminal.get("structured_output")
        schema_echo = terminal.get("json_schema")
        if type(structured_output) is not dict or type(schema_echo) is not dict:
            hooks.malformed_output(cls)()
        try:
            canonical_provider_json(structured_output)
            expected_schema_bytes = canonical_provider_json(
                hooks.native_output_schema(cls)(expected_schema)
            )
            echoed_schema_bytes = canonical_provider_json(schema_echo)
            member_path = (("result",) if terminal is not outer_terminal else ()) + ("structured_output",)
            response_text = strict_json_member_text(stdout.decode("utf-8", "strict"), member_path)
        except (
            ProviderJsonEncodingError,
            UnicodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            hooks.malformed_output(cls)()
        if echoed_schema_bytes != expected_schema_bytes:
            hooks.malformed_output(cls)()
        if (recoverable_error or completion_required) and (
            completion_check is None
            or not completion_check(thread_id, structured_output, native_error or "")
        ):
            raise ProviderInvocationError(
                "Antigravity CLI did not complete successfully",
                taint_existing_thread=True,
            )
        raw_usage = hooks.provider_usage(cls)(terminal.get("usage"))
        return ProviderResult(
            thread_id=thread_id,
            response_text=response_text,
            usage=raw_usage,
            usage_observation=(None if raw_usage is None else ProviderUsageObservation(
                raw_usage, ProviderUsageScope.UNKNOWN, "antigravity.completed.usage")),
            response_diagnostic=(
                response_diagnostic if recoverable_error or completion_required else None
            ),
        )

    return _provider_result
