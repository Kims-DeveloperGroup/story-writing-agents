"""Content-free context execution failures and closed diagnostic codes."""

from __future__ import annotations

from enum import StrEnum


class ContextExecutionErrorCode(StrEnum):
    """Stable content-free failures owned by contextual orchestration."""

    INVALID_VALUE = "invalid_value"
    PROVIDER_CONTRACT = "provider_contract"
    MALFORMED_ACTION = "malformed_action"
    ACTION_EXHAUSTED = "action_exhausted"
    CONTINUATION_MISMATCH = "continuation_mismatch"


class ContextExecutionErrorDetail(StrEnum):
    """Private allow-list for fixed, content-free execution diagnostics."""

    READ_SOURCE_HANDLES_REQUIRED = "read_source_handles_required"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"


_EXECUTION_ERROR_MESSAGES = {
    ContextExecutionErrorCode.INVALID_VALUE: "contextual invocation is invalid",
    ContextExecutionErrorCode.PROVIDER_CONTRACT: (
        "contextual provider contract is invalid"
    ),
    ContextExecutionErrorCode.MALFORMED_ACTION: (
        "contextual provider action is malformed"
    ),
    ContextExecutionErrorCode.ACTION_EXHAUSTED: (
        "contextual provider action limit is exhausted"
    ),
    ContextExecutionErrorCode.CONTINUATION_MISMATCH: (
        "contextual provider continuation does not match"
    ),
}


_EXECUTION_ERROR_DETAIL_MESSAGES = {
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ): (
        "contextual provider read action is invalid: `handles` must contain "
        "source handles returned by the preceding context search"
    ),
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ): (
        "contextual provider response is invalid: `correlation` does not match "
        "the current request correlation token"
    ),
}


class ContextExecutionError(ValueError):
    """Report one fixed contextual lifecycle failure without story content."""

    def __init__(self, code: ContextExecutionErrorCode) -> None:
        if type(code) is not ContextExecutionErrorCode:
            raise TypeError(
                "context execution error code must be a ContextExecutionErrorCode"
            )
        self.code = code
        super().__init__(_EXECUTION_ERROR_MESSAGES[code])


def execution_error(
    code: ContextExecutionErrorCode,
    *,
    detail: ContextExecutionErrorDetail | None = None,
) -> ContextExecutionError:
    failure = ContextExecutionError(code)
    if detail is None:
        return failure
    if type(detail) is not ContextExecutionErrorDetail:
        raise TypeError("context execution error detail is invalid")
    try:
        message = _EXECUTION_ERROR_DETAIL_MESSAGES[(code, detail)]
    except KeyError:
        raise TypeError("context execution error detail is invalid") from None
    failure.args = (message,)
    failure._detail = detail
    return failure


def execution_error_has_detail(
    failure: ContextExecutionError,
    detail: ContextExecutionErrorDetail,
) -> bool:
    """Recognize only one internally attached, unchanged fixed detail."""

    if (
        type(failure) is not ContextExecutionError
        or type(detail) is not ContextExecutionErrorDetail
        or getattr(failure, "_detail", None) is not detail
    ):
        return False
    try:
        expected = _EXECUTION_ERROR_DETAIL_MESSAGES[(failure.code, detail)]
    except KeyError:
        return False
    return str(failure) == expected


__all__ = [
    'ContextExecutionErrorCode',
    'ContextExecutionErrorDetail',
    'ContextExecutionError',
    'execution_error',
    'execution_error_has_detail',
]
