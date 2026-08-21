"""Pure lifecycle interpretation for the interactive controller."""

from __future__ import annotations

from ..errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionAccessBlockedError,
    SessionExistsError,
    SessionIdError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    SessionPausedError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..interactive_terminal import InteractiveError, InteractiveErrorCode
from ..session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)


RECOVERABLE_SESSION_ERROR_CODES = frozenset(
    {
        InteractiveErrorCode.SESSION_INVALID,
        InteractiveErrorCode.SESSION_EXISTS,
        InteractiveErrorCode.SESSION_NOT_FOUND,
        InteractiveErrorCode.SESSION_PAUSED,
        InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
        InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN,
        InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED,
        InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
    }
)


def require_lifecycle_state(
    state: object,
    expected_session: str,
) -> SessionLifecycleState:
    """Return an exact correlated lifecycle value or raise protocol error."""

    if type(state) is not SessionLifecycleState:
        raise BackendProtocolError(
            "backend returned an invalid session lifecycle value"
        )
    try:
        provider_work_is_ready(
            state,
            provider_configuration_ready=False,
        )
    except (TypeError, ValueError) as exc:
        raise BackendProtocolError(
            "backend returned malformed session lifecycle facts"
        ) from exc
    if state.session_id != expected_session:
        raise BackendProtocolError(
            "backend returned lifecycle facts for another session"
        )
    return state


def require_ready_access_state(
    state: object,
    expected_session: str,
) -> SessionLifecycleState:
    """Require exact ready durable state with access held by this process."""

    value = require_lifecycle_state(state, expected_session)
    if (
        value.durable_state is not SessionDurableState.READY
        or value.access_state is not SessionAccessState.HELD_HERE
    ):
        raise BackendProtocolError(
            "backend did not return ready access held by this process"
        )
    return value


def require_correlated_state(
    initial: SessionLifecycleState | None,
    transition: object,
    observed: object,
    expected_session: str,
) -> SessionLifecycleState:
    """Require the transition result and immediate post-read to be identical."""

    first = require_ready_access_state(transition, expected_session)
    second = require_ready_access_state(observed, expected_session)
    if initial is not None:
        before = require_lifecycle_state(initial, expected_session)
        if first.created_at != before.created_at:
            raise BackendProtocolError(
                "session creation identity changed during access acquisition"
            )
        if (
            before.durable_state is SessionDurableState.READY
            and first.updated_at != before.updated_at
        ):
            raise BackendProtocolError(
                "ready session changed during access acquisition"
            )
        if (
            before.durable_state is SessionDurableState.PAUSED
            and first.updated_at == before.updated_at
        ):
            raise BackendProtocolError(
                "reopened session did not advance its durable timestamp"
            )
    if second != first:
        raise BackendProtocolError(
            "session lifecycle changed during access acquisition"
        )
    return second


def require_released_state(
    state: object,
    expected_session: str,
) -> SessionLifecycleState:
    """Require a valid post-release state no longer held by this process."""

    value = require_lifecycle_state(state, expected_session)
    if value.access_state is SessionAccessState.HELD_HERE:
        raise BackendProtocolError(
            "backend retained process access after release"
        )
    return value


def next_session_action(
    state: SessionLifecycleState | None,
    *,
    attached: bool,
    focused: bool,
    provider_ready: bool,
) -> str:
    """Return the fixed next action for one read-only lifecycle snapshot."""

    if state is None or state.durable_state is SessionDurableState.ABSENT:
        return "Create a session."
    if state.access_state is SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER:
        return "Close the owning local process normally, then retry."
    if state.access_state is SessionAccessState.OWNERSHIP_UNKNOWN:
        return "Verify ownership; do not remove the access claim."
    if state.durable_state is SessionDurableState.PAUSED:
        return "Resume the session to make it ready."
    if state.access_state is SessionAccessState.CLAIMABLE:
        return "Resume the session."
    if not provider_ready:
        return "Repair provider configuration, then retry."
    if attached and not focused:
        return "Create or choose a conversation."
    if attached and focused:
        return "Continue with the current focus."
    return "Resume the session."


def classify_session_failure(failure: Exception) -> InteractiveError:
    """Select one safe interactive session code from an exception type."""

    if isinstance(failure, SessionIdError):
        code = InteractiveErrorCode.SESSION_INVALID
    elif isinstance(failure, SessionExistsError):
        code = InteractiveErrorCode.SESSION_EXISTS
    elif isinstance(failure, SessionNotFoundError):
        code = InteractiveErrorCode.SESSION_NOT_FOUND
    elif isinstance(failure, SessionPausedError):
        code = InteractiveErrorCode.SESSION_PAUSED
    elif isinstance(failure, SessionAccessBlockedError):
        code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
    elif isinstance(failure, SessionOwnershipUnknownError):
        code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif isinstance(failure, (ConfigurationError, BackendProtocolError)):
        code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


__all__ = [
    "RECOVERABLE_SESSION_ERROR_CODES",
    "classify_session_failure",
    "next_session_action",
    "require_correlated_state",
    "require_lifecycle_state",
    "require_ready_access_state",
    "require_released_state",
]
