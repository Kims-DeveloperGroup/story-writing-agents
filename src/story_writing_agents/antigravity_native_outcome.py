"""Interpret Antigravity native subprocess outcomes after role revalidation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import re
from typing import Any

from .agent_context import ProviderCallState
from .errors import ProviderAuthenticationRequiredError
from .provider_adapter import (
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResponseDiagnostic,
    ProviderResult,
    record_provider_failure_accounting,
)
from .provider_process import ProcessResult


_NATIVE_PARTIAL_TIMEOUT = re.compile(
    rb"^\[agy\] print timeout after [^\r\n]+ with turn in progress; "
    rb"returning partial output\r?$", re.MULTILINE,
)
_NATIVE_TRUNCATED = re.compile(
    rb"^error: [^\r\n]+ \(response may be truncated\)\r?$", re.MULTILINE,
)


def complete_native_outcome(
    result: ProcessResult,
    invocation: ProviderInvocation,
    *,
    finalize_stream_observation: Callable[
        [Callable[[], bytes] | None], tuple[Sequence[bytes], str | None, bool]
    ],
    transport_utf8_bytes: int,
    get_authentication_failure: Callable[
        [], Callable[[bytes, int], ProviderAuthenticationRequiredError | None]
    ],
    decode_result: Callable[[bytes, ProviderResponseDiagnostic, bool], ProviderResult],
) -> ProviderResult:
    replay_stream = invocation.stream_sink is not None
    (
        captured_result_bytes,
        invalid_stream_line,
        has_trailing_data_after_terminal,
    ) = finalize_stream_observation((lambda: result.stdout) if replay_stream else None)
    if replay_stream:
        diagnostic_stdout = (
            result.stdout
            if len(result.stdout.splitlines()) <= 1
            else captured_result_bytes[0]
            if len(captured_result_bytes) == 1
            else b""
        )
    else:
        diagnostic_stdout = result.stdout

    partial_timeout = bool(_NATIVE_PARTIAL_TIMEOUT.search(result.stderr))
    incomplete_output = partial_timeout or bool(
        _NATIVE_TRUNCATED.search(result.stderr)
    )
    response_diagnostic = ProviderResponseDiagnostic(
        stdout=diagnostic_stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
    )
    try:
        if invocation.stream_sink is not None:
            if invalid_stream_line is not None:
                raise ProviderInvocationError(
                    f"Antigravity streaming response contained invalid stream line: {invalid_stream_line}",
                    taint_existing_thread=True,
                )
        effective_stdout = (
            captured_result_bytes[0]
            if (invocation.stream_sink is not None and len(captured_result_bytes) == 1)
            else result.stdout
        )
        authentication = get_authentication_failure()(
            effective_stdout, result.returncode,
        )
        if authentication is not None:
            raise ProviderInvocationError(
                str(authentication),
                taint_existing_thread=not authentication.replay_safe,
                authentication_failure=authentication,
            )
        if result.returncode != 0:
            raise ProviderInvocationError(
                f"Antigravity CLI exited with status {result.returncode}",
                taint_existing_thread=True,
            )
        if invocation.stream_sink is not None:
            if len(captured_result_bytes) != 1:
                raise ProviderInvocationError(
                    f"Antigravity streaming response contained {len(captured_result_bytes)} terminal result events; expected exactly 1",
                    taint_existing_thread=True,
                )
            if has_trailing_data_after_terminal:
                raise ProviderInvocationError(
                    "Antigravity streaming response contained trailing data after terminal result",
                    taint_existing_thread=True,
                )
        provider_result = decode_result(effective_stdout, response_diagnostic, incomplete_output)
        return ProviderResult(
            thread_id=provider_result.thread_id,
            response_text=provider_result.response_text,
            usage=provider_result.usage,
            usage_observation=provider_result.usage_observation,
            actual_transport_utf8_bytes=transport_utf8_bytes,
            response_diagnostic=provider_result.response_diagnostic,
        )
    except BaseException as exc:
        if isinstance(exc, ProviderInvocationError):
            if partial_timeout:
                pass
            exc.record_response_diagnostic(response_diagnostic)
        record_provider_failure_accounting(
            exc,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=transport_utf8_bytes,
        )
        raise


def classify_native_authentication_failure(
    stdout: bytes,
    returncode: int,
    *,
    parse_result: Callable[[bytes], dict[str, Any]],
    unwrap_result_envelope: Callable[[dict[str, Any]], dict[str, Any]],
) -> ProviderAuthenticationRequiredError | None:
    try:
        terminal = parse_result(stdout)
    except ProviderInvocationError:
        return None
    terminal = unwrap_result_envelope(terminal)
    error = terminal.get("error")
    if terminal.get("status") != "ERROR":
        return None
    message = error.get("message") if type(error) is dict else error
    native_401 = (
        type(error) is dict
        and any(type(error.get(key)) is int and error[key] == 401
                for key in ("status", "status_code", "code"))
    ) or (
        type(message) is str
        and re.search(r"\b(?:HTTP(?: status)?|status(?: code)?)\s*:?\s*401\b|\b401 Unauthorized\b", message, re.IGNORECASE)
        is not None
    )
    if message != "authentication failed or timed out" and not native_401:
        return None
    usage = terminal.get("usage")
    counters = {"input_tokens", "output_tokens", "thinking_tokens",
                "cache_read_tokens", "total_tokens"}
    replay_safe = (
        returncode == 1
        and set(terminal) == {"conversation_id", "status", "response", "error",
                              "duration_seconds", "num_turns", "usage"}
        and terminal["error"] == "authentication failed or timed out"
        and terminal["conversation_id"] == ""
        and terminal["response"] == ""
        and type(terminal["duration_seconds"]) is int
        and terminal["duration_seconds"] == 0
        and type(terminal["num_turns"]) is int and terminal["num_turns"] == 0
        and type(usage) is dict and set(usage) == counters
        and all(type(value) is int and value == 0 for value in usage.values())
    )
    return ProviderAuthenticationRequiredError("antigravity", replay_safe=replay_safe)
